"""Unit tests for SavingsTracker."""
import json
import tempfile
from pathlib import Path

import pytest

from savings import SavingsTracker


@pytest.fixture
def tracker_env(tmp_path):
    """Create a temporary environment with findings_store and ledger paths."""
    findings = {
        "scan_id": "test-001",
        "completed_at": "2026-06-27T05:31:48.159990+00:00",
        "findings": [
            {"resource_id": "res-1", "cost_estimate_monthly": 10.0},
            {"resource_id": "res-2", "cost_estimate_monthly": 20.0},
            {"resource_id": "res-3", "cost_estimate_monthly": 5.0},
        ],
    }
    findings_path = tmp_path / "findings_store.json"
    ledger_path = tmp_path / "savings_ledger.json"
    findings_path.write_text(json.dumps(findings))

    tracker = SavingsTracker(ledger_path=ledger_path, findings_store_path=findings_path)
    return tracker, ledger_path, findings_path


def test_record_run_new(tracker_env):
    """First record_run creates ledger with correct structure and content."""
    tracker, ledger_path, _ = tracker_env
    result = tracker.record_run(["res-1", "res-2"])
    assert result is True
    assert ledger_path.exists()

    # Verify ledger has correct top-level schema
    ledger = json.loads(ledger_path.read_text())
    assert "total_lifetime_savings" in ledger
    assert "runs" in ledger
    assert isinstance(ledger["runs"], list)
    assert len(ledger["runs"]) == 1
    assert ledger["total_lifetime_savings"] == 30.0  # res-1=10 + res-2=20


def test_record_run_duplicate(tracker_env):
    tracker, _, _ = tracker_env
    tracker.record_run(["res-1", "res-2"])
    result = tracker.record_run(["res-1", "res-2"])
    assert result is False


def test_duplicate_does_not_modify_file(tracker_env):
    """SavingsTracker must NOT update total_lifetime_savings when duplicate run_id is detected.

    Verifies both that the file mtime is unchanged AND that total_lifetime_savings
    remains the same value — not incremented by re-processing the same run.
    """
    tracker, ledger_path, _ = tracker_env
    tracker.record_run(["res-1"])

    # Read total after first write
    ledger_before = json.loads(ledger_path.read_text())
    total_before = ledger_before["total_lifetime_savings"]
    assert total_before == 10.0  # res-1 costs 10.0/mo

    mtime_before = ledger_path.stat().st_mtime

    # Attempt duplicate
    result = tracker.record_run(["res-1"])
    assert result is False

    mtime_after = ledger_path.stat().st_mtime
    assert mtime_before == mtime_after, (
        "File mtime changed on duplicate run — ledger was improperly rewritten"
    )

    # Verify total_lifetime_savings was NOT inflated
    ledger_after = json.loads(ledger_path.read_text())
    assert ledger_after["total_lifetime_savings"] == total_before, (
        f"total_lifetime_savings changed from {total_before} to "
        f"{ledger_after['total_lifetime_savings']} on duplicate run"
    )


def test_savings_summary_after_run(tracker_env):
    tracker, _, _ = tracker_env
    tracker.record_run(["res-1", "res-2"])
    summary = tracker.get_savings_summary()
    assert summary["total_lifetime_monthly"] == 30.0
    assert summary["total_lifetime_annual"] == 360.0
    assert summary["total_runs"] == 1
    assert summary["last_run_savings"] == 30.0


def test_savings_summary_partial_resources(tracker_env):
    tracker, _, _ = tracker_env
    tracker.record_run(["res-1"])
    summary = tracker.get_savings_summary()
    assert summary["total_lifetime_monthly"] == 10.0
    assert summary["total_lifetime_annual"] == 120.0
    assert summary["last_run_savings"] == 10.0


def test_savings_summary_empty_ledger(tracker_env):
    tracker, _, _ = tracker_env
    summary = tracker.get_savings_summary()
    assert summary["total_lifetime_monthly"] == 0.0
    assert summary["total_lifetime_annual"] == 0.0
    assert summary["total_runs"] == 0
    assert summary["last_run_savings"] == 0.0


def test_missing_ledger_file(tracker_env):
    tracker, ledger_path, _ = tracker_env
    tracker.record_run(["res-1"])
    ledger_path.unlink()
    summary = tracker.get_savings_summary()
    assert summary["total_lifetime_monthly"] == 0.0
    assert summary["total_runs"] == 0


def test_corrupt_ledger_file(tracker_env):
    tracker, ledger_path, _ = tracker_env
    ledger_path.write_text("not valid json")
    summary = tracker.get_savings_summary()
    assert summary["total_lifetime_monthly"] == 0.0
    assert summary["total_runs"] == 0


def test_missing_cost_estimate_treated_as_zero(tmp_path):
    findings = {
        "scan_id": "test-002",
        "completed_at": "2026-06-27T06:00:00+00:00",
        "findings": [
            {"resource_id": "res-no-cost"},
        ],
    }
    findings_path = tmp_path / "findings_store.json"
    ledger_path = tmp_path / "savings_ledger.json"
    findings_path.write_text(json.dumps(findings))

    tracker = SavingsTracker(ledger_path=ledger_path, findings_store_path=findings_path)
    tracker.record_run(["res-no-cost"])
    summary = tracker.get_savings_summary()
    assert summary["total_lifetime_monthly"] == 0.0
    assert summary["total_runs"] == 1


def test_run_entry_schema(tracker_env):
    tracker, ledger_path, _ = tracker_env
    tracker.record_run(["res-1", "res-3"])
    ledger = json.loads(ledger_path.read_text())

    assert ledger["total_lifetime_savings"] == 15.0
    assert len(ledger["runs"]) == 1

    entry = ledger["runs"][0]
    assert entry["run_id"] == "test-001"
    assert entry["timestamp"] == "2026-06-27T05:31:48.159990+00:00"
    assert entry["resources_remediated"] == ["res-1", "res-3"]
    assert entry["monthly_savings_added"] == 15.0
    assert entry["cumulative_at_time"] == 15.0


def test_recalculate_from_source(tmp_path):
    """Verify total is recalculated from source, not incremented."""
    findings_path = tmp_path / "findings_store.json"
    ledger_path = tmp_path / "savings_ledger.json"

    # First run
    findings = {
        "scan_id": "run-1",
        "completed_at": "2026-06-27T01:00:00+00:00",
        "findings": [{"resource_id": "a", "cost_estimate_monthly": 10.0}],
    }
    findings_path.write_text(json.dumps(findings))
    tracker = SavingsTracker(ledger_path=ledger_path, findings_store_path=findings_path)
    tracker.record_run(["a"])

    # Second run with different scan_id
    findings["scan_id"] = "run-2"
    findings["findings"] = [{"resource_id": "b", "cost_estimate_monthly": 25.0}]
    findings_path.write_text(json.dumps(findings))
    tracker.record_run(["b"])

    ledger = json.loads(ledger_path.read_text())
    assert ledger["total_lifetime_savings"] == 35.0
    assert ledger["runs"][-1]["cumulative_at_time"] == 35.0


# ──────────────────────────────────────────────────────────────────────
# Orchestrator → SavingsTracker wiring tests (Task 5.3)
# ──────────────────────────────────────────────────────────────────────


import subprocess
from unittest.mock import MagicMock, patch, PropertyMock

from orchestrator import Orchestrator, ApprovalResult
from agents.remediation_architect import RemediationPlan


@pytest.fixture
def orchestrator_env(tmp_path):
    """Set up a minimal project environment for orchestrator wiring tests."""
    # Create directories
    (tmp_path / ".kiro" / "hooks").mkdir(parents=True)
    (tmp_path / "output").mkdir()
    (tmp_path / "rollbacks").mkdir()

    # Create hook scripts (pass-through)
    pre_hook = tmp_path / ".kiro" / "hooks" / "pre-remediation.sh"
    pre_hook.write_text("#!/usr/bin/env bash\nexit 0\n")
    pre_hook.chmod(0o755)

    post_hook = tmp_path / ".kiro" / "hooks" / "post-remediation.sh"
    post_hook.write_text("#!/usr/bin/env bash\nexit 0\n")
    post_hook.chmod(0o755)

    # Create findings_store.json with both agents
    findings = {
        "scan_id": "scan-wiring-001",
        "completed_at": "2026-06-27T05:31:48.159990+00:00",
        "findings": [
            {
                "id": "f1",
                "resource_id": "vol-abc123",
                "resource_type": "ebs",
                "agent": "finops",
                "category": "waste",
                "severity": "MEDIUM",
                "title": "Idle EBS volume",
                "description": "Idle for 60 days",
                "cost_estimate_monthly": 25.0,
                "idle_days": 60,
                "metadata": {},
                "detected_at": "2026-06-27T05:00:00+00:00",
            },
            {
                "id": "f2",
                "resource_id": "sg-open-redis",
                "resource_type": "security_group",
                "agent": "secops",
                "category": "security",
                "severity": "CRITICAL",
                "title": "Open Redis port",
                "description": "0.0.0.0/0 on port 6379",
                "cost_estimate_monthly": 0.0,
                "idle_days": 0,
                "metadata": {},
                "detected_at": "2026-06-27T05:01:00+00:00",
            },
        ],
        "summary": {
            "total": 2,
            "by_severity": {"LOW": 0, "MEDIUM": 1, "HIGH": 0, "CRITICAL": 1},
            "by_agent": {"finops": 1, "secops": 1},
            "total_monthly_waste": 25.0,
        },
    }
    (tmp_path / "findings_store.json").write_text(json.dumps(findings, indent=2))

    # Create output files
    (tmp_path / "output" / "remediation.tf").write_text(
        'resource "null_resource" "test" {}'
    )
    (tmp_path / "rollbacks" / "vol-abc123.tf").write_text(
        'resource "null_resource" "rollback" {}'
    )

    return tmp_path


def _setup_orchestrator_for_approval(tmp_path):
    """Create an orchestrator with mocked agents, ready for approval testing."""
    orch = Orchestrator(project_root=tmp_path, approver="test-user")

    # Mock agent scans
    orch._finops.scan = MagicMock(return_value=[
        {"id": "f1", "resource_id": "vol-abc123", "agent": "finops"}
    ])
    orch._secops.scan = MagicMock(return_value=[
        {"id": "f2", "resource_id": "sg-open-redis", "agent": "secops"}
    ])

    # Mock architect plans
    plans = [
        RemediationPlan(
            resource_id="vol-abc123",
            finding={"resource_id": "vol-abc123", "resource_type": "ebs"},
            blocked=False,
            remediation_hcl='resource "null_resource" "test" {}',
            rollback_hcl='resource "null_resource" "rollback" {}',
        ),
    ]
    orch._architect.plan = MagicMock(return_value=plans)

    return orch


class TestOrchestratorSavingsTrackerWiring:
    """Tests for Orchestrator → SavingsTracker integration (Requirements 5.1, 5.2, 5.3)."""

    def test_record_run_called_from_approve_with_correct_args(self, orchestrator_env):
        """Verify record_run() is called from approve() with the approved resource_id.

        Validates Requirement 5.1: Orchestrator invokes SavingsTracker after
        successful approval and execution.
        """
        orch = _setup_orchestrator_for_approval(orchestrator_env)

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            orch.execute_audit()

            # Mock the savings tracker to observe calls
            orch._savings_tracker.record_run = MagicMock(return_value=True)

            result = orch.approve("APPROVE vol-abc123")
            assert result.success is True

            # Verify record_run was called exactly once with correct arguments
            orch._savings_tracker.record_run.assert_called_once_with(
                resources_remediated=["vol-abc123"]
            )

    def test_record_run_not_called_from_post_remediation_hook(self, orchestrator_env):
        """Verify record_run() is NOT called from _run_post_remediation_hook.

        Validates Requirement 5.3: SavingsTracker invocation occurs exclusively
        in approve(), not in _run_post_remediation_hook, to avoid double-counting.
        """
        orch = _setup_orchestrator_for_approval(orchestrator_env)

        # Spy on the savings tracker
        orch._savings_tracker.record_run = MagicMock(return_value=True)

        # Call _run_post_remediation_hook directly
        orch._run_post_remediation_hook("vol-abc123", "remediate", "success")

        # record_run should NOT have been called
        orch._savings_tracker.record_run.assert_not_called()

    def test_savings_tracker_file_not_found_does_not_block_approval(
        self, orchestrator_env
    ):
        """Verify FileNotFoundError from savings tracker doesn't block approval.

        Validates Requirement 5.3 (error handling): savings tracking is
        non-blocking to remediation.
        """
        orch = _setup_orchestrator_for_approval(orchestrator_env)

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            orch.execute_audit()

            # Make savings tracker raise FileNotFoundError
            orch._savings_tracker.record_run = MagicMock(
                side_effect=FileNotFoundError("findings_store.json not found")
            )

            result = orch.approve("APPROVE vol-abc123")
            # Approval still succeeds despite savings tracker error
            assert result.success is True
            assert result.resource_id == "vol-abc123"

    def test_savings_tracker_os_error_does_not_block_approval(self, orchestrator_env):
        """Verify OSError from savings tracker doesn't block approval.

        Validates Requirement 5.3 (error handling): OSError (e.g., disk full)
        is handled gracefully.
        """
        orch = _setup_orchestrator_for_approval(orchestrator_env)

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            orch.execute_audit()

            # Make savings tracker raise OSError
            orch._savings_tracker.record_run = MagicMock(
                side_effect=OSError("Permission denied: savings_ledger.json")
            )

            result = orch.approve("APPROVE vol-abc123")
            # Approval still succeeds despite savings tracker error
            assert result.success is True
            assert result.resource_id == "vol-abc123"

    def test_savings_tracker_error_logged_as_warning(self, orchestrator_env):
        """Verify savings tracker errors are logged in the audit trail.

        Validates that errors don't silently disappear — they're captured
        in the audit trail as warnings.
        """
        orch = _setup_orchestrator_for_approval(orchestrator_env)

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            orch.execute_audit()

            orch._savings_tracker.record_run = MagicMock(
                side_effect=OSError("disk full")
            )

            orch.approve("APPROVE vol-abc123")

            # Check audit trail has a savings warning entry
            trail = orch.get_audit_trail()
            savings_entries = [
                e for e in trail if e.action == "savings" and e.result == "warning"
            ]
            assert len(savings_entries) == 1
            assert "disk full" in savings_entries[0].details

    def test_record_run_called_after_post_remediation_hook(self, orchestrator_env):
        """Verify record_run() is called AFTER _run_post_remediation_hook completes.

        Validates the execution order in approve(): post-hook runs first,
        then savings tracking.
        """
        orch = _setup_orchestrator_for_approval(orchestrator_env)

        call_order = []

        with patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            orch.execute_audit()

            # Track call order: post-hook fires subprocess.run, record_run is mocked
            original_post_hook = orch._run_post_remediation_hook

            def tracked_post_hook(*args, **kwargs):
                call_order.append("post_hook")
                return original_post_hook(*args, **kwargs)

            def tracked_record_run(*args, **kwargs):
                call_order.append("record_run")
                return True

            orch._run_post_remediation_hook = tracked_post_hook
            orch._savings_tracker.record_run = tracked_record_run

            orch.approve("APPROVE vol-abc123")

            assert "post_hook" in call_order
            assert "record_run" in call_order
            assert call_order.index("post_hook") < call_order.index("record_run")
