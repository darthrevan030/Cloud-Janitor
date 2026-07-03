"""Property tests for _extract_resource_id_from_command allowlist validation.

**Validates: Requirements 9.1, 9.2, 9.3**

Property 4: Resource ID Extraction Allowlist
For any candidate string, _extract_resource_id_from_command() SHALL return
the candidate if and only if: (a) the candidate is non-empty and not
whitespace-only, AND (b) the candidate fully matches the regex
^[a-zA-Z0-9\\-_:./]{1,256}$. All other inputs SHALL produce None.
"""

import re

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from orchestrator import Orchestrator


# Independent oracle: re-implement the allowlist check without referencing
# the production code's pattern object, to avoid tautological testing.
_ORACLE_PATTERN = re.compile(r"^[a-zA-Z0-9\-_:./]{1,256}$")

ALLOWED_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:./"
PREFIX = "APPROVE"


def _make_orch() -> Orchestrator:
    """Create a minimal Orchestrator instance without running __init__."""
    return Orchestrator.__new__(Orchestrator)


def _oracle_should_accept(candidate: str) -> bool:
    """Independent oracle: returns True iff candidate should be accepted."""
    if not candidate or candidate.isspace():
        return False
    return _ORACLE_PATTERN.fullmatch(candidate) is not None


# Strategy for valid resource IDs: 1-256 chars from the allowed set
valid_resource_id = st.text(
    alphabet=ALLOWED_CHARS,
    min_size=1,
    max_size=256,
)

# Strategy for arbitrary strings (includes invalid chars, empty, whitespace, long)
arbitrary_candidate = st.text(min_size=0, max_size=300)


class TestPropertyResourceIdAllowlist:
    """Property 4: Resource ID Extraction Allowlist.

    **Validates: Requirements 9.1, 9.2, 9.3**
    """

    @given(candidate=valid_resource_id)
    @settings(max_examples=200)
    def test_valid_candidates_are_always_returned(self, candidate):
        """Any candidate from the allowlist alphabet (length 1-256) is returned."""
        orch = _make_orch()
        command = f"{PREFIX} {candidate}"
        result = orch._extract_resource_id_from_command(command, PREFIX)
        assert result == candidate, (
            f"Expected valid candidate to be returned, got None for: {candidate!r}"
        )

    @given(candidate=arbitrary_candidate)
    @settings(max_examples=500)
    def test_oracle_agreement_on_arbitrary_input(self, candidate):
        """For any arbitrary candidate string, the function agrees with the oracle.

        If oracle says accept -> function returns candidate.
        If oracle says reject -> function returns None.
        """
        orch = _make_orch()
        command = f"{PREFIX} {candidate}"
        result = orch._extract_resource_id_from_command(command, PREFIX)
        expected_accept = _oracle_should_accept(candidate)

        if expected_accept:
            assert result == candidate, (
                f"Oracle accepts {candidate!r} but function returned None"
            )
        else:
            assert result is None, (
                f"Oracle rejects {candidate!r} but function returned {result!r}"
            )

    @given(candidate=st.text(min_size=257, max_size=300, alphabet=ALLOWED_CHARS))
    @settings(max_examples=50)
    def test_over_256_chars_always_rejected(self, candidate):
        """Candidates exceeding 256 characters are always rejected (Req 9.1)."""
        orch = _make_orch()
        command = f"{PREFIX} {candidate}"
        result = orch._extract_resource_id_from_command(command, PREFIX)
        assert result is None, (
            f"Expected None for {len(candidate)}-char candidate, got {result!r}"
        )

    @given(whitespace=st.text(
        alphabet=" \t\n\r\x0b\x0c",
        min_size=1,
        max_size=20,
    ))
    @settings(max_examples=50)
    def test_whitespace_only_candidates_rejected_without_regex(self, whitespace):
        """Whitespace-only candidates return None (Req 9.3)."""
        orch = _make_orch()
        command = f"{PREFIX} {whitespace}"
        result = orch._extract_resource_id_from_command(command, PREFIX)
        assert result is None, (
            f"Expected None for whitespace-only candidate {whitespace!r}"
        )

    @given(candidate=st.from_regex(r"[^a-zA-Z0-9\-_:./]", fullmatch=True))
    @settings(max_examples=100)
    def test_single_disallowed_char_always_rejected(self, candidate):
        """Any single character outside the allowlist is rejected (Req 9.1)."""
        assume(not candidate.isspace())  # whitespace is Req 9.3, tested separately
        orch = _make_orch()
        command = f"{PREFIX} {candidate}"
        result = orch._extract_resource_id_from_command(command, PREFIX)
        assert result is None, (
            f"Expected None for disallowed char {candidate!r}, got {result!r}"
        )
