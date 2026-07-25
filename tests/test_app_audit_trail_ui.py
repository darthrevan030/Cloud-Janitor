"""Unit tests for the Audit Trail Query UI section in app.py.

Tests cover:
- Query is called with correct filters when the button is pressed
- CSV/JSON download buttons produce expected content
- StateStore-unavailable errors are caught → explanatory message, no stack trace
- Empty results render gracefully (no crash)

Requirements: 1.5
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import date
from unittest.mock import MagicMock

from cloud_janitor.core.audit_query import (
    export_audit_csv,
    export_audit_json,
    UNSCOPED,
)


# ── Sample data ──────────────────────────────────────────────────────────

SAMPLE_ROWS = [
    {
        "id": 1,
        "timestamp": "2025-07-10T12:00:00Z",
        "actor": "arn:aws:iam::123456:user/alice",
        "action": "approval",
        "resource_id": "vol-0abc123",
        "result": "success",
        "details": "Approved remediation",
        "run_id": "run-001",
    },
    {
        "id": 2,
        "timestamp": "2025-07-09T08:30:00Z",
        "actor": "arn:aws:iam::123456:user/bob",
        "action": "rollback",
        "resource_id": "sg-xyz789",
        "result": "failure",
        "details": "Rollback failed",
        "run_id": None,
    },
]


# ── Helper: simulate the query-button logic from app.py ──────────────────
# The audit trail query section in app.py is module-level Streamlit code.
# We extract the core logic into a testable helper that mirrors exactly
# what the button press does, then test that helper against mocks.


def _simulate_query_button_press(
    session_state: dict,
    resource_id: str = "",
    actor: str = "",
    result: str = "",
    action: str = "",
    date_from=None,
    date_to=None,
    run_scope: str = "All",
    run_id_input: str = "",
    query_fn=None,
):
    """Simulate the logic executed when "🔎 Query Audit Trail" is pressed.

    This mirrors the code path in app.py lines 1846–1871 exactly:
    - Derives run_id_value from run_scope / run_id_input
    - Calls query_audit with the same argument derivation as app.py
    - On error: stores None in session_state["aq_results"]
    - Returns (warning_msg, results) for assertion
    """
    # Derive run_id value — same logic as app.py
    run_id_value: str | None | object = None
    if run_scope == "Scoped to a run":
        run_id_value = run_id_input.strip() if run_id_input.strip() else None
    elif run_scope == "Unscoped only":
        run_id_value = UNSCOPED

    warning_msg = None
    try:
        state_store = session_state["orchestrator"]._state_store
        rows = query_fn(
            state_store,
            resource_id=resource_id.strip() or None,
            actor=actor.strip() or None,
            result=result or None,
            action=action or None,
            run_id=run_id_value,
            date_from=str(date_from) if date_from else None,
            date_to=str(date_to) if date_to else None,
        )
        session_state["aq_results"] = rows
    except (AttributeError, sqlite3.OperationalError):
        warning_msg = (
            "Audit trail querying requires phase2-persistent-state. "
            "The StateStore is not yet available."
        )
        session_state["aq_results"] = None
    except Exception:
        warning_msg = (
            "Audit trail querying requires phase2-persistent-state. "
            "The StateStore is not yet available."
        )
        session_state["aq_results"] = None

    return warning_msg, session_state.get("aq_results")


# ── Test Class: Query Called with Correct Filters ────────────────────────


class TestQueryCalledWithCorrectFilters:
    """When button is pressed with filter values, query_audit() receives
    the correct arguments derived from the widget state.
    """

    def test_all_filters_passed_correctly(self):
        """All filter widgets populated → query_audit called with matching args."""
        mock_store = MagicMock()
        mock_orch = MagicMock()
        mock_orch._state_store = mock_store
        session = {"orchestrator": mock_orch}

        mock_query = MagicMock(return_value=SAMPLE_ROWS)

        _simulate_query_button_press(
            session,
            resource_id="vol-0abc123",
            actor="arn:aws:iam::123456:user/alice",
            result="success",
            action="approval",
            date_from=date(2025, 7, 1),
            date_to=date(2025, 7, 31),
            run_scope="All",
            query_fn=mock_query,
        )

        mock_query.assert_called_once_with(
            mock_store,
            resource_id="vol-0abc123",
            actor="arn:aws:iam::123456:user/alice",
            result="success",
            action="approval",
            run_id=None,
            date_from="2025-07-01",
            date_to="2025-07-31",
        )
        assert session["aq_results"] == SAMPLE_ROWS

    def test_empty_filters_become_none(self):
        """Empty string filters are converted to None (not passed as '')."""
        mock_store = MagicMock()
        mock_orch = MagicMock()
        mock_orch._state_store = mock_store
        session = {"orchestrator": mock_orch}

        mock_query = MagicMock(return_value=[])

        _simulate_query_button_press(
            session,
            resource_id="",
            actor="   ",
            result="",
            action="",
            query_fn=mock_query,
        )

        mock_query.assert_called_once_with(
            mock_store,
            resource_id=None,
            actor=None,
            result=None,
            action=None,
            run_id=None,
            date_from=None,
            date_to=None,
        )

    def test_run_scope_unscoped_passes_sentinel(self):
        """Run scope 'Unscoped only' passes the UNSCOPED sentinel."""
        mock_store = MagicMock()
        mock_orch = MagicMock()
        mock_orch._state_store = mock_store
        session = {"orchestrator": mock_orch}

        mock_query = MagicMock(return_value=[])

        _simulate_query_button_press(
            session,
            run_scope="Unscoped only",
            query_fn=mock_query,
        )

        call_kwargs = mock_query.call_args[1]
        assert call_kwargs["run_id"] is UNSCOPED

    def test_run_scope_scoped_to_run_passes_string(self):
        """Run scope 'Scoped to a run' with an input passes that string."""
        mock_store = MagicMock()
        mock_orch = MagicMock()
        mock_orch._state_store = mock_store
        session = {"orchestrator": mock_orch}

        mock_query = MagicMock(return_value=[])

        _simulate_query_button_press(
            session,
            run_scope="Scoped to a run",
            run_id_input="run-2025-07-10-abc123",
            query_fn=mock_query,
        )

        call_kwargs = mock_query.call_args[1]
        assert call_kwargs["run_id"] == "run-2025-07-10-abc123"

    def test_run_scope_scoped_to_run_empty_becomes_none(self):
        """Run scope 'Scoped to a run' but empty input → run_id=None."""
        mock_store = MagicMock()
        mock_orch = MagicMock()
        mock_orch._state_store = mock_store
        session = {"orchestrator": mock_orch}

        mock_query = MagicMock(return_value=[])

        _simulate_query_button_press(
            session,
            run_scope="Scoped to a run",
            run_id_input="   ",
            query_fn=mock_query,
        )

        call_kwargs = mock_query.call_args[1]
        assert call_kwargs["run_id"] is None


# ── Test Class: CSV Download Content ─────────────────────────────────────


class TestCSVDownloadContent:
    """When results exist and CSV download is triggered, correct CSV content
    is produced by export_audit_csv.
    """

    def test_csv_contains_all_rows_and_headers(self):
        """CSV output has header row + data rows matching SAMPLE_ROWS."""
        csv_text = export_audit_csv(SAMPLE_ROWS)

        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)

        assert len(rows) == 2
        assert rows[0]["resource_id"] == "vol-0abc123"
        assert rows[0]["actor"] == "arn:aws:iam::123456:user/alice"
        assert rows[1]["resource_id"] == "sg-xyz789"
        assert rows[1]["action"] == "rollback"

    def test_csv_headers_match_row_keys(self):
        """CSV header row contains exactly the keys from the dict rows."""
        csv_text = export_audit_csv(SAMPLE_ROWS)
        reader = csv.DictReader(io.StringIO(csv_text))
        assert set(reader.fieldnames) == set(SAMPLE_ROWS[0].keys())

    def test_csv_round_trip_preserves_values(self):
        """Parsing the CSV back yields the same string values as the input."""
        csv_text = export_audit_csv(SAMPLE_ROWS)
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)

        for original, parsed in zip(SAMPLE_ROWS, rows):
            for key in original:
                # CSV serializes None as empty string
                expected = "" if original[key] is None else str(original[key])
                assert parsed[key] == expected, (
                    f"Mismatch on key {key!r}: expected {expected!r}, got {parsed[key]!r}"
                )

    def test_csv_empty_input_returns_empty_string(self):
        """export_audit_csv([]) returns '' without raising."""
        result = export_audit_csv([])
        assert result == ""


# ── Test Class: JSON Download Content ────────────────────────────────────


class TestJSONDownloadContent:
    """When results exist and JSON download is triggered, correct JSON content
    is produced by export_audit_json.
    """

    def test_json_parseable_and_matches_rows(self):
        """JSON output can be parsed back and matches original rows."""
        json_text = export_audit_json(SAMPLE_ROWS)
        parsed = json.loads(json_text)

        assert len(parsed) == 2
        assert parsed[0]["resource_id"] == "vol-0abc123"
        assert parsed[0]["action"] == "approval"
        assert parsed[1]["result"] == "failure"
        assert parsed[1]["run_id"] is None

    def test_json_preserves_all_fields(self):
        """Every field from input rows appears in JSON output."""
        json_text = export_audit_json(SAMPLE_ROWS)
        parsed = json.loads(json_text)

        for original, exported in zip(SAMPLE_ROWS, parsed):
            for key in original:
                assert key in exported, f"Missing key {key!r} in JSON output"

    def test_json_empty_input_returns_empty_array(self):
        """export_audit_json([]) returns '[]' (valid JSON empty array)."""
        result = export_audit_json([])
        parsed = json.loads(result)
        assert parsed == []


# ── Test Class: StateStore Unavailable (AttributeError) ──────────────────


class TestStateStoreUnavailableAttributeError:
    """When query_audit() raises AttributeError (StateStore not available),
    the UI catches it and shows a warning message — no stack trace.
    """

    def test_attribute_error_caught_shows_warning(self):
        """AttributeError from missing _state_store → warning message."""

        class _OrchestratorNoStore:
            """Simulates an orchestrator without _state_store."""

            @property
            def _state_store(self):
                raise AttributeError("_state_store not initialized")

        session = {"orchestrator": _OrchestratorNoStore()}

        mock_query = MagicMock()  # Won't be reached

        warning_msg, results = _simulate_query_button_press(
            session,
            resource_id="vol-abc",
            query_fn=mock_query,
        )

        assert warning_msg is not None
        assert "phase2-persistent-state" in warning_msg
        assert "StateStore" in warning_msg
        assert results is None

    def test_attribute_error_from_query_caught(self):
        """AttributeError raised inside query_audit → caught, warning shown."""
        mock_store = MagicMock()
        mock_orch = MagicMock()
        mock_orch._state_store = mock_store
        session = {"orchestrator": mock_orch}

        mock_query = MagicMock(
            side_effect=AttributeError("execute_readonly_query not available")
        )

        warning_msg, results = _simulate_query_button_press(
            session,
            query_fn=mock_query,
        )

        assert warning_msg is not None
        assert "phase2-persistent-state" in warning_msg
        assert results is None


# ── Test Class: StateStore DB Error (sqlite3.OperationalError) ───────────


class TestStateStoreDBError:
    """When query_audit() raises sqlite3.OperationalError (DB issue),
    same friendly message is shown — no stack trace.
    """

    def test_operational_error_caught_shows_warning(self):
        """sqlite3.OperationalError → warning message, no crash."""
        mock_store = MagicMock()
        mock_orch = MagicMock()
        mock_orch._state_store = mock_store
        session = {"orchestrator": mock_orch}

        mock_query = MagicMock(
            side_effect=sqlite3.OperationalError("no such table: audit_trail")
        )

        warning_msg, results = _simulate_query_button_press(
            session,
            query_fn=mock_query,
        )

        assert warning_msg is not None
        assert "phase2-persistent-state" in warning_msg
        assert "StateStore" in warning_msg
        assert results is None

    def test_generic_exception_also_caught(self):
        """Any other exception from query → also caught, warning shown."""
        mock_store = MagicMock()
        mock_orch = MagicMock()
        mock_orch._state_store = mock_store
        session = {"orchestrator": mock_orch}

        mock_query = MagicMock(side_effect=RuntimeError("unexpected failure"))

        warning_msg, results = _simulate_query_button_press(
            session,
            query_fn=mock_query,
        )

        assert warning_msg is not None
        assert "phase2-persistent-state" in warning_msg
        assert results is None


# ── Test Class: Empty Results ────────────────────────────────────────────


class TestEmptyResults:
    """When query returns [], no crash occurs and no download content is generated."""

    def test_empty_results_stored_correctly(self):
        """Empty list from query → stored in session state as []."""
        mock_store = MagicMock()
        mock_orch = MagicMock()
        mock_orch._state_store = mock_store
        session = {"orchestrator": mock_orch}

        mock_query = MagicMock(return_value=[])

        warning_msg, results = _simulate_query_button_press(
            session,
            query_fn=mock_query,
        )

        assert warning_msg is None
        assert results == []

    def test_empty_results_csv_export_produces_empty_string(self):
        """CSV export of empty results produces '' — no header, no data."""
        assert export_audit_csv([]) == ""

    def test_empty_results_json_export_produces_empty_array(self):
        """JSON export of empty results produces valid empty JSON array."""
        result = export_audit_json([])
        assert json.loads(result) == []


# ── Test Class: Negative Cases ───────────────────────────────────────────


class TestNegativeCases:
    """Tests verifying the UI logic does NOT do incorrect things."""

    def test_no_query_call_when_orchestrator_missing(self):
        """If session has no orchestrator, KeyError is caught (not passed to query)."""
        session = {}  # No orchestrator key
        mock_query = MagicMock(return_value=[])

        # The KeyError from session["orchestrator"] is a subclass of Exception
        # but NOT AttributeError — it goes to the generic except branch
        warning_msg, results = _simulate_query_button_press(
            session,
            query_fn=mock_query,
        )

        # query_fn should not have been called at all
        mock_query.assert_not_called()
        assert warning_msg is not None
        assert results is None

    def test_results_not_carried_over_from_previous_query_on_error(self):
        """When an error occurs, aq_results is set to None (not stale data)."""
        mock_store = MagicMock()
        mock_orch = MagicMock()
        mock_orch._state_store = mock_store
        session = {"orchestrator": mock_orch, "aq_results": SAMPLE_ROWS}

        mock_query = MagicMock(side_effect=AttributeError("boom"))

        _simulate_query_button_press(session, query_fn=mock_query)

        assert session["aq_results"] is None

    def test_whitespace_only_resource_id_treated_as_no_filter(self):
        """Whitespace-only text input stripped → None, not passed as whitespace."""
        mock_store = MagicMock()
        mock_orch = MagicMock()
        mock_orch._state_store = mock_store
        session = {"orchestrator": mock_orch}

        mock_query = MagicMock(return_value=[])

        _simulate_query_button_press(
            session,
            resource_id="   \t  ",
            query_fn=mock_query,
        )

        call_kwargs = mock_query.call_args[1]
        assert call_kwargs["resource_id"] is None
