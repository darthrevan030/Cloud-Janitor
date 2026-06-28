"""Tests for agents/multi_account_orchestrator.py.

Covers: Requirement 9.1-9.8, 14.4, 14.7
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agents.multi_account_orchestrator import (
    MultiAccountOrchestrator,
    PRIORITY_ORDER,
    _empty_result,
)


VALID_ACCOUNT = {
    "account_id": "123456789012",
    "account_name": "Production",
    "role_arn": "arn:aws:iam::123456789012:role/CloudJanitorReadOnly",
    "region": "us-east-1",
    "priority": "high",
}

VALID_ACCOUNT_2 = {
    "account_id": "987654321098",
    "account_name": "Staging",
    "role_arn": "arn:aws:iam::987654321098:role/CloudJanitorReadOnly",
    "region": "eu-west-1",
    "priority": "medium",
}

VALID_ACCOUNT_3 = {
    "account_id": "111222333444",
    "account_name": "Development",
    "role_arn": "arn:aws:iam::111222333444:role/JanitorDev",
    "region": "us-west-2",
    "priority": "low",
}


# ─── Helper ──────────────────────────────────────────────────────────────────


def _write_accounts(tmp_dir: Path, accounts: list[dict]) -> Path:
    """Write accounts.json fixture and return its path."""
    p = tmp_dir / "accounts.json"
    p.write_text(json.dumps(accounts), encoding="utf-8")
    return p


# ─── Empty / invalid input tests ─────────────────────────────────────────────


class TestLoadAccounts:
    """Tests for load_accounts() validation logic."""

    def test_missing_file_returns_empty(self, tmp_path):
        """Req 9.5: Missing accounts.json → empty list."""
        mao = MultiAccountOrchestrator(accounts_path=tmp_path / "nonexistent.json")
        assert mao.load_accounts() == []

    def test_invalid_json_returns_empty(self, tmp_path):
        """Req 9.5: Invalid JSON → empty list."""
        p = tmp_path / "accounts.json"
        p.write_text("not valid json {{{", encoding="utf-8")
        mao = MultiAccountOrchestrator(accounts_path=p)
        assert mao.load_accounts() == []

    def test_non_list_json_returns_empty(self, tmp_path):
        """Req 9.5: JSON that isn't a list → empty list."""
        p = tmp_path / "accounts.json"
        p.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        mao = MultiAccountOrchestrator(accounts_path=p)
        assert mao.load_accounts() == []

    def test_missing_required_field_skipped(self, tmp_path):
        """Req 9.5: Entry missing required field is skipped."""
        account_no_priority = {
            "account_id": "123456789012",
            "account_name": "Test",
            "role_arn": "arn:aws:iam::123456789012:role/Role",
            "region": "us-east-1",
            # missing 'priority'
        }
        p = _write_accounts(tmp_path, [account_no_priority])
        mao = MultiAccountOrchestrator(accounts_path=p)
        assert mao.load_accounts() == []

    def test_invalid_role_arn_skipped(self, tmp_path):
        """Req 14.4: Invalid role_arn format → entry skipped with stderr log."""
        account = dict(VALID_ACCOUNT, role_arn="not-a-valid-arn")
        p = _write_accounts(tmp_path, [account])
        mao = MultiAccountOrchestrator(accounts_path=p)
        result = mao.load_accounts()
        assert result == []

    def test_invalid_priority_skipped(self, tmp_path):
        """Invalid priority value → entry skipped."""
        account = dict(VALID_ACCOUNT, priority="critical")
        p = _write_accounts(tmp_path, [account])
        mao = MultiAccountOrchestrator(accounts_path=p)
        assert mao.load_accounts() == []

    def test_valid_account_loads(self, tmp_path):
        """Valid account is returned."""
        p = _write_accounts(tmp_path, [VALID_ACCOUNT])
        mao = MultiAccountOrchestrator(accounts_path=p)
        accounts = mao.load_accounts()
        assert len(accounts) == 1
        assert accounts[0]["account_id"] == "123456789012"

    def test_mixed_valid_invalid_keeps_valid(self, tmp_path):
        """Only valid entries are returned, invalid are skipped."""
        invalid = dict(VALID_ACCOUNT, role_arn="bad", account_id="000000000001")
        p = _write_accounts(tmp_path, [VALID_ACCOUNT, invalid, VALID_ACCOUNT_2])
        mao = MultiAccountOrchestrator(accounts_path=p)
        accounts = mao.load_accounts()
        assert len(accounts) == 2
        assert accounts[0]["account_id"] == "123456789012"
        assert accounts[1]["account_id"] == "987654321098"


# ─── run_all() tests ─────────────────────────────────────────────────────────


class TestRunAll:
    """Tests for run_all() orchestration logic."""

    def test_empty_accounts_returns_empty_result(self, tmp_path):
        """Req 9.5: No valid accounts → empty result dict with all required fields."""
        p = _write_accounts(tmp_path, [])
        mao = MultiAccountOrchestrator(accounts_path=p)
        result = mao.run_all()
        expected = _empty_result()
        assert result == expected

    def test_result_has_all_required_keys(self, tmp_path):
        """Req 9.8: Result dict has all required fields."""
        p = _write_accounts(tmp_path, [])
        mao = MultiAccountOrchestrator(accounts_path=p)
        result = mao.run_all()
        required_keys = {
            "accounts_scanned", "total_findings", "total_waste",
            "critical_count", "by_account", "aggregate_findings",
            "cross_account_duplicates",
        }
        assert set(result.keys()) == required_keys

    @patch("agents.multi_account_orchestrator.MultiAccountOrchestrator._audit_account")
    def test_successful_audit_populates_result(self, mock_audit, tmp_path):
        """Req 9.3, 9.8: Successful audit → findings aggregated with account_id."""
        mock_audit.return_value = {
            "findings": [
                {"resource_id": "vol-123", "resource_type": "ebs", "check_type": "encryption",
                 "severity": "HIGH", "cost_estimate_monthly": 10.0},
            ],
            "waste": 10.0,
            "critical_count": 0,
        }
        p = _write_accounts(tmp_path, [VALID_ACCOUNT])
        mao = MultiAccountOrchestrator(accounts_path=p)
        result = mao.run_all()

        assert result["accounts_scanned"] == 1
        assert result["total_findings"] == 1
        assert result["total_waste"] == 10.0
        assert len(result["aggregate_findings"]) == 1
        # Account ID injected (Req 9.3)
        assert result["aggregate_findings"][0]["account_id"] == "123456789012"

    @patch("agents.multi_account_orchestrator.MultiAccountOrchestrator._audit_account")
    def test_failed_account_continues_others(self, mock_audit, tmp_path):
        """Req 9.2, 14.7: One account failure doesn't stop others."""
        def side_effect(account):
            if account["account_id"] == "123456789012":
                raise RuntimeError("Simulated failure")
            return {"findings": [{"resource_id": "r-1", "resource_type": "ec2",
                                   "check_type": "idle_resource", "severity": "LOW",
                                   "cost_estimate_monthly": 5.0}],
                    "waste": 5.0, "critical_count": 0}

        mock_audit.side_effect = side_effect
        p = _write_accounts(tmp_path, [VALID_ACCOUNT, VALID_ACCOUNT_2])
        mao = MultiAccountOrchestrator(accounts_path=p)
        result = mao.run_all()

        # One succeeded, one failed
        assert result["accounts_scanned"] == 1
        failed = [a for a in result["by_account"] if a["status"] == "failed"]
        success = [a for a in result["by_account"] if a["status"] == "success"]
        assert len(failed) == 1
        assert len(success) == 1
        assert "RuntimeError" in failed[0]["error"]

    @patch("agents.multi_account_orchestrator.MultiAccountOrchestrator._audit_account")
    def test_priority_sorting(self, mock_audit, tmp_path):
        """Req 9.4: by_account sorted high → medium → low, alphabetically within."""
        mock_audit.return_value = {"findings": [], "waste": 0.0, "critical_count": 0}
        p = _write_accounts(tmp_path, [VALID_ACCOUNT_3, VALID_ACCOUNT, VALID_ACCOUNT_2])
        mao = MultiAccountOrchestrator(accounts_path=p)
        result = mao.run_all()

        priorities = [a["priority"] for a in result["by_account"]]
        assert priorities == ["high", "medium", "low"]
        # Verify names within same priority are alphabetical
        names = [a["account_name"] for a in result["by_account"]]
        assert names == ["Production", "Staging", "Development"]

    @patch("agents.multi_account_orchestrator.MultiAccountOrchestrator._audit_account")
    def test_cross_account_duplicates(self, mock_audit, tmp_path):
        """Req 9.6: Duplicates counted by (resource_type, check_type) across accounts."""
        def side_effect(account):
            if account["account_id"] == "123456789012":
                return {
                    "findings": [
                        {"resource_id": "r-1", "resource_type": "ebs", "check_type": "encryption",
                         "severity": "HIGH", "cost_estimate_monthly": 10.0},
                        {"resource_id": "r-2", "resource_type": "ec2", "check_type": "idle_resource",
                         "severity": "LOW", "cost_estimate_monthly": 5.0},
                    ],
                    "waste": 15.0,
                    "critical_count": 0,
                }
            return {
                "findings": [
                    {"resource_id": "r-3", "resource_type": "ebs", "check_type": "encryption",
                     "severity": "MEDIUM", "cost_estimate_monthly": 8.0},
                ],
                "waste": 8.0,
                "critical_count": 0,
            }

        mock_audit.side_effect = side_effect
        p = _write_accounts(tmp_path, [VALID_ACCOUNT, VALID_ACCOUNT_2])
        mao = MultiAccountOrchestrator(accounts_path=p)
        result = mao.run_all()

        # ebs/encryption appears in both accounts → 2 findings are duplicates
        # ec2/idle_resource only in account 1 → not a duplicate
        assert result["cross_account_duplicates"] == 2

    @patch("agents.multi_account_orchestrator.MultiAccountOrchestrator._audit_account")
    def test_account_id_injected_into_every_finding(self, mock_audit, tmp_path):
        """Req 9.3: Every finding in aggregate_findings has account_id."""
        mock_audit.return_value = {
            "findings": [
                {"resource_id": "r-1", "resource_type": "ebs", "check_type": "encryption",
                 "severity": "HIGH", "cost_estimate_monthly": 10.0},
                {"resource_id": "r-2", "resource_type": "ec2", "check_type": "idle_resource",
                 "severity": "LOW", "cost_estimate_monthly": 3.0},
            ],
            "waste": 13.0,
            "critical_count": 0,
        }
        p = _write_accounts(tmp_path, [VALID_ACCOUNT])
        mao = MultiAccountOrchestrator(accounts_path=p)
        result = mao.run_all()

        for finding in result["aggregate_findings"]:
            assert "account_id" in finding
            assert finding["account_id"] == "123456789012"


# ─── Cross-account duplicates calculation ─────────────────────────────────────


class TestCrossAccountDuplicates:
    """Unit tests for _calculate_cross_account_duplicates."""

    def test_no_duplicates_single_account(self, tmp_path):
        """No duplicates when only one account."""
        p = _write_accounts(tmp_path, [VALID_ACCOUNT])
        mao = MultiAccountOrchestrator(accounts_path=p)
        by_account = [{
            "account_id": "A",
            "findings": [
                {"resource_type": "ebs", "check_type": "encryption"},
                {"resource_type": "ec2", "check_type": "idle_resource"},
            ],
        }]
        assert mao._calculate_cross_account_duplicates(by_account) == 0

    def test_duplicates_across_two_accounts(self, tmp_path):
        """Findings with same pair across accounts are counted."""
        p = _write_accounts(tmp_path, [])
        mao = MultiAccountOrchestrator(accounts_path=p)
        by_account = [
            {"account_id": "A", "findings": [
                {"resource_type": "ebs", "check_type": "encryption"},
                {"resource_type": "ec2", "check_type": "idle_resource"},
            ]},
            {"account_id": "B", "findings": [
                {"resource_type": "ebs", "check_type": "encryption"},
            ]},
        ]
        # ebs/encryption in A and B → 2 findings are duplicates
        # ec2/idle_resource only in A → 0
        assert mao._calculate_cross_account_duplicates(by_account) == 2

    def test_no_duplicates_different_pairs(self, tmp_path):
        """No duplicates when pairs are unique per account."""
        p = _write_accounts(tmp_path, [])
        mao = MultiAccountOrchestrator(accounts_path=p)
        by_account = [
            {"account_id": "A", "findings": [{"resource_type": "ebs", "check_type": "encryption"}]},
            {"account_id": "B", "findings": [{"resource_type": "ec2", "check_type": "idle_resource"}]},
        ]
        assert mao._calculate_cross_account_duplicates(by_account) == 0


# ─── Negative test: role_arn validation ───────────────────────────────────────


class TestRoleArnValidation:
    """Req 14.4: role_arn must match arn:aws:iam::\\d{12}:role/.+"""

    @pytest.mark.parametrize("bad_arn", [
        "",
        "arn:aws:iam::12345:role/Short",           # account too short
        "arn:aws:iam::1234567890123:role/Long",    # account too long
        "arn:aws:iam::123456789012:user/NotRole",  # not a role
        "arn:gcp:iam::123456789012:role/Wrong",    # wrong partition
        "random-string",
    ])
    def test_invalid_arns_rejected(self, tmp_path, bad_arn):
        """Invalid role_arns are rejected."""
        account = dict(VALID_ACCOUNT, role_arn=bad_arn)
        p = _write_accounts(tmp_path, [account])
        mao = MultiAccountOrchestrator(accounts_path=p)
        assert mao.load_accounts() == []

    @pytest.mark.parametrize("good_arn", [
        "arn:aws:iam::123456789012:role/CloudJanitorReadOnly",
        "arn:aws:iam::000000000000:role/x",
        "arn:aws:iam::999999999999:role/my-role/with-path",
    ])
    def test_valid_arns_accepted(self, tmp_path, good_arn):
        """Valid role_arns are accepted."""
        account = dict(VALID_ACCOUNT, role_arn=good_arn)
        p = _write_accounts(tmp_path, [account])
        mao = MultiAccountOrchestrator(accounts_path=p)
        accounts = mao.load_accounts()
        assert len(accounts) == 1
