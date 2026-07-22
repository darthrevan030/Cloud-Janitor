"""Property test: History Append Survives Pipeline Exceptions (Property 1)."""

import json
import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from hypothesis import given, settings, strategies as st

from scheduler import JanitorScheduler


# Strategy: exception types that Orchestrator or execute_audit might raise
exception_types_st = st.sampled_from([RuntimeError, ValueError, OSError, TypeError, KeyError])

# Strategy: exception messages (ASCII only to avoid Windows cp1252 encoding issues)
exception_msg_st = st.text(
    min_size=0,
    max_size=30,
    alphabet=st.characters(whitelist_categories=("L", "N"), blacklist_categories=("Cs",), blacklist_characters="\x00", max_codepoint=127),
)

# Strategy: which phase raises — "init" (Orchestrator constructor) or "audit" (execute_audit)
failure_phase_st = st.sampled_from(["init", "audit"])


def _make_scheduler_in(tmp_path: Path) -> JanitorScheduler:
    (tmp_path / "output" / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "remediations").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "rollbacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "policies").mkdir(parents=True, exist_ok=True)
    (tmp_path / "hooks").mkdir(parents=True, exist_ok=True)
    sched = JanitorScheduler(project_root=tmp_path, notifiers=[])
    return sched


def _cleanup_logger():
    """Remove file handlers from the scheduler logger to release file locks."""
    logger = logging.getLogger("janitor_scheduler")
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)


@settings(max_examples=100, deadline=None)
@given(exc_type=exception_types_st, exc_msg=exception_msg_st, phase=failure_phase_st)
def test_property_history_appended_on_any_pipeline_exception(exc_type, exc_msg, phase):
    """For any exception raised during Orchestrator init or execute_audit,
    exactly one history record is appended with status=failed, findings_snapshot={},
    and no exception propagates."""
    # Clean up any leftover logger handlers from previous iterations
    _cleanup_logger()

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        sched = _make_scheduler_in(tmp_path)

        with patch("scheduler.Orchestrator") as mock_orch_cls:
            if phase == "init":
                mock_orch_cls.side_effect = exc_type(exc_msg)
            else:
                mock_instance = MagicMock()
                mock_instance.execute_audit.side_effect = exc_type(exc_msg)
                mock_orch_cls.return_value = mock_instance

            # Must not raise
            sched._run_scan()

        history_path = tmp_path / "output" / "logs" / "scheduler_history.jsonl"
        assert history_path.exists(), "History file must exist after _run_scan()"
        lines = history_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1, f"Expected 1 history record, got {len(lines)}"

        record = json.loads(lines[0])
        assert record["status"] == "failed"
        assert record["findings_snapshot"] == {}
        assert "scan_id" in record
        assert "timestamp_start" in record
        assert "timestamp_end" in record

        # Clean up logger to release file handles before tempdir deletion
        _cleanup_logger()
