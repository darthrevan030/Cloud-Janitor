"""Tests for ApprovalGateStore persistence layer."""

import json
import os
from pathlib import Path

import pytest

from agents.approval_gate import ApprovalGateStore


class TestApprovalGateStoreInit:
    """Tests for ApprovalGateStore initialization."""

    def test_init_sets_path(self, tmp_path):
        store_path = tmp_path / "gates.json"
        store = ApprovalGateStore(store_path)
        assert store._path == store_path

    def test_init_starts_with_empty_gates(self, tmp_path):
        store_path = tmp_path / "gates.json"
        store = ApprovalGateStore(store_path)
        assert store.get_gate("anything") is None

    def test_is_corrupted_false_on_init(self, tmp_path):
        store_path = tmp_path / "gates.json"
        store = ApprovalGateStore(store_path)
        assert store.is_corrupted is False


class TestApprovalGateStoreLoad:
    """Tests for load() — requirement 6.2, 6.3, 6.4."""

    def test_load_nonexistent_file_gives_empty_gates(self, tmp_path):
        """Req 6.3: If no durable store file exists, start with empty gates."""
        store_path = tmp_path / "does_not_exist.json"
        store = ApprovalGateStore(store_path)
        store.load()
        assert store.get_gate("vol-123") is None
        assert store.is_corrupted is False

    def test_load_valid_json(self, tmp_path):
        """Req 6.2: On init, load all previously persisted gate states."""
        store_path = tmp_path / "gates.json"
        data = {
            "gates": [
                {
                    "resource_id": "vol-abc",
                    "attempts": 2,
                    "locked": False,
                    "max_attempts": 3,
                }
            ]
        }
        store_path.write_text(json.dumps(data), encoding="utf-8")

        store = ApprovalGateStore(store_path)
        store.load()

        gate = store.get_gate("vol-abc")
        assert gate is not None
        assert gate["resource_id"] == "vol-abc"
        assert gate["attempts"] == 2
        assert gate["locked"] is False
        assert gate["max_attempts"] == 3
        assert store.is_corrupted is False

    def test_load_multiple_gates(self, tmp_path):
        """Req 6.2: Load multiple gate states."""
        store_path = tmp_path / "gates.json"
        data = {
            "gates": [
                {"resource_id": "vol-1", "attempts": 0, "locked": False, "max_attempts": 3},
                {"resource_id": "sg-2", "attempts": 3, "locked": True, "max_attempts": 3},
            ]
        }
        store_path.write_text(json.dumps(data), encoding="utf-8")

        store = ApprovalGateStore(store_path)
        store.load()

        assert store.get_gate("vol-1") is not None
        assert store.get_gate("sg-2") is not None
        assert store.get_gate("sg-2")["locked"] is True

    def test_load_corrupted_invalid_json(self, tmp_path):
        """Req 6.4: Malformed JSON triggers corruption handling."""
        store_path = tmp_path / "gates.json"
        store_path.write_text("{not valid json!!!", encoding="utf-8")

        store = ApprovalGateStore(store_path)
        store.load()

        assert store.is_corrupted is True

    def test_load_corrupted_missing_gates_key(self, tmp_path):
        """Req 6.4: Valid JSON but missing 'gates' key triggers corruption."""
        store_path = tmp_path / "gates.json"
        store_path.write_text(json.dumps({"other": []}), encoding="utf-8")

        store = ApprovalGateStore(store_path)
        store.load()

        assert store.is_corrupted is True

    def test_load_corrupted_not_a_dict(self, tmp_path):
        """Req 6.4: JSON that is not a dict triggers corruption."""
        store_path = tmp_path / "gates.json"
        store_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

        store = ApprovalGateStore(store_path)
        store.load()

        assert store.is_corrupted is True

    def test_load_corrupted_logs_warning(self, tmp_path, caplog):
        """Req 6.4: Corruption logs a WARNING."""
        store_path = tmp_path / "gates.json"
        store_path.write_text("broken!", encoding="utf-8")

        store = ApprovalGateStore(store_path)
        import logging

        with caplog.at_level(logging.WARNING):
            store.load()

        assert "Failed to parse approval gate store" in caplog.text
        assert "All gates initialized as locked" in caplog.text

    def test_load_skips_entries_without_resource_id(self, tmp_path):
        """Entries missing 'resource_id' are silently skipped."""
        store_path = tmp_path / "gates.json"
        data = {
            "gates": [
                {"resource_id": "vol-good", "attempts": 0, "locked": False, "max_attempts": 3},
                {"attempts": 1, "locked": False, "max_attempts": 3},  # no resource_id
                "not a dict",  # not even a dict
            ]
        }
        store_path.write_text(json.dumps(data), encoding="utf-8")

        store = ApprovalGateStore(store_path)
        store.load()

        assert store.get_gate("vol-good") is not None
        assert store.is_corrupted is False


class TestApprovalGateStoreSave:
    """Tests for save() — requirement 6.5."""

    def test_save_creates_file(self, tmp_path):
        """Req 6.3: Create the file on first state change."""
        store_path = tmp_path / "gates.json"
        store = ApprovalGateStore(store_path)
        store.set_gate("vol-123", attempts=1, locked=False, max_attempts=3)

        assert store_path.exists()
        data = json.loads(store_path.read_text(encoding="utf-8"))
        assert "gates" in data
        assert len(data["gates"]) == 1
        assert data["gates"][0]["resource_id"] == "vol-123"

    def test_save_creates_parent_directories(self, tmp_path):
        """Parent directories are created if they don't exist."""
        store_path = tmp_path / "deep" / "nested" / "dir" / "gates.json"
        store = ApprovalGateStore(store_path)
        store.set_gate("vol-abc", attempts=0, locked=False, max_attempts=3)

        assert store_path.exists()

    def test_save_atomic_no_partial_writes(self, tmp_path):
        """Req 6.5: Write-then-rename means no .tmp files left on success."""
        store_path = tmp_path / "gates.json"
        store = ApprovalGateStore(store_path)
        store.set_gate("vol-1", attempts=0, locked=False, max_attempts=3)

        # No .tmp files should remain in the directory
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_save_overwrites_existing_file(self, tmp_path):
        """Successive saves update the file content."""
        store_path = tmp_path / "gates.json"
        store = ApprovalGateStore(store_path)
        store.set_gate("vol-1", attempts=0, locked=False, max_attempts=3)
        store.set_gate("vol-2", attempts=1, locked=True, max_attempts=3)

        data = json.loads(store_path.read_text(encoding="utf-8"))
        resource_ids = [g["resource_id"] for g in data["gates"]]
        assert "vol-1" in resource_ids
        assert "vol-2" in resource_ids

    def test_save_produces_valid_json(self, tmp_path):
        """Saved file is always valid JSON with 'gates' key."""
        store_path = tmp_path / "gates.json"
        store = ApprovalGateStore(store_path)
        store.set_gate("vol-x", attempts=2, locked=True, max_attempts=5)

        content = store_path.read_text(encoding="utf-8")
        data = json.loads(content)
        assert isinstance(data, dict)
        assert "gates" in data
        assert isinstance(data["gates"], list)


class TestApprovalGateStoreGetSet:
    """Tests for get_gate() and set_gate()."""

    def test_get_gate_returns_none_for_unknown(self, tmp_path):
        store = ApprovalGateStore(tmp_path / "g.json")
        assert store.get_gate("nonexistent") is None

    def test_set_gate_then_get_gate(self, tmp_path):
        store = ApprovalGateStore(tmp_path / "g.json")
        store.set_gate("vol-abc", attempts=1, locked=False, max_attempts=3)

        gate = store.get_gate("vol-abc")
        assert gate == {
            "resource_id": "vol-abc",
            "attempts": 1,
            "locked": False,
            "max_attempts": 3,
        }

    def test_set_gate_persists_immediately(self, tmp_path):
        """Req 6.1: Persist updated gate state before returning."""
        store_path = tmp_path / "g.json"
        store = ApprovalGateStore(store_path)
        store.set_gate("vol-persist", attempts=0, locked=False, max_attempts=3)

        # Read back from disk with a fresh store
        store2 = ApprovalGateStore(store_path)
        store2.load()
        gate = store2.get_gate("vol-persist")
        assert gate is not None
        assert gate["resource_id"] == "vol-persist"

    def test_set_gate_overwrites_existing(self, tmp_path):
        """Setting the same resource_id updates the existing entry."""
        store = ApprovalGateStore(tmp_path / "g.json")
        store.set_gate("vol-x", attempts=0, locked=False, max_attempts=3)
        store.set_gate("vol-x", attempts=2, locked=True, max_attempts=3)

        gate = store.get_gate("vol-x")
        assert gate["attempts"] == 2
        assert gate["locked"] is True


class TestApprovalGateStoreRoundTrip:
    """Integration tests: save then load."""

    def test_full_round_trip(self, tmp_path):
        """Save multiple gates, reload from disk, verify all present."""
        store_path = tmp_path / "gates.json"
        store1 = ApprovalGateStore(store_path)
        store1.set_gate("vol-1", attempts=0, locked=False, max_attempts=3)
        store1.set_gate("sg-2", attempts=3, locked=True, max_attempts=3)
        store1.set_gate("cache-3", attempts=1, locked=False, max_attempts=5)

        # Load in a fresh instance
        store2 = ApprovalGateStore(store_path)
        store2.load()

        assert store2.get_gate("vol-1")["locked"] is False
        assert store2.get_gate("sg-2")["locked"] is True
        assert store2.get_gate("cache-3")["max_attempts"] == 5
        assert store2.is_corrupted is False
