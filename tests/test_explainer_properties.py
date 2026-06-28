"""Property-based tests for RemediationExplainer schema completeness.

**Validates: Requirements 3.4, 1.2**

Property 4: RemediationExplainer Schema Completeness
For any combination of inputs, verify: dict has exactly 3 keys, each value is a non-empty string.

Uses Hypothesis to generate arbitrary inputs and mock the LLM client to simulate
various response scenarios: valid JSON, invalid JSON, exceptions, and edge cases.
"""

import json
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from agents.explainer import RemediationExplainer


REQUIRED_KEYS = {"risk_explanation", "what_terraform_does", "what_rollback_restores"}


def _make_mock_response(content: str) -> MagicMock:
    """Build a mock OpenAI ChatCompletion response."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = content
    return mock_resp


# --- Strategy: LLM returns valid JSON with random field values ---

@given(
    resource_id=st.text(min_size=0, max_size=100),
    finding=st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.text(max_size=50),
        max_size=5,
    ),
    remediation_hcl=st.text(min_size=0, max_size=200),
    rollback_hcl=st.text(min_size=0, max_size=200),
    risk_val=st.text(min_size=1, max_size=100),
    terraform_val=st.text(min_size=1, max_size=100),
    rollback_val=st.text(min_size=1, max_size=100),
)
@settings(max_examples=100)
def test_schema_completeness_with_valid_llm_response(
    resource_id, finding, remediation_hcl, rollback_hcl,
    risk_val, terraform_val, rollback_val,
):
    """Property 4: When LLM returns valid JSON, output always has exactly 3 keys
    with non-empty string values.

    **Validates: Requirements 3.4, 1.2**
    """
    llm_response = json.dumps({
        "risk_explanation": risk_val,
        "what_terraform_does": terraform_val,
        "what_rollback_restores": rollback_val,
    })

    with patch("agents.explainer.get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_mock_response(llm_response)

        explainer = RemediationExplainer()
        result = explainer.explain(resource_id, finding, remediation_hcl, rollback_hcl)

    # Invariant: exactly 3 keys
    assert set(result.keys()) == REQUIRED_KEYS, (
        f"Expected exactly keys {REQUIRED_KEYS}, got {set(result.keys())}"
    )
    assert len(result) == 3

    # Invariant: each value is a non-empty string
    for key in REQUIRED_KEYS:
        assert isinstance(result[key], str), f"{key} is not a string: {type(result[key])}"
        assert len(result[key]) > 0, f"{key} is empty string"


# --- Strategy: LLM raises various exceptions ---

@given(
    resource_id=st.text(min_size=0, max_size=100),
    finding=st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.text(max_size=50),
        max_size=5,
    ),
    remediation_hcl=st.text(min_size=1, max_size=200),
    rollback_hcl=st.text(min_size=1, max_size=200),
    exc_type=st.sampled_from([
        ConnectionError, TimeoutError, RuntimeError, ValueError, OSError,
        EnvironmentError, KeyError, TypeError,
    ]),
)
@settings(max_examples=80)
def test_schema_completeness_on_llm_exception(
    resource_id, finding, remediation_hcl, rollback_hcl, exc_type,
):
    """Property 4: When LLM raises any exception, output still has exactly 3 keys
    with non-empty string values (safe default).

    **Validates: Requirements 3.4, 1.2**
    """
    # Need non-whitespace HCL to actually trigger LLM path
    assume(remediation_hcl.strip() != "")
    assume(rollback_hcl.strip() != "")

    with patch("agents.explainer.get_client") as mock_get_client:
        mock_get_client.side_effect = exc_type("Simulated failure")

        explainer = RemediationExplainer()
        result = explainer.explain(resource_id, finding, remediation_hcl, rollback_hcl)

    # Invariant: exactly 3 keys
    assert set(result.keys()) == REQUIRED_KEYS, (
        f"Expected exactly keys {REQUIRED_KEYS}, got {set(result.keys())}"
    )
    assert len(result) == 3

    # Invariant: each value is a non-empty string
    for key in REQUIRED_KEYS:
        assert isinstance(result[key], str), f"{key} is not a string: {type(result[key])}"
        assert len(result[key]) > 0, f"{key} is empty string"


# --- Strategy: LLM returns invalid JSON ---

@given(
    resource_id=st.text(min_size=0, max_size=100),
    finding=st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.text(max_size=50),
        max_size=5,
    ),
    remediation_hcl=st.text(min_size=1, max_size=200),
    rollback_hcl=st.text(min_size=1, max_size=200),
    garbage=st.text(min_size=1, max_size=200),
)
@settings(max_examples=80)
def test_schema_completeness_on_invalid_json(
    resource_id, finding, remediation_hcl, rollback_hcl, garbage,
):
    """Property 4: When LLM returns unparseable garbage, output still has exactly
    3 keys with non-empty string values.

    **Validates: Requirements 3.4, 1.2**
    """
    assume(remediation_hcl.strip() != "")
    assume(rollback_hcl.strip() != "")

    # Make sure the garbage is not accidentally valid JSON with all 3 keys
    try:
        parsed = json.loads(garbage)
        if isinstance(parsed, dict) and REQUIRED_KEYS <= set(parsed.keys()):
            assume(False)  # skip this case — it's valid
    except (json.JSONDecodeError, TypeError):
        pass  # good — it's actually invalid

    with patch("agents.explainer.get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_mock_response(garbage)

        explainer = RemediationExplainer()
        result = explainer.explain(resource_id, finding, remediation_hcl, rollback_hcl)

    # Invariant: exactly 3 keys
    assert set(result.keys()) == REQUIRED_KEYS, (
        f"Expected exactly keys {REQUIRED_KEYS}, got {set(result.keys())}"
    )
    assert len(result) == 3

    # Invariant: each value is a non-empty string
    for key in REQUIRED_KEYS:
        assert isinstance(result[key], str), f"{key} is not a string: {type(result[key])}"
        assert len(result[key]) > 0, f"{key} is empty string"


# --- Strategy: Empty/whitespace HCL inputs (should still satisfy schema invariant) ---

@given(
    resource_id=st.text(min_size=0, max_size=100),
    finding=st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.text(max_size=50),
        max_size=5,
    ),
    remediation_hcl=st.from_regex(r"^\s*$", fullmatch=True),
    rollback_hcl=st.text(min_size=0, max_size=200),
)
@settings(max_examples=50)
def test_schema_completeness_with_empty_remediation_hcl(
    resource_id, finding, remediation_hcl, rollback_hcl,
):
    """Property 4: When remediation_hcl is empty/whitespace, output still has exactly
    3 keys with non-empty string values (safe default without LLM call).

    **Validates: Requirements 3.4, 1.2**
    """
    with patch("agents.explainer.get_client") as mock_get_client:
        explainer = RemediationExplainer()
        result = explainer.explain(resource_id, finding, remediation_hcl, rollback_hcl)

        # Should NOT have called LLM
        mock_get_client.assert_not_called()

    # Invariant: exactly 3 keys
    assert set(result.keys()) == REQUIRED_KEYS, (
        f"Expected exactly keys {REQUIRED_KEYS}, got {set(result.keys())}"
    )
    assert len(result) == 3

    # Invariant: each value is a non-empty string
    for key in REQUIRED_KEYS:
        assert isinstance(result[key], str), f"{key} is not a string: {type(result[key])}"
        assert len(result[key]) > 0, f"{key} is empty string"


@given(
    resource_id=st.text(min_size=0, max_size=100),
    finding=st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.text(max_size=50),
        max_size=5,
    ),
    remediation_hcl=st.text(min_size=1, max_size=200),
    rollback_hcl=st.from_regex(r"^\s*$", fullmatch=True),
)
@settings(max_examples=50)
def test_schema_completeness_with_empty_rollback_hcl(
    resource_id, finding, remediation_hcl, rollback_hcl,
):
    """Property 4: When rollback_hcl is empty/whitespace, output still has exactly
    3 keys with non-empty string values (safe default without LLM call).

    **Validates: Requirements 3.4, 1.2**
    """
    with patch("agents.explainer.get_client") as mock_get_client:
        explainer = RemediationExplainer()
        result = explainer.explain(resource_id, finding, remediation_hcl, rollback_hcl)

        # Should NOT have called LLM
        mock_get_client.assert_not_called()

    # Invariant: exactly 3 keys
    assert set(result.keys()) == REQUIRED_KEYS, (
        f"Expected exactly keys {REQUIRED_KEYS}, got {set(result.keys())}"
    )
    assert len(result) == 3

    # Invariant: each value is a non-empty string
    for key in REQUIRED_KEYS:
        assert isinstance(result[key], str), f"{key} is not a string: {type(result[key])}"
        assert len(result[key]) > 0, f"{key} is empty string"
