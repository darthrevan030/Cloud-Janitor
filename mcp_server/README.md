# AWS Janitor MCP Server

Exposes AWS infrastructure data and Terraform validation via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/), backed by fixture JSON. No live AWS credentials needed.

## Prerequisites

- Python 3.12+
- `mcp >= 1.28.1` (FastMCP)
- `terraform` CLI on PATH (for HCL validation)

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
      "id": "string",
      "type": "elasticache | ebs | ec2",
      "idle_days": "int",
      "monthly_cost": "float",
      "description": "string",
      ...
    }
  ]
}
```

Each resource includes type-specific fields (e.g. `volume_type`, `engine`, `instance_type`).

### `aws_config_inspector.json`

```json
{
  "findings": [
    {
      "id": "string",
      "resource_id": "string",
      "check_type": "security_group | encryption | public_access",
      "severity": "CRITICAL | HIGH | MEDIUM | LOW",
      "current_state": "string",
      "required_state": "string",
      ...
    }
  ],
  "dependencies": {
    "<resource_id>": ["<dependent_id>", ...]
  }
}
```

## Architecture Note

This is a genuine MCP server built with FastMCP — it implements the real MCP protocol and can be consumed by any MCP-compatible client. The data layer uses static fixture JSON files instead of live AWS API calls, making it fully self-contained for demo and development purposes.
