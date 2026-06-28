"""Property-based tests for SavingsTracker.

Uses Hypothesis to validate universal correctness properties across
randomly generated inputs.
"""

import json
import math
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from savings import SavingsTracker


# --- Strategies ---

# Generate valid UUIDs as strings for scan_id
scan_id_strategy = st.uuids().map(str)

# Generate ISO 8601 datetime strings for completed_at
completed_at_strategy = st.datetimes().map(lambda dt: dt.isoformat())

# Generate a single finding with resource_id and cost_estimate_monthly
finding_strategy = st.fixed_dictionaries({
    "resource_id": st.text(min_size=1, max_size=50, alphabet=st.characters(
        whitelist_categories=("L", "N", "P"),
        blacklist_characters=("\x00",),
    )),
    "cost_estimate_monthly": st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
})

# Generate a non-empty list of findings with unique resource_ids
findings_strategy = st.lists(finding_strategy, min_size=1, max_size=20).map(
    lambda findings: {f["resource_id"]: f for f in findings}.values()
).map(list)  # deduplicate by resource_id to avoid ambiguity


# --- Property 1: RunEntry schema and field correctness ---
# Feature: savings-tracker-localstack, Property 1: RunEntry schema and field correctness

@settings(max_examples=100, deadline=None)
@given(
    scan_id=scan_id_strategy,
    completed_at=completed_at_strategy,
    findings=findings_strategy,
    data=st.data(),
)
def test_run_entry_schema_and_field_correctness(scan_id, completed_at, findings, data):
    """
    Property 1: RunEntry schema and field correctness

    For any valid findings_store.json (containing a scan_id, completed_at, and
    findings with cost_estimate_monthly) and for any non-empty list of
    resources_remediated whose IDs appear in findings, after calling record_run(),
    the appended RunEntry SHALL contain all required keys (run_id, timestamp,
    resources_remediated, monthly_savings_added, cumulative_at_time) with run_id
    equal to the findings_store scan_id and timestamp equal to completed_at.

    **Validates: Requirements 1.2, 2.1**
    """
    # Select a non-empty subset of resource_ids from findings
    resource_ids = [f["resource_id"] for f in findings]
    subset_size = data.draw(st.integers(min_value=1, max_value=len(resource_ids)))
    resources_remediated = data.draw(
        st.lists(
            st.sampled_from(resource_ids),
            min_size=subset_size,
            max_size=subset_size,
            unique=True,
        )
    )
    assume(len(resources_remediated) > 0)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Set up findings_store.json
        findings_store = {
            "scan_id": scan_id,
            "completed_at": completed_at,
            "findings": findings,
        }
        findings_path = tmp_path / "findings_store.json"
        findings_path.write_text(json.dumps(findings_store), encoding="utf-8")

        # Set up ledger path (doesn't exist yet)
        ledger_path = tmp_path / "savings_ledger.json"

        # Create tracker and record a run
        tracker = SavingsTracker(ledger_path=ledger_path, findings_store_path=findings_path)
        result = tracker.record_run(resources_remediated)

        # The run should be recorded successfully
        assert result is True

        # Read back the ledger
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))

        # Verify ledger top-level schema
        assert "total_lifetime_savings" in ledger
        assert "runs" in ledger
        assert len(ledger["runs"]) == 1

        # Verify RunEntry has all required keys
        entry = ledger["runs"][0]
        required_keys = {"run_id", "timestamp", "resources_remediated", "monthly_savings_added", "cumulative_at_time"}
        assert required_keys.issubset(entry.keys()), (
            f"Missing keys: {required_keys - set(entry.keys())}"
        )

        # Verify run_id equals scan_id from findings_store
        assert entry["run_id"] == scan_id

        # Verify timestamp equals completed_at from findings_store
        assert entry["timestamp"] == completed_at

        # Verify resources_remediated matches what was passed in
        assert entry["resources_remediated"] == resources_remediated

        # Verify monthly_savings_added is a non-negative float
        assert isinstance(entry["monthly_savings_added"], (int, float))
        assert entry["monthly_savings_added"] >= 0.0

        # Verify cumulative_at_time is a non-negative float
        assert isinstance(entry["cumulative_at_time"], (int, float))
        assert entry["cumulative_at_time"] >= 0.0


# --- Property 2: Monthly savings computation ---
# Feature: savings-tracker-localstack, Property 2: Monthly savings computation

@settings(max_examples=100)
@given(
    scan_id=scan_id_strategy,
    completed_at=completed_at_strategy,
    findings=findings_strategy,
    data=st.data(),
)
def test_monthly_savings_computation(scan_id, completed_at, findings, data):
    """
    Property 2: Monthly savings computation

    For any findings_store.json containing N findings with arbitrary
    cost_estimate_monthly values and for any subset S of resource IDs from
    those findings passed to record_run(), the resulting RunEntry's
    monthly_savings_added SHALL equal the sum of cost_estimate_monthly for
    exactly those findings whose resource_id is in S.

    **Validates: Requirements 2.2**
    """
    # Draw a random subset of resource_ids from findings (can be empty)
    resource_ids = [f["resource_id"] for f in findings]
    subset = data.draw(
        st.lists(st.sampled_from(resource_ids), unique=True, min_size=0, max_size=len(resource_ids))
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Set up findings_store.json
        findings_store = {
            "scan_id": scan_id,
            "completed_at": completed_at,
            "findings": findings,
        }
        findings_path = tmp_path / "findings_store.json"
        findings_path.write_text(json.dumps(findings_store), encoding="utf-8")

        # Set up empty ledger
        ledger_path = tmp_path / "savings_ledger.json"

        # Create tracker and record a run
        tracker = SavingsTracker(ledger_path=ledger_path, findings_store_path=findings_path)
        result = tracker.record_run(subset)

        # The run should be recorded successfully
        assert result is True

        # Read back the ledger
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        entry = ledger["runs"][0]

        # Compute expected monthly savings: sum of cost_estimate_monthly
        # for findings whose resource_id is in the subset
        expected_savings = sum(
            f["cost_estimate_monthly"]
            for f in findings
            if f["resource_id"] in subset
        )

        # Verify monthly_savings_added equals the expected sum
        # Use relative tolerance to handle floating-point precision with large sums
        assert math.isclose(entry["monthly_savings_added"], expected_savings, rel_tol=1e-9, abs_tol=1e-9), (
            f"Expected {expected_savings}, got {entry['monthly_savings_added']}"
        )


# --- Property 3: Recalculate-from-source invariant ---
# Feature: savings-tracker-localstack, Property 3: Recalculate-from-source invariant

# Strategy for generating a sequence of runs (1-10 runs, each with distinct findings)
run_sequence_strategy = st.lists(
    st.fixed_dictionaries({
        "scan_id": scan_id_strategy,
        "completed_at": completed_at_strategy,
        "findings": findings_strategy,
    }),
    min_size=1,
    max_size=10,
    unique_by=lambda x: x["scan_id"],
)


@settings(max_examples=100, deadline=None)
@given(
    run_sequence=run_sequence_strategy,
    data=st.data(),
)
def test_recalculate_from_source_invariant(run_sequence, data):
    """
    Property 3: Recalculate-from-source invariant

    For any sequence of K calls to record_run() (each with distinct run_ids),
    after the K-th write, both total_lifetime_savings and the K-th entry's
    cumulative_at_time SHALL equal the sum of monthly_savings_added across
    all K entries in the runs array. Neither value shall be computed by
    incremental addition from a prior stored total.

    **Validates: Requirements 2.3, 2.4**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        findings_path = tmp_path / "findings_store.json"
        ledger_path = tmp_path / "savings_ledger.json"

        for k, run_data in enumerate(run_sequence):
            findings = run_data["findings"]
            resource_ids = [f["resource_id"] for f in findings]

            # Draw a non-empty subset of resources for this run
            subset = data.draw(
                st.lists(
                    st.sampled_from(resource_ids),
                    min_size=1,
                    max_size=len(resource_ids),
                    unique=True,
                )
            )

            # Write findings_store.json with the current run's data
            findings_store = {
                "scan_id": run_data["scan_id"],
                "completed_at": run_data["completed_at"],
                "findings": findings,
            }
            findings_path.write_text(json.dumps(findings_store), encoding="utf-8")

            # Record this run
            tracker = SavingsTracker(ledger_path=ledger_path, findings_store_path=findings_path)
            result = tracker.record_run(subset)
            assert result is True

            # Read back the ledger
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))

            # Verify we have k+1 runs
            assert len(ledger["runs"]) == k + 1

            # Compute expected total: sum of monthly_savings_added across ALL entries
            expected_total = sum(r["monthly_savings_added"] for r in ledger["runs"])

            # Verify total_lifetime_savings equals the sum from source
            assert abs(ledger["total_lifetime_savings"] - expected_total) < 1e-9, (
                f"After run {k+1}: total_lifetime_savings={ledger['total_lifetime_savings']} "
                f"but sum of monthly_savings_added={expected_total}"
            )

            # Verify the K-th entry's cumulative_at_time equals the same sum
            assert abs(ledger["runs"][k]["cumulative_at_time"] - expected_total) < 1e-9, (
                f"After run {k+1}: cumulative_at_time={ledger['runs'][k]['cumulative_at_time']} "
                f"but sum of monthly_savings_added={expected_total}"
            )


# --- Property 4: Duplicate run idempotency ---
# Feature: savings-tracker-localstack, Property 4: Duplicate run idempotency

import os
import time


@settings(max_examples=100, deadline=None)
@given(
    scan_id=scan_id_strategy,
    completed_at=completed_at_strategy,
    findings=findings_strategy,
    data=st.data(),
)
def test_duplicate_run_idempotency(scan_id, completed_at, findings, data):
    """
    Property 4: Duplicate run idempotency

    For any ledger containing one or more RunEntry objects, calling record_run()
    with a run_id that already exists in the runs array SHALL leave the file
    completely unmodified — the file's modification time (mtime) SHALL be
    identical before and after the call, and the runs array length SHALL remain
    unchanged.

    **Validates: Requirements 3.1, 3.3**
    """
    # Select a non-empty subset of resource_ids from findings
    resource_ids = [f["resource_id"] for f in findings]
    subset = data.draw(
        st.lists(
            st.sampled_from(resource_ids),
            min_size=1,
            max_size=len(resource_ids),
            unique=True,
        )
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Set up findings_store.json
        findings_store = {
            "scan_id": scan_id,
            "completed_at": completed_at,
            "findings": findings,
        }
        findings_path = tmp_path / "findings_store.json"
        findings_path.write_text(json.dumps(findings_store), encoding="utf-8")

        # Set up ledger path
        ledger_path = tmp_path / "savings_ledger.json"

        # Record the first run successfully
        tracker = SavingsTracker(ledger_path=ledger_path, findings_store_path=findings_path)
        first_result = tracker.record_run(subset)
        assert first_result is True

        # Read the ledger after first write to get runs array length
        ledger_after_first = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert len(ledger_after_first["runs"]) == 1

        # Small sleep to ensure filesystem mtime would differ if file were rewritten
        time.sleep(0.05)

        # Get mtime after first write (before duplicate call)
        mtime_before = os.path.getmtime(str(ledger_path))

        # Call record_run() again with the same scan_id (duplicate)
        tracker2 = SavingsTracker(ledger_path=ledger_path, findings_store_path=findings_path)
        duplicate_result = tracker2.record_run(subset)

        # Verify record_run() returns False (duplicate detected)
        assert duplicate_result is False

        # Verify the file's mtime has NOT changed
        mtime_after = os.path.getmtime(str(ledger_path))
        assert mtime_before == mtime_after, (
            f"File was modified on duplicate run: mtime_before={mtime_before}, mtime_after={mtime_after}"
        )

        # Verify the runs array length is still 1
        ledger_after_dup = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert len(ledger_after_dup["runs"]) == 1


# --- Property 5: Savings summary correctness ---
# Feature: savings-tracker-localstack, Property 5: Savings summary correctness

# Strategy for generating a list of RunEntry dicts (1-20 entries)
run_entry_strategy = st.fixed_dictionaries({
    "run_id": scan_id_strategy,
    "timestamp": completed_at_strategy,
    "resources_remediated": st.lists(
        st.text(min_size=1, max_size=30, alphabet=st.characters(
            whitelist_categories=("L", "N"),
        )),
        min_size=1,
        max_size=5,
    ),
    "monthly_savings_added": st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
    "cumulative_at_time": st.floats(min_value=0.0, max_value=1e7, allow_nan=False, allow_infinity=False),
})

runs_strategy = st.lists(
    run_entry_strategy,
    min_size=1,
    max_size=20,
    unique_by=lambda x: x["run_id"],
)


@settings(max_examples=100)
@given(runs=runs_strategy)
def test_savings_summary_correctness(runs):
    """
    Property 5: Savings summary correctness

    For any ledger state with N >= 1 runs, get_savings_summary() SHALL return
    a dictionary where:
    - total_lifetime_annual equals total_lifetime_monthly * 12
    - total_runs equals the number of entries in the runs array
    - last_run_savings equals the monthly_savings_added value of the most
      recent (last) RunEntry

    **Validates: Requirements 4.1, 4.2, 4.4**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Compute total_lifetime_savings as sum of all monthly_savings_added
        total_lifetime_savings = sum(r["monthly_savings_added"] for r in runs)

        # Write ledger directly to disk
        ledger = {
            "total_lifetime_savings": total_lifetime_savings,
            "runs": runs,
        }
        ledger_path = tmp_path / "savings_ledger.json"
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

        # findings_store.json not needed for get_savings_summary(), but provide
        # a valid path to satisfy the constructor
        findings_path = tmp_path / "findings_store.json"
        findings_path.write_text(json.dumps({"scan_id": "", "completed_at": "", "findings": []}), encoding="utf-8")

        # Create tracker and get summary
        tracker = SavingsTracker(ledger_path=ledger_path, findings_store_path=findings_path)
        summary = tracker.get_savings_summary()

        # Verify required keys exist
        assert "total_lifetime_monthly" in summary
        assert "total_lifetime_annual" in summary
        assert "total_runs" in summary
        assert "last_run_savings" in summary

        # Property: total_lifetime_annual == total_lifetime_monthly * 12
        assert abs(summary["total_lifetime_annual"] - summary["total_lifetime_monthly"] * 12) < 1e-9, (
            f"Annual {summary['total_lifetime_annual']} != monthly {summary['total_lifetime_monthly']} * 12"
        )

        # Property: total_runs == number of entries in runs array
        assert summary["total_runs"] == len(runs), (
            f"total_runs {summary['total_runs']} != len(runs) {len(runs)}"
        )

        # Property: last_run_savings == monthly_savings_added of the last RunEntry
        expected_last_run_savings = runs[-1]["monthly_savings_added"]
        assert abs(summary["last_run_savings"] - expected_last_run_savings) < 1e-9, (
            f"last_run_savings {summary['last_run_savings']} != "
            f"last entry monthly_savings_added {expected_last_run_savings}"
        )
