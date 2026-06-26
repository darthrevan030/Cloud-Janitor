# Kiro Autonomous Cloud Janitor — Blueprint v3

**AWS Kiro BuildFest 2026 Singapore**

---

## The One-Sentence Pitch

> An AI that *thinks before it touches* — discovering AWS waste and security gaps, reasoning about dependencies, and generating production-ready Terraform with rollback before a human approves a single change.

**The line that wins the room:** *"Cloud Custodian runs rules. This runs reasoning."*

---

## Awards We're Going For (and How)

| Award                            | Key Rubric Signal                                                                                                                   | Our Answer                                                          |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| **Best Overall**           | Working end-to-end, genuine Kiro use, clear story                                                                                   | Ghost Cluster scenario runs start to finish; .kiro/ is the skeleton |
| **Best Spec-Driven Build** | `.kiro/specs/` with user stories + acceptance criteria + design doc; spec-to-code alignment; commit history shows it was lived in | Full specs folder below; commit order mirrors task plan             |
| **Best Kiro Power User**   | Hooks on real events; custom MCP server (strong signal); agents with steering rules                                                 | Custom AWS MCP server + hooks on save/pre-deploy                    |
| **Most Practical**         | Real user, focused scope, error handling, usable without explanation                                                                | One scenario done perfectly; error states defined                   |
| **Most Ambitious**         | Multi-component, technically demanding, real progress                                                                               | 3 agents + custom MCP + hooks + Terraform generation                |

---

## The Demo Scenario (Locked — Build Exactly This)

**Scenario: "The Ghost Cluster"**

```
Discovered:  ElastiCache cluster (cache-prod-legacy) — unattached 34 days
             Security Group (sg-0a3f...) — port 6379 open to 0.0.0.0/0

FinOps finds:   $847/month waste, 34-day idle confirmed
SecOps finds:   Redis exposed to internet, no auth, encryption disabled
Remediation:    Snapshot → delete cluster → narrow SG to VPC CIDR only
Rollback:       Restore from snapshot + re-open SG if needed
```

---

## Repository Structure 

```
.
├── .kiro/
│   ├── specs/                          ← Judges read this for Best Spec-Driven Build
│   │   ├── requirements.md             ← User stories + acceptance criteria
│   │   ├── design.md                   ← Components, data flow, decisions
│   │   └── tasks.md                    ← Ordered task plan with checkboxes
│   ├── steering/
│   │   ├── AGENTS.md                   ← Agent roles + hard boundaries
│   │   └── rules.md                    ← Remediation standards
│   └── hooks/                          ← Judges read this for Best Kiro Power User
│       ├── pre-remediation.sh          ← Runs terraform validate before approval prompt
│       └── post-remediation.sh         ← Writes audit log entry after execution
├── mcp_server/                         ← Custom MCP server (strong Power User signal)
│   ├── aws_janitor_mcp.py
│   └── README.md
├── agents/
│   ├── finops_auditor.py
│   ├── secops_guard.py
│   └── remediation_architect.py
├── fixtures/
│   ├── aws_cost_explorer.json
│   └── aws_config_inspector.json
├── rollbacks/                          ← Auto-generated, never hand-edited
│   └── cache-prod-legacy.tf
├── audit.log
└── app.py                              ← Streamlit dashboard
```

---

## `.kiro/specs/` — The Spec-Driven Build Heart

> Judges compare specs against shipped code. Every user story below must have a corresponding implementation. Commit the specs on Day 1 before writing any code.

### `.kiro/specs/requirements.md`

```markdown
# Cloud Janitor — Requirements

## Problem Statement
AWS cloud environments accumulate waste and security vulnerabilities silently.
Existing tools (Cloud Custodian, custom scripts) apply rigid rules without
understanding context, dependencies, or risk. Engineers need a system that
reasons about infrastructure the way a senior DevOps engineer would — before
touching anything.

## User Stories

### FinOps Discovery
US-01: As a cloud engineer, I want the system to scan my AWS environment and
       identify idle/orphaned resources so I can see what's costing money
       without providing value.
  Acceptance:
  - [ ] Finds ElastiCache clusters idle > 30 days
  - [ ] Finds unattached EBS volumes > 30 days
  - [ ] Reports estimated monthly cost per finding
  - [ ] Does not modify any resource during scan

US-02: As a cloud engineer, I want each finding tagged with severity (LOW /
       MEDIUM / HIGH / CRITICAL) so I can prioritise what to fix first.
  Acceptance:
  - [ ] All findings have a severity field
  - [ ] ElastiCache idle > 30d = HIGH
  - [ ] Unattached EBS > 30d = MEDIUM

### SecOps Discovery
US-03: As a security engineer, I want the system to flag Security Groups with
       0.0.0.0/0 ingress on sensitive ports so I can remediate exposure.
  Acceptance:
  - [ ] Detects 0.0.0.0/0 on ports: 22, 3306, 5432, 6379, 27017
  - [ ] Reports affected resource ID and port
  - [ ] Severity = CRITICAL for database/cache ports

US-04: As a security engineer, I want the system to flag unencrypted storage
       so I can ensure data at rest is protected.
  Acceptance:
  - [ ] Checks ElastiCache encryption_at_rest
  - [ ] Checks EBS encryption
  - [ ] Reports current state vs. required state

### Remediation Planning
US-05: As a cloud engineer, I want the system to generate a Terraform
       remediation plan AND rollback plan before asking me to approve anything,
       so I can make an informed decision.
  Acceptance:
  - [ ] Remediation HCL generated before approval prompt
  - [ ] Rollback HCL generated alongside remediation (not after)
  - [ ] Both plans shown side-by-side in UI
  - [ ] terraform validate passes on both plans

US-06: As a cloud engineer, I want the system to check resource dependencies
       before planning remediation, so I don't break something that depends
       on the resource being removed.
  Acceptance:
  - [ ] Dependency check runs before HCL generation
  - [ ] If dependency found: block remediation, surface warning
  - [ ] If no dependency: proceed to plan

### Approval & Execution
US-07: As a cloud engineer, I want to explicitly approve each remediation
       action by typing a confirmation string, so nothing runs without my
       intent.
  Acceptance:
  - [ ] Approval requires typing "APPROVE <resource-id>"
  - [ ] No infrastructure change occurs without approval
  - [ ] Approval is logged with timestamp and user

US-08: As a cloud engineer, I want to rollback any remediation within 24h
       by typing a rollback command.
  Acceptance:
  - [ ] "ROLLBACK <resource-id>" triggers rollback flow
  - [ ] Rollback plan shown before execution
  - [ ] Rollback confirmed with "CONFIRM ROLLBACK <resource-id>"
  - [ ] Rollback logged to audit trail

### Observability
US-09: As a cloud engineer, I want every action logged to an audit trail so
       I can prove what the system did and when.
  Acceptance:
  - [ ] Audit log written for: scan, plan, approval, execution, rollback
  - [ ] Each entry: timestamp, action, resource_id, actor, result
  - [ ] Log is append-only (no deletes)

## Out of Scope
- Live AWS credentials / real infrastructure modification
- EC2 rightsizing
- RDS idle detection
- Multi-account support
```

### `.kiro/specs/design.md`

```markdown
# Cloud Janitor — Design

## Architecture Overview

The system has four layers: Spec Engine, Agent Orchestration, MCP Transport, 
and Fixture Infrastructure.
```

User
  │ triggers audit
  ▼
Streamlit Dashboard (app.py)
  │ dispatches to orchestrator
  ▼
Agent Orchestrator (orchestrator.py)
  │ reads .kiro/steering/ before spawning agents
  │ enforces sequencing: FinOps → SecOps → Remediation
  │
  ├── FinOps Auditor ──────────────────────┐
  │     calls: mcp.get_cost_data()         │
  │     produces: findings[] severity=waste│
  │                                        ▼
  ├── SecOps Guard ──────────────────────▶ findings_store.json
  │     calls: mcp.get_security_data()     ▲
  │     produces: findings[] severity=sec  │
  │                                        │
  └── Remediation Architect ───────────────┘
        reads: findings_store.json
        calls: mcp.validate_hcl()
        produces: remediation.tf + rollback.tf
        writes: rollbacks/<resource_id>.tf

MCP Layer (mcp_server/aws_janitor_mcp.py)
  ├── get_cost_data(resource_type, min_idle_days)
  │     → reads fixtures/aws_cost_explorer.json
  ├── get_security_data(check_type)
  │     → reads fixtures/aws_config_inspector.json
  └── validate_hcl(hcl_string)
        → shells out to: terraform validate

Hooks (.kiro/hooks/)
  ├── pre-remediation.sh
  │     trigger: before Remediation Architect writes output
  │     action: terraform validate on generated HCL
  │     blocks execution if validate fails
  └── post-remediation.sh
        trigger: after approved remediation runs
        action: appends to audit.log

```

## Data Flow

1. User clicks "Execute Audit"
2. Orchestrator reads `.kiro/steering/AGENTS.md` to load agent configs
3. FinOps Auditor calls `mcp.get_cost_data()` → parses fixture → produces findings
4. SecOps Guard calls `mcp.get_security_data()` → parses fixture → appends findings
5. findings_store.json written
6. Remediation Architect reads findings, runs dependency check, generates HCL
7. `pre-remediation` hook fires → terraform validate
8. UI shows diff + rollback side-by-side
9. User types "APPROVE <id>"
10. Terraform executes (against fixture/mock provider)
11. `post-remediation` hook fires → audit.log written
12. Rollback artifact saved to rollbacks/<id>.tf

## Key Design Decisions

**Why simulated infrastructure?**
Live AWS requires credentials, introduces demo risk, and is unnecessary to
demonstrate the reasoning architecture. Judges care about the agent logic,
MCP integration, and spec workflow — not whether real EC2 instances exist.
Simulated data is stated explicitly in the demo.

**Why a custom MCP server instead of off-the-shelf?**
Per judging criteria, building a custom MCP server is a "strong signal of depth"
for the Best Kiro Power User award. Our MCP server wraps the fixture data behind
a real MCP protocol interface, making the integration architecture genuine even
with seeded data.

**Why sequential agents instead of parallel?**
SecOps findings can affect remediation scope (e.g. an idle cluster that's also
publicly exposed needs a different remediation plan than one that's just idle).
Sequential execution with shared findings_store ensures Remediation Architect
has complete context.

**Why typed approval instead of a button?**
Typed confirmation ("APPROVE cache-prod-legacy") creates friction that is
intentional — it forces the engineer to name the specific resource they're
authorising. A button click is too easy to misfire on a production system.
```

### `.kiro/specs/tasks.md`

```markdown
# Cloud Janitor — Task Plan

## Phase 1: Foundation (Day 1 Morning)
- [x] T-001: Create .kiro/ directory structure and commit
- [x] T-002: Write requirements.md with all user stories
- [x] T-003: Write design.md with architecture + data flow
- [ ] T-004: Write fixture JSON for Cost Explorer (3 resources, 2 flaggable)
- [ ] T-005: Write fixture JSON for Config/Inspector (2 security findings)

## Phase 2: MCP Server (Day 1 Afternoon)
- [ ] T-006: Implement aws_janitor_mcp.py with MCP protocol
- [ ] T-007: Implement get_cost_data() → reads Cost Explorer fixture
- [ ] T-008: Implement get_security_data() → reads Inspector fixture
- [ ] T-009: Implement validate_hcl() → shells to terraform validate
- [ ] T-010: Write mcp_server/README.md

## Phase 3: Agents (Day 1 Evening – Day 2 Midday)
- [ ] T-011: FinOps Auditor — calls MCP, produces findings[], writes findings_store.json
- [ ] T-012: SecOps Guard — calls MCP, appends to findings_store.json
- [ ] T-013: Remediation Architect — reads findings, dependency check, generates HCL
- [ ] T-014: Rollback HCL generation (alongside remediation, not after)
- [ ] T-015: findings_store.json schema validation

## Phase 4: Hooks (Day 2 Midday)
- [ ] T-016: pre-remediation.sh — terraform validate gate
- [ ] T-017: post-remediation.sh — audit.log append
- [ ] T-018: Wire hooks into orchestrator call sequence

## Phase 5: Approval + Execution (Day 2 Afternoon)
- [ ] T-019: Approval gate — parse "APPROVE <id>", reject malformed input
- [ ] T-020: Rollback gate — parse "ROLLBACK <id>" + "CONFIRM ROLLBACK <id>"
- [ ] T-021: Audit log writer (append-only)
- [ ] T-022: Error states: dependency found, validate fails, malformed approval

## Phase 6: UI (Day 2 Evening)
- [ ] T-023: Streamlit layout — 4 panels (agent feed, findings, diff, audit log)
- [ ] T-024: Agent activity feed with live status dots
- [ ] T-025: Side-by-side diff view (remediation HCL vs rollback HCL)
- [ ] T-026: Approval input field + confirmation display
- [ ] T-027: Savings counter

## Phase 7: Polish + Demo (Day 3)
- [ ] T-028: End-to-end Ghost Cluster scenario run (no errors)
- [ ] T-029: Rollback flow run (no errors)
- [ ] T-030: Error state test: approval typo rejected gracefully
- [ ] T-031: Rehearse 6-min demo script 3x
- [ ] T-032: Record demo video for Devpost submission
- [ ] T-033: Write Devpost submission copy
```

---

## `.kiro/steering/` — Agent Steering Files

### `.kiro/steering/AGENTS.md`

```markdown
# Cloud Janitor Agent Steering

## Agent Roles

### FinOps Auditor
- Detects financial waste: unattached EBS, idle EC2, orphaned ElastiCache
- Confirms idle duration before flagging (minimum 7 days; flag at 30+)
- Estimates monthly cost of each waste item using Cost Explorer fixture
- Tags findings: severity LOW / MEDIUM / HIGH

### SecOps Guard
- Flags Security Groups with 0.0.0.0/0 ingress on sensitive ports
- Audits ElastiCache encryption at rest and auth_token settings
- Checks EBS volume encryption
- Tags findings: severity HIGH / CRITICAL; includes port + CVE ref where applicable

### Remediation Architect
- Receives complete findings_store.json (both FinOps + SecOps findings)
- Runs dependency check before generating any HCL
- Produces in order: dependency report → remediation HCL → rollback HCL
- Never generates code without first completing dependency check
- All generated resources tagged: ManagedBy, Environment, RemediatedAt, RollbackRef

## Agent Sequencing
FinOps Auditor → SecOps Guard → Remediation Architect
No agent may skip its predecessor. Remediation Architect must not run
until findings_store.json contains entries from both prior agents.

## Hard Boundaries (Never Violate)
- Never generate AWS access keys or secrets
- Never expose plaintext credentials in any output
- Never modify infrastructure without explicit typed approval
- Always generate rollback HCL before surfacing approval prompt
- Rollback HCL must pass terraform validate before approval prompt appears
```

### `.kiro/steering/rules.md`

```markdown
# Infrastructure Remediation Standards

## Terraform Tag Requirements
Every generated resource block must include:
  ManagedBy    = "Kiro-Janitor"
  Environment  = var.environment
  RemediatedAt = timestamp()
  RollbackRef  = "rollbacks/<resource_id>.tf"

## EBS Volume Rules
- Unattached > 7 days: FLAG (severity=MEDIUM)
- Unattached > 30 days: REMEDIATE
  1. aws_ebs_snapshot_copy (snapshot first, depends_on enforced)
  2. aws_ebs_volume destroy
  Rollback: aws_ebs_volume restore from snapshot ARN

## Security Group Rules
- Never delete a Security Group — always narrow CIDR
- Replace 0.0.0.0/0 ingress with data.aws_vpc.current.cidr_block
- Sensitive ports (VPC-only required): 22, 3306, 5432, 6379, 27017
- Rollback: restore original 0.0.0.0/0 rule (stored in rollback HCL)

## ElastiCache Rules
- Idle > 30 days + no active connections: snapshot → delete
- Rollback: restore from snapshot, same node_type and engine version
- New clusters must have: encryption_at_rest=true, auth_token if public subnet

## Approval Gate Protocol
- Display dependency check result first
- Display remediation HCL diff
- Display rollback HCL alongside (not below)
- Require: "APPROVE <resource-id>" — exact match, case-sensitive
- Reject and re-prompt on any other input
- Log approval with: timestamp, resource_id, approver, action

## Error Handling Rules
- Dependency found: surface warning, block remediation, suggest manual review
- terraform validate fails: surface error text, block approval prompt
- Approval string mismatch: display expected format, re-prompt (max 3 attempts)
- Rollback artifact missing: surface error, do not proceed
```

---

## `.kiro/hooks/` — Automation on Real Triggers

> Hooks are evaluated for Best Kiro Power User. They must be wired to real events, not just exist as files.

### `.kiro/hooks/pre-remediation.sh`

```bash
#!/bin/bash
# Trigger: before Remediation Architect surfaces approval prompt
# Action: validate generated HCL — block if invalid
# This hook runs automatically; engineers cannot skip it.

set -e

REMEDIATION_FILE="${1:-/tmp/remediation.tf}"
ROLLBACK_FILE="${2:-/tmp/rollback.tf}"

echo "[pre-remediation] Validating remediation HCL..."
terraform validate "$REMEDIATION_FILE"
if [ $? -ne 0 ]; then
  echo "[pre-remediation] BLOCKED: remediation.tf failed validation"
  exit 1
fi

echo "[pre-remediation] Validating rollback HCL..."
terraform validate "$ROLLBACK_FILE"
if [ $? -ne 0 ]; then
  echo "[pre-remediation] BLOCKED: rollback.tf failed validation"
  exit 1
fi

echo "[pre-remediation] Both plans valid. Proceeding to approval prompt."
exit 0
```

### `.kiro/hooks/post-remediation.sh`

```bash
#!/bin/bash
# Trigger: after approved remediation completes
# Action: append structured entry to audit.log

RESOURCE_ID="$1"
ACTION="$2"        # "remediate" | "rollback"
RESULT="$3"        # "success" | "failed"
APPROVER="$4"

AUDIT_LOG="./audit.log"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "${TIMESTAMP} | ${ACTION} | ${RESOURCE_ID} | ${RESULT} | approver=${APPROVER}" >> "$AUDIT_LOG"
echo "[post-remediation] Audit entry written."
```

---

## Custom MCP Server

> Building a custom MCP server is called out by judges as a "strong signal of depth." This is not optional for the Power User award.

### `mcp_server/aws_janitor_mcp.py`

```python
"""
AWS Janitor MCP Server
Exposes AWS infrastructure data and Terraform validation via MCP protocol.
Backed by fixture JSON — no live AWS credentials required.
"""

import json
import subprocess
import tempfile
import os
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

class AWSJanitorMCP:
    """Custom MCP server for the Cloud Janitor agents."""

    def get_cost_data(self, resource_type: str = None, min_idle_days: int = 7) -> dict:
        """
        Returns idle/orphaned resource data from Cost Explorer fixture.
      
        Args:
            resource_type: Filter by type (elasticache|ebs|ec2). None = all.
            min_idle_days: Minimum idle days to include in results.
      
        Returns:
            {"resources": [...], "total_monthly_waste": float}
        """
        with open(FIXTURES_DIR / "aws_cost_explorer.json") as f:
            data = json.load(f)
      
        resources = data["resources"]
        if resource_type:
            resources = [r for r in resources if r["type"] == resource_type]
        resources = [r for r in resources if r["idle_days"] >= min_idle_days]
      
        total_waste = sum(r["monthly_cost"] for r in resources)
        return {"resources": resources, "total_monthly_waste": round(total_waste, 2)}

    def get_security_data(self, check_type: str = None) -> dict:
        """
        Returns security finding data from Config/Inspector fixture.
      
        Args:
            check_type: Filter by type (security_group|encryption|public_access).
      
        Returns:
            {"findings": [...], "critical_count": int}
        """
        with open(FIXTURES_DIR / "aws_config_inspector.json") as f:
            data = json.load(f)
      
        findings = data["findings"]
        if check_type:
            findings = [f for f in findings if f["check_type"] == check_type]
      
        critical = sum(1 for f in findings if f["severity"] == "CRITICAL")
        return {"findings": findings, "critical_count": critical}

    def validate_hcl(self, hcl_content: str) -> dict:
        """
        Validates Terraform HCL by writing to temp file and running terraform validate.
      
        Returns:
            {"valid": bool, "error": str | None}
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            hcl_path = os.path.join(tmpdir, "main.tf")
            with open(hcl_path, "w") as f:
                f.write(hcl_content)
          
            result = subprocess.run(
                ["terraform", "validate"],
                cwd=tmpdir,
                capture_output=True,
                text=True
            )
          
            if result.returncode == 0:
                return {"valid": True, "error": None}
            else:
                return {"valid": False, "error": result.stderr.strip()}

    def check_dependencies(self, resource_id: str) -> dict:
        """
        Checks whether any other resource references the target resource.
        Backed by fixture data — returns dependency list.
      
        Returns:
            {"has_dependencies": bool, "dependents": [...]}
        """
        with open(FIXTURES_DIR / "aws_config_inspector.json") as f:
            data = json.load(f)
      
        deps = data.get("dependencies", {}).get(resource_id, [])
        return {"has_dependencies": len(deps) > 0, "dependents": deps}


# MCP protocol entrypoint
mcp = AWSJanitorMCP()
```

---

## Fixture Data

### `fixtures/aws_cost_explorer.json`

```json
{
  "account_id": "123456789012",
  "scan_timestamp": "2026-06-26T08:00:00Z",
  "resources": [
    {
      "resource_id": "cache-prod-legacy",
      "type": "elasticache",
      "name": "cache-prod-legacy",
      "region": "ap-southeast-1",
      "idle_days": 34,
      "monthly_cost": 847.20,
      "last_connection": "2026-05-23T14:22:00Z",
      "node_type": "cache.t3.micro",
      "engine": "redis",
      "engine_version": "7.0.7"
    },
    {
      "resource_id": "vol-0a1b2c3d4e",
      "type": "ebs",
      "name": "old-data-volume",
      "region": "ap-southeast-1",
      "idle_days": 45,
      "monthly_cost": 38.40,
      "last_attachment": "2026-05-11T09:00:00Z",
      "size_gb": 100,
      "volume_type": "gp3"
    },
    {
      "resource_id": "i-0deadbeef",
      "type": "ec2",
      "name": "dev-test-server",
      "region": "ap-southeast-1",
      "idle_days": 3,
      "monthly_cost": 12.00,
      "cpu_avg_7d": 0.8
    }
  ]
}
```

### `fixtures/aws_config_inspector.json`

```json
{
  "account_id": "123456789012",
  "scan_timestamp": "2026-06-26T08:00:00Z",
  "findings": [
    {
      "resource_id": "sg-0a3f9b2c1d",
      "check_type": "security_group",
      "severity": "CRITICAL",
      "detail": "Port 6379 (Redis) open to 0.0.0.0/0",
      "port": 6379,
      "protocol": "tcp",
      "current_cidr": "0.0.0.0/0",
      "required_cidr": "VPC_CIDR"
    },
    {
      "resource_id": "cache-prod-legacy",
      "check_type": "encryption",
      "severity": "HIGH",
      "detail": "ElastiCache cluster has encryption_at_rest disabled",
      "current_state": {"encryption_at_rest": false, "auth_token": false},
      "required_state": {"encryption_at_rest": true}
    }
  ],
  "dependencies": {
    "cache-prod-legacy": [],
    "sg-0a3f9b2c1d": ["cache-prod-legacy"],
    "vol-0a1b2c3d4e": []
  }
}
```

---

## Error States (Required for Most Practical Award)

The rubric explicitly requires "sensible handling of errors and empty states, not only the ideal path."

| Error                     | Trigger                                | Handling                                                                      |
| ------------------------- | -------------------------------------- | ----------------------------------------------------------------------------- |
| Dependency found          | Resource has dependents in fixture     | Block remediation, surface warning with dependent list, suggest manual review |
| HCL validation fails      | terraform validate returns non-zero    | Block approval prompt, show error text verbatim, log attempt                  |
| Approval string mismatch  | User types wrong confirmation          | Show expected format, re-prompt, max 3 attempts then abort                    |
| Rollback artifact missing | ROLLBACK called but .tf file not found | Surface error, explain artifact may have been cleaned up                      |
| Empty scan results        | Fixture has no flaggable resources     | Show "No findings — your infrastructure looks clean" state                   |
| MCP server unreachable    | aws_janitor_mcp.py not running         | Surface connection error, show "Check mcp_server/ README"                     |

---

## Demo Script (6 Minutes)

### 0:00–0:30 — Hook

> *"Every cloud team has waste they know about and security gaps they don't. Existing tools apply rigid rules without context — they don't understand dependencies, they don't generate rollback, and they've all deleted something they shouldn't have. We built an AI that reasons about infrastructure the way a senior DevOps engineer would — checking dependencies, generating rollback before touching anything, and requiring your approval at every step."*

### 0:30–2:00 — Scan

- Click **Execute Audit**
- Agent feed animates: "FinOps Auditor scanning… 2 findings" → "SecOps Guard scanning… 1 critical" → "Remediation Architect ready"
- Findings panel: `cache-prod-legacy` ($847/mo, 34d idle, HIGH) + `sg-0a3f` (0.0.0.0/0:6379, CRITICAL)
- Point at `.kiro/steering/AGENTS.md` panel: *"The agents follow steering files — rules the team owns and can change without touching code."*

### 2:00–3:30 — Plan

- Click **Generate Remediation Plan** on `cache-prod-legacy`
- Show dependency check: agent queries, returns empty → "No dependents. Safe to proceed."
- Side-by-side diff: current state vs. remediation HCL vs. rollback HCL
- *"The rollback is generated before we ask for approval — not after."*
- Point at `pre-remediation` hook in UI: *"Before this diff appeared, a hook automatically ran terraform validate on both plans."*

### 3:30–4:30 — Approve + Execute

- Type `APPROVE cache-prod-legacy`
- Progress feed: snapshot → delete → audit log written
- Savings counter: **+$847/month**
- Show audit.log entry in UI

### 4:30–5:30 — Rollback *(The moment that wins)*

- *"Now — what if we made a mistake?"*
- Type `ROLLBACK cache-prod-legacy`
- Rollback plan appears. Type `CONFIRM ROLLBACK cache-prod-legacy`
- Cluster restored. Audit log entry written.
- *"This is what makes autonomous cloud tooling safe enough to actually use in production."*

### 5:30–6:00 — Repo Close

- Show `.kiro/` in repo: *"Everything — agent roles, remediation rules, task plan, hooks — lives here. Reviewable, versionable, auditable."*
- Show `mcp_server/`: *"We built a custom MCP server so the agents talk to infrastructure data through a real protocol, not hardcoded function calls."*
- Show `tasks.md` with checkboxes ticked: *"The spec drove the build — every task came from a user story."*

---

## UI Layout (Build Exactly This, No More)

```
┌─────────────────────────────────────────────────────────────┐
│  Kiro Cloud Janitor                        [Execute Audit ▶] │
├──────────────────────────┬──────────────────────────────────┤
│  AGENT ACTIVITY          │  FINDINGS                        │
│                          │                                  │
│  ● FinOps Auditor        │  ▲ HIGH   cache-prod-legacy      │
│    Scanning Cost MCP...  │          $847/mo · 34d idle      │
│                          │          [Generate Plan ▶]       │
│  ● SecOps Guard          │                                  │
│    Checking Security...  │  ● CRIT  sg-0a3f9b2c1d           │
│                          │          0.0.0.0/0 → port 6379  │
│  ✓ Remediation Arch      │          [Generate Plan ▶]       │
│    Plans ready           │                                  │
├──────────────────────────┴──────────────────────────────────┤
│  REMEDIATION PLAN                  ROLLBACK PLAN            │
│  ┌─────────────────────┐          ┌──────────────────────┐  │
│  │ - cluster running   │          │ + restore snapshot   │  │
│  │ + snapshot created  │          │ + cluster restored   │  │
│  │ + cluster deleted   │          │                      │  │
│  └─────────────────────┘          └──────────────────────┘  │
│                                                              │
│  Hook: pre-remediation ✓ validated    [?] Dependency check  │
│                                                              │
│  Approval: [APPROVE cache-prod-legacy              ] [OK]   │
├─────────────────────────────────────────────────────────────┤
│  AUDIT LOG                                                   │
│  2026-06-26T14:23:01Z | scan      | all         | success   │
│  2026-06-26T14:24:15Z | plan      | cache-prod  | success   │
│  2026-06-26T14:24:44Z | remediate | cache-prod  | approved  │
│  2026-06-26T14:24:58Z | remediate | cache-prod  | success   │
└─────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer               | Choice                                 | Reason                                     |
| ------------------- | -------------------------------------- | ------------------------------------------ |
| UI                  | Streamlit                              | Fast; judges can run locally without setup |
| Agent orchestration | Python                                 | Direct Kiro integration                    |
| MCP server          | Custom Python (`aws_janitor_mcp.py`) | Strong Power User signal per rubric        |
| Infra data          | JSON fixtures                          | No live AWS needed; stated upfront in demo |
| HCL validation      | `terraform validate`                 | Real tool, real output                     |
| Hooks               | Bash scripts in`.kiro/hooks/`        | Wired to real orchestrator events          |
| Audit log           | Append-only flat file                  | Simple, demoable, inspectable              |

---

## Build Order

Each phase is independently demoable if time runs out.

```
Day 1 AM    T-001–T-003   .kiro/specs/ written and committed (before any code)
Day 1 PM    T-004–T-010   Fixtures + custom MCP server
Day 1 EVE   T-011–T-013   FinOps + SecOps agents

Day 2 AM    T-014–T-018   Remediation Architect + hooks
Day 2 PM    T-019–T-022   Approval gate + error states
Day 2 EVE   T-023–T-027   Streamlit UI

Day 3 AM    T-028–T-031   End-to-end test + error state validation
Day 3 PM    T-032–T-033   Demo video + Devpost submission
```

**Cut order if time collapses:** rollback execution (keep display) → EBS agent (keep ElastiCache only) → Streamlit (terminal output). Never cut `.kiro/specs/` or the custom MCP server.

---

## Devpost Submission Copy

**Title:** Kiro Autonomous Cloud Janitor

**Tagline:** Cloud Custodian runs rules. This runs reasoning.

**Cover image:** Screenshot of the 4-panel dashboard mid-audit (agent feed animating, findings visible, diff panel populated).

**Write-up structure:**

1. The problem (2 sentences): AWS teams waste money on idle resources and miss security gaps. Existing tools apply rigid rules — they don't understand dependencies and they've deleted things they shouldn't have.
2. Our approach (3 sentences): Three specialist AI agents — FinOps Auditor, SecOps Guard, Remediation Architect — guided by `.kiro/` steering files. Each agent reasons about the infrastructure it sees. Nothing runs without a human approval and a pre-generated rollback plan.
3. How we used Kiro (bullet list): Spec-driven build from day one (`.kiro/specs/`); agent steering via AGENTS.md; hooks on remediation events; custom MCP server for infrastructure data transport.
4. Demo link + repo link.

---

## Judging Criteria Checklist

### Best Spec-Driven Build

- [ ] `.kiro/specs/` folder exists with requirements.md, design.md, tasks.md
- [ ] requirements.md has user stories with testable acceptance criteria
- [ ] design.md explains components, data flow, and decisions
- [ ] tasks.md checkboxes ticked in commit history as work was done
- [ ] Every shipped feature maps to a user story in requirements.md

### Best Kiro Power User

- [ ] Custom MCP server implemented (`mcp_server/aws_janitor_mcp.py`)
- [ ] Hooks wired to real events (`pre-remediation`, `post-remediation`)
- [ ] Hooks demonstrably ran (audit log shows hook output)
- [ ] Agent steering rules in AGENTS.md actively shape agent behaviour
- [ ] Agents do meaningful multi-step work (scan → plan → validate → approve)

### Most Practical

- [ ] One user (cloud engineer) with one real problem (waste + security)
- [ ] Runs end-to-end without explanation needed beyond "click Execute Audit"
- [ ] All 6 error states handled gracefully
- [ ] Audit log provides traceability
- [ ] Could be pointed at real infra with credential swap

### Best Overall + Most Ambitious

- [ ] Demo runs start to finish with no errors
- [ ] Rollback moment lands clearly
- [ ] Devpost write-up tells problem → approach → result in under 3 minutes of reading
- [ ] Demo video is under 6 minutes and covers the rollback

### People's Choice

- [ ] Strong cover image (dashboard screenshot)
- [ ] Short demo video (highlight reel version: 90 seconds)
- [ ] Clear project title on Devpost

---

## What Not to Build

- Live AWS credentials / real infrastructure modification
- EC2 rightsizing or RDS detection (out of scope per requirements.md)
- More than 4 UI panels — judges care about agents, not CSS
- Multi-account support
- A CLI-only interface (Streamlit is more demoable)
- General-purpose rule engine — depth of one scenario beats breadth of fi

# Kiro Autonomous Cloud Janitor — Blueprint v2

**AWS Kiro BuildFest 2026 Singapore**

---

## The One-Sentence Pitch

> An AI that *thinks before it touches* — discovering AWS waste and security gaps, reasoning about dependencies, and generating production-ready Terraform with rollback before a human ever approves a change.

This is what separates us from Cloud Custodian and every Bash script ever written: **reasoning, not rules.**

---

## What Changed From v1 (Council Improvements)

| Area             | v1                          | v2                                                      |
| ---------------- | --------------------------- | ------------------------------------------------------- |
| Demo scope       | Vague "Streamlit dashboard" | One locked scenario, bulletproof end-to-end             |
| Demo moment      | Happy path only             | Includes rollback demo — safety story made visceral    |
| Agent visibility | Static architecture diagram | Live agent progress feed in UI                          |
| Pitch hook       | Buried in comparison table  | 30-second verbal hook defined upfront                   |
| MCP honesty      | Implied live AWS            | Explicit simulated infra — architecture legible anyway |

---

## The Demo Scenario (Locked — Build Exactly This)

Do not generalize. Build one flow that is cinematically perfect.

**Scenario: "The Ghost Cluster"**

```
Discovered:  ElastiCache cluster (cache-prod-legacy) — unattached 34 days
             Security Group (sg-0a3f...) — port 6379 open to 0.0.0.0/0

FinOps finds:   $847/month waste, 34-day idle confirmed
SecOps finds:   Redis exposed to internet, no auth, encryption disabled
Remediation:    Snapshot → delete cluster → narrow SG to VPC CIDR only
Rollback:       Restore from snapshot + re-open SG if needed
```

This scenario hits **all three agents**, produces a **Terraform diff the judges can read**, and lets you demo the rollback in 60 seconds.

---

## Competitive Edge

| Feature             | Cloud Custodian / Bash | Kiro Cloud Janitor                                |
| ------------------- | ---------------------- | ------------------------------------------------- |
| Logic engine        | Hardcoded YAML rules   | Sequential-reasoning AI agents                    |
| Context awareness   | None                   | Guided by`.kiro/` steering files                |
| Safety model        | Blind execution        | Spec → plan → human approval → execute         |
| Dependency handling | Fails or ignores       | Agent discovers and resolves before acting        |
| Rollback            | Manual                 | Auto-generated HCL rollback block, pre-approved   |
| Audit trail         | Logs (if lucky)        | Every action tagged`ManagedBy = "Kiro-Janitor"` |

**The line that wins the room:** *"Cloud Custodian runs rules. This runs reasoning."*

---

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    User / Developer                      │
│              "Execute Cloud Audit"                       │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│                  Kiro Spec Engine                        │
│         Validates .kiro/AGENTS.md + rules.md            │
│         Loads task spec before any agent fires          │
└──────┬──────────────────┬───────────────────┬───────────┘
       │                  │                   │
       ▼                  ▼                   ▼
┌────────────┐   ┌───────────────┐   ┌──────────────────┐
│   FinOps   │   │    SecOps     │   │  Remediation     │
│  Auditor   │──▶│    Guard      │──▶│  Architect       │
│            │   │               │   │                  │
│ Finds waste│   │ Finds vulns   │   │ Generates HCL    │
│ Tags idle  │   │ Flags SGs     │   │ + rollback block │
│ resources  │   │ Checks S3     │   │ Requires approval│
└────────────┘   └───────────────┘   └──────────────────┘
       │                  │                   │
       └──────────────────┴───────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│              MODEL CONTEXT PROTOCOL LAYER               │
├─────────────────┬──────────────────┬────────────────────┤
│  AWS Cost MCP   │  AWS Security MCP│   Terraform MCP    │
│  Cost Explorer  │  Inspector/Config│   HCL Validation   │
│  (simulated)    │  (simulated)     │   (real output)    │
└─────────────────┴──────────────────┴────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│            Target AWS Infrastructure (Simulated)        │
│  • cache-prod-legacy  ElastiCache  — 34 days idle       │
│  • sg-0a3f...         Security Group — 0.0.0.0/0:6379   │
└─────────────────────────────────────────────────────────┘
```

**Why simulated infra is fine to say out loud:** Judges at a hackathon care about architecture, reasoning quality, and Kiro's spec-driven workflow. You will explicitly state "we're running against a simulated AWS environment" in the demo opening. The MCP integration *architecture* is real — backing data is seeded. This is standard practice and judges will respect the honesty.

---

## `.kiro/` Directory (Commit Everything — Judges Will Read It)

```
.kiro/
├── AGENTS.md
├── rules.md
└── tasks/
    ├── 001_initial_scan_spec.md
    ├── 002_remediation_spec.md        ← NEW
    └── 003_rollback_spec.md           ← NEW
```

### `.kiro/AGENTS.md`

```markdown
# Cloud Janitor Agent Steering

## Agent Roles

### FinOps Auditor
- Detects financial waste: unattached EBS, idle EC2, orphaned ElastiCache
- Confirms idle duration before flagging (minimum 7 days; flag at 30+)
- Estimates monthly cost of each waste item
- Tags findings with severity: LOW / MEDIUM / HIGH

### SecOps Guard
- Flags Security Groups with 0.0.0.0/0 ingress on sensitive ports
- Checks S3 bucket public access settings
- Audits EBS/RDS/ElastiCache encryption at rest
- Tags findings with CVE references where applicable

### Remediation Architect
- Receives structured findings from FinOps + SecOps
- Investigates resource dependencies before generating any HCL
- Produces: remediation spec → Terraform block → rollback block
- Never generates code without a prior spec
- All generated resources tagged ManagedBy = "Kiro-Janitor"

## Hard Boundaries (Never Violate)
- Never generate AWS access keys or secrets
- Never expose plaintext credentials in HCL output
- Never modify infrastructure without explicit user approval
- Always generate rollback before asking for approval
- Rollback must be tested (plan only) before approval prompt
```

### `.kiro/rules.md`

```markdown
# Infrastructure Remediation Standards

## Terraform Tag Requirements
Every generated resource block must include:
  ManagedBy   = "Kiro-Janitor"
  Environment = var.environment   # "dev" | "staging" | "prod"
  RemediatedAt = timestamp()

## EBS Volume Rules
- Unattached > 7 days: FLAG
- Unattached > 30 days: REMEDIATE
  1. aws_ebs_snapshot_copy (snapshot first)
  2. aws_volume_attachment destroy
  3. aws_ebs_volume destroy
  4. Rollback: restore from snapshot ARN

## Security Group Rules
- Never delete a Security Group — always narrow CIDR
- Replace 0.0.0.0/0 ingress with VPC CIDR block (data.aws_vpc.current.cidr_block)
- Sensitive ports requiring VPC-only: 22, 3306, 5432, 6379, 27017

## ElastiCache Rules
- Idle > 30 days: snapshot → delete
- Require encryption_at_rest = true on all new clusters
- auth_token required if reachable from public subnet

## Approval Gate
- Display full Terraform plan output before asking for approval
- Display rollback plan alongside remediation plan
- Require typed confirmation: "APPROVE <resource-id>"
```

### `.kiro/tasks/001_initial_scan_spec.md`

```markdown
# Task 001 — MCP Ingestion & Baseline Discovery

## Objective
Implement a secure integration layer using MCP to ingest AWS infrastructure metrics
and produce a structured findings report.

## Inputs
- Simulated AWS Cost Explorer data (JSON fixture)
- Simulated AWS Config/Inspector data (JSON fixture)

## Success Criteria
- [ ] Identifies at least 3 distinct waste/security categories
- [ ] Produces findings.json with: resource_id, type, severity, estimated_cost, idle_days
- [ ] Generates findings.md human-readable report
- [ ] Zero infrastructure modification during discovery phase

## Output Schema
{
  "findings": [
    {
      "resource_id": "string",
      "resource_type": "elasticache|ebs|ec2|sg|s3",
      "finding_type": "waste|security",
      "severity": "low|medium|high|critical",
      "estimated_monthly_cost": number,
      "idle_days": number | null,
      "details": "string"
    }
  ]
}
```

### `.kiro/tasks/002_remediation_spec.md` *(New)*

```markdown
# Task 002 — Remediation Planning

## Objective
For each HIGH/CRITICAL finding from Task 001, produce a complete remediation plan
before any infrastructure action.

## Steps (in order — no skipping)
1. Dependency check: query all resources referencing target resource
2. Impact assessment: document what breaks if resource is removed
3. Generate remediation Terraform HCL
4. Generate rollback Terraform HCL
5. Run terraform plan (dry-run) on both
6. Present diff to user for approval

## Approval Format
Display to user:
  REMEDIATION PLAN: <resource_id>
  Estimated savings: $X/month
  Risk: LOW | MEDIUM | HIGH
  Dependencies affected: [list]

  [Terraform diff block]
  [Rollback block]

  Type "APPROVE <resource_id>" to proceed.

## Success Criteria
- [ ] Rollback generated before remediation presented
- [ ] Dependency list accurate
- [ ] No resource modified before APPROVE received
```

### `.kiro/tasks/003_rollback_spec.md` *(New)*

```markdown
# Task 003 — Rollback Execution

## Trigger
User types "ROLLBACK <resource_id>" within 24h of remediation.

## Steps
1. Locate rollback artifact for resource_id in ./rollbacks/
2. Validate rollback HCL (terraform validate)
3. Run terraform plan -target=<resource>
4. Display plan to user
5. Require confirmation: "CONFIRM ROLLBACK <resource_id>"
6. Execute. Record to audit log.

## Success Criteria
- [ ] Resource restored to pre-remediation state
- [ ] Audit log entry written
- [ ] Rollback artifact marked consumed
```

---

## Demo Flow (6 Minutes)

### 0:00–0:30 — The Hook

Say this verbatim (or close):

> *"Every cloud team has waste they know about and vulnerabilities they don't. The existing tools either run rigid rules that miss context, or require engineers to write scripts for every scenario. We built something different: an AI that reasons about your infrastructure the way a senior DevOps engineer would — checking dependencies, generating rollback before touching anything, and requiring your approval at every step."*

### 0:30–2:00 — Scan

- Dashboard loads. Click **Execute Cloud Audit.**
- **Show the agent progress feed** — "FinOps Auditor scanning… found 2 findings" then "SecOps Guard scanning… found 1 critical."
- Findings appear: `cache-prod-legacy` ($847/mo, 34 days idle) + `sg-0a3f` (0.0.0.0/0 on 6379).
- Point at the `.kiro/AGENTS.md` panel: *"The agents are guided by steering files — business rules the team can edit without touching code."*

### 2:00–3:30 — Remediation Plan

- Click **Generate Remediation Plan** on `cache-prod-legacy`.
- **Show the dependency check running** — agent queries what references this cluster. Result: nothing live depends on it.
- Side-by-side diff appears: current state (cluster running) vs. proposed (snapshot → delete).
- Rollback block shown alongside: *"The rollback is generated before we ask for approval — not after."*
- Point at the Terraform tags: `ManagedBy = "Kiro-Janitor"`. Every action is traceable.

### 3:30–4:30 — Approval + Execution

- Type `APPROVE cache-prod-legacy` into the approval field.
- Terraform executes. Progress feed shows each step.
- Audit log entry written. Savings counter updates: **+$847/month.**

### 4:30–5:30 — Rollback Demo *(The moment that wins)*

- Say: *"Now watch what happens if we made a mistake."*
- Type `ROLLBACK cache-prod-legacy`.
- Rollback plan appears. Confirm. Cluster restored from snapshot.
- Say: *"This is what makes autonomous cloud tooling safe enough to actually use."*

### 5:30–6:00 — .kiro/ Close

- Show the `.kiro/` directory in the repo.
- Say: *"Everything the agents know — their roles, their rules, their task specs — lives here. Reviewable, versionable, auditable. This is Kiro's spec-driven build in practice."*

---

## UI: What the Dashboard Must Show

**Do not build a generic dashboard. Build exactly these panels:**

```
┌─────────────────────────────────────────────────┐
│  🔍 Cloud Audit                    [Execute Audit]│
├──────────────────────┬──────────────────────────┤
│  AGENT ACTIVITY      │  FINDINGS                │
│                      │                          │
│  ● FinOps Auditor    │  ⚠ cache-prod-legacy     │
│    Scanning Cost     │    $847/mo | 34d idle    │
│    Explorer...       │    HIGH                  │
│                      │                          │
│  ● SecOps Guard      │  🔴 sg-0a3f...           │
│    Checking SGs...   │    0.0.0.0/0:6379        │
│                      │    CRITICAL              │
│  ✓ Remediation Arch  │                          │
│    Plan ready        │  [Generate Plan ▶]       │
├──────────────────────┴──────────────────────────┤
│  REMEDIATION DIFF          ROLLBACK PLAN        │
│  [current terraform]  vs   [rollback terraform] │
│                                                  │
│  Approval: [APPROVE cache-prod-legacy    ] [OK] │
├─────────────────────────────────────────────────┤
│  AUDIT LOG                                       │
│  14:23:01  Scan complete. 2 findings.            │
│  14:24:15  Plan generated. Awaiting approval.    │
│  14:24:44  APPROVED. Executing...               │
│  14:24:58  Done. Savings: $847/mo.              │
└─────────────────────────────────────────────────┘
```

**Agent Activity feed is the most important UI element.** It makes the multi-agent architecture *visible* and saves you from having to explain the architecture diagram. Judges see it working.

---

## Generated Terraform Samples

### Remediation HCL — ElastiCache Delete

```hcl
# Generated by Kiro Cloud Janitor — Task 002
# Resource: cache-prod-legacy
# Estimated savings: $847/month
# Approved by: <user> at <timestamp>

resource "aws_elasticache_snapshot" "cache_prod_legacy_pre_delete" {
  cluster_id             = "cache-prod-legacy"
  snapshot_name          = "kiro-janitor-pre-delete-${formatdate("YYYYMMDD", timestamp())}"

  tags = {
    ManagedBy    = "Kiro-Janitor"
    Environment  = var.environment
    RemediatedAt = timestamp()
    RollbackRef  = "rollbacks/cache-prod-legacy.tf"
  }
}

resource "null_resource" "cache_prod_legacy_delete" {
  depends_on = [aws_elasticache_snapshot.cache_prod_legacy_pre_delete]

  provisioner "local-exec" {
    command = "aws elasticache delete-cache-cluster --cache-cluster-id cache-prod-legacy"
  }
}
```

### Rollback HCL — ElastiCache Restore

```hcl
# ROLLBACK PLAN — cache-prod-legacy
# Generated alongside remediation. Do not modify.
# Trigger: ROLLBACK cache-prod-legacy

resource "aws_elasticache_cluster" "cache_prod_legacy_restored" {
  cluster_id           = "cache-prod-legacy"
  engine               = "redis"
  node_type            = "cache.t3.micro"
  num_cache_nodes      = 1
  snapshot_name        = "kiro-janitor-pre-delete-${var.snapshot_date}"
  security_group_ids   = [var.original_sg_id]

  tags = {
    ManagedBy   = "Kiro-Janitor"
    RestoredAt  = timestamp()
    RestoredBy  = "rollback"
  }
}
```

### Remediation HCL — Security Group Narrow

```hcl
# Generated by Kiro Cloud Janitor — Task 002
# Resource: sg-0a3f... (port 6379 exposure)
# Action: narrow CIDR from 0.0.0.0/0 to VPC CIDR only

data "aws_vpc" "current" {
  default = true
}

resource "aws_security_group_rule" "redis_ingress_vpc_only" {
  type              = "ingress"
  from_port         = 6379
  to_port           = 6379
  protocol          = "tcp"
  cidr_blocks       = [data.aws_vpc.current.cidr_block]
  security_group_id = "sg-0a3f..."
  description       = "Kiro-Janitor: narrowed from 0.0.0.0/0 — ${timestamp()}"
}

resource "aws_security_group_rule" "redis_ingress_public_remove" {
  type              = "ingress"
  from_port         = 6379
  to_port           = 6379
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = "sg-0a3f..."
  lifecycle {
    prevent_destroy = false
  }
}
```

---

## Tech Stack

| Layer       | Choice                                        | Reason                                |
| ----------- | --------------------------------------------- | ------------------------------------- |
| UI          | Streamlit                                     | Fast to build, judges can run locally |
| Agents      | Python + Kiro orchestration                   | Native Kiro integration               |
| MCP servers | AWS Cost MCP, AWS Security MCP, Terraform MCP | Required for Kiro Power User award    |
| Infra data  | JSON fixtures (simulated)                     | No live AWS creds needed for demo     |
| HCL output  | Generated strings + terraform validate        | Real Terraform, fake target           |
| Audit log   | Local SQLite or flat file                     | Simple, demoable, no infra dependency |

---

## Build Order (Hackathon Sequencing)

Build in this exact order. Each step is demoable on its own if time runs out.

```
Day 1 Morning   — .kiro/ directory + steering files (commit immediately)
Day 1 Afternoon — JSON fixtures for simulated AWS infra
Day 1 Evening   — FinOps agent: scan fixtures, produce findings.json

Day 2 Morning   — SecOps agent: scan fixtures, add findings
Day 2 Midday    — Remediation Architect: generate HCL from findings
Day 2 Afternoon — Approval gate + rollback generation
Day 2 Evening   — Streamlit UI: agent feed + diff panel + approval input

Day 3 Morning   — Rollback execution flow
Day 3 Midday    — Polish demo scenario end-to-end
Day 3 Afternoon — Rehearse 6-minute demo 3x. Cut anything that wobbles.
```

**If short on time, cut in this order:** rollback execution (keep rollback *display*), SecOps agent (keep FinOps only), Streamlit (use terminal output). Never cut the `.kiro/` files — judges look there first.

---

## Judging Criteria Map

| Award                  | What Judges Look For                                 | Where It's In This Build                  |
| ---------------------- | ---------------------------------------------------- | ----------------------------------------- |
| Best Spec-Driven Build | `.kiro/` directory quality, agents guided by specs | AGENTS.md, rules.md, 3 task specs         |
| Best Kiro Power User   | MCP integration depth, multi-agent orchestration     | 3 MCP servers, 3 specialist agents        |
| Most Practical         | Real-world applicability, production readiness       | Terraform tags, rollback, approval gate   |
| Best Demo              | Judges could see it in their org                     | "Ghost Cluster" scenario, rollback moment |

---

## What Not to Build

- **General-purpose rule engine** — scope creep, won't finish
- **Live AWS integration** — security risk, demo dependency, not needed
- **Beautiful UI beyond the 4 panels above** — judges care about the agents, not the CSS
- **More than 3 finding types** — depth beats breadth in a hackathon demo
- **CLI tool** — Streamlit dashboard is more demoable to a non-technical jud

# 🚀 Project Blueprint: Kiro Autonomous Cloud Janitor (FinOps & DevSecOps)

**Target Event:** AWS Kiro Buildfest 2026 Singapore

**Core Objective:** Win by maximizing Kiro's Spec-Driven AI capabilities, Model Context Protocol (MCP) servers, and agent orchestration.

---

# 💡 The Core Concept

The **Kiro Autonomous Cloud Janitor** is an enterprise-grade, multi-agent AI system that continuously audits, plans, and remediates AWS cloud waste and security vulnerabilities.

Unlike traditional, rigid automation tools that blindly run deletion scripts and break production, this system acts like an elite human DevOps engineer. Guided by Kiro steering files, it investigates resource dependencies, generates structural design plans, and writes production-ready Terraform (HCL) remediation and rollback blocks before touching live infrastructure.

---

# ⚡ Competitive Edge (Why This Wins)

When pitching to AWS Solutions Architecture judges, we'll directly compare our project against legacy automation tools such as Cloud Custodian and custom Bash scripts.

| Feature                       | Legacy Tools          | Kiro Autonomous Cloud Janitor                                               |
| ----------------------------- | --------------------- | --------------------------------------------------------------------------- |
| **Logic Engine**        | Hardcoded YAML rules  | Dynamic sequential-thinking AI agents                                       |
| **Context Awareness**   | No business awareness | Guided by`.kiro/` steering files                                          |
| **Safety**              | Blind deletions       | Spec-driven planning with rollback generation before execution              |
| **Dependency Handling** | Fails on dependencies | Automatically discovers and safely resolves dependencies before remediation |

---

# 🗺️ System Architecture

```text
                    +---------------------------------------+
                    |           User / Developer            |
                    +-------------------+-------------------+
                                        |
                               Prompt / UI Action
                                        |
                                        v
                    +-------------------+-------------------+
                    |          Kiro Spec Engine             |
                    |  Validates AGENTS.md & RULES.md       |
                    +-------------------+-------------------+
                                        |
          +-----------------------------+-----------------------------+
          |                             |                             |
          v                             v                             v
+----------------------+     +----------------------+     +----------------------+
| FinOps Scanner       |     | SecOps Auditor       |     | Remediation          |
| (Orchestrator)       |     | (Orchestrator)       |     | Architect            |
+----------------------+     +----------------------+     +----------------------+
          \                     |                     /
           \                    |                    /
            +-------------------+-------------------+
                                |
                                v
+--------------------------------------------------------------------------+
|                 MODEL CONTEXT PROTOCOL (MCP) LAYER                        |
|                                                                          |
|  AWS Security MCP  |  AWS Cost MCP  |  Terraform MCP                     |
|  Inspector/Config  | Cost Explorer  | HCL Validation                     |
+--------------------------------------------------------------------------+
                                |
                                v
                 +---------------------------------------+
                 |      Target AWS Infrastructure        |
                 |---------------------------------------|
                 | • Unattached EBS Volumes              |
                 | • Orphaned ElastiCache Clusters       |
                 | • Open Security Groups                |
                 +---------------------------------------+
```

---

# 📂 Required `.kiro/` Directory Structure

> **Critical Hackathon Rule**
>
> Commit the entire `.kiro/` directory into GitHub. Judges will inspect it for the **Best Spec-Driven Build** and **Best Kiro Power User** awards.

```
.kiro/
├── AGENTS.md
├── rules.md
└── tasks/
    └── 001_initial_scan_spec.md
```

---

# `.kiro/AGENTS.md`

```markdown
# Kiro Cloud Janitor Agent Steering Rules

## Agent Roles

### FinOps Auditor

- Detects financial waste
- Finds unattached EBS volumes
- Detects idle EC2 instances
- Detects abandoned ElastiCache clusters

### SecOps Guard

- Detects security vulnerabilities
- Flags open Security Groups (0.0.0.0/0)
- Checks storage encryption
- Reviews S3 security posture

### Remediation Architect

- Receives findings from auditors
- Produces remediation specifications
- Generates production-ready Terraform (HCL)

## Boundary Rules

- Never generate AWS credentials.
- Never expose plaintext secrets.
- Always generate a remediation specification before code.
- Require explicit user approval before modifying infrastructure.
```

---

# `.kiro/rules.md`

```markdown
# Infrastructure Remediation Standards

- Every Terraform change must include:

  ManagedBy = "Kiro-Janitor"
  Environment = "Dev" | "Prod"

- If an EBS volume has been unattached for more than 30 days:

  1. Generate a snapshot.
  2. Generate the deletion block.

- Security Group remediation must narrow CIDR ranges to internal VPC subnets instead of deleting Security Groups.
```

---

# `.kiro/tasks/001_initial_scan_spec.md`

```markdown
# Task 001 — MCP Ingestion & Baseline Discovery

## Objective

Implement a secure integration layer using the Model Context Protocol (MCP) to ingest active AWS infrastructure metrics.

## Success Metrics

- Parse simulated AWS infrastructure via MCP servers.
- Identify at least three categories of cloud waste or security issues.
- Generate a Markdown report locally.
- Never modify production infrastructure during discovery.
```

---

# 🛠️ Step-by-Step Demo Plan

## 1. User Interface

Build a lightweight **Streamlit** dashboard displaying current AWS resource health and optimization opportunities.

## 2. Execute Audit

Clicking **Execute Cloud Audit** launches the MCP orchestration pipeline.

## 3. Spec-Driven Showcase

Display a side-by-side Git diff comparing:

- Current infrastructure
- Generated Terraform remediation
- Rollback plan

## 4. Safe Deployment

Only after explicit user confirmation:

- Execute the generated Terraform.
- Record all actions.
- Preserve rollback artifacts.
- Ensure every action is traceable and reversible.
