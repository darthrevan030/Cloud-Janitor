"""Property-based tests for MultiAccountOrchestrator.

**Validates: Requirements 9.2, 9.3, 9.4**

Property 18: MultiAccountOrchestrator Fault Isolation
When one account raises, remaining accounts succeed unaffected.

Property 19: MultiAccountOrchestrator Account ID Injection
Every finding in aggregate_findings has account_id matching its source account.

Property 20: MultiAccountOrchestrator Priority Sorting
by_account is sorted high → medium → low.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from agents.multi_account_orchestrator import (
    MultiAccountOrchestrator,
    PRIORITY_ORDER,
)


# ─── Strategies ──────────────────────────────────────────────────────────────


@st.composite
def account_id_strategy(draw):
    """Generate a 12-digit AWS account ID string."""
    digits = draw(st.text(alphabet="0123456789", min_size=12, max_size=12))
    return digits


@st.composite
def account_strategy(draw, priority=None):
    """Generate a valid account config dict for accounts.json."""
    acct_id = draw(account_id_strategy())
    name = draw(st.text(
        min_size=1, max_size=20,
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_ "),
    ))
    # Ensure name is non-empty after strip
    assume(name.strip() != "")
    region = draw(st.sampled_from(["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"]))
    if priority is None:
        priority = draw(st.sampled_from(["high", "medium", "low"]))
    role_arn = f"arn:aws:iam::{acct_id}:role/CloudJanitorReadOnly"
    return {
        "account_id": acct_id,
        "account_name": name.strip(),
        "role_arn": role_arn,
        "region": region,
        "priority": priority,
    }


@st.composite
def finding_strategy(draw):
    """Generate a finding dict as returned by _audit_account."""
    resource_id = draw(st.text(
        min_size=3, max_size=20,
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    ))
    return {
        "resource_id": resource_id,
        "resource_type": draw(st.sampled_from(["ebs", "ec2", "security_group", "elasticache"])),
        "check_type": draw(st.sampled_from(["encryption", "idle_resource", "open_port", "no_auth"])),
        "severity": draw(st.sampled_from(["LOW", "MEDIUM", "HIGH", "CRITICAL"])),
        "cost_estimate_monthly": draw(st.floats(min_value=0.0, max_value=1000.0, allow_nan=False)),
    }


@st.composite
def audit_result_strategy(draw):
    """Generate a successful _audit_account return value."""
    findings = draw(st.lists(finding_strategy(), min_size=0, max_size=5))
    waste = sum(f["cost_estimate_monthly"] for f in findings)
    critical_count = sum(1 for f in findings if f["severity"] == "CRITICAL")
    return {
        "findings": findings,
        "waste": waste,
        "critical_count": critical_count,
    }


@st.composite
def accounts_list_strategy(draw, min_size=2, max_size=5):
    """Generate a list of accounts with unique account_ids."""
    accounts = draw(st.lists(
        account_strategy(),
        min_size=min_size,
        max_size=max_size,
    ))
    # Ensure unique account_ids
    seen_ids = set()
    unique_accounts = []
    for acct in accounts:
        if acct["account_id"] not in seen_ids:
            seen_ids.add(acct["account_id"])
            unique_accounts.append(acct)
    assume(len(unique_accounts) >= min_size)
    return unique_accounts[:max_size]


def _write_accounts_file(tmp_dir: Path, accounts: list[dict]) -> Path:
    """Write accounts.json to a temp directory and return its path."""
    p = tmp_dir / "accounts.json"
    p.write_text(json.dumps(accounts), encoding="utf-8")
    return p


# ─── Property 18: Fault Isolation ────────────────────────────────────────────


class TestMultiAccountOrchestratorFaultIsolation:
    """Property 18: MultiAccountOrchestrator Fault Isolation.

    **Validates: Requirements 9.2**

    When one account raises, remaining accounts succeed unaffected.
    The orchestrator catches exceptions per-account and continues processing
    the rest.
    """

    @given(
        accounts=accounts_list_strategy(min_size=2, max_size=5),
        audit_results=st.lists(audit_result_strategy(), min_size=2, max_size=5),
    )
    @settings(max_examples=200, deadline=None)
    def test_one_account_failure_does_not_affect_others(self, accounts, audit_results):
        """When one account raises an exception, remaining accounts succeed."""
        # Ensure we have matching counts
        n = min(len(accounts), len(audit_results))
        assume(n >= 2)
        accounts = accounts[:n]
        audit_results = audit_results[:n]

        # The first account will raise, the rest will succeed
        failing_account_id = accounts[0]["account_id"]

        def mock_audit(account):
            if account["account_id"] == failing_account_id:
                raise RuntimeError("Simulated failure for property test")
            idx = next(
                i for i, a in enumerate(accounts) if a["account_id"] == account["account_id"]
            )
            return audit_results[idx]

        with tempfile.TemporaryDirectory() as tmp_dir:
            accounts_path = _write_accounts_file(Path(tmp_dir), accounts)

            with patch(
                "agents.multi_account_orchestrator.MultiAccountOrchestrator._audit_account"
            ) as mock:
                mock.side_effect = mock_audit
                mao = MultiAccountOrchestrator(accounts_path=accounts_path)
                result = mao.run_all()

        # The failed account should be marked as failed
        failed_entries = [a for a in result["by_account"] if a["status"] == "failed"]
        success_entries = [a for a in result["by_account"] if a["status"] == "success"]

        assert any(
            e["account_id"] == failing_account_id for e in failed_entries
        ), "The failing account should be in the failed list"

        # Remaining accounts should have succeeded
        expected_success_count = n - 1
        assert len(success_entries) == expected_success_count, (
            f"Expected {expected_success_count} successful accounts, got {len(success_entries)}"
        )

        # accounts_scanned should only count successes
        assert result["accounts_scanned"] == expected_success_count

    @given(
        accounts=accounts_list_strategy(min_size=3, max_size=5),
        audit_results=st.lists(audit_result_strategy(), min_size=3, max_size=5),
    )
    @settings(max_examples=200, deadline=None)
    def test_multiple_failures_still_isolate(self, accounts, audit_results):
        """When multiple accounts fail, remaining accounts still succeed."""
        n = min(len(accounts), len(audit_results))
        assume(n >= 3)
        accounts = accounts[:n]
        audit_results = audit_results[:n]

        # First two accounts will fail
        failing_ids = {accounts[0]["account_id"], accounts[1]["account_id"]}

        def mock_audit(account):
            if account["account_id"] in failing_ids:
                raise ValueError(f"Simulated failure for {account['account_id']}")
            idx = next(
                i for i, a in enumerate(accounts) if a["account_id"] == account["account_id"]
            )
            return audit_results[idx]

        with tempfile.TemporaryDirectory() as tmp_dir:
            accounts_path = _write_accounts_file(Path(tmp_dir), accounts)

            with patch(
                "agents.multi_account_orchestrator.MultiAccountOrchestrator._audit_account"
            ) as mock:
                mock.side_effect = mock_audit
                mao = MultiAccountOrchestrator(accounts_path=accounts_path)
                result = mao.run_all()

        failed_entries = [a for a in result["by_account"] if a["status"] == "failed"]
        success_entries = [a for a in result["by_account"] if a["status"] == "success"]

        assert len(failed_entries) == 2
        assert len(success_entries) == n - 2
        assert result["accounts_scanned"] == n - 2

    @given(
        accounts=accounts_list_strategy(min_size=2, max_size=4),
        audit_results=st.lists(audit_result_strategy(), min_size=2, max_size=4),
    )
    @settings(max_examples=200, deadline=None)
    def test_failed_account_findings_not_in_aggregate(self, accounts, audit_results):
        """Findings from a failed account must NOT appear in aggregate_findings."""
        n = min(len(accounts), len(audit_results))
        assume(n >= 2)
        accounts = accounts[:n]
        audit_results = audit_results[:n]

        failing_account_id = accounts[0]["account_id"]

        def mock_audit(account):
            if account["account_id"] == failing_account_id:
                raise RuntimeError("Simulated failure")
            idx = next(
                i for i, a in enumerate(accounts) if a["account_id"] == account["account_id"]
            )
            return audit_results[idx]

        with tempfile.TemporaryDirectory() as tmp_dir:
            accounts_path = _write_accounts_file(Path(tmp_dir), accounts)

            with patch(
                "agents.multi_account_orchestrator.MultiAccountOrchestrator._audit_account"
            ) as mock:
                mock.side_effect = mock_audit
                mao = MultiAccountOrchestrator(accounts_path=accounts_path)
                result = mao.run_all()

        # No finding in aggregate_findings should have the failing account's ID
        for finding in result["aggregate_findings"]:
            assert finding["account_id"] != failing_account_id, (
                f"Finding from failed account {failing_account_id} leaked into aggregate_findings"
            )


# ─── Property 19: Account ID Injection ──────────────────────────────────────


class TestMultiAccountOrchestratorAccountIDInjection:
    """Property 19: MultiAccountOrchestrator Account ID Injection.

    **Validates: Requirements 9.3**

    Every finding in aggregate_findings has account_id matching its source account.
    """

    @given(
        accounts=accounts_list_strategy(min_size=1, max_size=5),
        audit_results=st.lists(audit_result_strategy(), min_size=1, max_size=5),
    )
    @settings(max_examples=200, deadline=None)
    def test_every_finding_has_correct_account_id(self, accounts, audit_results):
        """Every finding in aggregate_findings has account_id matching its source."""
        n = min(len(accounts), len(audit_results))
        assume(n >= 1)
        accounts = accounts[:n]
        audit_results = audit_results[:n]

        def mock_audit(account):
            idx = next(
                i for i, a in enumerate(accounts) if a["account_id"] == account["account_id"]
            )
            return audit_results[idx]

        with tempfile.TemporaryDirectory() as tmp_dir:
            accounts_path = _write_accounts_file(Path(tmp_dir), accounts)

            with patch(
                "agents.multi_account_orchestrator.MultiAccountOrchestrator._audit_account"
            ) as mock:
                mock.side_effect = mock_audit
                mao = MultiAccountOrchestrator(accounts_path=accounts_path)
                result = mao.run_all()

        # Build expected mapping: account_id → findings
        valid_account_ids = {acct["account_id"] for acct in accounts}

        # Every finding in aggregate_findings must have an account_id field
        for finding in result["aggregate_findings"]:
            assert "account_id" in finding, (
                f"Finding missing account_id: {finding}"
            )
            # The account_id must be one of the valid account IDs
            assert finding["account_id"] in valid_account_ids, (
                f"Finding account_id '{finding['account_id']}' not in valid accounts"
            )

    @given(
        accounts=accounts_list_strategy(min_size=2, max_size=4),
        audit_results=st.lists(audit_result_strategy(), min_size=2, max_size=4),
    )
    @settings(max_examples=200, deadline=None)
    def test_finding_count_matches_sum_of_account_findings(self, accounts, audit_results):
        """Total aggregate_findings count equals sum of all account findings."""
        n = min(len(accounts), len(audit_results))
        assume(n >= 2)
        accounts = accounts[:n]
        audit_results = audit_results[:n]

        def mock_audit(account):
            idx = next(
                i for i, a in enumerate(accounts) if a["account_id"] == account["account_id"]
            )
            return audit_results[idx]

        with tempfile.TemporaryDirectory() as tmp_dir:
            accounts_path = _write_accounts_file(Path(tmp_dir), accounts)

            with patch(
                "agents.multi_account_orchestrator.MultiAccountOrchestrator._audit_account"
            ) as mock:
                mock.side_effect = mock_audit
                mao = MultiAccountOrchestrator(accounts_path=accounts_path)
                result = mao.run_all()

        expected_total = sum(len(ar["findings"]) for ar in audit_results[:n])
        assert len(result["aggregate_findings"]) == expected_total, (
            f"Expected {expected_total} total findings, got {len(result['aggregate_findings'])}"
        )

    @given(
        accounts=accounts_list_strategy(min_size=2, max_size=4),
        audit_results=st.lists(audit_result_strategy(), min_size=2, max_size=4),
    )
    @settings(max_examples=200, deadline=None)
    def test_account_id_not_overwrite_existing_finding_fields(self, accounts, audit_results):
        """Injection of account_id preserves all original finding fields."""
        n = min(len(accounts), len(audit_results))
        assume(n >= 2)
        accounts = accounts[:n]
        audit_results = audit_results[:n]

        # Ensure at least one finding exists
        total_findings = sum(len(ar["findings"]) for ar in audit_results[:n])
        assume(total_findings > 0)

        def mock_audit(account):
            idx = next(
                i for i, a in enumerate(accounts) if a["account_id"] == account["account_id"]
            )
            return audit_results[idx]

        with tempfile.TemporaryDirectory() as tmp_dir:
            accounts_path = _write_accounts_file(Path(tmp_dir), accounts)

            with patch(
                "agents.multi_account_orchestrator.MultiAccountOrchestrator._audit_account"
            ) as mock:
                mock.side_effect = mock_audit
                mao = MultiAccountOrchestrator(accounts_path=accounts_path)
                result = mao.run_all()

        # Verify original finding fields are preserved
        for finding in result["aggregate_findings"]:
            assert "resource_id" in finding, "Original resource_id field must be preserved"
            assert "resource_type" in finding, "Original resource_type field must be preserved"
            assert "check_type" in finding, "Original check_type field must be preserved"
            assert "severity" in finding, "Original severity field must be preserved"


# ─── Property 20: Priority Sorting ──────────────────────────────────────────


class TestMultiAccountOrchestratorPrioritySorting:
    """Property 20: MultiAccountOrchestrator Priority Sorting.

    **Validates: Requirements 9.4**

    by_account is sorted high → medium → low, then alphabetically by account_name.
    """

    @given(
        accounts=accounts_list_strategy(min_size=2, max_size=5),
    )
    @settings(max_examples=200, deadline=None)
    def test_by_account_sorted_by_priority(self, accounts):
        """by_account entries are sorted high → medium → low."""
        def mock_audit(account):
            return {"findings": [], "waste": 0.0, "critical_count": 0}

        with tempfile.TemporaryDirectory() as tmp_dir:
            accounts_path = _write_accounts_file(Path(tmp_dir), accounts)

            with patch(
                "agents.multi_account_orchestrator.MultiAccountOrchestrator._audit_account"
            ) as mock:
                mock.side_effect = mock_audit
                mao = MultiAccountOrchestrator(accounts_path=accounts_path)
                result = mao.run_all()

        priorities = [a["priority"] for a in result["by_account"]]
        priority_indices = [PRIORITY_ORDER[p] for p in priorities]

        # Must be non-decreasing (high=0, medium=1, low=2)
        for i in range(len(priority_indices) - 1):
            assert priority_indices[i] <= priority_indices[i + 1], (
                f"Priority order violated: {priorities[i]} came before {priorities[i+1]} "
                f"at positions {i}, {i+1}. Full order: {priorities}"
            )

    @given(
        data=st.data(),
    )
    @settings(max_examples=200, deadline=None)
    def test_same_priority_sorted_alphabetically(self, data):
        """Within the same priority level, accounts are sorted alphabetically by name."""
        # Generate multiple accounts with the same priority
        priority = data.draw(st.sampled_from(["high", "medium", "low"]))
        accounts = data.draw(st.lists(
            account_strategy(priority=priority),
            min_size=2,
            max_size=5,
        ))
        # Ensure unique account_ids
        seen_ids = set()
        unique_accounts = []
        for acct in accounts:
            if acct["account_id"] not in seen_ids:
                seen_ids.add(acct["account_id"])
                unique_accounts.append(acct)
        assume(len(unique_accounts) >= 2)
        accounts = unique_accounts

        def mock_audit(account):
            return {"findings": [], "waste": 0.0, "critical_count": 0}

        with tempfile.TemporaryDirectory() as tmp_dir:
            accounts_path = _write_accounts_file(Path(tmp_dir), accounts)

            with patch(
                "agents.multi_account_orchestrator.MultiAccountOrchestrator._audit_account"
            ) as mock:
                mock.side_effect = mock_audit
                mao = MultiAccountOrchestrator(accounts_path=accounts_path)
                result = mao.run_all()

        names = [a["account_name"] for a in result["by_account"]]
        assert names == sorted(names), (
            f"Within priority '{priority}', names should be alphabetical. "
            f"Got: {names}, expected: {sorted(names)}"
        )

    @given(
        data=st.data(),
    )
    @settings(max_examples=200, deadline=None)
    def test_mixed_priorities_maintain_full_sort_invariant(self, data):
        """A mix of all three priorities is sorted high → medium → low, alpha within."""
        # Generate at least one account per priority
        high_accounts = data.draw(st.lists(
            account_strategy(priority="high"), min_size=1, max_size=2
        ))
        medium_accounts = data.draw(st.lists(
            account_strategy(priority="medium"), min_size=1, max_size=2
        ))
        low_accounts = data.draw(st.lists(
            account_strategy(priority="low"), min_size=1, max_size=2
        ))
        all_accounts = high_accounts + medium_accounts + low_accounts

        # Ensure unique account_ids
        seen_ids = set()
        unique_accounts = []
        for acct in all_accounts:
            if acct["account_id"] not in seen_ids:
                seen_ids.add(acct["account_id"])
                unique_accounts.append(acct)
        assume(len(unique_accounts) >= 3)

        def mock_audit(account):
            return {"findings": [], "waste": 0.0, "critical_count": 0}

        with tempfile.TemporaryDirectory() as tmp_dir:
            accounts_path = _write_accounts_file(Path(tmp_dir), unique_accounts)

            with patch(
                "agents.multi_account_orchestrator.MultiAccountOrchestrator._audit_account"
            ) as mock:
                mock.side_effect = mock_audit
                mao = MultiAccountOrchestrator(accounts_path=accounts_path)
                result = mao.run_all()

        # Check the full sort key: (priority_order, account_name)
        by_account = result["by_account"]
        sort_keys = [
            (PRIORITY_ORDER[a["priority"]], a["account_name"])
            for a in by_account
        ]
        assert sort_keys == sorted(sort_keys), (
            f"by_account not sorted correctly. Sort keys: {sort_keys}"
        )
