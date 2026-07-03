# Agents

Cloud Janitor's agent pipeline detects waste and security issues, generates Terraform remediation HCL, and gates execution behind strict human approval.

## Agent Classes

### FinOpsAuditor

**Purpose:** Detects idle and orphaned cloud resources representing financial waste.

**What it scans:**

- ElastiCache clusters idle for extended periods
- Unattached EBS volumes
- Idle EC2 instances

**Severity rules:**

| Resource Type | Condition | Severity |
|---|---|---|
| ElastiCache | idle > 30 days | HIGH |
| EBS | unattached > 30 days | MEDIUM |
| All others | idle > 30 days | LOW |

**Data source:** Calls `get_cost_data()` MCP tool with `min_idle_days=0` to retrieve all resources, then applies the 30-day threshold internally.

**Output:** Writes `output/findings_store.json` fresh (overwrites any existing file). The store contains a `scan_id`, timestamps, the findings array, and a summary with counts by severity, by agent, and total monthly waste.

---

### SecOpsGuard

**Purpose:** Detects security vulnerabilities in security groups and unencrypted storage.

**What it scans:**

- Security groups with `0.0.0.0/0` ingress on sensitive ports
- ElastiCache clusters without encryption at rest
- EBS volumes without encryption at rest

**Severity rules:**

| Check | Condition | Severity |
|---|---|---|
| Security group | Ports 6379, 3306, 5432, 27017 open to `0.0.0.0/0` | CRITICAL |
| Security group | Port 22 open to `0.0.0.0/0` | HIGH |
| Encryption | Unencrypted ElastiCache or EBS | HIGH |

**Sensitive ports (must be VPC-only):** 22, 3306, 5432, 6379, 27017

**Data source:** Calls `get_security_data()` MCP tool with `check_type="security_group"` and `check_type="encryption"`.

**Output:** Appends findings to `output/findings_store.json` (does not overwrite FinOps findings). Recalculates the summary section to cover all findings from both agents.

---

### RemediationArchitect

**Purpose:** Generates Terraform HCL for remediating findings and corresponding rollback HCL.

**Inputs:** Reads `output/findings_store.json` (must contain entries from both FinOps and SecOps agents).

**Workflow:**

1. Load all findings from `output/findings_store.json`
2. For each finding, call `check_dependencies()` MCP tool
3. If dependencies found → block remediation, produce warning (manual review required)
4. If no dependencies → generate remediation HCL AND rollback HCL side by side
5. Write output files

**Output files:**

- `output/remediation.tf` — combined remediation HCL for all unblocked findings (overwritten each run)
- `output/rollbacks/<resource_id>.tf` — one rollback file per resource

**HCL generation rules:**

- EBS waste: snapshot first (`depends_on` enforced), then destroy
- Security groups: never delete — narrow CIDR from `0.0.0.0/0` to `data.aws_vpc.current.cidr_block`
- ElastiCache waste: snapshot then delete
- Encryption findings: documented for manual review (cannot enable in-place)
- All generated resources include standard tags: `ManagedBy`, `Environment`, `RemediatedAt`, `RollbackRef`

---

### ApprovalGate

**Purpose:** Implements the strict approval protocol before any infrastructure change executes.

**Accepted command formats (case-sensitive, exact match required):**

| Command | Format | Purpose |
|---|---|---|
| Approve | `APPROVE <resource_id>` | Approve a remediation action |
| Rollback | `ROLLBACK <resource_id>` | Request a rollback |
| Confirm rollback | `CONFIRM ROLLBACK <resource_id>` | Confirm a rollback (two-step) |

**State machine:**

```
┌───────────────────┐
│ awaiting_rollback  │──── ROLLBACK <id> ───►┌──────────────────────────┐
└───────────────────┘                        │ awaiting_confirmation     │
        │                                    └──────────────────────────┘
        │                                                │
        │ (failure x3)                    CONFIRM ROLLBACK <id>
        ▼                                                │
┌───────────────────┐                                    ▼
│     locked         │◄─────── (failure x3) ───┌──────────────────────────┐
└───────────────────┘                          │       confirmed           │
                                               └──────────────────────────┘
```

**3-attempt lock behaviour:**

- Each invalid input increments a failure counter
- After 3 consecutive failures, the gate locks permanently
- A locked gate rejects all further input (even valid commands)
- `reset()` clears the counter and unlocks the gate

**Validation rules:**

- No leading or trailing whitespace allowed
- Command prefix must be exact (case-sensitive)
- Single space separator between command and resource ID
- Resource ID must match the expected value exactly

---

### ReasoningLogger

**Purpose:** Structured event logger for agent reasoning traces. Provides observability into agent decision-making.

**Format:** JSONL (one JSON object per line)

**When it truncates:** At the start of each new audit run (via `truncate()`). During the run, events are appended sequentially.

**Event schema:**

```json
{
  "timestamp": "2025-01-15T10:30:00+00:00",
  "agent": "finops_auditor",
  "event_type": "check",
  "resource_id": "cache-prod-legacy-01",
  "message": "Checking idle duration"
}
```

**Field constraints:**

- `agent`: max 64 characters (truncated silently if longer)
- `event_type`: one of `check`, `finding`, `skip`, `decision`, `handoff`. Invalid values become `"unknown"`.
- `message`: max 500 characters (truncated silently if longer)

**Error handling:** Filesystem errors are printed to stderr and never raised — agent execution must not be interrupted by logging failures.

---

### AuditLogger

**Purpose:** Append-only audit log for compliance and traceability. Records approvals, rollbacks, and other actions.

**Format:** JSONL (append-only — never truncates or overwrites existing content)

**Entry schema:**

```json
{
  "timestamp": "2025-01-15T10:30:00+00:00",
  "resource_id": "vol-abc123",
  "actor": "admin",
  "action": "approval",
  "result": "success"
}
```

**Semantics:**

- File is opened in append mode (`'a'`) for every write
- Returns `True` on success, `False` on failure (never raises)
- Malformed lines are silently skipped when reading back
- Additional keys beyond the core schema are preserved

---

## Agent Sequencing

The agents must run in strict order:

```
FinOpsAuditor → SecOpsGuard → RemediationArchitect
```

**Why order is enforced:**

1. **FinOpsAuditor runs first** — writes `output/findings_store.json` fresh (overwrites). This establishes the baseline store with cost/waste findings.

2. **SecOpsGuard runs second** — reads the existing `output/findings_store.json` and appends security findings. It recalculates the summary to include both agent's contributions.

3. **RemediationArchitect runs last** — reads the complete `output/findings_store.json` containing entries from both prior agents. It needs the full picture to:
   - Check dependencies across all flagged resources
   - Generate remediation that accounts for both cost and security findings on the same resource
   - Avoid generating conflicting remediations

**No agent may skip its predecessor.** RemediationArchitect must not run until `output/findings_store.json` contains entries from both prior agents.

---

## output/findings_store.json Schema

The shared state file that passes data between agents.

```json
{
  "scan_id": "uuid-v4",
  "started_at": "ISO-8601 timestamp (UTC)",
  "completed_at": "ISO-8601 timestamp (UTC) or null",
  "findings": [
    {
      "id": "uuid-v4 (unique finding identifier)",
      "resource_id": "cloud resource ID (e.g. cache-prod-legacy-01, sg-prod-redis)",
      "resource_type": "elasticache | ebs | ec2 | security_group | unknown",
      "agent": "finops | secops",
      "category": "waste | security",
      "severity": "LOW | MEDIUM | HIGH | CRITICAL",
      "title": "Human-readable finding title",
      "description": "Detailed explanation of the finding",
      "cost_estimate_monthly": 0.00,
      "idle_days": 0,
      "metadata": {
        "// Additional fields vary by resource type and check type": "",
        "// FinOps examples: name, status, availability_zone, engine, instance_type": "",
        "// SecOps examples: port, cidr, current_state, required_state, encryption_at_rest": ""
      },
      "detected_at": "ISO-8601 timestamp (UTC)"
    }
  ],
  "summary": {
    "total": 0,
    "by_severity": {
      "LOW": 0,
      "MEDIUM": 0,
      "HIGH": 0,
      "CRITICAL": 0
    },
    "by_agent": {
      "finops": 0,
      "secops": 0
    },
    "total_monthly_waste": 0.00
  }
}
```

**Field details:**

| Field | Type | Description |
|---|---|---|
| `scan_id` | string (UUID v4) | Unique identifier for the scan run |
| `started_at` | string (ISO-8601) | When the scan began |
| `completed_at` | string (ISO-8601) or null | When the scan completed (null until SecOps finishes) |
| `findings` | array | All findings from both agents |
| `findings[].id` | string (UUID v4) | Unique finding identifier |
| `findings[].resource_id` | string | Cloud resource identifier |
| `findings[].resource_type` | string | Resource type enum |
| `findings[].agent` | string | Which agent produced this finding (`finops` or `secops`) |
| `findings[].category` | string | Finding category (`waste` or `security`) |
| `findings[].severity` | string | Severity level: `LOW`, `MEDIUM`, `HIGH`, or `CRITICAL` |
| `findings[].title` | string | Human-readable title |
| `findings[].description` | string | Detailed description |
| `findings[].cost_estimate_monthly` | float | Estimated monthly cost (0.0 for security findings) |
| `findings[].idle_days` | int | Days idle/unattached (0 for security findings) |
| `findings[].metadata` | object | Additional context (varies by resource type) |
| `findings[].detected_at` | string (ISO-8601) | When this finding was detected |
| `summary.total` | int | Total number of findings across all agents |
| `summary.by_severity` | object | Count of findings per severity level |
| `summary.by_agent` | object | Count of findings per agent |
| `summary.total_monthly_waste` | float | Sum of all `cost_estimate_monthly` values (rounded to 2 decimals) |
