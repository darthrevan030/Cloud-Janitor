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

import json
import time
from pathlib import Path

import streamlit as st

from orchestrator import AuditResult, Orchestrator

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
# Constants
# ──────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent
FINDINGS_STORE_PATH = PROJECT_ROOT / "findings_store.json"
REMEDIATION_PATH = PROJECT_ROOT / "output" / "remediation.tf"
ROLLBACKS_DIR = PROJECT_ROOT / "rollbacks"
AUDIT_LOG_PATH = PROJECT_ROOT / "audit.log"

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
    st.subheader("📝 Remediation & Rollback HCL")

    with st.container(border=True):
        # Remediation HCL
        remediation_hcl = load_remediation_hcl()

        # Determine available rollback resources
        rollback_files = list(ROLLBACKS_DIR.glob("*.tf"))
        resource_ids = [f.stem for f in rollback_files if f.stem != ".gitkeep"]

        if resource_ids:
            selected_resource = st.selectbox(
                "Select resource for side-by-side view:",
                options=resource_ids,
                key="diff_resource_select",
            )

            diff_left, diff_right = st.columns([1, 1])

            with diff_left:
                st.markdown("**Remediation HCL**")
                st.code(remediation_hcl, language="hcl")

            with diff_right:
                st.markdown("**Rollback HCL**")
                rollback_hcl = load_rollback_hcl(selected_resource)
                st.code(rollback_hcl, language="hcl")
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
