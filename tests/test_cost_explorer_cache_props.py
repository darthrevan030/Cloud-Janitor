"""Property-based tests for CostExplorerCache.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.6**

Property 3: Cache Round-Trip and TTL Expiry
Property 4: Corrupted Cache File Degrades to Full Miss
"""

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

from cloud_janitor.mcp_server.backends.cost_explorer import CostExplorerCache


# --- Strategies ---

# Cache keys: non-empty strings without null bytes (Windows-safe)
cache_keys = st.text(
    st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    min_size=1,
    max_size=100,
)

# Cache responses: arbitrary JSON-serializable dicts
_json_primitives = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(
        st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
        min_size=0,
        max_size=50,
    ),
)

# Simple dict responses (keys are strings, values are primitives or lists of primitives)
cache_responses = st.dictionaries(
    keys=st.text(
        st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
        min_size=1,
        max_size=30,
    ),
    values=st.one_of(
        _json_primitives,
        st.lists(_json_primitives, max_size=5),
    ),
    min_size=0,
    max_size=10,
)

# Byte sequences that are NOT valid cache JSON
_invalid_cache_bytes = st.one_of(
    # Random bytes that won't parse as JSON
    st.binary(min_size=1, max_size=200).filter(
        lambda b: _is_not_valid_cache_json(b)
    ),
    # Valid JSON but not a dict at the top level
    st.sampled_from([
        b"[]",
        b"null",
        b"42",
        b'"just a string"',
        b"true",
    ]),
    # Truncated/malformed JSON
    st.sampled_from([
        b'{"key": {',
        b"{",
        b"not json at all",
        b"\xff\xfe",
        b'{"key": {"response": "x"',
    ]),
)


def _is_not_valid_cache_json(data: bytes) -> bool:
    """Return True if data does NOT represent a valid cache dict."""
    try:
        parsed = json.loads(data.decode("utf-8"))
        return not isinstance(parsed, dict)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return True


class TestProperty3CacheRoundTripAndTTL:
    """Property 3: Cache Round-Trip and TTL Expiry.

    For any key/response pair, set(key, response) followed by get(key)
    returns the same response; after TTL expiry, get(key) returns None.

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
    """

    @given(key=cache_keys, response=cache_responses)
    @settings(max_examples=100, deadline=None)
    def test_set_then_get_within_ttl_returns_same_response(self, key, response):
        """A cached response is retrievable within the TTL window."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = Path(tmp_dir) / "cache.json"
            cache = CostExplorerCache(path=cache_file, ttl_seconds=3600)

            cache.set(key, response)
            result = cache.get(key)

            assert result == response, (
                f"get({key!r}) should return the stored response, "
                f"got {result!r} instead of {response!r}"
            )

    @given(key=cache_keys, response=cache_responses)
    @settings(max_examples=100, deadline=None)
    def test_get_after_ttl_expiry_returns_none(self, key, response):
        """A cached response returns None after the TTL has elapsed."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = Path(tmp_dir) / "cache.json"
            ttl = 60
            cache = CostExplorerCache(path=cache_file, ttl_seconds=ttl)

            # Write at a time that's already beyond the TTL in the past
            past_time = time.time() - ttl - 1
            with patch(
                "cloud_janitor.mcp_server.backends.cost_explorer.time.time",
                return_value=past_time,
            ):
                cache.set(key, response)

            # Read at current time — the entry should be expired
            result = cache.get(key)

            assert result is None, (
                f"get({key!r}) should return None after TTL expiry, got {result!r}"
            )

    @given(key=cache_keys, response=cache_responses)
    @settings(max_examples=50, deadline=None)
    def test_fresh_cache_instance_reads_persisted_data(self, key, response):
        """A second CostExplorerCache instance can read data written by the first."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = Path(tmp_dir) / "cache.json"

            cache1 = CostExplorerCache(path=cache_file, ttl_seconds=3600)
            cache1.set(key, response)

            # Create a completely new instance pointing to the same file
            cache2 = CostExplorerCache(path=cache_file, ttl_seconds=3600)
            result = cache2.get(key)

            assert result == response, (
                f"A fresh cache instance should read persisted data; "
                f"got {result!r} instead of {response!r}"
            )


class TestProperty4CorruptedCacheDegradesToMiss:
    """Property 4: Corrupted Cache File Degrades to Full Miss.

    Writing arbitrary bytes to the cache file, then calling get(key)
    returns None without raising.

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.6**
    """

    @given(corrupt_data=_invalid_cache_bytes, key=cache_keys)
    @settings(max_examples=100, deadline=None)
    def test_corrupted_file_returns_none_without_raising(self, corrupt_data, key):
        """get() on a corrupted cache file returns None, never raises."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = Path(tmp_dir) / "cache.json"
            cache_file.write_bytes(corrupt_data)

            cache = CostExplorerCache(path=cache_file, ttl_seconds=3600)
            result = cache.get(key)

            assert result is None, (
                f"get({key!r}) with corrupted cache should return None, "
                f"got {result!r} for corrupt data: {corrupt_data!r}"
            )

    @given(corrupt_data=_invalid_cache_bytes, key=cache_keys, response=cache_responses)
    @settings(max_examples=50, deadline=None)
    def test_set_after_corruption_rebuilds_cache(self, corrupt_data, key, response):
        """set() after corruption safely rewrites the cache file."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = Path(tmp_dir) / "cache.json"
            cache_file.write_bytes(corrupt_data)

            cache = CostExplorerCache(path=cache_file, ttl_seconds=3600)

            # set() should not raise even on a corrupted file
            cache.set(key, response)

            # The cache should now be readable
            result = cache.get(key)
            assert result == response, (
                f"After set() on a corrupted cache, get({key!r}) should return "
                f"the new response {response!r}, got {result!r}"
            )

    @given(key=cache_keys)
    @settings(max_examples=50, deadline=None)
    def test_missing_file_returns_none(self, key):
        """get() on a non-existent cache file returns None, never raises."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = Path(tmp_dir) / "nonexistent_cache.json"

            cache = CostExplorerCache(path=cache_file, ttl_seconds=3600)
            result = cache.get(key)

            assert result is None, (
                f"get({key!r}) with missing cache file should return None, "
                f"got {result!r}"
            )
