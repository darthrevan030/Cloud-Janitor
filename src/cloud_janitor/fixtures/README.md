# Fixtures

Fixture files provide **fake AWS data** for running Cloud Janitor without AWS credentials or network access. They power the default `fixture` backend (`JANITOR_BACKEND=fixture`) and are the data source for the demo pipeline, property tests, and local development.

No AWS account, IAM permissions, or boto3 installation is required when using fixtures.

---

## `aws_cost_explorer.json`

Contains idle/orphaned resource data that the FinOps Auditor scans for waste.

### Schema

```json
{
  "resources": [
    {
      "id": "cache-prod-legacy-01",
      "type": "elasticache",
      "name": "prod-session-cache",
      "idle_days": 42,
      "monthly_cost": 45.6,
      "status": "available",
      "availability_zone": "us-east-1a",
      "created_at": "2024-08-15T09:30:00Z",
      "description": "Human-readable explanation of why this resource is idle"
    }
  ]
}
```

### Common Fields (all resource types)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | Cloud resource ID (e.g. `cache-prod-legacy-01`, `vol-0abc123def456789a`) |
| `type` | string | yes | Resource type: `"elasticache"`, `"ebs"`, or `"ec2"` |
| `name` | string | yes | Human-friendly name |
| `idle_days` | int | yes | Days since last meaningful activity |
| `monthly_cost` | float | yes | Estimated monthly cost in USD |
| `status` | string | yes | Current state — values vary by type (see below) |
| `availability_zone` | string | yes | AWS AZ (e.g. `"us-east-1a"`) |
| `created_at` | string | yes | ISO 8601 timestamp |
| `description` | string | yes | Explains why the resource is flagged |

### Type-Specific Fields

#### `elasticache`

| Field | Type | Description | Valid Values |
|-------|------|-------------|--------------|
| `connections` | int | Active connection count (0 = idle) | `0`, positive int |
| `instance_type` | string | Node size | `"cache.t3.medium"`, etc. |
| `engine` | string | Cache engine | `"redis"`, `"memcached"` |
| `engine_version` | string | Engine version | `"7.0.7"`, etc. |
| `cluster_mode` | string | Cluster mode | `"disabled"`, `"enabled"` |
| `num_cache_nodes` | int | Number of nodes | Positive int |
| `status` | string | Cluster state | `"available"`, `"creating"`, `"deleting"` |

#### `ebs`

| Field | Type | Description | Valid Values |
|-------|------|-------------|--------------|
| `attached` | bool | Whether volume is attached to an instance | `true`, `false` |
| `volume_type` | string | EBS volume type | `"gp2"`, `"gp3"`, `"io1"`, `"io2"`, `"st1"`, `"sc1"` |
| `size_gb` | int | Volume size in GB | Positive int |
| `encrypted` | bool | Whether volume is encrypted | `true`, `false` |
| `status` | string | Volume state | `"available"` (unattached), `"in-use"` (attached) |

#### `ec2`

| Field | Type | Description | Valid Values |
|-------|------|-------------|--------------|
| `instance_type` | string | Instance size | `"t3.micro"`, `"m5.large"`, etc. |
| `state` | string | Instance state | `"running"`, `"stopped"` |
| `cpu_utilization` | float | Average CPU % over idle period | `0.0` – `100.0` |
| `status` | string | Same as state for EC2 | `"running"`, `"stopped"` |

---

## `aws_config_inspector.json`

Contains security findings and a resource dependency map. The SecOps Guard reads findings; the Remediation Architect reads the dependency map before generating HCL.

### Schema

```json
{
  "findings": [
    {
      "id": "finding-sg-redis-001",
      "resource_id": "sg-prod-redis",
      "resource_type": "aws_security_group",
      "check_type": "security_group",
      "severity": "CRITICAL",
      "current_state": "open_to_world",
      "required_state": "vpc_only",
      "title": "Redis port open to internet",
      "description": "Detailed explanation of the security issue"
    }
  ],
  "dependencies": {
    "sg-prod-redis": ["cache-prod-legacy"]
  }
}
```

### Findings Array

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | Unique finding identifier |
| `resource_id` | string | yes | Affected resource ID |
| `resource_type` | string | yes | AWS resource type (e.g. `"aws_security_group"`, `"aws_elasticache_cluster"`, `"aws_ebs_volume"`) |
| `check_type` | string | yes | Category of check — see enum below |
| `severity` | string | yes | Severity level — see enum below |
| `current_state` | string | yes | What the resource looks like now |
| `required_state` | string | yes | What compliance requires |
| `title` | string | yes | One-line summary |
| `description` | string | yes | Detailed explanation |

#### Check-Type-Specific Fields

**`security_group` findings** also include:

| Field | Type | Description |
|-------|------|-------------|
| `port` | int | The exposed port number (e.g. `6379`, `22`, `3306`) |
| `cidr` | string | Source CIDR (e.g. `"0.0.0.0/0"`) |

**`encryption` findings** also include:

| Field | Type | Description |
|-------|------|-------------|
| `encryption_at_rest` | bool | Current encryption state (`false` = non-compliant) |

### `check_type` Enum

| Value | Meaning |
|-------|---------|
| `"security_group"` | Overly permissive ingress rules |
| `"encryption"` | Missing encryption at rest |
| `"public_access"` | Unintended public exposure |

### `severity` Enum

| Value | Meaning |
|-------|---------|
| `"CRITICAL"` | Immediate risk — sensitive ports open to internet |
| `"HIGH"` | Significant risk — SSH open or missing encryption |
| `"MEDIUM"` | Moderate risk — unencrypted non-sensitive storage |
| `"LOW"` | Informational |

### Dependencies Map

```json
{
  "dependencies": {
    "<resource_id>": ["<dependent_resource_id>", ...]
  }
}
```

- Keys are resource IDs that may have dependents.
- Values are arrays of resource IDs that depend on the key resource.
- An empty array (`[]`) means the resource has no dependents and can be safely remediated.
- A non-empty array means the resource has dependents — the Remediation Architect will surface a warning and block automatic remediation.

`check_dependencies(resource_id)` looks up the resource ID in this map:

- If the key exists, returns `{"has_dependencies": bool, "dependents": [...]}`.
- If the key does not exist, returns `{"has_dependencies": false, "dependents": []}`.

---

## Demo Scenario: Ghost Cluster

The fixture data ships with a pre-built "Ghost Cluster" scenario that demonstrates the full Cloud Janitor pipeline. The resources were chosen to exercise every agent and show realistic cross-concern remediation.

### Resources

| Resource ID | Type | Problem | Agent | Severity |
|-------------|------|---------|-------|----------|
| `cache-prod-legacy-01` | ElastiCache | Idle 42 days, 0 connections, $45.60/mo waste | FinOps Auditor | HIGH |
| `vol-0abc123def456789a` | EBS | Unattached 35 days, $12.00/mo waste | FinOps Auditor | MEDIUM |
| `sg-prod-redis` | Security Group | Port 6379 open to `0.0.0.0/0` | SecOps Guard | CRITICAL |
| `cache-prod-legacy` | ElastiCache | No encryption at rest | SecOps Guard | HIGH |

### Why These Resources?

1. **Cross-concern linking**: `sg-prod-redis` protects `cache-prod-legacy` — the dependency map ties them together, so remediating the security group triggers a dependency warning.
2. **Multiple severity levels**: CRITICAL (open Redis port), HIGH (idle cache, missing encryption), MEDIUM (unattached EBS) — exercises the full severity spectrum.
3. **Both agents fire**: FinOps finds cost waste; SecOps finds security gaps. The Remediation Architect receives findings from both.
4. **Safe-to-remediate resource**: `vol-0abc123def456789a` and `cache-prod-legacy` have empty dependency arrays — they can be remediated without blockers.
5. **Below-threshold resource**: `vol-0def456abc789012b` (5 idle days, still attached) is intentionally NOT flagged — it exercises the filtering logic.

---

## Extending Fixtures

### Adding a New Resource to `aws_cost_explorer.json`

1. Add an object to the `resources` array.
2. Required fields: `id`, `type`, `name`, `idle_days`, `monthly_cost`, `status`, `availability_zone`, `created_at`, `description`.
3. Add type-specific fields based on the resource type (see tables above).
4. Ensure the `type` value matches one of: `"elasticache"`, `"ebs"`, `"ec2"`.

### Adding a New Finding to `aws_config_inspector.json`

1. Add an object to the `findings` array.
2. Required fields: `id`, `resource_id`, `resource_type`, `check_type`, `severity`, `current_state`, `required_state`, `title`, `description`.
3. Add check-type-specific fields (`port`/`cidr` for security_group, `encryption_at_rest` for encryption).
4. Use a unique `id` value (convention: `finding-<type>-<resource>-<seq>`).

### Updating the Dependencies Map

1. Add the resource ID as a key in `dependencies`.
2. Set the value to an array of resource IDs that depend on this resource.
3. Use an empty array `[]` if the resource has no dependents (safe to remediate).
4. `check_dependencies` only looks up keys in this map — if a resource ID is not present, it returns `has_dependencies: false`.

### Adding a New Resource Type

1. Add resources with the new `type` value to `aws_cost_explorer.json`.
2. The FixtureProvider filters by exact string match on the `type` field — no code changes needed.
3. Document any new type-specific fields in this README.
4. Add corresponding security findings if applicable.
