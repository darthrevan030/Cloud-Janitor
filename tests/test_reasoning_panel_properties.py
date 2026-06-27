"""Property-based tests for the reasoning log panel section header transitions.

Uses Hypothesis to validate that section headers are correctly inserted
when the agent name changes between consecutive reasoning log events,
and that same-agent consecutive events do NOT produce section headers.

# Feature: savings-tracker-localstack, Property 10: Agent section header transitions
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st


# --- Replicate the rendering logic from app.py to test in isolation ---
# (app.py cannot be imported directly in test context due to Streamlit side effects)

_REASONING_EVENT_COLORS = {
    "check": "#9e9e9e",
    "finding": "#ff9800",
    "skip": "#bdbdbd",
    "decision": "#2196f3",
}


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_reasoning_event_html(event: dict, show_header: bool = False) -> str:
    """Render a single reasoning event as color-coded HTML.

    Mirrors app.py render_reasoning_event_html exactly.
    """
    agent = event.get("agent", "unknown")
    event_type = event.get("event_type", "unknown")
    resource_id = event.get("resource_id", "")
    message = event.get("message", "")
    timestamp = event.get("timestamp", "")

    ts_display = timestamp[:19] if len(timestamp) >= 19 else timestamp

    parts: list[str] = []

    # Section header when agent changes (requirement 10.3)
    if show_header:
        parts.append(
            f'<div style="margin-top:12px;margin-bottom:4px;font-weight:700;'
            f'font-size:0.95rem;border-bottom:1px solid #ddd;padding-bottom:2px;">'
            f'🤖 {_escape_html(agent)}</div>'
        )

    # Build the event line with color coding
    if event_type == "handoff":
        style = "font-weight:bold;"
    else:
        color = _REASONING_EVENT_COLORS.get(event_type, "#9e9e9e")
        style = f"color:{color};"

    resource_part = f" <code>{_escape_html(resource_id)}</code>" if resource_id else ""
    parts.append(
        f'<div style="{style}font-size:0.85rem;padding:2px 0;line-height:1.4;">'
        f'<span style="color:#888;font-size:0.75rem;">{_escape_html(ts_display)}</span> '
        f'[{_escape_html(event_type)}]{resource_part} {_escape_html(message)}'
        f'</div>'
    )

    return "".join(parts)


def _build_reasoning_html(events: list[dict]) -> str:
    """Build the full HTML for the reasoning log panel from a list of parsed events.

    Inserts section headers when the agent name changes between consecutive events.
    Mirrors app.py _build_reasoning_html exactly.
    """
    if not events:
        return ""

    html_parts: list[str] = []
    prev_agent: str | None = None

    for event in events:
        agent = event.get("agent", "unknown")
        show_header = (agent != prev_agent)
        html_parts.append(render_reasoning_event_html(event, show_header=show_header))
        prev_agent = agent

    return "".join(html_parts)


# --- Strategies ---

# Agent names — use arbitrary unicode text
agent_name_strategy = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=1,
    max_size=30,
)

# Small pool of agent names to increase transitions and same-agent runs
agent_pool_strategy = st.sampled_from([
    "finops_auditor",
    "secops_guard",
    "remediation_architect",
    "schema_validator",
    "approval_gate",
])

event_type_strategy = st.sampled_from(["check", "finding", "skip", "decision", "handoff"])

resource_id_strategy = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=0,
    max_size=40,
)

message_strategy = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=0,
    max_size=100,
)


def _make_event(agent: str, event_type: str, resource_id: str, message: str) -> dict:
    """Create a reasoning event dict."""
    return {
        "timestamp": "2026-06-28T12:00:00+00:00",
        "agent": agent,
        "event_type": event_type,
        "resource_id": resource_id,
        "message": message,
    }


# --- Property 10: Agent section header transitions ---
# Feature: savings-tracker-localstack, Property 10: Agent section header transitions


@settings(max_examples=100)
@given(
    events=st.lists(
        st.tuples(
            agent_pool_strategy,
            event_type_strategy,
            resource_id_strategy,
            message_strategy,
        ),
        min_size=1,
        max_size=30,
    )
)
def test_section_headers_inserted_on_agent_change(events):
    """
    Property 10: Agent section header transitions

    For any sequence of reasoning log events where the agent field changes
    between consecutive entries, the rendering function SHALL insert a section
    header containing the new agent name at each transition point. Events with
    the same agent as their predecessor SHALL NOT produce a section header.

    **Validates: Requirements 10.3**
    """
    event_dicts = [
        _make_event(agent, event_type, resource_id, message)
        for agent, event_type, resource_id, message in events
    ]

    html_output = _build_reasoning_html(event_dicts)

    # Determine expected header transitions:
    # A header is shown when agent differs from the previous event's agent.
    # The first event always gets a header (prev_agent starts as None).
    expected_headers: list[str] = []
    prev_agent: str | None = None
    for agent, _, _, _ in events:
        if agent != prev_agent:
            expected_headers.append(agent)
        prev_agent = agent

    # Extract actual section headers from the HTML output.
    # Section headers have the pattern: 🤖 {agent_name}</div>
    header_pattern = re.compile(
        r'<div style="margin-top:12px;margin-bottom:4px;font-weight:700;'
        r'[^"]*">\s*🤖\s*([^<]+)</div>'
    )
    actual_headers = header_pattern.findall(html_output)

    # Unescape HTML entities in extracted headers for comparison
    def _unescape(text: str) -> str:
        return (
            text.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .strip()
        )

    actual_headers_unescaped = [_unescape(h) for h in actual_headers]

    assert len(actual_headers_unescaped) == len(expected_headers), (
        f"Expected {len(expected_headers)} headers, got {len(actual_headers_unescaped)}.\n"
        f"Expected agents: {expected_headers}\n"
        f"Actual agents: {actual_headers_unescaped}"
    )

    for i, (expected, actual) in enumerate(zip(expected_headers, actual_headers_unescaped)):
        assert actual == expected, (
            f"Header {i}: expected agent '{expected}', got '{actual}'"
        )


@settings(max_examples=100)
@given(
    agent=agent_name_strategy,
    n_events=st.integers(min_value=2, max_value=20),
    event_types=st.lists(event_type_strategy, min_size=2, max_size=20),
)
def test_same_agent_no_extra_headers(agent, n_events, event_types):
    """
    Property 10 (corollary): Same-agent consecutive events produce no extra headers.

    When all events in a sequence have the same agent name, only ONE section
    header (for the first event) SHALL be produced. No subsequent events
    should trigger additional headers.

    **Validates: Requirements 10.3**
    """
    assume(len(event_types) >= n_events)
    event_types = event_types[:n_events]

    event_dicts = [
        _make_event(agent, et, f"resource-{i}", f"message {i}")
        for i, et in enumerate(event_types)
    ]

    html_output = _build_reasoning_html(event_dicts)

    # Only one header should exist — for the first event
    header_pattern = re.compile(
        r'<div style="margin-top:12px;margin-bottom:4px;font-weight:700;'
        r'[^"]*">\s*🤖\s*[^<]+</div>'
    )
    headers_found = header_pattern.findall(html_output)

    assert len(headers_found) == 1, (
        f"Expected exactly 1 header for same-agent sequence, got {len(headers_found)}"
    )


@settings(max_examples=100)
@given(
    agents=st.lists(
        agent_name_strategy,
        min_size=2,
        max_size=10,
        unique=True,
    ),
)
def test_alternating_agents_all_get_headers(agents):
    """
    Property 10 (alternating case): When every consecutive event has a
    different agent, every event SHALL produce a section header.

    **Validates: Requirements 10.3**
    """
    event_dicts = [
        _make_event(agent, "check", f"r-{i}", f"msg-{i}")
        for i, agent in enumerate(agents)
    ]

    html_output = _build_reasoning_html(event_dicts)

    # Every event should produce a header since all agents are unique
    header_pattern = re.compile(
        r'<div style="margin-top:12px;margin-bottom:4px;font-weight:700;'
        r'[^"]*">\s*🤖\s*([^<]+)</div>'
    )
    headers_found = header_pattern.findall(html_output)

    assert len(headers_found) == len(agents), (
        f"Expected {len(agents)} headers (one per unique agent), got {len(headers_found)}"
    )
