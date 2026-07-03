"""Fixture-backed cloud provider for local development and testing."""

import json
from pathlib import Path
from typing import Optional

from mcp_server.backends import CloudProvider


class FixtureProvider(CloudProvider):
    """CloudProvider implementation backed by local JSON fixture files."""

    def __init__(self, fixtures_dir: Optional[Path] = None):
        if fixtures_dir is None:
            fixtures_dir = Path(__file__).parent.parent.parent / "fixtures"
        self.fixtures_dir = fixtures_dir

    def get_cost_data(self, resource_type: Optional[str] = None, min_idle_days: int = 7) -> dict:
        """Return idle/orphaned resource data from Cost Explorer fixture."""
        fixture_path = self.fixtures_dir / "aws_cost_explorer.json"
        if not fixture_path.exists():
            return {"error": f"Fixture not found: {fixture_path}", "resources": [], "total_monthly_waste": 0.0}

        with open(fixture_path) as f:
            data = json.load(f)

        resources = data["resources"]
        if resource_type:
            resources = [r for r in resources if r["type"] == resource_type]
        resources = [r for r in resources if r["idle_days"] >= min_idle_days]

        total_waste = sum(r["monthly_cost"] for r in resources)
        return {"resources": resources, "total_monthly_waste": round(total_waste, 2)}

    def get_security_data(self, check_type: Optional[str] = None) -> dict:
        """Return security findings from Config/Inspector fixture."""
        fixture_path = self.fixtures_dir / "aws_config_inspector.json"
        if not fixture_path.exists():
            return {"error": f"Fixture not found: {fixture_path}", "findings": [], "critical_count": 0}

        with open(fixture_path) as f:
            data = json.load(f)

        findings = data["findings"]
        if check_type:
            findings = [f for f in findings if f["check_type"] == check_type]

        critical = sum(1 for f in findings if f["severity"] == "CRITICAL")
        return {"findings": findings, "critical_count": critical}

    def check_dependencies(self, resource_id: str) -> dict:
        """Check resource dependency graph."""
        fixture_path = self.fixtures_dir / "aws_config_inspector.json"
        if not fixture_path.exists():
            return {"error": f"Fixture not found: {fixture_path}", "has_dependencies": False, "dependents": []}

        with open(fixture_path) as f:
            data = json.load(f)

        deps = data.get("dependencies", {}).get(resource_id, [])
        return {"has_dependencies": len(deps) > 0, "dependents": deps}
