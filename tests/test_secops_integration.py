"""Integration tests for SecOps Guard + ReasoningLogger wiring.

Verifies that SecOpsGuard.scan() emits the correct reasoning events:
- "check" at scan start
- "check" per security group rule and per encryption resource
- "finding" per violation detected
- "handoff" at scan complete
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.reasoning_logger import ReasoningLogger
from agents.secops_guard import SecOpsGuard


class TestSecOpsReasoningIntegration:
    """Verify SecOps Guard emits reasoning events during scan."""

    def _run_scan_with_logger(self, tmp_path: Path) -> list[dict]:
        """Run a full scan and return parsed reasoning log entries."""
        log_file = tmp_path / "reasoning.log"
        store_path = tmp_path / "findings_store.json"

        logger = ReasoningLogger(log_path=log_file)
        guard = SecOpsGuard(
            findings_store_path=store_path,
            reasoning_logger=logger,
        )
        guard.scan()

        lines = log_file.read_text().strip().splitlines()
        return [json.loads(line) for line in lines]

    def test_scan_emits_check_at_start(self, tmp_path: Path):
        """First event should be a 'check' indicating scan start."""
        events = self._run_scan_with_logger(tmp_path)
        assert len(events) > 0
        assert events[0]["event_type"] == "check"
        assert events[0]["agent"] == "secops_guard"
        assert "security audit" in events[0]["message"].lower()

    def test_scan_emits_handoff_at_end(self, tmp_path: Path):
        """Last event should be a 'handoff' indicating scan complete."""
        events = self._run_scan_with_logger(tmp_path)
        assert len(events) > 0
        assert events[-1]["event_type"] == "handoff"
        assert events[-1]["agent"] == "secops_guard"
        assert "complete" in events[-1]["message"].lower()

    def test_scan_emits_check_per_rule(self, tmp_path: Path):
        """Should emit 'check' events for each resource/rule examined."""
        events = self._run_scan_with_logger(tmp_path)
        check_events = [e for e in events if e["event_type"] == "check"]
        # At minimum: 1 start + per-resource checks
        assert len(check_events) >= 2

    def test_scan_emits_finding_per_violation(self, tmp_path: Path):
        """Should emit 'finding' events for each detected violation."""
        events = self._run_scan_with_logger(tmp_path)
        finding_events = [e for e in events if e["event_type"] == "finding"]
        # Fixtures produce 4 violations: 2 SG + 2 encryption
        assert len(finding_events) == 4

    def test_all_events_have_correct_agent(self, tmp_path: Path):
        """All emitted events should have agent='secops_guard'."""
        events = self._run_scan_with_logger(tmp_path)
        for event in events:
            assert event["agent"] == "secops_guard"

    def test_all_events_are_valid_json_with_required_keys(self, tmp_path: Path):
        """Every event must have timestamp, agent, event_type, resource_id, message."""
        events = self._run_scan_with_logger(tmp_path)
        required_keys = {"timestamp", "agent", "event_type", "resource_id", "message"}
        for event in events:
            assert required_keys.issubset(event.keys())

    def test_finding_events_reference_resource_ids(self, tmp_path: Path):
        """Finding events should include the resource_id of the violation."""
        events = self._run_scan_with_logger(tmp_path)
        finding_events = [e for e in events if e["event_type"] == "finding"]
        resource_ids = {e["resource_id"] for e in finding_events}
        # Should reference the fixture resources
        assert "sg-prod-redis" in resource_ids
        assert "sg-web-servers" in resource_ids

    def test_event_ordering(self, tmp_path: Path):
        """Events should follow: check (start) → checks/findings → handoff."""
        events = self._run_scan_with_logger(tmp_path)
        assert events[0]["event_type"] == "check"
        assert events[-1]["event_type"] == "handoff"
        # No handoff in the middle
        for event in events[1:-1]:
            assert event["event_type"] in ("check", "finding")

    def test_handoff_message_includes_count(self, tmp_path: Path):
        """Handoff message should mention number of findings detected."""
        events = self._run_scan_with_logger(tmp_path)
        handoff = events[-1]
        assert "4" in handoff["message"]  # 4 findings from fixtures
