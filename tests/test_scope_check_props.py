"""Property-based tests for _check_plan_scope allowlist partition.

**Validates: Requirements 7.3, 7.4, 7.5, 7.6, 7.7, 7.8**

Property 7: Scope Check Allowlist Partition (Both Flows)

For any plan_json constructed with resource_changes that match a known
(resource_type, category, flow) rule from _SCOPE_ALLOWLIST, _check_plan_scope
must return (True, ...). For any plan_json whose changes DON'T match the
allowlist (wrong prefix, missing required prefix, wrong action set, wrong
CIDR direction), it must return (False, ...).
"""

from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st

from cloud_janitor.orchestrator.orchestrator import (
    _check_plan_scope,
    _sanitize_id,
    _SCOPE_ALLOWLIST,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Hex characters for realistic AWS resource IDs
_hex_chars = st.sampled_from("0123456789abcdef")
_hex_string = st.text(_hex_chars, min_size=8, max_size=17)


# Resource ID prefixes mirroring real AWS patterns
_resource_id_prefixes = st.sampled_from([
    "vol-", "sg-", "cache-", "snap-", "eni-", "i-",
])

resource_ids = st.builds(
    lambda prefix, hex_id: f"{prefix}{hex_id}",
    _resource_id_prefixes,
    _hex_string,
)

# Flow values
flows = st.sampled_from(["remediate", "rollback"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_change(address: str, actions: list[str], resource_type: str = "aws_security_group_rule",
                 after: dict | None = None) -> dict:
    """Build a single resource_change entry for plan_json."""
    change_block: dict = {"actions": actions}
    if after is not None:
        change_block["after"] = after
    return {
        "address": address,
        "mode": "managed",
        "type": resource_type,
        "change": change_block,
    }


def _make_plan_json(changes: list[dict]) -> dict:
    """Wrap changes into a plan_json structure."""
    return {"resource_changes": changes}


def _make_finding(resource_type: str, category: str) -> dict:
    """Build a finding dict with the expected fields."""
    return {"resource_type": f"aws_{resource_type}", "category": category}


# ---------------------------------------------------------------------------
# EBS/ElastiCache waste rules: valid plan builders
# ---------------------------------------------------------------------------

def _build_multi_prefix_plan(rule_key: tuple, resource_id: str) -> dict:
    """Build a valid plan_json for multi-prefix (exhaustive) rules."""
    rule = _SCOPE_ALLOWLIST[rule_key]
    safe_id = _sanitize_id(resource_id)
    action_list = list(list(rule["actions"])[0])  # e.g. ["create"]
    changes = []
    for prefix in rule["addr_prefixes"]:
        addr = f"{prefix}{safe_id}"
        changes.append(_make_change(addr, action_list, resource_type="null_resource"))
    return _make_plan_json(changes)


def _build_sg_plan(rule_key: tuple, resource_id: str,
                   cidr_blocks: list[str] | None = None) -> dict:
    """Build a valid plan_json for security_group rules."""
    rule = _SCOPE_ALLOWLIST[rule_key]
    safe_id = _sanitize_id(resource_id)
    action_list = list(list(rule["actions"])[0])  # e.g. ["create"]
    addr = f"{rule['addr_prefix']}{safe_id}"
    after = {}
    if cidr_blocks is not None:
        after["cidr_blocks"] = cidr_blocks
    change = _make_change(addr, action_list, resource_type=rule["resource_type"], after=after)
    return _make_plan_json([change])


# Private CIDRs that are NOT 0.0.0.0/0
_private_cidrs = st.sampled_from([
    "10.0.0.0/16", "172.16.0.0/12", "192.168.1.0/24",
    "10.1.2.0/24", "172.31.0.0/16",
])

# ---------------------------------------------------------------------------
# PROPERTY: For any valid-shaped plan matching a rule, scope check passes
# ---------------------------------------------------------------------------


class TestScopeCheckValidPlansPass:
    """For any valid plan matching a _SCOPE_ALLOWLIST rule, _check_plan_scope
    must return (True, ...)."""


    @given(resource_id=resource_ids)
    @settings(max_examples=100, deadline=5000)
    def test_ebs_waste_remediate_valid(self, resource_id):
        """EBS waste remediate plan with correct prefixes passes."""
        key = ("ebs", "waste", "remediate")
        plan_json = _build_multi_prefix_plan(key, resource_id)
        finding = _make_finding("ebs", "waste")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "remediate")
        assert passed is True, f"Expected pass for valid ebs/waste/remediate: {reason}"

    @given(resource_id=resource_ids)
    @settings(max_examples=100, deadline=5000)
    def test_ebs_waste_rollback_valid(self, resource_id):
        """EBS waste rollback plan with correct prefix passes."""
        key = ("ebs", "waste", "rollback")
        plan_json = _build_multi_prefix_plan(key, resource_id)
        finding = _make_finding("ebs", "waste")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "rollback")
        assert passed is True, f"Expected pass for valid ebs/waste/rollback: {reason}"

    @given(resource_id=resource_ids)
    @settings(max_examples=100, deadline=5000)
    def test_elasticache_waste_remediate_valid(self, resource_id):
        """ElastiCache waste remediate plan with correct prefixes passes."""
        key = ("elasticache", "waste", "remediate")
        plan_json = _build_multi_prefix_plan(key, resource_id)
        finding = _make_finding("elasticache", "waste")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "remediate")
        assert passed is True, f"Expected pass for valid elasticache/waste/remediate: {reason}"


    @given(resource_id=resource_ids)
    @settings(max_examples=100, deadline=5000)
    def test_elasticache_waste_rollback_valid(self, resource_id):
        """ElastiCache waste rollback plan with correct prefix passes."""
        key = ("elasticache", "waste", "rollback")
        plan_json = _build_multi_prefix_plan(key, resource_id)
        finding = _make_finding("elasticache", "waste")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "rollback")
        assert passed is True, f"Expected pass for valid elasticache/waste/rollback: {reason}"

    @given(resource_id=resource_ids, cidr=_private_cidrs)
    @settings(max_examples=100, deadline=5000)
    def test_sg_security_remediate_valid_private_cidr(self, resource_id, cidr):
        """Security group remediate plan with private CIDR passes."""
        key = ("security_group", "security", "remediate")
        plan_json = _build_sg_plan(key, resource_id, cidr_blocks=[cidr])
        finding = _make_finding("security_group", "security")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "remediate")
        assert passed is True, f"Expected pass for valid sg/security/remediate: {reason}"

    @given(resource_id=resource_ids, cidr=_private_cidrs)
    @settings(max_examples=100, deadline=5000)
    def test_sg_security_rollback_valid(self, resource_id, cidr):
        """Security group rollback plan passes (even with broad CIDRs if not 0.0.0.0/0)."""
        key = ("security_group", "security", "rollback")
        plan_json = _build_sg_plan(key, resource_id, cidr_blocks=[cidr])
        finding = _make_finding("security_group", "security")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "rollback")
        assert passed is True, f"Expected pass for valid sg/security/rollback: {reason}"


    @given(resource_id=resource_ids)
    @settings(max_examples=100, deadline=5000)
    def test_ebs_security_remediate_zero_changes(self, resource_id):
        """EBS security remediate with zero changes passes (max_changes=0 rule)."""
        plan_json = _make_plan_json([])
        finding = _make_finding("ebs", "security")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "remediate")
        assert passed is True, f"Expected pass for ebs/security/remediate zero changes: {reason}"

    @given(resource_id=resource_ids)
    @settings(max_examples=100, deadline=5000)
    def test_elasticache_security_rollback_zero_changes(self, resource_id):
        """ElastiCache security rollback with zero changes passes."""
        plan_json = _make_plan_json([])
        finding = _make_finding("elasticache", "security")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "rollback")
        assert passed is True, f"Expected pass for elasticache/security/rollback zero changes: {reason}"


# ---------------------------------------------------------------------------
# COUNTER-PROPERTY: Out-of-scope address prefix → scope check fails
# ---------------------------------------------------------------------------


class TestScopeCheckWrongPrefixFails:
    """For any plan with an out-of-scope address prefix, scope check must
    return (False, ...)."""


    # Bad prefixes that don't match any allowlist entry
    _bad_prefixes = st.sampled_from([
        "aws_instance.rogue_",
        "aws_s3_bucket.hack_",
        "null_resource.evil_",
        "aws_iam_role.escalate_",
        "aws_ebs_volume.unauthorized_",
        "local_exec.backdoor_",
    ])

    @given(resource_id=resource_ids, bad_prefix=_bad_prefixes)
    @settings(max_examples=100, deadline=5000)
    def test_ebs_waste_remediate_wrong_prefix(self, resource_id, bad_prefix):
        """EBS waste remediate with a wrong address prefix must fail."""
        safe_id = _sanitize_id(resource_id)
        # Build a plan with a bad prefix address (still contains safe_id)
        addr = f"{bad_prefix}{safe_id}"
        change = _make_change(addr, ["create"], resource_type="null_resource")
        plan_json = _make_plan_json([change])
        finding = _make_finding("ebs", "waste")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "remediate")
        assert passed is False, (
            f"Expected FAIL for wrong prefix '{bad_prefix}' on ebs/waste/remediate "
            f"but got pass: {reason}"
        )

    @given(resource_id=resource_ids, bad_prefix=_bad_prefixes)
    @settings(max_examples=100, deadline=5000)
    def test_elasticache_waste_remediate_wrong_prefix(self, resource_id, bad_prefix):
        """ElastiCache waste remediate with a wrong address prefix must fail."""
        safe_id = _sanitize_id(resource_id)
        addr = f"{bad_prefix}{safe_id}"
        change = _make_change(addr, ["create"], resource_type="null_resource")
        plan_json = _make_plan_json([change])
        finding = _make_finding("elasticache", "waste")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "remediate")
        assert passed is False, (
            f"Expected FAIL for wrong prefix '{bad_prefix}' on elasticache/waste/remediate "
            f"but got pass: {reason}"
        )


    @given(resource_id=resource_ids, bad_prefix=_bad_prefixes)
    @settings(max_examples=100, deadline=5000)
    def test_sg_security_remediate_wrong_prefix(self, resource_id, bad_prefix):
        """Security group remediate with a wrong address prefix must fail."""
        safe_id = _sanitize_id(resource_id)
        addr = f"{bad_prefix}{safe_id}"
        change = _make_change(addr, ["create"], resource_type="aws_security_group_rule")
        plan_json = _make_plan_json([change])
        finding = _make_finding("security_group", "security")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "remediate")
        assert passed is False, (
            f"Expected FAIL for wrong prefix '{bad_prefix}' on sg/security/remediate "
            f"but got pass: {reason}"
        )

    @given(resource_id=resource_ids, bad_prefix=_bad_prefixes)
    @settings(max_examples=100, deadline=5000)
    def test_sg_security_rollback_wrong_prefix(self, resource_id, bad_prefix):
        """Security group rollback with a wrong address prefix must fail."""
        safe_id = _sanitize_id(resource_id)
        addr = f"{bad_prefix}{safe_id}"
        change = _make_change(addr, ["create"], resource_type="aws_security_group_rule")
        plan_json = _make_plan_json([change])
        finding = _make_finding("security_group", "security")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "rollback")
        assert passed is False, (
            f"Expected FAIL for wrong prefix '{bad_prefix}' on sg/security/rollback "
            f"but got pass: {reason}"
        )


# ---------------------------------------------------------------------------
# COUNTER-PROPERTY: Missing required prefix → scope check fails
# ---------------------------------------------------------------------------


class TestScopeCheckMissingPrefixFails:
    """For any (ebs, waste, remediate) plan missing one required prefix,
    scope check must return (False, ...)."""

    @given(resource_id=resource_ids)
    @settings(max_examples=100, deadline=5000)
    def test_ebs_waste_remediate_missing_snapshot_prefix(self, resource_id):
        """EBS waste remediate missing the snapshot prefix must fail.

        The rule requires BOTH aws_ebs_snapshot.pre_remediation_ AND
        null_resource.destroy_. Supplying only the destroy prefix fails
        the exhaustive check.
        """
        safe_id = _sanitize_id(resource_id)
        # Only provide the destroy prefix, missing the snapshot prefix
        addr = f"null_resource.destroy_{safe_id}"
        change = _make_change(addr, ["create"], resource_type="null_resource")
        plan_json = _make_plan_json([change])
        finding = _make_finding("ebs", "waste")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "remediate")
        assert passed is False, (
            f"Expected FAIL when snapshot prefix missing for ebs/waste/remediate: {reason}"
        )

    @given(resource_id=resource_ids)
    @settings(max_examples=100, deadline=5000)
    def test_ebs_waste_remediate_missing_destroy_prefix(self, resource_id):
        """EBS waste remediate missing the destroy prefix must fail.

        Only snapshot prefix is present; exhaustive check requires both.
        """
        safe_id = _sanitize_id(resource_id)
        # Only provide the snapshot prefix, missing destroy
        addr = f"aws_ebs_snapshot.pre_remediation_{safe_id}"
        change = _make_change(addr, ["create"], resource_type="aws_ebs_snapshot")
        plan_json = _make_plan_json([change])
        finding = _make_finding("ebs", "waste")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "remediate")
        assert passed is False, (
            f"Expected FAIL when destroy prefix missing for ebs/waste/remediate: {reason}"
        )


    @given(resource_id=resource_ids)
    @settings(max_examples=100, deadline=5000)
    def test_elasticache_waste_remediate_missing_snapshot_prefix(self, resource_id):
        """ElastiCache waste remediate missing the snapshot prefix must fail."""
        safe_id = _sanitize_id(resource_id)
        # Only provide destroy, missing snapshot
        addr = f"null_resource.destroy_{safe_id}"
        change = _make_change(addr, ["create"], resource_type="null_resource")
        plan_json = _make_plan_json([change])
        finding = _make_finding("elasticache", "waste")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "remediate")
        assert passed is False, (
            f"Expected FAIL when snapshot prefix missing for elasticache/waste/remediate: {reason}"
        )

    @given(resource_id=resource_ids)
    @settings(max_examples=100, deadline=5000)
    def test_elasticache_waste_remediate_missing_destroy_prefix(self, resource_id):
        """ElastiCache waste remediate missing the destroy prefix must fail."""
        safe_id = _sanitize_id(resource_id)
        # Only provide snapshot, missing destroy
        addr = f"null_resource.snapshot_{safe_id}"
        change = _make_change(addr, ["create"], resource_type="null_resource")
        plan_json = _make_plan_json([change])
        finding = _make_finding("elasticache", "waste")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "remediate")
        assert passed is False, (
            f"Expected FAIL when destroy prefix missing for elasticache/waste/remediate: {reason}"
        )


# ---------------------------------------------------------------------------
# COUNTER-PROPERTY: 0.0.0.0/0 CIDR on remediate flow → scope check fails
# ---------------------------------------------------------------------------


class TestScopeCheckCidrRejection:
    """For any (security_group, security, remediate) plan with 0.0.0.0/0 in
    cidr_blocks, scope check must return (False, ...)."""

    @given(resource_id=resource_ids, extra_cidr=_private_cidrs)
    @settings(max_examples=100, deadline=5000)
    def test_sg_remediate_rejects_full_open_cidr(self, resource_id, extra_cidr):
        """Security group remediate plan with 0.0.0.0/0 in cidr_blocks must fail.

        This tests the reject_cidr_0000 rule: the remediation must never widen
        access back to the full internet.
        """
        key = ("security_group", "security", "remediate")
        # Include 0.0.0.0/0 alongside other valid CIDRs
        plan_json = _build_sg_plan(
            key, resource_id, cidr_blocks=["0.0.0.0/0", extra_cidr]
        )
        finding = _make_finding("security_group", "security")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "remediate")
        assert passed is False, (
            f"Expected FAIL when 0.0.0.0/0 present in sg/security/remediate: {reason}"
        )

    @given(resource_id=resource_ids)
    @settings(max_examples=100, deadline=5000)
    def test_sg_remediate_rejects_sole_open_cidr(self, resource_id):
        """Security group remediate with ONLY 0.0.0.0/0 must fail."""
        key = ("security_group", "security", "remediate")
        plan_json = _build_sg_plan(key, resource_id, cidr_blocks=["0.0.0.0/0"])
        finding = _make_finding("security_group", "security")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "remediate")
        assert passed is False, (
            f"Expected FAIL for sole 0.0.0.0/0 in sg/security/remediate: {reason}"
        )


    @given(resource_id=resource_ids, extra_cidr=_private_cidrs)
    @settings(max_examples=100, deadline=5000)
    def test_sg_rollback_allows_open_cidr(self, resource_id, extra_cidr):
        """Security group rollback plan with 0.0.0.0/0 SHOULD pass.

        Rollbacks restore the original broad rule; reject_cidr_0000 is False
        for rollback. This is the critical asymmetry test.
        """
        key = ("security_group", "security", "rollback")
        plan_json = _build_sg_plan(
            key, resource_id, cidr_blocks=["0.0.0.0/0", extra_cidr]
        )
        finding = _make_finding("security_group", "security")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "rollback")
        assert passed is True, (
            f"Expected PASS for 0.0.0.0/0 in sg/security/rollback (restore original): {reason}"
        )


# ---------------------------------------------------------------------------
# COUNTER-PROPERTY: Wrong action set → scope check fails
# ---------------------------------------------------------------------------


class TestScopeCheckWrongActionsFails:
    """For any plan with an action set not in the allowlist rule, scope check
    must return (False, ...)."""

    _disallowed_actions_for_create_only = st.sampled_from([
        ["delete"],
        ["update"],
        ["delete", "create"],
        ["read"],
    ])


    @given(resource_id=resource_ids, bad_actions=_disallowed_actions_for_create_only)
    @settings(max_examples=100, deadline=5000)
    def test_ebs_waste_remediate_wrong_actions(self, resource_id, bad_actions):
        """EBS waste remediate only allows {("create",)}. Other actions must fail."""
        safe_id = _sanitize_id(resource_id)
        changes = []
        rule = _SCOPE_ALLOWLIST[("ebs", "waste", "remediate")]
        for prefix in rule["addr_prefixes"]:
            addr = f"{prefix}{safe_id}"
            changes.append(_make_change(addr, bad_actions, resource_type="null_resource"))
        plan_json = _make_plan_json(changes)
        finding = _make_finding("ebs", "waste")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "remediate")
        assert passed is False, (
            f"Expected FAIL for actions {bad_actions} on ebs/waste/remediate: {reason}"
        )

    @given(resource_id=resource_ids, bad_actions=_disallowed_actions_for_create_only)
    @settings(max_examples=100, deadline=5000)
    def test_elasticache_waste_rollback_wrong_actions(self, resource_id, bad_actions):
        """ElastiCache waste rollback only allows {("create",)}. Others must fail."""
        safe_id = _sanitize_id(resource_id)
        rule = _SCOPE_ALLOWLIST[("elasticache", "waste", "rollback")]
        addr = f"{rule['addr_prefixes'][0]}{safe_id}"
        change = _make_change(addr, bad_actions, resource_type="aws_elasticache_cluster")
        plan_json = _make_plan_json([change])
        finding = _make_finding("elasticache", "waste")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "rollback")
        assert passed is False, (
            f"Expected FAIL for actions {bad_actions} on elasticache/waste/rollback: {reason}"
        )


    # Actions not allowed for security group rules (only create and update are)
    _disallowed_sg_actions = st.sampled_from([
        ["delete"],
        ["delete", "create"],
        ["read"],
        ["no-op"],
    ])

    @given(resource_id=resource_ids, bad_actions=_disallowed_sg_actions)
    @settings(max_examples=100, deadline=5000)
    def test_sg_security_remediate_wrong_actions(self, resource_id, bad_actions):
        """Security group remediate only allows create/update. Other actions fail."""
        safe_id = _sanitize_id(resource_id)
        rule = _SCOPE_ALLOWLIST[("security_group", "security", "remediate")]
        addr = f"{rule['addr_prefix']}{safe_id}"
        change = _make_change(
            addr, bad_actions, resource_type="aws_security_group_rule"
        )
        plan_json = _make_plan_json([change])
        finding = _make_finding("security_group", "security")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "remediate")
        assert passed is False, (
            f"Expected FAIL for actions {bad_actions} on sg/security/remediate: {reason}"
        )


# ---------------------------------------------------------------------------
# COUNTER-PROPERTY: Zero-change rule violated → scope check fails
# ---------------------------------------------------------------------------


class TestScopeCheckZeroChangeViolation:
    """For max_changes=0 rules, any managed change must cause failure."""


    @given(
        resource_id=resource_ids,
        num_changes=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=100, deadline=5000)
    def test_ebs_security_remediate_nonzero_changes_fail(self, resource_id, num_changes):
        """EBS security remediate expects zero changes; any change must fail."""
        safe_id = _sanitize_id(resource_id)
        changes = [
            _make_change(f"aws_ebs_volume.modify_{safe_id}_{i}", ["update"])
            for i in range(num_changes)
        ]
        plan_json = _make_plan_json(changes)
        finding = _make_finding("ebs", "security")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "remediate")
        assert passed is False, (
            f"Expected FAIL for {num_changes} changes on ebs/security/remediate "
            f"(max_changes=0): {reason}"
        )

    @given(
        resource_id=resource_ids,
        num_changes=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=100, deadline=5000)
    def test_elasticache_security_rollback_nonzero_changes_fail(self, resource_id, num_changes):
        """ElastiCache security rollback expects zero changes; any change must fail."""
        safe_id = _sanitize_id(resource_id)
        changes = [
            _make_change(f"aws_elasticache_cluster.tweak_{safe_id}_{i}", ["create"])
            for i in range(num_changes)
        ]
        plan_json = _make_plan_json(changes)
        finding = _make_finding("elasticache", "security")
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, "rollback")
        assert passed is False, (
            f"Expected FAIL for {num_changes} changes on elasticache/security/rollback "
            f"(max_changes=0): {reason}"
        )


# ---------------------------------------------------------------------------
# COUNTER-PROPERTY: Unrecognized template with changes → fails
# ---------------------------------------------------------------------------


class TestScopeCheckUnrecognizedTemplate:
    """An unrecognized (resource_type, category, flow) triple with changes must fail."""

    _unknown_types = st.sampled_from([
        "rds", "lambda", "s3", "vpc", "iam_role", "cloudfront",
    ])

    _unknown_categories = st.sampled_from([
        "compliance", "performance", "cost", "reliability",
    ])

    @given(
        resource_id=resource_ids,
        res_type=_unknown_types,
        category=_unknown_categories,
        flow=flows,
    )
    @settings(max_examples=100, deadline=5000)
    def test_unknown_template_with_changes_fails(self, resource_id, res_type, category, flow):
        """Any unrecognized (type, category, flow) with managed changes must fail."""
        # Verify this key truly isn't in the allowlist
        assume((res_type, category, flow) not in _SCOPE_ALLOWLIST)

        safe_id = _sanitize_id(resource_id)
        change = _make_change(f"aws_instance.rogue_{safe_id}", ["create"])
        plan_json = _make_plan_json([change])
        finding = _make_finding(res_type, category)
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, flow)
        assert passed is False, (
            f"Expected FAIL for unrecognized template ({res_type}, {category}, {flow}) "
            f"with changes: {reason}"
        )

    @given(
        resource_id=resource_ids,
        res_type=_unknown_types,
        category=_unknown_categories,
        flow=flows,
    )
    @settings(max_examples=100, deadline=5000)
    def test_unknown_template_zero_changes_passes(self, resource_id, res_type, category, flow):
        """An unrecognized template with ZERO changes passes (default safe)."""
        assume((res_type, category, flow) not in _SCOPE_ALLOWLIST)

        plan_json = _make_plan_json([])
        finding = _make_finding(res_type, category)
        passed, reason = _check_plan_scope(plan_json, resource_id, finding, flow)
        assert passed is True, (
            f"Expected PASS for unrecognized template with zero changes: {reason}"
        )
