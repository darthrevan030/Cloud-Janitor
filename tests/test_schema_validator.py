"""Tests for agents.schema_validator."""

import pytest

from cloud_janitor.agents.schema_validator import (
    ALL_VALID_RESOURCE_TYPES,
    VALID_AGENTS,
    VALID_CATEGORIES,
    VALID_RESOURCE_TYPES,
    VALID_SEVERITIES,
    validate_finding,
    validate_findings_store,
)


def _valid_finding(**overrides):
    """Create a valid finops finding dict with optional overrides."""
    base = {
        "id": "finding-001",
        "resource_id": "vol-abc123",
        "resource_type": "ebs",
        "agent": "finops",
        "category": "waste",
        "severity": "MEDIUM",
        "title": "Unattached EBS volume",
        "description": "Volume has been unattached for 45 days",
        "cost_estimate_monthly": 12.50,
        "idle_days": 45,
        "metadata": {"availability_zone": "us-east-1a"},
        "detected_at": "2025-01-15T10:30:00+00:00",
    }
    base.update(overrides)
    return base


def _valid_secops_sg_finding(**overrides):
    """Create a valid secops security_group finding."""
    base = {
        "id": "finding-sec-001",
        "resource_id": "sg-abc123",
        "resource_type": "security_group",
        "agent": "secops",
        "category": "security",
        "severity": "CRITICAL",
        "title": "Open security group on port 6379",
        "description": "Security group open to 0.0.0.0/0 on Redis port",
        "metadata": {"port": 6379, "cidr": "0.0.0.0/0"},
        "detected_at": "2025-01-15T10:30:00+00:00",
    }
    base.update(overrides)
    return base


def _valid_secops_encryption_finding(**overrides):
    """Create a valid secops encryption finding."""
    base = {
        "id": "finding-sec-002",
        "resource_id": "cache-prod-legacy",
        "resource_type": "elasticache",
        "agent": "secops",
        "category": "security",
        "severity": "HIGH",
        "title": "ElastiCache cluster missing encryption at rest",
        "description": "Cluster does not have encryption at rest enabled",
        "metadata": {
            "encryption_at_rest": False,
            "current_state": "unencrypted",
            "required_state": "encrypted",
        },
        "detected_at": "2025-01-15T10:30:00+00:00",
    }
    base.update(overrides)
    return base


def _valid_store(**overrides):
    """Create a valid findings_store.json dict with optional overrides."""
    findings = overrides.pop("findings", [_valid_finding()])
    base = {
        "scan_id": "550e8400-e29b-41d4-a716-446655440000",
        "started_at": "2025-01-15T10:00:00+00:00",
        "completed_at": "2025-01-15T10:05:00+00:00",
        "findings": findings,
        "summary": {
            "total": len(findings),
            "by_severity": {
                "LOW": sum(1 for f in findings if f.get("severity") == "LOW"),
                "MEDIUM": sum(1 for f in findings if f.get("severity") == "MEDIUM"),
                "HIGH": sum(1 for f in findings if f.get("severity") == "HIGH"),
                "CRITICAL": sum(1 for f in findings if f.get("severity") == "CRITICAL"),
            },
            "by_agent": {
                "finops": sum(1 for f in findings if f.get("agent") == "finops"),
                "secops": sum(1 for f in findings if f.get("agent") == "secops"),
            },
            "total_monthly_waste": sum(
                f.get("cost_estimate_monthly", 0) for f in findings
            ),
        },
    }
    base.update(overrides)
    return base


class TestValidStore:
    def test_valid_store_passes(self):
        valid, errors = validate_findings_store(_valid_store())
        assert valid
        assert errors == []

    def test_valid_store_with_multiple_findings(self):
        findings = [
            _valid_finding(id="f1", severity="LOW"),
            _valid_secops_sg_finding(id="f2"),
        ]
        valid, errors = validate_findings_store(_valid_store(findings=findings))
        assert valid
        assert errors == []

    def test_completed_at_null_is_valid(self):
        valid, errors = validate_findings_store(_valid_store(completed_at=None))
        assert valid
        assert errors == []


class TestTopLevelValidation:
    def test_missing_scan_id(self):
        store = _valid_store()
        del store["scan_id"]
        valid, errors = validate_findings_store(store)
        assert not valid
        assert any("scan_id" in e for e in errors)

    def test_missing_started_at(self):
        store = _valid_store()
        del store["started_at"]
        valid, errors = validate_findings_store(store)
        assert not valid
        assert any("started_at" in e for e in errors)

    def test_missing_completed_at(self):
        store = _valid_store()
        del store["completed_at"]
        valid, errors = validate_findings_store(store)
        assert not valid
        assert any("completed_at" in e for e in errors)

    def test_missing_findings(self):
        store = _valid_store()
        del store["findings"]
        valid, errors = validate_findings_store(store)
        assert not valid
        assert any("findings" in e for e in errors)

    def test_missing_summary(self):
        store = _valid_store()
        del store["summary"]
        valid, errors = validate_findings_store(store)
        assert not valid
        assert any("summary" in e for e in errors)

    def test_invalid_started_at(self):
        store = _valid_store(started_at="not-a-timestamp")
        valid, errors = validate_findings_store(store)
        assert not valid
        assert any("started_at" in e and "ISO-8601" in e for e in errors)

    def test_invalid_completed_at(self):
        store = _valid_store(completed_at="bad-timestamp")
        valid, errors = validate_findings_store(store)
        assert not valid
        assert any("completed_at" in e and "ISO-8601" in e for e in errors)

    def test_non_dict_root(self):
        valid, errors = validate_findings_store([1, 2, 3])
        assert not valid
        assert any("JSON object" in e for e in errors)


class TestFindingValidation:
    def test_valid_finops_finding(self):
        valid, errors = validate_finding(_valid_finding())
        assert valid
        assert errors == []

    def test_valid_secops_sg_finding(self):
        valid, errors = validate_finding(_valid_secops_sg_finding())
        assert valid
        assert errors == []

    def test_valid_secops_encryption_finding(self):
        valid, errors = validate_finding(_valid_secops_encryption_finding())
        assert valid
        assert errors == []

    def test_missing_required_field(self):
        finding = _valid_finding()
        del finding["title"]
        valid, errors = validate_finding(finding)
        assert not valid
        assert any("title" in e for e in errors)

    def test_invalid_severity(self):
        finding = _valid_finding(severity="EXTREME")
        valid, errors = validate_finding(finding)
        assert not valid
        assert any("severity" in e for e in errors)

    def test_invalid_resource_type(self):
        finding = _valid_finding(resource_type="lambda")
        valid, errors = validate_finding(finding)
        assert not valid
        assert any("resource_type" in e for e in errors)

    def test_invalid_agent(self):
        finding = _valid_finding(agent="devops")
        valid, errors = validate_finding(finding)
        assert not valid
        assert any("agent" in e for e in errors)

    def test_invalid_category(self):
        finding = _valid_finding(category="compliance")
        valid, errors = validate_finding(finding)
        assert not valid
        assert any("category" in e for e in errors)

    def test_invalid_detected_at(self):
        finding = _valid_finding(detected_at="not-a-date")
        valid, errors = validate_finding(finding)
        assert not valid
        assert any("detected_at" in e for e in errors)

    def test_non_dict_finding(self):
        valid, errors = validate_finding("not a dict")
        assert not valid
        assert any("must be a dict" in e for e in errors)


class TestFinOpsSpecificValidation:
    def test_finops_missing_cost_estimate(self):
        finding = _valid_finding()
        del finding["cost_estimate_monthly"]
        valid, errors = validate_finding(finding)
        assert not valid
        assert any("cost_estimate_monthly" in e for e in errors)

    def test_finops_missing_idle_days(self):
        finding = _valid_finding()
        del finding["idle_days"]
        valid, errors = validate_finding(finding)
        assert not valid
        assert any("idle_days" in e for e in errors)

    def test_finops_cost_estimate_not_a_number(self):
        finding = _valid_finding(cost_estimate_monthly="twelve")
        valid, errors = validate_finding(finding)
        assert not valid
        assert any("cost_estimate_monthly" in e and "number" in e for e in errors)

    def test_finops_idle_days_not_integer(self):
        finding = _valid_finding(idle_days=3.5)
        valid, errors = validate_finding(finding)
        assert not valid
        assert any("idle_days" in e and "integer" in e for e in errors)

    def test_finops_idle_days_null_is_valid(self):
        finding = _valid_finding(idle_days=None)
        valid, errors = validate_finding(finding)
        assert valid

    def test_secops_without_cost_or_idle_days_is_valid(self):
        finding = _valid_secops_sg_finding()
        # secops findings don't require cost_estimate_monthly or idle_days
        assert "cost_estimate_monthly" not in finding
        assert "idle_days" not in finding
        valid, errors = validate_finding(finding)
        assert valid


class TestSecOpsMetadataValidation:
    def test_secops_sg_missing_port(self):
        finding = _valid_secops_sg_finding()
        del finding["metadata"]["port"]
        valid, errors = validate_finding(finding)
        assert not valid
        assert any("port" in e for e in errors)

    def test_secops_sg_missing_cidr(self):
        finding = _valid_secops_sg_finding()
        del finding["metadata"]["cidr"]
        valid, errors = validate_finding(finding)
        assert not valid
        assert any("cidr" in e for e in errors)

    def test_secops_encryption_missing_encryption_at_rest(self):
        finding = _valid_secops_encryption_finding()
        del finding["metadata"]["encryption_at_rest"]
        valid, errors = validate_finding(finding)
        assert not valid
        assert any("encryption_at_rest" in e for e in errors)

    def test_secops_encryption_missing_current_state(self):
        finding = _valid_secops_encryption_finding()
        del finding["metadata"]["current_state"]
        valid, errors = validate_finding(finding)
        assert not valid
        assert any("current_state" in e for e in errors)

    def test_secops_encryption_missing_required_state(self):
        finding = _valid_secops_encryption_finding()
        del finding["metadata"]["required_state"]
        valid, errors = validate_finding(finding)
        assert not valid
        assert any("required_state" in e for e in errors)

    def test_secops_missing_metadata_entirely(self):
        finding = _valid_secops_sg_finding()
        del finding["metadata"]
        valid, errors = validate_finding(finding)
        assert not valid
        assert any("metadata" in e for e in errors)

    def test_secops_metadata_not_dict(self):
        finding = _valid_secops_sg_finding(metadata="not a dict")
        valid, errors = validate_finding(finding)
        assert not valid
        assert any("metadata" in e and "dict" in e for e in errors)


class TestResourceTypePrefixes:
    def test_aws_prefixed_security_group_is_valid(self):
        finding = _valid_secops_sg_finding(resource_type="aws_security_group")
        valid, errors = validate_finding(finding)
        assert valid
        assert errors == []

    def test_aws_prefixed_elasticache_is_valid(self):
        finding = _valid_secops_encryption_finding(resource_type="aws_elasticache")
        valid, errors = validate_finding(finding)
        assert valid
        assert errors == []

    def test_aws_prefixed_ebs_is_valid(self):
        finding = _valid_finding(resource_type="aws_ebs")
        valid, errors = validate_finding(finding)
        assert valid
        assert errors == []

    def test_invalid_aws_prefixed_type_rejected(self):
        finding = _valid_finding(resource_type="aws_lambda")
        valid, errors = validate_finding(finding)
        assert not valid
        assert any("resource_type" in e for e in errors)


class TestSummaryValidation:
    def test_total_mismatch(self):
        store = _valid_store()
        store["summary"]["total"] = 99
        valid, errors = validate_findings_store(store)
        assert not valid
        assert any("summary.total" in e for e in errors)

    def test_by_severity_mismatch(self):
        store = _valid_store()
        store["summary"]["by_severity"]["MEDIUM"] = 0
        valid, errors = validate_findings_store(store)
        assert not valid
        assert any("by_severity.MEDIUM" in e for e in errors)

    def test_by_agent_mismatch(self):
        store = _valid_store()
        store["summary"]["by_agent"]["finops"] = 99
        valid, errors = validate_findings_store(store)
        assert not valid
        assert any("by_agent.finops" in e for e in errors)

    def test_missing_total_monthly_waste(self):
        store = _valid_store()
        del store["summary"]["total_monthly_waste"]
        valid, errors = validate_findings_store(store)
        assert not valid
        assert any("total_monthly_waste" in e for e in errors)

    def test_total_monthly_waste_not_a_number(self):
        store = _valid_store()
        store["summary"]["total_monthly_waste"] = "expensive"
        valid, errors = validate_findings_store(store)
        assert not valid
        assert any("total_monthly_waste" in e and "number" in e for e in errors)

    def test_missing_total(self):
        store = _valid_store()
        del store["summary"]["total"]
        valid, errors = validate_findings_store(store)
        assert not valid
        assert any("'total'" in e for e in errors)

    def test_missing_by_severity(self):
        store = _valid_store()
        del store["summary"]["by_severity"]
        valid, errors = validate_findings_store(store)
        assert not valid
        assert any("by_severity" in e for e in errors)

    def test_missing_by_agent(self):
        store = _valid_store()
        del store["summary"]["by_agent"]
        valid, errors = validate_findings_store(store)
        assert not valid
        assert any("by_agent" in e for e in errors)
