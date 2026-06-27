"""Tests for agents.remediation_architect.

Verifies:
  - generate_rollback() returns valid HCL strings
  - Rollback HCL contains required tags (ManagedBy, Environment, RemediatedAt, RollbackRef)
  - plan() generates both remediation and rollback for the same finding
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.remediation_architect import (
    RemediationArchitect,
    RemediationPlan,
    DependencyReport,
    _sanitize_id,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ebs_finding(**overrides) -> dict:
    """Create a sample EBS finding."""
    base = {
        "id": "finding-001",
        "resource_id": "vol-0abc123def456789a",
        "resource_type": "ebs",
        "agent": "finops",
        "category": "waste",
        "severity": "MEDIUM",
        "title": "Unattached EBS volume",
        "description": "Volume idle for 35 days",
        "cost_estimate_monthly": 12.0,
        "idle_days": 35,
        "metadata": {"availability_zone": "us-east-1b", "volume_type": "gp3", "size_gb": 100},
        "detected_at": "2025-01-15T10:30:00+00:00",
    }
    base.update(overrides)
    return base


def _sg_finding(**overrides) -> dict:
    """Create a sample Security Group finding."""
    base = {
        "id": "finding-sg-001",
        "resource_id": "sg-prod-redis",
        "resource_type": "security_group",
        "agent": "secops",
        "category": "security",
        "severity": "CRITICAL",
        "title": "Redis port open to internet",
        "description": "0.0.0.0/0 on port 6379",
        "cost_estimate_monthly": 0.0,
        "idle_days": 0,
        "metadata": {"port": 6379, "cidr": "0.0.0.0/0"},
        "detected_at": "2025-01-15T10:30:00+00:00",
    }
    base.update(overrides)
    return base


def _elasticache_finding(**overrides) -> dict:
    """Create a sample ElastiCache finding."""
    base = {
        "id": "finding-cache-001",
        "resource_id": "cache-prod-legacy-01",
        "resource_type": "elasticache",
        "agent": "finops",
        "category": "waste",
        "severity": "HIGH",
        "title": "Idle ElastiCache cluster",
        "description": "Cluster idle 42 days",
        "cost_estimate_monthly": 45.60,
        "idle_days": 42,
        "metadata": {
            "instance_type": "cache.t3.medium",
            "engine": "redis",
            "engine_version": "7.0.7",
            "num_cache_nodes": 1,
        },
        "detected_at": "2025-01-15T10:30:00+00:00",
    }
    base.update(overrides)
    return base


def _mock_no_dependencies(resource_id: str) -> dict:
    """Mock check_dependencies response with no dependencies."""
    return {"has_dependencies": False, "dependents": []}


def _mock_has_dependencies(resource_id: str) -> dict:
    """Mock check_dependencies response with dependencies."""
    return {"has_dependencies": True, "dependents": ["cache-prod-legacy"]}


# ─────────────────────────────────────────────────────────────────────────────
# Tests: generate_rollback() returns valid HCL strings
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateRollback:
    """Verify generate_rollback() returns valid HCL for each resource type."""

    def setup_method(self):
        self.architect = RemediationArchitect()

    def test_ebs_rollback_is_string(self):
        hcl = self.architect.generate_rollback(_ebs_finding())
        assert isinstance(hcl, str)
        assert len(hcl) > 0

    def test_ebs_rollback_contains_resource_block(self):
        hcl = self.architect.generate_rollback(_ebs_finding())
        assert 'resource "aws_ebs_volume"' in hcl
        assert "restore_" in hcl

    def test_ebs_rollback_references_snapshot(self):
        hcl = self.architect.generate_rollback(_ebs_finding())
        assert "snapshot_id" in hcl
        assert "aws_ebs_snapshot.pre_remediation_" in hcl

    def test_ebs_rollback_includes_availability_zone(self):
        hcl = self.architect.generate_rollback(_ebs_finding())
        assert "us-east-1b" in hcl

    def test_sg_rollback_is_string(self):
        hcl = self.architect.generate_rollback(_sg_finding())
        assert isinstance(hcl, str)
        assert len(hcl) > 0

    def test_sg_rollback_contains_resource_block(self):
        hcl = self.architect.generate_rollback(_sg_finding())
        assert 'resource "aws_security_group_rule"' in hcl
        assert "restore_" in hcl

    def test_sg_rollback_restores_open_cidr(self):
        hcl = self.architect.generate_rollback(_sg_finding())
        assert '0.0.0.0/0' in hcl

    def test_sg_rollback_uses_correct_port(self):
        hcl = self.architect.generate_rollback(_sg_finding())
        assert "from_port" in hcl
        assert "6379" in hcl

    def test_elasticache_rollback_is_string(self):
        hcl = self.architect.generate_rollback(_elasticache_finding())
        assert isinstance(hcl, str)
        assert len(hcl) > 0

    def test_elasticache_rollback_contains_resource_block(self):
        hcl = self.architect.generate_rollback(_elasticache_finding())
        assert 'resource "aws_elasticache_cluster"' in hcl
        assert "restore_" in hcl

    def test_elasticache_rollback_references_snapshot(self):
        hcl = self.architect.generate_rollback(_elasticache_finding())
        assert "snapshot_name" in hcl
        assert "aws_elasticache_snapshot.pre_remediation_" in hcl

    def test_elasticache_rollback_includes_engine_details(self):
        hcl = self.architect.generate_rollback(_elasticache_finding())
        assert "redis" in hcl
        assert "7.0.7" in hcl
        assert "cache.t3.medium" in hcl


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Rollback HCL contains required tags
# ─────────────────────────────────────────────────────────────────────────────

class TestRollbackTags:
    """Verify rollback HCL includes all required tags."""

    def setup_method(self):
        self.architect = RemediationArchitect()

    def _assert_required_tags(self, hcl: str, resource_id: str):
        """Assert all required tags are present in generated HCL."""
        assert 'ManagedBy' in hcl
        assert 'Kiro-Janitor' in hcl
        assert 'Environment' in hcl
        assert 'var.environment' in hcl
        assert 'RemediatedAt' in hcl
        assert 'timestamp()' in hcl
        assert 'RollbackRef' in hcl
        assert f'rollbacks/{resource_id}.tf' in hcl

    def test_ebs_rollback_has_required_tags(self):
        finding = _ebs_finding()
        hcl = self.architect.generate_rollback(finding)
        self._assert_required_tags(hcl, finding["resource_id"])

    def test_sg_rollback_has_required_tags(self):
        finding = _sg_finding()
        hcl = self.architect.generate_rollback(finding)
        self._assert_required_tags(hcl, finding["resource_id"])

    def test_elasticache_rollback_has_required_tags(self):
        finding = _elasticache_finding()
        hcl = self.architect.generate_rollback(finding)
        self._assert_required_tags(hcl, finding["resource_id"])


# ─────────────────────────────────────────────────────────────────────────────
# Tests: plan() generates both remediation and rollback for the same finding
# ─────────────────────────────────────────────────────────────────────────────

class TestPlanGeneratesBoth:
    """Verify plan() produces both remediation and rollback HCL simultaneously."""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.rollbacks_dir = Path(self.tmp_dir) / "rollbacks"
        self.architect = RemediationArchitect(rollbacks_dir=self.rollbacks_dir)

    @patch("agents.remediation_architect.check_dependencies", side_effect=_mock_no_dependencies)
    def test_plan_produces_remediation_and_rollback_for_ebs(self, mock_deps):
        plans = self.architect.plan([_ebs_finding()])
        assert len(plans) == 1
        plan = plans[0]
        assert plan.remediation_hcl is not None
        assert plan.rollback_hcl is not None
        assert len(plan.remediation_hcl) > 0
        assert len(plan.rollback_hcl) > 0

    @patch("agents.remediation_architect.check_dependencies", side_effect=_mock_no_dependencies)
    def test_plan_produces_remediation_and_rollback_for_sg(self, mock_deps):
        plans = self.architect.plan([_sg_finding()])
        assert len(plans) == 1
        plan = plans[0]
        assert plan.remediation_hcl is not None
        assert plan.rollback_hcl is not None

    @patch("agents.remediation_architect.check_dependencies", side_effect=_mock_no_dependencies)
    def test_plan_produces_remediation_and_rollback_for_elasticache(self, mock_deps):
        plans = self.architect.plan([_elasticache_finding()])
        assert len(plans) == 1
        plan = plans[0]
        assert plan.remediation_hcl is not None
        assert plan.rollback_hcl is not None

    @patch("agents.remediation_architect.check_dependencies", side_effect=_mock_no_dependencies)
    def test_plan_writes_rollback_file(self, mock_deps):
        finding = _ebs_finding()
        self.architect.plan([finding])
        rollback_path = self.rollbacks_dir / f"{finding['resource_id']}.tf"
        assert rollback_path.exists()
        content = rollback_path.read_text()
        assert 'resource "aws_ebs_volume"' in content

    @patch("agents.remediation_architect.check_dependencies", side_effect=_mock_no_dependencies)
    def test_plan_rollback_file_contains_tags(self, mock_deps):
        finding = _sg_finding()
        self.architect.plan([finding])
        rollback_path = self.rollbacks_dir / f"{finding['resource_id']}.tf"
        content = rollback_path.read_text()
        assert "Kiro-Janitor" in content
        assert "var.environment" in content

    @patch("agents.remediation_architect.check_dependencies", side_effect=_mock_has_dependencies)
    def test_plan_blocks_when_dependencies_found(self, mock_deps):
        plans = self.architect.plan([_sg_finding()])
        assert len(plans) == 1
        plan = plans[0]
        assert plan.blocked is True
        assert plan.remediation_hcl is None
        assert plan.rollback_hcl is None

    @patch("agents.remediation_architect.check_dependencies", side_effect=_mock_no_dependencies)
    def test_plan_handles_multiple_findings(self, mock_deps):
        findings = [_ebs_finding(), _sg_finding(), _elasticache_finding()]
        plans = self.architect.plan(findings)
        assert len(plans) == 3
        for plan in plans:
            assert plan.remediation_hcl is not None
            assert plan.rollback_hcl is not None
            assert not plan.blocked

    @patch("agents.remediation_architect.check_dependencies", side_effect=_mock_no_dependencies)
    def test_rollback_hcl_not_identical_to_remediation_hcl(self, mock_deps):
        """Rollback HCL must NOT be identical to remediation HCL.

        If they were identical, applying the rollback would re-apply the
        remediation instead of reverting it — defeating its purpose.
        """
        for finding_fn in (_ebs_finding, _sg_finding, _elasticache_finding):
            plans = self.architect.plan([finding_fn()])
            plan = plans[0]
            assert plan.remediation_hcl != plan.rollback_hcl, (
                f"Rollback HCL is identical to remediation HCL for "
                f"{finding_fn.__name__} — they must differ"
            )

    @patch("agents.remediation_architect.check_dependencies", side_effect=_mock_has_dependencies)
    def test_plan_does_not_generate_hcl_before_dependency_check(self, mock_deps):
        """Remediation Architect must NOT generate HCL before dependency check completes.

        When dependencies are found, no HCL should be generated at all.
        """
        plans = self.architect.plan([_ebs_finding()])
        assert len(plans) == 1
        plan = plans[0]
        assert plan.blocked is True
        assert plan.remediation_hcl is None, (
            "Remediation HCL was generated despite dependencies being found"
        )
        assert plan.rollback_hcl is None, (
            "Rollback HCL was generated despite dependencies being found"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Tests: _sanitize_id helper
# ─────────────────────────────────────────────────────────────────────────────

class TestSanitizeId:
    def test_dashes_become_underscores(self):
        assert _sanitize_id("vol-abc-123") == "vol_abc_123"

    def test_dots_become_underscores(self):
        assert _sanitize_id("sg.prod.redis") == "sg_prod_redis"

    def test_alphanumeric_preserved(self):
        assert _sanitize_id("vol0abc123") == "vol0abc123"
