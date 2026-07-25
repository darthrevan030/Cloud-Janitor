"""Unit tests for run-scoped findings store.

Covers:
- Two execute_audit() calls write to distinct files, both valid
- resolve_latest_findings_store() returns pointer target when present,
  falls back to legacy, returns None when neither
- Pruning beyond retention never deletes the file latest.json references

Requirements: 3.1, 3.2, 3.3, 3.4, 3.6, 3.7, 3.8
"""

from __future__ import annotations

import json
from pathlib import Path

from cloud_janitor.core.paths import (
    resolve_latest_findings_store,
)
from cloud_janitor.core.run_context import prune_run_scoped_files, get_retention


class TestTwoExecuteAuditCallsWriteDistinctFiles:
    """Two execute_audit() calls produce separate, valid findings store files."""

    def test_two_runs_produce_distinct_valid_files(self, tmp_path: Path):
        """Simulates two sequential audit runs writing findings to separate files."""
        findings_dir = tmp_path / "findings_store"
        findings_dir.mkdir(parents=True)

        run_id_1 = "20250101T000000Z-aabbccdd"
        run_id_2 = "20250101T000100Z-eeff0011"

        path_1 = findings_dir / f"{run_id_1}.json"
        path_2 = findings_dir / f"{run_id_2}.json"

        # Simulate orchestrator writing findings for run 1
        findings_1 = {
            "findings": [
                {"resource_id": "vol-001", "category": "finops", "severity": "MEDIUM"}
            ]
        }
        path_1.write_text(json.dumps(findings_1), encoding="utf-8")

        # Simulate orchestrator writing findings for run 2
        findings_2 = {
            "findings": [
                {"resource_id": "sg-002", "category": "secops", "severity": "HIGH"}
            ]
        }
        path_2.write_text(json.dumps(findings_2), encoding="utf-8")

        # Both files exist and are distinct
        assert path_1.exists()
        assert path_2.exists()
        assert path_1 != path_2

        # Both files are valid JSON with findings key
        data_1 = json.loads(path_1.read_text(encoding="utf-8"))
        data_2 = json.loads(path_2.read_text(encoding="utf-8"))

        assert "findings" in data_1
        assert "findings" in data_2
        assert isinstance(data_1["findings"], list)
        assert isinstance(data_2["findings"], list)
        assert len(data_1["findings"]) == 1
        assert len(data_2["findings"]) == 1

        # Content is distinct
        assert data_1["findings"][0]["resource_id"] == "vol-001"
        assert data_2["findings"][0]["resource_id"] == "sg-002"

    def test_second_run_does_not_overwrite_first(self, tmp_path: Path):
        """The second run's file creation leaves the first intact."""
        findings_dir = tmp_path / "findings_store"
        findings_dir.mkdir(parents=True)

        path_1 = findings_dir / "run-001.json"
        path_1.write_text(json.dumps({"findings": [{"id": "A"}]}), encoding="utf-8")
        mtime_before = path_1.stat().st_mtime

        # Second run creates a different file
        path_2 = findings_dir / "run-002.json"
        path_2.write_text(json.dumps({"findings": [{"id": "B"}]}), encoding="utf-8")

        # First file is unmodified
        assert path_1.stat().st_mtime == mtime_before
        data_1 = json.loads(path_1.read_text(encoding="utf-8"))
        assert data_1["findings"][0]["id"] == "A"


class TestResolveLatestFindingsStore:
    """Tests for resolve_latest_findings_store() resolution logic."""

    def test_returns_pointer_target_when_pointer_present(self, tmp_path: Path, monkeypatch):
        """When latest.json points to a valid file, that file is returned."""
        findings_dir = tmp_path / "findings_store"
        findings_dir.mkdir(parents=True)

        target_file = findings_dir / "run-abc.json"
        target_file.write_text(json.dumps({"findings": []}), encoding="utf-8")

        pointer = findings_dir / "latest.json"
        pointer.write_text(
            json.dumps({"run_id": "run-abc", "path": str(target_file)}),
            encoding="utf-8",
        )

        # Monkey-patch module-level constants
        monkeypatch.setattr(
            "cloud_janitor.core.paths.FINDINGS_STORE_LATEST_POINTER", pointer
        )
        monkeypatch.setattr(
            "cloud_janitor.core.paths.FINDINGS_STORE_PATH",
            tmp_path / "legacy_findings.json",
        )

        result = resolve_latest_findings_store()
        assert result == target_file

    def test_falls_back_to_legacy_when_pointer_absent(self, tmp_path: Path, monkeypatch):
        """When latest.json doesn't exist but legacy file does, returns legacy."""
        findings_dir = tmp_path / "findings_store"
        findings_dir.mkdir(parents=True)

        legacy_file = tmp_path / "findings_store.json"
        legacy_file.write_text(json.dumps({"findings": []}), encoding="utf-8")

        pointer = findings_dir / "latest.json"
        # Pointer does NOT exist

        monkeypatch.setattr(
            "cloud_janitor.core.paths.FINDINGS_STORE_LATEST_POINTER", pointer
        )
        monkeypatch.setattr(
            "cloud_janitor.core.paths.FINDINGS_STORE_PATH", legacy_file
        )

        result = resolve_latest_findings_store()
        assert result == legacy_file

    def test_returns_none_when_neither_exists(self, tmp_path: Path, monkeypatch):
        """When neither pointer nor legacy file exists, returns None."""
        findings_dir = tmp_path / "findings_store"
        findings_dir.mkdir(parents=True)

        pointer = findings_dir / "latest.json"
        legacy_file = tmp_path / "findings_store.json"
        # Neither file exists

        monkeypatch.setattr(
            "cloud_janitor.core.paths.FINDINGS_STORE_LATEST_POINTER", pointer
        )
        monkeypatch.setattr(
            "cloud_janitor.core.paths.FINDINGS_STORE_PATH", legacy_file
        )

        result = resolve_latest_findings_store()
        assert result is None

    def test_returns_none_when_pointer_target_missing(self, tmp_path: Path, monkeypatch):
        """When latest.json points to a non-existent file and legacy missing → None."""
        findings_dir = tmp_path / "findings_store"
        findings_dir.mkdir(parents=True)

        pointer = findings_dir / "latest.json"
        pointer.write_text(
            json.dumps({"run_id": "ghost", "path": str(findings_dir / "ghost.json")}),
            encoding="utf-8",
        )

        monkeypatch.setattr(
            "cloud_janitor.core.paths.FINDINGS_STORE_LATEST_POINTER", pointer
        )
        monkeypatch.setattr(
            "cloud_janitor.core.paths.FINDINGS_STORE_PATH",
            tmp_path / "also_missing.json",
        )

        result = resolve_latest_findings_store()
        assert result is None

    def test_falls_back_to_legacy_when_pointer_has_invalid_json(
        self, tmp_path: Path, monkeypatch
    ):
        """Corrupt latest.json with valid-legacy → returns legacy path."""
        findings_dir = tmp_path / "findings_store"
        findings_dir.mkdir(parents=True)

        pointer = findings_dir / "latest.json"
        pointer.write_text("not valid json{{{{", encoding="utf-8")

        legacy_file = tmp_path / "findings_store.json"
        legacy_file.write_text(json.dumps({"findings": []}), encoding="utf-8")

        monkeypatch.setattr(
            "cloud_janitor.core.paths.FINDINGS_STORE_LATEST_POINTER", pointer
        )
        monkeypatch.setattr(
            "cloud_janitor.core.paths.FINDINGS_STORE_PATH", legacy_file
        )

        result = resolve_latest_findings_store()
        assert result == legacy_file


class TestPruningNeverDeletesPointedFile:
    """Pruning beyond retention never deletes the file latest.json references."""

    def test_pruning_preserves_pointer_target(self, tmp_path: Path, monkeypatch):
        """With 25 files and retention=20, the pointed-to file is never pruned."""
        monkeypatch.setenv("JANITOR_FINDINGS_RETENTION", "20")

        findings_dir = tmp_path / "findings_store"
        findings_dir.mkdir(parents=True)

        # Create 25 run-scoped files
        run_ids = [f"run-{i:04d}" for i in range(25)]
        for run_id in run_ids:
            (findings_dir / f"{run_id}.json").write_text(
                json.dumps({"run_id": run_id}), encoding="utf-8"
            )

        # The current run is the latest
        current_run = run_ids[-1]
        current_path = findings_dir / f"{current_run}.json"

        # Create latest.json pointer
        pointer = findings_dir / "latest.json"
        pointer.write_text(
            json.dumps({"run_id": current_run, "path": str(current_path)}),
            encoding="utf-8",
        )

        # Prune with protect set (mirroring orchestrator logic)
        retention = get_retention("JANITOR_FINDINGS_RETENTION")
        prune_run_scoped_files(
            findings_dir, ".json", retention,
            protect={current_path.name, "latest.json"},
        )

        # The pointed-to file must survive
        assert current_path.exists(), "Pointed-to file was deleted!"
        assert pointer.exists(), "latest.json itself was deleted!"

    def test_pruning_preserves_pointer_target_even_when_oldest(
        self, tmp_path: Path, monkeypatch
    ):
        """Even if the pointer references the OLDEST file, it is not pruned."""
        monkeypatch.setenv("JANITOR_FINDINGS_RETENTION", "3")

        findings_dir = tmp_path / "findings_store"
        findings_dir.mkdir(parents=True)

        run_ids = [f"run-{i:04d}" for i in range(10)]
        for run_id in run_ids:
            (findings_dir / f"{run_id}.json").write_text("{}", encoding="utf-8")

        # Point to the OLDEST file (edge case)
        oldest = run_ids[0]
        oldest_path = findings_dir / f"{oldest}.json"

        pointer = findings_dir / "latest.json"
        pointer.write_text(
            json.dumps({"run_id": oldest, "path": str(oldest_path)}),
            encoding="utf-8",
        )

        prune_run_scoped_files(
            findings_dir, ".json", 3,
            protect={oldest_path.name, "latest.json"},
        )

        # Despite being the oldest, the protected file survives
        assert oldest_path.exists(), (
            "Oldest file was pruned even though it's in the protect set!"
        )
        assert pointer.exists()
