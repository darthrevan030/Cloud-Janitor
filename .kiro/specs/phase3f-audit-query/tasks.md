# Implementation Plan: Queryable and Exportable Audit Trail (OBS-2)

## Overview

This plan implements Requirement 1 (queryable and exportable audit trail), derived from backlog issue OBS-2. **This spec's end-to-end enablement is gated on phase2-persistent-state (INF-1) shipping** — its unit tests can be developed and run against an in-memory SQLite fixture matching the assumed `audit_trail` schema before phase2 exists, but task 4 (live wiring) below must not be started until phase2 has landed.

## Tasks

- [x] 1. Create `core/audit_query.py` with `query_audit()`, `export_audit_csv()`, `export_audit_json()`
  - `query_audit()` builds a fully parameterized SQL `WHERE` clause and retrieves rows via `state_store.execute_readonly_query(sql, params)` — the public, lock-guarded method phase2-persistent-state's `StateStore` exposes for this purpose (NOT a `state_store.connection` attribute — `StateStore` has no such public attribute); no filter value is ever interpolated into the SQL string; column names come only from a fixed, hardcoded tuple matching phase2's `audit_trail` DDL, never from caller input
  - Define a module-level `UNSCOPED` sentinel object; `run_id` parameter accepts `None` (no filter), `UNSCOPED` (filter to `run_id IS NULL`), or a string (filter to that exact value) — document all three in the docstring
  - Wire `_ALLOWED_FILTERS` in as an active guard: validate every filter name `query_audit()` is about to apply against `_ALLOWED_FILTERS`, raising `ValueError` on a mismatch, rather than leaving the constant unreferenced
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.7, 1.8_

- [x] 2. Write property test for audit query filter conjunction
  - **Property 1: Audit Query Filter Conjunction**
  - **Validates: Requirements 1.1, 1.2**

- [x] 3. Write unit tests for `core/audit_query.py` against an in-memory SQLite fixture (`tests/test_audit_query.py`)
  - Build a throwaway `sqlite3.Connection` with a table matching the assumed `audit_trail` schema (`timestamp`, `action`, `resource_id`, `actor`, `result`, `details`, `run_id`), wrapped in a minimal stand-in object exposing `execute_readonly_query(sql, params)` (mirroring phase2's method signature) so `query_audit()` is exercised exactly as it will be against a real `StateStore`, seeded with flaggable rows
  - Single-filter and multi-filter (AND) queries return exactly the matching rows; no-filter query returns most-recent-`limit` ordered descending
  - `export_audit_csv()`/`export_audit_json()` round-trip: exporting then re-parsing reproduces the same row set
  - Empty result set: `export_audit_csv([])` returns `""` without raising
  - SQL-injection-shaped filter values (e.g. `resource_id="x'; DROP TABLE audit_trail; --"`) are treated as literal string filters, not executed as SQL
  - **NULL `run_id` filtering (Req 1.7):** seed rows with a mix of non-NULL `run_id` values and `run_id IS NULL` rows; `query_audit(run_id=UNSCOPED)` returns only the NULL-`run_id` rows; `query_audit()` (default) returns rows regardless of `run_id`; `query_audit(run_id="abc")` returns only the exact-match rows — all three cases distinguished correctly
  - **Filter allowlist guard (Req 1.8):** force a mismatch between an applied filter name and `_ALLOWED_FILTERS` (e.g. via monkeypatching `_ALLOWED_FILTERS` to a smaller set) and assert `query_audit()` raises `ValueError` rather than silently skipping the filter
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.7, 1.8_

- [x] 4. Checkpoint — Ensure all tests pass (unit tests only; do not proceed to task 5 until phase2-persistent-state has shipped)
  - Ensure all tests pass, ask the user if questions arise. Confirm phase2-persistent-state's `StateStore` exists in the codebase before starting task 5.

- [x] 5. Add the "Audit Trail" view to the Streamlit UI (blocked on phase2)
  - [x] 5.1 Add filter widgets (resource ID, actor, result, action, date range) that call `query_audit()`; render results with `st.dataframe`; CSV/JSON download buttons wired to the export functions
    - _Requirements: 1.5_

  - [x] 5.2 Catch `StateStore`-unavailable errors and render an explanatory message instead of a stack trace
    - Relevant until phase2 ships, and as a defensive fallback afterward in case the store fails to initialize
    - _Requirements: 1.5_

  - [x] 5.3 Write unit tests for the UI view (`tests/test_app_audit_trail_ui.py`)
    - Filter widgets render and call `query_audit()` on change; CSV/JSON download buttons produce the expected content; `StateStore`-unavailable error is caught and renders an explanatory message instead of a stack trace
    - _Requirements: 1.5_

- [x] 6. Document the phase2 dependency and non-goal
  - Add a code comment on `query_audit()` referencing `phase2-persistent-state`'s `StateStore` as the assumed data source, and a comment on the Streamlit view noting tamper-evidence is an explicit non-goal for this spec
  - _Requirements: 1.6_

- [x] 7. Write and gate the end-to-end enablement test (blocked on phase2)
  - Write `tests/test_audit_query_e2e.py` verifying `_log_action()` writes through a live `StateStore` and `query_audit()` sees it; mark skipped/xfail until phase2-persistent-state has shipped, then un-skip and verify it passes against the real `StateStore`
  - _Requirements: 1.1 (end-to-end verification)_

- [x] 8. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise. Do not mark this spec fully complete until task 7's end-to-end test passes against a real `StateStore`.

## Notes

- All tasks are mandatory — property tests, unit tests, and integration tests are required, not optional, per project convention (see `.kiro/specs/audit-remediation/tasks.md`)
- Each task references specific requirements for traceability
- Tests use pytest + hypothesis; invoke via `.venv/Scripts/python.exe -m pytest`
- Tasks 1-3 (query/export logic and its unit tests) do not require phase2-persistent-state to exist, since they run against a local SQLite fixture matching the assumed schema
- Tasks 5 and 7 (Streamlit wiring and end-to-end verification) are explicitly blocked on phase2-persistent-state shipping — do not start them before phase2's `StateStore` exists in the codebase
- This spec has no dependency on phase1-trust-hardening or on the other four phase3 splits (secret scanning, preflight/timeouts, run-scoped artifacts, plan preview, scheduled alerting)

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2", "3"] },
    { "id": 2, "tasks": ["4"] },
    { "id": 3, "tasks": ["5.1", "5.2", "6"] },
    { "id": 4, "tasks": ["5.3", "7"] },
    { "id": 5, "tasks": ["8"] }
  ]
}
```
