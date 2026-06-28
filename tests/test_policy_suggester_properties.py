"""Property-based tests for PolicySuggester output bounds and exclusion.

**Validates: Requirements 4.1, 4.2, 4.3, 4.4**

Uses Hypothesis to verify that for ANY combination of findings and
already_checked inputs, the PolicySuggester output satisfies:
- 0-5 dicts returned
- Each dict has exactly 5 required keys
- Each priority is one of {"high", "medium", "low"}
- Each suggestion_id is a non-empty string
- Each title is a non-empty string with len <= 80
- Each rationale is a non-empty string with len <= 200
- Each query is a non-empty string
- No suggestion's check_type (inferred or explicit) appears in already_checked
"""

import json
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from agents.policy_suggester import PolicySuggester, KNOWN_CHECK_TYPES, VALID_PRIORITIES


# ──────────────────────────────────────────────────────────────────────
# Strategies
# ──────────────────────────────────────────────────────────────────────

# Findings: lists of arbitrary dicts (the PolicySuggester handles any shape)
finding_strategy = st.fixed_dictionaries({
    "id": st.text(min_size=1, max_size=10),
    "resource_id": st.text(min_size=1, max_size=30),
    "severity": st.sampled_from(["LOW", "MEDIUM", "HIGH", "CRITICAL"]),
    "check_type": st.sampled_from(KNOWN_CHECK_TYPES),
})

findings_strategy = st.lists(finding_strategy, min_size=0, max_size=5)

# already_checked: subset of known check types
already_checked_strategy = st.lists(
    st.sampled_from(KNOWN_CHECK_TYPES),
    min_size=0,
    max_size=4,
    unique=True,
)

# Valid suggestion for mock LLM responses — includes explicit check_type
valid_suggestion_strategy = st.fixed_dictionaries({
    "suggestion_id": st.text(
        min_size=1, max_size=30,
        alphabet=st.characters(whitelist_categories=("Ll", "Nd"), whitelist_characters="-_"),
    ),
    "title": st.text(min_size=1, max_size=80),
    "rationale": st.text(min_size=1, max_size=200),
    "query": st.text(min_size=1, max_size=100),
    "priority": st.sampled_from(["high", "medium", "low"]),
    "check_type": st.sampled_from(KNOWN_CHECK_TYPES),
})

# Generate between 0 and 8 suggestions (to test the cap at 5)
mock_suggestions_strategy = st.lists(valid_suggestion_strategy, min_size=0, max_size=8)

# Strategy for simulating LLM failure modes
llm_failure_strategy = st.sampled_from([
    "connection_error",
    "invalid_json",
    "returns_dict_not_list",
    "returns_null",
    "environment_error",
])


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

REQUIRED_KEYS = {"suggestion_id", "title", "rationale", "query", "priority"}


def _build_mock_response(content: str) -> MagicMock:
    """Build a mock OpenAI response with given content string."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    return mock_response


def _assert_schema_invariants(result: list[dict], already_checked: list[str]) -> None:
    """Assert all schema invariants on the result list."""
    # Invariant 1: Result is a list with 0-5 items
    assert isinstance(result, list), f"Result must be a list, got {type(result)}"
    assert len(result) <= 5, f"Result must have at most 5 items, got {len(result)}"

    for i, item in enumerate(result):
        # Invariant 2: Each item is a dict with exactly 5 required keys
        assert isinstance(item, dict), f"Item {i} must be a dict, got {type(item)}"
        assert set(item.keys()) == REQUIRED_KEYS, (
            f"Item {i} must have exactly keys {REQUIRED_KEYS}, got {set(item.keys())}"
        )

        # Invariant 3: priority is valid
        assert item["priority"] in VALID_PRIORITIES, (
            f"Item {i} priority must be in {VALID_PRIORITIES}, got {item['priority']!r}"
        )

        # Invariant 4: suggestion_id is a non-empty string
        assert isinstance(item["suggestion_id"], str), (
            f"Item {i} suggestion_id must be a string"
        )
        assert len(item["suggestion_id"].strip()) > 0, (
            f"Item {i} suggestion_id must be non-empty"
        )

        # Invariant 5: title is a non-empty string with len <= 80
        assert isinstance(item["title"], str), f"Item {i} title must be a string"
        assert len(item["title"].strip()) > 0, f"Item {i} title must be non-empty"
        assert len(item["title"]) <= 80, (
            f"Item {i} title must be at most 80 chars, got {len(item['title'])}"
        )

        # Invariant 6: rationale is a non-empty string with len <= 200
        assert isinstance(item["rationale"], str), f"Item {i} rationale must be a string"
        assert len(item["rationale"].strip()) > 0, f"Item {i} rationale must be non-empty"
        assert len(item["rationale"]) <= 200, (
            f"Item {i} rationale must be at most 200 chars, got {len(item['rationale'])}"
        )

        # Invariant 7: query is a non-empty string
        assert isinstance(item["query"], str), f"Item {i} query must be a string"
        assert len(item["query"].strip()) > 0, f"Item {i} query must be non-empty"


def _assert_exclusion_invariant(result: list[dict], already_checked: list[str]) -> None:
    """Assert the exclusion property: no suggestion references a check_type in already_checked.

    We test this by re-running the PolicySuggester's own _infer_check_type logic
    on each returned suggestion. If the inferred check_type is in already_checked,
    the suggestion should NOT be present.
    """
    if not already_checked:
        return  # No exclusion to verify when already_checked is empty

    suggester = PolicySuggester()
    already_set = set(already_checked)

    for i, item in enumerate(result):
        # Reconstruct what the suggester would infer from this output
        # The output dict has only 5 keys (no check_type), but we can still
        # check if the content-based inference would match already_checked
        # by building a dict with the output fields
        infer_dict = {
            "suggestion_id": item["suggestion_id"],
            "title": item["title"],
            "query": item["query"],
        }
        inferred = suggester._infer_check_type(infer_dict)
        if inferred is not None:
            assert inferred not in already_set, (
                f"Item {i} has inferred check_type '{inferred}' which is in "
                f"already_checked {already_checked}. Suggestion should have been filtered."
            )


# ──────────────────────────────────────────────────────────────────────
# Property Test: Valid LLM Response
# ──────────────────────────────────────────────────────────────────────


@settings(max_examples=50, deadline=None)
@given(
    findings=findings_strategy,
    already_checked=already_checked_strategy,
    mock_suggestions=mock_suggestions_strategy,
)
def test_valid_llm_response_satisfies_all_invariants(
    findings: list[dict],
    already_checked: list[str],
    mock_suggestions: list[dict],
):
    """Property 5: For any valid LLM response, output satisfies all schema and exclusion invariants.

    **Validates: Requirements 4.1, 4.2, 4.3, 4.4**
    """
    suggester = PolicySuggester()
    mock_response = _build_mock_response(json.dumps(mock_suggestions))

    with patch("agents.policy_suggester.get_client") as mock_get:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_get.return_value = mock_client

        result = suggester.suggest(findings, already_checked)

    _assert_schema_invariants(result, already_checked)
    _assert_exclusion_invariant(result, already_checked)


# ──────────────────────────────────────────────────────────────────────
# Property Test: LLM Failure Modes
# ──────────────────────────────────────────────────────────────────────


@settings(max_examples=50, deadline=None)
@given(
    findings=findings_strategy,
    already_checked=already_checked_strategy,
    failure_mode=llm_failure_strategy,
)
def test_llm_failure_satisfies_all_invariants(
    findings: list[dict],
    already_checked: list[str],
    failure_mode: str,
):
    """Property 5: On any LLM failure, output is still a valid (possibly empty) list satisfying invariants.

    **Validates: Requirements 4.1, 4.2, 4.3, 4.4**
    """
    suggester = PolicySuggester()

    with patch("agents.policy_suggester.get_client") as mock_get:
        if failure_mode == "connection_error":
            mock_get.side_effect = ConnectionError("Network unreachable")
        elif failure_mode == "environment_error":
            mock_get.side_effect = EnvironmentError("OPENROUTER_API_KEY is not set")
        elif failure_mode == "invalid_json":
            mock_response = _build_mock_response("this is not valid json {{[")
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client
        elif failure_mode == "returns_dict_not_list":
            mock_response = _build_mock_response(json.dumps({"not": "a list"}))
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client
        elif failure_mode == "returns_null":
            mock_response = _build_mock_response("null")
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

        result = suggester.suggest(findings, already_checked)

    # On failure with non-empty findings, we expect []
    # On failure with empty findings, the fallback defaults may still return results
    _assert_schema_invariants(result, already_checked)
    _assert_exclusion_invariant(result, already_checked)


# ──────────────────────────────────────────────────────────────────────
# Property Test: Empty Findings (Default Suggestions Path)
# ──────────────────────────────────────────────────────────────────────


@settings(max_examples=50, deadline=None)
@given(
    already_checked=already_checked_strategy,
    mock_suggestions=mock_suggestions_strategy,
)
def test_empty_findings_satisfies_all_invariants(
    already_checked: list[str],
    mock_suggestions: list[dict],
):
    """Property 5: When findings is empty, defaults or LLM suggestions still satisfy all invariants.

    **Validates: Requirements 4.1, 4.2, 4.3, 4.4**
    """
    suggester = PolicySuggester()
    mock_response = _build_mock_response(json.dumps(mock_suggestions))

    with patch("agents.policy_suggester.get_client") as mock_get:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_get.return_value = mock_client

        result = suggester.suggest([], already_checked)

    _assert_schema_invariants(result, already_checked)
    _assert_exclusion_invariant(result, already_checked)


# ──────────────────────────────────────────────────────────────────────
# Property Test: Empty Findings with LLM Failure (Hardcoded Defaults)
# ──────────────────────────────────────────────────────────────────────


@settings(max_examples=50, deadline=None)
@given(
    already_checked=already_checked_strategy,
)
def test_empty_findings_llm_failure_uses_defaults_satisfying_invariants(
    already_checked: list[str],
):
    """Property 5: When findings is empty and LLM fails, hardcoded defaults satisfy all invariants.

    **Validates: Requirements 4.1, 4.2, 4.3, 4.4**
    """
    suggester = PolicySuggester()

    with patch("agents.policy_suggester.get_client") as mock_get:
        mock_get.side_effect = Exception("API unavailable")

        result = suggester.suggest([], already_checked)

    _assert_schema_invariants(result, already_checked)
    _assert_exclusion_invariant(result, already_checked)


# ──────────────────────────────────────────────────────────────────────
# Property Test: Exclusion Enforcement with Explicit check_type
# ──────────────────────────────────────────────────────────────────────


@settings(max_examples=50, deadline=None)
@given(
    findings=findings_strategy,
    already_checked=st.lists(
        st.sampled_from(KNOWN_CHECK_TYPES),
        min_size=1,
        max_size=4,
        unique=True,
    ),
    mock_suggestions=st.lists(valid_suggestion_strategy, min_size=1, max_size=8),
)
def test_exclusion_property_with_explicit_check_types(
    findings: list[dict],
    already_checked: list[str],
    mock_suggestions: list[dict],
):
    """Property 5: Suggestions with check_type in already_checked are NEVER in the output.

    This test specifically verifies that the post-processing filter removes
    suggestions whose explicit check_type field matches already_checked entries.

    **Validates: Requirements 4.1, 4.2, 4.3, 4.4**
    """
    suggester = PolicySuggester()
    mock_response = _build_mock_response(json.dumps(mock_suggestions))

    with patch("agents.policy_suggester.get_client") as mock_get:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_get.return_value = mock_client

        result = suggester.suggest(findings, already_checked)

    _assert_schema_invariants(result, already_checked)
    _assert_exclusion_invariant(result, already_checked)

    # Additionally verify: none of the returned suggestions could have
    # had an explicit check_type from already_checked. The internal filter
    # uses _infer_check_type which checks the explicit field AND content.
    # Since the output only has 5 keys (no check_type), we verify via inference.
    already_set = set(already_checked)
    for item in result:
        # Build a dict that mimics what _infer_check_type would see
        # before the output was stripped to 5 keys
        for original in mock_suggestions:
            if original["suggestion_id"] == item["suggestion_id"]:
                # If this suggestion had an explicit check_type in already_checked,
                # it should NOT be in the result
                explicit_ct = original.get("check_type", "")
                if explicit_ct in already_set:
                    assert False, (
                        f"Suggestion '{item['suggestion_id']}' with explicit "
                        f"check_type '{explicit_ct}' should have been filtered "
                        f"by already_checked={already_checked}"
                    )
