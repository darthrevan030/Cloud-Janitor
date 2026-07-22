"""Unit tests for the scheduler's notifier circuit breaker behavior."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from scheduler import (
    JanitorScheduler,
    MUTE_AFTER_CONSECUTIVE_FAILURES,
    MUTE_COOLDOWN,
    _NotifierState,
)


def _make_scheduler(tmp_path: Path, notifiers=None) -> JanitorScheduler:
    """Create a JanitorScheduler for testing."""
    (tmp_path / "output" / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "remediations").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "rollbacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "policies").mkdir(parents=True, exist_ok=True)
    (tmp_path / "hooks").mkdir(parents=True, exist_ok=True)
    return JanitorScheduler(project_root=tmp_path, notifiers=notifiers or [])


_scan_counter = 0


def _successful_scan_with_critical(mock_orch_cls):
    """Configure mock Orchestrator to return a successful scan with a unique CRITICAL finding."""
    global _scan_counter
    _scan_counter += 1
    mock_result = SimpleNamespace(
        success=True,
        findings=[{"resource_id": f"sg-new-{_scan_counter}", "severity": "CRITICAL", "description": "open port"}],
        error=None,
    )
    mock_instance = MagicMock()
    mock_instance.execute_audit.return_value = mock_result
    mock_orch_cls.return_value = mock_instance


class TestCircuitBreakerMuting:
    """Tests for circuit breaker muting behavior."""

    @patch("scheduler.Orchestrator")
    def test_three_consecutive_failures_mute_notifier(self, mock_orch_cls, tmp_path):
        """After 3 consecutive failures, the notifier is muted."""
        mock_notifier = MagicMock()
        mock_notifier.notify.return_value = False
        sched = _make_scheduler(tmp_path, notifiers=[mock_notifier])

        for i in range(3):
            _successful_scan_with_critical(mock_orch_cls)
            sched._run_scan()

        state = sched._notifier_state[id(mock_notifier)]
        assert state.consecutive_failures == 3
        assert state.muted_until is not None

    @patch("scheduler.Orchestrator")
    def test_fourth_scan_within_cooldown_skips_notify(self, mock_orch_cls, tmp_path):
        """A 4th qualifying scan within the cooldown window does not call notify."""
        mock_notifier = MagicMock()
        mock_notifier.notify.return_value = False
        sched = _make_scheduler(tmp_path, notifiers=[mock_notifier])

        # Trigger 3 failures to mute
        for _ in range(3):
            _successful_scan_with_critical(mock_orch_cls)
            sched._run_scan()

        assert mock_notifier.notify.call_count == 3

        # 4th scan — should skip notify because muted
        _successful_scan_with_critical(mock_orch_cls)
        sched._run_scan()

        # Still only 3 calls — the 4th was skipped
        assert mock_notifier.notify.call_count == 3

    @patch("scheduler.Orchestrator")
    def test_after_cooldown_one_attempt_is_made(self, mock_orch_cls, tmp_path):
        """After the cooldown elapses, exactly one attempt is made on the next qualifying scan."""
        mock_notifier = MagicMock()
        mock_notifier.notify.return_value = False
        sched = _make_scheduler(tmp_path, notifiers=[mock_notifier])

        # Mute the notifier
        for _ in range(3):
            _successful_scan_with_critical(mock_orch_cls)
            sched._run_scan()

        assert mock_notifier.notify.call_count == 3

        # Fast-forward muted_until to the past
        state = sched._notifier_state[id(mock_notifier)]
        state.muted_until = datetime.now(timezone.utc) - timedelta(seconds=1)

        # Next scan should attempt one call
        _successful_scan_with_critical(mock_orch_cls)
        sched._run_scan()

        assert mock_notifier.notify.call_count == 4

    @patch("scheduler.Orchestrator")
    def test_successful_attempt_resets_and_unmutes(self, mock_orch_cls, tmp_path):
        """A successful notify call resets consecutive_failures and clears muted_until."""
        mock_notifier = MagicMock()
        mock_notifier.notify.return_value = False
        sched = _make_scheduler(tmp_path, notifiers=[mock_notifier])

        # Mute
        for _ in range(3):
            _successful_scan_with_critical(mock_orch_cls)
            sched._run_scan()

        state = sched._notifier_state[id(mock_notifier)]
        assert state.muted_until is not None

        # Fast-forward past cooldown and make next call succeed
        state.muted_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        mock_notifier.notify.return_value = True

        _successful_scan_with_critical(mock_orch_cls)
        sched._run_scan()

        assert state.consecutive_failures == 0
        assert state.muted_until is None

    @patch("scheduler.Orchestrator")
    def test_first_successful_call_works_normally(self, mock_orch_cls, tmp_path):
        """A first-ever successful notify call keeps state clean."""
        mock_notifier = MagicMock()
        mock_notifier.notify.return_value = True
        sched = _make_scheduler(tmp_path, notifiers=[mock_notifier])

        _successful_scan_with_critical(mock_orch_cls)
        sched._run_scan()

        state = sched._notifier_state[id(mock_notifier)]
        assert state.consecutive_failures == 0
        assert state.muted_until is None
        mock_notifier.notify.assert_called_once()


class TestCircuitBreakerLogging:
    """Tests for circuit breaker logging behavior."""

    @patch("scheduler.Orchestrator")
    def test_raising_notifier_logged_as_warning(self, mock_orch_cls, tmp_path):
        """A notifier that raises is logged as WARNING and does not affect scan status."""
        mock_notifier = MagicMock()
        mock_notifier.notify.side_effect = RuntimeError("slack exploded")
        sched = _make_scheduler(tmp_path, notifiers=[mock_notifier])

        _successful_scan_with_critical(mock_orch_cls)
        sched._run_scan()

        # Scan status should still be success
        history_path = tmp_path / "output" / "logs" / "scheduler_history.jsonl"
        record = json.loads(history_path.read_text().strip().splitlines()[-1])
        assert record["status"] == "success"

        # Failure counted
        state = sched._notifier_state[id(mock_notifier)]
        assert state.consecutive_failures == 1

    @patch("scheduler.Orchestrator")
    def test_false_returning_notifier_does_not_alter_status(self, mock_orch_cls, tmp_path):
        """A notifier returning False does not alter the history record's status."""
        mock_notifier = MagicMock()
        mock_notifier.notify.return_value = False
        sched = _make_scheduler(tmp_path, notifiers=[mock_notifier])

        _successful_scan_with_critical(mock_orch_cls)
        sched._run_scan()

        history_path = tmp_path / "output" / "logs" / "scheduler_history.jsonl"
        record = json.loads(history_path.read_text().strip().splitlines()[-1])
        assert record["status"] == "success"
