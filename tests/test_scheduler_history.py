"""Unit tests for scheduler history persistence — crash safety, pruning, format."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from scheduler import JanitorScheduler, HISTORY_RETENTION


def _make_scheduler(tmp_path: Path, notifiers=None) -> JanitorScheduler:
    """Create a JanitorScheduler pointed at tmp_path with mocked dependencies."""
    (tmp_path / "output" / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "remediations").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "rollbacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "policies").mkdir(parents=True, exist_ok=True)
    (tmp_path / "hooks").mkdir(parents=True, exist_ok=True)
    scheduler = JanitorScheduler(project_root=tmp_path, notifiers=notifiers or [])
    return scheduler


class TestHistoryCrashSafety:
    """Tests proving history is appended even when the pipeline raises."""

    @patch("scheduler.Orchestrator")
    def test_orchestrator_init_raises_still_appends_history(self, mock_orch_cls, tmp_path):
        """If Orchestrator(...) constructor raises, history is still appended with status=failed."""
        mock_orch_cls.side_effect = RuntimeError("constructor boom")
        sched = _make_scheduler(tmp_path)

        # Should not raise
        sched._run_scan()

        history_path = tmp_path / "output" / "logs" / "scheduler_history.jsonl"
        assert history_path.exists()
        records = [json.loads(line) for line in history_path.read_text().strip().splitlines()]
        assert len(records) == 1
        assert records[0]["status"] == "failed"
        assert records[0]["findings_snapshot"] == {}

    @patch("scheduler.Orchestrator")
    def test_execute_audit_raises_still_appends_history(self, mock_orch_cls, tmp_path):
        """If execute_audit() raises after Orchestrator() succeeds, history still appended."""
        mock_instance = MagicMock()
        mock_instance.execute_audit.side_effect = RuntimeError("audit boom")
        mock_orch_cls.return_value = mock_instance
        sched = _make_scheduler(tmp_path)

        sched._run_scan()

        history_path = tmp_path / "output" / "logs" / "scheduler_history.jsonl"
        records = [json.loads(line) for line in history_path.read_text().strip().splitlines()]
        assert len(records) == 1
        assert records[0]["status"] == "failed"
        assert records[0]["findings_snapshot"] == {}

    @patch("scheduler.Orchestrator")
    def test_successful_scan_records_findings_snapshot(self, mock_orch_cls, tmp_path):
        """A successful scan records the correct findings_snapshot."""
        mock_result = SimpleNamespace(
            success=True,
            findings=[
                {"resource_id": "vol-1", "severity": "HIGH", "cost_estimate_monthly": 10.0},
                {"resource_id": "sg-2", "severity": "CRITICAL", "cost_estimate_monthly": 0.0},
            ],
            error=None,
        )
        mock_instance = MagicMock()
        mock_instance.execute_audit.return_value = mock_result
        mock_orch_cls.return_value = mock_instance
        sched = _make_scheduler(tmp_path)

        sched._run_scan()

        history_path = tmp_path / "output" / "logs" / "scheduler_history.jsonl"
        records = [json.loads(line) for line in history_path.read_text().strip().splitlines()]
        assert len(records) == 1
        assert records[0]["status"] == "success"
        assert records[0]["findings_snapshot"] == {"vol-1": "HIGH", "sg-2": "CRITICAL"}
        assert records[0]["total_findings"] == 2
        assert records[0]["total_waste"] == 10.0

    @patch("scheduler.Orchestrator")
    def test_result_success_false_records_failed(self, mock_orch_cls, tmp_path):
        """A scan where result.success is False records status=failed with correct snapshot."""
        mock_result = SimpleNamespace(
            success=False,
            findings=[{"resource_id": "vol-1", "severity": "MEDIUM", "cost_estimate_monthly": 5.0}],
            error="partial failure",
        )
        mock_instance = MagicMock()
        mock_instance.execute_audit.return_value = mock_result
        mock_orch_cls.return_value = mock_instance
        sched = _make_scheduler(tmp_path)

        sched._run_scan()

        history_path = tmp_path / "output" / "logs" / "scheduler_history.jsonl"
        records = [json.loads(line) for line in history_path.read_text().strip().splitlines()]
        assert len(records) == 1
        assert records[0]["status"] == "failed"
        assert records[0]["findings_snapshot"] == {"vol-1": "MEDIUM"}


class TestHistoryPruning:
    """Tests for JSONL history pruning at HISTORY_RETENTION (500)."""

    @patch("scheduler.Orchestrator")
    def test_pruning_caps_at_500(self, mock_orch_cls, tmp_path):
        """History file is pruned to 500 records after exceeding the limit."""
        sched = _make_scheduler(tmp_path)
        history_path = tmp_path / "output" / "logs" / "scheduler_history.jsonl"

        # Pre-populate with 501 records
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(history_path, "w", encoding="utf-8") as f:
            for i in range(501):
                record = {
                    "scan_id": f"scan-{i:04d}",
                    "timestamp_start": "2025-01-01T00:00:00+00:00",
                    "timestamp_end": "2025-01-01T00:01:00+00:00",
                    "status": "success",
                    "total_findings": 0,
                    "total_waste": 0.0,
                    "findings_snapshot": {},
                }
                f.write(json.dumps(record) + "\n")

        # Now run a scan to trigger pruning
        mock_result = SimpleNamespace(success=True, findings=[], error=None)
        mock_instance = MagicMock()
        mock_instance.execute_audit.return_value = mock_result
        mock_orch_cls.return_value = mock_instance

        sched._run_scan()

        lines = history_path.read_text().strip().splitlines()
        assert len(lines) == HISTORY_RETENTION


class TestHistoryFormat:
    """Tests for history record schema."""

    @patch("scheduler.Orchestrator")
    def test_record_has_required_keys(self, mock_orch_cls, tmp_path):
        """Every history record has the required schema keys."""
        mock_result = SimpleNamespace(success=True, findings=[], error=None)
        mock_instance = MagicMock()
        mock_instance.execute_audit.return_value = mock_result
        mock_orch_cls.return_value = mock_instance
        sched = _make_scheduler(tmp_path)

        sched._run_scan()

        history_path = tmp_path / "output" / "logs" / "scheduler_history.jsonl"
        record = json.loads(history_path.read_text().strip().splitlines()[0])
        required_keys = {"scan_id", "timestamp_start", "timestamp_end", "status", "total_findings", "total_waste", "findings_snapshot"}
        assert required_keys.issubset(record.keys())
        assert isinstance(record["findings_snapshot"], dict)
        assert isinstance(record["total_findings"], int)
        assert isinstance(record["total_waste"], (int, float))
        assert record["status"] in ("success", "failed")


class TestHistoryNotificationTrigger:
    """Tests for notification triggered by escalated findings."""

    @patch("scheduler.Orchestrator")
    def test_new_critical_finding_triggers_notify(self, mock_orch_cls, tmp_path):
        """A new CRITICAL finding on a successful scan triggers notify."""
        mock_notifier = MagicMock()
        mock_notifier.notify.return_value = True
        sched = _make_scheduler(tmp_path, notifiers=[mock_notifier])

        mock_result = SimpleNamespace(
            success=True,
            findings=[{"resource_id": "sg-1", "severity": "CRITICAL", "description": "open"}],
            error=None,
        )
        mock_instance = MagicMock()
        mock_instance.execute_audit.return_value = mock_result
        mock_orch_cls.return_value = mock_instance

        sched._run_scan()

        mock_notifier.notify.assert_called_once()
        call_args = mock_notifier.notify.call_args
        assert "CRITICAL" in call_args[0][0] or "HIGH+" in call_args[0][0]

    @patch("scheduler.Orchestrator")
    def test_previously_seen_high_does_not_trigger(self, mock_orch_cls, tmp_path):
        """A HIGH finding that was already HIGH in the previous run does not trigger notify."""
        mock_notifier = MagicMock()
        mock_notifier.notify.return_value = True
        sched = _make_scheduler(tmp_path, notifiers=[mock_notifier])

        # Pre-populate history with a previous scan showing vol-1 as HIGH
        history_path = tmp_path / "output" / "logs" / "scheduler_history.jsonl"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        prev_record = {
            "scan_id": "prev-0001",
            "timestamp_start": "2025-01-01T00:00:00+00:00",
            "timestamp_end": "2025-01-01T00:01:00+00:00",
            "status": "success",
            "total_findings": 1,
            "total_waste": 0.0,
            "findings_snapshot": {"vol-1": "HIGH"},
        }
        history_path.write_text(json.dumps(prev_record) + "\n", encoding="utf-8")

        # Current scan with the same HIGH finding
        mock_result = SimpleNamespace(
            success=True,
            findings=[{"resource_id": "vol-1", "severity": "HIGH"}],
            error=None,
        )
        mock_instance = MagicMock()
        mock_instance.execute_audit.return_value = mock_result
        mock_orch_cls.return_value = mock_instance

        sched._run_scan()

        mock_notifier.notify.assert_not_called()

    @patch("scheduler.Orchestrator")
    def test_failed_scan_does_not_trigger_notification(self, mock_orch_cls, tmp_path):
        """A failed scan (exception) does not trigger notifications even if previous had findings."""
        mock_notifier = MagicMock()
        mock_notifier.notify.return_value = True
        sched = _make_scheduler(tmp_path, notifiers=[mock_notifier])

        mock_orch_cls.side_effect = RuntimeError("boom")

        sched._run_scan()

        mock_notifier.notify.assert_not_called()

    @patch("scheduler.Orchestrator")
    def test_notifier_failure_does_not_alter_history_status(self, mock_orch_cls, tmp_path):
        """A notifier that returns False does not change the history record's status."""
        mock_notifier = MagicMock()
        mock_notifier.notify.return_value = False
        sched = _make_scheduler(tmp_path, notifiers=[mock_notifier])

        mock_result = SimpleNamespace(
            success=True,
            findings=[{"resource_id": "sg-1", "severity": "CRITICAL", "description": "open"}],
            error=None,
        )
        mock_instance = MagicMock()
        mock_instance.execute_audit.return_value = mock_result
        mock_orch_cls.return_value = mock_instance

        sched._run_scan()

        history_path = tmp_path / "output" / "logs" / "scheduler_history.jsonl"
        record = json.loads(history_path.read_text().strip().splitlines()[-1])
        assert record["status"] == "success"
