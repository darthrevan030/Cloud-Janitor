"""Savings Tracker module for Cloud Janitor.

Manages the savings_ledger.json lifecycle — recording remediation runs,
computing cumulative savings, and exposing a summary API.
"""

import json
from pathlib import Path


class SavingsTracker:
    """Manages the savings_ledger.json lifecycle."""

    def __init__(
        self,
        ledger_path: Path | None = None,
        findings_store_path: Path | None = None,
    ):
        from cloud_janitor.core.paths import SAVINGS_LEDGER_PATH, FINDINGS_STORE_PATH
        self._ledger_path = ledger_path or SAVINGS_LEDGER_PATH
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._findings_store_path = findings_store_path or FINDINGS_STORE_PATH

    def record_run(self, resources_remediated: list[str]) -> bool:
        """
        Record a remediation run in the ledger.

        Args:
            resources_remediated: List of resource_id strings that were
                approved and executed.

        Returns:
            True if the run was recorded, False if it was a duplicate.
        """
        # 1. Read scan_id and completed_at from findings_store.json
        findings_data = self._read_findings_store()
        run_id = findings_data["scan_id"]
        timestamp = findings_data["completed_at"]

        # 2. Check if scan_id already exists in ledger runs → skip if duplicate
        ledger = self._load_ledger()
        for run in ledger["runs"]:
            if run["run_id"] == run_id:
                return False

        # 3. Compute monthly_savings_added from findings
        monthly_savings_added = self._compute_monthly_savings(resources_remediated)

        # 4. Append RunEntry
        ledger["runs"].append({
            "run_id": run_id,
            "timestamp": timestamp,
            "resources_remediated": resources_remediated,
            "monthly_savings_added": monthly_savings_added,
            "cumulative_at_time": 0.0,  # placeholder, recalculated below
        })

        # 5. Recalculate total_lifetime_savings from all runs
        total = self._recalculate_total(ledger["runs"])
        ledger["total_lifetime_savings"] = total

        # Update cumulative_at_time for the new entry (recalculated from source)
        ledger["runs"][-1]["cumulative_at_time"] = total

        # 6. Write ledger file
        self._write_ledger(ledger)
        return True

    def record_rollback(self, resource_id: str) -> bool:
        """
        Record a rollback in the ledger, negating the savings from the earliest
        non-reversed run that remediated this resource.

        Args:
            resource_id: The resource whose remediation is being rolled back.

        Returns:
            True if a rollback entry was recorded, False if no matching run found.
        """
        ledger = self._load_ledger()
        already_reversed = {
            (r["resources_remediated"][0], r["rolled_back_run_id"])
            for r in ledger["runs"] if r.get("type") == "rollback"
        }
        matching_run = next(
            (r for r in ledger["runs"]
             if r.get("type") != "rollback"
             and resource_id in r["resources_remediated"]
             and (resource_id, r["run_id"]) not in already_reversed),
            None,
        )
        if matching_run is None:
            return False

        reversed_amount = matching_run["monthly_savings_added"]
        ledger["runs"].append({
            "run_id": f"rollback-{resource_id}-{matching_run['run_id']}",
            "type": "rollback",
            "timestamp": matching_run["timestamp"],
            "resources_remediated": [resource_id],
            "rolled_back_run_id": matching_run["run_id"],
            "monthly_savings_added": -reversed_amount,
            "cumulative_at_time": 0.0,
        })
        total = self._recalculate_total(ledger["runs"])
        ledger["total_lifetime_savings"] = total
        ledger["runs"][-1]["cumulative_at_time"] = total
        self._write_ledger(ledger)
        return True

    def get_savings_summary(self) -> dict:
        """
        Return savings summary.

        Returns:
            {
                "total_lifetime_monthly": float,
                "total_lifetime_annual": float,
                "total_runs": int,
                "last_run_savings": float,
            }
        """
        ledger = self._load_ledger()
        runs = ledger["runs"]

        if not runs:
            return {
                "total_lifetime_monthly": 0.0,
                "total_lifetime_annual": 0.0,
                "total_runs": 0,
                "last_run_savings": 0.0,
            }

        total_monthly = self._recalculate_total(runs)
        last_run_savings = runs[-1]["monthly_savings_added"]

        return {
            "total_lifetime_monthly": total_monthly,
            "total_lifetime_annual": total_monthly * 12,
            "total_runs": len(runs),
            "last_run_savings": last_run_savings,
        }

    def _load_ledger(self) -> dict:
        """Load ledger from disk or return empty structure."""
        try:
            content = self._ledger_path.read_text(encoding="utf-8")
            return json.loads(content)  # type: ignore[no-any-return]
        except (FileNotFoundError, json.JSONDecodeError):
            return {"total_lifetime_savings": 0.0, "runs": []}

    def _write_ledger(self, ledger: dict) -> None:
        """Write ledger to disk (atomic: tmp + rename)."""
        tmp_path = self._ledger_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(ledger, indent=2), encoding="utf-8"
        )
        tmp_path.replace(self._ledger_path)

    def _compute_monthly_savings(self, resources_remediated: list[str]) -> float:
        """Sum cost_estimate_monthly for matching findings. Missing cost treated as 0.0."""
        findings_data = self._read_findings_store()
        findings = findings_data.get("findings", [])

        remediated_set = set(resources_remediated)
        return sum(  # type: ignore[no-any-return]
            finding.get("cost_estimate_monthly", 0.0)
            for finding in findings
            if finding.get("resource_id") in remediated_set
        )

    def _recalculate_total(self, runs: list[dict]) -> float:
        """Sum monthly_savings_added across all runs."""
        return sum(r["monthly_savings_added"] for r in runs)  # type: ignore[no-any-return]

    def _read_findings_store(self) -> dict:
        """Read and parse findings_store.json. Returns empty store on missing/invalid file."""
        try:
            content = self._findings_store_path.read_text(encoding="utf-8")
            return json.loads(content)  # type: ignore[no-any-return]
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"scan_id": "", "completed_at": "", "findings": []}

