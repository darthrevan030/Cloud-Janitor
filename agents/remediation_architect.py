"""
Remediation Architect Agent

Reads findings_store.json (populated by FinOps + SecOps agents), runs dependency
checks via MCP, generates remediation HCL + rollback HCL side by side, and writes
output files.

Must run AFTER FinOps Auditor and SecOps Guard — requires findings_store.json
to contain entries from both prior agents.

Usage:
    python -m agents.remediation_architect
"""

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Import MCP tools directly (same process, no network transport needed)
sys.path.insert(0, str(Path(__file__).parent.parent))
from mcp_server.aws_janitor_mcp import check_dependencies, validate_hcl


# Project root for output files
PROJECT_ROOT = Path(__file__).parent.parent
FINDINGS_STORE_PATH = PROJECT_ROOT / "findings_store.json"
OUTPUT_DIR = PROJECT_ROOT / "output"
ROLLBACKS_DIR = PROJECT_ROOT / "rollbacks"


def _sanitize_id(resource_id: str) -> str:
    """
    Sanitize a resource ID for use in Terraform resource names.

    Terraform resource names only support alphanumeric and underscores.
    Replaces dashes, dots, and other non-alphanumeric chars with underscores.
    """
    return re.sub(r"[^a-zA-Z0-9]", "_", resource_id)


@dataclass
class DependencyReport:
    """Result of a dependency check for a resource."""

    resource_id: str
    has_dependencies: bool
    dependencies: list[str] = field(default_factory=list)
    recommendation: str = ""
    checked_at: str = ""


@dataclass
class RemediationPlan:
    """Plan for a single finding — includes remediation HCL, rollback HCL, or blocked status."""

    resource_id: str
    finding: dict
    blocked: bool = False
    block_reason: str = ""
    dependency_report: Optional[DependencyReport] = None
    remediation_hcl: Optional[str] = None
    rollback_hcl: Optional[str] = None


class RemediationArchitect:
    """
    Remediation Architect agent — generates Terraform HCL for remediation and rollback.

    Workflow:
      1. Read findings_store.json (FinOps + SecOps findings)
      2. For each finding, check dependencies via MCP check_dependencies()
      3. If dependencies found: block remediation, produce warning
      4. If no dependencies: generate remediation HCL AND rollback HCL
      5. Write output/remediation.tf and rollbacks/<resource_id>.tf
    """

    def __init__(
        self,
        findings_store_path: Path | None = None,
        output_dir: Path | None = None,
        rollbacks_dir: Path | None = None,
    ):
        self.findings_store_path = findings_store_path or FINDINGS_STORE_PATH
        self.output_dir = output_dir or OUTPUT_DIR
        self.rollbacks_dir = rollbacks_dir or ROLLBACKS_DIR

    def check_resource_dependencies(self, finding: dict) -> DependencyReport:
        """
        Check dependencies for a resource via MCP check_dependencies() tool.

        Args:
            finding: A finding dict with at least 'resource_id'.

        Returns:
            DependencyReport with dependency status and recommendation.
        """
        resource_id = finding["resource_id"]
        result = check_dependencies(resource_id=resource_id)

        has_deps = result.get("has_dependencies", False)
        dependents = result.get("dependents", [])
        checked_at = datetime.now(timezone.utc).isoformat()

        if has_deps:
            recommendation = (
                f"BLOCKED: Resource {resource_id} has {len(dependents)} dependent(s): "
                f"{', '.join(dependents)}. Manual review required before remediation."
            )
        else:
            recommendation = f"CLEAR: No dependencies found for {resource_id}. Safe to remediate."

        return DependencyReport(
            resource_id=resource_id,
            has_dependencies=has_deps,
            dependencies=dependents,
            recommendation=recommendation,
            checked_at=checked_at,
        )

    def generate_remediation(self, finding: dict) -> str:
        """
        Generate remediation HCL for a finding based on resource type and category.

        Args:
            finding: A finding dict with resource_type, category, and metadata.

        Returns:
            HCL string for remediation.
        """
        resource_type = finding.get("resource_type", "")
        category = finding.get("category", "")

        # Normalize resource_type (strip aws_ prefix)
        normalized_type = resource_type.replace("aws_", "", 1) if resource_type.startswith("aws_") else resource_type

        if normalized_type == "ebs" and category == "waste":
            return self._remediation_ebs_waste(finding)
        elif normalized_type == "ebs" and category == "security":
            return self._remediation_ebs_encryption(finding)
        elif normalized_type == "security_group" and category == "security":
            return self._remediation_security_group(finding)
        elif normalized_type == "elasticache" and category == "waste":
            return self._remediation_elasticache_waste(finding)
        elif normalized_type == "elasticache" and category == "security":
            return self._remediation_elasticache_encryption(finding)
        else:
            return self._remediation_generic(finding)

    def generate_rollback(self, finding: dict) -> str:
        """
        Generate rollback HCL for a finding based on resource type and category.

        Args:
            finding: A finding dict with resource_type, category, and metadata.

        Returns:
            HCL string for rollback.
        """
        resource_type = finding.get("resource_type", "")
        category = finding.get("category", "")

        # Normalize resource_type (strip aws_ prefix)
        normalized_type = resource_type.replace("aws_", "", 1) if resource_type.startswith("aws_") else resource_type

        if normalized_type == "ebs" and category == "waste":
            return self._rollback_ebs_waste(finding)
        elif normalized_type == "ebs" and category == "security":
            return self._rollback_ebs_encryption(finding)
        elif normalized_type == "security_group" and category == "security":
            return self._rollback_security_group(finding)
        elif normalized_type == "elasticache" and category == "waste":
            return self._rollback_elasticache_waste(finding)
        elif normalized_type == "elasticache" and category == "security":
            return self._rollback_elasticache_encryption(finding)
        else:
            return self._rollback_generic(finding)

    def plan(self, findings: list[dict] | None = None) -> list[RemediationPlan]:
        """
        Main planning method. Reads findings, checks dependencies, generates HCL.

        Args:
            findings: Optional list of findings. If None, reads from findings_store.json.

        Returns:
            List of RemediationPlan objects, one per finding.
        """
        if findings is None:
            findings = self._load_findings()

        # Ensure output directories exist
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.rollbacks_dir.mkdir(parents=True, exist_ok=True)

        plans: list[RemediationPlan] = []

        for finding in findings:
            resource_id = finding["resource_id"]

            # Step 1: Check dependencies
            dep_report = self.check_resource_dependencies(finding)

            if dep_report.has_dependencies:
                # Blocked — produce warning, no HCL generated
                plan = RemediationPlan(
                    resource_id=resource_id,
                    finding=finding,
                    blocked=True,
                    block_reason=dep_report.recommendation,
                    dependency_report=dep_report,
                    remediation_hcl=None,
                    rollback_hcl=None,
                )
                plans.append(plan)
                continue

            # Step 2: Generate remediation + rollback HCL (side by side)
            remediation_hcl = self.generate_remediation(finding)
            rollback_hcl = self.generate_rollback(finding)

            plan = RemediationPlan(
                resource_id=resource_id,
                finding=finding,
                blocked=False,
                dependency_report=dep_report,
                remediation_hcl=remediation_hcl,
                rollback_hcl=rollback_hcl,
            )
            plans.append(plan)

            # Step 3: Write individual rollback file
            rollback_path = self.rollbacks_dir / f"{resource_id}.tf"
            rollback_path.write_text(rollback_hcl)

        # Step 4: Write combined remediation file
        remediation_parts = [p.remediation_hcl for p in plans if p.remediation_hcl]
        if remediation_parts:
            combined_remediation = "\n\n".join(remediation_parts)
            remediation_path = self.output_dir / "remediation.tf"
            remediation_path.write_text(combined_remediation)

        return plans

    # ──────────────────────────────────────────────────────────────────────
    # Private: HCL generation per resource type
    # ──────────────────────────────────────────────────────────────────────

    def _tags_block(self, resource_id: str) -> str:
        """Generate the standard tags block required on every resource."""
        return (
            "  tags = {\n"
            '    ManagedBy    = "Kiro-Janitor"\n'
            "    Environment  = var.environment\n"
            "    RemediatedAt = timestamp()\n"
            f'    RollbackRef  = "rollbacks/{resource_id}.tf"\n'
            "  }"
        )

    def _remediation_ebs_waste(self, finding: dict) -> str:
        """EBS volume waste: snapshot first, then schedule destroy."""
        resource_id = finding["resource_id"]
        safe_id = _sanitize_id(resource_id)
        metadata = finding.get("metadata", {})

        return (
            f'# Remediation: EBS volume {resource_id} — snapshot then destroy\n'
            f'resource "aws_ebs_snapshot" "pre_remediation_{safe_id}" {{\n'
            f'  volume_id   = "{resource_id}"\n'
            f'  description = "Pre-remediation snapshot for {resource_id}"\n'
            f'\n'
            f'{self._tags_block(resource_id)}\n'
            f'}}\n'
            f'\n'
            f'resource "null_resource" "destroy_{safe_id}" {{\n'
            f'  depends_on = [aws_ebs_snapshot.pre_remediation_{safe_id}]\n'
            f'\n'
            f'  provisioner "local-exec" {{\n'
            f'    command = "aws ec2 delete-volume --volume-id {resource_id}"\n'
            f'  }}\n'
            f'}}'
        )

    def _rollback_ebs_waste(self, finding: dict) -> str:
        """EBS volume rollback: restore from snapshot."""
        resource_id = finding["resource_id"]
        safe_id = _sanitize_id(resource_id)
        metadata = finding.get("metadata", {})
        az = metadata.get("availability_zone", "us-east-1a")
        volume_type = metadata.get("volume_type", "gp3")
        size_gb = metadata.get("size_gb", 100)

        return (
            f'# Rollback: Restore EBS volume {resource_id} from snapshot\n'
            f'resource "aws_ebs_volume" "restore_{safe_id}" {{\n'
            f'  availability_zone = "{az}"\n'
            f'  snapshot_id       = aws_ebs_snapshot.pre_remediation_{safe_id}.id\n'
            f'  size              = {size_gb}\n'
            f'  type              = "{volume_type}"\n'
            f'\n'
            f'{self._tags_block(resource_id)}\n'
            f'}}'
        )

    def _remediation_security_group(self, finding: dict) -> str:
        """Security group: replace open rule with VPC-only CIDR."""
        resource_id = finding["resource_id"]
        safe_id = _sanitize_id(resource_id)
        metadata = finding.get("metadata", {})
        port = metadata.get("port", 0)

        return (
            f'# Remediation: Narrow {resource_id} port {port} to VPC-only\n'
            f'data "aws_vpc" "current" {{\n'
            f'  default = true\n'
            f'}}\n'
            f'\n'
            f'resource "aws_security_group_rule" "remediate_{safe_id}_port_{port}" {{\n'
            f'  type              = "ingress"\n'
            f'  from_port         = {port}\n'
            f'  to_port           = {port}\n'
            f'  protocol          = "tcp"\n'
            f'  cidr_blocks       = [data.aws_vpc.current.cidr_block]\n'
            f'  security_group_id = "{resource_id}"\n'
            f'  description       = "Kiro-Janitor: Narrowed from 0.0.0.0/0 to VPC CIDR"\n'
            f'}}'
        )

    def _rollback_security_group(self, finding: dict) -> str:
        """Security group rollback: restore original 0.0.0.0/0 rule."""
        resource_id = finding["resource_id"]
        safe_id = _sanitize_id(resource_id)
        metadata = finding.get("metadata", {})
        port = metadata.get("port", 0)

        return (
            f'# Rollback: Restore original 0.0.0.0/0 rule on {resource_id} port {port}\n'
            f'resource "aws_security_group_rule" "restore_{safe_id}_port_{port}" {{\n'
            f'  type              = "ingress"\n'
            f'  from_port         = {port}\n'
            f'  to_port           = {port}\n'
            f'  protocol          = "tcp"\n'
            f'  cidr_blocks       = ["0.0.0.0/0"]\n'
            f'  security_group_id = "{resource_id}"\n'
            f'  description       = "Kiro-Janitor: Rollback — restored original open rule"\n'
            f'\n'
            f'{self._tags_block(resource_id)}\n'
            f'}}'
        )

    def _remediation_elasticache_waste(self, finding: dict) -> str:
        """ElastiCache waste: snapshot then delete."""
        resource_id = finding["resource_id"]
        safe_id = _sanitize_id(resource_id)
        metadata = finding.get("metadata", {})

        return (
            f'# Remediation: ElastiCache {resource_id} — snapshot then delete\n'
            f'resource "aws_elasticache_snapshot" "pre_remediation_{safe_id}" {{\n'
            f'  cluster_id       = "{resource_id}"\n'
            f'  snapshot_name    = "pre-remediation-{resource_id}"\n'
            f'}}\n'
            f'\n'
            f'resource "null_resource" "destroy_{safe_id}" {{\n'
            f'  depends_on = [aws_elasticache_snapshot.pre_remediation_{safe_id}]\n'
            f'\n'
            f'  provisioner "local-exec" {{\n'
            f'    command = "aws elasticache delete-cache-cluster --cache-cluster-id {resource_id} --final-snapshot-identifier final-{resource_id}"\n'
            f'  }}\n'
            f'\n'
            f'{self._tags_block(resource_id)}\n'
            f'}}'
        )

    def _rollback_elasticache_waste(self, finding: dict) -> str:
        """ElastiCache rollback: restore from snapshot with same config."""
        resource_id = finding["resource_id"]
        safe_id = _sanitize_id(resource_id)
        metadata = finding.get("metadata", {})
        engine = metadata.get("engine", "redis")
        engine_version = metadata.get("engine_version", "7.0.7")
        node_type = metadata.get("instance_type", "cache.t3.medium")
        num_nodes = metadata.get("num_cache_nodes", 1)

        return (
            f'# Rollback: Restore ElastiCache {resource_id} from snapshot\n'
            f'resource "aws_elasticache_cluster" "restore_{safe_id}" {{\n'
            f'  cluster_id           = "{resource_id}-restored"\n'
            f'  engine               = "{engine}"\n'
            f'  engine_version       = "{engine_version}"\n'
            f'  node_type            = "{node_type}"\n'
            f'  num_cache_nodes      = {num_nodes}\n'
            f'  snapshot_name        = aws_elasticache_snapshot.pre_remediation_{safe_id}.snapshot_name\n'
            f'\n'
            f'{self._tags_block(resource_id)}\n'
            f'}}'
        )

    def _remediation_elasticache_encryption(self, finding: dict) -> str:
        """ElastiCache encryption: document finding only (cannot enable in-place)."""
        resource_id = finding["resource_id"]

        return (
            f'# Remediation: ElastiCache {resource_id} — encryption at rest\n'
            f'# NOTE: Cannot enable encryption in-place on existing cluster.\n'
            f'# This finding is documented for manual review.\n'
            f'# Recommended: Create new cluster with encryption_at_rest_enabled = true,\n'
            f'# migrate data, then decommission the old cluster.\n'
            f'#\n'
            f'# resource "aws_elasticache_cluster" "encrypted_{_sanitize_id(resource_id)}" {{\n'
            f'#   cluster_id                 = "{resource_id}-encrypted"\n'
            f'#   engine                     = "redis"\n'
            f'#   at_rest_encryption_enabled = true\n'
            f'#   transit_encryption_enabled = true\n'
            f'# }}'
        )

    def _rollback_elasticache_encryption(self, finding: dict) -> str:
        """ElastiCache encryption rollback: N/A (in-place not possible)."""
        resource_id = finding["resource_id"]

        return (
            f'# Rollback: ElastiCache {resource_id} — encryption\n'
            f'# No rollback needed — encryption cannot be applied in-place.\n'
            f'# Original cluster remains unchanged.'
        )

    def _remediation_ebs_encryption(self, finding: dict) -> str:
        """EBS encryption: document finding only (cannot enable in-place on existing volume)."""
        resource_id = finding["resource_id"]

        return (
            f'# Remediation: EBS volume {resource_id} — encryption at rest\n'
            f'# NOTE: Cannot enable encryption in-place on existing EBS volume.\n'
            f'# This finding is documented for manual review.\n'
            f'# Recommended: Create encrypted snapshot copy, then restore to new\n'
            f'# encrypted volume and swap the attachment.\n'
            f'#\n'
            f'# resource "aws_ebs_snapshot_copy" "encrypted_{_sanitize_id(resource_id)}" {{\n'
            f'#   source_snapshot_id = "<original-snapshot-id>"\n'
            f'#   encrypted         = true\n'
            f'# }}'
        )

    def _rollback_ebs_encryption(self, finding: dict) -> str:
        """EBS encryption rollback: N/A (in-place not possible)."""
        resource_id = finding["resource_id"]

        return (
            f'# Rollback: EBS volume {resource_id} — encryption\n'
            f'# No rollback needed — encryption cannot be applied in-place.\n'
            f'# Original volume remains unchanged.'
        )

    def _remediation_generic(self, finding: dict) -> str:
        """Generic remediation placeholder for unhandled resource types."""
        resource_id = finding["resource_id"]
        resource_type = finding.get("resource_type", "unknown")
        category = finding.get("category", "unknown")

        return (
            f'# Remediation: {resource_type} {resource_id} ({category})\n'
            f'# NOTE: No automated remediation template for this resource type.\n'
            f'# Manual review required.'
        )

    def _rollback_generic(self, finding: dict) -> str:
        """Generic rollback placeholder for unhandled resource types."""
        resource_id = finding["resource_id"]
        resource_type = finding.get("resource_type", "unknown")

        return (
            f'# Rollback: {resource_type} {resource_id}\n'
            f'# NOTE: No automated rollback template for this resource type.\n'
            f'# Manual review required.'
        )

    # ──────────────────────────────────────────────────────────────────────
    # Private: Data loading
    # ──────────────────────────────────────────────────────────────────────

    def _load_findings(self) -> list[dict]:
        """Load findings from findings_store.json."""
        if not self.findings_store_path.exists():
            print(
                f"[Remediation Architect] ERROR: findings_store.json not found at "
                f"{self.findings_store_path}",
                file=sys.stderr,
            )
            return []

        try:
            with open(self.findings_store_path) as f:
                data = json.load(f)
            return data.get("findings", [])
        except (json.JSONDecodeError, IOError) as e:
            print(f"[Remediation Architect] ERROR: Could not read findings_store.json: {e}", file=sys.stderr)
            return []


def main() -> None:
    """Run the Remediation Architect and generate HCL output."""
    print("[Remediation Architect] Starting remediation planning...")

    architect = RemediationArchitect()
    plans = architect.plan()

    blocked = [p for p in plans if p.blocked]
    remediated = [p for p in plans if not p.blocked]

    print(f"[Remediation Architect] Processed {len(plans)} finding(s)")
    print(f"[Remediation Architect] Remediated: {len(remediated)} resource(s)")
    print(f"[Remediation Architect] Blocked: {len(blocked)} resource(s)")

    if blocked:
        print("\n  Blocked resources (dependencies found):")
        for p in blocked:
            print(f"    ⚠ {p.resource_id}: {p.block_reason}")

    if remediated:
        print("\n  Remediated resources:")
        for p in remediated:
            print(f"    ✓ {p.resource_id}")

    if remediated:
        print(f"\n[Remediation Architect] Wrote output/remediation.tf")
        print(f"[Remediation Architect] Wrote rollback files to rollbacks/")


if __name__ == "__main__":
    main()
