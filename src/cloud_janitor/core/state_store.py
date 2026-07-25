"""Persistent state store for plans, pending rollbacks, and the audit trail.

Generalizes the ApprovalGateStore persistence pattern (agents/approval_gate.py)
to the remaining mutable Orchestrator state. Backed by SQLite (stdlib sqlite3)
in WAL mode — see design.md "Key Architectural Decisions" for why SQLite was
chosen over Postgres/Redis for this phase.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cloud_janitor.agents.remediation_architect import RemediationPlan
    from cloud_janitor.orchestrator.orchestrator import AuditEntry

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

# NOTE: no FOREIGN KEY constraint declares the plans.run_id / audit_trail.run_id
# relationship. This is intentional, not an oversight: replace_plans() performs
# a wholesale DELETE+INSERT of the plans table per run (Req 2.5), and a real FK
# from audit_trail.run_id -> plans.run_id would force ON DELETE semantics/ordering
# decisions that this persistence-only phase has no need to make (audit_trail
# rows must survive a plans DELETE regardless of run_id). Consequently no
# `PRAGMA foreign_keys` pragma is set either — there being no FK constraint to
# enforce, the pragma would be inert.
_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS plans (
    resource_id             TEXT PRIMARY KEY,
    run_id                  TEXT NOT NULL,
    finding_json            TEXT NOT NULL,
    blocked                 INTEGER NOT NULL DEFAULT 0,
    block_reason            TEXT NOT NULL DEFAULT '',
    dependency_report_json  TEXT,
    remediation_hcl         TEXT,
    rollback_hcl            TEXT,
    created_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_rollbacks (
    resource_id  TEXT PRIMARY KEY,
    requested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_trail (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    result      TEXT NOT NULL,
    details     TEXT NOT NULL DEFAULT '',
    run_id      TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_trail(resource_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_trail(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_run_id ON audit_trail(run_id);
CREATE INDEX IF NOT EXISTS idx_plans_run_id ON plans(run_id);
"""


class StateStoreCorruptedError(Exception):
    """Raised when the underlying SQLite file exists but cannot be opened
    or queried as a valid database — a genuine sqlite3.DatabaseError that is
    NOT a sqlite3.OperationalError (see StateStoreUnavailableError below) —
    or when its PRAGMA user_version is newer than this build's _SCHEMA_VERSION.
    Callers MUST treat this as a hard stop — the Orchestrator refuses to start
    (Req 5.1, 5.2)."""


class StateStoreUnavailableError(Exception):
    """Raised when StateStore construction fails for a transient reason —
    a sqlite3.OperationalError such as "database is locked" — rather than
    genuine corruption. sqlite3.OperationalError IS a subclass of
    sqlite3.DatabaseError, so it must be caught and handled separately from
    StateStoreCorruptedError; conflating the two would make the Orchestrator
    refuse to start on ordinary transient lock contention (e.g. an antivirus
    scanner hold, or another connection's in-progress WAL checkpoint) — the
    exact class of event WAL mode + busy_timeout exist to tolerate. Callers
    MAY retry construction once after a short delay (Req 5.6)."""


class StateStore:
    """Persists plans, pending rollbacks, and the audit trail to a single
    SQLite database file, in WAL mode for multi-process safety.

    Fresh files are created automatically — there is no prior on-disk
    format to migrate from (Req 7.2).
    """

    _AUDIT_APPEND_MAX_ATTEMPTS = 3
    _AUDIT_APPEND_BACKOFF_SECONDS = (0.05, 0.15)
    _AUDIT_APPEND_RETRY_BUSY_TIMEOUT_MS = 250

    def __init__(self, db_path: Path, busy_timeout_ms: int = 5000) -> None:
        self._path = db_path
        # Defense-in-depth guard around self._conn (see design.md "Concurrency
        # assumption for a single session"): overlapping calls into the same
        # StateStore instance are not expected today, but the lock costs
        # nothing and protects against that assumption changing later.
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        # Retained so append_audit_entry() can restore the connection-level
        # busy_timeout after temporarily shortening it for its own retry loop
        # (see Component 4 — Req 6.6 latency fix).
        self._busy_timeout_ms = busy_timeout_ms
        initialized = False
        try:
            # mkdir() lives INSIDE this try block (not before it, as an earlier
            # draft had it) specifically so an OSError here — permission
            # denied, a read-only filesystem, a path component that is
            # actually a file, etc. — is caught below and raised as
            # StateStoreUnavailableError instead of propagating as a raw,
            # unhandled OSError. A directory-permission problem is an
            # environmental/transient condition, not database corruption, so
            # it belongs in this design's existing two-exception taxonomy
            # (StateStoreCorruptedError / StateStoreUnavailableError) rather
            # than escaping it entirely.
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self._path),
                timeout=busy_timeout_ms / 1000,
                check_same_thread=False,
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            # busy_timeout_ms and _SCHEMA_VERSION are internal module/constructor
            # constants, never user-supplied input — f-string interpolation here
            # carries no injection risk. Called out explicitly since raw
            # interpolation would be unsafe practice if either value ever
            # originated from external input.
            self._conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            with self._conn:
                # NOTE: executescript() issues its own implicit COMMIT before
                # running and does NOT participate in this `with self._conn:`
                # block's transaction the normal way — it is not atomic together
                # with the PRAGMA user_version check below. This is safe in
                # practice only because every statement in _SCHEMA_DDL is
                # idempotent (`CREATE ... IF NOT EXISTS`), not because of any
                # transactional guarantee from the surrounding `with` block.
                self._conn.executescript(_SCHEMA_DDL)
                current_version = self._conn.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                if current_version == 0:
                    self._conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
                elif current_version > _SCHEMA_VERSION:
                    # No migration path exists (Req 7.2) — there is no version-
                    # aware read/write logic to back a "proceed with best-effort
                    # compatibility" promise, so this fails closed instead,
                    # consistent with the corruption-guard's fail-fast stance.
                    raise StateStoreCorruptedError(
                        f"State database at {self._path} has schema version "
                        f"{current_version}, newer than this build's "
                        f"{_SCHEMA_VERSION}. Move the file aside (see recovery "
                        f"guidance) or upgrade Cloud Janitor."
                    )
            initialized = True
        except StateStoreCorruptedError:
            # Re-raise without wrapping — this was already raised intentionally
            # by the user_version check above.
            raise
        except OSError as exc:
            # Directory creation failed. Listed first because mkdir() is the
            # first operation in this try block; ordering relative to the
            # sqlite3.* handlers below doesn't otherwise matter since OSError
            # and sqlite3.Error are disjoint exception hierarchies. Treated as
            # StateStoreUnavailableError, not StateStoreCorruptedError: the
            # database file itself was never opened or found corrupted, and a
            # permissions/filesystem problem may well be transient (e.g.
            # resolved once an operator fixes directory permissions), so it
            # should not be conflated with genuine database corruption.
            logger.warning(
                "StateStore could not create directory %s (%s) — treating as "
                "unavailable, not corrupted.",
                self._path.parent,
                exc,
            )
            raise StateStoreUnavailableError(
                f"State database directory {self._path.parent} could not be "
                f"created: {exc}"
            ) from exc
        except sqlite3.OperationalError as exc:
            # MUST be caught before sqlite3.DatabaseError below —
            # OperationalError is a DatabaseError subclass, and a transient
            # lock hit here (e.g. "database is locked") is not corruption.
            #
            # KNOWN IMPRECISION (documented, not fixed here): sqlite3.OperationalError
            # is also raised for some NON-transient conditions that are not lock
            # contention at all — e.g. "unable to open database file" (a
            # permissions problem distinct from the mkdir() case above) or
            # "disk I/O error" (failing storage). Every OperationalError reaching
            # this except clause is treated identically as "transient, safe to
            # retry," which could mask a genuine, non-recoverable problem behind
            # misleading "try again" messaging. This is a diagnostics-quality gap,
            # not a correctness bug: the original sqlite3 exception message is
            # preserved via `raise ... from exc` chaining, so the real cause is
            # still visible to anyone inspecting the full traceback or logging
            # `exc` directly. A full fix would parse the SQLite error message/code
            # to distinguish lock-contention from I/O-type errors before choosing
            # StateStoreUnavailableError vs. StateStoreCorruptedError; deliberately
            # not implemented here — judged not worth the added complexity for a
            # persistence-only phase (see design.md's Key Architectural Decisions).
            logger.warning(
                "StateStore at %s is temporarily unavailable (%s) — likely "
                "transient lock contention (antivirus scan hold, another "
                "connection's WAL checkpoint, or ordinary contention), not "
                "corruption. Safe to retry.",
                self._path,
                exc,
            )
            raise StateStoreUnavailableError(
                f"State database at {self._path} is temporarily unavailable: "
                f"{exc}"
            ) from exc
        except sqlite3.DatabaseError as exc:
            logger.error(
                "StateStore at %s is corrupted or unreadable: %s",
                self._path,
                exc,
            )
            raise StateStoreCorruptedError(
                f"State database at {self._path} could not be opened: {exc}"
            ) from exc
        finally:
            # sqlite3.connect() succeeds even against a corrupted file (SQLite
            # doesn't validate file format until first real access) — so on
            # any failure path above, self._conn is a live handle that must be
            # closed before we re-raise, or it leaks a connection every time
            # (compounding under Streamlit's one-StateStore-per-session pattern).
            # On the OSError path above, self._conn is never assigned (mkdir()
            # fails before sqlite3.connect() is reached), so this is a no-op
            # there — nothing to close.
            if not initialized and self._conn is not None:
                self._conn.close()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()

    def replace_plans(self, plans: list["RemediationPlan"], run_id: str) -> bool:
        """Atomically replace the entire plans table with a new batch.

        Matches the existing `self._last_plans = plans` wholesale-replacement
        semantics (Req 2.5) rather than merging with prior runs.
        """
        try:
            with self._lock, self._conn:
                self._conn.execute("DELETE FROM plans")
                self._conn.executemany(
                    """INSERT INTO plans (resource_id, run_id, finding_json, blocked, block_reason,
                       dependency_report_json, remediation_hcl, rollback_hcl, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                    [
                        (
                            p.resource_id,
                            run_id,
                            json.dumps(p.finding),
                            int(p.blocked),
                            p.block_reason,
                            json.dumps(asdict(p.dependency_report)) if p.dependency_report else None,
                            p.remediation_hcl,
                            p.rollback_hcl,
                        )
                        for p in plans
                    ],
                )
            return True
        except sqlite3.Error as exc:
            logger.warning("StateStore.replace_plans failed (%s): %s", type(exc).__name__, exc)
            return False

    def get_plan(self, resource_id: str) -> "RemediationPlan | None":
        """Return the plan for resource_id, or None if absent, blocked, or on read error.

        Blocked plans are intentionally excluded to preserve the existing
        `_find_plan()` filter (Req 2.3): a blocked plan is treated identically
        to "no plan found" by approve().
        """
        from cloud_janitor.agents.remediation_architect import DependencyReport, RemediationPlan

        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT resource_id, finding_json, blocked, block_reason, "
                    "dependency_report_json, remediation_hcl, rollback_hcl "
                    "FROM plans WHERE resource_id = ?",
                    (resource_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            logger.warning("StateStore.get_plan failed (%s): %s", type(exc).__name__, exc)
            return None

        if row is None or bool(row[2]):  # missing, or blocked
            return None

        dep_json = row[4]
        dependency_report = DependencyReport(**json.loads(dep_json)) if dep_json else None
        return RemediationPlan(
            resource_id=row[0],
            finding=json.loads(row[1]),
            blocked=bool(row[2]),
            block_reason=row[3],
            dependency_report=dependency_report,
            remediation_hcl=row[5],
            rollback_hcl=row[6],
        )

    # ------------------------------------------------------------------
    # Pending rollbacks
    # ------------------------------------------------------------------

    def add_pending_rollback(self, resource_id: str) -> bool:
        """Record a pending rollback request. Idempotent (INSERT OR IGNORE)."""
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT OR IGNORE INTO pending_rollbacks (resource_id, requested_at) "
                    "VALUES (?, datetime('now'))",
                    (resource_id,),
                )
            return True
        except sqlite3.Error as exc:
            logger.warning("StateStore.add_pending_rollback failed (%s): %s", type(exc).__name__, exc)
            return False

    def has_pending_rollback(self, resource_id: str) -> bool:
        """Check whether a pending rollback exists for the given resource."""
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT 1 FROM pending_rollbacks WHERE resource_id = ?", (resource_id,)
                ).fetchone()
            return row is not None
        except sqlite3.Error as exc:
            logger.warning("StateStore.has_pending_rollback failed (%s): %s", type(exc).__name__, exc)
            return False

    def discard_pending_rollback(self, resource_id: str) -> bool:
        """Remove a pending rollback record by resource_id."""
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "DELETE FROM pending_rollbacks WHERE resource_id = ?", (resource_id,)
                )
            return True
        except sqlite3.Error as exc:
            logger.warning("StateStore.discard_pending_rollback failed (%s): %s", type(exc).__name__, exc)
            return False

    # ------------------------------------------------------------------
    # Audit trail
    # ------------------------------------------------------------------

    def append_audit_entry(self, entry: "AuditEntry", run_id: str | None = None) -> bool:
        """Insert one audit row with bounded retry and short per-attempt busy_timeout."""
        last_exc: sqlite3.Error | None = None
        with self._lock:
            self._conn.execute(
                f"PRAGMA busy_timeout={self._AUDIT_APPEND_RETRY_BUSY_TIMEOUT_MS}"
            )
            try:
                for attempt in range(self._AUDIT_APPEND_MAX_ATTEMPTS):
                    try:
                        with self._conn:
                            self._conn.execute(
                                "INSERT INTO audit_trail (timestamp, actor, action, resource_id, result, details, run_id) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (entry.timestamp, entry.actor, entry.action, entry.resource_id,
                                 entry.result, entry.details, run_id),
                            )
                        return True
                    except sqlite3.Error as exc:
                        last_exc = exc
                        if attempt < self._AUDIT_APPEND_MAX_ATTEMPTS - 1:
                            time.sleep(self._AUDIT_APPEND_BACKOFF_SECONDS[attempt])
            finally:
                self._conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")

        logger.warning(
            "StateStore.append_audit_entry failed after %d attempts (%s): %s "
            "[data_loss] audit entry for %s/%s was NOT persisted to state.db "
            "(it IS still in audit.log via AuditLogger).",
            self._AUDIT_APPEND_MAX_ATTEMPTS, type(last_exc).__name__, last_exc,
            entry.action, entry.resource_id,
        )
        return False

    def get_audit_trail(self) -> "list[AuditEntry] | None":
        """Returns AuditEntry objects in append order, or None on read failure."""
        from cloud_janitor.orchestrator.orchestrator import AuditEntry

        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT timestamp, action, resource_id, actor, result, details "
                    "FROM audit_trail ORDER BY id ASC"
                ).fetchall()
        except sqlite3.Error as exc:
            logger.warning("StateStore.get_audit_trail failed (%s): %s", type(exc).__name__, exc)
            return None

        return [
            AuditEntry(timestamp=r[0], action=r[1], resource_id=r[2], actor=r[3],
                       result=r[4], details=r[5])
            for r in rows
        ]

    def execute_readonly_query(self, sql: str, params: tuple = ()) -> list[tuple]:
        """Execute an arbitrary read-only SQL statement and return all rows."""
        with self._lock:
            try:
                return self._conn.execute(sql, params).fetchall()
            except sqlite3.Error as exc:
                logger.warning(
                    "StateStore.execute_readonly_query failed (%s): %s",
                    type(exc).__name__, exc,
                )
                raise
