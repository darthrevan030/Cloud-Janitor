"""Smoke test for SavingsTracker."""
import json
import tempfile
from pathlib import Path

from savings import SavingsTracker


def test_savings_tracker():
    findings = {
        "scan_id": "test-001",
        "completed_at": "2026-06-27T05:31:48.159990+00:00",
        "findings": [
            {"resource_id": "res-1", "cost_estimate_monthly": 10.0},
            {"resource_id": "res-2", "cost_estimate_monthly": 20.0},
        ],
    }

    with tempfile.TemporaryDirectory() as td:
        findings_path = Path(td) / "findings_store.json"
        ledger_path = Path(td) / "savings_ledger.json"
        findings_path.write_text(json.dumps(findings))

        tracker = SavingsTracker(ledger_path=ledger_path, findings_store_path=findings_path)

        # Test record_run
        result = tracker.record_run(["res-1", "res-2"])
        assert result is True, "Expected True for new run"

        # Test duplicate detection
        result = tracker.record_run(["res-1", "res-2"])
        assert result is False, "Expected False for duplicate"

        # Test summary
        summary = tracker.get_savings_summary()
        assert summary["total_lifetime_monthly"] == 30.0
        assert summary["total_lifetime_annual"] == 360.0
        assert summary["total_runs"] == 1
        assert summary["last_run_savings"] == 30.0

        # Test missing ledger
        ledger_path.unlink()
        summary = tracker.get_savings_summary()
        assert summary["total_lifetime_monthly"] == 0.0
        assert summary["total_runs"] == 0

        # Test corrupt ledger
        ledger_path.write_text("not valid json")
        summary = tracker.get_savings_summary()
        assert summary["total_lifetime_monthly"] == 0.0
        assert summary["total_runs"] == 0

        # Test partial resource list
        ledger_path.unlink()
        result = tracker.record_run(["res-1"])
        assert result is True
        summary = tracker.get_savings_summary()
        assert summary["total_lifetime_monthly"] == 10.0
        assert summary["total_lifetime_annual"] == 120.0

    print("All smoke tests passed!")


if __name__ == "__main__":
    test_savings_tracker()
