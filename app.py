"""
Cloud Janitor Dashboard

Streamlit-based UI with 4 panels:
  - Agent Activity Feed (left-top): shows sequential agent execution status with live dots
  - Findings Panel (right-top): displays findings with severity tags
  - Diff View (left-bottom): remediation HCL vs rollback HCL side by side
  - Audit Log (right-bottom): append-only audit trail

Usage:
    streamlit run app.py
"""

from __future__ import annotations

import difflib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from packaging.version import Version

import streamlit as st

from orchestrator import ApprovalResult, AuditResult, Orchestrator, RollbackResult

# ──────────────────────────────────────────────────────────────────────
# Page config
# ──────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Cloud Janitor Dashboard",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ──────────────────────────────────────────────────────────────────────
# CSS for pulsing status dot animation
# ──────────────────────────────────────────────────────────────────────

st.markdown(
    """
    <style>
    @keyframes pulse {
        0% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.4; transform: scale(1.3); }
        100% { opacity: 1; transform: scale(1); }
    }
    .status-dot {
        display: inline-block;
        width: 12px;
        height: 12px;
        border-radius: 50%;
        margin-right: 8px;
        vertical-align: middle;
    }
    .dot-idle {
        background-color: #9e9e9e;
    }
    .dot-running {
        background-color: #2196f3;
        animation: pulse 1s ease-in-out infinite;
    }
    .dot-success {
        background-color: #4caf50;
    }
    .dot-failure {
        background-color: #f44336;
    }
    .agent-row {
        display: flex;
        align-items: center;
        padding: 8px 0;
        font-size: 1rem;
    }
    .agent-name {
        font-weight: 600;
        margin-right: 8px;
    }
    .agent-status {
        color: #888;
        font-style: italic;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("☁️ Cloud Janitor Dashboard")

# ──────────────────────────────────────────────────────────────────────
# Savings Counter (displayed after session state is initialized below)
# ──────────────────────────────────────────────────────────────────────
savings_placeholder = st.empty()

# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent
FINDINGS_STORE_PATH = PROJECT_ROOT / "findings_store.json"
REMEDIATION_PATH = PROJECT_ROOT / "output" / "remediation.tf"
ROLLBACKS_DIR = PROJECT_ROOT / "rollbacks"
AUDIT_LOG_PATH = PROJECT_ROOT / "audit.log"
REASONING_LOG_PATH = PROJECT_ROOT / "agent_reasoning.log"

# ──────────────────────────────────────────────────────────────────────
# Session state initialization
# ──────────────────────────────────────────────────────────────────────

if "orchestrator" not in st.session_state:
    st.session_state.orchestrator = Orchestrator()

if "audit_result" not in st.session_state:
    st.session_state.audit_result = None

if "agent_status" not in st.session_state:
    st.session_state.agent_status = {
        "finops": "idle",
        "secops": "idle",
        "remediation": "idle",
    }

if "approval_history" not in st.session_state:
    st.session_state.approval_history = []

if "pending_rollback" not in st.session_state:
    st.session_state.pending_rollback = None

if "total_savings" not in st.session_state:
    st.session_state.total_savings = 0.0

if "last_saving_delta" not in st.session_state:
    st.session_state.last_saving_delta = None

# ──────────────────────────────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────────────────────────────


def load_findings() -> list[dict]:
    """Load findings from findings_store.json."""
    if not FINDINGS_STORE_PATH.exists():
        return []
    try:
        with open(FINDINGS_STORE_PATH) as f:
            data = json.load(f)
        return data.get("findings", [])
    except (json.JSONDecodeError, IOError):
        return []


def load_remediation_hcl() -> str:
    """Load the generated remediation HCL."""
    if not REMEDIATION_PATH.exists():
        return "No remediation plan generated yet."
    return REMEDIATION_PATH.read_text(encoding="utf-8")


def load_rollback_hcl(resource_id: str) -> str:
    """Load rollback HCL for a specific resource."""
    rollback_path = ROLLBACKS_DIR / f"{resource_id}.tf"
    if not rollback_path.exists():
        return f"No rollback file for {resource_id}."
    return rollback_path.read_text(encoding="utf-8")


def load_audit_log() -> list[str]:
    """Load the append-only audit log lines."""
    if not AUDIT_LOG_PATH.exists():
        return []
    try:
        lines = AUDIT_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
        return lines
    except IOError:
        return []


def severity_color(severity: str) -> str:
    """Map severity to a color for display."""
    colors = {
        "CRITICAL": "🔴",
        "HIGH": "🟠",
        "MEDIUM": "🟡",
        "LOW": "🟢",
    }
    return colors.get(severity, "⚪")


def agent_status_icon(status: str) -> str:
    """Map agent status to an icon."""
    icons = {
        "idle": "⚪",
        "running": "🔵",
        "success": "🟢",
        "failure": "🔴",
    }
    return icons.get(status, "⚪")


def render_agent_status_html(agent_name: str, status: str) -> str:
    """Render a single agent row with an animated CSS dot."""
    status_labels = {
        "idle": "idle",
        "running": "running…",
        "success": "complete",
        "failure": "failed",
    }
    label = status_labels.get(status, "idle")
    return (
        f'<div class="agent-row">'
        f'<span class="status-dot dot-{status}"></span>'
        f'<span class="agent-name">{agent_name}</span>'
        f'<span class="agent-status">{label}</span>'
        f'</div>'
    )


def render_diff_html(left_text: str, right_text: str) -> tuple[str, str, bool]:
    """Compute a side-by-side diff and return color-coded HTML for left and right panels.

    Returns (left_html, right_html, is_identical).
    Green highlight = line added in that panel's version.
    Red highlight = line removed (present in the other panel's version).
    """
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()

    matcher = difflib.SequenceMatcher(None, left_lines, right_lines)
    opcodes = matcher.get_opcodes()

    if all(tag == "equal" for tag, *_ in opcodes):
        return "", "", True

    left_html_lines: list[str] = []
    right_html_lines: list[str] = []

    def _escape(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    line_style_base = (
        "margin:0;padding:2px 6px;font-family:monospace;font-size:0.82rem;"
        "white-space:pre;line-height:1.5;"
    )
    style_neutral = f'{line_style_base}background:transparent;'
    style_add = f'{line_style_base}background:#d4edda;color:#155724;'
    style_remove = f'{line_style_base}background:#f8d7da;color:#721c24;'
    style_change_left = f'{line_style_base}background:#fff3cd;color:#856404;'
    style_change_right = f'{line_style_base}background:#cce5ff;color:#004085;'
    style_blank = f'{line_style_base}background:#f5f5f5;color:#aaa;'

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            for line in left_lines[i1:i2]:
                escaped = _escape(line)
                left_html_lines.append(f'<div style="{style_neutral}">{escaped}</div>')
                right_html_lines.append(f'<div style="{style_neutral}">{escaped}</div>')
        elif tag == "replace":
            max_len = max(i2 - i1, j2 - j1)
            for idx in range(max_len):
                if idx < (i2 - i1):
                    escaped = _escape(left_lines[i1 + idx])
                    left_html_lines.append(f'<div style="{style_change_left}">- {escaped}</div>')
                else:
                    left_html_lines.append(f'<div style="{style_blank}"> </div>')
                if idx < (j2 - j1):
                    escaped = _escape(right_lines[j1 + idx])
                    right_html_lines.append(f'<div style="{style_change_right}">+ {escaped}</div>')
                else:
                    right_html_lines.append(f'<div style="{style_blank}"> </div>')
        elif tag == "delete":
            for line in left_lines[i1:i2]:
                escaped = _escape(line)
                left_html_lines.append(f'<div style="{style_remove}">- {escaped}</div>')
                right_html_lines.append(f'<div style="{style_blank}"> </div>')
        elif tag == "insert":
            for line in right_lines[j1:j2]:
                escaped = _escape(line)
                left_html_lines.append(f'<div style="{style_blank}"> </div>')
                right_html_lines.append(f'<div style="{style_add}">+ {escaped}</div>')

    left_html = (
        '<div style="border:1px solid #ddd;border-radius:4px;padding:8px;'
        'overflow-x:auto;max-height:400px;overflow-y:auto;background:#fafafa;">'
        + "".join(left_html_lines)
        + "</div>"
    )
    right_html = (
        '<div style="border:1px solid #ddd;border-radius:4px;padding:8px;'
        'overflow-x:auto;max-height:400px;overflow-y:auto;background:#fafafa;">'
        + "".join(right_html_lines)
        + "</div>"
    )

    return left_html, right_html, False


def render_agent_feed_html(statuses: dict[str, str]) -> str:
    """Render the full agent activity feed as HTML with animated dots."""
    agents = [
        ("FinOps Auditor", "finops"),
        ("SecOps Guard", "secops"),
        ("Remediation Architect", "remediation"),
    ]
    rows = "".join(
        render_agent_status_html(name, statuses[key]) for name, key in agents
    )
    pipeline = (
        '<div style="margin-top:8px;color:#888;font-size:0.85rem;">'
        'Pipeline: FinOps Auditor → SecOps Guard → Remediation Architect'
        '</div>'
    )
    return rows + pipeline


# ──────────────────────────────────────────────────────────────────────
# Reasoning Log Panel helpers
# ──────────────────────────────────────────────────────────────────────

# Color mapping for reasoning event types
_REASONING_EVENT_COLORS: dict[str, str] = {
    "check": "#9e9e9e",
    "finding": "#ff9800",
    "skip": "#bdbdbd",
    "decision": "#2196f3",
}

# Streamlit version check for fragment support
_STREAMLIT_HAS_FRAGMENT = Version(st.__version__) >= Version("1.33.0")


def parse_reasoning_events(log_path: Path | None = None) -> list[dict]:
    """Read agent_reasoning.log and return a list of parsed JSON events.

    Silently skips malformed lines (requirement 10.6).
    Returns an empty list if the file does not exist or cannot be read.
    """
    if log_path is None:
        log_path = REASONING_LOG_PATH
    if not log_path.exists():
        return []
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return []

    events: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            events.append(event)
        except (json.JSONDecodeError, ValueError):
            # Skip malformed lines silently (requirement 10.6)
            continue
    return events


def render_reasoning_event_html(event: dict, show_header: bool = False) -> str:
    """Render a single reasoning event as color-coded HTML.

    Args:
        event: Parsed JSON event dict with keys: agent, event_type, resource_id, message, timestamp.
        show_header: If True, render a section header with the agent name above the event.

    Returns:
        HTML string for this event (with optional header).
    """
    agent = event.get("agent", "unknown")
    event_type = event.get("event_type", "unknown")
    resource_id = event.get("resource_id", "")
    message = event.get("message", "")
    timestamp = event.get("timestamp", "")

    # Truncate timestamp for display
    ts_display = timestamp[:19] if len(timestamp) >= 19 else timestamp

    parts: list[str] = []

    # Section header when agent changes (requirement 10.3)
    if show_header:
        parts.append(
            f'<div style="margin-top:12px;margin-bottom:4px;font-weight:700;'
            f'font-size:0.95rem;border-bottom:1px solid #ddd;padding-bottom:2px;">'
            f'🤖 {_escape_html(agent)}</div>'
        )

    # Build the event line with color coding (requirement 10.2)
    if event_type == "handoff":
        # Handoff: bold text, no specific color
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


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_reasoning_html(events: list[dict]) -> str:
    """Build the full HTML for the reasoning log panel from a list of parsed events.

    Inserts section headers when the agent name changes between consecutive events.
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


def reasoning_log_panel() -> None:
    """Poll agent_reasoning.log and render color-coded events.

    Uses @st.fragment(run_every=1) when Streamlit >= 1.33 for efficient
    partial reruns. Falls back to full page integration otherwise.

    Clears previous display when a new audit starts (requirement 10.5).
    Auto-scrolls to latest event during audit execution (requirement 10.4).
    """
    # Check if a new audit has started — clear previous events
    if "reasoning_log_last_count" not in st.session_state:
        st.session_state.reasoning_log_last_count = 0

    events = parse_reasoning_events()

    # Detect new audit: if events count decreased (file was truncated)
    if len(events) < st.session_state.reasoning_log_last_count:
        # File was truncated — new audit started (requirement 10.5)
        st.session_state.reasoning_log_last_count = 0

    st.session_state.reasoning_log_last_count = len(events)

    if not events:
        st.info("No reasoning events yet. Execute an audit to see agent reasoning.")
        return

    # Build and render the HTML
    html_content = _build_reasoning_html(events)

    # Wrap in a scrollable container with auto-scroll
    container_html = (
        '<div id="reasoning-log-container" style="'
        'max-height:400px;overflow-y:auto;border:1px solid #ddd;'
        'border-radius:4px;padding:8px;background:#fafafa;">'
        f'{html_content}'
        '</div>'
    )

    st.markdown(container_html, unsafe_allow_html=True)

    # Auto-scroll to bottom via JavaScript (requirement 10.4)
    scroll_js = (
        '<script>'
        'var container = document.getElementById("reasoning-log-container");'
        'if (container) { container.scrollTop = container.scrollHeight; }'
        '</script>'
    )
    st.markdown(scroll_js, unsafe_allow_html=True)


# Apply @st.fragment decorator for Streamlit >= 1.33
if _STREAMLIT_HAS_FRAGMENT:
    reasoning_log_panel = st.fragment(reasoning_log_panel, run_every=1)


def _reasoning_log_fallback_polling() -> None:
    """Fallback for Streamlit < 1.33: poll reasoning log in main thread.

    Runs audit in a background thread and polls using st.empty() + time.sleep(1)
    while checking session_state['audit_running'] flag.
    """
    placeholder = st.empty()

    if st.session_state.get("audit_running", False):
        while st.session_state.get("audit_running", False):
            events = parse_reasoning_events()
            if events:
                html_content = _build_reasoning_html(events)
                container_html = (
                    '<div style="max-height:400px;overflow-y:auto;'
                    'border:1px solid #ddd;border-radius:4px;padding:8px;'
                    'background:#fafafa;">'
                    f'{html_content}'
                    '</div>'
                )
                placeholder.markdown(container_html, unsafe_allow_html=True)
            time.sleep(1)
    else:
        # Static render when not running
        events = parse_reasoning_events()
        if events:
            html_content = _build_reasoning_html(events)
            container_html = (
                '<div style="max-height:400px;overflow-y:auto;'
                'border:1px solid #ddd;border-radius:4px;padding:8px;'
                'background:#fafafa;">'
                f'{html_content}'
                '</div>'
            )
            placeholder.markdown(container_html, unsafe_allow_html=True)
        else:
            placeholder.info("No reasoning events yet. Execute an audit to see agent reasoning.")


# ──────────────────────────────────────────────────────────────────────
# Render Savings Counter
# ──────────────────────────────────────────────────────────────────────

def _calculate_potential_savings() -> float:
    """Sum cost_estimate_monthly from all loaded findings."""
    findings = load_findings()
    return sum(f.get("cost_estimate_monthly", 0.0) for f in findings)


with savings_placeholder.container(border=True):
    total_savings = st.session_state.total_savings
    last_delta = st.session_state.last_saving_delta
    potential_savings = _calculate_potential_savings()

    savings_left, savings_right = st.columns(2)

    with savings_left:
        delta_str = f"+${last_delta:.2f}" if last_delta else None
        st.metric(
            label="Estimated Monthly Savings",
            value=f"${total_savings:.2f}/mo",
            delta=delta_str,
        )

    with savings_right:
        st.metric(
            label="Potential Savings",
            value=f"${potential_savings:.2f}/mo",
        )

# ──────────────────────────────────────────────────────────────────────
# Execute Audit button
# ──────────────────────────────────────────────────────────────────────

st.divider()

# Agent feed placeholder for live updates during execution
agent_feed_placeholder = st.empty()


def _render_live_feed(statuses: dict[str, str]) -> None:
    """Update the agent feed placeholder with current statuses."""
    agent_feed_placeholder.markdown(
        render_agent_feed_html(statuses),
        unsafe_allow_html=True,
    )


if st.button("🚀 Execute Audit", type="primary", use_container_width=True):
    orch = st.session_state.orchestrator

    # Reset to idle
    statuses = {"finops": "idle", "secops": "idle", "remediation": "idle"}
    _render_live_feed(statuses)
    time.sleep(0.3)

    # Step 1: FinOps Auditor
    statuses["finops"] = "running"
    _render_live_feed(statuses)

    try:
        orch._log_action("scan", "all", "started", "FinOps Auditor scan initiated")
        finops_findings = orch._finops.scan()
        orch._log_action("scan", "all", "success", f"FinOps found {len(finops_findings)} finding(s)")
        statuses["finops"] = "success"
    except Exception as e:
        statuses["finops"] = "failure"
        _render_live_feed(statuses)
        st.session_state.agent_status = statuses
        st.error(f"FinOps Auditor failed: {e}")
        st.stop()

    _render_live_feed(statuses)

    # Step 2: SecOps Guard
    statuses["secops"] = "running"
    _render_live_feed(statuses)

    try:
        orch._log_action("scan", "all", "started", "SecOps Guard scan initiated")
        secops_findings = orch._secops.scan()
        orch._log_action("scan", "all", "success", f"SecOps found {len(secops_findings)} finding(s)")
        statuses["secops"] = "success"
    except Exception as e:
        statuses["secops"] = "failure"
        _render_live_feed(statuses)
        st.session_state.agent_status = statuses
        st.error(f"SecOps Guard failed: {e}")
        st.stop()

    _render_live_feed(statuses)

    # Step 3: Remediation Architect
    statuses["remediation"] = "running"
    _render_live_feed(statuses)

    try:
        # Validate findings store
        validation_error = orch._validate_findings_store()
        if validation_error:
            raise RuntimeError(validation_error)

        # Plan
        orch._log_action("plan", "all", "started", "Remediation Architect planning")
        plans = orch._architect.plan()
        orch._last_plans = plans

        blocked_plans = [p for p in plans if p.blocked]
        active_plans = [p for p in plans if not p.blocked]

        for p in blocked_plans:
            orch._log_action("plan", p.resource_id, "blocked", p.block_reason)

        orch._log_action(
            "plan", "all", "success",
            f"Generated {len(active_plans)} plan(s), {len(blocked_plans)} blocked",
        )

        # Pre-remediation hook
        hook_error = None
        if active_plans:
            hook_error = orch._run_pre_remediation_hook(active_plans)

        if hook_error:
            orch._log_action("plan", "all", "blocked", f"Pre-remediation hook failed: {hook_error}")
            statuses["remediation"] = "failure"
            _render_live_feed(statuses)
            st.session_state.agent_status = statuses

            st.session_state.audit_result = AuditResult(
                success=False,
                findings=finops_findings + secops_findings,
                plans=active_plans,
                blocked_plans=blocked_plans,
                hook_error=hook_error,
            )
            st.error(f"Pre-remediation hook failed: {hook_error}")
            st.stop()

        statuses["remediation"] = "success"

        st.session_state.audit_result = AuditResult(
            success=True,
            findings=finops_findings + secops_findings,
            plans=active_plans,
            blocked_plans=blocked_plans,
        )

    except Exception as e:
        statuses["remediation"] = "failure"
        _render_live_feed(statuses)
        st.session_state.agent_status = statuses
        st.error(f"Remediation Architect failed: {e}")
        st.stop()

    _render_live_feed(statuses)
    st.session_state.agent_status = statuses
    st.success("Audit pipeline completed successfully.")
else:
    # Static render when not executing
    _render_live_feed(st.session_state.agent_status)

st.divider()

# ──────────────────────────────────────────────────────────────────────
# 4-Panel Layout
# ──────────────────────────────────────────────────────────────────────

top_left, top_right = st.columns([1, 1])
bottom_left, bottom_right = st.columns([1, 1])

# ──────────────────────────────────────────────────────────────────────
# Panel 1: Agent Activity Feed (top-left)
# ──────────────────────────────────────────────────────────────────────

with top_left:
    st.subheader("🤖 Agent Activity Feed")

    with st.container(border=True):
        st.markdown(
            render_agent_feed_html(st.session_state.agent_status),
            unsafe_allow_html=True,
        )

# ──────────────────────────────────────────────────────────────────────
# Panel 2: Findings Panel (top-right)
# ──────────────────────────────────────────────────────────────────────

with top_right:
    st.subheader("🔍 Findings")

    with st.container(border=True):
        findings = load_findings()

        if not findings:
            st.info("No findings yet. Click 'Execute Audit' to scan.")
        else:
            for finding in findings:
                severity = finding.get("severity", "UNKNOWN")
                icon = severity_color(severity)
                resource_id = finding.get("resource_id", "unknown")
                title = finding.get("title", "Untitled finding")
                agent = finding.get("agent", "unknown")
                cost = finding.get("cost_estimate_monthly", 0)

                col_sev, col_detail = st.columns([1, 5])
                with col_sev:
                    st.markdown(f"{icon} **{severity}**")
                with col_detail:
                    st.markdown(f"**{title}**")
                    detail_parts = [f"`{resource_id}`", f"Agent: {agent}"]
                    if cost > 0:
                        detail_parts.append(f"${cost:.2f}/mo")
                    st.caption(" · ".join(detail_parts))

            st.divider()
            st.caption(f"Total findings: {len(findings)}")

# ──────────────────────────────────────────────────────────────────────
# Panel 3: Diff View (bottom-left)
# ──────────────────────────────────────────────────────────────────────

with bottom_left:
    st.subheader("📝 Remediation vs Rollback Diff")

    with st.container(border=True):
        # Remediation HCL
        remediation_hcl = load_remediation_hcl()

        # Determine available rollback resources
        rollback_files = list(ROLLBACKS_DIR.glob("*.tf"))
        resource_ids = [f.stem for f in rollback_files if f.stem != ".gitkeep"]

        if resource_ids:
            selected_resource = st.selectbox(
                "Select resource to compare:",
                options=resource_ids,
                key="diff_resource_select",
            )

            rollback_hcl = load_rollback_hcl(selected_resource)

            # Compute diff
            left_html, right_html, is_identical = render_diff_html(
                remediation_hcl, rollback_hcl
            )

            if is_identical:
                st.success("No differences — remediation and rollback HCL are identical.")
                st.code(remediation_hcl, language="hcl")
            else:
                # Legend
                st.markdown(
                    '<div style="font-size:0.8rem;margin-bottom:8px;">'
                    '<span style="background:#fff3cd;padding:2px 6px;border-radius:3px;">changed (remediation)</span> '
                    '<span style="background:#cce5ff;padding:2px 6px;border-radius:3px;">changed (rollback)</span> '
                    '<span style="background:#f8d7da;padding:2px 6px;border-radius:3px;">removed</span> '
                    '<span style="background:#d4edda;padding:2px 6px;border-radius:3px;">added</span>'
                    '</div>',
                    unsafe_allow_html=True,
                )

                diff_left, diff_right = st.columns([1, 1])

                with diff_left:
                    st.markdown("**Remediation HCL**")
                    st.markdown(left_html, unsafe_allow_html=True)

                with diff_right:
                    st.markdown("**Rollback HCL**")
                    st.markdown(right_html, unsafe_allow_html=True)
        else:
            st.info("No remediation or rollback plans available yet.")
            st.code(remediation_hcl, language="hcl")

# ──────────────────────────────────────────────────────────────────────
# Panel 4: Audit Log (bottom-right)
# ──────────────────────────────────────────────────────────────────────

with bottom_right:
    st.subheader("📋 Audit Log")

    with st.container(border=True):
        # Show in-memory audit trail from orchestrator
        trail = st.session_state.orchestrator.get_audit_trail()

        if trail:
            for entry in reversed(trail):
                ts = entry.timestamp[:19]  # Trim to readable timestamp
                action = entry.action
                resource = entry.resource_id
                result = entry.result
                details = entry.details

                if result == "success":
                    result_icon = "✅"
                elif result == "failure":
                    result_icon = "❌"
                elif result == "blocked":
                    result_icon = "🚫"
                else:
                    result_icon = "ℹ️"

                st.markdown(
                    f"{result_icon} `{ts}` | **{action}** | `{resource}` — {details}"
                )
        else:
            # Fall back to file-based audit log
            log_lines = load_audit_log()
            if log_lines:
                for line in reversed(log_lines[-50:]):  # Show latest 50 entries
                    st.text(line)
            else:
                st.info("No audit entries yet. Execute an audit to generate trail.")


# ──────────────────────────────────────────────────────────────────────
# Reasoning Log Panel
# ──────────────────────────────────────────────────────────────────────

st.divider()
st.subheader("🧠 Agent Reasoning Log")

with st.container(border=True):
    if _STREAMLIT_HAS_FRAGMENT:
        reasoning_log_panel()
    else:
        _reasoning_log_fallback_polling()

# ──────────────────────────────────────────────────────────────────────
# Approval & Actions Section
# ──────────────────────────────────────────────────────────────────────

st.divider()
st.subheader("🔐 Approval & Actions")

# Only show when an audit has completed with plans
audit_result = st.session_state.audit_result
if audit_result is not None and audit_result.plans:
    # Gather resource IDs from active plans
    plan_resource_ids = [p.resource_id for p in audit_result.plans]

    with st.container(border=True):
        # Resource selector
        selected_resource = st.selectbox(
            "Select resource to approve or rollback:",
            options=plan_resource_ids,
            key="approval_resource_select",
        )

        # ── Pending rollback confirmation flow ────────────────────────
        if st.session_state.pending_rollback is not None:
            pending_id = st.session_state.pending_rollback
            st.warning(
                f"⚠️ Rollback pending confirmation for `{pending_id}`. "
                f'Type **CONFIRM ROLLBACK {pending_id}** below to proceed.'
            )

            confirm_input = st.text_input(
                "Confirm rollback command:",
                key="confirm_rollback_input",
                placeholder=f"CONFIRM ROLLBACK {pending_id}",
            )

            col_confirm, col_cancel = st.columns([1, 1])
            with col_confirm:
                if st.button("✅ Confirm Rollback", key="btn_confirm_rollback", use_container_width=True):
                    orch = st.session_state.orchestrator
                    result = orch.rollback(confirm_input)
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

                    if result.success:
                        st.session_state.pending_rollback = None
                        st.session_state.approval_history.append({
                            "action": "rollback_confirmed",
                            "resource_id": result.resource_id,
                            "timestamp": ts,
                            "success": True,
                        })
                        st.rerun()
                    else:
                        st.session_state.approval_history.append({
                            "action": "rollback_confirm_failed",
                            "resource_id": pending_id,
                            "timestamp": ts,
                            "success": False,
                            "error": result.error,
                        })
                        st.rerun()

            with col_cancel:
                if st.button("❌ Cancel Rollback", key="btn_cancel_rollback", use_container_width=True):
                    st.session_state.pending_rollback = None
                    st.rerun()

        # ── Normal approval / rollback flow ───────────────────────────
        else:
            # Approval input
            approval_input = st.text_input(
                "Approval command:",
                key="approval_input",
                placeholder=f"APPROVE {selected_resource}",
                help="Enter the exact command: APPROVE <resource-id> (case-sensitive)",
            )

            col_approve, col_rollback = st.columns([1, 1])

            with col_approve:
                if st.button("✅ Submit Approval", key="btn_approve", use_container_width=True):
                    orch = st.session_state.orchestrator
                    result = orch.approve(approval_input, resource_id=selected_resource)
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

                    # Track savings on successful approval
                    if result.success and audit_result and audit_result.findings:
                        approved_resource_id = result.resource_id or selected_resource
                        for finding in audit_result.findings:
                            if finding.get("resource_id") == approved_resource_id:
                                cost = finding.get("cost_estimate_monthly", 0.0)
                                if cost > 0:
                                    st.session_state.total_savings += cost
                                    st.session_state.last_saving_delta = cost
                                break

                    st.session_state.approval_history.append({
                        "action": "approval",
                        "resource_id": result.resource_id or selected_resource,
                        "timestamp": ts,
                        "success": result.success,
                        "error": result.error,
                        "locked": result.locked,
                        "expected_format": result.expected_format,
                        "attempts_remaining": result.attempts_remaining,
                    })
                    st.rerun()

            with col_rollback:
                if st.button("🔄 Rollback", key="btn_rollback", use_container_width=True):
                    orch = st.session_state.orchestrator
                    command = f"ROLLBACK {selected_resource}"
                    result = orch.rollback(command)
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

                    if result.needs_confirmation:
                        st.session_state.pending_rollback = selected_resource
                        st.session_state.approval_history.append({
                            "action": "rollback_initiated",
                            "resource_id": result.resource_id,
                            "timestamp": ts,
                            "success": False,
                            "needs_confirmation": True,
                        })
                        st.rerun()
                    elif result.success:
                        st.session_state.approval_history.append({
                            "action": "rollback",
                            "resource_id": result.resource_id,
                            "timestamp": ts,
                            "success": True,
                        })
                        st.rerun()
                    else:
                        st.session_state.approval_history.append({
                            "action": "rollback_failed",
                            "resource_id": result.resource_id or selected_resource,
                            "timestamp": ts,
                            "success": False,
                            "error": result.error,
                        })
                        st.rerun()

    # ── Confirmation display ──────────────────────────────────────────
    if st.session_state.approval_history:
        st.markdown("**Recent Actions**")
        # Show last 5 actions in reverse order
        for entry in reversed(st.session_state.approval_history[-5:]):
            ts = entry.get("timestamp", "")
            resource = entry.get("resource_id", "")
            action = entry.get("action", "")

            if entry.get("success"):
                # Success — green confirmation
                if action == "rollback_confirmed":
                    st.success(f"✅ Rollback confirmed for `{resource}` at {ts}")
                else:
                    st.success(f"✅ Approved `{resource}` at {ts}")

            elif entry.get("locked"):
                # Locked — max attempts exceeded
                st.error(
                    f"🔒 **Locked** — Max approval attempts exceeded for `{resource}`. "
                    f"No further attempts allowed."
                )

            elif entry.get("needs_confirmation"):
                # Rollback awaiting confirmation — yellow
                st.warning(
                    f"⏳ Rollback initiated for `{resource}` — awaiting confirmation "
                    f"(`CONFIRM ROLLBACK {resource}`)"
                )

            else:
                # Failure — red error with hints
                error_msg = entry.get("error", "Unknown error")
                expected = entry.get("expected_format")
                remaining = entry.get("attempts_remaining")

                detail_parts = [f"❌ Failed: {error_msg}"]
                if expected:
                    detail_parts.append(f"Expected format: `{expected}`")
                if remaining is not None:
                    detail_parts.append(f"Attempts remaining: **{remaining}**")

                st.error(" · ".join(detail_parts))

else:
    with st.container(border=True):
        st.info("No remediation plans available. Execute an audit to generate plans for approval.")
