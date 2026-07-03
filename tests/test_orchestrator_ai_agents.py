"""Tests for AI agent integrations in orchestrator.py (Task 8.7).

Tests the following:
- execute_natural_language_audit() method
- AnomalyDetector integration (post-scan, before drift)
- DriftDetector integration (save_snapshot + detect after each audit)
- QueryInterpreter failure → fallback to full scan
- Safe defaults when any agent fails (Req 1.10)
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator import AuditResult, Orchestrator


@pytest.fixture
def tmp_orchestrator(tmp_path: Path) -> Orchestrator:
    """Create an Orchestrator with a temp project root, mocking out agent I/O."""
    # Create required directories and files
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir(parents=True)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (tmp_path / "output" / "rollbacks").mkdir()
    (tmp_path / "output" / "logs").mkdir()
    (tmp_path / "output" / "policies").mkdir()

    # Create a minimal findings_store.json so validation passes
    findings_store = tmp_path / "output" / "findings_store.json"
    findings_store.write_text(
        '{"schema_version": "1.0.0", "findings": [{"agent":"finops","resource_id":"vol-1"},{"agent":"secops","resource_id":"sg-1"}]}'
    )

    with patch("orchestrator.get_cost_data") as mock_cost, \
         patch("orchestrator.get_security_data") as mock_sec:
        mock_cost.return_value = {
            "resources": [
                {"id": "vol-abc", "type": "ebs", "cost_estimate_monthly": 10.0},
            ],
            "total_monthly_waste": 10.0,
        }
        mock_sec.return_value = {
            "findings": [
                {"resource_id": "sg-123", "check_type": "security_group", "severity": "HIGH"},
            ],
            "critical_count": 0,
        }

        orch = Orchestrator(project_root=tmp_path)

    return orch


class TestAuditResultDataclass:
    """Validate AuditResult has the new fields."""

    def test_audit_result_has_anomalies_field(self):
        result = AuditResult(success=True)
        assert result.anomalies == []

    def test_audit_result_has_drift_report_field(self):
        result = AuditResult(success=True)
        assert result.drift_report is None

    def test_audit_result_anomalies_can_hold_data(self):
        anomalies = [{"anomaly_id": "a1", "resource_id": "vol-1"}]
        result = AuditResult(success=True, anomalies=anomalies)
        assert result.anomalies == anomalies

    def test_audit_result_drift_report_can_hold_data(self):
        drift = {"new_findings": [], "resolved_findings": [], "waste_delta": 0.0}
        result = AuditResult(success=True, drift_report=drift)
        assert result.drift_report == drift


class TestExecuteAuditWithAIAgents:
    """Test that execute_audit integrates anomaly detection and drift detection."""

    @patch("orchestrator.get_cost_data")
    @patch("orchestrator.get_security_data")
    def test_execute_audit_returns_anomalies(self, mock_sec, mock_cost, tmp_orchestrator):
        mock_cost.return_value = {
            "resources": [{"id": "vol-1", "type": "ebs", "cost_estimate_monthly": 5.0}],
            "total_monthly_waste": 5.0,
        }
        mock_sec.return_value = {
            "findings": [{"resource_id": "sg-1", "check_type": "security_group"}],
            "critical_count": 0,
        }

        anomalies = [{"anomaly_id": "a1", "resource_id": "vol-2", "anomaly_type": "cost_outlier",
                      "description": "Unusual cost", "severity": "medium", "evidence": "high cost"}]

        with patch.object(tmp_orchestrator._anomaly_detector, "detect", return_value=anomalies):
            with patch.object(tmp_orchestrator._finops, "scan", return_value=[]):
                with patch.object(tmp_orchestrator._secops, "scan", return_value=[]):
                    with patch.object(tmp_orchestrator._architect, "plan", return_value=[]):
                        result = tmp_orchestrator.execute_audit()

        assert result.success is True
        assert result.anomalies == anomalies

    @patch("orchestrator.get_cost_data")
    @patch("orchestrator.get_security_data")
    def test_execute_audit_returns_drift_report(self, mock_sec, mock_cost, tmp_orchestrator):
        mock_cost.return_value = {"resources": [], "total_monthly_waste": 0.0}
        mock_sec.return_value = {"findings": [], "critical_count": 0}

        drift_report = {"drift": None, "reason": "insufficient history"}

        with patch.object(tmp_orchestrator._anomaly_detector, "detect", return_value=[]):
            with patch.object(tmp_orchestrator._drift_detector, "detect", return_value=drift_report):
                with patch.object(tmp_orchestrator._drift_detector, "save_snapshot"):
                    with patch.object(tmp_orchestrator._finops, "scan", return_value=[]):
                        with patch.object(tmp_orchestrator._secops, "scan", return_value=[]):
                            with patch.object(tmp_orchestrator._architect, "plan", return_value=[]):
                                result = tmp_orchestrator.execute_audit()

        assert result.success is True
        assert result.drift_report == drift_report

    @patch("orchestrator.get_cost_data")
    @patch("orchestrator.get_security_data")
    def test_anomaly_detector_failure_returns_empty_list(self, mock_sec, mock_cost, tmp_orchestrator):
        """Req 1.10: If AnomalyDetector fails, pipeline continues with safe default."""
        mock_cost.return_value = {"resources": [], "total_monthly_waste": 0.0}
        mock_sec.return_value = {"findings": [], "critical_count": 0}

        with patch.object(tmp_orchestrator._anomaly_detector, "detect", side_effect=RuntimeError("LLM down")):
            with patch.object(tmp_orchestrator._drift_detector, "detect", return_value={"drift": None, "reason": "insufficient history"}):
                with patch.object(tmp_orchestrator._drift_detector, "save_snapshot"):
                    with patch.object(tmp_orchestrator._finops, "scan", return_value=[]):
                        with patch.object(tmp_orchestrator._secops, "scan", return_value=[]):
                            with patch.object(tmp_orchestrator._architect, "plan", return_value=[]):
                                result = tmp_orchestrator.execute_audit()

        assert result.success is True
        assert result.anomalies == []

    @patch("orchestrator.get_cost_data")
    @patch("orchestrator.get_security_data")
    def test_drift_save_snapshot_called_with_correct_args(self, mock_sec, mock_cost, tmp_orchestrator):
        mock_cost.return_value = {"resources": [], "total_monthly_waste": 0.0}
        mock_sec.return_value = {"findings": [], "critical_count": 0}

        findings = [{"resource_id": "vol-1", "cost_estimate_monthly": 15.0}]

        with patch.object(tmp_orchestrator._anomaly_detector, "detect", return_value=[]):
            with patch.object(tmp_orchestrator._drift_detector, "save_snapshot") as mock_save:
                with patch.object(tmp_orchestrator._drift_detector, "detect", return_value={"drift": None, "reason": "insufficient history"}):
                    with patch.object(tmp_orchestrator._finops, "scan", return_value=findings):
                        with patch.object(tmp_orchestrator._secops, "scan", return_value=[]):
                            with patch.object(tmp_orchestrator._architect, "plan", return_value=[]):
                                result = tmp_orchestrator.execute_audit()

        # save_snapshot was called
        mock_save.assert_called_once()
        call_args = mock_save.call_args
        # scan_id is a UUID string
        assert isinstance(call_args[0][0], str)
        # findings list
        assert call_args[0][1] == findings
        # anomalies (empty)
        assert call_args[0][2] == []
        # total_waste
        assert call_args[0][3] == 15.0


class TestExecuteNaturalLanguageAudit:
    """Test the execute_natural_language_audit() method."""

    @patch("orchestrator.get_cost_data")
    @patch("orchestrator.get_security_data")
    def test_successful_interpretation_uses_params(self, mock_sec, mock_cost, tmp_orchestrator):
        """Interpreted query parameters are used to filter scans."""
        mock_cost.return_value = {
            "resources": [{"id": "vol-1", "type": "ebs", "cost_estimate_monthly": 8.0}],
            "total_monthly_waste": 8.0,
        }
        mock_sec.return_value = {"findings": [], "critical_count": 0}

        interpreted = {
            "resource_types": ["ebs"],
            "check_types": [],
            "min_idle_days": 14,
            "intent_summary": "Find idle EBS volumes.",
            "confidence": 0.9,
        }

        with patch.object(tmp_orchestrator._query_interpreter, "interpret", return_value=interpreted):
            with patch.object(tmp_orchestrator._anomaly_detector, "detect", return_value=[]):
                with patch.object(tmp_orchestrator._drift_detector, "save_snapshot"):
                    with patch.object(tmp_orchestrator._drift_detector, "detect", return_value={"drift": None, "reason": "insufficient history"}):
                        with patch.object(tmp_orchestrator._architect, "plan", return_value=[]):
                            result = tmp_orchestrator.execute_natural_language_audit("find idle EBS volumes")

        assert result.success is True
        # get_cost_data called with resource_type="ebs" and min_idle_days=14
        mock_cost.assert_called_with(resource_type="ebs", min_idle_days=14)

    @patch("orchestrator.get_cost_data")
    @patch("orchestrator.get_security_data")
    def test_low_confidence_falls_back_to_full_scan(self, mock_sec, mock_cost, tmp_orchestrator):
        """Req 1.10: confidence=0.0 triggers fallback to execute_audit()."""
        mock_cost.return_value = {"resources": [], "total_monthly_waste": 0.0}
        mock_sec.return_value = {"findings": [], "critical_count": 0}

        safe_default = {
            "resource_types": [],
            "check_types": [],
            "min_idle_days": 7,
            "intent_summary": "Could not interpret query.",
            "confidence": 0.0,
        }

        with patch.object(tmp_orchestrator._query_interpreter, "interpret", return_value=safe_default):
            with patch.object(tmp_orchestrator, "execute_audit") as mock_audit:
                mock_audit.return_value = AuditResult(success=True)
                result = tmp_orchestrator.execute_natural_language_audit("gibberish xyz")

        mock_audit.assert_called_once()
        assert result.success is True

    @patch("orchestrator.get_cost_data")
    @patch("orchestrator.get_security_data")
    def test_interpreter_exception_falls_back_to_full_scan(self, mock_sec, mock_cost, tmp_orchestrator):
        """Req 1.10: Exception in QueryInterpreter triggers fallback."""
        mock_cost.return_value = {"resources": [], "total_monthly_waste": 0.0}
        mock_sec.return_value = {"findings": [], "critical_count": 0}

        with patch.object(tmp_orchestrator._query_interpreter, "interpret", side_effect=RuntimeError("LLM error")):
            with patch.object(tmp_orchestrator, "execute_audit") as mock_audit:
                mock_audit.return_value = AuditResult(success=True)
                result = tmp_orchestrator.execute_natural_language_audit("anything")

        mock_audit.assert_called_once()

    @patch("orchestrator.get_cost_data")
    @patch("orchestrator.get_security_data")
    def test_nl_audit_includes_anomalies_and_drift(self, mock_sec, mock_cost, tmp_orchestrator):
        """NL audit returns anomalies and drift_report in result."""
        mock_cost.return_value = {
            "resources": [{"id": "vol-1", "type": "ebs", "cost_estimate_monthly": 5.0}],
            "total_monthly_waste": 5.0,
        }
        mock_sec.return_value = {"findings": [], "critical_count": 0}

        interpreted = {
            "resource_types": [],
            "check_types": [],
            "min_idle_days": 7,
            "intent_summary": "Scan everything.",
            "confidence": 0.8,
        }
        anomalies = [{"anomaly_id": "a1", "resource_id": "vol-99"}]
        drift = {"new_findings": [], "resolved_findings": [], "waste_delta": 0.0}

        with patch.object(tmp_orchestrator._query_interpreter, "interpret", return_value=interpreted):
            with patch.object(tmp_orchestrator._anomaly_detector, "detect", return_value=anomalies):
                with patch.object(tmp_orchestrator._drift_detector, "save_snapshot"):
                    with patch.object(tmp_orchestrator._drift_detector, "detect", return_value=drift):
                        with patch.object(tmp_orchestrator._architect, "plan", return_value=[]):
                            result = tmp_orchestrator.execute_natural_language_audit("scan all")

        assert result.success is True
        assert result.anomalies == anomalies
        assert result.drift_report == drift

    @patch("orchestrator.get_cost_data")
    @patch("orchestrator.get_security_data")
    def test_nl_audit_cost_data_failure_uses_safe_default(self, mock_sec, mock_cost, tmp_orchestrator):
        """Req 1.10: get_cost_data failure → empty resources, pipeline continues."""
        mock_cost.side_effect = RuntimeError("AWS down")
        mock_sec.return_value = {
            "findings": [{"resource_id": "sg-1", "check_type": "security_group"}],
            "critical_count": 0,
        }

        interpreted = {
            "resource_types": ["ec2"],
            "check_types": ["security_group"],
            "min_idle_days": 7,
            "intent_summary": "Check EC2 security.",
            "confidence": 0.85,
        }

        with patch.object(tmp_orchestrator._query_interpreter, "interpret", return_value=interpreted):
            with patch.object(tmp_orchestrator._anomaly_detector, "detect", return_value=[]):
                with patch.object(tmp_orchestrator._drift_detector, "save_snapshot"):
                    with patch.object(tmp_orchestrator._drift_detector, "detect", return_value={"drift": None, "reason": "insufficient history"}):
                        with patch.object(tmp_orchestrator._architect, "plan", return_value=[]):
                            result = tmp_orchestrator.execute_natural_language_audit("check EC2 security")

        assert result.success is True
        # Still has security findings even though cost data failed
        assert len(result.findings) == 1

    @patch("orchestrator.get_cost_data")
    @patch("orchestrator.get_security_data")
    def test_nl_audit_multiple_resource_types(self, mock_sec, mock_cost, tmp_orchestrator):
        """Multiple resource types result in multiple get_cost_data calls."""
        mock_cost.return_value = {
            "resources": [{"id": "r-1", "type": "ec2", "cost_estimate_monthly": 3.0}],
            "total_monthly_waste": 3.0,
        }
        mock_sec.return_value = {"findings": [], "critical_count": 0}

        interpreted = {
            "resource_types": ["ec2", "ebs"],
            "check_types": [],
            "min_idle_days": 10,
            "intent_summary": "Find idle EC2 and EBS.",
            "confidence": 0.95,
        }

        with patch.object(tmp_orchestrator._query_interpreter, "interpret", return_value=interpreted):
            with patch.object(tmp_orchestrator._anomaly_detector, "detect", return_value=[]):
                with patch.object(tmp_orchestrator._drift_detector, "save_snapshot"):
                    with patch.object(tmp_orchestrator._drift_detector, "detect", return_value={"drift": None, "reason": "insufficient history"}):
                        with patch.object(tmp_orchestrator._architect, "plan", return_value=[]):
                            result = tmp_orchestrator.execute_natural_language_audit("find idle ec2 and ebs")

        # Called once for "ec2" and once for "ebs"
        assert mock_cost.call_count == 2


class TestOrchestratorInitialization:
    """Verify that new AI agents are initialized in __init__."""

    def test_has_query_interpreter(self, tmp_orchestrator):
        assert hasattr(tmp_orchestrator, "_query_interpreter")
        from agents.query_interpreter import QueryInterpreter
        assert isinstance(tmp_orchestrator._query_interpreter, QueryInterpreter)

    def test_has_anomaly_detector(self, tmp_orchestrator):
        assert hasattr(tmp_orchestrator, "_anomaly_detector")
        from agents.anomaly_detector import AnomalyDetector
        assert isinstance(tmp_orchestrator._anomaly_detector, AnomalyDetector)

    def test_has_drift_detector(self, tmp_orchestrator):
        assert hasattr(tmp_orchestrator, "_drift_detector")
        from agents.drift_detector import DriftDetector
        assert isinstance(tmp_orchestrator._drift_detector, DriftDetector)
