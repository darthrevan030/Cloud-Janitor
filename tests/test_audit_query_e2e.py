"""End-to-end test: _log_action() writes through a live StateStore and query_audit() sees it.

This test verifies the full integration path:
  1. A real StateStore instance (SQLite-backed, using tmp_path) creates the
     audit_trail table via its schema DDL.
  2. StateStore.append_audit_entry() writes an AuditEntry (simulating what
     Orchestrator._log_action() does internally).
  3. query_audit() reads the entry back through StateStore.execute_readonly_query().

Requirements tested: 1.1 (end-to-end verification)

Phase2-persistent-state dependency: This test was originally gated (xfail)
until phase2 shipped. Phase2's StateStore now exists and is functional —
the gate has been lifted and this test runs normally.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cloud_janitor.core.audit_query import UNSCOPED, query_audit
from cloud_janitor.core.state_store import StateStore
from cloud_janitor.orchestrator.orchestrator import AuditEntry


@pytest.fixture()
def live_state_store(tmp_path: Path) -> StateStore:
    """Create a real StateStore backed by a temporary SQLite DB."""
    db_path = tmp_path / "state.db"
    store = StateStore(db_path)
    yield store
    store.close()


class TestAuditQueryEndToEnd:
    """End-to-end: append_audit_entry -> query_audit round-trip via live StateStore."""

    def test_single_entry_round_trip(self, live_state_store: StateStore) -> None:
        """An entry written via append_audit_entry is visible to query_audit."""
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            action="approve",
            resource_id="vol-abc123",
            actor="arn:aws:iam::123456789012:user/ops-lead",
            result="applied",
            details="Remediation applied successfully",
        )
        run_id = "run-e2e-001"

        success = live_state_store.append_audit_entry(entry, run_id=run_id)
        assert success is True

        rows = query_audit(live_state_store, resource_id="vol-abc123")
        assert len(rows) == 1
        row = rows[0]
        assert row["action"] == "approve"
        assert row["resource_id"] == "vol-abc123"
        assert row["actor"] == "arn:aws:iam::123456789012:user/ops-lead"
        assert row["result"] == "applied"
        assert row["details"] == "Remediation applied successfully"
        assert row["run_id"] == run_id

    def test_multiple_entries_filtered(self, live_state_store: StateStore) -> None:
        """Multiple entries written; query_audit filters correctly."""
        entries = [
            (
                AuditEntry(
                    timestamp="2025-01-15T10:00:00+00:00",
                    action="approve",
                    resource_id="vol-001",
                    actor="user-a",
                    result="applied",
                    details="",
                ),
                "run-100",
            ),
            (
                AuditEntry(
                    timestamp="2025-01-15T11:00:00+00:00",
                    action="rollback",
                    resource_id="vol-002",
                    actor="user-b",
                    result="rolled_back",
                    details="",
                ),
                "run-100",
            ),
            (
                AuditEntry(
                    timestamp="2025-01-15T12:00:00+00:00",
                    action="approve",
                    resource_id="sg-003",
                    actor="user-a",
                    result="applied",
                    details="",
                ),
                None,  # unscoped entry
            ),
        ]
        for entry, rid in entries:
            assert live_state_store.append_audit_entry(entry, run_id=rid) is True

        # Filter by action
        approvals = query_audit(live_state_store, action="approve")
        assert len(approvals) == 2
        assert all(r["action"] == "approve" for r in approvals)

        # Filter by actor
        user_b_rows = query_audit(live_state_store, actor="user-b")
        assert len(user_b_rows) == 1
        assert user_b_rows[0]["resource_id"] == "vol-002"

        # Filter by run_id (exact match)
        run_100_rows = query_audit(live_state_store, run_id="run-100")
        assert len(run_100_rows) == 2

        # Filter by UNSCOPED (run_id IS NULL)
        unscoped_rows = query_audit(live_state_store, run_id=UNSCOPED)
        assert len(unscoped_rows) == 1
        assert unscoped_rows[0]["resource_id"] == "sg-003"
        assert unscoped_rows[0]["run_id"] is None

    def test_no_filter_returns_all_ordered_desc(self, live_state_store: StateStore) -> None:
        """No filters returns all entries ordered by timestamp descending."""
        timestamps = [
            "2025-01-01T01:00:00+00:00",
            "2025-01-01T03:00:00+00:00",
            "2025-01-01T02:00:00+00:00",
        ]
        for ts in timestamps:
            entry = AuditEntry(
                timestamp=ts,
                action="scan",
                resource_id="res-x",
                actor="system",
                result="completed",
                details="",
            )
            live_state_store.append_audit_entry(entry, run_id=None)

        rows = query_audit(live_state_store)
        assert len(rows) == 3
        # Should be ordered descending by timestamp
        assert rows[0]["timestamp"] == "2025-01-01T03:00:00+00:00"
        assert rows[1]["timestamp"] == "2025-01-01T02:00:00+00:00"
        assert rows[2]["timestamp"] == "2025-01-01T01:00:00+00:00"

    def test_combined_filters_and_conjunction(self, live_state_store: StateStore) -> None:
        """Multiple filters applied as AND conjunction."""
        entry_match = AuditEntry(
            timestamp="2025-06-01T10:00:00+00:00",
            action="approve",
            resource_id="vol-target",
            actor="admin",
            result="applied",
            details="matched",
        )
        entry_no_match = AuditEntry(
            timestamp="2025-06-01T11:00:00+00:00",
            action="approve",
            resource_id="vol-other",
            actor="admin",
            result="failed",
            details="not matched",
        )
        live_state_store.append_audit_entry(entry_match, run_id="run-x")
        live_state_store.append_audit_entry(entry_no_match, run_id="run-x")

        rows = query_audit(
            live_state_store,
            action="approve",
            actor="admin",
            result="applied",
            resource_id="vol-target",
        )
        assert len(rows) == 1
        assert rows[0]["details"] == "matched"

    def test_date_range_filter(self, live_state_store: StateStore) -> None:
        """date_from/date_to filters work against a live StateStore."""
        entries_data = [
            ("2025-03-01T08:00:00+00:00", "early"),
            ("2025-03-15T12:00:00+00:00", "middle"),
            ("2025-03-30T18:00:00+00:00", "late"),
        ]
        for ts, detail in entries_data:
            entry = AuditEntry(
                timestamp=ts,
                action="scan",
                resource_id="res-date-test",
                actor="cron",
                result="completed",
                details=detail,
            )
            live_state_store.append_audit_entry(entry, run_id=None)

        rows = query_audit(
            live_state_store,
            date_from="2025-03-10T00:00:00+00:00",
            date_to="2025-03-20T00:00:00+00:00",
        )
        assert len(rows) == 1
        assert rows[0]["details"] == "middle"

    def test_limit_respected(self, live_state_store: StateStore) -> None:
        """The limit parameter caps result count."""
        for i in range(5):
            entry = AuditEntry(
                timestamp=f"2025-04-01T{i:02d}:00:00+00:00",
                action="scan",
                resource_id=f"res-{i}",
                actor="system",
                result="completed",
                details="",
            )
            live_state_store.append_audit_entry(entry, run_id=None)

        rows = query_audit(live_state_store, limit=3)
        assert len(rows) == 3
