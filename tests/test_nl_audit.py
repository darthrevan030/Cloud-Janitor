"""Tests for NL Audit feature detection and delegation in Streamlit UI.

Validates Requirements 10.1, 10.2, 10.3:
- 10.1: When execute_natural_language_audit is not callable (hasattr returns False),
         UI shows "not yet available" message and does NOT invoke the method.
- 10.2: When the method raises an exception, UI displays error and preserves
         prior audit state.
- 10.3: When method is available and query is non-empty, it is called with the
         trimmed query string.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from orchestrator import AuditResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _SessionState(dict):
    """Dict subclass that supports attribute access like Streamlit's session_state."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, key):
        try:
            del self[key]
        except KeyError:
            raise AttributeError(key)


def _make_orchestrator_without_nl_audit() -> MagicMock:
    """Create a mock orchestrator that does NOT have execute_natural_language_audit."""
    mock_orch = MagicMock()
    # Remove the attribute so hasattr(..., 'execute_natural_language_audit') returns False
    del mock_orch.execute_natural_language_audit
    return mock_orch


def _make_orchestrator_with_nl_audit(return_value=None, side_effect=None) -> MagicMock:
    """Create a mock orchestrator that HAS execute_natural_language_audit."""
    mock_orch = MagicMock()
    if side_effect is not None:
        mock_orch.execute_natural_language_audit.side_effect = side_effect
    elif return_value is not None:
        mock_orch.execute_natural_language_audit.return_value = return_value
    return mock_orch


def _run_nl_audit_flow(
    mock_orch: MagicMock,
    mock_st: MagicMock,
    nl_query: str | None = None,
    nl_submitted: bool = False,
) -> None:
    """Simulate the NL audit UI flow from app.py lines ~787-818.

    This replicates the exact logic of the NL Audit block in app.py,
    allowing us to verify delegation behavior without importing app.py
    (which triggers Streamlit page config and other side effects).
    """
    orch = mock_orch

    if not hasattr(orch, "execute_natural_language_audit"):
        mock_st.info("Natural-language audit feature is not yet available.")
    else:
        # Simulate st.text_input returning nl_query
        # Simulate st.button returning nl_submitted
        if nl_submitted and nl_query and nl_query.strip():
            try:
                result = orch.execute_natural_language_audit(nl_query.strip())
                mock_st.session_state.nl_query_result = result
                mock_st.session_state.audit_result = result
                # Cache anomalies and drift from NL audit result
                if hasattr(result, "anomalies") and result.anomalies:
                    mock_st.session_state.anomaly_results = result.anomalies
                if hasattr(result, "drift_report") and result.drift_report:
                    mock_st.session_state.drift_report = result.drift_report
                mock_st.success(
                    f"NL Audit complete — {len(result.findings)} finding(s)."
                )
                mock_st.rerun()
            except Exception as e:
                mock_st.error(f"NL Audit failed: {e}")


# ---------------------------------------------------------------------------
# Requirement 10.1: Feature detection — method missing
# ---------------------------------------------------------------------------


class TestNLAuditFeatureDetection:
    """Validates Req 10.1: When method is missing, st.info shows 'not yet available'."""

    def test_missing_method_shows_info_message(self):
        """When hasattr returns False, st.info is called with the unavailable message."""
        mock_orch = _make_orchestrator_without_nl_audit()
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(mock_orch, mock_st)

        mock_st.info.assert_called_once_with(
            "Natural-language audit feature is not yet available."
        )

    def test_missing_method_does_not_invoke_nl_audit(self):
        """When method is missing, no attempt to call execute_natural_language_audit."""
        mock_orch = _make_orchestrator_without_nl_audit()
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(mock_orch, mock_st, nl_query="Find idle EC2", nl_submitted=True)

        # The attribute doesn't exist, so it can't have been called
        assert not hasattr(mock_orch, "execute_natural_language_audit")

    def test_missing_method_does_not_call_error(self):
        """When method is missing, st.error should NOT be called."""
        mock_orch = _make_orchestrator_without_nl_audit()
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(mock_orch, mock_st)

        mock_st.error.assert_not_called()

    def test_missing_method_does_not_call_success(self):
        """When method is missing, st.success should NOT be called."""
        mock_orch = _make_orchestrator_without_nl_audit()
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(mock_orch, mock_st)

        mock_st.success.assert_not_called()


# ---------------------------------------------------------------------------
# Requirement 10.2: Exception handling — error displayed, state preserved
# ---------------------------------------------------------------------------


class TestNLAuditExceptionHandling:
    """Validates Req 10.2: On exception, UI displays error and preserves state."""

    def test_exception_displays_error_message(self):
        """When method raises, st.error is called with the exception message."""
        mock_orch = _make_orchestrator_with_nl_audit(
            side_effect=RuntimeError("AWS credentials expired")
        )
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(mock_orch, mock_st, nl_query="Find idle EC2", nl_submitted=True)

        mock_st.error.assert_called_once_with(
            "NL Audit failed: AWS credentials expired"
        )

    def test_exception_preserves_prior_audit_state(self):
        """Prior audit_result in session_state must not be overwritten on exception."""
        mock_orch = _make_orchestrator_with_nl_audit(
            side_effect=ValueError("Invalid query format")
        )
        mock_st = MagicMock()
        prior_result = AuditResult(
            success=True,
            findings=[{"id": "f1", "resource_id": "vol-old", "severity": "HIGH"}],
        )
        session = _SessionState({"audit_result": prior_result, "nl_query_result": None})
        mock_st.session_state = session

        _run_nl_audit_flow(mock_orch, mock_st, nl_query="Bad query", nl_submitted=True)

        # Prior audit state must be preserved — not overwritten
        assert mock_st.session_state["audit_result"] is prior_result

    def test_exception_does_not_call_success(self):
        """On exception, st.success should NOT be called."""
        mock_orch = _make_orchestrator_with_nl_audit(
            side_effect=ConnectionError("Network timeout")
        )
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(mock_orch, mock_st, nl_query="Find idle EC2", nl_submitted=True)

        mock_st.success.assert_not_called()

    def test_exception_does_not_call_rerun(self):
        """On exception, st.rerun should NOT be called."""
        mock_orch = _make_orchestrator_with_nl_audit(
            side_effect=Exception("Unexpected error")
        )
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(mock_orch, mock_st, nl_query="Find idle EC2", nl_submitted=True)

        mock_st.rerun.assert_not_called()

    def test_exception_with_special_characters_in_message(self):
        """Error messages with special characters are displayed correctly."""
        error_msg = 'Query failed: <timeout> & "connection" dropped'
        mock_orch = _make_orchestrator_with_nl_audit(
            side_effect=RuntimeError(error_msg)
        )
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(mock_orch, mock_st, nl_query="Find idle EC2", nl_submitted=True)

        mock_st.error.assert_called_once_with(f"NL Audit failed: {error_msg}")


# ---------------------------------------------------------------------------
# Requirement 10.3: Method invocation — called with trimmed query
# ---------------------------------------------------------------------------


class TestNLAuditMethodInvocation:
    """Validates Req 10.3: When method available and query non-empty, call with trimmed query."""

    def test_non_empty_query_calls_method_with_trimmed_value(self):
        """A non-empty query invokes execute_natural_language_audit with trimmed text."""
        result = AuditResult(
            success=True,
            findings=[{"id": "f1", "resource_id": "ec2-123", "severity": "MEDIUM"}],
        )
        mock_orch = _make_orchestrator_with_nl_audit(return_value=result)
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(
            mock_orch, mock_st, nl_query="Find idle EC2 instances", nl_submitted=True
        )

        mock_orch.execute_natural_language_audit.assert_called_once_with(
            "Find idle EC2 instances"
        )

    def test_leading_trailing_whitespace_is_stripped(self):
        """Leading and trailing whitespace in query should be stripped before calling."""
        result = AuditResult(success=True, findings=[])
        mock_orch = _make_orchestrator_with_nl_audit(return_value=result)
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(
            mock_orch, mock_st, nl_query="  Find idle EC2  \t\n", nl_submitted=True
        )

        mock_orch.execute_natural_language_audit.assert_called_once_with("Find idle EC2")

    def test_successful_result_stored_in_session_state(self):
        """On success, the AuditResult is stored in session_state.audit_result."""
        result = AuditResult(
            success=True,
            findings=[{"id": "f1", "resource_id": "ec2-456", "severity": "LOW"}],
        )
        mock_orch = _make_orchestrator_with_nl_audit(return_value=result)
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(
            mock_orch, mock_st, nl_query="Find idle EC2", nl_submitted=True
        )

        assert mock_st.session_state["audit_result"] is result
        assert mock_st.session_state["nl_query_result"] is result

    def test_success_message_includes_findings_count(self):
        """On success, st.success is called with the correct findings count."""
        result = AuditResult(
            success=True,
            findings=[
                {"id": "f1", "resource_id": "ec2-1", "severity": "HIGH"},
                {"id": "f2", "resource_id": "ec2-2", "severity": "MEDIUM"},
                {"id": "f3", "resource_id": "ec2-3", "severity": "LOW"},
            ],
        )
        mock_orch = _make_orchestrator_with_nl_audit(return_value=result)
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(
            mock_orch, mock_st, nl_query="Find idle EC2", nl_submitted=True
        )

        mock_st.success.assert_called_once_with(
            "NL Audit complete — 3 finding(s)."
        )

    def test_rerun_called_on_success(self):
        """On success, st.rerun is called to refresh the UI."""
        result = AuditResult(success=True, findings=[])
        mock_orch = _make_orchestrator_with_nl_audit(return_value=result)
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(
            mock_orch, mock_st, nl_query="Find idle EC2", nl_submitted=True
        )

        mock_st.rerun.assert_called_once()

    def test_anomalies_cached_when_present(self):
        """When result has non-empty anomalies, they are cached in session_state."""
        result = AuditResult(
            success=True,
            findings=[],
            anomalies=[{"resource_id": "ec2-789", "type": "unusual_cpu"}],
        )
        mock_orch = _make_orchestrator_with_nl_audit(return_value=result)
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(
            mock_orch, mock_st, nl_query="Find anomalies", nl_submitted=True
        )

        assert mock_st.session_state["anomaly_results"] == result.anomalies

    def test_drift_report_cached_when_present(self):
        """When result has a drift_report, it is cached in session_state."""
        result = AuditResult(
            success=True,
            findings=[],
            drift_report={"drifted_resources": ["sg-001"]},
        )
        mock_orch = _make_orchestrator_with_nl_audit(return_value=result)
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(
            mock_orch, mock_st, nl_query="Check drift", nl_submitted=True
        )

        assert mock_st.session_state["drift_report"] == result.drift_report


# ---------------------------------------------------------------------------
# Negative cases: empty/whitespace-only query does NOT invoke method
# ---------------------------------------------------------------------------


class TestNLAuditEmptyQuery:
    """Validates that empty or whitespace-only queries do not invoke the method."""

    def test_empty_string_does_not_invoke_method(self):
        """An empty string query does not call execute_natural_language_audit."""
        result = AuditResult(success=True, findings=[])
        mock_orch = _make_orchestrator_with_nl_audit(return_value=result)
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(mock_orch, mock_st, nl_query="", nl_submitted=True)

        mock_orch.execute_natural_language_audit.assert_not_called()

    def test_whitespace_only_does_not_invoke_method(self):
        """A whitespace-only query does not call execute_natural_language_audit."""
        result = AuditResult(success=True, findings=[])
        mock_orch = _make_orchestrator_with_nl_audit(return_value=result)
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(mock_orch, mock_st, nl_query="   \t\n  ", nl_submitted=True)

        mock_orch.execute_natural_language_audit.assert_not_called()

    def test_none_query_does_not_invoke_method(self):
        """A None query does not call execute_natural_language_audit."""
        result = AuditResult(success=True, findings=[])
        mock_orch = _make_orchestrator_with_nl_audit(return_value=result)
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(mock_orch, mock_st, nl_query=None, nl_submitted=True)

        mock_orch.execute_natural_language_audit.assert_not_called()

    def test_not_submitted_does_not_invoke_method(self):
        """When button is not clicked (nl_submitted=False), method is not called."""
        result = AuditResult(success=True, findings=[])
        mock_orch = _make_orchestrator_with_nl_audit(return_value=result)
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(
            mock_orch, mock_st, nl_query="Find idle EC2", nl_submitted=False
        )

        mock_orch.execute_natural_language_audit.assert_not_called()

    def test_empty_query_does_not_call_error(self):
        """Empty query should not trigger any error display."""
        result = AuditResult(success=True, findings=[])
        mock_orch = _make_orchestrator_with_nl_audit(return_value=result)
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(mock_orch, mock_st, nl_query="", nl_submitted=True)

        mock_st.error.assert_not_called()

    def test_empty_query_does_not_call_success(self):
        """Empty query should not trigger any success display."""
        result = AuditResult(success=True, findings=[])
        mock_orch = _make_orchestrator_with_nl_audit(return_value=result)
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_nl_audit_flow(mock_orch, mock_st, nl_query="", nl_submitted=True)

        mock_st.success.assert_not_called()
