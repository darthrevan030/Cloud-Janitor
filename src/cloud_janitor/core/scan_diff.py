"""Severity escalation diff for scheduled scan alerting."""

_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def diff_high_severity_findings(previous_snapshot: dict[str, str], current_findings: list[dict]) -> list[dict]:
    """Return findings that are newly HIGH/CRITICAL or have escalated since last run."""
    escalated = []
    for finding in current_findings:
        severity = finding.get("severity", "LOW")
        if _RANK.get(severity, 0) < _RANK["HIGH"]:
            continue
        prior = previous_snapshot.get(finding["resource_id"])
        if prior is None or _RANK.get(prior, 0) < _RANK.get(severity, 0):
            escalated.append(finding)
    return escalated
