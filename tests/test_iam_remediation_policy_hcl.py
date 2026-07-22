"""Property 8: Remediation Policy HCL-Derived Coverage.

Cross-checks actual rendered HCL from RemediationArchitect's template methods
against iam/janitor-remediation-policy.json — verifies that every AWS action
implied by the generated HCL resource blocks and local-exec CLI commands is
covered by the IAM policy.

This test operates on the RETURNED HCL STRING, not the hand-written Component 4
table — it is the first test in this phase that would have caught a missing
resource type independently of a human re-deriving the table correctly.

Validates: Requirements 4.1, 4.4
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cloud_janitor.agents.remediation_architect import RemediationArchitect

# ---------------------------------------------------------------------------
# Locate policy file relative to project root
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REMEDIATION_POLICY_PATH = PROJECT_ROOT / "iam" / "janitor-remediation-policy.json"

# ---------------------------------------------------------------------------
# HCL resource type → IAM actions mapping
# ---------------------------------------------------------------------------

# Maps Terraform resource types to the IAM actions they require
HCL_RESOURCE_TYPE_TO_ACTIONS: dict[str, set[str]] = {
    "aws_ebs_snapshot": {"ec2:CreateSnapshot", "ec2:CreateTags"},
    "aws_ebs_volume": {"ec2:CreateVolume", "ec2:CreateTags"},
    "aws_security_group_rule": {
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:RevokeSecurityGroupIngress",
    },
    "aws_elasticache_cluster": {
        "elasticache:CreateCacheCluster",
        "elasticache:AddTagsToResource",
    },
}

# Maps AWS CLI subcommands (found in local-exec provisioners) → IAM actions
LOCAL_EXEC_CLI_TO_ACTIONS: dict[str, str] = {
    "aws ec2 delete-volume": "ec2:DeleteVolume",
    "aws elasticache create-snapshot": "elasticache:CreateSnapshot",
    "aws elasticache delete-cache-cluster": "elasticache:DeleteCacheCluster",
}

# ---------------------------------------------------------------------------
# Representative sample finding dicts
# ---------------------------------------------------------------------------

EBS_FINDING: dict = {
    "id": "f1",
    "resource_id": "vol-abc123",
    "resource_type": "ebs",
    "category": "waste",
    "severity": "MEDIUM",
    "title": "test",
    "description": "test",
    "cost_estimate_monthly": 10.0,
    "idle_days": 35,
}

SECURITY_GROUP_FINDING: dict = {
    "id": "f2",
    "resource_id": "sg-abc123",
    "resource_type": "security_group",
    "category": "security",
    "severity": "CRITICAL",
    "title": "test",
    "description": "test",
    "metadata": {"port": 6379, "cidr": "0.0.0.0/0"},
}

ELASTICACHE_FINDING: dict = {
    "id": "f3",
    "resource_id": "cache-abc123",
    "resource_type": "elasticache",
    "category": "waste",
    "severity": "HIGH",
    "title": "test",
    "description": "test",
    "cost_estimate_monthly": 45.0,
    "idle_days": 42,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Regex to find HCL resource blocks: resource "<type>" "<name>" {
RESOURCE_BLOCK_RE = re.compile(r'resource\s+"([^"]+)"\s+"([^"]+)"')

# Regex to find local-exec command strings
LOCAL_EXEC_COMMAND_RE = re.compile(r'command\s*=\s*"([^"]+)"')


def _load_policy_actions() -> set[str]:
    """Load all actions from janitor-remediation-policy.json."""
    text = REMEDIATION_POLICY_PATH.read_text(encoding="utf-8")
    policy = json.loads(text)
    actions: set[str] = set()
    for statement in policy.get("Statement", []):
        action = statement.get("Action")
        if action is None:
            continue
        if isinstance(action, str):
            actions.add(action)
        elif isinstance(action, list):
            actions.update(action)
    return actions


def _extract_resource_types_from_hcl(hcl: str) -> set[str]:
    """Extract all Terraform resource types from HCL string."""
    return {match.group(1) for match in RESOURCE_BLOCK_RE.finditer(hcl)}


def _extract_local_exec_commands(hcl: str) -> list[str]:
    """Extract all command strings from local-exec provisioners in HCL."""
    return LOCAL_EXEC_COMMAND_RE.findall(hcl)


def _derive_actions_from_hcl(hcl: str) -> set[str]:
    """Derive all expected IAM actions from a rendered HCL string.

    Examines both resource blocks (mapped via HCL_RESOURCE_TYPE_TO_ACTIONS)
    and local-exec CLI commands (mapped via LOCAL_EXEC_CLI_TO_ACTIONS).
    """
    derived_actions: set[str] = set()

    # Map resource types to IAM actions
    resource_types = _extract_resource_types_from_hcl(hcl)
    for rtype in resource_types:
        if rtype in HCL_RESOURCE_TYPE_TO_ACTIONS:
            derived_actions.update(HCL_RESOURCE_TYPE_TO_ACTIONS[rtype])

    # Map local-exec CLI commands to IAM actions
    commands = _extract_local_exec_commands(hcl)
    for cmd in commands:
        for cli_prefix, iam_action in LOCAL_EXEC_CLI_TO_ACTIONS.items():
            if cmd.startswith(cli_prefix):
                derived_actions.add(iam_action)

    return derived_actions


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestRemediationPolicyHCLCoverage:
    """Property 8: Every IAM action derived from actual rendered HCL is
    covered by iam/janitor-remediation-policy.json."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        """Set up RemediationArchitect instance and load policy actions."""
        self.architect = RemediationArchitect()
        self.policy_actions = _load_policy_actions()

    def test_policy_file_exists(self) -> None:
        """Precondition: the remediation policy file must exist."""
        assert REMEDIATION_POLICY_PATH.exists(), (
            f"Remediation policy not found at {REMEDIATION_POLICY_PATH}"
        )

    def test_remediation_ebs_waste_covered(self) -> None:
        """HCL from _remediation_ebs_waste has all actions in policy."""
        hcl = self.architect._remediation_ebs_waste(EBS_FINDING)
        derived = _derive_actions_from_hcl(hcl)

        assert len(derived) > 0, (
            "_remediation_ebs_waste HCL should derive at least one IAM action"
        )
        missing = derived - self.policy_actions
        assert missing == set(), (
            f"_remediation_ebs_waste actions missing from policy: {sorted(missing)}"
        )

    def test_rollback_ebs_waste_covered(self) -> None:
        """HCL from _rollback_ebs_waste has all actions in policy."""
        hcl = self.architect._rollback_ebs_waste(EBS_FINDING)
        derived = _derive_actions_from_hcl(hcl)

        assert len(derived) > 0, (
            "_rollback_ebs_waste HCL should derive at least one IAM action"
        )
        missing = derived - self.policy_actions
        assert missing == set(), (
            f"_rollback_ebs_waste actions missing from policy: {sorted(missing)}"
        )

    def test_remediation_security_group_covered(self) -> None:
        """HCL from _remediation_security_group has all actions in policy."""
        hcl = self.architect._remediation_security_group(SECURITY_GROUP_FINDING)
        derived = _derive_actions_from_hcl(hcl)

        assert len(derived) > 0, (
            "_remediation_security_group HCL should derive at least one IAM action"
        )
        missing = derived - self.policy_actions
        assert missing == set(), (
            f"_remediation_security_group actions missing from policy: {sorted(missing)}"
        )

    def test_rollback_security_group_covered(self) -> None:
        """HCL from _rollback_security_group has all actions in policy."""
        hcl = self.architect._rollback_security_group(SECURITY_GROUP_FINDING)
        derived = _derive_actions_from_hcl(hcl)

        assert len(derived) > 0, (
            "_rollback_security_group HCL should derive at least one IAM action"
        )
        missing = derived - self.policy_actions
        assert missing == set(), (
            f"_rollback_security_group actions missing from policy: {sorted(missing)}"
        )

    def test_remediation_elasticache_waste_covered(self) -> None:
        """HCL from _remediation_elasticache_waste has all actions in policy."""
        hcl = self.architect._remediation_elasticache_waste(ELASTICACHE_FINDING)
        derived = _derive_actions_from_hcl(hcl)

        assert len(derived) > 0, (
            "_remediation_elasticache_waste HCL should derive at least one IAM action"
        )
        missing = derived - self.policy_actions
        assert missing == set(), (
            f"_remediation_elasticache_waste actions missing from policy: {sorted(missing)}"
        )

    def test_rollback_elasticache_waste_covered(self) -> None:
        """HCL from _rollback_elasticache_waste has all actions in policy."""
        hcl = self.architect._rollback_elasticache_waste(ELASTICACHE_FINDING)
        derived = _derive_actions_from_hcl(hcl)

        assert len(derived) > 0, (
            "_rollback_elasticache_waste HCL should derive at least one IAM action"
        )
        missing = derived - self.policy_actions
        assert missing == set(), (
            f"_rollback_elasticache_waste actions missing from policy: {sorted(missing)}"
        )

    def test_all_templates_combined_coverage(self) -> None:
        """Union of all six templates' derived actions is fully covered."""
        all_hcl_parts = [
            self.architect._remediation_ebs_waste(EBS_FINDING),
            self.architect._rollback_ebs_waste(EBS_FINDING),
            self.architect._remediation_security_group(SECURITY_GROUP_FINDING),
            self.architect._rollback_security_group(SECURITY_GROUP_FINDING),
            self.architect._remediation_elasticache_waste(ELASTICACHE_FINDING),
            self.architect._rollback_elasticache_waste(ELASTICACHE_FINDING),
        ]

        all_derived: set[str] = set()
        for hcl in all_hcl_parts:
            all_derived.update(_derive_actions_from_hcl(hcl))

        assert len(all_derived) > 0, (
            "Combined HCL from all templates must derive at least one IAM action"
        )
        missing = all_derived - self.policy_actions
        assert missing == set(), (
            f"Actions derived from HCL templates but missing from policy: {sorted(missing)}"
        )

    def test_hcl_resource_blocks_detected(self) -> None:
        """Sanity: the HCL parser actually finds resource blocks."""
        hcl = self.architect._remediation_ebs_waste(EBS_FINDING)
        resource_types = _extract_resource_types_from_hcl(hcl)
        assert "aws_ebs_snapshot" in resource_types, (
            "Parser must detect aws_ebs_snapshot resource block in EBS remediation HCL"
        )

    def test_local_exec_commands_detected(self) -> None:
        """Sanity: the HCL parser actually finds local-exec commands."""
        hcl = self.architect._remediation_ebs_waste(EBS_FINDING)
        commands = _extract_local_exec_commands(hcl)
        assert any("delete-volume" in cmd for cmd in commands), (
            "Parser must detect 'aws ec2 delete-volume' in EBS remediation HCL"
        )

    def test_ebs_remediation_derives_expected_actions(self) -> None:
        """Verify specific expected actions from EBS remediation template."""
        hcl = self.architect._remediation_ebs_waste(EBS_FINDING)
        derived = _derive_actions_from_hcl(hcl)
        expected = {"ec2:CreateSnapshot", "ec2:CreateTags", "ec2:DeleteVolume"}
        assert expected.issubset(derived), (
            f"EBS remediation must derive {expected}, got {derived}"
        )

    def test_elasticache_remediation_derives_expected_actions(self) -> None:
        """Verify specific expected actions from ElastiCache remediation template."""
        hcl = self.architect._remediation_elasticache_waste(ELASTICACHE_FINDING)
        derived = _derive_actions_from_hcl(hcl)
        expected = {"elasticache:CreateSnapshot", "elasticache:DeleteCacheCluster"}
        assert expected.issubset(derived), (
            f"ElastiCache remediation must derive {expected}, got {derived}"
        )

    def test_security_group_derives_expected_actions(self) -> None:
        """Verify specific expected actions from security group templates."""
        remediate_hcl = self.architect._remediation_security_group(
            SECURITY_GROUP_FINDING
        )
        rollback_hcl = self.architect._rollback_security_group(
            SECURITY_GROUP_FINDING
        )
        combined_derived = _derive_actions_from_hcl(
            remediate_hcl
        ) | _derive_actions_from_hcl(rollback_hcl)

        # Security group rules need both Authorize and Revoke
        expected = {
            "ec2:AuthorizeSecurityGroupIngress",
            "ec2:RevokeSecurityGroupIngress",
        }
        assert expected.issubset(combined_derived), (
            f"Security group templates must derive {expected}, got {combined_derived}"
        )
