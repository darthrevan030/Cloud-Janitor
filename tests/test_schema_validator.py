"""Tests for agents.schema_validator."""

import json
import tempfile
from pathlib import Path

import pytest

from agents.schema_validator import (
    VALID_AGENTS,
    VALID_RESOURCE_TYPES,
    VALID_SEVERITIES,
    ValidationResult,
    validate_findings_store,
)


def _valid_finding(**overrides):
    """Create a valid finding dict with optional overrides."""
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


def _write_json(data: dict) -> Path:
    """Write data to a temporary JSON file and return its path."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, tmp)
    tmp.close()
    return Path(tmp.name)


class TestValidStore:
    def test_valid_store_passes(self):
        path = _write_json(_valid_store())
        result = validate_findings_store(path)
        assert result.valid
        assert result.errors == []

    def test_valid_store_with_multiple_findings(self):
        findings = [
            _valid_finding(id="f1", severity="LOW", agent="finops"),
            _valid_finding(id="f2", severity="HIGH", agent="secops",
                           resource_type="security_group", category="security"),
        ]
        path = _write_json(_valid_store(findings=findings))
        result = validate_findings_store(path)
        assert result.valid

    def test_completed_at_null_is_valid(self):
        path = _write_json(_valid_store(completed_at=None))
        result = validate_findings_store(path)
        assert result.valid


class TestFileErrors:
    def test_file_not_found(self):
        result = validate_findings_store("/nonexistent/path.json")
        assert not result.valid
        assert any("File not found" in e for e in result.errors)

    def test_invalid_json(self):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        tmp.write("{invalid json}")
        tmp.close()
        result = validate_findings_store(tmp.name)
        assert not result.valid
        assert any("Invalid JSON" in e for e in result.errors)


class TestTopLevelValidation:
    def test_missing_scan_id(self):
        store = _valid_store()
        del store["scan_id"]
        result = validate_findings_store(_write_json(store))
        assert not result.valid
        assert any("scan_id" in e for e in result.errors)

    def test_missing_started_at(self):
        store = _valid_store()
        del store["started_at"]
        result = validate_findings_store(_write_json(store))
        assert not result.valid
        assert any("started_at" in e for e in result.errors)

    def test_missing_findings(self):
        store = _valid_store()
        del store["findings"]
        result = validate_findings_store(_write_json(store))
        assert not result.valid
        assert any("findings" in e for e in result.errors)

    def test_missing_summary(self):
        store = _valid_store()
        del store["summary"]
        result = validate_findings_store(_write_json(store))
        assert not result.valid
        assert any("summary" in e for e in result.errors)

    def test_invalid_started_at(self):
        store = _valid_store(started_at="not-a-timestamp")
        result = validate_findings_store(_write_json(store))
        assert not result.valid
        assert any("started_at" in e and "ISO-8601" in e for e in result.errors)


class TestFindingValidation:
    def test_missing_required_field(self):
        finding = _valid_finding()
        del finding["title"]
        store = _valid_store(findings=[finding])
        result = validate_findings_store(_write_json(store))
        assert not result.valid
        assert any("title" in e for e in result.errors)

    def test_invalid_severity(self):
        finding = _valid_finding(severity="EXTREME")
        store = _valid_store(findings=[finding])
        result = validate_findings_store(_write_json(store))
        assert not result.valid
        assert any("severity" in e for e in result.errors)

    def test_invalid_resource_type(self):
        finding = _valid_finding(resource_type="lambda")
        store = _valid_store(findings=[finding])
        result = validate_findings_store(_write_json(store))
        assert not result.valid
        assert any("resource_type" in e for e in result.errors)

    def test_invalid_agent(self):
        finding = _valid_finding(agent="devops")
        store = _valid_store(findings=[finding])
        result = validate_findings_store(_write_json(store))
        assert not result.valid
        assert any("agent" in e for e in result.errors)

    def test_finops_missing_cost_estimate(self):
        finding = _valid_finding(agent="finops")
        del finding["cost_estimate_monthly"]
        store = _valid_store(findings=[finding])
        result = validate_findings_store(_write_json(store))
        assert not result.valid
        assert any("cost_estimate_monthly" in e for e in result.errors)

    def test_secops_without_cost_estimate_is_valid(self):
        finding = _valid_finding(agent="secops", category="security",
                                  resource_type="security_group")
        del finding["cost_estimate_monthly"]
        store = _valid_store(findings=[finding])
        result = validate_findings_store(_write_json(store))
        assert result.valid


class TestSummaryValidation:
    def test_total_mismatch(self):
        store = _valid_store()
        store["summary"]["total"] = 99
        result = validate_findings_store(_write_json(store))
        assert not result.valid
        assert any("summary.total" in e for e in result.errors)

    def test_by_severity_mismatch(self):
        store = _valid_store()
        store["summary"]["by_severity"]["MEDIUM"] = 0
        result = validate_findings_store(_write_json(store))
        assert not result.valid
        assert any("by_severity.MEDIUM" in e for e in result.errors)

    def test_by_agent_mismatch(self):
        store = _valid_store()
        store["summary"]["by_agent"]["finops"] = 99
        result = validate_findings_store(_write_json(store))
        assert not result.valid
        assert any("by_agent.finops" in e for e in result.errors)

    def test_missing_total_monthly_waste(self):
        store = _valid_store()
        del store["summary"]["total_monthly_waste"]
        result = validate_findings_store(_write_json(store))
        assert not result.valid
        assert any("total_monthly_waste" in e for e in result.errors)
