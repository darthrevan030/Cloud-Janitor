# Tests

## Running

```bash
# Full suite
pytest

# Skip slow property tests
pytest --ignore=tests/test_*_properties.py

# Single file
pytest tests/test_orchestrator.py

# By keyword
pytest -k "approval"
```

## Test Files

### Core Pipeline

| File | Description |
|------|-------------|
| `test_orchestrator.py` | Agent sequencing, pre/post hooks, approval gate, rollback, audit trail |
| `test_orchestrator_ai_agents.py` | Orchestrator integration with AI agents (anomaly detection, drift, NL queries) |
| `test_error_states.py` | Dependency blocking, terraform validate failure, approval lockout edge cases |
| `test_approval_gate.py` | Command parsing (exact-match format, rejection of malformed input, 3-attempt lockout) |
| `test_audit_logger.py` | Append-only audit log writer (entry schema, file creation, append semantics) |

### Agents

| File | Description |
|------|-------------|
| `test_anomaly_detector.py` | AnomalyDetector LLM-based anomaly classification |
| `test_drift_detector.py` | DriftDetector snapshot comparison and LLM narrative generation |
| `test_explainer.py` | RemediationExplainer plain-English explanations |
| `test_incident_policy_generator.py` | IncidentPolicyGenerator policy JSON generation |
| `test_llm_client.py` | Shared LLM client (`core/llm_client.py`): API key handling, base_url, DEFAULT_MODEL |
| `test_multi_account_orchestrator.py` | MultiAccountOrchestrator concurrent multi-account auditing |
| `test_policy_suggester.py` | PolicySuggester LLM-based security policy recommendations |
| `test_query_interpreter.py` | QueryInterpreter NL-to-structured-params parsing |
| `test_reasoning_logger.py` | ReasoningLogger init, truncate, and JSONL event writing |
| `test_reasoning_panel_quick.py` | Reasoning panel parse/display logic |
| `test_remediation_architect.py` | HCL generation, required tags, plan produces remediation + rollback |
| `test_savings_tracker.py` | SavingsTracker ledger writes, cost aggregation, duplicate detection |
| `test_schema_validator.py` | Schema validation for findings_store.json entries |
| `test_secops_guard.py` | SecOpsGuard sensitive port detection, scan output, findings_store writing |
| `test_secops_integration.py` | SecOpsGuard + ReasoningLogger wiring (reasoning events emitted during scan) |
| `test_tagger_validate.py` | Tagger LLM-based environment classification |

### MCP Server

| File | Description |
|------|-------------|
| `test_fixture.py` | Validates fixture JSON schema and content (required fields, types, flaggable data) |
| `test_mcp_tools_phase_bc.py` | MCP tool endpoints (get_cost_data, get_security_data, check_dependencies, etc.) |
| `test_mcp_interpret_query.py` | MCP interpret_query tool integration with QueryInterpreter |

### Dev Tooling

| File | Description |
|------|-------------|
| `test_compliance_generator_properties.py` | SPEC_COMPLIANCE.md generator correctness across random inputs |

### Property Tests (Hypothesis)

| File | Validates |
|------|-----------|
| `test_anomaly_detector_properties.py` | Output schema invariants for any resource/finding input |
| `test_backward_compatibility_properties.py` | FixtureProvider equivalence to original inline implementation |
| `test_drift_detector_properties.py` | Snapshot storage and drift detection invariants |
| `test_explainer_properties.py` | Explainer output schema for any finding input |
| `test_fixture_provider_properties.py` | Cost sum accuracy, critical count, dependency boolean |
| `test_incident_policy_generator_properties.py` | Policy JSON schema for any finding input |
| `test_malformed_line_resilience.py` | Reasoning log parser skips malformed lines |
| `test_multi_account_orchestrator_properties.py` | Concurrent execution invariants |
| `test_policy_suggester_properties.py` | Suggestion schema for any findings input |
| `test_provider_selection_properties.py` | Backend registry completeness and invalid rejection |
| `test_query_interpreter_properties.py` | Output schema for any query string |
| `test_reasoning_logger_properties.py` | JSON validity and sequential append for any unicode |
| `test_reasoning_panel_properties.py` | Section header transitions on agent name changes |
| `test_savings_tracker_properties.py` | Ledger accumulation invariants |
| `test_scheduler_properties.py` | JanitorScheduler status schema and idempotent start |
| `test_tagger_properties.py` | Tagger output schema for any resource input |

## Test Philosophy

**Hostile reviewer standard**: if you deliberately broke the thing a test claims to test, the test must fail. See `.kiro/steering/rules.md` for forbidden patterns.

**Property-based testing** (Hypothesis): generates hundreds of random inputs to verify invariants that must hold universally, not just for hand-picked examples.

## What Is Not Tested

- **`app.py`** — Streamlit UI requires a browser runtime. Cannot be unit tested in headless pytest.
- **LocalStack-dependent paths** — `tflocal apply/rollback` skipped unless LocalStack is running. Tested manually or in CI.

## Adding Tests for a New Agent

1. Import the agent class from its module
2. Mock external I/O (MCP tool calls, file reads) — never mock the agent itself
3. Test `scan()` returns findings when fixture data contains flaggable items
4. Validate output schema (required keys, correct types)
5. Test `findings_store.json` side effects (written/appended correctly)
6. Include a negative test (empty list when no flaggable data)
7. Run pytest — if a previously-passing test now fails, fix the implementation, not the test
