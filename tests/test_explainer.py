"""Unit tests for agents/explainer.py — RemediationExplainer.

Tests the RemediationExplainer agent that generates plain-English explanations
for remediation plans shown in the approval UI panel.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 1.2, 1.8, 1.9, 1.11
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from agents.explainer import RemediationExplainer, SAFE_DEFAULT


# --- Test fixtures ---

SAMPLE_FINDING = {
    "resource_id": "sg-12345",
    "severity": "HIGH",
    "category": "security_group",
    "title": "Open ingress on port 22",
}

SAMPLE_REMEDIATION_HCL = """
resource "aws_security_group_rule" "restrict_ssh" {
  type              = "ingress"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = [data.aws_vpc.current.cidr_block]
  security_group_id = aws_security_group.main.id
}
"""

SAMPLE_ROLLBACK_HCL = """
resource "aws_security_group_rule" "restore_ssh" {
  type              = "ingress"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.main.id
}
"""

VALID_LLM_RESPONSE = {
    "risk_explanation": "This security group allows SSH access from the entire internet, exposing the instance to brute-force attacks. Any compromised key could grant full server access.",
    "what_terraform_does": "The remediation narrows the SSH ingress rule from 0.0.0.0/0 to only the VPC CIDR block. This ensures only internal traffic can reach port 22.",
    "what_rollback_restores": "Rollback restores the original 0.0.0.0/0 ingress rule, re-opening SSH to the internet.",
}


def _make_mock_response(content: str) -> MagicMock:
    """Build a mock OpenAI ChatCompletion response."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = content
    return mock_resp


class TestSafeDefaultConstant:
    """Verify the SAFE_DEFAULT constant structure."""

    def test_safe_default_has_exactly_three_keys(self):
        assert set(SAFE_DEFAULT.keys()) == {
            "risk_explanation",
            "what_terraform_does",
            "what_rollback_restores",
        }

    def test_safe_default_values_are_explanation_unavailable(self):
        for key, value in SAFE_DEFAULT.items():
            assert value == "Explanation unavailable."


class TestEmptyInputHandling:
    """Requirement 3.6: empty/whitespace HCL returns SAFE_DEFAULT without LLM call."""

    @patch("agents.explainer.get_client")
    def test_empty_remediation_hcl_returns_safe_default(self, mock_get_client):
        explainer = RemediationExplainer()
        result = explainer.explain("sg-123", SAMPLE_FINDING, "", SAMPLE_ROLLBACK_HCL)

        assert result == SAFE_DEFAULT
        mock_get_client.assert_not_called()

    @patch("agents.explainer.get_client")
    def test_whitespace_remediation_hcl_returns_safe_default(self, mock_get_client):
        explainer = RemediationExplainer()
        result = explainer.explain("sg-123", SAMPLE_FINDING, "   \n\t  ", SAMPLE_ROLLBACK_HCL)

        assert result == SAFE_DEFAULT
        mock_get_client.assert_not_called()

    @patch("agents.explainer.get_client")
    def test_empty_rollback_hcl_returns_safe_default(self, mock_get_client):
        explainer = RemediationExplainer()
        result = explainer.explain("sg-123", SAMPLE_FINDING, SAMPLE_REMEDIATION_HCL, "")

        assert result == SAFE_DEFAULT
        mock_get_client.assert_not_called()

    @patch("agents.explainer.get_client")
    def test_whitespace_rollback_hcl_returns_safe_default(self, mock_get_client):
        explainer = RemediationExplainer()
        result = explainer.explain("sg-123", SAMPLE_FINDING, SAMPLE_REMEDIATION_HCL, "  \t\n ")

        assert result == SAFE_DEFAULT
        mock_get_client.assert_not_called()

    @patch("agents.explainer.get_client")
    def test_both_empty_returns_safe_default(self, mock_get_client):
        explainer = RemediationExplainer()
        result = explainer.explain("sg-123", SAMPLE_FINDING, "", "")

        assert result == SAFE_DEFAULT
        mock_get_client.assert_not_called()

    @patch("agents.explainer.get_client")
    def test_none_remediation_hcl_returns_safe_default(self, mock_get_client):
        explainer = RemediationExplainer()
        result = explainer.explain("sg-123", SAMPLE_FINDING, None, SAMPLE_ROLLBACK_HCL)

        assert result == SAFE_DEFAULT
        mock_get_client.assert_not_called()

    @patch("agents.explainer.get_client")
    def test_none_rollback_hcl_returns_safe_default(self, mock_get_client):
        explainer = RemediationExplainer()
        result = explainer.explain("sg-123", SAMPLE_FINDING, SAMPLE_REMEDIATION_HCL, None)

        assert result == SAFE_DEFAULT
        mock_get_client.assert_not_called()


class TestSuccessfulExplanation:
    """Test LLM-powered explanation generation with valid inputs."""

    @patch("agents.explainer.get_client")
    def test_returns_valid_explanation_from_llm(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_mock_response(
            json.dumps(VALID_LLM_RESPONSE)
        )

        explainer = RemediationExplainer()
        result = explainer.explain("sg-12345", SAMPLE_FINDING, SAMPLE_REMEDIATION_HCL, SAMPLE_ROLLBACK_HCL)

        assert result == VALID_LLM_RESPONSE

    @patch("agents.explainer.get_client")
    def test_result_has_exactly_three_keys(self, mock_get_client):
        """Requirement 3.4: exactly 3 keys."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_mock_response(
            json.dumps(VALID_LLM_RESPONSE)
        )

        explainer = RemediationExplainer()
        result = explainer.explain("sg-12345", SAMPLE_FINDING, SAMPLE_REMEDIATION_HCL, SAMPLE_ROLLBACK_HCL)

        assert set(result.keys()) == {"risk_explanation", "what_terraform_does", "what_rollback_restores"}
        assert len(result) == 3

    @patch("agents.explainer.get_client")
    def test_all_values_are_non_empty_strings(self, mock_get_client):
        """Requirement 3.4: each value is a non-empty string."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_mock_response(
            json.dumps(VALID_LLM_RESPONSE)
        )

        explainer = RemediationExplainer()
        result = explainer.explain("sg-12345", SAMPLE_FINDING, SAMPLE_REMEDIATION_HCL, SAMPLE_ROLLBACK_HCL)

        for key, value in result.items():
            assert isinstance(value, str), f"{key} is not a string"
            assert len(value) > 0, f"{key} is empty"

    @patch("agents.explainer.get_client")
    def test_max_tokens_is_400(self, mock_get_client):
        """Requirement 3.5: max_tokens=400 on LLM call."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_mock_response(
            json.dumps(VALID_LLM_RESPONSE)
        )

        explainer = RemediationExplainer()
        explainer.explain("sg-12345", SAMPLE_FINDING, SAMPLE_REMEDIATION_HCL, SAMPLE_ROLLBACK_HCL)

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["max_tokens"] == 400

    @patch("agents.explainer.get_client")
    def test_uses_default_model(self, mock_get_client):
        """Requirement 1.11: uses DEFAULT_MODEL from llm_client."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_mock_response(
            json.dumps(VALID_LLM_RESPONSE)
        )

        explainer = RemediationExplainer()
        explainer.explain("sg-12345", SAMPLE_FINDING, SAMPLE_REMEDIATION_HCL, SAMPLE_ROLLBACK_HCL)

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        # The model should be whatever DEFAULT_MODEL is — just verify it's passed
        assert "model" in call_kwargs
        assert call_kwargs["model"] is not None


class TestErrorHandling:
    """Requirements 1.2, 1.8, 1.9: graceful failure with SAFE_DEFAULT."""

    @patch("agents.explainer.get_client")
    def test_api_error_returns_safe_default(self, mock_get_client):
        """Requirement 1.2: API unavailable → SAFE_DEFAULT."""
        mock_get_client.side_effect = Exception("Connection refused")

        explainer = RemediationExplainer()
        result = explainer.explain("sg-123", SAMPLE_FINDING, SAMPLE_REMEDIATION_HCL, SAMPLE_ROLLBACK_HCL)

        assert result == SAFE_DEFAULT

    @patch("agents.explainer.get_client")
    def test_invalid_json_returns_safe_default(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_mock_response(
            "This is not valid JSON at all"
        )

        explainer = RemediationExplainer()
        result = explainer.explain("sg-123", SAMPLE_FINDING, SAMPLE_REMEDIATION_HCL, SAMPLE_ROLLBACK_HCL)

        assert result == SAFE_DEFAULT

    @patch("agents.explainer.get_client")
    def test_environment_error_returns_safe_default(self, mock_get_client):
        """Requirement 1.8: EnvironmentError (no API key) → SAFE_DEFAULT."""
        mock_get_client.side_effect = EnvironmentError("OPENROUTER_API_KEY is not set")

        explainer = RemediationExplainer()
        result = explainer.explain("sg-123", SAMPLE_FINDING, SAMPLE_REMEDIATION_HCL, SAMPLE_ROLLBACK_HCL)

        assert result == SAFE_DEFAULT

    @patch("agents.explainer.get_client")
    def test_never_raises_exception(self, mock_get_client):
        """Requirement 1.8: never raise to callers."""
        mock_get_client.side_effect = RuntimeError("Unexpected failure")

        explainer = RemediationExplainer()
        # Should not raise
        result = explainer.explain("sg-123", SAMPLE_FINDING, SAMPLE_REMEDIATION_HCL, SAMPLE_ROLLBACK_HCL)
        assert isinstance(result, dict)

    @patch("agents.explainer.get_client")
    def test_logs_error_to_stderr(self, mock_get_client, capsys):
        """Requirement 1.9: log failures to stderr."""
        mock_get_client.side_effect = ConnectionError("Network down")

        explainer = RemediationExplainer()
        explainer.explain("sg-123", SAMPLE_FINDING, SAMPLE_REMEDIATION_HCL, SAMPLE_ROLLBACK_HCL)

        captured = capsys.readouterr()
        assert "RemediationExplainer" in captured.err
        assert "ConnectionError" in captured.err


class TestValidation:
    """Tests for _validate behavior with malformed LLM output."""

    @patch("agents.explainer.get_client")
    def test_missing_key_gets_safe_default_value(self, mock_get_client):
        """If LLM omits a key, that key gets the safe default value."""
        incomplete = {
            "risk_explanation": "This is a valid explanation for a test.",
            # missing what_terraform_does and what_rollback_restores
        }
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_mock_response(
            json.dumps(incomplete)
        )

        explainer = RemediationExplainer()
        result = explainer.explain("sg-123", SAMPLE_FINDING, SAMPLE_REMEDIATION_HCL, SAMPLE_ROLLBACK_HCL)

        assert result["risk_explanation"] == "This is a valid explanation for a test."
        assert result["what_terraform_does"] == "Explanation unavailable."
        assert result["what_rollback_restores"] == "Explanation unavailable."

    @patch("agents.explainer.get_client")
    def test_empty_string_value_gets_safe_default(self, mock_get_client):
        """If LLM returns an empty string for a key, it gets the safe default."""
        data = {
            "risk_explanation": "",
            "what_terraform_does": "Valid explanation here for the test to pass.",
            "what_rollback_restores": "Restores the original configuration safely.",
        }
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_mock_response(
            json.dumps(data)
        )

        explainer = RemediationExplainer()
        result = explainer.explain("sg-123", SAMPLE_FINDING, SAMPLE_REMEDIATION_HCL, SAMPLE_ROLLBACK_HCL)

        assert result["risk_explanation"] == "Explanation unavailable."
        assert result["what_terraform_does"] == "Valid explanation here for the test to pass."

    @patch("agents.explainer.get_client")
    def test_whitespace_only_value_gets_safe_default(self, mock_get_client):
        """If LLM returns whitespace-only for a key, it gets the safe default."""
        data = {
            "risk_explanation": "   \n\t  ",
            "what_terraform_does": "Valid explanation for Terraform changes.",
            "what_rollback_restores": "Restores original state successfully.",
        }
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_mock_response(
            json.dumps(data)
        )

        explainer = RemediationExplainer()
        result = explainer.explain("sg-123", SAMPLE_FINDING, SAMPLE_REMEDIATION_HCL, SAMPLE_ROLLBACK_HCL)

        assert result["risk_explanation"] == "Explanation unavailable."

    @patch("agents.explainer.get_client")
    def test_non_string_value_gets_safe_default(self, mock_get_client):
        """If LLM returns a non-string value, it gets the safe default."""
        data = {
            "risk_explanation": 42,
            "what_terraform_does": ["not", "a", "string"],
            "what_rollback_restores": "This is a valid rollback explanation.",
        }
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_mock_response(
            json.dumps(data)
        )

        explainer = RemediationExplainer()
        result = explainer.explain("sg-123", SAMPLE_FINDING, SAMPLE_REMEDIATION_HCL, SAMPLE_ROLLBACK_HCL)

        assert result["risk_explanation"] == "Explanation unavailable."
        assert result["what_terraform_does"] == "Explanation unavailable."
        assert result["what_rollback_restores"] == "This is a valid rollback explanation."


class TestNoDirectOpenAIImport:
    """Requirement 1.11: agent uses llm_client, not openai directly."""

    def test_module_does_not_import_openai_directly(self):
        import inspect
        import agents.explainer

        source = inspect.getsource(agents.explainer)
        assert "import openai" not in source
        assert "from openai" not in source
