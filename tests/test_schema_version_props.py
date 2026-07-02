"""Property-based tests for findings store schema versioning.

**Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5**

Uses Hypothesis to verify universal properties of schema version validation
and findings store schema version presence across all valid inputs.
"""

import json
import re
from unittest.mock import patch

import pytest
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from orchestrator import Orchestrator, SCHEMA_VERSION


# Shared settings for all property tests in this module
_SETTINGS = settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# --- Fixtures ---


@pytest.fixture
def tmp_project(tmp_path):
    """Set up a temporary project structure for testing."""
    (tmp_path / "hooks").mkdir(parents=True)
    (tmp_path / "output" / "rollbacks").mkdir(parents=True)
    (tmp_path / "output" / "logs").mkdir(parents=True)
    (tmp_path / "output" / "policies").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def orchestrator(tmp_project):
    """Create an Orchestrator instance with mocked TF_CMD."""
    with patch("orchestrator._validate_tf_cmd", return_value="/usr/bin/tflocal"):
        return Orchestrator(project_root=tmp_project)


# --- Strategies ---

# Parse expected major/minor from SCHEMA_VERSION constant
_EXPECTED_MAJOR = int(SCHEMA_VERSION.split(".")[0])
_EXPECTED_MINOR = int(SCHEMA_VERSION.split(".")[1])

SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


@st.composite
def semver_matching_major(draw):
    """Generate a semver string with the same major version as SCHEMA_VERSION."""
    minor = draw(st.integers(min_value=0, max_value=999))
    patch_v = draw(st.integers(min_value=0, max_value=999))
    return f"{_EXPECTED_MAJOR}.{minor}.{patch_v}"


@st.composite
def semver_different_major(draw):
    """Generate a semver string with a different major version."""
    major = draw(st.integers(min_value=0, max_value=99).filter(lambda m: m != _EXPECTED_MAJOR))
    minor = draw(st.integers(min_value=0, max_value=999))
    patch_v = draw(st.integers(min_value=0, max_value=999))
    return f"{major}.{minor}.{patch_v}"


@st.composite
def non_parseable_version_string(draw):
    """Generate strings whose major version cannot be parsed as an integer,
    or that are empty. These SHALL always be rejected by _validate_schema_version."""
    strategy = st.one_of(
        # Empty string
        st.just(""),
        # Strings starting with non-digit characters
        st.text(
            min_size=1, max_size=50,
            alphabet=st.characters(whitelist_categories=("L", "P", "S", "Z")),
        ),
        # Strings like "abc.0.0" where first segment isn't numeric
        st.builds(
            lambda prefix, rest: f"{prefix}.{rest}",
            st.text(min_size=1, max_size=10, alphabet=st.characters(
                whitelist_categories=("L",)
            )),
            st.text(min_size=1, max_size=10),
        ),
    )
    return draw(strategy)


@st.composite
def semver_higher_minor(draw):
    """Generate a semver string with same major but higher minor than expected."""
    minor = draw(st.integers(min_value=_EXPECTED_MINOR + 1, max_value=999))
    patch_v = draw(st.integers(min_value=0, max_value=999))
    return f"{_EXPECTED_MAJOR}.{minor}.{patch_v}"


@st.composite
def semver_same_or_lower_minor(draw):
    """Generate a semver string with same major and same-or-lower minor."""
    minor = draw(st.integers(min_value=0, max_value=_EXPECTED_MINOR))
    patch_v = draw(st.integers(min_value=0, max_value=999))
    return f"{_EXPECTED_MAJOR}.{minor}.{patch_v}"


# Arbitrary findings list entries
finding_strategy = st.fixed_dictionaries({
    "id": st.text(min_size=1, max_size=20, alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-_"
    )),
    "agent": st.sampled_from(["finops", "secops"]),
    "resource_id": st.text(min_size=1, max_size=40, alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-_:."
    )),
})

metadata_strategy = st.fixed_dictionaries({
    "scan_id": st.text(min_size=1, max_size=30, alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-_"
    )),
})


# --- Property 10: Schema Version Validation ---


class TestProperty10SchemaVersionValidation:
    """Property 10: Schema Version Validation.

    *For any* semantic version string in the findings store, validation SHALL pass
    if and only if the major version matches the expected major version. A missing
    `schema_version` field SHALL always be rejected. A higher minor version with
    matching major SHALL produce a WARNING but still pass.

    **Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5**
    """

    @given(version=semver_matching_major())
    @_SETTINGS
    def test_matching_major_passes_validation(self, version, orchestrator):
        """Any semver with matching major version SHALL pass validation (return None)."""
        store = {"schema_version": version, "findings": []}
        error = orchestrator._validate_schema_version(store)
        assert error is None, (
            f"Expected validation to pass for version '{version}' "
            f"(major matches {_EXPECTED_MAJOR}), but got error: {error}"
        )

    @given(version=semver_different_major())
    @_SETTINGS
    def test_different_major_rejects_validation(self, version, orchestrator):
        """Any semver with different major version SHALL be rejected."""
        store = {"schema_version": version, "findings": []}
        error = orchestrator._validate_schema_version(store)
        assert error is not None, (
            f"Expected rejection for version '{version}' "
            f"(major differs from {_EXPECTED_MAJOR}), but validation passed"
        )
        assert "Incompatible schema version" in error
        assert version in error

    @given(data=st.data())
    @_SETTINGS
    def test_missing_schema_version_always_rejected(self, data, orchestrator):
        """A store without schema_version field SHALL always be rejected."""
        findings = data.draw(st.lists(finding_strategy, min_size=0, max_size=5))
        store = {"findings": findings}
        store.pop("schema_version", None)

        error = orchestrator._validate_schema_version(store)
        assert error == "schema_version field is missing", (
            f"Expected 'schema_version field is missing', got: {error}"
        )

    @given(version=semver_higher_minor())
    @_SETTINGS
    def test_higher_minor_passes_but_warns(self, version, orchestrator, caplog):
        """Higher minor with matching major SHALL pass validation (return None)."""
        import logging

        store = {"schema_version": version, "findings": []}
        with caplog.at_level(logging.WARNING):
            error = orchestrator._validate_schema_version(store)

        assert error is None, (
            f"Expected validation to pass for version '{version}' "
            f"(higher minor), but got error: {error}"
        )
        assert any("minor version" in rec.message.lower() for rec in caplog.records), (
            f"Expected WARNING about minor version for '{version}', "
            f"but no warning found in logs"
        )

    @given(version=semver_same_or_lower_minor())
    @_SETTINGS
    def test_same_or_lower_minor_no_warning(self, version, orchestrator, caplog):
        """Same or lower minor with matching major SHALL pass without WARNING."""
        import logging

        store = {"schema_version": version, "findings": []}
        with caplog.at_level(logging.WARNING):
            error = orchestrator._validate_schema_version(store)

        assert error is None, (
            f"Expected validation to pass for version '{version}', "
            f"but got error: {error}"
        )
        assert not any("minor version" in rec.message.lower() for rec in caplog.records), (
            f"Unexpected WARNING about minor version for '{version}'"
        )

    @given(bad_value=non_parseable_version_string())
    @_SETTINGS
    def test_non_parseable_version_rejected(self, bad_value, orchestrator):
        """Any string whose major version cannot be parsed SHALL be rejected."""
        store = {"schema_version": bad_value, "findings": []}
        error = orchestrator._validate_schema_version(store)
        assert error is not None, (
            f"Expected rejection for unparseable value '{bad_value}', "
            f"but validation passed"
        )

    @_SETTINGS
    @given(version=st.none())
    def test_none_schema_version_rejected(self, version, orchestrator):
        """Explicit None value SHALL be rejected as missing."""
        store = {"schema_version": None, "findings": []}
        error = orchestrator._validate_schema_version(store)
        assert error == "schema_version field is missing"


# --- Property 15: Findings Store Schema Version Presence ---


class TestProperty15FindingsStoreSchemaVersionPresence:
    """Property 15: Findings Store Schema Version Presence.

    *For any* audit run that writes a findings store, the resulting JSON file
    SHALL contain a top-level `schema_version` field whose value is a string
    matching the pattern `^\\d+\\.\\d+\\.\\d+$`.

    **Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5**
    """

    @given(
        findings=st.lists(finding_strategy, min_size=0, max_size=10),
        metadata=metadata_strategy,
    )
    @_SETTINGS
    def test_written_store_has_schema_version_field(
        self, findings, metadata, orchestrator
    ):
        """Written findings store SHALL contain a top-level schema_version field."""
        orchestrator._write_findings_store(findings=findings, metadata=metadata)

        with open(orchestrator.findings_store_path) as f:
            store = json.load(f)

        assert "schema_version" in store, (
            "Findings store missing 'schema_version' field after write"
        )

    @given(
        findings=st.lists(finding_strategy, min_size=0, max_size=10),
        metadata=metadata_strategy,
    )
    @_SETTINGS
    def test_schema_version_is_string(self, findings, metadata, orchestrator):
        """schema_version field SHALL be a string value."""
        orchestrator._write_findings_store(findings=findings, metadata=metadata)

        with open(orchestrator.findings_store_path) as f:
            store = json.load(f)

        assert isinstance(store["schema_version"], str), (
            f"Expected schema_version to be str, got {type(store['schema_version']).__name__}"
        )

    @given(
        findings=st.lists(finding_strategy, min_size=0, max_size=10),
        metadata=metadata_strategy,
    )
    @_SETTINGS
    def test_schema_version_matches_semver_pattern(
        self, findings, metadata, orchestrator
    ):
        """schema_version SHALL match the pattern ^\\d+\\.\\d+\\.\\d+$."""
        orchestrator._write_findings_store(findings=findings, metadata=metadata)

        with open(orchestrator.findings_store_path) as f:
            store = json.load(f)

        version = store["schema_version"]
        assert SEMVER_PATTERN.match(version), (
            f"schema_version '{version}' does not match semver pattern "
            r"^\d+\.\d+\.\d+$"
        )

    @given(
        findings=st.lists(finding_strategy, min_size=0, max_size=10),
        metadata=metadata_strategy,
    )
    @_SETTINGS
    def test_schema_version_equals_constant(self, findings, metadata, orchestrator):
        """Written schema_version SHALL equal the SCHEMA_VERSION constant."""
        orchestrator._write_findings_store(findings=findings, metadata=metadata)

        with open(orchestrator.findings_store_path) as f:
            store = json.load(f)

        assert store["schema_version"] == SCHEMA_VERSION, (
            f"Expected schema_version '{SCHEMA_VERSION}', "
            f"got '{store['schema_version']}'"
        )

    @given(
        findings=st.lists(finding_strategy, min_size=0, max_size=10),
        metadata=metadata_strategy,
    )
    @_SETTINGS
    def test_written_store_passes_own_validation(
        self, findings, metadata, orchestrator
    ):
        """Written findings store SHALL pass _validate_schema_version."""
        orchestrator._write_findings_store(findings=findings, metadata=metadata)

        with open(orchestrator.findings_store_path) as f:
            store = json.load(f)

        error = orchestrator._validate_schema_version(store)
        assert error is None, (
            f"Freshly written store failed validation: {error}"
        )
