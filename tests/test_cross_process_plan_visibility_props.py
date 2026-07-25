"""Property test for cross-process plan visibility (Task 6.3).

Property 2: replace_plans() on one StateStore instance, get_plan() on a SECOND
instance (same file) returns the plan — validating WAL-mode multi-connection
semantics.

**Validates: Requirement 6.1 (cross-connection visibility)**
"""

from __future__ import annotations


from hypothesis import given, settings
from hypothesis import strategies as st

from cloud_janitor.agents.remediation_architect import DependencyReport, RemediationPlan
from cloud_janitor.core.state_store import StateStore

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_safe_text = st.text(
    min_size=1,
    max_size=80,
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
)

_resource_id_strategy = st.text(
    min_size=1,
    max_size=60,
    alphabet=st.characters(
        whitelist_categories=("L", "N", "Pd"),
        whitelist_characters="-_:/.",
        blacklist_characters="\x00",
    ),
)

_finding_strategy = st.fixed_dictionaries(
    {
        "resource_id": _resource_id_strategy,
        "category": st.sampled_from(["cost", "security", "compliance"]),
        "severity": st.sampled_from(["LOW", "MEDIUM", "HIGH", "CRITICAL"]),
    },
    optional={"description": _safe_text},
)

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

_hcl_strategy = st.one_of(
    st.none(),
    st.text(
        min_size=1,
        max_size=200,
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    ),
)

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
    """Deduplicate plans by resource_id, keeping last occurrence."""
    seen: dict[str, RemediationPlan] = {}
    for p in plans:
        seen[p.resource_id] = p
    return list(seen.values())


# ---------------------------------------------------------------------------
# Property 2: Cross-process plan visibility
# ---------------------------------------------------------------------------


class TestCrossProcessPlanVisibility:
    """Property 2: replace_plans() on instance A, get_plan() on instance B
    (same file) returns the plan — simulating cross-process visibility via
    SQLite WAL mode.
    """

    @settings(max_examples=100, deadline=None)
    @given(plans=st.lists(_plan_strategy, min_size=1, max_size=10))
    def test_write_on_instance_a_visible_on_instance_b(self, plans, tmp_path_factory):
        """Plans written by one StateStore connection are immediately readable
        by a second StateStore connection to the same file.

        This is the defining contract of SQLite WAL mode: a committed write
        on connection A is visible to a subsequent read on connection B,
        even without any explicit coordination beyond the WAL checkpoint.
        """
        plans = _plans_with_unique_ids(plans)
        if not plans:
            return  # dedup may empty the list for pathological inputs

        db_path = tmp_path_factory.mktemp("cross_process") / "state.db"

        store_a = StateStore(db_path)
        store_b = StateStore(db_path)
        try:
            # Write plans via instance A
            run_id = "cross-process-run-001"
            success = store_a.replace_plans(plans, run_id)
            assert success is True, "replace_plans() on instance A must succeed"

            # Read each plan via instance B (different connection)
            for original in plans:
                retrieved = store_b.get_plan(original.resource_id)

                assert retrieved is not None, (
                    f"get_plan({original.resource_id!r}) on instance B returned None — "
                    f"plan written by instance A is not visible cross-connection"
                )

                # Verify key fields round-trip correctly across connections
                assert retrieved.resource_id == original.resource_id, (
                    f"resource_id mismatch: {retrieved.resource_id!r} != {original.resource_id!r}"
                )
                assert retrieved.finding == original.finding, (
                    f"finding mismatch for {original.resource_id!r}"
                )
                assert retrieved.blocked == original.blocked
                assert retrieved.block_reason == original.block_reason
                assert retrieved.remediation_hcl == original.remediation_hcl, (
                    f"remediation_hcl mismatch for {original.resource_id!r}"
                )
                assert retrieved.rollback_hcl == original.rollback_hcl, (
                    f"rollback_hcl mismatch for {original.resource_id!r}"
                )

                # DependencyReport round-trip
                if original.dependency_report is None:
                    assert retrieved.dependency_report is None
                else:
                    assert retrieved.dependency_report is not None
                    assert retrieved.dependency_report.resource_id == original.dependency_report.resource_id
                    assert retrieved.dependency_report.has_dependencies == original.dependency_report.has_dependencies
                    assert retrieved.dependency_report.dependencies == original.dependency_report.dependencies
                    assert retrieved.dependency_report.recommendation == original.dependency_report.recommendation
                    assert retrieved.dependency_report.checked_at == original.dependency_report.checked_at
        finally:
            store_a.close()
            store_b.close()

    @settings(max_examples=50, deadline=None)
    @given(plans=st.lists(_plan_strategy, min_size=1, max_size=8))
    def test_replace_on_a_clears_prior_plans_visible_on_b(self, plans, tmp_path_factory):
        """When instance A replaces plans, instance B no longer sees the OLD plans.

        This validates that the wholesale-replace semantics (DELETE + INSERT)
        are atomic and visible cross-connection.
        """
        plans = _plans_with_unique_ids(plans)
        if not plans:
            return

        db_path = tmp_path_factory.mktemp("cross_replace") / "state.db"

        store_a = StateStore(db_path)
        store_b = StateStore(db_path)
        try:
            # Plant a sentinel plan via instance A
            sentinel = RemediationPlan(
                resource_id="sentinel-cross-process",
                finding={"resource_id": "sentinel", "category": "cost", "severity": "LOW"},
                blocked=False,
                block_reason="",
                dependency_report=None,
                remediation_hcl="resource {}",
                rollback_hcl=None,
            )
            store_a.replace_plans([sentinel], "run-0")

            # Confirm instance B can see it
            assert store_b.get_plan("sentinel-cross-process") is not None, (
                "Precondition: sentinel must be visible on instance B"
            )

            # Replace with new plans via instance A (sentinel not in new batch)
            store_a.replace_plans(plans, "run-1")

            # Instance B must NOT see the sentinel anymore
            assert store_b.get_plan("sentinel-cross-process") is None, (
                "replace_plans() wholesale semantics not visible cross-connection — "
                "sentinel survived the replace"
            )

            # Instance B must see the new plans
            for p in plans:
                assert store_b.get_plan(p.resource_id) is not None, (
                    f"New plan {p.resource_id!r} not visible on instance B "
                    f"after replace"
                )
        finally:
            store_a.close()
            store_b.close()
