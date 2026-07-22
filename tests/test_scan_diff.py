"""Unit tests for core/scan_diff.py — severity escalation diff."""

from cloud_janitor.core.scan_diff import diff_high_severity_findings


class TestDiffHighSeverityFindings:
    """Example-based tests for diff_high_severity_findings."""

    def test_new_high_finding_returned(self):
        """A HIGH finding with no prior entry is escalated."""
        previous = {}
        current = [{"resource_id": "vol-123", "severity": "HIGH", "description": "unencrypted"}]
        result = diff_high_severity_findings(previous, current)
        assert len(result) == 1
        assert result[0]["resource_id"] == "vol-123"

    def test_new_critical_finding_returned(self):
        """A CRITICAL finding with no prior entry is escalated."""
        previous = {}
        current = [{"resource_id": "sg-abc", "severity": "CRITICAL", "description": "open port"}]
        result = diff_high_severity_findings(previous, current)
        assert len(result) == 1
        assert result[0]["resource_id"] == "sg-abc"

    def test_same_high_twice_not_escalated(self):
        """A HIGH finding that was HIGH last time is NOT escalated."""
        previous = {"vol-123": "HIGH"}
        current = [{"resource_id": "vol-123", "severity": "HIGH"}]
        result = diff_high_severity_findings(previous, current)
        assert result == []

    def test_medium_to_high_is_escalated(self):
        """A finding that went from MEDIUM to HIGH IS escalated."""
        previous = {"vol-123": "MEDIUM"}
        current = [{"resource_id": "vol-123", "severity": "HIGH"}]
        result = diff_high_severity_findings(previous, current)
        assert len(result) == 1
        assert result[0]["resource_id"] == "vol-123"

    def test_high_to_critical_is_escalated(self):
        """A finding that went from HIGH to CRITICAL IS escalated."""
        previous = {"sg-abc": "HIGH"}
        current = [{"resource_id": "sg-abc", "severity": "CRITICAL"}]
        result = diff_high_severity_findings(previous, current)
        assert len(result) == 1
        assert result[0]["severity"] == "CRITICAL"

    def test_critical_to_critical_not_escalated(self):
        """A CRITICAL that was already CRITICAL is NOT escalated."""
        previous = {"sg-abc": "CRITICAL"}
        current = [{"resource_id": "sg-abc", "severity": "CRITICAL"}]
        result = diff_high_severity_findings(previous, current)
        assert result == []

    def test_low_and_medium_findings_excluded(self):
        """LOW and MEDIUM findings are never returned regardless of prior state."""
        previous = {}
        current = [
            {"resource_id": "vol-1", "severity": "LOW"},
            {"resource_id": "vol-2", "severity": "MEDIUM"},
        ]
        result = diff_high_severity_findings(previous, current)
        assert result == []

    def test_mixed_findings_only_escalated_returned(self):
        """Only findings meeting both criteria (HIGH+ AND new/escalated) are returned."""
        previous = {"vol-1": "HIGH", "vol-2": "MEDIUM"}
        current = [
            {"resource_id": "vol-1", "severity": "HIGH"},      # same — excluded
            {"resource_id": "vol-2", "severity": "HIGH"},      # escalated
            {"resource_id": "vol-3", "severity": "CRITICAL"},  # new
            {"resource_id": "vol-4", "severity": "LOW"},       # below threshold
        ]
        result = diff_high_severity_findings(previous, current)
        ids = [f["resource_id"] for f in result]
        assert "vol-2" in ids
        assert "vol-3" in ids
        assert "vol-1" not in ids
        assert "vol-4" not in ids
        assert len(result) == 2

    def test_empty_current_returns_empty(self):
        """No current findings → empty result."""
        previous = {"vol-1": "HIGH"}
        result = diff_high_severity_findings(previous, [])
        assert result == []

    def test_missing_severity_defaults_to_low(self):
        """A finding without a severity key defaults to LOW and is excluded."""
        previous = {}
        current = [{"resource_id": "vol-1"}]
        result = diff_high_severity_findings(previous, current)
        assert result == []
