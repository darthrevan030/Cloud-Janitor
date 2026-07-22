"""Unit tests for core/audit_query.py against an in-memory SQLite fixture.

Exercises query_audit(), export_audit_csv(), export_audit_json() against a
throwaway sqlite3.Connection with a table matching phase2's assumed
audit_trail schema, wrapped in a minimal stand-in exposing
execute_readonly_query(sql, params).

Requirements tested: 1.1, 1.2, 1.3, 1.4, 1.7, 1.8
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3

import pytest

from cloud_janitor.core.audit_query import (
    UNSCOPED,
    _ALLOWED_FILTERS,
    export_audit_csv,
    export_audit_json,
    query_audit,
)


# ---------------------------------------------------------------------------
# Fixture: minimal StateStore stand-in backed by an in-memory SQLite DB
# ---------------------------------------------------------------------------


class _FakeStateStore:
    """Minimal stand-in exposing execute_readonly_query(sql, params).

    Mirrors phase2's StateStore method signature exactly so query_audit()
    is exercised the same way it will be against a real StateStore.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute_readonly_query(self, sql: str, params: tuple = ()) -> list[tuple]:
        cur = self._conn.execute(sql, params)
        return cur.fetchall()


_CREATE_TABLE = """
CREATE TABLE audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    result TEXT NOT NULL,
    details TEXT NOT NULL,
    run_id TEXT
)
"""

# Seed data — varied across all filter dimensions, including NULL run_id rows
_SEED_ROWS = [
    # (timestamp, actor, action, resource_id, result, details, run_id)
    ("2024-01-10T10:00:00Z", "alice", "approve", "vol-001", "success", "approved ebs", "run-abc"),
    ("2024-01-09T09:00:00Z", "bob", "rollback", "sg-002", "failure", "rollback failed", "run-abc"),
    ("2024-01-08T08:00:00Z", "alice", "approve", "cache-003", "success", "approved cache", "run-def"),
    ("2024-01-07T07:00:00Z", "carol", "scan", "vol-004", "success", "scan completed", None),
    ("2024-01-06T06:00:00Z", "bob", "approve", "sg-005", "success", "approved sg", None),
    ("2024-01-05T05:00:00Z", "carol", "rollback", "vol-001", "failure", "rollback timeout", "run-ghi"),
]


@pytest.fixture()
def store() -> _FakeStateStore:
    """Create an in-memory SQLite DB seeded with test rows."""
    conn = sqlite3.connect(":memory:")
    conn.execute(_CREATE_TABLE)
    conn.executemany(
        "INSERT INTO audit_trail (timestamp, actor, action, resource_id, result, details, run_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        _SEED_ROWS,
    )
    conn.commit()
    return _FakeStateStore(conn)


# ---------------------------------------------------------------------------
# Req 1.1 / 1.2: Single-filter and multi-filter (AND) queries
# ---------------------------------------------------------------------------


class TestSingleFilter:
    """Single-filter queries return exactly the matching rows."""

    def test_filter_by_actor(self, store: _FakeStateStore) -> None:
        rows = query_audit(store, actor="alice")
        assert len(rows) == 2
        assert all(r["actor"] == "alice" for r in rows)

    def test_filter_by_resource_id(self, store: _FakeStateStore) -> None:
        rows = query_audit(store, resource_id="vol-001")
        assert len(rows) == 2
        assert all(r["resource_id"] == "vol-001" for r in rows)

    def test_filter_by_result(self, store: _FakeStateStore) -> None:
        rows = query_audit(store, result="failure")
        assert len(rows) == 2
        assert all(r["result"] == "failure" for r in rows)

    def test_filter_by_action(self, store: _FakeStateStore) -> None:
        rows = query_audit(store, action="approve")
        assert len(rows) == 3
        assert all(r["action"] == "approve" for r in rows)

    def test_filter_no_match(self, store: _FakeStateStore) -> None:
        rows = query_audit(store, actor="nobody")
        assert rows == []


class TestMultiFilter:
    """Multi-filter (AND) queries return only rows matching ALL filters."""

    def test_actor_and_action(self, store: _FakeStateStore) -> None:
        rows = query_audit(store, actor="alice", action="approve")
        assert len(rows) == 2
        assert all(r["actor"] == "alice" and r["action"] == "approve" for r in rows)

    def test_actor_and_result(self, store: _FakeStateStore) -> None:
        rows = query_audit(store, actor="bob", result="failure")
        assert len(rows) == 1
        assert rows[0]["resource_id"] == "sg-002"

    def test_three_filters(self, store: _FakeStateStore) -> None:
        rows = query_audit(store, actor="carol", action="rollback", result="failure")
        assert len(rows) == 1
        assert rows[0]["resource_id"] == "vol-001"
        assert rows[0]["run_id"] == "run-ghi"

    def test_multi_filter_no_match(self, store: _FakeStateStore) -> None:
        rows = query_audit(store, actor="alice", result="failure")
        assert rows == []


# ---------------------------------------------------------------------------
# Req 1.3: No-filter returns most-recent-limit ordered descending
# ---------------------------------------------------------------------------


class TestNoFilter:
    """No-filter query returns most-recent-limit ordered by timestamp DESC."""

    def test_returns_all_rows_ordered_descending(self, store: _FakeStateStore) -> None:
        rows = query_audit(store)
        assert len(rows) == 6
        timestamps = [r["timestamp"] for r in rows]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_limit_respected(self, store: _FakeStateStore) -> None:
        rows = query_audit(store, limit=3)
        assert len(rows) == 3
        # Should be the 3 most recent
        assert rows[0]["timestamp"] == "2024-01-10T10:00:00Z"
        assert rows[2]["timestamp"] == "2024-01-08T08:00:00Z"


# ---------------------------------------------------------------------------
# Req 1.4: export_audit_csv / export_audit_json round-trip
# ---------------------------------------------------------------------------


class TestExportRoundTrip:
    """Exporting then re-parsing reproduces the same row set."""

    def test_csv_round_trip(self, store: _FakeStateStore) -> None:
        rows = query_audit(store, actor="alice")
        csv_text = export_audit_csv(rows)
        reader = csv.DictReader(io.StringIO(csv_text))
        parsed = list(reader)
        assert len(parsed) == 2
        for original, restored in zip(rows, parsed):
            for key in original:
                # CSV serializes None as empty string
                expected = "" if original[key] is None else str(original[key])
                assert restored[key] == expected

    def test_json_round_trip(self, store: _FakeStateStore) -> None:
        rows = query_audit(store, action="rollback")
        json_text = export_audit_json(rows)
        parsed = json.loads(json_text)
        assert parsed == rows

    def test_csv_empty_result_returns_empty_string(self) -> None:
        """export_audit_csv([]) returns '' without raising."""
        assert export_audit_csv([]) == ""

    def test_json_empty_result(self) -> None:
        """export_audit_json([]) returns valid JSON for empty list."""
        result = export_audit_json([])
        assert json.loads(result) == []


# ---------------------------------------------------------------------------
# Req 1.1: SQL-injection-shaped filter values treated as literals
# ---------------------------------------------------------------------------


class TestSQLInjectionPrevention:
    """SQL-injection-shaped values are treated as literal string filters."""

    def test_injection_in_resource_id(self, store: _FakeStateStore) -> None:
        malicious = "x'; DROP TABLE audit_trail; --"
        # Should not raise and should return empty (no row has that resource_id)
        rows = query_audit(store, resource_id=malicious)
        assert rows == []
        # Verify the table still exists by querying it
        all_rows = query_audit(store)
        assert len(all_rows) == 6

    def test_injection_in_actor(self, store: _FakeStateStore) -> None:
        malicious = "' OR '1'='1"
        rows = query_audit(store, actor=malicious)
        assert rows == []
        # Table intact
        assert len(query_audit(store)) == 6


# ---------------------------------------------------------------------------
# Req 1.7: NULL run_id filtering (UNSCOPED sentinel)
# ---------------------------------------------------------------------------


class TestRunIdFiltering:
    """Three-way run_id semantics: None (no filter), UNSCOPED (IS NULL), string (exact)."""

    def test_default_no_filter_returns_all(self, store: _FakeStateStore) -> None:
        """run_id=None (default) returns rows regardless of run_id value."""
        rows = query_audit(store)
        assert len(rows) == 6
        # Should include both NULL and non-NULL run_id rows
        run_ids = {r["run_id"] for r in rows}
        assert None in run_ids
        assert "run-abc" in run_ids

    def test_unscoped_returns_only_null_run_id(self, store: _FakeStateStore) -> None:
        """run_id=UNSCOPED returns only rows where run_id IS NULL."""
        rows = query_audit(store, run_id=UNSCOPED)
        assert len(rows) == 2
        assert all(r["run_id"] is None for r in rows)
        # Verify we got the right rows
        resource_ids = {r["resource_id"] for r in rows}
        assert resource_ids == {"vol-004", "sg-005"}

    def test_exact_run_id_returns_matching(self, store: _FakeStateStore) -> None:
        """run_id='run-abc' returns only rows with that exact run_id."""
        rows = query_audit(store, run_id="run-abc")
        assert len(rows) == 2
        assert all(r["run_id"] == "run-abc" for r in rows)

    def test_run_id_combined_with_other_filter(self, store: _FakeStateStore) -> None:
        """run_id filter combines with other filters via AND."""
        rows = query_audit(store, run_id="run-abc", actor="bob")
        assert len(rows) == 1
        assert rows[0]["action"] == "rollback"
        assert rows[0]["resource_id"] == "sg-002"

    def test_unscoped_combined_with_actor(self, store: _FakeStateStore) -> None:
        """UNSCOPED + actor filter narrows correctly."""
        rows = query_audit(store, run_id=UNSCOPED, actor="carol")
        assert len(rows) == 1
        assert rows[0]["resource_id"] == "vol-004"


# ---------------------------------------------------------------------------
# Req 1.8: Filter allowlist guard
# ---------------------------------------------------------------------------


class TestFilterAllowlistGuard:
    """_ALLOWED_FILTERS is consulted actively; violations raise ValueError."""

    def test_monkeypatch_removes_actor_raises(self, store: _FakeStateStore, monkeypatch) -> None:
        """Removing 'actor' from _ALLOWED_FILTERS causes ValueError when filtering by actor."""
        reduced = _ALLOWED_FILTERS - {"actor"}
        monkeypatch.setattr(
            "cloud_janitor.core.audit_query._ALLOWED_FILTERS", reduced
        )
        with pytest.raises(ValueError, match="Unrecognized audit_trail filter"):
            query_audit(store, actor="alice")

    def test_monkeypatch_removes_run_id_raises(self, store: _FakeStateStore, monkeypatch) -> None:
        """Removing 'run_id' from _ALLOWED_FILTERS causes ValueError."""
        reduced = _ALLOWED_FILTERS - {"run_id"}
        monkeypatch.setattr(
            "cloud_janitor.core.audit_query._ALLOWED_FILTERS", reduced
        )
        with pytest.raises(ValueError, match="Unrecognized audit_trail filter"):
            query_audit(store, run_id="run-abc")

    def test_original_allowlist_does_not_raise(self, store: _FakeStateStore) -> None:
        """With the real _ALLOWED_FILTERS intact, no ValueError is raised."""
        # Should not raise
        query_audit(store, actor="alice", resource_id="vol-001", result="success", action="approve")


# ---------------------------------------------------------------------------
# Schema validation: returned dicts have correct keys and types
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    """Returned row dicts contain all expected keys."""

    def test_row_has_all_columns(self, store: _FakeStateStore) -> None:
        rows = query_audit(store, limit=1)
        assert len(rows) == 1
        row = rows[0]
        expected_keys = {"id", "timestamp", "actor", "action", "resource_id", "result", "details", "run_id"}
        assert set(row.keys()) == expected_keys

    def test_id_is_integer(self, store: _FakeStateStore) -> None:
        rows = query_audit(store, limit=1)
        assert isinstance(rows[0]["id"], int)
