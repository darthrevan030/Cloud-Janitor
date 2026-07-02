# Cloud Janitor — Principal Engineer Codebase Audit

**Date:** 2026-07-01  
**Auditor:** Principal Engineer Review  
**Scope:** Full end-to-end codebase audit across architecture, scalability, reliability, security, business logic, and operability.

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Architecture & Structure](#architecture--structure)
3. [Scalability & Performance](#scalability--performance)
4. [Reliability & Resilience](#reliability--resilience)
5. [Security & Trust](#security--trust)
6. [Business Logic](#business-logic)
7. [Operability & Lifecycle](#operability--lifecycle)
8. [Summary of Findings](#summary-of-findings)

---

## Executive Summary

Cloud Janitor is a well-architected multi-agent system that demonstrates strong software engineering practices for a buildathon project. The codebase implements 17 agents, a FastMCP server with 10 tools, a polished Streamlit dashboard, and a full Terraform remediation pipeline with human-in-the-loop approval. The design exhibits clear separation of concerns, consistent error handling patterns, and meaningful test coverage (44 test files including property-based tests with Hypothesis).

**Key Strengths:**
- Excellent defensive programming: every AI agent returns safe defaults on failure — the pipeline never crashes due to an LLM outage
- Clean provider abstraction (CloudProvider ABC) enabling fixture/AWS/GCP/Azure backends
- Well-designed approval gate with deliberate friction (exact-match, 3-attempt lockout, 2-step rollback)
- Strong test suite using both conventional unit tests and property-based testing
- Atomic file operations where they matter (DriftDetector uses filelock + tmp-rename)
- Comprehensive documentation (README, per-directory READMEs, SPEC_COMPLIANCE tracking)

**Key Weaknesses:**
- No build system or CLI entry point — cannot be `pip install`-ed
- No retry/rate-limiting on LLM calls — a single 429 from OpenRouter cascades to a pipeline failure
- File-based state (JSON on disk) won't survive concurrent access at scale
- Subprocess calls (`tflocal`, `bash`) introduce platform-dependent failure modes and potential injection vectors
- The Streamlit app directly accesses orchestrator private methods, creating tight coupling
- Missing structured logging — agents use `print()` to stderr

**Overall Assessment:** This is a well-engineered demo/prototype with production-quality patterns in critical paths (approval gate, error handling, data validation). It requires specific hardening around I/O, concurrency, and packaging to become a distributable production tool. The architecture is sound enough that these gaps can be addressed incrementally without major rewrites.

---

## Architecture & Structure

### Strengths

**Clear Agent Boundaries and Single Responsibility**

Each agent has a single, well-defined job. `FinOpsAuditor` detects cost waste. `SecOpsGuard` detects security vulnerabilities. `RemediationArchitect` generates Terraform HCL. They communicate through a shared `findings_store.json` schema — a simple but effective data contract.

The strict sequencing (FinOps → SecOps → Remediation) is enforced both by the orchestrator and by the findings store validation (the orchestrator won't proceed to remediation unless both agents have written entries).

**Provider Abstraction**

The `CloudProvider` ABC in `mcp_server/backends/__init__.py` is clean and minimal — three abstract methods, well-documented return schemas. Swapping fixture mode for live AWS requires only changing an environment variable. This is the right level of abstraction for a multi-cloud tool.

**Consistent Internal API Patterns**

Every AI agent follows the same pattern:
1. Import `get_client, DEFAULT_MODEL` from `core/llm_client.py`
2. Accept inputs, validate, build prompt
3. Call LLM, parse JSON response
4. Validate output against a known schema
5. Return safe default on any exception

This consistency makes the codebase predictable and easy to extend.

**MCP Tool Layer is Thin**

The MCP server (`aws_janitor_mcp.py`) is a thin delegation layer — each `@mcp.tool()` function instantiates the agent and delegates. This keeps the MCP transport concerns separate from business logic.

### Weaknesses

**Flat Project Layout Prevents Packaging** — Severity: HIGH

Files: `orchestrator.py`, `scheduler.py`, `app.py` (project root)

The project uses a flat layout with `agents/`, `core/`, `mcp_server/` as packages but `orchestrator.py` and `scheduler.py` at the root. There's no `src/` directory, no `[build-system]` in `pyproject.toml`, and no `[project.scripts]` entry point. This means `pip install .` won't produce a working package.

**Remediation:** Add `[build-system]` with hatchling, restructure under `src/cloud_janitor/`, and add CLI entry points.

---

**Streamlit App Accesses Private Methods** — Severity: MEDIUM

File: `app.py` (lines accessing `orch._finops.scan()`, `orch._secops.scan()`, `orch._validate_findings_store()`, etc.)

The dashboard directly calls internal orchestrator methods (prefixed with `_`) to get per-agent control over the UI pipeline animation. This couples the UI to orchestrator internals and will break if the orchestrator is refactored.

**Remediation:** Expose a structured API on the orchestrator (e.g., `execute_audit_streaming()` that yields agent status updates), or use event callbacks.

---

**sys.path Manipulation in Agent Modules** — Severity: LOW

Files: `agents/secops_guard.py`, `agents/finops_auditor.py`, `agents/remediation_architect.py`

```python
sys.path.insert(0, str(Path(__file__).parent.parent))
```

This is a workaround for the lack of proper package installation. It works but makes imports fragile and IDE tooling inconsistent.

**Remediation:** Proper package structure with editable install (`pip install -e .`) eliminates the need for path manipulation.

---

**Module-Level Provider Instantiation** — Severity: MEDIUM

File: `mcp_server/aws_janitor_mcp.py`

```python
_provider: CloudProvider = _load_provider()
```

The provider is instantiated at module import time. If `JANITOR_BACKEND=aws` and boto3 credentials are misconfigured, the import fails — taking down the entire MCP server even if you just wanted to import a function for testing.

**Remediation:** Use lazy initialization (a property or function that caches on first call), or catch and defer initialization errors.

---

## Scalability & Performance

### Strengths

**Scan History Rotation**

The `DriftDetector` caps `scan_history.json` at 30 snapshots with FIFO rotation. This prevents unbounded growth without manual intervention.

**Concurrent Multi-Account Execution**

`MultiAccountOrchestrator` uses `ThreadPoolExecutor(max_workers=5)` with per-account timeouts (300s). Each account gets an isolated findings store, preventing cross-contamination.

**Scheduler Overlap Prevention**

`JanitorScheduler` uses a threading `Event` to skip overlapping triggers. If a scan is already running, the next cron trigger is dropped with a warning log.

### Weaknesses

**File-Based State is a Concurrency Bottleneck** — Severity: HIGH

Files: `findings_store.json`, `savings_ledger.json`, `scan_history.json`

All agent state lives in JSON files on disk. The `DriftDetector` uses `filelock` for thread safety, but no other component does. If the Streamlit app reads `findings_store.json` while the orchestrator is writing it, you get a partial read or `JSONDecodeError`. The `SavingsTracker` has no locking at all — concurrent approval calls could corrupt the ledger.

**Remediation:** Add `filelock` to `SavingsTracker` and `FinOpsAuditor`. For a production system, consider SQLite or a proper database for state that's written and read concurrently.

---

**LLM Calls Are Sequential and Unbounded** — Severity: MEDIUM

Files: All AI agents (`query_interpreter.py`, `explainer.py`, `anomaly_detector.py`, etc.)

Each LLM call has no timeout, no retry, and no rate limiting. A slow OpenRouter response blocks the entire pipeline. The `RemediationExplainer` is called per-finding in the UI — with 10 findings, that's 10 sequential LLM calls before the panel renders.

**Remediation:** Add `httpx` timeouts to the OpenAI client, implement exponential backoff with `tenacity`, and consider parallelizing independent LLM calls (e.g., explanation generation per finding).

---

**Remediation Architect Checks Dependencies Sequentially** — Severity: LOW

File: `agents/remediation_architect.py` (the `plan()` method)

For each finding, `check_dependencies()` is called synchronously via the fixture/AWS provider. With live AWS and 50+ findings, this becomes a serial bottleneck.

**Remediation:** Use `asyncio.gather()` or `ThreadPoolExecutor` to parallelize dependency checks.

---

**Large Streamlit CSS Block Loaded on Every Render** — Severity: LOW

File: `app.py` (lines 1-300 approximately)

A ~300-line CSS block is injected via `st.markdown(unsafe_allow_html=True)` on every page render. Streamlit caches this due to static content hashing, but it bloats the initial HTML payload.

**Remediation:** Move CSS to a static file and reference it, or use `st.cache_data` with a hash to minimize re-injection.

---

## Reliability & Resilience

### Strengths

**Never-Raise Guarantee Across All AI Agents**

Every agent wraps its entire execution in a try/except that returns a safe default. This is validated by property tests (Property 1 and Property 2 in the task spec). The system gracefully degrades when OpenRouter is down — you lose AI explanations but the core FinOps/SecOps pipeline still runs.

**Atomic File Writes in DriftDetector**

```python
self._tmp_path.write_text(...)
self._tmp_path.replace(self._history_path)
```

Write-to-tmp-then-rename prevents half-written files on crash. Combined with `filelock`, this is the correct pattern for concurrent file access.

**Post-Remediation Hook is Non-Blocking**

The orchestrator catches all exceptions from the post-remediation hook and continues. The approval succeeds even if the audit log write fails.

**Schema Validation Module**

`agents/schema_validator.py` provides full structural validation of `findings_store.json` including cross-referencing summary counts against actual data. This is defensive programming done right.

### Weaknesses

**No Retry on Subprocess Calls** — Severity: HIGH

Files: `orchestrator.py` (approval flow), `hooks/pre-remediation.sh`

`tflocal apply` is called once with a 120-second timeout. If LocalStack is briefly unavailable or the network hiccups, the approval fails permanently. There's no retry, no circuit breaker, and no way to re-attempt without resetting the approval gate.

**Remediation:** Add 1-2 retries with exponential backoff on `tflocal apply`. Separate transient failures (timeout, network) from permanent failures (invalid HCL).

---

**Approval Gate Lock is Unrecoverable via UI** — Severity: MEDIUM

File: `agents/approval_gate.py`

After 3 failed attempts, the gate locks permanently. The only recovery is calling `gate.reset()` in code — there's no dashboard button, no admin override, no time-based unlock. In a demo or production setting, a typo during a presentation locks you out permanently.

**Remediation:** Add a reset mechanism in the UI (perhaps behind a confirmation dialog), or implement a time-based cooldown (e.g., unlock after 5 minutes).

---

**FinOps Overwrites findings_store.json** — Severity: MEDIUM

File: `agents/finops_auditor.py` (`_write_findings_store` method)

FinOps writes a fresh `findings_store.json` on every scan. If the orchestrator crashes between FinOps and SecOps, you lose the previous scan's data. There's no backup or journaling.

**Remediation:** Write to a timestamped file and maintain a symlink to `latest`, or use append-only semantics like SecOps does.

---

**No Health Check for LLM Availability** — Severity: LOW

File: `core/llm_client.py`

`get_client()` validates that `OPENROUTER_API_KEY` is set but doesn't verify the key is valid or that OpenRouter is reachable. The first failure happens deep in agent execution.

**Remediation:** Add a `verify_connection()` method that makes a lightweight API call at startup. Surface the result in the dashboard status.

---

## Security & Trust

### Strengths

**Approval Gate is Deliberately Strict**

The command parser rejects:
- Leading/trailing whitespace
- Case variations (`approve` vs `APPROVE`)
- Double spaces between command and resource ID
- Resource ID mismatches

This strict parsing prevents accidental approvals and social engineering ("just type approve").

**Two-Step Rollback Prevents Accidental Reverts**

`ROLLBACK <id>` → `CONFIRM ROLLBACK <id>` is a conscious UX decision. You can't accidentally roll back production changes.

**LLM Output is Never Rendered with unsafe_allow_html**

In `app.py`, LLM-generated text (explanations, narratives, suggestions) is rendered via `st.markdown(f"**Risk:** {_esc(explanation...)}")` with explicit HTML escaping. This prevents XSS from adversarial LLM outputs.

**Secrets Management**

`.env` is gitignored, `.env.example` contains only placeholder values, and `OPENROUTER_API_KEY` is never stored in `st.session_state`.

### Weaknesses

**Shell Injection via Resource IDs in Subprocess Calls** — Severity: HIGH

File: `agents/remediation_architect.py`

```python
f'command = "aws ec2 delete-volume --volume-id {resource_id}"\n'
```

Resource IDs from fixture data are interpolated directly into shell commands within generated HCL. If a resource ID contained shell metacharacters (e.g., `vol-abc; rm -rf /`), the generated HCL would contain an injection payload. In fixture mode this is theoretical, but with live AWS data or user-controlled inputs, it's exploitable.

File: `orchestrator.py`

```python
subprocess.run([TF_CMD, "apply", "-auto-approve"], ...)
```

This specific call is safe (list-form subprocess), but the hook calls pass through `bash`:

```python
subprocess.run(["bash", hook_path, remediation_path, rollback_path], ...)
```

If file paths contain spaces or special characters, behavior is undefined.

**Remediation:** Validate resource IDs against a strict pattern (`^[a-zA-Z0-9\-_.]+$`) before interpolating into any shell context. Use `shlex.quote()` for path arguments passed to bash.

---

**accounts.json Contains Sensitive IAM Role ARNs** — Severity: MEDIUM

File: `accounts.json`

This file contains IAM role ARNs and account IDs. It's not in `.gitignore`. While role ARNs aren't secrets per se, they reveal your AWS account structure and cross-account trust relationships.

**Remediation:** Add `accounts.json` to `.gitignore` (keep `accounts.json.example`). The file already ships example data but it's checked into the repo.

---

**No Input Validation on MCP Tool Parameters** — Severity: MEDIUM

File: `mcp_server/aws_janitor_mcp.py`

MCP tool functions accept arbitrary `dict` and `list` parameters from external MCP clients. There's no schema validation at the MCP boundary — validation happens downstream in individual agents. A malicious MCP client could pass deeply nested dicts designed to consume memory or trigger unexpected behavior.

**Remediation:** Add pydantic models or explicit depth/size validation at the MCP tool boundary before delegating to agents.

---

**Dependency Versions Use Minimum Bounds Only** — Severity: MEDIUM

File: `requirements.txt`

```
openai>=2.44.0
boto3>=1.34.0
streamlit>=1.45.0
```

No upper bounds or pinned versions. A future breaking change in any dependency will silently break the build. There's no `requirements.lock` or equivalent.

**Remediation:** Use `pip-compile` (pip-tools) to generate a locked `requirements.txt` from `pyproject.toml`. Or add upper bounds for critical deps.

---

**Docker Socket Mounted in docker-compose.yml** — Severity: LOW

File: `docker-compose.yml`

```yaml
volumes:
  - "/var/run/docker.sock:/var/run/docker.sock"
```

Mounting the Docker socket gives the LocalStack container full control over the host's Docker daemon. This is required for some LocalStack features but is a privilege escalation vector in shared environments.

**Remediation:** Document the security implication. For CI/CD, consider running LocalStack without socket access or in a VM.

---

## Business Logic

### Strengths

**Severity Classification is Correct and Documented**

FinOps:
- ElastiCache idle >30d = HIGH (expensive, easy to forget)
- EBS unattached >30d = MEDIUM (cheaper but still waste)

SecOps:
- Database/cache ports open to 0.0.0.0/0 = CRITICAL
- SSH open to 0.0.0.0/0 = HIGH
- Unencrypted storage = HIGH

These classifications align with industry standard risk frameworks.

**Dependency Blocking is Conservative**

The Remediation Architect blocks remediation for any resource with dependents. This is the right default — it's better to leave a resource running than to break a dependency chain. The blocking reason is surfaced in both the audit trail and the UI.

**HCL Generation Handles Edge Cases Correctly**

- ElastiCache encryption can't be enabled in-place → generates a commented recommendation, not a broken `resource` block
- EBS snapshot is created with `depends_on` before deletion → prevents data loss race condition
- Security groups are never deleted — only narrowed from `0.0.0.0/0` to VPC CIDR

**Cross-Account Duplicate Detection**

The `MultiAccountOrchestrator` identifies findings that appear across multiple accounts (same `resource_type + check_type` pair). This is useful for identifying systemic issues vs. isolated ones.

### Weaknesses

**FinOps Auditor Filters After Fetching All Data** — Severity: LOW

File: `agents/finops_auditor.py`

```python
all_cost_data = get_cost_data(resource_type=None, min_idle_days=0)
```

The auditor fetches ALL resources (min_idle_days=0) so it can emit skip events, then filters for >30 days. In fixture mode this is fine (4 resources), but with live AWS and thousands of volumes, this is wasteful. The MCP tool supports `min_idle_days` filtering at the source.

**Remediation:** Fetch with `min_idle_days=30` for the actual findings, and optionally fetch the full set only when reasoning log verbosity is enabled.

---

**NL Audit Doesn't Apply Filters to Remediation Plans** — Severity: MEDIUM

File: `orchestrator.py` (`execute_natural_language_audit`)

After interpreting the query and filtering findings, the method calls `self._architect.plan()` — which reads from `findings_store.json` on disk (the unfiltered full scan). The plans generated don't respect the NL query filters.

**Remediation:** Pass the filtered findings directly to `self._architect.plan(findings=filtered_findings)` instead of calling `plan()` with no arguments.

---

**Savings Tracker Only Records on Approval — Not on Actual Resource Deletion** — Severity: LOW

File: `orchestrator.py` (approve method)

Savings are recorded after `tflocal apply` returns success. But `tflocal apply` against LocalStack doesn't actually verify the resource was deleted — it just applies the Terraform plan. If the plan had a `null_resource` provisioner that failed silently, savings would be recorded for a resource that still exists.

**Remediation:** For production, verify resource deletion via a post-apply check (e.g., call `check_dependencies` or `get_cost_data` to confirm the resource is gone).

---

**README Claims AWS Provider is a Stub (It's Not)** — Severity: LOW

File: `README.md`

> **AWS mode** — Points at a real AWS account via boto3. Currently a stub — NotImplementedError is raised on all methods.

This is factually wrong. `mcp_server/backends/aws_provider.py` is fully implemented (~350 lines of live boto3 queries). The README contradicts the code.

**Remediation:** Update the README to reflect reality. The existing production-readiness audit already flagged this.

---

## Operability & Lifecycle

### Strengths

**Comprehensive README**

The README is excellent — it covers quick start, architecture, every agent, environment variables, the demo scenario, testing, and extension guides. It's written for developers who want to understand and contribute.

**Dependabot Configuration**

`.github/dependabot.yml` monitors both `pip` and `terraform` dependencies weekly. This catches security vulnerabilities in dependencies automatically.

**Makefile for Demo**

`make demo` is a single command that starts LocalStack, waits for readiness, and launches the dashboard. This eliminates "it works on my machine" issues for demos.

**Reasoning Logger Provides Agent Observability**

The `ReasoningLogger` emits structured JSONL with agent name, event type, resource ID, and message. The dashboard renders this in real time. This is extremely useful for debugging agent decisions.

**SPEC_COMPLIANCE.md Tracks Implementation Progress**

A generated compliance report maps every spec task to its implementation status. This level of traceability is unusual for a buildathon project and demonstrates engineering discipline.

### Weaknesses

**No Structured Logging** — Severity: HIGH

Files: All agents, `orchestrator.py`

Agents use `print(..., file=sys.stderr)` for error reporting. There's no log levels, no structured output, no correlation IDs, and no way to route logs to external systems (CloudWatch, Datadog, etc.).

The `ReasoningLogger` is a purpose-built file writer — it's not a general logging framework and doesn't support the standard Python `logging` module.

**Remediation:** Replace `print()` calls with Python's `logging` module. Configure formatters (JSON for production, human-readable for development). Add a correlation ID (scan_id) to every log entry for distributed tracing.

---

**No CI/CD Pipeline** — Severity: HIGH

File: `.github/` (only has `dependabot.yml`)

There's no GitHub Actions workflow. PRs are not validated by automated tests, type checking, or linting. A broken commit can reach `main` without detection.

**Remediation:** Add a CI workflow with: `ruff` lint, `mypy` type check, `pytest` (full suite), and package build verification. Gate merges on CI pass.

---

**No Type Checking** — Severity: MEDIUM

File: `pyproject.toml` (no `[tool.mypy]` section)

The codebase uses type annotations extensively (PEP 604 unions, `Path | None`, typed dicts) but never validates them. There's no `mypy` configuration, no `py.typed` marker, and no type checking in the workflow.

**Remediation:** Add `mypy` to dev dependencies, create a `mypy.ini` or `pyproject.toml` section, and fix any type errors. This catches bugs like passing `None` where a `str` is expected.

---

**Runtime Output Directories Created Implicitly** — Severity: LOW

Files: `orchestrator.py`, `agents/reasoning_logger.py`, `agents/savings_tracker.py`

Multiple modules call `.mkdir(parents=True, exist_ok=True)` on output directories during initialization. If the filesystem is read-only or permissions are wrong, the error surfaces deep in execution rather than at startup.

**Remediation:** Consolidate directory creation into a single initialization function called at app startup. Fail fast with a clear error if directories can't be created.

---

**`hypothesis` is a Production Dependency** — Severity: LOW

File: `pyproject.toml`

```toml
dependencies = [
    ...
    "hypothesis>=6.155.7",
    ...
]
```

Hypothesis is a test framework. It should be in the `[dependency-groups] dev` section. Users who `pip install` the package get an unnecessary 10MB+ testing framework.

**Remediation:** Move `hypothesis` to the `dev` dependency group alongside `pytest` and `moto`.

---

**`anthropic` Package is Declared but Never Used** — Severity: LOW

File: `pyproject.toml`

```toml
"anthropic>=0.25.0",
```

No file in the codebase imports `anthropic`. All LLM calls go through the `openai` SDK pointed at OpenRouter.

**Remediation:** Remove from dependencies.

---

## Summary of Findings

### By Severity

| Severity | Count | Key Items |
|----------|-------|-----------|
| HIGH | 6 | Shell injection in HCL generation, no CI/CD, file-based concurrency issues, no structured logging, no retry on subprocess/LLM calls, no build system |
| MEDIUM | 8 | NL audit doesn't filter plans, no input validation at MCP boundary, accounts.json not gitignored, approval gate unrecoverable from UI, Streamlit accesses private methods, unpinned deps, LLM calls are sequential/unbounded, no type checking |
| LOW | 8 | sys.path manipulation, CSS injection overhead, FinOps fetches all data, README inaccuracy, hypothesis in prod deps, anthropic dead dep, implicit directory creation, module-level provider init |

### Top 5 Recommendations (Prioritized by Impact)

1. **Add CI/CD with lint + type check + test** — Prevents regressions, catches bugs before merge, establishes quality gate. Effort: 2-3 hours.

2. **Add retry + timeout to LLM calls** — The entire AI feature set fails silently on a single 429 or timeout. Add `tenacity` retry with exponential backoff and a 30s timeout. Effort: 1 hour.

3. **Validate resource IDs before shell interpolation** — The HCL generation templates interpolate user-controlled strings into shell commands. Add a strict regex validation (`^[a-zA-Z0-9\-_.]+$`) at the remediation architect boundary. Effort: 30 minutes.

4. **Add filelock to SavingsTracker and FinOpsAuditor** — Concurrent dashboard reads while the orchestrator writes will corrupt JSON files. The pattern already exists in DriftDetector. Effort: 1 hour.

5. **Add [build-system] and CLI entry point** — Without this, the project can't be distributed or installed cleanly. The existing production-readiness audit details the exact changes needed. Effort: 2-3 hours.

---

*End of audit.*
