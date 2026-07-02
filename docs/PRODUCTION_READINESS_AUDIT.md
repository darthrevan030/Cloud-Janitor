# Production Readiness Audit — Cloud Janitor

**Branch:** `feat/audit-remediation`  
**Date:** 2026-07-02  
**Scope:** Security, Reliability, Observability, Portability, Configuration, Data Integrity

---

## 1. Security

| # | File : Line | Severity | Risk | Recommended Fix |
|---|------------|----------|------|----------------|
| S1 | `agents/remediation_architect.py:298` | **High** | Resource IDs are interpolated unsanitized into `local-exec` shell commands (`"aws ec2 delete-volume --volume-id {resource_id}"`). A crafted resource ID containing shell metacharacters would execute arbitrary commands when `terraform apply` runs. | Validate resource_id against `^[a-zA-Z0-9\-_.:/]+$` before interpolation, or use HCL `templatefile()` with proper escaping. |
| S2 | `core/llm_client.py:26-28` | **Medium** | No timeout on the OpenAI client — a hung TLS handshake to OpenRouter blocks the calling thread indefinitely. | Pass `timeout=httpx.Timeout(30.0)` to `openai.OpenAI(...)`. |
| S3 | `mcp_server/aws_janitor_mcp.py:all tools` | **Medium** | MCP tool functions accept arbitrary `dict`/`list` from external MCP clients with no schema validation at the boundary. A malicious client could send deeply nested structures to exhaust memory. | Add pydantic model validation or max-depth checks at MCP tool entry points. |
| S4 | `accounts.json` (tracked in git) | **Medium** | Contains IAM role ARNs and account IDs. While not secrets, they reveal AWS account topology. File is not gitignored. | Add `accounts.json` to `.gitignore`; keep `accounts.json.example` with dummy values. |
| S5 | `mcp_server/aws_janitor_mcp.py:1` (module-level) | **Low** | MCP server has no authentication — any stdio client can invoke all tools including `policy_from_incident` which writes files to disk. | Document that MCP server is intended for local use only; add optional token-based auth for networked deployments. |
| S6 | `app.py:937,940` | **Low** | Diff HTML rendered with `unsafe_allow_html=True` uses `_esc()` for content, but `render_diff_html()` constructs raw HTML divs. If `_esc()` has a bypass (unlikely but possible), XSS could occur. Currently safe because `_esc` does proper entity encoding. | No immediate action needed; consider using Streamlit's native diff component when available. |

---

## 2. Reliability

| # | File : Line | Severity | Risk | Recommended Fix |
|---|------------|----------|------|----------------|
| R1 | `core/llm_client.py:25-28` | **High** | Zero timeout and zero retry on all LLM calls (7 agents × 1-2 calls each). A single 429 or network timeout cascades to a failed pipeline. The `openai.OpenAI` client defaults to infinite wait. | Configure `timeout=httpx.Timeout(30.0, connect=5.0)` and add tenacity retry with exponential backoff (3 attempts, 1s/2s/4s). |
| R2 | `agents/finops_auditor.py:219` | **High** | `findings_store.json` is written with `Path.write_text()` — not atomic. A crash mid-write corrupts the file and breaks the entire pipeline (SecOps, Remediation, UI all depend on it). | Use write-to-tmp-then-rename pattern (already implemented in `drift_detector.py`). |
| R3 | `agents/savings_tracker.py:112` | **Medium** | `savings_ledger.json` written with `Path.write_text()` without locking. Concurrent approval calls (e.g., rapid UI clicks) can corrupt the ledger. | Add `filelock` (already a dependency) around read-modify-write cycle. |
| R4 | `agents/secops_guard.py:_append_findings_to_store` | **Medium** | Loads existing `findings_store.json`, appends, writes back — no lock. If FinOps is still writing (unlikely in current sequential flow but possible in future parallelization), data is lost. | Add `filelock` or use the drift_detector's atomic-write pattern. |
| R5 | `orchestrator.py:576` | **Medium** | `tflocal apply -auto-approve` has 120s timeout but no retry. Transient LocalStack network issues fail the approval permanently — the user must restart the entire audit flow. | Add 1 retry with 5s backoff for transient failures (non-zero exit with "connection refused" in stderr). |
| R6 | `agents/multi_account_orchestrator.py:200-210` | **Low** | Per-account audit creates isolated `findings_store_{account_id}.json` files that are never cleaned up. Over time (especially with scheduled scans), orphan files accumulate. | Add cleanup logic after aggregation or use temp directories. |

---

## 3. Observability

| # | File : Line | Severity | Risk | Recommended Fix |
|---|------------|----------|------|----------------|
| O1 | All agents (print to stderr) | **High** | All error logging uses `print(..., file=sys.stderr)`. No log levels, no structured format, no correlation ID (scan_id). In production, these are invisible unless stderr is explicitly captured. | Replace with Python `logging` module; attach scan_id to log records via `LoggerAdapter` or filter. |
| O2 | `agents/audit_logger.py` | **Medium** | Writes JSON-lines to `audit.log` which is good for log aggregators, but the `orchestrator.py` internal audit trail (`_audit_trail`) is in-memory only — lost on process restart. | The file-based `AuditLogger` already persists; ensure the orchestrator always writes through it (it does). No action needed on this specific point. |
| O3 | `orchestrator.py:783-784` | **Medium** | Post-remediation hook failures are silently swallowed (`except ... pass`). If the hook consistently fails, there's no indication anywhere. | Log a warning to stderr (or better, to the reasoning logger) when the post-hook fails. |
| O4 | `agents/anomaly_detector.py:92` / all agents | **Low** | LLM call failures are logged to stderr but don't include the request payload or latency. Makes debugging "why did the anomaly detector return []?" impossible without reproducing. | Log the prompt length, model name, and elapsed time on failure. |

---

## 4. Portability

| # | File : Line | Severity | Risk | Recommended Fix |
|---|------------|----------|------|----------------|
| P1 | `orchestrator.py:50` | **Medium** | `_find_bash()` hardcodes `r"C:\Program Files\Git\bin\bash.exe"`. On non-English Windows installations or custom Git installs, this path doesn't exist. | Use `shutil.which("bash")` first, then check the hardcoded path as fallback. Or use `where bash` on Windows. |
| P2 | `hooks/pre-remediation.sh:1` | **Medium** | Shebang is `#!/usr/bin/env bash` — correct for Linux/macOS. On Windows, the orchestrator must explicitly invoke it via `BASH_CMD`. If `BASH_CMD` resolution fails, the hook fails silently with "bash not found". | Already handled by the `_find_bash()` fix; document the requirement for Git Bash in README's prerequisites. |
| P3 | `docker-compose.yml:10` | **Low** | `/var/run/docker.sock` volume mount doesn't exist on Windows. Docker Desktop handles this via its WSL2 backend, but it emits a warning on some configurations. | Conditionally include the volume mount only when not on Windows, or document it as Linux/macOS only. |
| P4 | `Makefile` | **Low** | Uses `curl`, `grep`, `printf`, `sleep` — POSIX utilities not available in Windows `cmd`. Only works if run from Git Bash or WSL. | Document that `make demo` requires Git Bash/WSL, or provide a `demo.ps1` equivalent. |

---

## 5. Configuration

| # | File : Line | Severity | Risk | Recommended Fix |
|---|------------|----------|------|----------------|
| C1 | `agents/finops_auditor.py:52` | **Medium** | `MIN_IDLE_DAYS = 30` is hardcoded as a class constant. No way to change the idle threshold without editing source. | Read from `JANITOR_MIN_IDLE_DAYS` env var with 30 as default. |
| C2 | `agents/secops_guard.py:23-26` | **Low** | `SENSITIVE_PORTS` and `DATABASE_CACHE_PORTS` are hardcoded lists. Adding a port (e.g., 9200 for Elasticsearch) requires a code change. | Move to a config file or env var (comma-separated list). |
| C3 | `agents/drift_detector.py:19` | **Low** | `MAX_SNAPSHOTS = 30` is hardcoded. No way to tune history depth without code change. | Read from `JANITOR_MAX_SNAPSHOTS` env var. |
| C4 | `.env.example` | **Low** | Missing entries for `BASH_PATH`, `AWS_ENDPOINT_URL`, `LOCALSTACK_AUTH_TOKEN` (newly required). Code reads these but they're not documented in the example file. | Add commented entries for all env vars the code reads. |
| C5 | Fixture mode completeness | **None** | The system runs fully offline in fixture mode (`JANITOR_BACKEND=fixture`). All LLM agents return safe defaults when `OPENROUTER_API_KEY` is unset and the LLM call fails. Air-gapped operation is supported. | No action needed — this works correctly today. |

---

## 6. Data Integrity

| # | File : Line | Severity | Risk | Recommended Fix |
|---|------------|----------|------|----------------|
| D1 | `agents/finops_auditor.py:219` | **High** | `findings_store.json` written via `Path.write_text()` — no atomicity. A kill/crash mid-write leaves a 0-byte or partial file. SecOps guard then reads it and gets `JSONDecodeError` or empty findings. | Use tmp+rename pattern: `path.with_suffix('.tmp').write_text(data); path.with_suffix('.tmp').replace(path)`. |
| D2 | `agents/savings_tracker.py:112` | **Medium** | Same issue as D1 for `savings_ledger.json`. A crash during write loses cumulative savings history. | Use tmp+rename atomic write. |
| D3 | `agents/secops_guard.py:_load_existing_findings_store` | **Medium** | If the file is corrupt (partial write from D1), the method prints a warning and returns an empty store — effectively losing all FinOps findings for this scan. The scan continues as if FinOps found nothing. | Fail loudly (raise) if the file exists but can't be parsed, rather than silently discarding data. |
| D4 | `agents/remediation_architect.py:250` | **Low** | Rollback files are written with `rollback_path.write_text(rollback_hcl)` — not atomic. Less critical because rollback files are independent (one per resource), but a partial write could produce invalid HCL that confuses the pre-remediation hook. | Use atomic write for consistency. |
| D5 | `agents/drift_detector.py:89-90` | **None (positive)** | Uses `self._tmp_path.write_text(...); self._tmp_path.replace(self._history_path)` with filelock. This is the correct atomic-write pattern. Other modules should follow this example. | N/A — already correct. |

---

## Summary by Severity

| Severity | Count | Key Themes |
|----------|-------|-----------|
| **Critical** | 0 | — |
| **High** | 4 | Shell injection in HCL templates (S1), no LLM timeout/retry (R1), non-atomic writes to shared state (R2/D1), unstructured stderr logging (O1) |
| **Medium** | 10 | Missing filelock on concurrent writes, MCP input validation, no retry on terraform apply, hardcoded thresholds, WSL/bash portability |
| **Low** | 9 | Orphan files, missing .env.example entries, Docker socket mount, Makefile portability |

---

## Top 5 Recommendations (Priority Order)

1. **Atomic writes everywhere** — Apply the tmp+rename pattern from `drift_detector.py` to `findings_store.json` and `savings_ledger.json`. ~30 minutes, eliminates D1/D2/R2.

2. **LLM client timeout + retry** — Add `timeout=httpx.Timeout(30.0)` to `get_client()` and wrap all `chat.completions.create` calls with `tenacity.retry(stop=stop_after_attempt(3), wait=wait_exponential())`. ~1 hour, eliminates R1/S2.

3. **Resource ID validation** — Add a regex check (`^[a-zA-Z0-9\-_.:/]+$`) in `RemediationArchitect.plan()` before any HCL generation. Reject findings with unsafe IDs. ~15 minutes, eliminates S1.

4. **Structured logging** — Replace `print(..., file=sys.stderr)` across all agents with Python's `logging` module. Add scan_id as a context field. ~2 hours, eliminates O1.

5. **Add .env.example entries** — Document `LOCALSTACK_AUTH_TOKEN`, `BASH_PATH`, `AWS_ENDPOINT_URL` in `.env.example`. ~5 minutes, eliminates C4.
