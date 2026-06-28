#!/usr/bin/env python3
"""Generate SPEC_COMPLIANCE.md by parsing tasks.md and verifying artifacts exist."""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Project root is where this script lives
PROJECT_ROOT = Path(__file__).resolve().parent

# Default tasks.md path (relative to project root)
DEFAULT_TASKS_MD = ".kiro/specs/savings-tracker-localstack/tasks.md"

# Output file
OUTPUT_PATH = PROJECT_ROOT / "SPEC_COMPLIANCE.md"

# Keyword-to-file mapping table per requirements 8.3
# Each entry: (keyword_pattern, artifact_path_or_check_type)
KEYWORD_MAPPING = [
    (r"\brequirements\b", ".kiro/specs/requirements.md"),
    (r"\bdesign\b", ".kiro/specs/design.md"),
    (r"\bfixture\b", "fixtures/"),
    (r"(?i)\bmcp\b|mcp_|_mcp", "mcp_server/aws_janitor_mcp.py"),
    (r"\bFinOps\b|\bfinops\b", "agents/finops_auditor.py"),
    (r"\bSecOps\b|\bsecops\b", "agents/secops_guard.py"),
    (r"\bpre-remediation\b", ".kiro/hooks/pre-remediation.sh"),
    (r"\bpost-remediation\b", ".kiro/hooks/post-remediation.sh"),
    (r"\bRemediation\b|\bremediation\b", "agents/remediation_architect.py"),
    (r"\brollback\b", "rollbacks/"),
    (r"\bfindings_store\b", "findings_store.json"),
    (r"\bapproval\b", "__approval_check__"),
    (r"\baudit log\b", "__audit_log_check__"),
    (r"\bStreamlit\b|\bUI\b|\bapp\.py\b", "app.py"),
    (r"\bsavings\b", "savings.py"),
]


def find_tasks_md(spec_path: str | None = None) -> Path | None:
    """Find the tasks.md file.

    If spec_path is provided (command-line arg), use it directly.
    Otherwise use the default path. If that doesn't exist, search for
    the first tasks.md under .kiro/specs/.
    """
    if spec_path:
        candidate = PROJECT_ROOT / spec_path
        return candidate if candidate.exists() else None

    # Try default path first
    default = PROJECT_ROOT / DEFAULT_TASKS_MD
    if default.exists():
        return default

    # Search for any tasks.md in .kiro/specs/
    specs_dir = PROJECT_ROOT / ".kiro" / "specs"
    if specs_dir.exists():
        matches = list(specs_dir.rglob("tasks.md"))
        if matches:
            return matches[0]

    return None


def parse_tasks(content: str) -> list[dict]:
    """Parse tasks.md content and extract checkbox lines with their status.

    Returns a list of dicts with keys: number, task, status_char, status_display.
    """
    tasks = []
    # Match lines like: - [x] Some task description
    # Supports: [x] done, [ ] pending, [-] partial, [~] partial
    pattern = re.compile(r"^\s*-\s+\[([x \-~])\]\s+(.+)$")
    counter = 0

    for line in content.splitlines():
        match = pattern.match(line)
        if match:
            counter += 1
            status_char = match.group(1)
            task_text = match.group(2).strip()

            if status_char == "x":
                status_display = "✅ Done"
            elif status_char == " ":
                status_display = "❌ Pending"
            elif status_char in ("-", "~"):
                status_display = "⚠️ Partial"
            else:
                status_display = "❌ Pending"

            tasks.append({
                "number": counter,
                "task": task_text,
                "status_char": status_char,
                "status_display": status_display,
            })

    return tasks


def _check_file_or_dir(artifact_path: str) -> tuple[bool, str]:
    """Check if a file or directory exists.

    For .kiro/specs/requirements.md and .kiro/specs/design.md, also check
    subdirectories if the direct path doesn't exist.

    Returns (exists, description).
    """
    full_path = PROJECT_ROOT / artifact_path

    if full_path.exists():
        return True, f"`{artifact_path}` exists"

    # For requirements.md and design.md, also check subdirectories
    if artifact_path in (".kiro/specs/requirements.md", ".kiro/specs/design.md"):
        filename = Path(artifact_path).name
        specs_dir = PROJECT_ROOT / ".kiro" / "specs"
        if specs_dir.exists():
            matches = list(specs_dir.rglob(filename))
            if matches:
                rel = matches[0].relative_to(PROJECT_ROOT)
                return True, f"`{rel}` exists"

    return False, f"`{artifact_path}` missing"


def _check_approval_keyword() -> tuple[bool, str]:
    """Check if APPROVE keyword exists in agents/ or orchestrator.py."""
    orchestrator = PROJECT_ROOT / "orchestrator.py"
    if orchestrator.exists():
        try:
            content = orchestrator.read_text(encoding="utf-8", errors="ignore")
            if "APPROVE" in content:
                return True, "`orchestrator.py` contains APPROVE"
        except OSError:
            pass

    agents_dir = PROJECT_ROOT / "agents"
    if agents_dir.exists():
        for py_file in agents_dir.glob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                if "APPROVE" in content:
                    return True, f"`agents/{py_file.name}` contains APPROVE"
            except OSError:
                continue

    return False, "`APPROVE` keyword missing"


def _check_audit_log() -> tuple[bool, str]:
    """Check if audit.log exists or an audit log writer is in the codebase."""
    audit_log = PROJECT_ROOT / "audit.log"
    if audit_log.exists():
        return True, "`audit.log` exists"

    # Check for audit logger module
    audit_logger = PROJECT_ROOT / "agents" / "audit_logger.py"
    if audit_logger.exists():
        return True, "`agents/audit_logger.py` exists"

    # Check for audit log writer in the codebase
    agents_dir = PROJECT_ROOT / "agents"
    if agents_dir.exists():
        for py_file in agents_dir.glob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                if "audit" in content.lower() and "log" in content.lower():
                    return True, f"`agents/{py_file.name}` contains audit log writer"
            except OSError:
                continue

    orchestrator = PROJECT_ROOT / "orchestrator.py"
    if orchestrator.exists():
        try:
            content = orchestrator.read_text(encoding="utf-8", errors="ignore")
            if "audit" in content.lower() and "log" in content.lower():
                return True, "`orchestrator.py` contains audit log writer"
        except OSError:
            pass

    return False, "`audit.log` missing"


def verify_artifact(task_text: str) -> str:
    """Check if an artifact corresponding to the task exists.

    Returns a description of the artifact status:
    - "<path> exists" if found
    - "<path> missing" if not found
    - "—" if no keyword mapping matches
    """
    for keyword_pattern, artifact_path in KEYWORD_MAPPING:
        if re.search(keyword_pattern, task_text):
            # Special check for "approval" keyword
            if artifact_path == "__approval_check__":
                _, description = _check_approval_keyword()
                return description

            # Special check for "audit log" keyword
            if artifact_path == "__audit_log_check__":
                _, description = _check_audit_log()
                return description

            # Standard file/directory existence check
            _, description = _check_file_or_dir(artifact_path)
            return description

    return "—"


def generate_compliance_report(tasks: list[dict]) -> str:
    """Generate the SPEC_COMPLIANCE.md content as a 4-column Markdown table."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        "# Spec Compliance Report",
        "",
        f"Generated: {timestamp}",
        "",
        "| # | Task | Status | Artifact Verified |",
        "|---|------|--------|-------------------|",
    ]

    for task in tasks:
        # Verify artifacts for done tasks; for pending/partial show dash
        if task["status_char"] == "x":
            artifact_status = verify_artifact(task["task"])
        else:
            artifact_status = "—"

        # Clean task text for table display (remove trailing requirement refs)
        display_text = task_text.rstrip()
        # Ensure display_text is never empty/whitespace-only (breaks markdown table)
        if not display_text.strip():
            display_text = "(untitled task)"
        # Escape pipe characters to avoid breaking markdown table structure
        display_text = display_text.replace("|", "\\|")

        lines.append(
            f"| {task['number']} | {task_text} | {task['status_display']} | {artifact_status} |"
        )

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    """Main entry point."""
    # Accept optional command-line argument for tasks.md path
    spec_path = sys.argv[1] if len(sys.argv) > 1 else None

    tasks_md_path = find_tasks_md(spec_path)

    if tasks_md_path is None:
        print(
            "Error: tasks.md not found. Provide path as argument or ensure "
            f"{DEFAULT_TASKS_MD} exists.",
            file=sys.stderr,
        )
        return 1

    content = tasks_md_path.read_text(encoding="utf-8")
    tasks = parse_tasks(content)

    if not tasks:
        print("Warning: No tasks found in tasks.md", file=sys.stderr)

    report = generate_compliance_report(tasks)
    OUTPUT_PATH.write_text(report, encoding="utf-8")
    print(f"Generated {OUTPUT_PATH.name} with {len(tasks)} tasks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
