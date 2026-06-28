# Tests

## Test Files

| File | Description |
|------|-------------|
| `test_approval_gate.py` | ApprovalGate and RollbackGate command parsing (exact-match format, rejection of malformed input) |
| `test_audit_logger.py` | Append-only audit log writer (entry schema, file creation, append behavior) |
| `test_backward_compatibility_properties.py` | Property test: FixtureProvider output matches the original inline implementation for any valid inputs |
| `test_compliance_generator_properties.py` | Property test: SPEC_COMPLIANCE.md generator correctness across random task/artifact inputs |
| `test_error_states.py` | Integration tests for error handling: dependency blocking, terraform validate failure, approval lockout |
| `test_fixture.py` | Validates `aws_cost_explorer.json` fixture schema and content (required fields, types, flaggable data) |
| `test_fixture_provider_properties.py` | Property test: FixtureProvider structural invariants (cost sum, critical count, dependency boolean) |
| `test_malformed_line_resilience.py` | Property test: reasoning log parser skips malformed lines without raising exceptions |
| `test_orchestrator.py` | Orchestrator agent sequencing, pre/post-remediation hooks, approval gate, rollback, audit trail |
| `test_provider_selection_properties.py` | Property test: `_load_provider()` resolves valid backends and rejects invalid ones with ValueError |
| `test_reasoning_logger.py` | ReasoningLogger init, truncate, and structured JSONL event writing |
| `test_reasoning_logger_properties.py` | Property test: ReasoningLogger JSON validity and sequential append across random unicode inputs |
| `test_reasoning_panel_properties.py` | Property test: section header transitions when agent name changes in reasoning log events |
| `test_reasoning_panel_quick.py` | Quick validation of reasoning panel parse/display logic (parse_reasoning_events, section headers) |
| `test_remediation_architect.py` | RemediationArchitect HCL generation, required tags, plan produces both remediation and rollback |
| `test_savings_tracker.py` | SavingsTracker unit tests (ledger writes, cost aggregation, findings loading) |
| `test_savings_tracker_properties.py` | Property test: SavingsTracker ledger invariants across random findings data |
| `test_schema_validator.py` | Schema validator for findings_store.json entries (field presence, types, enums) |
| `test_secops_guard.py` | SecOpsGuard agent: sensitive port detection, scan output, findings_store.json writing |
| `test_secops_integration.py` | Integration test: SecOpsGuard + ReasoningLogger wiring (correct reasoning events emitted during scan) |

## Running Tests

Run the full suite:

```bash
pytest
```

Verbose output (shows each test name):

```bash
pytest -v
```

Run a single test file:

```bash
pytest tests/test_orchestrator.py
```

Run a specific test by name:

```bash
pytest tests/test_approval_gate.py -k "test_valid_approval"
```

## Test Philosophy

This project uses **Hypothesis** for property-based testing alongside traditional unit and integration tests.

Property-based tests generate hundreds of random inputs to verify invariants that must hold universally, rather than checking a handful of hand-picked examples. They are used in:

- **Savings tracker** (`test_savings_tracker_properties.py`) — ledger accumulation invariants across random findings
- **Reasoning logger** (`test_reasoning_logger_properties.py`) — structured JSON validity for any unicode input
- **Orchestrator/provider selection** (`test_provider_selection_properties.py`) — registry completeness and invalid backend rejection
- **FixtureProvider** (`test_fixture_provider_properties.py`) — cost sum accuracy, critical count consistency, dependency boolean correctness
- **Backward compatibility** (`test_backward_compatibility_properties.py`) — FixtureProvider equivalence to original inline implementation
- **Compliance generator** (`test_compliance_generator_properties.py`) — report generation correctness across random task structures
- **Reasoning panel** (`test_reasoning_panel_properties.py`, `test_malformed_line_resilience.py`) — section header transitions and malformed line handling

Every test in this project must satisfy the **hostile reviewer standard**: if you deliberately broke the thing the test claims to test, the test must fail. See `.kiro/steering/rules.md` for forbidden patterns (tautological assertions, pass-by-default, mocked-away units, etc.).

## What Is Not Tested

- **`app.py`** — Streamlit UI requires a browser context and active Streamlit runtime. Cannot be unit tested in a headless pytest session.
- **LocalStack-dependent paths** — Terraform apply/rollback operations via `tflocal` are skipped unless `TF_CMD=tflocal` is set and a LocalStack instance is running. These paths are tested manually or in CI with LocalStack services.

## Adding Tests for a New Agent

Checklist when introducing a new agent module:

1. **Import the agent class** from its module (e.g., `from agents.my_agent import MyAgent`)
2. **Mock external I/O** — mock the MCP tool calls or file reads the agent depends on (never mock the agent itself)
3. **Test `scan()` returns a list of dicts** — verify at least one finding is present when fixture data contains flaggable items
4. **Validate output schema** — assert required keys (`id`, `resource_id`, `severity`, etc.) exist with correct types
5. **Test `findings_store.json` side effects** — verify the file is written/appended correctly after scan
6. **Include a negative test** — verify the agent returns an empty list (or does not write) when no flaggable data exists
7. **Run pytest** — if a previously-passing test now fails because your new test exposed a real bug, that is a success. Fix the implementation, not the test.
