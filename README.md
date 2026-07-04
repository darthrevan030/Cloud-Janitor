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
- [Security Hardening](#security-hardening)
- [Running Modes](#running-modes)
- [CLI Commands](#cli-commands)
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

**Prerequisites:** Docker Desktop, Python 3.11+, [Terraform](https://developer.hashicorp.com/terraform/install) (the actual CLI binary — `tflocal` alone is not enough, see [LocalStack & Terraform](#localstack--terraform)), and a free [LocalStack account](<https://app.localstack> cloud/sign-up) with an auth token

```bash
# 1. Clone the repo
git clone https://github.com/darthrevan030/Cloud-Janitor.git
cd Cloud-Janitor

# 2. Install the package (using uv — recommended)
uv sync
# Or via pip:
# pip install cloud-janitor

# For the optional Streamlit dashboard:
# pip install cloud-janitor[dashboard]

# 3. Set up environment
cp .env.example .env
# Open .env and add your OPENROUTER_API_KEY (or JANITOR_LLM_API_KEY for BYO-endpoint)
# Get a free OpenRouter key at https://openrouter.ai/keys

# 4. (Optional) Set up multi-account config
cp accounts.example.json accounts.json
# Edit accounts.json with your real account IDs and role ARNs
# Note: accounts.json is gitignored — never committed

# 5. Run the demo
make demo           # Community (free, no token needed)
# OR
make demo-live      # Pro (requires LOCALSTACK_AUTH_TOKEN — full end-to-end, no fixtures)
```

`make demo` starts a LocalStack Community container (emulates EC2 + S3 at `localhost:4566`), waits for it to be ready, then launches the Streamlit dashboard at **<http://127.0.0.1:8501>** (bound to localhost only for security).

`make demo-live` uses the Pro image with full ElastiCache emulation and scans live seeded resources via boto3 — the full end-to-end experience with no fixture files involved.

Click **Run Audit** to run the full pipeline. No AWS account required.

---

## How It Works

Cloud Janitor runs a multi-agent pipeline in strict sequence:

```text
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
| --- | --- | --- |
| ElastiCache | idle ≥ 30 days | HIGH |
| EBS | unattached ≥ 30 days | MEDIUM |
| EC2 | idle ≥ 30 days | LOW |

**Output:** Writes `findings_store.json` fresh (overwrites any previous file).

---

### SecOps Guard

Detects security vulnerabilities in security groups and unencrypted storage.

**Severity rules:**

| Check | Condition | Severity |
| --- | --- | --- |
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

```text
FinOpsAuditor → SecOpsGuard → RemediationArchitect
```

---

### findings_store.json Schema

Shared state that passes data between agents.

```json
{
  "schema_version": "1.0.0",
  "scan_id": "uuid-v4",
  "started_at": "ISO-8601 UTC",
  "completed_at": "ISO-8601 UTC or null",
  "agents_completed": ["finops", "secops"],
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

The `agents_completed` field tracks which agents have successfully run. This distinguishes "agent ran and found nothing" (healthy account) from "agent never ran" (pipeline failure).

---

## AI Features

All AI features route through a configurable LLM endpoint via `src/cloud_janitor/core/llm_client.py`. By default this is OpenRouter, but enterprises can point to any OpenAI-compatible API (Bedrock, Azure OpenAI, vLLM) using `JANITOR_LLM_BASE_URL`. Each agent fails gracefully to a safe default — the pipeline never crashes because of an LLM failure.

### Privacy Controls

| Mode | Variable | Effect |
| --- | --- | --- |
| BYO-endpoint | `JANITOR_LLM_BASE_URL` | Route all calls to your private LLM — zero third-party egress |
| AI kill switch | `JANITOR_AI_ENABLED=false` | Disable all LLM calls entirely — no network calls made |
| Strict mode | `JANITOR_PRIVACY_MODE=strict` | Require no-training provider policies; block free-tier models |

### Natural Language Query Interface

Type a free-form question in the dashboard and the `QueryInterpreter` agent maps it to structured scan parameters.

```text
"find all unencrypted storage with public access"
→ { resource_types: [], check_types: ["encryption", "public_access"], min_idle_days: 0 }
```

**Model string:** `src/cloud_janitor/agents/query_interpreter.py` — `QueryInterpreter`

---

### Remediation Explainer

Generates plain-English explanation of each finding alongside the Terraform diff in the approval panel.

Returns three sections: why this is risky, what the Terraform does, what rollback restores.

**Model string:** `src/cloud_janitor/agents/explainer.py` — `RemediationExplainer`

---

### Policy Suggester

After a scan, analyses finding patterns and recommends additional checks the user may have missed. Filters out check types already covered.

Returns 0–5 suggestions, each with a query you can paste directly into the NL query interface.

**Model string:** `src/cloud_janitor/agents/policy_suggester.py` — `PolicySuggester`

---

### Anomaly Detector

Uses an LLM to flag resources that are suspicious even without a matching rule — naming inconsistencies, region mismatches, unusual port configurations, cost outliers.

Only runs on resources not already in `findings_store.json`.

**Model string:** `src/cloud_janitor/agents/anomaly_detector.py` — `AnomalyDetector`

---

### Drift Detector

Compares the current scan against previous scans (stored in `scan_history.json`) and generates a 2–3 sentence plain-English narrative of what changed, whether things improved or worsened, and any notable patterns.

Uses atomic writes with `filelock` for thread safety. Keeps a maximum of 30 snapshots.

**Model string:** `src/cloud_janitor/agents/drift_detector.py` — `DriftDetector`

---

### Resource Tagger

Infers environment, team, owner, and risk level from resource names and IDs when explicit tags are absent or incomplete.

```text
"cache-prod-legacy-01" → { env: "production", team: "platform", risk_level: "high" }
```

Supports single inference and batch mode (chunks of 10, one LLM call per chunk).

**Model string:** `src/cloud_janitor/agents/tagger.py` — `ResourceTagger`

---

### Incident Policy Generator

Describe a past incident or breach in natural language — the agent generates 3–5 preventive scan policies that would have caught it earlier.

Policies are written to `policies/<policy_id>.json` and are idempotent (same incident text returns same policies without a second LLM call).

**Model string:** `src/cloud_janitor/agents/incident_policy_generator.py` — `IncidentPolicyGenerator`

---

### Multi-Account Orchestrator

Runs concurrent audits across multiple AWS accounts defined in `accounts.json` (copy `accounts.example.json` as a starting template — `accounts.json` is gitignored to prevent leaking real account IDs), using `ThreadPoolExecutor` with fault isolation per account. Findings are tagged with `account_id` before aggregation.

Results are sorted by priority (high → medium → low), then alphabetically within the same priority.

**Model string:** `src/cloud_janitor/agents/multi_account_orchestrator.py` — `MultiAccountOrchestrator`

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

### LLM Configuration

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `OPENROUTER_API_KEY` | — | Yes (for AI) | API key for OpenRouter. Get one free at <https://openrouter.ai/keys> |
| `JANITOR_LLM_API_KEY` | — | No | Override API key (takes precedence over `OPENROUTER_API_KEY`) |
| `JANITOR_LLM_BASE_URL` | `https://openrouter.ai/api/v1` | No | BYO-endpoint: point to Bedrock, Azure OpenAI, vLLM, or any OpenAI-compatible API |
| `JANITOR_LLM_MODEL` | `anthropic/claude-haiku-4-5` | No | LLM model string |
| `JANITOR_AI_ENABLED` | `true` | No | Set to `false` to disable all LLM calls (AI kill switch — zero network egress) |
| `JANITOR_PRIVACY_MODE` | — | No | Set to `strict` to require no-training provider policies and block free-tier models |

### Infrastructure & Runtime

| Variable | Default | Required | Description |
| --- | --- | --- | --- |
| `JANITOR_BACKEND` | `fixture` | No | Cloud provider: `fixture`, `aws`, `gcp`, `azure` |
| `TF_CMD` | `tflocal` | No | Terraform binary: `tflocal` (LocalStack) or `terraform` (real AWS) |
| `JANITOR_SCHEDULE` | `disabled` | No | Cron expression for scheduled scans (e.g. `0 6 * * *`) |
| `JANITOR_HOME` | cwd | No | Base directory for all output files |
| `LOCALSTACK_AUTH_TOKEN` | — | Yes (for demo) | LocalStack auth token for container usage |

### Enterprise Deployment (BYO-Endpoint)

For enterprise environments where no data should leave the network, configure a private LLM endpoint:

```bash
# Route all AI calls through your own Bedrock proxy
JANITOR_LLM_BASE_URL=https://your-bedrock-proxy.internal/v1
JANITOR_LLM_API_KEY=your-internal-key
JANITOR_LLM_MODEL=anthropic.claude-3-haiku-20240307-v1:0
```

Or disable AI entirely for a guaranteed zero-egress audit:

```bash
JANITOR_AI_ENABLED=false
```

### Free LLM Models

These models are free on OpenRouter (no credit card needed) and work as drop-in replacements for `JANITOR_LLM_MODEL`:

| Model | Model string | Best for |
| --- | --- | --- |
| gpt-oss-120b ⭐ | `openai/gpt-oss-120b:free` | Complex reasoning — incident policy, anomaly detection |
| Gemma 4 31B | `google/gemma-4-31b-it:free` | Drift narratives, explainer |
| Gemma 4 26B A4B | `google/gemma-4-26b-a4b-it:free` | Fast, high-volume — tagging, query interpretation |

Free tier may queue under heavy load — the codebase retries automatically or returns safe defaults.

---

## Security Hardening

Cloud Janitor implements defense-in-depth for sensitive operations:

### Subprocess Environment Isolation

All child processes (terraform, hooks) receive a **minimal environment** — never the full parent env. Specifically:

- **Terraform children**: receive only `PATH`, AWS credentials, and `AWS_ENDPOINT_URL`. Never receive `OPENROUTER_API_KEY` or `JANITOR_LLM_API_KEY`.
- **Hook children**: receive only `PATH` and system vars. No cloud credentials, no API keys.

This prevents secrets from leaking to Terraform providers (which are arbitrary downloaded code) or to hook scripts.

### Output Redaction

Terraform error output is scrubbed before logging or display. Patterns redacted: AWS account IDs, ARNs, access keys (`AKIA*`/`ASIA*`), API keys (`sk-or-*`), VPC IDs, and subnet IDs.

### Pre-Remediation Validation

The pre-remediation hook (`hooks/pre-remediation.sh`) **fails closed** — if the hook script is missing, remediation is blocked. This ensures HCL validation cannot be silently bypassed.

### Dashboard Binding

The Streamlit dashboard binds to `127.0.0.1` only. It is not exposed to the network by default. For remote access, use an authenticated reverse proxy or SSH tunnel.

### Docker Socket

The `docker-compose.yml` mounts the Docker socket for LocalStack Redis container mode. On shared hosts, this is a privilege escalation vector — scope or remove it in production deployments.

---

## Running Modes

### Fixture mode (default)

No AWS account required. All data comes from bundled `cloud_janitor/fixtures/*.json`.

```bash
# .env
JANITOR_BACKEND=fixture
```

### AWS mode

Queries live AWS infrastructure via boto3. The AWS provider is a complete implementation that connects to your real AWS account, retrieves cost and security data, and checks resource dependencies.

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

## CLI Commands

Cloud Janitor provides a CLI interface via the `cloud-janitor` command:

| Command | Syntax | Description |
| --- | --- | --- |
| Scan (full) | `cloud-janitor scan` | Execute the full audit pipeline (FinOps + SecOps) |
| Scan (FinOps only) | `cloud-janitor scan --finops` | Run only the FinOps cost-waste auditor |
| Scan (SecOps only) | `cloud-janitor scan --secops` | Run only the SecOps security guard |
| Approve | `cloud-janitor approve <resource-id>` | Approve a remediation plan for the specified resource |
| Rollback | `cloud-janitor rollback <resource-id>` | Roll back a previously applied remediation |
| Dashboard | `cloud-janitor dashboard` | Launch the Streamlit dashboard (requires `[dashboard]` extra) |
| MCP | `cloud-janitor mcp` | Start the MCP server on stdio transport |

---

## The Approval Gate

Before any infrastructure change executes, the operator must type an exact approval string. The gate is intentionally unforgiving — it will not accept typos, extra spaces, or case variations.

### Command formats

| Action | Command | Notes |
| --- | --- | --- |
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
2. Sign up for LocalStack (free Hobby plan): <https://app.localstack.cloud/sign-up> — as of March 2026, LocalStack requires an auth token for all usage, including the free tier. Set `LOCALSTACK_AUTH_TOKEN` in your `.env` after signing up.
3. Install the CLI tools:

```bash
pip install localstack awscli-local
```

1. **Install Terraform itself.** `tflocal` is a thin wrapper around a real `terraform` binary — it does not bundle one. Skipping this fails silently deep inside the approval-gate hook (`terraform init failed`) with no indication the binary is simply missing.

```bash
# macOS
brew tap hashicorp/tap && brew install hashicorp/tap/terraform
# Windows (Chocolatey)
choco install terraform -y
# Linux / manual: https://developer.hashicorp.com/terraform/install
```

Verify both are resolvable before continuing:

```bash
which tflocal terraform
```

> **Windows / Git Bash:** a few platform-specific gotchas to know —
>
> - `make` doesn't inherit your venv's PATH the same way interactive bash does; if `make demo` fails to find `cloud-janitor`, run `uv run cloud-janitor dashboard` directly instead.
> - PATH updates from `choco`/`winget`/`pip` installs don't propagate to already-open terminals — open a fresh one after installing anything.
> - `docker-compose` only auto-loads a file named exactly `.env`, not `.env.local`.
> - We've seen `docker-compose up` intermittently fail LocalStack's license activation on a fresh network while `localstack start` (the CLI) has been consistently reliable — worth trying that path first if `make demo` won't start LocalStack.

### Start LocalStack

```bash
# Community edition (free — no auth token needed)
make demo

# Pro edition (requires LOCALSTACK_AUTH_TOKEN in .env — enables ElastiCache)
make demo-pro

# Live mode (Pro required) — full end-to-end against LocalStack, no fixtures
# Scan uses boto3 against seeded LocalStack resources
make demo-live

# Manually (community)
docker-compose up -d

# Manually (pro)
docker-compose -f docker-compose.yml -f docker-compose.pro.yml up -d
```

### Custom Seed Scenarios

Cloud Janitor seeds LocalStack with demo resources automatically. You can also create custom scenarios to test specific resource configurations:

```bash
# Copy the template
cp scripts/seeds/example-custom.sh scripts/seeds/my-test.sh
# Edit with your resources (use awslocal commands)
# Run with custom seed
bash scripts/seed-localstack.sh scripts/seeds/my-test.sh
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

The MCP server (`src/cloud_janitor/mcp_server/aws_janitor_mcp.py`) exposes infrastructure data and AI tools via the [Model Context Protocol](https://modelcontextprotocol.io/). Built with FastMCP.

### Core tools

| Tool | Parameters | Returns |
| --- | --- | --- |
| `get_cost_data` | `resource_type?`, `min_idle_days=7` | `{resources: [...], total_monthly_waste: float}` |
| `get_security_data` | `check_type?` | `{findings: [...], critical_count: int}` |
| `check_dependencies` | `resource_id` | `{has_dependencies: bool, dependents: [...]}` |
| `validate_hcl` | `hcl_content` | `{valid: bool, error: str\|null}` |

### AI tools

| Tool | Parameters | Returns |
| --- | --- | --- |
| `interpret_query` | `user_query` | Structured scan parameters |
| `explain_remediation` | `resource_id`, `finding`, `remediation_hcl`, `rollback_hcl` | `{risk_explanation, what_terraform_does, what_rollback_restores}` |
| `suggest_policies` | `findings`, `already_checked` | List of 0–5 policy suggestions |
| `infer_resource_context` | `resource_id`, `resource_name`, `existing_tags?` | `{env, team, owner, risk_level, confidence}` |
| `detect_anomalies` | `resources`, `findings` | List of anomaly objects |
| `policy_from_incident` | `incident_description` | List of 3–5 policy objects |

### Running the server

```bash
cloud-janitor mcp
```

Uses FastMCP's default stdio transport. Can be consumed by any MCP-compatible client (Kiro, Claude Desktop, Cursor, etc.).

### Provider backends

| Backend | `JANITOR_BACKEND` | Status |
| --- | --- | --- |
| Fixture | `fixture` | Complete |
| AWS | `aws` | Complete |
| GCP | `gcp` | Interface only |
| Azure | `azure` | Interface only |

---

## Project Structure

```text
cloud-janitor/
├── src/
│   └── cloud_janitor/               # Installable package (src-layout)
│       ├── __init__.py              # __version__ via importlib.metadata
│       ├── py.typed                 # PEP 561 marker
│       ├── cli.py                   # Click CLI entry point
│       ├── app.py                   # Streamlit dashboard
│       ├── logging_config.py        # Logging setup re-export
│       ├── agents/                  # All agent classes
│       │   ├── finops_auditor.py    # Cost waste detection
│       │   ├── secops_guard.py      # Security findings
│       │   ├── remediation_architect.py # Terraform HCL generation
│       │   ├── approval_gate.py     # Human approval state machine
│       │   ├── audit_logger.py      # Append-only compliance log
│       │   ├── reasoning_logger.py  # Structured agent reasoning traces
│       │   ├── schema_validator.py  # findings_store.json validation
│       │   ├── query_interpreter.py # NL → structured scan params
│       │   ├── explainer.py         # Plain-English remediation explanation
│       │   ├── policy_suggester.py  # Post-scan policy recommendations
│       │   ├── tagger.py           # LLM-inferred resource context
│       │   ├── anomaly_detector.py  # LLM anomaly detection
│       │   ├── drift_detector.py    # Snapshot diff with narrative
│       │   ├── incident_policy_generator.py # Policies from incident descriptions
│       │   └── multi_account_orchestrator.py # Concurrent multi-account audits
│       ├── core/                    # Shared infrastructure
│       │   ├── llm_client.py        # LLM client with retry, BYO-endpoint, AI kill switch
│       │   ├── logging_config.py    # Logging configuration
│       │   ├── paths.py             # Centralized path constants
│       │   └── error_telemetry.py   # Structured error recording
│       ├── mcp_server/              # MCP protocol server
│       │   ├── aws_janitor_mcp.py   # FastMCP tool registrations
│       │   └── backends/
│       │       ├── __init__.py      # CloudProvider ABC
│       │       ├── fixture_provider.py # Fixture backend (complete)
│       │       ├── aws_provider.py  # AWS backend (complete)
│       │       ├── gcp_provider.py  # GCP backend (stub)
│       │       └── azure_provider.py # Azure backend (stub)
│       ├── fixtures/                # Bundled fixture data (included in wheel)
│       │   ├── __init__.py
│       │   ├── aws_cost_explorer.json # Fake cost/idle resource data
│       │   └── aws_config_inspector.json # Fake security findings + dependency map
│       └── orchestrator/            # Agent pipeline + approval flow
│           ├── __init__.py          # Re-exports Orchestrator, AuditResult, etc.
│           └── orchestrator.py      # Main orchestrator implementation
├── bin/
│   └── tflocal                      # Repo-local wrapper (dry-run or delegates to real binary)
├── hooks/
│   ├── pre-remediation.sh           # HCL validation gate (runtime)
│   └── post-remediation.sh          # Audit log append (runtime)
├── output/
│   ├── logs/                        # audit.log, scheduler.log, agent_reasoning.log
│   ├── policies/                    # Incident-generated policy JSON files
│   ├── rollbacks/                   # Per-resource rollback HCL
│   └── remediation.tf               # Auto-generated (overwritten each scan)
├── scripts/
│   ├── seed-localstack.sh           # Pre-seed LocalStack with demo resources
│   ├── seeds/                       # Modular seed scenarios
│   │   ├── ghost-cluster.sh         # Default demo: idle cache + orphaned EBS + open SG
│   │   └── example-custom.sh       # Template for custom scenarios
│   ├── git-hooks/post-commit        # Git hook: auto-regen SPEC_COMPLIANCE.md
│   ├── generate_spec_compliance.py  # Dev tool: spec compliance report
│   └── setup-hooks.sh               # Install git hooks
├── tests/                           # pytest + hypothesis property tests
├── scheduler.py                     # Cron-based background scans
├── accounts.example.json            # Multi-account config template (copy to accounts.json)
├── .env.example                     # Environment variable template
├── docker-compose.yml               # LocalStack Community config (free tier)
├── docker-compose.pro.yml           # LocalStack Pro override (ElastiCache, auth token)
├── Makefile                         # make demo / make demo-pro entry points
└── pyproject.toml                   # Project metadata
```

---

## Demo Scenario: The Ghost Cluster

The fixture data ships with a pre-built scenario that exercises every agent and demonstrates realistic cross-concern remediation.

| Resource | Type | Problem | Agent | Severity |
| --- | --- | --- | --- | --- |
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
uv run pytest

# Verbose (shows each test name)
uv run pytest -v

# Single file
uv run pytest tests/test_orchestrator.py

# Single test by name
uv run pytest tests/test_approval_gate.py -k "test_valid_approval"
```

649 tests. No AWS credentials required — all tests run against fixture data or mocks.

The suite uses [Hypothesis](https://hypothesis.readthedocs.io/) for property-based testing. Property tests verify invariants that must hold for *any* input, not just hand-picked examples. See `tests/README.md` for the full test inventory and philosophy.

---

## Adding a New Provider

1. Create `src/cloud_janitor/mcp_server/backends/<name>_provider.py`:

```python
from cloud_janitor.mcp_server.backends import CloudProvider

class MyProvider(CloudProvider):
    def get_cost_data(self, resource_type=None, min_idle_days=7) -> dict: ...
    def get_security_data(self, check_type=None) -> dict: ...
    def check_dependencies(self, resource_id: str) -> dict: ...
```

1. Register it in `src/cloud_janitor/mcp_server/aws_janitor_mcp.py`:

```python
from cloud_janitor.mcp_server.backends.my_provider import MyProvider
PROVIDER_REGISTRY["my_backend"] = MyProvider
```

1. Activate with `JANITOR_BACKEND=my_backend`.

---

## Extending Fixtures

### Add a resource to `aws_cost_explorer.json`

Required fields: `id`, `type` (`elasticache`/`ebs`/`ec2`), `name`, `idle_days`, `monthly_cost`, `status`, `availability_zone`, `created_at`, `description`. Add type-specific fields (see `src/cloud_janitor/fixtures/README.md` for field tables).

### Add a finding to `aws_config_inspector.json`

Required fields: `id`, `resource_id`, `resource_type`, `check_type` (`security_group`/`encryption`/`public_access`), `severity`, `current_state`, `required_state`, `title`, `description`. Add `port`/`cidr` for security_group findings, `encryption_at_rest` for encryption findings.

### Update the dependency map

Add the resource ID as a key in `dependencies`. Value is an array of IDs that depend on it — use `[]` if safe to remediate freely.
