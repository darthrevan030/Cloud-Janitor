"""Property-based tests for core/plan_diff.py — Property 1: Partition Completeness.

Every managed non-no-op entry with a recognized action tuple appears in exactly
one bucket of PlanPreview. Entries with mode!="managed", actions==["no-op"],
or unrecognized action tuples appear in zero buckets.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from cloud_janitor.core.plan_diff import summarize_plan, _ACTION_BUCKET


# Strategy: generate a list of resource_changes entries
_MODES = st.sampled_from(["managed", "data"])
_RECOGNIZED_ACTIONS = [["create"], ["update"], ["delete"], ["create", "delete"], ["delete", "create"]]
_ALL_ACTIONS = st.sampled_from(
    _RECOGNIZED_ACTIONS + [["no-op"], ["import"], ["read"], ["forget"]]
)

_KEY_STRATEGY = st.text(
    min_size=1, max_size=10,
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_"),
)
_VALUE_STRATEGY = st.one_of(st.text(max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))), st.integers(), st.booleans())

_ATTRS = st.dictionaries(keys=_KEY_STRATEGY, values=_VALUE_STRATEGY, min_size=0, max_size=5)


@st.composite
def resource_change_entry(draw):
    mode = draw(_MODES)
    actions = draw(_ALL_ACTIONS)
    address = draw(st.text(min_size=1, max_size=40, alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_.")))
    resource_type = draw(st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_")))
    before = draw(_ATTRS)
    after = draw(_ATTRS)
    return {
        "address": address,
        "type": resource_type,
        "mode": mode,
        "change": {
            "actions": actions,
            "before": before,
            "after": after,
        },
    }


@given(changes=st.lists(resource_change_entry(), min_size=0, max_size=20))
@settings(max_examples=100, deadline=None)
def test_partition_completeness(changes):
    """Property 1: every managed non-no-op entry with a recognized action
    tuple appears in exactly one bucket; all others appear in zero buckets."""
    plan_json = {"resource_changes": changes}
    result = summarize_plan(plan_json)

    bucket_lists = [result.creates, result.updates, result.deletes, result.replacements]

    # Count how many entries from each bucket exist total
    total_in_buckets = sum(len(b) for b in bucket_lists)

    # Count how many input entries SHOULD appear (managed, non-no-op, recognized action)
    expected_count = 0
    for change in changes:
        mode = change.get("mode")
        actions = change["change"]["actions"]
        is_managed = mode == "managed"
        is_no_op = actions == ["no-op"]
        is_recognized = tuple(sorted(actions)) in _ACTION_BUCKET
        if is_managed and not is_no_op and is_recognized:
            expected_count += 1

    # Total partition size must equal exactly the number of qualifying entries
    assert total_in_buckets == expected_count, (
        f"Expected {expected_count} entries in buckets, got {total_in_buckets}"
    )

    # No entry in any bucket should have mode != "managed" or actions == ["no-op"]
    # (all bucket entries must correspond to qualifying input entries)
    for bucket in bucket_lists:
        for entry in bucket:
            assert entry.actions != ["no-op"], f"no-op entry found in bucket: {entry}"
            # Verify the action tuple is recognized
            assert tuple(sorted(entry.actions)) in _ACTION_BUCKET, (
                f"Unrecognized actions in bucket: {entry.actions}"
            )
