"""Property-based tests for DriftDetector.

**Validates: Requirements 8.3, 8.4, 8.5, 8.8**

Property 14: DriftDetector Max Snapshots Invariant
For any sequence of save_snapshot calls, entries in scan_history.json never exceed 30.

Property 15: DriftDetector Waste Delta Correctness
For two snapshots with total_waste W_prev and W_curr, waste_delta = W_curr - W_prev.

Property 16: DriftDetector Finding Diff Correctness
new_findings contains exactly findings in current but not previous;
resolved_findings the inverse.

Property 17: DriftDetector Output Schema
For history with >=2 snapshots, returns dict with all required keys and correct types.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from agents.drift_detector import DriftDetector, MAX_SNAPSHOTS


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

REQUIRED_DRIFT_KEYS = {
    "new_findings", "resolved_findings", "waste_delta",
    "critical_delta", "narrative", "compared_scans",
}


def _mock_llm_response(text: str) -> MagicMock:
    """Create a mock OpenAI chat completions response."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = text
    return mock_response


@st.composite
def resource_id_strategy(draw):
    """Generate realistic resource IDs."""
    prefix = draw(st.sampled_from(["i-", "vol-", "sg-", "ec2-", "rds-", "cache-"]))
    suffix = draw(st.text(
        min_size=3, max_size=12,
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    ))
    return prefix + suffix


@st.composite
def check_type_strategy(draw):
    """Generate valid check_type values."""
    return draw(st.sampled_from([
        "security_group", "encryption", "public_access", "idle_resource",
    ]))


@st.composite
def severity_strategy(draw):
    """Generate severity values."""
    return draw(st.sampled_from(["LOW", "MEDIUM", "HIGH", "CRITICAL"]))


@st.composite
def finding_strategy(draw):
    """Generate a finding dict with resource_id, check_type, severity, cost."""
    return {
        "resource_id": draw(resource_id_strategy()),
        "check_type": draw(check_type_strategy()),
        "severity": draw(severity_strategy()),
        "cost_estimate_monthly": draw(st.floats(min_value=0.0, max_value=10000.0)),
    }


@st.composite
def unique_findings_strategy(draw, min_size=0, max_size=10):
    """Generate a list of findings with unique (resource_id, check_type) pairs."""
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    seen_keys = set()
    findings = []
    for _ in range(n * 3):  # generate extra attempts to fill unique slots
        if len(findings) >= n:
            break
        f = draw(finding_strategy())
        key = (f["resource_id"], f["check_type"])
        if key not in seen_keys:
            seen_keys.add(key)
            findings.append(f)
    return findings


# ---------------------------------------------------------------------------
# Property 14: DriftDetector Max Snapshots Invariant
# ---------------------------------------------------------------------------


class TestDriftDetectorMaxSnapshotsInvariant:
    """Property 14: DriftDetector Max Snapshots Invariant.

    **Validates: Requirements 8.3**

    For any sequence of save_snapshot calls, entries in scan_history.json
    never exceed max_snapshots (default 30).
    """

    @given(
        num_calls=st.integers(min_value=1, max_value=60),
        max_snapshots=st.integers(min_value=1, max_value=30),
    )
    @settings(max_examples=200, deadline=None)
    def test_history_never_exceeds_max_snapshots(self, tmp_path, num_calls, max_snapshots):
        """After any number of save_snapshot calls, file has <= max_snapshots entries."""
        history_path = tmp_path / "scan_history.json"

        with patch("agents.drift_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_get_client.return_value = mock_client

            detector = DriftDetector(history_path=history_path, max_snapshots=max_snapshots)
            for i in range(num_calls):
                detector.save_snapshot(f"scan-{i:04d}", [], [], float(i))

        data = json.loads(history_path.read_text())
        assert len(data) <= max_snapshots, (
            f"After {num_calls} saves with max_snapshots={max_snapshots}, "
            f"history has {len(data)} entries (expected <= {max_snapshots})"
        )

    @given(num_calls=st.integers(min_value=31, max_value=60))
    @settings(max_examples=200, deadline=None)
    def test_default_max_30_never_exceeded(self, tmp_path, num_calls):
        """With default max_snapshots=30, history never exceeds 30 entries."""
        history_path = tmp_path / "scan_history.json"

        with patch("agents.drift_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_get_client.return_value = mock_client

            detector = DriftDetector(history_path=history_path)
            for i in range(num_calls):
                detector.save_snapshot(f"scan-{i:04d}", [], [], float(i))

        data = json.loads(history_path.read_text())
        assert len(data) <= MAX_SNAPSHOTS, (
            f"After {num_calls} saves, history has {len(data)} entries "
            f"(expected <= {MAX_SNAPSHOTS})"
        )
        assert len(data) == MAX_SNAPSHOTS, (
            f"After {num_calls} > 30 saves, history should be exactly 30, got {len(data)}"
        )


    @given(
        num_calls=st.integers(min_value=1, max_value=50),
        max_snapshots=st.integers(min_value=1, max_value=30),
    )
    @settings(max_examples=200, deadline=None)
    def test_oldest_entries_are_dropped(self, tmp_path, num_calls, max_snapshots):
        """Rotation drops the oldest entries, keeping the most recent ones."""
        history_path = tmp_path / "scan_history.json"

        with patch("agents.drift_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_get_client.return_value = mock_client

            detector = DriftDetector(history_path=history_path, max_snapshots=max_snapshots)
            for i in range(num_calls):
                detector.save_snapshot(f"scan-{i:04d}", [], [], float(i))

        data = json.loads(history_path.read_text())
        # The last entry should always be the most recently saved
        assert data[-1]["scan_id"] == f"scan-{num_calls - 1:04d}", (
            f"Last entry should be scan-{num_calls - 1:04d}, got {data[-1]['scan_id']}"
        )
        # If we exceeded max, the first entry should be the oldest retained
        if num_calls > max_snapshots:
            expected_first_id = f"scan-{num_calls - max_snapshots:04d}"
            assert data[0]["scan_id"] == expected_first_id, (
                f"First entry should be {expected_first_id}, got {data[0]['scan_id']}"
            )
