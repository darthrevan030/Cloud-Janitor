"""
Cloud Janitor Dashboard

Streamlit-based UI with dark terminal-adjacent design.
Panels: Agent Pipeline, Findings, Diff View, Audit Log, Reasoning Log, Approval Gate.

Usage:
    streamlit run app.py
"""

from __future__ import annotations

import difflib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from packaging.version import Version

import streamlit as st

from cloud_janitor.orchestrator import Orchestrator
from cloud_janitor.core.logging_config import configure_logging
from cloud_janitor.core.audit_query import query_audit, export_audit_csv, export_audit_json, UNSCOPED
from cloud_janitor.core.paths import (
    FINDINGS_STORE_PATH,
    REMEDIATIONS_DIR,
    ROLLBACKS_DIR,
    AUDIT_LOG_PATH,
    REASONING_LOG_PATH,
    OUTPUT_DIR,
    PROJECT_ROOT,
    resolve_latest_findings_store,
)

configure_logging()

# Phase B/C agent imports — each agent imported individually with explicit
# ImportError handling so that type checkers see the fallback as Optional[type].
from typing import Optional  # noqa: E402

try:
    from cloud_janitor.agents.query_interpreter import QueryInterpreter
except ImportError:
    QueryInterpreter: Optional[type] = None  # type: ignore[no-redef]

try:
    from cloud_janitor.agents.explainer import RemediationExplainer
except ImportError:
    RemediationExplainer: Optional[type] = None  # type: ignore[no-redef]

try:
    from cloud_janitor.agents.policy_suggester import PolicySuggester
except ImportError:
    PolicySuggester: Optional[type] = None  # type: ignore[no-redef]

try:
    from cloud_janitor.agents.anomaly_detector import AnomalyDetector
except ImportError:
    AnomalyDetector: Optional[type] = None  # type: ignore[no-redef]

try:
    from cloud_janitor.agents.drift_detector import DriftDetector
except ImportError:
    DriftDetector: Optional[type] = None  # type: ignore[no-redef]

try:
    from cloud_janitor.agents.multi_account_orchestrator import MultiAccountOrchestrator
except ImportError:
    MultiAccountOrchestrator: Optional[type] = None  # type: ignore[no-redef]

try:
    from cloud_janitor.agents.tagger import ResourceTagger
except ImportError:
    ResourceTagger: Optional[type] = None  # type: ignore[no-redef]

try:
    from cloud_janitor.agents.incident_policy_generator import IncidentPolicyGenerator
except ImportError:
    IncidentPolicyGenerator: Optional[type] = None  # type: ignore[no-redef]

try:
    from scheduler import JanitorScheduler
except ImportError:
    JanitorScheduler: Optional[type] = None  # type: ignore[no-redef]

# ──────────────────────────────────────────────────────────────────────
# Page config
# ──────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Cloud Janitor",
    page_icon="🧹",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ──────────────────────────────────────────────────────────────────────
# Design system — dark terminal aesthetic
# ──────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap');

/* ── Global reset ── */
html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', system-ui, sans-serif;
}

/* ── Dark base ── */
.stApp {
    background-color: #0d1117;
    color: #e6edf3;
}

/* ── Main content padding ── */
.main .block-container {
    padding: 1.5rem 2rem 3rem;
    max-width: 1400px;
}

/* ── Hide Streamlit chrome ── */
#MainMenu, footer, header { visibility: hidden; }

/* ── Page header ── */
.cj-header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0 0 1.5rem;
    border-bottom: 1px solid #21262d;
    margin-bottom: 1.5rem;
}
.cj-logo {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.4rem;
    font-weight: 600;
    color: #58a6ff;
    letter-spacing: -0.5px;
}
.cj-tagline {
    font-size: 0.8rem;
    color: #8b949e;
    font-family: 'IBM Plex Mono', monospace;
}

/* ── Metric cards ── */
.cj-metric-row {
    display: flex;
    gap: 12px;
    margin-bottom: 1.5rem;
}
.cj-metric {
    flex: 1;
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 6px;
    padding: 1rem 1.25rem;
}
.cj-metric-label {
    font-size: 0.72rem;
    color: #8b949e;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-family: 'IBM Plex Mono', monospace;
    margin-bottom: 4px;
}
.cj-metric-value {
    font-size: 1.6rem;
    font-weight: 600;
    font-family: 'IBM Plex Mono', monospace;
    color: #e6edf3;
    line-height: 1.2;
}
.cj-metric-value.green { color: #3fb950; }
.cj-metric-value.yellow { color: #d29922; }
.cj-metric-value.red { color: #f85149; }
.cj-metric-delta {
    font-size: 0.75rem;
    color: #3fb950;
    font-family: 'IBM Plex Mono', monospace;
    margin-top: 2px;
}

/* ── Panel cards ── */
.cj-panel {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 6px;
    padding: 1rem 1.25rem;
    margin-bottom: 1rem;
}
.cj-panel-title {
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: #8b949e;
    font-family: 'IBM Plex Mono', monospace;
    margin-bottom: 0.75rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid #21262d;
}

/* ── Pipeline agent rows ── */
@keyframes pulse {
    0%   { opacity: 1; box-shadow: 0 0 0 0 rgba(88,166,255,0.4); }
    70%  { opacity: 0.7; box-shadow: 0 0 0 6px rgba(88,166,255,0); }
    100% { opacity: 1; box-shadow: 0 0 0 0 rgba(88,166,255,0); }
}
.cj-agent-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 0;
    border-bottom: 1px solid #21262d;
}
.cj-agent-row:last-child { border-bottom: none; }
.cj-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
}
.cj-dot.idle    { background: #30363d; }
.cj-dot.running { background: #58a6ff; animation: pulse 1.2s ease-in-out infinite; }
.cj-dot.success { background: #3fb950; }
.cj-dot.failure { background: #f85149; }
.cj-agent-name {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.85rem;
    font-weight: 600;
    color: #e6edf3;
    flex: 1;
}
.cj-agent-status {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.75rem;
    color: #8b949e;
}
.cj-agent-status.running { color: #58a6ff; }
.cj-agent-status.success { color: #3fb950; }
.cj-agent-status.failure { color: #f85149; }
.cj-pipeline-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem;
    color: #30363d;
    margin-top: 10px;
    letter-spacing: 0.05em;
}

/* ── Severity badges ── */
.cj-badge {
    display: inline-block;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.65rem;
    font-weight: 600;
    padding: 2px 7px;
    border-radius: 3px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}
.cj-badge.CRITICAL { background: rgba(248,81,73,0.15); color: #f85149; border: 1px solid rgba(248,81,73,0.3); }
.cj-badge.HIGH     { background: rgba(210,153,34,0.15); color: #d29922; border: 1px solid rgba(210,153,34,0.3); }
.cj-badge.MEDIUM   { background: rgba(88,166,255,0.12); color: #58a6ff; border: 1px solid rgba(88,166,255,0.25); }
.cj-badge.LOW      { background: rgba(63,185,80,0.12);  color: #3fb950; border: 1px solid rgba(63,185,80,0.25); }

/* ── Finding rows ── */
.cj-finding {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 8px 0;
    border-bottom: 1px solid #21262d;
}
.cj-finding:last-child { border-bottom: none; }
.cj-finding-body { flex: 1; min-width: 0; }
.cj-finding-title {
    font-size: 0.88rem;
    font-weight: 500;
    color: #e6edf3;
    margin-bottom: 2px;
}
.cj-finding-meta {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.72rem;
    color: #8b949e;
}
.cj-finding-cost {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.72rem;
    color: #3fb950;
    white-space: nowrap;
    margin-top: 2px;
}

/* ── Code / diff panels ── */
.cj-code {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.78rem;
    background: #0d1117;
    border: 1px solid #21262d;
    border-radius: 4px;
    padding: 10px 12px;
    overflow-x: auto;
    overflow-y: auto;
    max-height: 380px;
    white-space: pre;
    line-height: 1.6;
    color: #e6edf3;
}
.cj-diff-line { margin: 0; padding: 1px 6px; white-space: pre; line-height: 1.6; font-family: 'IBM Plex Mono', monospace; font-size: 0.76rem; }
.cj-diff-equal   { color: #8b949e; }
.cj-diff-add     { background: rgba(63,185,80,0.1);  color: #3fb950; }
.cj-diff-remove  { background: rgba(248,81,73,0.1);  color: #f85149; }
.cj-diff-left    { background: rgba(210,153,34,0.1); color: #d29922; }
.cj-diff-right   { background: rgba(88,166,255,0.1); color: #58a6ff; }
.cj-diff-blank   { color: #21262d; }

/* ── Audit log ── */
.cj-log-line {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.76rem;
    padding: 4px 0;
    border-bottom: 1px solid #21262d;
    color: #8b949e;
    line-height: 1.5;
}
.cj-log-line:last-child { border-bottom: none; }
.cj-log-line .ts   { color: #30363d; }
.cj-log-line .act  { color: #58a6ff; }
.cj-log-line .ok   { color: #3fb950; }
.cj-log-line .fail { color: #f85149; }
.cj-log-line .blk  { color: #d29922; }

/* ── Reasoning log ── */
.cj-reasoning-container {
    background: #0d1117;
    border: 1px solid #21262d;
    border-radius: 4px;
    padding: 8px 12px;
    max-height: 320px;
    overflow-y: auto;
}
.cj-reasoning-agent {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem;
    font-weight: 600;
    color: #58a6ff;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin: 10px 0 4px;
    padding-bottom: 2px;
    border-bottom: 1px solid #21262d;
}
.cj-reasoning-agent:first-child { margin-top: 0; }
.cj-reasoning-event {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.75rem;
    padding: 2px 0;
    line-height: 1.5;
}
.cj-re-check    { color: #8b949e; }
.cj-re-finding  { color: #d29922; }
.cj-re-skip     { color: #30363d; }
.cj-re-decision { color: #58a6ff; }
.cj-re-handoff  { color: #3fb950; font-weight: 600; }
.cj-re-unknown  { color: #8b949e; }
.cj-re-ts       { color: #30363d; font-size: 0.68rem; margin-right: 4px; }
.cj-re-rid      { color: #6e7681; font-size: 0.7rem; }

/* ── Approval gate ── */
.cj-gate {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 1.25rem;
}
.cj-gate-warning {
    background: rgba(210,153,34,0.08);
    border: 1px solid rgba(210,153,34,0.3);
    border-radius: 4px;
    padding: 10px 14px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.8rem;
    color: #d29922;
    margin-bottom: 12px;
}
.cj-action-history {
    margin-top: 1rem;
    padding-top: 0.75rem;
    border-top: 1px solid #21262d;
}
.cj-action-entry {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.76rem;
    padding: 4px 0;
    color: #8b949e;
    border-bottom: 1px solid #21262d;
}
.cj-action-entry:last-child { border-bottom: none; }
.cj-action-entry.ok   { color: #3fb950; }
.cj-action-entry.fail { color: #f85149; }
.cj-action-entry.warn { color: #d29922; }
.cj-action-entry.lock { color: #f85149; font-weight: 600; }

/* ── Streamlit widget overrides ── */
.stButton > button {
    font-family: 'IBM Plex Mono', monospace !important;
    font-size: 0.82rem !important;
    font-weight: 600 !important;
    border-radius: 5px !important;
    border: 1px solid #30363d !important;
    background: #21262d !important;
    color: #e6edf3 !important;
    padding: 0.45rem 1rem !important;
    transition: all 0.15s !important;
}
.stButton > button:hover {
    background: #30363d !important;
    border-color: #58a6ff !important;
    color: #58a6ff !important;
}
.stButton > button[kind="primary"] {
    background: #1f6feb !important;
    border-color: #1f6feb !important;
    color: #fff !important;
}
.stButton > button[kind="primary"]:hover {
    background: #388bfd !important;
    border-color: #388bfd !important;
}
.stTextInput > div > div > input {
    font-family: 'IBM Plex Mono', monospace !important;
    font-size: 0.85rem !important;
    background: #0d1117 !important;
    border: 1px solid #30363d !important;
    border-radius: 5px !important;
    color: #e6edf3 !important;
}
.stTextInput > div > div > input:focus {
    border-color: #58a6ff !important;
    box-shadow: 0 0 0 2px rgba(88,166,255,0.15) !important;
}
.stSelectbox > div > div {
    font-family: 'IBM Plex Mono', monospace !important;
    font-size: 0.82rem !important;
    background: #0d1117 !important;
    border: 1px solid #30363d !important;
    border-radius: 5px !important;
    color: #e6edf3 !important;
}
div[data-testid="stAlert"] {
    border-radius: 5px !important;
    font-family: 'IBM Plex Mono', monospace !important;
    font-size: 0.82rem !important;
}
div[data-testid="stMetric"] {
    background: #161b22 !important;
    border: 1px solid #21262d !important;
    border-radius: 6px !important;
    padding: 0.75rem 1rem !important;
}

/* ── Structured error display ── */
.cj-error-structured {
    background: rgba(248,81,73,0.06);
    border: 1px solid rgba(248,81,73,0.25);
    border-radius: 6px;
    padding: 0.75rem 1rem;
    margin-bottom: 0.75rem;
    font-family: 'IBM Plex Mono', monospace;
}
.cj-error-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
}
.cj-error-category-badge {
    display: inline-block;
    font-size: 0.62rem;
    font-weight: 600;
    padding: 2px 6px;
    border-radius: 3px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    background: rgba(248,81,73,0.15);
    color: #f85149;
    border: 1px solid rgba(248,81,73,0.3);
}
.cj-error-agent-label {
    font-size: 0.72rem;
    color: #8b949e;
}
.cj-error-agent-name {
    font-size: 0.72rem;
    color: #58a6ff;
    font-weight: 600;
}
.cj-error-message {
    font-size: 0.82rem;
    color: #f85149;
    line-height: 1.4;
}
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

REMEDIATION_PATH = OUTPUT_DIR / "remediation.tf"

_STREAMLIT_HAS_FRAGMENT = Version(st.__version__) >= Version("1.33.0")

_REASONING_EVENT_COLORS: dict[str, str] = {
    "check": "cj-re-check",
    "finding": "cj-re-finding",
    "skip": "cj-re-skip",
    "decision": "cj-re-decision",
    "handoff": "cj-re-handoff",
}

# ──────────────────────────────────────────────────────────────────────
# Session state
# ──────────────────────────────────────────────────────────────────────

if "orchestrator" not in st.session_state:
    st.session_state.orchestrator = Orchestrator()
if "audit_result" not in st.session_state:
    st.session_state.audit_result = None
if "agent_status" not in st.session_state:
    st.session_state.agent_status = {"finops": "idle", "secops": "idle", "remediation": "idle"}
if "approval_history" not in st.session_state:
    st.session_state.approval_history = []
if "pending_rollback" not in st.session_state:
    st.session_state.pending_rollback = None
if "previewed_resources" not in st.session_state:
    st.session_state.previewed_resources = set()
if "total_savings" not in st.session_state:
    st.session_state.total_savings = 0.0
if "last_saving_delta" not in st.session_state:
    st.session_state.last_saving_delta = None
if "reasoning_log_last_count" not in st.session_state:
    st.session_state.reasoning_log_last_count = 0

# Phase B+C session state keys
if "nl_query_result" not in st.session_state:
    st.session_state.nl_query_result = None
if "explanation_cache" not in st.session_state:
    st.session_state.explanation_cache = {}
if "policy_suggestions" not in st.session_state:
    st.session_state.policy_suggestions = None
if "resource_tags_cache" not in st.session_state:
    st.session_state.resource_tags_cache = {}
if "incident_policies" not in st.session_state:
    st.session_state.incident_policies = None
if "anomaly_results" not in st.session_state:
    st.session_state.anomaly_results = None
if "drift_report" not in st.session_state:
    st.session_state.drift_report = None
if "scheduler_instance" not in st.session_state:
    st.session_state.scheduler_instance = None
if "multi_account_results" not in st.session_state:
    st.session_state.multi_account_results = None

# ──────────────────────────────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_structured_error(
    message: str,
    error_category: str | None = None,
    agent_name: str | None = None,
) -> None:
    """Render a structured error display in the Streamlit UI.

    When structured fields (error_category, agent_name) are available, renders
    a styled panel showing category badge, agent name, and message.
    Falls back to a plain st.error() when only the raw message is available.
    """
    if error_category or agent_name:
        category_html = ""
        if error_category:
            category_html = (
                f'<span class="cj-error-category-badge">{_esc(error_category)}</span>'
            )
        agent_html = ""
        if agent_name:
            agent_html = (
                f'<span class="cj-error-agent-label">agent:</span> '
                f'<span class="cj-error-agent-name">{_esc(agent_name)}</span>'
            )
        html = (
            f'<div class="cj-error-structured">'
            f'<div class="cj-error-header">{category_html}{agent_html}</div>'
            f'<div class="cj-error-message">{_esc(message)}</div>'
            f'</div>'
        )
        st.markdown(html, unsafe_allow_html=True)
    else:
        st.error(message)


def load_findings() -> list[dict]:
    path = resolve_latest_findings_store()
    if path is None:
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("findings", [])  # type: ignore[no-any-return]
    except FileNotFoundError:
        # Narrow TOCTOU race: pointed-to file pruned between resolve and open.
        # Retry resolution once.
        path = resolve_latest_findings_store()
        if path is None:
            return []
        try:
            with open(path) as f:
                data = json.load(f)
            return data.get("findings", [])  # type: ignore[no-any-return]
        except (json.JSONDecodeError, IOError):
            return []
    except (json.JSONDecodeError, IOError):
        return []


def load_remediation_hcl(resource_id: str | None = None) -> str:
    if resource_id:
        per_resource_path = REMEDIATIONS_DIR / f"{resource_id}.tf"
        if per_resource_path.exists():
            return per_resource_path.read_text(encoding="utf-8", errors="replace")
    if not REMEDIATION_PATH.exists():
        return ""
    return REMEDIATION_PATH.read_text(encoding="utf-8", errors="replace")


def load_rollback_hcl(resource_id: str) -> str:
    rollback_path = ROLLBACKS_DIR / f"{resource_id}.tf"
    if not rollback_path.exists():
        return ""
    return rollback_path.read_text(encoding="utf-8", errors="replace")


def load_audit_log() -> list[str]:
    if not AUDIT_LOG_PATH.exists():
        return []
    try:
        return AUDIT_LOG_PATH.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except IOError:
        return []


def _calculate_potential_savings() -> float:
    return sum(f.get("cost_estimate_monthly", 0.0) for f in load_findings())  # type: ignore[no-any-return]


def parse_reasoning_events(log_path: Path | None = None) -> list[dict]:
    if log_path is None:
        log_path = REASONING_LOG_PATH
    if not log_path.exists():
        return []
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue
    return events


# ──────────────────────────────────────────────────────────────────────
# Render helpers
# ──────────────────────────────────────────────────────────────────────

def render_pipeline_html(statuses: dict[str, str]) -> str:
    agents = [
        ("FinOps Auditor", "finops"),
        ("SecOps Guard", "secops"),
        ("Remediation Architect", "remediation"),
    ]
    status_labels = {"idle": "idle", "running": "running…", "success": "complete", "failure": "failed"}
    rows = ""
    for name, key in agents:
        s = statuses.get(key, "idle")
        label = status_labels.get(s, "idle")
        rows += (
            f'<div class="cj-agent-row">'
            f'<div class="cj-dot {s}"></div>'
            f'<span class="cj-agent-name">{name}</span>'
            f'<span class="cj-agent-status {s}">{label}</span>'
            f'</div>'
        )
    rows += '<div class="cj-pipeline-label">finops → secops → remediation</div>'
    return rows


def render_findings_html(findings: list[dict]) -> str:
    if not findings:
        return '<div style="color:#8b949e;font-family:\'IBM Plex Mono\',monospace;font-size:0.82rem;padding:8px 0;">No findings yet — run an audit.</div>'
    html = ""
    for f in findings:
        sev = f.get("severity", "LOW")
        rid = _esc(f.get("resource_id", "unknown"))
        title = _esc(f.get("title", "Untitled"))
        agent = _esc(f.get("agent", "unknown"))
        cost = f.get("cost_estimate_monthly", 0)
        cost_str = f'<div class="cj-finding-cost">${cost:.2f}/mo</div>' if cost > 0 else ""
        html += (
            f'<div class="cj-finding">'
            f'<div><span class="cj-badge {sev}">{sev}</span></div>'
            f'<div class="cj-finding-body">'
            f'<div class="cj-finding-title">{title}</div>'
            f'<div class="cj-finding-meta">{rid} · {agent}</div>'
            f'{cost_str}'
            f'</div>'
            f'</div>'
        )
    return html


def render_diff_html(left_text: str, right_text: str) -> tuple[str, str, bool]:
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    matcher = difflib.SequenceMatcher(None, left_lines, right_lines)
    opcodes = matcher.get_opcodes()

    if all(tag == "equal" for tag, *_ in opcodes):
        return "", "", True

    left_parts: list[str] = []
    right_parts: list[str] = []

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            for line in left_lines[i1:i2]:
                e = _esc(line)
                left_parts.append(f'<div class="cj-diff-line cj-diff-equal">{e}</div>')
                right_parts.append(f'<div class="cj-diff-line cj-diff-equal">{e}</div>')
        elif tag == "replace":
            max_len = max(i2 - i1, j2 - j1)
            for idx in range(max_len):
                if idx < (i2 - i1):
                    left_parts.append(f'<div class="cj-diff-line cj-diff-left">- {_esc(left_lines[i1+idx])}</div>')
                else:
                    left_parts.append('<div class="cj-diff-line cj-diff-blank"> </div>')
                if idx < (j2 - j1):
                    right_parts.append(f'<div class="cj-diff-line cj-diff-right">+ {_esc(right_lines[j1+idx])}</div>')
                else:
                    right_parts.append('<div class="cj-diff-line cj-diff-blank"> </div>')
        elif tag == "delete":
            for line in left_lines[i1:i2]:
                left_parts.append(f'<div class="cj-diff-line cj-diff-remove">- {_esc(line)}</div>')
                right_parts.append('<div class="cj-diff-line cj-diff-blank"> </div>')
        elif tag == "insert":
            for line in right_lines[j1:j2]:
                left_parts.append('<div class="cj-diff-line cj-diff-blank"> </div>')
                right_parts.append(f'<div class="cj-diff-line cj-diff-add">+ {_esc(line)}</div>')

    wrap = '<div class="cj-code" style="padding:6px 0;">{}</div>'
    return wrap.format("".join(left_parts)), wrap.format("".join(right_parts)), False


def render_audit_trail_html(trail: list, log_lines: list[str]) -> str:
    if trail:
        entries = list(reversed(trail[:20]))
        html = ""
        for entry in entries:
            ts = entry.timestamp[:19]
            action = _esc(entry.action)
            resource = _esc(entry.resource_id)
            result = entry.result
            details = _esc(entry.details)
            cls = {"success": "ok", "failure": "fail", "blocked": "blk"}.get(result, "")
            html += (
                f'<div class="cj-log-line">'
                f'<span class="ts">{ts}</span> '
                f'<span class="act">{action}</span> '
                f'<span class="{cls}">{resource}</span> '
                f'— {details}'
                f'</div>'
            )
        return html
    elif log_lines:
        html = ""
        for line in reversed(log_lines[-30:]):
            html += f'<div class="cj-log-line">{_esc(line)}</div>'
        return html
    return '<div style="color:#8b949e;font-family:\'IBM Plex Mono\',monospace;font-size:0.82rem;padding:8px 0;">No audit entries yet.</div>'


def render_reasoning_html(events: list[dict]) -> str:
    if not events:
        return '<div style="color:#8b949e;font-family:\'IBM Plex Mono\',monospace;font-size:0.8rem;padding:8px 0;">No reasoning events yet.</div>'
    html = ""
    prev_agent = None
    for event in events:
        agent = event.get("agent", "unknown")
        event_type = event.get("event_type", "unknown")
        resource_id = event.get("resource_id", "")
        message = _esc(event.get("message", ""))
        timestamp = event.get("timestamp", "")
        ts_display = timestamp[:19] if len(timestamp) >= 19 else timestamp

        if agent != prev_agent:
            html += f'<div class="cj-reasoning-agent">▸ {_esc(agent)}</div>'
            prev_agent = agent

        cls = _REASONING_EVENT_COLORS.get(event_type, "cj-re-unknown")
        rid_html = f' <span class="cj-re-rid">{_esc(resource_id)}</span>' if resource_id else ""
        html += (
            f'<div class="cj-reasoning-event {cls}">'
            f'<span class="cj-re-ts">{_esc(ts_display)}</span>'
            f'[{_esc(event_type)}]{rid_html} {message}'
            f'</div>'
        )
    return html


# ──────────────────────────────────────────────────────────────────────
# Page header
# ──────────────────────────────────────────────────────────────────────

st.markdown("""
<div class="cj-header">
    <div>
        <div class="cj-logo">🧹 cloud-janitor</div>
        <div class="cj-tagline">AI-native AWS auditor · finds waste · generates Terraform · requires approval</div>
    </div>
</div>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────
# Metrics row
# ──────────────────────────────────────────────────────────────────────

findings = load_findings()
potential = _calculate_potential_savings()
total_saved = st.session_state.total_savings
n_critical = sum(1 for f in findings if f.get("severity") == "CRITICAL")
n_high = sum(1 for f in findings if f.get("severity") == "HIGH")

m1, m2, m3, m4 = st.columns(4)
with m1:
    st.metric("Potential Savings", f"${potential:.2f}/mo", help="Sum of monthly costs across all flagged resources")
with m2:
    st.metric("Savings Achieved", f"${total_saved:.2f}/mo",
              delta=f"+${st.session_state.last_saving_delta:.2f}" if st.session_state.last_saving_delta else None)
with m3:
    st.metric("Critical Findings", str(n_critical), delta=f"{n_high} HIGH" if n_high else None,
              delta_color="inverse" if n_high else "off")
with m4:
    st.metric("Total Findings", str(len(findings)))

st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────
# Execute Audit button
# ──────────────────────────────────────────────────────────────────────

agent_feed_placeholder = st.empty()


def _render_live_feed(statuses: dict[str, str]) -> None:
    with agent_feed_placeholder.container():
        st.markdown(
            f'<div class="cj-panel">'
            f'<div class="cj-panel-title">Agent Pipeline</div>'
            f'{render_pipeline_html(statuses)}'
            f'</div>',
            unsafe_allow_html=True,
        )


_render_live_feed(st.session_state.agent_status)

# ──────────────────────────────────────────────────────────────────────
# Natural Language Query Input
# ──────────────────────────────────────────────────────────────────────

orch = st.session_state.orchestrator

if not hasattr(orch, "execute_natural_language_audit"):
    st.info("Natural-language audit feature is not yet available.")
else:
    nl_query = st.text_input(
        "Ask in plain English",
        key="nl_query_input",
        placeholder="e.g. Find idle EC2 instances older than 30 days",
        help="Describe what you want to audit — the system will interpret your query.",
    )

    nl_col1, nl_col2 = st.columns([1, 4])
    with nl_col1:
        nl_submitted = st.button("🔍  NL Audit", use_container_width=True)

    if nl_submitted and nl_query and nl_query.strip():
        with st.spinner("Interpreting query and running audit..."):
            try:
                result = orch.execute_natural_language_audit(nl_query.strip())
                st.session_state.nl_query_result = result
                st.session_state.audit_result = result
                # Cache anomalies and drift from NL audit result
                if hasattr(result, "anomalies") and result.anomalies:
                    st.session_state.anomaly_results = result.anomalies
                if hasattr(result, "drift_report") and result.drift_report:
                    st.session_state.drift_report = result.drift_report
                st.success(f"NL Audit complete — {len(result.findings)} finding(s).")
                st.rerun()
            except Exception as e:
                render_structured_error(f"NL Audit failed: {e}")

# Show NL query result summary if available
if st.session_state.nl_query_result is not None:
    nl_res = st.session_state.nl_query_result
    if hasattr(nl_res, "findings") and nl_res.findings:
        st.markdown(
            f'<div class="cj-panel">'
            f'<div class="cj-panel-title">NL Query Result ({len(nl_res.findings)} findings)</div>'
            f'{render_findings_html(nl_res.findings)}'
            f'</div>',
            unsafe_allow_html=True,
        )

st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

# Backend readiness indicator (calls the same function the Orchestrator uses)
from cloud_janitor.core.health import check_backend_health as _check_health

_health = _check_health()
if _health.reachable:
    st.success("✅ Backend ready", icon="✅")
elif _health.mode == "invalid_credentials":
    st.error("🔑 Credentials invalid — check AWS session/IAM permissions", icon="🔑")
else:
    st.error("🔴 Backend unreachable — check LocalStack is running / network connectivity", icon="🔴")

if st.button("▶  Run Audit", type="primary", use_container_width=False):
    orch = st.session_state.orchestrator

    def _on_agent_status(agent_name: str, status: str) -> None:
        st.session_state.setdefault("agent_status", {})[agent_name] = status

    with st.spinner("Running audit pipeline..."):
        result = orch.execute_audit(status_callback=_on_agent_status)

    # Render final agent statuses
    _render_live_feed(st.session_state.agent_status)

    if not result.success:
        render_structured_error(
            result.error or "Unknown error",
            error_category=result.error_category,
            agent_name=result.error_agent,
        )
        st.session_state.audit_result = result
        st.stop()

    st.session_state.audit_result = result

    # Populate anomaly and drift panels from the audit result
    if hasattr(result, "anomalies") and result.anomalies:
        st.session_state.anomaly_results = result.anomalies
    if hasattr(result, "drift_report") and result.drift_report:
        st.session_state.drift_report = result.drift_report

    st.success("Audit complete.")
    st.rerun()

st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────
# 4-Panel Layout
# ──────────────────────────────────────────────────────────────────────

top_left, top_right = st.columns([1, 1])
bottom_left, bottom_right = st.columns([1, 1])

# ── Findings ──────────────────────────────────────────────────────────
with top_left:
    findings = load_findings()
    st.markdown(
        f'<div class="cj-panel">'
        f'<div class="cj-panel-title">Findings ({len(findings)})</div>'
        f'{render_findings_html(findings)}'
        f'</div>',
        unsafe_allow_html=True,
    )

# ── Audit Log ─────────────────────────────────────────────────────────
# NOTE: Tamper-evidence (hash-chaining of audit trail entries) is an
# explicit non-goal for this spec phase — deferred until a specific
# compliance requirement names it (YAGNI). See phase3f-audit-query/requirements.md.
with top_right:
    trail = st.session_state.orchestrator.get_audit_trail()
    log_lines = load_audit_log()
    st.markdown(
        f'<div class="cj-panel" style="max-height:340px;overflow-y:auto;">'
        f'<div class="cj-panel-title">Audit Trail</div>'
        f'{render_audit_trail_html(trail, log_lines)}'
        f'</div>',
        unsafe_allow_html=True,
    )

# ── Diff View ─────────────────────────────────────────────────────────
with bottom_left:
    st.markdown('<div class="cj-panel"><div class="cj-panel-title">Remediation vs Rollback</div>', unsafe_allow_html=True)

    rollback_files = [f for f in ROLLBACKS_DIR.glob("*.tf") if f.stem != ".gitkeep"]
    resource_ids = [f.stem for f in rollback_files]

    if resource_ids:
        selected_diff_resource = st.selectbox("Resource", options=resource_ids, key="diff_resource_select", label_visibility="collapsed")
        remediation_hcl = load_remediation_hcl(selected_diff_resource)
        rollback_hcl = load_rollback_hcl(selected_diff_resource)
        left_html, right_html, is_identical = render_diff_html(remediation_hcl, rollback_hcl)

        if is_identical:
            st.info("Remediation and rollback HCL are identical.")
            st.markdown(f'<div class="cj-code">{_esc(remediation_hcl)}</div>', unsafe_allow_html=True)
        else:
            dc_left, dc_right = st.columns(2)
            with dc_left:
                st.markdown('<div style="font-family:\'IBM Plex Mono\',monospace;font-size:0.7rem;color:#d29922;margin-bottom:4px;">REMEDIATION</div>', unsafe_allow_html=True)
                st.markdown(left_html, unsafe_allow_html=True)
            with dc_right:
                st.markdown('<div style="font-family:\'IBM Plex Mono\',monospace;font-size:0.7rem;color:#58a6ff;margin-bottom:4px;">ROLLBACK</div>', unsafe_allow_html=True)
                st.markdown(right_html, unsafe_allow_html=True)
    elif load_remediation_hcl():
        remediation_hcl = load_remediation_hcl()
        st.markdown(f'<div class="cj-code">{_esc(remediation_hcl)}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div style="color:#8b949e;font-family:\'IBM Plex Mono\',monospace;font-size:0.82rem;padding:8px 0;">No remediation plan yet.</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

# ── Reasoning Log ─────────────────────────────────────────────────────
with bottom_right:
    events = parse_reasoning_events()
    if len(events) < st.session_state.reasoning_log_last_count:
        st.session_state.reasoning_log_last_count = 0
    st.session_state.reasoning_log_last_count = len(events)

    st.markdown(
        f'<div class="cj-panel">'
        f'<div class="cj-panel-title">Agent Reasoning ({len(events)} events)</div>'
        f'<div class="cj-reasoning-container">{render_reasoning_html(events)}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Run-Scoped Reasoning Logs ─────────────────────────────────────
    _reasoning_run_dir = OUTPUT_DIR / "logs" / "agent_reasoning"
    if _reasoning_run_dir.exists():
        _run_logs = sorted(_reasoning_run_dir.glob("*.log"), reverse=True)[:20]
        if _run_logs:
            st.markdown(
                '<div class="cj-panel">'
                '<div class="cj-panel-title">Run-Scoped Reasoning Logs</div>',
                unsafe_allow_html=True,
            )
            _selected_log = st.selectbox(
                "Select run log",
                options=[p.name for p in _run_logs],
                key="reasoning_run_log_select",
                label_visibility="collapsed",
            )
            if _selected_log:
                _selected_log_path = _reasoning_run_dir / _selected_log
                _run_events = parse_reasoning_events(_selected_log_path)
                st.markdown(
                    f'<div class="cj-reasoning-container">'
                    f'{render_reasoning_html(_run_events)}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            st.markdown('</div>', unsafe_allow_html=True)

st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────
# Approval Gate
# ──────────────────────────────────────────────────────────────────────

st.markdown('<div class="cj-panel-title" style="margin-bottom:8px;">Approval Gate</div>', unsafe_allow_html=True)

audit_result = st.session_state.audit_result

if audit_result is not None and audit_result.plans:
    plan_resource_ids = [p.resource_id for p in audit_result.plans]

    with st.container():
        selected_resource = st.selectbox(
            "Resource", options=plan_resource_ids,
            key="approval_resource_select", label_visibility="visible"
        )

        if st.session_state.pending_rollback is not None:
            pending_id = st.session_state.pending_rollback
            st.markdown(
                f'<div class="cj-gate-warning">⚠ Rollback pending — type CONFIRM ROLLBACK {_esc(pending_id)} to proceed</div>',
                unsafe_allow_html=True,
            )
            confirm_input = st.text_input(
                "Confirm rollback", key="confirm_rollback_input",
                placeholder=f"CONFIRM ROLLBACK {pending_id}",
            )
            col_confirm, col_cancel = st.columns([1, 1])
            with col_confirm:
                if st.button("Confirm Rollback", key="btn_confirm_rollback", use_container_width=True):
                    result = st.session_state.orchestrator.rollback(confirm_input)
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    if result.success:
                        st.session_state.pending_rollback = None
                        st.session_state.approval_history.append({"action": "rollback_confirmed", "resource_id": result.resource_id, "timestamp": ts, "success": True})
                    else:
                        st.session_state.approval_history.append({"action": "rollback_confirm_failed", "resource_id": pending_id, "timestamp": ts, "success": False, "error": result.error})
                    st.rerun()
            with col_cancel:
                if st.button("Cancel", key="btn_cancel_rollback", use_container_width=True):
                    st.session_state.pending_rollback = None
                    st.rerun()

        else:
            # Preview Plan button — gates Approve
            col_preview, col_spacer = st.columns([1, 1])
            with col_preview:
                if st.button("Preview Plan", key="btn_preview_plan", use_container_width=True):
                    orch = st.session_state.orchestrator
                    preview_result = orch.preview_plan(selected_resource)
                    if preview_result.success:
                        st.session_state.previewed_resources.add(selected_resource)
                        st.session_state[f"preview_{selected_resource}"] = preview_result
                    else:
                        st.session_state[f"preview_{selected_resource}"] = preview_result
                    st.rerun()

            # Render PlanPreview tables if available
            preview_key = f"preview_{selected_resource}"
            if preview_key in st.session_state and st.session_state[preview_key] is not None:
                prev = st.session_state[preview_key]
                if prev.success and prev.preview is not None:
                    p = prev.preview
                    if not prev.scope_ok:
                        st.warning(f"⚠ Scope check failed: {prev.scope_reason}")
                    if p.creates:
                        with st.expander(f"Creates ({len(p.creates)})", expanded=True):
                            st.table([{"Address": c.address, "Type": c.resource_type, "Actions": ", ".join(c.actions)} for c in p.creates])
                    if p.updates:
                        with st.expander(f"Updates ({len(p.updates)})", expanded=True):
                            st.table([{"Address": c.address, "Type": c.resource_type, "Changed Keys": ", ".join(c.changed_keys)} for c in p.updates])
                    if p.deletes:
                        with st.expander(f"Deletes ({len(p.deletes)})", expanded=True):
                            st.table([{"Address": c.address, "Type": c.resource_type, "Actions": ", ".join(c.actions)} for c in p.deletes])
                    if p.replacements:
                        with st.expander(f"Replacements ({len(p.replacements)})", expanded=True):
                            st.table([{"Address": c.address, "Type": c.resource_type, "Changed Keys": ", ".join(c.changed_keys)} for c in p.replacements])
                elif not prev.success:
                    st.error(f"Preview failed: {prev.error}")

            approval_input = st.text_input(
                "Command", key="approval_input",
                placeholder=f"APPROVE {selected_resource}",
                help="Exact match required — case-sensitive",
            )
            col_approve, col_rollback = st.columns([1, 1])
            with col_approve:
                approve_disabled = selected_resource not in st.session_state.previewed_resources
                if st.button("Approve", key="btn_approve", type="primary", use_container_width=True, disabled=approve_disabled):
                    orch = st.session_state.orchestrator
                    result = orch.approve(approval_input, resource_id=selected_resource)
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    if result.success and audit_result and audit_result.findings:
                        rid = result.resource_id or selected_resource
                        for f in audit_result.findings:
                            if f.get("resource_id") == rid:
                                cost = f.get("cost_estimate_monthly", 0.0)
                                if cost > 0:
                                    st.session_state.total_savings += cost
                                    st.session_state.last_saving_delta = cost
                                break
                    st.session_state.approval_history.append({
                        "action": "approval", "resource_id": result.resource_id or selected_resource,
                        "timestamp": ts, "success": result.success, "error": result.error,
                        "error_category": result.error_category, "error_agent": result.error_agent,
                        "locked": result.locked, "expected_format": result.expected_format,
                        "attempts_remaining": result.attempts_remaining,
                    })
                    st.rerun()
            with col_rollback:
                if st.button("Rollback", key="btn_rollback", use_container_width=True):
                    orch = st.session_state.orchestrator
                    result = orch.rollback(f"ROLLBACK {selected_resource}")
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    if result.needs_confirmation:
                        st.session_state.pending_rollback = selected_resource
                        st.session_state.approval_history.append({"action": "rollback_initiated", "resource_id": result.resource_id, "timestamp": ts, "success": False, "needs_confirmation": True})
                    elif result.success:
                        st.session_state.approval_history.append({"action": "rollback", "resource_id": result.resource_id, "timestamp": ts, "success": True})
                    else:
                        st.session_state.approval_history.append({"action": "rollback_failed", "resource_id": result.resource_id or selected_resource, "timestamp": ts, "success": False, "error": result.error, "error_category": result.error_category, "error_agent": result.error_agent})
                    st.rerun()

    # Action history
    if st.session_state.approval_history:
        st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
        history_html = '<div class="cj-action-history">'
        for entry in reversed(st.session_state.approval_history[-5:]):
            ts = entry.get("timestamp", "")[:19]
            resource = _esc(entry.get("resource_id", ""))
            action = _esc(entry.get("action", ""))
            if entry.get("locked"):
                history_html += f'<div class="cj-action-entry lock">🔒 {ts} · {resource} · locked — max attempts exceeded</div>'
            elif entry.get("success"):
                history_html += f'<div class="cj-action-entry ok">✓ {ts} · {action} · {resource}</div>'
            elif entry.get("needs_confirmation"):
                history_html += f'<div class="cj-action-entry warn">⟳ {ts} · rollback pending confirmation · {resource}</div>'
            else:
                err = _esc(entry.get("error", "unknown error"))
                rem = entry.get("attempts_remaining")
                rem_str = f" · {rem} attempts remaining" if rem is not None else ""
                cat = entry.get("error_category")
                agent = entry.get("error_agent")
                structured_parts = ""
                if cat:
                    structured_parts += f' · <span class="cj-error-category-badge">{_esc(cat)}</span>'
                if agent:
                    structured_parts += f' · <span class="cj-error-agent-name">{_esc(agent)}</span>'
                history_html += f'<div class="cj-action-entry fail">✗ {ts} · {resource}{structured_parts} · {err}{rem_str}</div>'
        history_html += '</div>'
        st.markdown(history_html, unsafe_allow_html=True)

else:
    st.markdown(
        '<div style="color:#8b949e;font-family:\'IBM Plex Mono\',monospace;font-size:0.82rem;padding:8px 0;">'
        'No remediation plans — run an audit first.'
        '</div>',
        unsafe_allow_html=True,
    )

# ──────────────────────────────────────────────────────────────────────
# Remediation Explanation Panel (alongside Approval Gate)
# ──────────────────────────────────────────────────────────────────────

if audit_result is not None and audit_result.plans and RemediationExplainer is not None:
    with st.expander("💡 Remediation Explanations", expanded=False):
        # Retry button — clears explanation cache and re-runs LLM calls
        if st.button("🔄 Retry Explanations", key="btn_retry_explanations"):
            st.session_state.explanation_cache = {}
            st.rerun()

        for plan in audit_result.plans:
            rid = plan.resource_id
            cache_key = rid

            if cache_key not in st.session_state.explanation_cache:
                # Find the finding for this resource
                finding = {}
                if audit_result.findings:
                    for f in audit_result.findings:
                        if isinstance(f, dict) and f.get("resource_id") == rid:
                            finding = f
                            break

                remediation_hcl = load_remediation_hcl(rid)
                rollback_hcl = load_rollback_hcl(rid)

                if remediation_hcl.strip() and rollback_hcl.strip():
                    try:
                        explainer = RemediationExplainer()
                        explanation = explainer.explain(rid, finding, remediation_hcl, rollback_hcl)
                        st.session_state.explanation_cache[cache_key] = explanation
                    except Exception:
                        st.session_state.explanation_cache[cache_key] = {
                            "risk_explanation": "Explanation unavailable — click Retry.",
                            "what_terraform_does": "Explanation unavailable — click Retry.",
                            "what_rollback_restores": "Explanation unavailable — click Retry.",
                        }
                else:
                    st.session_state.explanation_cache[cache_key] = {
                        "risk_explanation": "Explanation unavailable (no HCL).",
                        "what_terraform_does": "Explanation unavailable (no HCL).",
                        "what_rollback_restores": "Explanation unavailable (no HCL).",
                    }

            explanation = st.session_state.explanation_cache[cache_key]
            st.markdown(f"**{_esc(rid)}**")
            # Render LLM text safely — no unsafe_allow_html
            st.markdown(f"**Risk:** {_esc(explanation.get('risk_explanation', 'N/A'))}")
            st.markdown(f"**Terraform Fix:** {_esc(explanation.get('what_terraform_does', 'N/A'))}")
            st.markdown(f"**Rollback Restores:** {_esc(explanation.get('what_rollback_restores', 'N/A'))}")
            st.markdown("---")

st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────
# Policy Suggestions Panel (shown post-scan)
# ──────────────────────────────────────────────────────────────────────

if audit_result is not None and PolicySuggester is not None:
    with st.expander("📋 Policy Suggestions", expanded=False):
        # Retry button
        if st.button("🔄 Retry Suggestions", key="btn_retry_suggestions"):
            st.session_state.policy_suggestions = None
            st.rerun()

        if st.session_state.policy_suggestions is None:
            try:
                suggester = PolicySuggester()
                current_findings = load_findings()
                already_checked = list({
                    f.get("check_type", "") for f in current_findings if f.get("check_type")
                })
                suggestions = suggester.suggest(current_findings, already_checked)
                st.session_state.policy_suggestions = suggestions
            except Exception:
                st.session_state.policy_suggestions = []

        suggestions = st.session_state.policy_suggestions or []
        if suggestions:
            for s in suggestions:
                priority = s.get("priority", "low").upper()
                title = _esc(s.get("title", "Untitled"))
                rationale = _esc(s.get("rationale", ""))
                query = _esc(s.get("query", ""))

                # Use badge styling for priority
                badge_class = {"HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW"}.get(priority, "LOW")
                st.markdown(
                    f'<div class="cj-finding">'
                    f'<div><span class="cj-badge {badge_class}">{priority}</span></div>'
                    f'<div class="cj-finding-body">'
                    f'<div class="cj-finding-title">{title}</div>'
                    f'<div class="cj-finding-meta">{rationale}</div>'
                    f'<div class="cj-finding-cost">{query}</div>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown("No policy suggestions available.")

st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────
# Resource Tagger Panel (shown post-scan)
# ──────────────────────────────────────────────────────────────────────

if audit_result is not None and ResourceTagger is not None:
    with st.expander("🏷️ Resource Tags (AI-Inferred)", expanded=False):
        if st.button("🔄 Re-infer Tags", key="btn_retry_tags"):
            st.session_state.resource_tags_cache = {}
            st.rerun()

        if not st.session_state.resource_tags_cache:
            try:
                tagger = ResourceTagger()
                findings = audit_result.findings or []
                resources_to_tag = []
                seen_ids: set[str] = set()
                for f in findings:
                    if not isinstance(f, dict):
                        continue
                    rid = f.get("resource_id", "")
                    if rid and rid not in seen_ids:
                        seen_ids.add(rid)
                        meta = f.get("metadata", {})
                        # Build a meaningful resource_name from available fields.
                        # metadata.name is best; fall back to title or description
                        # which often contains the human-readable name.
                        name = meta.get("name", "")
                        if not name:
                            # Extract name from title like "Idle ElastiCache cluster (cache-prod-legacy-01)"
                            # or description like "Security group sg-prod-redis (sg-xxx) allows..."
                            title = f.get("title", "")
                            desc = f.get("description", "")
                            name = title or desc[:80] or rid
                        resources_to_tag.append({
                            "resource_id": rid,
                            "resource_name": name,
                        })
                if resources_to_tag:
                    results = tagger.infer_batch(resources_to_tag)
                    for r in results:
                        st.session_state.resource_tags_cache[r.get("resource_id", "")] = r
            except Exception:
                pass

        tags_cache = st.session_state.resource_tags_cache
        if tags_cache:
            # Check if all results are safe defaults (LLM likely unavailable)
            all_defaults = all(
                tags.get("confidence", 0.0) == 0.0 and tags.get("env") == "unknown"
                for tags in tags_cache.values()
            )
            if all_defaults:
                st.warning(
                    "All tags returned defaults — LLM is likely unavailable. "
                    "Set `OPENROUTER_API_KEY` in `.env` to enable AI inference."
                )
            for rid, tags in tags_cache.items():
                env = _esc(str(tags.get("env", "unknown")))
                team = _esc(str(tags.get("team", "unknown")))
                owner = _esc(str(tags.get("owner", "unknown")))
                risk = _esc(str(tags.get("risk_level", "unknown")))
                confidence = tags.get("confidence", 0.0)
                conf_pct = f"{confidence * 100:.0f}%"

                st.markdown(
                    f'<div class="cj-finding">'
                    f'<div class="cj-finding-body">'
                    f'<div class="cj-finding-title">{_esc(rid)}</div>'
                    f'<div class="cj-finding-meta">'
                    f'env: <b>{env}</b> · team: <b>{team}</b> · owner: <b>{owner}</b> · '
                    f'risk: <b>{risk}</b> · confidence: {conf_pct}'
                    f'</div>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown("No resources to tag, or inference unavailable.")

st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────
# Incident Policy Generator Panel
# ──────────────────────────────────────────────────────────────────────

if IncidentPolicyGenerator is not None:
    with st.expander("🚨 Incident Policy Generator", expanded=False):
        st.markdown(
            '<div style="color:#8b949e;font-size:0.82rem;margin-bottom:8px;">'
            'Describe a past incident or near-miss to generate preventive scan policies.'
            '</div>',
            unsafe_allow_html=True,
        )
        incident_desc = st.text_area(
            "Incident description",
            placeholder="e.g. Redis cluster was publicly accessible for 3 days before detection...",
            key="incident_description_input",
            label_visibility="collapsed",
        )

        gen_col, list_col = st.columns(2)
        with gen_col:
            if st.button("Generate Policies", key="btn_gen_policies", use_container_width=True):
                if incident_desc and incident_desc.strip():
                    with st.spinner("Generating policies..."):
                        try:
                            gen = IncidentPolicyGenerator()
                            policies = gen.generate(incident_desc.strip())
                            st.session_state.incident_policies = policies
                            if policies:
                                st.success(f"Generated {len(policies)} policy/policies.")
                            else:
                                st.warning("No policies generated. Try a more detailed description.")
                        except Exception:
                            st.session_state.incident_policies = []
                            st.error("Policy generation failed.")
                    st.rerun()
                else:
                    st.warning("Enter an incident description first.")

        with list_col:
            if st.button("Load Saved Policies", key="btn_list_policies", use_container_width=True):
                try:
                    gen = IncidentPolicyGenerator()
                    st.session_state.incident_policies = gen.list_policies()
                except Exception:
                    st.session_state.incident_policies = []
                st.rerun()

        policies: list[dict] | None = st.session_state.incident_policies  # type: ignore[no-redef]
        if policies:
            for p in policies:
                severity = p.get("severity", "medium").upper()
                title = _esc(p.get("title", p.get("policy_id", "Untitled")) or "Untitled")
                description = _esc(p.get("description", ""))
                check_type = _esc(p.get("check_type", ""))
                resource_type = _esc(p.get("resource_type", ""))

                badge_class = {"HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW", "CRITICAL": "HIGH"}.get(severity, "MEDIUM")
                st.markdown(
                    f'<div class="cj-finding">'
                    f'<div><span class="cj-badge {badge_class}">{severity}</span></div>'
                    f'<div class="cj-finding-body">'
                    f'<div class="cj-finding-title">{title}</div>'
                    f'<div class="cj-finding-meta">{description}</div>'
                    f'<div class="cj-finding-cost">{resource_type} · {check_type}</div>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        elif policies is not None:
            st.markdown("No policies found.")

st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────
# Anomaly Detection Results Panel
# ──────────────────────────────────────────────────────────────────────

if st.session_state.anomaly_results is not None:
    with st.expander("🔎 Anomaly Detection Results", expanded=False):
        anomalies = st.session_state.anomaly_results
        if anomalies:
            for anomaly in anomalies:
                severity = anomaly.get("severity", "low").upper()
                anomaly_type = _esc(anomaly.get("anomaly_type", "unknown"))
                description = _esc(anomaly.get("description", ""))
                resource_id = _esc(anomaly.get("resource_id", "unknown"))
                evidence = _esc(anomaly.get("evidence", ""))

                badge_class = {"HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW"}.get(severity, "LOW")
                st.markdown(
                    f'<div class="cj-finding">'
                    f'<div><span class="cj-badge {badge_class}">{severity}</span></div>'
                    f'<div class="cj-finding-body">'
                    f'<div class="cj-finding-title">{anomaly_type}: {description}</div>'
                    f'<div class="cj-finding-meta">{resource_id} · Evidence: {evidence}</div>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown("No anomalies detected.")

st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────
# Drift Report Panel
# ──────────────────────────────────────────────────────────────────────

if st.session_state.drift_report is not None:
    with st.expander("📊 Drift Report", expanded=False):
        drift = st.session_state.drift_report

        if drift.get("drift") is None and "reason" in drift:
            st.markdown(f"*No drift data available:* {_esc(drift.get('reason', 'unknown'))}")
        else:
            # Narrative — render LLM text safely (no unsafe_allow_html)
            narrative = drift.get("narrative", "")
            if narrative:
                st.markdown(f"**Narrative:** {_esc(narrative)}")

            # Delta summary
            waste_delta = drift.get("waste_delta", 0.0)
            critical_delta = drift.get("critical_delta", 0)
            compared = drift.get("compared_scans", [])

            delta_color = "red" if waste_delta > 0 else "green"
            delta_sign = "+" if waste_delta > 0 else ""
            st.markdown(
                f'<div class="cj-metric-row">'
                f'<div class="cj-metric"><div class="cj-metric-label">Waste Delta</div>'
                f'<div class="cj-metric-value {delta_color}">{delta_sign}${waste_delta:.2f}/mo</div></div>'
                f'<div class="cj-metric"><div class="cj-metric-label">Critical Delta</div>'
                f'<div class="cj-metric-value">{"+" if critical_delta > 0 else ""}{critical_delta}</div></div>'
                f'<div class="cj-metric"><div class="cj-metric-label">Scans Compared</div>'
                f'<div class="cj-metric-value">{len(compared)}</div></div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # New and resolved findings
            new_findings = drift.get("new_findings", [])
            resolved_findings = drift.get("resolved_findings", [])

            if new_findings:
                st.markdown(f"**New Findings ({len(new_findings)}):**")
                for nf in new_findings[:5]:
                    st.markdown(f"- {_esc(nf.get('resource_id', 'unknown'))} ({_esc(nf.get('check_type', 'unknown'))})")

            if resolved_findings:
                st.markdown(f"**Resolved Findings ({len(resolved_findings)}):**")
                for rf in resolved_findings[:5]:
                    st.markdown(f"- ~~{_esc(rf.get('resource_id', 'unknown'))}~~ ({_esc(rf.get('check_type', 'unknown'))})")

st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────
# Scheduler Controls
# ──────────────────────────────────────────────────────────────────────

if JanitorScheduler is not None:
    with st.expander("⏰ Scheduler Controls", expanded=False):
        sched_col1, sched_col2, sched_col3 = st.columns(3)

        with sched_col1:
            if st.button("▶ Start Scheduler", key="btn_sched_start", use_container_width=True):
                if st.session_state.scheduler_instance is None:
                    st.session_state.scheduler_instance = JanitorScheduler()
                st.session_state.scheduler_instance.start()
                st.success("Scheduler started.")
                st.rerun()

        with sched_col2:
            if st.button("⏹ Stop Scheduler", key="btn_sched_stop", use_container_width=True):
                if st.session_state.scheduler_instance is not None:
                    st.session_state.scheduler_instance.stop()
                    st.info("Scheduler stopped.")
                    st.rerun()

        with sched_col3:
            if st.button("🔄 Refresh Status", key="btn_sched_status", use_container_width=True):
                st.rerun()

        # Show status
        if st.session_state.scheduler_instance is not None:
            status = st.session_state.scheduler_instance.get_status()
            running_indicator = "🟢 Running" if status.get("running") else "🔴 Stopped"
            st.markdown(f"**Status:** {running_indicator}")
            st.markdown(f"**Schedule:** `{_esc(status.get('schedule', 'N/A'))}`")
            st.markdown(f"**Next Run:** {_esc(str(status.get('next_run', 'N/A')))}")
            st.markdown(f"**Last Run:** {_esc(str(status.get('last_run', 'N/A')))}")
            st.markdown(f"**Runs Completed:** {status.get('runs_completed', 0)}")
        else:
            st.markdown("Scheduler not initialized. Click **Start Scheduler** to begin.")

st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────
# Multi-Account View
# ──────────────────────────────────────────────────────────────────────

if MultiAccountOrchestrator is not None:
    with st.expander("🌐 Multi-Account View", expanded=False):
        if st.button("Run Multi-Account Audit", key="btn_multi_account", use_container_width=True):
            _accounts_file = PROJECT_ROOT / "accounts.json"
            if not _accounts_file.exists():
                _example_file = PROJECT_ROOT / "accounts.example.json"
                if _example_file.exists():
                    st.error(
                        "**accounts.json not found.** "
                        "Copy `accounts.example.json` to `accounts.json` and fill in your account details."
                    )
                else:
                    st.error(
                        "**accounts.json not found.** "
                        "Create an `accounts.json` file in the project root with your AWS account configurations. "
                        "See the README for the expected format."
                    )
            else:
                with st.spinner("Running audits across accounts..."):
                    try:
                        multi_orch = MultiAccountOrchestrator()
                        ma_results = multi_orch.run_all()
                        st.session_state.multi_account_results = ma_results
                        st.success(f"Multi-account audit complete — {ma_results.get('accounts_scanned', 0)} account(s) scanned.")
                        st.rerun()
                    except Exception as e:
                        render_structured_error(f"Multi-account audit failed: {e}")

        if st.session_state.multi_account_results is not None:
            ma = st.session_state.multi_account_results

            # Summary metrics
            st.markdown(
                f'<div class="cj-metric-row">'
                f'<div class="cj-metric"><div class="cj-metric-label">Accounts Scanned</div>'
                f'<div class="cj-metric-value">{ma.get("accounts_scanned", 0)}</div></div>'
                f'<div class="cj-metric"><div class="cj-metric-label">Total Findings</div>'
                f'<div class="cj-metric-value">{ma.get("total_findings", 0)}</div></div>'
                f'<div class="cj-metric"><div class="cj-metric-label">Total Waste</div>'
                f'<div class="cj-metric-value yellow">${ma.get("total_waste", 0.0):.2f}/mo</div></div>'
                f'<div class="cj-metric"><div class="cj-metric-label">Cross-Account Duplicates</div>'
                f'<div class="cj-metric-value">{ma.get("cross_account_duplicates", 0)}</div></div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # Per-account breakdown
            by_account = ma.get("by_account", [])
            if by_account:
                st.markdown("**Per-Account Breakdown:**")
                for acct in by_account:
                    acct_name = _esc(acct.get("account_name", "unknown"))
                    acct_id = _esc(acct.get("account_id", ""))
                    priority = acct.get("priority", "low").upper()
                    status = acct.get("status", "unknown")
                    findings_list = acct.get("findings", [])
                    findings_count = len(findings_list)
                    waste = acct.get("waste", 0.0)
                    error = acct.get("error")

                    status_icon = "✓" if status == "success" else "✗"

                    badge_class = {"HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW"}.get(priority, "LOW")

                    entry_html = (
                        f'<div class="cj-finding">'
                        f'<div><span class="cj-badge {badge_class}">{priority}</span></div>'
                        f'<div class="cj-finding-body">'
                        f'<div class="cj-finding-title">{status_icon} {acct_name} ({acct_id})</div>'
                        f'<div class="cj-finding-meta">{findings_count} findings · ${waste:.2f}/mo waste · {_esc(status)}</div>'
                    )
                    if error:
                        entry_html += f'<div class="cj-finding-meta" style="color:#f85149;">Error: {_esc(error)}</div>'
                    entry_html += '</div></div>'
                    st.markdown(entry_html, unsafe_allow_html=True)

                    # Show per-account findings detail
                    if findings_list:
                        with st.expander(f"  📄 {acct_name} — {findings_count} finding(s)", expanded=False):
                            for f in findings_list:
                                if not isinstance(f, dict):
                                    continue
                                f_severity = str(f.get("severity", "unknown")).upper()
                                f_title = _esc(f.get("title", f.get("resource_id", "unknown")) or "unknown")
                                f_resource = _esc(f.get("resource_id", ""))
                                f_type = _esc(f.get("resource_type", ""))
                                f_category = _esc(f.get("category", ""))
                                f_cost = f.get("cost_estimate_monthly", 0.0)
                                f_desc = _esc(f.get("description", ""))

                                f_badge = {"CRITICAL": "HIGH", "HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW"}.get(f_severity, "LOW")
                                cost_str = f" · ${f_cost:.2f}/mo" if f_cost else ""

                                st.markdown(
                                    f'<div class="cj-finding" style="margin-left:12px;">'
                                    f'<div><span class="cj-badge {f_badge}">{f_severity}</span></div>'
                                    f'<div class="cj-finding-body">'
                                    f'<div class="cj-finding-title">{f_title}</div>'
                                    f'<div class="cj-finding-meta">{f_resource} · {f_type} · {f_category}{cost_str}</div>'
                                    f'<div class="cj-finding-meta" style="color:#8b949e;">{f_desc}</div>'
                                    f'</div>'
                                    f'</div>',
                                    unsafe_allow_html=True,
                                )

            # Aggregate cross-account findings
            aggregate = ma.get("aggregate_findings", [])
            if aggregate:
                with st.expander(f"📊 All Findings Across Accounts ({len(aggregate)})", expanded=False):
                    # Group by severity
                    by_sev: dict[str, list] = {}
                    for f in aggregate:
                        if not isinstance(f, dict):
                            continue
                        sev = str(f.get("severity", "unknown")).upper()
                        by_sev.setdefault(sev, []).append(f)

                    for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
                        items = by_sev.get(sev, [])
                        if not items:
                            continue
                        st.markdown(f"**{sev}** ({len(items)})")
                        for f in items:
                            f_title = _esc(f.get("title", f.get("resource_id", "unknown")) or "unknown")
                            f_acct = _esc(f.get("account_id", ""))
                            f_resource = _esc(f.get("resource_id", ""))
                            st.markdown(
                                f'<div class="cj-finding" style="margin-left:12px;">'
                                f'<div><span class="cj-badge {sev}">{sev}</span></div>'
                                f'<div class="cj-finding-body">'
                                f'<div class="cj-finding-title">{f_title}</div>'
                                f'<div class="cj-finding-meta">{f_resource} · account: {f_acct}</div>'
                                f'</div>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )


st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────
# Audit Trail Query (phase3f-audit-query)
# Tamper-evidence (hash-chaining) is an explicit non-goal for this spec.
# ──────────────────────────────────────────────────────────────────────

with st.expander("🔍 Audit Trail Query", expanded=False):
    st.markdown(
        '<div style="color:#8b949e;font-size:0.82rem;margin-bottom:8px;">'
        'Filter and export audit trail entries. Requires phase2-persistent-state.'
        '</div>',
        unsafe_allow_html=True,
    )

    # Filter widgets
    _aq_col1, _aq_col2 = st.columns(2)
    with _aq_col1:
        _aq_resource_id = st.text_input(
            "Resource ID",
            key="aq_resource_id",
            placeholder="e.g. vol-0abc123",
        )
        _aq_actor = st.text_input(
            "Actor",
            key="aq_actor",
            placeholder="e.g. arn:aws:iam::...",
        )
    with _aq_col2:
        _aq_result = st.selectbox(
            "Result",
            options=["", "success", "failure", "blocked"],
            key="aq_result",
            format_func=lambda x: "All" if x == "" else x,
        )
        _aq_action = st.selectbox(
            "Action",
            options=["", "approval", "rollback", "scan", "remediation"],
            key="aq_action",
            format_func=lambda x: "All" if x == "" else x,
        )

    # Date range
    _aq_date_col1, _aq_date_col2 = st.columns(2)
    with _aq_date_col1:
        _aq_date_from = st.date_input("Date From", value=None, key="aq_date_from")
    with _aq_date_col2:
        _aq_date_to = st.date_input("Date To", value=None, key="aq_date_to")

    # Tri-state run scope control
    _aq_run_scope = st.selectbox(
        "Run Scope",
        options=["All", "Scoped to a run", "Unscoped only"],
        key="aq_run_scope",
    )
    _aq_run_id_value: str | None | object = None
    if _aq_run_scope == "Scoped to a run":
        _aq_run_id_input = st.text_input(
            "Run ID",
            key="aq_run_id_input",
            placeholder="e.g. run-2025-07-10-abc123",
        )
        _aq_run_id_value = _aq_run_id_input if _aq_run_id_input.strip() else None
    elif _aq_run_scope == "Unscoped only":
        _aq_run_id_value = UNSCOPED

    # Query button
    if st.button("🔎 Query Audit Trail", key="btn_query_audit", use_container_width=True):
        try:
            _aq_state_store = st.session_state.orchestrator._state_store
            _aq_rows = query_audit(
                _aq_state_store,
                resource_id=_aq_resource_id.strip() or None,
                actor=_aq_actor.strip() or None,
                result=_aq_result or None,
                action=_aq_action or None,
                run_id=_aq_run_id_value,
                date_from=str(_aq_date_from) if _aq_date_from else None,
                date_to=str(_aq_date_to) if _aq_date_to else None,
            )
            st.session_state["aq_results"] = _aq_rows
        except (AttributeError, sqlite3.OperationalError):
            st.warning(
                "Audit trail querying requires phase2-persistent-state. "
                "The StateStore is not yet available."
            )
            st.session_state["aq_results"] = None
        except Exception:
            st.warning(
                "Audit trail querying requires phase2-persistent-state. "
                "The StateStore is not yet available."
            )
            st.session_state["aq_results"] = None

    # Render results
    _aq_results = st.session_state.get("aq_results")
    if _aq_results is not None:
        if _aq_results:
            import pandas as pd

            _aq_df = pd.DataFrame(_aq_results)
            st.dataframe(_aq_df, use_container_width=True)

            # Download buttons
            _aq_dl_col1, _aq_dl_col2 = st.columns(2)
            with _aq_dl_col1:
                _aq_csv_data = export_audit_csv(_aq_results)
                st.download_button(
                    "📥 Download CSV",
                    data=_aq_csv_data,
                    file_name="audit_trail.csv",
                    mime="text/csv",
                    key="btn_download_audit_csv",
                    use_container_width=True,
                )
            with _aq_dl_col2:
                _aq_json_data = export_audit_json(_aq_results)
                st.download_button(
                    "📥 Download JSON",
                    data=_aq_json_data,
                    file_name="audit_trail.json",
                    mime="application/json",
                    key="btn_download_audit_json",
                    use_container_width=True,
                )
        else:
            st.info("No audit trail entries match the current filters.")
