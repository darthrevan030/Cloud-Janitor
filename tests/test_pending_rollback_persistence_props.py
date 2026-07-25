"""Property test for pending rollback persistence (Task 7.2).

Property 3: add_pending_rollback + has_pending_rollback on a fresh StateStore
(same file) survives restart — validating that pending rollback state is
durable across process boundaries.

**Validates: Requirement 3.1 (rollback state durability)**
"""

from __future__ import annotations


from hypothesis import given, settings
from hypothesis import strategies as st

from cloud_janitor.core.state_store import StateStore

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_resource_id_strategy = st.text(
    min_size=1,
    max_size=60,
    alphabet=st.characters(
        whitelist_categories=("L", "N", "Pd"),
        whitelist_characters="-_:/.",
        blacklist_characters="\x00",
    ),
)


# ---------------------------------------------------------------------------
# Property 3: Pending rollback persistence survives restart
# ---------------------------------------------------------------------------


class TestPendingRollbackPersistence:
    """Property 3: add_pending_rollback() persists to SQLite such that a
    FRESH StateStore instance (same file path) can has_pending_rollback()
    for the same resource_id — simulating a process restart.
    """

    @settings(max_examples=100, deadline=None)
    @given(resource_id=_resource_id_strategy)
    def test_pending_rollback_survives_restart(self, resource_id, tmp_path_factory):
        """For any valid resource_id, add_pending_rollback() on one instance
        followed by close + new instance construction (simulating restart)
        results in has_pending_rollback() returning True on the new instance.
        """
        db_path = tmp_path_factory.mktemp("rollback_persist") / "state.db"

        # Instance 1: add_pending_rollback
        store1 = StateStore(db_path)
        try:
            success = store1.add_pending_rollback(resource_id)
            assert success is True, (
                f"add_pending_rollback({resource_id!r}) returned False"
            )
        finally:
            store1.close()

        # Instance 2: fresh connection to same file (simulates process restart)
        store2 = StateStore(db_path)
        try:
            has_it = store2.has_pending_rollback(resource_id)
            assert has_it is True, (
                f"has_pending_rollback({resource_id!r}) returned False on fresh "
                f"instance — pending rollback state did not survive restart"
            )
        finally:
            store2.close()

    @settings(max_examples=50, deadline=None)
    @given(resource_ids=st.lists(_resource_id_strategy, min_size=1, max_size=10, unique=True))
    def test_multiple_pending_rollbacks_survive_restart(self, resource_ids, tmp_path_factory):
        """Multiple pending rollbacks all survive a restart — none are lost."""
        db_path = tmp_path_factory.mktemp("rollback_multi") / "state.db"

        # Instance 1: add all pending rollbacks
        store1 = StateStore(db_path)
        try:
            for rid in resource_ids:
                success = store1.add_pending_rollback(rid)
                assert success is True
        finally:
            store1.close()

        # Instance 2: verify all are present
        store2 = StateStore(db_path)
        try:
            for rid in resource_ids:
                assert store2.has_pending_rollback(rid) is True, (
                    f"has_pending_rollback({rid!r}) is False after restart — "
                    f"pending rollback lost"
                )
        finally:
            store2.close()

    @settings(max_examples=50, deadline=None)
    @given(resource_id=_resource_id_strategy)
    def test_discard_pending_rollback_persists_across_restart(self, resource_id, tmp_path_factory):
        """discard_pending_rollback() removes the entry durably — a fresh
        instance does NOT see it after the discard.
        """
        db_path = tmp_path_factory.mktemp("rollback_discard") / "state.db"

        # Instance 1: add then discard
        store1 = StateStore(db_path)
        try:
            store1.add_pending_rollback(resource_id)
            store1.discard_pending_rollback(resource_id)
        finally:
            store1.close()

        # Instance 2: must not see it
        store2 = StateStore(db_path)
        try:
            assert store2.has_pending_rollback(resource_id) is False, (
                f"has_pending_rollback({resource_id!r}) is True after discard + "
                f"restart — discard did not persist"
            )
        finally:
            store2.close()

    @settings(max_examples=50, deadline=None)
    @given(resource_id=_resource_id_strategy)
    def test_add_pending_rollback_is_idempotent(self, resource_id, tmp_path_factory):
        """Calling add_pending_rollback() twice for the same ID does NOT
        raise or fail — INSERT OR IGNORE semantics.
        """
        db_path = tmp_path_factory.mktemp("rollback_idempotent") / "state.db"

        store = StateStore(db_path)
        try:
            result1 = store.add_pending_rollback(resource_id)
            result2 = store.add_pending_rollback(resource_id)
            assert result1 is True
            assert result2 is True

            # Only one row should exist
            rows = store.execute_readonly_query(
                "SELECT COUNT(*) FROM pending_rollbacks WHERE resource_id = ?",
                (resource_id,),
            )
            assert rows[0][0] == 1, (
                f"Expected exactly 1 row for {resource_id!r}, got {rows[0][0]} — "
                f"duplicate insert was not ignored"
            )
        finally:
            store.close()
