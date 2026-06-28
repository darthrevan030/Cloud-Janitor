
# Cloud Janitor Agent Steering

## Project Layout

```
agents/          — All agent classes (FinOps, SecOps, Remediation, AI agents, SavingsTracker)
core/            — Shared infrastructure (llm_client.py)
hooks/           — Runtime pipeline hooks (pre/post-remediation.sh)
scripts/         — Dev tooling (compliance generator, git hooks, setup)
mcp_server/      — MCP protocol server + cloud provider backends
fixtures/        — Mock AWS data for dev/test
output/          — Runtime artifacts (findings, remediation.tf, logs, rollbacks, policies)
tests/           — pytest + hypothesis property tests
```

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

- Receives complete `output/findings_store.json` (both FinOps + SecOps findings)
- Runs dependency check before generating any HCL
- Produces in order: dependency report → remediation HCL → rollback HCL
- Never generates code without first completing dependency check
- All generated resources tagged: ManagedBy, Environment, RemediatedAt, RollbackRef

## Agent Sequencing

FinOps Auditor → SecOps Guard → Remediation Architect
No agent may skip its predecessor. Remediation Architect must not run
until `output/findings_store.json` contains entries from both prior agents.

## Hard Boundaries (Never Violate)

- Never generate AWS access keys or secrets
- Never expose plaintext credentials in any output
- Never modify infrastructure without explicit typed approval
- Always generate rollback HCL before surfacing approval prompt
- Rollback HCL must pass terraform validate before approval prompt appears
- Runtime hooks live in `hooks/` (not `scripts/`) — they are pipeline gates, not dev tools
- All LLM calls go through `core/llm_client.py` — never import openai directly in agents
