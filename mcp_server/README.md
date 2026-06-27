# AWS Janitor MCP Server

Exposes AWS infrastructure data and Terraform validation via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/), backed by fixture JSON. No live AWS credentials needed.

## Prerequisites

- Python 3.12+
- `mcp >= 1.28.1` (FastMCP)
- `terraform` CLI on PATH (for HCL validation)

## Environment Variables

| Variable | Valid values | Default | Description |
|----------|-------------|---------|-------------|
| `TF_CMD` | `tflocal`, `terraform` | `tflocal` | Terraform binary |

## Running the Server

```bash
python mcp_server/aws_janitor_mcp.py
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

Validates Terraform HCL by writing to a temp directory and running `terraform init -backend=false` + `terraform validate`.

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

## Fixture Data Format

Fixtures live in `fixtures/` at the project root.

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

## Architecture Note

This is a genuine MCP server built with FastMCP — it implements the real MCP protocol and can be consumed by any MCP-compatible client. The data layer uses static fixture JSON files instead of live AWS API calls, making it fully self-contained for demo and development purposes.

## Adding New Fixtures

1. Add or edit JSON files in `fixtures/`.
2. Follow the schema above — `get_cost_data` expects a top-level `resources` array; `get_security_data` expects `findings` + `dependencies`.
3. Restart the server to pick up changes (fixture files are read on each tool invocation, so no restart is actually needed for data changes).
