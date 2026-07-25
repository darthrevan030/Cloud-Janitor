"""Property-based tests for CostExplorerCache — round-trip/TTL and corruption resilience.

Property 3: Cache Round-Trip and TTL Expiry — for any key/response pair,
set() followed by get() returns the response within TTL, and returns None
after TTL expires.

Property 4: Corrupted Cache File Degrades to Full Miss — if the cache file
contains invalid JSON, get() returns None (not raises).

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.6**
"""

from __future__ import annotations

from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

from cloud_janitor.mcp_server.backends.cost_explorer import CostExplorerCache

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Safe text for cache keys — no null bytes, no surrogates.
_safe_key = st.text(
    min_size=1,
    max_size=100,
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
)

# JSON-safe response values — nested dicts with string keys and primitive values.
_json_value = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-1_000_000, max_value=1_000_000),
        st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6),
        st.text(
            min_size=0,
            max_size=50,
            alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
        ),
    ),
    lambda children: st.one_of(
        st.lists(children, min_size=0, max_size=5),
        st.dictionaries(
            keys=st.text(
                min_size=1,
                max_size=20,
                alphabet=st.characters(
                    whitelist_categories=("L", "N"),
                    blacklist_characters="\x00",
                ),
            ),
            values=children,
            min_size=0,
            max_size=5,
        ),
    ),
    max_leaves=10,
)

# Response strategy: always a dict (the cache stores dicts).
_response_strategy = st.dictionaries(
    keys=st.text(
        min_size=1,
        max_size=20,
        alphabet=st.characters(
            whitelist_categories=("L", "N"),
            blacklist_characters="\x00",
        ),
    ),
    values=_json_value,
    min_size=1,
    max_size=5,
)

# TTL strategy: positive integers representing seconds.
_ttl_strategy = st.integers(min_value=1, max_value=86400)

# Garbage content for corrupted cache files.
_garbage_strategy = st.one_of(
    st.text(
        min_size=1,
        max_size=200,
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    ),
    st.binary(min_size=1, max_size=200).map(
        lambda b: b.decode("latin-1")
    ),
    st.just("{not valid json"),
    st.just(""),
    st.just("[[[[["),
    st.just("null"),
)


# ---------------------------------------------------------------------------
# Property 3: Cache Round-Trip and TTL Expiry
# ---------------------------------------------------------------------------


class TestCacheRoundTripAndTTLExpiry:
    """Property 3: For any key/response pair, set() followed by get()
    returns the response within TTL, and returns None after TTL expires.

    This validates that:
    - The cache serializes and deserializes response dicts losslessly (Req 3.3)
    - TTL is honored: fresh entries are returned, expired entries are not (Req 3.2, 3.4)
    - The cache key lookup is exact-match (Req 3.1)

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
    """

    @settings(max_examples=100, deadline=None)
    @given(key=_safe_key, response=_response_strategy, ttl=_ttl_strategy)
    def test_set_then_get_within_ttl_returns_same_response(self, tmp_path_factory, key, response, ttl):
        """set(key, response) then get(key) within TTL returns the exact response."""
        tmp_path = tmp_path_factory.mktemp("cache")
        cache_path = tmp_path / "cache.json"
        cache = CostExplorerCache(path=cache_path, ttl_seconds=ttl)

        # Freeze time at a known point for set
        fake_time = 1_000_000.0
        with patch("cloud_janitor.mcp_server.backends.cost_explorer.time.time", return_value=fake_time):
            cache.set(key, response)

        # get() at the same time — well within TTL
        with patch("cloud_janitor.mcp_server.backends.cost_explorer.time.time", return_value=fake_time):
            result = cache.get(key)

        assert result is not None, (
            f"get({key!r}) returned None immediately after set() — "
            f"cache round-trip failed"
        )
        assert result == response, (
            f"get({key!r}) returned {result!r}, expected {response!r} — "
            f"response was corrupted during serialization round-trip"
        )

    @settings(max_examples=100, deadline=None)
    @given(key=_safe_key, response=_response_strategy, ttl=_ttl_strategy)
    def test_get_after_ttl_expires_returns_none(self, tmp_path_factory, key, response, ttl):
        """set(key, response) then get(key) AFTER TTL expires returns None."""
        tmp_path = tmp_path_factory.mktemp("cache")
        cache_path = tmp_path / "cache.json"
        cache = CostExplorerCache(path=cache_path, ttl_seconds=ttl)

        # set() at time T
        set_time = 1_000_000.0
        with patch("cloud_janitor.mcp_server.backends.cost_explorer.time.time", return_value=set_time):
            cache.set(key, response)

        # get() at time T + TTL + 1 (just past expiry)
        expired_time = set_time + ttl + 1
        with patch("cloud_janitor.mcp_server.backends.cost_explorer.time.time", return_value=expired_time):
            result = cache.get(key)

        assert result is None, (
            f"get({key!r}) returned {result!r} after TTL expired "
            f"(set_time={set_time}, get_time={expired_time}, ttl={ttl}) — "
            f"TTL expiry not honored"
        )

    @settings(max_examples=50, deadline=None)
    @given(key=_safe_key, response=_response_strategy, ttl=_ttl_strategy)
    def test_get_just_before_ttl_expiry_returns_response(self, tmp_path_factory, key, response, ttl):
        """set(key, response) then get(key) at exactly TTL seconds should still
        return None (boundary: time.time() - timestamp > ttl means expired at ttl+epsilon).
        But at exactly ttl - 1, it should still be valid.
        """
        tmp_path = tmp_path_factory.mktemp("cache")
        cache_path = tmp_path / "cache.json"
        cache = CostExplorerCache(path=cache_path, ttl_seconds=ttl)

        set_time = 1_000_000.0
        with patch("cloud_janitor.mcp_server.backends.cost_explorer.time.time", return_value=set_time):
            cache.set(key, response)

        # get() at T + ttl - 1 (just before expiry)
        almost_expired_time = set_time + ttl - 1
        with patch("cloud_janitor.mcp_server.backends.cost_explorer.time.time", return_value=almost_expired_time):
            result = cache.get(key)

        assert result is not None, (
            f"get({key!r}) returned None at {almost_expired_time} "
            f"(set_time={set_time}, ttl={ttl}) — "
            f"entry expired prematurely (still had 1 second left)"
        )
        assert result == response, (
            f"get({key!r}) returned wrong value just before TTL expiry"
        )

    @settings(max_examples=50, deadline=None)
    @given(
        key1=_safe_key,
        key2=_safe_key,
        response=_response_strategy,
        ttl=_ttl_strategy,
    )
    def test_get_with_different_key_returns_none(self, tmp_path_factory, key1, key2, response, ttl):
        """set(key1, response) then get(key2) returns None when key1 != key2.
        Validates cache key is exact-match (Req 3.1).
        """
        from hypothesis import assume
        assume(key1 != key2)

        tmp_path = tmp_path_factory.mktemp("cache")
        cache_path = tmp_path / "cache.json"
        cache = CostExplorerCache(path=cache_path, ttl_seconds=ttl)

        fake_time = 1_000_000.0
        with patch("cloud_janitor.mcp_server.backends.cost_explorer.time.time", return_value=fake_time):
            cache.set(key1, response)

        with patch("cloud_janitor.mcp_server.backends.cost_explorer.time.time", return_value=fake_time):
            result = cache.get(key2)

        assert result is None, (
            f"get({key2!r}) returned {result!r} — but only key1={key1!r} was set. "
            f"Cache key matching is not exact."
        )


# ---------------------------------------------------------------------------
# Property 4: Corrupted Cache File Degrades to Full Miss
# ---------------------------------------------------------------------------


class TestCorruptedCacheFileDegradesToFullMiss:
    """Property 4: If the cache file contains invalid JSON, get() returns
    None (not raises). The cache gracefully degrades to a full miss on any
    corruption, allowing the next set() to rebuild the file.

    This validates Requirement 3.6: corrupted/missing cache file is treated
    as a full cache miss for all keys, with no error raised.

    **Validates: Requirement 3.6**
    """

    @settings(max_examples=100, deadline=None)
    @given(key=_safe_key, garbage=_garbage_strategy)
    def test_get_on_corrupted_file_returns_none(self, tmp_path_factory, key, garbage):
        """Writing garbage to the cache file, then calling get() returns None
        without raising an exception.
        """
        tmp_path = tmp_path_factory.mktemp("cache")
        cache_path = tmp_path / "cache.json"

        # Write garbage content to the cache file
        cache_path.write_text(garbage, encoding="utf-8")
        assert cache_path.exists(), "Precondition: garbage file must exist"

        cache = CostExplorerCache(path=cache_path, ttl_seconds=3600)

        # get() must return None, never raise
        result = cache.get(key)

        assert result is None, (
            f"get({key!r}) returned {result!r} on a corrupted cache file "
            f"(content={garbage!r}) — expected None (full miss)"
        )

    @settings(max_examples=50, deadline=None)
    @given(key=_safe_key, response=_response_strategy, garbage=_garbage_strategy)
    def test_set_after_corruption_rebuilds_cache(self, tmp_path_factory, key, response, garbage):
        """After corruption, set() successfully overwrites the garbage and
        a subsequent get() returns the newly-set value.
        """
        tmp_path = tmp_path_factory.mktemp("cache")
        cache_path = tmp_path / "cache.json"

        # Corrupt the file
        cache_path.write_text(garbage, encoding="utf-8")

        cache = CostExplorerCache(path=cache_path, ttl_seconds=3600)

        fake_time = 1_000_000.0
        with patch("cloud_janitor.mcp_server.backends.cost_explorer.time.time", return_value=fake_time):
            # set() should succeed despite existing corruption
            cache.set(key, response)

        with patch("cloud_janitor.mcp_server.backends.cost_explorer.time.time", return_value=fake_time):
            result = cache.get(key)

        assert result is not None, (
            f"get({key!r}) returned None after set() on previously-corrupted file — "
            f"set() did not rebuild the cache"
        )
        assert result == response, (
            f"get({key!r}) returned {result!r} after rebuild, "
            f"expected {response!r}"
        )

    @settings(max_examples=50, deadline=None)
    @given(key=_safe_key)
    def test_get_on_missing_file_returns_none(self, tmp_path_factory, key):
        """get() on a non-existent cache file returns None without raising."""
        tmp_path = tmp_path_factory.mktemp("cache")
        cache_path = tmp_path / "nonexistent_cache.json"
        assert not cache_path.exists(), "Precondition: file must not exist"

        cache = CostExplorerCache(path=cache_path, ttl_seconds=3600)
        result = cache.get(key)

        assert result is None, (
            f"get({key!r}) returned {result!r} on a missing cache file — "
            f"expected None"
        )

    @settings(max_examples=50, deadline=None)
    @given(key=_safe_key)
    def test_get_on_empty_file_returns_none(self, tmp_path_factory, key):
        """get() on an empty cache file returns None without raising."""
        tmp_path = tmp_path_factory.mktemp("cache")
        cache_path = tmp_path / "cache.json"
        cache_path.write_text("", encoding="utf-8")

        cache = CostExplorerCache(path=cache_path, ttl_seconds=3600)
        result = cache.get(key)

        assert result is None, (
            f"get({key!r}) returned {result!r} on an empty cache file — "
            f"expected None"
        )
