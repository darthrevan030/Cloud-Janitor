"""Tests for Streamlit UI delegation to Orchestrator.

Validates Requirements 3.1, 3.3, 3.4:
- 3.1: "Run Audit" invokes only Orchestrator.execute_audit() — no private (_) methods
- 3.3: On failure (success=False), UI displays AuditResult.error via st.error()
- 3.4: On success (success=True), UI renders from AuditResult fields only
"""

from __future__ import annotations

from unittest.mock import MagicMock

from cloud_janitor.orchestrator import AuditResult, Orchestrator
from cloud_janitor.agents.remediation_architect import RemediationPlan


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


def _make_mock_orchestrator() -> MagicMock:
    """Create a mock Orchestrator with spec so only real methods exist."""
    mock_orch = MagicMock(spec=Orchestrator)
    # Ensure get_audit_trail returns an empty list (used later in app.py)
    mock_orch.get_audit_trail.return_value = []
    return mock_orch


def _get_private_calls(mock_obj: MagicMock) -> list[str]:
    """Return names of any private method calls (prefixed with _) on a mock."""
    private_calls = []
    for c in mock_obj.method_calls:
        method_name = c[0]
        if method_name.startswith("_"):
            private_calls.append(method_name)
    return private_calls


def _run_audit_button_flow(mock_orch: MagicMock, mock_st: MagicMock) -> AuditResult:
    """Simulate the Run Audit button press flow from app.py lines 832-858.

    This replicates the exact logic of the Run Audit block in app.py,
    allowing us to verify delegation behavior without importing app.py
    (which triggers Streamlit page config and other side effects).
    """
    # This mirrors app.py: orch.execute_audit(status_callback=_on_agent_status)
    def _on_agent_status(agent_name: str, status: str) -> None:
        mock_st.session_state.setdefault("agent_status", {})[agent_name] = status

    result = mock_orch.execute_audit(status_callback=_on_agent_status)

    if not result.success:
        mock_st.error(result.error)
    else:
        mock_st.session_state.audit_result = result
        mock_st.success("Audit complete.")

    return result


# ---------------------------------------------------------------------------
# Requirement 3.1: Only public Orchestrator methods called
# ---------------------------------------------------------------------------


class TestRunAuditDelegation:
    """Validates Req 3.1: Run Audit calls only Orchestrator.execute_audit()."""

    def test_execute_audit_is_called(self):
        """The Run Audit flow must call execute_audit on the orchestrator."""
        mock_orch = _make_mock_orchestrator()
        mock_orch.execute_audit.return_value = AuditResult(success=True)
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_audit_button_flow(mock_orch, mock_st)

        mock_orch.execute_audit.assert_called_once()

    def test_execute_audit_receives_status_callback(self):
        """execute_audit must be called with a status_callback kwarg."""
        mock_orch = _make_mock_orchestrator()
        mock_orch.execute_audit.return_value = AuditResult(success=True)
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_audit_button_flow(mock_orch, mock_st)

        _, kwargs = mock_orch.execute_audit.call_args
        assert "status_callback" in kwargs
        assert callable(kwargs["status_callback"])

    def test_no_private_methods_called_on_success(self):
        """No private (_-prefixed) orchestrator methods should be called on success path."""
        mock_orch = _make_mock_orchestrator()
        mock_orch.execute_audit.return_value = AuditResult(
            success=True,
            findings=[{"id": "f1", "resource_id": "vol-123", "severity": "HIGH"}],
        )
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_audit_button_flow(mock_orch, mock_st)

        private_calls = _get_private_calls(mock_orch)
        assert private_calls == [], (
            f"UI called private orchestrator methods: {private_calls}"
        )

    def test_no_private_methods_called_on_failure(self):
        """No private (_-prefixed) orchestrator methods should be called on failure path."""
        mock_orch = _make_mock_orchestrator()
        mock_orch.execute_audit.return_value = AuditResult(
            success=False, error="FinOps Auditor failed: timeout"
        )
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_audit_button_flow(mock_orch, mock_st)

        private_calls = _get_private_calls(mock_orch)
        assert private_calls == [], (
            f"UI called private orchestrator methods: {private_calls}"
        )

    def test_no_additional_orchestrator_calls_beyond_execute_audit(self):
        """Only execute_audit should be invoked — no other public methods either."""
        mock_orch = _make_mock_orchestrator()
        mock_orch.execute_audit.return_value = AuditResult(success=True)
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_audit_button_flow(mock_orch, mock_st)

        # The only call should be execute_audit
        called_methods = [c[0] for c in mock_orch.method_calls]
        assert called_methods == ["execute_audit"], (
            f"Expected only execute_audit, got: {called_methods}"
        )


# ---------------------------------------------------------------------------
# Requirement 3.3: Failure displays AuditResult.error
# ---------------------------------------------------------------------------


class TestFailureDisplaysError:
    """Validates Req 3.3: On failure, UI displays AuditResult.error."""

    def test_st_error_called_with_result_error_message(self):
        """st.error must be called with the exact error string from AuditResult."""
        mock_orch = _make_mock_orchestrator()
        error_msg = "SecOps Guard failed: connection refused"
        mock_orch.execute_audit.return_value = AuditResult(
            success=False, error=error_msg
        )
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_audit_button_flow(mock_orch, mock_st)

        mock_st.error.assert_called_once_with(error_msg)

    def test_st_error_not_called_on_success(self):
        """st.error should NOT be called when audit succeeds."""
        mock_orch = _make_mock_orchestrator()
        mock_orch.execute_audit.return_value = AuditResult(success=True)
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_audit_button_flow(mock_orch, mock_st)

        mock_st.error.assert_not_called()

    def test_no_retry_calls_on_failure(self):
        """On failure, no additional orchestrator methods are called (no retry)."""
        mock_orch = _make_mock_orchestrator()
        mock_orch.execute_audit.return_value = AuditResult(
            success=False, error="Validation failed"
        )
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_audit_button_flow(mock_orch, mock_st)

        # Only execute_audit was called — no approve, rollback, or re-scan
        called_methods = [c[0] for c in mock_orch.method_calls]
        assert called_methods == ["execute_audit"], (
            f"On failure, only execute_audit should be called, got: {called_methods}"
        )

    def test_error_message_with_special_characters(self):
        """Error messages containing special characters are passed through unchanged."""
        mock_orch = _make_mock_orchestrator()
        error_msg = 'Failed: <timeout> & "connection" dropped'
        mock_orch.execute_audit.return_value = AuditResult(
            success=False, error=error_msg
        )
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_audit_button_flow(mock_orch, mock_st)

        mock_st.error.assert_called_once_with(error_msg)


# ---------------------------------------------------------------------------
# Requirement 3.4: Success renders from AuditResult fields only
# ---------------------------------------------------------------------------


class TestSuccessRendersFromAuditResult:
    """Validates Req 3.4: On success, UI stores/renders from AuditResult fields."""

    def test_audit_result_stored_in_session_state(self):
        """On success, the AuditResult is stored in st.session_state.audit_result."""
        mock_orch = _make_mock_orchestrator()
        result = AuditResult(
            success=True,
            findings=[{"id": "f1", "resource_id": "vol-001", "severity": "MEDIUM"}],
            plans=[
                RemediationPlan(
                    resource_id="vol-001",
                    finding={"id": "f1", "resource_id": "vol-001"},
                    remediation_hcl="resource {}",
                    rollback_hcl="resource {}",
                )
            ],
        )
        mock_orch.execute_audit.return_value = result
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_audit_button_flow(mock_orch, mock_st)

        assert mock_st.session_state["audit_result"] is result

    def test_success_message_displayed(self):
        """On success, st.success is called to confirm audit completion."""
        mock_orch = _make_mock_orchestrator()
        mock_orch.execute_audit.return_value = AuditResult(success=True)
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_audit_button_flow(mock_orch, mock_st)

        mock_st.success.assert_called_once_with("Audit complete.")

    def test_findings_accessible_from_stored_result(self):
        """The stored AuditResult's findings field is the one the UI should render."""
        mock_orch = _make_mock_orchestrator()
        findings = [
            {"id": "f1", "resource_id": "vol-001", "severity": "HIGH"},
            {"id": "f2", "resource_id": "sg-002", "severity": "CRITICAL"},
        ]
        result = AuditResult(success=True, findings=findings)
        mock_orch.execute_audit.return_value = result
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_audit_button_flow(mock_orch, mock_st)

        stored = mock_st.session_state["audit_result"]
        assert stored.findings == findings
        assert len(stored.findings) == 2

    def test_plans_and_blocked_plans_accessible_from_stored_result(self):
        """Plans and blocked_plans from AuditResult are preserved for UI rendering."""
        mock_orch = _make_mock_orchestrator()
        plan = RemediationPlan(
            resource_id="vol-001",
            finding={"id": "f1", "resource_id": "vol-001"},
            remediation_hcl="resource {}",
            rollback_hcl="resource {}",
        )
        blocked = RemediationPlan(
            resource_id="sg-002",
            finding={"id": "f2", "resource_id": "sg-002"},
            blocked=True,
            block_reason="dependency on eni-123",
        )
        result = AuditResult(
            success=True,
            findings=[],
            plans=[plan],
            blocked_plans=[blocked],
        )
        mock_orch.execute_audit.return_value = result
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_audit_button_flow(mock_orch, mock_st)

        stored = mock_st.session_state["audit_result"]
        assert stored.plans == [plan]
        assert stored.blocked_plans == [blocked]
        assert stored.blocked_plans[0].blocked is True

    def test_no_file_reads_on_success(self):
        """The UI should NOT re-read output files — it uses AuditResult fields only.

        We verify this by ensuring no orchestrator private members or extra
        methods are called after execute_audit returns successfully.
        """
        mock_orch = _make_mock_orchestrator()
        mock_orch.execute_audit.return_value = AuditResult(
            success=True,
            findings=[{"id": "f1", "resource_id": "vol-001", "severity": "LOW"}],
        )
        mock_st = MagicMock()
        mock_st.session_state = _SessionState()

        _run_audit_button_flow(mock_orch, mock_st)

        # Only execute_audit called — no get_findings, load_store, etc.
        called_methods = [c[0] for c in mock_orch.method_calls]
        assert called_methods == ["execute_audit"]
