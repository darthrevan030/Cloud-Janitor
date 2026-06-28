"""Unit tests for IncidentPolicyGenerator.

Tests validate:
- Input validation (empty/whitespace → [], truncation >2000 chars)
- Policy generation with mocked LLM
- Idempotency (same hash returns existing without LLM call)
- Security: unsafe policy_id rejection (path traversal)
- Graceful failure: LLM errors → [] with no partial files
- Schema validation of generated policies
- list_policies() reads from disk
- Retry logic when < 3 policies generated
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure OPENROUTER_API_KEY is set for tests
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-for-testing")

from agents.incident_policy_generator import (
    IncidentPolicyGenerator,
    VALID_CHECK_TYPES,
    VALID_RESOURCE_TYPES,
    POLICY_ID_PATTERN,
)


def _make_mock_response(policies: list[dict]) -> MagicMock:
    """Create a mock LLM response with the given policies as JSON content."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(policies)
    return mock_response


def _make_valid_policies(count: int = 3) -> list[dict]:
    """Generate a list of valid policy dicts for testing."""
    templates = [
        {
            "policy_id": "policy-check-sg-redis",
            "policy_name": "Check Redis security groups",
            "resource_types": ["elasticache"],
            "check_type": "security_group",
            "check_logic_description": "Check for open Redis ports in security groups",
            "rationale": "Open Redis ports led to data breach",
            "query": "Find elasticache clusters with open security groups",
        },
        {
            "policy_id": "policy-encrypt-ebs",
            "policy_name": "Encrypt EBS volumes",
            "resource_types": ["ebs"],
            "check_type": "encryption",
            "check_logic_description": "Verify EBS volume encryption is enabled",
            "rationale": "Unencrypted volumes risk data exposure",
            "query": "Find unencrypted EBS volumes",
        },
        {
            "policy_id": "policy-public-access-ec2",
            "policy_name": "Block public EC2 access",
            "resource_types": ["ec2"],
            "check_type": "public_access",
            "check_logic_description": "Check for publicly accessible EC2 instances",
            "rationale": "Public access was exploited in the incident",
            "query": "Find publicly accessible EC2 instances",
        },
        {
            "policy_id": "policy-idle-clusters",
            "policy_name": "Find idle clusters",
            "resource_types": ["elasticache"],
            "check_type": "idle_resource",
            "check_logic_description": "Detect idle ElastiCache clusters",
            "rationale": "Idle clusters waste money",
            "query": "Find idle ElastiCache clusters",
        },
        {
            "policy_id": "policy-sg-sensitive-ports",
            "policy_name": "Check sensitive port access",
            "resource_types": ["ec2", "elasticache"],
            "check_type": "security_group",
            "check_logic_description": "Check security groups for sensitive port exposure",
            "rationale": "Sensitive ports should not be publicly accessible",
            "query": "Find security groups exposing sensitive ports",
        },
    ]
    return templates[:count]


class TestInputValidation:
    """Tests for input validation behavior."""

    def test_empty_string_returns_empty_list(self):
        gen = IncidentPolicyGenerator()
        assert gen.generate("") == []

    def test_whitespace_only_returns_empty_list(self):
        gen = IncidentPolicyGenerator()
        assert gen.generate("   ") == []
        assert gen.generate("\t\n") == []

    def test_none_like_returns_empty_list(self):
        """None input should not crash, returns []."""
        gen = IncidentPolicyGenerator()
        # None is not a string but the agent should handle gracefully
        assert gen.generate(None) == []

    def test_truncation_over_2000_chars(self):
        """Input >2000 chars is truncated before LLM call; hash uses original."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            long_desc = "X" * 3000
            expected_hash = hashlib.sha256(long_desc.encode("utf-8")).hexdigest()[:8]

            mock_response = _make_mock_response(_make_valid_policies(3))
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate(long_desc)

            # Hash should be of the ORIGINAL un-truncated description
            assert all(p["incident_hash"] == expected_hash for p in result)

            # Verify the LLM received at most 2000 chars of content
            call_args = mock_client.chat.completions.create.call_args
            prompt_content = call_args[1]["messages"][1]["content"]
            assert "X" * 3000 not in prompt_content
            assert "X" * 2000 in prompt_content


class TestPolicyGeneration:
    """Tests for successful policy generation."""

    def test_generates_3_policies_with_correct_schema(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            mock_response = _make_mock_response(_make_valid_policies(3))
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate("A Redis cluster was compromised via open port.")

            assert len(result) == 3

            required_keys = {
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
            for policy in result:
                assert set(policy.keys()) == required_keys
                assert policy["version"] == 1
                assert isinstance(policy["generated_at"], str)
                assert isinstance(policy["incident_hash"], str)
                assert len(policy["incident_hash"]) == 8
                assert policy["check_type"] in VALID_CHECK_TYPES
                assert all(r in VALID_RESOURCE_TYPES for r in policy["resource_types"])
                assert len(policy["resource_types"]) > 0
                assert POLICY_ID_PATTERN.match(policy["policy_id"])

    def test_caps_at_5_policies(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            mock_response = _make_mock_response(_make_valid_policies(5))
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate("Major incident description")

            assert len(result) <= 5

    def test_files_written_to_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            mock_response = _make_mock_response(_make_valid_policies(3))
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate("Incident: open security group exposed Redis.")

            # Verify files exist
            assert policies_dir.exists()
            files = list(policies_dir.glob("*.json"))
            assert len(files) == 3

            # Verify file content matches returned policies
            for policy in result:
                file_path = policies_dir / f"{policy['policy_id']}.json"
                assert file_path.exists()
                on_disk = json.loads(file_path.read_text(encoding="utf-8"))
                assert on_disk == policy

    def test_incident_hash_computed_correctly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            description = "A specific incident description"
            expected_hash = hashlib.sha256(description.encode("utf-8")).hexdigest()[:8]

            mock_response = _make_mock_response(_make_valid_policies(3))
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate(description)

            assert all(p["incident_hash"] == expected_hash for p in result)


class TestIdempotency:
    """Tests for idempotent behavior when incident_hash already exists."""

    def test_returns_existing_without_llm_call(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            mock_response = _make_mock_response(_make_valid_policies(3))
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result1 = gen.generate("Test incident for idempotency")
                assert len(result1) == 3

                # Reset mock and verify no additional LLM calls
                mock_client.chat.completions.create.reset_mock()
                result2 = gen.generate("Test incident for idempotency")
                assert len(result2) == 3
                mock_client.chat.completions.create.assert_not_called()


class TestSecurityValidation:
    """Tests for security-related validation."""

    def test_unsafe_policy_id_rejected(self):
        """Policy IDs with path traversal or special chars are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            policies_with_bad_ids = [
                {
                    "policy_id": "valid-policy-one",
                    "policy_name": "Valid One",
                    "resource_types": ["ec2"],
                    "check_type": "security_group",
                    "check_logic_description": "Check something",
                    "rationale": "Reason",
                    "query": "Query text",
                },
                {
                    "policy_id": "../../../etc/passwd",
                    "policy_name": "Malicious",
                    "resource_types": ["ec2"],
                    "check_type": "security_group",
                    "check_logic_description": "Check",
                    "rationale": "Reason",
                    "query": "Query",
                },
                {
                    "policy_id": "UPPERCASE-BAD",
                    "policy_name": "Uppercase",
                    "resource_types": ["ebs"],
                    "check_type": "encryption",
                    "check_logic_description": "Check",
                    "rationale": "Reason",
                    "query": "Query",
                },
                {
                    "policy_id": "valid-policy-two",
                    "policy_name": "Valid Two",
                    "resource_types": ["ebs"],
                    "check_type": "encryption",
                    "check_logic_description": "Check encryption",
                    "rationale": "Reason two",
                    "query": "Query two",
                },
                {
                    "policy_id": "valid-policy-three",
                    "policy_name": "Valid Three",
                    "resource_types": ["elasticache"],
                    "check_type": "idle_resource",
                    "check_logic_description": "Check idle",
                    "rationale": "Reason three",
                    "query": "Query three",
                },
            ]

            mock_response = _make_mock_response(policies_with_bad_ids)
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate("Incident with unsafe IDs")

            # Only valid IDs should be present
            ids = [p["policy_id"] for p in result]
            assert "../../../etc/passwd" not in ids
            assert "UPPERCASE-BAD" not in ids
            assert "valid-policy-one" in ids
            assert "valid-policy-two" in ids
            assert "valid-policy-three" in ids

    def test_invalid_check_type_rejected(self):
        """Policies with invalid check_type are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            policies = [
                {
                    "policy_id": "valid-one",
                    "policy_name": "Valid",
                    "resource_types": ["ec2"],
                    "check_type": "security_group",
                    "check_logic_description": "Check",
                    "rationale": "Reason",
                    "query": "Query",
                },
                {
                    "policy_id": "bad-check-type",
                    "policy_name": "Bad",
                    "resource_types": ["ec2"],
                    "check_type": "sql_injection",  # invalid
                    "check_logic_description": "Check",
                    "rationale": "Reason",
                    "query": "Query",
                },
                {
                    "policy_id": "valid-two",
                    "policy_name": "Valid Two",
                    "resource_types": ["ebs"],
                    "check_type": "encryption",
                    "check_logic_description": "Check",
                    "rationale": "Reason",
                    "query": "Query",
                },
                {
                    "policy_id": "valid-three",
                    "policy_name": "Valid Three",
                    "resource_types": ["elasticache"],
                    "check_type": "idle_resource",
                    "check_logic_description": "Check",
                    "rationale": "Reason",
                    "query": "Query",
                },
            ]

            mock_response = _make_mock_response(policies)
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate("Incident with bad check types")

            ids = [p["policy_id"] for p in result]
            assert "bad-check-type" not in ids
            assert len(result) == 3

    def test_invalid_resource_types_rejected(self):
        """Policies with invalid resource_types are filtered; empty after filter → skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            policies = [
                {
                    "policy_id": "valid-mixed",
                    "policy_name": "Mixed valid/invalid resources",
                    "resource_types": ["ec2", "rds"],  # rds filtered out, ec2 remains
                    "check_type": "security_group",
                    "check_logic_description": "Check",
                    "rationale": "Reason",
                    "query": "Query",
                },
                {
                    "policy_id": "all-invalid-resources",
                    "policy_name": "All invalid",
                    "resource_types": ["rds", "s3"],  # all invalid → skipped
                    "check_type": "encryption",
                    "check_logic_description": "Check",
                    "rationale": "Reason",
                    "query": "Query",
                },
                {
                    "policy_id": "valid-two",
                    "policy_name": "Valid",
                    "resource_types": ["ebs"],
                    "check_type": "encryption",
                    "check_logic_description": "Check",
                    "rationale": "Reason",
                    "query": "Query",
                },
                {
                    "policy_id": "valid-three",
                    "policy_name": "Valid",
                    "resource_types": ["elasticache"],
                    "check_type": "idle_resource",
                    "check_logic_description": "Check",
                    "rationale": "Reason",
                    "query": "Query",
                },
            ]

            mock_response = _make_mock_response(policies)
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate("Incident with resource type issues")

            # valid-mixed should have only ["ec2"] after filtering
            mixed = next(p for p in result if p["policy_id"] == "valid-mixed")
            assert mixed["resource_types"] == ["ec2"]

            # all-invalid-resources should be completely excluded
            ids = [p["policy_id"] for p in result]
            assert "all-invalid-resources" not in ids


class TestErrorHandling:
    """Tests for graceful error handling."""

    def test_llm_failure_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = Exception("LLM down")

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate("Some incident description")

            assert result == []
            # No files should be written
            if policies_dir.exists():
                assert len(list(policies_dir.glob("*.json"))) == 0

    def test_environment_error_returns_empty_list(self):
        """Missing API key raises EnvironmentError, caught by agent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            with patch(
                "agents.incident_policy_generator.get_client",
                side_effect=EnvironmentError("OPENROUTER_API_KEY is not set"),
            ):
                result = gen.generate("Some incident description")

            assert result == []

    def test_invalid_json_from_llm_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "not valid json at all"

            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate("Some incident")

            assert result == []

    def test_io_error_cleans_up_partial_files(self):
        """If file write fails, no partial files left on disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            valid_policies = _make_valid_policies(3)
            mock_response = _make_mock_response(valid_policies)
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response

            # Make the directory read-only to cause write failure after creation
            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                # Patch Path.write_text to fail on the second file
                original_write_text = Path.write_text
                call_count = [0]

                def failing_write_text(self_path, *args, **kwargs):
                    call_count[0] += 1
                    if call_count[0] == 2:
                        raise OSError("Disk full")
                    return original_write_text(self_path, *args, **kwargs)

                with patch.object(Path, "write_text", failing_write_text):
                    result = gen.generate("Incident causing IO error")

            # Should return [] on IO error
            assert result == []
            # No partial files should remain
            if policies_dir.exists():
                remaining = list(policies_dir.glob("*.json"))
                assert len(remaining) == 0


class TestRetryLogic:
    """Tests for retry behavior when < 3 policies generated."""

    def test_retries_once_when_fewer_than_3(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            # First call returns 2, retry returns 3
            first_response = _make_mock_response(_make_valid_policies(2))
            retry_response = _make_mock_response(_make_valid_policies(3))

            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = [
                first_response,
                retry_response,
            ]

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate("Incident needing retry")

            # Should have called LLM twice
            assert mock_client.chat.completions.create.call_count == 2
            assert len(result) == 3

    def test_returns_whatever_generated_if_retry_also_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)

            # Both calls return only 2 policies
            response = _make_mock_response(_make_valid_policies(2))
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = response

            with patch(
                "agents.incident_policy_generator.get_client",
                return_value=mock_client,
            ):
                result = gen.generate("Incident with limited results")

            # Should return whatever was generated (2 in this case)
            assert mock_client.chat.completions.create.call_count == 2
            assert len(result) == 2


class TestListPolicies:
    """Tests for list_policies() method."""

    def test_returns_policies_from_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            policies_dir.mkdir()

            policy = {
                "policy_id": "test-policy",
                "policy_name": "Test Policy",
                "check_type": "security_group",
            }
            (policies_dir / "test-policy.json").write_text(
                json.dumps(policy), encoding="utf-8"
            )

            gen = IncidentPolicyGenerator(policies_dir=policies_dir)
            result = gen.list_policies()

            assert len(result) == 1
            assert result[0]["policy_id"] == "test-policy"

    def test_empty_directory_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            policies_dir.mkdir()

            gen = IncidentPolicyGenerator(policies_dir=policies_dir)
            result = gen.list_policies()
            assert result == []

    def test_missing_directory_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "nonexistent"
            gen = IncidentPolicyGenerator(policies_dir=policies_dir)
            result = gen.list_policies()
            assert result == []

    def test_malformed_json_files_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            policies_dir = Path(tmpdir) / "policies"
            policies_dir.mkdir()

            # Valid file
            (policies_dir / "good.json").write_text(
                json.dumps({"policy_id": "good"}), encoding="utf-8"
            )
            # Malformed file
            (policies_dir / "bad.json").write_text("not json", encoding="utf-8")

            gen = IncidentPolicyGenerator(policies_dir=policies_dir)
            result = gen.list_policies()

            assert len(result) == 1
            assert result[0]["policy_id"] == "good"
