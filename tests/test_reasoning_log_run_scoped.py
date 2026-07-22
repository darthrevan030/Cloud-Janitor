"""Unit tests for ReasoningLogger.start_new_run() — run-scoped log files.

Covers:
- Two sequential start_new_run() calls leave both files present (no truncation)
- Pruning at default retention boundary (21st call prunes the oldest)
- Pruning at custom JANITOR_RUN_RETENTION=5
- emit() after start_new_run() writes to the new run's file only
- Filesystem error during start_new_run() logs to stderr, does not raise

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.7
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from cloud_janitor.agents.reasoning_logger import ReasoningLogger


class TestStartNewRunCreatesRunScopedFiles:
    """Tests that start_new_run() creates isolated per-run log files."""

    def test_two_sequential_calls_leave_both_files_present(self, tmp_path: Path):
        """Two start_new_run() calls must not truncate or delete the first file."""
        log_file = tmp_path / "logs" / "agent_reasoning.log"
        log_file.parent.mkdir(parents=True)
        log_file.touch()
        logger = ReasoningLogger(log_path=log_file)

        logger.start_new_run("run-001")
        logger.emit("agent1", "check", "res-1", "first run event")

        logger.start_new_run("run-002")
        logger.emit("agent2", "finding", "res-2", "second run event")

        run_dir = log_file.parent / "agent_reasoning"
        file_1 = run_dir / "run-001.log"
        file_2 = run_dir / "run-002.log"

        assert file_1.exists(), "First run's file must still exist"
        assert file_2.exists(), "Second run's file must exist"

        # Verify content isolation — each file has only its own event
        entries_1 = [json.loads(line) for line in file_1.read_text().strip().splitlines()]
        entries_2 = [json.loads(line) for line in file_2.read_text().strip().splitlines()]

        assert len(entries_1) == 1
        assert entries_1[0]["message"] == "first run event"
        assert entries_1[0]["agent"] == "agent1"

        assert len(entries_2) == 1
        assert entries_2[0]["message"] == "second run event"
        assert entries_2[0]["agent"] == "agent2"

    def test_start_new_run_creates_subdirectory(self, tmp_path: Path):
        """The agent_reasoning/ subdirectory is created if it does not exist."""
        log_file = tmp_path / "logs" / "agent_reasoning.log"
        log_file.parent.mkdir(parents=True)
        log_file.touch()
        logger = ReasoningLogger(log_path=log_file)

        run_dir = log_file.parent / "agent_reasoning"
        assert not run_dir.exists()

        logger.start_new_run("run-abc")
        assert run_dir.is_dir()
        assert (run_dir / "run-abc.log").exists()

    def test_log_path_updated_after_start_new_run(self, tmp_path: Path):
        """After start_new_run(), log_path property points to the new file."""
        log_file = tmp_path / "logs" / "agent_reasoning.log"
        log_file.parent.mkdir(parents=True)
        log_file.touch()
        logger = ReasoningLogger(log_path=log_file)

        logger.start_new_run("run-xyz")

        expected = log_file.parent / "agent_reasoning" / "run-xyz.log"
        assert logger.log_path == expected


class TestStartNewRunPruning:
    """Tests that start_new_run() prunes old files beyond retention."""

    def test_21st_call_prunes_oldest_at_default_retention_20(self, tmp_path: Path):
        """Default retention is 20; the 21st file triggers pruning of the oldest."""
        log_file = tmp_path / "logs" / "agent_reasoning.log"
        log_file.parent.mkdir(parents=True)
        log_file.touch()
        logger = ReasoningLogger(log_path=log_file)

        # Create 20 runs (fills retention)
        for i in range(20):
            logger.start_new_run(f"run-{i:03d}")

        run_dir = log_file.parent / "agent_reasoning"
        assert len(list(run_dir.glob("*.log"))) == 20

        # 21st run should prune the oldest
        logger.start_new_run("run-020")
        remaining = sorted(p.name for p in run_dir.glob("*.log"))
        assert len(remaining) == 20
        # The very first file should be gone
        assert "run-000.log" not in remaining
        # The newest file should be present
        assert "run-020.log" in remaining

    @patch.dict("os.environ", {"JANITOR_RUN_RETENTION": "5"})
    def test_custom_retention_5_prunes_at_correct_boundary(self, tmp_path: Path):
        """JANITOR_RUN_RETENTION=5 keeps only 5 files."""
        log_file = tmp_path / "logs" / "agent_reasoning.log"
        log_file.parent.mkdir(parents=True)
        log_file.touch()
        logger = ReasoningLogger(log_path=log_file)

        for i in range(6):
            logger.start_new_run(f"run-{i:03d}")

        run_dir = log_file.parent / "agent_reasoning"
        remaining = sorted(p.name for p in run_dir.glob("*.log"))
        assert len(remaining) == 5
        assert "run-000.log" not in remaining
        assert "run-005.log" in remaining


class TestStartNewRunEmitIsolation:
    """Tests that emit() after start_new_run() writes to the correct file."""

    def test_emit_writes_to_new_run_file_only(self, tmp_path: Path):
        """After start_new_run(), emit() targets only the new run's file."""
        log_file = tmp_path / "logs" / "agent_reasoning.log"
        log_file.parent.mkdir(parents=True)
        log_file.touch()
        logger = ReasoningLogger(log_path=log_file)

        logger.start_new_run("run-A")
        logger.emit("agent", "check", "r1", "event in A")

        logger.start_new_run("run-B")
        logger.emit("agent", "finding", "r2", "event in B")

        run_dir = log_file.parent / "agent_reasoning"
        file_a = run_dir / "run-A.log"
        file_b = run_dir / "run-B.log"

        # File A must not have file B's event
        lines_a = file_a.read_text().strip().splitlines()
        assert len(lines_a) == 1
        assert json.loads(lines_a[0])["resource_id"] == "r1"

        # File B must not have file A's event
        lines_b = file_b.read_text().strip().splitlines()
        assert len(lines_b) == 1
        assert json.loads(lines_b[0])["resource_id"] == "r2"


class TestStartNewRunErrorHandling:
    """Tests that start_new_run() handles filesystem errors gracefully."""

    def test_filesystem_error_prints_to_stderr(self, tmp_path: Path, capsys):
        """On failure to create the log file, error is printed to stderr."""
        # Point to a path where mkdir will succeed but touch will fail
        # Use a read-only parent trick — but simpler: mock Path.touch to raise
        log_file = tmp_path / "logs" / "agent_reasoning.log"
        log_file.parent.mkdir(parents=True)
        log_file.touch()
        logger = ReasoningLogger(log_path=log_file)

        # Patch touch to simulate an OS error
        with patch.object(Path, "touch", side_effect=OSError("disk full")):
            logger.start_new_run("run-fail")

        captured = capsys.readouterr()
        assert "ReasoningLogger: failed to create" in captured.err
        assert "disk full" in captured.err

    def test_filesystem_error_does_not_raise(self, tmp_path: Path):
        """start_new_run() must never propagate exceptions to the caller."""
        log_file = tmp_path / "logs" / "agent_reasoning.log"
        log_file.parent.mkdir(parents=True)
        log_file.touch()
        logger = ReasoningLogger(log_path=log_file)

        with patch.object(Path, "touch", side_effect=OSError("disk full")):
            # Must not raise
            logger.start_new_run("run-fail")
