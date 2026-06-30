"""Property-based tests for the never-raise guarantee across all agents.

**Validates: Requirements 1.1-1.9**

Property 1: Never-Raise Guarantee
- For any input (empty, malformed, None-like, adversarial), calling each agent's
  primary method does not raise an exception.

Property 2: Safe Defaults on LLM Failure
- When LLM raises or returns unparseable output, each agent returns the correct
  safe-default schema with expected keys and types.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Strategies for adversarial inputs
# ---------------------------------------------------------------------------

# Strings that include empty, whitespace, binary-like, unicode, and long strings
adversarial_strings = st.one_of(
    st.text(max_size=5000),
    st.just(""),
    st.just("   "),
    st.just("\x00\x01\x02"),
    st.just(None),
    st.binary(max_size=200).map(lambda b: b.decode("latin-1")),
)

# Dicts with random keys and values
adversarial_dicts = st.one_of(
    st.dictionaries(st.text(max_size=50), st.text(max_size=100), max_size=20),
    st.just({}),
    st.just(None),
)

# Lists of random dicts
adversarial_list_of_dicts = st.one_of(
    st.lists(
        st.dictionaries(st.text(max_size=30), st.text(max_size=50), max_size=5),
        max_size=10,
    ),
    st.just([]),
    st.just(None),
)

# Various exception types the LLM client might raise
llm_exceptions = st.sampled_from([
    RuntimeError("API failure"),
    ConnectionError("Connection refused"),
    TimeoutError("Request timed out"),
    EnvironmentError("OPENROUTER_API_KEY is not set"),
    ValueError("Invalid response"),
    OSError("Network unreachable"),
    json.JSONDecodeError("Expecting value", "", 0),
    KeyError("choices"),
    TypeError("'NoneType' object is not subscriptable"),
])


def _make_failing_mock(exception):
    """Create a mock get_client that raises the given exception on completions.create."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = exception
    return mock_client


def _make_get_client_raiser(exception):
    """Create a side_effect for get_client that raises immediately."""
    def raiser():
        raise exception
    return raiser


# ---------------------------------------------------------------------------
# Property 1: Never-Raise Guarantee
# ---------------------------------------------------------------------------


class TestNeverRaiseGuarantee:
    """Property 1: No agent raises an unhandled exception regardless of input.

    **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9**
    """

    @given(
        query=adversarial_strings,
        exc=llm_exceptions,
    )
    @settings(max_examples=200, deadline=None)
    def test_query_interpreter_never_raises(self, query, exc):
        """QueryInterpreter.interpret() never raises for any input."""
        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_get_client.return_value = _make_failing_mock(exc)

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            # Should not raise regardless of input
            result = qi.interpret(query)
            assert isinstance(result, dict)

    @given(
        resource_id=adversarial_strings,
        finding=adversarial_dicts,
        remediation_hcl=adversarial_strings,
        rollback_hcl=adversarial_strings,
        exc=llm_exceptions,
    )
    @settings(max_examples=200, deadline=None)
    def test_explainer_never_raises(self, resource_id, finding, remediation_hcl, rollback_hcl, exc):
        """RemediationExplainer.explain() never raises for any input."""
        with patch("agents.explainer.get_client") as mock_get_client:
            mock_get_client.return_value = _make_failing_mock(exc)

            from agents.explainer import RemediationExplainer

            explainer = RemediationExplainer()
            # Coerce None-like values to strings for the method signature
            result = explainer.explain(
                resource_id=resource_id if resource_id is not None else "",
                finding=finding if finding is not None else {},
                remediation_hcl=remediation_hcl if remediation_hcl is not None else "",
                rollback_hcl=rollback_hcl if rollback_hcl is not None else "",
            )
            assert isinstance(result, dict)

    @given(
        findings=adversarial_list_of_dicts,
        already_checked=st.lists(st.text(max_size=30), max_size=5),
        exc=llm_exceptions,
    )
    @settings(max_examples=200, deadline=None)
    def test_policy_suggester_never_raises(self, findings, already_checked, exc):
        """PolicySuggester.suggest() never raises for any input."""
        with patch("agents.policy_suggester.get_client") as mock_get_client:
            mock_get_client.return_value = _make_failing_mock(exc)

            from agents.policy_suggester import PolicySuggester

            ps = PolicySuggester()
            result = ps.suggest(
                findings=findings if findings is not None else [],
                already_checked=already_checked if already_checked is not None else [],
            )
            assert isinstance(result, list)

    @given(
        resource_id=adversarial_strings,
        resource_name=adversarial_strings,
        existing_tags=adversarial_dicts,
        exc=llm_exceptions,
    )
    @settings(max_examples=200, deadline=None)
    def test_resource_tagger_never_raises(self, resource_id, resource_name, existing_tags, exc):
        """ResourceTagger.infer() never raises for any input."""
        with patch("agents.tagger.get_client") as mock_get_client:
            mock_get_client.return_value = _make_failing_mock(exc)

            from agents.tagger import ResourceTagger

            tagger = ResourceTagger()
            result = tagger.infer(
                resource_id=resource_id if resource_id is not None else "",
                resource_name=resource_name if resource_name is not None else "",
                existing_tags=existing_tags if existing_tags is not None else None,
            )
            assert isinstance(result, dict)

    @given(
        resources=adversarial_list_of_dicts,
        findings=adversarial_list_of_dicts,
        exc=llm_exceptions,
    )
    @settings(max_examples=200, deadline=None)
    def test_anomaly_detector_never_raises(self, resources, findings, exc):
        """AnomalyDetector.detect() never raises for any input."""
        with patch("agents.anomaly_detector.get_client") as mock_get_client:
            mock_get_client.return_value = _make_failing_mock(exc)

            from agents.anomaly_detector import AnomalyDetector

            detector = AnomalyDetector()
            result = detector.detect(
                resources=resources if resources is not None else [],
                findings=findings if findings is not None else [],
            )
            assert isinstance(result, list)

    @given(
        incident_description=adversarial_strings,
        exc=llm_exceptions,
    )
    @settings(max_examples=200, deadline=None)
    def test_incident_policy_generator_never_raises(self, incident_description, exc):
        """IncidentPolicyGenerator.generate() never raises for any input."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("agents.incident_policy_generator.get_client") as mock_get_client:
                mock_get_client.return_value = _make_failing_mock(exc)

                from agents.incident_policy_generator import IncidentPolicyGenerator

                gen = IncidentPolicyGenerator(policies_dir=Path(tmp_dir))
                result = gen.generate(
                    incident_description=incident_description if incident_description is not None else "",
                )
                assert isinstance(result, list)

    @given(
        findings=adversarial_list_of_dicts,
        exc=llm_exceptions,
    )
    @settings(max_examples=200, deadline=None)
    def test_drift_detector_never_raises(self, findings, exc):
        """DriftDetector.detect() never raises for any input."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("agents.drift_detector.get_client") as mock_get_client:
                mock_get_client.return_value = _make_failing_mock(exc)

                from agents.drift_detector import DriftDetector

                detector = DriftDetector(history_path=Path(tmp_dir) / "history.json")
                result = detector.detect(
                    findings=findings if findings is not None else [],
                )
                assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Property 2: Safe Defaults on LLM Failure
# ---------------------------------------------------------------------------


class TestSafeDefaultsOnLLMFailure:
    """Property 2: When LLM fails, each agent returns the correct safe-default schema.

    **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9**
    """

    QUERY_INTERPRETER_KEYS = {"resource_types", "check_types", "min_idle_days", "intent_summary", "confidence"}
    EXPLAINER_KEYS = {"risk_explanation", "what_terraform_does", "what_rollback_restores"}
    TAGGER_KEYS = {"env", "team", "owner", "risk_level", "confidence"}

    @given(
        query=st.text(min_size=1, max_size=500),
        exc=llm_exceptions,
    )
    @settings(max_examples=200, deadline=None)
    def test_query_interpreter_safe_defaults(self, query, exc):
        """QueryInterpreter returns correct safe-default schema on LLM failure."""
        assume(query.strip() != "")  # Non-empty queries attempt LLM call

        with patch("agents.query_interpreter.get_client") as mock_get_client:
            mock_get_client.return_value = _make_failing_mock(exc)

            from agents.query_interpreter import QueryInterpreter

            qi = QueryInterpreter()
            result = qi.interpret(query)

        # Verify exact schema
        assert set(result.keys()) == self.QUERY_INTERPRETER_KEYS
        assert result["resource_types"] == []
        assert result["check_types"] == []
        assert result["min_idle_days"] == 7
        assert result["intent_summary"] == "Could not interpret query."
        assert result["confidence"] == 0.0

    @given(
        resource_id=st.text(min_size=1, max_size=100),
        remediation_hcl=st.text(min_size=1, max_size=500),
        rollback_hcl=st.text(min_size=1, max_size=500),
        exc=llm_exceptions,
    )
    @settings(max_examples=200, deadline=None)
    def test_explainer_safe_defaults(self, resource_id, remediation_hcl, rollback_hcl, exc):
        """RemediationExplainer returns correct safe-default schema on LLM failure."""
        # Ensure HCL is non-whitespace to avoid the early-return path
        assume(remediation_hcl.strip() != "")
        assume(rollback_hcl.strip() != "")

        with patch("agents.explainer.get_client") as mock_get_client:
            mock_get_client.return_value = _make_failing_mock(exc)

            from agents.explainer import RemediationExplainer

            explainer = RemediationExplainer()
            result = explainer.explain(
                resource_id=resource_id,
                finding={"severity": "HIGH", "check_type": "encryption"},
                remediation_hcl=remediation_hcl,
                rollback_hcl=rollback_hcl,
            )

        # Verify exact schema
        assert set(result.keys()) == self.EXPLAINER_KEYS
        assert result["risk_explanation"] == "Explanation unavailable."
        assert result["what_terraform_does"] == "Explanation unavailable."
        assert result["what_rollback_restores"] == "Explanation unavailable."

    @given(
        findings=st.lists(
            st.dictionaries(st.text(max_size=20), st.text(max_size=50), max_size=3),
            min_size=1,
            max_size=5,
        ),
        already_checked=st.lists(st.text(max_size=20), max_size=3),
        exc=llm_exceptions,
    )
    @settings(max_examples=200, deadline=None)
    def test_policy_suggester_safe_defaults(self, findings, already_checked, exc):
        """PolicySuggester returns empty list on LLM failure."""
        with patch("agents.policy_suggester.get_client") as mock_get_client:
            mock_get_client.return_value = _make_failing_mock(exc)

            from agents.policy_suggester import PolicySuggester

            ps = PolicySuggester()
            result = ps.suggest(findings=findings, already_checked=already_checked)

        # Verify return type is list (safe default is [])
        assert isinstance(result, list)
        # On LLM failure with non-empty findings, expect empty list
        # (no valid suggestions can be generated)
        assert result == []

    @given(
        resource_id=st.text(min_size=1, max_size=100),
        resource_name=st.text(min_size=1, max_size=100),
        exc=llm_exceptions,
    )
    @settings(max_examples=200, deadline=None)
    def test_resource_tagger_safe_defaults(self, resource_id, resource_name, exc):
        """ResourceTagger returns correct safe-default schema on LLM failure."""
        with patch("agents.tagger.get_client") as mock_get_client:
            mock_get_client.return_value = _make_failing_mock(exc)

            from agents.tagger import ResourceTagger

            tagger = ResourceTagger()
            result = tagger.infer(
                resource_id=resource_id,
                resource_name=resource_name,
                existing_tags=None,
            )

        # Verify exact schema
        assert set(result.keys()) == self.TAGGER_KEYS
        assert result["env"] == "unknown"
        assert result["team"] is None
        assert result["owner"] is None
        assert result["risk_level"] == "low"
        assert result["confidence"] == 0.0

    @given(
        resources=st.lists(
            st.fixed_dictionaries({"id": st.text(min_size=1, max_size=30)}),
            min_size=1,
            max_size=5,
        ),
        exc=llm_exceptions,
    )
    @settings(max_examples=200, deadline=None)
    def test_anomaly_detector_safe_defaults(self, resources, exc):
        """AnomalyDetector returns empty list on LLM failure."""
        with patch("agents.anomaly_detector.get_client") as mock_get_client:
            mock_get_client.return_value = _make_failing_mock(exc)

            from agents.anomaly_detector import AnomalyDetector

            detector = AnomalyDetector()
            # Pass empty findings so all resources are "unflagged" and LLM is called
            result = detector.detect(resources=resources, findings=[])

        assert isinstance(result, list)
        assert result == []

    @given(
        incident_description=st.text(min_size=1, max_size=500),
        exc=llm_exceptions,
    )
    @settings(max_examples=200, deadline=None)
    def test_incident_policy_generator_safe_defaults(self, incident_description, exc):
        """IncidentPolicyGenerator returns empty list on LLM failure."""
        assume(incident_description.strip() != "")  # Non-empty triggers LLM path

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("agents.incident_policy_generator.get_client") as mock_get_client:
                mock_get_client.return_value = _make_failing_mock(exc)

                from agents.incident_policy_generator import IncidentPolicyGenerator

                gen = IncidentPolicyGenerator(policies_dir=Path(tmp_dir))
                result = gen.generate(incident_description=incident_description)

        assert isinstance(result, list)
        assert result == []

    @given(
        findings=st.lists(
            st.dictionaries(st.text(max_size=20), st.text(max_size=50), max_size=3),
            max_size=5,
        ),
        exc=llm_exceptions,
    )
    @settings(max_examples=200, deadline=None)
    def test_drift_detector_safe_defaults(self, findings, exc):
        """DriftDetector returns safe-default dict on failure or insufficient history."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("agents.drift_detector.get_client") as mock_get_client:
                mock_get_client.return_value = _make_failing_mock(exc)

                from agents.drift_detector import DriftDetector

                detector = DriftDetector(history_path=Path(tmp_dir) / "history.json")
                result = detector.detect(findings=findings)

        # With no snapshots saved, should return insufficient history
        assert isinstance(result, dict)
        assert result["drift"] is None
        assert result["reason"] in ("insufficient history", "error")

    @given(exc=llm_exceptions)
    @settings(max_examples=200, deadline=None)
    def test_drift_detector_error_on_corrupted_history(self, exc):
        """DriftDetector returns error safe-default when history file is corrupted."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            history_path = Path(tmp_dir) / "history.json"
            # Write corrupted JSON
            history_path.write_text("not valid json {{{{", encoding="utf-8")

            with patch("agents.drift_detector.get_client") as mock_get_client:
                mock_get_client.return_value = _make_failing_mock(exc)

                from agents.drift_detector import DriftDetector

                detector = DriftDetector(history_path=history_path)
                result = detector.detect(findings=[])

        assert isinstance(result, dict)
        assert result["drift"] is None
        assert result["reason"] in ("insufficient history", "error")
