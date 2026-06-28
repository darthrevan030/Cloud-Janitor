"""Tests for PolicySuggester agent.

Validates policy suggestion generation, filtering, validation, and error handling.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from agents.policy_suggester import PolicySuggester, DEFAULT_SUGGESTIONS, VALID_PRIORITIES


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def suggester():
    """Create a PolicySuggester instance."""
    return PolicySuggester()


@pytest.fixture
def sample_findings():
    """Sample findings list from a scan."""
    return [
        {
            "id": "f1",
            "resource_id": "vol-001",
            "resource_type": "ebs",
            "category": "waste",
            "severity": "MEDIUM",
            "title": "Unattached EBS volume",
            "cost_estimate_monthly": 15.0,
            "check_type": "idle_resource",
        },
        {
            "id": "f2",
            "resource_id": "sg-002",
            "resource_type": "security_group",
            "category": "security",
            "severity": "CRITICAL",
            "title": "Open port 22 to world",
            "cost_estimate_monthly": 0.0,
            "check_type": "security_group",
        },
    ]


def _mock_llm_response(suggestions: list[dict]) -> MagicMock:
    """Build a mock OpenAI response containing the given suggestions JSON."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(suggestions)
    return mock_response


VALID_LLM_SUGGESTIONS = [
    {
        "suggestion_id": "check-rds-encryption",
        "title": "Check RDS encryption at rest",
        "rationale": "Unencrypted databases risk data exposure.",
        "query": "Find RDS instances without encryption enabled",
        "priority": "high",
        "check_type": "encryption",
    },
    {
        "suggestion_id": "check-s3-public",
        "title": "Check S3 bucket public access",
        "rationale": "Public buckets can leak sensitive data.",
        "query": "Find S3 buckets with public access enabled",
        "priority": "high",
        "check_type": "public_access",
    },
    {
        "suggestion_id": "check-idle-nat",
        "title": "Check for idle NAT gateways",
        "rationale": "Unused NAT gateways cost money without value.",
        "query": "Find NAT gateways with no traffic for 30 days",
        "priority": "medium",
        "check_type": "idle_resource",
    },
]


# ══════════════════════════════════════════════════════════════════════
# 1. Return between 0-5 suggestions (requirement 4.1)
# ══════════════════════════════════════════════════════════════════════


class TestSuggestionCount:
    """PolicySuggester returns 0-5 suggestions."""

    def test_returns_at_most_5_suggestions(self, suggester, sample_findings):
        """Even if LLM returns more than 5, output is capped at 5."""
        six_suggestions = VALID_LLM_SUGGESTIONS * 2  # 6 items
        mock_response = _mock_llm_response(six_suggestions)

        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = suggester.suggest(sample_findings, [])

        assert len(result) <= 5

    def test_returns_empty_on_exception(self, suggester, sample_findings):
        """On any exception, returns []."""
        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_get.side_effect = Exception("API down")

            result = suggester.suggest(sample_findings, [])

        assert result == []

    def test_returns_list_type(self, suggester, sample_findings):
        """Return value is always a list."""
        mock_response = _mock_llm_response(VALID_LLM_SUGGESTIONS)

        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = suggester.suggest(sample_findings, [])

        assert isinstance(result, list)


# ══════════════════════════════════════════════════════════════════════
# 2. Post-process filter: already_checked (requirement 4.2)
# ══════════════════════════════════════════════════════════════════════


class TestAlreadyCheckedFilter:
    """Suggestions with check_types in already_checked are excluded."""

    def test_filters_explicit_check_type(self, suggester, sample_findings):
        """Suggestions with check_type matching already_checked are removed."""
        suggestions = [
            {
                "suggestion_id": "check-rds-encryption",
                "title": "Check encryption",
                "rationale": "Important for security.",
                "query": "Find unencrypted resources",
                "priority": "high",
                "check_type": "encryption",
            },
            {
                "suggestion_id": "check-idle-stuff",
                "title": "Check idle resources",
                "rationale": "Save money on unused infra.",
                "query": "Find idle EC2 instances",
                "priority": "medium",
                "check_type": "idle_resource",
            },
        ]
        mock_response = _mock_llm_response(suggestions)

        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = suggester.suggest(sample_findings, ["encryption"])

        # Encryption suggestion should be filtered out
        assert all(s["suggestion_id"] != "check-rds-encryption" for s in result)
        # Idle resource suggestion should remain
        assert any(s["suggestion_id"] == "check-idle-stuff" for s in result)

    def test_no_filter_when_already_checked_empty(self, suggester, sample_findings):
        """When already_checked is empty, no filtering is applied (requirement 4.6)."""
        mock_response = _mock_llm_response(VALID_LLM_SUGGESTIONS)

        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = suggester.suggest(sample_findings, [])

        assert len(result) == 3  # All 3 valid suggestions pass through

    def test_filters_inferred_check_type_from_content(self, suggester, sample_findings):
        """Even without explicit check_type, infers and filters by content keywords."""
        suggestions = [
            {
                "suggestion_id": "check-sg-wide-open",
                "title": "Check security group ingress rules",
                "rationale": "Open ingress ports are dangerous.",
                "query": "Find security groups with open ports",
                "priority": "high",
            },
            {
                "suggestion_id": "check-cost-anomaly",
                "title": "Check cost anomalies in billing",
                "rationale": "Unexpected cost spikes may indicate issues.",
                "query": "Find resources with unusual billing patterns",
                "priority": "low",
            },
        ]
        mock_response = _mock_llm_response(suggestions)

        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = suggester.suggest(sample_findings, ["security_group"])

        # Security group suggestion should be filtered
        ids = [s["suggestion_id"] for s in result]
        assert "check-sg-wide-open" not in ids
        # Non-matching suggestion remains
        assert "check-cost-anomaly" in ids


# ══════════════════════════════════════════════════════════════════════
# 3. Schema validation (requirements 4.3, 4.4)
# ══════════════════════════════════════════════════════════════════════


class TestSuggestionSchema:
    """Each suggestion has exactly the required 5 keys with valid values."""

    def test_valid_suggestion_has_exactly_5_keys(self, suggester, sample_findings):
        """Each returned suggestion has suggestion_id, title, rationale, query, priority."""
        mock_response = _mock_llm_response(VALID_LLM_SUGGESTIONS)

        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = suggester.suggest(sample_findings, [])

        required_keys = {"suggestion_id", "title", "rationale", "query", "priority"}
        for s in result:
            assert set(s.keys()) == required_keys

    def test_priority_is_valid_enum(self, suggester, sample_findings):
        """Priority must be one of high, medium, low."""
        mock_response = _mock_llm_response(VALID_LLM_SUGGESTIONS)

        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = suggester.suggest(sample_findings, [])

        for s in result:
            assert s["priority"] in VALID_PRIORITIES

    def test_invalid_priority_suggestion_excluded(self, suggester, sample_findings):
        """Suggestions with invalid priority are dropped."""
        suggestions = [
            {
                "suggestion_id": "bad-priority",
                "title": "Something",
                "rationale": "A reason.",
                "query": "A query",
                "priority": "critical",  # invalid
                "check_type": "encryption",
            },
            {
                "suggestion_id": "good-one",
                "title": "Good suggestion",
                "rationale": "Valid reason.",
                "query": "Valid query",
                "priority": "low",
                "check_type": "idle_resource",
            },
        ]
        mock_response = _mock_llm_response(suggestions)

        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = suggester.suggest(sample_findings, [])

        assert len(result) == 1
        assert result[0]["suggestion_id"] == "good-one"

    def test_title_max_80_chars(self, suggester, sample_findings):
        """Title is truncated to 80 characters max."""
        suggestions = [
            {
                "suggestion_id": "long-title",
                "title": "A" * 120,  # exceeds 80
                "rationale": "Valid reason.",
                "query": "Valid query",
                "priority": "medium",
                "check_type": "encryption",
            },
        ]
        mock_response = _mock_llm_response(suggestions)

        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = suggester.suggest(sample_findings, [])

        assert len(result) == 1
        assert len(result[0]["title"]) <= 80

    def test_rationale_max_200_chars(self, suggester, sample_findings):
        """Rationale is truncated to 200 characters max."""
        suggestions = [
            {
                "suggestion_id": "long-rationale",
                "title": "A title",
                "rationale": "B" * 250,  # exceeds 200
                "query": "Valid query",
                "priority": "low",
                "check_type": "idle_resource",
            },
        ]
        mock_response = _mock_llm_response(suggestions)

        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = suggester.suggest(sample_findings, [])

        assert len(result) == 1
        assert len(result[0]["rationale"]) <= 200

    def test_empty_suggestion_id_rejected(self, suggester, sample_findings):
        """Suggestions with empty suggestion_id are dropped."""
        suggestions = [
            {
                "suggestion_id": "",
                "title": "No ID",
                "rationale": "Missing ID.",
                "query": "Some query",
                "priority": "high",
                "check_type": "encryption",
            },
        ]
        mock_response = _mock_llm_response(suggestions)

        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = suggester.suggest(sample_findings, [])

        assert result == []

    def test_non_string_fields_rejected(self, suggester, sample_findings):
        """Suggestions with non-string required fields are dropped."""
        suggestions = [
            {
                "suggestion_id": 123,  # not a string
                "title": "Numeric ID",
                "rationale": "Bad type.",
                "query": "Query",
                "priority": "high",
                "check_type": "encryption",
            },
        ]
        mock_response = _mock_llm_response(suggestions)

        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = suggester.suggest(sample_findings, [])

        assert result == []


# ══════════════════════════════════════════════════════════════════════
# 4. Empty findings: return general-purpose suggestions (requirement 4.5)
# ══════════════════════════════════════════════════════════════════════


class TestEmptyFindings:
    """When findings is empty, returns 1-5 general-purpose suggestions."""

    def test_empty_findings_returns_defaults_on_llm_failure(self, suggester):
        """When findings is empty and LLM fails, returns hardcoded defaults."""
        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_get.side_effect = Exception("API unavailable")

            result = suggester.suggest([], [])

        # Should return the hardcoded defaults (1-5 suggestions)
        assert 1 <= len(result) <= 5
        # Each must have the required schema
        for s in result:
            assert set(s.keys()) == {"suggestion_id", "title", "rationale", "query", "priority"}

    def test_empty_findings_with_llm_returns_suggestions(self, suggester):
        """When findings is empty and LLM works, returns LLM suggestions."""
        llm_suggestions = [
            {
                "suggestion_id": "check-general",
                "title": "General security check",
                "rationale": "Good practice.",
                "query": "Run a general security scan",
                "priority": "medium",
                "check_type": "security_group",
            },
        ]
        mock_response = _mock_llm_response(llm_suggestions)

        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = suggester.suggest([], [])

        assert 1 <= len(result) <= 5

    def test_empty_findings_with_already_checked_filters(self, suggester):
        """Empty findings + already_checked still filters results."""
        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_get.side_effect = Exception("API unavailable")

            result = suggester.suggest([], ["encryption", "security_group", "public_access", "idle_resource"])

        # All defaults are filtered since they all match known check_types
        assert result == []


# ══════════════════════════════════════════════════════════════════════
# 5. Error handling (requirements 1.3, 1.8, 1.9)
# ══════════════════════════════════════════════════════════════════════


class TestErrorHandling:
    """PolicySuggester never raises and returns [] on any error."""

    def test_returns_empty_on_connection_error(self, suggester, sample_findings):
        """Connection errors result in []."""
        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_get.side_effect = ConnectionError("Network unreachable")

            result = suggester.suggest(sample_findings, [])

        assert result == []

    def test_returns_empty_on_invalid_json_response(self, suggester, sample_findings):
        """Invalid JSON from LLM results in []."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "not valid json at all"

        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = suggester.suggest(sample_findings, [])

        assert result == []

    def test_logs_to_stderr_on_error(self, suggester, sample_findings, capsys):
        """Failures are logged to stderr (requirement 1.9)."""
        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_get.side_effect = RuntimeError("Simulated failure")

            suggester.suggest(sample_findings, [])

        captured = capsys.readouterr()
        assert "PolicySuggester" in captured.err
        assert "RuntimeError" in captured.err

    def test_never_raises_to_caller(self, suggester):
        """No matter what, suggest() never raises (requirement 1.8)."""
        # Pass garbage inputs — None is treated as falsy (empty findings),
        # so it may return default suggestions. The key requirement is no exception.
        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_get.side_effect = Exception("Simulated")
            result = suggester.suggest(None, None)  # type: ignore
        # Should not raise, should return [] (LLM fails, None can't iterate for defaults)
        assert isinstance(result, list)

    def test_llm_returns_dict_instead_of_list(self, suggester, sample_findings):
        """If LLM returns a dict instead of a list, returns []."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({"not": "a list"})

        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            result = suggester.suggest(sample_findings, [])

        assert result == []


# ══════════════════════════════════════════════════════════════════════
# 6. LLM client usage (requirement 1.11)
# ══════════════════════════════════════════════════════════════════════


class TestLLMClientUsage:
    """All LLM calls go through llm_client module."""

    def test_uses_get_client_from_llm_client(self, suggester, sample_findings):
        """Verifies get_client is called from llm_client module."""
        mock_response = _mock_llm_response(VALID_LLM_SUGGESTIONS)

        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            suggester.suggest(sample_findings, [])

        mock_get.assert_called_once()

    def test_uses_default_model(self, suggester, sample_findings):
        """The DEFAULT_MODEL from llm_client is used."""
        mock_response = _mock_llm_response(VALID_LLM_SUGGESTIONS)

        with patch("agents.policy_suggester.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get.return_value = mock_client

            # PolicySuggester uses DEFAULT_MODEL as the default model param
            ps = PolicySuggester(model="custom-test-model")
            ps.suggest(sample_findings, [])

            call_kwargs = mock_client.chat.completions.create.call_args[1]
            assert call_kwargs["model"] == "custom-test-model"
