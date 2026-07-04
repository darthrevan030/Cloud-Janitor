"""
Agent Orchestrator

Orchestrates the multi-agent pipeline: FinOps Auditor → SecOps Guard → Remediation Architect.
Wires shell hooks (pre-remediation, post-remediation), integrates the approval gate,
and manages the audit trail.

Usage:
    from orchestrator import Orchestrator

    orch = Orchestrator()
    result = orch.execute_audit()
    approval = orch.approve("APPROVE vol-abc123")
    rollback = orch.rollback("ROLLBACK vol-abc123")
    trail = orch.get_audit_trail()
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def _find_bash() -> str:
    """Locate a working bash binary.

    Resolution order:
      1. BASH_PATH environment variable (explicit override)
      2. Git Bash on Windows (C:/Program Files/Git/bin/bash.exe)
      3. 'bash' on PATH (works on Linux/macOS; on Windows may hit WSL wrapper)

    Returns the path string to use in subprocess calls.
    """
    # Allow explicit override via env var
    env_bash = os.environ.get("BASH_PATH")
    if env_bash and shutil.which(env_bash):
        return env_bash

    # On Windows, prefer Git Bash over WSL wrapper
    if platform.system() == "Windows":
        git_bash = r"C:\Program Files\Git\bin\bash.exe"
        if os.path.isfile(git_bash):
            return git_bash

    # Fall back to whatever 'bash' resolves to on PATH
    return "bash"


def _to_bash_path(p: Path) -> str:
    """Convert a Path to a bash-compatible string.

    On Windows, converts 'D:/foo/bar' to '/d/foo/bar' so Git Bash can resolve it.
    On other platforms, returns the POSIX string unchanged.
    """
    posix = p.as_posix()
    if platform.system() == "Windows" and len(posix) >= 2 and posix[1] == ":":
        # D:/foo/bar → /d/foo/bar
        drive_letter = posix[0].lower()
        return f"/{drive_letter}{posix[2:]}"
    return posix

from cloud_janitor.agents.approval_gate import (  # noqa: E402
    ApprovalGate,
    ApprovalGateStore,
    parse_confirm_rollback,
    parse_rollback,
)
from cloud_janitor.agents.anomaly_detector import AnomalyDetector  # noqa: E402
from cloud_janitor.agents.audit_logger import AuditLogger  # noqa: E402
from cloud_janitor.agents.drift_detector import DriftDetector  # noqa: E402
from cloud_janitor.agents.finops_auditor import FinOpsAuditor  # noqa: E402
from cloud_janitor.agents.query_interpreter import QueryInterpreter  # noqa: E402
from cloud_janitor.agents.reasoning_logger import ReasoningLogger  # noqa: E402
from cloud_janitor.agents.remediation_architect import RemediationArchitect, RemediationPlan  # noqa: E402
from cloud_janitor.agents.secops_guard import SecOpsGuard  # noqa: E402
from cloud_janitor.mcp_server.aws_janitor_mcp import get_cost_data, get_security_data  # noqa: E402
from cloud_janitor.agents.savings_tracker import SavingsTracker  # noqa: E402
from cloud_janitor.core.paths import (  # noqa: E402
    PROJECT_ROOT as _CORE_PROJECT_ROOT,
    OUTPUT_DIR as _CORE_OUTPUT_DIR,
    ROLLBACKS_DIR as _CORE_ROLLBACKS_DIR,
    FINDINGS_STORE_PATH as _CORE_FINDINGS_STORE_PATH,
    AUDIT_LOG_PATH as _CORE_AUDIT_LOG_PATH,
    REASONING_LOG_PATH as _CORE_REASONING_LOG_PATH,
    APPROVAL_GATES_PATH as _CORE_APPROVAL_GATES_PATH,
    SAVINGS_LEDGER_PATH as _CORE_SAVINGS_LEDGER_PATH,
    HOOKS_DIR as _CORE_HOOKS_DIR,
    ensure_output_dirs,
)
from cloud_janitor.core.error_telemetry import build_error_record, write_error_record  # noqa: E402


TF_CMD = os.environ.get("TF_CMD", "tflocal")

TF_CMD_ALLOWLIST = {"terraform", "tflocal"}

BASH_CMD = _find_bash()

SCHEMA_VERSION = "1.0.0"

_RESOURCE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9\-_:./]{1,256}\Z")

# ── Subprocess environment isolation (L2 fix) ─────────────────────────
# Never pass the full parent environment to subprocesses. Build a minimal
# env containing only what each child legitimately needs.

# Regex patterns for sensitive data redaction (L1 fix)
_REDACT_PATTERNS = [
    re.compile(r"\b\d{12}\b"),                         # AWS account IDs
    re.compile(r"arn:aws:[^\s\"']+"),                   # ARNs
    re.compile(r"AKIA[0-9A-Z]{16}"),                   # AWS access key IDs
    re.compile(r"ASIA[0-9A-Z]{16}"),                   # AWS temporary key IDs
    re.compile(r"sk-or-[A-Za-z0-9\-]+"),               # OpenRouter keys
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                # Generic API keys
    re.compile(r"vpc-[0-9a-f]+"),                       # VPC IDs
    re.compile(r"subnet-[0-9a-f]+"),                    # Subnet IDs
]


def _redact(text: str) -> str:
    """Redact sensitive patterns and strip ANSI escape codes from subprocess output.

    Replaces AWS account IDs, ARNs, access keys, VPC/subnet IDs, and
    API keys with [REDACTED] placeholders. Also removes terminal color codes
    so error messages are readable in logs and UI.
    """
    # Strip ANSI escape sequences (color codes, cursor movement, etc.)
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
    text = re.sub(r"\x1b\[?[0-9;]*[a-zA-Z]", "", text)
    # Strip other common unicode box-drawing artifacts from terraform output
    text = re.sub(r"[╷╵│╶]", "", text)

    for pattern in _REDACT_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text.strip()


def _build_subprocess_env(kind: str) -> dict[str, str]:
    """Build a minimal, safe environment for a child process.

    Args:
        kind: One of "terraform" or "hook". Controls which env vars are passed.

    Returns:
        A dict suitable for subprocess.run(env=...).
    """
    # Start with minimal PATH (cleaned of any sensitive directories)
    env: dict[str, str] = {}

    # PATH is always needed
    if "PATH" in os.environ:
        env["PATH"] = os.environ["PATH"]

    # Windows-specific system vars needed for process execution
    for var in ("SYSTEMROOT", "TEMP", "TMP", "COMSPEC", "HOMEDRIVE", "HOMEPATH"):
        if var in os.environ:
            env[var] = os.environ[var]

    if kind == "terraform":
        # Terraform needs AWS credentials and region, plus endpoint override
        for var in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_DEFAULT_REGION",
            "AWS_REGION",
            "AWS_ENDPOINT_URL",
            "LOCALSTACK_AUTH_TOKEN",
            "TF_LOG",
            "TF_DATA_DIR",
        ):
            if var in os.environ:
                env[var] = os.environ[var]
    elif kind == "hook":
        # Pre/post-remediation hooks run terraform validation — they need
        # AWS credentials and endpoint for tflocal, but never LLM API keys.
        for var in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_DEFAULT_REGION",
            "AWS_REGION",
            "AWS_ENDPOINT_URL",
            "LOCALSTACK_AUTH_TOKEN",
            "TF_CMD",
            "JANITOR_DRY_RUN",
        ):
            if var in os.environ:
                env[var] = os.environ[var]

    # Explicitly: OPENROUTER_API_KEY, JANITOR_LLM_API_KEY never passed to children
    return env

# Schema version for findings_store.json — bump when the schema changes.
# The orchestrator will warn (not fail) if the store has a newer version than expected.
FINDINGS_STORE_SCHEMA_VERSION = "1.0.0"


def _validate_tf_cmd() -> str:
    """Validate and resolve TF_CMD from environment.

    Returns:
        Absolute path to the validated binary.

    Raises:
        RuntimeError: If TF_CMD fails any validation check.
    """
    raw = os.environ.get("TF_CMD", "tflocal")

    # Reject path separators
    if "/" in raw or "\\" in raw:
        raise RuntimeError(
            f"TF_CMD contains path separators: '{raw}'. "
            f"Only bare binary names are permitted: {sorted(TF_CMD_ALLOWLIST)}"
        )

    # Extract basename (redundant given separator check, but defense-in-depth)
    basename = os.path.basename(raw)

    # Validate against allowlist
    if basename not in TF_CMD_ALLOWLIST:
        raise RuntimeError(
            f"TF_CMD '{basename}' is not in the allowlist. "
            f"Permitted values: {sorted(TF_CMD_ALLOWLIST)}"
        )

    # Resolve to absolute path via PATH lookup
    resolved = shutil.which(basename)
    if resolved is None:
        raise RuntimeError(
            f"TF_CMD '{basename}' could not be found on PATH."
        )

    return resolved


PROJECT_ROOT = _CORE_PROJECT_ROOT
FINDINGS_STORE_PATH = _CORE_FINDINGS_STORE_PATH
HOOKS_DIR = _CORE_HOOKS_DIR
OUTPUT_DIR = _CORE_OUTPUT_DIR
ROLLBACKS_DIR = _CORE_ROLLBACKS_DIR
AUDIT_LOG_PATH = _CORE_AUDIT_LOG_PATH


@dataclass
class AuditEntry:
    """A single entry in the audit trail."""

    timestamp: str
    action: str
    resource_id: str
    actor: str
    result: str
    details: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "action": self.action,
            "resource_id": self.resource_id,
            "actor": self.actor,
            "result": self.result,
            "details": self.details,
        }


@dataclass
class AuditResult:
    """Result of execute_audit()."""

    success: bool
    findings: list[dict] = field(default_factory=list)
    plans: list[RemediationPlan] = field(default_factory=list)
    blocked_plans: list[RemediationPlan] = field(default_factory=list)
    hook_error: str | None = None
    error: str | None = None
    error_category: str | None = None
    error_agent: str | None = None
    anomalies: list[dict] = field(default_factory=list)
    drift_report: dict | None = None


@dataclass
class ApprovalResult:
    """Result of approve()."""

    success: bool
    resource_id: str = ""
    error: str | None = None
    error_category: str | None = None
    error_agent: str | None = None
    locked: bool = False
    expected_format: str | None = None
    attempts_remaining: int | None = None


@dataclass
class RollbackResult:
    """Result of rollback()."""

    success: bool
    resource_id: str = ""
    error: str | None = None
    error_category: str | None = None
    error_agent: str | None = None
    needs_confirmation: bool = False
    exit_code: int | None = None


class Orchestrator:
    """
    Orchestrates the Cloud Janitor agent pipeline.

    Sequence:
      1. FinOps Auditor scans → writes findings_store.json
      2. SecOps Guard scans → appends to findings_store.json
      3. Validate findings_store has entries from both agents
      4. Remediation Architect plans → generates HCL
      5. Pre-remediation hook validates HCL
      6. Approval gate for user confirmation
      7. Post-remediation hook logs audit entry
    """

    def __init__(
        self,
        project_root: Path | None = None,
        approver: str = "system",
    ):
        self.project_root = project_root or PROJECT_ROOT

        # Determine whether to use centralized path constants or custom-root paths
        using_default_root = (project_root is None)

        if using_default_root:
            # Use centralized path constants from core/paths.py (Req 4.4)
            # Ensure output directories exist — halt on failure (Req 4.3, 4.5)
            try:
                ensure_output_dirs()
            except RuntimeError as e:
                raise RuntimeError(
                    f"Orchestrator initialization failed: {e}"
                ) from e

            self.findings_store_path = FINDINGS_STORE_PATH
            self.hooks_dir = HOOKS_DIR
            self.output_dir = OUTPUT_DIR
            self.rollbacks_dir = ROLLBACKS_DIR
            self.audit_log_path = AUDIT_LOG_PATH
        else:
            # Custom project root (tests) — construct paths relative to it
            self.output_dir = self.project_root / "output"
            self.rollbacks_dir = self.output_dir / "rollbacks"
            self.findings_store_path = self.output_dir / "findings_store.json"
            self.hooks_dir = self.project_root / "hooks"
            self.audit_log_path = self.output_dir / "logs" / "audit.log"
            # Create required directories for custom root
            try:
                for d in [self.output_dir, self.rollbacks_dir,
                          self.output_dir / "logs", self.output_dir / "policies"]:
                    os.makedirs(d, exist_ok=True)
            except OSError as e:
                raise RuntimeError(
                    f"Orchestrator initialization failed: could not create directory: {e}"
                ) from e

        self.approver = approver

        # Validate and resolve TF_CMD binary lazily (only when actually needed)
        self._tf_cmd: str | None = None

        # Reasoning logger (shared across all agents)
        reasoning_log_path = (
            _CORE_REASONING_LOG_PATH if using_default_root
            else self.output_dir / "logs" / "agent_reasoning.log"
        )
        self._reasoning_logger = ReasoningLogger(
            log_path=reasoning_log_path
        )

        # Agent instances
        self._finops = FinOpsAuditor(
            findings_store_path=self.findings_store_path,
            reasoning_logger=self._reasoning_logger,
        )
        self._secops = SecOpsGuard(
            findings_store_path=self.findings_store_path,
            reasoning_logger=self._reasoning_logger,
        )
        self._architect = RemediationArchitect(
            findings_store_path=self.findings_store_path,
            output_dir=self.output_dir,
            rollbacks_dir=self.rollbacks_dir,
            reasoning_logger=self._reasoning_logger,
        )

        # Audit logger (append-only, file-based)
        self._audit_logger = AuditLogger(self.audit_log_path)

        # AI agents
        self._query_interpreter = QueryInterpreter()
        self._anomaly_detector = AnomalyDetector()
        self._drift_detector = DriftDetector(
            history_path=(
                OUTPUT_DIR / "scan_history.json" if using_default_root
                else self.output_dir / "scan_history.json"
            )
        )

        # Savings tracker
        self._savings_tracker = SavingsTracker(
            ledger_path=(
                _CORE_SAVINGS_LEDGER_PATH if using_default_root
                else self.output_dir / "savings_ledger.json"
            ),
            findings_store_path=self.findings_store_path,
        )

        # Persistent approval gate store
        gate_store_path = (
            _CORE_APPROVAL_GATES_PATH if using_default_root
            else self.output_dir / "approval_gates.json"
        )
        self._gate_store = ApprovalGateStore(gate_store_path)
        self._gate_store.load()

        # Approval gates per resource (keyed by resource_id)
        self._approval_gates: dict[str, ApprovalGate] = {}

        # Internal audit trail
        self._audit_trail: list[AuditEntry] = []

        # Track last plans for approval flow
        self._last_plans: list[RemediationPlan] = []

        # Track rollback state (resource_id → awaiting confirmation)
        self._pending_rollbacks: set[str] = set()

    # ──────────────────────────────────────────────────────────────────────
    # Properties
    # ──────────────────────────────────────────────────────────────────────

    @property
    def tf_cmd(self) -> str:
        """Lazily validate and resolve TF_CMD binary.

        Only called when terraform operations are actually needed (approve/rollback),
        not during scan-only usage.
        """
        if self._tf_cmd is None:
            self._tf_cmd = _validate_tf_cmd()
        return self._tf_cmd

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def execute_audit(
        self,
        status_callback: Callable[[str, str], None] | None = None,
    ) -> AuditResult:
        """
        Execute the full audit pipeline: FinOps → SecOps → Remediation Architect.

        Args:
            status_callback: Optional callable (agent_name, status) invoked at each
                pipeline stage. Status values: "idle", "running", "success", "failure".

        Returns:
            AuditResult with findings, plans, and any errors.
        """

        def _emit(agent: str, status: str) -> None:
            if status_callback is not None:
                status_callback(agent, status)

        # Truncate reasoning log at the start of each new audit run
        self._reasoning_logger.truncate()

        # Step 1: FinOps Auditor scan
        logger.info("[Orchestrator] Step 1: Running FinOps Auditor scan...")
        _emit("finops", "running")
        self._log_action("scan", "all", "started", "FinOps Auditor scan initiated")
        try:
            finops_findings = self._finops.scan()
        except Exception as e:
            _emit("finops", "failure")
            self._record_error(e, "FinOpsAuditor", context="")
            return AuditResult(success=False, error=f"FinOps Auditor failed: {e}", error_category="agent_failure", error_agent="FinOpsAuditor")
        logger.info("[Orchestrator] Step 1 complete: FinOps found %d finding(s)", len(finops_findings))
        self._log_action("scan", "all", "success", f"FinOps found {len(finops_findings)} finding(s)")
        _emit("finops", "success")

        # Step 2: SecOps Guard scan
        logger.info("[Orchestrator] Step 2: Running SecOps Guard scan...")
        _emit("secops", "running")
        self._log_action("scan", "all", "started", "SecOps Guard scan initiated")
        try:
            secops_findings = self._secops.scan()
        except Exception as e:
            _emit("secops", "failure")
            self._record_error(e, "SecOpsGuard", context="")
            return AuditResult(
                success=False,
                findings=finops_findings,
                error=f"SecOps Guard failed: {e}",
                error_category="agent_failure",
                error_agent="SecOpsGuard",
            )
        logger.info("[Orchestrator] Step 2 complete: SecOps found %d finding(s)", len(secops_findings))
        self._log_action("scan", "all", "success", f"SecOps found {len(secops_findings)} finding(s)")
        _emit("secops", "success")

        # Step 3: Validate findings_store has entries from both agents
        logger.info("[Orchestrator] Step 3: Validating findings store...")
        _emit("remediation", "running")
        validation_error = self._validate_findings_store()
        if validation_error:
            logger.warning("[Orchestrator] Step 3 failed: %s", validation_error)
            self._log_action("plan", "all", "failure", validation_error)
            _emit("remediation", "failure")
            val_exc = RuntimeError(validation_error)
            self._record_error(val_exc, "Orchestrator", context="schema_check")
            return AuditResult(success=False, error=validation_error, error_category="validation_failure", error_agent="Orchestrator")
        logger.info("[Orchestrator] Step 3 complete: Findings store valid")

        # Step 4: Remediation Architect plans
        logger.info("[Orchestrator] Step 4: Running Remediation Architect (dependency checks + HCL generation)...")
        self._log_action("plan", "all", "started", "Remediation Architect planning")
        try:
            plans = self._architect.plan()
        except Exception as e:
            _emit("remediation", "failure")
            self._record_error(e, "RemediationArchitect", context="")
            return AuditResult(
                success=False,
                findings=finops_findings + secops_findings,
                error=f"Remediation Architect failed: {e}",
                error_category="agent_failure",
                error_agent="RemediationArchitect",
            )
        self._last_plans = plans

        blocked_plans = [p for p in plans if p.blocked]
        active_plans = [p for p in plans if not p.blocked]

        # Log blocked plans
        for p in blocked_plans:
            self._log_action("plan", p.resource_id, "blocked", p.block_reason)

        logger.info(
            "[Orchestrator] Step 4 complete: %d plan(s) generated, %d blocked",
            len(active_plans), len(blocked_plans),
        )
        self._log_action(
            "plan", "all", "success",
            f"Generated {len(active_plans)} plan(s), {len(blocked_plans)} blocked"
        )

        # Step 5: Run pre-remediation hook on active plans
        if active_plans:
            logger.info("[Orchestrator] Step 5: Running pre-remediation hook (terraform validate)...")
            hook_error = self._run_pre_remediation_hook(active_plans)
            if hook_error:
                logger.warning("[Orchestrator] Step 5 failed: %s", hook_error[:100])
                self._log_action("plan", "all", "blocked", f"Pre-remediation hook failed: {hook_error}")
                _emit("remediation", "failure")
                hook_exc = RuntimeError(hook_error)
                self._record_error(hook_exc, "Orchestrator", context="hook_validation")
                return AuditResult(
                    success=False,
                    findings=finops_findings + secops_findings,
                    plans=active_plans,
                    blocked_plans=blocked_plans,
                    hook_error=hook_error,
                    error_category="validation_failure",
                    error_agent="Orchestrator",
                )
            logger.info("[Orchestrator] Step 5 complete: Hook validation passed")

        _emit("remediation", "success")
        all_findings = finops_findings + secops_findings

        # Step 6: Anomaly Detection (post-scan, before drift) — Req 6.4
        logger.info("[Orchestrator] Step 6: Running anomaly detection...")
        # Use findings as the resource pool for anomaly detection to avoid
        # redundant API/fixture calls (agents already fetched the data).
        anomalies = self._run_anomaly_detection(all_findings, all_findings)
        logger.info("[Orchestrator] Step 6 complete: %d anomalies detected", len(anomalies))

        # Step 7: Drift Detection — save snapshot then detect
        logger.info("[Orchestrator] Step 7: Running drift detection...")
        total_waste = sum(
            f.get("cost_estimate_monthly", 0.0) for f in all_findings
        )
        scan_id = str(uuid.uuid4())
        self._drift_detector.save_snapshot(scan_id, all_findings, anomalies, total_waste)
        drift_report = self._drift_detector.detect(all_findings)
        logger.info("[Orchestrator] Step 7 complete: Drift report generated")

        logger.info("[Orchestrator] ✅ Audit complete: %d findings, %d plans", len(all_findings), len(active_plans))
        return AuditResult(
            success=True,
            findings=all_findings,
            plans=active_plans,
            blocked_plans=blocked_plans,
            anomalies=anomalies,
            drift_report=drift_report,
        )

    def execute_natural_language_audit(self, query: str) -> AuditResult:
        """
        Execute an audit filtered by a natural language query.

        Uses QueryInterpreter to parse the query into structured parameters.
        On interpreter failure (confidence=0.0): falls back to full unfiltered scan.

        Args:
            query: Free-text query describing what to audit.

        Returns:
            AuditResult with findings, anomalies, and drift info.
        """
        # Step 1: Interpret the query
        try:
            params = self._query_interpreter.interpret(query)
        except Exception as exc:
            print(
                f"[Orchestrator] QueryInterpreter error: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            self._record_error(exc, "QueryInterpreter", context="")
            # Req 1.10: fall back to full scan on failure
            return self.execute_audit()

        # Step 2: Check confidence — fall back to full scan if too low
        if params.get("confidence", 0.0) == 0.0:
            self._log_action(
                "nl_audit", "all", "fallback",
                "QueryInterpreter returned low confidence, falling back to full scan",
            )
            return self.execute_audit()

        # Step 3: Use interpreted parameters to gather filtered data
        resource_types = params.get("resource_types", [])
        check_types = params.get("check_types", [])
        min_idle_days = params.get("min_idle_days", 7)

        self._log_action(
            "nl_audit", "all", "started",
            f"NL audit: {params.get('intent_summary', 'Query interpreted')}",
        )

        # Gather cost data (filtered by resource types)
        all_resources: list[dict] = []
        cost_findings: list[dict] = []
        try:
            if resource_types:
                for rt in resource_types:
                    cost_data = get_cost_data(resource_type=rt, min_idle_days=min_idle_days)
                    all_resources.extend(cost_data.get("resources", []))
            else:
                cost_data = get_cost_data(min_idle_days=min_idle_days)
                all_resources.extend(cost_data.get("resources", []))
            # Transform raw resources into finding-like dicts for downstream compatibility
            for r in all_resources:
                cost_findings.append({
                    "id": r.get("id", ""),
                    "resource_id": r.get("id", ""),
                    "resource_type": r.get("type", "unknown"),
                    "agent": "finops",
                    "category": "waste",
                    "severity": "MEDIUM" if r.get("type") == "ebs" else "HIGH" if r.get("type") == "elasticache" else "LOW",
                    "title": r.get("description", f"Idle {r.get('type', 'resource')}"),
                    "description": r.get("description", ""),
                    "cost_estimate_monthly": r.get("monthly_cost", 0.0),
                    "idle_days": r.get("idle_days", 0),
                    "metadata": {k: v for k, v in r.items() if k not in ("id", "type", "idle_days", "monthly_cost", "description")},
                    "detected_at": r.get("created_at", ""),
                })
        except Exception as exc:
            print(
                f"[Orchestrator] get_cost_data error: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            # Req 1.10: safe default — empty list

        # Gather security data (filtered by check types)
        security_findings: list[dict] = []
        try:
            if check_types:
                for ct in check_types:
                    sec_data = get_security_data(check_type=ct)
                    security_findings.extend(sec_data.get("findings", []))
            else:
                sec_data = get_security_data()
                security_findings.extend(sec_data.get("findings", []))
        except Exception as exc:
            print(
                f"[Orchestrator] get_security_data error: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            # Req 1.10: safe default — empty list

        all_findings = cost_findings + security_findings

        # Step 4: Run Remediation Architect on gathered findings
        plans: list[RemediationPlan] = []
        blocked_plans: list[RemediationPlan] = []
        try:
            if all_findings:
                plans = self._architect.plan()
                blocked_plans = [p for p in plans if p.blocked]
                plans = [p for p in plans if not p.blocked]
        except Exception as exc:
            print(
                f"[Orchestrator] Architect error during NL audit: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            # Req 1.10: safe default — empty plans

        # Step 5: Anomaly Detection (post-scan, before drift) — Req 6.4
        anomalies = self._run_anomaly_detection(all_resources, all_findings)

        # Step 6: Drift Detection
        total_waste = sum(
            f.get("cost_estimate_monthly", 0.0) for f in all_findings
        )
        scan_id = str(uuid.uuid4())
        self._drift_detector.save_snapshot(scan_id, all_findings, anomalies, total_waste)
        drift_report = self._drift_detector.detect(all_findings)

        self._log_action(
            "nl_audit", "all", "success",
            f"NL audit completed: {len(all_findings)} findings, {len(anomalies)} anomalies",
        )

        return AuditResult(
            success=True,
            findings=all_findings,
            plans=plans,
            blocked_plans=blocked_plans,
            anomalies=anomalies,
            drift_report=drift_report,
        )

    def approve(self, command: str, resource_id: str | None = None) -> ApprovalResult:
        """
        Process an approval command: "APPROVE <resource-id>".

        The resource_id parameter allows callers (e.g. the UI) to specify which
        resource this approval attempt targets. This ensures all attempts — even
        malformed ones — count against the gate for that resource.

        If resource_id is not provided, it is extracted from the command string.
        Commands that don't start with "APPROVE " and have no explicit resource_id
        are rejected immediately.

        Args:
            command: The approval command string.
            resource_id: Optional explicit resource ID this attempt targets.

        Returns:
            ApprovalResult indicating success or failure.
        """
        # Determine target resource_id
        if resource_id is None:
            resource_id = self._extract_resource_id_from_command(command, "APPROVE")

        if not resource_id:
            return ApprovalResult(
                success=False,
                error="Invalid command format",
                expected_format="APPROVE <resource-id>",
            )

        # Verify the resource has a plan
        plan = self._find_plan(resource_id)
        if not plan:
            return ApprovalResult(
                success=False,
                resource_id=resource_id,
                error=f"No remediation plan found for resource: {resource_id}",
            )

        # Guard: reject if gate store is corrupted
        if self._gate_store.is_corrupted:
            return ApprovalResult(
                success=False,
                resource_id=resource_id,
                error="Approval gate store is corrupted — all operations locked until operator resets the store file",
                locked=True,
            )

        # Use approval gate (creates one if needed)
        gate = self._get_or_create_gate(resource_id)
        result = gate.attempt_approval(command, resource_id)

        if not result["valid"]:
            # Persist gate state on every failed attempt or lockout
            self._gate_store.set_gate(
                resource_id, gate.attempts, gate.locked, gate.max_attempts
            )
            if result.get("locked"):
                self._log_action("approval", resource_id, "failure", "Max attempts exceeded")
                return ApprovalResult(
                    success=False,
                    resource_id=resource_id,
                    error="Max attempts exceeded",
                    locked=True,
                )
            self._log_action("approval", resource_id, "failure", result.get("error", ""))
            return ApprovalResult(
                success=False,
                resource_id=resource_id,
                error=result.get("error", "Invalid approval"),
                expected_format=result.get("expected_format"),
                attempts_remaining=result.get("attempts_remaining"),
            )

        # Approval valid — execute remediation (log action)
        self._log_action("approval", resource_id, "success", f"Approved by {self.approver}")

        # Dry-run mode: skip terraform execution entirely (demo/dev convenience)
        if os.environ.get("JANITOR_DRY_RUN") == "1":
            self._log_action("execution", resource_id, "success", "Remediation executed (dry-run)")
            self._run_post_remediation_hook(resource_id, "remediate", "success")
            try:
                self._savings_tracker.record_run()
            except Exception:
                pass
            return ApprovalResult(success=True, resource_id=resource_id)

        # Isolate the approved resource's HCL into a temporary working directory
        # so terraform apply only affects the approved resource, not all resources.
        import tempfile
        apply_dir = Path(tempfile.mkdtemp(prefix="janitor_apply_"))
        try:
            # Write only this resource's remediation HCL
            if plan.remediation_hcl:
                (apply_dir / "main.tf").write_text(plan.remediation_hcl, encoding="utf-8")
            else:
                self._log_action("execution", resource_id, "failure", "No remediation HCL for resource")
                return ApprovalResult(
                    success=False,
                    error="No remediation HCL available for this resource",
                    resource_id=resource_id,
                )

            # Inject provider config so terraform can connect to LocalStack or AWS
            endpoint_url = os.environ.get("AWS_ENDPOINT_URL", "")
            is_localstack = "localhost" in endpoint_url or "127.0.0.1" in endpoint_url
            if is_localstack:
                (apply_dir / "providers.tf").write_text(
                    'terraform {\n'
                    '  required_providers {\n'
                    '    aws = {\n'
                    '      source  = "hashicorp/aws"\n'
                    '      version = ">= 4.0"\n'
                    '    }\n'
                    '  }\n'
                    '}\n\n'
                    'provider "aws" {\n'
                    '  access_key                  = "test"\n'
                    '  secret_key                  = "test"\n'
                    '  skip_credentials_validation = true\n'
                    '  skip_metadata_api_check     = true\n'
                    '  skip_requesting_account_id  = true\n'
                    f'  region                      = "{os.environ.get("AWS_DEFAULT_REGION", "us-east-1")}"\n'
                    '\n'
                    '  endpoints {\n'
                    f'    ec2         = "{endpoint_url}"\n'
                    f'    s3          = "{endpoint_url}"\n'
                    f'    elasticache = "{endpoint_url}"\n'
                    f'    iam         = "{endpoint_url}"\n'
                    f'    sts         = "{endpoint_url}"\n'
                    '  }\n'
                    '}\n\n'
                    'variable "environment" {\n'
                    '  default = "dev"\n'
                    '}\n',
                    encoding="utf-8",
                )
            else:
                (apply_dir / "providers.tf").write_text(
                    'terraform {\n'
                    '  required_providers {\n'
                    '    aws = {\n'
                    '      source  = "hashicorp/aws"\n'
                    '      version = ">= 4.0"\n'
                    '    }\n'
                    '  }\n'
                    '}\n\n'
                    'variable "environment" {\n'
                    '  default = "dev"\n'
                    '}\n',
                    encoding="utf-8",
                )

            # Initialize the working directory before apply.
            logger.info("[Orchestrator] Running terraform init for %s...", resource_id)
            init_result = subprocess.run(
                [self.tf_cmd, "init", "-input=false"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(apply_dir),
                env=_build_subprocess_env("terraform"),
            )
            if init_result.returncode != 0:
                error = _redact(init_result.stderr.strip() or init_result.stdout.strip())
                self._log_action("execution", resource_id, "failure", f"{self.tf_cmd} init failed: {error}")
                tf_exc = RuntimeError(f"{self.tf_cmd} init failed: {error}")
                self._record_error(tf_exc, "Orchestrator", context="tf_apply")
                return ApprovalResult(
                    success=False,
                    error=f"{self.tf_cmd} init failed: {error}",
                    resource_id=resource_id,
                )

            # Execute terraform apply only for the approved resource
            logger.info("[Orchestrator] Running terraform apply for %s...", resource_id)
            apply_result = subprocess.run(
                [self.tf_cmd, "apply", "-auto-approve"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(apply_dir),
                env=_build_subprocess_env("terraform"),
            )
            if apply_result.returncode != 0:
                error = _redact(apply_result.stderr.strip() or apply_result.stdout.strip())
                self._log_action("execution", resource_id, "failure", f"{self.tf_cmd} apply failed: {error}")
                tf_exc = RuntimeError(f"{self.tf_cmd} apply failed: {error}")
                self._record_error(tf_exc, "Orchestrator", context="tf_apply")
                return ApprovalResult(
                    success=False,
                    error=f"{self.tf_cmd} apply failed: {error}",
                    resource_id=resource_id,
                )
        finally:
            # Clean up temp directory
            import shutil as _shutil
            _shutil.rmtree(apply_dir, ignore_errors=True)

        self._log_action("execution", resource_id, "success", "Remediation executed")

        # Run post-remediation hook
        self._run_post_remediation_hook(resource_id, "remediate", "success")

        # Record savings (non-blocking — errors are logged but don't fail approval)
        try:
            self._savings_tracker.record_run(resources_remediated=[resource_id])
        except Exception as e:
            logging.getLogger(__name__).warning(
                "Savings tracking failed (%s): %s", type(e).__name__, e
            )
            self._log_action(
                "savings", resource_id, "warning",
                f"Savings tracking failed ({type(e).__name__}): {e}",
            )

        return ApprovalResult(success=True, resource_id=resource_id)

    def rollback(self, command: str) -> RollbackResult:
        """
        Process a rollback command: "ROLLBACK <resource-id>" or "CONFIRM ROLLBACK <resource-id>".

        Args:
            command: The rollback command string.

        Returns:
            RollbackResult indicating success or next step needed.
        """
        # Check if this is a confirmation
        if command.startswith("CONFIRM ROLLBACK "):
            return self._handle_confirm_rollback(command)

        # Extract resource_id
        resource_id = self._extract_resource_id_from_command(command, "ROLLBACK")
        if not resource_id:
            return RollbackResult(
                success=False,
                error="Invalid command format. Expected: ROLLBACK <resource-id>",
            )

        # Guard: reject if gate store is corrupted
        if self._gate_store.is_corrupted:
            return RollbackResult(
                success=False,
                resource_id=resource_id,
                error="Approval gate store is corrupted — all operations locked until operator resets the store file",
            )

        # Enforce gate: check attempts / lockout for rollback
        gate = self._get_or_create_gate(resource_id)
        if gate.locked:
            self._log_action("rollback", resource_id, "failure", "Max attempts exceeded")
            return RollbackResult(
                success=False,
                resource_id=resource_id,
                error="Max attempts exceeded — rollback locked for this resource",
            )

        # Validate rollback artifact exists
        rollback_path = self.rollbacks_dir / f"{resource_id}.tf"
        if not rollback_path.exists():
            self._log_action("rollback", resource_id, "failure", "Rollback artifact missing")
            return RollbackResult(
                success=False,
                resource_id=resource_id,
                error=f"Rollback artifact not found: rollbacks/{resource_id}.tf",
            )

        # Parse the rollback command
        result = parse_rollback(command, resource_id)
        if not result["valid"]:
            # Count as a failed attempt against the gate
            gate._attempts += 1
            if gate._attempts >= gate.max_attempts:
                gate._locked = True
            self._gate_store.set_gate(
                resource_id, gate.attempts, gate.locked, gate.max_attempts
            )
            if gate.locked:
                self._log_action("rollback", resource_id, "failure", "Max attempts exceeded")
                return RollbackResult(
                    success=False,
                    resource_id=resource_id,
                    error="Max attempts exceeded — rollback locked for this resource",
                )
            return RollbackResult(
                success=False,
                resource_id=resource_id,
                error=result.get("error", "Invalid rollback command"),
            )

        # Mark as pending confirmation
        self._pending_rollbacks.add(resource_id)
        self._log_action("rollback", resource_id, "started", "Awaiting confirmation")

        return RollbackResult(
            success=False,
            resource_id=resource_id,
            needs_confirmation=True,
        )

    def get_audit_trail(self) -> list[AuditEntry]:
        """Return the complete audit trail."""
        return list(self._audit_trail)

    # ──────────────────────────────────────────────────────────────────────
    # Private: Hook execution
    # ──────────────────────────────────────────────────────────────────────

    def _run_pre_remediation_hook(self, plans: list[RemediationPlan]) -> str | None:
        """
        Run the pre-remediation hook (terraform validate) on generated HCL.

        Args:
            plans: Active (non-blocked) remediation plans.

        Returns:
            Error string if hook fails, None if passes.
        """
        hook_path = self.hooks_dir / "pre-remediation.sh"
        if not hook_path.exists():
            # Fail closed: missing hook means validation cannot be confirmed.
            # Log prominently so operators notice the missing hook.
            logger.warning(
                "Pre-remediation hook not found at %s — failing closed. "
                "Remediation cannot proceed without validation.",
                hook_path,
            )
            return (
                "Pre-remediation hook missing — validation cannot be confirmed. "
                "Create hooks/pre-remediation.sh to enable remediation."
            )

        remediation_path = self.output_dir / "remediation.tf"
        if not remediation_path.exists():
            return "remediation.tf not found in output directory"

        # Validate each rollback file (not just the first one)
        rollback_paths = []
        for plan in plans:
            candidate = self.rollbacks_dir / f"{plan.resource_id}.tf"
            if candidate.exists():
                rollback_paths.append(candidate)

        if not rollback_paths:
            return "No rollback file found for validation"

        try:
            for rollback_path in rollback_paths:
                result = subprocess.run(
                    [
                        BASH_CMD,
                        _to_bash_path(hook_path),
                        _to_bash_path(remediation_path),
                        _to_bash_path(rollback_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    cwd=str(self.project_root),
                    env=_build_subprocess_env("hook"),
                )

                if result.returncode != 0:
                    error_output = _redact(result.stderr.strip() or result.stdout.strip())
                    return f"Pre-remediation hook failed: {error_output}"

        except subprocess.TimeoutExpired:
            return "Pre-remediation hook timed out"
        except FileNotFoundError:
            return "bash not found — cannot execute pre-remediation hook"
        except OSError as e:
            return f"Failed to execute pre-remediation hook: {e}"

        return None

    def _run_post_remediation_hook(
        self, resource_id: str, action: str, result: str
    ) -> None:
        """
        Run the post-remediation hook (audit.log append).

        Args:
            resource_id: The resource that was acted upon.
            action: "remediate" or "rollback".
            result: "success" or "failed".
        """
        hook_path = self.hooks_dir / "post-remediation.sh"
        if not hook_path.exists():
            return  # Hook not present, skip silently

        try:
            subprocess.run(
                [
                    BASH_CMD,
                    _to_bash_path(hook_path),
                    resource_id,
                    action,
                    result,
                    self.approver,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self.project_root),
                env=_build_subprocess_env("hook"),
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            # Post-remediation hook is non-blocking — log but don't fail
            pass

    def _run_pre_remediation_hook_full(
        self, plans: list[RemediationPlan]
    ) -> tuple[list[Path], list[str]]:
        """Validate rollback files for ALL active plans.

        Returns:
            (validated_paths, failures) — failures is a list of
            resource_ids missing rollback coverage.

        Raises:
            TimeoutError: If total validation exceeds 60 seconds.
        """
        start = time.monotonic()
        timeout = 60.0
        validated: list[Path] = []
        failures: list[str] = []

        for plan in plans:
            if time.monotonic() - start > timeout:
                raise TimeoutError(
                    "Pre-remediation hook exceeded 60s timeout"
                )

            rollback_path = self.rollbacks_dir / f"{plan.resource_id}.tf"

            # Check existence and non-empty
            if not rollback_path.exists() or rollback_path.stat().st_size == 0:
                failures.append(plan.resource_id)
                continue

            # Run hook script validation
            hook_path = self.hooks_dir / "pre-remediation.sh"
            if not hook_path.exists():
                # Missing hook cannot confirm exit 0 — validation failure
                failures.append(plan.resource_id)
                continue

            remaining = timeout - (time.monotonic() - start)
            try:
                result = subprocess.run(
                    [BASH_CMD, _to_bash_path(hook_path), _to_bash_path(rollback_path)],
                    capture_output=True,
                    text=True,
                    timeout=max(remaining, 1),
                    cwd=str(self.project_root),
                    env=_build_subprocess_env("hook"),
                )
            except subprocess.TimeoutExpired:
                raise TimeoutError(
                    "Pre-remediation hook exceeded 60s timeout"
                )

            if result.returncode != 0:
                failures.append(plan.resource_id)
                continue

            validated.append(rollback_path)

        return validated, failures

    # ──────────────────────────────────────────────────────────────────────
    # Private: AI agent helpers
    # ──────────────────────────────────────────────────────────────────────

    def _gather_resources(self) -> list[dict]:
        """Gather resources from MCP tools for anomaly detection.

        Returns combined resource list from cost and security data.
        Returns [] on any error (Req 1.10).
        """
        resources: list[dict] = []
        try:
            cost_data = get_cost_data()
            resources.extend(cost_data.get("resources", []))
        except Exception as exc:
            print(
                f"[Orchestrator] Error gathering cost resources: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

        try:
            sec_data = get_security_data()
            resources.extend(sec_data.get("findings", []))
        except Exception as exc:
            print(
                f"[Orchestrator] Error gathering security resources: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

        return resources

    def _run_anomaly_detection(
        self, resources: list[dict], findings: list[dict]
    ) -> list[dict]:
        """Run AnomalyDetector with safe default on failure (Req 1.10, 6.4).

        Args:
            resources: Combined resource list.
            findings: Combined findings from FinOps + SecOps.

        Returns:
            List of anomaly dicts, [] on failure.
        """
        try:
            return self._anomaly_detector.detect(resources, findings)
        except Exception as exc:
            print(
                f"[Orchestrator] AnomalyDetector error: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return []

    # ──────────────────────────────────────────────────────────────────────
    # Private: Validation and helpers
    # ──────────────────────────────────────────────────────────────────────

    def _write_findings_store(self, findings: list[dict], metadata: dict) -> None:
        """Write findings store with schema version.

        Args:
            findings: List of finding dicts to persist.
            metadata: Additional top-level metadata fields.
        """
        store = {
            "schema_version": SCHEMA_VERSION,
            **metadata,
            "findings": findings,
        }
        self.findings_store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.findings_store_path, "w") as f:
            json.dump(store, f, indent=2)

    def _validate_schema_version(self, store: dict) -> str | None:
        """Validate schema_version field.

        Returns error string or None.
        """
        version_str = store.get("schema_version")
        if version_str is None:
            return "schema_version field is missing"

        try:
            parts = version_str.split(".")
            found_major = int(parts[0])
        except (ValueError, IndexError, AttributeError):
            return f"Invalid schema_version format: '{version_str}'"

        expected_major = int(SCHEMA_VERSION.split(".")[0])

        if found_major != expected_major:
            return (
                f"Incompatible schema version: found {version_str}, "
                f"expected major version {expected_major}"
            )

        # Warn on higher minor version
        if len(parts) >= 2:
            try:
                found_minor = int(parts[1])
                expected_minor = int(SCHEMA_VERSION.split(".")[1])
                if found_minor > expected_minor:
                    logging.getLogger(__name__).warning(
                        "Findings store minor version %s is higher than expected %s. "
                        "Proceeding with best-effort parsing.",
                        version_str, SCHEMA_VERSION,
                    )
            except (ValueError, IndexError):
                return f"Invalid schema_version format: '{version_str}'"

        return None

    def _validate_findings_store(self) -> str | None:
        """
        Validate findings_store.json exists, is readable, and has a compatible
        schema version. Both agents must have completed (tracked by the
        'agents_completed' field), but zero findings from either agent is valid
        — it means the account is healthy, not that an agent failed to run.

        Returns:
            Error string if validation fails, None if valid.
        """
        if not self.findings_store_path.exists():
            return "findings_store.json does not exist"

        try:
            with open(self.findings_store_path) as f:
                store = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            return f"Cannot read findings_store.json: {e}"

        # Validate schema version first
        schema_error = self._validate_schema_version(store)
        if schema_error:
            return schema_error

        # Check agent completion markers (preferred) — distinguishes
        # "agent ran and found nothing" from "agent never ran".
        agents_completed = set(store.get("agents_completed", []))
        if agents_completed:
            if "finops" not in agents_completed:
                return "findings_store.json: FinOps agent did not complete"
            if "secops" not in agents_completed:
                return "findings_store.json: SecOps agent did not complete"
            return None

        # Fallback for legacy stores without agents_completed:
        # require at least one finding tagged from each agent.
        findings = store.get("findings", [])
        agents_present = {f.get("agent") for f in findings}

        if "finops" not in agents_present:
            return "findings_store.json missing FinOps agent entries"
        if "secops" not in agents_present:
            return "findings_store.json missing SecOps agent entries"

        return None

    def _extract_resource_id_from_command(self, command: str, prefix: str) -> str | None:
        """Extract and validate resource_id from a command string.

        Validates against an allowlist regex permitting only:
        alphanumeric, hyphens, underscores, colons, periods, forward slashes.
        Total length: 1-256 characters.

        Returns None and logs at DEBUG level if validation fails.
        """
        expected_prefix = prefix + " "
        if not command.startswith(expected_prefix):
            return None

        candidate = command[len(expected_prefix):]

        # Reject empty or whitespace-only before regex
        if not candidate or candidate.isspace():
            return None

        # Allowlist validation
        if not _RESOURCE_ID_PATTERN.match(candidate):
            logging.getLogger(__name__).debug(
                "Rejected resource ID: %s", candidate[:64]
            )
            return None

        return candidate

    def _find_plan(self, resource_id: str) -> RemediationPlan | None:
        """Find a remediation plan by resource_id."""
        for plan in self._last_plans:
            if plan.resource_id == resource_id and not plan.blocked:
                return plan
        return None

    def _get_or_create_gate(self, resource_id: str) -> ApprovalGate:
        """Get or create an approval gate for a resource.

        Restores persisted state (attempts, locked) from the gate store
        when creating a new in-memory gate instance.
        """
        if resource_id not in self._approval_gates:
            gate = ApprovalGate(max_attempts=3)
            # Restore persisted state if available
            persisted = self._gate_store.get_gate(resource_id)
            if persisted:
                gate._attempts = persisted.get("attempts", 0)
                gate._locked = persisted.get("locked", False)
            self._approval_gates[resource_id] = gate
        return self._approval_gates[resource_id]

    def _handle_confirm_rollback(self, command: str) -> RollbackResult:
        """Handle a CONFIRM ROLLBACK command."""
        # Extract resource_id from "CONFIRM ROLLBACK <id>"
        prefix = "CONFIRM ROLLBACK "
        if not command.startswith(prefix):
            return RollbackResult(
                success=False,
                error="Invalid format. Expected: CONFIRM ROLLBACK <resource-id>",
            )

        resource_id = command[len(prefix):]
        if not resource_id:
            return RollbackResult(
                success=False,
                error="Missing resource ID in confirm rollback command",
            )

        # Validate resource_id format (defense-in-depth)
        if not _RESOURCE_ID_PATTERN.match(resource_id):
            return RollbackResult(
                success=False,
                error="Invalid resource ID format in confirm rollback command",
            )

        # Guard: reject if gate store is corrupted
        if self._gate_store.is_corrupted:
            return RollbackResult(
                success=False,
                resource_id=resource_id,
                error="Approval gate store is corrupted — all operations locked until operator resets the store file",
            )

        # Enforce gate: check attempts / lockout
        gate = self._get_or_create_gate(resource_id)
        if gate.locked:
            self._log_action("rollback", resource_id, "failure", "Max attempts exceeded")
            return RollbackResult(
                success=False,
                resource_id=resource_id,
                error="Max attempts exceeded — rollback locked for this resource",
            )

        # Validate resource was pending rollback
        if resource_id not in self._pending_rollbacks:
            return RollbackResult(
                success=False,
                resource_id=resource_id,
                error=f"No pending rollback for resource: {resource_id}. "
                      f"Send 'ROLLBACK {resource_id}' first.",
            )

        # Parse the confirm rollback command
        result = parse_confirm_rollback(command, resource_id)
        if not result["valid"]:
            # Count as a failed attempt against the gate
            gate._attempts += 1
            if gate._attempts >= gate.max_attempts:
                gate._locked = True
            self._gate_store.set_gate(
                resource_id, gate.attempts, gate.locked, gate.max_attempts
            )
            if gate.locked:
                self._log_action("rollback", resource_id, "failure", "Max attempts exceeded")
                return RollbackResult(
                    success=False,
                    resource_id=resource_id,
                    error="Max attempts exceeded — rollback locked for this resource",
                )
            return RollbackResult(
                success=False,
                resource_id=resource_id,
                error=result.get("error", "Invalid confirm rollback command"),
            )

        # Validate rollback artifact still exists
        rollback_path = self.rollbacks_dir / f"{resource_id}.tf"
        if not rollback_path.exists():
            self._log_action("rollback", resource_id, "failure", "Rollback artifact missing")
            return RollbackResult(
                success=False,
                resource_id=resource_id,
                error=f"Rollback artifact not found: rollbacks/{resource_id}.tf",
            )

        # Stage the rollback HCL as the active config, then init + apply it
        # against LocalStack — mirrors the same init/apply pattern approve() uses.
        # Without this, CONFIRM ROLLBACK only updated bookkeeping and never
        # touched real infrastructure.
        remediation_path = self.output_dir / "remediation.tf"
        try:
            remediation_path.write_text(rollback_path.read_text())
        except OSError as e:
            self._log_action(
                "rollback", resource_id, "failure",
                f"Failed to stage rollback artifact: {e}",
            )
            return RollbackResult(
                success=False,
                resource_id=resource_id,
                error=f"Failed to stage rollback artifact: {e}",
            )

        logger.info("[Orchestrator] Running terraform init for rollback %s...", resource_id)
        init_result = subprocess.run(
            [self.tf_cmd, "init", "-input=false"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(self.output_dir),
            env=_build_subprocess_env("terraform"),
        )
        if init_result.returncode != 0:
            error = _redact(init_result.stderr.strip() or init_result.stdout.strip())
            self._log_action("rollback", resource_id, "failure", f"{self.tf_cmd} init failed: {error}")
            tf_exc = RuntimeError(f"{self.tf_cmd} init failed: {error}")
            self._record_error(tf_exc, "Orchestrator", context="tf_apply")
            return RollbackResult(
                success=False,
                resource_id=resource_id,
                error=f"{self.tf_cmd} init failed: {error}",
                error_category="terraform_failure",
                error_agent="Orchestrator",
                exit_code=init_result.returncode,
            )

        logger.info("[Orchestrator] Running terraform apply for rollback %s...", resource_id)
        apply_result = subprocess.run(
            [self.tf_cmd, "apply", "-auto-approve"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(self.output_dir),
            env=_build_subprocess_env("terraform"),
        )
        if apply_result.returncode != 0:
            error = _redact(apply_result.stderr.strip() or apply_result.stdout.strip())
            self._log_action("rollback", resource_id, "failure", f"{self.tf_cmd} apply failed: {error}")
            tf_exc = RuntimeError(f"{self.tf_cmd} apply failed: {error}")
            self._record_error(tf_exc, "Orchestrator", context="tf_apply")
            # Leave the resource pending so the caller can retry CONFIRM ROLLBACK
            return RollbackResult(
                success=False,
                resource_id=resource_id,
                error=f"{self.tf_cmd} apply failed: {error}",
                error_category="terraform_failure",
                error_agent="Orchestrator",
                exit_code=apply_result.returncode,
            )

        # Rollback applied successfully
        self._pending_rollbacks.discard(resource_id)
        self._log_action("rollback", resource_id, "success", f"Rollback executed by {self.approver}")

        # Run post-remediation hook for rollback (only after apply actually succeeded)
        self._run_post_remediation_hook(resource_id, "rollback", "success")

        return RollbackResult(success=True, resource_id=resource_id)

    def _log_action(self, action: str, resource_id: str, result: str, details: str = "") -> None:
        """Append an entry to the internal audit trail and the persistent audit log."""
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            action=action,
            resource_id=resource_id,
            actor=self.approver,
            result=result,
            details=details,
        )
        self._audit_trail.append(entry)
        # Persist to append-only file log (failures are non-blocking)
        self._audit_logger.append(entry.to_dict())

    def _classify_error(self, exc: Exception, context: str = "") -> str:
        """Classify an exception into one of the structured error categories.

        Classification logic:
          - context in {"tf_validate", "tf_apply", "tf_plan"} → "terraform_failure"
          - isinstance(exc, (OSError, IOError, PermissionError)) → "io_failure"
          - context in {"schema_check", "gate_check", "hook_validation", "resource_id_check"} → "validation_failure"
          - default → "agent_failure"

        Args:
            exc: The caught exception.
            context: Optional context string indicating where the error occurred.

        Returns:
            One of: "terraform_failure", "io_failure", "validation_failure", "agent_failure".
        """
        if context in ("tf_validate", "tf_apply", "tf_plan"):
            return "terraform_failure"
        if isinstance(exc, (OSError, IOError, PermissionError)):
            return "io_failure"
        if context in ("schema_check", "gate_check", "hook_validation", "resource_id_check"):
            return "validation_failure"
        return "agent_failure"

    def _record_error(self, exc: Exception, agent_name: str, context: str = "") -> None:
        """Build and write a structured error record to the audit log.

        Args:
            exc: The caught exception.
            agent_name: Identifying string for the failing agent/component.
            context: Optional context string for error classification.
        """
        category = self._classify_error(exc, context)
        record = build_error_record(exc, agent_name, category)
        try:
            write_error_record(record, self.audit_log_path)
        except Exception as write_exc:
            logger.warning(
                "Failed to write error record: %s: %s",
                type(write_exc).__name__, write_exc,
            )