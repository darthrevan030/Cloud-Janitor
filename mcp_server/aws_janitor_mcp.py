"""
AWS Janitor MCP Server
Exposes AWS infrastructure data and Terraform validation via MCP protocol.
Backed by fixture JSON — no live AWS credentials required.

Usage:
    python mcp_server/aws_janitor_mcp.py
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from mcp_server.backends import CloudProvider, FixtureProvider, AWSProvider, GCPProvider, AzureProvider

# Fixtures live at project root / fixtures/
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

TF_CMD = os.environ.get("TF_CMD", "tflocal")

# Create the MCP server instance
mcp = FastMCP("aws-janitor")

# Provider registry mapping backend names to provider classes
PROVIDER_REGISTRY: dict[str, type[CloudProvider]] = {
    "fixture": FixtureProvider,
    "aws": AWSProvider,
    "gcp": GCPProvider,
    "azure": AzureProvider,
}


def _load_provider() -> CloudProvider:
    """Instantiate the provider based on JANITOR_BACKEND env var."""
    backend = os.environ.get("JANITOR_BACKEND", "fixture")
    if backend not in PROVIDER_REGISTRY:
        valid = ", ".join(sorted(PROVIDER_REGISTRY.keys()))
        raise ValueError(
            f"Invalid JANITOR_BACKEND={backend!r}. Valid options: {valid}"
        )
    return PROVIDER_REGISTRY[backend]()


_provider: CloudProvider = _load_provider()


@mcp.tool()
def get_cost_data(resource_type: Optional[str] = None, min_idle_days: int = 7) -> dict:
    """
    Returns idle/orphaned resource data from Cost Explorer fixture.

    Args:
        resource_type: Filter by type (elasticache|ebs|ec2). None = all.
        min_idle_days: Minimum idle days to include in results.

    Returns:
        {"resources": [...], "total_monthly_waste": float}
    """
    fixture_path = FIXTURES_DIR / "aws_cost_explorer.json"
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


@mcp.tool()
def get_security_data(check_type: Optional[str] = None) -> dict:
    """
    Returns security finding data from Config/Inspector fixture.

    Args:
        check_type: Filter by type (security_group|encryption|public_access).

    Returns:
        {"findings": [...], "critical_count": int}
    """
    fixture_path = FIXTURES_DIR / "aws_config_inspector.json"
    if not fixture_path.exists():
        return {"error": f"Fixture not found: {fixture_path}", "findings": [], "critical_count": 0}

    with open(fixture_path) as f:
        data = json.load(f)

    findings = data["findings"]
    if check_type:
        findings = [f for f in findings if f["check_type"] == check_type]

    critical = sum(1 for f in findings if f["severity"] == "CRITICAL")
    return {"findings": findings, "critical_count": critical}


@mcp.tool()
def validate_hcl(hcl_content: str) -> dict:
    """
    Validates Terraform HCL by writing to a temp file and running tflocal validate.

    Args:
        hcl_content: Raw HCL/Terraform configuration string to validate.

    Returns:
        {"valid": bool, "error": str | None}
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        hcl_path = os.path.join(tmpdir, "main.tf")
        with open(hcl_path, "w") as f:
            f.write(hcl_content)

        # terraform init is required before validate in most cases
        init_result = subprocess.run(
            [TF_CMD, "init", "-backend=false"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
        )

        if init_result.returncode != 0:
            return {"valid": False, "error": f"{TF_CMD} init failed: {init_result.stderr.strip()}"}

        result = subprocess.run(
            [TF_CMD, "validate"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            return {"valid": True, "error": None}
        else:
            return {"valid": False, "error": result.stderr.strip()}


@mcp.tool()
def check_dependencies(resource_id: str) -> dict:
    """
    Checks whether any other resource references the target resource.
    Backed by fixture data — returns dependency list.

    Args:
        resource_id: The AWS resource ID to check dependencies for.

    Returns:
        {"has_dependencies": bool, "dependents": [...]}
    """
    fixture_path = FIXTURES_DIR / "aws_config_inspector.json"
    if not fixture_path.exists():
        return {"error": f"Fixture not found: {fixture_path}", "has_dependencies": False, "dependents": []}

    with open(fixture_path) as f:
        data = json.load(f)

    deps = data.get("dependencies", {}).get(resource_id, [])
    return {"has_dependencies": len(deps) > 0, "dependents": deps}


if __name__ == "__main__":
    mcp.run()
