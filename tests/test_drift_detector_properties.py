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
import tempfile
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


def _fresh_history_path() -> Path:
    """Create a fresh temporary directory and return a history file path."""
    tmp_dir = tempfile.mkdtemp()
    return Path(tmp_dir) / "scan_history.json"


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
    def test_history_never_exceeds_max_snapshots(self, num_calls, max_snapshots):
        """After any number of save_snapshot calls, file has <= max_snapshots entries."""
        history_path = _fresh_history_path()

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
    def test_default_max_30_never_exceeded(self, num_calls):
        """With default max_snapshots=30, history never exceeds 30 entries."""
        history_path = _fresh_history_path()

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
    def test_oldest_entries_are_dropped(self, num_calls, max_snapshots):
        """Rotation drops the oldest entries, keeping the most recent ones."""
        history_path = _fresh_history_path()

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


# ---------------------------------------------------------------------------
# Property 15: DriftDetector Waste Delta Correctness
# ---------------------------------------------------------------------------


class TestDriftDetectorWasteDeltaCorrectness:
    """Property 15: DriftDetector Waste Delta Correctness.

    **Validates: Requirements 8.4**

    For two snapshots with total_waste W_prev and W_curr,
    waste_delta = W_curr - W_prev.
    """

    @given(
        w_prev=st.floats(min_value=0.0, max_value=100000.0, allow_nan=False, allow_infinity=False),
        w_curr=st.floats(min_value=0.0, max_value=100000.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200, deadline=None)
    def test_waste_delta_equals_current_minus_previous(self, w_prev, w_curr):
        """waste_delta must equal W_curr - W_prev for any two waste values."""
        history_path = _fresh_history_path()

        with patch("agents.drift_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_llm_response(
                "Drift analysis complete."
            )
            mock_get_client.return_value = mock_client

            detector = DriftDetector(history_path=history_path)
            detector.save_snapshot("scan-prev", [], [], w_prev)
            detector.save_snapshot("scan-curr", [], [], w_curr)
            result = detector.detect([])

        expected_delta = w_curr - w_prev
        assert abs(result["waste_delta"] - expected_delta) < 1e-6, (
            f"waste_delta should be {expected_delta}, got {result['waste_delta']}"
        )

    @given(
        w_prev=st.floats(min_value=0.0, max_value=100000.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200, deadline=None)
    def test_zero_delta_when_waste_unchanged(self, w_prev):
        """When waste is the same in both snapshots, delta is 0."""
        history_path = _fresh_history_path()

        with patch("agents.drift_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_llm_response(
                "No change in waste."
            )
            mock_get_client.return_value = mock_client

            detector = DriftDetector(history_path=history_path)
            detector.save_snapshot("scan-prev", [], [], w_prev)
            detector.save_snapshot("scan-curr", [], [], w_prev)
            result = detector.detect([])

        assert result["waste_delta"] == 0.0, (
            f"waste_delta should be 0.0 when waste unchanged, got {result['waste_delta']}"
        )


    @given(
        w_prev=st.floats(min_value=0.01, max_value=100000.0, allow_nan=False, allow_infinity=False),
        w_curr=st.floats(min_value=0.0, max_value=100000.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200, deadline=None)
    def test_negative_delta_when_waste_decreases(self, w_prev, w_curr):
        """When W_curr < W_prev, waste_delta is negative."""
        assume(w_curr < w_prev)
        history_path = _fresh_history_path()

        with patch("agents.drift_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_llm_response(
                "Waste decreased."
            )
            mock_get_client.return_value = mock_client

            detector = DriftDetector(history_path=history_path)
            detector.save_snapshot("scan-prev", [], [], w_prev)
            detector.save_snapshot("scan-curr", [], [], w_curr)
            result = detector.detect([])

        assert result["waste_delta"] < 0, (
            f"waste_delta should be negative when waste decreased, got {result['waste_delta']}"
        )


# ---------------------------------------------------------------------------
# Property 16: DriftDetector Finding Diff Correctness
# ---------------------------------------------------------------------------


class TestDriftDetectorFindingDiffCorrectness:
    """Property 16: DriftDetector Finding Diff Correctness.

    **Validates: Requirements 8.5**

    new_findings contains exactly findings in current but not previous;
    resolved_findings contains exactly findings in previous but not current.
    Matching is by (resource_id, check_type) pair.
    """

    @given(
        prev_findings=unique_findings_strategy(min_size=0, max_size=8),
        curr_findings=unique_findings_strategy(min_size=0, max_size=8),
    )
    @settings(max_examples=200, deadline=None)
    def test_new_findings_are_in_current_not_previous(self, prev_findings, curr_findings):
        """new_findings contains exactly the findings in current but not in previous."""
        history_path = _fresh_history_path()

        with patch("agents.drift_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_llm_response(
                "Drift detected."
            )
            mock_get_client.return_value = mock_client

            detector = DriftDetector(history_path=history_path)
            detector.save_snapshot("scan-prev", prev_findings, [], 10.0)
            detector.save_snapshot("scan-curr", curr_findings, [], 20.0)
            result = detector.detect(curr_findings)

        # Compute expected keys
        prev_keys = {
            (f["resource_id"], f["check_type"]) for f in prev_findings
            if f.get("resource_id") and f.get("check_type")
        }
        curr_keys = {
            (f["resource_id"], f["check_type"]) for f in curr_findings
            if f.get("resource_id") and f.get("check_type")
        }
        expected_new_keys = curr_keys - prev_keys

        actual_new_keys = {
            (f["resource_id"], f["check_type"]) for f in result["new_findings"]
        }
        assert actual_new_keys == expected_new_keys, (
            f"new_findings keys mismatch.\n"
            f"Expected: {expected_new_keys}\nGot: {actual_new_keys}"
        )


    @given(
        prev_findings=unique_findings_strategy(min_size=0, max_size=8),
        curr_findings=unique_findings_strategy(min_size=0, max_size=8),
    )
    @settings(max_examples=200, deadline=None)
    def test_resolved_findings_are_in_previous_not_current(self, prev_findings, curr_findings):
        """resolved_findings contains exactly the findings in previous but not in current."""
        history_path = _fresh_history_path()

        with patch("agents.drift_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_llm_response(
                "Some findings resolved."
            )
            mock_get_client.return_value = mock_client

            detector = DriftDetector(history_path=history_path)
            detector.save_snapshot("scan-prev", prev_findings, [], 10.0)
            detector.save_snapshot("scan-curr", curr_findings, [], 20.0)
            result = detector.detect(curr_findings)

        prev_keys = {
            (f["resource_id"], f["check_type"]) for f in prev_findings
            if f.get("resource_id") and f.get("check_type")
        }
        curr_keys = {
            (f["resource_id"], f["check_type"]) for f in curr_findings
            if f.get("resource_id") and f.get("check_type")
        }
        expected_resolved_keys = prev_keys - curr_keys

        actual_resolved_keys = {
            (f["resource_id"], f["check_type"]) for f in result["resolved_findings"]
        }
        assert actual_resolved_keys == expected_resolved_keys, (
            f"resolved_findings keys mismatch.\n"
            f"Expected: {expected_resolved_keys}\nGot: {actual_resolved_keys}"
        )

    @given(findings=unique_findings_strategy(min_size=1, max_size=8))
    @settings(max_examples=200, deadline=None)
    def test_identical_snapshots_produce_no_diff(self, findings):
        """When both snapshots have the same findings, new and resolved are both empty."""
        history_path = _fresh_history_path()

        with patch("agents.drift_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_llm_response(
                "No drift."
            )
            mock_get_client.return_value = mock_client

            detector = DriftDetector(history_path=history_path)
            detector.save_snapshot("scan-prev", findings, [], 10.0)
            detector.save_snapshot("scan-curr", findings, [], 10.0)
            result = detector.detect(findings)

        assert result["new_findings"] == [], (
            f"With identical findings, new_findings should be [], got {result['new_findings']}"
        )
        assert result["resolved_findings"] == [], (
            f"With identical findings, resolved_findings should be [], "
            f"got {result['resolved_findings']}"
        )


    @given(findings=unique_findings_strategy(min_size=1, max_size=8))
    @settings(max_examples=200, deadline=None)
    def test_all_new_when_previous_empty(self, findings):
        """When previous has no findings and current has some, all are new."""
        history_path = _fresh_history_path()

        with patch("agents.drift_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_llm_response(
                "All new findings."
            )
            mock_get_client.return_value = mock_client

            detector = DriftDetector(history_path=history_path)
            detector.save_snapshot("scan-prev", [], [], 0.0)
            detector.save_snapshot("scan-curr", findings, [], 10.0)
            result = detector.detect(findings)

        expected_new_keys = {
            (f["resource_id"], f["check_type"]) for f in findings
            if f.get("resource_id") and f.get("check_type")
        }
        actual_new_keys = {
            (f["resource_id"], f["check_type"]) for f in result["new_findings"]
        }
        assert actual_new_keys == expected_new_keys, (
            f"All findings should be new. Expected: {expected_new_keys}\nGot: {actual_new_keys}"
        )
        assert result["resolved_findings"] == []

    @given(findings=unique_findings_strategy(min_size=1, max_size=8))
    @settings(max_examples=200, deadline=None)
    def test_all_resolved_when_current_empty(self, findings):
        """When current has no findings and previous had some, all are resolved."""
        history_path = _fresh_history_path()

        with patch("agents.drift_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_llm_response(
                "All findings resolved."
            )
            mock_get_client.return_value = mock_client

            detector = DriftDetector(history_path=history_path)
            detector.save_snapshot("scan-prev", findings, [], 10.0)
            detector.save_snapshot("scan-curr", [], [], 0.0)
            result = detector.detect([])

        expected_resolved_keys = {
            (f["resource_id"], f["check_type"]) for f in findings
            if f.get("resource_id") and f.get("check_type")
        }
        actual_resolved_keys = {
            (f["resource_id"], f["check_type"]) for f in result["resolved_findings"]
        }
        assert actual_resolved_keys == expected_resolved_keys, (
            f"All findings should be resolved. Expected: {expected_resolved_keys}\n"
            f"Got: {actual_resolved_keys}"
        )
        assert result["new_findings"] == []


# ---------------------------------------------------------------------------
# Property 17: DriftDetector Output Schema
# ---------------------------------------------------------------------------


class TestDriftDetectorOutputSchema:
    """Property 17: DriftDetector Output Schema.

    **Validates: Requirements 8.8**

    For history with >=2 snapshots, returns dict with all required keys
    and correct types.
    """

    @given(
        prev_findings=unique_findings_strategy(min_size=0, max_size=5),
        curr_findings=unique_findings_strategy(min_size=0, max_size=5),
        w_prev=st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
        w_curr=st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
        scan_id_prev=st.text(min_size=1, max_size=20, alphabet=st.characters(
            whitelist_categories=("L", "N"), whitelist_characters="-_"
        )),
        scan_id_curr=st.text(min_size=1, max_size=20, alphabet=st.characters(
            whitelist_categories=("L", "N"), whitelist_characters="-_"
        )),
    )
    @settings(max_examples=200, deadline=None)
    def test_output_has_all_required_keys(
        self, prev_findings, curr_findings, w_prev, w_curr,
        scan_id_prev, scan_id_curr,
    ):
        """detect() output has exactly the 6 required keys."""
        history_path = _fresh_history_path()

        with patch("agents.drift_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_llm_response(
                "Schema test narrative."
            )
            mock_get_client.return_value = mock_client

            detector = DriftDetector(history_path=history_path)
            detector.save_snapshot(scan_id_prev, prev_findings, [], w_prev)
            detector.save_snapshot(scan_id_curr, curr_findings, [], w_curr)
            result = detector.detect(curr_findings)

        assert set(result.keys()) == REQUIRED_DRIFT_KEYS, (
            f"Expected keys {REQUIRED_DRIFT_KEYS}, got {set(result.keys())}"
        )


    @given(
        prev_findings=unique_findings_strategy(min_size=0, max_size=5),
        curr_findings=unique_findings_strategy(min_size=0, max_size=5),
        w_prev=st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
        w_curr=st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200, deadline=None)
    def test_output_types_are_correct(self, prev_findings, curr_findings, w_prev, w_curr):
        """Each field in detect() output has the correct type."""
        history_path = _fresh_history_path()

        with patch("agents.drift_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_llm_response(
                "Type check narrative."
            )
            mock_get_client.return_value = mock_client

            detector = DriftDetector(history_path=history_path)
            detector.save_snapshot("scan-prev", prev_findings, [], w_prev)
            detector.save_snapshot("scan-curr", curr_findings, [], w_curr)
            result = detector.detect(curr_findings)

        assert isinstance(result["new_findings"], list), (
            f"new_findings must be list, got {type(result['new_findings'])}"
        )
        assert isinstance(result["resolved_findings"], list), (
            f"resolved_findings must be list, got {type(result['resolved_findings'])}"
        )
        assert isinstance(result["waste_delta"], float), (
            f"waste_delta must be float, got {type(result['waste_delta'])}"
        )
        assert isinstance(result["critical_delta"], int), (
            f"critical_delta must be int, got {type(result['critical_delta'])}"
        )
        assert isinstance(result["narrative"], str), (
            f"narrative must be str, got {type(result['narrative'])}"
        )
        assert isinstance(result["compared_scans"], list), (
            f"compared_scans must be list, got {type(result['compared_scans'])}"
        )
        assert len(result["compared_scans"]) == 2, (
            f"compared_scans must have 2 elements, got {len(result['compared_scans'])}"
        )
        assert all(isinstance(s, str) for s in result["compared_scans"]), (
            "compared_scans elements must be str"
        )


    @given(
        prev_findings=unique_findings_strategy(min_size=0, max_size=5),
        curr_findings=unique_findings_strategy(min_size=0, max_size=5),
        w_prev=st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
        w_curr=st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200, deadline=None)
    def test_narrative_is_non_empty(self, prev_findings, curr_findings, w_prev, w_curr):
        """narrative is always a non-empty string (LLM or fallback)."""
        history_path = _fresh_history_path()

        with patch("agents.drift_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_llm_response(
                "Non-empty narrative."
            )
            mock_get_client.return_value = mock_client

            detector = DriftDetector(history_path=history_path)
            detector.save_snapshot("scan-prev", prev_findings, [], w_prev)
            detector.save_snapshot("scan-curr", curr_findings, [], w_curr)
            result = detector.detect(curr_findings)

        assert len(result["narrative"].strip()) > 0, (
            "narrative must be non-empty"
        )

    @given(
        prev_findings=unique_findings_strategy(min_size=0, max_size=5),
        curr_findings=unique_findings_strategy(min_size=0, max_size=5),
        w_prev=st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
        w_curr=st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200, deadline=None)
    def test_compared_scans_matches_snapshot_ids(
        self, prev_findings, curr_findings, w_prev, w_curr
    ):
        """compared_scans contains [previous_scan_id, current_scan_id] in order."""
        history_path = _fresh_history_path()

        with patch("agents.drift_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_llm_response(
                "Compare check."
            )
            mock_get_client.return_value = mock_client

            detector = DriftDetector(history_path=history_path)
            detector.save_snapshot("id-prev-fixed", prev_findings, [], w_prev)
            detector.save_snapshot("id-curr-fixed", curr_findings, [], w_curr)
            result = detector.detect(curr_findings)

        assert result["compared_scans"] == ["id-prev-fixed", "id-curr-fixed"], (
            f"compared_scans should be ['id-prev-fixed', 'id-curr-fixed'], "
            f"got {result['compared_scans']}"
        )


    @given(
        prev_findings=unique_findings_strategy(min_size=0, max_size=5),
        curr_findings=unique_findings_strategy(min_size=0, max_size=5),
    )
    @settings(max_examples=200, deadline=None)
    def test_new_findings_contain_dicts(self, prev_findings, curr_findings):
        """Every element in new_findings and resolved_findings is a dict."""
        history_path = _fresh_history_path()

        with patch("agents.drift_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _mock_llm_response(
                "Dict check."
            )
            mock_get_client.return_value = mock_client

            detector = DriftDetector(history_path=history_path)
            detector.save_snapshot("scan-prev", prev_findings, [], 5.0)
            detector.save_snapshot("scan-curr", curr_findings, [], 10.0)
            result = detector.detect(curr_findings)

        for item in result["new_findings"]:
            assert isinstance(item, dict), (
                f"new_findings element must be dict, got {type(item)}"
            )
        for item in result["resolved_findings"]:
            assert isinstance(item, dict), (
                f"resolved_findings element must be dict, got {type(item)}"
            )


# ---------------------------------------------------------------------------
# Negative Tests
# ---------------------------------------------------------------------------


class TestDriftDetectorNegativeCases:
    """Negative tests for DriftDetector.

    Validates behavior when preconditions are not met (insufficient history,
    errors, etc.).
    """

    @given(
        findings=unique_findings_strategy(min_size=0, max_size=5),
    )
    @settings(max_examples=200, deadline=None)
    def test_insufficient_history_with_zero_snapshots(self, findings):
        """With no snapshots, detect returns insufficient history — never drift data."""
        history_path = _fresh_history_path()

        with patch("agents.drift_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_get_client.return_value = mock_client

            detector = DriftDetector(history_path=history_path)
            result = detector.detect(findings)

        assert result == {"drift": None, "reason": "insufficient history"}, (
            f"With 0 snapshots, expected insufficient history, got {result}"
        )
