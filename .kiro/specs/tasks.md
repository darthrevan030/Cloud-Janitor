# Implementation Plan

## Overview

Cloud Janitor implementation progresses through seven phases: foundation setup (directory structure, fixtures), MCP server with tool endpoints, three specialized agents (FinOps Auditor, SecOps Guard, Remediation Architect), Kiro hooks for validation gates, approval/execution logic, Streamlit UI, and final polish with demo preparation. Each phase builds on the previous, with fixture data enabling development without live AWS credentials.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3"] },
    { "id": 1, "tasks": ["1.4", "1.5"] },
    { "id": 2, "tasks": ["2.1", "2.2", "2.3", "2.4", "2.5"] },
    { "id": 3, "tasks": ["3.1", "3.2", "3.5"] },
    { "id": 4, "tasks": ["3.3", "3.4"] },
    { "id": 5, "tasks": ["4.1", "4.2", "5.1", "5.2", "5.3", "5.4"] },
    { "id": 6, "tasks": ["4.3", "6.1", "6.2", "6.3", "6.4", "6.5"] },
    { "id": 7, "tasks": ["7.1", "7.2", "7.3", "7.4"] },
    { "id": 8, "tasks": ["7.5", "7.6"] }
  ]
}
```

## Tasks

### Phase 1: Foundation

- [x] 1. Create .kiro/ directory structure and commit
- [x] 2. Write requirements.md with all user stories
- [x] 3. Write design.md with architecture + data flow
- [x] 4. Write fixture JSON for Cost Explorer (3 resources, 2 flaggable)
- [x] 5. Write fixture JSON for Config/Inspector (2 security findings)

### Phase 2: MCP Server

- [x] 1. Implement aws_janitor_mcp.py with MCP protocol
- [x] 2. Implement get_cost_data() → reads Cost Explorer fixture
- [x] 3. Implement get_security_data() → reads Inspector fixture
- [x] 4. Implement validate_hcl() → shells to terraform validate
- [x] 5. Write mcp_server/README.md

### Phase 3: Agents

- [x] 1. FinOps Auditor — calls MCP, produces findings[], writes findings_store.json
- [x] 2. SecOps Guard — calls MCP, appends to findings_store.json
- [x] 3. Remediation Architect — reads findings, dependency check, generates HCL
- [x] 4. Rollback HCL generation (alongside remediation, not after)
- [x] 5. findings_store.json schema validation

### Phase 4: Hooks

- [x] 1. pre-remediation.sh — terraform validate gate
- [x] 2. post-remediation.sh — audit.log append
- [x] 3. Wire hooks into orchestrator call sequence

### Phase 5: Approval + Execution

- [x] 1. Approval gate — parse "APPROVE \<id\>", reject malformed input
- [x] 2. Rollback gate — parse "ROLLBACK \<id\>" + "CONFIRM ROLLBACK \<id\>"
- [x] 3. Audit log writer (append-only)
- [x] 4. Error states: dependency found, validate fails, malformed approval

### Phase 6: UI

- [x] 1. Streamlit layout — 4 panels (agent feed, findings, diff, audit log)
- [x] 2. Agent activity feed with live status dots
- [x] 3. Side-by-side diff view (remediation HCL vs rollback HCL)
- [x] 4. Approval input field + confirmation display
- [x] 5. Savings counter

### Phase 7: Polish + Demo

- [ ] 1. End-to-end Ghost Cluster scenario run (no errors)
- [ ] 2. Rollback flow run (no errors)
- [ ] 3. Error state test: approval typo rejected gracefully
- [ ] 4. Rehearse 6-min demo script 3x
- [ ] 5. Record demo video for Devpost submission
- [ ] 6. Write Devpost submission copy

## Notes

- Phase 1 tasks 1-3 are spec/foundation tasks; task 4+ are implementation tasks
- Each phase builds on the previous — fixture data must exist before MCP server can read it
- The MCP server wraps fixture data behind a genuine MCP protocol interface for demo authenticity
- Agents execute sequentially: FinOps → SecOps → Remediation (never in parallel)
- All generated Terraform HCL must include required tags (ManagedBy, Environment, RemediatedAt, RollbackRef)
- Rollback HCL is always generated alongside remediation HCL, never after
- Property-based tests use `hypothesis` (Python) with minimum 100 iterations per property
- Demo tasks (Phase 7, tasks 4-6) are non-coding tasks retained for project completeness
