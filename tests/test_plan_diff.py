"""Unit tests for core/plan_diff.py — summarize_plan().

Tests cover:
- One test per action bucket (create, update, delete, replace)
- mode=="data" entries excluded from all buckets
- actions==["no-op"] entries excluded from all buckets
- Unrecognized action combination skipped without raising
- changed_keys correct for updates/replacements
"""


from cloud_janitor.core.plan_diff import PlanPreview, summarize_plan


def _make_change(
    address: str = "aws_instance.foo",
    resource_type: str = "aws_instance",
    mode: str = "managed",
    actions: list[str] | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> dict:
    """Helper to build a single resource_change entry."""
    return {
        "address": address,
        "type": resource_type,
        "mode": mode,
        "change": {
            "actions": actions or ["create"],
            "before": before,
            "after": after,
        },
    }


class TestSummarizePlanCreateBucket:
    """Action bucket: creates."""

    def test_create_action_lands_in_creates(self):
        plan_json = {
            "resource_changes": [
                _make_change(
                    address="aws_ebs_snapshot.pre_remediation_vol_abc",
                    resource_type="aws_ebs_snapshot",
                    actions=["create"],
                    after={"volume_id": "vol-abc123"},
                ),
            ]
        }
        result = summarize_plan(plan_json)
        assert len(result.creates) == 1
        assert result.creates[0].address == "aws_ebs_snapshot.pre_remediation_vol_abc"
        assert result.creates[0].resource_type == "aws_ebs_snapshot"
        assert result.creates[0].actions == ["create"]
        assert result.creates[0].changed_keys == []  # creates don't track changed_keys

    def test_create_does_not_appear_in_other_buckets(self):
        plan_json = {
            "resource_changes": [
                _make_change(actions=["create"]),
            ]
        }
        result = summarize_plan(plan_json)
        assert len(result.updates) == 0
        assert len(result.deletes) == 0
        assert len(result.replacements) == 0


class TestSummarizePlanUpdateBucket:
    """Action bucket: updates."""

    def test_update_action_lands_in_updates(self):
        plan_json = {
            "resource_changes": [
                _make_change(
                    address="aws_security_group_rule.remediate_sg_123",
                    resource_type="aws_security_group_rule",
                    actions=["update"],
                    before={"cidr_blocks": ["0.0.0.0/0"], "from_port": 22},
                    after={"cidr_blocks": ["10.0.0.0/16"], "from_port": 22},
                ),
            ]
        }
        result = summarize_plan(plan_json)
        assert len(result.updates) == 1
        assert result.updates[0].address == "aws_security_group_rule.remediate_sg_123"
        assert result.updates[0].actions == ["update"]

    def test_update_changed_keys_correct(self):
        plan_json = {
            "resource_changes": [
                _make_change(
                    actions=["update"],
                    before={"cidr_blocks": ["0.0.0.0/0"], "from_port": 22, "protocol": "tcp"},
                    after={"cidr_blocks": ["10.0.0.0/16"], "from_port": 22, "protocol": "tcp"},
                ),
            ]
        }
        result = summarize_plan(plan_json)
        assert result.updates[0].changed_keys == ["cidr_blocks"]


class TestSummarizePlanDeleteBucket:
    """Action bucket: deletes."""

    def test_delete_action_lands_in_deletes(self):
        plan_json = {
            "resource_changes": [
                _make_change(
                    address="aws_ebs_volume.old_vol",
                    resource_type="aws_ebs_volume",
                    actions=["delete"],
                    before={"volume_id": "vol-old"},
                ),
            ]
        }
        result = summarize_plan(plan_json)
        assert len(result.deletes) == 1
        assert result.deletes[0].address == "aws_ebs_volume.old_vol"
        assert result.deletes[0].actions == ["delete"]
        assert result.deletes[0].changed_keys == []  # deletes don't track changed_keys


class TestSummarizePlanReplaceBucket:
    """Action bucket: replacements (create+delete)."""

    def test_create_delete_lands_in_replacements(self):
        plan_json = {
            "resource_changes": [
                _make_change(
                    address="aws_instance.web",
                    resource_type="aws_instance",
                    actions=["create", "delete"],
                    before={"ami": "ami-old", "instance_type": "t2.micro"},
                    after={"ami": "ami-new", "instance_type": "t2.micro"},
                ),
            ]
        }
        result = summarize_plan(plan_json)
        assert len(result.replacements) == 1
        assert result.replacements[0].address == "aws_instance.web"
        assert result.replacements[0].actions == ["create", "delete"]

    def test_delete_create_also_lands_in_replacements(self):
        plan_json = {
            "resource_changes": [
                _make_change(
                    address="aws_instance.web",
                    resource_type="aws_instance",
                    actions=["delete", "create"],
                    before={"ami": "ami-old"},
                    after={"ami": "ami-new"},
                ),
            ]
        }
        result = summarize_plan(plan_json)
        assert len(result.replacements) == 1
        assert result.replacements[0].actions == ["delete", "create"]

    def test_replacement_changed_keys_correct(self):
        plan_json = {
            "resource_changes": [
                _make_change(
                    actions=["create", "delete"],
                    before={"ami": "ami-old", "instance_type": "t2.micro", "tags": {}},
                    after={"ami": "ami-new", "instance_type": "t2.micro", "tags": {"Name": "web"}},
                ),
            ]
        }
        result = summarize_plan(plan_json)
        assert result.replacements[0].changed_keys == ["ami", "tags"]


class TestSummarizePlanExclusions:
    """Entries that should be excluded from all buckets."""

    def test_mode_data_excluded(self):
        plan_json = {
            "resource_changes": [
                _make_change(mode="data", actions=["read"]),
            ]
        }
        result = summarize_plan(plan_json)
        assert len(result.creates) == 0
        assert len(result.updates) == 0
        assert len(result.deletes) == 0
        assert len(result.replacements) == 0

    def test_no_op_excluded(self):
        plan_json = {
            "resource_changes": [
                _make_change(actions=["no-op"]),
            ]
        }
        result = summarize_plan(plan_json)
        assert len(result.creates) == 0
        assert len(result.updates) == 0
        assert len(result.deletes) == 0
        assert len(result.replacements) == 0

    def test_unrecognized_action_skipped_without_raising(self):
        plan_json = {
            "resource_changes": [
                _make_change(actions=["import"]),
                _make_change(
                    address="aws_s3_bucket.real",
                    actions=["create"],
                    after={"bucket": "my-bucket"},
                ),
            ]
        }
        result = summarize_plan(plan_json)
        # The unrecognized "import" action is skipped, the "create" is processed
        assert len(result.creates) == 1
        assert result.creates[0].address == "aws_s3_bucket.real"

    def test_empty_resource_changes(self):
        result = summarize_plan({"resource_changes": []})
        assert result == PlanPreview()

    def test_missing_resource_changes_key(self):
        result = summarize_plan({})
        assert result == PlanPreview()
