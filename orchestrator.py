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
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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

from agents.approval_gate import (
    ApprovalGate,
    ApprovalGateStore,
    parse_approval,
    parse_confirm_rollback,
    parse_rollback,
)
from agents.anomaly_detector import AnomalyDetector
from agents.audit_logger import AuditLogger
from agents.drift_detector import DriftDetector
from agents.finops_auditor import FinOpsAuditor
from agents.query_interpreter import QueryInterpreter
from agents.reasoning_logger import ReasoningLogger
from agents.remediation_architect import RemediationArchitect, RemediationPlan
from agents.secops_guard import SecOpsGuard
from mcp_server.aws_janitor_mcp import get_cost_data, get_security_data
from agents.savings_tracker import SavingsTracker


TF_CMD = os.environ.get("TF_CMD", "tflocal")

TF_CMD_ALLOWLIST = {"terraform", "tflocal"}

BASH_CMD = _find_bash()

_RESOURCE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9\-_:./]{1,256}$")


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


PROJECT_ROOT = Path(__file__).parent
FINDINGS_STORE_PATH = PROJECT_ROOT / "output" / "findings_store.json"
HOOKS_DIR = PROJECT_ROOT / "hooks"
OUTPUT_DIR = PROJECT_ROOT / "output"
ROLLBACKS_DIR = PROJECT_ROOT / "output" / "rollbacks"
AUDIT_LOG_PATH = PROJECT_ROOT / "output" / "logs" / "audit.log"


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
    anomalies: list[dict] = field(default_factory=list)
    drift_report: dict | None = None


@dataclass
class ApprovalResult:
    """Result of approve()."""

    success: bool
    resource_id: str = ""
    error: str | None = None
    locked: bool = False
    expected_format: str | None = None
    attempts_remaining: int | None = None


@dataclass
class RollbackResult:
    """Result of rollback()."""

    success: bool
    resource_id: str = ""
    error: str | None = None
    needs_confirmation: bool = False


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
        self.findings_store_path = self.project_root / "output" / "findings_store.json"
        # Ensure output directories exist
        (self.project_root / "output" / "logs").mkdir(parents=True, exist_ok=True)
        (self.project_root / "output" / "rollbacks").mkdir(parents=True, exist_ok=True)
        (self.project_root / "output" / "policies").mkdir(parents=True, exist_ok=True)
        self.hooks_dir = self.project_root / "hooks"
        self.output_dir = self.project_root / "output"
        self.rollbacks_dir = self.project_root / "output" / "rollbacks"
        self.audit_log_path = self.project_root / "output" / "logs" / "audit.log"
        self.approver = approver

        # Validate and resolve TF_CMD binary
        self._tf_cmd = _validate_tf_cmd()

        # Reasoning logger (shared across all agents)
        self._reasoning_logger = ReasoningLogger(
            log_path=self.project_root / "output" / "logs" / "agent_reasoning.log"
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
            history_path=self.project_root / "output" / "scan_history.json"
        )

        # Savings tracker
        self._savings_tracker = SavingsTracker(
            ledger_path=self.project_root / "output" / "savings_ledger.json",
            findings_store_path=self.findings_store_path,
        )

        # Persistent approval gate store
        self._gate_store = ApprovalGateStore(self.output_dir / "approval_gates.json")
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
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def execute_audit(self) -> AuditResult:
        """
        Execute the full audit pipeline: FinOps → SecOps → Remediation Architect.

        Returns:
            AuditResult with findings, plans, and any errors.
        """
        # Truncate reasoning log at the start of each new audit run
        self._reasoning_logger.truncate()

        # Step 1: FinOps Auditor scan
        self._log_action("scan", "all", "started", "FinOps Auditor scan initiated")
        finops_findings = self._finops.scan()
        self._log_action("scan", "all", "success", f"FinOps found {len(finops_findings)} finding(s)")

        # Step 2: SecOps Guard scan
        self._log_action("scan", "all", "started", "SecOps Guard scan initiated")
        secops_findings = self._secops.scan()
        self._log_action("scan", "all", "success", f"SecOps found {len(secops_findings)} finding(s)")

        # Step 3: Validate findings_store has entries from both agents
        validation_error = self._validate_findings_store()
        if validation_error:
            self._log_action("plan", "all", "failure", validation_error)
            return AuditResult(success=False, error=validation_error)

        # Step 4: Remediation Architect plans
        self._log_action("plan", "all", "started", "Remediation Architect planning")
        plans = self._architect.plan()
        self._last_plans = plans

        blocked_plans = [p for p in plans if p.blocked]
        active_plans = [p for p in plans if not p.blocked]

        # Log blocked plans
        for p in blocked_plans:
            self._log_action("plan", p.resource_id, "blocked", p.block_reason)

        self._log_action(
            "plan", "all", "success",
            f"Generated {len(active_plans)} plan(s), {len(blocked_plans)} blocked"
        )

        # Step 5: Run pre-remediation hook on active plans
        if active_plans:
            hook_error = self._run_pre_remediation_hook(active_plans)
            if hook_error:
                self._log_action("plan", "all", "blocked", f"Pre-remediation hook failed: {hook_error}")
                return AuditResult(
                    success=False,
                    findings=finops_findings + secops_findings,
                    plans=active_plans,
                    blocked_plans=blocked_plans,
                    hook_error=hook_error,
                )

        all_findings = finops_findings + secops_findings

        # Step 6: Anomaly Detection (post-scan, before drift) — Req 6.4
        resources = self._gather_resources()
        anomalies = self._run_anomaly_detection(resources, all_findings)

        # Step 7: Drift Detection — save snapshot then detect
        total_waste = sum(
            f.get("cost_estimate_monthly", 0.0) for f in all_findings
        )
        scan_id = str(uuid.uuid4())
        self._drift_detector.save_snapshot(scan_id, all_findings, anomalies, total_waste)
        drift_report = self._drift_detector.detect(all_findings)

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
            cost_findings = list(all_resources)
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

        # Initialize the working directory before apply. Without this, terraform
        # has no provider plugin selected and no lock file, causing apply to fail
        # with "inconsistent dependency lock file" or similar init-required errors.
        init_result = subprocess.run(
            [self._tf_cmd, "init", "-input=false"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(self.project_root / "output"),
        )
        if init_result.returncode != 0:
            error = init_result.stderr.strip() or init_result.stdout.strip()
            self._log_action("execution", resource_id, "failure", f"{self._tf_cmd} init failed: {error}")
            return ApprovalResult(
                success=False,
                error=f"{self._tf_cmd} init failed: {error}",
                resource_id=resource_id,
            )

        # Execute terraform apply against LocalStack
        apply_result = subprocess.run(
            [self._tf_cmd, "apply", "-auto-approve"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(self.project_root / "output"),
        )
        if apply_result.returncode != 0:
            error = apply_result.stderr.strip() or apply_result.stdout.strip()
            self._log_action("execution", resource_id, "failure", f"{self._tf_cmd} apply failed: {error}")
            return ApprovalResult(
                success=False,
                error=f"{self._tf_cmd} apply failed: {error}",
                resource_id=resource_id,
            )

        self._log_action("execution", resource_id, "success", "Remediation executed")

        # Run post-remediation hook
        self._run_post_remediation_hook(resource_id, "remediate", "success")

        # Record savings (non-blocking — errors are logged but don't fail approval)
        try:
            self._savings_tracker.record_run(resources_remediated=[resource_id])
        except (FileNotFoundError, OSError) as e:
            self._log_action(
                "savings", resource_id, "warning",
                f"Savings tracking failed: {e}",
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
            return None  # Hook not present, skip

        remediation_path = self.output_dir / "remediation.tf"
        if not remediation_path.exists():
            return "remediation.tf not found in output directory"

        # Find a rollback file for validation (use first active plan's resource)
        rollback_path = None
        for plan in plans:
            candidate = self.rollbacks_dir / f"{plan.resource_id}.tf"
            if candidate.exists():
                rollback_path = candidate
                break

        if not rollback_path:
            return "No rollback file found for validation"

        try:
            result = subprocess.run(
                [
                    BASH_CMD,
                    _to_bash_path(hook_path),
                    _to_bash_path(remediation_path),
                    _to_bash_path(rollback_path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(self.project_root),
            )

            if result.returncode != 0:
                error_output = result.stderr.strip() or result.stdout.strip()
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
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            # Post-remediation hook is non-blocking — log but don't fail
            pass

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

    def _validate_findings_store(self) -> str | None:
        """
        Validate findings_store.json has entries from both FinOps and SecOps agents.

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

        init_result = subprocess.run(
            [self._tf_cmd, "init", "-input=false"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(self.output_dir),
        )
        if init_result.returncode != 0:
            error = init_result.stderr.strip() or init_result.stdout.strip()
            self._log_action("rollback", resource_id, "failure", f"{self._tf_cmd} init failed: {error}")
            return RollbackResult(
                success=False,
                resource_id=resource_id,
                error=f"{self._tf_cmd} init failed: {error}",
            )

        apply_result = subprocess.run(
            [self._tf_cmd, "apply", "-auto-approve"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(self.output_dir),
        )
        if apply_result.returncode != 0:
            error = apply_result.stderr.strip() or apply_result.stdout.strip()
            self._log_action("rollback", resource_id, "failure", f"{self._tf_cmd} apply failed: {error}")
            # Leave the resource pending so the caller can retry CONFIRM ROLLBACK
            return RollbackResult(
                success=False,
                resource_id=resource_id,
                error=f"{self._tf_cmd} apply failed: {error}",
            )

        # Execute rollback
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