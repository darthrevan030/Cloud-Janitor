"""Property-based tests for audit query filter conjunction.

Property 1: Audit Query Filter Conjunction
  For any non-empty combination of query_audit() filter arguments and any
  audit_trail table contents, every row returned SHALL satisfy all supplied
  filters, and no row satisfying all supplied filters SHALL be excluded
  (subject to limit/ordering).

Validates: Requirements 1.1, 1.2
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from cloud_janitor.core.audit_query import query_audit, UNSCOPED


# --- Minimal StateStore stand-in ---


@dataclass
class _FakeStateStore:
    """Wraps an in-memory sqlite3.Connection exposing execute_readonly_query."""

    conn: sqlite3.Connection

    def execute_readonly_query(self, sql: str, params: tuple = ()) -> list[tuple]:
        cursor = self.conn.execute(sql, params)
        return cursor.fetchall()


# --- Schema setup ---

_CREATE_TABLE = """\
CREATE TABLE audit_trail (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    result TEXT NOT NULL,
    details TEXT NOT NULL,
    run_id TEXT
)
"""


def _make_store(rows: list[dict]) -> _FakeStateStore:
    """Create an in-memory SQLite DB seeded with the given rows."""
    conn = sqlite3.connect(":memory:")
    conn.execute(_CREATE_TABLE)
    for row in rows:
        conn.execute(
            "INSERT INTO audit_trail (id, timestamp, actor, action, resource_id, result, details, run_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                row["timestamp"],
                row["actor"],
                row["action"],
                row["resource_id"],
                row["result"],
                row["details"],
                row["run_id"],
            ),
        )
    conn.commit()
    return _FakeStateStore(conn=conn)


# --- Strategies ---

_safe_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S"),
        blacklist_categories=("Cs",),
        blacklist_characters="\x00",
    ),
    min_size=1,
    max_size=20,
)

_timestamp_strategy = st.tuples(
    st.integers(min_value=2020, max_value=2026),
    st.integers(min_value=1, max_value=12),
    st.integers(min_value=1, max_value=28),
    st.integers(min_value=0, max_value=23),
    st.integers(min_value=0, max_value=59),
    st.integers(min_value=0, max_value=59),
).map(lambda t: f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d}T{t[3]:02d}:{t[4]:02d}:{t[5]:02d}")

_action_values = st.sampled_from(["approve", "rollback", "scan", "remediate", "flag"])
_result_values = st.sampled_from(["success", "failure", "skipped", "blocked"])

_run_id_strategy = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            blacklist_categories=("Cs",),
            blacklist_characters="\x00",
        ),
        min_size=3,
        max_size=15,
    ).map(lambda s: f"run-{s}"),
)

_audit_row_strategy = st.fixed_dictionaries(
    {
        "id": st.uuids().map(str),
        "timestamp": _timestamp_strategy,
        "actor": _safe_text,
        "action": _action_values,
        "resource_id": _safe_text,
        "result": _result_values,
        "details": _safe_text,
        "run_id": _run_id_strategy,
    }
)


# Strategy for filter combinations: at least one filter must be active
@st.composite
def _filter_combo(draw):
    """Draw a non-empty combination of filter arguments for query_audit().

    Returns a dict of keyword arguments to pass to query_audit().
    """
    filters = {}

    # Equality filters - each independently may or may not be active
    if draw(st.booleans()):
        filters["resource_id"] = draw(_safe_text)
    if draw(st.booleans()):
        filters["actor"] = draw(_safe_text)
    if draw(st.booleans()):
        filters["result"] = draw(_result_values)
    if draw(st.booleans()):
        filters["action"] = draw(_action_values)

    # run_id: None (no filter), UNSCOPED (IS NULL), or a string
    run_id_choice = draw(st.sampled_from(["none", "unscoped", "string"]))
    if run_id_choice == "unscoped":
        filters["run_id"] = UNSCOPED
    elif run_id_choice == "string":
        filters["run_id"] = draw(
            st.text(
                alphabet=st.characters(
                    whitelist_categories=("Ll", "Lu", "Nd"),
                    blacklist_categories=("Cs",),
                    blacklist_characters="\x00",
                ),
                min_size=3,
                max_size=15,
            ).map(lambda s: f"run-{s}")
        )
    # else: run_id not set (default None = no filter)

    # Date range filters
    if draw(st.booleans()):
        filters["date_from"] = draw(_timestamp_strategy)
    if draw(st.booleans()):
        filters["date_to"] = draw(_timestamp_strategy)

    # Ensure at least one filter is active
    assume(len(filters) > 0)

    return filters


def _row_matches_filters(row: dict, filters: dict) -> bool:
    """Independent oracle: does a row satisfy all active filters?"""
    if "resource_id" in filters and row["resource_id"] != filters["resource_id"]:
        return False
    if "actor" in filters and row["actor"] != filters["actor"]:
        return False
    if "result" in filters and row["result"] != filters["result"]:
        return False
    if "action" in filters and row["action"] != filters["action"]:
        return False

    # run_id handling
    if "run_id" in filters:
        run_id_filter = filters["run_id"]
        if isinstance(run_id_filter, type(UNSCOPED)):
            # UNSCOPED means run_id IS NULL
            if row["run_id"] is not None:
                return False
        else:
            # String match
            if row["run_id"] != run_id_filter:
                return False

    # Date range
    if "date_from" in filters:
        if row["timestamp"] < filters["date_from"]:
            return False
    if "date_to" in filters:
        if row["timestamp"] > filters["date_to"]:
            return False

    return True


# ──────────────────────────────────────────────────────────────────────────────
# Property 1: Audit Query Filter Conjunction
# ──────────────────────────────────────────────────────────────────────────────


@settings(deadline=None, max_examples=100)
@given(
    rows=st.lists(_audit_row_strategy, min_size=0, max_size=20),
    filters=_filter_combo(),
    limit=st.integers(min_value=1, max_value=100),
)
def test_filter_conjunction_soundness_and_completeness(
    rows: list[dict], filters: dict, limit: int
):
    """For any non-empty filter combination and any audit_trail contents:

    1. SOUNDNESS: every returned row satisfies ALL supplied filters.
    2. COMPLETENESS: no row satisfying all filters is excluded (subject to
       limit, ordered by timestamp DESC).

    **Validates: Requirements 1.1, 1.2**
    """
    store = _make_store(rows)

    # Call query_audit with the generated filters and limit
    result = query_audit(store, limit=limit, **filters)

    # --- SOUNDNESS: every returned row matches all filters ---
    for returned_row in result:
        assert _row_matches_filters(returned_row, filters), (
            f"Returned row does not satisfy all filters.\n"
            f"Row: {returned_row}\nFilters: {filters}"
        )

    # --- COMPLETENESS (subject to limit + ordering) ---
    # Compute the expected result set independently: all matching rows,
    # sorted by timestamp DESC, truncated to limit.
    matching_rows = [r for r in rows if _row_matches_filters(r, filters)]
    matching_rows.sort(key=lambda r: r["timestamp"], reverse=True)
    expected = matching_rows[:limit]

    # The returned IDs must exactly equal the expected IDs (preserving order)
    returned_ids = [r["id"] for r in result]
    expected_ids = [r["id"] for r in expected]
    assert returned_ids == expected_ids, (
        f"Result set mismatch.\n"
        f"Returned IDs: {returned_ids}\n"
        f"Expected IDs: {expected_ids}\n"
        f"Filters: {filters}\n"
        f"Limit: {limit}"
    )
