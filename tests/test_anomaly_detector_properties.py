"""Property-based tests for AnomalyDetector.

**Validates: Requirements 6.1, 6.2, 6.3**

Property 8: AnomalyDetector Disjoint Resource IDs
For any resources list and for any findings list, the set of resource_ids
in detect() output SHALL be disjoint from the set of resource_ids in the
findings input.

Property 9: AnomalyDetector Output Schema
For any input, detect() SHALL return a flat list where each element contains:
anomaly_id, resource_id, anomaly_type, description, severity ∈ {"high",
"medium", "low"}, and evidence.
"""

import json
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from agents.anomaly_detector import (
    AnomalyDetector,
    MAX_ANOMALIES,
    REQUIRED_ANOMALY_KEYS,
    VALID_SEVERITIES,
)


def _make_mock_response(content: str) -> MagicMock:
    """Create a mock OpenAI chat completions response."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    return mock_response


@st.composite
def resource_id_strategy(draw):
    """Generate realistic resource IDs."""
    prefix = draw(st.sampled_from(["i-", "vol-", "sg-", "cache-", "r-", "arn:aws:"]))
    suffix = draw(st.text(
        min_size=3, max_size=20,
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    ))
    return prefix + suffix


@st.composite
def resource_strategy(draw):
    """Generate a resource dict with either 'id' or 'resource_id' field."""
    rid = draw(resource_id_strategy())
    use_id_field = draw(st.booleans())
    resource = {}
    if use_id_field:
        resource["id"] = rid
    else:
        resource["resource_id"] = rid
    resource["name"] = draw(st.text(min_size=1, max_size=30))
    resource["type"] = draw(st.sampled_from(["ec2", "ebs", "security_group", "elasticache"]))
    return resource


@st.composite
def finding_strategy(draw):
    """Generate a finding dict with a resource_id field."""
    rid = draw(resource_id_strategy())
    return {
        "resource_id": rid,
        "severity": draw(st.sampled_from(["LOW", "MEDIUM", "HIGH", "CRITICAL"])),
        "check_type": draw(st.sampled_from(["security_group", "encryption", "idle_resource"])),
    }


@st.composite
def anomaly_llm_response(draw, resource_ids: list[str] | None = None):
    """Generate a JSON string mimicking an AnomalyDetector LLM response.

    If resource_ids is provided, anomalies may reference those IDs (adversarial case).
    """
    num_anomalies = draw(st.integers(min_value=0, max_value=25))
    anomalies = []
    for i in range(num_anomalies):
        # Sometimes use a provided resource_id (adversarial), sometimes generate new
        if resource_ids and draw(st.booleans()):
            rid = draw(st.sampled_from(resource_ids))
        else:
            rid = draw(resource_id_strategy())

        severity = draw(st.sampled_from(
            ["high", "medium", "low", "critical", "HIGH", "none", ""]
        ))
        anomaly = {
            "anomaly_id": draw(st.one_of(
                st.text(min_size=1, max_size=40, alphabet=st.characters(
                    whitelist_categories=("L", "N"), whitelist_characters="-_"
                )),
                st.just(""),  # adversarial empty
            )),
            "resource_id": rid,
            "anomaly_type": draw(st.one_of(
                st.sampled_from(["unusual_port", "naming_anomaly", "region_mismatch", "cost_outlier"]),
                st.text(min_size=0, max_size=20),
            )),
            "description": draw(st.one_of(
                st.text(min_size=1, max_size=100),
                st.just(""),  # adversarial empty
            )),
            "severity": severity,
            "evidence": draw(st.one_of(
                st.text(min_size=1, max_size=60),
                st.just(""),  # adversarial empty
            )),
        }
        anomalies.append(anomaly)

    return json.dumps(anomalies)


@st.composite
def adversarial_llm_response_for_disjoint(draw, finding_resource_ids: list[str]):
    """Generate LLM response that deliberately references finding resource_ids.

    This tests that the post-filter correctly removes anomalies referencing findings.
    """
    assume(len(finding_resource_ids) > 0)
    num_anomalies = draw(st.integers(min_value=1, max_value=10))
    anomalies = []
    for i in range(num_anomalies):
        # Always use a finding resource_id to test disjoint enforcement
        rid = draw(st.sampled_from(finding_resource_ids))
        anomalies.append({
            "anomaly_id": f"anomaly-adversarial-{i}",
            "resource_id": rid,
            "anomaly_type": "cost_outlier",
            "description": "Adversarial anomaly referencing a finding resource.",
            "severity": "high",
            "evidence": f"Evidence for adversarial anomaly {i}",
        })
    return json.dumps(anomalies)


def _get_resource_id(resource: dict) -> str:
    """Extract resource_id from a resource dict (mirrors implementation logic)."""
    return resource.get("id", resource.get("resource_id", ""))


def _get_finding_resource_ids(findings: list[dict]) -> set[str]:
    """Extract all resource_ids from findings list."""
    return {f["resource_id"] for f in findings if "resource_id" in f}


class TestAnomalyDetectorDisjointResourceIDs:
    """Property 8: AnomalyDetector Disjoint Resource IDs.

    **Validates: Requirements 6.1**

    For any resources list and for any findings list, the set of resource_ids
    in detect() output SHALL be disjoint from the set of resource_ids in the
    findings input.
    """

    @given(
        resources=st.lists(resource_strategy(), min_size=1, max_size=10),
        findings=st.lists(finding_strategy(), min_size=1, max_size=10),
        llm_response=anomaly_llm_response(),
    )
    @settings(max_examples=200, deadline=None)
    def test_output_resource_ids_disjoint_from_findings(self, resources, findings, llm_response):
        """For any resources and findings, anomaly resource_ids are disjoint from finding resource_ids."""
        finding_resource_ids = _get_finding_resource_ids(findings)

        with patch("agents.anomaly_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(llm_response)
            mock_get_client.return_value = mock_client

            detector = AnomalyDetector()
            result = detector.detect(resources, findings)

        # Core property: output resource_ids ∩ finding resource_ids = ∅
        output_resource_ids = {a["resource_id"] for a in result}
        intersection = output_resource_ids & finding_resource_ids
        assert intersection == set(), (
            f"Anomaly resource_ids must be disjoint from finding resource_ids. "
            f"Overlap: {intersection}"
        )

    @given(
        resources=st.lists(resource_strategy(), min_size=1, max_size=5),
        findings=st.lists(finding_strategy(), min_size=1, max_size=5),
    )
    @settings(max_examples=200, deadline=None)
    def test_adversarial_llm_response_still_disjoint(self, resources, findings):
        """Even when LLM deliberately returns finding resource_ids, output is disjoint."""
        finding_resource_ids = list(_get_finding_resource_ids(findings))
        assume(len(finding_resource_ids) > 0)

        # Craft adversarial response referencing only finding resource_ids
        adversarial_anomalies = [
            {
                "anomaly_id": f"anomaly-bad-{i}",
                "resource_id": fid,
                "anomaly_type": "cost_outlier",
                "description": "Adversarial anomaly.",
                "severity": "high",
                "evidence": "Evidence for adversarial anomaly.",
            }
            for i, fid in enumerate(finding_resource_ids)
        ]
        response_json = json.dumps(adversarial_anomalies)

        with patch("agents.anomaly_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(response_json)
            mock_get_client.return_value = mock_client

            detector = AnomalyDetector()
            result = detector.detect(resources, findings)

        output_resource_ids = {a["resource_id"] for a in result}
        intersection = output_resource_ids & set(finding_resource_ids)
        assert intersection == set(), (
            f"Adversarial anomalies referencing finding IDs must be filtered. "
            f"Overlap: {intersection}"
        )

    @given(
        resources=st.lists(resource_strategy(), min_size=0, max_size=5),
    )
    @settings(max_examples=200, deadline=None)
    def test_empty_findings_no_constraint_violation(self, resources):
        """With empty findings list, disjoint property is trivially satisfied."""
        valid_anomalies = [
            {
                "anomaly_id": f"anomaly-{i}",
                "resource_id": _get_resource_id(r),
                "anomaly_type": "naming_anomaly",
                "description": "Resource has inconsistent naming.",
                "severity": "medium",
                "evidence": "Name does not follow convention.",
            }
            for i, r in enumerate(resources)
        ]
        response_json = json.dumps(valid_anomalies)

        with patch("agents.anomaly_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(response_json)
            mock_get_client.return_value = mock_client

            detector = AnomalyDetector()
            result = detector.detect(resources, [])

        # With no findings, any resource_id is valid — no disjoint violation possible
        output_resource_ids = {a["resource_id"] for a in result}
        assert output_resource_ids & set() == set()


class TestAnomalyDetectorOutputSchema:
    """Property 9: AnomalyDetector Output Schema.

    **Validates: Requirements 6.2, 6.3**

    For any input, detect() SHALL return a flat list where each element contains:
    anomaly_id, resource_id, anomaly_type, description, severity ∈ {"high",
    "medium", "low"}, and evidence. All values are non-empty strings.
    List length ≤ MAX_ANOMALIES (20).
    """

    @given(
        resources=st.lists(resource_strategy(), min_size=1, max_size=10),
        findings=st.lists(finding_strategy(), min_size=0, max_size=5),
    )
    @settings(max_examples=200, deadline=None)
    def test_output_is_flat_list_of_dicts(self, resources, findings):
        """Output is always a flat list (isinstance list), each element is a dict."""
        valid_anomalies = [
            {
                "anomaly_id": f"anomaly-schema-{i}",
                "resource_id": _get_resource_id(r),
                "anomaly_type": "naming_anomaly",
                "description": "Resource has unusual naming pattern.",
                "severity": "low",
                "evidence": "Name prefix does not match team convention.",
            }
            for i, r in enumerate(resources[:5])
        ]
        response_json = json.dumps(valid_anomalies)

        with patch("agents.anomaly_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(response_json)
            mock_get_client.return_value = mock_client

            detector = AnomalyDetector()
            result = detector.detect(resources, findings)

        assert isinstance(result, list), f"Output must be a list, got {type(result)}"
        for item in result:
            assert isinstance(item, dict), f"Each element must be a dict, got {type(item)}"

    @given(
        resources=st.lists(resource_strategy(), min_size=1, max_size=10),
        findings=st.lists(finding_strategy(), min_size=0, max_size=5),
    )
    @settings(max_examples=200, deadline=None)
    def test_each_anomaly_has_exactly_6_required_keys(self, resources, findings):
        """Each anomaly dict has exactly the 6 required keys."""
        valid_anomalies = [
            {
                "anomaly_id": f"anomaly-keys-{i}",
                "resource_id": _get_resource_id(r),
                "anomaly_type": "region_mismatch",
                "description": "Resource deployed in unexpected region.",
                "severity": "medium",
                "evidence": "Region us-west-2 not in team's usual regions.",
            }
            for i, r in enumerate(resources[:3])
        ]
        response_json = json.dumps(valid_anomalies)

        with patch("agents.anomaly_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(response_json)
            mock_get_client.return_value = mock_client

            detector = AnomalyDetector()
            result = detector.detect(resources, findings)

        for anomaly in result:
            assert set(anomaly.keys()) == REQUIRED_ANOMALY_KEYS, (
                f"Expected exactly keys {REQUIRED_ANOMALY_KEYS}, got {set(anomaly.keys())}"
            )

    @given(
        resources=st.lists(resource_strategy(), min_size=1, max_size=10),
        findings=st.lists(finding_strategy(), min_size=0, max_size=5),
    )
    @settings(max_examples=200, deadline=None)
    def test_all_values_are_nonempty_strings(self, resources, findings):
        """Each value in the anomaly dict is a non-empty string."""
        valid_anomalies = [
            {
                "anomaly_id": f"anomaly-val-{i}",
                "resource_id": _get_resource_id(r),
                "anomaly_type": "unusual_port",
                "description": "Port 9999 is open which is uncommon.",
                "severity": "high",
                "evidence": "Port 9999 TCP open to 10.0.0.0/8.",
            }
            for i, r in enumerate(resources[:4])
        ]
        response_json = json.dumps(valid_anomalies)

        with patch("agents.anomaly_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(response_json)
            mock_get_client.return_value = mock_client

            detector = AnomalyDetector()
            result = detector.detect(resources, findings)

        for anomaly in result:
            for key in REQUIRED_ANOMALY_KEYS:
                value = anomaly[key]
                assert isinstance(value, str), (
                    f"Key '{key}' must be a string, got {type(value)}"
                )
                assert value.strip() != "", (
                    f"Key '{key}' must be non-empty, got {value!r}"
                )

    @given(
        resources=st.lists(resource_strategy(), min_size=1, max_size=10),
        findings=st.lists(finding_strategy(), min_size=0, max_size=5),
    )
    @settings(max_examples=200, deadline=None)
    def test_severity_is_valid_enum(self, resources, findings):
        """Severity must be one of {"high", "medium", "low"}."""
        valid_anomalies = [
            {
                "anomaly_id": f"anomaly-sev-{i}",
                "resource_id": _get_resource_id(r),
                "anomaly_type": "cost_outlier",
                "description": "Resource cost is 3x above average.",
                "severity": "high",
                "evidence": "Monthly cost $600 vs average $200.",
            }
            for i, r in enumerate(resources[:5])
        ]
        response_json = json.dumps(valid_anomalies)

        with patch("agents.anomaly_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(response_json)
            mock_get_client.return_value = mock_client

            detector = AnomalyDetector()
            result = detector.detect(resources, findings)

        for anomaly in result:
            assert anomaly["severity"] in VALID_SEVERITIES, (
                f"severity must be in {VALID_SEVERITIES}, got {anomaly['severity']!r}"
            )

    @given(
        resources=st.lists(resource_strategy(), min_size=1, max_size=10),
        findings=st.lists(finding_strategy(), min_size=0, max_size=5),
    )
    @settings(max_examples=200, deadline=None)
    def test_output_length_at_most_max_anomalies(self, resources, findings):
        """Output list length ≤ MAX_ANOMALIES (20)."""
        # Generate more than 20 valid anomalies
        many_anomalies = [
            {
                "anomaly_id": f"anomaly-cap-{i}",
                "resource_id": _get_resource_id(resources[0]),
                "anomaly_type": "cost_outlier",
                "description": f"Cost anomaly number {i}.",
                "severity": "low",
                "evidence": f"Evidence {i}.",
            }
            for i in range(30)
        ]
        response_json = json.dumps(many_anomalies)

        with patch("agents.anomaly_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(response_json)
            mock_get_client.return_value = mock_client

            detector = AnomalyDetector()
            result = detector.detect(resources, findings)

        assert len(result) <= MAX_ANOMALIES, (
            f"Output must be ≤ {MAX_ANOMALIES}, got {len(result)}"
        )

    @given(
        resources=st.lists(resource_strategy(), min_size=1, max_size=8),
        findings=st.lists(finding_strategy(), min_size=0, max_size=5),
        garbage=st.text(min_size=1, max_size=200),
    )
    @settings(max_examples=200, deadline=None)
    def test_invalid_json_returns_empty_list(self, resources, findings, garbage):
        """When LLM returns non-JSON, output is [] and never raises."""
        try:
            json.loads(garbage)
            assume(False)  # skip if garbage happens to be valid JSON
        except (json.JSONDecodeError, ValueError):
            pass

        with patch("agents.anomaly_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(garbage)
            mock_get_client.return_value = mock_client

            detector = AnomalyDetector()
            result = detector.detect(resources, findings)

        assert result == [], f"Invalid JSON should produce [], got {result}"

    @given(
        resources=st.lists(resource_strategy(), min_size=1, max_size=8),
        findings=st.lists(finding_strategy(), min_size=0, max_size=5),
    )
    @settings(max_examples=200, deadline=None)
    def test_llm_exception_returns_empty_list(self, resources, findings):
        """When LLM raises any exception, output is [] and never raises."""
        with patch("agents.anomaly_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = RuntimeError("API down")
            mock_get_client.return_value = mock_client

            detector = AnomalyDetector()
            result = detector.detect(resources, findings)

        assert result == [], f"LLM exception should produce [], got {result}"

    @given(
        resources=st.lists(resource_strategy(), min_size=1, max_size=8),
        findings=st.lists(finding_strategy(), min_size=0, max_size=5),
    )
    @settings(max_examples=200, deadline=None)
    def test_get_client_exception_returns_empty_list(self, resources, findings):
        """When get_client() raises, output is [] and never raises."""
        with patch("agents.anomaly_detector.get_client") as mock_get_client:
            mock_get_client.side_effect = EnvironmentError("OPENROUTER_API_KEY is not set")

            detector = AnomalyDetector()
            result = detector.detect(resources, findings)

        assert result == [], f"get_client error should produce [], got {result}"

    def test_empty_resources_returns_empty_list(self):
        """Empty resources list returns [] without calling LLM."""
        with patch("agents.anomaly_detector.get_client") as mock_get_client:
            detector = AnomalyDetector()
            result = detector.detect([], [])

        mock_get_client.assert_not_called()
        assert result == []


class TestAnomalyDetectorNegativeCases:
    """Negative tests — what AnomalyDetector should NOT do.

    Validates that invalid anomalies from LLM are never passed through to output.
    """

    @given(
        resources=st.lists(resource_strategy(), min_size=1, max_size=5),
        findings=st.lists(finding_strategy(), min_size=0, max_size=3),
    )
    @settings(max_examples=200, deadline=None)
    def test_invalid_severity_not_in_output(self, resources, findings):
        """Anomalies with invalid severity values are never included in output."""
        bad_anomalies = [
            {
                "anomaly_id": "anomaly-bad-sev",
                "resource_id": _get_resource_id(resources[0]),
                "anomaly_type": "cost_outlier",
                "description": "This anomaly has invalid severity.",
                "severity": "critical",  # invalid
                "evidence": "Some evidence.",
            },
            {
                "anomaly_id": "anomaly-bad-sev-2",
                "resource_id": _get_resource_id(resources[0]),
                "anomaly_type": "cost_outlier",
                "description": "This anomaly has empty severity.",
                "severity": "",  # invalid
                "evidence": "Some evidence.",
            },
        ]
        response_json = json.dumps(bad_anomalies)

        with patch("agents.anomaly_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(response_json)
            mock_get_client.return_value = mock_client

            detector = AnomalyDetector()
            result = detector.detect(resources, findings)

        # No anomaly with invalid severity should appear
        for anomaly in result:
            assert anomaly["severity"] in VALID_SEVERITIES

        # Specifically, these bad anomalies should NOT be in output
        assert all(a.get("anomaly_id") != "anomaly-bad-sev" for a in result)
        assert all(a.get("anomaly_id") != "anomaly-bad-sev-2" for a in result)

    @given(
        resources=st.lists(resource_strategy(), min_size=1, max_size=5),
        findings=st.lists(finding_strategy(), min_size=0, max_size=3),
    )
    @settings(max_examples=200, deadline=None)
    def test_missing_keys_not_in_output(self, resources, findings):
        """Anomalies with missing required keys are never included in output."""
        incomplete_anomalies = [
            {
                "anomaly_id": "anomaly-incomplete",
                "resource_id": _get_resource_id(resources[0]),
                # missing anomaly_type, description, severity, evidence
            },
            {
                "anomaly_id": "anomaly-partial",
                "resource_id": _get_resource_id(resources[0]),
                "anomaly_type": "naming_anomaly",
                # missing description, severity, evidence
            },
        ]
        response_json = json.dumps(incomplete_anomalies)

        with patch("agents.anomaly_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(response_json)
            mock_get_client.return_value = mock_client

            detector = AnomalyDetector()
            result = detector.detect(resources, findings)

        assert result == [], (
            f"Anomalies with missing keys should be dropped, got {result}"
        )

    @given(
        resources=st.lists(resource_strategy(), min_size=1, max_size=5),
        findings=st.lists(finding_strategy(), min_size=0, max_size=3),
    )
    @settings(max_examples=200, deadline=None)
    def test_empty_string_values_not_in_output(self, resources, findings):
        """Anomalies with empty string values for required keys are dropped."""
        bad_anomalies = [
            {
                "anomaly_id": "",  # empty
                "resource_id": _get_resource_id(resources[0]),
                "anomaly_type": "cost_outlier",
                "description": "Description present.",
                "severity": "high",
                "evidence": "Evidence present.",
            },
            {
                "anomaly_id": "anomaly-empty-desc",
                "resource_id": _get_resource_id(resources[0]),
                "anomaly_type": "cost_outlier",
                "description": "",  # empty
                "severity": "low",
                "evidence": "Evidence present.",
            },
        ]
        response_json = json.dumps(bad_anomalies)

        with patch("agents.anomaly_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(response_json)
            mock_get_client.return_value = mock_client

            detector = AnomalyDetector()
            result = detector.detect(resources, findings)

        assert result == [], (
            f"Anomalies with empty string values should be dropped, got {result}"
        )

    @given(
        resources=st.lists(resource_strategy(), min_size=1, max_size=5),
        findings=st.lists(finding_strategy(), min_size=0, max_size=3),
    )
    @settings(max_examples=200, deadline=None)
    def test_non_list_llm_response_returns_empty(self, resources, findings):
        """When LLM returns a JSON dict instead of a list, output is []."""
        response_json = json.dumps({"not": "a list", "anomalies": []})

        with patch("agents.anomaly_detector.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = _make_mock_response(response_json)
            mock_get_client.return_value = mock_client

            detector = AnomalyDetector()
            result = detector.detect(resources, findings)

        assert result == [], (
            f"Non-list LLM response should produce [], got {result}"
        )
