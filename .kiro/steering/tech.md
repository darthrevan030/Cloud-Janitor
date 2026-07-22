# Technology Stack

## Package Management

**This project uses `uv` exclusively. Never use pip, pip-tools, poetry, or `python -m venv` directly — even for one-off installs, quick scripts, or "just testing something."**

| Instead of...                     | Use...                          |
|-----------------------------------|----------------------------------|
| `pip install <package>`             | `uv add <package>`              |
| `pip install --dev <package>`       | `uv add --dev <package>`        |
| `pip install -r requirements.txt`   | `uv sync`                       |
| `pip freeze`                       | `uv pip freeze` (read-only only)|
| `python -m venv .venv`             | not needed — `uv sync` creates/manages the env |
| `python script.py`                 | `uv run script.py`              |
| `pytest`                           | `uv run pytest`                 |
| upgrading dependencies             | `uv lock --upgrade`             |

- Never generate, reference, or restore a `requirements.txt` file.
- Never instruct the user to activate a virtualenv manually before running a command — prefix with `uv run` instead.
- The environment is Windows / Git Bash; when a literal interpreter path is needed, it is `.venv/Scripts/python.exe`, not `.venv/bin/python`.
- If a task doc, hook, or script anywhere in this repo contains a `pip install` or `requirements.txt` reference, treat it as stale and flag it — do not execute it as written.

## Language & Runtime

- Python (managed via `uv`; version pinned in `pyproject.toml` / `.python-version`)
- Dependency lockfile: `uv.lock` — always committed, always kept in sync with `pyproject.toml`

## Project Layout

- Current layout uses `src/cloud_janitor/` (src-layout). Package installs via `hatchling` with `packages = ["src/cloud_janitor"]`.
- All generated/runtime artifacts are consolidated under `output/`, with subdirectories:
  - `output/rollbacks/`
  - `output/remediations/`
  - `output/logs/`
  - `output/policies/`
- Runtime artifacts (`remediation_*.tf`, `rollback_*.tf`, `findings_store.json`, etc.) are **never** committed to git.

## Core Architecture

- Multi-agent system with an `Orchestrator` coordinating specialized agents for auditing (cost/security findings) and remediation (Terraform HCL generation).
- Human-in-the-loop approval gate before any remediation is executed — no agent applies infrastructure changes without explicit approval.
- Before checking whether a class is *used*, always verify it's actually *instantiated* (`grep "ClassName("`), not just imported (`grep "ClassName"`) — some agents are instantiated in the orchestrator and used indirectly by the dashboard via `execute_audit()`.
- The Orchestrator constructor takes `approver: str | None = None`. When `None`, identity is resolved via STS at call-time (fail-closed in real-AWS mode). When explicit, that value is used verbatim and STS is never called.
- Resolved actor is threaded as a LOCAL variable through `approve()`/`rollback()`/`_handle_confirm_rollback()` — never stored back on `self`. This is the concurrency-safety invariant.
- `_log_action()` and `_run_post_remediation_hook()` have optional `actor`/`actor_verified` kwargs — only the 4 approval/rollback stamp sites pass them; the ~28 other call sites use the default fallback to `self.approver`.

## AWS Interaction

- `boto3` for all AWS SDK calls.
- **LocalStack** (via Docker) is used for local AWS simulation. The `AWS_ENDPOINT_URL` environment variable toggles boto3 clients between LocalStack and real AWS — never hardcode an endpoint.
- **LocalStack detection** uses `"localhost" in endpoint or "127.0.0.1" in endpoint` (substring check on `AWS_ENDPOINT_URL`). Do NOT use `bool(os.environ.get("AWS_ENDPOINT_URL"))` — that would misclassify FIPS, VPC, and PrivateLink endpoints as LocalStack.
- **moto** (`@mock_aws`) is used for AWS service mocking in unit tests — do not stand up LocalStack for unit-level tests where moto suffices.
- **Docker socket isolation**: The base `docker-compose.yml` (ec2/s3 only) has no docker.sock mount. The Pro override (`docker-compose.pro.yml`) routes Docker API access through `docker-socket-proxy` with a minimal allowlist — never re-add a raw `/var/run/docker.sock` mount to the LocalStack service itself.
- **Post-hackathon**: Once moving to a hosted MCP endpoint or shared EC2, the LocalStack compose stack must not run on the same host that holds production AWS credentials or LLM API keys. Isolate it in a separate VM or container namespace.
- `_make_client(service, region, config=None)` in `aws_provider.py` accepts an optional `botocore.config.Config` for timeout/retry overrides — used by `core/identity.py` for the STS call with short timeouts.

## Terraform / Remediation Execution

- Terraform (via `tflocal` for LocalStack-backed runs) generates and applies HCL remediations.
- The `TF_CMD` environment variable controls which binary is invoked.
- The repo-local wrapper script `bin/tflocal` is the canonical way to simulate Terraform without real infrastructure — it short-circuits with exit 0 when `JANITOR_DRY_RUN=1`. Prefer this wrapper over setting `TF_CMD=echo` directly in new code paths.
- Never edit canonical fixture files in place — both the demo narrative and property-based tests depend on them staying fixed.
- The approval flow is: `init` → `plan -out=tfplan` → `show -json tfplan` (scope check) → `apply -auto-approve`. Both `approve()` and `_handle_confirm_rollback()` follow this sequence.
- `_check_plan_scope()` validates plans against `_SCOPE_ALLOWLIST` keyed by `(resource_type, category, flow)` — NOT just `(resource_type, category)`. Remediation and rollback HCL are structurally different (different resource addresses, opposite CIDR intent for security groups).
- When terraform is mocked/stubbed and `show -json` returns empty/unparseable output, the scope check is skipped gracefully (not errored).

## LLM Integration

- **OpenRouter** is the shared LLM gateway, accessed through `llm_client.py` (OpenAI-compatible API).
- Default free-tier models: `openai/gpt-oss-120b:free`, `google/gemma-4-31b-it:free`.
- Do not call other LLM providers directly — route all model calls through `llm_client.py` so the gateway abstraction stays consistent.
- `JANITOR_PRIVACY_MODE=strict` requires `JANITOR_LLM_RETENTION_POLICY=none` — the retention policy check fires BEFORE the free-router check in `get_client()`.
- All 6 LLM-calling agents (`explainer`, `anomaly_detector`, `tagger`, `policy_suggester`, `incident_policy_generator`, `drift_detector`) use the redact→delimit→rehydrate pattern:
  1. `redact(data)` before prompt construction (strips ARNs, account IDs, resource IDs)
  2. Wrap scrubbed data in `<untrusted_finding_data>` / `<untrusted_hcl>` delimiters
  3. `rehydrate(response, mapping)` after receiving LLM output
- When adding a new LLM-calling agent, follow this same pattern — it's enforced by `tests/test_agent_redaction.py`.

## Dashboard

- **Streamlit** powers the optional dashboard UI.
- Use `@st.fragment(run_every=1)` for polling live state rather than full-page reruns.

## Testing

- **Hypothesis** property-based tests are standard across the codebase — new logic touching parsing, classification, or data transforms should include property tests, not just example-based ones.
- **moto** (`@mock_aws`) for AWS service mocking in unit tests.
- Hold to a hostile-reviewer standard: no pass-by-default assertions, no tests that trivially pass regardless of implementation correctness.
- Run tests via `uv run pytest`, never bare `pytest`.

## CI / Repo Conventions

- GitHub: `darthrevan030/Cloud-Janitor`
- Branch protection is enabled on `master`; Dependabot alerts and push protection are enabled — do not propose disabling these.
- Machine-specific config (absolute paths, local venv locations, personal IDE settings) must never be committed.

## Anti-Patterns Discovered (Do NOT Do These)

- **Do NOT store resolved actor on `self`** in the Orchestrator. One call's identity must never leak into another concurrent call's audit stamp. Resolved actor is always a local variable, passed explicitly as a kwarg.
- **Do NOT make `actor` a required parameter** on `_log_action()` or `_run_post_remediation_hook()`. The Orchestrator has ~28 other call sites that have no resolved actor. Use an optional defaulting to `None` + fallback to `self.approver`.
- **Do NOT use `bool(os.environ.get("AWS_ENDPOINT_URL"))` for LocalStack detection.** This misclassifies FIPS/VPC/PrivateLink endpoints. Use `"localhost" in endpoint or "127.0.0.1" in endpoint`.
- **Do NOT recompute savings from `findings_store.json` during rollback.** That file is overwritten by every `execute_audit()` scan — the resource may no longer be present. Always use the ledger's own stored `monthly_savings_added`.
- **Do NOT key allowlists by `(resource_type, category)` alone.** Rollback and remediation templates for the same finding type generate structurally different HCL.
- **Do NOT compare against `"system"` to detect omitted approver.** A caller who explicitly passes `approver="system"` is indistinguishable. Use `is not None`.
- **Do NOT rely on STS exception-path timeout in sandbox mode.** Short-circuit before making the STS call when `is_real_aws` is False — otherwise every approve/rollback stalls for boto3's full default timeout in fixture/demo mode.
- **Do NOT use `-p no:xdist` when running the full suite in CI** (it disables parallelism). Only use it for debugging individual test files where ordering matters.
- **Do NOT assert `subprocess.run` call count without accounting for all subprocess steps** in the approval flow (init, plan, show, apply). When adding new subprocess steps, update all existing tests that assert call counts.
