"""Property-based tests for ResourceTagger.

**Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.6**

Property 6: ResourceTagger Enum and Confidence Constraints
For any resource_id and resource_name input, the infer() method SHALL return
env ∈ {"production", "staging", "development", "unknown"},
risk_level ∈ {"high", "medium", "low"}, and confidence ∈ [0.0, 1.0].
If confidence is strictly below confidence_threshold, team and owner SHALL be None;
if confidence equals confidence_threshold exactly, inferred values SHALL be preserved.

Property 7: ResourceTagger Existing Tags Passthrough
For any resource where existing_tags contains env, team, or owner keys with
non-empty non-null string values, those fields in the output SHALL equal the
existing_tags values regardless of LLM inference results.
"""

import json
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st


VALID_ENVS = {"production", "staging", "development", "unknown"}
VALID_RISK_LEVELS = {"high", "medium", "low"}
EXPECTED_KEYS = {"env", "team", "owner", "risk_level", "confidence"}
CONFIDENCE_THRESHOLD = 0.7


def _make_mock_response(content: str) -> MagicMock:
    """Create a mock OpenAI chat completions response."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    return mock_response


@st.composite
def tagger_llm_json_response(draw):
    """Generate a JSON string mimicking a ResourceTagger LLM response with random values."""
    env = draw(st.sampled_from(
        ["production", "staging", "development", "unknown", "test", "qa", "", "PRODUCTION"]
    ))
    team = draw(st.one_of(
        st.text(min_size=0, max_size=30),
        st.none(),
        st.just(""),
    ))
    owner = draw(st.one_of(
        st.text(min_size=0, max_size=30),
        st.none(),
        st.just(""),
    ))
    risk_level = draw(st.sampled_from(
        ["high", "medium", "low", "critical", "none", "", "HIGH"]
    ))
    confidence = draw(st.one_of(
        st.floats(min_value=-2.0, max_value=2.0, allow_nan=False, allow_infinity=False),
        st.integers(min_value=-1, max_value=2),
        st.text(max_size=5),
    ))

    payload = {
        "env": env,
        "team": team,
        "owner": owner,
        "risk_level": risk_level,
        "confidence": confidence,
    }
    return json.dumps(payload)


@st.composite
def nonempty_string(draw):
    """Generate a non-empty, non-whitespace-only string."""
    s = draw(st.text(min_size=1, max_size=50, alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S"),
    )))
    assume(s.strip() != "")
    return s


def _assert_tagger_invariants(result: dict) -> None:
    """Assert all output invariants hold for a ResourceTagger result."""
    # Exactly 5 keys (Req 5.1)
    assert set(result.keys()) == EXPECTED_KEYS, (
        f"Expected exactly keys {EXPECTED_KEYS}, got {set(result.keys())}"
    )

    # env ∈ valid set (Req 5.2)
    assert result["env"] in VALID_ENVS, (
        f"env must be in {VALID_ENVS}, got {result['env']!r}"
    )

    # risk_level ∈ valid set
    assert result["risk_level"] in VALID_RISK_LEVELS, (
        f"risk_level must be in {VALID_RISK_LEVELS}, got {result['risk_level']!r}"
    )

    # confidence ∈ [0.0, 1.0] (Req 5.3)
    assert isinstance(result["confidence"], float), (
        f"confidence must be float, got {type(result['confidence'])}"
    )
    assert 0.0 <= result["confidence"] <= 1.0, (
        f"confidence must be in [0.0, 1.0], got {result['confidence']}"
    )

    # team is either None or a non-empty string
    assert result["team"] is None or (isinstance(result["team"], str) and result["team"].strip() != ""), (
        f"team must be None or non-empty string, got {result['team']!r}"
    )

    # owner is either None or a non-empty string
    assert result["owner"] is None or (isinstance(result["owner"], str) and result["owner"].strip() != ""), (
        f"owner must be None or non-empty string, got {result['owner']!r}"
    )


class TestResourceTaggerEnumAndConfidence:
    """Property 6: ResourceTagger Enum and Confidence Constraints.

    **Validates: Requirements 5.1, 5.2, 5.3, 5.6**

    For any input, verify:
    - env ∈ {"production", "staging", "development", "unknown"}
    - risk_level ∈ {"high", "medium", "low"}
    - confidence ∈ [0.0, 1.0]
    - Result has exactly 5 keys
    - When confidence < 0.7, team and owner MUST be None
    """

    @given(
        resource_id=st.text(min_size=1, max_size=50),
        resource_name=st.text(min_size=1, max_size=100),
        llm_response=tagger_llm_json_response(),
    )
    @settings(max_examples=200, deadline=None)
    def test_valid_json_responses_satisfy_enum_constraints(self, resource_id, resource_name, llm_response):
        """For any resource + valid JSON LLM response, output satisfies all enum/range constraints."""
        with patch("agents.tagger.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(llm_response)
            mock_get_client.return_value = mock_client

            from agents.tagger import ResourceTagger

            tagger = ResourceTagger()
            result = tagger.infer(resource_id, resource_name)

        _assert_tagger_invariants(result)

    @given(
        resource_id=st.text(min_size=1, max_size=50),
        resource_name=st.text(min_size=1, max_size=100),
        llm_response=tagger_llm_json_response(),
    )
    @settings(max_examples=200, deadline=None)
    def test_confidence_below_threshold_nullifies_team_owner(self, resource_id, resource_name, llm_response):
        """When confidence < threshold, team and owner MUST be None (Req 5.4)."""
        with patch("agents.tagger.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(llm_response)
            mock_get_client.return_value = mock_client

            from agents.tagger import ResourceTagger

            tagger = ResourceTagger()
            result = tagger.infer(resource_id, resource_name)

        if result["confidence"] < CONFIDENCE_THRESHOLD:
            assert result["team"] is None, (
                f"team must be None when confidence={result['confidence']} < {CONFIDENCE_THRESHOLD}, "
                f"got {result['team']!r}"
            )
            assert result["owner"] is None, (
                f"owner must be None when confidence={result['confidence']} < {CONFIDENCE_THRESHOLD}, "
                f"got {result['owner']!r}"
            )

    @given(
        resource_id=st.text(min_size=1, max_size=50),
        resource_name=st.text(min_size=1, max_size=100),
    )
    @settings(max_examples=200, deadline=None)
    def test_confidence_at_threshold_preserves_inferred_values(self, resource_id, resource_name):
        """When confidence == threshold exactly, team/owner should retain inferred values."""
        # Craft a response where confidence is exactly 0.7 and team/owner are set
        response_payload = json.dumps({
            "env": "production",
            "team": "platform-team",
            "owner": "infra-ops",
            "risk_level": "medium",
            "confidence": 0.7,
        })

        with patch("agents.tagger.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(response_payload)
            mock_get_client.return_value = mock_client

            from agents.tagger import ResourceTagger

            tagger = ResourceTagger()
            result = tagger.infer(resource_id, resource_name)

        _assert_tagger_invariants(result)
        # At threshold exactly, values should be preserved
        assert result["confidence"] == 0.7
        assert result["team"] == "platform-team", (
            f"team should be preserved at threshold, got {result['team']!r}"
        )
        assert result["owner"] == "infra-ops", (
            f"owner should be preserved at threshold, got {result['owner']!r}"
        )

    @given(
        resource_id=st.text(min_size=1, max_size=50),
        resource_name=st.text(min_size=1, max_size=100),
    )
    @settings(max_examples=200, deadline=None)
    def test_llm_exception_produces_valid_output(self, resource_id, resource_name):
        """When LLM raises any exception, output still satisfies all invariants."""
        with patch("agents.tagger.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = RuntimeError("API failure")
            mock_get_client.return_value = mock_client

            from agents.tagger import ResourceTagger

            tagger = ResourceTagger()
            result = tagger.infer(resource_id, resource_name)

        _assert_tagger_invariants(result)
        # On exception, should return safe defaults
        assert result["confidence"] == 0.0
        assert result["team"] is None
        assert result["owner"] is None

    @given(
        resource_id=st.text(min_size=1, max_size=50),
        resource_name=st.text(min_size=1, max_size=100),
    )
    @settings(max_examples=200, deadline=None)
    def test_get_client_exception_produces_valid_output(self, resource_id, resource_name):
        """When get_client() raises EnvironmentError, output still satisfies all invariants."""
        with patch("agents.tagger.get_client") as mock_get_client:
            mock_get_client.side_effect = EnvironmentError("OPENROUTER_API_KEY is not set")

            from agents.tagger import ResourceTagger

            tagger = ResourceTagger()
            result = tagger.infer(resource_id, resource_name)

        _assert_tagger_invariants(result)
        assert result["confidence"] == 0.0
        assert result["env"] == "unknown"

    @given(
        resource_id=st.text(min_size=1, max_size=50),
        resource_name=st.text(min_size=1, max_size=100),
        garbage=st.text(min_size=1, max_size=200),
    )
    @settings(max_examples=200, deadline=None)
    def test_invalid_json_response_produces_valid_output(self, resource_id, resource_name, garbage):
        """When LLM returns non-JSON text, output still satisfies all invariants."""
        try:
            json.loads(garbage)
            assume(False)  # skip if garbage is accidentally valid JSON
        except (json.JSONDecodeError, ValueError):
            pass

        with patch("agents.tagger.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(garbage)
            mock_get_client.return_value = mock_client

            from agents.tagger import ResourceTagger

            tagger = ResourceTagger()
            result = tagger.infer(resource_id, resource_name)

        _assert_tagger_invariants(result)


class TestResourceTaggerExistingTagsPassthrough:
    """Property 7: ResourceTagger Existing Tags Passthrough.

    **Validates: Requirements 5.4**

    For any existing_tags with non-empty non-null string values for env/team/owner,
    output preserves those values regardless of LLM response.
    """

    @given(
        resource_id=st.text(min_size=1, max_size=50),
        resource_name=st.text(min_size=1, max_size=100),
        existing_env=st.sampled_from(list(VALID_ENVS)),
        existing_team=nonempty_string(),
        existing_owner=nonempty_string(),
        llm_response=tagger_llm_json_response(),
    )
    @settings(max_examples=200, deadline=None)
    def test_all_existing_tags_preserved(
        self, resource_id, resource_name, existing_env, existing_team, existing_owner, llm_response
    ):
        """When existing_tags has env/team/owner with non-empty values, output preserves them."""
        existing_tags = {
            "env": existing_env,
            "team": existing_team,
            "owner": existing_owner,
        }

        with patch("agents.tagger.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(llm_response)
            mock_get_client.return_value = mock_client

            from agents.tagger import ResourceTagger

            tagger = ResourceTagger()
            result = tagger.infer(resource_id, resource_name, existing_tags=existing_tags)

        _assert_tagger_invariants(result)
        # Passthrough assertions
        assert result["env"] == existing_env, (
            f"env should be preserved from existing_tags: expected {existing_env!r}, got {result['env']!r}"
        )
        assert result["team"] == existing_team, (
            f"team should be preserved from existing_tags: expected {existing_team!r}, got {result['team']!r}"
        )
        assert result["owner"] == existing_owner, (
            f"owner should be preserved from existing_tags: expected {existing_owner!r}, got {result['owner']!r}"
        )

    @given(
        resource_id=st.text(min_size=1, max_size=50),
        resource_name=st.text(min_size=1, max_size=100),
        existing_team=nonempty_string(),
        existing_owner=nonempty_string(),
        llm_response=tagger_llm_json_response(),
    )
    @settings(max_examples=200, deadline=None)
    def test_partial_existing_tags_team_owner_preserved(
        self, resource_id, resource_name, existing_team, existing_owner, llm_response
    ):
        """When only team/owner present in existing_tags, those are preserved."""
        existing_tags = {
            "team": existing_team,
            "owner": existing_owner,
        }

        with patch("agents.tagger.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(llm_response)
            mock_get_client.return_value = mock_client

            from agents.tagger import ResourceTagger

            tagger = ResourceTagger()
            result = tagger.infer(resource_id, resource_name, existing_tags=existing_tags)

        _assert_tagger_invariants(result)
        assert result["team"] == existing_team, (
            f"team should be preserved: expected {existing_team!r}, got {result['team']!r}"
        )
        assert result["owner"] == existing_owner, (
            f"owner should be preserved: expected {existing_owner!r}, got {result['owner']!r}"
        )

    @given(
        resource_id=st.text(min_size=1, max_size=50),
        resource_name=st.text(min_size=1, max_size=100),
        llm_response=tagger_llm_json_response(),
    )
    @settings(max_examples=200, deadline=None)
    def test_empty_string_existing_tags_do_not_passthrough(
        self, resource_id, resource_name, llm_response
    ):
        """Empty strings in existing_tags do NOT trigger passthrough (Req 5.5)."""
        existing_tags = {
            "env": "",
            "team": "",
            "owner": "",
        }

        with patch("agents.tagger.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(llm_response)
            mock_get_client.return_value = mock_client

            from agents.tagger import ResourceTagger

            tagger = ResourceTagger()
            result = tagger.infer(resource_id, resource_name, existing_tags=existing_tags)

        _assert_tagger_invariants(result)
        # Empty strings should not passthrough — inference proceeds normally
        # The output should NOT be the empty strings
        assert result["env"] != "", "Empty string env should not passthrough"

    @given(
        resource_id=st.text(min_size=1, max_size=50),
        resource_name=st.text(min_size=1, max_size=100),
        llm_response=tagger_llm_json_response(),
    )
    @settings(max_examples=200, deadline=None)
    def test_none_existing_tags_do_not_passthrough(
        self, resource_id, resource_name, llm_response
    ):
        """None values in existing_tags do NOT trigger passthrough (Req 5.5)."""
        existing_tags = {
            "env": None,
            "team": None,
            "owner": None,
        }

        with patch("agents.tagger.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(llm_response)
            mock_get_client.return_value = mock_client

            from agents.tagger import ResourceTagger

            tagger = ResourceTagger()
            result = tagger.infer(resource_id, resource_name, existing_tags=existing_tags)

        _assert_tagger_invariants(result)
        # All output invariants still hold — None doesn't break anything


class TestResourceTaggerBatchOrder:
    """Batch order preservation tests (Req 5.6).

    **Validates: Requirements 5.6**
    """

    @given(
        num_resources=st.integers(min_value=1, max_value=25),
    )
    @settings(max_examples=50, deadline=None)
    def test_batch_preserves_input_order(self, num_resources):
        """Batch inference preserves order of inputs in outputs."""
        resources = [
            {
                "resource_id": f"res-{i}",
                "resource_name": f"resource-{i}",
                "existing_tags": {},
            }
            for i in range(num_resources)
        ]

        # Create a batch LLM response that echoes resource indices
        def make_batch_response(resources_list):
            results = []
            for i in range(len(resources_list)):
                results.append({
                    "env": "production",
                    "team": f"team-{i}",
                    "owner": f"owner-{i}",
                    "risk_level": "medium",
                    "confidence": 0.9,
                })
            return json.dumps(results)

        with patch("agents.tagger.get_client") as mock_get_client:
            mock_client = MagicMock()

            # Each chunk call returns a corresponding batch response
            def create_side_effect(*args, **kwargs):
                # Parse the prompt to count how many resources in this chunk
                prompt_content = kwargs.get("messages", [{}])[-1].get("content", "")
                # Count resource entries in the prompt
                chunk_resources = json.loads(
                    prompt_content.split("Resources:\n")[1] if "Resources:\n" in prompt_content else "[]"
                )
                return _make_mock_response(make_batch_response(chunk_resources))

            # Simpler approach: always return the right number of results per call
            call_count = [0]

            def side_effect_fn(*args, **kwargs):
                # Determine the chunk size from call sequence
                start = call_count[0] * 10
                end = min(start + 10, num_resources)
                chunk_size = end - start
                call_count[0] += 1

                results = []
                for i in range(start, end):
                    results.append({
                        "env": "staging",
                        "team": f"team-{i}",
                        "owner": f"owner-{i}",
                        "risk_level": "low",
                        "confidence": 0.85,
                    })
                return _make_mock_response(json.dumps(results))

            mock_client.chat.completions.create.side_effect = side_effect_fn
            mock_get_client.return_value = mock_client

            from agents.tagger import ResourceTagger

            tagger = ResourceTagger()
            results = tagger.infer_batch(resources)

        # Verify count matches
        assert len(results) == num_resources, (
            f"Expected {num_resources} results, got {len(results)}"
        )

        # Verify order: each result should have team-{i} and owner-{i}
        for i, result in enumerate(results):
            _assert_tagger_invariants(result)
            assert result["team"] == f"team-{i}", (
                f"Result {i} team mismatch: expected 'team-{i}', got {result['team']!r}"
            )
            assert result["owner"] == f"owner-{i}", (
                f"Result {i} owner mismatch: expected 'owner-{i}', got {result['owner']!r}"
            )

    @given(
        num_resources=st.integers(min_value=11, max_value=25),
    )
    @settings(max_examples=30, deadline=None)
    def test_batch_splits_into_chunks_of_ten(self, num_resources):
        """Batch with >10 resources makes multiple LLM calls, each chunk ≤10 (Req 5.6)."""
        import math

        resources = [
            {
                "resource_id": f"res-{i}",
                "resource_name": f"resource-{i}",
                "existing_tags": {},
            }
            for i in range(num_resources)
        ]

        expected_chunks = math.ceil(num_resources / 10)

        with patch("agents.tagger.get_client") as mock_get_client:
            mock_client = MagicMock()

            call_count = [0]

            def side_effect_fn(*args, **kwargs):
                start = call_count[0] * 10
                end = min(start + 10, num_resources)
                chunk_size = end - start
                call_count[0] += 1

                results = [{
                    "env": "development",
                    "team": f"team-{j}",
                    "owner": f"owner-{j}",
                    "risk_level": "high",
                    "confidence": 0.8,
                } for j in range(start, end)]
                return _make_mock_response(json.dumps(results))

            mock_client.chat.completions.create.side_effect = side_effect_fn
            mock_get_client.return_value = mock_client

            from agents.tagger import ResourceTagger

            tagger = ResourceTagger()
            results = tagger.infer_batch(resources)

        assert len(results) == num_resources
        assert call_count[0] == expected_chunks, (
            f"Expected {expected_chunks} LLM calls for {num_resources} resources, got {call_count[0]}"
        )


class TestResourceTaggerNegativeCases:
    """Negative tests — what the ResourceTagger should NOT do.

    Verifies that team/owner are NOT preserved when confidence < threshold,
    even if the LLM returned values for them.
    """

    @given(
        resource_id=st.text(min_size=1, max_size=50),
        resource_name=st.text(min_size=1, max_size=100),
        team_val=nonempty_string(),
        owner_val=nonempty_string(),
        low_confidence=st.floats(min_value=0.0, max_value=0.69, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200, deadline=None)
    def test_team_owner_not_preserved_when_below_threshold(
        self, resource_id, resource_name, team_val, owner_val, low_confidence
    ):
        """Negative: team/owner MUST NOT be preserved when confidence < threshold,
        even if the LLM explicitly returned them."""
        response_payload = json.dumps({
            "env": "production",
            "team": team_val,
            "owner": owner_val,
            "risk_level": "high",
            "confidence": low_confidence,
        })

        with patch("agents.tagger.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(response_payload)
            mock_get_client.return_value = mock_client

            from agents.tagger import ResourceTagger

            tagger = ResourceTagger()
            result = tagger.infer(resource_id, resource_name)

        # team and owner MUST be None when confidence < threshold
        assert result["team"] is None, (
            f"team should be None when confidence={low_confidence} < {CONFIDENCE_THRESHOLD}, "
            f"got {result['team']!r}"
        )
        assert result["owner"] is None, (
            f"owner should be None when confidence={low_confidence} < {CONFIDENCE_THRESHOLD}, "
            f"got {result['owner']!r}"
        )

    @given(
        resource_id=st.text(min_size=1, max_size=50),
        resource_name=st.text(min_size=1, max_size=100),
    )
    @settings(max_examples=100, deadline=None)
    def test_safe_defaults_on_exception(self, resource_id, resource_name):
        """On any exception, returns safe defaults with specific known values."""
        with patch("agents.tagger.get_client") as mock_get_client:
            mock_get_client.side_effect = EnvironmentError("OPENROUTER_API_KEY is not set")

            from agents.tagger import ResourceTagger

            tagger = ResourceTagger()
            result = tagger.infer(resource_id, resource_name)

        # Verify exact safe default values
        assert result == {
            "env": "unknown",
            "team": None,
            "owner": None,
            "risk_level": "low",
            "confidence": 0.0,
        }, f"Expected SAFE_DEFAULT on exception, got {result}"
