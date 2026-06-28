"""Property-based tests for backward compatibility of FixtureProvider.

Property 1: Fixture backend behavioral equivalence — for any valid resource_type
and min_idle_days, FixtureProvider output matches the original inline implementation
output. This uses independent reference functions as the oracle.

**Validates: Requirements 8.1, 8.3**
"""

import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from mcp_server.backends.fixture_provider import FixtureProvider


# --- Reference Implementation (Oracle) ---
# These standalone functions replicate the original inline logic from aws_janitor_mcp.py
# exactly as it existed before the refactoring. They serve as the independent oracle
# to compare FixtureProvider against.


def reference_get_cost_data(fixtures_dir: Path, resource_type=None, min_idle_days: int = 7) -> dict:
    """Reference implementation of the original inline cost data logic."""
    fixture_path = fixtures_dir / "aws_cost_explorer.json"
    if not fixture_path.exists():
        return {"error": f"Fixture not found: {fixture_path}", "resources": [], "total_monthly_waste": 0.0}

    with open(fixture_path) as f:
        data = json.load(f)

    resources = data["resources"]
    if resource_type:
        resources = [r for r in resources if r["type"] == resource_type]
    resources = [r for r in resources if r["idle_days"] >= min_idle_days]

    total_waste = sum(r["monthly_cost"] for r in resources)
    return {"resources": resources, "total_monthly_waste": round(total_waste, 2)}


def reference_get_security_data(fixtures_dir: Path, check_type=None) -> dict:
    """Reference implementation of the original inline security data logic."""
    fixture_path = fixtures_dir / "aws_config_inspector.json"
    if not fixture_path.exists():
        return {"error": f"Fixture not found: {fixture_path}", "findings": [], "critical_count": 0}

    with open(fixture_path) as f:
        data = json.load(f)

    findings = data["findings"]
    if check_type:
        findings = [f for f in findings if f["check_type"] == check_type]

    critical = sum(1 for f in findings if f["severity"] == "CRITICAL")
    return {"findings": findings, "critical_count": critical}


def reference_check_dependencies(fixtures_dir: Path, resource_id: str) -> dict:
    """Reference implementation of the original inline dependency check logic."""
    fixture_path = fixtures_dir / "aws_config_inspector.json"
    if not fixture_path.exists():
        return {"error": f"Fixture not found: {fixture_path}", "has_dependencies": False, "dependents": []}

    with open(fixture_path) as f:
        data = json.load(f)

    deps = data.get("dependencies", {}).get(resource_id, [])
    return {"has_dependencies": len(deps) > 0, "dependents": deps}


# --- Strategies ---

RESOURCE_TYPES = ["elasticache", "ebs", "ec2", "rds", "s3"]
CHECK_TYPES = ["security_group", "encryption", "public_access"]
SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]

cost_resource_strategy = st.fixed_dictionaries({
    "id": st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N", "Pd"))),
    "type": st.sampled_from(RESOURCE_TYPES),
    "name": st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "Zs"))),
    "idle_days": st.integers(min_value=0, max_value=365),
    "monthly_cost": st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
    "status": st.sampled_from(["available", "in-use", "stopped"]),
})

cost_resources_strategy = st.lists(cost_resource_strategy, min_size=0, max_size=20)

security_finding_strategy = st.fixed_dictionaries({
    "id": st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N", "Pd"))),
    "resource_id": st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N", "Pd"))),
    "resource_type": st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N"))),
    "check_type": st.sampled_from(CHECK_TYPES),
    "severity": st.sampled_from(SEVERITIES),
    "current_state": st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L",))),
    "required_state": st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L",))),
    "title": st.text(min_size=1, max_size=80, alphabet=st.characters(whitelist_categories=("L", "N", "Zs"))),
    "description": st.text(min_size=1, max_size=200, alphabet=st.characters(whitelist_categories=("L", "N", "Zs"))),
})

security_findings_strategy = st.lists(security_finding_strategy, min_size=0, max_size=20)

dependency_map_strategy = st.dictionaries(
    keys=st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N", "Pd"))),
    values=st.lists(
        st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N", "Pd"))),
        min_size=0,
        max_size=5,
    ),
    min_size=0,
    max_size=10,
)


# --- Property 1: Fixture backend behavioral equivalence ---


@settings(max_examples=100, deadline=None)
@given(
    resources=cost_resources_strategy,
    resource_type_filter=st.one_of(st.none(), st.sampled_from(RESOURCE_TYPES)),
    min_idle_days=st.integers(min_value=0, max_value=365),
)
def test_cost_data_matches_reference_implementation(resources, resource_type_filter, min_idle_days):
    """
    Property 1a: Fixture backend behavioral equivalence for get_cost_data.

    For any valid combination of resource_type and min_idle_days, FixtureProvider
    output must be identical to the reference implementation output given the same
    fixture data. This ensures the refactored code preserves original behavior.

    **Validates: Requirements 8.1, 8.3**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        fixtures_dir = Path(tmp_dir)

        # Write cost fixture
        cost_fixture = {"resources": resources}
        (fixtures_dir / "aws_cost_explorer.json").write_text(
            json.dumps(cost_fixture), encoding="utf-8"
        )

        # Run FixtureProvider
        provider = FixtureProvider(fixtures_dir=fixtures_dir)
        provider_result = provider.get_cost_data(
            resource_type=resource_type_filter, min_idle_days=min_idle_days
        )

        # Run reference implementation
        reference_result = reference_get_cost_data(
            fixtures_dir, resource_type=resource_type_filter, min_idle_days=min_idle_days
        )

        # Deep equality: both must produce identical output
        assert provider_result == reference_result, (
            f"FixtureProvider diverged from reference!\n"
            f"  Provider:  {provider_result}\n"
            f"  Reference: {reference_result}\n"
            f"  Params: resource_type={resource_type_filter}, min_idle_days={min_idle_days}"
        )

        # Schema validation on provider output
        assert "resources" in provider_result
        assert "total_monthly_waste" in provider_result
        assert isinstance(provider_result["resources"], list)
        assert isinstance(provider_result["total_monthly_waste"], (int, float))


@settings(max_examples=100, deadline=None)
@given(
    findings=security_findings_strategy,
    check_type_filter=st.one_of(st.none(), st.sampled_from(CHECK_TYPES)),
)
def test_security_data_matches_reference_implementation(findings, check_type_filter):
    """
    Property 1b: Fixture backend behavioral equivalence for get_security_data.

    For any security findings and any check_type filter, FixtureProvider output
    must be identical to the reference implementation output.

    **Validates: Requirements 8.1, 8.3**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        fixtures_dir = Path(tmp_dir)

        # Write security fixture
        security_fixture = {"findings": findings, "dependencies": {}}
        (fixtures_dir / "aws_config_inspector.json").write_text(
            json.dumps(security_fixture), encoding="utf-8"
        )

        # Run FixtureProvider
        provider = FixtureProvider(fixtures_dir=fixtures_dir)
        provider_result = provider.get_security_data(check_type=check_type_filter)

        # Run reference implementation
        reference_result = reference_get_security_data(
            fixtures_dir, check_type=check_type_filter
        )

        # Deep equality
        assert provider_result == reference_result, (
            f"FixtureProvider diverged from reference!\n"
            f"  Provider:  {provider_result}\n"
            f"  Reference: {reference_result}\n"
            f"  Params: check_type={check_type_filter}"
        )

        # Schema validation
        assert "findings" in provider_result
        assert "critical_count" in provider_result
        assert isinstance(provider_result["findings"], list)
        assert isinstance(provider_result["critical_count"], int)


@settings(max_examples=100, deadline=None)
@given(
    dependency_map=dependency_map_strategy,
    data=st.data(),
)
def test_check_dependencies_matches_reference_implementation(dependency_map, data):
    """
    Property 1c: Fixture backend behavioral equivalence for check_dependencies.

    For any dependency map and any resource_id (whether present in the map or not),
    FixtureProvider output must be identical to the reference implementation output.

    **Validates: Requirements 8.1, 8.3**
    """
    # Draw a resource_id: either one from the map or a random one not in the map
    if dependency_map:
        resource_id = data.draw(
            st.one_of(
                st.sampled_from(list(dependency_map.keys())),
                st.text(min_size=1, max_size=30, alphabet=st.characters(
                    whitelist_categories=("L", "N", "Pd")
                )),
            )
        )
    else:
        resource_id = data.draw(
            st.text(min_size=1, max_size=30, alphabet=st.characters(
                whitelist_categories=("L", "N", "Pd")
            ))
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        fixtures_dir = Path(tmp_dir)

        # Write fixture with dependencies
        fixture_data = {"findings": [], "dependencies": dependency_map}
        (fixtures_dir / "aws_config_inspector.json").write_text(
            json.dumps(fixture_data), encoding="utf-8"
        )

        # Run FixtureProvider
        provider = FixtureProvider(fixtures_dir=fixtures_dir)
        provider_result = provider.check_dependencies(resource_id)

        # Run reference implementation
        reference_result = reference_check_dependencies(fixtures_dir, resource_id)

        # Deep equality
        assert provider_result == reference_result, (
            f"FixtureProvider diverged from reference!\n"
            f"  Provider:  {provider_result}\n"
            f"  Reference: {reference_result}\n"
            f"  Params: resource_id={resource_id!r}"
        )

        # Schema validation
        assert "has_dependencies" in provider_result
        assert "dependents" in provider_result
        assert isinstance(provider_result["has_dependencies"], bool)
        assert isinstance(provider_result["dependents"], list)


# --- Negative case: missing fixture files ---


@settings(max_examples=50, deadline=None)
@given(
    resource_type_filter=st.one_of(st.none(), st.sampled_from(RESOURCE_TYPES)),
    min_idle_days=st.integers(min_value=0, max_value=365),
)
def test_missing_fixture_cost_data_matches_reference(resource_type_filter, min_idle_days):
    """
    Negative case: When fixture files don't exist, both FixtureProvider and
    reference must produce the same error response.

    **Validates: Requirements 8.1, 8.3**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        fixtures_dir = Path(tmp_dir)
        # No fixture files written — directory is empty

        provider = FixtureProvider(fixtures_dir=fixtures_dir)
        provider_result = provider.get_cost_data(
            resource_type=resource_type_filter, min_idle_days=min_idle_days
        )

        reference_result = reference_get_cost_data(
            fixtures_dir, resource_type=resource_type_filter, min_idle_days=min_idle_days
        )

        assert provider_result == reference_result
        # Verify error structure
        assert "error" in provider_result
        assert provider_result["resources"] == []
        assert provider_result["total_monthly_waste"] == 0.0


@settings(max_examples=50, deadline=None)
@given(
    check_type_filter=st.one_of(st.none(), st.sampled_from(CHECK_TYPES)),
)
def test_missing_fixture_security_data_matches_reference(check_type_filter):
    """
    Negative case: When fixture files don't exist, security data error response
    must match between FixtureProvider and reference.

    **Validates: Requirements 8.1, 8.3**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        fixtures_dir = Path(tmp_dir)

        provider = FixtureProvider(fixtures_dir=fixtures_dir)
        provider_result = provider.get_security_data(check_type=check_type_filter)

        reference_result = reference_get_security_data(
            fixtures_dir, check_type=check_type_filter
        )

        assert provider_result == reference_result
        assert "error" in provider_result
        assert provider_result["findings"] == []
        assert provider_result["critical_count"] == 0


def test_missing_fixture_check_dependencies_matches_reference():
    """
    Negative case: When fixture files don't exist, check_dependencies error
    response must match between FixtureProvider and reference.

    **Validates: Requirements 8.1, 8.3**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        fixtures_dir = Path(tmp_dir)

        provider = FixtureProvider(fixtures_dir=fixtures_dir)
        provider_result = provider.check_dependencies("nonexistent-resource")

        reference_result = reference_check_dependencies(fixtures_dir, "nonexistent-resource")

        assert provider_result == reference_result
        assert "error" in provider_result
        assert provider_result["has_dependencies"] is False
        assert provider_result["dependents"] == []
