"""Property-based tests for QueryInterpreter output validity.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.7**

Property 3: QueryInterpreter Output Validity
For any string input, verify:
- confidence ∈ [0.0, 1.0]
- resource_types items ∈ valid set
- check_types items ∈ valid set
- min_idle_days ≥ 0
- intent_summary is non-empty string
- exactly 5 keys returned
"""

import json
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st


VALID_RESOURCE_TYPES = {"elasticache", "ebs", "ec2"}
VALID_CHECK_TYPES = {"security_group", "encryption", "public_access"}
EXPECTED_KEYS = {"resource_types", "check_types", "min_idle_days", "intent_summary", "confidence"}


def _make_mock_response(content: str) -> MagicMock:
    """Create a mock OpenAI chat completions response."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    return mock_response


# Strategy: generate valid-looking LLM JSON responses with random field values
@st.composite
def valid_llm_json_response(draw):
    """Generate a JSON string that looks like a valid LLM response with random values."""
    resource_types = draw(st.lists(
        st.sampled_from(["elasticache", "ebs", "ec2", "s3", "rds", "lambda", ""]),
        max_size=5,
    ))
    check_types = draw(st.lists(
        st.sampled_from(["security_group", "encryption", "public_access", "iam_policy", "network", ""]),
        max_size=5,
    ))
    min_idle_days = draw(st.one_of(
        st.integers(min_value=-1000, max_value=100000),
        st.floats(min_value=-100, max_value=100000, allow_nan=False, allow_infinity=False),
        st.text(max_size=10),
    ))
    intent_summary = draw(st.one_of(
        st.text(min_size=0, max_size=500),
        st.integers(),
        st.none(),
    ))
    confidence = draw(st.one_of(
        st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False),
        st.integers(min_value=-5, max_value=5),
        st.text(max_size=5),
    ))

    payload = {
        "resource_types": resource_types,
        "check_types": check_types,
        "min_idle_days": min_idle_days,
        "intent_summary": intent_summary,
        "confidence": confidence,
    }
    return json.dumps(payload)


def _assert_output_invariants(result: dict) -> None:
    """Assert all output invariants hold for a QueryInterpreter result."""
    # Exactly 5 keys
    assert set(result.keys()) == EXPECTED_KEYS, (
        f"Expected exactly keys {EXPECTED_KEYS}, got {set(result.keys())}"
    )

    # confidence ∈ [0.0, 1.0]
    assert isinstance(result["confidence"], float), (
        f"confidence must be float, got {type(result['confidence'])}"
    )
    assert 0.0 <= result["confidence"] <= 1.0, (
        f"confidence must be in [0.0, 1.0], got {result['confidence']}"
    )

    # resource_types is a list with all items in valid set
    assert isinstance(result["resource_types"], list), (
        f"resource_types must be list, got {type(result['resource_types'])}"
    )
    for item in result["resource_types"]:
        assert item in VALID_RESOURCE_TYPES, (
            f"resource_types item '{item}' not in {VALID_RESOURCE_TYPES}"
        )

    # check_types is a list with all items in valid set
    assert isinstance(result["check_types"], list), (
        f"check_types must be list, got {type(result['check_types'])}"
    )
    for item in result["check_types"]:
        assert item in VALID_CHECK_TYPES, (
            f"check_types item '{item}' not in {VALID_CHECK_TYPES}"
        )

    # min_idle_days is an int >= 0
    assert isinstance(result["min_idle_days"], int), (
        f"min_idle_days must be int, got {type(result['min_idle_days'])}"
    )
    assert result["min_idle_days"] >= 0, (
        f"min_idle_days must be >= 0, got {result['min_idle_days']}"
    )

    # intent_summary is a non-empty string
    assert isinstance(result["intent_summary"], str), (
        f"intent_summary must be str, got {type(result['intent_summary'])}"
    )
    assert len(result["intent_summary"]) >= 1, (
        "intent_summary must be non-empty (len >= 1)"
    )


class TestQueryInterpreterOutputValidity:
    """Property 3: QueryInterpreter Output Validity.

    **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.7**
    """

    @given(query=st.text(), llm_response=valid_llm_json_response())
    @settings(max_examples=200, deadline=None)
    def test_valid_json_responses_produce_valid_output(self, query, llm_response):
        """For any query + valid JSON LLM response, output satisfies all invariants."""
        assume(query.strip() != "")  # Non-empty queries go to LLM path

        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(llm_response)
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            result = qi.interpret(query)

        _assert_output_invariants(result)

    @given(query=st.text())
    @settings(max_examples=200, deadline=None)
    def test_llm_exception_produces_valid_output(self, query):
        """When LLM raises any exception, output still satisfies all invariants."""
        assume(query.strip() != "")  # Non-empty queries attempt LLM call

        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = RuntimeError("API failure")
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            result = qi.interpret(query)

        _assert_output_invariants(result)

    @given(query=st.text(), garbage=st.text())
    @settings(max_examples=200, deadline=None)
    def test_invalid_json_response_produces_valid_output(self, query, garbage):
        """When LLM returns non-JSON text, output still satisfies all invariants."""
        assume(query.strip() != "")
        # Ensure the garbage isn't accidentally valid JSON
        try:
            json.loads(garbage)
            assume(False)  # skip if garbage happens to be valid JSON
        except (json.JSONDecodeError, ValueError):
            pass

        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(garbage)
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            result = qi.interpret(query)

        _assert_output_invariants(result)

    @given(query=st.text())
    @settings(max_examples=200, deadline=None)
    def test_get_client_exception_produces_valid_output(self, query):
        """When get_client() itself raises, output still satisfies all invariants."""
        assume(query.strip() != "")

        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_get_client.side_effect = EnvironmentError("OPENROUTER_API_KEY is not set")

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            result = qi.interpret(query)

        _assert_output_invariants(result)

    @given(query=st.from_regex(r"\s*", fullmatch=True))
    @settings(max_examples=100, deadline=None)
    def test_empty_whitespace_queries_produce_valid_output(self, query):
        """Empty/whitespace-only queries still produce valid output schema."""
        with patch("agents.query_interpreter.get_client") as mock_get_client:
            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            result = qi.interpret(query)

            # LLM should NOT be called for empty queries
            mock_get_client.assert_not_called()

        _assert_output_invariants(result)
