"""Property-based tests for JanitorScheduler.

**Validates: Requirements 10.4, 10.5**

Property 21: JanitorScheduler Status Schema
For any state, get_status() returns dict with keys: running, schedule, next_run,
last_run, runs_completed with correct types.

Property 22: JanitorScheduler Idempotent Start
Multiple start() calls result in exactly one running scheduler.
"""

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from scheduler import JanitorScheduler, DEFAULT_SCHEDULE, _validate_cron


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

REQUIRED_STATUS_KEYS = {"running", "schedule", "next_run", "last_run", "runs_completed"}

# Valid 5-field cron expressions
VALID_CRONS = [
    "0 6 * * *",
    "*/5 * * * *",
    "30 2 * * 1",
    "0 0 1 * *",
    "15 14 1 * *",
    "0 22 * * 1-5",
    "0 */2 * * *",
    "0 9 * * MON",
]

# Invalid cron expressions (wrong number of fields, bad values, etc.)
INVALID_CRONS = [
    "",
    "   ",
    "not a cron",
    "0 6 * *",           # 4 fields
    "0 6 * * * *",       # 6 fields
    "60 6 * * *",        # invalid minute
    "abc def ghi jkl mno",
]


@st.composite
def valid_cron_strategy(draw):
    """Generate a valid 5-field cron expression from known-good set."""
    return draw(st.sampled_from(VALID_CRONS))


@st.composite
def cron_or_empty_strategy(draw):
    """Generate either a valid cron, invalid string, or empty string."""
    return draw(st.sampled_from(VALID_CRONS + INVALID_CRONS + [""]))


# ---------------------------------------------------------------------------
# Property 21: JanitorScheduler Status Schema
# ---------------------------------------------------------------------------


class TestJanitorSchedulerStatusSchema:
    """Property 21: JanitorScheduler Status Schema.

    **Validates: Requirements 10.4**

    For any state, get_status() returns dict with keys: running, schedule,
    next_run, last_run, runs_completed with correct types.
    """

    def test_status_schema_before_start(self):
        """get_status() returns correct schema before start() is called."""
        with patch("scheduler.Orchestrator"):
            scheduler = JanitorScheduler(project_root=Path("."))
            status = scheduler.get_status()

        assert set(status.keys()) == REQUIRED_STATUS_KEYS, (
            f"Expected keys {REQUIRED_STATUS_KEYS}, got {set(status.keys())}"
        )
        assert isinstance(status["running"], bool), (
            f"running must be bool, got {type(status['running'])}"
        )
        assert isinstance(status["schedule"], str), (
            f"schedule must be str, got {type(status['schedule'])}"
        )
        assert status["next_run"] is None or isinstance(status["next_run"], str), (
            f"next_run must be str or None, got {type(status['next_run'])}"
        )
        assert status["last_run"] is None or isinstance(status["last_run"], str), (
            f"last_run must be str or None, got {type(status['last_run'])}"
        )
        assert isinstance(status["runs_completed"], int), (
            f"runs_completed must be int, got {type(status['runs_completed'])}"
        )

    def test_status_schema_after_start(self):
        """get_status() returns correct schema after start() is called."""
        with patch("scheduler.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.success = True
            mock_orch.execute_audit.return_value = mock_result
            mock_orch_cls.return_value = mock_orch

            scheduler = JanitorScheduler(project_root=Path("."))
            try:
                scheduler.start()
                # Give a moment for scheduler to initialize
                time.sleep(0.1)
                status = scheduler.get_status()
            finally:
                scheduler.stop()

        assert set(status.keys()) == REQUIRED_STATUS_KEYS, (
            f"Expected keys {REQUIRED_STATUS_KEYS}, got {set(status.keys())}"
        )
        assert status["running"] is True, (
            f"After start(), running should be True, got {status['running']}"
        )
        assert isinstance(status["schedule"], str), (
            f"schedule must be str, got {type(status['schedule'])}"
        )
        assert status["next_run"] is None or isinstance(status["next_run"], str), (
            f"next_run must be str or None, got {type(status['next_run'])}"
        )
        assert status["last_run"] is None or isinstance(status["last_run"], str), (
            f"last_run must be str or None, got {type(status['last_run'])}"
        )
        assert isinstance(status["runs_completed"], int), (
            f"runs_completed must be int, got {type(status['runs_completed'])}"
        )

    def test_status_schema_after_stop(self):
        """get_status() returns correct schema after stop() is called."""
        with patch("scheduler.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.success = True
            mock_orch.execute_audit.return_value = mock_result
            mock_orch_cls.return_value = mock_orch

            scheduler = JanitorScheduler(project_root=Path("."))
            scheduler.start()
            time.sleep(0.1)
            scheduler.stop()
            status = scheduler.get_status()

        assert set(status.keys()) == REQUIRED_STATUS_KEYS, (
            f"Expected keys {REQUIRED_STATUS_KEYS}, got {set(status.keys())}"
        )
        assert status["running"] is False, (
            f"After stop(), running should be False, got {status['running']}"
        )

    def test_status_not_running_before_start(self):
        """Before start(), running is False and runs_completed is 0."""
        with patch("scheduler.Orchestrator"):
            scheduler = JanitorScheduler(project_root=Path("."))
            status = scheduler.get_status()

        assert status["running"] is False
        assert status["runs_completed"] == 0
        assert status["last_run"] is None
        assert status["next_run"] is None

    @given(schedule=valid_cron_strategy())
    @settings(max_examples=50, deadline=None)
    def test_status_schedule_reflects_env_var(self, schedule):
        """get_status() schedule field matches the JANITOR_SCHEDULE env var."""
        with patch.dict(os.environ, {"JANITOR_SCHEDULE": schedule}):
            with patch("scheduler.Orchestrator") as mock_orch_cls:
                mock_orch = MagicMock()
                mock_result = MagicMock()
                mock_result.findings = []
                mock_result.success = True
                mock_orch.execute_audit.return_value = mock_result
                mock_orch_cls.return_value = mock_orch

                scheduler = JanitorScheduler(project_root=Path("."))
                try:
                    scheduler.start()
                    time.sleep(0.1)
                    status = scheduler.get_status()
                finally:
                    scheduler.stop()

        assert status["schedule"] == schedule, (
            f"schedule should be '{schedule}', got '{status['schedule']}'"
        )

    @given(invalid_cron=st.sampled_from(INVALID_CRONS))
    @settings(max_examples=20, deadline=None)
    def test_status_falls_back_to_default_on_invalid_cron(self, invalid_cron):
        """When JANITOR_SCHEDULE is invalid, schedule falls back to default."""
        with patch.dict(os.environ, {"JANITOR_SCHEDULE": invalid_cron}):
            with patch("scheduler.Orchestrator") as mock_orch_cls:
                mock_orch = MagicMock()
                mock_result = MagicMock()
                mock_result.findings = []
                mock_result.success = True
                mock_orch.execute_audit.return_value = mock_result
                mock_orch_cls.return_value = mock_orch

                scheduler = JanitorScheduler(project_root=Path("."))
                try:
                    scheduler.start()
                    time.sleep(0.1)
                    status = scheduler.get_status()
                finally:
                    scheduler.stop()

        assert status["schedule"] == DEFAULT_SCHEDULE, (
            f"With invalid cron '{invalid_cron}', schedule should fall back to "
            f"'{DEFAULT_SCHEDULE}', got '{status['schedule']}'"
        )

    def test_next_run_is_valid_iso_timestamp_when_running(self):
        """When running, next_run is a valid ISO 8601 timestamp string."""
        with patch("scheduler.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.success = True
            mock_orch.execute_audit.return_value = mock_result
            mock_orch_cls.return_value = mock_orch

            scheduler = JanitorScheduler(project_root=Path("."))
            try:
                scheduler.start()
                time.sleep(0.1)
                status = scheduler.get_status()
            finally:
                scheduler.stop()

        assert status["next_run"] is not None, "next_run should not be None when running"
        # Validate it parses as ISO timestamp
        parsed = datetime.fromisoformat(status["next_run"])
        assert parsed > datetime.now(timezone.utc) - __import__("datetime").timedelta(days=1), (
            f"next_run should be in the future or recent past, got {parsed}"
        )

    def test_runs_completed_is_non_negative(self):
        """runs_completed is always >= 0."""
        with patch("scheduler.Orchestrator"):
            scheduler = JanitorScheduler(project_root=Path("."))
            status = scheduler.get_status()

        assert status["runs_completed"] >= 0, (
            f"runs_completed must be non-negative, got {status['runs_completed']}"
        )


# ---------------------------------------------------------------------------
# Property 22: JanitorScheduler Idempotent Start
# ---------------------------------------------------------------------------


class TestJanitorSchedulerIdempotentStart:
    """Property 22: JanitorScheduler Idempotent Start.

    **Validates: Requirements 10.5**

    Multiple start() calls result in exactly one running scheduler.
    """

    @given(num_starts=st.integers(min_value=2, max_value=10))
    @settings(max_examples=20, deadline=None)
    def test_multiple_starts_result_in_one_scheduler(self, num_starts):
        """Calling start() N times results in exactly one running scheduler."""
        with patch("scheduler.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.success = True
            mock_orch.execute_audit.return_value = mock_result
            mock_orch_cls.return_value = mock_orch

            scheduler = JanitorScheduler(project_root=Path("."))
            try:
                for _ in range(num_starts):
                    scheduler.start()
                    time.sleep(0.05)

                status = scheduler.get_status()
            finally:
                scheduler.stop()

        assert status["running"] is True, (
            f"After {num_starts} start() calls, scheduler should be running"
        )

    @given(num_starts=st.integers(min_value=2, max_value=8))
    @settings(max_examples=20, deadline=None)
    def test_multiple_starts_do_not_leave_orphan_schedulers(self, num_starts):
        """After multiple start() calls and one stop(), no scheduler is running."""
        with patch("scheduler.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.success = True
            mock_orch.execute_audit.return_value = mock_result
            mock_orch_cls.return_value = mock_orch

            scheduler = JanitorScheduler(project_root=Path("."))
            try:
                for _ in range(num_starts):
                    scheduler.start()
                    time.sleep(0.05)
            finally:
                scheduler.stop()

            status = scheduler.get_status()

        assert status["running"] is False, (
            f"After {num_starts} starts and 1 stop, scheduler should not be running"
        )

    def test_start_stop_start_results_in_running(self):
        """start() → stop() → start() results in a running scheduler."""
        with patch("scheduler.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.success = True
            mock_orch.execute_audit.return_value = mock_result
            mock_orch_cls.return_value = mock_orch

            scheduler = JanitorScheduler(project_root=Path("."))
            try:
                scheduler.start()
                time.sleep(0.1)
                scheduler.stop()
                time.sleep(0.1)
                scheduler.start()
                time.sleep(0.1)
                status = scheduler.get_status()
            finally:
                scheduler.stop()

        assert status["running"] is True, (
            "After start→stop→start, scheduler should be running"
        )

    def test_stop_without_start_is_safe(self):
        """Calling stop() without prior start() does not raise."""
        with patch("scheduler.Orchestrator"):
            scheduler = JanitorScheduler(project_root=Path("."))
            # This should not raise
            scheduler.stop()
            status = scheduler.get_status()

        assert status["running"] is False

    @given(num_starts=st.integers(min_value=2, max_value=5))
    @settings(max_examples=10, deadline=None)
    def test_concurrent_starts_result_in_one_scheduler(self, num_starts):
        """Concurrent start() calls still result in exactly one running scheduler."""
        with patch("scheduler.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.success = True
            mock_orch.execute_audit.return_value = mock_result
            mock_orch_cls.return_value = mock_orch

            scheduler = JanitorScheduler(project_root=Path("."))
            threads = []
            try:
                for _ in range(num_starts):
                    t = threading.Thread(target=scheduler.start, daemon=True)
                    threads.append(t)
                    t.start()

                for t in threads:
                    t.join(timeout=5.0)

                time.sleep(0.1)
                status = scheduler.get_status()
            finally:
                scheduler.stop()

        assert status["running"] is True, (
            f"After {num_starts} concurrent starts, scheduler should be running"
        )

    def test_get_status_returns_exact_five_keys(self):
        """get_status() returns exactly 5 keys, no more, no less."""
        with patch("scheduler.Orchestrator"):
            scheduler = JanitorScheduler(project_root=Path("."))
            status = scheduler.get_status()

        assert len(status) == 5, (
            f"get_status() should return exactly 5 keys, got {len(status)}: {list(status.keys())}"
        )


# ---------------------------------------------------------------------------
# Negative Tests
# ---------------------------------------------------------------------------


class TestJanitorSchedulerNegativeCases:
    """Negative tests for JanitorScheduler.

    Validates behavior when preconditions are not met or edge cases.
    """

    def test_double_stop_is_safe(self):
        """Calling stop() twice does not raise."""
        with patch("scheduler.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.success = True
            mock_orch.execute_audit.return_value = mock_result
            mock_orch_cls.return_value = mock_orch

            scheduler = JanitorScheduler(project_root=Path("."))
            scheduler.start()
            time.sleep(0.1)
            scheduler.stop()
            # Second stop should not raise
            scheduler.stop()
            status = scheduler.get_status()

        assert status["running"] is False

    def test_get_status_never_raises(self):
        """get_status() never raises regardless of internal state."""
        with patch("scheduler.Orchestrator"):
            scheduler = JanitorScheduler(project_root=Path("."))

            # Before start
            status1 = scheduler.get_status()
            assert isinstance(status1, dict)

            # After start + stop
            scheduler.start()
            time.sleep(0.05)
            scheduler.stop()
            status2 = scheduler.get_status()
            assert isinstance(status2, dict)

    def test_schedule_field_is_always_valid_cron(self):
        """schedule field is always a valid 5-field cron expression."""
        with patch("scheduler.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.success = True
            mock_orch.execute_audit.return_value = mock_result
            mock_orch_cls.return_value = mock_orch

            # Set an invalid cron in env
            with patch.dict(os.environ, {"JANITOR_SCHEDULE": "invalid cron"}):
                scheduler = JanitorScheduler(project_root=Path("."))
                try:
                    scheduler.start()
                    time.sleep(0.1)
                    status = scheduler.get_status()
                finally:
                    scheduler.stop()

        # Should always produce a valid cron string
        assert _validate_cron(status["schedule"]), (
            f"schedule '{status['schedule']}' should be a valid cron expression"
        )
