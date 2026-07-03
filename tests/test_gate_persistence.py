"""Unit tests for ApprovalGateStore atomic write and corruption handling.

_Requirements: 6.1, 6.4, 6.5_

Tests:
- Atomic write-then-rename persists gate state correctly on success
- os.replace failure cleans up temp file with no corruption
- Corrupted store (invalid JSON) results in is_corrupted == True
- Corrupted store (missing "gates" key) results in is_corrupted == True
- Fresh store (no file on disk) initializes with empty gates
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.approval_gate import ApprovalGateStore


class TestAtomicWritePersistsState:
    """Test that atomic write-then-rename persists gate state correctly."""

    def test_atomic_write_persists_state_on_success(self, tmp_path: Path):
        """set_gate() persists state that a fresh store instance can load back."""
        store_path = tmp_path / "gates.json"

        # Create store and set a gate
        store = ApprovalGateStore(store_path)
        store.set_gate(
            resource_id="vol-abc123",
            attempts=2,
            locked=False,
            max_attempts=3,
        )

        # Verify the file exists on disk
        assert store_path.exists(), "Store file should be written to disk"

        # Create a completely new store instance on the same path
        fresh_store = ApprovalGateStore(store_path)
        fresh_store.load()

        # Verify the loaded gate matches what was persisted
        gate = fresh_store.get_gate("vol-abc123")
        assert gate is not None, "Gate should be retrievable after round-trip"
        assert gate["resource_id"] == "vol-abc123"
        assert gate["attempts"] == 2
        assert gate["locked"] is False
        assert gate["max_attempts"] == 3

        # Verify no corruption flag
        assert fresh_store.is_corrupted is False

    def test_persisted_file_is_valid_json_with_gates_key(self, tmp_path: Path):
        """The persisted file contains valid JSON with the required 'gates' key."""
        store_path = tmp_path / "gates.json"

        store = ApprovalGateStore(store_path)
        store.set_gate(
            resource_id="sg-999",
            attempts=0,
            locked=True,
            max_attempts=5,
        )

        # Parse the raw file and validate schema
        content = store_path.read_text(encoding="utf-8")
        data = json.loads(content)

        assert isinstance(data, dict), "Root must be a dict"
        assert "gates" in data, "Root must have 'gates' key"
        assert isinstance(data["gates"], list), "'gates' must be a list"
        assert len(data["gates"]) == 1

        gate_entry = data["gates"][0]
        assert gate_entry["resource_id"] == "sg-999"
        assert gate_entry["attempts"] == 0
        assert gate_entry["locked"] is True
        assert gate_entry["max_attempts"] == 5


class TestOsReplaceFailure:
    """Test behavior when os.replace raises OSError."""

    def test_os_replace_failure_cleans_temp_file(self, tmp_path: Path):
        """When os.replace raises OSError, no .tmp files remain and exception propagates."""
        store_path = tmp_path / "gates.json"
        store = ApprovalGateStore(store_path)

        with patch("agents.approval_gate.os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                store.set_gate(
                    resource_id="vol-xyz",
                    attempts=1,
                    locked=False,
                    max_attempts=3,
                )

        # Verify no .tmp files remain in the directory
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], (
            f"Temp files should be cleaned up on failure, found: {tmp_files}"
        )

    def test_os_replace_failure_does_not_corrupt_existing_store(self, tmp_path: Path):
        """If a store file existed before the failed write, it remains unchanged."""
        store_path = tmp_path / "gates.json"

        # First, write a valid store
        store = ApprovalGateStore(store_path)
        store.set_gate(
            resource_id="vol-original",
            attempts=0,
            locked=False,
            max_attempts=3,
        )

        # Record mtime of the original file
        original_mtime = store_path.stat().st_mtime
        original_content = store_path.read_text(encoding="utf-8")

        # Now attempt a write that will fail at os.replace
        with patch("agents.approval_gate.os.replace", side_effect=OSError("permission denied")):
            with pytest.raises(OSError, match="permission denied"):
                store.set_gate(
                    resource_id="vol-new",
                    attempts=1,
                    locked=True,
                    max_attempts=5,
                )

        # The original file should be unchanged
        assert store_path.read_text(encoding="utf-8") == original_content, (
            "Original store file must not be modified on failed write"
        )


class TestCorruptedStore:
    """Test that corrupted store files are detected properly."""

    def test_corrupted_store_invalid_json(self, tmp_path: Path):
        """Loading a file with invalid JSON results in is_corrupted == True."""
        store_path = tmp_path / "gates.json"
        store_path.write_bytes(b"not valid json {{{")

        store = ApprovalGateStore(store_path)
        store.load()

        assert store.is_corrupted is True, (
            "Store should be marked corrupted when file contains invalid JSON"
        )

    def test_corrupted_store_missing_gates_key(self, tmp_path: Path):
        """Loading valid JSON without a 'gates' key results in is_corrupted == True."""
        store_path = tmp_path / "gates.json"
        store_path.write_text(json.dumps({"items": [], "version": "1.0"}))

        store = ApprovalGateStore(store_path)
        store.load()

        assert store.is_corrupted is True, (
            "Store should be marked corrupted when JSON lacks 'gates' key"
        )

    def test_corrupted_store_gates_not_a_list(self, tmp_path: Path):
        """Loading JSON where 'gates' is not a list results in is_corrupted == True."""
        store_path = tmp_path / "gates.json"
        store_path.write_text(json.dumps({"gates": "not a list"}))

        store = ApprovalGateStore(store_path)
        store.load()

        assert store.is_corrupted is True, (
            "Store should be marked corrupted when 'gates' is not a list"
        )

    def test_corrupted_store_get_gate_returns_none_for_real_ids(self, tmp_path: Path):
        """A corrupted store returns None for normal resource IDs (only __corrupted__ exists)."""
        store_path = tmp_path / "gates.json"
        store_path.write_bytes(b"\xff\xfe invalid bytes")

        store = ApprovalGateStore(store_path)
        store.load()

        assert store.is_corrupted is True
        assert store.get_gate("vol-abc123") is None, (
            "Normal resource IDs should not be found in corrupted store"
        )


class TestFreshStoreNoFile:
    """Test that a fresh store with no file on disk initializes correctly."""

    def test_fresh_store_no_file(self, tmp_path: Path):
        """A store pointing to a non-existent file loads with empty gates."""
        store_path = tmp_path / "nonexistent" / "gates.json"

        # Confirm file does not exist
        assert not store_path.exists()

        store = ApprovalGateStore(store_path)
        store.load()

        assert store.is_corrupted is False, (
            "Fresh store should not be marked corrupted"
        )
        assert store.get_gate("anything") is None, (
            "Fresh store should have no gates"
        )
        assert store.get_gate("vol-test") is None
