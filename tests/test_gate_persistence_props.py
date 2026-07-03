"""Property-based tests for approval gate persistence.

**Validates: Requirements 6.1, 6.2, 6.4**

Property 8: Approval Gate Persistence Round Trip
Property 9: Corrupted Gate Store Locks All Gates
"""

import json
import tempfile
from pathlib import Path

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from agents.approval_gate import ApprovalGateStore
from orchestrator import Orchestrator


# --- Strategies ---

# Generate valid resource IDs matching the allowlist pattern [a-zA-Z0-9\-_:./]{1,256}
_resource_id_chars = st.sampled_from(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:./"
)
resource_ids = st.text(_resource_id_chars, min_size=1, max_size=64)

# Resource IDs that are filesystem-safe (for tests that create files with the ID in the name)
_fs_safe_resource_id_chars = st.sampled_from(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)
fs_safe_resource_ids = st.text(_fs_safe_resource_id_chars, min_size=1, max_size=64)

# Generate valid gate state dicts
gate_states = st.fixed_dictionaries(
    {
        "resource_id": resource_ids.filter(lambda r: r != "__corrupted__"),
        "attempts": st.integers(min_value=0, max_value=100),
        "locked": st.booleans(),
        "max_attempts": st.integers(min_value=1, max_value=20),
    }
)

# Lists of gate states with unique resource_ids
gate_state_lists = st.lists(
    gate_states, min_size=1, max_size=10, unique_by=lambda g: g["resource_id"]
)

# Byte sequences that are NOT valid gate store JSON
_invalid_json_bytes = st.one_of(
    # Random bytes that won't parse as JSON
    st.binary(min_size=1, max_size=200).filter(
        lambda b: _is_not_valid_gate_json(b)
    ),
    # Valid JSON but missing the required "gates" key
    st.sampled_from([
        b"{}",
        b'{"items": []}',
        b'{"gate": []}',
        b"[]",
        b'{"gates": "not_a_list"}',  # valid JSON, has "gates" but not a list
        b"null",
        b"42",
        b'"just a string"',
    ]),
    # Truncated/malformed JSON
    st.sampled_from([
        b'{"gates": [',
        b"{",
        b"not json at all",
        b"\xff\xfe",
        b'{"gates": [{"resource_id": "x"',
    ]),
)


def _is_not_valid_gate_json(data: bytes) -> bool:
    """Return True if data does NOT represent a valid gate store."""
    try:
        parsed = json.loads(data.decode("utf-8"))
        if isinstance(parsed, dict) and "gates" in parsed:
            gates = parsed["gates"]
            if isinstance(gates, list) and all(
                isinstance(g, dict) and "resource_id" in g for g in gates
            ):
                return False
        return True
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return True


def _make_project_dirs(tmp_path: Path) -> Path:
    """Create the minimal project structure needed for Orchestrator init."""
    (tmp_path / "hooks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "rollbacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "policies").mkdir(parents=True, exist_ok=True)

    pre_hook = tmp_path / "hooks" / "pre-remediation.sh"
    pre_hook.write_text("#!/usr/bin/env bash\nexit 0\n")
    post_hook = tmp_path / "hooks" / "post-remediation.sh"
    post_hook.write_text("#!/usr/bin/env bash\nexit 0\n")

    return tmp_path


class TestProperty8RoundTrip:
    """Property 8: Approval Gate Persistence Round Trip.

    For any set of ApprovalGateState objects, persisting them via
    ApprovalGateStore.save() and then loading via a fresh
    ApprovalGateStore.load() SHALL produce gate states where each gate's
    resource_id, attempts, locked, and max_attempts fields are identical
    to the originals.
    """

    @given(gates=gate_state_lists)
    @settings(max_examples=100, deadline=5000)
    def test_round_trip_preserves_all_fields(self, gates):
        """Persisted gate state round-trips through save/load without loss."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store_path = Path(tmp_dir) / "gates.json"

            # Create store, populate with generated gates, and save
            store = ApprovalGateStore(store_path)
            for gate in gates:
                store.set_gate(
                    resource_id=gate["resource_id"],
                    attempts=gate["attempts"],
                    locked=gate["locked"],
                    max_attempts=gate["max_attempts"],
                )

            # Load into a completely fresh store instance
            fresh_store = ApprovalGateStore(store_path)
            fresh_store.load()

            # Verify every gate round-trips with identical fields
            for original in gates:
                loaded = fresh_store.get_gate(original["resource_id"])
                assert loaded is not None, (
                    f"Gate for {original['resource_id']!r} was lost after round-trip"
                )
                assert loaded["resource_id"] == original["resource_id"]
                assert loaded["attempts"] == original["attempts"]
                assert loaded["locked"] == original["locked"]
                assert loaded["max_attempts"] == original["max_attempts"]

            # Verify no extra gates appeared
            assert not fresh_store.is_corrupted


class TestProperty9CorruptedStoreLocks:
    """Property 9: Corrupted Gate Store Locks All Gates.

    For any byte sequence that is not valid JSON or is valid JSON but lacks
    the required "gates" key structure, loading the gate store SHALL result
    in a corrupted state where all approval and rollback operations are rejected.
    """

    @given(corrupt_data=_invalid_json_bytes, rid=fs_safe_resource_ids)
    @settings(max_examples=50, deadline=10000)
    def test_corrupted_store_rejects_approve_and_rollback(
        self, corrupt_data, rid
    ):
        """Orchestrator rejects approve/rollback when gate file is corrupted."""
        from agents.remediation_architect import RemediationPlan

        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = _make_project_dirs(Path(tmp_dir))

            # Write corrupt data to the gate store file
            gate_path = project_dir / "output" / "approval_gates.json"
            gate_path.write_bytes(corrupt_data)

            # Create Orchestrator — it loads the corrupted store at init
            orch = Orchestrator(project_root=project_dir, approver="test-user")

            # Inject a plan so approve() doesn't short-circuit at "no plan found"
            orch._last_plans = [
                RemediationPlan(
                    resource_id=rid,
                    finding={"resource_id": rid, "resource_type": "ebs"},
                    blocked=False,
                    remediation_hcl='resource "null_resource" "test" {}',
                    rollback_hcl='resource "null_resource" "rollback" {}',
                ),
            ]

            # Create rollback artifact so rollback doesn't fail at file-not-found
            (project_dir / "output" / "rollbacks" / f"{rid}.tf").write_text(
                'resource "null_resource" "rollback" {}'
            )

            # --- Test approve() rejects ---
            approve_result = orch.approve(f"APPROVE {rid}", resource_id=rid)
            assert approve_result.success is False, (
                f"approve() should fail with corrupted store, got success=True "
                f"for corrupt data: {corrupt_data!r}"
            )
            assert "corrupted" in approve_result.error.lower(), (
                f"approve() error should mention 'corrupted', got: {approve_result.error!r}"
            )

            # --- Test rollback() rejects ---
            rollback_result = orch.rollback(f"ROLLBACK {rid}")
            assert rollback_result.success is False, (
                f"rollback() should fail with corrupted store, got success=True "
                f"for corrupt data: {corrupt_data!r}"
            )
            assert "corrupted" in rollback_result.error.lower(), (
                f"rollback() error should mention 'corrupted', got: {rollback_result.error!r}"
            )
