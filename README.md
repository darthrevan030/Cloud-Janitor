# Cloud Janitor

AI-native AWS cloud auditor — finds waste and security gaps, generates Terraform remediations, and requires human approval before touching anything.

Cloud Custodian shows you what's wrong with a YAML rules engine. Cloud Janitor reasons about it with AI, explains it in plain English, and generates the fix — with a human in the loop before anything executes.

---

## Table of Contents

- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [Agents](#agents)
- [AI Features](#ai-features)
- [Environment Variables](#environment-variables)
- [Running Modes](#running-modes)
- [The Approval Gate](#the-approval-gate)
- [LocalStack & Terraform](#localstack--terraform)
- [MCP Server](#mcp-server)
- [Project Structure](#project-structure)
- [Demo Scenario: The Ghost Cluster](#demo-scenario-the-ghost-cluster)
- [Running Tests](#running-tests)
- [Adding a New Provider](#adding-a-new-provider)
- [Extending Fixtures](#extending-fixtures)

---

## Quick Start

**Prerequisites:** Docker Desktop, Python 3.11+

```bash
# 1. Clone the repo
git clone https://github.com/darthrevan030/Cloud-Janitor.git
cd Cloud-Janitor

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set up environment
cp .env.example .env
# Open .env and add your OPENROUTER_API_KEY
# Get a free key at https://openrouter.ai/keys

# 4. Run the demo
make demo
```

`make demo` starts a LocalStack container (emulates AWS at `localhost:4566`), waits for it to be ready, then launches the Streamlit dashboard at **<http://localhost:8501>**.

Click **Execute Audit** to run the full pipeline against fixture data. No AWS account required.

---

## How It Works

Cloud Janitor runs a multi-agent pipeline in strict sequence:

```
┌─────────────────┐     ┌──────────────┐     ┌────────────────────────┐
│  FinOps Auditor │ ──▶ │ SecOps Guard │ ──▶ │ Remediation Architect  │
│  (cost waste)   │     │ (security)   │     │ (generates Terraform)  │
└─────────────────┘     └──────────────┘     └────────────────────────┘
                                                          │
                              ┌───────────────────────────┤
                              │                           │
                              ▼                           ▼
                    ┌──────────────────┐       ┌─────────────────────┐
                    │  Approval Gate   │       │  AI Agents          │
                    │  APPROVE <id>    │       │  Explain / Suggest  │
                    └──────────────────┘       │  Detect / Drift     │
                           │      │            └─────────────────────┘
                           ▼      ▼
                       tflocal  Rollback
                        apply   (revert)
```

1. **FinOps Auditor** scans for idle/orphaned resources and writes `findings_store.json`.
2. **SecOps Guard** appends security findings to the same store.
3. **Remediation Architect** reads all findings, checks dependencies, and generates `output/remediation.tf` + per-resource rollback HCL in `rollbacks/`.
4. **AI agents** explain the findings, detect anomalies, suggest follow-up policies, and track drift.
5. **Approval Gate** requires exact typed approval (`APPROVE <resource-id>`) before any change executes. Three failed attempts lock the gate.
6. On approval, `tflocal apply` executes against LocalStack. Rollback is a two-step process: `ROLLBACK <id>` then `CONFIRM ROLLBACK <id>`.

---

## Agents

### FinOps Auditor

Detects idle and orphaned resources representing financial waste.

**Severity rules:**

| Resource | Condition | Severity |
|---|---|---|
| ElastiCache | idle ≥ 30 days | HIGH |
| EBS | unattached ≥ 30 days | MEDIUM |
| EC2 | idle ≥ 30 days | LOW |

**Output:** Writes `findings_store.json` fresh (overwrites any previous file).

---

### SecOps Guard

Detects security vulnerabilities in security groups and unencrypted storage.

**Severity rules:**

| Check | Condition | Severity |
|---|---|---|
| Security group | Ports 6379, 3306, 5432, 27017 open to `0.0.0.0/0` | CRITICAL |
| Security group | Port 22 open to `0.0.0.0/0` | HIGH |
| Encryption | Unencrypted ElastiCache or EBS | HIGH |

**Output:** Appends to `findings_store.json` (never overwrites FinOps findings).

---

### Remediation Architect

Generates Terraform HCL to fix findings, plus rollback HCL for every change.

**Workflow:**

1. Reads all findings from `findings_store.json`
2. Calls `check_dependencies()` for each resource
3. Resources with dependents → blocked, warning surfaced
4. Resources without dependents → generates remediation + rollback HCL side by side

**HCL generation rules:**

- **EBS waste**: snapshot first (`depends_on` enforced), then destroy
- **Security groups**: never delete — narrow CIDR from `0.0.0.0/0` to `data.aws_vpc.current.cidr_block`
- **ElastiCache waste**: snapshot then delete
- **Encryption findings**: documented for manual review (cannot enable in-place)
- All generated resources include standard tags: `ManagedBy`, `Environment`, `RemediatedAt`, `RollbackRef`

**Output files:**

- `output/remediation.tf` — combined HCL for all unblocked findings (overwritten each run)
- `rollbacks/<resource_id>.tf` — one file per resource

---

### Agent Sequencing

Agents must run in strict order. **FinOps must run first** (writes the store). **SecOps must run second** (appends). **Remediation runs last** (reads both). No agent may skip its predecessor.

```
FinOpsAuditor → SecOpsGuard → RemediationArchitect
```

---

### findings_store.json Schema

Shared state that passes data between agents.

```json
{
  "scan_id": "uuid-v4",
  "started_at": "ISO-8601 UTC",
  "completed_at": "ISO-8601 UTC or null",
  "findings": [
    {
      "id": "uuid-v4",
      "resource_id": "cache-prod-legacy-01",
      "resource_type": "elasticache | ebs | ec2 | security_group",
      "agent": "finops | secops",
      "category": "waste | security",
      "severity": "LOW | MEDIUM | HIGH | CRITICAL",
      "title": "Human-readable title",
      "description": "Detailed explanation",
      "cost_estimate_monthly": 45.60,
      "idle_days": 42,
      "metadata": {},
      "detected_at": "ISO-8601 UTC"
    }
  ],
  "summary": {
    "total": 4,
    "by_severity": { "LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 1 },
    "by_agent": { "finops": 2, "secops": 2 },
    "total_monthly_waste": 57.60
  }
}
```

---

## AI Features

All AI features route through OpenRouter via `core/llm_client.py`. Set `OPENROUTER_API_KEY` in your `.env` to enable them. Each agent fails gracefully to a safe default — the pipeline never crashes because of an LLM failure.

### Natural Language Query Interface

Type a free-form question in the dashboard and the `QueryInterpreter` agent maps it to structured scan parameters.

```
"find all unencrypted storage with public access"
→ { resource_types: [], check_types: ["encryption", "public_access"], min_idle_days: 0 }
```

**Model string:** `agents/query_interpreter.py` — `QueryInterpreter`

---

### Remediation Explainer

Generates plain-English explanation of each finding alongside the Terraform diff in the approval panel.

Returns three sections: why this is risky, what the Terraform does, what rollback restores.

**Model string:** `agents/explainer.py` — `RemediationExplainer`

---

### Policy Suggester

After a scan, analyses finding patterns and recommends additional checks the user may have missed. Filters out check types already covered.

Returns 0–5 suggestions, each with a query you can paste directly into the NL query interface.

**Model string:** `agents/policy_suggester.py` — `PolicySuggester`

---

### Anomaly Detector

Uses an LLM to flag resources that are suspicious even without a matching rule — naming inconsistencies, region mismatches, unusual port configurations, cost outliers.

Only runs on resources not already in `findings_store.json`.

**Model string:** `agents/anomaly_detector.py` — `AnomalyDetector`

---

### Drift Detector

Compares the current scan against previous scans (stored in `scan_history.json`) and generates a 2–3 sentence plain-English narrative of what changed, whether things improved or worsened, and any notable patterns.

Uses atomic writes with `filelock` for thread safety. Keeps a maximum of 30 snapshots.

**Model string:** `agents/drift_detector.py` — `DriftDetector`

---

### Resource Tagger

Infers environment, team, owner, and risk level from resource names and IDs when explicit tags are absent or incomplete.

```
"cache-prod-legacy-01" → { env: "production", team: "platform", risk_level: "high" }
```

Supports single inference and batch mode (chunks of 10, one LLM call per chunk).

**Model string:** `agents/tagger.py` — `ResourceTagger`

---

### Incident Policy Generator

Describe a past incident or breach in natural language — the agent generates 3–5 preventive scan policies that would have caught it earlier.

Policies are written to `policies/<policy_id>.json` and are idempotent (same incident text returns same policies without a second LLM call).

**Model string:** `agents/incident_policy_generator.py` — `IncidentPolicyGenerator`

---

### Multi-Account Orchestrator

Runs concurrent audits across multiple AWS accounts defined in `accounts.json`, using `ThreadPoolExecutor` with fault isolation per account. Findings are tagged with `account_id` before aggregation.

Results are sorted by priority (high → medium → low), then alphabetically within the same priority.

**Model string:** `agents/multi_account_orchestrator.py` — `MultiAccountOrchestrator`

---

### Scheduler

Cron-based background scans via APScheduler. Runs as a daemon thread and exits with the main process.

```bash
# In .env:
JANITOR_SCHEDULE=0 6 * * *   # run at 6am daily
```

Logs to `scheduler.log` (RotatingFileHandler, 10MB max, 3 backups). Skips overlapping triggers if a scan is already running.

**Model string:** `scheduler.py` — `JanitorScheduler`

---

## Environment Variables

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

| Variable | Default | Required | Description |
|---|---|---|---|
| `OPENROUTER_API_KEY` | — | Yes (for AI) | API key for OpenRouter. Get one free at <https://openrouter.ai/keys> |
| `JANITOR_BACKEND` | `fixture` | No | Cloud provider: `fixture`, `aws`, `gcp`, `azure` |
| `TF_CMD` | `tflocal` | No | Terraform binary: `tflocal` (LocalStack) or `terraform` (real AWS) |
| `JANITOR_LLM_MODEL` | `anthropic/claude-haiku-4-5` | No | LLM model string via OpenRouter |
| `JANITOR_SCHEDULE` | `disabled` | No | Cron expression for scheduled scans (e.g. `0 6 * * *`) |

### Free LLM Models

These models are free on OpenRouter (no credit card needed) and work as drop-in replacements for `JANITOR_LLM_MODEL`:

| Model | Model string | Best for |
|---|---|---|
| gpt-oss-120b ⭐ | `openai/gpt-oss-120b:free` | Complex reasoning — incident policy, anomaly detection |
| Gemma 4 31B | `google/gemma-4-31b-it:free` | Drift narratives, explainer |
| Gemma 4 26B A4B | `google/gemma-4-26b-a4b-it:free` | Fast, high-volume — tagging, query interpretation |

Free tier may queue under heavy load — the codebase retries automatically or returns safe defaults.

---

## Running Modes

### Fixture mode (default)

No AWS account required. All data comes from `fixtures/*.json`.

```bash
# .env
JANITOR_BACKEND=fixture
```

### AWS mode

Points at a real AWS account via boto3. Currently a stub — `NotImplementedError` is raised on all methods. Implementation is planned.

```bash
# .env
JANITOR_BACKEND=aws
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
```

### GCP / Azure

Interface stubs exist. Setting `JANITOR_BACKEND=gcp` or `JANITOR_BACKEND=azure` instantiates the provider class but raises `NotImplementedError` on all calls.

---

## The Approval Gate

Before any infrastructure change executes, the operator must type an exact approval string. The gate is intentionally unforgiving — it will not accept typos, extra spaces, or case variations.

### Command formats

| Action | Command | Notes |
|---|---|---|
| Approve a remediation | `APPROVE <resource_id>` | Exact match, case-sensitive |
| Request rollback | `ROLLBACK <resource_id>` | Advances to awaiting confirmation |
| Confirm rollback | `CONFIRM ROLLBACK <resource_id>` | Completes the rollback |

### Lockout behaviour

- 3 consecutive failures → gate locks permanently
- A locked gate rejects all further input, including valid commands
- `reset()` is required to unlock — only callable via code, not via the dashboard

### Rollback is two steps by design

`ROLLBACK <id>` alone does nothing. You must follow it with `CONFIRM ROLLBACK <id>`. Both failure counts share the same 3-attempt budget.

---

## LocalStack & Terraform

LocalStack emulates AWS services on `localhost:4566`. Cloud Janitor uses it to safely execute `tflocal apply` without touching a real AWS account.

### Setup

1. Install Docker Desktop: <https://www.docker.com/products/docker-desktop/>
2. Sign up for LocalStack (free Hobby plan): <https://app.localstack.cloud/sign-up>
3. Install the CLI tools:

```bash
pip install localstack awscli-local
```

### Start LocalStack

```bash
# Via Makefile (recommended — waits for ready signal)
make demo

# Manually
docker-compose up -d
```

### Verify

```bash
awslocal s3 ls   # returns empty list with no errors when ready
```

### Switching to real AWS

```bash
# .env
TF_CMD=terraform
JANITOR_BACKEND=aws
```

`TF_CMD=terraform` swaps `tflocal` for the standard Terraform binary, which targets real AWS instead of LocalStack.

---

## MCP Server

The MCP server (`mcp_server/aws_janitor_mcp.py`) exposes infrastructure data and AI tools via the [Model Context Protocol](https://modelcontextprotocol.io/). Built with FastMCP.

### Core tools

| Tool | Parameters | Returns |
|---|---|---|
| `get_cost_data` | `resource_type?`, `min_idle_days=7` | `{resources: [...], total_monthly_waste: float}` |
| `get_security_data` | `check_type?` | `{findings: [...], critical_count: int}` |
| `check_dependencies` | `resource_id` | `{has_dependencies: bool, dependents: [...]}` |
| `validate_hcl` | `hcl_content` | `{valid: bool, error: str\|null}` |

### AI tools

| Tool | Parameters | Returns |
|---|---|---|
| `interpret_query` | `user_query` | Structured scan parameters |
| `explain_remediation` | `resource_id`, `finding`, `remediation_hcl`, `rollback_hcl` | `{risk_explanation, what_terraform_does, what_rollback_restores}` |
| `suggest_policies` | `findings`, `already_checked` | List of 0–5 policy suggestions |
| `infer_resource_context` | `resource_id`, `resource_name`, `existing_tags?` | `{env, team, owner, risk_level, confidence}` |
| `detect_anomalies` | `resources`, `findings` | List of anomaly objects |
| `policy_from_incident` | `incident_description` | List of 3–5 policy objects |

### Running the server

```bash
python mcp_server/aws_janitor_mcp.py
```

Uses FastMCP's default stdio transport. Can be consumed by any MCP-compatible client (Kiro, Claude Desktop, Cursor, etc.).

### Provider backends

| Backend | `JANITOR_BACKEND` | Status |
|---|---|---|
| Fixture | `fixture` | Complete |
| AWS | `aws` | Stub (`NotImplementedError`) |
| GCP | `gcp` | Interface only |
| Azure | `azure` | Interface only |

---

## Project Structure

```
cloud-janitor/
├── bin/
│   └── tflocal                      # Repo-local wrapper (dry-run or delegates to real binary)
├── agents/
│   ├── finops_auditor.py            # Cost waste detection
│   ├── secops_guard.py              # Security findings
│   ├── remediation_architect.py     # Terraform HCL generation
│   ├── approval_gate.py             # Human approval state machine
│   ├── audit_logger.py              # Append-only compliance log
│   ├── reasoning_logger.py          # Structured agent reasoning traces
│   ├── schema_validator.py          # findings_store.json validation
│   ├── query_interpreter.py         # NL → structured scan params
│   ├── explainer.py                 # Plain-English remediation explanation
│   ├── policy_suggester.py          # Post-scan policy recommendations
│   ├── tagger.py                    # LLM-inferred resource context
│   ├── anomaly_detector.py          # LLM anomaly detection
│   ├── drift_detector.py            # Snapshot diff with narrative
│   ├── incident_policy_generator.py # Policies from incident descriptions
│   └── multi_account_orchestrator.py# Concurrent multi-account audits
├── mcp_server/
│   ├── aws_janitor_mcp.py           # FastMCP tool registrations
│   └── backends/
│       ├── __init__.py              # CloudProvider ABC
│       ├── fixture_provider.py      # Fixture backend (complete)
│       ├── aws_provider.py          # AWS backend (stub)
│       ├── gcp_provider.py          # GCP backend (stub)
│       └── azure_provider.py        # Azure backend (stub)
├── core/
│   └── llm_client.py               # Shared LLM client (OpenRouter)
├── fixtures/
│   ├── aws_cost_explorer.json       # Fake cost/idle resource data
│   └── aws_config_inspector.json    # Fake security findings + dependency map
├── hooks/
│   ├── pre-remediation.sh           # HCL validation gate (runtime)
│   └── post-remediation.sh          # Audit log append (runtime)
├── output/
│   ├── logs/                        # audit.log, scheduler.log, agent_reasoning.log
│   ├── policies/                    # Incident-generated policy JSON files
│   ├── rollbacks/                   # Per-resource rollback HCL
│   └── remediation.tf               # Auto-generated (overwritten each scan)
├── scripts/
│   ├── git-hooks/post-commit        # Git hook: auto-regen SPEC_COMPLIANCE.md
│   ├── generate_spec_compliance.py  # Dev tool: spec compliance report
│   └── setup-hooks.sh               # Install git hooks
├── tests/                           # pytest + hypothesis property tests
├── app.py                           # Streamlit dashboard
├── orchestrator.py                  # Agent pipeline + approval flow
├── scheduler.py                     # Cron-based background scans
├── .env.example                     # Environment variable template
├── docker-compose.yml               # LocalStack container definition
├── Makefile                         # make demo entry point
├── requirements.txt                 # Python dependencies
└── pyproject.toml                   # Project metadata
```

---

## Demo Scenario: The Ghost Cluster

The fixture data ships with a pre-built scenario that exercises every agent and demonstrates realistic cross-concern remediation.

| Resource | Type | Problem | Agent | Severity |
|---|---|---|---|---|
| `cache-prod-legacy-01` | ElastiCache | Idle 42 days, 0 connections, $45.60/mo | FinOps | HIGH |
| `vol-0abc123def456789a` | EBS | Unattached 35 days, $12.00/mo | FinOps | MEDIUM |
| `sg-prod-redis` | Security Group | Port 6379 open to `0.0.0.0/0` | SecOps | CRITICAL |
| `cache-prod-legacy` | ElastiCache | No encryption at rest | SecOps | HIGH |

**Why these resources:**

- `sg-prod-redis` depends on `cache-prod-legacy` in the dependency map — remediating the security group triggers a dependency warning, demonstrating the blocking logic
- Two resources have empty dependency arrays (`vol-0abc123def456789a`, `cache-prod-legacy`) and can be freely remediated
- One resource in the fixture (`vol-0def456abc789012b`, 5 idle days) is intentionally below the threshold — exercises the filtering logic

**Full pipeline walkthrough:**

1. FinOps flags the idle ElastiCache and unattached EBS
2. SecOps flags the open Redis port and missing encryption
3. Remediation Architect generates HCL for all four findings, blocks the security group due to its dependency
4. Dashboard shows the Terraform diff and explainer panels
5. Operator types `APPROVE cache-prod-legacy-01` → tflocal snapshots and deletes the cluster
6. Operator types `APPROVE vol-0abc123def456789a` → tflocal snapshots and deletes the volume

---

## Running Tests

```bash
# Full suite
pytest

# Verbose (shows each test name)
pytest -v

# Single file
pytest tests/test_orchestrator.py

# Single test by name
pytest tests/test_approval_gate.py -k "test_valid_approval"
```

649 tests. No AWS credentials required — all tests run against fixture data or mocks.

The suite uses [Hypothesis](https://hypothesis.readthedocs.io/) for property-based testing. Property tests verify invariants that must hold for *any* input, not just hand-picked examples. See `tests/README.md` for the full test inventory and philosophy.

---

## Adding a New Provider

1. Create `mcp_server/backends/<name>_provider.py`:

```python
from mcp_server.backends import CloudProvider

class MyProvider(CloudProvider):
    def get_cost_data(self, resource_type=None, min_idle_days=7) -> dict: ...
    def get_security_data(self, check_type=None) -> dict: ...
    def check_dependencies(self, resource_id: str) -> dict: ...
```

1. Register it in `mcp_server/aws_janitor_mcp.py`:

```python
from mcp_server.backends.my_provider import MyProvider
PROVIDER_REGISTRY["my_backend"] = MyProvider
```

1. Activate with `JANITOR_BACKEND=my_backend`.

---

## Extending Fixtures

### Add a resource to `aws_cost_explorer.json`

Required fields: `id`, `type` (`elasticache`/`ebs`/`ec2`), `name`, `idle_days`, `monthly_cost`, `status`, `availability_zone`, `created_at`, `description`. Add type-specific fields (see `fixtures/README.md` for field tables).

### Add a finding to `aws_config_inspector.json`

Required fields: `id`, `resource_id`, `resource_type`, `check_type` (`security_group`/`encryption`/`public_access`), `severity`, `current_state`, `required_state`, `title`, `description`. Add `port`/`cidr` for security_group findings, `encryption_at_rest` for encryption findings.

### Update the dependency map

Add the resource ID as a key in `dependencies`. Value is an array of IDs that depend on it — use `[]` if safe to remediate freely.
