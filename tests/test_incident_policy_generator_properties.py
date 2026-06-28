"""Property-based tests for IncidentPolicyGenerator.

**Validates: Requirements 7.1, 7.2, 7.4, 7.5, 7.6, 7.7**

Property 10: IncidentPolicyGenerator Idempotency
Calling generate() twice with the same text returns the same result without a
second LLM call. The second call should find existing policies via incident_hash
and not invoke the LLM.

Property 11: IncidentPolicyGenerator File Consistency
For successful generation, files exist at policies/{policy_id}.json matching
the returned dicts. On failure (LLM exception), no new files should be created
in the policies directory.

Property 12: IncidentPolicyGenerator Schema and Bounds
For valid input, returns 3-5 dicts with correct schema (all required keys
present, valid enums for check_type and resource_types).

Property 13: IncidentPolicyGenerator Input Validation
Whitespace-only strings return []. Strings > 2000 chars are truncated before
LLM call (hash uses original).
"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

os.environ.setdefault("OPENROUTER_API_KEY", "test-key-for-testing")

from agents.incident_policy_generator import (
    IncidentPolicyGenerator,
    VALID_CHECK_TYPES,
    VALID_RESOURCE_TYPES,
    POLICY_ID_PATTERN,
    MAX_DESCRIPTION_LENGTH,
    MIN_POLICIES,
    MAX_POLICIES,
)

# ---------------------------------------------------------------------------
# Helper strategies — kept small to avoid Hypothesis health check failures
# ---------------------------------------------------------------------------

# Fixed pool of valid policy IDs to avoid complex generation
FIXED_POLICY_IDS = [
    "policy-sg-redis",
    "policy-encrypt-ebs",
    "policy-public-ec2",
    "policy-idle-cache",
    "policy-check-ports",
    "rule-sg-open",
    "check-encrypt-vol",
    "scan-public-access",
]


@st.composite
def valid_policy_list_strategy(draw, count=None):
    """Generate a list of valid policy dicts with unique policy_ids (small)."""
    if count is None:
        count = draw(st.integers(min_value=MIN_POLICIES, max_value=MAX_POLICIES))

    ids = draw(st.permutations(FIXED_POLICY_IDS).map(lambda x: list(x[:count])))
    assume(len(ids) == count)

    policies = []
    for pid in ids:
        resource_types = draw(st.lists(
            st.sampled_from(sorted(VALID_RESOURCE_TYPES)),
            min_size=1,
            max_size=3,
            unique=True,
        ))
        check_type = draw(st.sampled_from(sorted(VALID_CHECK_TYPES)))
        policies.append({
            "policy_id": pid,
            "policy_name": f"Policy for {pid}",
            "resource_types": resource_types,
            "check_type": check_type,
            "check_logic_description": f"Check logic for {pid}",
            "rationale": f"Rationale for {pid}",
            "query": f"Find resources related to {pid}",
        })
    return policies


# Simple incident description strategy using sampled_from for speed
INCIDENT_PREFIXES = [
    "A Redis cluster was compromised via open port",
    "Unencrypted EBS volumes exposed sensitive data",
    "EC2 instances had public access enabled",
    "ElastiCache was idle for months costing money",
    "Security group allowed unrestricted ingress",
]


@st.composite
def incident_description_strategy(draw):
    """Generate a valid non-empty incident description (simple, fast)."""
    prefix = draw(st.sampled_from(INCIDENT_PREFIXES))
    suffix = draw(st.text(min_size=1, max_size=30, alphabet="abcdefghijklmnopqrstuvwxyz "))
    return f"{prefix} {suffix}".strip()


def _make_mock_response(policies: list[dict]) -> MagicMock:
    """Create a mock LLM response with policies as JSON content."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(policies)
    return mock_response


# ---------------------------------------------------------------------------
# Property 10: Idempotency
# ---------------------------------------------------------------------------


class TestIncidentPolicyGeneratorIdempotency:
    """Property 10: IncidentPolicyGenerator Idempotency.

    **Validates: Requirements 7.6**

    Calling generate() twice with the same text returns the same result without
    a second LLM call. The second call should find existing policies via
    incident_hash and not invoke the LLM.
    """

    @given(
        description=incident_description_strategy(),
        policies=valid_policy_list_strategy(),
    )
    @settings(max_examples=200, deadline=None)
    def test_second_call_skips_llm(self, description, policies):
        """generate() called twice with same input: second call must NOT invoke LLM."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            mock_response = _make_mock_response(policies)
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result1 = gen.generate(description)
                assume(len(result1) > 0)

                # Reset mock to track second call
                mock_client.chat.completions.create.reset_mock()

                result2 = gen.generate(description)

            # Second call should NOT invoke LLM
            mock_client.chat.completions.create.assert_not_called()

            # Both calls return same policies (same content)
            assert len(result2) == len(result1)
            result1_ids = {p["policy_id"] for p in result1}
            result2_ids = {p["policy_id"] for p in result2}
            assert result1_ids == result2_ids

    @given(
        description=incident_description_strategy(),
        policies=valid_policy_list_strategy(),
    )
    @settings(max_examples=200, deadline=None)
    def test_idempotency_preserves_incident_hash(self, description, policies):
        """Both calls must produce policies with the same incident_hash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            expected_hash = hashlib.sha256(description.encode("utf-8")).hexdigest()[:8]

            mock_response = _make_mock_response(policies)
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result1 = gen.generate(description)
                assume(len(result1) > 0)
                result2 = gen.generate(description)

            for p in result1:
                assert p["incident_hash"] == expected_hash
            for p in result2:
                assert p["incident_hash"] == expected_hash


# ---------------------------------------------------------------------------
# Property 11: File Consistency
# ---------------------------------------------------------------------------


class TestIncidentPolicyGeneratorFileConsistency:
    """Property 11: IncidentPolicyGenerator File Consistency.

    **Validates: Requirements 7.2**

    For successful generation, files exist at policies/{policy_id}.json
    matching the returned dicts. On failure (LLM exception), no new files
    should be created in the policies directory.
    """

    @given(
        description=incident_description_strategy(),
        policies=valid_policy_list_strategy(),
    )
    @settings(max_examples=200, deadline=None)
    def test_successful_generation_writes_matching_files(self, description, policies):
        """Every returned policy must have a corresponding file on disk with matching content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            mock_response = _make_mock_response(policies)
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate(description)

            assume(len(result) > 0)

            for policy in result:
                file_path = policies_dir / f"{policy['policy_id']}.json"
                assert file_path.exists(), (
                    f"File for policy_id={policy['policy_id']} must exist on disk"
                )
                on_disk = json.loads(file_path.read_text(encoding="utf-8"))
                assert on_disk == policy, (
                    f"File content must match returned policy for {policy['policy_id']}"
                )

    @given(
        description=incident_description_strategy(),
    )
    @settings(max_examples=200, deadline=None)
    def test_llm_failure_creates_no_files(self, description):
        """On LLM exception, no new files should exist in the policies directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = RuntimeError("LLM unavailable")

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate(description)

            assert result == []
            if policies_dir.exists():
                files = list(policies_dir.glob("*.json"))
                assert len(files) == 0, (
                    f"No files should be created on LLM failure, found {len(files)}"
                )

    @given(
        description=incident_description_strategy(),
        policies=valid_policy_list_strategy(),
    )
    @settings(max_examples=200, deadline=None)
    def test_file_count_matches_result_count(self, description, policies):
        """Number of files on disk must equal number of returned policies."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            mock_response = _make_mock_response(policies)
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate(description)

            assume(len(result) > 0)

            files = list(policies_dir.glob("*.json"))
            assert len(files) == len(result), (
                f"Expected {len(result)} files, found {len(files)}"
            )


# ---------------------------------------------------------------------------
# Property 12: Schema and Bounds
# ---------------------------------------------------------------------------


class TestIncidentPolicyGeneratorSchemaAndBounds:
    """Property 12: IncidentPolicyGenerator Schema and Bounds.

    **Validates: Requirements 7.1, 7.7**

    For valid input, returns 3-5 dicts with correct schema (all required keys
    present, valid enums for check_type and resource_types). Each policy has
    exactly the required keys; check_type in valid set; resource_types items in
    valid set and non-empty; version == 1; policy_id matches pattern;
    incident_hash is 8 hex chars.
    """

    REQUIRED_KEYS = {
        "policy_id",
        "policy_name",
        "resource_types",
        "check_type",
        "check_logic_description",
        "rationale",
        "query",
        "generated_at",
        "incident_hash",
        "version",
    }

    @given(
        description=incident_description_strategy(),
        policies=valid_policy_list_strategy(),
    )
    @settings(max_examples=200, deadline=None)
    def test_output_has_exactly_required_keys(self, description, policies):
        """Each returned policy has exactly the 10 required keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            mock_response = _make_mock_response(policies)
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate(description)

            assume(len(result) > 0)

            for policy in result:
                assert set(policy.keys()) == self.REQUIRED_KEYS, (
                    f"Expected keys {self.REQUIRED_KEYS}, got {set(policy.keys())}"
                )

    @given(
        description=incident_description_strategy(),
        policies=valid_policy_list_strategy(),
    )
    @settings(max_examples=200, deadline=None)
    def test_output_count_between_min_and_max(self, description, policies):
        """Output length must be between MIN_POLICIES and MAX_POLICIES (3-5)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            mock_response = _make_mock_response(policies)
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate(description)

            assume(len(result) > 0)
            assert MIN_POLICIES <= len(result) <= MAX_POLICIES, (
                f"Expected {MIN_POLICIES}-{MAX_POLICIES} policies, got {len(result)}"
            )

    @given(
        description=incident_description_strategy(),
        policies=valid_policy_list_strategy(),
    )
    @settings(max_examples=200, deadline=None)
    def test_check_type_is_valid_enum(self, description, policies):
        """check_type must be one of the valid set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            mock_response = _make_mock_response(policies)
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate(description)

            assume(len(result) > 0)
            for policy in result:
                assert policy["check_type"] in VALID_CHECK_TYPES, (
                    f"check_type must be in {VALID_CHECK_TYPES}, got {policy['check_type']!r}"
                )

    @given(
        description=incident_description_strategy(),
        policies=valid_policy_list_strategy(),
    )
    @settings(max_examples=200, deadline=None)
    def test_resource_types_valid_and_nonempty(self, description, policies):
        """resource_types must be non-empty and contain only valid items."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            mock_response = _make_mock_response(policies)
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate(description)

            assume(len(result) > 0)
            for policy in result:
                assert isinstance(policy["resource_types"], list)
                assert len(policy["resource_types"]) > 0, "resource_types must be non-empty"
                for rt in policy["resource_types"]:
                    assert rt in VALID_RESOURCE_TYPES, (
                        f"resource_type must be in {VALID_RESOURCE_TYPES}, got {rt!r}"
                    )

    @given(
        description=incident_description_strategy(),
        policies=valid_policy_list_strategy(),
    )
    @settings(max_examples=200, deadline=None)
    def test_version_is_1(self, description, policies):
        """version must always be 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            mock_response = _make_mock_response(policies)
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate(description)

            assume(len(result) > 0)
            for policy in result:
                assert policy["version"] == 1, f"version must be 1, got {policy['version']}"

    @given(
        description=incident_description_strategy(),
        policies=valid_policy_list_strategy(),
    )
    @settings(max_examples=200, deadline=None)
    def test_policy_id_matches_pattern(self, description, policies):
        """policy_id must match ^[a-z0-9\\-]+$."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            mock_response = _make_mock_response(policies)
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate(description)

            assume(len(result) > 0)
            for policy in result:
                assert POLICY_ID_PATTERN.match(policy["policy_id"]), (
                    f"policy_id must match pattern, got {policy['policy_id']!r}"
                )

    @given(
        description=incident_description_strategy(),
        policies=valid_policy_list_strategy(),
    )
    @settings(max_examples=200, deadline=None)
    def test_incident_hash_is_8_hex_chars(self, description, policies):
        """incident_hash must be exactly 8 hexadecimal characters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            expected_hash = hashlib.sha256(description.encode("utf-8")).hexdigest()[:8]

            mock_response = _make_mock_response(policies)
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate(description)

            assume(len(result) > 0)
            hex_pattern = re.compile(r"^[0-9a-f]{8}$")
            for policy in result:
                assert hex_pattern.match(policy["incident_hash"]), (
                    f"incident_hash must be 8 hex chars, got {policy['incident_hash']!r}"
                )
                assert policy["incident_hash"] == expected_hash, (
                    "incident_hash must equal sha256[:8] of original description"
                )


# ---------------------------------------------------------------------------
# Property 13: Input Validation
# ---------------------------------------------------------------------------


class TestIncidentPolicyGeneratorInputValidation:
    """Property 13: IncidentPolicyGenerator Input Validation.

    **Validates: Requirements 7.4, 7.5**

    Whitespace-only strings return []. Strings > 2000 chars are truncated
    before LLM call (hash uses original).
    """

    @given(
        whitespace=st.sampled_from([
            "", " ", "  ", "\t", "\n", "\r\n", "   \t  \n  ",
            "\t\t\t", "\n\n\n", "  \r\n  \t  ",
        ]),
    )
    @settings(max_examples=200, deadline=None)
    def test_whitespace_only_returns_empty_list(self, whitespace):
        """Whitespace-only or empty strings must return [] without calling LLM."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            with patch("agents.incident_policy_generator.get_client") as mock_get_client:
                result = gen.generate(whitespace)

            assert result == [], (
                f"Whitespace-only input must return [], got {result}"
            )
            # LLM should NOT be called
            mock_get_client.assert_not_called()

    @given(
        extra_length=st.integers(min_value=1, max_value=2000),
        policies=valid_policy_list_strategy(),
    )
    @settings(max_examples=200, deadline=None)
    def test_long_strings_truncated_before_llm(self, extra_length, policies):
        """Strings > 2000 chars are truncated before LLM call; hash uses original."""
        # Build a string longer than 2000 chars
        long_desc = "Incident: " + "X" * (MAX_DESCRIPTION_LENGTH + extra_length)
        assert len(long_desc) > MAX_DESCRIPTION_LENGTH

        expected_hash = hashlib.sha256(long_desc.encode("utf-8")).hexdigest()[:8]

        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            mock_response = _make_mock_response(policies)
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate(long_desc)

            assume(len(result) > 0)

            # Hash must be computed from the ORIGINAL un-truncated description
            for p in result:
                assert p["incident_hash"] == expected_hash, (
                    "incident_hash must use original un-truncated description"
                )

            # Verify that the LLM received at most 2000 chars of the description
            call_args = mock_client.chat.completions.create.call_args
            prompt_content = call_args[1]["messages"][1]["content"]
            # The full long_desc should NOT appear in the prompt
            assert long_desc not in prompt_content, (
                "Full long description must not be sent to LLM"
            )

    @given(
        description=incident_description_strategy(),
    )
    @settings(max_examples=200, deadline=None)
    def test_none_input_returns_empty_list(self, description):
        """None input must return [] without crashing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            result = gen.generate(None)
            assert result == [], "None input must return []"


# ---------------------------------------------------------------------------
# Negative Cases
# ---------------------------------------------------------------------------


class TestIncidentPolicyGeneratorNegativeCases:
    """Negative tests — what IncidentPolicyGenerator should NOT do.

    Validates that invalid LLM output is properly filtered and errors are handled.
    """

    @given(
        description=incident_description_strategy(),
    )
    @settings(max_examples=200, deadline=None)
    def test_invalid_json_from_llm_returns_empty(self, description):
        """Non-JSON LLM output must return [] without raising."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "not valid json {"

            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate(description)

            assert result == [], "Invalid JSON from LLM must return []"

    @given(
        description=incident_description_strategy(),
    )
    @settings(max_examples=200, deadline=None)
    def test_get_client_failure_returns_empty(self, description):
        """When get_client() raises, output is [] without raising."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            with patch(
                "agents.incident_policy_generator.get_client",
                side_effect=EnvironmentError("OPENROUTER_API_KEY is not set"),
            ):
                result = gen.generate(description)

            assert result == [], "get_client failure must return []"
