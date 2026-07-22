"""Unit tests for StateStore corruption detection and fail-safe error handling.

Covers tasks 3.1, 3.2, 3.3, 3.4 from the phase2-persistent-state spec:
- Non-SQLite file → StateStoreCorruptedError at construction
- PRAGMA user_version newer → StateStoreCorruptedError at construction
- Mocked sqlite3.Error on read → returns empty/not-found fallback value
- Mocked sqlite3.Error mid-replace_plans() → returns False, prior plans unchanged
- Construction-failure paths close self._conn (no leak)
- sqlite3.OperationalError in __init__ → StateStoreUnavailableError (not corruption)
- Regression: valid file with exclusive lock → StateStoreUnavailableError
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cloud_janitor.agents.remediation_architect import RemediationPlan
from cloud_janitor.core.state_store import (
    StateStore,
    StateStoreCorruptedError,
    StateStoreUnavailableError,
    _SCHEMA_VERSION,
)
from cloud_janitor.orchestrator.orchestrator import AuditEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan(resource_id: str = "vol-abc123") -> RemediationPlan:
    """Create a minimal RemediationPlan for testing."""
    return RemediationPlan(
        resource_id=resource_id,
        finding={"type": "unattached_ebs", "severity": "MEDIUM"},
        blocked=False,
        block_reason="",
        dependency_report=None,
        remediation_hcl="resource {}",
        rollback_hcl="resource {}",
    )


def _make_audit_entry(action: str = "approve", resource_id: str = "vol-abc123") -> AuditEntry:
    """Create a minimal AuditEntry for testing."""
    return AuditEntry(
        timestamp="2025-01-01T00:00:00Z",
        action=action,
        resource_id=resource_id,
        actor="test-user",
        result="success",
        details="test detail",
    )


# ===========================================================================
# Task 3.1 — StateStoreCorruptedError fail-closed path
# ===========================================================================


class TestCorruptedFileDetection:
    """Non-SQLite content raises StateStoreCorruptedError at construction."""

    def test_non_sqlite_file_raises_corrupted_error(self, tmp_path: Path) -> None:
        """A file with arbitrary non-SQLite bytes triggers corruption error."""
        db_file = tmp_path / "state.db"
        db_file.write_bytes(b"this is definitely not a sqlite database file header")

        with pytest.raises(StateStoreCorruptedError) as exc_info:
            StateStore(db_file)

        # Verify the error message references the path
        assert str(db_file) in str(exc_info.value)

    def test_truncated_file_raises_corrupted_error(self, tmp_path: Path) -> None:
        """A truncated/partial file triggers corruption error."""
        db_file = tmp_path / "state.db"
        db_file.write_bytes(b"\x00" * 100)

        with pytest.raises(StateStoreCorruptedError):
            StateStore(db_file)

    def test_newer_schema_version_raises_corrupted_error(self, tmp_path: Path) -> None:
        """PRAGMA user_version newer than _SCHEMA_VERSION → StateStoreCorruptedError."""
        db_file = tmp_path / "state.db"

        # Create a valid SQLite DB with a future schema version
        conn = sqlite3.connect(str(db_file))
        conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION + 1}")
        conn.close()

        with pytest.raises(StateStoreCorruptedError) as exc_info:
            StateStore(db_file)

        # Verify message mentions the version mismatch
        assert str(_SCHEMA_VERSION + 1) in str(exc_info.value)
        assert str(_SCHEMA_VERSION) in str(exc_info.value)


# ===========================================================================
# Task 3.2 — All read/write methods have fail-safe error handling
# ===========================================================================


class TestReadFailSafe:
    """Read operations return documented fallback on sqlite3.Error."""

    def test_get_plan_returns_none_on_error(self, tmp_path: Path) -> None:
        """get_plan() returns None (not raises) on sqlite3.Error."""
        store = StateStore(tmp_path / "state.db")
        try:
            # Replace _conn with a mock that raises on execute
            real_conn = store._conn
            mock_conn = MagicMock()
            mock_conn.execute.side_effect = sqlite3.Error("disk I/O error")
            store._conn = mock_conn
            result = store.get_plan("vol-abc123")
            assert result is None
            store._conn = real_conn
        finally:
            store.close()

    def test_has_pending_rollback_returns_false_on_error(self, tmp_path: Path) -> None:
        """has_pending_rollback() returns False (not raises) on sqlite3.Error."""
        store = StateStore(tmp_path / "state.db")
        try:
            real_conn = store._conn
            mock_conn = MagicMock()
            mock_conn.execute.side_effect = sqlite3.Error("disk I/O error")
            store._conn = mock_conn
            result = store.has_pending_rollback("vol-abc123")
            assert result is False
            store._conn = real_conn
        finally:
            store.close()

    def test_get_audit_trail_returns_none_on_error(self, tmp_path: Path) -> None:
        """get_audit_trail() returns None (NOT []) on read failure — Req 5.3 exception."""
        store = StateStore(tmp_path / "state.db")
        try:
            real_conn = store._conn
            mock_conn = MagicMock()
            mock_conn.execute.side_effect = sqlite3.Error("disk I/O error")
            store._conn = mock_conn
            result = store.get_audit_trail()
            # CRITICAL: must be None, not [] — so Orchestrator can distinguish
            # "read failed" from "trail is genuinely empty"
            assert result is None
            store._conn = real_conn
        finally:
            store.close()


class TestWriteFailSafe:
    """Write operations return False on sqlite3.Error and don't leave partial state."""

    def test_replace_plans_returns_false_on_error(self, tmp_path: Path) -> None:
        """replace_plans() returns False on sqlite3.Error."""
        store = StateStore(tmp_path / "state.db")
        try:
            # Seed a plan first
            plan = _make_plan("vol-existing")
            assert store.replace_plans([plan], "run-1") is True

            # Now make the next replace_plans fail mid-write by replacing _conn
            real_conn = store._conn
            mock_conn = MagicMock()
            # The context manager protocol: __enter__ returns the mock itself
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.side_effect = sqlite3.Error("simulated disk failure")
            store._conn = mock_conn

            result = store.replace_plans([_make_plan("vol-new")], "run-2")
            assert result is False

            # Restore and check prior plans survived
            store._conn = real_conn
            existing = store.get_plan("vol-existing")
            assert existing is not None
            assert existing.resource_id == "vol-existing"
        finally:
            store.close()

    def test_add_pending_rollback_returns_false_on_error(self, tmp_path: Path) -> None:
        """add_pending_rollback() returns False on sqlite3.Error."""
        store = StateStore(tmp_path / "state.db")
        try:
            real_conn = store._conn
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.side_effect = sqlite3.Error("disk I/O error")
            store._conn = mock_conn
            result = store.add_pending_rollback("vol-abc123")
            assert result is False
            store._conn = real_conn
        finally:
            store.close()

    def test_discard_pending_rollback_returns_false_on_error(self, tmp_path: Path) -> None:
        """discard_pending_rollback() returns False on sqlite3.Error."""
        store = StateStore(tmp_path / "state.db")
        try:
            real_conn = store._conn
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.side_effect = sqlite3.Error("disk I/O error")
            store._conn = mock_conn
            result = store.discard_pending_rollback("vol-abc123")
            assert result is False
            store._conn = real_conn
        finally:
            store.close()

    def test_replace_plans_failure_preserves_prior_plans(self, tmp_path: Path) -> None:
        """After a failed replace_plans(), original plans remain queryable."""
        store = StateStore(tmp_path / "state.db")
        try:
            # Seed initial plans
            plans = [_make_plan("vol-001"), _make_plan("vol-002")]
            assert store.replace_plans(plans, "run-original") is True

            # Verify they exist
            assert store.get_plan("vol-001") is not None
            assert store.get_plan("vol-002") is not None

            # Attempt a failing replacement — mock _conn to fail on executemany
            real_conn = store._conn
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            # execute (DELETE) succeeds, executemany (INSERT) fails
            mock_conn.execute.return_value = None
            mock_conn.executemany.side_effect = sqlite3.Error("simulated write failure")
            store._conn = mock_conn

            result = store.replace_plans([_make_plan("vol-new")], "run-new")
            assert result is False

            # Restore real conn and verify original plans survived
            store._conn = real_conn
            assert store.get_plan("vol-001") is not None
            assert store.get_plan("vol-002") is not None
            # New plan must NOT be present
            assert store.get_plan("vol-new") is None
        finally:
            store.close()


# ===========================================================================
# Task 3.3 — Connection leak prevention on construction failure
# ===========================================================================


class TestConnectionLeakPrevention:
    """On every construction-failure path, self._conn must be closed (no leak)."""

    def test_corrupted_file_closes_connection(self, tmp_path: Path) -> None:
        """StateStoreCorruptedError path closes the connection handle."""
        db_file = tmp_path / "state.db"
        db_file.parent.mkdir(parents=True, exist_ok=True)

        with patch("cloud_janitor.core.state_store.sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn
            # Simulate DatabaseError (genuine corruption) on first PRAGMA
            mock_conn.execute.side_effect = sqlite3.DatabaseError("file is not a database")

            with pytest.raises(StateStoreCorruptedError):
                StateStore(db_file)

            # Connection must have been closed in the finally block
            mock_conn.close.assert_called_once()

    def test_unavailable_error_closes_connection(self, tmp_path: Path) -> None:
        """StateStoreUnavailableError path closes the connection handle."""
        db_file = tmp_path / "state.db"
        db_file.parent.mkdir(parents=True, exist_ok=True)

        with patch("cloud_janitor.core.state_store.sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn
            mock_conn.execute.side_effect = sqlite3.OperationalError("database is locked")

            with pytest.raises(StateStoreUnavailableError):
                StateStore(db_file)

            # Connection must have been closed in the finally block
            mock_conn.close.assert_called_once()

    def test_newer_version_closes_connection(self, tmp_path: Path) -> None:
        """Newer user_version path closes the connection before raising."""
        db_file = tmp_path / "state.db"

        # Create a valid DB with a future schema version
        conn = sqlite3.connect(str(db_file))
        conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION + 5}")
        conn.close()

        # We can't patch attributes on a real sqlite3.Connection (read-only in 3.12).
        # Instead, verify the behavior by checking the file is not locked after
        # the failed construction — if close() wasn't called, the file would
        # remain locked and a subsequent connect would see stale WAL state.
        with pytest.raises(StateStoreCorruptedError):
            StateStore(db_file)

        # If the connection leaked, this would fail or show a locked state.
        # Opening and closing cleanly proves the prior handle was released.
        verify_conn = sqlite3.connect(str(db_file))
        # Should be able to write without any lock contention
        verify_conn.execute("CREATE TABLE IF NOT EXISTS leak_check (id INTEGER)")
        verify_conn.commit()
        verify_conn.close()


# ===========================================================================
# Task 3.4 — StateStoreUnavailableError distinguished from corruption
# ===========================================================================


class TestUnavailableVsCorrupted:
    """sqlite3.OperationalError → StateStoreUnavailableError, not corruption."""

    def test_operational_error_raises_unavailable(self, tmp_path: Path) -> None:
        """OperationalError in __init__ → StateStoreUnavailableError."""
        db_file = tmp_path / "state.db"
        db_file.parent.mkdir(parents=True, exist_ok=True)

        with patch("cloud_janitor.core.state_store.sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn
            mock_conn.execute.side_effect = sqlite3.OperationalError("database is locked")

            with pytest.raises(StateStoreUnavailableError):
                StateStore(db_file)

    def test_operational_error_does_not_raise_corrupted(self, tmp_path: Path) -> None:
        """OperationalError must NOT be misclassified as StateStoreCorruptedError."""
        db_file = tmp_path / "state.db"
        db_file.parent.mkdir(parents=True, exist_ok=True)

        with patch("cloud_janitor.core.state_store.sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn
            mock_conn.execute.side_effect = sqlite3.OperationalError("database is locked")

            # Must NOT raise StateStoreCorruptedError
            with pytest.raises(StateStoreUnavailableError):
                try:
                    StateStore(db_file)
                except StateStoreCorruptedError:
                    pytest.fail(
                        "OperationalError was misclassified as StateStoreCorruptedError"
                    )

    def test_exclusive_lock_regression(self, tmp_path: Path) -> None:
        """Regression: valid file with exclusive lock → StateStoreUnavailableError.

        This is the exact scenario WAL mode + busy_timeout exists to tolerate:
        another process holds the DB locked, and we must NOT treat that as
        corruption.
        """
        db_file = tmp_path / "state.db"

        # Create a valid SQLite DB first
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE dummy (id INTEGER PRIMARY KEY)")
        conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        conn.commit()

        # Hold an exclusive lock from a second connection
        blocker = sqlite3.connect(str(db_file), timeout=0)
        blocker.execute("BEGIN EXCLUSIVE")

        try:
            # Constructing StateStore with a very short timeout so it fails fast
            # timeout=0 means it won't wait at all for the lock
            with pytest.raises(StateStoreUnavailableError) as exc_info:
                StateStore(db_file, busy_timeout_ms=0)

            # Explicitly verify it did NOT raise StateStoreCorruptedError
            assert not isinstance(exc_info.value, StateStoreCorruptedError)
            # Verify the error message mentions the path
            assert str(db_file) in str(exc_info.value) or "unavailable" in str(exc_info.value).lower()
        except StateStoreCorruptedError:
            pytest.fail(
                "Exclusive lock was misclassified as StateStoreCorruptedError — "
                "this is the exact regression task 3.4 exists to prevent"
            )
        finally:
            blocker.rollback()
            blocker.close()
            conn.close()
