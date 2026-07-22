"""JanitorScheduler — Cron-based automated scans using APScheduler.

Provides non-blocking, daemon-threaded scheduled scans with:
- Configurable cron via JANITOR_SCHEDULE env var
- Idempotent start/stop lifecycle
- Overlap prevention (skips trigger if previous scan running)
- RotatingFileHandler logging to scheduler.log
- Immediate scan on first start if no scan has run today
- Run history persistence (JSONL, crash-safe)
- Severity-based alerting with circuit-breaker muting

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 14.5
"""

import json
import logging
import os
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from cloud_janitor.core.notifiers import Notifier, build_notifiers_from_env
from cloud_janitor.core.scan_diff import diff_high_severity_findings
from cloud_janitor.orchestrator import Orchestrator

DEFAULT_SCHEDULE = "0 6 * * *"
JOB_ID = "janitor_scheduled_scan"
HISTORY_RETENTION = 500
MUTE_AFTER_CONSECUTIVE_FAILURES = 3
MUTE_COOLDOWN = timedelta(hours=1)


def _validate_cron(expression: str) -> bool:
    """Validate a 5-field cron expression by attempting to build a CronTrigger."""
    fields = expression.strip().split()
    if len(fields) != 5:
        return False
    try:
        CronTrigger.from_crontab(expression.strip())
        return True
    except (ValueError, TypeError):
        return False


@dataclass
class _NotifierState:
    """Per-notifier circuit breaker state."""

    consecutive_failures: int = 0
    muted_until: datetime | None = None


class JanitorScheduler:
    """Cron-based automated scan scheduler using APScheduler BackgroundScheduler.

    The scheduler runs as a daemon thread that exits with the main process.
    """

    def __init__(self, project_root: Path | None = None, notifiers: list[Notifier] | None = None):
        self._project_root = project_root or Path(__file__).resolve().parent
        self._scheduler: BackgroundScheduler | None = None
        self._lock = threading.Lock()
        self._scan_running = threading.Event()
        self._runs_completed: int = 0
        self._last_run: datetime | None = None
        self._schedule: str = DEFAULT_SCHEDULE
        self._logger = self._setup_logger()
        self._notifiers = notifiers if notifiers is not None else build_notifiers_from_env()
        self._notifier_state: dict[int, _NotifierState] = {
            id(n): _NotifierState() for n in self._notifiers
        }
        self._history_path = self._project_root / "output" / "logs" / "scheduler_history.jsonl"

    def _setup_logger(self) -> logging.Logger:
        """Configure rotating file handler for scheduler.log."""
        logger = logging.getLogger("janitor_scheduler")
        logger.setLevel(logging.INFO)

        # Avoid duplicate handlers on re-init
        if not logger.handlers:
            log_path = self._project_root / "output" / "logs" / "scheduler.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                str(log_path),
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=3,
            )
            handler.setFormatter(
                logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            )
            logger.addHandler(handler)

        return logger

    def start(self) -> None:
        """Start the background scheduler. Non-blocking, idempotent.

        Stops any previous scheduler before starting a new one.
        Reads schedule from JANITOR_SCHEDULE env var (default: "0 6 * * *").
        If no scan has run today, runs one immediately.
        """
        with self._lock:
            # Idempotent: stop previous scheduler if running
            if self._scheduler is not None:
                self._stop_internal()

            # Read and validate schedule
            env_schedule = os.environ.get("JANITOR_SCHEDULE", "").strip()
            if env_schedule and _validate_cron(env_schedule):
                self._schedule = env_schedule
            elif env_schedule:
                print(
                    f"WARNING: Invalid JANITOR_SCHEDULE '{env_schedule}', "
                    f"falling back to default '{DEFAULT_SCHEDULE}'",
                    file=sys.stderr,
                )
                self._schedule = DEFAULT_SCHEDULE
            else:
                self._schedule = DEFAULT_SCHEDULE

            # Create BackgroundScheduler with daemon thread
            self._scheduler = BackgroundScheduler(daemon=True)

            # Add cron job
            trigger = CronTrigger.from_crontab(self._schedule)
            self._scheduler.add_job(
                self._run_scan,
                trigger=trigger,
                id=JOB_ID,
                replace_existing=True,
                misfire_grace_time=60,
            )

            self._scheduler.start()
            self._logger.info(
                f"Scheduler started with schedule: {self._schedule}"
            )

            # Run immediately if no scan today
            if not self._has_run_today():
                threading.Thread(
                    target=self._run_scan, daemon=True, name="janitor-immediate-scan"
                ).start()

    def stop(self) -> None:
        """Stop the scheduler gracefully within 5 seconds."""
        with self._lock:
            self._stop_internal()

    def get_status(self) -> dict:
        """Return current scheduler status.

        Returns:
            dict with keys: running, schedule, next_run, last_run, runs_completed
        """
        with self._lock:
            running = self._scheduler is not None and self._scheduler.running
            next_run = None

            if running and self._scheduler is not None:
                job = self._scheduler.get_job(JOB_ID)
                if job and job.next_run_time:
                    next_run = job.next_run_time.isoformat()

            return {
                "running": running,
                "schedule": self._schedule,
                "next_run": next_run,
                "last_run": self._last_run.isoformat() if self._last_run else None,
                "runs_completed": self._runs_completed,
            }

    # ──────────────────────────────────────────────────────────────────────
    # Internal methods
    # ──────────────────────────────────────────────────────────────────────

    def _stop_internal(self) -> None:
        """Stop the scheduler (must be called while holding self._lock)."""
        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=True)
            except Exception:
                try:
                    self._scheduler.shutdown(wait=False)
                except Exception:
                    pass
            self._scheduler = None
            self._logger.info("Scheduler stopped")

    def _has_run_today(self) -> bool:
        """Check if a scan has already run today."""
        if self._last_run is None:
            return False
        today = datetime.now(timezone.utc).date()
        return self._last_run.date() == today

    def _run_scan(self) -> None:
        """Execute a single scan. Skips if previous scan still running."""
        # Overlap prevention
        if self._scan_running.is_set():
            self._logger.warning(
                "Skipping scheduled scan — previous scan still in progress"
            )
            return

        self._scan_running.set()
        scan_id = str(uuid.uuid4())[:8]
        start_time = datetime.now(timezone.utc)
        status = "success"
        total_findings = 0
        total_waste = 0.0
        # Pre-initialized outside the try block, alongside the existing
        # total_findings/total_waste defaults, for exactly the same reason:
        # this must be a safe value the except/finally path can read even
        # if Orchestrator(...) construction or execute_audit() itself raises.
        findings: list[dict] = []

        try:
            self._logger.info(f"Starting scheduled scan: {scan_id}")

            orchestrator = Orchestrator(project_root=self._project_root)
            result = orchestrator.execute_audit()

            findings = result.findings  # Only reference `result` inside this try block
            total_findings = len(findings)
            total_waste = sum(
                f.get("cost_estimate_monthly", 0.0) for f in findings
            )

            if not result.success:
                status = "failed"
                self._logger.error(
                    f"Scan {scan_id} failed: {result.error or 'unknown error'}"
                )
            else:
                self._logger.info(
                    f"Scan {scan_id} completed: "
                    f"{total_findings} findings, ${total_waste:.2f}/month waste"
                )

        except Exception as e:
            status = "failed"
            self._logger.error(f"Scan {scan_id} raised exception: {type(e).__name__}: {e}")
            # `findings` remains [] here — set outside the try block precisely
            # for this path. History is still appended below with an empty
            # snapshot rather than crashing with UnboundLocalError.

        finally:
            self._scan_running.clear()
            end_time = datetime.now(timezone.utc)

            # Update state
            self._last_run = end_time
            self._runs_completed += 1

            # History/notification logic operates exclusively on the safe
            # `findings` local, never on `result` (which may not exist).
            previous_snapshot = self._load_last_snapshot()
            current_snapshot = {f["resource_id"]: f.get("severity", "LOW") for f in findings}

            record = {
                "scan_id": scan_id,
                "timestamp_start": start_time.isoformat(),
                "timestamp_end": end_time.isoformat(),
                "status": status,
                "total_findings": total_findings,
                "total_waste": total_waste,
                "findings_snapshot": current_snapshot,
            }
            self._append_history(record)

            # Severity diff and notification — only on successful scans
            escalated = diff_high_severity_findings(previous_snapshot, findings) if status == "success" else []
            if escalated:
                summary = f"Cloud Janitor scan {scan_id}: {len(escalated)} new/escalated HIGH+ finding(s)"
                for notifier in self._notifiers:
                    self._notify_with_circuit_breaker(notifier, summary, escalated)

            # Log entry to scheduler.log
            self._logger.info(
                f"Scan summary | scan_id={scan_id} | "
                f"timestamp={end_time.isoformat()} | "
                f"total_findings={total_findings} | "
                f"total_waste={total_waste:.2f} | "
                f"status={status} | "
                f"duration={(end_time - start_time).total_seconds():.1f}s"
            )

    def _notify_with_circuit_breaker(self, notifier: Notifier, summary: str, findings: list[dict]) -> None:
        """Invoke notifier with circuit-breaker muting on sustained failures."""
        state = self._notifier_state[id(notifier)]
        now = datetime.now(timezone.utc)

        if state.muted_until is not None and now < state.muted_until:
            self._logger.warning(
                "Notifier %s is muted until %s (sustained failures) — skipping",
                type(notifier).__name__, state.muted_until.isoformat(),
            )
            return

        try:
            ok = notifier.notify(summary, findings)
        except Exception as exc:
            ok = False
            self._logger.warning("Notifier %s raised: %s", type(notifier).__name__, exc)

        if ok:
            if state.consecutive_failures > 0 or state.muted_until is not None:
                self._logger.info("Notifier %s recovered after prior failures — un-muting", type(notifier).__name__)
            state.consecutive_failures = 0
            state.muted_until = None
        else:
            state.consecutive_failures += 1
            self._logger.warning(
                "Notifier %s returned False (%d consecutive failure(s))",
                type(notifier).__name__, state.consecutive_failures,
            )
            if state.consecutive_failures >= MUTE_AFTER_CONSECUTIVE_FAILURES:
                state.muted_until = now + MUTE_COOLDOWN
                self._logger.warning(
                    "Notifier %s muted until %s after %d consecutive failures",
                    type(notifier).__name__, state.muted_until.isoformat(), state.consecutive_failures,
                )

    def _load_last_snapshot(self) -> dict[str, str]:
        """Load findings_snapshot from the last history record."""
        if not self._history_path.exists():
            return {}
        try:
            text = self._history_path.read_text(encoding="utf-8").strip()
            if not text:
                return {}
            last_line = text.splitlines()[-1]
            return json.loads(last_line)["findings_snapshot"] if last_line else {}
        except (OSError, ValueError, json.JSONDecodeError, KeyError):
            return {}

    def _append_history(self, record: dict) -> None:
        """Append a history record to the JSONL file."""
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            self._prune_history()
        except OSError as exc:
            self._logger.warning("Failed to write scheduler history: %s", exc)

    def _prune_history(self) -> None:
        """Prune history file to HISTORY_RETENTION lines."""
        try:
            lines = self._history_path.read_text(encoding="utf-8").splitlines()
            if len(lines) > HISTORY_RETENTION:
                self._history_path.write_text(
                    "\n".join(lines[-HISTORY_RETENTION:]) + "\n", encoding="utf-8"
                )
        except OSError:
            pass
