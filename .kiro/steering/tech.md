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

- Current layout is **flat** (no `src/` directory). Do not introduce a `src/` layout, package renames, or import-path changes unless explicitly working the src-layout migration task — that work is deliberately deferred and must not be mixed into unrelated changes, to avoid import-path conflicts with in-flight work.
- All generated/runtime artifacts are consolidated under `output/`, with subdirectories:
  - `output/rollbacks/`
  - `output/logs/`
  - `output/policies/`
- Runtime artifacts (`remediation_*.tf`, `rollback_*.tf`, `findings_store.json`, etc.) are **never** committed to git.

## Core Architecture

- Multi-agent system with an `Orchestrator` coordinating specialized agents for auditing (cost/security findings) and remediation (Terraform HCL generation).
- Human-in-the-loop approval gate before any remediation is executed — no agent applies infrastructure changes without explicit approval.
- Before checking whether a class is *used*, always verify it's actually *instantiated* (`grep "ClassName("`), not just imported (`grep "ClassName"`) — several agents are imported in `app.py` but never wired in.

## AWS Interaction

- `boto3` for all AWS SDK calls.
- **LocalStack** (via Docker) is used for local AWS simulation. The `AWS_ENDPOINT_URL` environment variable toggles boto3 clients between LocalStack and real AWS — never hardcode an endpoint.
- **moto** (`@mock_aws`) is used for AWS service mocking in unit tests — do not stand up LocalStack for unit-level tests where moto suffices.

## Terraform / Remediation Execution

- Terraform (via `tflocal` for LocalStack-backed runs) generates and applies HCL remediations.
- The `TF_CMD` environment variable controls which binary is invoked.
- The repo-local wrapper script `bin/tflocal` is the canonical way to simulate Terraform without real infrastructure — it short-circuits with exit 0 when `JANITOR_DRY_RUN=1`. Prefer this wrapper over setting `TF_CMD=echo` directly in new code paths.
- Never edit canonical fixture files in place — both the demo narrative and property-based tests depend on them staying fixed.

## LLM Integration

- **OpenRouter** is the shared LLM gateway, accessed through `llm_client.py` (OpenAI-compatible API).
- Default free-tier models: `openai/gpt-oss-120b:free`, `google/gemma-4-31b-it:free`.
- Do not call other LLM providers directly — route all model calls through `llm_client.py` so the gateway abstraction stays consistent.

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
