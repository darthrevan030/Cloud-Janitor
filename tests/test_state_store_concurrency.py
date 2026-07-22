"""Concurrency integration tests and property tests for StateStore WAL mode.

Task 4.1: Integration test harness — two StateStore instances on the same file,
           with three threads (plan writer, audit writer, rollback reader) hitting
           the DB simultaneously. Verifies no OperationalError surfaces under the
           configured busy_timeout, and verifies the data_loss path when contention
           exhausts append_audit_entry's retry budget.

Task 4.2: Property test (Property 5) — WAL Concurrent Reader/Writer Non-Blocking.
           Hypothesis generates random sequences of write operations, runs them on
           two concurrent StateStore instances, verifies all writes reflected in
           the final state.

**Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.6**
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cloud_janitor.agents.remediation_architect import DependencyReport, RemediationPlan
from cloud_janitor.core.state_store import StateStore
from cloud_janitor.orchestrator.orchestrator import AuditEntry

# ---------------------------------------------------------------------------
# Shared strategies (same as test_state_store_props.py)
# ---------------------------------------------------------------------------

_safe_text = st.text(
    min_size=1,
    max_size=40,
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
)

_resource_id_strategy = st.text(
    min_size=1,
    max_size=40,
    alphabet=st.characters(
        whitelist_categories=("L", "N", "Pd"),
        whitelist_characters="-_:/.",
        blacklist_characters="\x00",
    ),
)


# ---------------------------------------------------------------------------
# Task 4.1: Concurrency Integration Test Harness
# ---------------------------------------------------------------------------


class TestConcurrencyIntegration:
    """Two StateStore instances against the same file, three threads overlapping."""

    N_ITERATIONS = 20

    def test_concurrent_writers_and_reader_no_operational_error(
        self, tmp_path: Path
    ) -> None:
        """Req 6.1, 6.2, 6.3, 6.4: Spawn 3 threads (plan writer, audit writer,
        rollback reader) all hitting the same db file simultaneously for N iterations.
        Assert no OperationalError surfaces and final state reflects all writes.
        """
        db_path = tmp_path / "concurrent.db"
        store_a = StateStore(db_path, busy_timeout_ms=5000)
        store_b = StateStore(db_path, busy_timeout_ms=5000)

        errors: list[Exception] = []
        plan_write_count = 0
        audit_write_count = 0
        rollback_read_count = 0

        def plan_writer():
            """Thread 1: replaces plans using store_a."""
            nonlocal plan_write_count
            for i in range(self.N_ITERATIONS):
                plan = RemediationPlan(
                    resource_id=f"plan-resource-{i}",
                    finding={"resource_id": f"plan-resource-{i}", "category": "cost", "severity": "LOW"},
                    blocked=False,
                    block_reason="",
                    dependency_report=None,
                    remediation_hcl=f"resource \"aws_ebs_volume\" \"vol_{i}\" {{}}",
                    rollback_hcl=None,
                )
                try:
                    result = store_a.replace_plans([plan], f"run-{i}")
                    if result:
                        plan_write_count += 1
                except sqlite3.OperationalError as exc:
                    errors.append(exc)
                except Exception as exc:
                    errors.append(exc)

        def audit_writer():
            """Thread 2: appends audit entries using store_b."""
            nonlocal audit_write_count
            for i in range(self.N_ITERATIONS):
                entry = AuditEntry(
                    timestamp=f"2025-01-01T00:00:{i:02d}Z",
                    action="approve",
                    resource_id=f"audit-resource-{i}",
                    actor="test-actor",
                    result="success",
                    details=f"iteration {i}",
                )
                try:
                    result = store_b.append_audit_entry(entry, run_id=f"audit-run-{i}")
                    if result:
                        audit_write_count += 1
                except sqlite3.OperationalError as exc:
                    errors.append(exc)
                except Exception as exc:
                    errors.append(exc)

        def rollback_reader():
            """Thread 3: reads pending rollbacks using store_a."""
            nonlocal rollback_read_count
            for i in range(self.N_ITERATIONS):
                try:
                    # Add a rollback then immediately check it
                    store_a.add_pending_rollback(f"rollback-resource-{i}")
                    _exists = store_a.has_pending_rollback(f"rollback-resource-{i}")
                    rollback_read_count += 1
                except sqlite3.OperationalError as exc:
                    errors.append(exc)
                except Exception as exc:
                    errors.append(exc)

        threads = [
            threading.Thread(target=plan_writer, name="plan-writer"),
            threading.Thread(target=audit_writer, name="audit-writer"),
            threading.Thread(target=rollback_reader, name="rollback-reader"),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # Close stores
        store_a.close()
        store_b.close()

        # Assert no OperationalError surfaced
        operational_errors = [
            e for e in errors if isinstance(e, sqlite3.OperationalError)
        ]
        assert not operational_errors, (
            f"sqlite3.OperationalError(s) surfaced despite busy_timeout: "
            f"{operational_errors}"
        )

        # Assert no other unexpected exceptions
        assert not errors, f"Unexpected exception(s) during concurrent access: {errors}"

        # Assert final state reflects all writes
        assert plan_write_count == self.N_ITERATIONS, (
            f"Expected {self.N_ITERATIONS} successful plan writes, got {plan_write_count}"
        )
        assert audit_write_count == self.N_ITERATIONS, (
            f"Expected {self.N_ITERATIONS} successful audit writes, got {audit_write_count}"
        )
        assert rollback_read_count == self.N_ITERATIONS, (
            f"Expected {self.N_ITERATIONS} successful rollback reads, got {rollback_read_count}"
        )

        # Verify data integrity: open a fresh store and check the final state
        verify_store = StateStore(db_path)
        try:
            # Audit trail should have all N entries
            trail = verify_store.get_audit_trail()
            assert trail is not None, "get_audit_trail() returned None"
            assert len(trail) == self.N_ITERATIONS, (
                f"Expected {self.N_ITERATIONS} audit entries, got {len(trail)}"
            )

            # All rollback resources should exist
            for i in range(self.N_ITERATIONS):
                assert verify_store.has_pending_rollback(f"rollback-resource-{i}"), (
                    f"rollback-resource-{i} not found in pending_rollbacks"
                )
        finally:
            verify_store.close()

    def test_data_loss_path_when_contention_exhausts_retry_budget(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Req 6.6: Hold an exclusive lock on the DB long enough to exhaust
        append_audit_entry's retry budget (~1 second total across 3 attempts
        with 250ms busy_timeout each + backoff), then verify:
        1. append_audit_entry returns False
        2. A WARNING containing [data_loss] is logged
        """
        db_path = tmp_path / "data_loss.db"

        # Create the store and populate schema
        store = StateStore(db_path, busy_timeout_ms=5000)

        # Open a raw connection that holds an exclusive lock
        blocker_conn = sqlite3.connect(str(db_path), timeout=0)
        blocker_conn.execute("PRAGMA journal_mode=WAL")
        # BEGIN IMMEDIATE acquires a RESERVED lock, blocking writers but
        # allowing readers. For WAL mode, we need to actually write to block
        # other writers from committing.
        blocker_conn.execute("BEGIN IMMEDIATE")
        # Insert a dummy row to hold the write lock
        blocker_conn.execute(
            "INSERT INTO pending_rollbacks (resource_id, requested_at) "
            "VALUES ('blocker-sentinel', datetime('now'))"
        )
        # NOTE: We intentionally do NOT commit — this keeps the write lock held.

        entry = AuditEntry(
            timestamp="2025-01-01T00:00:00Z",
            action="approve",
            resource_id="blocked-resource",
            actor="test-actor",
            result="success",
            details="should fail due to lock contention",
        )

        with caplog.at_level(logging.WARNING, logger="cloud_janitor.core.state_store"):
            start = time.monotonic()
            result = store.append_audit_entry(entry, run_id="run-blocked")
            elapsed = time.monotonic() - start

        # Release the blocker
        blocker_conn.rollback()
        blocker_conn.close()
        store.close()

        # 1. append_audit_entry must return False
        assert result is False, (
            "append_audit_entry should return False when retry budget is exhausted"
        )

        # 2. The elapsed time should be bounded — roughly 1 second
        # (3 attempts x 250ms busy_timeout + backoff of 50ms + 150ms = ~950ms)
        # Allow up to 3 seconds for slow CI, but it must be > 0.2s (not instant)
        assert elapsed > 0.2, (
            f"append_audit_entry returned too quickly ({elapsed:.2f}s) — "
            f"retry loop may not have fired"
        )
        assert elapsed < 5.0, (
            f"append_audit_entry took too long ({elapsed:.2f}s) — "
            f"should be bounded to ~1 second, not using default 5000ms busy_timeout"
        )

        # 3. WARNING with [data_loss] marker must be logged
        data_loss_warnings = [
            record for record in caplog.records
            if record.levelname == "WARNING" and "[data_loss]" in record.getMessage()
        ]
        assert len(data_loss_warnings) >= 1, (
            f"Expected at least one WARNING with [data_loss] marker. "
            f"Log records: {[r.getMessage() for r in caplog.records]}"
        )


# ---------------------------------------------------------------------------
# Task 4.2: Property Test — WAL Concurrent Reader/Writer Non-Blocking
# ---------------------------------------------------------------------------


# Strategies for generating write operations
_write_op = st.one_of(
    # Plan write: (op_type, resource_id, run_id)
    st.tuples(
        st.just("plan"),
        _resource_id_strategy,
        _safe_text,
    ),
    # Audit write: (op_type, resource_id, action)
    st.tuples(
        st.just("audit"),
        _resource_id_strategy,
        st.sampled_from(["approve", "rollback", "deny", "scan"]),
    ),
    # Rollback write: (op_type, resource_id)
    st.tuples(
        st.just("rollback"),
        _resource_id_strategy,
    ),
)


class TestWALConcurrentReaderWriterNonBlocking:
    """Property 5: WAL Concurrent Reader/Writer Non-Blocking.

    For any random sequence of write operations distributed across two
    concurrent StateStore instances, all successful writes are reflected
    in the final database state and no sqlite3.OperationalError is raised.

    **Validates: Requirements 6.1, 6.2, 6.3**
    """

    @settings(max_examples=50, deadline=None)
    @given(
        ops_a=st.lists(_write_op, min_size=1, max_size=10),
        ops_b=st.lists(_write_op, min_size=1, max_size=10),
    )
    def test_concurrent_writes_all_reflected_in_final_state(
        self, ops_a: list, ops_b: list, tmp_path_factory
    ) -> None:
        """For any two sequences of write operations run concurrently on
        two StateStore instances pointing at the same file, every successful
        write is reflected in the final state read back from a third instance.
        """
        tmp_dir = tmp_path_factory.mktemp("wal_prop")
        db_path = tmp_dir / "prop_concurrent.db"

        store_a = StateStore(db_path, busy_timeout_ms=5000)
        store_b = StateStore(db_path, busy_timeout_ms=5000)

        # Track which writes succeeded
        successful_audits_a: list[str] = []
        successful_audits_b: list[str] = []
        successful_rollbacks: set[str] = set()
        errors: list[Exception] = []

        def execute_ops(store: StateStore, ops: list, audit_tracker: list):
            for op in ops:
                try:
                    if op[0] == "plan":
                        _, resource_id, run_id = op
                        plan = RemediationPlan(
                            resource_id=resource_id,
                            finding={"resource_id": resource_id, "category": "cost", "severity": "LOW"},
                            blocked=False,
                            block_reason="",
                            dependency_report=None,
                            remediation_hcl=f"resource {{}}",
                            rollback_hcl=None,
                        )
                        store.replace_plans([plan], run_id)
                    elif op[0] == "audit":
                        _, resource_id, action = op
                        entry = AuditEntry(
                            timestamp="2025-01-01T00:00:00Z",
                            action=action,
                            resource_id=resource_id,
                            actor="prop-test",
                            result="success",
                            details="property test",
                        )
                        result = store.append_audit_entry(entry, run_id="prop-run")
                        if result:
                            audit_tracker.append(resource_id)
                    elif op[0] == "rollback":
                        _, resource_id = op
                        result = store.add_pending_rollback(resource_id)
                        if result:
                            successful_rollbacks.add(resource_id)
                except sqlite3.OperationalError as exc:
                    errors.append(exc)
                except Exception as exc:
                    errors.append(exc)

        thread_a = threading.Thread(
            target=execute_ops,
            args=(store_a, ops_a, successful_audits_a),
            name="prop-writer-a",
        )
        thread_b = threading.Thread(
            target=execute_ops,
            args=(store_b, ops_b, successful_audits_b),
            name="prop-writer-b",
        )

        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=30)
        thread_b.join(timeout=30)

        store_a.close()
        store_b.close()

        # No OperationalError should have surfaced
        operational_errors = [
            e for e in errors if isinstance(e, sqlite3.OperationalError)
        ]
        assert not operational_errors, (
            f"sqlite3.OperationalError surfaced during concurrent writes: "
            f"{operational_errors}"
        )

        # Verify final state reflects all successful writes
        verify_store = StateStore(db_path)
        try:
            # All successful audit entries should be present
            trail = verify_store.get_audit_trail()
            assert trail is not None, "get_audit_trail() returned None"
            total_expected_audits = len(successful_audits_a) + len(successful_audits_b)
            assert len(trail) == total_expected_audits, (
                f"Expected {total_expected_audits} audit entries, got {len(trail)}. "
                f"Writes were lost!"
            )

            # All successful rollbacks should be present
            for resource_id in successful_rollbacks:
                assert verify_store.has_pending_rollback(resource_id), (
                    f"Pending rollback for {resource_id!r} was lost"
                )
        finally:
            verify_store.close()
