"""Property-based tests for SavingsTracker.record_rollback().

Property 3: Savings Reversal Idempotence Per (resource_id, run_id)

Validates: Requirements 2.2, 2.3
"""

import json
import tempfile
from pathlib import Path

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from cloud_janitor.agents.savings_tracker import SavingsTracker


# --- Strategies ---

# Non-empty resource_id: printable, no NUL, reasonable length
resource_id_strategy = st.text(
    min_size=1,
    max_size=60,
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P"),
        blacklist_characters=("\x00",),
    ),
)

# UUID-style run_id
run_id_strategy = st.uuids().map(str)

# Positive monthly savings (must be > 0 so reversal is observable)
monthly_savings_strategy = st.floats(
    min_value=0.01, max_value=1e6, allow_nan=False, allow_infinity=False
)

# ISO 8601 timestamp
timestamp_strategy = st.datetimes().map(lambda dt: dt.isoformat())


def _build_ledger_with_run(
    run_id: str,
    resource_id: str,
    monthly_savings_added: float,
    timestamp: str,
) -> dict:
    """Build a minimal valid ledger containing a single remediation run."""
    return {
        "total_lifetime_savings": monthly_savings_added,
        "runs": [
            {
                "run_id": run_id,
                "timestamp": timestamp,
                "resources_remediated": [resource_id],
                "monthly_savings_added": monthly_savings_added,
                "cumulative_at_time": monthly_savings_added,
            }
        ],
    }


def _build_ledger_with_two_runs(
    run_id_1: str,
    run_id_2: str,
    resource_id: str,
    savings_1: float,
    savings_2: float,
    timestamp: str,
) -> dict:
    """Build a ledger containing two distinct remediation runs for the same resource."""
    total = savings_1 + savings_2
    return {
        "total_lifetime_savings": total,
        "runs": [
            {
                "run_id": run_id_1,
                "timestamp": timestamp,
                "resources_remediated": [resource_id],
                "monthly_savings_added": savings_1,
                "cumulative_at_time": savings_1,
            },
            {
                "run_id": run_id_2,
                "timestamp": timestamp,
                "resources_remediated": [resource_id],
                "monthly_savings_added": savings_2,
                "cumulative_at_time": total,
            },
        ],
    }


def _make_tracker(tmp_path: Path, ledger: dict) -> tuple[SavingsTracker, Path]:
    """Write ledger to disk and return a configured tracker + ledger path."""
    ledger_path = tmp_path / "savings_ledger.json"
    ledger_path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")

    # findings_store not needed for rollback (rollback reads from ledger),
    # but constructor requires a path
    findings_path = tmp_path / "findings_store.json"
    findings_path.write_text(
        json.dumps({"scan_id": "", "completed_at": "", "findings": []}),
        encoding="utf-8",
    )

    tracker = SavingsTracker(ledger_path=ledger_path, findings_store_path=findings_path)
    return tracker, ledger_path


# --- Property 3a: Single-run reversal idempotence ---


@settings(max_examples=200, deadline=None)
@given(
    resource_id=resource_id_strategy,
    run_id=run_id_strategy,
    monthly_savings=monthly_savings_strategy,
    timestamp=timestamp_strategy,
)
def test_reversal_idempotence_single_run(
    resource_id, run_id, monthly_savings, timestamp
):
    """
    Property 3a: For any (resource_id, run_id) with ONE matching run,
    calling record_rollback(resource_id) twice on the same tracker:
      - First call returns True
      - Second call returns False (idempotent — no double-reverse)
      - Ledger total after both calls equals original savings minus ONE
        reversal amount (not double-subtracted)

    Validates: Requirements 2.2, 2.3
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Build ledger with one run containing our resource
        ledger = _build_ledger_with_run(run_id, resource_id, monthly_savings, timestamp)
        original_total = ledger["total_lifetime_savings"]

        tracker, ledger_path = _make_tracker(tmp_path, ledger)

        # --- First rollback: must succeed ---
        first_result = tracker.record_rollback(resource_id)
        assert first_result is True, (
            f"First record_rollback({resource_id!r}) should return True "
            f"when a matching run exists"
        )

        # --- Second rollback: must be idempotent (no match remaining) ---
        second_result = tracker.record_rollback(resource_id)
        assert second_result is False, (
            f"Second record_rollback({resource_id!r}) should return False "
            f"(already reversed for this run_id={run_id!r})"
        )

        # --- Verify ledger total: original minus exactly ONE reversal ---
        final_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        expected_total = original_total - monthly_savings

        assert abs(final_ledger["total_lifetime_savings"] - expected_total) < 1e-9, (
            f"Ledger total should be {expected_total} "
            f"(original {original_total} - one reversal {monthly_savings}), "
            f"but got {final_ledger['total_lifetime_savings']}. "
            f"Double-subtraction bug detected if total < expected."
        )

        # --- Verify the rollback entry structure ---
        rollback_entries = [
            r for r in final_ledger["runs"] if r.get("type") == "rollback"
        ]
        assert len(rollback_entries) == 1, (
            f"Expected exactly 1 rollback entry, got {len(rollback_entries)}. "
            f"Idempotence violated if > 1."
        )

        rb_entry = rollback_entries[0]
        assert rb_entry["rolled_back_run_id"] == run_id
        assert rb_entry["monthly_savings_added"] == -monthly_savings, (
            f"Rollback entry savings should be -{monthly_savings}, "
            f"got {rb_entry['monthly_savings_added']}"
        )
        assert resource_id in rb_entry["resources_remediated"]


# --- Property 3b: Two separate runs, independent reversal ---


@settings(max_examples=200, deadline=None)
@given(
    resource_id=resource_id_strategy,
    run_id_1=run_id_strategy,
    run_id_2=run_id_strategy,
    savings_1=monthly_savings_strategy,
    savings_2=monthly_savings_strategy,
    timestamp=timestamp_strategy,
)
def test_reversal_independent_across_runs(
    resource_id, run_id_1, run_id_2, savings_1, savings_2, timestamp
):
    """
    Property 3b: Same resource_id remediated in two separate runs (distinct
    run_ids) → reversing each one separately both return True, and the total
    correctly reflects both reversals.

    After both reversals, a third call returns False (no remaining unreversed
    entries for this resource).

    Validates: Requirements 2.2, 2.3
    """
    # Ensure distinct run_ids — same run_id would be a duplicate, not two runs
    assume(run_id_1 != run_id_2)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Build ledger with two runs for the same resource
        ledger = _build_ledger_with_two_runs(
            run_id_1, run_id_2, resource_id, savings_1, savings_2, timestamp
        )
        original_total = ledger["total_lifetime_savings"]

        tracker, ledger_path = _make_tracker(tmp_path, ledger)

        # --- First rollback: reverses earliest matching run (run_1) ---
        first_result = tracker.record_rollback(resource_id)
        assert first_result is True, (
            f"First rollback for {resource_id!r} should succeed "
            f"(run_id_1={run_id_1!r} should be reversible)"
        )

        # Verify intermediate total: original minus savings_1 only
        intermediate_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        expected_after_first = original_total - savings_1
        assert abs(intermediate_ledger["total_lifetime_savings"] - expected_after_first) < 1e-9, (
            f"After first reversal, total should be {expected_after_first}, "
            f"got {intermediate_ledger['total_lifetime_savings']}"
        )

        # --- Second rollback: reverses the other run (run_2) ---
        second_result = tracker.record_rollback(resource_id)
        assert second_result is True, (
            f"Second rollback for {resource_id!r} should succeed "
            f"(run_id_2={run_id_2!r} should still be reversible)"
        )

        # Verify final total: original minus BOTH reversals
        final_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        expected_final = original_total - savings_1 - savings_2
        assert abs(final_ledger["total_lifetime_savings"] - expected_final) < 1e-9, (
            f"After both reversals, total should be {expected_final}, "
            f"got {final_ledger['total_lifetime_savings']}"
        )

        # --- Third rollback: no remaining unreversed runs → False ---
        third_result = tracker.record_rollback(resource_id)
        assert third_result is False, (
            f"Third rollback for {resource_id!r} should return False "
            f"(both runs already reversed)"
        )

        # --- Verify exactly 2 rollback entries exist ---
        rollback_entries = [
            r for r in final_ledger["runs"] if r.get("type") == "rollback"
        ]
        assert len(rollback_entries) == 2, (
            f"Expected exactly 2 rollback entries, got {len(rollback_entries)}"
        )

        # Verify each rollback targets a distinct run_id
        rolled_back_run_ids = {r["rolled_back_run_id"] for r in rollback_entries}
        assert rolled_back_run_ids == {run_id_1, run_id_2}, (
            f"Rollback entries should target run_ids {{{run_id_1}, {run_id_2}}}, "
            f"got {rolled_back_run_ids}"
        )

        # Verify each rollback negates the correct amount
        for rb in rollback_entries:
            if rb["rolled_back_run_id"] == run_id_1:
                assert rb["monthly_savings_added"] == -savings_1
            else:
                assert rb["monthly_savings_added"] == -savings_2


# --- Property 3c: Reversal with no matching run ---


@settings(max_examples=100, deadline=None)
@given(
    resource_id=resource_id_strategy,
    other_resource_id=resource_id_strategy,
    run_id=run_id_strategy,
    monthly_savings=monthly_savings_strategy,
    timestamp=timestamp_strategy,
)
def test_reversal_no_matching_run_returns_false(
    resource_id, other_resource_id, run_id, monthly_savings, timestamp
):
    """
    Negative property: record_rollback() for a resource_id that does NOT appear
    in any run's resources_remediated list returns False and leaves the ledger
    completely unchanged.

    This guards against false positives where record_rollback blindly succeeds.

    Validates: Requirement 2.2 (no-op when no match)
    """
    # Ensure the resource we roll back is different from the one in the ledger
    assume(resource_id != other_resource_id)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Build ledger with a run for `other_resource_id`
        ledger = _build_ledger_with_run(run_id, other_resource_id, monthly_savings, timestamp)
        original_total = ledger["total_lifetime_savings"]
        original_run_count = len(ledger["runs"])

        tracker, ledger_path = _make_tracker(tmp_path, ledger)

        # Attempt rollback for a resource NOT in the ledger
        result = tracker.record_rollback(resource_id)
        assert result is False, (
            f"record_rollback({resource_id!r}) should return False when "
            f"resource is not in any run"
        )

        # Verify ledger unchanged
        final_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert abs(final_ledger["total_lifetime_savings"] - original_total) < 1e-9, (
            "Ledger total should be unchanged after no-op rollback"
        )
        assert len(final_ledger["runs"]) == original_run_count, (
            "No new entries should be appended for a no-op rollback"
        )
