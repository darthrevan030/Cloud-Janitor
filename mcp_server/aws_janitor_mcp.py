"""
AWS Janitor MCP Server
Exposes AWS infrastructure data and Terraform validation via MCP protocol.
Backed by fixture JSON — no live AWS credentials required.

Usage:
    python mcp_server/aws_janitor_mcp.py
"""

import os
import subprocess
import tempfile
from typing import Optional

from mcp.server.fastmcp import FastMCP

from mcp_server.backends import CloudProvider, FixtureProvider, AWSProvider, GCPProvider, AzureProvider
from agents.query_interpreter import QueryInterpreter
from agents.explainer import RemediationExplainer
from agents.policy_suggester import PolicySuggester
from agents.tagger import ResourceTagger
from agents.anomaly_detector import AnomalyDetector
from agents.incident_policy_generator import IncidentPolicyGenerator

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
    return _provider.get_cost_data(resource_type, min_idle_days)


@mcp.tool()
def get_security_data(check_type: Optional[str] = None) -> dict:
    """
    Returns security finding data from Config/Inspector fixture.

    Args:
        check_type: Filter by type (security_group|encryption|public_access).

    Returns:
        {"findings": [...], "critical_count": int}
    """
    return _provider.get_security_data(check_type)


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
    return _provider.check_dependencies(resource_id)


@mcp.tool()
def interpret_query(user_query: str) -> dict:
    """
    Interprets a natural language query into structured scan parameters.

    Uses direct import of QueryInterpreter agent (no network transport).

    Args:
        user_query: Natural language query describing what to scan.

    Returns:
        ScanParameters dict with keys: resource_types, check_types,
        min_idle_days, intent_summary, confidence.
        On error: returns dict with "error" key and safe defaults.
    """
    try:
        interpreter = QueryInterpreter()
        return interpreter.interpret(user_query)
    except Exception as exc:
        return {
            "error": str(exc),
            "resource_types": [],
            "check_types": [],
            "min_idle_days": 7,
            "intent_summary": "Could not interpret query.",
            "confidence": 0.0,
        }


@mcp.tool()
def explain_remediation(
    resource_id: str,
    finding: dict,
    remediation_hcl: str,
    rollback_hcl: str,
) -> dict:
    """
    Generates plain-English explanations of a remediation plan.

    Uses direct import of RemediationExplainer agent (no network transport).

    Args:
        resource_id: The AWS resource ID being remediated.
        finding: The finding dict that triggered remediation.
        remediation_hcl: The generated Terraform HCL for the fix.
        rollback_hcl: The generated Terraform HCL for rollback.

    Returns:
        Dict with keys: risk_explanation, what_terraform_does, what_rollback_restores.
        On error: returns safe defaults with all values set to "Explanation unavailable."
    """
    try:
        explainer = RemediationExplainer()
        return explainer.explain(resource_id, finding, remediation_hcl, rollback_hcl)
    except Exception:
        return {
            "risk_explanation": "Explanation unavailable.",
            "what_terraform_does": "Explanation unavailable.",
            "what_rollback_restores": "Explanation unavailable.",
        }


@mcp.tool()
def suggest_policies(findings: list, already_checked: list) -> list:
    """
    Suggests additional policy checks based on scan findings patterns.

    Uses direct import of PolicySuggester agent (no network transport).

    Args:
        findings: List of finding dicts from the scan results.
        already_checked: List of check_type strings already run.

    Returns:
        List of suggestion dicts, each with keys: suggestion_id, title,
        rationale, query, priority.
        On error: returns empty list (safe default).
    """
    try:
        suggester = PolicySuggester()
        return suggester.suggest(findings, already_checked)
    except Exception:
        return []


@mcp.tool()
def infer_resource_context(
    resource_id: str,
    resource_name: str,
    existing_tags: dict | None = None,
) -> dict:
    """
    Infers environment, team, and owner context from resource metadata.

    Uses direct import of ResourceTagger agent (no network transport).

    Args:
        resource_id: The AWS resource ID to analyze.
        resource_name: Human-readable resource name.
        existing_tags: Already-known tags (skips inference for present fields).
            Defaults to empty dict if not provided.

    Returns:
        Dict with keys: env, team, owner, risk_level, confidence.
        On error: returns safe defaults with env="unknown", team=None,
        owner=None, risk_level="low", confidence=0.0.
    """
    try:
        tagger = ResourceTagger()
        return tagger.infer(resource_id, resource_name, existing_tags or {})
    except Exception:
        return {
            "env": "unknown",
            "team": None,
            "owner": None,
            "risk_level": "low",
            "confidence": 0.0,
        }


@mcp.tool()
def detect_anomalies(resources: list, findings: list) -> list:
    """
    Detects suspicious resources not caught by rule-based checks.

    Uses direct import of AnomalyDetector agent (no network transport).

    Args:
        resources: List of resource dicts from get_cost_data() and get_security_data().
        findings: List of already-identified finding dicts from FinOps + SecOps agents.

    Returns:
        List of anomaly dicts, each with keys: anomaly_id, resource_id,
        anomaly_type, description, severity, evidence.
        On error: returns empty list (safe default).
    """
    try:
        detector = AnomalyDetector()
        return detector.detect(resources, findings)
    except Exception:
        return []


@mcp.tool()
def policy_from_incident(incident_description: str) -> list:
    """
    Generates preventive scan policies from an incident description.

    Uses direct import of IncidentPolicyGenerator agent (no network transport).

    Args:
        incident_description: Plain text describing a past incident or near-miss.

    Returns:
        List of policy dicts, each with keys: policy_id, policy_name,
        resource_types, check_type, check_logic_description, rationale,
        query, generated_at, incident_hash, version.
        On error: returns empty list (safe default).
    """
    try:
        generator = IncidentPolicyGenerator()
        return generator.generate(incident_description)
    except Exception:
        return []


if __name__ == "__main__":
    mcp.run()
