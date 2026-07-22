"""Property-based test for reasoning log retention bound.

Property 2: Reasoning Log Retention Bound
  For N calls to start_new_run() where N > configured retention,
  the directory contains at most that many .log files, and the
  remaining files are the most recently created.

Validates: Requirement 2.3
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from cloud_janitor.agents.reasoning_logger import ReasoningLogger


@settings(deadline=None, max_examples=50)
@given(
    retention=st.integers(min_value=1, max_value=10),
    extra=st.integers(min_value=1, max_value=10),
)
def test_retention_bound_never_exceeded(retention: int, extra: int):
    """After N > retention calls to start_new_run(), directory has at most `retention` .log files.

    The files that survive are the most recently created (lexicographically greatest run_ids).
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        os.environ["JANITOR_RUN_RETENTION"] = str(retention)
        try:
            log_file = tmp_path / "logs" / "agent_reasoning.log"
            log_file.parent.mkdir(parents=True)
            log_file.touch()
            logger = ReasoningLogger(log_path=log_file)

            total_runs = retention + extra
            run_ids = [f"run-{i:04d}" for i in range(total_runs)]

            for run_id in run_ids:
                logger.start_new_run(run_id)

            run_dir = log_file.parent / "agent_reasoning"
            remaining_files = sorted(p.name for p in run_dir.glob("*.log"))

            # Invariant 1: never more than retention files
            assert len(remaining_files) <= retention, (
                f"Expected at most {retention} files, got {len(remaining_files)}"
            )

            # Invariant 2: the surviving files are the most recent (highest sorted run_ids)
            expected_survivors = sorted(f"{rid}.log" for rid in run_ids[-retention:])
            assert remaining_files == expected_survivors, (
                f"Expected survivors {expected_survivors}, got {remaining_files}"
            )
        finally:
            os.environ.pop("JANITOR_RUN_RETENTION", None)


@settings(deadline=None, max_examples=30)
@given(
    retention=st.integers(min_value=2, max_value=8),
    n_runs=st.integers(min_value=1, max_value=8),
)
def test_within_retention_no_files_deleted(retention: int, n_runs: int):
    """When N <= retention, no files are deleted — all remain present."""
    assume(n_runs <= retention)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        os.environ["JANITOR_RUN_RETENTION"] = str(retention)
        try:
            log_file = tmp_path / "logs" / "agent_reasoning.log"
            log_file.parent.mkdir(parents=True)
            log_file.touch()
            logger = ReasoningLogger(log_path=log_file)

            run_ids = [f"run-{i:04d}" for i in range(n_runs)]
            for run_id in run_ids:
                logger.start_new_run(run_id)

            run_dir = log_file.parent / "agent_reasoning"
            remaining_files = sorted(p.name for p in run_dir.glob("*.log"))

            assert len(remaining_files) == n_runs, (
                f"Expected all {n_runs} files present, got {len(remaining_files)}"
            )
            for run_id in run_ids:
                assert f"{run_id}.log" in remaining_files
        finally:
            os.environ.pop("JANITOR_RUN_RETENTION", None)
