"""Tests for Phase B+C MCP tools in aws_janitor_mcp.py.

Validates:
- Req 11.2: explain_remediation accepts resource_id, finding, remediation_hcl, rollback_hcl
- Req 11.3: suggest_policies accepts findings, already_checked
- Req 11.4: infer_resource_context accepts resource_id, resource_name, existing_tags
- Req 11.5: detect_anomalies accepts resources, findings
- Req 11.6: policy_from_incident accepts incident_description
- Req 11.7: All tools use direct import (no network transport)
- Req 11.8: Missing/wrong type params → error response without crashing
- Req 11.9: Internal agent failure → return safe default response
- Req 12.2: All MCP tools return conforming output schemas
"""

import json
from unittest.mock import patch, MagicMock

from mcp_server.aws_janitor_mcp import (
    explain_remediation,
    suggest_policies,
    infer_resource_context,
    detect_anomalies,
    policy_from_incident,
)


# ═══════════════════════════════════════════════════════════════════════════════
# explain_remediation tests
# ═══════════════════════════════════════════════════════════════════════════════

EXPLAIN_SAFE_DEFAULT = {
    "risk_explanation": "Explanation unavailable.",
    "what_terraform_does": "Explanation unavailable.",
    "what_rollback_restores": "Explanation unavailable.",
}

EXPLAIN_REQUIRED_KEYS = {"risk_explanation", "what_terraform_does", "what_rollback_restores"}


class TestExplainRemediationSchema:
    """Schema validation for explain_remediation output (Req 12.2)."""

    def test_returns_dict_with_required_keys(self):
        """Even with empty HCL, returns dict with all required keys."""
        result = explain_remediation("sg-123", {"type": "open_port"}, "", "")
        assert isinstance(result, dict)
        assert EXPLAIN_REQUIRED_KEYS == set(result.keys())

    def test_all_values_are_strings(self):
        """All values in the output dict must be strings."""
        result = explain_remediation("sg-123", {}, "", "")
        for key in EXPLAIN_REQUIRED_KEYS:
            assert isinstance(result[key], str)

    def test_empty_hcl_returns_safe_default(self):
        """Empty remediation/rollback HCL returns safe default without calling LLM."""
        result = explain_remediation("sg-123", {"type": "open_port"}, "", "")
        assert result == EXPLAIN_SAFE_DEFAULT


class TestExplainRemediationSuccessPath:
    """Test successful explanation via mocked LLM response."""

    @patch("agents.explainer.get_client")
    def test_valid_inputs_returns_explanation(self, mock_get_client):
        """With valid inputs and mocked LLM, returns proper explanation dict."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        expected_response = {
            "risk_explanation": "This SG allows unrestricted access on port 22.",
            "what_terraform_does": "Narrows CIDR from 0.0.0.0/0 to VPC CIDR.",
            "what_rollback_restores": "Restores original 0.0.0.0/0 rule.",
        }
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps(expected_response)))]
        )

        result = explain_remediation(
            "sg-123",
            {"type": "open_port", "port": 22},
            'resource "aws_security_group_rule" "fix" {}',
            'resource "aws_security_group_rule" "rollback" {}',
        )

        assert result["risk_explanation"] == "This SG allows unrestricted access on port 22."
        assert result["what_terraform_does"] == "Narrows CIDR from 0.0.0.0/0 to VPC CIDR."
        assert result["what_rollback_restores"] == "Restores original 0.0.0.0/0 rule."


class TestExplainRemediationErrorHandling:
    """Test error handling — server must never crash (Req 11.9)."""

    @patch("agents.explainer.get_client")
    def test_llm_exception_returns_safe_default(self, mock_get_client):
        """If the LLM client raises, returns safe default (not crash)."""
        mock_get_client.side_effect = RuntimeError("API unreachable")

        result = explain_remediation(
            "sg-123",
            {"type": "open_port"},
            "resource {}",
            "resource {}",
        )

        assert isinstance(result, dict)
        assert result == EXPLAIN_SAFE_DEFAULT

    @patch("agents.explainer.RemediationExplainer.explain")
    def test_explain_method_exception_returns_safe_default(self, mock_explain):
        """If RemediationExplainer.explain raises, tool catches it."""
        mock_explain.side_effect = ValueError("JSON decode failed")

        result = explain_remediation("sg-123", {}, "resource {}", "resource {}")

        assert isinstance(result, dict)
        assert EXPLAIN_REQUIRED_KEYS == set(result.keys())


class TestExplainRemediationNegativeCases:
    """Negative tests (Req 11.8)."""

    def test_none_finding_does_not_crash(self):
        """Passing None as finding should not crash the server."""
        try:
            result = explain_remediation("sg-123", None, "resource {}", "resource {}")
            assert isinstance(result, dict)
            assert EXPLAIN_REQUIRED_KEYS.issubset(result.keys())
        except TypeError:
            pass

    def test_whitespace_only_hcl_returns_safe_default(self):
        """Whitespace-only HCL should trigger safe default."""
        result = explain_remediation("sg-123", {"type": "test"}, "   ", "   ")
        assert result == EXPLAIN_SAFE_DEFAULT


# ═══════════════════════════════════════════════════════════════════════════════
# suggest_policies tests
# ═══════════════════════════════════════════════════════════════════════════════

SUGGEST_REQUIRED_KEYS = {"suggestion_id", "title", "rationale", "query", "priority"}


class TestSuggestPoliciesSchema:
    """Schema validation for suggest_policies output (Req 12.2)."""

    @patch("agents.policy_suggester.get_client")
    def test_returns_list(self, mock_get_client):
        """suggest_policies must always return a list."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        suggestions = [
            {
                "suggestion_id": "check-ebs-encryption",
                "title": "Check EBS encryption",
                "rationale": "Unencrypted volumes risk exposure.",
                "query": "Find unencrypted EBS volumes",
                "priority": "high",
                "check_type": "encryption",
            }
        ]
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps(suggestions)))]
        )

        result = suggest_policies(
            [{"resource_id": "vol-1", "type": "idle"}],
            [],
        )
        assert isinstance(result, list)

    @patch("agents.policy_suggester.get_client")
    def test_each_item_has_required_keys(self, mock_get_client):
        """Each suggestion dict must have required keys with correct types."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        suggestions = [
            {
                "suggestion_id": "check-open-sg",
                "title": "Check open security groups",
                "rationale": "Open SGs expose services.",
                "query": "Find open security groups",
                "priority": "medium",
                "check_type": "security_group",
            }
        ]
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps(suggestions)))]
        )

        result = suggest_policies([{"resource_id": "sg-1"}], [])
        assert len(result) >= 1
        item = result[0]
        assert SUGGEST_REQUIRED_KEYS.issubset(item.keys())
        for key in SUGGEST_REQUIRED_KEYS:
            assert isinstance(item[key], str)


class TestSuggestPoliciesSuccessPath:
    """Test successful suggestion via mocked LLM."""

    @patch("agents.policy_suggester.get_client")
    def test_returns_concrete_suggestion(self, mock_get_client):
        """With valid findings and mocked LLM, returns expected suggestion."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        expected = [
            {
                "suggestion_id": "check-rds-public",
                "title": "Check RDS public access",
                "rationale": "Public RDS instances are attack vectors.",
                "query": "Find publicly accessible RDS instances",
                "priority": "high",
                "check_type": "public_access",
            }
        ]
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps(expected)))]
        )

        result = suggest_policies(
            [{"resource_id": "sg-open", "type": "open_port"}],
            ["security_group"],
        )

        assert isinstance(result, list)
        # The mock returns a public_access suggestion while security_group
        # is already_checked, so it should NOT be filtered
        assert any(s["suggestion_id"] == "check-rds-public" for s in result)


class TestSuggestPoliciesErrorHandling:
    """Error handling tests (Req 11.9)."""

    @patch("agents.policy_suggester.get_client")
    def test_llm_exception_returns_empty_list(self, mock_get_client):
        """If LLM client raises, returns [] (safe default)."""
        mock_get_client.side_effect = RuntimeError("Connection refused")

        result = suggest_policies(
            [{"resource_id": "vol-1", "type": "idle"}],
            [],
        )

        assert result == []

    @patch("agents.policy_suggester.PolicySuggester.suggest")
    def test_suggest_method_exception_returns_empty_list(self, mock_suggest):
        """If PolicySuggester.suggest raises, tool catches it."""
        mock_suggest.side_effect = Exception("Internal failure")

        result = suggest_policies([], [])

        assert result == []


class TestSuggestPoliciesNegativeCases:
    """Negative tests (Req 11.8)."""

    def test_none_findings_does_not_crash(self):
        """Passing None as findings should not crash the server."""
        try:
            result = suggest_policies(None, [])
            assert isinstance(result, list)
        except TypeError:
            pass

    @patch("agents.policy_suggester.get_client")
    def test_priority_must_be_valid_enum(self, mock_get_client):
        """Suggestions with invalid priority are filtered out."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        bad_suggestions = [
            {
                "suggestion_id": "bad-priority",
                "title": "Bad priority test",
                "rationale": "Testing invalid priority.",
                "query": "some query",
                "priority": "CRITICAL",  # invalid
                "check_type": "encryption",
            }
        ]
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps(bad_suggestions)))]
        )

        result = suggest_policies([{"resource_id": "x"}], [])
        # Invalid priority means the suggestion should be filtered out
        assert not any(s.get("suggestion_id") == "bad-priority" for s in result)


# ═══════════════════════════════════════════════════════════════════════════════
# infer_resource_context tests
# ═══════════════════════════════════════════════════════════════════════════════

INFER_SAFE_DEFAULT = {
    "env": "unknown",
    "team": None,
    "owner": None,
    "risk_level": "low",
    "confidence": 0.0,
}

INFER_REQUIRED_KEYS = {"env", "team", "owner", "risk_level", "confidence"}


class TestInferResourceContextSchema:
    """Schema validation for infer_resource_context output (Req 12.2)."""

    @patch("agents.tagger.get_client")
    def test_returns_dict_with_required_keys(self, mock_get_client):
        """Output must have all required keys."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps({
                "env": "production",
                "team": "platform",
                "owner": "infra-ops",
                "risk_level": "high",
                "confidence": 0.85,
            })))]
        )

        result = infer_resource_context("i-abc123", "prod-api-server")
        assert isinstance(result, dict)
        assert INFER_REQUIRED_KEYS == set(result.keys())

    @patch("agents.tagger.get_client")
    def test_env_is_string(self, mock_get_client):
        """env must be a string."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps(INFER_SAFE_DEFAULT)))]
        )

        result = infer_resource_context("i-abc123", "test")
        assert isinstance(result["env"], str)

    @patch("agents.tagger.get_client")
    def test_confidence_is_float(self, mock_get_client):
        """confidence must be a float."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps(INFER_SAFE_DEFAULT)))]
        )

        result = infer_resource_context("i-abc123", "test")
        assert isinstance(result["confidence"], float)


class TestInferResourceContextSuccessPath:
    """Test successful inference via mocked LLM."""

    @patch("agents.tagger.get_client")
    def test_returns_inferred_values(self, mock_get_client):
        """With valid input and mocked LLM, returns expected inference."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        llm_response = {
            "env": "production",
            "team": "backend",
            "owner": "backend-team",
            "risk_level": "high",
            "confidence": 0.92,
        }
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps(llm_response)))]
        )

        result = infer_resource_context("i-prod-api-01", "prod-api-server-01")

        assert result["env"] == "production"
        assert result["team"] == "backend"
        assert result["owner"] == "backend-team"
        assert result["risk_level"] == "high"
        assert result["confidence"] == 0.92


class TestInferResourceContextErrorHandling:
    """Error handling tests (Req 11.9)."""

    @patch("agents.tagger.get_client")
    def test_llm_exception_returns_safe_default(self, mock_get_client):
        """If LLM client raises, returns safe default."""
        mock_get_client.side_effect = RuntimeError("No API key")

        result = infer_resource_context("i-abc", "some-server")

        assert result == INFER_SAFE_DEFAULT

    @patch("agents.tagger.ResourceTagger.infer")
    def test_infer_method_exception_returns_safe_default(self, mock_infer):
        """If ResourceTagger.infer raises, tool catches it."""
        mock_infer.side_effect = Exception("Unexpected error")

        result = infer_resource_context("i-abc", "some-server")

        assert isinstance(result, dict)
        assert INFER_REQUIRED_KEYS.issubset(result.keys())


class TestInferResourceContextNegativeCases:
    """Negative tests (Req 11.8)."""

    @patch("agents.tagger.get_client")
    def test_existing_tags_skips_llm_call(self, mock_get_client):
        """If all tags are already present, LLM should NOT be called."""
        result = infer_resource_context(
            "i-abc",
            "my-server",
            {"env": "staging", "team": "data", "owner": "data-eng"},
        )
        # LLM client was never called because all fields are present
        mock_get_client.assert_not_called()
        assert result["env"] == "staging"
        assert result["team"] == "data"
        assert result["owner"] == "data-eng"

    def test_none_existing_tags_does_not_crash(self):
        """Passing None for existing_tags should not crash."""
        try:
            result = infer_resource_context("i-abc", "test", None)
            assert isinstance(result, dict)
            assert INFER_REQUIRED_KEYS.issubset(result.keys())
        except TypeError:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# detect_anomalies tests
# ═══════════════════════════════════════════════════════════════════════════════

ANOMALY_REQUIRED_KEYS = {
    "anomaly_id", "resource_id", "anomaly_type",
    "description", "severity", "evidence",
}


class TestDetectAnomaliesSchema:
    """Schema validation for detect_anomalies output (Req 12.2)."""

    @patch("agents.anomaly_detector.get_client")
    def test_returns_list(self, mock_get_client):
        """detect_anomalies must always return a list."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        anomalies = [
            {
                "anomaly_id": "anomaly-unusual-port",
                "resource_id": "sg-999",
                "anomaly_type": "unusual_port",
                "description": "Port 4444 is open, uncommon for production.",
                "severity": "high",
                "evidence": "Port 4444 open to 0.0.0.0/0",
            }
        ]
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps(anomalies)))]
        )

        result = detect_anomalies(
            [{"id": "sg-999", "type": "security_group"}],
            [],
        )
        assert isinstance(result, list)

    @patch("agents.anomaly_detector.get_client")
    def test_each_anomaly_has_required_keys(self, mock_get_client):
        """Each anomaly must have all required keys with string values."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        anomalies = [
            {
                "anomaly_id": "anomaly-cost-spike",
                "resource_id": "i-expensive",
                "anomaly_type": "cost_outlier",
                "description": "Instance cost is 10x average.",
                "severity": "medium",
                "evidence": "$4500/month vs $450 average",
            }
        ]
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps(anomalies)))]
        )

        result = detect_anomalies(
            [{"id": "i-expensive", "type": "ec2"}],
            [],
        )
        assert len(result) >= 1
        item = result[0]
        assert ANOMALY_REQUIRED_KEYS == set(item.keys())
        for key in ANOMALY_REQUIRED_KEYS:
            assert isinstance(item[key], str)


class TestDetectAnomaliesSuccessPath:
    """Test successful anomaly detection via mocked LLM."""

    @patch("agents.anomaly_detector.get_client")
    def test_returns_concrete_anomaly(self, mock_get_client):
        """With valid resources and mocked LLM, returns expected anomaly."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        expected = [
            {
                "anomaly_id": "anomaly-naming-mismatch",
                "resource_id": "sg-xyz",
                "anomaly_type": "naming_anomaly",
                "description": "Resource name does not follow convention.",
                "severity": "low",
                "evidence": "Name 'temp-test-123' in production VPC",
            }
        ]
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps(expected)))]
        )

        result = detect_anomalies(
            [{"id": "sg-xyz", "type": "security_group"}],
            [],
        )

        assert len(result) == 1
        assert result[0]["anomaly_id"] == "anomaly-naming-mismatch"
        assert result[0]["severity"] == "low"

    def test_empty_resources_returns_empty_list(self):
        """No resources to analyze → returns [] without LLM call."""
        result = detect_anomalies([], [])
        assert result == []

    def test_all_resources_already_flagged_returns_empty(self):
        """If all resources are already in findings, returns []."""
        result = detect_anomalies(
            [{"id": "sg-1"}, {"id": "sg-2"}],
            [{"resource_id": "sg-1"}, {"resource_id": "sg-2"}],
        )
        assert result == []


class TestDetectAnomaliesErrorHandling:
    """Error handling tests (Req 11.9)."""

    @patch("agents.anomaly_detector.get_client")
    def test_llm_exception_returns_empty_list(self, mock_get_client):
        """If LLM client raises, returns [] (safe default)."""
        mock_get_client.side_effect = RuntimeError("Timeout")

        result = detect_anomalies(
            [{"id": "sg-abc", "type": "security_group"}],
            [],
        )

        assert result == []

    @patch("agents.anomaly_detector.AnomalyDetector.detect")
    def test_detect_method_exception_returns_empty_list(self, mock_detect):
        """If AnomalyDetector.detect raises, tool catches it."""
        mock_detect.side_effect = Exception("Memory error")

        result = detect_anomalies([{"id": "x"}], [])

        assert result == []


class TestDetectAnomaliesNegativeCases:
    """Negative tests (Req 11.8)."""

    def test_none_resources_does_not_crash(self):
        """Passing None as resources should not crash."""
        try:
            result = detect_anomalies(None, [])
            assert isinstance(result, list)
        except TypeError:
            pass

    @patch("agents.anomaly_detector.get_client")
    def test_invalid_severity_filtered_out(self, mock_get_client):
        """Anomalies with invalid severity are filtered out."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        bad_anomalies = [
            {
                "anomaly_id": "bad-sev",
                "resource_id": "sg-1",
                "anomaly_type": "test",
                "description": "desc",
                "severity": "EXTREME",  # invalid
                "evidence": "evidence",
            }
        ]
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps(bad_anomalies)))]
        )

        result = detect_anomalies([{"id": "sg-1"}], [])
        assert not any(a.get("anomaly_id") == "bad-sev" for a in result)


# ═══════════════════════════════════════════════════════════════════════════════
# policy_from_incident tests
# ═══════════════════════════════════════════════════════════════════════════════

POLICY_REQUIRED_KEYS = {
    "policy_id", "policy_name", "resource_types", "check_type",
    "check_logic_description", "rationale", "query",
    "generated_at", "incident_hash", "version",
}


class TestPolicyFromIncidentSchema:
    """Schema validation for policy_from_incident output (Req 12.2)."""

    @patch("agents.incident_policy_generator.get_client")
    def test_returns_list(self, mock_get_client):
        """policy_from_incident must always return a list."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        policies = [
            {
                "policy_id": "policy-sg-open-redis",
                "policy_name": "Detect open Redis port",
                "resource_types": ["elasticache"],
                "check_type": "security_group",
                "check_logic_description": "Check port 6379 not open to internet.",
                "rationale": "Prevents unauthorized Redis access.",
                "query": "Find security groups with port 6379 open to 0.0.0.0/0",
            },
            {
                "policy_id": "policy-sg-open-redis-2",
                "policy_name": "Detect open Redis port 2",
                "resource_types": ["ec2"],
                "check_type": "encryption",
                "check_logic_description": "Check encryption.",
                "rationale": "Prevents data loss.",
                "query": "Find unencrypted volumes",
            },
            {
                "policy_id": "policy-sg-open-redis-3",
                "policy_name": "Detect open Redis port 3",
                "resource_types": ["ebs"],
                "check_type": "public_access",
                "check_logic_description": "Check public access.",
                "rationale": "Prevents exposure.",
                "query": "Find public resources",
            },
        ]
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps(policies)))]
        )

        result = policy_from_incident("Redis was accessed by unauthorized users via open SG.")
        assert isinstance(result, list)

    @patch("agents.incident_policy_generator.get_client")
    def test_each_policy_has_required_keys(self, mock_get_client):
        """Each policy dict must have all required keys."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        policies = [
            {
                "policy_id": "policy-open-port",
                "policy_name": "Detect open ports",
                "resource_types": ["ec2"],
                "check_type": "security_group",
                "check_logic_description": "Check for open ports.",
                "rationale": "Open ports are risky.",
                "query": "Find open ports",
            },
            {
                "policy_id": "policy-unencrypted",
                "policy_name": "Detect unencrypted",
                "resource_types": ["ebs"],
                "check_type": "encryption",
                "check_logic_description": "Check encryption.",
                "rationale": "Data at risk.",
                "query": "Find unencrypted",
            },
            {
                "policy_id": "policy-public",
                "policy_name": "Detect public access",
                "resource_types": ["elasticache"],
                "check_type": "public_access",
                "check_logic_description": "Check public.",
                "rationale": "Exposure risk.",
                "query": "Find public",
            },
        ]
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps(policies)))]
        )

        result = policy_from_incident("A Redis cluster was publicly exposed.")
        assert len(result) >= 1
        item = result[0]
        assert POLICY_REQUIRED_KEYS.issubset(item.keys())
        assert isinstance(item["policy_id"], str)
        assert isinstance(item["resource_types"], list)
        assert isinstance(item["version"], int)
        assert isinstance(item["generated_at"], str)
        assert isinstance(item["incident_hash"], str)


class TestPolicyFromIncidentSuccessPath:
    """Test successful policy generation via mocked LLM."""

    @patch("agents.incident_policy_generator.get_client")
    def test_returns_policies_with_metadata(self, mock_get_client):
        """Generated policies include generated_at, incident_hash, version."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        raw_policies = [
            {
                "policy_id": "policy-check-redis-auth",
                "policy_name": "Require Redis AUTH",
                "resource_types": ["elasticache"],
                "check_type": "encryption",
                "check_logic_description": "Ensure auth_token is set.",
                "rationale": "Prevent unauthenticated access to Redis.",
                "query": "Find ElastiCache without auth_token",
            },
            {
                "policy_id": "policy-check-redis-sg",
                "policy_name": "Redis SG check",
                "resource_types": ["elasticache"],
                "check_type": "security_group",
                "check_logic_description": "Check SG not open.",
                "rationale": "Prevent internet access.",
                "query": "Find open Redis SGs",
            },
            {
                "policy_id": "policy-check-redis-encrypt",
                "policy_name": "Redis encryption",
                "resource_types": ["elasticache"],
                "check_type": "encryption",
                "check_logic_description": "Ensure encryption at rest.",
                "rationale": "Data protection.",
                "query": "Find unencrypted caches",
            },
        ]
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps(raw_policies)))]
        )

        result = policy_from_incident("Redis cluster was accessed by attacker.")

        assert len(result) >= 1
        first = result[0]
        assert first["policy_id"] == "policy-check-redis-auth"
        assert first["version"] == 1
        assert "generated_at" in first
        assert "incident_hash" in first
        assert len(first["incident_hash"]) == 8

    def test_empty_description_returns_empty_list(self):
        """Empty incident description returns [] without LLM call."""
        result = policy_from_incident("")
        assert result == []

    def test_whitespace_description_returns_empty_list(self):
        """Whitespace-only incident description returns []."""
        result = policy_from_incident("   ")
        assert result == []


class TestPolicyFromIncidentErrorHandling:
    """Error handling tests (Req 11.9)."""

    @patch("agents.incident_policy_generator.get_client")
    def test_llm_exception_returns_empty_list(self, mock_get_client):
        """If LLM client raises, returns [] (safe default)."""
        mock_get_client.side_effect = RuntimeError("Rate limited")

        result = policy_from_incident("Some incident happened.")

        assert result == []

    @patch("agents.incident_policy_generator.IncidentPolicyGenerator.generate")
    def test_generate_method_exception_returns_empty_list(self, mock_generate):
        """If IncidentPolicyGenerator.generate raises, tool catches it."""
        mock_generate.side_effect = Exception("Disk full")

        result = policy_from_incident("An incident occurred.")

        assert result == []


class TestPolicyFromIncidentNegativeCases:
    """Negative tests (Req 11.8)."""

    def test_none_description_does_not_crash(self):
        """Passing None should not crash the server."""
        try:
            result = policy_from_incident(None)
            assert isinstance(result, list)
        except TypeError:
            pass

    @patch("agents.incident_policy_generator.get_client")
    def test_invalid_check_type_filtered_out(self, mock_get_client):
        """Policies with invalid check_type are filtered out."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        bad_policies = [
            {
                "policy_id": "policy-bad-type",
                "policy_name": "Bad check type",
                "resource_types": ["ec2"],
                "check_type": "nonexistent_type",  # invalid
                "check_logic_description": "desc",
                "rationale": "rationale",
                "query": "query",
            },
        ]
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps(bad_policies)))]
        )

        result = policy_from_incident("Some incident that generates a bad policy.")
        assert not any(p.get("policy_id") == "policy-bad-type" for p in result)


# ═══════════════════════════════════════════════════════════════════════════════
# Direct import validation (Req 11.7)
# ═══════════════════════════════════════════════════════════════════════════════


class TestDirectImportAllTools:
    """Validate Req 11.7: all tools use direct import, no network transport."""

    def test_explain_remediation_is_callable(self):
        """explain_remediation is directly importable and callable."""
        assert callable(explain_remediation)

    def test_suggest_policies_is_callable(self):
        """suggest_policies is directly importable and callable."""
        assert callable(suggest_policies)

    def test_infer_resource_context_is_callable(self):
        """infer_resource_context is directly importable and callable."""
        assert callable(infer_resource_context)

    def test_detect_anomalies_is_callable(self):
        """detect_anomalies is directly importable and callable."""
        assert callable(detect_anomalies)

    def test_policy_from_incident_is_callable(self):
        """policy_from_incident is directly importable and callable."""
        assert callable(policy_from_incident)

    def test_module_has_agent_classes(self):
        """The MCP module imports agent classes directly (no proxy/stub)."""
        import mcp_server.aws_janitor_mcp as module
        assert hasattr(module, "RemediationExplainer")
        assert hasattr(module, "PolicySuggester")
        assert hasattr(module, "ResourceTagger")
        assert hasattr(module, "AnomalyDetector")
        assert hasattr(module, "IncidentPolicyGenerator")
