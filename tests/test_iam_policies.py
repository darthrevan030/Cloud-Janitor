"""Static validity tests for IAM policy artifacts.

Validates that both `iam/janitor-read-policy.json` and
`iam/janitor-remediation-policy.json` are well-formed IAM policy documents
with correct structure, unique Sid values, and valid Resource patterns.

Validates: Requirements 3.1, 4.1
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Locate policy files relative to project root
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
READ_POLICY_PATH = PROJECT_ROOT / "iam" / "janitor-read-policy.json"
REMEDIATION_POLICY_PATH = PROJECT_ROOT / "iam" / "janitor-remediation-policy.json"

POLICY_FILES = [READ_POLICY_PATH, REMEDIATION_POLICY_PATH]

# ARN pattern: arn:aws:<service>:<region>:<account>:<resource>
# Region and account fields may be:
#   - "*" (wildcard, common in IAM policy Resource elements)
#   - empty (valid for global services like IAM)
#   - a placeholder like ACCOUNT_ID (template for operator substitution)
#   - an actual region or 12-digit account number
# Service is lowercase alphanumeric + hyphens.
ARN_PATTERN = re.compile(r"^arn:aws:[a-z0-9-]+:[a-zA-Z0-9_*-]*:[a-zA-Z0-9_*-]*:.+$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_policy(path: Path) -> dict:
    """Load and parse a policy JSON file."""
    text = path.read_text(encoding="utf-8")
    return json.loads(text)


def _all_resource_values(policy: dict) -> list[str]:
    """Extract all Resource values from a policy (handles string or list)."""
    resources: list[str] = []
    for statement in policy.get("Statement", []):
        resource = statement.get("Resource")
        if resource is None:
            continue
        if isinstance(resource, str):
            resources.append(resource)
        elif isinstance(resource, list):
            resources.extend(resource)
    return resources


# ---------------------------------------------------------------------------
# Tests: Valid JSON parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("policy_path", POLICY_FILES, ids=["read-policy", "remediation-policy"])
class TestPolicyJsonValidity:
    """Both policy files must be valid JSON documents."""

    def test_file_exists(self, policy_path: Path) -> None:
        assert policy_path.exists(), f"Policy file not found: {policy_path}"

    def test_valid_json(self, policy_path: Path) -> None:
        text = policy_path.read_text(encoding="utf-8")
        # json.loads will raise JSONDecodeError if invalid
        policy = json.loads(text)
        assert isinstance(policy, dict), "Policy root must be a JSON object"

    def test_version_field_present_and_correct(self, policy_path: Path) -> None:
        policy = _load_policy(policy_path)
        assert "Version" in policy, "Policy must have a 'Version' field"
        assert policy["Version"] == "2012-10-17", (
            f"Expected Version '2012-10-17', got '{policy['Version']}'"
        )

    def test_has_statement_array(self, policy_path: Path) -> None:
        policy = _load_policy(policy_path)
        assert "Statement" in policy, "Policy must have a 'Statement' field"
        assert isinstance(policy["Statement"], list), "Statement must be an array"
        assert len(policy["Statement"]) > 0, "Statement array must not be empty"


# ---------------------------------------------------------------------------
# Tests: No duplicate Sid values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("policy_path", POLICY_FILES, ids=["read-policy", "remediation-policy"])
class TestNoDuplicateSids:
    """Each policy must have unique Sid values across all statements."""

    def test_no_duplicate_sids(self, policy_path: Path) -> None:
        policy = _load_policy(policy_path)
        sids: list[str] = []
        for statement in policy["Statement"]:
            sid = statement.get("Sid")
            if sid is not None:
                sids.append(sid)

        duplicates = [sid for sid in sids if sids.count(sid) > 1]
        assert len(duplicates) == 0, (
            f"Duplicate Sid values found: {set(duplicates)}"
        )

    def test_all_statements_have_sid(self, policy_path: Path) -> None:
        """Every statement should have a Sid for clarity and auditability."""
        policy = _load_policy(policy_path)
        for i, statement in enumerate(policy["Statement"]):
            assert "Sid" in statement, (
                f"Statement at index {i} is missing a 'Sid' field"
            )


# ---------------------------------------------------------------------------
# Tests: Resource values are valid
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("policy_path", POLICY_FILES, ids=["read-policy", "remediation-policy"])
class TestResourceValidity:
    """Every Resource value must be '*' or a well-formed ARN pattern."""

    def test_all_resources_valid(self, policy_path: Path) -> None:
        policy = _load_policy(policy_path)
        resources = _all_resource_values(policy)

        assert len(resources) > 0, "Policy must have at least one Resource value"

        invalid: list[str] = []
        for resource in resources:
            if resource == "*":
                continue
            if not ARN_PATTERN.match(resource):
                invalid.append(resource)

        assert len(invalid) == 0, (
            f"Invalid Resource values (not '*' and not a valid ARN pattern): {invalid}"
        )

    def test_resource_field_present_in_every_statement(self, policy_path: Path) -> None:
        """Every statement must have a Resource field."""
        policy = _load_policy(policy_path)
        for i, statement in enumerate(policy["Statement"]):
            assert "Resource" in statement, (
                f"Statement at index {i} (Sid={statement.get('Sid', 'N/A')}) "
                f"is missing a 'Resource' field"
            )
