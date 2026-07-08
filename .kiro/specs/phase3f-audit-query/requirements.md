# Requirements Document

## Introduction

This specification covers OBS-2 (queryable and exportable audit trail) from the Cloud Janitor product backlog (`.kiro/2026-07-08-product-backlog.md`). It is one of six independently-shippable specs split out of the original bundled `phase3-ops-hardening` spec, per a principal-engineer design review (`.kiro/2026-07-08-phase-plans-audit.md`).

**Dependency on phase2-persistent-state — this spec is gated and blocked until phase2 ships.** This requirement assumes `phase2-persistent-state` (INF-1) has delivered a `StateStore` (`src/cloud_janitor/core/state_store.py`, SQLite-backed) exposing an `audit_trail` table populated from `Orchestrator._log_action()`, in place of (or alongside) today's append-only `AuditLogger`. This specification does not design that persistence layer — it designs the query/export layer built on top of it, and assumes the table carries at minimum the fields already present on `AuditEntry` (`timestamp`, `action`, `resource_id`, `actor`, `result`, `details`), plus a `run_id` column, which phase2's design already includes for exactly this purpose. It further assumes `StateStore` exposes a public `execute_readonly_query(sql: str, params: tuple = ()) -> list[tuple]` method (also part of phase2's design) as the sole read path this spec's query layer uses — `StateStore` has no public `.connection` attribute, so this spec never assumes one.

Development and unit testing of this spec's query/export logic MAY proceed against a schema-compatible in-memory SQLite fixture before phase2 ships — the `query_audit()`/`export_audit_csv()`/`export_audit_json()` functions have no runtime dependency on a real `StateStore` instance, only on its documented schema shape. **End-to-end enablement (verifying `_log_action()` actually writes through a live `StateStore`, and wiring the Streamlit UI to real data) is blocked on phase2's completion** and SHALL NOT be marked done until phase2 has shipped and this spec's UI view has been verified against a real `StateStore`.

**This spec has no dependency on phase1-trust-hardening**, and no dependency on the other four phase3 splits (secret scanning, preflight/timeouts, run-scoped artifacts, plan preview, scheduled alerting) — other than sharing the general `run_id` concept those specs also use, which is why filtering by `run_id` is included here as a first-class filter.

Full technical design is in `design.md` in this folder.

## Glossary

- **StateStore**: A SQLite-backed persistence class delivered by phase2-persistent-state (`src/cloud_janitor/core/state_store.py`), holding plans, pending rollbacks, and (relevant to this spec) an `audit_trail` table, exposed for this spec's purposes via its public `execute_readonly_query()` method (not a `.connection` attribute, which it does not have). Not designed in this spec — assumed as an external dependency.
- **UNSCOPED**: A module-level sentinel object defined in `core/audit_query.py`. Passed as `query_audit(run_id=UNSCOPED)` to filter to Audit_Trail entries whose `run_id` column is `NULL` — distinct from the default `run_id=None`, which applies no filter on `run_id` at all.
- **Audit_Trail**: The full history of approval, rollback, and scan actions, each entry mirroring the existing `AuditEntry` dataclass shape (`timestamp`, `action`, `resource_id`, `actor`, `result`, `details`), plus phase2's added `run_id` column.
- **Run_ID**: An identifier tagging which `execute_audit()` invocation, if any, was active when a given audit-trail entry was written. Generated and assigned per phase2's design (and, if phase3c has also landed, phase3c's `generate_run_id()`).

## Requirements

### Requirement 1: Queryable and Exportable Audit Trail

**User Story:** As a compliance-focused operator, I want to filter and export the Audit_Trail by resource, actor, result, action, and date range, so that I can produce evidence for an audit or investigation without grepping a raw JSONL log file.

**Dependency note:** this requirement is gated on phase2-persistent-state's `StateStore` existing with an `audit_trail` table. Do not begin end-to-end enablement work (task involving a live `StateStore`) before phase2 ships — see the Introduction above.

#### Acceptance Criteria

1. THE codebase SHALL provide a `query_audit(resource_id: str | None = None, actor: str | None = None, result: str | None = None, action: str | None = None, run_id: str | None | object = None, date_from: str | None = None, date_to: str | None = None, limit: int = 1000) -> list[dict]` function that queries the StateStore's `audit_trail` table using a dynamically constructed, fully parameterized SQL `WHERE` clause — no filter value SHALL be interpolated directly into the SQL string. `query_audit()` SHALL retrieve rows via `state_store.execute_readonly_query(sql, params)` — the public, lock-guarded method phase2-persistent-state's `StateStore` exposes for exactly this purpose — and SHALL NOT assume or depend on a public `.connection` attribute on `state_store`, since `StateStore` does not expose one.
2. WHEN two or more filter arguments to `query_audit()` are non-`None`, THE function SHALL combine them with SQL `AND` (an entry must match every supplied filter to be returned).
3. WHEN no filter arguments are supplied, THE function SHALL return the most recent `limit` Audit_Trail entries ordered by `timestamp` descending.
4. THE codebase SHALL provide `export_audit_csv(rows: list[dict]) -> str` and `export_audit_json(rows: list[dict]) -> str` functions that serialize a `query_audit()` result set to CSV and JSON text respectively, preserving every field present in the input rows and producing output that a standard CSV/JSON parser can read back into an equivalent structure.
5. THE Streamlit_UI SHALL provide an "Audit Trail" view with filter widgets for resource ID, actor, result, action, and date range, calling `query_audit()` when a filter changes, rendering results in a table, and offering CSV and JSON download buttons wired to the export functions. IF the StateStore is not yet available (phase2 not deployed, so `execute_readonly_query()` does not exist), THEN THE view SHALL catch the resulting error and render an explanatory message rather than a stack trace.
6. Tamper-evidence (hash-chaining of Audit_Trail entries) SHALL NOT be implemented in this phase. This is an explicit non-goal for now, deferred until a specific compliance requirement names it (YAGNI).
7. Because `run_id` is nullable in real Audit_Trail data (entries logged outside of any `execute_audit()` run, e.g. a stale-plan `approve()`/`rollback()` call in a separate process, get `run_id=NULL`), THE codebase SHALL provide a way to filter specifically to `run_id IS NULL` — distinct from the default `run_id=None` meaning "no filter on run_id at all." THIS SHALL be exposed as a module-level sentinel, `UNSCOPED`, such that `query_audit(run_id=UNSCOPED)` SHALL return only entries whose `run_id` column is `NULL`, while `query_audit()` (the default) continues to return entries regardless of their `run_id` value. This distinction and its rationale SHALL be documented in `query_audit()`'s docstring.
8. THE codebase SHALL validate that every filter `query_audit()` applies to the `audit_trail` table's `WHERE` clause corresponds to a name in a maintained allowlist (`resource_id`, `actor`, `result`, `action`, `run_id`), and SHALL raise `ValueError` if that invariant is ever violated (e.g. by a future edit that adds a new filter parameter without updating the allowlist) — defense-in-depth against an unrecognized filter being silently dropped rather than either applied or loudly rejected.
