"""Property tests for severity escalation diff (Property 2)."""

from hypothesis import given, settings, strategies as st

from cloud_janitor.core.scan_diff import diff_high_severity_findings, _RANK

SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

# Strategy: resource IDs as simple ASCII strings (no null bytes)
resource_id_st = st.text(
    min_size=1,
    max_size=20,
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
)

# Strategy: a single finding dict
finding_st = st.fixed_dictionaries({
    "resource_id": resource_id_st,
    "severity": st.sampled_from(SEVERITIES),
    "description": st.text(max_size=50, alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00")),
})

# Strategy: previous snapshot (resource_id → severity)
snapshot_st = st.dictionaries(
    keys=resource_id_st,
    values=st.sampled_from(SEVERITIES),
    max_size=20,
)


@settings(max_examples=100, deadline=None)
@given(previous=snapshot_st, current=st.lists(finding_st, max_size=20))
def test_property_only_high_or_critical_returned(previous, current):
    """All returned findings have severity HIGH or CRITICAL."""
    result = diff_high_severity_findings(previous, current)
    for f in result:
        assert _RANK.get(f.get("severity", "LOW"), 0) >= _RANK["HIGH"]


@settings(max_examples=100, deadline=None)
@given(previous=snapshot_st, current=st.lists(finding_st, max_size=20))
def test_property_returned_only_if_new_or_escalated(previous, current):
    """A returned finding is either new (not in previous) or strictly escalated."""
    result = diff_high_severity_findings(previous, current)
    for f in result:
        rid = f["resource_id"]
        severity = f.get("severity", "LOW")
        prior = previous.get(rid)
        assert prior is None or _RANK.get(prior, 0) < _RANK.get(severity, 0)


@settings(max_examples=100, deadline=None)
@given(previous=snapshot_st, current=st.lists(finding_st, max_size=20))
def test_property_completeness_no_qualifying_finding_omitted(previous, current):
    """Every HIGH/CRITICAL finding that is new or escalated appears in the result."""
    result = diff_high_severity_findings(previous, current)
    result_set = {id(f) for f in result}
    for finding in current:
        severity = finding.get("severity", "LOW")
        if _RANK.get(severity, 0) < _RANK["HIGH"]:
            continue
        prior = previous.get(finding["resource_id"])
        if prior is None or _RANK.get(prior, 0) < _RANK.get(severity, 0):
            assert id(finding) in result_set, (
                f"Qualifying finding {finding} was omitted from result"
            )


@settings(max_examples=100, deadline=None)
@given(previous=snapshot_st, current=st.lists(finding_st, max_size=20))
def test_property_result_is_subset_of_current(previous, current):
    """Every finding in the result is also in the current list (no fabricated entries)."""
    result = diff_high_severity_findings(previous, current)
    current_ids = {id(f) for f in current}
    for f in result:
        assert id(f) in current_ids
