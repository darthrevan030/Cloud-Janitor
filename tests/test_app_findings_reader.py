"""Unit tests for the retry-once findings reader in app.py.

Covers:
- Mocked open() raising FileNotFoundError on first attempt → retry succeeds → findings returned
- Both attempts fail → falls back to []

Requirements: 3.5, 3.9
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

from cloud_janitor.app import load_findings


class TestRetryOnceReaderSuccess:
    """When first open() raises FileNotFoundError but retry resolution succeeds."""

    def test_retry_succeeds_after_first_file_not_found(self, tmp_path: Path):
        """First open raises FileNotFoundError, second resolution points to
        an existing file → findings are returned (not the empty fallback).
        """
        valid_findings = {"findings": [{"resource_id": "vol-123", "severity": "HIGH"}]}
        valid_json = json.dumps(valid_findings)

        # Create a real file that the retry resolution will point to
        retry_file = tmp_path / "retry_target.json"
        retry_file.write_text(valid_json, encoding="utf-8")

        call_count = {"n": 0}

        def mock_resolve():
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First call: return a path that will trigger FileNotFoundError
                return tmp_path / "gone.json"
            else:
                # Second call (retry): return the real file
                return retry_file

        with patch("cloud_janitor.app.resolve_latest_findings_store", side_effect=mock_resolve):
            result = load_findings()

        assert call_count["n"] == 2, "Expected exactly 2 calls to resolve (initial + retry)"
        assert len(result) == 1
        assert result[0]["resource_id"] == "vol-123"
        assert result[0]["severity"] == "HIGH"

    def test_retry_returns_findings_with_multiple_entries(self, tmp_path: Path):
        """Retry succeeds and all findings from the resolved file are returned."""
        valid_findings = {
            "findings": [
                {"resource_id": "sg-001", "severity": "CRITICAL"},
                {"resource_id": "vol-002", "severity": "MEDIUM"},
            ]
        }
        retry_file = tmp_path / "valid.json"
        retry_file.write_text(json.dumps(valid_findings), encoding="utf-8")

        call_count = {"n": 0}

        def mock_resolve():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return tmp_path / "pruned.json"
            return retry_file

        with patch("cloud_janitor.app.resolve_latest_findings_store", side_effect=mock_resolve):
            result = load_findings()

        assert len(result) == 2
        assert result[0]["resource_id"] == "sg-001"
        assert result[1]["resource_id"] == "vol-002"


class TestRetryOnceReaderBothFail:
    """When both attempts fail, the function falls back to []."""

    def test_both_attempts_file_not_found_returns_empty(self, tmp_path: Path):
        """Both resolutions return paths that don't exist → falls back to []."""
        call_count = {"n": 0}

        def mock_resolve():
            call_count["n"] += 1
            # Both resolutions return a non-existent file
            return tmp_path / f"missing_{call_count['n']}.json"

        with patch("cloud_janitor.app.resolve_latest_findings_store", side_effect=mock_resolve):
            result = load_findings()

        assert result == []
        assert call_count["n"] == 2, "Expected 2 resolution attempts before fallback"

    def test_first_file_not_found_then_resolve_returns_none(self, tmp_path: Path):
        """First open raises FileNotFoundError, retry resolution returns None → []."""
        call_count = {"n": 0}

        def mock_resolve():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return tmp_path / "deleted.json"
            return None  # Retry resolution finds nothing

        with patch("cloud_janitor.app.resolve_latest_findings_store", side_effect=mock_resolve):
            result = load_findings()

        assert result == []

    def test_initial_resolve_returns_none_skips_entirely(self):
        """When the very first resolution returns None, returns [] without retry."""
        with patch("cloud_janitor.app.resolve_latest_findings_store", return_value=None):
            result = load_findings()

        assert result == []


class TestRetryOnceReaderEdgeCases:
    """Edge cases for the retry-once reader."""

    def test_first_open_succeeds_no_retry_needed(self, tmp_path: Path):
        """When first open succeeds, returns findings directly — no retry."""
        findings_file = tmp_path / "good.json"
        findings_file.write_text(
            json.dumps({"findings": [{"resource_id": "i-999"}]}), encoding="utf-8"
        )

        call_count = {"n": 0}

        def mock_resolve():
            call_count["n"] += 1
            return findings_file

        with patch("cloud_janitor.app.resolve_latest_findings_store", side_effect=mock_resolve):
            result = load_findings()

        # Only one resolution call needed (no retry)
        assert call_count["n"] == 1
        assert len(result) == 1
        assert result[0]["resource_id"] == "i-999"

    def test_first_open_json_decode_error_returns_empty(self, tmp_path: Path):
        """A JSONDecodeError on first open returns [] (no retry for decode errors)."""
        corrupt_file = tmp_path / "corrupt.json"
        corrupt_file.write_text("not json at all {{{{", encoding="utf-8")

        with patch(
            "cloud_janitor.app.resolve_latest_findings_store",
            return_value=corrupt_file,
        ):
            result = load_findings()

        assert result == []
