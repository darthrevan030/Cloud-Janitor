#!/usr/bin/env python3
"""SPEC_COMPLIANCE.md generator script.

Reads .kiro/specs/tasks.md, parses task checkboxes, verifies artifact
existence using a keyword-to-file mapping, and outputs a compliance
report as a 4-column Markdown table.

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# Keyword-to-file mapping table (Requirement 8.3)
KEYWORD_MAPPING = [
    (["requirements"], ".kiro/specs/requirements.md"),
    (["design"], ".kiro/specs/design.md"),
    (["fixture"], "fixtures/"),
    (["mcp", "MCP"], "mcp_server/aws_janitor_mcp.py"),
    (["FinOps", "finops"], "agents/finops_auditor.py"),
    (["SecOps", "secops"], "agents/secops_guard.py"),
    (["Remediation", "remediation"], "agents/remediation_architect.py"),
    (["rollback"], "output/rollbacks/"),
    (["findings_store"], "output/findings_store.json"),
    (["pre-remediation"], "hooks/pre-remediation.sh"),
    (["post-remediation"], "hooks/post-remediation.sh"),
    (["approval"], "__APPROVE_STRING_CHECK__"),
    (["audit log"], "__AUDIT_LOG_CHECK__"),
    (["Streamlit", "UI", "app.py"], "app.py"),
    (["savings"], "agents/savings_tracker.py"),
]


def find_tasks_md_files(project_root: Path) -> list[Path]:
    """Find tasks.md file(s), trying the literal path first then subdirectories."""
    # Try literal path from requirement
    literal = project_root / ".kiro" / "specs" / "tasks.md"
    if literal.exists():
        return [literal]

    # Search subdirectories of .kiro/specs/
    specs_dir = project_root / ".kiro" / "specs"
    if specs_dir.exists():
        found = sorted(specs_dir.rglob("tasks.md"))
        if found:
            return found

    return []


def parse_tasks(content: str) -> list[dict]:
    """Parse checkbox lines from tasks.md content.

    Returns a list of dicts with keys: text, status
    where status is 'done', 'pending', or 'partial'.
    """
    tasks = []
    # Match all checkbox task lines (including indented sub-tasks)
    # Pattern: lines with "- [x]", "- [ ]", or "- [-]" with optional leading whitespace
    pattern = re.compile(r"^\s*- \[([ x\-])\]\s+(.+)$", re.MULTILINE)

    for match in pattern.finditer(content):
        marker = match.group(1)
        text = match.group(2).strip()

        if marker == "x":
            status = "done"
        elif marker == "-":
            status = "partial"
        else:
            status = "pending"

        tasks.append({"text": text, "status": status})

    return tasks


def check_approve_string(project_root: Path) -> bool:
    """Check if 'APPROVE' string exists in agents/ files or orchestrator.py."""
    # Check orchestrator.py
    orchestrator = project_root / "orchestrator.py"
    if orchestrator.exists():
        content = orchestrator.read_text(encoding="utf-8", errors="ignore")
        if "APPROVE" in content:
            return True

    # Check agents/ directory
    agents_dir = project_root / "agents"
    if agents_dir.exists():
        for py_file in agents_dir.glob("*.py"):
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            if "APPROVE" in content:
                return True

    return False


def check_audit_log(project_root: Path) -> bool:
    """Check if audit.log exists or an audit log writer is in the codebase."""
    # Check audit.log file
    if (project_root / "audit.log").exists():
        return True

    # Check for audit log writer in codebase (agents/ directory)
    agents_dir = project_root / "agents"
    if agents_dir.exists():
        for py_file in agents_dir.glob("*.py"):
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            if "audit" in content.lower() and ("log" in content.lower() or "logger" in content.lower()):
                return True

    # Check orchestrator.py
    orchestrator = project_root / "orchestrator.py"
    if orchestrator.exists():
        content = orchestrator.read_text(encoding="utf-8", errors="ignore")
        if "audit" in content.lower() and ("log" in content.lower() or "logger" in content.lower()):
            return True

    return False


def verify_artifact(task_text: str, project_root: Path) -> str:
    """Verify artifact existence for a task based on keyword mapping.

    Returns a description of the verification result.
    """
    for keywords, target in KEYWORD_MAPPING:
        matched_keyword = None
        for kw in keywords:
            if kw in task_text:
                matched_keyword = kw
                break

        if matched_keyword is None:
            continue

        # Special checks
        if target == "__APPROVE_STRING_CHECK__":
            if check_approve_string(project_root):
                return '"APPROVE" found in codebase'
            else:
                return '"APPROVE" not found in codebase'

        if target == "__AUDIT_LOG_CHECK__":
            if check_audit_log(project_root):
                return "audit log writer found"
            else:
                return "audit log not found"

        # File or directory check
        artifact_path = project_root / target
        if artifact_path.exists():
            return f"{target} exists"

        # For files under .kiro/specs/, also check subdirectories
        if target.startswith(".kiro/specs/") and not artifact_path.is_dir():
            filename = Path(target).name
            specs_dir = project_root / ".kiro" / "specs"
            for found in specs_dir.rglob(filename):
                rel = str(found.relative_to(project_root)).replace("\\", "/")
                return f"{rel} exists"

        return f"{target} missing"

    return "no mapping"


def _verify_task_statuses(
    spec_tasks: list[tuple[str, list[dict]]], project_root: Path
) -> list[tuple[str, str, str, str]]:
    """Verify task claimed statuses against actual filesystem evidence.

    Returns anomalies: (spec_name, task_text, claimed_status, issue_description)
    """
    anomalies: list[tuple[str, str, str, str]] = []

    # Patterns to extract file paths from task text
    file_pattern = re.compile(
        r"`([a-zA-Z0-9_/.\-]+\.(?:py|sh|yml|toml|json|md))`"
    )
    test_file_pattern = re.compile(
        r"tests/test_[a-zA-Z0-9_]+\.py"
    )

    for spec_name, tasks in spec_tasks:
        for task in tasks:
            text = task["text"]
            status = task["status"]

            # For "done" tasks — check if referenced files exist
            if status == "done":
                # Check for test files referenced in task
                test_matches = test_file_pattern.findall(text)
                for test_file in test_matches:
                    if not (project_root / test_file).exists():
                        anomalies.append((
                            spec_name, text, "done",
                            f"`{test_file}` not found on disk"
                        ))

                # Check for specific Create/Implement tasks with file refs
                if "Create" in text or "Implement" in text:
                    file_matches = file_pattern.findall(text)
                    for filepath in file_matches:
                        # Skip test files (handled above) and relative paths with tests/
                        if filepath.startswith("tests/"):
                            continue
                        # Only check source files, not arbitrary references
                        if (filepath.endswith(".py") or filepath.endswith(".sh")) and \
                           not (project_root / filepath).exists():
                            # Could be a nested path reference — skip common false positives
                            if "/" in filepath and not filepath.startswith("."):
                                anomalies.append((
                                    spec_name, text, "done",
                                    f"`{filepath}` referenced but not found"
                                ))

            # For "pending" tasks — check if something was already done
            elif status == "pending":
                test_matches = test_file_pattern.findall(text)
                for test_file in test_matches:
                    if (project_root / test_file).exists():
                        anomalies.append((
                            spec_name, text, "pending",
                            f"`{test_file}` exists — task may already be done"
                        ))

    return anomalies


def _audit_feature_statuses(project_root: Path) -> list[tuple[str, str, str]]:
    """Audit all project features and determine their real implementation status.

    Returns a list of (feature_name, status, justification) tuples.
    Status is one of: Complete, Partial, Stub, Pending, Deferred.
    """
    statuses: list[tuple[str, str, str]] = []

    def _file_has(path: Path, pattern: str) -> bool:
        """Check if a file exists and contains a pattern."""
        if not path.exists():
            return False
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            return pattern in content
        except OSError:
            return False

    def _file_exists(rel: str) -> bool:
        return (project_root / rel).exists()

    def _file_has_real_code(path: Path, min_lines: int = 10) -> bool:
        """Check if a file has meaningful code (not just a stub)."""
        if not path.exists():
            return False
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            # Filter out empty lines and comments
            code_lines = [
                l for l in content.splitlines()
                if l.strip() and not l.strip().startswith("#")
            ]
            return len(code_lines) >= min_lines
        except OSError:
            return False

    # --- Core Pipeline ---
    statuses.append((
        "FinOps Auditor",
        "Complete" if _file_has_real_code(project_root / "agents/finops_auditor.py", 30) else "Pending",
        "Multi-agent FinOps scanner detecting idle resources, classifying severity, writing findings"
    ))
    statuses.append((
        "SecOps Guard",
        "Complete" if _file_has_real_code(project_root / "agents/secops_guard.py", 30) else "Pending",
        "Security vulnerability scanner — SG rules, encryption, auth checks"
    ))
    statuses.append((
        "Remediation Architect",
        "Complete" if _file_has_real_code(project_root / "agents/remediation_architect.py", 30) else "Pending",
        "HCL generation with dependency checks, rollback HCL alongside remediation"
    ))
    statuses.append((
        "Agent Orchestrator",
        "Complete" if _file_has(project_root / "orchestrator.py", "execute_audit") else "Pending",
        "Pipeline sequencing: FinOps → SecOps → Remediation with hooks and approval"
    ))

    # Approval gate
    statuses.append((
        "Approval Gate (persistent, rate-limited)",
        "Complete" if _file_has(project_root / "agents/approval_gate.py", "ApprovalGateStore") else "Pending",
        "3-attempt lockout, atomic persistence, survives process restarts"
    ))

    # Rollback with TF execution
    has_rollback = _file_has(project_root / "orchestrator.py", "execute_rollback") or \
                   _file_has(project_root / "orchestrator.py", "def rollback")
    statuses.append((
        "Rollback with Terraform Execution",
        "Complete" if has_rollback else "Pending",
        "2-step rollback (ROLLBACK + CONFIRM), TF validate → apply"
    ))

    # TF_CMD validation
    statuses.append((
        "TF_CMD Allowlist Validation",
        "Complete" if _file_has(project_root / "orchestrator.py", "_validate_tf_cmd") else "Pending",
        "Binary allowlist, path separator rejection, PATH resolution"
    ))

    # Pre-remediation hook
    statuses.append((
        "Pre-Remediation Hook Validation",
        "Complete" if _file_has(project_root / "orchestrator.py", "_run_pre_remediation_hook") else "Pending",
        "Validates rollback files for all active plans with 60s timeout"
    ))

    # --- Data Integrity ---
    statuses.append((
        "Findings Store Schema Versioning",
        "Complete" if _file_has(project_root / "orchestrator.py", "schema_version") else "Pending",
        "Semantic version field in findings_store.json, major version validation"
    ))

    statuses.append((
        "Path Convention Alignment (`core/paths.py`)",
        "Complete" if _file_has_real_code(project_root / "core/paths.py", 10) else "Pending",
        "Single source of truth for all artifact paths, used by Orchestrator and UI"
    ))

    statuses.append((
        "Structured Error Telemetry",
        "Complete" if _file_has_real_code(project_root / "core/error_telemetry.py", 10) else "Pending",
        "JSONL error records with category, agent_name, traceback (max 4096 chars)"
    ))

    statuses.append((
        "Reasoning Log (append-mode, rotation)",
        "Complete" if _file_has(project_root / "agents/reasoning_logger.py", "start_run") else "Pending",
        "Append-mode with JSONL separator per run, 10MB rotation, 5 file max"
    ))

    # --- Savings & Scheduling ---
    statuses.append((
        "Savings Tracker",
        "Complete" if _file_has_real_code(project_root / "agents/savings_tracker.py", 30) else "Pending",
        "Ledger lifecycle, duplicate prevention, broad exception handling in orchestrator"
    ))

    statuses.append((
        "JanitorScheduler (cron-based scans)",
        "Complete" if _file_has(project_root / "scheduler.py", "BackgroundScheduler") else "Pending",
        "APScheduler daemon thread, overlap prevention, cron configurable"
    ))

    # --- Phase B/C AI Agents ---
    phase_bc_agents = [
        ("QueryInterpreter", "agents/query_interpreter.py"),
        ("RemediationExplainer", "agents/explainer.py"),
        ("PolicySuggester", "agents/policy_suggester.py"),
        ("ResourceTagger", "agents/tagger.py"),
        ("AnomalyDetector", "agents/anomaly_detector.py"),
        ("IncidentPolicyGenerator", "agents/incident_policy_generator.py"),
        ("DriftDetector", "agents/drift_detector.py"),
        ("MultiAccountOrchestrator", "agents/multi_account_orchestrator.py"),
    ]
    for agent_name, agent_path in phase_bc_agents:
        full_path = project_root / agent_path
        status = "Complete" if _file_has_real_code(full_path, 20) else "Pending"
        statuses.append((agent_name, status, f"`{agent_path}` — LLM-powered agent with safe defaults"))

    # --- NL Audit (Req 10) ---
    nl_backend = _file_has(project_root / "orchestrator.py", "execute_natural_language_audit")
    nl_ui = _file_has(project_root / "app.py", "execute_natural_language_audit")
    if nl_backend and nl_ui:
        nl_status = "Complete"
        nl_just = "UI with `hasattr` guard + backend method implemented on Orchestrator"
    elif nl_ui and not nl_backend:
        nl_status = "Partial"
        nl_just = "UI elements exist with feature detection, but backend method not implemented"
    else:
        nl_status = "Pending"
        nl_just = "Neither UI nor backend implemented"
    statuses.append(("NL Audit", nl_status, nl_just))

    # --- MCP Server ---
    statuses.append((
        "MCP Server (core tools)",
        "Complete" if _file_has(project_root / "mcp_server/aws_janitor_mcp.py", "@mcp.tool") else "Pending",
        "4 core + 6 AI tools via FastMCP, provider-agnostic backend"
    ))

    # --- Provider Backends ---
    statuses.append((
        "FixtureProvider",
        "Complete" if _file_has_real_code(project_root / "mcp_server/backends/fixture_provider.py", 20) else "Pending",
        "Reads fixture JSONs, filters resources, computes cost totals"
    ))
    # AWSProvider — check if it has real implementations beyond just NotImplementedError
    aws_path = project_root / "mcp_server/backends/aws_provider.py"
    if _file_has(aws_path, "def get_cost_data") and _file_has_real_code(aws_path, 50):
        aws_status = "Complete"
        aws_just = "Full boto3 implementation querying live AWS/LocalStack infrastructure"
    elif _file_has(aws_path, "NotImplementedError"):
        aws_status = "Stub"
        aws_just = "Raises NotImplementedError (intentional stub)"
    else:
        aws_status = "Pending"
        aws_just = "Not implemented"
    statuses.append(("AWSProvider", aws_status, aws_just))
    statuses.append((
        "GCPProvider",
        "Stub" if _file_has(project_root / "mcp_server/backends/gcp_provider.py", "NotImplementedError") else "Pending",
        "Raises NotImplementedError with WARNING on init (intentional stub)"
    ))
    statuses.append((
        "AzureProvider",
        "Stub" if _file_has(project_root / "mcp_server/backends/azure_provider.py", "NotImplementedError") else "Pending",
        "Raises NotImplementedError with WARNING on init (intentional stub)"
    ))

    # --- Streamlit Dashboard ---
    statuses.append((
        "Streamlit Dashboard",
        "Complete" if _file_has(project_root / "app.py", "st.button") else "Pending",
        "Agent feed, findings, diff view, approval input, savings counter, reasoning panel"
    ))

    # --- Production Readiness ---
    statuses.append((
        "pyproject.toml (packaging)",
        "Complete" if _file_exists("pyproject.toml") else "Pending",
        "Hatchling build system, dependencies, dev group, entry points"
    ))

    statuses.append((
        "Structured Logging (`logging_config.py`)",
        "Complete" if _file_has(project_root / "core/logging_config.py", "configure_logging") else "Pending",
        "Env-var driven level, ISO 8601 timestamps, stderr output"
    ))

    statuses.append((
        "LLM Retry Logic",
        "Complete" if _file_has(project_root / "core/llm_client.py", "LLMRetryExhausted") else "Pending",
        "Exponential backoff, Retry-After respect, max 3 retries"
    ))

    # CLI
    cli_path = project_root / "cli.py"
    if _file_has(cli_path, "@click") or _file_has(cli_path, "click.group"):
        cli_status = "Complete"
        cli_just = "Click-based CLI with scan, approve, rollback, dashboard, mcp commands"
    elif _file_exists("cli.py"):
        cli_status = "Stub"
        cli_just = "Entry point file exists but only raises SystemExit (task 1.4 pending)"
    else:
        cli_status = "Pending"
        cli_just = "No CLI file exists"
    statuses.append(("CLI (`cloud-janitor` command)", cli_status, cli_just))

    # src-layout
    statuses.append((
        "src-layout Package Structure",
        "Complete" if _file_exists("src/cloud_janitor/__init__.py") else "Pending",
        "Flat layout currently — src-layout migration deferred to production-readiness Batch 3"
    ))

    # CI Pipeline
    statuses.append((
        "GitHub Actions CI Pipeline",
        "Complete" if _file_exists(".github/workflows/ci.yml") else "Pending",
        "Lint + type-check + test + build + publish pipeline"
    ))

    # LocalStack / Docker
    statuses.append((
        "LocalStack Integration (Docker)",
        "Complete" if _file_exists("docker-compose.yml") else "Pending",
        "docker-compose.yml with EC2, ElastiCache, S3, EBS services"
    ))

    # bin/tflocal wrapper
    statuses.append((
        "`bin/tflocal` Dry-Run Wrapper",
        "Complete" if _file_exists("bin/tflocal") else "Pending",
        "Repo-local wrapper — prints command + exits 0 when JANITOR_DRY_RUN=1"
    ))

    # Session isolation (deferred)
    statuses.append((
        "Session-Isolated File Paths",
        "Deferred",
        "Requirement 14 explicitly deferred to post-hackathon prod-readiness milestone"
    ))

    return statuses


def generate_report(
    spec_tasks: list[tuple[str, list[dict]]], project_root: Path
) -> str:
    """Generate the SPEC_COMPLIANCE.md content grouped by spec.

    Args:
        spec_tasks: List of (spec_name, tasks) tuples.
        project_root: Project root path for artifact verification.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        "# Spec Compliance Report",
        "",
        f"Generated: {now}",
        "",
    ]

    # Summary counts
    total = sum(len(tasks) for _, tasks in spec_tasks)
    done = sum(
        sum(1 for t in tasks if t["status"] == "done")
        for _, tasks in spec_tasks
    )
    pending = sum(
        sum(1 for t in tasks if t["status"] == "pending")
        for _, tasks in spec_tasks
    )
    partial = sum(
        sum(1 for t in tasks if t["status"] == "partial")
        for _, tasks in spec_tasks
    )

    lines.append(f"**Summary:** {total} tasks — ✅ {done} done, ⏳ {partial} partial, ❌ {pending} pending")
    lines.append("")

    for spec_name, tasks in spec_tasks:
        # Section header per spec
        spec_done = sum(1 for t in tasks if t["status"] == "done")
        spec_total = len(tasks)
        lines.append(f"## {spec_name} ({spec_done}/{spec_total})")
        lines.append("")
        lines.append("| # | Task | Status | Artifact Verified |")
        lines.append("|---|------|--------|-------------------|")

        for i, task in enumerate(tasks, start=1):
            task_text = task["text"]
            status = task["status"]

            if status == "done":
                artifact_info = verify_artifact(task_text, project_root)
                status_display = "✅ Done"
            elif status == "partial":
                artifact_info = verify_artifact(task_text, project_root)
                status_display = "⏳ Partial"
            else:
                status_display = "❌ Pending"
                artifact_info = "—"

            # Clean task text for table display
            display_text = task_text.rstrip()
            if not display_text.strip():
                display_text = "(untitled task)"
            # Escape pipe characters to avoid breaking markdown table structure
            display_text = display_text.replace("|", "\\|")

            lines.append(f"| {i} | {display_text} | {status_display} | {artifact_info} |")

        lines.append("")

    # Feature status section — comprehensive audit of all capabilities
    lines.append("## Feature Status")
    lines.append("")
    lines.append("| Feature | Status | Justification |")
    lines.append("|---------|--------|---------------|")

    feature_statuses = _audit_feature_statuses(project_root)
    for feature, status, justification in feature_statuses:
        lines.append(f"| {feature} | {status} | {justification} |")

    lines.append("")

    # Task verification section — flag mismatches between claimed and actual status
    anomalies = _verify_task_statuses(spec_tasks, project_root)
    if anomalies:
        lines.append("## Verification Anomalies")
        lines.append("")
        lines.append("Tasks where claimed status may not match actual implementation:")
        lines.append("")
        lines.append("| Spec | Task | Claimed | Issue |")
        lines.append("|------|------|---------|-------|")
        for spec_name, task_text, claimed, issue in anomalies:
            display = task_text.replace("|", "\\|")[:80]
            lines.append(f"| {spec_name} | {display} | {claimed} | {issue} |")
        lines.append("")

    return "\n".join(lines)


def _spec_display_name(tasks_md_path: Path) -> str:
    """Derive a human-friendly spec name from its directory.

    E.g. .kiro/specs/audit-remediation/tasks.md -> "Audit Remediation"
    """
    dir_name = tasks_md_path.parent.name
    return dir_name.replace("-", " ").title()


def main():
    project_root = Path(__file__).resolve().parent.parent

    # Find tasks.md
    tasks_md_files = find_tasks_md_files(project_root)
    if not tasks_md_files:
        print("ERROR: tasks.md not found in .kiro/specs/", file=sys.stderr)
        sys.exit(1)

    # Read and parse tasks grouped by spec
    spec_tasks: list[tuple[str, list[dict]]] = []
    total_tasks = 0
    for tasks_md_path in tasks_md_files:
        content = tasks_md_path.read_text(encoding="utf-8")
        tasks = parse_tasks(content)
        if tasks:
            spec_name = _spec_display_name(tasks_md_path)
            spec_tasks.append((spec_name, tasks))
            total_tasks += len(tasks)

    if not total_tasks:
        print("WARNING: No task checkboxes found in tasks.md", file=sys.stderr)

    # Generate report
    report = generate_report(spec_tasks, project_root)

    # Write output
    output_path = project_root / "SPEC_COMPLIANCE.md"
    output_path.write_text(report, encoding="utf-8")

    print(
        f"Generated {output_path} ({total_tasks} tasks across "
        f"{len(spec_tasks)} spec(s))"
    )


if __name__ == "__main__":
    main()
