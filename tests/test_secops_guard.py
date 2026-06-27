"""Tests for the SecOps Guard agent."""

import json
import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.secops_guard import SecOpsGuard, SENSITIVE_PORTS


class TestSecOpsGuard:
    """Tests for SecOpsGuard class."""

    def test_sensitive_ports_constant(self):
        """SENSITIVE_PORTS contains all required ports."""
        assert 22 in SENSITIVE_PORTS
        assert 3306 in SENSITIVE_PORTS
        assert 5432 in SENSITIVE_PORTS
        assert 6379 in SENSITIVE_PORTS
        assert 27017 in SENSITIVE_PORTS

    def test_scan_returns_findings(self):
        """scan() returns a list of finding dicts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "findings_store.json"
            guard = SecOpsGuard(findings_store_path=store_path)
            findings = guard.scan()

            assert isinstance(findings, list)
            assert len(findings) == 4  # 2 SG + 2 encryption from fixtures

    def test_scan_creates_findings_store(self):
        """scan() creates findings_store.json when it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "findings_store.json"
            guard = SecOpsGuard(findings_store_path=store_path)
            guard.scan()

            assert store_path.exists()
            store = json.loads(store_path.read_text())
            assert "findings" in store
            assert "summary" in store

    def test_scan_appends_to_existing_store(self):
        """scan() appends findings to existing findings_store.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "findings_store.json"

            # Pre-populate with a finops finding
            existing_store = {
                "scan_id": "test-scan",
                "started_at": "2026-01-01T00:00:00+00:00",
                "completed_at": None,
                "findings": [
                    {
                        "id": "existing-finding",
                        "resource_id": "vol-existing",
                        "resource_type": "ebs",
                        "agent": "finops",
                        "category": "waste",
                        "severity": "MEDIUM",
                        "title": "Existing finding",
                        "description": "Test",
                        "cost_estimate_monthly": 12.0,
                        "idle_days": 35,
                        "metadata": {},
                        "detected_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
                "summary": {
                    "total": 1,
                    "by_severity": {"LOW": 0, "MEDIUM": 1, "HIGH": 0, "CRITICAL": 0},
                    "by_agent": {"finops": 1, "secops": 0},
                    "total_monthly_waste": 12.0,
                },
            }
            store_path.write_text(json.dumps(existing_store))

            guard = SecOpsGuard(findings_store_path=store_path)
            findings = guard.scan()

            store = json.loads(store_path.read_text())
            # Should have existing finops finding + 4 secops findings
            assert store["summary"]["total"] == 5
            assert store["summary"]["by_agent"]["finops"] == 1
            assert store["summary"]["by_agent"]["secops"] == 4
            assert store["summary"]["total_monthly_waste"] == 12.0

    def test_check_security_groups_detects_open_ports(self):
        """check_security_groups() returns findings for open sensitive ports."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "findings_store.json"
            guard = SecOpsGuard(findings_store_path=store_path)
            findings = guard.check_security_groups()

            assert len(findings) == 2  # Redis (6379) and SSH (22) from fixtures
            resource_ids = [f["resource_id"] for f in findings]
            assert "sg-prod-redis" in resource_ids
            assert "sg-web-servers" in resource_ids

    def test_check_encryption_elasticache(self):
        """check_encryption('elasticache') returns unencrypted cache findings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "findings_store.json"
            guard = SecOpsGuard(findings_store_path=store_path)
            findings = guard.check_encryption("elasticache")

            assert len(findings) == 1
            assert findings[0]["resource_id"] == "cache-prod-legacy"
            assert findings[0]["resource_type"] == "elasticache"

    def test_check_encryption_ebs(self):
        """check_encryption('ebs') returns unencrypted EBS findings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "findings_store.json"
            guard = SecOpsGuard(findings_store_path=store_path)
            findings = guard.check_encryption("ebs")

            assert len(findings) == 1
            assert findings[0]["resource_id"] == "vol-data-001"
            assert findings[0]["resource_type"] == "ebs"

    def test_severity_database_port_critical(self):
        """Open SG on database/cache ports = CRITICAL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "findings_store.json"
            guard = SecOpsGuard(findings_store_path=store_path)
            findings = guard.check_security_groups()

            redis_finding = next(f for f in findings if f["resource_id"] == "sg-prod-redis")
            assert redis_finding["severity"] == "CRITICAL"

    def test_severity_ssh_port_high(self):
        """Open SG on SSH (22) = HIGH."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "findings_store.json"
            guard = SecOpsGuard(findings_store_path=store_path)
            findings = guard.check_security_groups()

            ssh_finding = next(f for f in findings if f["resource_id"] == "sg-web-servers")
            assert ssh_finding["severity"] == "HIGH"

    def test_severity_unencrypted_storage_high(self):
        """Unencrypted storage = HIGH."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "findings_store.json"
            guard = SecOpsGuard(findings_store_path=store_path)

            cache_findings = guard.check_encryption("elasticache")
            assert cache_findings[0]["severity"] == "HIGH"

            ebs_findings = guard.check_encryption("ebs")
            assert ebs_findings[0]["severity"] == "HIGH"

    def test_finding_schema(self):
        """Each finding matches the expected schema."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "findings_store.json"
            guard = SecOpsGuard(findings_store_path=store_path)
            findings = guard.scan()

            required_keys = {
                "id", "resource_id", "resource_type", "agent", "category",
                "severity", "title", "description", "cost_estimate_monthly",
                "idle_days", "metadata", "detected_at",
            }

            for f in findings:
                assert required_keys.issubset(f.keys()), f"Missing keys: {required_keys - f.keys()}"
                assert f["agent"] == "secops"
                assert f["category"] == "security"
                assert f["cost_estimate_monthly"] == 0.0
                assert f["idle_days"] == 0
                assert f["severity"] in ("HIGH", "CRITICAL")

    def test_finding_metadata_security_group(self):
        """SG findings have port, cidr, current_state, required_state in metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "findings_store.json"
            guard = SecOpsGuard(findings_store_path=store_path)
            findings = guard.check_security_groups()

            for f in findings:
                assert "port" in f["metadata"]
                assert "cidr" in f["metadata"]
                assert f["metadata"]["cidr"] == "0.0.0.0/0"
                assert "current_state" in f["metadata"]
                assert "required_state" in f["metadata"]

    def test_finding_metadata_encryption(self):
        """Encryption findings have encryption_at_rest, current_state, required_state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "findings_store.json"
            guard = SecOpsGuard(findings_store_path=store_path)
            findings = guard.check_encryption("elasticache")

            for f in findings:
                assert "encryption_at_rest" in f["metadata"]
                assert f["metadata"]["encryption_at_rest"] is False
                assert "current_state" in f["metadata"]
                assert "required_state" in f["metadata"]
