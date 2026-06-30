"""Validation tests for fixture data files.

Ensures the fixtures contain the expected data for downstream tests:
- Correct number of resources
- At least one flaggable resource (idle >= 30 days, matching FinOps agent threshold)
- At least one resource below threshold (idle < 7 days) for negative testing
- All resources have required schema fields with correct types
- All required resource_types are present (Req 12.1)
- All required check_types are present (Req 12.1)
"""

import json
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
COST_FIXTURE_PATH = FIXTURES_DIR / "aws_cost_explorer.json"
SECURITY_FIXTURE_PATH = FIXTURES_DIR / "aws_config_inspector.json"


@pytest.fixture
def cost_fixture_data():
    """Load the aws_cost_explorer.json fixture."""
    with open(COST_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def resources(cost_fixture_data):
    """Extract the resources list from the cost fixture."""
    return cost_fixture_data["resources"]


@pytest.fixture
def security_fixture_data():
    """Load the aws_config_inspector.json fixture."""
    with open(SECURITY_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def findings(security_fixture_data):
    """Extract the findings list from the security fixture."""
    return security_fixture_data["findings"]


class TestFixtureSchema:
    """Verify fixture schema and required fields."""

    def test_fixture_file_exists(self):
        """The fixture file must exist on disk."""
        assert COST_FIXTURE_PATH.exists(), f"Fixture not found at {COST_FIXTURE_PATH}"

    def test_resources_key_exists(self, cost_fixture_data):
        """Top-level key 'resources' must be present."""
        assert "resources" in cost_fixture_data
        assert isinstance(cost_fixture_data["resources"], list)

    def test_resource_count(self, resources):
        """Fixture contains exactly 4 resources."""
        assert len(resources) == 4

    def test_each_resource_has_required_fields(self, resources):
        """Every resource must have id, type, idle_days, monthly_cost."""
        required_keys = {"id", "type", "idle_days", "monthly_cost"}
        for r in resources:
            missing = required_keys - set(r.keys())
            assert missing == set(), (
                f"Resource {r.get('id', 'UNKNOWN')} is missing keys: {missing}"
            )

    def test_field_types(self, resources):
        """Required fields must have correct types."""
        for r in resources:
            assert isinstance(r["id"], str), f"{r['id']}: id must be str"
            assert isinstance(r["type"], str), f"{r['id']}: type must be str"
            assert isinstance(r["idle_days"], (int, float)), f"{r['id']}: idle_days must be numeric"
            assert isinstance(r["monthly_cost"], (int, float)), f"{r['id']}: monthly_cost must be numeric"


class TestFixtureFlaggableResources:
    """Verify the fixture has the right mix of flaggable/non-flaggable resources.

    'Flaggable' here means eligible for FinOps agent remediation, which uses
    a 30-day idle threshold (FinOpsAuditor.MIN_IDLE_DAYS = 30).
    """

    def test_has_flaggable_resources(self, resources):
        """At least one resource must be idle >= 30 days (flaggable by FinOps)."""
        flaggable = [r for r in resources if r["idle_days"] >= 30]
        assert len(flaggable) == 2, (
            f"Expected 2 flaggable resources (idle >= 30d), got {len(flaggable)}"
        )

    def test_has_non_flaggable_resource(self, resources):
        """At least one resource must be idle < 7 days (negative test data).

        This is critical: if this resource is missing, tests that verify
        'FinOps does NOT flag recent resources' cannot function.
        """
        below_threshold = [r for r in resources if r["idle_days"] < 7]
        assert len(below_threshold) == 1, (
            f"Expected 1 resource below 7-day threshold, got {len(below_threshold)}"
        )
        # Verify it's the expected resource
        assert below_threshold[0]["id"] == "vol-0def456abc789012b"
        assert below_threshold[0]["idle_days"] == 5

    def test_flaggable_resource_ids(self, resources):
        """Verify the exact IDs of flaggable resources (idle >= 30d)."""
        flaggable_ids = {r["id"] for r in resources if r["idle_days"] >= 30}
        assert flaggable_ids == {"cache-prod-legacy-01", "vol-0abc123def456789a"}

    def test_below_remediation_threshold_not_flaggable(self, resources):
        """Resources idle < 30 days must NOT be counted as flaggable.

        The EC2 instance i-0abc123def456ec2a has idle_days=28, which is
        below the FinOps agent's 30-day remediation threshold.
        """
        below_remediation = [r for r in resources if 7 <= r["idle_days"] < 30]
        assert len(below_remediation) == 1
        assert below_remediation[0]["id"] == "i-0abc123def456ec2a"
        assert below_remediation[0]["idle_days"] == 28

    def test_non_flaggable_resource_is_below_threshold(self, resources):
        """The non-flaggable resource must be clearly below the 30-day threshold."""
        non_flaggable = [r for r in resources if r["idle_days"] < 30]
        # 2 resources below 30d: the 5-day EBS and the 28-day EC2
        assert len(non_flaggable) == 2
        # At least one should be well below threshold (< 7 days)
        very_recent = [r for r in non_flaggable if r["idle_days"] < 7]
        assert len(very_recent) == 1
        assert very_recent[0]["idle_days"] < 7


class TestFixtureResourceTypeCoverage:
    """Verify fixture contains all required resource_types for Phase B+C.

    Validates: Requirement 12.1
    """

    REQUIRED_RESOURCE_TYPES = {"elasticache", "ebs", "ec2"}

    def test_all_resource_types_present(self, resources):
        """Fixture must contain at least one resource of each required type."""
        present_types = {r["type"] for r in resources}
        missing = self.REQUIRED_RESOURCE_TYPES - present_types
        assert missing == set(), (
            f"Fixture is missing required resource_types: {missing}"
        )

    def test_each_resource_type_has_flaggable_resource(self, resources):
        """Each resource_type must have at least one resource idle >= 7 days.

        This uses the MCP server's default min_idle_days=7 threshold (not the
        FinOps agent's 30-day remediation threshold). The purpose is to ensure
        every resource type has at least one non-trivial entry for agents to
        exercise their detection logic against.
        """
        for rtype in self.REQUIRED_RESOURCE_TYPES:
            flaggable = [
                r for r in resources
                if r["type"] == rtype and r["idle_days"] >= 7
            ]
            assert len(flaggable) >= 1, (
                f"No resource with idle >= 7d for type '{rtype}'"
            )


class TestFixtureCheckTypeCoverage:
    """Verify fixture contains all required check_types for Phase B+C.

    Validates: Requirement 12.1
    """

    REQUIRED_CHECK_TYPES = {"security_group", "encryption", "public_access"}

    def test_security_fixture_file_exists(self):
        """The security fixture file must exist on disk."""
        assert SECURITY_FIXTURE_PATH.exists(), (
            f"Security fixture not found at {SECURITY_FIXTURE_PATH}"
        )

    def test_findings_key_exists(self, security_fixture_data):
        """Top-level key 'findings' must be present."""
        assert "findings" in security_fixture_data
        assert isinstance(security_fixture_data["findings"], list)

    def test_all_check_types_present(self, findings):
        """Fixture must contain at least one finding for each required check_type."""
        present_types = {f["check_type"] for f in findings}
        missing = self.REQUIRED_CHECK_TYPES - present_types
        assert missing == set(), (
            f"Fixture is missing required check_types: {missing}"
        )

    def test_each_check_type_has_at_least_one_finding(self, findings):
        """Each check_type must have at least one finding."""
        for ctype in self.REQUIRED_CHECK_TYPES:
            matches = [f for f in findings if f["check_type"] == ctype]
            assert len(matches) >= 1, (
                f"No finding for check_type '{ctype}'"
            )

    def test_findings_have_required_fields(self, findings):
        """Every finding must have id, resource_id, check_type, severity."""
        required_keys = {"id", "resource_id", "check_type", "severity"}
        for f in findings:
            missing = required_keys - set(f.keys())
            assert missing == set(), (
                f"Finding {f.get('id', 'UNKNOWN')} is missing keys: {missing}"
            )
