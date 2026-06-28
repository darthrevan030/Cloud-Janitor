"""JanitorScheduler — Cron-based automated scans using APScheduler.

Provides non-blocking, daemon-threaded scheduled scans with:
- Configurable cron via JANITOR_SCHEDULE env var
- Idempotent start/stop lifecycle
- Overlap prevention (skips trigger if previous scan running)
- RotatingFileHandler logging to scheduler.log
- Immediate scan on first start if no scan has run today

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 14.5
"""

import logging
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from orchestrator import Orchestrator

DEFAULT_SCHEDULE = "0 6 * * *"
JOB_ID = "janitor_scheduled_scan"


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


class JanitorScheduler:
    """Cron-based automated scan scheduler using APScheduler BackgroundScheduler.

    The scheduler runs as a daemon thread that exits with the main process.
    """

    def __init__(self, project_root: Path | None = None):
        self._project_root = project_root or Path(__file__).resolve().parent
        self._scheduler: BackgroundScheduler | None = None
        self._lock = threading.Lock()
        self._scan_running = threading.Event()
        self._runs_completed: int = 0
        self._last_run: datetime | None = None
        self._schedule: str = DEFAULT_SCHEDULE
        self._logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        """Configure rotating file handler for scheduler.log."""
        logger = logging.getLogger("janitor_scheduler")
        logger.setLevel(logging.INFO)

        # Avoid duplicate handlers on re-init
        if not logger.handlers:
            log_path = self._project_root / "scheduler.log"
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

        try:
            self._logger.info(f"Starting scheduled scan: {scan_id}")

            orchestrator = Orchestrator(project_root=self._project_root)
            result = orchestrator.execute_audit()

            total_findings = len(result.findings)
            total_waste = sum(
                f.get("cost_estimate_monthly", 0.0) for f in result.findings
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

        finally:
            self._scan_running.clear()
            end_time = datetime.now(timezone.utc)

            # Update state
            self._last_run = end_time
            self._runs_completed += 1

            # Log entry to scheduler.log
            self._logger.info(
                f"Scan summary | scan_id={scan_id} | "
                f"timestamp={end_time.isoformat()} | "
                f"total_findings={total_findings} | "
                f"total_waste={total_waste:.2f} | "
                f"status={status} | "
                f"duration={( end_time - start_time).total_seconds():.1f}s"
            )
