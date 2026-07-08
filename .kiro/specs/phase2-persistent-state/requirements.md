# Requirements Document

## Introduction

This specification covers Phase 2 of the Cloud Janitor product backlog (`.kiro/2026-07-08-product-backlog.md`) — **INF-1: "Plans / pending rollbacks / audit trail are still in-memory."** INF-1 is the remaining slice of the multi-user/multi-process blocker after Phase 1's approval-gate persistence work: `ApprovalGateStore` already persists gate attempt/lockout state (`agents/approval_gate.py:375`, instantiated at `orchestrator.py:442`), but `_audit_trail` (`orchestrator.py:449`), `_last_plans` (`:452`), and `_pending_rollbacks` (`:455`) remain plain in-memory `list`/`list`/`set` attributes with no load/save path. This phase generalizes the gate-store persistence approach into a single `StateStore` that covers all three.

This is a single-backlog-item phase; no other backlog IDs are in scope. OBS-2 ("Queryable / exportable audit trail for compliance") depends on this phase's `audit_trail` table but its query/export/UI work is explicitly **out of scope** here — this phase only makes the underlying data durable and queryable at the storage layer.

**Decisions resolved (the backlog ticket left these open):**

- **Storage backend: SQLite via the stdlib `sqlite3` module**, not Postgres/Redis. The backlog's own roadmap ties that choice to "whether near-term target is one shared EC2 box or an autoscaled service" — this codebase has no load balancer, no session-affinity infrastructure, and no multi-instance deployment story anywhere in it today, so a single-node embedded database is the correct near-term choice. A future migration to Postgres remains possible if the deployment model changes to an autoscaled service, and is explicitly not designed here (YAGNI).
- **No migration/backward-compatibility path.** `_last_plans`, `_pending_rollbacks`, and `_audit_trail` have never been persisted in any prior release — there is no legacy on-disk format to read, upgrade, or migrate. A fresh SQLite file is created on first use.
- **Multi-process access uses SQLite WAL mode.** `PRAGMA journal_mode=WAL` is enabled to reduce writer contention between concurrent Streamlit worker processes and CLI invocations sharing the same output directory.

Full technical design is in `design.md` in this folder.

## Glossary

- **Orchestrator**: The central coordination module (`src/cloud_janitor/orchestrator/orchestrator.py`) that sequences agent execution, manages approval gates, and invokes Terraform operations.
- **StateStore**: The new persistence class (`src/cloud_janitor/core/state_store.py`) introduced by this phase, backed by a single SQLite database file, covering three logical concerns: plans, pending rollbacks, and the audit trail.
- **Gate_Store**: The existing `ApprovalGateStore` (`agents/approval_gate.py`) that persists approval-gate attempt/lockout state to `output/approval_gates.json`. Unchanged by this phase — referenced only as the pattern being generalized.
- **RemediationPlan**: The dataclass (`agents/remediation_architect.py`) produced by the Remediation Architect for a single finding, holding `resource_id`, `finding`, `blocked`, `block_reason`, an optional `dependency_report`, `remediation_hcl`, and `rollback_hcl`.
- **DependencyReport**: The dataclass (`agents/remediation_architect.py`) nested inside a `RemediationPlan`, holding `resource_id`, `has_dependencies`, `dependencies`, `recommendation`, and `checked_at`.
- **AuditEntry**: The dataclass (`orchestrator.py`) representing one audit-trail row: `timestamp`, `action`, `resource_id`, `actor`, `result`, `details`.
- **Run_ID**: An identifier tagging which `execute_audit()` invocation produced a given batch of persisted plans, used to support the wholesale-replace semantics of Requirement 2.
- **WAL_Mode**: SQLite's Write-Ahead Logging journal mode, which allows concurrent readers to proceed without blocking on an in-progress writer.
- **StateStoreUnavailableError**: A distinct, non-fatal exception raised when `StateStore` construction fails for a transient reason (e.g. `sqlite3.OperationalError` from lock contention) rather than genuine file corruption. See Requirement 5.6. Contrast with `StateStoreCorruptedError`, which is fatal.
- **Sandbox/Real-AWS mode terminology from Phase 1 does not apply here** — INF-1 is backend-agnostic; the persistence layer behaves identically regardless of `JANITOR_BACKEND`.

## Requirements

### Requirement 1: StateStore Schema, Initialization, and Storage Backend

**User Story:** As an operator running Cloud Janitor as a shared service, I want all approval-flow state backed by a real embedded database file rather than ad hoc JSON structures, so that plans, pending rollbacks, and the audit trail share one consistent, transactional persistence mechanism.

#### Acceptance Criteria

1. THE codebase SHALL provide a `StateStore` class in `src/cloud_janitor/core/state_store.py` backed by the stdlib `sqlite3` module, with no dependency on Postgres, Redis, or any external database server process.
2. WHEN a `StateStore` is constructed against a path where no database file exists, THE StateStore SHALL create the file and all three required tables (`plans`, `pending_rollbacks`, `audit_trail`) within that same construction call, without requiring any external migration tool or pre-existing schema.
3. WHEN a `StateStore` opens its underlying SQLite connection, THE StateStore SHALL execute `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` before performing any other read or write.
4. THE `plans` table SHALL use `resource_id` as its primary key and SHALL store sufficient columns (finding data, blocked flag, block reason, dependency report, remediation HCL, rollback HCL, owning Run_ID, created-at timestamp) to fully reconstruct a `RemediationPlan` instance via `get_plan()`.
5. THE `pending_rollbacks` table SHALL use `resource_id` as its primary key and SHALL store only existence plus a `requested_at` timestamp — no remediation data is duplicated into this table.
6. THE `audit_trail` table SHALL be append-only: THE StateStore SHALL expose no method that updates or deletes an existing row — only `append_audit_entry()` (insert) and `get_audit_trail()` (read).
7. THE `audit_trail` table's columns SHALL include the existing in-memory `AuditEntry` dataclass fields exactly (`timestamp`, `actor`, `action`, `resource_id`, `result`, `details`) plus one additional nullable `run_id` column that is not part of the `AuditEntry` dataclass — `run_id` tags which `execute_audit()` batch, if any, was active when the entry was written, and exists to support the queryable/exportable audit trail work in a future phase (OBS-2) without requiring a further schema change. `run_id` SHALL be indexed alongside `resource_id` and `timestamp`.
8. THE `StateStore` SHALL expose a public `execute_readonly_query(sql: str, params: tuple = ()) -> list[tuple]` method that acquires the same internal lock as every other method, executes the given SQL statement against the underlying connection, and returns `.fetchall()`. This method exists specifically so a future query/export layer (OBS-2, see phase3f-audit-query) can run arbitrary `SELECT` statements — including dynamically constructed filter/`WHERE` clauses — against the `audit_trail` table without the `StateStore` needing to expose a raw `.connection` attribute (which would bypass the lock and the class's fail-safe error handling). `execute_readonly_query()` is read-only **by convention, not by enforcement**: it SHALL NOT inspect or restrict the supplied SQL to reject non-`SELECT` statements — callers are contractually responsible for passing only `SELECT` statements. On a `sqlite3.Error`, `execute_readonly_query()` SHALL log a WARNING and raise the exception to the caller (unlike the other read methods in this requirement, which swallow errors and return an empty/"not found" value) since callers of a generic query method are expected to handle query-shaped failures themselves rather than receive an ambiguous empty result that could be confused with "no rows matched."

### Requirement 2: Persistent Remediation Plans

**User Story:** As an operator, I want a remediation plan generated by one Cloud Janitor process (or Streamlit worker) to be visible to `approve()` in any other process reading the same output directory, so that plan lookup does not silently fail after a restart or in a multi-worker deployment.

#### Acceptance Criteria

1. WHEN `execute_audit()` completes the Remediation Architect step, THE Orchestrator SHALL persist the full list of plans (both blocked and active) to the StateStore via a single atomic replace operation tagged with a Run_ID, before returning the `AuditResult`.
2. WHEN the Orchestrator's `approve()` method needs to find the plan for a resource_id, THE Orchestrator SHALL query the StateStore rather than reading the in-memory `_last_plans` list.
3. IF `StateStore.get_plan(resource_id)` returns a plan whose `blocked` field is `True`, THEN THE Orchestrator SHALL treat this identically to "no plan found" (matching the existing `_find_plan()` filter) and SHALL NOT proceed to gate or Terraform logic for that resource_id.
4. WHEN a plan is persisted and later retrieved via `get_plan()` — including from a newly constructed `StateStore` instance pointed at the same file after the original process has exited — THE reconstructed `RemediationPlan` SHALL be field-for-field equal to the plan that was persisted, including its nested `DependencyReport` when present and `None` when absent.
5. WHEN `execute_audit()` runs a second time against the same StateStore, THE previous run's plans SHALL be replaced in full (not merged or appended), matching the existing `self._last_plans = plans` replacement semantics.

### Requirement 3: Persistent Pending Rollbacks

**User Story:** As an operator, I want a rollback that has been requested but not yet confirmed to remain pending across a process restart, so that the two-step `ROLLBACK` / `CONFIRM ROLLBACK` protocol cannot be silently reset by an application restart between the two steps.

#### Acceptance Criteria

1. WHEN `Orchestrator.rollback()` accepts a valid `ROLLBACK <resource_id>` command, THE Orchestrator SHALL record `resource_id` as pending in the StateStore before returning `RollbackResult(needs_confirmation=True)`.
2. WHEN `Orchestrator._handle_confirm_rollback()` processes a `CONFIRM ROLLBACK <resource_id>` command, THE Orchestrator SHALL check pending status via the StateStore rather than the in-memory `_pending_rollbacks` set.
3. WHEN a rollback is applied successfully, THE Orchestrator SHALL remove `resource_id` from the StateStore's pending rollbacks before returning `RollbackResult(success=True)`.
4. IF the Orchestrator process restarts between a successful `ROLLBACK` request and its `CONFIRM ROLLBACK`, THEN a newly constructed Orchestrator instance pointed at the same output directory SHALL still recognize `resource_id` as pending.
5. Adding the same `resource_id` to pending rollbacks more than once (e.g., a repeated `ROLLBACK` request before confirmation) SHALL NOT create duplicate rows or otherwise change the observable pending state beyond the single existing entry.

### Requirement 4: Persistent Audit Trail

**User Story:** As a compliance-focused operator, I want the rich, structured audit trail (not just the append-only text log) to survive a restart, so that `get_audit_trail()` reflects the tool's full action history rather than only whatever happened since the process last started.

#### Acceptance Criteria

1. WHEN `Orchestrator._log_action()` is called, THE Orchestrator SHALL write the resulting `AuditEntry` to both the existing append-only `audit.log` file (via `AuditLogger`, unchanged) and the StateStore's `audit_trail` table, passing `self.current_run_id` (or `None` if no scan is active in this process) as the row's `run_id`.
2. THE StateStore write in criterion 1 SHALL be non-blocking: if it fails for any reason, THE Orchestrator SHALL log a WARNING and SHALL NOT raise or alter the return value of the operation that triggered `_log_action()` — matching the existing non-blocking failure semantics of `AuditLogger.append()`.
3. WHEN `Orchestrator.get_audit_trail()` is called, THE Orchestrator SHALL return entries read from the StateStore rather than the in-memory `_audit_trail` list, in the order they were originally appended. IF the StateStore read fails (`StateStore.get_audit_trail()` returns `None` per Requirement 5.3's exception), THEN THE Orchestrator SHALL fall back to returning its in-memory `_audit_trail` cache for that call rather than an empty list, so a transient read error is not indistinguishable from "no history exists."
4. THE relationship between `audit.log` and the StateStore's `audit_trail` table SHALL be documented as: `audit.log` remains the durable, human-diffable, append-only record intended for tamper-resistance and out-of-band review; the StateStore's `audit_trail` table is the queryable structured mirror consumed by `get_audit_trail()` and, in a future phase, by OBS-2's query/export API. Both are written on every `_log_action()` call; neither replaces the other.
5. IF the Orchestrator process restarts, THEN `get_audit_trail()` on the new instance SHALL include every entry logged by any prior instance that shared the same StateStore file, not only entries logged since the current process started.

### Requirement 5: Fail-Safe Behavior on Store Errors

**User Story:** As an operator, I want a corrupted or unreadable state database to fail in a safe, predictable direction rather than crash the application or silently fabricate state.

#### Acceptance Criteria

1. IF the StateStore's underlying SQLite file exists but is not a valid SQLite database (e.g., truncated or non-database content) — a genuine `sqlite3.DatabaseError` that is NOT a `sqlite3.OperationalError` (see criterion 6 below) — THEN constructing a `StateStore` against that path SHALL raise a `StateStoreCorruptedError` at construction time — not deferred to first use — and SHALL log an ERROR identifying the path and the underlying `sqlite3` exception. A `PRAGMA user_version` newer than this build's `_SCHEMA_VERSION` SHALL also raise `StateStoreCorruptedError` rather than proceed with best-effort compatibility, since no version-aware read/write logic exists to back such a promise, consistent with the no-migration-path stance of Requirement 7.2.
2. WHEN `StateStore` construction raises `StateStoreCorruptedError`, THE Orchestrator SHALL refuse to start, matching the existing fail-fast pattern already used for TF_CMD validation and required-output-directory creation failures in `Orchestrator.__init__`.
3. IF an individual read operation (`get_plan`, `has_pending_rollback`, `get_audit_trail`) raises a `sqlite3.Error` after the StateStore was already successfully constructed (e.g., a transient disk I/O error), THEN THE StateStore SHALL log a WARNING and return the "not found"/empty value for that call (`None`, `False`, or `[]`) rather than propagating the exception. EXCEPTION: `get_audit_trail()` SHALL return `None` (not `[]`) specifically on a read failure, so that `Orchestrator.get_audit_trail()` can distinguish "StateStore read failed" from "the audit trail is genuinely empty" (`[]`) and fall back to its in-memory `_audit_trail` cache in the former case (see Requirement 4.3).
4. IF an individual write operation (`replace_plans`, `add_pending_rollback`, `discard_pending_rollback`) raises a `sqlite3.Error`, THEN THE StateStore SHALL log a WARNING, return `False`, and SHALL NOT leave the affected table in a partially written state — the underlying SQLite transaction SHALL roll back.
5. IF `replace_plans()` fails per criterion 4, THEN THE Orchestrator SHALL append a message to a new `AuditResult.warnings: list[str]` field (returned even when `AuditResult.success` is `True`) rather than silently reporting a fully successful audit run while plan durability could not be guaranteed.
6. IF `StateStore` construction fails because the underlying SQLite connection raises `sqlite3.OperationalError` (e.g., "database is locked" from another process's exclusive lock, an antivirus scanner hold, or a slow WAL checkpoint on another connection) — a transient condition, not corruption, even though `sqlite3.OperationalError` is a subclass of `sqlite3.DatabaseError` — THEN constructing a `StateStore` SHALL raise `StateStoreUnavailableError` instead of `StateStoreCorruptedError`, and SHALL log a WARNING (not ERROR) identifying the path and noting the failure is expected to be retryable. THE Orchestrator MAY retry construction once after a short delay, or SHALL otherwise surface a "database busy, try again" message rather than treating this identically to corruption.

### Requirement 6: Multi-Process Concurrency Safety

**User Story:** As an operator running Cloud Janitor with multiple Streamlit worker processes or concurrent CLI invocations against the same output directory, I want reads and writes to the state database to remain consistent and non-corrupting under concurrent access.

#### Acceptance Criteria

1. THE StateStore SHALL enable SQLite's Write-Ahead Logging mode (`PRAGMA journal_mode=WAL`) so that readers are not blocked by an in-progress writer and vice versa.
2. THE StateStore SHALL set a `busy_timeout` (default 5000 ms) so that a writer contending with another writer waits and retries internally rather than immediately raising `sqlite3.OperationalError: database is locked`.
3. WHEN two `StateStore` instances in separate processes write to different tables (e.g., one appending an audit entry while another replaces plans) at overlapping times, THE final state of both tables SHALL reflect both writes — neither write SHALL be silently lost, absent contention that exhausts `append_audit_entry()`'s bounded retry budget (criterion 6 below). That retry budget uses its own short, retry-scoped `busy_timeout` (not the default 5000 ms `busy_timeout` used by every other operation), so the total worst-case latency of a single `append_audit_entry()` call — across all retry attempts and backoff combined — is bounded to roughly 1 second, not to a multiple of the default `busy_timeout` compounded across attempts. IF that budget is exhausted and a write is dropped, THEN THE StateStore SHALL log this at WARNING level in a form distinguishable from a routine warning (e.g., including a `data_loss` marker in the log message), so operators can identify audit-trail gaps during log review rather than mistaking the loss for a benign, self-healing retry.
4. THE StateStore SHALL wrap each logical write (e.g., the `DELETE`+`INSERT` sequence inside `replace_plans()`) in a single SQLite transaction, so that a crash or lock-wait timeout mid-write leaves the previous state intact rather than a half-replaced table.
5. THE design SHALL document that WAL mode requires the database file and its directory to reside on a filesystem that supports POSIX-style file locking (true for local disk and the project's existing `output/` directory; not guaranteed on some network filesystems, e.g. NFS- or SMB-mounted `output/` directories) — this is a documented constraint of the single-shared-host deployment model this phase targets, not a defect to be engineered around.
6. Because the `audit_trail` table is the compliance-critical table referenced in Requirement 4's "compliance-focused operator" framing, THE StateStore SHALL apply a bounded retry with short backoff specifically around `append_audit_entry()` (e.g., up to 3 attempts with short increasing backoff) before giving up and returning `False` — distinct from, and in addition to, SQLite's own `busy_timeout` retry and the read path's single-attempt fail-soft behavior (Requirement 5.3) — reflecting that silent loss matters most for this table. TO KEEP this retry bounded in TIME as well as in attempt count, EACH retry attempt SHALL be scoped to a substantially shorter `busy_timeout` (e.g., ~200-300 ms) applied only for the duration of `append_audit_entry()`'s own retry loop, distinct from, and not compounding, the default 5000 ms `busy_timeout` used by every other StateStore operation (Requirement 6.2) — re-incurring the full default `busy_timeout` on each of 3 attempts would otherwise produce a worst case of roughly 15 seconds for a single `_log_action()` call, unacceptable given `_log_action()` runs synchronously from `approve()`/`rollback()` and is called 8+ times within a single `execute_audit()` run with no circuit breaker. THE resulting worst-case latency for a single `append_audit_entry()` call, across all attempts and backoff, SHALL be documented and bounded to approximately 1 second.

### Requirement 7: Orchestrator Integration and Fresh-Install Compatibility

**User Story:** As a developer, I want the StateStore wired into the Orchestrator the same way the existing `ApprovalGateStore` is, with no backward-compatibility burden, so the change is a straightforward generalization rather than a data-migration project.

#### Acceptance Criteria

1. THE Orchestrator SHALL construct exactly one `StateStore` instance in `__init__`, using a `STATE_STORE_PATH` constant added to `core/paths.py` when using the default project root, and an equivalent path under the custom `output_dir` for the existing custom-root (test) code path — mirroring the existing `_gate_store` construction pattern exactly.
2. Since `_last_plans`, `_pending_rollbacks`, and `_audit_trail` have never been persisted in any prior release, THE Orchestrator SHALL NOT implement any migration, import, or upgrade path from a prior on-disk format — a freshly created SQLite file is the only supported starting state.
3. THE Orchestrator SHALL treat the StateStore as the sole source of truth for reads and writes of plans, pending rollbacks, and the audit trail performed by `approve()`, `rollback()`, `_handle_confirm_rollback()`, and `get_audit_trail()`; any retained in-memory attributes SHALL NOT be read by these methods once this phase lands.
4. THE `StateStore` SHALL be constructed and its schema created during `Orchestrator.__init__`, following the same fail-fast-at-startup convention already used by `ensure_output_dirs()` and TF_CMD validation.
