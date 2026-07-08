# Implementation Plan: Phase 2 Persistent State

## Overview

This plan implements the 7 requirements derived from a single backlog issue, INF-1. Work flows from the foundational `StateStore` module (schema, WAL setup, plans/pending-rollbacks/audit-trail operations) through Orchestrator wiring for each of the three state kinds, then concurrency/corruption hardening.

## Tasks

- [ ] 1. Create the foundational `StateStore` module
  - [ ] 1.1 Implement schema creation, connection setup, and WAL configuration in `core/state_store.py`
    - Create `src/cloud_janitor/core/state_store.py` with `_SCHEMA_DDL` (the `plans`, `pending_rollbacks`, `audit_trail` tables plus indexes — no `FOREIGN KEY` constraint and no `PRAGMA foreign_keys`; see design.md for why), a `threading.RLock` guarding `self._conn` (defense-in-depth per design.md's concurrency-model note), `StateStoreCorruptedError`, `StateStoreUnavailableError`, and `StateStore.__init__`
    - `__init__` SHALL: create parent directories if needed, open the SQLite connection, execute `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000`, run the schema DDL inside a transaction (noting in a comment that `executescript()` does not truly participate in that transaction), set/check `PRAGMA user_version` (raising `StateStoreCorruptedError` — not a warn-and-continue branch — if it exceeds `_SCHEMA_VERSION`), catch `sqlite3.OperationalError` **before** the general `sqlite3.DatabaseError` handler and raise `StateStoreUnavailableError` for it (see task 3.4 — this is the CRITICAL fix), raise `StateStoreCorruptedError` for any other `sqlite3.DatabaseError`, and close `self._conn` in a `finally` block on any failure path so no connection handle leaks
    - _Requirements: 1.1, 1.2, 1.3, 5.1, 5.6_

  - [ ] 1.2 Implement plans table operations
    - `replace_plans(plans, run_id)`: single transaction `DELETE FROM plans` + `INSERT` all rows, guarded by `self._lock`; catches `sqlite3.Error`, logs WARNING, returns `False` on failure without partial writes
    - `get_plan(resource_id)`: reconstructs a `RemediationPlan` (with nested `DependencyReport` when present) from a row; returns `None` if missing, blocked, or on read error
    - _Requirements: 1.4, 2.3, 2.4, 2.5, 5.3, 5.4_

  - [ ] 1.3 Implement pending_rollbacks table operations
    - `add_pending_rollback(resource_id)`: `INSERT OR IGNORE` (idempotent — repeated adds do not duplicate or error)
    - `has_pending_rollback(resource_id)`: existence check, returns `False` on read error
    - `discard_pending_rollback(resource_id)`: `DELETE` by resource_id
    - All three guarded by `self._lock`, catch `sqlite3.Error`, log WARNING, return the documented fallback value
    - _Requirements: 1.5, 3.5, 5.3, 5.4_

  - [ ] 1.4 Implement audit_trail table operations
    - `append_audit_entry(entry, run_id=None)`: `INSERT` only — no update/delete method exists anywhere in the class; applies a bounded retry with short backoff (up to 3 attempts), each attempt scoped to a short retry-only `busy_timeout` (~250ms, restored to the connection's default 5000ms in a `finally` block once the method returns) rather than re-incurring the full default `busy_timeout` on every attempt — this bounds the worst-case latency of a single call to roughly 1 second instead of roughly 15 seconds (3 x 5000ms + backoff) — before giving up, logging a WARNING tagged `[data_loss]` and returning `False` (this is the HIGH fix for Req 6.3/6.6 — the audit trail is the compliance-critical table where silent loss matters most)
    - `get_audit_trail()`: `SELECT ... ORDER BY id ASC`, reconstructs `AuditEntry` list; returns `None` (not `[]`) on read error, so a read failure is distinguishable from a genuinely empty trail (Req 5.3 exception, backing the Orchestrator-level fallback in task 9.2)
    - `execute_readonly_query(sql, params=())`: generic parameterized query method guarded by `self._lock`, returning `.fetchall()`; read-only by convention, not by enforcement (no SQL parsing/validation of the statement shape); unlike the other read methods above, a `sqlite3.Error` here is logged at WARNING and then RE-RAISED, not swallowed — this is the method phase3f-audit-query's `query_audit()` calls instead of reaching for a nonexistent public `.connection` attribute
    - _Requirements: 1.6, 1.7, 1.8, 4.3, 5.3, 6.6_

  - [ ] 1.5 Write property tests for `StateStore` (foundational)
    - **Property 1: Plan Persistence Round Trip**
    - **Property 4: Audit Trail Append-Only Ordering**
    - **Property 6: Fresh Store Zero-Migration Bootstrap**
    - **Validates: Requirements 1.2, 1.4, 1.6, 2.4, 4.3, 4.5, 7.2**

  - [ ] 1.6 Write unit tests for schema and bootstrap (`tests/test_state_store_schema.py`)
    - Constructing against a non-existent path creates the file and all three tables with no manual migration step
    - `PRAGMA journal_mode` readback confirms `wal`; `PRAGMA busy_timeout` readback confirms the configured value
    - `StateStore` exposes no update/delete method touching `audit_trail` (introspect public method names)
    - `execute_readonly_query()` returns `.fetchall()`-shaped rows for a parameterized `SELECT` against a seeded table, and re-raises (does not swallow) a mocked `sqlite3.Error`
    - _Requirements: 1.1, 1.2, 1.3, 1.6, 1.8_

- [ ] 2. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 3. Add `StateStore` corruption and fail-safe handling
  - [ ] 3.1 Implement `StateStoreCorruptedError` fail-closed path
    - Constructing against a file containing non-SQLite bytes raises `StateStoreCorruptedError` at construction, with an ERROR log identifying the path and underlying exception
    - Constructing against a valid file whose `PRAGMA user_version` is newer than `_SCHEMA_VERSION` also raises `StateStoreCorruptedError` (no best-effort-compatibility warn-and-continue branch — Req 7.2's no-migration-path stance means there is no version-aware logic to back that promise)
    - _Requirements: 5.1_

  - [ ] 3.2 Wrap all read/write methods with the documented fail-safe error handling
    - Confirm every method in tasks 1.2–1.4 catches `sqlite3.Error` (not bare `Exception`) and returns its documented fallback rather than propagating
    - Confirm `get_audit_trail()` specifically returns `None` (not `[]`) on a read failure, per the Req 5.3 exception carved out for it
    - _Requirements: 5.3, 5.4_

  - [ ] 3.3 Write unit tests for corruption and fail-safe behavior (`tests/test_state_store_corruption.py`)
    - Non-SQLite file content → `StateStoreCorruptedError` at construction (not at first use)
    - `PRAGMA user_version` newer than `_SCHEMA_VERSION` → `StateStoreCorruptedError` at construction
    - Mocked `sqlite3.Error` on a read operation → returns the empty/not-found value (`None` for `get_audit_trail()`), does not raise
    - Mocked `sqlite3.Error` mid-`replace_plans()` write → returns `False`, prior plans row(s) remain unchanged (verify via a direct read after the failed call)
    - On every construction-failure path (both `StateStoreCorruptedError` and `StateStoreUnavailableError`), assert `self._conn` was closed and no connection handle is leaked
    - _Requirements: 5.1, 5.3, 5.4_

  - [ ] 3.4 Implement `StateStoreUnavailableError` and distinguish it from corruption (CRITICAL regression test)
    - Catch `sqlite3.OperationalError` in `StateStore.__init__` **before** the general `sqlite3.DatabaseError` handler (`OperationalError` is a `DatabaseError` subclass) and raise `StateStoreUnavailableError` — a distinct, non-fatal exception — instead of `StateStoreCorruptedError`; log at WARNING (not ERROR) and note the failure is retryable
    - Write a regression test that opens a valid SQLite file, holds it open with an exclusive/reserved lock from a second connection (e.g. `BEGIN EXCLUSIVE` on a second `sqlite3.connect()` to the same path, or a short `timeout=0` connection contending for the lock), then constructs a `StateStore` against the same path and asserts it raises `StateStoreUnavailableError` — and explicitly asserts it does **NOT** raise `StateStoreCorruptedError` — matching the exact scenario this phase's WAL + `busy_timeout` machinery exists to tolerate
    - The Orchestrator-level retry-once behavior on `StateStoreUnavailableError` is implemented and tested in task 6.1, not here — this task covers only the `StateStore`-level distinction
    - _Requirements: 5.6_

- [ ] 4. Add WAL concurrency verification
  - [ ] 4.1 Write a concurrency integration test harness
    - Two `StateStore` instances (simulating two processes) opened against the same file; one thread/process replaces plans while another appends audit entries and a third reads pending rollbacks, overlapping in time
    - Assert no `sqlite3.OperationalError: database is locked` surfaces given the configured `busy_timeout`, and assert the final state reflects all writes from both instances
    - Additionally: with contention held long enough to exhaust `append_audit_entry()`'s own short, retry-scoped `busy_timeout` across all 3 attempts (~1 second total, not ~15 seconds), assert the call returns `False` and a WARNING containing a `[data_loss]` marker is logged (Req 6.6) — this is the sanctioned "loss" case that Req 6.3's wording explicitly carves out, not a silent one
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.6_

  - [ ] 4.2 Write property test for concurrent access
    - **Property 5: WAL Concurrent Reader/Writer Non-Blocking**
    - **Validates: Requirements 6.1, 6.2, 6.3**

- [ ] 5. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. Wire `StateStore` into the Orchestrator for plans
  - [ ] 6.1 Add `STATE_STORE_PATH` to `core/paths.py` and construct `StateStore` in `Orchestrator.__init__`
    - `STATE_STORE_PATH = OUTPUT_DIR / "state.db"`, alongside the existing path constants
    - Construct `self._state_store` in `__init__` for both the default-root and custom-root (test) code paths, mirroring the existing `_gate_store` construction; on `StateStoreUnavailableError`, retry construction once after a short delay before giving up (Req 5.6); re-raise `StateStoreCorruptedError` (or a second consecutive `StateStoreUnavailableError`) as `RuntimeError` so Orchestrator construction fails fast
    - _Requirements: 5.2, 5.6, 7.1, 7.4_

  - [ ] 6.2 Replace plan-write and plan-read call sites
    - Add a `warnings: list[str] = field(default_factory=list)` field to `AuditResult` (`orchestrator.py:284`) — the concrete mechanism for Req 5.5, distinct from `error`/`error_category` which are reserved for `success=False` outcomes
    - `execute_audit()`: assign `self.current_run_id = uuid.uuid4().hex` as the *first line* of the method (before Step 1 / FinOps scan) — not at the Remediation Architect step — so phase3c-run-scoped-artifacts's run-scoped Reasoning_Log/Findings_Store work can tag artifacts from the first scan step onward; after the Remediation Architect step, call `self._state_store.replace_plans(plans, self.current_run_id)` reusing that same value (do not generate a second `run_id`); on `False`, log a WARNING (do not fail the audit) and append a message to a local `audit_warnings` list that is passed into the returned `AuditResult(..., warnings=audit_warnings)`
    - `_find_plan()`: replace the `_last_plans` list scan with `self._state_store.get_plan(resource_id)`
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 5.5_

  - [ ] 6.3 Write property test for cross-process plan visibility
    - **Property 2: Cross-Process Plan Visibility**
    - **Validates: Requirements 2.2, 2.4**

  - [ ] 6.4 Write unit tests for plan persistence wiring (`tests/test_orchestrator_plan_persistence.py`)
    - `execute_audit()` followed by `approve()` on a *second* `Orchestrator` instance pointed at the same output directory finds the plan and proceeds
    - A blocked plan is not found by `approve()` on a second instance (matches existing same-process behavior)
    - A second `execute_audit()` run replaces the first run's plans entirely (old plans no longer found)
    - `AuditResult.success` is still `True` when `replace_plans()` is mocked to fail, and `AuditResult.warnings` contains a non-empty message referencing the failed persistence (Req 5.5's concrete field)
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 5.5, 7.1, 7.2_

- [ ] 7. Wire `StateStore` into the Orchestrator for pending rollbacks
  - [ ] 7.1 Replace pending-rollback call sites in `rollback()` and `_handle_confirm_rollback()`
    - `rollback()`: replace `self._pending_rollbacks.add(resource_id)` with `self._state_store.add_pending_rollback(resource_id)`
    - `_handle_confirm_rollback()`: replace the `resource_id not in self._pending_rollbacks` check with `not self._state_store.has_pending_rollback(resource_id)`; replace `self._pending_rollbacks.discard(resource_id)` on success with `self._state_store.discard_pending_rollback(resource_id)`
    - Remove the `_pending_rollbacks` set attribute entirely once no code path reads it
    - _Requirements: 3.1, 3.2, 3.3_

  - [ ] 7.2 Write property test for pending rollback persistence
    - **Property 3: Pending Rollback Idempotence and Restart Survival**
    - **Validates: Requirements 3.4, 3.5**

  - [ ] 7.3 Write unit tests for rollback persistence wiring (`tests/test_orchestrator_rollback_persistence.py`)
    - `ROLLBACK <id>` on one `Orchestrator` instance, then `CONFIRM ROLLBACK <id>` on a *second* instance pointed at the same output directory (restart simulation) succeeds
    - Repeated `ROLLBACK <id>` requests before confirmation do not create duplicate pending entries or otherwise change observable state
    - `CONFIRM ROLLBACK` for a resource_id with no pending entry still returns the existing "No pending rollback" error
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [ ] 8. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Wire `StateStore` into the Orchestrator for the audit trail
  - [ ] 9.1 Extend `_log_action()` to write to the StateStore alongside the existing `audit.log` write
    - Call `self._state_store.append_audit_entry(entry, run_id=self.current_run_id)` after the existing `self._audit_logger.append(entry.to_dict())` call; `append_audit_entry()` applies its own bounded retry internally (Req 6.6) — this call site does not add a second retry layer; on `False` (retries exhausted), log a WARNING and do not raise or alter the caller's return value
    - _Requirements: 4.1, 4.2, 6.6_

  - [ ] 9.2 Rewire `get_audit_trail()` to read from the StateStore, with in-memory fallback on read failure
    - Replace `return list(self._audit_trail)` with logic that calls `self._state_store.get_audit_trail()`, returns it directly when not `None`, and falls back to `list(self._audit_trail)` (logging a WARNING) when it is `None` — so a transient StateStore read failure is not indistinguishable from "no audit history exists" (Req 4.3)
    - _Requirements: 4.3, 4.5, 5.3_

  - [ ] 9.3 Write unit tests for audit trail persistence wiring (`tests/test_orchestrator_audit_persistence.py`)
    - `_log_action()` writes appear in both `audit.log` and `get_audit_trail()`'s output
    - `get_audit_trail()` on a *second* `Orchestrator` instance (restart simulation) includes entries logged by the first instance
    - Mocked `StateStore.append_audit_entry` returning `False` does not affect the return value of the operation that triggered `_log_action()` (e.g. `approve()` still returns `success=True`)
    - Mocked `StateStore.get_audit_trail` returning `None` causes `Orchestrator.get_audit_trail()` to fall back to `self._audit_trail` instead of returning an empty list
    - _Requirements: 4.1, 4.2, 4.3, 4.5_

- [ ] 10. Final integration and cleanup pass
  - [ ] 10.1 Confirm no remaining control-flow reads of `_last_plans`/`_pending_rollbacks`/`_audit_trail`
    - Grep the Orchestrator for remaining reads of these attributes outside of the retained same-process convenience caches noted in `design.md`; confirm `approve()`, `rollback()`, `_handle_confirm_rollback()`, and `get_audit_trail()` read exclusively from `self._state_store`
    - _Requirements: 7.3_

  - [ ] 10.2 Write a wiring-integrity unit test (`tests/test_orchestrator_state_store_wiring.py`)
    - Mock `StateStore.get_plan`/`has_pending_rollback`/`get_audit_trail` and assert each is actually called by the corresponding Orchestrator method (not bypassed by a stale in-memory read)
    - _Requirements: 7.1, 7.3, 7.4_

- [ ] 11. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- All tasks are mandatory — property tests, unit tests, and integration tests are required, not optional, per project convention (see `.kiro/specs/audit-remediation/tasks.md`)
- Each task references specific requirements for traceability
- Tests use pytest + hypothesis; invoke via `.venv/Scripts/python.exe -m pytest`
- No task in this plan touches `agents/approval_gate.py` or `agents/audit_logger.py` — both are unchanged; this phase is additive persistence for the three state kinds that were never previously persisted
- No migration task exists by design — `_last_plans`, `_pending_rollbacks`, and `_audit_trail` have no prior on-disk format (Req 7.2)
- OBS-2 (queryable/exportable audit trail with a UI and export path) is explicitly out of scope; this phase only makes the `audit_trail` table exist, be indexed, and be readable via `get_audit_trail()` — OBS-2 is a separate future spec that builds a query/filter/export API on top of this table

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4"] },
    { "id": 2, "tasks": ["1.5", "1.6"] },
    { "id": 3, "tasks": ["2"] },
    { "id": 4, "tasks": ["3.1", "3.2"] },
    { "id": 5, "tasks": ["3.3", "3.4"] },
    { "id": 6, "tasks": ["4.1"] },
    { "id": 7, "tasks": ["4.2"] },
    { "id": 8, "tasks": ["5"] },
    { "id": 9, "tasks": ["6.1"] },
    { "id": 10, "tasks": ["6.2", "7.1"] },
    { "id": 11, "tasks": ["6.3", "6.4", "7.2", "7.3"] },
    { "id": 12, "tasks": ["8"] },
    { "id": 13, "tasks": ["9.1"] },
    { "id": 14, "tasks": ["9.2"] },
    { "id": 15, "tasks": ["9.3", "10.1"] },
    { "id": 16, "tasks": ["10.2"] },
    { "id": 17, "tasks": ["11"] }
  ]
}
```

Note: task IDs `2`, `5`, `8`, and `11` (waves 3, 8, 12, 17) are the four "Checkpoint — Ensure all tests pass" tasks from the prose list, each placed in its own wave immediately after the tasks it gates. Earlier versions of this graph omitted them, making it an incomplete schedule; they are included here explicitly.

**Fix (second-pass review)**: an earlier version of this graph placed checkpoint task `8` in wave 12 while scheduling `9.1` in wave 10 and `9.2` in wave 11 — both _before_ the checkpoint's wave, even though the prose task list places checkpoint 8 between task 7.3 and task 9.1 specifically so "all tests pass" gates section 9's audit-trail wiring before it begins. That ordering defeated the checkpoint's purpose. `9.1` and `9.2` have been moved out of waves 10–11 (which now contain only the section 6/7 tasks the checkpoint is meant to gate) and rescheduled into waves 13–14, after checkpoint `8`'s wave 12 — matching the prose ordering. All subsequent wave IDs shifted accordingly (final checkpoint `11` is now wave 17, not wave 15).
