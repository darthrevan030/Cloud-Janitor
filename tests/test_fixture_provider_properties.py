"""Property-based tests for FixtureProvider.

Uses Hypothesis to validate structural invariants of the FixtureProvider
across randomly generated fixture data.
"""

import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from mcp_server.backends.fixture_provider import FixtureProvider


# --- Strategies ---

# Valid resource types for cost data
RESOURCE_TYPES = ["elasticache", "ebs", "ec2", "rds", "s3"]

# Valid check types for security data
CHECK_TYPES = ["security_group", "encryption", "public_access"]

# Valid severities for security findings
SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]

# Generate a single cost resource
cost_resource_strategy = st.fixed_dictionaries({
    "id": st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N", "Pd"))),
    "type": st.sampled_from(RESOURCE_TYPES),
    "name": st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "Zs"))),
    "idle_days": st.integers(min_value=0, max_value=365),
    "monthly_cost": st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
    "status": st.sampled_from(["available", "in-use", "stopped"]),
})

# Generate a list of cost resources
cost_resources_strategy = st.lists(cost_resource_strategy, min_size=0, max_size=20)

# Generate a single security finding
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

# Generate a list of security findings
security_findings_strategy = st.lists(security_finding_strategy, min_size=0, max_size=20)

# Generate dependency maps: resource_id -> list of dependent resource_ids
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


# --- Property 2: Cost data structural invariants ---


@settings(max_examples=100, deadline=None)
@given(
    resources=cost_resources_strategy,
    resource_type_filter=st.one_of(st.none(), st.sampled_from(RESOURCE_TYPES)),
    min_idle_days=st.integers(min_value=0, max_value=365),
)
def test_cost_data_total_waste_equals_sum_of_filtered_costs(resources, resource_type_filter, min_idle_days):
    """
    Property 2: Cost data structural invariants

    For any fixture data and any combination of resource_type filter and
    min_idle_days threshold, total_monthly_waste must equal round(sum of
    monthly_cost for all returned resources, 2), all returned resources
    must match the resource_type filter (if provided), and all returned
    resources must have idle_days >= min_idle_days.

    **Validates: Requirements 2.2, 2.3, 2.4**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        fixtures_dir = Path(tmp_dir)

        # Write cost fixture
        cost_fixture = {"resources": resources}
        (fixtures_dir / "aws_cost_explorer.json").write_text(
            json.dumps(cost_fixture), encoding="utf-8"
        )

        provider = FixtureProvider(fixtures_dir=fixtures_dir)
        result = provider.get_cost_data(
            resource_type=resource_type_filter, min_idle_days=min_idle_days
        )

        # Schema validation: required keys exist with correct types
        assert "resources" in result
        assert "total_monthly_waste" in result
        assert isinstance(result["resources"], list)
        assert isinstance(result["total_monthly_waste"], (int, float))

        # Structural invariant: total_monthly_waste == round(sum of costs, 2)
        expected_total = round(sum(r["monthly_cost"] for r in result["resources"]), 2)
        assert result["total_monthly_waste"] == expected_total, (
            f"total_monthly_waste={result['total_monthly_waste']} != "
            f"round(sum(costs), 2)={expected_total}"
        )

        # Filter correctness: all returned resources match resource_type filter
        if resource_type_filter is not None:
            for r in result["resources"]:
                assert r["type"] == resource_type_filter, (
                    f"Resource {r['id']} has type={r['type']} but filter={resource_type_filter}"
                )

        # Filter correctness: all returned resources have idle_days >= min_idle_days
        for r in result["resources"]:
            assert r["idle_days"] >= min_idle_days, (
                f"Resource {r['id']} has idle_days={r['idle_days']} < min_idle_days={min_idle_days}"
            )


# --- Property 3: Security data critical count consistency ---


@settings(max_examples=100, deadline=None)
@given(
    findings=security_findings_strategy,
    check_type_filter=st.one_of(st.none(), st.sampled_from(CHECK_TYPES)),
)
def test_security_data_critical_count_matches_critical_findings(findings, check_type_filter):
    """
    Property 3: Security data critical count consistency

    For any fixture data and any check_type filter (None or a specific type),
    critical_count must equal the number of findings in the returned list
    where severity == "CRITICAL", and all returned findings must match the
    check_type filter (if provided).

    **Validates: Requirements 2.6, 2.7**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        fixtures_dir = Path(tmp_dir)

        # Write security fixture
        security_fixture = {"findings": findings, "dependencies": {}}
        (fixtures_dir / "aws_config_inspector.json").write_text(
            json.dumps(security_fixture), encoding="utf-8"
        )

        provider = FixtureProvider(fixtures_dir=fixtures_dir)
        result = provider.get_security_data(check_type=check_type_filter)

        # Schema validation: required keys exist with correct types
        assert "findings" in result
        assert "critical_count" in result
        assert isinstance(result["findings"], list)
        assert isinstance(result["critical_count"], int)

        # Structural invariant: critical_count == count of CRITICAL findings
        expected_critical = sum(
            1 for f in result["findings"] if f["severity"] == "CRITICAL"
        )
        assert result["critical_count"] == expected_critical, (
            f"critical_count={result['critical_count']} != "
            f"actual CRITICAL count={expected_critical}"
        )

        # Filter correctness: all returned findings match check_type filter
        if check_type_filter is not None:
            for f in result["findings"]:
                assert f["check_type"] == check_type_filter, (
                    f"Finding {f['id']} has check_type={f['check_type']} "
                    f"but filter={check_type_filter}"
                )


# --- Property 4: Dependency response boolean consistency ---


@settings(max_examples=100, deadline=None)
@given(
    dependency_map=dependency_map_strategy,
    data=st.data(),
)
def test_dependency_response_boolean_consistency(dependency_map, data):
    """
    Property 4: Dependency response boolean consistency

    For any dependency map and any resource_id, has_dependencies must be True
    if and only if len(dependents) > 0. The response must always contain both
    "has_dependencies" and "dependents" keys.

    **Validates: Requirements 2.8, 2.9**
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

        provider = FixtureProvider(fixtures_dir=fixtures_dir)
        result = provider.check_dependencies(resource_id)

        # Schema validation: required keys exist with correct types
        assert "has_dependencies" in result, "Missing 'has_dependencies' key"
        assert "dependents" in result, "Missing 'dependents' key"
        assert isinstance(result["has_dependencies"], bool)
        assert isinstance(result["dependents"], list)

        # Boolean consistency: has_dependencies == (len(dependents) > 0)
        assert result["has_dependencies"] == (len(result["dependents"]) > 0), (
            f"has_dependencies={result['has_dependencies']} but "
            f"len(dependents)={len(result['dependents'])}"
        )
