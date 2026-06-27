"""Unit tests for SavingsTracker."""
import json
import tempfile
from pathlib import Path

import pytest

from savings import SavingsTracker


@pytest.fixture
def tracker_env(tmp_path):
    """Create a temporary environment with findings_store and ledger paths."""
    findings = {
        "scan_id": "test-001",
        "completed_at": "2026-06-27T05:31:48.159990+00:00",
        "findings": [
            {"resource_id": "res-1", "cost_estimate_monthly": 10.0},
            {"resource_id": "res-2", "cost_estimate_monthly": 20.0},
            {"resource_id": "res-3", "cost_estimate_monthly": 5.0},
        ],
    }
    findings_path = tmp_path / "findings_store.json"
    ledger_path = tmp_path / "savings_ledger.json"
    findings_path.write_text(json.dumps(findings))

    tracker = SavingsTracker(ledger_path=ledger_path, findings_store_path=findings_path)
    return tracker, ledger_path, findings_path


def test_record_run_new(tracker_env):
    """First record_run creates ledger with correct structure and content."""
    tracker, ledger_path, _ = tracker_env
    result = tracker.record_run(["res-1", "res-2"])
    assert result is True
    assert ledger_path.exists()

    # Verify ledger has correct top-level schema
    ledger = json.loads(ledger_path.read_text())
    assert "total_lifetime_savings" in ledger
    assert "runs" in ledger
    assert isinstance(ledger["runs"], list)
    assert len(ledger["runs"]) == 1
    assert ledger["total_lifetime_savings"] == 30.0  # res-1=10 + res-2=20


def test_record_run_duplicate(tracker_env):
    tracker, _, _ = tracker_env
    tracker.record_run(["res-1", "res-2"])
    result = tracker.record_run(["res-1", "res-2"])
    assert result is False


def test_duplicate_does_not_modify_file(tracker_env):
    """SavingsTracker must NOT update total_lifetime_savings when duplicate run_id is detected.

    Verifies both that the file mtime is unchanged AND that total_lifetime_savings
    remains the same value — not incremented by re-processing the same run.
    """
    tracker, ledger_path, _ = tracker_env
    tracker.record_run(["res-1"])

    # Read total after first write
    ledger_before = json.loads(ledger_path.read_text())
    total_before = ledger_before["total_lifetime_savings"]
    assert total_before == 10.0  # res-1 costs 10.0/mo

    mtime_before = ledger_path.stat().st_mtime

    # Attempt duplicate
    result = tracker.record_run(["res-1"])
    assert result is False

    mtime_after = ledger_path.stat().st_mtime
    assert mtime_before == mtime_after, (
        "File mtime changed on duplicate run — ledger was improperly rewritten"
    )

    # Verify total_lifetime_savings was NOT inflated
    ledger_after = json.loads(ledger_path.read_text())
    assert ledger_after["total_lifetime_savings"] == total_before, (
        f"total_lifetime_savings changed from {total_before} to "
        f"{ledger_after['total_lifetime_savings']} on duplicate run"
    )


def test_savings_summary_after_run(tracker_env):
    tracker, _, _ = tracker_env
    tracker.record_run(["res-1", "res-2"])
    summary = tracker.get_savings_summary()
    assert summary["total_lifetime_monthly"] == 30.0
    assert summary["total_lifetime_annual"] == 360.0
    assert summary["total_runs"] == 1
    assert summary["last_run_savings"] == 30.0


def test_savings_summary_partial_resources(tracker_env):
    tracker, _, _ = tracker_env
    tracker.record_run(["res-1"])
    summary = tracker.get_savings_summary()
    assert summary["total_lifetime_monthly"] == 10.0
    assert summary["total_lifetime_annual"] == 120.0
    assert summary["last_run_savings"] == 10.0


def test_savings_summary_empty_ledger(tracker_env):
    tracker, _, _ = tracker_env
    summary = tracker.get_savings_summary()
    assert summary["total_lifetime_monthly"] == 0.0
    assert summary["total_lifetime_annual"] == 0.0
    assert summary["total_runs"] == 0
    assert summary["last_run_savings"] == 0.0


def test_missing_ledger_file(tracker_env):
    tracker, ledger_path, _ = tracker_env
    tracker.record_run(["res-1"])
    ledger_path.unlink()
    summary = tracker.get_savings_summary()
    assert summary["total_lifetime_monthly"] == 0.0
    assert summary["total_runs"] == 0


def test_corrupt_ledger_file(tracker_env):
    tracker, ledger_path, _ = tracker_env
    ledger_path.write_text("not valid json")
    summary = tracker.get_savings_summary()
    assert summary["total_lifetime_monthly"] == 0.0
    assert summary["total_runs"] == 0


def test_missing_cost_estimate_treated_as_zero(tmp_path):
    findings = {
        "scan_id": "test-002",
        "completed_at": "2026-06-27T06:00:00+00:00",
        "findings": [
            {"resource_id": "res-no-cost"},
        ],
    }
    findings_path = tmp_path / "findings_store.json"
    ledger_path = tmp_path / "savings_ledger.json"
    findings_path.write_text(json.dumps(findings))

    tracker = SavingsTracker(ledger_path=ledger_path, findings_store_path=findings_path)
    tracker.record_run(["res-no-cost"])
    summary = tracker.get_savings_summary()
    assert summary["total_lifetime_monthly"] == 0.0
    assert summary["total_runs"] == 1


def test_run_entry_schema(tracker_env):
    tracker, ledger_path, _ = tracker_env
    tracker.record_run(["res-1", "res-3"])
    ledger = json.loads(ledger_path.read_text())

    assert ledger["total_lifetime_savings"] == 15.0
    assert len(ledger["runs"]) == 1

    entry = ledger["runs"][0]
    assert entry["run_id"] == "test-001"
    assert entry["timestamp"] == "2026-06-27T05:31:48.159990+00:00"
    assert entry["resources_remediated"] == ["res-1", "res-3"]
    assert entry["monthly_savings_added"] == 15.0
    assert entry["cumulative_at_time"] == 15.0


def test_recalculate_from_source(tmp_path):
    """Verify total is recalculated from source, not incremented."""
    findings_path = tmp_path / "findings_store.json"
    ledger_path = tmp_path / "savings_ledger.json"

    # First run
    findings = {
        "scan_id": "run-1",
        "completed_at": "2026-06-27T01:00:00+00:00",
        "findings": [{"resource_id": "a", "cost_estimate_monthly": 10.0}],
    }
    findings_path.write_text(json.dumps(findings))
    tracker = SavingsTracker(ledger_path=ledger_path, findings_store_path=findings_path)
    tracker.record_run(["a"])

    # Second run with different scan_id
    findings["scan_id"] = "run-2"
    findings["findings"] = [{"resource_id": "b", "cost_estimate_monthly": 25.0}]
    findings_path.write_text(json.dumps(findings))
    tracker.record_run(["b"])

    ledger = json.loads(ledger_path.read_text())
    assert ledger["total_lifetime_savings"] == 35.0
    assert ledger["runs"][-1]["cumulative_at_time"] == 35.0
