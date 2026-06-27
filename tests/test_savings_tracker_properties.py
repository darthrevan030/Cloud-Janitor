"""Property-based tests for SavingsTracker.

Uses Hypothesis to validate universal correctness properties across
randomly generated inputs.
"""

import json
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

@settings(max_examples=100)
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
        assert abs(entry["monthly_savings_added"] - expected_savings) < 1e-9, (
            f"Expected {expected_savings}, got {entry['monthly_savings_added']}"
        )
