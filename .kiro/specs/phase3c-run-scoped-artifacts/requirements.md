# Requirements Document

## Introduction

This specification covers OBS-1 (run-scoped reasoning log retention) and BUG-2 (run-scoped findings store) from the Cloud Janitor product backlog (`.kiro/2026-07-08-product-backlog.md`). It is one of six independently-shippable specs split out of the original bundled `phase3-ops-hardening` spec, per a principal-engineer design review (`.kiro/2026-07-08-phase-plans-audit.md`).

**OBS-1 and BUG-2 are genuinely coupled and are bundled together deliberately** — unlike the other four phase3 splits, these two share a single foundational concept and a single integration point: both need a per-run identifier generated once per `execute_audit()` invocation, and both consume it at the same place in the Orchestrator. The design review found that the original bundled spec's requirements document claimed BUG-2 "can be implemented independently," while its own task list anchored BUG-2's findings-store wiring to OBS-1's run-ID-generation task landing first — a same-phase coupling that was being implied away rather than stated plainly. This spec corrects that: **OBS-1 and BUG-2 must land together, or BUG-2 must land after OBS-1's run-ID-generation work**, because both consume the identifier introduced in Requirement 1 below.

**Dependency on phase2-persistent-state:** this spec's Requirement 1 (shared run identifier) generates and assigns `self.current_run_id` at the top of `Orchestrator.execute_audit()`. If `phase2-persistent-state` (INF-1) has already landed when this spec is implemented, that assignment point already exists — phase2's design assigns `self.current_run_id = uuid.uuid4().hex` as the first line of `execute_audit()`, specifically so that this spec's run-scoped Reasoning_Log and Findings_Store work could tag artifacts from the very first scan step onward. In that case, this spec's implementation is a one-line swap of the generator function (`uuid.uuid4().hex` → `generate_run_id()`), reusing phase2's already-relocated call site rather than moving it again. If phase2 has not landed yet, this spec performs the full assignment itself at the same call site (top of `execute_audit()`, before Step 1), and phase2, if implemented later, adopts this spec's `generate_run_id()` call unchanged. Either way, the call site is the top of `execute_audit()` — this is not a point of ambiguity between the two specs.

**This spec has no dependency on phase1-trust-hardening**, and no dependency on the other four phase3 splits (secret scanning, preflight/timeouts, plan preview, scheduled alerting, audit query).

Full technical design is in `design.md` in this folder.

## Glossary

- **Orchestrator**: The central coordination module (`src/cloud_janitor/orchestrator/orchestrator.py`) that sequences agent execution, manages approval gates, and invokes Terraform operations.
- **Run_ID**: A single identifier generated once per `execute_audit()` invocation, in the form `<UTC timestamp>-<8 hex char UUID4 fragment>`, used to scope the Reasoning_Log and Findings_Store to that run.
- **Reasoning_Log**: A JSONL log file capturing agent decision-making traces, historically truncated on every run; this spec makes it run-scoped instead.
- **Findings_Store**: The JSON file where FinOps and SecOps agents persist scan results for downstream consumption by the Remediation Architect and UI. Historically a single shared path (`output/findings_store.json`); this spec makes it run-scoped with a "latest" pointer.
- **Findings_Store_Pointer**: A small JSON file (`output/findings_store/latest.json`) identifying which run-scoped Findings_Store file is current.

## Requirements

### Requirement 1: Shared Run Identifier Generation

**User Story:** As a developer, I want every audit run to be assigned a single stable identifier at the start of `execute_audit()`, so that the Reasoning_Log, the Findings_Store, and a future persisted Audit_Trail (phase2/phase3f) can all be correlated back to the same run without independent identifier schemes.

#### Acceptance Criteria

1. WHEN `Orchestrator.execute_audit()` begins, THE Orchestrator SHALL generate exactly one Run_ID for that invocation, in the format `<UTC timestamp: YYYYMMDDTHHMMSSZ>-<8 hex character UUID4 fragment>` (e.g. `20260708T143000Z-a1b2c3d4`).
2. THE Run_ID format SHALL be lexicographically sortable by generation time (timestamp-prefixed), so that sorting run-scoped filenames alphabetically also sorts them by recency.
3. THE Orchestrator SHALL expose the current run's Run_ID via an `AuditResult.run_id` field and via a `self.current_run_id` attribute for the duration of that `execute_audit()` call.
4. THE Run_ID SHALL be assigned as the first line of `execute_audit()`, before Step 1 (the FinOps scan) — the same call site phase2-persistent-state's design already uses for its own `self.current_run_id = uuid.uuid4().hex` assignment. If phase2 has landed first, this requirement is satisfied by replacing that generator call with `generate_run_id()` (Requirement 1, criterion 5) at the same call site, not by relocating it.
5. THE codebase SHALL provide a `core/run_context.py` module exposing `generate_run_id() -> str` and `prune_run_scoped_files(directory: Path, suffix: str, keep: int, protect: set[str] | None = None) -> None`, used by both the Reasoning_Log (Requirement 2) and the Findings_Store (Requirement 3), so retention/pruning logic exists in exactly one place.
6. `prune_run_scoped_files()` SHALL delete the oldest files matching `*<suffix>` in `directory` (sorted by filename, which is equivalent to sort-by-recency per criterion 2) until at most `keep` remain, and SHALL silently skip any file it cannot delete (permission error, file in use) rather than raising.
7. The retention count (`keep`) used by both the Reasoning_Log and Findings_Store pruning calls SHALL be independently configurable via environment variables (see Requirements 2.7 and 3.8) rather than a single shared hardcoded constant — the two artifact kinds may need different retention windows in practice (e.g. reasoning logs are typically larger per-file than findings-store JSON).

### Requirement 2: Run-Scoped Reasoning Log Retention

**User Story:** As a developer relying on agent reasoning traces for debugging and auditability, I want each audit run's reasoning log preserved under its own run-scoped file, so that a given run's reasoning can be retrieved after later runs have executed, instead of being wiped by the next audit's call to `truncate()`.

#### Acceptance Criteria

1. WHEN `execute_audit()` begins, THE Orchestrator SHALL set the ReasoningLogger's active file path to `output/logs/agent_reasoning/<run_id>.log` using that run's Run_ID (Requirement 1), and SHALL create the file if it does not exist.
2. THE ReasoningLogger SHALL NOT truncate, rename, or delete the contents of any other run's reasoning log file when starting a new run — the existing `truncate()` method (which wipes the single shared file on every normal-size run) SHALL NOT be called from `execute_audit()`.
3. WHEN a new run's reasoning-log file is created and `output/logs/agent_reasoning/` then contains more run-scoped `.log` files than the configured retention count (criterion 7), THE ReasoningLogger SHALL delete the oldest files (via `core.run_context.prune_run_scoped_files()`, Requirement 1) until at most that many remain.
4. THE ReasoningLogger SHALL retain its existing `emit()` behavior (event-type validation, per-field truncation at 64/500 characters, JSONL format) unchanged, writing only to the current run's file.
5. IF the reasoning-log directory cannot be created or a write to it fails, THEN THE ReasoningLogger SHALL log the failure to stderr and SHALL NOT raise — consistent with its existing fail-silent filesystem error handling.
6. THE Streamlit_UI SHALL provide a control to list the available run-scoped reasoning logs (most recent N, newest first, where N is the configured retention count) and to view the contents of a selected run's log.
7. THE reasoning-log retention count SHALL be configurable via the environment variable `JANITOR_RUN_RETENTION`, defaulting to 20 when unset or unparseable as a positive integer, consistent with the project's existing pattern (established by TECH-1's timeout configuration work) of making previously-hardcoded operational constants configurable rather than fixed.

### Requirement 3: Run-Scoped Findings Store

**User Story:** As an operator, I want two concurrently running single-account audits to never corrupt each other's findings, so that `_validate_findings_store()` and downstream remediation planning always work from a consistent, non-torn store.

#### Acceptance Criteria

1. WHEN `execute_audit()` begins, THE Orchestrator SHALL set `findings_store_path` on the FinOps Auditor, SecOps Guard, and Remediation Architect instances it owns to `output/findings_store/<run_id>.json`, using that run's Run_ID (Requirement 1) — reusing the same constructor-injection override mechanism `MultiAccountOrchestrator` already uses for per-account isolation (`multi_account_orchestrator.py:243-246`).
2. WHEN all agents that write to the Findings_Store in a given run have completed successfully, THE Orchestrator SHALL atomically (write-to-temp-file-then-rename) update a pointer file at `output/findings_store/latest.json` containing at minimum `{"run_id": ..., "path": ...}` identifying that run's Findings_Store as current.
3. THE Orchestrator SHALL NOT write to the legacy fixed path `output/findings_store.json` for any new audit run initiated after this feature ships.
4. THE codebase SHALL provide `core.paths.resolve_latest_findings_store() -> Path | None`, which: returns the path referenced by `output/findings_store/latest.json` if that pointer file exists and parses; otherwise returns the legacy fixed-path file (`output/findings_store.json`) if it exists (one-time backward-compatibility fallback for a store written before this feature existed); otherwise returns `None`.
5. THE Streamlit_UI SHALL read the Findings_Store exclusively via `resolve_latest_findings_store()`, replacing the current hardcoded `FINDINGS_STORE_PATH` reference at `app.py:607,610`. THE reader at this call site SHALL treat a `FileNotFoundError` raised when opening the resolved path (e.g. because the pointed-to file was pruned between resolution and open — a narrow TOCTOU race, see criterion 9) the same way it already treats a `json.JSONDecodeError`/other `OSError` today (`app.py`'s existing `except (json.JSONDecodeError, IOError)` clause already covers `FileNotFoundError`, since `IOError` is an alias for `OSError` in Python 3) — by retrying `resolve_latest_findings_store()` once before falling back to the existing "no data available" UI state, rather than surfacing a hard error on the first attempt.
6. WHEN more than the configured retention count (criterion 8) of run-scoped Findings_Store files exist in `output/findings_store/`, THE Orchestrator SHALL prune the oldest beyond that count using `core.run_context.prune_run_scoped_files()` (Requirement 1), and SHALL NOT prune a file that `latest.json` currently references even if it would otherwise be the oldest.
7. TWO overlapping `execute_audit()` invocations against the same `output/` directory SHALL each write to a distinct run-scoped file and SHALL NOT interleave writes to the same file. After both complete, `output/findings_store/latest.json` SHALL point to a valid, complete, parseable Findings_Store belonging to one of the two runs (whichever's pointer update executed last) — not a merged or torn result.
8. THE findings-store retention count SHALL be configurable via the environment variable `JANITOR_FINDINGS_RETENTION`, defaulting to 20 when unset or unparseable as a positive integer, consistent with Requirement 2's reasoning-log retention configuration and the project's established pattern of configurable operational constants.
9. **Known narrow race (documented, not a blocking defect):** between `resolve_latest_findings_store()` resolving a path and a caller opening it, enough runs could theoretically complete and prune for that file to be deleted. This is addressed by criterion 5's retry behavior, not by locking — the likelihood is low (requires the full retention window to be consumed within the gap between resolution and open) and a single retry is sufficient given the pointer always resolves to *some* valid file except in this narrow window.
