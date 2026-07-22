"""Structured summary of a `terraform plan -json` result.

Reuses the exact plan_json produced by phase1-trust-hardening's
_check_plan_scope() call site — this module only adds a
human-readable partition on top of the same data, it does not
invoke `terraform plan` itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlanChange:
    address: str
    resource_type: str
    actions: list[str]
    changed_keys: list[str] = field(default_factory=list)


@dataclass
class PlanPreview:
    creates: list[PlanChange] = field(default_factory=list)
    updates: list[PlanChange] = field(default_factory=list)
    deletes: list[PlanChange] = field(default_factory=list)
    replacements: list[PlanChange] = field(default_factory=list)


_ACTION_BUCKET = {
    ("create",): "creates",
    ("update",): "updates",
    ("delete",): "deletes",
    ("create", "delete"): "replacements",
    ("delete", "create"): "replacements",
}


def summarize_plan(plan_json: dict) -> PlanPreview:
    """Partition resource_changes into creates/updates/deletes/replacements.

    Only processes entries with mode=="managed" and actions != ["no-op"].
    Unrecognized action combinations are skipped (not raised).
    """
    preview = PlanPreview()
    for change in plan_json.get("resource_changes", []):
        if change.get("mode") != "managed":
            continue
        actions = change["change"]["actions"]
        if actions == ["no-op"]:
            continue
        bucket_name = _ACTION_BUCKET.get(tuple(sorted(actions)))
        if bucket_name is None:
            continue  # unrecognized action combination — skip, don't crash the UI

        before = change["change"].get("before") or {}
        after = change["change"].get("after") or {}
        changed_keys = sorted(
            k for k in set(before) | set(after) if before.get(k) != after.get(k)
        ) if bucket_name in ("updates", "replacements") else []

        entry = PlanChange(
            address=change["address"],
            resource_type=change.get("type", ""),
            actions=actions,
            changed_keys=changed_keys,
        )
        getattr(preview, bucket_name).append(entry)
    return preview
