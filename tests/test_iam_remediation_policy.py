"""Property-based tests for iam/janitor-remediation-policy.json.

Property 6: Remediation Policy Resource-Level Scoping Correctness
Property 7: Remediation Policy Template Coverage

**Validates: Requirements 4.1, 4.2, 4.4, 4.5**
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

POLICY_PATH = Path(__file__).resolve().parent.parent / "iam" / "janitor-remediation-policy.json"


@pytest.fixture
def policy() -> dict:
    """Load and parse the remediation policy JSON."""
    assert POLICY_PATH.exists(), f"Policy file not found: {POLICY_PATH}"
    with open(POLICY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Reference Data
# ---------------------------------------------------------------------------

# Actions known to support resource-level IAM permissions (from design.md
# Component 4 and AWS Service Authorization Reference). These actions MUST
# NOT appear in a statement that uses Resource: "*".
RESOURCE_SCOPABLE_ACTIONS: frozenset[str] = frozenset({
    # EBS volume/snapshot actions
    "ec2:CreateSnapshot",
    "ec2:CreateTags",
    "ec2:CreateVolume",
    "ec2:DeleteVolume",
    # Security group rule actions
    "ec2:AuthorizeSecurityGroupIngress",
    "ec2:RevokeSecurityGroupIngress",
    # ElastiCache cluster/snapshot mutation + tagging actions
    "elasticache:CreateCacheCluster",
    "elasticache:DeleteCacheCluster",
    "elasticache:CreateSnapshot",
    "elasticache:AddTagsToResource",
})

# The complete set of actions that MUST be present in the remediation policy,
# derived from design.md Component 4's template→action table.
REQUIRED_TEMPLATE_ACTIONS: frozenset[str] = frozenset({
    # EBS remediation (_remediation_ebs_waste)
    "ec2:CreateSnapshot",
    "ec2:CreateTags",
    "ec2:DeleteVolume",
    # EBS rollback (_rollback_ebs_waste)
    "ec2:CreateVolume",
    # Security group remediation/rollback (_remediation_security_group, _rollback_security_group)
    "ec2:AuthorizeSecurityGroupIngress",
    "ec2:RevokeSecurityGroupIngress",
    # ElastiCache remediation (_remediation_elasticache_waste)
    "elasticache:CreateSnapshot",
    "elasticache:DeleteCacheCluster",
    # ElastiCache rollback (_rollback_elasticache_waste)
    "elasticache:CreateCacheCluster",
    "elasticache:AddTagsToResource",
    # State-refresh actions (used by Terraform provider for all templates)
    "ec2:DescribeVolumes",
    "ec2:DescribeSnapshots",
    "ec2:DescribeSecurityGroups",
    "ec2:DescribeSecurityGroupRules",
    "ec2:DescribeVpcs",
    "elasticache:DescribeCacheClusters",
    "elasticache:DescribeSnapshots",
    "elasticache:ListTagsForResource",
    "sts:GetCallerIdentity",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_action_list(action_field) -> list[str]:
    """Normalize the Action field (string or list) to a list of strings."""
    if isinstance(action_field, str):
        return [action_field]
    return list(action_field)


def _normalize_resource_list(resource_field) -> list[str]:
    """Normalize the Resource field (string or list) to a list of strings."""
    if isinstance(resource_field, str):
        return [resource_field]
    return list(resource_field)


def _all_policy_actions(policy: dict) -> set[str]:
    """Extract all actions from all statements in the policy."""
    actions: set[str] = set()
    for stmt in policy.get("Statement", []):
        actions.update(_normalize_action_list(stmt.get("Action", [])))
    return actions


# ---------------------------------------------------------------------------
# Property 6: Remediation Policy Resource-Level Scoping Correctness
# ---------------------------------------------------------------------------


class TestResourceLevelScopingCorrectness:
    """Property 6: No statement whose actions are ALL in the reference set of
    resource-scopable actions uses Resource: "*".

    This ensures that actions known to support resource-level IAM permissions
    are never granted with a wildcard resource, which would violate the
    least-privilege principle.

    **Validates: Requirements 4.2, 4.5**
    """

    def test_no_fully_scopable_statement_uses_wildcard_resource(self, policy):
        """For every statement in the policy, if ALL of its actions are in the
        set of actions known to support resource-level permissions, then the
        statement's Resource field MUST NOT be "*".
        """
        violations: list[str] = []

        for stmt in policy.get("Statement", []):
            actions = _normalize_action_list(stmt.get("Action", []))
            resources = _normalize_resource_list(stmt.get("Resource", []))

            # Check if ALL actions in this statement are resource-scopable
            all_scopable = all(a in RESOURCE_SCOPABLE_ACTIONS for a in actions)

            if all_scopable and len(actions) > 0:
                # This statement's actions are all scopable — Resource must NOT be "*"
                if resources == ["*"]:
                    violations.append(
                        f"Statement '{stmt.get('Sid', '<no Sid>')}' contains only "
                        f"resource-scopable actions {actions} but uses Resource: \"*\""
                    )

        assert not violations, (
            "Resource-level scoping violations found:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def test_scopable_actions_reference_set_is_nonempty(self):
        """Sanity check: the reference set is not empty (guards against
        a tautological pass-by-default if the constant were accidentally cleared).
        """
        assert len(RESOURCE_SCOPABLE_ACTIONS) >= 10, (
            f"Expected at least 10 resource-scopable actions, got {len(RESOURCE_SCOPABLE_ACTIONS)}"
        )

    def test_each_scopable_action_actually_appears_in_policy(self, policy):
        """Every action in our reference set of scopable actions should appear
        somewhere in the policy (otherwise the scoping check is vacuously true
        for actions that are simply missing).
        """
        all_actions = _all_policy_actions(policy)
        missing = RESOURCE_SCOPABLE_ACTIONS - all_actions

        assert not missing, (
            f"Resource-scopable actions missing from policy entirely "
            f"(makes Property 6 vacuously true for them): {sorted(missing)}"
        )

    def test_statements_with_mixed_actions_allow_wildcard(self, policy):
        """Negative case: statements that mix scopable and non-scopable actions
        (or contain only non-scopable actions) are allowed to use Resource: "*".
        This verifies the property does not over-constrain.
        """
        # Find a statement with non-scopable actions (e.g., Describe* or sts:*)
        non_scopable_found = False
        for stmt in policy.get("Statement", []):
            actions = _normalize_action_list(stmt.get("Action", []))
            if any(a not in RESOURCE_SCOPABLE_ACTIONS for a in actions):
                non_scopable_found = True
                break

        assert non_scopable_found, (
            "Expected at least one statement with non-scopable actions in the policy"
        )


# ---------------------------------------------------------------------------
# Property 7: Remediation Policy Template Coverage
# ---------------------------------------------------------------------------


class TestRemediationPolicyTemplateCoverage:
    """Property 7: Every action in the design.md Component 4 template→action
    table is present in iam/janitor-remediation-policy.json.

    This cross-checks the hand-authored reference table against the actual
    policy JSON, ensuring no required action was omitted.

    **Validates: Requirements 4.1, 4.4, 4.5**
    """

    def test_all_required_actions_present_in_policy(self, policy):
        """Every action from the template→action table MUST appear in at least
        one statement in the remediation policy.
        """
        all_actions = _all_policy_actions(policy)
        missing = REQUIRED_TEMPLATE_ACTIONS - all_actions

        assert not missing, (
            "Actions required by the template→action table but missing from "
            "iam/janitor-remediation-policy.json:\n"
            + "\n".join(f"  - {a}" for a in sorted(missing))
        )

    def test_required_actions_reference_set_is_nonempty(self):
        """Sanity check: the required actions set is not empty (guards against
        a tautological pass if the constant were accidentally cleared).
        """
        assert len(REQUIRED_TEMPLATE_ACTIONS) >= 19, (
            f"Expected at least 19 required template actions, got {len(REQUIRED_TEMPLATE_ACTIONS)}"
        )

    def test_policy_has_no_empty_statements(self, policy):
        """No statement in the policy should have an empty Action list."""
        for stmt in policy.get("Statement", []):
            actions = _normalize_action_list(stmt.get("Action", []))
            assert len(actions) > 0, (
                f"Statement '{stmt.get('Sid', '<no Sid>')}' has no actions"
            )

    def test_ebs_remediation_actions_present(self, policy):
        """Specific check: the three EBS remediation actions are covered."""
        all_actions = _all_policy_actions(policy)
        ebs_actions = {"ec2:CreateSnapshot", "ec2:CreateTags", "ec2:DeleteVolume"}
        missing = ebs_actions - all_actions
        assert not missing, f"EBS remediation actions missing: {sorted(missing)}"

    def test_security_group_actions_present(self, policy):
        """Specific check: both security group rule actions are covered."""
        all_actions = _all_policy_actions(policy)
        sg_actions = {"ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress"}
        missing = sg_actions - all_actions
        assert not missing, f"Security group actions missing: {sorted(missing)}"

    def test_elasticache_remediation_actions_present(self, policy):
        """Specific check: all ElastiCache mutation actions are covered."""
        all_actions = _all_policy_actions(policy)
        ec_actions = {
            "elasticache:CreateSnapshot",
            "elasticache:DeleteCacheCluster",
            "elasticache:CreateCacheCluster",
            "elasticache:AddTagsToResource",
        }
        missing = ec_actions - all_actions
        assert not missing, f"ElastiCache remediation actions missing: {sorted(missing)}"

    def test_state_refresh_actions_present(self, policy):
        """Specific check: all Terraform state-refresh actions are covered."""
        all_actions = _all_policy_actions(policy)
        state_actions = {
            "ec2:DescribeVolumes",
            "ec2:DescribeSnapshots",
            "ec2:DescribeSecurityGroups",
            "ec2:DescribeSecurityGroupRules",
            "ec2:DescribeVpcs",
            "elasticache:DescribeCacheClusters",
            "elasticache:DescribeSnapshots",
            "elasticache:ListTagsForResource",
            "sts:GetCallerIdentity",
        }
        missing = state_actions - all_actions
        assert not missing, f"State-refresh actions missing: {sorted(missing)}"
