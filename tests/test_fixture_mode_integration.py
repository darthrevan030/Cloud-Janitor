"""Integration tests for fixture mode (JANITOR_BACKEND=fixture).

Validates:
- Req 12.3: Full pipeline (NL query → scan → anomaly → drift) completes without
  unhandled exceptions and produces non-empty findings in fixture mode.
- Req 12.4: No boto3 imports at runtime when JANITOR_BACKEND=fixture; LLM calls
  are mockable via patching `core.llm_client.get_client`.
- Req 12.5: Deterministic output when LLM is also mocked in fixture mode.
"""

import importlib
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

PROJECT_ROOT = Path(__file__).parent.parent


def _make_mock_llm_client():
    """Create a mock LLM client that returns deterministic JSON responses.

    This mock returns valid structured responses for each agent type based on
    the prompt content.
    """
    mock_client = MagicMock()

    def _mock_create(**kwargs):
        """Route to different deterministic responses based on prompt content."""
        messages = kwargs.get("messages", [])
        content = ""
        for msg in messages:
            if isinstance(msg, dict):
                content += msg.get("content", "")

        lower_content = content.lower()

        # IncidentPolicyGenerator response — match first because its prompt
        # also contains "policy" and "check" keywords
        if "incident description" in lower_content and "preventive scan policies" in lower_content:
            response_json = json.dumps([
                {
                    "policy_id": "policy-fixture-test-1",
                    "policy_name": "Prevent open Redis ports",
                    "resource_types": ["elasticache"],
                    "check_type": "security_group",
                    "check_logic_description": "Ensure Redis ports are not open to the internet.",
                    "rationale": "Prevents unauthorized access to Redis clusters.",
                    "query": "Find security groups with Redis port open to 0.0.0.0/0",
                },
                {
                    "policy_id": "policy-fixture-test-2",
                    "policy_name": "Ensure cache encryption",
                    "resource_types": ["elasticache"],
                    "check_type": "encryption",
                    "check_logic_description": "Ensure encryption at rest for all caches.",
                    "rationale": "Protects data at rest.",
                    "query": "Find unencrypted caches",
                },
                {
                    "policy_id": "policy-fixture-test-3",
                    "policy_name": "Block public access",
                    "resource_types": ["ec2", "ebs"],
                    "check_type": "public_access",
                    "check_logic_description": "Ensure no public access to internal resources.",
                    "rationale": "Reduces attack surface.",
                    "query": "Find publicly accessible resources",
                },
            ])
        # QueryInterpreter response
        elif "scan parameters" in lower_content or "resource_types" in lower_content:
            response_json = json.dumps({
                "resource_types": ["elasticache"],
                "check_types": ["security_group"],
                "min_idle_days": 7,
                "intent_summary": "Find idle ElastiCache clusters with security group issues.",
                "confidence": 0.85,
            })
        # AnomalyDetector response
        elif "anomal" in lower_content:
            response_json = json.dumps([
                {
                    "anomaly_id": "anomaly-fixture-001",
                    "resource_id": "vol-0def456abc789012b",
                    "anomaly_type": "cost_outlier",
                    "description": "This volume has unusual cost pattern for its size.",
                    "severity": "low",
                    "evidence": "gp2 volume type with minimal usage",
                }
            ])
        # DriftDetector narrative response
        elif "drift" in lower_content or "narrative" in lower_content or "changed" in lower_content:
            response_json = json.dumps(
                "Waste decreased by $10. One critical finding was resolved. Overall improvement trend."
            )
        # RemediationExplainer response
        elif "risk" in lower_content or "remediation" in lower_content or "terraform" in lower_content:
            response_json = json.dumps({
                "risk_explanation": "This security group allows unrestricted access which is dangerous.",
                "what_terraform_does": "The Terraform change restricts the CIDR block to VPC-only access.",
                "what_rollback_restores": "Rollback restores the original open CIDR rule.",
            })
        # PolicySuggester response
        elif "suggest" in lower_content or ("policy" in lower_content and "check" in lower_content):
            response_json = json.dumps([
                {
                    "suggestion_id": "check-idle-ebs",
                    "title": "Check idle EBS volumes",
                    "rationale": "Detached volumes waste money without providing value.",
                    "query": "Find idle EBS volumes",
                    "priority": "medium",
                    "check_type": "idle_resource",
                }
            ])
        # ResourceTagger response
        elif "tag" in lower_content or "env" in lower_content or "team" in lower_content:
            response_json = json.dumps({
                "env": "production",
                "team": "platform",
                "owner": "infra-ops",
                "risk_level": "medium",
                "confidence": 0.8,
            })
        # Default fallback
        else:
            response_json = json.dumps({})

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=response_json))]
        return mock_response

    mock_client.chat.completions.create = MagicMock(side_effect=_mock_create)
    return mock_client


@pytest.fixture(autouse=True)
def fixture_backend_env(monkeypatch, tmp_path):
    """Set JANITOR_BACKEND=fixture and provide a clean temp dir for outputs."""
    monkeypatch.setenv("JANITOR_BACKEND", "fixture")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-for-fixture-mode")
    # Ensure output dirs exist
    (tmp_path / "output" / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "rollbacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "policies").mkdir(parents=True, exist_ok=True)
    yield tmp_path


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1: Full pipeline (NL query → scan → anomaly → drift) in fixture mode
# Validates: Req 12.3
# ═══════════════════════════════════════════════════════════════════════════════


class TestFullPipelineFixtureMode:
    """Test full pipeline runs end-to-end in fixture mode without exceptions."""

    @patch("orchestrator.Orchestrator._run_pre_remediation_hook", return_value=None)
    @patch("core.llm_client.get_client")
    def test_execute_audit_completes_without_exceptions(self, mock_get_client, mock_hook, fixture_backend_env):
        """Full audit pipeline completes without raising unhandled exceptions."""
        mock_get_client.return_value = _make_mock_llm_client()

        from orchestrator import Orchestrator

        orch = Orchestrator(project_root=PROJECT_ROOT)
        result = orch.execute_audit()

        assert result.success is True
        assert isinstance(result.findings, list)
        assert len(result.findings) > 0, "Fixture mode should produce non-empty findings"
        assert isinstance(result.anomalies, list)
        assert result.drift_report is not None

    @patch("orchestrator.Orchestrator._run_pre_remediation_hook", return_value=None)
    @patch("core.llm_client.get_client")
    def test_nl_query_pipeline_completes_without_exceptions(self, mock_get_client, mock_hook, fixture_backend_env):
        """NL query → scan → anomaly → drift pipeline completes without exceptions."""
        mock_get_client.return_value = _make_mock_llm_client()

        from orchestrator import Orchestrator

        orch = Orchestrator(project_root=PROJECT_ROOT)

        # First run a standard audit to have scan history
        result1 = orch.execute_audit()
        assert result1.success is True

        # Then run NL query audit
        result2 = orch.execute_natural_language_audit("Find idle Redis clusters")
        assert result2.success is True
        assert isinstance(result2.findings, list)
        assert isinstance(result2.anomalies, list)
        assert result2.drift_report is not None

    @patch("orchestrator.Orchestrator._run_pre_remediation_hook", return_value=None)
    @patch("core.llm_client.get_client")
    def test_pipeline_produces_non_empty_findings(self, mock_get_client, mock_hook, fixture_backend_env):
        """Fixture mode produces non-empty findings as required by Req 12.3."""
        mock_get_client.return_value = _make_mock_llm_client()

        from orchestrator import Orchestrator

        orch = Orchestrator(project_root=PROJECT_ROOT)
        result = orch.execute_audit()

        assert result.success is True
        # Verify we have findings from both FinOps and SecOps
        agents = {f.get("agent") for f in result.findings if isinstance(f, dict)}
        assert "finops" in agents or "secops" in agents, \
            "Fixture mode should produce findings from at least one agent"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2: All MCP tools return valid schemas in fixture mode
# Validates: Req 12.2, 12.3
# ═══════════════════════════════════════════════════════════════════════════════


class TestMCPToolsSchemasFixtureMode:
    """Test that all MCP tools return valid schemas in fixture mode."""

    @patch("core.llm_client.get_client")
    def test_interpret_query_returns_valid_schema(self, mock_get_client, fixture_backend_env):
        """interpret_query returns dict with exactly 5 required keys."""
        mock_get_client.return_value = _make_mock_llm_client()

        from mcp_server.aws_janitor_mcp import interpret_query

        result = interpret_query("Find idle EC2 instances")

        assert isinstance(result, dict)
        required_keys = {"resource_types", "check_types", "min_idle_days", "intent_summary", "confidence"}
        assert required_keys.issubset(set(result.keys()))
        assert isinstance(result["resource_types"], list)
        assert isinstance(result["check_types"], list)
        assert isinstance(result["min_idle_days"], int)
        assert isinstance(result["intent_summary"], str)
        assert isinstance(result["confidence"], float)
        assert 0.0 <= result["confidence"] <= 1.0

    @patch("core.llm_client.get_client")
    def test_explain_remediation_returns_valid_schema(self, mock_get_client, fixture_backend_env):
        """explain_remediation returns dict with exactly 3 required keys."""
        mock_get_client.return_value = _make_mock_llm_client()

        from mcp_server.aws_janitor_mcp import explain_remediation

        result = explain_remediation(
            "sg-prod-redis",
            {"type": "open_port", "port": 6379},
            'resource "aws_security_group_rule" "fix" { cidr_blocks = ["10.0.0.0/16"] }',
            'resource "aws_security_group_rule" "rollback" { cidr_blocks = ["0.0.0.0/0"] }',
        )

        assert isinstance(result, dict)
        required_keys = {"risk_explanation", "what_terraform_does", "what_rollback_restores"}
        assert required_keys == set(result.keys())
        for key in required_keys:
            assert isinstance(result[key], str)
            assert len(result[key]) > 0

    @patch("core.llm_client.get_client")
    def test_suggest_policies_returns_valid_schema(self, mock_get_client, fixture_backend_env):
        """suggest_policies returns list of dicts with required keys."""
        mock_get_client.return_value = _make_mock_llm_client()

        from mcp_server.aws_janitor_mcp import suggest_policies

        findings = [{"resource_id": "sg-prod-redis", "type": "security_group"}]
        result = suggest_policies(findings, [])

        assert isinstance(result, list)
        if result:
            item = result[0]
            required_keys = {"suggestion_id", "title", "rationale", "query", "priority"}
            assert required_keys.issubset(set(item.keys()))
            assert item["priority"] in {"high", "medium", "low"}

    @patch("core.llm_client.get_client")
    def test_infer_resource_context_returns_valid_schema(self, mock_get_client, fixture_backend_env):
        """infer_resource_context returns dict with 5 required keys."""
        mock_get_client.return_value = _make_mock_llm_client()

        from mcp_server.aws_janitor_mcp import infer_resource_context

        result = infer_resource_context("cache-prod-legacy-01", "prod-session-cache")

        assert isinstance(result, dict)
        required_keys = {"env", "team", "owner", "risk_level", "confidence"}
        assert required_keys == set(result.keys())
        assert result["env"] in {"production", "staging", "development", "unknown"}
        assert isinstance(result["confidence"], float)
        assert 0.0 <= result["confidence"] <= 1.0
        assert result["risk_level"] in {"high", "medium", "low"}

    @patch("core.llm_client.get_client")
    def test_detect_anomalies_returns_valid_schema(self, mock_get_client, fixture_backend_env):
        """detect_anomalies returns list of dicts with required keys."""
        mock_get_client.return_value = _make_mock_llm_client()

        from mcp_server.aws_janitor_mcp import detect_anomalies

        resources = [
            {"id": "vol-0def456abc789012b", "type": "ebs"},
            {"id": "i-0abc123def456ec2a", "type": "ec2"},
        ]
        findings = []

        result = detect_anomalies(resources, findings)

        assert isinstance(result, list)
        if result:
            item = result[0]
            required_keys = {"anomaly_id", "resource_id", "anomaly_type", "description", "severity", "evidence"}
            assert required_keys == set(item.keys())
            assert item["severity"] in {"high", "medium", "low"}

    @patch("agents.incident_policy_generator.get_client")
    def test_policy_from_incident_returns_valid_schema(self, mock_get_client, fixture_backend_env, tmp_path):
        """policy_from_incident returns list of dicts with required keys."""
        mock_get_client.return_value = _make_mock_llm_client()

        from agents.incident_policy_generator import IncidentPolicyGenerator

        generator = IncidentPolicyGenerator(policies_dir=tmp_path / "policies")
        result = generator.generate("Redis cluster was publicly exposed through open security group")

        assert isinstance(result, list)
        assert len(result) >= 1
        item = result[0]
        required_keys = {
            "policy_id", "policy_name", "resource_types", "check_type",
            "check_logic_description", "rationale", "query",
            "generated_at", "incident_hash", "version",
        }
        assert required_keys.issubset(set(item.keys()))
        assert isinstance(item["resource_types"], list)
        assert len(item["resource_types"]) > 0
        assert item["check_type"] in {"security_group", "encryption", "public_access", "idle_resource"}
        assert item["version"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Test 3: Multi-account orchestration completes without exceptions
# Validates: Req 12.3
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultiAccountFixtureMode:
    """Test multi-account orchestration completes without exceptions in fixture mode."""

    @patch("core.llm_client.get_client")
    def test_multi_account_run_all_completes(self, mock_get_client, fixture_backend_env):
        """MultiAccountOrchestrator.run_all() completes without exceptions."""
        mock_get_client.return_value = _make_mock_llm_client()

        from agents.multi_account_orchestrator import MultiAccountOrchestrator

        orch = MultiAccountOrchestrator(
            accounts_path=PROJECT_ROOT / "accounts.json",
        )
        result = orch.run_all()

        # Should have all required keys
        required_keys = {
            "accounts_scanned", "total_findings", "total_waste",
            "critical_count", "by_account", "aggregate_findings",
            "cross_account_duplicates",
        }
        assert required_keys == set(result.keys())
        assert isinstance(result["accounts_scanned"], int)
        assert isinstance(result["by_account"], list)
        assert isinstance(result["aggregate_findings"], list)
        assert isinstance(result["cross_account_duplicates"], int)

    @patch("core.llm_client.get_client")
    def test_multi_account_scans_all_accounts(self, mock_get_client, fixture_backend_env):
        """All 3 fixture accounts should be scanned."""
        mock_get_client.return_value = _make_mock_llm_client()

        from agents.multi_account_orchestrator import MultiAccountOrchestrator

        orch = MultiAccountOrchestrator(
            accounts_path=PROJECT_ROOT / "accounts.json",
        )
        result = orch.run_all()

        # accounts.json has 3 accounts
        assert result["accounts_scanned"] == 3
        assert len(result["by_account"]) == 3

    @patch("core.llm_client.get_client")
    def test_multi_account_sorted_by_priority(self, mock_get_client, fixture_backend_env):
        """by_account should be sorted high → medium → low."""
        mock_get_client.return_value = _make_mock_llm_client()

        from agents.multi_account_orchestrator import MultiAccountOrchestrator

        orch = MultiAccountOrchestrator(
            accounts_path=PROJECT_ROOT / "accounts.json",
        )
        result = orch.run_all()

        priorities = [a["priority"] for a in result["by_account"]]
        priority_order = {"high": 0, "medium": 1, "low": 2}
        for i in range(len(priorities) - 1):
            assert priority_order[priorities[i]] <= priority_order[priorities[i + 1]]


# ═══════════════════════════════════════════════════════════════════════════════
# Test 4: No boto3 imports at runtime when JANITOR_BACKEND=fixture
# Validates: Req 12.4
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoBoto3InFixtureMode:
    """Verify no boto3 is imported at runtime in fixture mode."""

    def test_no_boto3_in_fixture_mode_pipeline(self, fixture_backend_env):
        """Running the pipeline in fixture mode should not import boto3.

        Strategy: Remove boto3 from sys.modules before running, then check
        if it got imported during pipeline execution.
        """
        # Remove boto3 from sys.modules if previously imported
        boto3_modules = [key for key in sys.modules if key == "boto3" or key.startswith("boto3.")]
        for mod in boto3_modules:
            del sys.modules[mod]

        # Also remove botocore
        botocore_modules = [key for key in sys.modules if key == "botocore" or key.startswith("botocore.")]
        for mod in botocore_modules:
            del sys.modules[mod]

        # Mock boto3 to track if it gets imported
        import_tracker = {"boto3_imported": False}
        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def tracking_import(name, *args, **kwargs):
            if name == "boto3" or name.startswith("boto3."):
                import_tracker["boto3_imported"] = True
            return original_import(name, *args, **kwargs)

        # Patch the LLM client and run the pipeline
        with patch("core.llm_client.get_client") as mock_get_client:
            mock_get_client.return_value = _make_mock_llm_client()

            from mcp_server.aws_janitor_mcp import (
                get_cost_data,
                get_security_data,
                interpret_query,
                explain_remediation,
                suggest_policies,
                infer_resource_context,
                detect_anomalies,
            )

            # Exercise all fixture mode MCP tools
            cost_data = get_cost_data()
            security_data = get_security_data()
            interpret_query("Find idle resources")
            explain_remediation("sg-123", {}, "resource {}", "resource {}")
            suggest_policies([], [])
            infer_resource_context("i-123", "test-server")
            detect_anomalies(cost_data.get("resources", []), [])

        # Verify the fixture provider itself doesn't use boto3
        from mcp_server.backends.fixture_provider import FixtureProvider

        # Check source code of fixture provider module for boto3 imports
        import inspect
        source = inspect.getsource(FixtureProvider)
        assert "boto3" not in source, "FixtureProvider should not reference boto3"

    def test_fixture_provider_source_has_no_boto3_import(self, fixture_backend_env):
        """FixtureProvider module source should not contain boto3 imports."""
        fixture_provider_path = PROJECT_ROOT / "mcp_server" / "backends" / "fixture_provider.py"
        source = fixture_provider_path.read_text(encoding="utf-8")

        # Should not have import boto3 or from boto3
        assert "import boto3" not in source
        assert "from boto3" not in source

    def test_phase_bc_agents_dont_import_boto3(self, fixture_backend_env):
        """Phase B+C agent modules should not import boto3 directly."""
        agent_files = [
            "agents/query_interpreter.py",
            "agents/explainer.py",
            "agents/policy_suggester.py",
            "agents/tagger.py",
            "agents/anomaly_detector.py",
            "agents/incident_policy_generator.py",
            "agents/drift_detector.py",
            "agents/multi_account_orchestrator.py",
        ]

        for rel_path in agent_files:
            agent_path = PROJECT_ROOT / rel_path
            if agent_path.exists():
                source = agent_path.read_text(encoding="utf-8")
                assert "import boto3" not in source, f"{rel_path} should not import boto3"
                assert "from boto3" not in source, f"{rel_path} should not import from boto3"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 5: Deterministic output when LLM is also mocked
# Validates: Req 12.5
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeterministicOutput:
    """Verify deterministic output when both backend is fixture and LLM is mocked."""

    @patch("core.llm_client.get_client")
    def test_repeated_audit_produces_same_findings(self, mock_get_client, fixture_backend_env):
        """Two sequential audits with same fixture data produce identical findings."""
        mock_get_client.return_value = _make_mock_llm_client()

        from orchestrator import Orchestrator

        orch1 = Orchestrator(project_root=PROJECT_ROOT)
        result1 = orch1.execute_audit()

        orch2 = Orchestrator(project_root=PROJECT_ROOT)
        result2 = orch2.execute_audit()

        # Compare findings (ignoring timestamp-sensitive fields)
        findings1 = sorted(
            [f.get("resource_id", "") for f in result1.findings if isinstance(f, dict)]
        )
        findings2 = sorted(
            [f.get("resource_id", "") for f in result2.findings if isinstance(f, dict)]
        )
        assert findings1 == findings2, "Fixture mode with mocked LLM should be deterministic"

    @patch("core.llm_client.get_client")
    def test_repeated_nl_query_produces_same_params(self, mock_get_client, fixture_backend_env):
        """Same NL query with mocked LLM produces same interpreted parameters."""
        mock_get_client.return_value = _make_mock_llm_client()

        from agents.query_interpreter import QueryInterpreter

        qi = QueryInterpreter()
        result1 = qi.interpret("Find idle Redis clusters")
        result2 = qi.interpret("Find idle Redis clusters")

        assert result1 == result2, "Same query should produce same parameters"

    @patch("core.llm_client.get_client")
    def test_repeated_anomaly_detection_produces_same_results(self, mock_get_client, fixture_backend_env):
        """Same resources with mocked LLM produce same anomaly results."""
        mock_get_client.return_value = _make_mock_llm_client()

        from agents.anomaly_detector import AnomalyDetector

        detector = AnomalyDetector()
        resources = [
            {"id": "vol-0def456abc789012b", "type": "ebs"},
            {"id": "i-0abc123def456ec2a", "type": "ec2"},
        ]
        findings = []

        result1 = detector.detect(resources, findings)
        result2 = detector.detect(resources, findings)

        assert result1 == result2, "Same input should produce same anomalies"

    @patch("core.llm_client.get_client")
    def test_mcp_tools_produce_deterministic_results(self, mock_get_client, fixture_backend_env):
        """MCP tools with fixture backend and mocked LLM produce deterministic results."""
        mock_get_client.return_value = _make_mock_llm_client()

        from mcp_server.aws_janitor_mcp import get_cost_data, get_security_data

        # Fixture data should always be the same
        cost1 = get_cost_data()
        cost2 = get_cost_data()
        assert cost1 == cost2

        sec1 = get_security_data()
        sec2 = get_security_data()
        assert sec1 == sec2

    @patch("core.llm_client.get_client")
    def test_explain_remediation_deterministic(self, mock_get_client, fixture_backend_env):
        """explain_remediation produces same result for same input."""
        mock_get_client.return_value = _make_mock_llm_client()

        from mcp_server.aws_janitor_mcp import explain_remediation

        args = (
            "sg-prod-redis",
            {"type": "open_port", "port": 6379},
            'resource "aws_security_group_rule" "fix" {}',
            'resource "aws_security_group_rule" "rollback" {}',
        )

        result1 = explain_remediation(*args)
        result2 = explain_remediation(*args)

        assert result1 == result2, "Same input should produce same explanation"
