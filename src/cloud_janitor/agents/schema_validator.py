"""Schema validation for findings_store.json.

Validates the findings store against the expected schema, checking:
- Required top-level fields (scan_id, started_at, completed_at, findings, summary)
- Each finding has all required fields with valid values
- FinOps findings require cost_estimate_monthly and idle_days
- SecOps findings require type-specific metadata fields
- Severity, resource_type, agent, and category enums are correct
- Summary counts match actual findings data

Usage:
    from agents.schema_validator import validate_findings_store, validate_finding

    # Validate entire store
    valid, errors = validate_findings_store(data)

    # Validate a single finding
    valid, errors = validate_finding(finding)
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

VALID_SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
VALID_RESOURCE_TYPES = {"elasticache", "ebs", "security_group"}
VALID_AGENTS = {"finops", "secops"}
VALID_CATEGORIES = {"waste", "security"}

# Also accept aws_ prefixed versions of resource types
AWS_PREFIXED_RESOURCE_TYPES = {f"aws_{rt}" for rt in VALID_RESOURCE_TYPES}
ALL_VALID_RESOURCE_TYPES = VALID_RESOURCE_TYPES | AWS_PREFIXED_RESOURCE_TYPES


def _is_iso8601(value: str) -> bool:
    """Check if a string is a valid ISO-8601 timestamp."""
    try:
        datetime.fromisoformat(value)
        return True
    except (ValueError, TypeError):
        return False


def validate_finding(finding: dict) -> tuple[bool, list[str]]:
    """Validate a single finding dict against the expected schema.

    Args:
        finding: A finding dictionary to validate.

    Returns:
        A tuple of (is_valid, error_messages).
        Returns (True, []) if valid.
        Returns (False, [list of error messages]) if invalid.
    """
    errors: list[str] = []

    if not isinstance(finding, dict):
        return (False, ["Finding must be a dict"])

    # Required fields for all findings
    required_fields = [
        "id", "resource_id", "resource_type", "agent", "category",
        "severity", "title", "description", "detected_at",
    ]

    for field_name in required_fields:
        if field_name not in finding:
            errors.append(f"Missing required field '{field_name}'")

    # Validate enum fields
    if "severity" in finding:
        if finding["severity"] not in VALID_SEVERITIES:
            errors.append(
                f"'severity' must be one of {sorted(VALID_SEVERITIES)}, "
                f"got '{finding['severity']}'"
            )

    if "resource_type" in finding:
        if finding["resource_type"] not in ALL_VALID_RESOURCE_TYPES:
            errors.append(
                f"'resource_type' must be one of {sorted(ALL_VALID_RESOURCE_TYPES)}, "
                f"got '{finding['resource_type']}'"
            )

    if "agent" in finding:
        if finding["agent"] not in VALID_AGENTS:
            errors.append(
                f"'agent' must be one of {sorted(VALID_AGENTS)}, "
                f"got '{finding['agent']}'"
            )

    if "category" in finding:
        if finding["category"] not in VALID_CATEGORIES:
            errors.append(
                f"'category' must be one of {sorted(VALID_CATEGORIES)}, "
                f"got '{finding['category']}'"
            )

    if "detected_at" in finding:
        if not isinstance(finding["detected_at"], str) or not _is_iso8601(finding["detected_at"]):
            errors.append("'detected_at' must be a valid ISO-8601 timestamp")

    # FinOps-specific required fields
    if finding.get("agent") == "finops":
        if "cost_estimate_monthly" not in finding:
            errors.append("'cost_estimate_monthly' is required for finops findings")
        if "idle_days" not in finding:
            errors.append("'idle_days' is required for finops findings")

    # Validate cost_estimate_monthly type if present
    if "cost_estimate_monthly" in finding:
        if not isinstance(finding["cost_estimate_monthly"], (int, float)):
            errors.append("'cost_estimate_monthly' must be a number")

    # Validate idle_days type if present
    if "idle_days" in finding and finding["idle_days"] is not None:
        if not isinstance(finding["idle_days"], int):
            errors.append("'idle_days' must be an integer or null")

    # SecOps metadata validation
    if finding.get("agent") == "secops":
        _validate_secops_metadata(finding, errors)

    return (len(errors) == 0, errors)


def _validate_secops_metadata(finding: dict, errors: list[str]) -> None:
    """Validate SecOps-specific metadata fields based on resource type."""
    resource_type = finding.get("resource_type", "")
    metadata = finding.get("metadata")

    if metadata is None:
        # metadata is not strictly required at the top level, but secops needs it
        errors.append("'metadata' is required for secops findings")
        return

    if not isinstance(metadata, dict):
        errors.append("'metadata' must be a dict")
        return

    # Normalize resource_type (strip aws_ prefix for checking)
    normalized_type = resource_type.replace("aws_", "", 1) if resource_type.startswith("aws_") else resource_type

    if normalized_type == "security_group":
        # Security group findings must have port and cidr
        if "port" not in metadata:
            errors.append("secops security_group finding metadata must include 'port'")
        if "cidr" not in metadata:
            errors.append("secops security_group finding metadata must include 'cidr'")

    elif normalized_type in ("elasticache", "ebs"):
        # Encryption findings must have encryption_at_rest, current_state, required_state
        if "encryption_at_rest" not in metadata:
            errors.append(
                f"secops {normalized_type} finding metadata must include 'encryption_at_rest'"
            )
        if "current_state" not in metadata:
            errors.append(
                f"secops {normalized_type} finding metadata must include 'current_state'"
            )
        if "required_state" not in metadata:
            errors.append(
                f"secops {normalized_type} finding metadata must include 'required_state'"
            )


def validate_findings_store(data: dict) -> tuple[bool, list[str]]:
    """Validate a findings_store.json data dict against the expected schema.

    Args:
        data: The parsed findings_store.json content as a dict.

    Returns:
        A tuple of (is_valid, error_messages).
        Returns (True, []) if valid.
        Returns (False, [list of error messages]) if invalid.
    """
    errors: list[str] = []

    if not isinstance(data, dict):
        return (False, ["Root element must be a JSON object"])

    # Required top-level fields
    required_top_level = ["scan_id", "started_at", "completed_at", "findings", "summary"]
    for field_name in required_top_level:
        if field_name not in data:
            errors.append(f"Missing required top-level field: '{field_name}'")

    # Validate scan_id
    if "scan_id" in data and not isinstance(data["scan_id"], str):
        errors.append("'scan_id' must be a string")

    # Validate started_at
    if "started_at" in data:
        if not isinstance(data["started_at"], str) or not _is_iso8601(data["started_at"]):
            errors.append("'started_at' must be a valid ISO-8601 timestamp")

    # Validate completed_at (can be null for in-progress scans)
    if "completed_at" in data and data["completed_at"] is not None:
        if not isinstance(data["completed_at"], str) or not _is_iso8601(data["completed_at"]):
            errors.append("'completed_at' must be a valid ISO-8601 timestamp or null")

    # Validate findings array
    if "findings" in data:
        if not isinstance(data["findings"], list):
            errors.append("'findings' must be a list")
        else:
            for i, finding in enumerate(data["findings"]):
                if not isinstance(finding, dict):
                    errors.append(f"Finding[{i}]: must be a JSON object")
                else:
                    valid, finding_errors = validate_finding(finding)
                    for err in finding_errors:
                        errors.append(f"Finding[{i}]: {err}")

    # Validate summary
    if "summary" in data:
        if not isinstance(data["summary"], dict):
            errors.append("'summary' must be a dict")
        else:
            findings = data.get("findings", [])
            if isinstance(findings, list):
                _validate_summary(data["summary"], findings, errors)

    return (len(errors) == 0, errors)


def _validate_summary(summary: dict, findings: list[dict], errors: list[str]) -> None:
    """Validate summary matches actual findings data."""
    # Required summary fields
    required_summary_fields = ["total", "by_severity", "by_agent", "total_monthly_waste"]
    for field_name in required_summary_fields:
        if field_name not in summary:
            errors.append(f"summary: missing required field '{field_name}'")

    # Validate total count
    if "total" in summary:
        if summary["total"] != len(findings):
            errors.append(
                f"summary.total ({summary['total']}) does not match "
                f"len(findings) ({len(findings)})"
            )

    # Validate by_severity counts
    if "by_severity" in summary:
        if not isinstance(summary["by_severity"], dict):
            errors.append("summary.by_severity must be a dict")
        else:
            actual_by_severity = {s: 0 for s in VALID_SEVERITIES}
            for finding in findings:
                sev = finding.get("severity")
                if sev in VALID_SEVERITIES:
                    actual_by_severity[sev] += 1

            for severity in VALID_SEVERITIES:
                expected = actual_by_severity[severity]
                actual = summary["by_severity"].get(severity, 0)
                if actual != expected:
                    errors.append(
                        f"summary.by_severity.{severity}: expected {expected}, got {actual}"
                    )

    # Validate by_agent counts
    if "by_agent" in summary:
        if not isinstance(summary["by_agent"], dict):
            errors.append("summary.by_agent must be a dict")
        else:
            actual_by_agent = {a: 0 for a in VALID_AGENTS}
            for finding in findings:
                agent = finding.get("agent")
                if agent in VALID_AGENTS:
                    actual_by_agent[agent] += 1

            for agent in VALID_AGENTS:
                expected = actual_by_agent[agent]
                actual = summary["by_agent"].get(agent, 0)
                if actual != expected:
                    errors.append(
                        f"summary.by_agent.{agent}: expected {expected}, got {actual}"
                    )

    # Validate total_monthly_waste
    if "total_monthly_waste" in summary:
        if not isinstance(summary["total_monthly_waste"], (int, float)):
            errors.append("summary.total_monthly_waste must be a number")


def main() -> None:
    """CLI entry point — validate findings_store.json and print results."""
    import json

    path = sys.argv[1] if len(sys.argv) > 1 else "findings_store.json"
    filepath = Path(path)

    if not filepath.exists():
        print(f"✗ File not found: {path}")
        sys.exit(1)

    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"✗ Invalid JSON: {e}")
        sys.exit(1)

    valid, errors = validate_findings_store(data)

    if valid:
        print(f"✓ {path} is valid")
    else:
        print(f"✗ {path} has {len(errors)} validation error(s):")
        for error in errors:
            print(f"  - {error}")

    sys.exit(0 if valid else 1)


if __name__ == "__main__":
    main()
