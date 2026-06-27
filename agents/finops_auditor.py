"""
FinOps Auditor Agent

Detects financial waste: idle ElastiCache clusters and unattached EBS volumes.
Calls the MCP server's get_cost_data() to retrieve resource data from fixtures,
filters for resources idle > 30 days, classifies severity, estimates monthly cost,
and writes findings to findings_store.json.

Usage:
    python -m agents.finops_auditor
"""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Import MCP tool directly (same process, no network transport needed)
sys.path.insert(0, str(Path(__file__).parent.parent))
from mcp_server.aws_janitor_mcp import get_cost_data


# Project root for output files
PROJECT_ROOT = Path(__file__).parent.parent
FINDINGS_STORE_PATH = PROJECT_ROOT / "findings_store.json"


class FinOpsAuditor:
    """
    FinOps Auditor agent — detects idle resources representing financial waste.

    Calls MCP get_cost_data() with min_idle_days=30 to retrieve resources that have
    been idle for over 30 days, classifies severity, estimates cost, and writes
    findings to findings_store.json.
    """

    MIN_IDLE_DAYS = 30

    def __init__(self, findings_store_path: Path | None = None):
        self.findings_store_path = findings_store_path or FINDINGS_STORE_PATH

    def classify_severity(self, resource: dict) -> str:
        """
        Classify finding severity based on resource type and idle duration.

        Severity rules:
          - ElastiCache idle > 30d = HIGH
          - EBS unattached > 30d = MEDIUM
          - All others = LOW
        """
        resource_type = resource.get("type", "")
        idle_days = resource.get("idle_days", 0)

        if resource_type == "elasticache" and idle_days > self.MIN_IDLE_DAYS:
            return "HIGH"
        elif resource_type == "ebs" and idle_days > self.MIN_IDLE_DAYS:
            return "MEDIUM"
        else:
            return "LOW"

    def estimate_cost(self, resource: dict) -> float:
        """
        Estimate monthly cost for a resource.

        Uses the monthly_cost field from Cost Explorer fixture data directly.
        Returns the cost rounded to 2 decimal places.
        """
        return round(resource.get("monthly_cost", 0.0), 2)

    def _build_finding(self, resource: dict) -> dict:
        """Build a Finding dict from a raw resource record."""
        resource_type = resource["type"]
        resource_id = resource["id"]
        severity = self.classify_severity(resource)
        idle_days = resource["idle_days"]
        monthly_cost = self.estimate_cost(resource)

        # Human-readable title based on resource type
        if resource_type == "elasticache":
            title = f"Idle ElastiCache cluster ({resource_id}) — {idle_days} days unused"
        elif resource_type == "ebs":
            title = f"Unattached EBS volume ({resource_id}) — {idle_days} days detached"
        else:
            title = f"Idle {resource_type} resource ({resource_id}) — {idle_days} days"

        description = resource.get(
            "description",
            f"Resource {resource_id} idle for {idle_days} days",
        )

        # Metadata carries extra resource details for downstream agents
        metadata = {
            k: v
            for k, v in resource.items()
            if k not in ("id", "type", "idle_days", "monthly_cost", "description")
        }

        return {
            "id": str(uuid.uuid4()),
            "resource_id": resource_id,
            "resource_type": resource_type,
            "agent": "finops",
            "category": "waste",
            "severity": severity,
            "title": title,
            "description": description,
            "cost_estimate_monthly": monthly_cost,
            "idle_days": idle_days,
            "metadata": metadata,
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }

    def scan(self) -> list[dict]:
        """
        Execute the FinOps audit scan.

        1. Calls MCP get_cost_data() with min_idle_days=30
        2. For each flagged resource, builds a Finding dict
        3. Writes findings_store.json

        Returns:
            List of Finding dicts for resources idle > 30 days.
        """
        # Call MCP tool with min_idle_days=30 (the tool filters server-side)
        cost_data = get_cost_data(resource_type=None, min_idle_days=self.MIN_IDLE_DAYS)

        if "error" in cost_data:
            print(f"[FinOps Auditor] ERROR: {cost_data['error']}", file=sys.stderr)
            return []

        resources = cost_data.get("resources", [])

        # Build findings for each resource returned (already filtered by MCP)
        findings = [self._build_finding(r) for r in resources]

        # Write findings store
        self._write_findings_store(findings)

        return findings

    def _write_findings_store(self, findings: list[dict]) -> dict:
        """
        Write findings_store.json with FinOps findings.

        Top-level schema:
          scan_id, started_at, completed_at, findings[], summary{}
        """
        now = datetime.now(timezone.utc).isoformat()

        # Calculate summary
        by_severity: dict[str, int] = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
        for f in findings:
            sev = f.get("severity", "LOW")
            if sev in by_severity:
                by_severity[sev] += 1

        total_monthly_waste = round(
            sum(f.get("cost_estimate_monthly", 0) for f in findings), 2
        )

        store = {
            "scan_id": str(uuid.uuid4()),
            "started_at": now,
            "completed_at": now,
            "findings": findings,
            "summary": {
                "total": len(findings),
                "by_severity": by_severity,
                "by_agent": {"finops": len(findings), "secops": 0},
                "total_monthly_waste": total_monthly_waste,
            },
        }

        self.findings_store_path.write_text(json.dumps(store, indent=2))
        return store


def main() -> None:
    """Run the FinOps Auditor and write findings_store.json."""
    print("[FinOps Auditor] Starting audit...")

    auditor = FinOpsAuditor()
    findings = auditor.scan()

    print(
        f"[FinOps Auditor] Found {len(findings)} finding(s) exceeding "
        f"{FinOpsAuditor.MIN_IDLE_DAYS}-day threshold"
    )
    for f in findings:
        print(f"  • [{f['severity']}] {f['title']} — ${f['cost_estimate_monthly']}/mo")

    # Read back to show summary
    store = json.loads(auditor.findings_store_path.read_text())
    print(f"[FinOps Auditor] Wrote findings_store.json (scan_id={store['scan_id']})")
    print(f"[FinOps Auditor] Summary: {store['summary']}")


if __name__ == "__main__":
    main()
