"""Property test: Circuit Breaker Mute Threshold (Property 3)."""

import json
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from hypothesis import given, settings, assume, strategies as st

from scheduler import (
    JanitorScheduler,
    MUTE_AFTER_CONSECUTIVE_FAILURES,
    MUTE_COOLDOWN,
    _NotifierState,
)

_counter = 0


def _cleanup_logger():
    """Remove file handlers from the scheduler logger to release file locks."""
    logger = logging.getLogger("janitor_scheduler")
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)


def _make_scheduler_in(tmp_path: Path, notifiers) -> JanitorScheduler:
    (tmp_path / "output" / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "remediations").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "rollbacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "policies").mkdir(parents=True, exist_ok=True)
    (tmp_path / "hooks").mkdir(parents=True, exist_ok=True)
    return JanitorScheduler(project_root=tmp_path, notifiers=notifiers)


def _run_scan_with_critical(sched, mock_orch_cls):
    """Run one scan that produces a unique new CRITICAL finding."""
    global _counter
    _counter += 1
    mock_result = SimpleNamespace(
        success=True,
        findings=[{"resource_id": f"sg-prop-{_counter}", "severity": "CRITICAL", "description": "open"}],
        error=None,
    )
    mock_instance = MagicMock()
    mock_instance.execute_audit.return_value = mock_result
    mock_orch_cls.return_value = mock_instance
    sched._run_scan()


@settings(max_examples=100, deadline=None)
@given(
    n_failures=st.integers(min_value=0, max_value=10),
)
def test_property_mute_threshold(n_failures):
    """After exactly MUTE_AFTER_CONSECUTIVE_FAILURES consecutive failures,
    the notifier is muted. Before that threshold, it is not muted."""
    _cleanup_logger()

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        mock_notifier = MagicMock()
        mock_notifier.notify.return_value = False
        sched = _make_scheduler_in(tmp_path, notifiers=[mock_notifier])

        with patch("scheduler.Orchestrator") as mock_orch_cls:
            for _ in range(n_failures):
                _run_scan_with_critical(sched, mock_orch_cls)

        state = sched._notifier_state[id(mock_notifier)]

        if n_failures < MUTE_AFTER_CONSECUTIVE_FAILURES:
            assert state.muted_until is None, (
                f"Should not be muted after {n_failures} failures "
                f"(threshold is {MUTE_AFTER_CONSECUTIVE_FAILURES})"
            )
        else:
            assert state.muted_until is not None, (
                f"Should be muted after {n_failures} failures"
            )

        _cleanup_logger()


@settings(max_examples=50, deadline=None)
@given(
    extra_scans_during_mute=st.integers(min_value=1, max_value=5),
)
def test_property_muted_notifier_skipped(extra_scans_during_mute):
    """While muted, notify() is never called regardless of how many qualifying scans run."""
    _cleanup_logger()

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        mock_notifier = MagicMock()
        mock_notifier.notify.return_value = False
        sched = _make_scheduler_in(tmp_path, notifiers=[mock_notifier])

        # Trigger muting
        with patch("scheduler.Orchestrator") as mock_orch_cls:
            for _ in range(MUTE_AFTER_CONSECUTIVE_FAILURES):
                _run_scan_with_critical(sched, mock_orch_cls)

            calls_at_mute = mock_notifier.notify.call_count
            assert calls_at_mute == MUTE_AFTER_CONSECUTIVE_FAILURES

            # Run additional scans during mute period
            for _ in range(extra_scans_during_mute):
                _run_scan_with_critical(sched, mock_orch_cls)

            # No additional calls should have been made
            assert mock_notifier.notify.call_count == calls_at_mute

        _cleanup_logger()


@settings(max_examples=50, deadline=None)
@given(
    cooldown_elapsed_seconds=st.integers(min_value=1, max_value=7200),
)
def test_property_reset_after_cooldown_success(cooldown_elapsed_seconds):
    """After cooldown elapses and the next notify() succeeds, state is fully reset."""
    _cleanup_logger()

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        mock_notifier = MagicMock()
        mock_notifier.notify.return_value = False
        sched = _make_scheduler_in(tmp_path, notifiers=[mock_notifier])

        # Mute the notifier
        with patch("scheduler.Orchestrator") as mock_orch_cls:
            for _ in range(MUTE_AFTER_CONSECUTIVE_FAILURES):
                _run_scan_with_critical(sched, mock_orch_cls)

            state = sched._notifier_state[id(mock_notifier)]
            assert state.muted_until is not None

            # Fast-forward past cooldown
            state.muted_until = datetime.now(timezone.utc) - timedelta(seconds=cooldown_elapsed_seconds)

            # Next call succeeds
            mock_notifier.notify.return_value = True
            _run_scan_with_critical(sched, mock_orch_cls)

            # State should be fully reset
            assert state.consecutive_failures == 0
            assert state.muted_until is None

        _cleanup_logger()
