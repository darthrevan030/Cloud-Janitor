"""Unit tests for StateStore schema bootstrap, pragma configuration, and read-only query.

Covers:
- Req 1.1: sqlite3-backed StateStore, no external DB dependency
- Req 1.2: construction against non-existent path creates file + all 3 tables
- Req 1.3: PRAGMA journal_mode=WAL, PRAGMA busy_timeout configured
- Req 1.6: audit_trail is append-only — no update/delete methods exposed
- Req 1.8: execute_readonly_query returns rows and re-raises sqlite3.Error
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cloud_janitor.core.state_store import StateStore


class TestSchemaBootstrap:
    """Constructing StateStore against a non-existent path creates file + tables."""

    def test_creates_file_and_tables_on_non_existent_path(self, tmp_path: Path) -> None:
        """Req 1.2: StateStore(path) creates the file and all three tables
        without requiring any external migration tool or pre-existing schema."""
        db_path = tmp_path / "sub" / "state.db"
        assert not db_path.exists(), "precondition: file must not exist before construction"

        store = StateStore(db_path)
        try:
            # File must exist after construction
            assert db_path.exists(), "StateStore did not create the database file"

            # Query sqlite_master for table names
            rows = store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            table_names = sorted(row[0] for row in rows)

            # All three required tables must exist
            assert "audit_trail" in table_names, "audit_trail table not created"
            assert "pending_rollbacks" in table_names, "pending_rollbacks table not created"
            assert "plans" in table_names, "plans table not created"
        finally:
            store.close()

    def test_parent_directories_created(self, tmp_path: Path) -> None:
        """Construction must create intermediate directories (parents=True)."""
        db_path = tmp_path / "deep" / "nested" / "dir" / "state.db"
        store = StateStore(db_path)
        try:
            assert db_path.exists()
            assert db_path.parent.is_dir()
        finally:
            store.close()


class TestPragmaConfiguration:
    """PRAGMA journal_mode and busy_timeout are set correctly after construction."""

    def test_journal_mode_is_wal(self, tmp_path: Path) -> None:
        """Req 1.3: PRAGMA journal_mode readback confirms WAL."""
        db_path = tmp_path / "pragmas.db"
        store = StateStore(db_path)
        try:
            result = store._conn.execute("PRAGMA journal_mode").fetchone()
            assert result is not None, "PRAGMA journal_mode returned no row"
            assert result[0].lower() == "wal", (
                f"Expected journal_mode='wal', got '{result[0]}'"
            )
        finally:
            store.close()

    def test_busy_timeout_default(self, tmp_path: Path) -> None:
        """Req 1.3: PRAGMA busy_timeout readback confirms the configured value (default 5000)."""
        db_path = tmp_path / "pragmas.db"
        store = StateStore(db_path, busy_timeout_ms=5000)
        try:
            result = store._conn.execute("PRAGMA busy_timeout").fetchone()
            assert result is not None, "PRAGMA busy_timeout returned no row"
            assert result[0] == 5000, (
                f"Expected busy_timeout=5000, got {result[0]}"
            )
        finally:
            store.close()

    def test_busy_timeout_custom_value(self, tmp_path: Path) -> None:
        """busy_timeout readback reflects a non-default configured value."""
        db_path = tmp_path / "pragmas_custom.db"
        store = StateStore(db_path, busy_timeout_ms=12345)
        try:
            result = store._conn.execute("PRAGMA busy_timeout").fetchone()
            assert result is not None
            assert result[0] == 12345, (
                f"Expected busy_timeout=12345, got {result[0]}"
            )
        finally:
            store.close()


class TestAuditTrailAppendOnly:
    """StateStore exposes no public method that updates or deletes audit_trail rows."""

    def test_no_update_or_delete_audit_methods(self) -> None:
        """Req 1.6: introspect public method names — none touch audit_trail
        with update or delete semantics."""
        public_methods = [
            name for name in dir(StateStore)
            if callable(getattr(StateStore, name)) and not name.startswith("_")
        ]

        # Forbidden patterns: any public method suggesting update/delete on audit
        forbidden_substrings = [
            "update_audit",
            "delete_audit",
            "remove_audit",
            "clear_audit",
            "drop_audit",
            "purge_audit",
            "truncate_audit",
            "edit_audit",
            "modify_audit",
        ]

        violations = []
        for method_name in public_methods:
            for forbidden in forbidden_substrings:
                if forbidden in method_name:
                    violations.append(f"{method_name} matches '{forbidden}'")

        assert not violations, (
            f"StateStore exposes methods that violate audit_trail append-only constraint: "
            f"{violations}"
        )

    def test_public_methods_are_sensible(self) -> None:
        """Sanity check: StateStore has *some* public methods (not a vacuous pass)."""
        public_methods = [
            name for name in dir(StateStore)
            if callable(getattr(StateStore, name)) and not name.startswith("_")
        ]
        # Must have at least append_audit_entry and get_audit_trail
        assert "append_audit_entry" in public_methods, (
            "append_audit_entry not found — test may be checking wrong class"
        )
        assert "get_audit_trail" in public_methods, (
            "get_audit_trail not found — test may be checking wrong class"
        )
        assert "execute_readonly_query" in public_methods


class TestExecuteReadonlyQuery:
    """execute_readonly_query returns rows for parameterized SELECT and re-raises errors."""

    def test_returns_rows_for_parameterized_select(self, tmp_path: Path) -> None:
        """Req 1.8: seed audit_trail, query by resource_id, verify row returned."""
        db_path = tmp_path / "readonly_query.db"
        store = StateStore(db_path)
        try:
            # Seed a row directly via the connection (not through the public API,
            # to isolate execute_readonly_query from append_audit_entry logic)
            store._conn.execute(
                "INSERT INTO audit_trail (timestamp, actor, action, resource_id, result, details, run_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("2025-01-01T00:00:00Z", "test-actor", "approve", "test-id", "success", "details here", "run-1"),
            )
            store._conn.commit()

            # Call the method under test
            rows = store.execute_readonly_query(
                "SELECT resource_id, actor, action FROM audit_trail WHERE resource_id = ?",
                ("test-id",),
            )

            assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
            assert rows[0][0] == "test-id", f"resource_id mismatch: {rows[0][0]}"
            assert rows[0][1] == "test-actor", f"actor mismatch: {rows[0][1]}"
            assert rows[0][2] == "approve", f"action mismatch: {rows[0][2]}"
        finally:
            store.close()

    def test_returns_empty_list_when_no_match(self, tmp_path: Path) -> None:
        """Query against seeded table with non-matching param returns empty list."""
        db_path = tmp_path / "readonly_empty.db"
        store = StateStore(db_path)
        try:
            rows = store.execute_readonly_query(
                "SELECT * FROM audit_trail WHERE resource_id = ?",
                ("nonexistent-id",),
            )
            assert rows == [], f"Expected empty list, got {rows}"
        finally:
            store.close()

    def test_reraises_sqlite3_operational_error(self, tmp_path: Path) -> None:
        """Req 1.8: on sqlite3.Error, execute_readonly_query re-raises (does not swallow).

        sqlite3.Connection.execute is a C-extension slot and cannot be patched
        directly. Instead, we replace store._conn with a mock that raises on execute().
        """
        db_path = tmp_path / "reraise.db"
        store = StateStore(db_path)
        try:
            mock_conn = MagicMock()
            mock_conn.execute.side_effect = sqlite3.OperationalError("disk I/O error")
            store._conn = mock_conn

            with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
                store.execute_readonly_query("SELECT * FROM audit_trail", ())
        finally:
            store.close()

    def test_reraises_on_invalid_sql(self, tmp_path: Path) -> None:
        """A real sqlite3.OperationalError from malformed SQL is re-raised, not swallowed."""
        db_path = tmp_path / "reraise_real.db"
        store = StateStore(db_path)
        try:
            with pytest.raises(sqlite3.OperationalError):
                store.execute_readonly_query("SELECT * FROM nonexistent_table_xyz", ())
        finally:
            store.close()
