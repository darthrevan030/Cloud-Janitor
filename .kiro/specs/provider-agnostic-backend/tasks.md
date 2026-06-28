# Implementation Plan: Provider-Agnostic Backend

## Overview

Refactor the MCP server to use a pluggable provider architecture. Extract existing fixture-reading logic into a `FixtureProvider` class behind a `CloudProvider` ABC, add stub providers for AWS/GCP/Azure, and wire provider selection through the `JANITOR_BACKEND` environment variable. All existing tool signatures and test behavior remain unchanged.

## Tasks

- [x] 1. Create the backends module with CloudProvider ABC
  - [x] 1.1 Create `mcp_server/backends/__init__.py` with CloudProvider abstract base class
    - Define `CloudProvider(ABC)` with abstract methods: `get_cost_data`, `get_security_data`, `check_dependencies`
    - Include full type hints and docstrings matching design document signatures
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 1.2 Implement FixtureProvider in `mcp_server/backends/fixture_provider.py`
    - Move existing fixture-reading logic from `aws_janitor_mcp.py` into the class verbatim
    - Constructor accepts optional `fixtures_dir` parameter defaulting to project `fixtures/` directory
    - Implement `get_cost_data` with resource_type and min_idle_days filtering
    - Implement `get_security_data` with check_type filtering and critical_count computation
    - Implement `check_dependencies` with dependencies map lookup and has_dependencies boolean
    - Handle missing fixture files gracefully with error dict
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10_

  - [x] 1.3 Write property tests for FixtureProvider
    - **Property 2: Cost data structural invariants** — verify total_monthly_waste == round(sum of costs, 2) and filtering correctness for any resource_type/min_idle_days
    - **Property 3: Security data critical count consistency** — verify critical_count matches CRITICAL findings count and check_type filtering
    - **Property 4: Dependency response boolean consistency** — verify has_dependencies == (len(dependents) > 0)
    - **Validates: Requirements 2.2, 2.3, 2.4, 2.6, 2.7, 2.8, 2.9**

- [x] 2. Implement stub providers
  - [x] 2.1 Implement AWSProvider in `mcp_server/backends/aws_provider.py`
    - Lazy import of boto3 in `__init__` with ImportError handling and helpful install message
    - All methods raise `NotImplementedError` with descriptive messages
    - Include IAM permission documentation in method docstrings
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 2.2 Implement GCPProvider and AzureProvider in `mcp_server/backends/gcp_provider.py` and `mcp_server/backends/azure_provider.py`
    - Minimal stub classes inheriting from CloudProvider
    - All methods raise `NotImplementedError` with descriptive messages
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [x] 2.3 Update `mcp_server/backends/__init__.py` to export all providers
    - Export CloudProvider, FixtureProvider, AWSProvider, GCPProvider, AzureProvider
    - _Requirements: 5.4_

- [x] 3. Wire provider selection into MCP server
  - [x] 3.1 Add PROVIDER_REGISTRY and `_load_provider()` to `aws_janitor_mcp.py`
    - Define registry mapping backend names to provider classes
    - Implement `_load_provider()` reading `JANITOR_BACKEND` env var (default: `"fixture"`)
    - Raise `ValueError` for invalid backend names with helpful message listing valid options
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [x] 3.2 Refactor MCP tool functions to delegate to provider instance
    - Replace inline fixture-reading logic in `get_cost_data`, `get_security_data`, `check_dependencies` with delegation to `_provider`
    - Keep `validate_hcl` unchanged and directly in `aws_janitor_mcp.py`
    - Remove the now-unused `FIXTURES_DIR` constant (FixtureProvider handles its own path)
    - **IMPORTANT: Do NOT remove imports that `validate_hcl` still needs** (`tempfile`, `os`, `subprocess`). After refactoring, verify with: `python -c "from mcp_server.aws_janitor_mcp import validate_hcl; print('ok')"`
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 7.1, 7.2, 7.3_

  - [x] 3.3 Write property tests for provider selection
    - **Property 5: Provider registry completeness** — for any valid backend name, _load_provider() returns a CloudProvider instance
    - **Property 6: Invalid backend rejection** — for any string not in registry, _load_provider() raises ValueError with the invalid name and valid options
    - **Validates: Requirements 5.3, 5.4, 5.5**

- [x] 4. Checkpoint - Verify backward compatibility
  - Ensure all existing tests pass without modification when `JANITOR_BACKEND` is unset
  - Run `pytest` and verify no regressions
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Write backward compatibility property test
  - **Property 1: Fixture backend behavioral equivalence** — for any valid resource_type and min_idle_days, FixtureProvider output matches the original inline implementation output
  - Compare FixtureProvider results against a reference implementation using the same fixture data
  - **Validates: Requirements 8.1, 8.3**

- [x] 6. Update dependencies and documentation

  - [x] 6.1 Add new dependencies to `requirements.txt`
    - Add `boto3>=1.34.0` (optional, for AWS backend)
    - Add `anthropic>=0.25.0` (for Phase B/C LLM calls — add now so it's declared)
    - Add `filelock>=3.13.0` (for drift detector atomic writes)
    - Add `APScheduler>=3.10.0` (for scheduled scans)
    - _Requirements: 10.1_

  - [x] 6.2 Rewrite `README.md` at project root as a product README
    - Current file is two lines ("# Cloud Janitor" + setup hook command) — replace entirely
    - Structure:
      - One-line product description ("AI-native AWS cloud auditor — finds waste and security gaps, generates Terraform remediations, requires human approval before applying")
      - **Quick start** section: `make demo` command, what it does, what port Streamlit runs on
      - **How it works** section: diagram or prose describing the 3-agent pipeline (FinOps → SecOps → Remediation Architect → approval gate → apply/rollback)
      - **Environment variables** table: `JANITOR_BACKEND`, `TF_CMD`, `JANITOR_SCHEDULE` — with valid values, defaults, and description for each
      - **Running modes** section: fixture mode (default, no AWS needed), aws mode (requires boto3 + credentials), brief note on gcp/azure stubs
      - **Project structure** section: flat annotated file tree covering all top-level files and directories
      - **Demo scenario** section: describe the Ghost Cluster scenario (idle ElastiCache + exposed security group) that ships with the fixture data
      - **Running tests** section: `pytest` command
    - _Requirements: 9.1, 9.2_

  - [x] 6.3 Update `mcp_server/README.md` with provider architecture documentation
    - Existing file documents the tools and fixture schema well — keep that content, add:
      - **Provider backends** section: table of `JANITOR_BACKEND` values (`fixture`, `aws`, `gcp`, `azure`), implementation status (fixture=complete, aws=stub, gcp/azure=interface only), and required env vars per backend
      - **Adding a new provider** section: step-by-step (create `backends/<name>_provider.py`, inherit `CloudProvider`, implement 3 methods, add to `PROVIDER_REGISTRY` in `aws_janitor_mcp.py`)
      - **New Phase B/C tools** subsection listing the tools that will be added in the next spec (`interpret_query`, `explain_remediation`, `suggest_policies`, `infer_resource_context`, `detect_anomalies`, `policy_from_incident`, `aggregate_findings`) — with one-line description each and `[planned]` status badge so the README stays accurate
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

  - [x] 6.4 Create `agents/README.md`
    - Document every agent class in `agents/`:
      - **FinOpsAuditor** — what it scans, severity rules (ElastiCache idle >30d = HIGH, EBS unattached >30d = MEDIUM), output file (`findings_store.json`, writes fresh)
      - **SecOpsGuard** — what it scans, severity rules (port 6379/3306/5432/27017 open to 0.0.0.0/0 = CRITICAL, port 22 open = HIGH, unencrypted cache/EBS = HIGH), output file (appends to `findings_store.json`)
      - **RemediationArchitect** — inputs (`findings_store.json`), dependency check flow, output files (`output/remediation.tf`, `rollbacks/<resource_id>.tf`)
      - **ApprovalGate** — state machine description, 3-attempt lock behaviour, accepted command formats (`APPROVE <id>`, `ROLLBACK <id>`, `CONFIRM ROLLBACK <id>`)
      - **ReasoningLogger** — JSONL format, when it truncates, event schema (`{timestamp, agent, event_type, resource_id, message}`)
      - **AuditLogger** — append-only JSONL, entry schema
      - **Agent sequencing** section: FinOps must run first (writes store), SecOps appends, RemediationArchitect reads both — explain why order is enforced
      - **findings_store.json schema** section: full annotated JSON schema for the shared state file
    - _Requirements: 9.1_

  - [x] 6.5 Create `fixtures/README.md`
    - Document the purpose of fixture files (fake AWS data — no credentials required)
    - Document `aws_cost_explorer.json` schema: full annotated example with every field, type, and valid values; type-specific fields table (elasticache/ebs/ec2)
    - Document `aws_config_inspector.json` schema: findings array schema, dependencies map schema, check_type values, severity enum
    - **Demo scenario** section: describe the specific resources in the current fixtures (Ghost Cluster scenario — `cache-prod-legacy-01`, `sg-prod-redis`, `vol-0abc123def456789a`) and why they were chosen
    - **Extending fixtures** section: how to add more resources or findings, what fields are required vs optional, how `check_dependencies` uses the `dependencies` map
    - _Requirements: 9.1_

  - [x] 6.6 Create `tests/README.md`
    - List every test file with one-line description of what it covers
    - **Running tests** section: `pytest`, `pytest -v`, `pytest tests/test_<file>.py` for single file
    - **Test philosophy** section: note the use of `hypothesis` for property tests and where they are used (savings tracker, reasoning logger, orchestrator)
    - **What is not tested** section: `app.py` (Streamlit UI — requires browser context), LocalStack-dependent paths (skipped unless `TF_CMD=tflocal` and LocalStack running)
    - **Adding tests for a new agent** section: brief checklist (import the class, mock the MCP tool it calls, test scan() returns list of dicts, test findings_store.json is written/appended correctly)
    - _Requirements: 9.1_

  - [x] 6.7 Create `output/README.md` and `rollbacks/README.md`
    - `output/README.md`: explain that `remediation.tf` is auto-generated by RemediationArchitect, should not be manually edited, is overwritten on each scan, and is the file submitted to `tflocal apply` on approval
    - `rollbacks/README.md`: explain that files are named `<resource_id>.tf`, generated alongside remediation HCL, one file per resource, executed on `CONFIRM ROLLBACK <resource_id>` command; note that these files are gitignored in production use (contain infrastructure state)
    - _Requirements: 9.1_

- [x] 7. Final checkpoint - Ensure all tests pass
  - Run full test suite with `pytest`
  - Verify no import errors when boto3 is not installed and backend is fixture
  - Ensure all tests pass, ask the user if questions arise.

## Task Dependency Graph

```json
{
  "waves": [
    {
      "name": "Wave 1: Foundation",
      "tasks": ["1.1"],
      "description": "Create CloudProvider ABC"
    },
    {
      "name": "Wave 2: Provider Implementations",
      "tasks": ["1.2", "2.1", "2.2"],
      "description": "Implement all providers (FixtureProvider + stubs)",
      "dependsOn": ["1.1"]
    },
    {
      "name": "Wave 3: Module Wiring",
      "tasks": ["2.3", "3.1"],
      "description": "Export providers and implement registry/selection",
      "dependsOn": ["1.2", "2.1", "2.2"]
    },
    {
      "name": "Wave 4: MCP Refactor",
      "tasks": ["3.2"],
      "description": "Refactor MCP tools to delegate to provider",
      "dependsOn": ["2.3", "3.1"]
    },
    {
      "name": "Wave 5: Verification",
      "tasks": ["4"],
      "description": "Checkpoint - verify backward compatibility",
      "dependsOn": ["3.2"]
    },
    {
      "name": "Wave 6: Documentation & Finalization",
      "tasks": ["6.1", "6.2", "7"],
      "description": "Update dependencies, documentation, and final checkpoint",
      "dependsOn": ["4"]
    }
  ],
  "optionalTasks": ["1.3", "3.3", "5"]
}
```

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- The FixtureProvider implementation must be a verbatim extraction of existing logic (no behavior changes)
- `validate_hcl` stays exactly where it is — it is NOT part of the provider interface
- Property tests use `hypothesis` which is already in requirements.txt
- boto3 is optional — the server must work without it when using fixture backend
- **TypedDicts in design.md are illustrative only.** Do NOT use them for runtime validation or as the basis for fixture parsing. The actual fixture schemas are:
  - `aws_cost_explorer.json` resources: `resource_id`, `type`, `name`, `idle_days`, `monthly_cost`, `status` (varies by resource type)
  - `findings_store.json` findings: `id`, `resource_id`, `resource_type`, `agent`, `category`, `severity`, `title`, `description`, `cost_estimate_monthly`, `idle_days`, `metadata`, `detected_at`
  - Parse the **actual fixture fields**, not the TypedDict fields. The provider passes through whatever the JSON contains.
