"""Quick validation of reasoning panel logic.

Tests the parse_reasoning_events function and section header detection.
Rewritten from script-style to proper pytest tests with concrete assertions.
"""

import json
import tempfile
from pathlib import Path

import pytest


# Core parsing function (replicated from app.py to avoid Streamlit import side effects)
def parse_reasoning_events(log_path: Path) -> list[dict]:
    """Parse reasoning events from a JSONL log file."""
    if not log_path.exists():
        return []
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return []
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            events.append(event)
        except (json.JSONDecodeError, ValueError):
            continue
    return events


def build_section_headers(events: list[dict]) -> list[bool]:
    """Determine which events should show a section header.

    Returns a list of booleans: True if the event should show a header
    (agent changed from previous), False otherwise.
    """
    prev_agent = None
    headers = []
    for event in events:
        agent = event.get("agent", "unknown")
        show_header = (agent != prev_agent)
        headers.append(show_header)
        prev_agent = agent
    return headers


class TestParseReasoningEvents:
    """Tests for parsing JSONL reasoning log files."""

    def test_malformed_lines_skipped_silently(self, tmp_path: Path):
        """Malformed lines are skipped, valid lines are preserved."""
        log_file = tmp_path / "reasoning.log"
        log_file.write_text(
            '{"agent":"a","event_type":"check","resource_id":"r1","message":"ok","timestamp":"2026-01-01T00:00:00Z"}\n'
            "this is not json\n"
            '{"agent":"b","event_type":"finding","resource_id":"r2","message":"found","timestamp":"2026-01-01T00:01:00Z"}\n'
        )
        events = parse_reasoning_events(log_file)
        assert len(events) == 2
        assert events[0]["agent"] == "a"
        assert events[0]["event_type"] == "check"
        assert events[1]["agent"] == "b"
        assert events[1]["event_type"] == "finding"

    def test_empty_file_returns_empty_list(self, tmp_path: Path):
        """An empty log file returns an empty list."""
        log_file = tmp_path / "reasoning.log"
        log_file.write_text("")
        events = parse_reasoning_events(log_file)
        assert events == []

    def test_nonexistent_file_returns_empty_list(self, tmp_path: Path):
        """A non-existent file returns an empty list (no exception)."""
        events = parse_reasoning_events(tmp_path / "nonexistent_file.log")
        assert events == []

    def test_all_valid_lines_returned(self, tmp_path: Path):
        """When all lines are valid JSON, all are returned."""
        log_file = tmp_path / "reasoning.log"
        log_file.write_text(
            '{"agent":"x","event_type":"check","resource_id":"","message":"m1","timestamp":"t1"}\n'
            '{"agent":"y","event_type":"finding","resource_id":"r1","message":"m2","timestamp":"t2"}\n'
        )
        events = parse_reasoning_events(log_file)
        assert len(events) == 2
        assert events[0]["message"] == "m1"
        assert events[1]["message"] == "m2"

    def test_required_keys_present_in_valid_events(self, tmp_path: Path):
        """Valid events must contain all required keys."""
        log_file = tmp_path / "reasoning.log"
        log_file.write_text(
            '{"agent":"finops","event_type":"check","resource_id":"r1","message":"ok","timestamp":"2026-01-01T00:00:00Z"}\n'
        )
        events = parse_reasoning_events(log_file)
        assert len(events) == 1
        required_keys = {"agent", "event_type", "resource_id", "message", "timestamp"}
        assert required_keys.issubset(events[0].keys())


class TestSectionHeaders:
    """Tests for section header detection on agent transitions."""

    def test_headers_inserted_on_agent_change(self):
        """Section header is True when agent changes from previous."""
        events = [
            {"agent": "finops", "event_type": "check", "resource_id": "r1", "message": "m1", "timestamp": "t1"},
            {"agent": "finops", "event_type": "finding", "resource_id": "r2", "message": "m2", "timestamp": "t2"},
            {"agent": "secops", "event_type": "check", "resource_id": "r3", "message": "m3", "timestamp": "t3"},
        ]
        headers = build_section_headers(events)
        assert headers == [True, False, True]

    def test_all_same_agent_one_header(self):
        """When all events have the same agent, only the first gets a header."""
        events = [
            {"agent": "finops", "event_type": "check"},
            {"agent": "finops", "event_type": "finding"},
            {"agent": "finops", "event_type": "handoff"},
        ]
        headers = build_section_headers(events)
        assert headers == [True, False, False]

    def test_alternating_agents_all_headers(self):
        """Alternating agents produce a header on every event."""
        events = [
            {"agent": "a", "event_type": "check"},
            {"agent": "b", "event_type": "check"},
            {"agent": "a", "event_type": "finding"},
        ]
        headers = build_section_headers(events)
        assert headers == [True, True, True]

    def test_empty_events_list(self):
        """Empty event list returns empty header list."""
        headers = build_section_headers([])
        assert headers == []

    def test_single_event_gets_header(self):
        """A single event always gets a header (first event)."""
        events = [{"agent": "finops", "event_type": "check"}]
        headers = build_section_headers(events)
        assert headers == [True]
