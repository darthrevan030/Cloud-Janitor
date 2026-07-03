"""Fixture-backed cloud provider for local development and testing."""

import importlib.resources
import json
from pathlib import Path
from typing import Optional

from cloud_janitor.mcp_server.backends import CloudProvider


class FixtureProvider(CloudProvider):
    """CloudProvider backed by bundled JSON fixture files."""

    def __init__(self, fixtures_dir: Optional[Path] = None):
        self._fixtures_dir = fixtures_dir

    def _load_fixture(self, filename: str) -> dict:
        if self._fixtures_dir is not None:
            with open(self._fixtures_dir / filename) as f:
                return json.load(f)  # type: ignore[no-any-return]
        ref = importlib.resources.files("cloud_janitor.fixtures").joinpath(filename)
        with importlib.resources.as_file(ref) as path:
            with open(path) as f:
                return json.load(f)  # type: ignore[no-any-return]

    def get_cost_data(self, resource_type: Optional[str] = None, min_idle_days: int = 7) -> dict:
        """Return idle/orphaned resource data from Cost Explorer fixture."""
        if self._fixtures_dir is not None:
            fixture_path = self._fixtures_dir / "aws_cost_explorer.json"
            if not fixture_path.exists():
                return {"error": f"Fixture not found: {fixture_path}", "resources": [], "total_monthly_waste": 0.0}

        try:
            data = self._load_fixture("aws_cost_explorer.json")
        except (FileNotFoundError, OSError) as e:
            return {"error": f"Fixture not found: {e}", "resources": [], "total_monthly_waste": 0.0}

        resources = data["resources"]
        if resource_type:
            resources = [r for r in resources if r["type"] == resource_type]
        resources = [r for r in resources if r["idle_days"] >= min_idle_days]

        total_waste = sum(r["monthly_cost"] for r in resources)
        return {"resources": resources, "total_monthly_waste": round(total_waste, 2)}

    def get_security_data(self, check_type: Optional[str] = None) -> dict:
        """Return security findings from Config/Inspector fixture."""
        if self._fixtures_dir is not None:
            fixture_path = self._fixtures_dir / "aws_config_inspector.json"
            if not fixture_path.exists():
                return {"error": f"Fixture not found: {fixture_path}", "findings": [], "critical_count": 0}

        try:
            data = self._load_fixture("aws_config_inspector.json")
        except (FileNotFoundError, OSError) as e:
            return {"error": f"Fixture not found: {e}", "findings": [], "critical_count": 0}

        findings = data["findings"]
        if check_type:
            findings = [f for f in findings if f["check_type"] == check_type]

        critical = sum(1 for f in findings if f["severity"] == "CRITICAL")
        return {"findings": findings, "critical_count": critical}

    def check_dependencies(self, resource_id: str) -> dict:
        """Check resource dependency graph."""
        if self._fixtures_dir is not None:
            fixture_path = self._fixtures_dir / "aws_config_inspector.json"
            if not fixture_path.exists():
                return {"error": f"Fixture not found: {fixture_path}", "has_dependencies": False, "dependents": []}

        try:
            data = self._load_fixture("aws_config_inspector.json")
        except (FileNotFoundError, OSError) as e:
            return {"error": f"Fixture not found: {e}", "has_dependencies": False, "dependents": []}

        deps = data.get("dependencies", {}).get(resource_id, [])
        return {"has_dependencies": len(deps) > 0, "dependents": deps}
