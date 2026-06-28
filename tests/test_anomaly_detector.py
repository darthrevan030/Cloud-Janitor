"""Tests for AnomalyDetector agent.

Validates anomaly detection, deduplication, schema validation, and error handling.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from agents.anomaly_detector import (
    AnomalyDetector,
    MAX_ANOMALIES,
    REQUIRED_ANOMALY_KEYS,
    VALID_SEVERITIES,
)


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def detector():
    """Create an AnomalyDetector instance."""
    return AnomalyDetector()


@pytest.fixture
def sample_resources():
    """Resources list as would come from get_cost_data/get_security_data."""
    return [
        {"id": "vol-001", "type": "ebs", "name": "dev-volume-1", "region": "us-east-1"},
        {"id": "i-002", "type": "ec2", "name": "prod-web-server", "region": "us-east-1"},
        {"id": "sg-003", "type": "security_group", "name": "default-sg", "region": "eu-west-1"},
    ]


@pytest.fixture
def sample_findings():
    """Already-identified findings from FinOps + SecOps."""
    return [
        {"resource_id": "vol-001", "severity": "MEDIUM", "check_type": "idle_resource"},
    ]


def _mock_llm_response(anomalies: list[dict]) -> MagicMock:
    """Build a mock OpenAI response containing the given anomalies JSON."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(anomalies)
    return mock_response


VALID_ANOMALIES = [
    {
        "anomaly_id": "anomaly-unusual-port-sg-003",
        "resource_id": "sg-003",
        "anomaly_type": "unusual_port",
        "description": "Security group has port 9999 open which is uncommon.",
        "severity": "medium",
        "evidence": "Port 9999 TCP is open to 10.0.0.0/8",
    },
    {
        "anomaly_id": "anomaly-region-mismatch-i-002",
        "resource_id": "i-002",
        "anomaly_type": "region_mismatch",
        "description": "EC2 instance named prod but in non-primary region.",
        "severity": "low",
        "evidence": "Instance named 'prod-web-server' is in us-east-1 while team uses eu-west-1",
    },
]


# ══════════════════════════════════════════════════════════════════════
# 1. Deduplication: exclude resources already in findings (Req 6.1)
# ══════════════════════════════════════════════════════════════════════


class TestDeduplication:
    """AnomalyDetector excludes resources already flagged in findings."""

    def test_filters_resources_already_in_findings(self, detector, sample_resources, sample_findings):
        """Resources with resource_id in findings are excluded before LLM call."""
        mock_response = _mock_llm_response(VALID_ANOMALIES)

        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = detector.detect(sample_resources, sample_findings)

        # vol-001 is in findings, so no anomaly should reference it
        for anomaly in result:
            assert anomaly["resource_id"] != "vol-001"

    def test_post_filter_removes_anomalies_referencing_findings(self, detector, sample_resources, sample_findings):
        """Even if LLM returns an anomaly for a flagged resource, post-filter removes it."""
        bad_anomalies = [
            {
                "anomaly_id": "anomaly-should-be-removed",
                "resource_id": "vol-001",  # already in findings
                "anomaly_type": "cost_outlier",
                "description": "This volume costs more than expected.",
                "severity": "high",
                "evidence": "Monthly cost $200 vs expected $50",
            },
            VALID_ANOMALIES[0],  # sg-003, not in findings
        ]
        mock_response = _mock_llm_response(bad_anomalies)

        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = detector.detect(sample_resources, sample_findings)

        assert len(result) == 1
        assert result[0]["resource_id"] == "sg-003"

    def test_all_resources_in_findings_returns_empty(self, detector):
        """When all resources are already flagged, returns [] without LLM call."""
        resources = [{"id": "r-1"}, {"id": "r-2"}]
        findings = [{"resource_id": "r-1"}, {"resource_id": "r-2"}]

        with patch("agents.anomaly_detector.get_client") as mock_get:
            result = detector.detect(resources, findings)

        # LLM should never be called
        mock_get.assert_not_called()
        assert result == []


# ══════════════════════════════════════════════════════════════════════
# 2. Schema validation (Req 6.2, 6.3)
# ══════════════════════════════════════════════════════════════════════


class TestSchemaValidation:
    """Each anomaly dict has required keys with valid values."""

    def test_valid_anomaly_has_exactly_6_keys(self, detector, sample_resources, sample_findings):
        """Each returned anomaly has anomaly_id, resource_id, anomaly_type, description, severity, evidence."""
        mock_response = _mock_llm_response(VALID_ANOMALIES)

        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = detector.detect(sample_resources, sample_findings)

        for anomaly in result:
            assert set(anomaly.keys()) == REQUIRED_ANOMALY_KEYS

    def test_severity_is_valid_enum(self, detector, sample_resources, sample_findings):
        """Severity must be one of high, medium, low."""
        mock_response = _mock_llm_response(VALID_ANOMALIES)

        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = detector.detect(sample_resources, sample_findings)

        for anomaly in result:
            assert anomaly["severity"] in VALID_SEVERITIES

    def test_invalid_severity_excluded(self, detector, sample_resources, sample_findings):
        """Anomalies with invalid severity are dropped."""
        anomalies = [
            {
                "anomaly_id": "bad-sev",
                "resource_id": "i-002",
                "anomaly_type": "cost_outlier",
                "description": "Cost is high.",
                "severity": "critical",  # invalid
                "evidence": "Cost $500/mo",
            },
        ]
        mock_response = _mock_llm_response(anomalies)

        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = detector.detect(sample_resources, sample_findings)

        assert result == []

    def test_missing_required_key_excluded(self, detector, sample_resources, sample_findings):
        """Anomalies missing required keys are dropped."""
        anomalies = [
            {
                "anomaly_id": "partial",
                "resource_id": "i-002",
                # missing anomaly_type, description, severity, evidence
            },
        ]
        mock_response = _mock_llm_response(anomalies)

        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = detector.detect(sample_resources, sample_findings)

        assert result == []

    def test_empty_string_value_excluded(self, detector, sample_resources, sample_findings):
        """Anomalies with empty string values for required keys are dropped."""
        anomalies = [
            {
                "anomaly_id": "",
                "resource_id": "i-002",
                "anomaly_type": "test",
                "description": "A description.",
                "severity": "low",
                "evidence": "Some evidence",
            },
        ]
        mock_response = _mock_llm_response(anomalies)

        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = detector.detect(sample_resources, sample_findings)

        assert result == []

    def test_cap_at_20_anomalies(self, detector, sample_resources, sample_findings):
        """Maximum 20 anomalies returned even if LLM returns more."""
        many_anomalies = [
            {
                "anomaly_id": f"anomaly-{i}",
                "resource_id": "i-002",
                "anomaly_type": "cost_outlier",
                "description": f"Cost anomaly number {i}.",
                "severity": "low",
                "evidence": f"Evidence for anomaly {i}",
            }
            for i in range(30)
        ]
        mock_response = _mock_llm_response(many_anomalies)

        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = detector.detect(sample_resources, sample_findings)

        assert len(result) == MAX_ANOMALIES


# ══════════════════════════════════════════════════════════════════════
# 3. Empty resources handling (Req 6.5)
# ══════════════════════════════════════════════════════════════════════


class TestEmptyResources:
    """Empty resources returns [] without calling LLM."""

    def test_empty_resources_returns_empty(self, detector):
        """Empty resources list returns [] immediately."""
        with patch("agents.anomaly_detector.get_client") as mock_get:
            result = detector.detect([], [])

        mock_get.assert_not_called()
        assert result == []

    def test_non_empty_resources_calls_llm(self, detector, sample_resources):
        """Non-empty unflagged resources always triggers LLM call."""
        mock_response = _mock_llm_response([])

        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = detector.detect(sample_resources, [])

        mock_get.assert_called_once()
        assert result == []

    def test_llm_returns_no_anomalies(self, detector, sample_resources):
        """When LLM finds no anomalies, returns empty list."""
        mock_response = _mock_llm_response([])

        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = detector.detect(sample_resources, [])

        assert result == []


# ══════════════════════════════════════════════════════════════════════
# 4. Error handling (Req 1.5, 1.8, 1.9, 6.6)
# ══════════════════════════════════════════════════════════════════════


class TestErrorHandling:
    """AnomalyDetector never raises and returns [] on any error."""

    def test_returns_empty_on_llm_exception(self, detector, sample_resources):
        """LLM errors result in []."""
        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_get.side_effect = Exception("API down")

            result = detector.detect(sample_resources, [])

        assert result == []

    def test_returns_empty_on_invalid_json(self, detector, sample_resources):
        """Invalid JSON from LLM results in []."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "not valid json {{{"

        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = detector.detect(sample_resources, [])

        assert result == []

    def test_returns_empty_on_dict_response(self, detector, sample_resources):
        """If LLM returns a dict instead of a list, returns []."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({"not": "a list"})

        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = detector.detect(sample_resources, [])

        assert result == []

    def test_logs_to_stderr_on_error(self, detector, sample_resources, capsys):
        """Failures are logged to stderr (Req 1.9)."""
        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_get.side_effect = RuntimeError("Simulated failure")

            detector.detect(sample_resources, [])

        captured = capsys.readouterr()
        assert "AnomalyDetector" in captured.err
        assert "RuntimeError" in captured.err

    def test_never_raises_to_caller(self, detector):
        """No matter what input, detect() never raises (Req 1.8)."""
        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_get.side_effect = Exception("Simulated")
            result = detector.detect(None, None)  # type: ignore
        assert isinstance(result, list)

    def test_environment_error_returns_empty(self, detector, sample_resources):
        """EnvironmentError (missing API key) returns []."""
        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_get.side_effect = EnvironmentError("OPENROUTER_API_KEY is not set")

            result = detector.detect(sample_resources, [])

        assert result == []

    def test_markdown_code_fences_stripped(self, detector, sample_resources):
        """LLM response wrapped in markdown code fences is parsed correctly."""
        content = '```json\n' + json.dumps(VALID_ANOMALIES) + '\n```'
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = content

        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = detector.detect(sample_resources, [])

        # Should successfully parse despite markdown wrapping
        assert len(result) == 2


# ══════════════════════════════════════════════════════════════════════
# 5. LLM client usage (Req 1.11)
# ══════════════════════════════════════════════════════════════════════


class TestLLMClientUsage:
    """All LLM calls go through llm_client module."""

    def test_uses_get_client_from_llm_client(self, detector, sample_resources):
        """Verifies get_client is called from llm_client module."""
        mock_response = _mock_llm_response(VALID_ANOMALIES)

        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            detector.detect(sample_resources, [])

        mock_get.assert_called_once()

    def test_uses_configured_model(self, sample_resources):
        """The model parameter is passed to the LLM call."""
        mock_response = _mock_llm_response(VALID_ANOMALIES)

        with patch("agents.anomaly_detector.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            custom_detector = AnomalyDetector(model="custom-test-model")
            custom_detector.detect(sample_resources, [])

            call_kwargs = mock_client.chat.completions.create.call_args[1]
            assert call_kwargs["model"] == "custom-test-model"


# ══════════════════════════════════════════════════════════════════════
# 6. Resource ID field handling
# ══════════════════════════════════════════════════════════════════════


class TestResourceIdFieldHandling:
    """Resources may use 'id' or 'resource_id' field."""

    def test_handles_resource_id_field(self, detector):
        """Resources with 'resource_id' field are handled correctly."""
        resources = [{"resource_id": "r-1", "name": "test"}]
        findings = [{"resource_id": "r-1"}]

        with patch("agents.anomaly_detector.get_client") as mock_get:
            result = detector.detect(resources, findings)

        mock_get.assert_not_called()
        assert result == []

    def test_handles_id_field(self, detector):
        """Resources with 'id' field are handled correctly."""
        resources = [{"id": "r-1", "name": "test"}]
        findings = [{"resource_id": "r-1"}]

        with patch("agents.anomaly_detector.get_client") as mock_get:
            result = detector.detect(resources, findings)

        mock_get.assert_not_called()
        assert result == []

    def test_id_takes_precedence_over_resource_id(self, detector):
        """When both 'id' and 'resource_id' exist, 'id' is used for filtering."""
        resources = [{"id": "r-1", "resource_id": "r-2", "name": "test"}]
        findings = [{"resource_id": "r-1"}]

        with patch("agents.anomaly_detector.get_client") as mock_get:
            result = detector.detect(resources, findings)

        # r-1 matches findings, so resource is filtered out → no LLM call
        mock_get.assert_not_called()
        assert result == []
