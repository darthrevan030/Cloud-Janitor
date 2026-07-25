# AWS Janitor MCP Server

Exposes AWS infrastructure data and Terraform validation via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/), backed by fixture JSON. No live AWS credentials needed.

## Prerequisites

- Python 3.12+
- `mcp >= 1.28.1` (FastMCP)
- `tflocal` CLI on PATH (from `terraform-local` package; override via `TF_CMD=terraform` for real AWS)

## Environment Variables

| Variable | Valid values | Default | Description |
|----------|-------------|---------|-------------|
| `JANITOR_BACKEND` | `fixture`, `aws`, `gcp`, `azure` | `fixture` | Active cloud data provider |
| `TF_CMD` | `tflocal`, `terraform` | `tflocal` | Terraform binary for `validate_hcl` |

## Running the Server

```bash
cloud-janitor mcp
```

The server starts using FastMCP's default stdio transport.

## Available Tools

### `get_cost_data(resource_type, min_idle_days)`

Returns idle/orphaned resources from the Cost Explorer fixture.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `resource_type` | `str \| None` | `None` | Filter by type: `elasticache`, `ebs`, `ec2`. `None` returns all. |
| `min_idle_days` | `int` | `7` | Minimum idle days threshold. |

**Returns:** `{"resources": [...], "total_monthly_waste": float}`

### `get_security_data(check_type)`

Returns security findings from the Config/Inspector fixture.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `check_type` | `str \| None` | `None` | Filter: `security_group`, `encryption`, `public_access`. |

**Returns:** `{"findings": [...], "critical_count": int}`

### `validate_hcl(hcl_content)`

Validates Terraform HCL by writing to a temp directory and running `$TF_CMD init -backend=false` + `$TF_CMD validate` (where `TF_CMD` defaults to `tflocal`).

| Parameter | Type | Description |
|-----------|------|-------------|
| `hcl_content` | `str` | Raw HCL/Terraform configuration to validate. |

**Returns:** `{"valid": bool, "error": str | None}`

### `check_dependencies(resource_id)`

Checks the resource dependency graph to determine if other resources reference the target.

| Parameter | Type | Description |
|-----------|------|-------------|
| `resource_id` | `str` | AWS resource ID to check. |

**Returns:** `{"has_dependencies": bool, "dependents": [...]}`

### `interpret_query(user_query)`

Parses a natural-language query into structured scan parameters via LLM.

| Parameter | Type | Description |
|-----------|------|-------------|
| `user_query` | `str` | Free-text question about infrastructure. |

**Returns:** `{"resource_type": str|null, "min_idle_days": int, "check_type": str|null, ...}`

### `explain_remediation(resource_id, finding, remediation_hcl, rollback_hcl)`

Generates a plain-English explanation of a remediation plan.

| Parameter | Type | Description |
|-----------|------|-------------|
| `resource_id` | `str` | Resource being remediated. |
| `finding` | `dict` | The original finding. |
| `remediation_hcl` | `str` | Generated remediation Terraform. |
| `rollback_hcl` | `str` | Generated rollback Terraform. |

**Returns:** `{"risk_explanation": str, "what_terraform_does": str, "what_rollback_restores": str}`

### `suggest_policies(findings, already_checked)`

Suggests additional security/cost checks based on current findings.

| Parameter | Type | Description |
|-----------|------|-------------|
| `findings` | `list[dict]` | Current findings from the scan. |
| `already_checked` | `list[str]` | Check types already covered. |

**Returns:** List of 0–5 policy suggestion dicts.

### `infer_resource_context(resource_id, resource_name, existing_tags?)`

Infers environment, team, owner, and risk level from resource names.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `resource_id` | `str` | — | AWS resource ID. |
| `resource_name` | `str` | — | Human-readable resource name. |
| `existing_tags` | `dict \| None` | `None` | Already-known tags (skips inference for present fields). |

**Returns:** `{"env": str, "team": str, "owner": str, "risk_level": str, "confidence": float}`

### `detect_anomalies(resources, findings)`

Uses LLM to flag suspicious resources not caught by rules.

| Parameter | Type | Description |
|-----------|------|-------------|
| `resources` | `list[dict]` | All resources from the scan. |
| `findings` | `list[dict]` | Already-flagged findings. |

**Returns:** List of anomaly dicts with `severity`, `anomaly_type`, `description`, `resource_id`, `evidence`.

### `policy_from_incident(incident_description)`

Generates preventive scan policies from a natural-language incident description.

| Parameter | Type | Description |
|-----------|------|-------------|
| `incident_description` | `str` | Plain text describing a past incident. |

**Returns:** List of 3–5 policy dicts written to `output/policies/`.

## Fixture Data Format

Fixtures live in `src/cloud_janitor/fixtures/` (bundled inside the installed package).

### `aws_cost_explorer.json`

```json
{
  "resources": [
    {
      "id": "cache-prod-legacy-01",
      "type": "elasticache | ebs | ec2",
      "name": "human-readable-name",
      "idle_days": 42,
      "monthly_cost": 45.6,
      "status": "available | in-use",
      "description": "Why this resource is flagged",
      "availability_zone": "us-east-1a",
      "created_at": "2024-08-15T09:30:00Z"
    }
  ]
}
```

Type-specific fields:

| Type | Extra Fields |
|------|-------------|
| `elasticache` | `connections`, `instance_type`, `engine`, `engine_version`, `cluster_mode`, `num_cache_nodes` |
| `ebs` | `attached`, `volume_type`, `size_gb`, `encrypted` |
| `ec2` | `instance_type`, `state` |

### `aws_config_inspector.json`

```json
{
  "findings": [
    {
      "id": "finding-sg-redis-001",
      "resource_id": "sg-prod-redis",
      "resource_type": "aws_security_group",
      "check_type": "security_group | encryption | public_access",
      "severity": "CRITICAL | HIGH | MEDIUM | LOW",
      "current_state": "open_to_world",
      "required_state": "vpc_only",
      "title": "Short description of the finding",
      "description": "Detailed explanation and remediation guidance"
    }
  ],
  "dependencies": {
    "<resource_id>": ["<dependent_id>", ...]
  }
}
```

Finding-specific fields:

| Check Type | Extra Fields |
|------------|-------------|
| `security_group` | `port`, `cidr` |
| `encryption` | `encryption_at_rest` |

The `dependencies` map drives `check_dependencies()` — keys are resource IDs, values are lists of resources that reference them. An empty list means safe to remediate without cascading impact.

## Provider Backends

The MCP server uses a pluggable provider architecture. The active backend is selected via the `JANITOR_BACKEND` environment variable.

| Backend | `JANITOR_BACKEND` value | Status | Required env vars | Description |
|---------|------------------------|--------|-------------------|-------------|
| Fixture | `fixture` | **Complete** | None | Reads from local JSON fixture files. Default backend. |
| AWS | `aws` | **Complete** | AWS credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`) | Queries live AWS infrastructure via boto3. |
| GCP | `gcp` | Interface only | `GOOGLE_APPLICATION_CREDENTIALS` or ADC | Requires `pip install 'cloud-janitor[gcp]'`. Credential resolution implemented; methods raise `NotImplementedError` (audit implementation in progress). |
| Azure | `azure` | Interface only | `AZURE_SUBSCRIPTION_ID` | Requires `pip install 'cloud-janitor[azure]'`. Placeholder for Microsoft Azure. All methods raise `NotImplementedError`. |

When `JANITOR_BACKEND` is unset, it defaults to `"fixture"`. Setting it to an invalid value raises a `ValueError` listing valid options.

### Provider class hierarchy

```text
CloudProvider (ABC)
├── FixtureProvider   — reads bundled cloud_janitor/fixtures/*.json via importlib.resources
├── AWSProvider       — complete, queries live AWS via boto3
├── GCPProvider       — credential resolution implemented, audit methods pending
└── AzureProvider     — stub (interface only)
```

All providers live in `src/cloud_janitor/mcp_server/backends/` and implement three abstract methods:

- `get_cost_data(resource_type, min_idle_days) -> dict`
- `get_security_data(check_type) -> dict`
- `check_dependencies(resource_id) -> dict`

## Adding a New Provider

1. Create `src/cloud_janitor/mcp_server/backends/<name>_provider.py`
2. Import and inherit from `CloudProvider`:

   ```python
   from cloud_janitor.mcp_server.backends import CloudProvider

   class MyProvider(CloudProvider):
       ...
   ```

3. Implement the three abstract methods: `get_cost_data`, `get_security_data`, `check_dependencies`
4. Register the provider in `src/cloud_janitor/mcp_server/aws_janitor_mcp.py`:

   ```python
   from cloud_janitor.mcp_server.backends.my_provider import MyProvider

   PROVIDER_REGISTRY["my_backend"] = MyProvider
   ```

5. Users can now activate it with `JANITOR_BACKEND=my_backend`

## Phase B/C Tools

The following AI-powered tools are implemented and available:

| Tool | Status | Description |
|------|--------|-------------|
| `interpret_query` | **Complete** | Translate natural-language infrastructure questions into structured tool calls |
| `explain_remediation` | **Complete** | Generate human-readable explanation of a proposed Terraform remediation |
| `suggest_policies` | **Complete** | Recommend additional security/cost checks based on current findings |
| `infer_resource_context` | **Complete** | Infer environment, team, owner, and risk level from resource names |
| `detect_anomalies` | **Complete** | Identify unusual cost or security patterns using LLM analysis |
| `policy_from_incident` | **Complete** | Generate preventive policies from a resolved security incident description |

## Architecture Note

This is a genuine MCP server built with FastMCP — it implements the real MCP protocol and can be consumed by any MCP-compatible client. The data layer uses static fixture JSON files instead of live AWS API calls, making it fully self-contained for demo and development purposes.

## Adding New Fixtures

1. Add or edit JSON files in `src/cloud_janitor/fixtures/`.
2. Follow the schema above — `get_cost_data` expects a top-level `resources` array; `get_security_data` expects `findings` + `dependencies`.
3. Restart the server to pick up changes (fixture files are read on each tool invocation, so no restart is actually needed for data changes).
