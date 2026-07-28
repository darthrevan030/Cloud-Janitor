# Tests

## Running

```bash
# Full suite
uv run pytest

# Skip slow property tests
uv run pytest --ignore=tests/test_*_properties.py

# Single file
uv run pytest tests/test_orchestrator.py

# By keyword
uv run pytest -k "approval"
```

## Test Files

### Core Pipeline

| File | Description |
|------|-------------|
| `test_orchestrator.py` | Agent sequencing, pre/post hooks, approval gate, rollback, audit trail |
| `test_orchestrator_ai_agents.py` | Orchestrator integration with AI agents (anomaly detection, drift, NL queries) |
| `test_orchestrator_remediation_role.py` | Remediation-role credential wiring into approve/rollback flow |
| `test_orchestrator_identity.py` | Identity resolution wiring — STS failure blocks, sandbox fallback, explicit approver bypass |
| `test_error_states.py` | Dependency blocking, terraform validate failure, approval lockout edge cases |
| `test_approval_gate.py` | Command parsing (exact-match format, rejection of malformed input, 3-attempt lockout) |
| `test_audit_logger.py` | Append-only audit log writer (entry schema, file creation, append semantics) |
| `test_audit_query.py` | Queryable audit trail — filter conjunction, export round-trip, SQL injection prevention |
| `test_audit_query_e2e.py` | End-to-end audit trail: StateStore write → query_audit read-back |
| `test_remediation_role.py` | `_assume_remediation_role()` unit tests — success/failure/unset paths |
| `test_gate_integration.py` | Approval gate persistence and lockout across orchestrator restarts |
| `test_rollback_flow.py` | Rollback state machine — pending state, confirm, missing file |
| `test_pre_hook.py` | Pre-remediation hook validation — timeout, non-zero exit, empty rollback |

### Agents

| File | Description |
|------|-------------|
| `test_anomaly_detector.py` | AnomalyDetector LLM-based anomaly classification |
| `test_drift_detector.py` | DriftDetector snapshot comparison and LLM narrative generation |
| `test_explainer.py` | RemediationExplainer plain-English explanations |
| `test_incident_policy_generator.py` | IncidentPolicyGenerator policy JSON generation |
| `test_llm_client.py` | Shared LLM client: API key handling, BYO-endpoint (`JANITOR_LLM_BASE_URL`/`JANITOR_LLM_API_KEY`), AI kill switch (`JANITOR_AI_ENABLED`), DEFAULT_MODEL |
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
| `test_aws_provider.py` | AWSProvider live backend (moto-mocked): cost data, security data, dependency checks |
| `test_fixture.py` | Validates fixture JSON schema and content (required fields, types, flaggable data) |
| `test_mcp_tools_phase_bc.py` | MCP tool endpoints (get_cost_data, get_security_data, check_dependencies, etc.) |
| `test_mcp_interpret_query.py` | MCP interpret_query tool integration with QueryInterpreter |
| `test_stub_providers.py` | GCP/Azure provider instantiation — SDK detection, NotImplementedError messages |
| `test_cost_explorer_cache_props.py` | Cost Explorer cache round-trip, TTL expiry, corrupted file resilience |

### IAM & Deployment

| File | Description |
|------|-------------|
| `test_iam_policies.py` | Static validity — JSON parse, Version field, no duplicate SIDs, valid ARN patterns |
| `test_iam_read_policy.py` | Property 4 & 5 — action-source completeness from aws_provider.py, no mutation verbs |
| `test_iam_remediation_policy.py` | Property 6 & 7 — resource-level scoping, template coverage |
| `test_iam_remediation_policy_hcl.py` | Property 8 — HCL-derived action coverage cross-check |
| `test_deployment_docs.py` | docs/deployment.md content-presence (required sections, literal strings) |

### Dev Tooling

| File | Description |
|------|-------------|
| `test_compliance_generator_properties.py` | SPEC_COMPLIANCE.md generator correctness across random inputs |

### Property Tests (Hypothesis)

| File | Validates |
|------|-----------|
| `test_anomaly_detector_properties.py` | Output schema invariants for any resource/finding input |
| `test_audit_query_properties.py` | Audit query filter conjunction soundness and completeness |
| `test_backward_compatibility_properties.py` | FixtureProvider equivalence to original inline implementation |
| `test_drift_detector_properties.py` | Snapshot storage and drift detection invariants |
| `test_explainer_properties.py` | Explainer output schema for any finding input |
| `test_fixture_provider_properties.py` | Cost sum accuracy, critical count, dependency boolean |
| `test_gate_lockout_props.py` | Approval gate lockout invariant after max attempts |
| `test_gate_persistence_props.py` | Gate state persistence and corruption handling |
| `test_incident_policy_generator_properties.py` | Policy JSON schema for any finding input |
| `test_malformed_line_resilience.py` | Reasoning log parser skips malformed lines |
| `test_multi_account_orchestrator_properties.py` | Concurrent execution invariants |
| `test_policy_suggester_properties.py` | Suggestion schema for any findings input |
| `test_pre_remediation_hook_props.py` | Pre-hook coverage and success path validation |
| `test_prop_stub_providers.py` | Stub provider NotImplementedError content for any method |
| `test_provider_selection_properties.py` | Backend registry completeness and invalid rejection |
| `test_query_interpreter_properties.py` | Output schema for any query string |
| `test_reasoning_logger_properties.py` | JSON validity and sequential append for any unicode |
| `test_reasoning_panel_properties.py` | Section header transitions on agent name changes |
| `test_remediation_role_properties.py` | Credential isolation, fail-closed, unset-role fallback |
| `test_rollback_failure_props.py` | Rollback failure error propagation for any exit code/stderr |
| `test_savings_exception_swallowing.py` | Savings tracker exceptions swallowed without blocking approval |
| `test_savings_tracker_properties.py` | Ledger accumulation invariants |
| `test_scheduler_properties.py` | JanitorScheduler status schema and idempotent start |
| `test_tagger_properties.py` | Tagger output schema for any resource input |

## Test Philosophy

**Hostile reviewer standard**: if you deliberately broke the thing a test claims to test, the test must fail. See `.kiro/steering/rules.md` for forbidden patterns.

**Property-based testing** (Hypothesis): generates hundreds of random inputs to verify invariants that must hold universally, not just for hand-picked examples.

## What Is Not Tested

- **`app.py` (Streamlit UI)** — Most Streamlit UI code requires a browser runtime and cannot be unit tested in headless pytest. However, the **Audit Trail Query** logic is extracted into a testable helper and covered by `test_app_audit_trail_ui.py`.
- **LocalStack-dependent paths** — `tflocal apply/rollback` skipped unless LocalStack is running. Tested manually or in CI.
- **GCP/Azure live API calls** — Provider tests use mocked SDK clients. Real-account smoke tests are documented in `tests/manual/`.

## Adding Tests for a New Agent

1. Import the agent class from its module
2. Mock external I/O (MCP tool calls, file reads) — never mock the agent itself
3. Test `scan()` returns findings when fixture data contains flaggable items
4. Validate output schema (required keys, correct types)
5. Test `findings_store.json` side effects (written/appended correctly)
6. Include a negative test (empty list when no flaggable data)
7. Run `uv run pytest` — if a previously-passing test now fails, fix the implementation, not the test
