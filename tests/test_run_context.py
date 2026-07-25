"""Unit tests for cloud_janitor.core.run_context.

Tests:
- prune_run_scoped_files: deletes oldest beyond `keep`, respects `protect` set,
  never raises on permission-denied unlink.
- get_retention fallback matrix: unset → 20, non-numeric → 20, zero → 20,
  negative → 20, valid positive → that value.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


from cloud_janitor.core.run_context import (
    DEFAULT_RETENTION,
    generate_run_id,
    get_retention,
    prune_run_scoped_files,
)


# ──────────────────────────────────────────────────────────────────────────────
# prune_run_scoped_files
# ──────────────────────────────────────────────────────────────────────────────


class TestPruneRunScopedFiles:
    """prune_run_scoped_files deletes oldest files beyond `keep`."""

    def _create_log_files(self, directory: Path, names: list[str]) -> list[Path]:
        """Helper: create sorted files in directory and return paths."""
        directory.mkdir(parents=True, exist_ok=True)
        paths = []
        for name in names:
            p = directory / name
            p.write_text(f"content of {name}")
            paths.append(p)
        return paths

    def test_deletes_oldest_beyond_keep(self, tmp_path):
        """With 5 files and keep=3, the 2 oldest are deleted."""
        names = [
            "20260701T100000Z-aaaa0001.log",
            "20260702T100000Z-aaaa0002.log",
            "20260703T100000Z-aaaa0003.log",
            "20260704T100000Z-aaaa0004.log",
            "20260705T100000Z-aaaa0005.log",
        ]
        self._create_log_files(tmp_path, names)

        prune_run_scoped_files(tmp_path, ".log", keep=3)

        remaining = sorted(p.name for p in tmp_path.glob("*.log"))
        assert remaining == [
            "20260703T100000Z-aaaa0003.log",
            "20260704T100000Z-aaaa0004.log",
            "20260705T100000Z-aaaa0005.log",
        ]

    def test_keeps_all_when_count_within_limit(self, tmp_path):
        """With 2 files and keep=5, nothing is deleted."""
        names = [
            "20260701T100000Z-aaaa0001.log",
            "20260702T100000Z-aaaa0002.log",
        ]
        self._create_log_files(tmp_path, names)

        prune_run_scoped_files(tmp_path, ".log", keep=5)

        remaining = sorted(p.name for p in tmp_path.glob("*.log"))
        assert remaining == names

    def test_respects_protect_set(self, tmp_path):
        """Protected files are never deleted, even if they are the oldest."""
        names = [
            "20260701T100000Z-aaaa0001.log",
            "20260702T100000Z-aaaa0002.log",
            "20260703T100000Z-aaaa0003.log",
            "20260704T100000Z-aaaa0004.log",
        ]
        self._create_log_files(tmp_path, names)

        # Protect the oldest file — it should survive even though it would
        # normally be pruned with keep=2
        prune_run_scoped_files(
            tmp_path, ".log", keep=2,
            protect={"20260701T100000Z-aaaa0001.log"},
        )

        remaining = sorted(p.name for p in tmp_path.glob("*.log"))
        # Protected file survives + 2 newest kept = oldest non-protected deleted
        assert "20260701T100000Z-aaaa0001.log" in remaining
        assert "20260704T100000Z-aaaa0004.log" in remaining
        # The protect set excludes the file from the candidate pool, so only
        # 3 candidates remain (02, 03, 04), keep=2 → 02 deleted
        assert "20260702T100000Z-aaaa0002.log" not in remaining

    def test_never_raises_on_permission_denied(self, tmp_path):
        """Permission-denied on unlink is silently swallowed."""
        names = [
            "20260701T100000Z-aaaa0001.log",
            "20260702T100000Z-aaaa0002.log",
            "20260703T100000Z-aaaa0003.log",
        ]
        self._create_log_files(tmp_path, names)

        # Patch Path.unlink to raise PermissionError
        with patch.object(Path, "unlink", side_effect=PermissionError("denied")):
            # Should not raise
            prune_run_scoped_files(tmp_path, ".log", keep=1)

        # All files still exist because unlink failed silently
        remaining = sorted(p.name for p in tmp_path.glob("*.log"))
        assert len(remaining) == 3

    def test_nonexistent_directory_does_not_raise(self):
        """Calling with a directory that doesn't exist is a no-op."""
        prune_run_scoped_files(Path("/nonexistent/dir/xyz"), ".log", keep=5)

    def test_only_matches_given_suffix(self, tmp_path):
        """Files not matching the suffix are ignored."""
        names_log = ["20260701T100000Z-aaaa0001.log", "20260702T100000Z-aaaa0002.log"]
        names_json = ["20260701T100000Z-bbbb0001.json", "20260702T100000Z-bbbb0002.json"]
        self._create_log_files(tmp_path, names_log + names_json)

        prune_run_scoped_files(tmp_path, ".log", keep=1)

        # Only oldest .log deleted; .json untouched
        remaining = sorted(p.name for p in tmp_path.iterdir())
        assert "20260701T100000Z-aaaa0001.log" not in remaining
        assert "20260702T100000Z-aaaa0002.log" in remaining
        assert "20260701T100000Z-bbbb0001.json" in remaining
        assert "20260702T100000Z-bbbb0002.json" in remaining

    def test_keep_zero_deletes_all_unprotected(self, tmp_path):
        """keep=0 means delete everything (that isn't protected)."""
        names = [
            "20260701T100000Z-aaaa0001.log",
            "20260702T100000Z-aaaa0002.log",
        ]
        self._create_log_files(tmp_path, names)

        prune_run_scoped_files(tmp_path, ".log", keep=0)

        remaining = list(tmp_path.glob("*.log"))
        assert remaining == []


# ──────────────────────────────────────────────────────────────────────────────
# get_retention fallback matrix
# ──────────────────────────────────────────────────────────────────────────────


class TestGetRetention:
    """get_retention returns correct value for all input classes."""

    def test_unset_returns_default(self, monkeypatch):
        """Env var not present → DEFAULT_RETENTION (20)."""
        monkeypatch.delenv("JANITOR_TEST_KEEP", raising=False)
        assert get_retention("JANITOR_TEST_KEEP") == 20

    def test_non_numeric_returns_default(self, monkeypatch):
        """Non-numeric string → DEFAULT_RETENTION."""
        monkeypatch.setenv("JANITOR_TEST_KEEP", "abc")
        assert get_retention("JANITOR_TEST_KEEP") == DEFAULT_RETENTION

    def test_zero_returns_default(self, monkeypatch):
        """Zero → DEFAULT_RETENTION (treated as invalid)."""
        monkeypatch.setenv("JANITOR_TEST_KEEP", "0")
        assert get_retention("JANITOR_TEST_KEEP") == DEFAULT_RETENTION

    def test_negative_returns_default(self, monkeypatch):
        """Negative value → DEFAULT_RETENTION."""
        monkeypatch.setenv("JANITOR_TEST_KEEP", "-5")
        assert get_retention("JANITOR_TEST_KEEP") == DEFAULT_RETENTION

    def test_valid_positive_returns_value(self, monkeypatch):
        """Valid positive integer → that value."""
        monkeypatch.setenv("JANITOR_TEST_KEEP", "42")
        assert get_retention("JANITOR_TEST_KEEP") == 42

    def test_valid_one_returns_one(self, monkeypatch):
        """Boundary: 1 is the smallest valid positive."""
        monkeypatch.setenv("JANITOR_TEST_KEEP", "1")
        assert get_retention("JANITOR_TEST_KEEP") == 1

    def test_float_string_returns_default(self, monkeypatch):
        """Float string like '3.5' → DEFAULT_RETENTION (int() fails)."""
        monkeypatch.setenv("JANITOR_TEST_KEEP", "3.5")
        assert get_retention("JANITOR_TEST_KEEP") == DEFAULT_RETENTION

    def test_whitespace_only_returns_default(self, monkeypatch):
        """Whitespace-only string → DEFAULT_RETENTION."""
        monkeypatch.setenv("JANITOR_TEST_KEEP", "   ")
        assert get_retention("JANITOR_TEST_KEEP") == DEFAULT_RETENTION

    def test_empty_string_returns_default(self, monkeypatch):
        """Empty string → DEFAULT_RETENTION."""
        monkeypatch.setenv("JANITOR_TEST_KEEP", "")
        assert get_retention("JANITOR_TEST_KEEP") == DEFAULT_RETENTION


# ──────────────────────────────────────────────────────────────────────────────
# generate_run_id format validation
# ──────────────────────────────────────────────────────────────────────────────


class TestGenerateRunId:
    """Basic format validation for generate_run_id()."""

    def test_format_matches_spec(self):
        """ID matches YYYYMMDDTHHMMSSZ-<8hex> format."""
        rid = generate_run_id()
        parts = rid.split("-")
        assert len(parts) == 2
        ts, hexfrag = parts
        assert ts.endswith("Z")
        assert len(ts) == len("YYYYMMDDTHHMMSSZ")
        assert len(hexfrag) == 8
        # Hex fragment is valid hex
        int(hexfrag, 16)

    def test_returns_string(self):
        """Return type is str."""
        assert isinstance(generate_run_id(), str)
