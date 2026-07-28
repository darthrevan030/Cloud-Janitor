"""Query/export layer over phase2's StateStore.audit_trail table.

Does not implement persistence — see phase2-persistent-state for the
StateStore itself. This module assumes a table with columns matching
AuditEntry's fields plus `run_id` (already part of phase2's schema).

This spec is gated on phase2-persistent-state shipping for end-to-end
enablement; the functions below can be developed and unit-tested against
an in-memory SQLite fixture matching the assumed schema before phase2
ships, since they have no runtime dependency on a real StateStore
instance beyond its documented shape — only on `execute_readonly_query()`
being present with phase2's documented signature and behavior.
"""

from __future__ import annotations

import csv
import io
import json

# Column order matches phase2-persistent-state's audit_trail DDL exactly
# (see phase2-persistent-state's design.md _SCHEMA_DDL). Selected explicitly
# by name (not `SELECT *`) so this module's row-to-dict mapping does not
# silently depend on the physical column order phase2 happens to create the
# table with.
_AUDIT_TRAIL_COLUMNS = (
    "id",
    "timestamp",
    "actor",
    "action",
    "resource_id",
    "result",
    "details",
    "run_id",
)

_ALLOWED_FILTERS = {"resource_id", "actor", "result", "action", "run_id"}


class _UnscopedType:
    """Sentinel type for UNSCOPED — see UNSCOPED below."""

    def __repr__(self) -> str:
        return "UNSCOPED"


UNSCOPED = _UnscopedType()
"""Pass as `run_id=UNSCOPED` to query_audit() to filter to entries with
`run_id IS NULL` — i.e. actions logged outside any execute_audit() run
(phase2's audit_trail.run_id column is nullable for exactly this case).

This is distinct from the default `run_id=None`, which means "do not filter
on run_id at all," matching every other query_audit() filter parameter's
"None means no filter" convention. Without a value distinct from None,
"show me only unscoped entries" would be inexpressible through this API,
since None is already taken to mean "no filter."
"""


def query_audit(
    state_store,
    resource_id: str | None = None,
    actor: str | None = None,
    result: str | None = None,
    action: str | None = None,
    run_id: str | None | object = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 1000,
) -> list[dict]:
    """Query state_store.audit_trail with a parameterized WHERE clause.

    Calls `state_store.execute_readonly_query(sql, params)` — the public,
    lock-guarded method phase2-persistent-state's StateStore exposes
    specifically for this purpose (see phase2's design.md). StateStore has
    no public `.connection` attribute; this function never reaches for one,
    and never interpolates a filter *value* into the SQL string (column
    names come only from the fixed, hardcoded tuples below — never from
    caller input).

    Data source: phase2-persistent-state's StateStore (see
    src/cloud_janitor/core/state_store.py). This function is the read/query
    layer — it does not implement persistence.

    `run_id` has three distinct meanings:
      - `None` (the default): no filter on run_id at all.
      - `UNSCOPED` (this module's sentinel, see above): filter to
        `run_id IS NULL` specifically.
      - any other string: filter to `run_id = <that string>`.

    Raises `ValueError` if an internal filter name is not recognized in
    `_ALLOWED_FILTERS` — defense-in-depth so a future edit that adds a new
    filter parameter without updating the allowlist (or vice versa) fails
    loudly instead of silently applying no filter for that column.
    """
    equality_filters = {
        "resource_id": resource_id,
        "actor": actor,
        "result": result,
        "action": action,
    }

    # Active guard: validate all filter names against _ALLOWED_FILTERS
    for name in equality_filters:
        if name not in _ALLOWED_FILTERS:
            raise ValueError(f"Unrecognized audit_trail filter: {name!r}")
    if "run_id" not in _ALLOWED_FILTERS:
        raise ValueError("Unrecognized audit_trail filter: 'run_id'")

    clauses: list[str] = []
    params: list[str] = []

    for column, value in equality_filters.items():
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)

    # Handle the three-way run_id semantics
    if isinstance(run_id, _UnscopedType):
        clauses.append("run_id IS NULL")
    elif run_id is not None:
        clauses.append("run_id = ?")
        params.append(str(run_id))

    if date_from is not None:
        clauses.append("timestamp >= ?")
        params.append(date_from)
    if date_to is not None:
        clauses.append("timestamp <= ?")
        params.append(date_to)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    columns_sql = ", ".join(_AUDIT_TRAIL_COLUMNS)
    sql = f"SELECT {columns_sql} FROM audit_trail {where} ORDER BY timestamp DESC LIMIT ?"
    params.append(str(limit))

    rows = state_store.execute_readonly_query(sql, tuple(params))
    return [dict(zip(_AUDIT_TRAIL_COLUMNS, row)) for row in rows]


def export_audit_csv(rows: list[dict]) -> str:
    """Serialize a query_audit() result set to CSV text.

    Returns "" for an empty row list without raising.
    """
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def export_audit_json(rows: list[dict]) -> str:
    """Serialize a query_audit() result set to JSON text.

    Uses `default=str` so datetime and other non-JSON-native types
    are serialized without raising.
    """
    return json.dumps(rows, indent=2, default=str)
