"""Property-based tests for agents.reasoning_logger.ReasoningLogger.

Uses Hypothesis to validate universal correctness properties across
randomly generated inputs. Covers structured JSON validity and
sequential append behavior.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from agents.reasoning_logger import ReasoningLogger


# --- Strategies ---

# Use characters that cover quotes, backslashes, and unicode — NOT just default ASCII.
# Blacklist surrogate category ('Cs') as these are not valid standalone characters.
unicode_text_strategy = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=0,
    max_size=100,
)

# Agent names: up to 100 chars to test truncation behavior too
agent_strategy = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=0,
    max_size=100,
)

# Message: up to 600 chars to test truncation behavior too
message_strategy = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=0,
    max_size=600,
)

# Valid event types
event_type_strategy = st.sampled_from(list(ReasoningLogger.VALID_EVENT_TYPES))

# Resource ID: arbitrary unicode text
resource_id_strategy = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=0,
    max_size=80,
)


# --- Property 8: Reasoning logger emits valid structured JSON ---
# Feature: savings-tracker-localstack, Property 8: Reasoning logger emits valid structured JSON


@settings(max_examples=100)
@given(
    agent=agent_strategy,
    event_type=event_type_strategy,
    resource_id=resource_id_strategy,
    message=message_strategy,
)
def test_reasoning_logger_emits_valid_structured_json(agent, event_type, resource_id, message):
    """
    Property 8: Reasoning logger emits valid structured JSON

    For any combination of agent name (string, 0-64 chars), event_type in
    {check, finding, skip, decision, handoff}, resource_id (string), and
    message (string, 0-500 chars), calling emit() SHALL append exactly one
    line to the log file that passes json.loads() and contains all required
    keys: timestamp, agent, event_type, resource_id, message.

    Uses st.text(alphabet=st.characters(blacklist_categories=('Cs',))) for
    message and agent fields to cover quotes, backslashes, and unicode
    characters — NOT just default ASCII.

    **Validates: Requirements 9.4, 9.9**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        log_file = tmp_path / "reasoning.log"

        logger = ReasoningLogger(log_path=log_file)
        logger.emit(agent, event_type, resource_id, message)

        # The log file must exist and have exactly one line
        content = log_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        assert len(lines) == 1, f"Expected 1 line, got {len(lines)}"

        # The line must be valid JSON
        entry = json.loads(lines[0])

        # All required keys must be present
        required_keys = {"timestamp", "agent", "event_type", "resource_id", "message"}
        assert required_keys.issubset(entry.keys()), (
            f"Missing keys: {required_keys - set(entry.keys())}"
        )

        # Verify field constraints
        # Agent is truncated to 64 chars
        assert len(entry["agent"]) <= 64
        assert entry["agent"] == agent[:64]

        # Message is truncated to 500 chars
        assert len(entry["message"]) <= 500
        assert entry["message"] == message[:500]

        # Event type is the valid one we passed in
        assert entry["event_type"] == event_type

        # Resource ID is preserved as-is
        assert entry["resource_id"] == resource_id

        # Timestamp is present and non-empty ISO 8601 with UTC
        assert len(entry["timestamp"]) > 0
        assert "+00:00" in entry["timestamp"]


# --- Property 9: Reasoning logger sequential append ---
# Feature: savings-tracker-localstack, Property 9: Reasoning logger sequential append


@settings(max_examples=100)
@given(
    events=st.lists(
        st.tuples(
            agent_strategy,
            event_type_strategy,
            resource_id_strategy,
            message_strategy,
        ),
        min_size=1,
        max_size=50,
    )
)
def test_reasoning_logger_sequential_append(events):
    """
    Property 9: Reasoning logger sequential append

    For any sequence of N calls to emit() within a single run (after a single
    truncate() call), the log file SHALL contain exactly N lines, and reading
    them back in order SHALL yield the same sequence of (agent, event_type,
    resource_id, message) tuples as the input sequence.

    Covers truncation behavior: agent truncated to 64 chars, message to 500 chars,
    invalid event_type becomes "unknown".

    **Validates: Requirements 9.6**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        log_file = tmp_path / "reasoning.log"

        logger = ReasoningLogger(log_path=log_file)
        logger.truncate()

        # Emit all events sequentially
        for agent, event_type, resource_id, message in events:
            logger.emit(agent, event_type, resource_id, message)

        # Read back the log file
        content = log_file.read_text(encoding="utf-8")
        lines = content.splitlines()

        # The log file must contain exactly N lines
        assert len(lines) == len(events), (
            f"Expected {len(events)} lines, got {len(lines)}"
        )

        # Each line must decode to the expected tuple (with truncation applied)
        for i, (agent, event_type, resource_id, message) in enumerate(events):
            entry = json.loads(lines[i])

            # Apply expected truncation/validation
            expected_agent = agent[:64]
            expected_message = message[:500]
            expected_event_type = (
                event_type if event_type in ReasoningLogger.VALID_EVENT_TYPES else "unknown"
            )

            assert entry["agent"] == expected_agent, (
                f"Line {i}: agent mismatch"
            )
            assert entry["event_type"] == expected_event_type, (
                f"Line {i}: event_type mismatch"
            )
            assert entry["resource_id"] == resource_id, (
                f"Line {i}: resource_id mismatch"
            )
            assert entry["message"] == expected_message, (
                f"Line {i}: message mismatch"
            )
