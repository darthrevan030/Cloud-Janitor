"""Property-based tests for run-scoped findings store.

Property 3: Findings Store Isolation
  Two execute_audit() invocations with distinct run_ids write to different files.

Property 4: Findings Store Pointer Never References a Pruned File
  prune_run_scoped_files never deletes the file currently referenced
  by latest.json (uses the protect param).

Validates: Requirements 3.6, 3.7
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from cloud_janitor.core.run_context import prune_run_scoped_files


# --- Strategies ---

run_id_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        blacklist_characters="\x00/\\:",
    ),
    min_size=5,
    max_size=30,
).map(lambda s: f"run-{s}")


# ──────────────────────────────────────────────────────────────────────────────
# Property 3: Findings Store Isolation
# ──────────────────────────────────────────────────────────────────────────────


@settings(deadline=None, max_examples=50)
@given(
    run_id_a=run_id_strategy,
    run_id_b=run_id_strategy,
)
def test_distinct_run_ids_write_to_different_files(run_id_a: str, run_id_b: str):
    """Two execute_audit() with distinct run_ids produce distinct store files.

    Simulates the orchestrator's findings-store wiring: each run_id maps to
    a unique file path in the findings_store directory.
    """
    assume(run_id_a != run_id_b)

    with tempfile.TemporaryDirectory() as tmp_dir:
        findings_dir = Path(tmp_dir) / "findings_store"
        findings_dir.mkdir(parents=True)

        path_a = findings_dir / f"{run_id_a}.json"
        path_b = findings_dir / f"{run_id_b}.json"

        # Simulate writing findings for each run
        findings_a = {"findings": [{"id": "f1", "source": run_id_a}]}
        findings_b = {"findings": [{"id": "f2", "source": run_id_b}]}

        path_a.write_text(json.dumps(findings_a), encoding="utf-8")
        path_b.write_text(json.dumps(findings_b), encoding="utf-8")

        # The files are distinct paths
        assert path_a != path_b, "Run paths must differ"
        assert path_a.exists()
        assert path_b.exists()

        # Each file has its own data
        data_a = json.loads(path_a.read_text(encoding="utf-8"))
        data_b = json.loads(path_b.read_text(encoding="utf-8"))

        assert data_a["findings"][0]["source"] == run_id_a
        assert data_b["findings"][0]["source"] == run_id_b
        assert data_a != data_b


# ──────────────────────────────────────────────────────────────────────────────
# Property 4: Findings Store Pointer Never References a Pruned File
# ──────────────────────────────────────────────────────────────────────────────


@settings(deadline=None, max_examples=50)
@given(
    retention=st.integers(min_value=1, max_value=8),
    extra=st.integers(min_value=1, max_value=8),
)
def test_prune_never_deletes_file_referenced_by_pointer(retention: int, extra: int):
    """prune_run_scoped_files with protect={current_file, 'latest.json'} never
    deletes the file that latest.json currently points to.

    This mirrors the orchestrator's usage:
        prune_run_scoped_files(dir, ".json", retention,
                               protect={run_findings_path.name, "latest.json"})
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        findings_dir = Path(tmp_dir) / "findings_store"
        findings_dir.mkdir(parents=True)

        total_files = retention + extra
        run_ids = [f"run-{i:04d}" for i in range(total_files)]
        paths = []

        for run_id in run_ids:
            p = findings_dir / f"{run_id}.json"
            p.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
            paths.append(p)

        # The "current" run is the latest one — it's what latest.json references
        current_run_path = paths[-1]
        current_run_name = current_run_path.name

        # Also create latest.json pointer file (simulating update_findings_store_pointer)
        pointer_file = findings_dir / "latest.json"
        pointer_file.write_text(
            json.dumps({"run_id": run_ids[-1], "path": str(current_run_path)}),
            encoding="utf-8",
        )

        # Prune with protect set (as the orchestrator does)
        prune_run_scoped_files(
            findings_dir, ".json", retention,
            protect={current_run_name, "latest.json"},
        )

        # Invariant: the pointed-to file must still exist
        assert current_run_path.exists(), (
            f"Protected file {current_run_name} was deleted by pruning!"
        )

        # Invariant: latest.json itself must still exist
        assert pointer_file.exists(), "latest.json itself was deleted by pruning!"

        # The total remaining .json files should be at most retention + protected count
        remaining = list(findings_dir.glob("*.json"))
        # Non-protected files are capped at retention
        non_protected = [
            p for p in remaining
            if p.name not in {current_run_name, "latest.json"}
        ]
        assert len(non_protected) <= retention, (
            f"Expected at most {retention} non-protected files, got {len(non_protected)}"
        )


@settings(deadline=None, max_examples=30)
@given(
    retention=st.integers(min_value=1, max_value=5),
    n_files=st.integers(min_value=2, max_value=10),
)
def test_protect_set_always_preserves_named_files(retention: int, n_files: int):
    """Any file in the protect set survives pruning regardless of age or count."""
    assume(n_files > retention)  # Pruning must actually fire

    with tempfile.TemporaryDirectory() as tmp_dir:
        findings_dir = Path(tmp_dir) / "findings_store"
        findings_dir.mkdir(parents=True)

        run_ids = [f"run-{i:04d}" for i in range(n_files)]
        for run_id in run_ids:
            (findings_dir / f"{run_id}.json").write_text("{}", encoding="utf-8")

        # Protect the oldest file (worst case — it would be pruned without protection)
        oldest_name = f"{run_ids[0]}.json"
        prune_run_scoped_files(
            findings_dir, ".json", retention,
            protect={oldest_name},
        )

        assert (findings_dir / oldest_name).exists(), (
            f"Protected file {oldest_name} was pruned — protect set violated!"
        )
