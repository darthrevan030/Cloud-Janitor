
# Cloud Janitor Agent Steering

## Project Layout

```
agents/          — All agent classes (FinOps, SecOps, Remediation, AI agents, SavingsTracker)
core/            — Shared infrastructure (llm_client.py, identity.py, redaction.py, paths.py)
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
- All LLM-facing data passes through `core/redaction.py` before prompt construction — never send raw ARNs/account IDs/resource IDs to an LLM
- All untrusted data in prompts is wrapped in `<untrusted_finding_data>` or `<untrusted_hcl>` delimiters — never inline raw cloud-controlled text (e.g. tags) into prompt instructions
- Identity resolution (`core/identity.py`) is fail-closed in real-AWS mode — if STS can't confirm the caller, no terraform operation proceeds
- `SavingsTracker.record_rollback()` negates the ledger's own stored amount — never recompute from the live `findings_store.json` which may have been overwritten by a subsequent scan
- The `_SCOPE_ALLOWLIST` must be keyed by `(resource_type, category, flow)` — never by `(resource_type, category)` alone, because remediation and rollback HCL have different resource addresses and opposite CIDR intent
