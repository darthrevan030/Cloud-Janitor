# Cloud Janitor

AI-native AWS cloud auditor — finds waste and security gaps, generates Terraform remediations, requires human approval before applying.

## Quick Start

Prerequisites: Docker, Python 3.11+, [uv](https://docs.astral.sh/uv/) or pip.

```bash
# Install dependencies
uv sync          # or: pip install -r requirements.txt

# Run the demo (starts LocalStack + Streamlit dashboard)
make demo
```

`make demo` starts a LocalStack container (emulates AWS on `localhost:4566`), waits for it to be ready, then launches the Streamlit dashboard on port **8501**.

Open <http://localhost:8501> and click **Execute Audit** to run the full pipeline against fixture data.

## How It Works

Cloud Janitor runs a 3-agent pipeline in strict sequence:

```
┌─────────────────┐     ┌──────────────┐     ┌────────────────────────┐
│  FinOps Auditor │ ──▶ │ SecOps Guard │ ──▶ │ Remediation Architect  │
│  (cost waste)   │     │ (security)   │     │ (generates Terraform)  │
└─────────────────┘     └──────────────┘     └────────────────────────┘
                                                        │
                                                        ▼
                                              ┌──────────────────┐
                                              │  Approval Gate   │
                                              │  (human confirm) │
                                              └──────────────────┘
                                                   │         │
                                                   ▼         ▼
                                              tflocal     Rollback
                                               apply      (revert)
```

1. **FinOps Auditor** scans for idle/orphaned resources (ElastiCache, EBS, EC2) and writes findings to `findings_store.json`.
2. **SecOps Guard** appends security findings (open ports, missing encryption) to the same store.
3. **Remediation Architect** reads all findings, checks resource dependencies, generates remediation HCL (`output/remediation.tf`) and per-resource rollback HCL (`rollbacks/<resource_id>.tf`).
4. **Approval Gate** presents the diff to the operator and requires exact-match typed approval (`APPROVE <resource-id>`) before executing. Three failed attempts lock the gate.
5. On approval, `tflocal apply` executes the remediation against LocalStack. Rollback is available via `ROLLBACK <resource-id>` followed by `CONFIRM ROLLBACK <resource-id>`.

## Environment Variables

| Variable | Valid Values | Default | Description |
|----------|-------------|---------|-------------|
| `JANITOR_BACKEND` | `fixture`, `aws`, `gcp`, `azure` | `fixture` | Selects which cloud data provider to use |
| `TF_CMD` | `tflocal`, `terraform` | `tflocal` | Terraform CLI binary — `tflocal` targets LocalStack, `terraform` targets real AWS |
| `JANITOR_SCHEDULE` | cron expression or `disabled` | `disabled` | Schedule for automated scans (requires APScheduler) |

## Running Modes

**Fixture mode** (default) — No AWS credentials required. Reads from `fixtures/*.json` files that simulate a realistic AWS environment. Ideal for demos, development, and testing.

**AWS mode** — Set `JANITOR_BACKEND=aws`. Requires `boto3` installed and valid AWS credentials. Currently a stub that raises `NotImplementedError` — implementation planned for Phase B.

**GCP / Azure** — Interface stubs exist for future multi-cloud support. Setting `JANITOR_BACKEND=gcp` or `JANITOR_BACKEND=azure` instantiates the provider but all methods raise `NotImplementedError`.

## Project Structure

```
cloud-janitor/
├── agents/                  # Agent classes (FinOps, SecOps, Remediation, Approval, Logging)
├── fixtures/                # Fake AWS data (no credentials needed)
│   ├── aws_cost_explorer.json
│   └── aws_config_inspector.json
├── mcp_server/              # MCP server with tool definitions and provider backends
│   ├── backends/            # CloudProvider ABC + concrete implementations
│   └── aws_janitor_mcp.py   # FastMCP tool registrations
├── output/                  # Generated remediation HCL (overwritten each scan)
├── rollbacks/               # Per-resource rollback HCL files
├── scripts/                 # Setup and hook scripts
├── tests/                   # pytest + hypothesis property tests
├── app.py                   # Streamlit dashboard (4-panel UI)
├── orchestrator.py          # Agent pipeline orchestration and approval flow
├── savings.py               # Savings tracker (records cost reductions)
├── findings_store.json      # Shared state between agents (written fresh each audit)
├── docker-compose.yml       # LocalStack container for terraform execution
├── Makefile                 # `make demo` entry point
├── requirements.txt         # Python dependencies
└── pyproject.toml           # Project metadata
```

## Demo Scenario: The Ghost Cluster

The fixture data ships with a realistic "Ghost Cluster" scenario:

- **`cache-prod-legacy-01`** — A Redis ElastiCache cluster (`cache.t3.medium`) that has been idle for 42 days with zero connections. The application migrated to DynamoDB weeks ago but nobody deleted the cache. Costs $45.60/month. Severity: HIGH.
- **`sg-prod-redis`** — A security group attached to the cache cluster with port 6379 open to `0.0.0.0/0`. This is a CRITICAL security finding — Redis should never be internet-accessible.
- **`vol-0abc123def456789a`** — An unattached EBS volume (100 GB gp3) detached 35 days ago from a terminated dev instance. Costs $12.00/month. Severity: MEDIUM.

Together these demonstrate the full pipeline: FinOps flags the cost waste, SecOps flags the security exposure, Remediation Architect generates HCL to snapshot-and-delete the cache, narrow the security group CIDR, and snapshot the orphaned volume — all requiring human approval before execution.

## Running Tests

```bash
# Full test suite
pytest

# Verbose output
pytest -v

# Single test file
pytest tests/test_fixture_provider_properties.py
```

The test suite uses [hypothesis](https://hypothesis.readthedocs.io/) for property-based testing alongside standard unit tests. No AWS credentials are needed — all tests run against fixture data or mocks.
