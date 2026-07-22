"""Property-based tests for core/state_store.py — StateStore persistence contracts.

Uses Hypothesis to validate universal correctness properties for plan
persistence round-trips and audit trail append-only ordering, plus a
deterministic test for zero-migration bootstrap.

**Validates: Requirements 1.2, 1.4, 1.6, 2.4, 4.3, 4.5, 7.2**
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from cloud_janitor.agents.remediation_architect import DependencyReport, RemediationPlan
from cloud_janitor.core.state_store import StateStore
from cloud_janitor.orchestrator.orchestrator import AuditEntry

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Safe text strategy: no null bytes (Windows rejects), no surrogates.
_safe_text = st.text(
    min_size=1,
    max_size=80,
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
)

# Resource IDs — non-empty, unique-friendly identifiers.
_resource_id_strategy = st.text(
    min_size=1,
    max_size=60,
    alphabet=st.characters(
        whitelist_categories=("L", "N", "Pd"),
        whitelist_characters="-_:/.",
        blacklist_characters="\x00",
    ),
)

# Finding dict — always contains at least one key to avoid trivial empty dicts.
_finding_strategy = st.fixed_dictionaries(
    {
        "resource_id": _resource_id_strategy,
        "category": st.sampled_from(["cost", "security", "compliance"]),
        "severity": st.sampled_from(["LOW", "MEDIUM", "HIGH", "CRITICAL"]),
    },
    optional={"description": _safe_text},
)

# DependencyReport strategy
_dependency_report_strategy = st.one_of(
    st.none(),
    st.builds(
        DependencyReport,
        resource_id=_resource_id_strategy,
        has_dependencies=st.booleans(),
        dependencies=st.lists(_safe_text, min_size=0, max_size=3),
        recommendation=_safe_text,
        checked_at=_safe_text,
    ),
)

# HCL text strategy (nullable)
_hcl_strategy = st.one_of(
    st.none(),
    st.text(
        min_size=1,
        max_size=200,
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    ),
)

# Full RemediationPlan strategy — blocked=False so get_plan() returns them
# (blocked plans are filtered out by get_plan, which is the correct behavior).
_plan_strategy = st.builds(
    RemediationPlan,
    resource_id=_resource_id_strategy,
    finding=_finding_strategy,
    blocked=st.just(False),
    block_reason=st.just(""),
    dependency_report=_dependency_report_strategy,
    remediation_hcl=_hcl_strategy,
    rollback_hcl=_hcl_strategy,
)


def _plans_with_unique_ids(plans: list[RemediationPlan]) -> list[RemediationPlan]:
    """Deduplicate plans by resource_id, keeping last occurrence.

    SQLite PRIMARY KEY on resource_id means duplicate IDs in a single
    executemany batch will fail or silently overwrite. We deduplicate
    upfront so the property focuses on the round-trip correctness, not
    on duplicate-key semantics.
    """
    seen: dict[str, RemediationPlan] = {}
    for p in plans:
        seen[p.resource_id] = p
    return list(seen.values())


# AuditEntry strategy
_audit_entry_strategy = st.builds(
    AuditEntry,
    timestamp=_safe_text,
    action=st.sampled_from(["approve", "rollback", "deny", "scan", "remediate"]),
    resource_id=_resource_id_strategy,
    actor=_safe_text,
    result=st.sampled_from(["success", "failure", "blocked", "skipped"]),
    details=_safe_text,
)


# ---------------------------------------------------------------------------
# Property 1: Plan Persistence Round Trip
# ---------------------------------------------------------------------------


class TestPlanPersistenceRoundTrip:
    """Property 1: For any list of RemediationPlan objects, replace_plans()
    then get_plan() for each plan's resource_id returns an equivalent plan
    (field-for-field match).

    This validates that the SQLite serialization/deserialization path is
    lossless: every field stored in the plans table can be reconstructed
    into an identical RemediationPlan instance.

    **Validates: Requirements 1.4, 2.4**
    """

    @settings(max_examples=100, deadline=None)
    @given(plans=st.lists(_plan_strategy, min_size=1, max_size=15))
    def test_replace_then_get_returns_equivalent_plans(self, plans):
        """For any batch of non-blocked plans with unique resource_ids,
        replace_plans() followed by get_plan() for each ID yields an
        identical RemediationPlan (field-for-field).
        """
        plans = _plans_with_unique_ids(plans)

        tmp_dir = tempfile.mkdtemp()
        db_path = Path(tmp_dir) / "test_roundtrip.db"
        store = StateStore(db_path)
        try:
            run_id = "test-run-001"
            success = store.replace_plans(plans, run_id)
            assert success is True, "replace_plans() must return True on success"

            for original in plans:
                retrieved = store.get_plan(original.resource_id)

                # Must not be None — we stored a non-blocked plan
                assert retrieved is not None, (
                    f"get_plan({original.resource_id!r}) returned None — "
                    f"plan was lost during persistence"
                )

                # Field-by-field equality
                assert retrieved.resource_id == original.resource_id, (
                    f"resource_id mismatch: {retrieved.resource_id!r} != {original.resource_id!r}"
                )
                assert retrieved.finding == original.finding, (
                    f"finding mismatch for {original.resource_id!r}: "
                    f"{retrieved.finding!r} != {original.finding!r}"
                )
                assert retrieved.blocked == original.blocked, (
                    f"blocked mismatch for {original.resource_id!r}"
                )
                assert retrieved.block_reason == original.block_reason, (
                    f"block_reason mismatch for {original.resource_id!r}"
                )
                assert retrieved.remediation_hcl == original.remediation_hcl, (
                    f"remediation_hcl mismatch for {original.resource_id!r}: "
                    f"{retrieved.remediation_hcl!r} != {original.remediation_hcl!r}"
                )
                assert retrieved.rollback_hcl == original.rollback_hcl, (
                    f"rollback_hcl mismatch for {original.resource_id!r}: "
                    f"{retrieved.rollback_hcl!r} != {original.rollback_hcl!r}"
                )

                # DependencyReport round-trip
                if original.dependency_report is None:
                    assert retrieved.dependency_report is None, (
                        f"dependency_report should be None for {original.resource_id!r}, "
                        f"got {retrieved.dependency_report!r}"
                    )
                else:
                    assert retrieved.dependency_report is not None, (
                        f"dependency_report is None for {original.resource_id!r} but "
                        f"original had {original.dependency_report!r}"
                    )
                    assert retrieved.dependency_report.resource_id == original.dependency_report.resource_id
                    assert retrieved.dependency_report.has_dependencies == original.dependency_report.has_dependencies
                    assert retrieved.dependency_report.dependencies == original.dependency_report.dependencies
                    assert retrieved.dependency_report.recommendation == original.dependency_report.recommendation
                    assert retrieved.dependency_report.checked_at == original.dependency_report.checked_at
        finally:
            store.close()

    @settings(max_examples=50, deadline=None)
    @given(plans=st.lists(_plan_strategy, min_size=1, max_size=10))
    def test_replace_plans_is_wholesale_not_merge(self, plans):
        """replace_plans() replaces ALL prior plans — no leftover plans from
        a previous batch survive. This validates Req 2.5 wholesale semantics.
        """
        plans = _plans_with_unique_ids(plans)

        tmp_dir = tempfile.mkdtemp()
        db_path = Path(tmp_dir) / "test_wholesale.db"
        store = StateStore(db_path)
        try:
            # First batch: insert a sentinel plan
            sentinel = RemediationPlan(
                resource_id="sentinel-should-vanish",
                finding={"resource_id": "sentinel", "category": "cost", "severity": "LOW"},
                blocked=False,
                block_reason="",
                dependency_report=None,
                remediation_hcl="resource {}",
                rollback_hcl=None,
            )
            store.replace_plans([sentinel], "run-0")

            # Second batch: replace with generated plans (none have sentinel ID)
            store.replace_plans(plans, "run-1")

            # Sentinel must be gone
            assert store.get_plan("sentinel-should-vanish") is None, (
                "replace_plans() did not remove prior plans — sentinel survived"
            )
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Property 4: Audit Trail Append-Only Ordering
# ---------------------------------------------------------------------------


class TestAuditTrailAppendOnlyOrdering:
    """Property 4: For any sequence of append_audit_entry() calls,
    get_audit_trail() returns entries in the same order they were appended.

    This validates the AUTOINCREMENT id column's monotonic ordering and
    the ORDER BY id ASC in get_audit_trail().

    **Validates: Requirements 1.6, 4.3, 4.5**
    """

    @settings(max_examples=100, deadline=None)
    @given(entries=st.lists(_audit_entry_strategy, min_size=1, max_size=20))
    def test_audit_trail_preserves_insertion_order(self, entries):
        """For any sequence of AuditEntry objects appended one by one,
        get_audit_trail() returns them in exactly the same order.
        """
        tmp_dir = tempfile.mkdtemp()
        db_path = Path(tmp_dir) / "test_audit_order.db"
        store = StateStore(db_path)
        try:
            for entry in entries:
                success = store.append_audit_entry(entry, run_id="run-audit-test")
                assert success is True, (
                    f"append_audit_entry() failed for entry with "
                    f"resource_id={entry.resource_id!r}"
                )

            trail = store.get_audit_trail()
            assert trail is not None, "get_audit_trail() returned None unexpectedly"
            assert len(trail) == len(entries), (
                f"Expected {len(entries)} audit entries, got {len(trail)}"
            )

            # Verify ordering: each entry at position i matches the i-th
            # appended entry on all AuditEntry fields.
            for i, (original, retrieved) in enumerate(zip(entries, trail)):
                assert retrieved.timestamp == original.timestamp, (
                    f"Entry [{i}] timestamp mismatch: "
                    f"{retrieved.timestamp!r} != {original.timestamp!r}"
                )
                assert retrieved.action == original.action, (
                    f"Entry [{i}] action mismatch: "
                    f"{retrieved.action!r} != {original.action!r}"
                )
                assert retrieved.resource_id == original.resource_id, (
                    f"Entry [{i}] resource_id mismatch: "
                    f"{retrieved.resource_id!r} != {original.resource_id!r}"
                )
                assert retrieved.actor == original.actor, (
                    f"Entry [{i}] actor mismatch: "
                    f"{retrieved.actor!r} != {original.actor!r}"
                )
                assert retrieved.result == original.result, (
                    f"Entry [{i}] result mismatch: "
                    f"{retrieved.result!r} != {original.result!r}"
                )
                assert retrieved.details == original.details, (
                    f"Entry [{i}] details mismatch: "
                    f"{retrieved.details!r} != {original.details!r}"
                )
        finally:
            store.close()

    @settings(max_examples=50, deadline=None)
    @given(entries=st.lists(_audit_entry_strategy, min_size=2, max_size=15))
    def test_audit_trail_is_append_only_no_overwrites(self, entries):
        """Appending new entries never modifies previously appended entries.
        Verifying this by appending half, reading, appending the rest,
        reading again, and confirming the first half is unchanged.
        """
        tmp_dir = tempfile.mkdtemp()
        db_path = Path(tmp_dir) / "test_audit_append_only.db"
        store = StateStore(db_path)
        try:
            midpoint = len(entries) // 2
            first_half = entries[:midpoint]
            second_half = entries[midpoint:]

            # Append first half
            for entry in first_half:
                store.append_audit_entry(entry, run_id="run-1")

            # Snapshot the trail after first batch
            trail_after_first = store.get_audit_trail()
            assert trail_after_first is not None
            assert len(trail_after_first) == len(first_half)

            # Append second half
            for entry in second_half:
                store.append_audit_entry(entry, run_id="run-2")

            # Full trail must start with the exact same first-half entries
            trail_full = store.get_audit_trail()
            assert trail_full is not None
            assert len(trail_full) == len(entries), (
                f"Expected {len(entries)} total entries, got {len(trail_full)}"
            )

            # First half entries must be unchanged
            for i, (before, after) in enumerate(
                zip(trail_after_first, trail_full[:midpoint])
            ):
                assert before.timestamp == after.timestamp, (
                    f"Entry [{i}] timestamp was modified after subsequent appends"
                )
                assert before.action == after.action, (
                    f"Entry [{i}] action was modified after subsequent appends"
                )
                assert before.resource_id == after.resource_id, (
                    f"Entry [{i}] resource_id was modified after subsequent appends"
                )
                assert before.actor == after.actor, (
                    f"Entry [{i}] actor was modified after subsequent appends"
                )
                assert before.result == after.result, (
                    f"Entry [{i}] result was modified after subsequent appends"
                )
                assert before.details == after.details, (
                    f"Entry [{i}] details was modified after subsequent appends"
                )
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Property 6: Fresh Store Zero-Migration Bootstrap
# ---------------------------------------------------------------------------


class TestFreshStoreZeroMigrationBootstrap:
    """Property 6: Constructing a StateStore against a non-existent path
    creates the file with all 3 tables and WAL mode, without any external
    tool or migration step.

    This is a deterministic test (not Hypothesis) because the property
    is about the constructor's side effects on a fresh path — no random
    input dimension is meaningful here.

    **Validates: Requirements 1.2, 7.2**
    """

    def test_fresh_path_creates_db_with_all_tables_and_wal(self, tmp_path):
        """Constructing StateStore against a non-existent nested path:
        1. Creates the file (and parent directories)
        2. Sets WAL journal mode
        3. Creates all 3 required tables (plans, pending_rollbacks, audit_trail)
        """
        # Use a nested path that does NOT exist yet
        db_path = tmp_path / "new_dir" / "nested" / "state.db"
        assert not db_path.exists(), "Precondition: path must not exist before test"
        assert not db_path.parent.exists(), "Precondition: parent dir must not exist"

        store = StateStore(db_path)
        try:
            # 1. File must exist after construction
            assert db_path.exists(), (
                f"StateStore constructor did not create the database file at {db_path}"
            )

            # 2. Verify WAL mode via PRAGMA (using a raw connection to the same file)
            conn = sqlite3.connect(str(db_path))
            try:
                journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                assert journal_mode.lower() == "wal", (
                    f"Expected WAL journal mode, got {journal_mode!r}"
                )
            finally:
                conn.close()

            # 3. Verify all 3 tables exist via sqlite_master
            rows = store.execute_readonly_query(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('plans', 'pending_rollbacks', 'audit_trail') "
                "ORDER BY name"
            )
            table_names = sorted(r[0] for r in rows)
            assert table_names == ["audit_trail", "pending_rollbacks", "plans"], (
                f"Expected all 3 tables, found: {table_names}"
            )
        finally:
            store.close()

    def test_fresh_path_no_prior_data_in_tables(self, tmp_path):
        """A freshly bootstrapped StateStore has zero rows in all tables —
        no phantom data from a migration or seed script.
        """
        db_path = tmp_path / "new_dir" / "state.db"
        store = StateStore(db_path)
        try:
            # Plans: get_plan for an arbitrary ID returns None
            assert store.get_plan("nonexistent-resource") is None, (
                "Fresh store should have no plans"
            )

            # Audit trail: empty
            trail = store.get_audit_trail()
            assert trail is not None, "get_audit_trail() returned None on fresh store"
            assert len(trail) == 0, (
                f"Fresh store should have 0 audit entries, got {len(trail)}"
            )

            # Pending rollbacks: none exist
            assert store.has_pending_rollback("anything") is False, (
                "Fresh store should have no pending rollbacks"
            )
        finally:
            store.close()

    def test_fresh_path_has_expected_indexes(self, tmp_path):
        """The bootstrapped schema includes the expected indexes for
        query performance (resource_id, timestamp, run_id on audit_trail).
        """
        db_path = tmp_path / "new_dir" / "state.db"
        store = StateStore(db_path)
        try:
            rows = store.execute_readonly_query(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name LIKE 'idx_%' ORDER BY name"
            )
            index_names = sorted(r[0] for r in rows)
            expected = sorted([
                "idx_audit_resource",
                "idx_audit_run_id",
                "idx_audit_timestamp",
                "idx_plans_run_id",
            ])
            assert index_names == expected, (
                f"Expected indexes {expected}, got {index_names}"
            )
        finally:
            store.close()
