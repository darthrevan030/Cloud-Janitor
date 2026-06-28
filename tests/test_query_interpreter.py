"""Unit tests for agents/query_interpreter.py.

Tests the QueryInterpreter agent that maps natural language queries
to structured scan parameters via LLM.

Requirements: 1.1, 1.8, 1.9, 1.11, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8
"""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest


EXPECTED_KEYS = {"resource_types", "check_types", "min_idle_days", "intent_summary", "confidence"}


def _make_mock_response(content: str) -> MagicMock:
    """Create a mock OpenAI chat completions response."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    return mock_response


class TestEmptyQuery:
    """Requirement 2.5: Empty/whitespace query returns SAFE_DEFAULT without calling LLM."""

    def test_empty_string_returns_safe_default(self):
        with patch("agents.query_interpreter.get_client") as mock_get_client:
            from agents.query_interpreter import QueryInterpreter, SAFE_DEFAULT

            qi = QueryInterpreter()
            result = qi.interpret("")

            assert result == SAFE_DEFAULT
            mock_get_client.assert_not_called()

    def test_whitespace_only_returns_safe_default(self):
        with patch("agents.query_interpreter.get_client") as mock_get_client:
            from agents.query_interpreter import QueryInterpreter, SAFE_DEFAULT

            qi = QueryInterpreter()
            result = qi.interpret("   \t\n  ")

            assert result == SAFE_DEFAULT
            mock_get_client.assert_not_called()

    def test_none_returns_safe_default(self):
        with patch("agents.query_interpreter.get_client") as mock_get_client:
            from agents.query_interpreter import QueryInterpreter, SAFE_DEFAULT

            qi = QueryInterpreter()
            result = qi.interpret(None)

            assert result == SAFE_DEFAULT
            mock_get_client.assert_not_called()


class TestSafeDefaultSchema:
    """Requirement 2.8: SAFE_DEFAULT has exactly 5 correct keys."""

    def test_safe_default_has_exactly_five_keys(self):
        from agents.query_interpreter import SAFE_DEFAULT

        assert set(SAFE_DEFAULT.keys()) == EXPECTED_KEYS

    def test_safe_default_values(self):
        from agents.query_interpreter import SAFE_DEFAULT

        assert SAFE_DEFAULT["resource_types"] == []
        assert SAFE_DEFAULT["check_types"] == []
        assert SAFE_DEFAULT["min_idle_days"] == 7
        assert SAFE_DEFAULT["confidence"] == 0.0
        assert SAFE_DEFAULT["intent_summary"] == "Could not interpret query."

    def test_safe_default_returns_copy_not_reference(self):
        """Mutating returned default must not affect the class constant."""
        with patch("agents.query_interpreter.get_client") as mock_get_client:
            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            result = qi.interpret("")
            result["resource_types"].append("ec2")

            from agents.query_interpreter import SAFE_DEFAULT
            assert SAFE_DEFAULT["resource_types"] == []


class TestSuccessfulParsing:
    """Requirement 2.1-2.4, 2.7, 2.8: Valid LLM response is parsed correctly."""

    def test_valid_response_parsed_correctly(self):
        valid_json = json.dumps({
            "resource_types": ["ec2", "ebs"],
            "check_types": ["encryption"],
            "min_idle_days": 30,
            "intent_summary": "Find idle EC2 and unencrypted EBS volumes.",
            "confidence": 0.85,
        })

        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(valid_json)
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            result = qi.interpret("show idle ec2 and unencrypted ebs")

        assert result["resource_types"] == ["ec2", "ebs"]
        assert result["check_types"] == ["encryption"]
        assert result["min_idle_days"] == 30
        assert result["intent_summary"] == "Find idle EC2 and unencrypted EBS volumes."
        assert result["confidence"] == 0.85

    def test_result_has_exactly_five_keys(self):
        valid_json = json.dumps({
            "resource_types": ["elasticache"],
            "check_types": [],
            "min_idle_days": 14,
            "intent_summary": "Check idle Redis clusters for cost savings.",
            "confidence": 0.9,
        })

        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(valid_json)
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            result = qi.interpret("idle redis clusters")

        assert set(result.keys()) == EXPECTED_KEYS


class TestResourceTypeValidation:
    """Requirement 2.2: Only valid resource_types are returned."""

    def test_invalid_resource_types_filtered_out(self):
        bad_json = json.dumps({
            "resource_types": ["ec2", "s3", "rds", "ebs"],
            "check_types": [],
            "min_idle_days": 7,
            "intent_summary": "Scan all resources for waste detection.",
            "confidence": 0.6,
        })

        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(bad_json)
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            result = qi.interpret("scan all resources")

        assert result["resource_types"] == ["ec2", "ebs"]
        assert "s3" not in result["resource_types"]
        assert "rds" not in result["resource_types"]


class TestCheckTypeValidation:
    """Requirement 2.3: Only valid check_types are returned."""

    def test_invalid_check_types_filtered_out(self):
        bad_json = json.dumps({
            "resource_types": [],
            "check_types": ["security_group", "iam_policy", "public_access", "network"],
            "min_idle_days": 7,
            "intent_summary": "Run all security checks on the infrastructure.",
            "confidence": 0.7,
        })

        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(bad_json)
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            result = qi.interpret("run security checks")

        assert result["check_types"] == ["security_group", "public_access"]
        assert "iam_policy" not in result["check_types"]
        assert "network" not in result["check_types"]


class TestMinIdleDaysClamping:
    """Requirement 2.4: min_idle_days is non-negative and <= 3650."""

    def test_negative_value_clamped_to_zero(self):
        bad_json = json.dumps({
            "resource_types": [],
            "check_types": [],
            "min_idle_days": -10,
            "intent_summary": "Find recently created resources.",
            "confidence": 0.5,
        })

        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(bad_json)
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            result = qi.interpret("find new resources")

        assert result["min_idle_days"] == 0

    def test_excessive_value_clamped_to_3650(self):
        bad_json = json.dumps({
            "resource_types": [],
            "check_types": [],
            "min_idle_days": 99999,
            "intent_summary": "Find very old resources in the account.",
            "confidence": 0.5,
        })

        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(bad_json)
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            result = qi.interpret("find very old resources")

        assert result["min_idle_days"] == 3650

    def test_non_numeric_min_idle_days_defaults_to_7(self):
        bad_json = json.dumps({
            "resource_types": [],
            "check_types": [],
            "min_idle_days": "not-a-number",
            "intent_summary": "General scan of resources in account.",
            "confidence": 0.3,
        })

        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(bad_json)
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            result = qi.interpret("general scan")

        assert result["min_idle_days"] == 7


class TestConfidenceClamping:
    """Requirement 2.1: confidence must be in [0.0, 1.0]."""

    def test_confidence_above_one_clamped(self):
        bad_json = json.dumps({
            "resource_types": ["ec2"],
            "check_types": [],
            "min_idle_days": 7,
            "intent_summary": "Find idle EC2 instances in the account.",
            "confidence": 5.0,
        })

        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(bad_json)
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            result = qi.interpret("find idle ec2")

        assert result["confidence"] == 1.0

    def test_confidence_below_zero_clamped(self):
        bad_json = json.dumps({
            "resource_types": [],
            "check_types": [],
            "min_idle_days": 7,
            "intent_summary": "Unknown intent from the user query.",
            "confidence": -0.5,
        })

        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(bad_json)
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            result = qi.interpret("???")

        assert result["confidence"] == 0.0


class TestIntentSummaryValidation:
    """Requirement 2.7: intent_summary is 10-200 characters."""

    def test_short_summary_padded_to_minimum(self):
        bad_json = json.dumps({
            "resource_types": [],
            "check_types": [],
            "min_idle_days": 7,
            "intent_summary": "Scan",
            "confidence": 0.5,
        })

        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(bad_json)
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            result = qi.interpret("scan")

        assert len(result["intent_summary"]) >= 10

    def test_long_summary_truncated_to_200(self):
        long_summary = "A" * 300
        bad_json = json.dumps({
            "resource_types": [],
            "check_types": [],
            "min_idle_days": 7,
            "intent_summary": long_summary,
            "confidence": 0.5,
        })

        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(bad_json)
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            result = qi.interpret("a really long query")

        assert len(result["intent_summary"]) == 200


class TestLLMFailure:
    """Requirement 1.1, 1.8, 2.6: LLM failures return SAFE_DEFAULT."""

    def test_invalid_json_returns_safe_default(self):
        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(
                "This is not JSON at all"
            )
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter, SAFE_DEFAULT

            qi = QueryInterpreter()
            result = qi.interpret("find idle ec2")

        assert result == SAFE_DEFAULT

    def test_api_exception_returns_safe_default(self):
        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = RuntimeError("API unavailable")
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter, SAFE_DEFAULT

            qi = QueryInterpreter()
            result = qi.interpret("find idle ec2")

        assert result == SAFE_DEFAULT

    def test_get_client_raises_returns_safe_default(self):
        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_get_client.side_effect = EnvironmentError("OPENROUTER_API_KEY is not set")

            from agents.query_interpreter import QueryInterpreter, SAFE_DEFAULT

            qi = QueryInterpreter()
            result = qi.interpret("find idle ec2")

        assert result == SAFE_DEFAULT


class TestErrorLogging:
    """Requirement 1.9: Failures logged to stderr."""

    def test_llm_error_logged_to_stderr(self, capsys):
        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = RuntimeError("timeout")
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            qi.interpret("find idle ec2")

        captured = capsys.readouterr()
        assert "QueryInterpreter" in captured.err
        assert "RuntimeError" in captured.err

    def test_invalid_json_logged_to_stderr(self, capsys):
        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response("not json!!!")
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            qi.interpret("anything")

        captured = capsys.readouterr()
        assert "QueryInterpreter" in captured.err


class TestNeverRaises:
    """Requirement 1.8: Never raises an exception to callers."""

    def test_does_not_raise_on_bizarre_input(self):
        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = Exception("total failure")
            mock_get_client.return_value = mock_client

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            # Should not raise
            result = qi.interpret(12345)  # type: ignore — intentionally wrong type

        assert set(result.keys()) == EXPECTED_KEYS


class TestLLMClientImport:
    """Requirement 1.11: All LLM calls go through llm_client module."""

    def test_does_not_import_openai_directly(self):
        import inspect
        from agents import query_interpreter

        source = inspect.getsource(query_interpreter)
        assert "import openai" not in source
        assert "from openai" not in source

    def test_imports_from_llm_client(self):
        import inspect
        from agents import query_interpreter

        source = inspect.getsource(query_interpreter)
        assert "from llm_client import" in source
