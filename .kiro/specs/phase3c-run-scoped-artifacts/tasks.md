# Implementation Plan: Run-Scoped Artifacts (OBS-1 + BUG-2)

## Overview

This plan implements 3 requirements derived from 2 backlog issues (OBS-1, BUG-2), which are genuinely coupled — both consume a single Run_ID generated once per `execute_audit()` invocation. Work flows from the foundational `core/run_context.py` module through the Reasoning_Log and Findings_Store wiring that both depend on it.

**Ordering note:** OBS-1's Run_ID-generation task (1.x below) must land before BUG-2's findings-store wiring task (3.x below), since both consume the same `run_id` value assigned once in `execute_audit()`. They may be implemented in the same PR/session; BUG-2's task should not be started independently of OBS-1's foundational work landing first.

**Cross-phase note:** if `phase2-persistent-state` has already landed, `self.current_run_id = uuid.uuid4().hex` already exists as the first line of `execute_audit()` — task 2.1 below becomes a one-line swap of the generator function, not a new assignment. If phase2 has not landed, task 2.1 performs the assignment itself at that same call site.

## Tasks

- [ ] 1. Create foundational module
  - [ ] 1.1 Create `core/run_context.py`
    - Implement `generate_run_id()` (timestamp + UUID4 fragment, format per design), `get_retention(env_var)` (reads `JANITOR_RUN_RETENTION`/`JANITOR_FINDINGS_RETENTION`, defaults to 20 on unset/invalid, WARNING on invalid), and `prune_run_scoped_files(directory, suffix, keep, protect=None)`
    - _Requirements: 1.1, 1.2, 1.5, 1.6, 1.7_

  - [ ] 1.2 Write property tests for `core/run_context.py`
    - **Property 1: Run_ID Uniqueness and Sortability**
    - **Property 5: Retention Configuration Partition**
    - **Validates: Requirements 1.1, 1.2, 2.7, 3.8**

  - [ ] 1.3 Write unit tests for `core/run_context.py` (`tests/test_run_context.py`)
    - `prune_run_scoped_files` deletes oldest beyond `keep`, respects `protect` set, never raises on a permission-denied `unlink` (mocked)
    - `get_retention` fallback matrix: unset, non-numeric, zero, negative → default 20; valid positive integer → that value
    - _Requirements: 1.5, 1.6, 1.7_

- [ ] 2. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 3. Implement run-scoped reasoning log (OBS-1)
  - [ ] 3.1 Wire `Orchestrator.execute_audit()` to generate/reuse a Run_ID
    - If phase2-persistent-state has landed, swap the existing `self.current_run_id = uuid.uuid4().hex` (first line of `execute_audit()`) to `self.current_run_id = generate_run_id()`; otherwise add the assignment at that same call site (first line, before Step 1); add `run_id` to `AuditResult`
    - _Requirements: 1.1, 1.3, 1.4_

  - [ ] 3.2 Add `ReasoningLogger.start_new_run(run_id)`, retire the `truncate()` call site
    - Redirect `self._log_path` to `output/logs/agent_reasoning/<run_id>.log`, create if missing, prune beyond the configured `JANITOR_RUN_RETENTION` via `core.run_context.prune_run_scoped_files`/`get_retention`
    - Mark `truncate()` as deprecated in its docstring; leave the method intact for now
    - Replace the `self._reasoning_logger.truncate()` call in `execute_audit()` with `self._reasoning_logger.start_new_run(run_id)`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.7_

  - [ ] 3.3 Write property test for reasoning log retention
    - **Property 2: Reasoning Log Retention Bound**
    - **Validates: Requirement 2.3**

  - [ ] 3.4 Write unit tests for run-scoped reasoning log (`tests/test_reasoning_log_run_scoped.py`)
    - Two sequential `start_new_run()` calls leave both files present with correct, distinct content (no truncation of the first)
    - 21st `start_new_run()` call (default retention) prunes exactly the single oldest file; `JANITOR_RUN_RETENTION=5` prunes at the correct boundary
    - `emit()` after `start_new_run()` writes to the new run's file only
    - Filesystem error during `start_new_run()` logs to stderr, does not raise
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.7_

  - [ ] 3.5 Add reasoning-log listing/viewing to the Streamlit UI
    - List the N most recent run-scoped log files (newest first, N = configured retention), render the selected file's contents
    - _Requirements: 2.6_

- [ ] 4. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Implement run-scoped findings store (BUG-2)
  - [ ] 5.1 Add `FINDINGS_STORE_DIR`, `FINDINGS_STORE_LATEST_POINTER` to `core/paths.py`
    - Implement `resolve_latest_findings_store()` (pointer → legacy fallback → `None`) and `update_findings_store_pointer(run_id, path)` (atomic write-then-rename)
    - _Requirements: 3.2, 3.4_

  - [ ] 5.2 Wire the Orchestrator to set run-scoped `findings_store_path` on FinOps/SecOps/Architect and update the pointer
    - At the same point `run_id` is generated (task 3.1), set `self._finops.findings_store_path = self._secops.findings_store_path = self._architect.findings_store_path = FINDINGS_STORE_DIR / f"{run_id}.json"`, mirroring `multi_account_orchestrator.py:243-246`'s existing override pattern
    - After Step 4 (Remediation Architect planning) succeeds, call `update_findings_store_pointer()` then `prune_run_scoped_files(FINDINGS_STORE_DIR, ".json", get_retention("JANITOR_FINDINGS_RETENTION"), protect={run_findings_path.name, "latest.json"})`
    - _Requirements: 3.1, 3.2, 3.3, 3.6, 3.8_

  - [ ] 5.3 Write property tests for findings store isolation and pointer safety
    - **Property 3: Findings Store Isolation**
    - **Property 4: Findings Store Pointer Never References a Pruned File**
    - **Validates: Requirements 3.6, 3.7**

  - [ ] 5.4 Write unit tests for run-scoped findings store (`tests/test_findings_store_run_scoped.py`)
    - Two `Orchestrator` instances (or two `execute_audit()` calls with mocked agents returning different findings) never write to the same file; both resulting files independently pass `_validate_findings_store()`
    - `resolve_latest_findings_store()` returns the pointer's target when present, falls back to `FINDINGS_STORE_PATH` when the pointer is absent but the legacy file exists, returns `None` when neither exists
    - Pruning beyond the configured retention never deletes the file `latest.json` currently references
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.6, 3.7, 3.8_

  - [ ] 5.5 Update `app.py` to read via `resolve_latest_findings_store()` with retry-once-on-`FileNotFoundError`
    - Replace the `FINDINGS_STORE_PATH` import and the two read sites (`app.py:607,610`) with calls to `resolve_latest_findings_store()`; on `FileNotFoundError` when opening the resolved path, retry resolution once before falling back to the existing "no data available" UI state
    - _Requirements: 3.5, 3.9_

  - [ ] 5.6 Write unit tests for the retry-once reader (`tests/test_app_findings_reader.py`)
    - Mocked `open()` raising `FileNotFoundError` on the first attempt, succeeding on the second (post-retry) resolution → findings are returned, not an empty fallback
    - Mocked `open()` raising `FileNotFoundError` on both attempts → falls back to `[]` (existing "no data available" behavior)
    - _Requirements: 3.5, 3.9_

- [ ] 6. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- All tasks are mandatory — property tests, unit tests, and integration tests are required, not optional, per project convention (see `.kiro/specs/audit-remediation/tasks.md`)
- Each task references specific requirements for traceability
- Tests use pytest + hypothesis; invoke via `.venv/Scripts/python.exe -m pytest`
- This spec has no dependency on phase1-trust-hardening or on the other four phase3 splits (secret scanning, preflight/timeouts, plan preview, scheduled alerting, audit query)
- Task 3 (OBS-1) must land before task 5 (BUG-2) starts, since task 5.2 consumes the `run_id` assigned in task 3.1 — this is the one explicit intra-spec ordering dependency

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3"] },
    { "id": 2, "tasks": ["3.1"] },
    { "id": 3, "tasks": ["3.2", "5.1"] },
    { "id": 4, "tasks": ["3.3", "3.4", "3.5", "5.2"] },
    { "id": 5, "tasks": ["5.3", "5.4"] },
    { "id": 6, "tasks": ["5.5"] },
    { "id": 7, "tasks": ["5.6"] }
  ]
}
```
