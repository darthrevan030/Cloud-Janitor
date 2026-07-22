"""Unit tests for _check_plan_scope and _sanitize_id.

Tests cover:
- In-scope plan acceptance for every (resource_type, category, flow) combination
- Out-of-scope address rejection
- Security-group widen-to-0.0.0.0/0 rejection in remediate flow
- Security-group widen-to-0.0.0.0/0 acceptance in rollback flow
- EBS/ElastiCache waste rollback address-prefix rejection
- Security-group wrong-direction address rejection
- EBS/ElastiCache waste exhaustiveness rejection
- Data-source-only plan ignored
"""

from __future__ import annotations

import pytest

from cloud_janitor.orchestrator.orchestrator import _check_plan_scope, _sanitize_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_finding(resource_type: str, category: str) -> dict:
    """Build a minimal finding dict."""
    return {"resource_type": resource_type, "category": category}


def _make_change(address: str, actions: list[str], *, resource_type: str = "aws_ebs_snapshot",
                 mode: str = "managed", after: dict | None = None) -> dict:
    """Build a single resource_changes entry."""
    change: dict = {"actions": actions}
    if after is not None:
        change["after"] = after
    return {
        "address": address,
        "mode": mode,
        "type": resource_type,
        "change": change,
    }


def _plan(*changes: dict) -> dict:
    """Wrap changes into a plan_json dict."""
    return {"resource_changes": list(changes)}


# ---------------------------------------------------------------------------
# _sanitize_id tests
# ---------------------------------------------------------------------------

class TestSanitizeId:
    def test_replaces_dashes(self):
        assert _sanitize_id("vol-0abc123") == "vol_0abc123"

    def test_replaces_slashes(self):
        assert _sanitize_id("a/b/c") == "a_b_c"

    def test_replaces_colons(self):
        assert _sanitize_id("arn:aws:ec2") == "arn_aws_ec2"

    def test_mixed_separators(self):
        assert _sanitize_id("vol-0abc/region:us-east-1") == "vol_0abc_region_us_east_1"

    def test_no_separators_unchanged(self):
        assert _sanitize_id("abc123") == "abc123"


# ---------------------------------------------------------------------------
# 1. In-scope plan acceptance — one per (resource_type, category, flow)
# ---------------------------------------------------------------------------

class TestInScopeAcceptance:
    """Validates that a correctly-scoped plan passes for each known combination."""

    def test_ebs_waste_remediate(self):
        """EBS waste remediation: snapshot + destroy."""
        safe = _sanitize_id("vol-0abc123")
        plan_json = _plan(
            _make_change(f"aws_ebs_snapshot.pre_remediation_{safe}", ["create"],
                         resource_type="aws_ebs_snapshot"),
            _make_change(f"null_resource.destroy_{safe}", ["create"],
                         resource_type="null_resource"),
        )
        finding = _make_finding("ebs", "waste")
        passed, reason = _check_plan_scope(plan_json, "vol-0abc123", finding, "remediate")
        assert passed is True, f"Expected pass, got: {reason}"

    def test_ebs_waste_rollback(self):
        """EBS waste rollback: restore volume from snapshot."""
        safe = _sanitize_id("vol-0abc123")
        plan_json = _plan(
            _make_change(f"aws_ebs_volume.restore_{safe}", ["create"],
                         resource_type="aws_ebs_volume"),
        )
        finding = _make_finding("ebs", "waste")
        passed, reason = _check_plan_scope(plan_json, "vol-0abc123", finding, "rollback")
        assert passed is True, f"Expected pass, got: {reason}"

    def test_elasticache_waste_remediate(self):
        """ElastiCache waste remediation: snapshot + destroy."""
        safe = _sanitize_id("cache-cluster-01")
        plan_json = _plan(
            _make_change(f"null_resource.snapshot_{safe}", ["create"],
                         resource_type="null_resource"),
            _make_change(f"null_resource.destroy_{safe}", ["create"],
                         resource_type="null_resource"),
        )
        finding = _make_finding("elasticache", "waste")
        passed, reason = _check_plan_scope(plan_json, "cache-cluster-01", finding, "remediate")
        assert passed is True, f"Expected pass, got: {reason}"

    def test_elasticache_waste_rollback(self):
        """ElastiCache waste rollback: restore cluster."""
        safe = _sanitize_id("cache-cluster-01")
        plan_json = _plan(
            _make_change(f"aws_elasticache_cluster.restore_{safe}", ["create"],
                         resource_type="aws_elasticache_cluster"),
        )
        finding = _make_finding("elasticache", "waste")
        passed, reason = _check_plan_scope(plan_json, "cache-cluster-01", finding, "rollback")
        assert passed is True, f"Expected pass, got: {reason}"

    def test_ebs_security_remediate(self):
        """EBS security remediation: zero managed changes expected."""
        plan_json = _plan()  # no changes
        finding = _make_finding("ebs", "security")
        passed, reason = _check_plan_scope(plan_json, "vol-0abc123", finding, "remediate")
        assert passed is True, f"Expected pass, got: {reason}"

    def test_ebs_security_rollback(self):
        """EBS security rollback: zero managed changes expected."""
        plan_json = _plan()  # no changes
        finding = _make_finding("ebs", "security")
        passed, reason = _check_plan_scope(plan_json, "vol-0abc123", finding, "rollback")
        assert passed is True, f"Expected pass, got: {reason}"

    def test_elasticache_security_remediate(self):
        """ElastiCache security remediation: zero managed changes expected."""
        plan_json = _plan()
        finding = _make_finding("elasticache", "security")
        passed, reason = _check_plan_scope(plan_json, "cache-cluster-01", finding, "remediate")
        assert passed is True, f"Expected pass, got: {reason}"

    def test_elasticache_security_rollback(self):
        """ElastiCache security rollback: zero managed changes expected."""
        plan_json = _plan()
        finding = _make_finding("elasticache", "security")
        passed, reason = _check_plan_scope(plan_json, "cache-cluster-01", finding, "rollback")
        assert passed is True, f"Expected pass, got: {reason}"

    def test_security_group_security_remediate(self):
        """Security group remediation: single rule narrowing CIDR."""
        safe = _sanitize_id("sg-0abc123")
        plan_json = _plan(
            _make_change(
                f"aws_security_group_rule.remediate_{safe}",
                ["create"],
                resource_type="aws_security_group_rule",
                after={"cidr_blocks": ["10.0.0.0/16"]},
            ),
        )
        finding = _make_finding("security_group", "security")
        passed, reason = _check_plan_scope(plan_json, "sg-0abc123", finding, "remediate")
        assert passed is True, f"Expected pass, got: {reason}"

    def test_security_group_security_rollback(self):
        """Security group rollback: restores original rule (may include 0.0.0.0/0)."""
        safe = _sanitize_id("sg-0abc123")
        plan_json = _plan(
            _make_change(
                f"aws_security_group_rule.restore_{safe}",
                ["create"],
                resource_type="aws_security_group_rule",
                after={"cidr_blocks": ["10.0.0.0/16"]},
            ),
        )
        finding = _make_finding("security_group", "security")
        passed, reason = _check_plan_scope(plan_json, "sg-0abc123", finding, "rollback")
        assert passed is True, f"Expected pass, got: {reason}"


# ---------------------------------------------------------------------------
# 2. Out-of-scope address rejection
# ---------------------------------------------------------------------------

class TestOutOfScopeAddressRejection:
    """Plans touching resources unrelated to the finding's resource_id are rejected."""

    def test_ebs_waste_remediate_wrong_resource_id(self):
        """Snapshot address references a different volume — must reject."""
        safe_wrong = _sanitize_id("vol-DIFFERENT")
        plan_json = _plan(
            _make_change(f"aws_ebs_snapshot.pre_remediation_{safe_wrong}", ["create"],
                         resource_type="aws_ebs_snapshot"),
            _make_change(f"null_resource.destroy_{safe_wrong}", ["create"],
                         resource_type="null_resource"),
        )
        finding = _make_finding("ebs", "waste")
        passed, reason = _check_plan_scope(plan_json, "vol-0abc123", finding, "remediate")
        assert passed is False
        assert "vol_0abc123" in reason or "does not contain" in reason

    def test_security_group_wrong_resource_id(self):
        """Security group address references a different SG — must reject."""
        safe_wrong = _sanitize_id("sg-WRONG")
        plan_json = _plan(
            _make_change(
                f"aws_security_group_rule.remediate_{safe_wrong}",
                ["create"],
                resource_type="aws_security_group_rule",
                after={"cidr_blocks": ["10.0.0.0/16"]},
            ),
        )
        finding = _make_finding("security_group", "security")
        passed, reason = _check_plan_scope(plan_json, "sg-0abc123", finding, "remediate")
        assert passed is False
        assert "does not contain" in reason


# ---------------------------------------------------------------------------
# 3. Security-group widen-to-0.0.0.0/0 REJECTION in remediate flow
# ---------------------------------------------------------------------------

class TestSgWidenRejectRemediate:
    """Remediation flow must reject plans that widen CIDR to 0.0.0.0/0."""

    def test_reject_cidr_0000_in_remediate(self):
        safe = _sanitize_id("sg-0abc123")
        plan_json = _plan(
            _make_change(
                f"aws_security_group_rule.remediate_{safe}",
                ["create"],
                resource_type="aws_security_group_rule",
                after={"cidr_blocks": ["0.0.0.0/0"]},
            ),
        )
        finding = _make_finding("security_group", "security")
        passed, reason = _check_plan_scope(plan_json, "sg-0abc123", finding, "remediate")
        assert passed is False
        assert "0.0.0.0/0" in reason

    def test_reject_cidr_0000_mixed_list(self):
        """Even if 0.0.0.0/0 is among other CIDRs, still rejected."""
        safe = _sanitize_id("sg-0abc123")
        plan_json = _plan(
            _make_change(
                f"aws_security_group_rule.remediate_{safe}",
                ["update"],
                resource_type="aws_security_group_rule",
                after={"cidr_blocks": ["10.0.0.0/16", "0.0.0.0/0"]},
            ),
        )
        finding = _make_finding("security_group", "security")
        passed, reason = _check_plan_scope(plan_json, "sg-0abc123", finding, "remediate")
        assert passed is False
        assert "0.0.0.0/0" in reason


# ---------------------------------------------------------------------------
# 4. Security-group widen-to-0.0.0.0/0 ACCEPTANCE in rollback flow
# ---------------------------------------------------------------------------

class TestSgWidenAcceptRollback:
    """Rollback flow is allowed to restore 0.0.0.0/0 (original state)."""

    def test_accept_cidr_0000_in_rollback(self):
        safe = _sanitize_id("sg-0abc123")
        plan_json = _plan(
            _make_change(
                f"aws_security_group_rule.restore_{safe}",
                ["create"],
                resource_type="aws_security_group_rule",
                after={"cidr_blocks": ["0.0.0.0/0"]},
            ),
        )
        finding = _make_finding("security_group", "security")
        passed, reason = _check_plan_scope(plan_json, "sg-0abc123", finding, "rollback")
        assert passed is True, f"Expected pass, got: {reason}"


# ---------------------------------------------------------------------------
# 5. EBS/ElastiCache waste rollback address-prefix rejection
# ---------------------------------------------------------------------------

class TestWasteRollbackPrefixRejection:
    """Rollback flow using remediation-direction address prefixes must be rejected."""

    def test_ebs_rollback_using_remediation_prefix(self):
        """Rollback plan with pre_remediation_ prefix (remediate direction) — reject."""
        safe = _sanitize_id("vol-0abc123")
        plan_json = _plan(
            _make_change(f"aws_ebs_snapshot.pre_remediation_{safe}", ["create"],
                         resource_type="aws_ebs_snapshot"),
        )
        finding = _make_finding("ebs", "waste")
        passed, reason = _check_plan_scope(plan_json, "vol-0abc123", finding, "rollback")
        assert passed is False
        assert "prefix" in reason.lower() or "does not match" in reason.lower()

    def test_elasticache_rollback_using_remediation_prefix(self):
        """Rollback plan using snapshot_ (remediate direction) — reject."""
        safe = _sanitize_id("cache-cluster-01")
        plan_json = _plan(
            _make_change(f"null_resource.snapshot_{safe}", ["create"],
                         resource_type="null_resource"),
        )
        finding = _make_finding("elasticache", "waste")
        passed, reason = _check_plan_scope(plan_json, "cache-cluster-01", finding, "rollback")
        assert passed is False
        assert "prefix" in reason.lower() or "does not match" in reason.lower()


# ---------------------------------------------------------------------------
# 6. Security-group wrong-direction address rejection
# ---------------------------------------------------------------------------

class TestSgWrongDirectionPrefix:
    """Remediate flow must use remediate_ prefix; rollback must use restore_ prefix."""

    def test_remediate_prefix_during_rollback(self):
        """Using remediate_ prefix in rollback flow — reject."""
        safe = _sanitize_id("sg-0abc123")
        plan_json = _plan(
            _make_change(
                f"aws_security_group_rule.remediate_{safe}",
                ["create"],
                resource_type="aws_security_group_rule",
                after={"cidr_blocks": ["10.0.0.0/16"]},
            ),
        )
        finding = _make_finding("security_group", "security")
        passed, reason = _check_plan_scope(plan_json, "sg-0abc123", finding, "rollback")
        assert passed is False
        assert "prefix" in reason.lower() or "does not start with" in reason.lower()

    def test_restore_prefix_during_remediate(self):
        """Using restore_ prefix in remediate flow — reject."""
        safe = _sanitize_id("sg-0abc123")
        plan_json = _plan(
            _make_change(
                f"aws_security_group_rule.restore_{safe}",
                ["create"],
                resource_type="aws_security_group_rule",
                after={"cidr_blocks": ["10.0.0.0/16"]},
            ),
        )
        finding = _make_finding("security_group", "security")
        passed, reason = _check_plan_scope(plan_json, "sg-0abc123", finding, "remediate")
        assert passed is False
        assert "prefix" in reason.lower() or "does not start with" in reason.lower()


# ---------------------------------------------------------------------------
# 7. EBS/ElastiCache waste exhaustiveness rejection
# ---------------------------------------------------------------------------

class TestWasteExhaustivenessRejection:
    """Destroying a resource without the paired snapshot/snapshot step must fail."""

    def test_ebs_destroy_without_snapshot(self):
        """Only destroy_* without pre_remediation_ snapshot — must reject."""
        safe = _sanitize_id("vol-0abc123")
        plan_json = _plan(
            _make_change(f"null_resource.destroy_{safe}", ["create"],
                         resource_type="null_resource"),
        )
        finding = _make_finding("ebs", "waste")
        passed, reason = _check_plan_scope(plan_json, "vol-0abc123", finding, "remediate")
        assert passed is False
        assert "missing" in reason.lower()

    def test_ebs_snapshot_without_destroy(self):
        """Only pre_remediation_ snapshot without destroy_* — must reject."""
        safe = _sanitize_id("vol-0abc123")
        plan_json = _plan(
            _make_change(f"aws_ebs_snapshot.pre_remediation_{safe}", ["create"],
                         resource_type="aws_ebs_snapshot"),
        )
        finding = _make_finding("ebs", "waste")
        passed, reason = _check_plan_scope(plan_json, "vol-0abc123", finding, "remediate")
        assert passed is False
        assert "missing" in reason.lower()

    def test_elasticache_destroy_without_snapshot(self):
        """Only destroy without snapshot — must reject."""
        safe = _sanitize_id("cache-cluster-01")
        plan_json = _plan(
            _make_change(f"null_resource.destroy_{safe}", ["create"],
                         resource_type="null_resource"),
        )
        finding = _make_finding("elasticache", "waste")
        passed, reason = _check_plan_scope(plan_json, "cache-cluster-01", finding, "remediate")
        assert passed is False
        assert "missing" in reason.lower()

    def test_elasticache_snapshot_without_destroy(self):
        """Only snapshot without destroy — must reject."""
        safe = _sanitize_id("cache-cluster-01")
        plan_json = _plan(
            _make_change(f"null_resource.snapshot_{safe}", ["create"],
                         resource_type="null_resource"),
        )
        finding = _make_finding("elasticache", "waste")
        passed, reason = _check_plan_scope(plan_json, "cache-cluster-01", finding, "remediate")
        assert passed is False
        assert "missing" in reason.lower()


# ---------------------------------------------------------------------------
# 8. Data-source-only plan (mode="data") does not count toward change threshold
# ---------------------------------------------------------------------------

class TestDataSourceIgnored:
    """Entries with mode='data' must be excluded from change counting."""

    def test_data_source_only_ebs_security(self):
        """A plan with only data-source reads should pass for zero-change rules."""
        plan_json = _plan(
            _make_change(
                "data.aws_vpc.current",
                ["read"],
                resource_type="aws_vpc",
                mode="data",
            ),
        )
        finding = _make_finding("ebs", "security")
        passed, reason = _check_plan_scope(plan_json, "vol-0abc123", finding, "remediate")
        assert passed is True, f"Data-source entry should be ignored, got: {reason}"

    def test_data_source_mixed_with_managed(self):
        """Data sources alongside a managed change: only managed counts."""
        safe = _sanitize_id("sg-0abc123")
        plan_json = {
            "resource_changes": [
                {
                    "address": "data.aws_vpc.current",
                    "mode": "data",
                    "type": "aws_vpc",
                    "change": {"actions": ["read"]},
                },
                {
                    "address": f"aws_security_group_rule.remediate_{safe}",
                    "mode": "managed",
                    "type": "aws_security_group_rule",
                    "change": {"actions": ["create"], "after": {"cidr_blocks": ["10.0.0.0/16"]}},
                },
            ]
        }
        finding = _make_finding("security_group", "security")
        passed, reason = _check_plan_scope(plan_json, "sg-0abc123", finding, "remediate")
        assert passed is True, f"Data source should be ignored in count, got: {reason}"

    def test_data_source_does_not_trigger_rejection_for_zero_change_rule(self):
        """EBS security expects 0 managed changes; data reads should not count."""
        plan_json = {
            "resource_changes": [
                {
                    "address": "data.aws_ebs_volume.info",
                    "mode": "data",
                    "type": "aws_ebs_volume",
                    "change": {"actions": ["read"]},
                },
                {
                    "address": "data.aws_caller_identity.current",
                    "mode": "data",
                    "type": "aws_caller_identity",
                    "change": {"actions": ["read"]},
                },
            ]
        }
        finding = _make_finding("ebs", "security")
        passed, reason = _check_plan_scope(plan_json, "vol-0abc123", finding, "remediate")
        assert passed is True, f"Multiple data sources should be ignored, got: {reason}"
