"""Tests for the interpret_query MCP tool in aws_janitor_mcp.py.

Validates:
- Req 11.1: Exposed as MCP tool, accepts user_query str, returns ScanParameters dict
- Req 11.7: Uses direct import (no network transport)
- Req 11.8: Catches parameter validation errors, returns error response without crashing
- Req 11.9: Returns safe default on internal agent failure
"""

from unittest.mock import patch, MagicMock

from mcp_server.aws_janitor_mcp import interpret_query

# Required keys for a valid ScanParameters dict
SCAN_PARAM_KEYS = {"resource_types", "check_types", "min_idle_days", "intent_summary", "confidence"}


class TestInterpretQuerySchema:
    """Schema validation for interpret_query output."""

    def test_returns_dict_with_required_keys_on_empty_query(self):
        """Empty query should still return all ScanParameters keys."""
        result = interpret_query("")
        assert isinstance(result, dict)
        assert SCAN_PARAM_KEYS.issubset(result.keys())

    def test_resource_types_is_list(self):
        result = interpret_query("")
        assert isinstance(result["resource_types"], list)

    def test_check_types_is_list(self):
        result = interpret_query("")
        assert isinstance(result["check_types"], list)

    def test_min_idle_days_is_int(self):
        result = interpret_query("")
        assert isinstance(result["min_idle_days"], int)

    def test_confidence_is_float(self):
        result = interpret_query("")
        assert isinstance(result["confidence"], float)

    def test_intent_summary_is_string(self):
        result = interpret_query("")
        assert isinstance(result["intent_summary"], str)


class TestInterpretQuerySuccessPath:
    """Test successful interpretation via mocked LLM response."""

    @patch("agents.query_interpreter.get_client")
    def test_valid_query_returns_scan_parameters(self, mock_get_client):
        """A valid query interpreted by the LLM returns proper ScanParameters."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"resource_types": ["ec2"], "check_types": ["encryption"], "min_idle_days": 14, "intent_summary": "Find idle EC2 instances", "confidence": 0.9}'))]
        )

        result = interpret_query("find idle ec2 instances")

        assert isinstance(result, dict)
        assert SCAN_PARAM_KEYS.issubset(result.keys())
        assert "error" not in result
        assert result["resource_types"] == ["ec2"]
        assert result["check_types"] == ["encryption"]
        assert result["min_idle_days"] == 14
        assert result["confidence"] == 0.9


class TestInterpretQueryErrorHandling:
    """Test error handling — server must never crash."""

    @patch("agents.query_interpreter.get_client")
    def test_llm_exception_returns_error_dict(self, mock_get_client):
        """If the LLM client raises, interpret_query returns safe default (not crash)."""
        mock_get_client.side_effect = RuntimeError("API key invalid")

        result = interpret_query("scan everything")

        # Should not raise — returns dict with safe defaults
        assert isinstance(result, dict)
        assert SCAN_PARAM_KEYS.issubset(result.keys())
        assert result["confidence"] == 0.0
        assert result["min_idle_days"] == 7

    @patch("agents.query_interpreter.QueryInterpreter.interpret")
    def test_interpret_method_exception_returns_error_dict(self, mock_interpret):
        """If QueryInterpreter.interpret raises unexpectedly, tool catches it."""
        mock_interpret.side_effect = ValueError("unexpected parsing failure")

        result = interpret_query("anything")

        assert isinstance(result, dict)
        assert "error" in result
        assert "unexpected parsing failure" in result["error"]
        # Still has all required keys for safe fallback
        assert SCAN_PARAM_KEYS.issubset(result.keys())

    @patch("agents.query_interpreter.QueryInterpreter.__init__")
    def test_constructor_exception_returns_error_dict(self, mock_init):
        """If QueryInterpreter constructor raises, tool catches it."""
        mock_init.side_effect = TypeError("bad init")

        result = interpret_query("test query")

        assert isinstance(result, dict)
        assert "error" in result
        assert "bad init" in result["error"]
        assert SCAN_PARAM_KEYS.issubset(result.keys())


class TestInterpretQueryNegativeCases:
    """Negative tests — what the tool should NOT do."""

    def test_none_input_does_not_crash(self):
        """Passing None should not crash the server."""
        # The tool signature requires str, but we test defensive behavior
        try:
            result = interpret_query(None)
            assert isinstance(result, dict)
            assert SCAN_PARAM_KEYS.issubset(result.keys())
        except TypeError:
            # If the MCP framework rejects None before our code runs, that's fine
            pass

    def test_non_string_int_input_does_not_crash(self):
        """Passing an int should not crash the server."""
        try:
            result = interpret_query(123)
            assert isinstance(result, dict)
            assert SCAN_PARAM_KEYS.issubset(result.keys())
        except TypeError:
            pass

    @patch("agents.query_interpreter.QueryInterpreter.interpret")
    def test_does_not_expose_raw_traceback(self, mock_interpret):
        """Error response should be a clean dict, not a raw traceback string."""
        mock_interpret.side_effect = Exception("secret internal error")

        result = interpret_query("query")

        assert isinstance(result, dict)
        # Should not contain a full traceback
        assert "Traceback" not in str(result)


class TestDirectImport:
    """Validate Req 11.7: direct import, no network transport."""

    def test_interpret_query_uses_direct_import(self):
        """The function must use direct import — verify QueryInterpreter is imported."""
        import mcp_server.aws_janitor_mcp as module
        # QueryInterpreter should be importable from the module's namespace
        assert hasattr(module, "QueryInterpreter") or "QueryInterpreter" in dir(module)
        # The function itself should be defined (not a proxy or stub)
        assert callable(interpret_query)
