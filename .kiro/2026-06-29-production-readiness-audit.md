# Cloud Janitor — Production Readiness Audit

**Date:** 2026-06-29  
**Goal:** Assess readiness for `pip install cloud-janitor` as a production-grade, AI/agentic alternative to Cloud Custodian.

---

## Executive Summary

The core logic is **fully implemented and well-tested** (17 agents, 44 test files, MCP server with 10 tools). The project works end-to-end in fixture mode with zero cloud credentials. However, it is **not pip-installable as a distributable package** — missing build system, CLI entry point, and proper package configuration.

---

## What's Working Well

| Area | Status | Details |
|------|--------|---------|
| Agent implementations | ✅ Complete | All 17 agents fully implemented — no stubs, no placeholders |
| MCP server | ✅ Complete | 10 tools registered via FastMCP, fixture + live AWS backends |
| Provider abstraction | ✅ Complete | Clean ABC with working FixtureProvider + AWSProvider |
| Test coverage | ✅ Strong | 44 test files, mix of unit/integration/property-based (Hypothesis) |
| Orchestrator | ✅ Complete | Full pipeline with hook gates, approval state machine, audit trail |
| Streamlit UI | ✅ Complete | Dark-themed dashboard with agent pipeline, findings, diff, approval |
| Documentation | ✅ Excellent | Comprehensive README, per-directory READMEs |
| Graceful degradation | ✅ Complete | Every AI agent returns safe defaults on LLM failure |

---

## Agent Inventory (All Complete)

| Agent | File | Lines | Real Logic |
|-------|------|-------|------------|
| FinOps Auditor | `agents/finops_auditor.py` | ~170 | Full scan, severity classification, cost estimation, findings_store write |
| SecOps Guard | `agents/secops_guard.py` | ~230 | SG checks, encryption checks, appends to findings_store |
| Remediation Architect | `agents/remediation_architect.py` | ~380 | Dependency checks, HCL generation for 6 resource/category combos, rollback generation |
| Approval Gate | `agents/approval_gate.py` | ~280 | State machine, 3-attempt lockout, parse/validate, RollbackGate with 2-step flow |
| Query Interpreter | `agents/query_interpreter.py` | ~100 | LLM call, validation, safe defaults |
| Explainer | `agents/explainer.py` | ~100 | LLM call, 3-section output, safe defaults |
| Anomaly Detector | `agents/anomaly_detector.py` | ~150 | LLM call, filters already-flagged resources, validates schema |
| Drift Detector | `agents/drift_detector.py` | ~250 | Atomic writes with filelock, snapshot rotation (30 max), LLM narrative + fallback |
| Tagger | `agents/tagger.py` | ~200 | Single + batch (chunks of 10) inference, confidence threshold, existing-tags passthrough |
| Policy Suggester | `agents/policy_suggester.py` | ~230 | LLM suggestions, already_checked filtering, hardcoded defaults fallback |
| Incident Policy Generator | `agents/incident_policy_generator.py` | ~220 | Idempotent (incident_hash dedup), writes policy JSON files, validates policy_id format |
| Multi-Account Orchestrator | `agents/multi_account_orchestrator.py` | ~220 | ThreadPoolExecutor, per-account fault isolation, cross-account duplicate detection |
| Savings Tracker | `agents/savings_tracker.py` | ~100 | Ledger lifecycle, dedup by scan_id, cumulative savings |
| Reasoning Logger | `agents/reasoning_logger.py` | ~80 | Structured JSONL, truncate-on-run, validated event types |
| Audit Logger | `agents/audit_logger.py` | ~80 | Append-only JSON-lines, graceful error handling |
| Schema Validator | `agents/schema_validator.py` | ~200 | Full findings_store validation (top-level + per-finding + summary cross-check) |

---

## MCP Server

- **File:** `mcp_server/aws_janitor_mcp.py`
- **Framework:** FastMCP (stdio transport)
- **Tools registered:** 10

### Infrastructure Tools

| Tool | Parameters | Returns |
|------|-----------|---------|
| `get_cost_data` | `resource_type?`, `min_idle_days=7` | `{resources: [...], total_monthly_waste: float}` |
| `get_security_data` | `check_type?` | `{findings: [...], critical_count: int}` |
| `check_dependencies` | `resource_id` | `{has_dependencies: bool, dependents: [...]}` |
| `validate_hcl` | `hcl_content` | `{valid: bool, error: str|null}` |

### AI Tools

| Tool | Parameters | Returns |
|------|-----------|---------|
| `interpret_query` | `user_query` | Structured scan parameters |
| `explain_remediation` | `resource_id`, `finding`, `remediation_hcl`, `rollback_hcl` | 3-section explanation |
| `suggest_policies` | `findings`, `already_checked` | List of 0–5 policy suggestions |
| `infer_resource_context` | `resource_id`, `resource_name`, `existing_tags?` | env/team/owner/risk inference |
| `detect_anomalies` | `resources`, `findings` | List of anomaly objects |
| `policy_from_incident` | `incident_description` | List of 3–5 policy objects |

### Provider Backends

| Backend | `JANITOR_BACKEND` | Status |
|---------|-------------------|--------|
| Fixture | `fixture` | ✅ Complete |
| AWS | `aws` | ✅ Complete (boto3, queries real AWS) |
| GCP | `gcp` | Stub (`NotImplementedError`) |
| Azure | `azure` | Stub (`NotImplementedError`) |

---

## Core Infrastructure

- **`core/llm_client.py`** — Single point of LLM configuration. OpenAI SDK pointed at OpenRouter. Exposes `get_client()` and `DEFAULT_MODEL`. All agents import from here (never import openai directly).

---

## Test Coverage

**44 test files** across 5 categories:

### Core Pipeline (5 files)

- `test_orchestrator.py` — Agent sequencing, hooks, approval, rollback, audit trail
- `test_orchestrator_ai_agents.py` — Orchestrator + AI agent integration
- `test_error_states.py` — Dependency blocking, validate failures, lockout
- `test_approval_gate.py` — Command parsing, format rejection, 3-attempt lockout
- `test_audit_logger.py` — Append-only log writer

### Agents (12 files)

Full unit tests for every agent module.

### MCP Server (4 files)

- `test_aws_provider.py` — AWSProvider with moto mocks
- `test_fixture.py` — Fixture JSON schema validation
- `test_mcp_tools_phase_bc.py` — MCP tool endpoints
- `test_mcp_interpret_query.py` — Query interpretation tool

### Property Tests (16 files)

Hypothesis-based property tests verifying schema invariants for every agent.

### Dev Tooling (1 file)

- `test_compliance_generator_properties.py`

---

## Dependencies

### Production (pyproject.toml)

| Package | Version | Purpose |
|---------|---------|---------|
| `mcp` | >=1.28.1 | FastMCP framework |
| `openai` | >=2.44.0 | LLM client (routed through OpenRouter) |
| `python-dotenv` | >=1.0.0 | .env file loading |
| `streamlit` | >=1.45.0 | Dashboard UI |
| `boto3` | >=1.34.0 | AWS SDK (live provider) |
| `filelock` | >=3.13.0 | Thread-safe file operations |
| `apscheduler` | >=3.10.0 | Cron scheduling |
| `pyyaml` | >=6.0.3 | YAML parsing |
| `terraform-local` | >=0.26.0 | tflocal CLI |
| `anthropic` | >=0.25.0 | ⚠️ Listed but unused |
| `hypothesis` | >=6.155.7 | ⚠️ Should be dev-only |

### Dev-only (dependency-groups)

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | >=9.1.1 | Test runner |
| `moto[ec2,elasticache,cloudwatch]` | >=5.0.0 | AWS mocking for tests |

---

## Gaps Blocking Production Release

### Critical (Must Fix)

#### 1. No CLI Entry Point

After `pip install cloud-janitor`, users have nothing to run. Need:

```toml
[project.scripts]
cloud-janitor = "cloud_janitor.cli:main"
```

Target commands:

```
cloud-janitor scan          # full audit pipeline
cloud-janitor scan --finops # just financial waste
cloud-janitor scan --secops # just security
cloud-janitor approve <id>  # approve a remediation
cloud-janitor rollback <id> # trigger rollback
cloud-janitor dashboard     # launch Streamlit UI
cloud-janitor mcp           # start MCP server
```

#### 2. No Build System Declared

`pyproject.toml` has no `[build-system]` section. Without this, `pip install .` won't produce a valid wheel for PyPI.

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

#### 3. Package Structure Not Configured

The flat layout (`agents/`, `core/`, `mcp_server/`, `orchestrator.py` at root) needs explicit package discovery or restructuring under `src/cloud_janitor/`.

---

### High Priority

#### 4. Dependency Issues

- `anthropic` — declared but never imported anywhere (dead dep, remove it)
- `hypothesis` — in production deps (should be dev-only)
- `packaging` — used in `app.py` but missing from pyproject.toml

#### 5. No GitHub Actions CI

No automated pipeline. Need at minimum:

- Lint (ruff)
- Type checking (mypy)
- Test matrix (Python 3.12+)
- Package build verification
- Publish to PyPI on tag

#### 6. README Claims AWS Provider is a Stub

The README says:
> **AWS mode** — Points at a real AWS account via boto3. Currently a stub — NotImplementedError is raised on all methods.

This is now **false** — `AWSProvider` is fully implemented. README needs updating.

---

### Medium Priority

#### 7. No Proper Logging

Agents use `print()` or ad-hoc file writes. A production package should use Python's `logging` module with configurable levels.

#### 8. No Retry/Rate Limiting for LLM Calls

`core/llm_client.py` is a thin wrapper — no retries, no exponential backoff, no token tracking. One 429 from OpenRouter and the pipeline fails.

#### 9. No `__version__` Importable

Users can't do `import cloud_janitor; print(cloud_janitor.__version__)`.

#### 10. No `py.typed` Marker

Type checkers won't recognize type annotations from the installed package.

---

### Low Priority (Post-Launch)

#### 11. GCP/Azure Provider Stubs

Both raise `NotImplementedError`. Fine for AWS-first launch, but should emit a clear warning at import time rather than silently accepting the backend config.

#### 12. Streamlit as Production Dependency

`streamlit` is heavy (~100MB). Consider making it optional: `pip install cloud-janitor[dashboard]`.

---

## Recommended Implementation Order

1. **Add `[build-system]`** to pyproject.toml (5 min)
2. **Create CLI module** with click/argparse (1-2 hrs)
3. **Add `[project.scripts]` entry point** (5 min)
4. **Fix dependencies** — remove anthropic, move hypothesis to dev, add packaging (10 min)
5. **Update README** — correct AWS provider status, add pip install instructions (30 min)
6. **Add GitHub Actions CI** (1 hr)
7. **Add retry logic** to llm_client.py with tenacity (30 min)
8. **Add proper logging** across agents (1-2 hrs)

**Total estimated effort:** ~6-8 hours to production-ready pip package.

---

## Architecture Diagram

```
User
 │
 ├── CLI (cloud-janitor scan/approve/rollback)
 │       │
 │       ▼
 ├── app.py (Streamlit dashboard)
 │       │
 │       ▼
 └──► orchestrator.py
          │
          ├── agents/finops_auditor.py      → writes findings_store.json
          ├── agents/secops_guard.py        → appends to findings_store.json
          ├── agents/remediation_architect.py → generates HCL + rollbacks
          ├── agents/approval_gate.py       → human approval state machine
          │
          ├── AI Agents (all via core/llm_client.py → OpenRouter)
          │   ├── query_interpreter.py
          │   ├── explainer.py
          │   ├── anomaly_detector.py
          │   ├── drift_detector.py
          │   ├── tagger.py
          │   ├── policy_suggester.py
          │   └── incident_policy_generator.py
          │
          └── mcp_server/aws_janitor_mcp.py (FastMCP, stdio)
                  │
                  └── backends/
                      ├── fixture_provider.py  (fixtures/*.json)
                      ├── aws_provider.py      (live boto3)
                      ├── gcp_provider.py      (stub)
                      └── azure_provider.py    (stub)
```

---

## File Inventory

```
cloud-janitor/
├── agents/                          (17 agent modules)
├── core/llm_client.py               (shared LLM client)
├── mcp_server/
│   ├── aws_janitor_mcp.py           (FastMCP server, 10 tools)
│   └── backends/                    (4 provider implementations)
├── fixtures/                        (2 fixture JSON files)
├── hooks/                           (pre/post remediation shell gates)
├── output/                          (runtime artifacts)
├── scripts/                         (dev tooling)
├── tests/                           (44 test files)
├── app.py                           (Streamlit dashboard)
├── orchestrator.py                  (pipeline orchestration)
├── scheduler.py                     (cron background scans)
├── .env.example                     (config template)
├── docker-compose.yml               (LocalStack)
├── Makefile                         (make demo)
├── requirements.txt                 (flat deps)
└── pyproject.toml                   (project metadata)
```
