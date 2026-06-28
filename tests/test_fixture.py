"""Validation tests for aws_cost_explorer.json fixture.

Ensures the fixture contains the expected data for downstream tests:
- Correct number of resources
- At least one flaggable resource (idle >= 30 days)
- At least one resource below threshold (idle < 7 days) for negative testing
- All resources have required schema fields with correct types
"""

import json
from pathlib import Path

import pytest


FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "aws_cost_explorer.json"


@pytest.fixture
def fixture_data():
    """Load the aws_cost_explorer.json fixture."""
    with open(FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def resources(fixture_data):
    """Extract the resources list from the fixture."""
    return fixture_data["resources"]


class TestFixtureSchema:
    """Verify fixture schema and required fields."""

    def test_fixture_file_exists(self):
        """The fixture file must exist on disk."""
        assert FIXTURE_PATH.exists(), f"Fixture not found at {FIXTURE_PATH}"

    def test_resources_key_exists(self, fixture_data):
        """Top-level key 'resources' must be present."""
        assert "resources" in fixture_data
        assert isinstance(fixture_data["resources"], list)

    def test_resource_count(self, resources):
        """Fixture contains exactly 3 resources."""
        assert len(resources) == 3

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
    """Verify the fixture has the right mix of flaggable/non-flaggable resources."""

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
        """Verify the exact IDs of flaggable resources."""
        flaggable_ids = {r["id"] for r in resources if r["idle_days"] >= 30}
        assert flaggable_ids == {"cache-prod-legacy-01", "vol-0abc123def456789a"}

    def test_non_flaggable_resource_is_below_threshold(self, resources):
        """The non-flaggable resource must be clearly below the 30-day threshold."""
        non_flaggable = [r for r in resources if r["idle_days"] < 30]
        assert len(non_flaggable) == 1
        # It should be well below threshold (5 days, not 29)
        assert non_flaggable[0]["idle_days"] < 7
