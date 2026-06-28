"""MultiAccountOrchestrator — Runs concurrent audits across multiple AWS accounts.

Loads account configurations from accounts.json, executes audits in parallel via
ThreadPoolExecutor, and aggregates findings with fault isolation per account.
"""

import concurrent.futures
import json
import os
import re
import sys
from pathlib import Path

ROLE_ARN_PATTERN = re.compile(r"^arn:aws:iam::\d{12}:role/.+$")
REQUIRED_ACCOUNT_FIELDS = {"account_id", "account_name", "role_arn", "region", "priority"}
VALID_PRIORITIES = {"high", "medium", "low"}
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
DEFAULT_MAX_WORKERS = 5
PER_ACCOUNT_TIMEOUT = 300


def _empty_result() -> dict:
    """Return the empty/failure result dict with all required fields."""
    return {
        "accounts_scanned": 0,
        "total_findings": 0,
        "total_waste": 0.0,
        "critical_count": 0,
        "by_account": [],
        "aggregate_findings": [],
        "cross_account_duplicates": 0,
    }


class MultiAccountOrchestrator:
    """Runs concurrent audits across multiple AWS accounts defined in accounts.json."""

    def __init__(
        self,
        accounts_path: Path | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ):
        if accounts_path is None:
            project_root = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            self._accounts_path = project_root / "accounts.json"
        else:
            self._accounts_path = Path(accounts_path)

        self._max_workers = max_workers
        self._project_root = self._accounts_path.parent

    def load_accounts(self) -> list[dict]:
        """Load and validate accounts from accounts.json.

        Returns:
            List of valid account config dicts. Returns [] if file is
            missing, invalid JSON, or contains no valid entries.
        """
        if not self._accounts_path.exists():
            return []

        try:
            data = json.loads(self._accounts_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"[MultiAccountOrchestrator] Error loading accounts.json: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return []

        if not isinstance(data, list):
            print(
                "[MultiAccountOrchestrator] accounts.json is not a list",
                file=sys.stderr,
            )
            return []

        valid_accounts = []
        for entry in data:
            if not isinstance(entry, dict):
                continue

            # Check all required fields are present
            missing = REQUIRED_ACCOUNT_FIELDS - set(entry.keys())
            if missing:
                print(
                    f"[MultiAccountOrchestrator] Skipping account entry missing fields: {missing}",
                    file=sys.stderr,
                )
                continue

            # Validate role_arn format (Req 14.4)
            role_arn = entry.get("role_arn", "")
            if not ROLE_ARN_PATTERN.match(role_arn):
                print(
                    f"[MultiAccountOrchestrator] Skipping account '{entry.get('account_name', 'unknown')}': "
                    f"invalid role_arn '{role_arn}'",
                    file=sys.stderr,
                )
                continue

            # Validate priority
            if entry.get("priority") not in VALID_PRIORITIES:
                print(
                    f"[MultiAccountOrchestrator] Skipping account '{entry.get('account_name', 'unknown')}': "
                    f"invalid priority '{entry.get('priority')}'",
                    file=sys.stderr,
                )
                continue

            valid_accounts.append(entry)

        return valid_accounts

    def run_all(self) -> dict:
        """Execute audits across all configured accounts concurrently.

        Returns:
            Complete result dict with all required fields per Requirement 9.8.
            Returns empty result on any top-level failure.
        """
        try:
            accounts = self.load_accounts()
        except Exception as exc:
            print(
                f"[MultiAccountOrchestrator] Error loading accounts: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return _empty_result()

        if not accounts:
            return _empty_result()

        # Execute concurrent audits (Req 9.1)
        by_account = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            future_to_account = {
                executor.submit(self._audit_account, account): account
                for account in accounts
            }

            for future in concurrent.futures.as_completed(future_to_account):
                account = future_to_account[future]
                account_id = account["account_id"]
                account_name = account["account_name"]
                priority = account["priority"]

                try:
                    # Per-account timeout (Req 9.1)
                    result = future.result(timeout=PER_ACCOUNT_TIMEOUT)
                    by_account.append({
                        "account_id": account_id,
                        "account_name": account_name,
                        "priority": priority,
                        "findings": result.get("findings", []),
                        "waste": result.get("waste", 0.0),
                        "critical_count": result.get("critical_count", 0),
                        "status": "success",
                        "error": None,
                    })
                except (Exception, concurrent.futures.TimeoutError, concurrent.futures.CancelledError) as exc:
                    # Req 9.2, 14.7: Fault isolation — continue with remaining accounts
                    print(
                        f"[MultiAccountOrchestrator] Account '{account_name}' ({account_id}) "
                        f"failed: {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
                    by_account.append({
                        "account_id": account_id,
                        "account_name": account_name,
                        "priority": priority,
                        "findings": [],
                        "waste": 0.0,
                        "critical_count": 0,
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    })

        # Sort by_account by priority (high → medium → low), then alphabetically (Req 9.4)
        by_account.sort(key=lambda a: (PRIORITY_ORDER.get(a["priority"], 99), a["account_name"]))

        # Aggregate findings (Req 9.3) — inject account_id into each finding
        aggregate_findings = []
        for account_entry in by_account:
            for finding in account_entry["findings"]:
                finding_with_account = dict(finding)
                finding_with_account["account_id"] = account_entry["account_id"]
                aggregate_findings.append(finding_with_account)

        # Calculate totals
        total_findings = len(aggregate_findings)
        total_waste = sum(a["waste"] for a in by_account)
        critical_count = sum(a["critical_count"] for a in by_account)
        accounts_scanned = sum(1 for a in by_account if a["status"] == "success")

        # Calculate cross_account_duplicates (Req 9.6)
        cross_account_duplicates = self._calculate_cross_account_duplicates(by_account)

        return {
            "accounts_scanned": accounts_scanned,
            "total_findings": total_findings,
            "total_waste": total_waste,
            "critical_count": critical_count,
            "by_account": by_account,
            "aggregate_findings": aggregate_findings,
            "cross_account_duplicates": cross_account_duplicates,
        }

    def _audit_account(self, account: dict) -> dict:
        """Run audit for a single account with an isolated findings store.

        Args:
            account: Account config dict with required fields.

        Returns:
            Dict with findings, waste, and critical_count for this account.
        """
        from orchestrator import Orchestrator

        account_id = account["account_id"]

        # Isolated findings store per account (Req 9.7)
        findings_store_path = self._project_root / f"findings_store_{account_id}.json"

        # Create an Orchestrator instance for this account
        orch = Orchestrator(
            project_root=self._project_root,
        )
        # Override the findings store path to isolate per account
        orch.findings_store_path = findings_store_path
        orch._finops._findings_store_path = findings_store_path
        orch._secops._findings_store_path = findings_store_path
        orch._architect._findings_store_path = findings_store_path

        # Execute the audit
        result = orch.execute_audit()

        # Gather findings from the isolated store
        findings = []
        if findings_store_path.exists():
            try:
                data = json.loads(findings_store_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    findings = data
                elif isinstance(data, dict) and "findings" in data:
                    findings = data["findings"]
            except (json.JSONDecodeError, OSError):
                pass

        # If the orchestrator returned findings directly, use those
        if result.findings:
            findings = [
                f if isinstance(f, dict) else f.to_dict() if hasattr(f, "to_dict") else {}
                for f in result.findings
            ]

        # Calculate waste and critical count from findings
        waste = sum(
            float(f.get("cost_estimate_monthly", 0.0))
            for f in findings
            if isinstance(f, dict)
        )
        critical_count = sum(
            1 for f in findings
            if isinstance(f, dict) and str(f.get("severity", "")).upper() == "CRITICAL"
        )

        return {
            "findings": findings,
            "waste": waste,
            "critical_count": critical_count,
        }

    def _calculate_cross_account_duplicates(self, by_account: list[dict]) -> int:
        """Calculate cross-account duplicates by (resource_type, check_type) pairs.

        A finding is a cross-account duplicate if it shares the same
        (resource_type, check_type) pair with at least one finding in a different account.

        Returns:
            Total count of findings that are cross-account duplicates.
        """
        # Map (resource_type, check_type) → set of account_ids that have that pair
        pair_to_accounts: dict[tuple[str, str], set[str]] = {}

        for account_entry in by_account:
            account_id = account_entry["account_id"]
            for finding in account_entry["findings"]:
                if not isinstance(finding, dict):
                    continue
                resource_type = finding.get("resource_type", "")
                check_type = finding.get("check_type", "")
                if resource_type and check_type:
                    key = (resource_type, check_type)
                    if key not in pair_to_accounts:
                        pair_to_accounts[key] = set()
                    pair_to_accounts[key].add(account_id)

        # Count findings whose (resource_type, check_type) pair appears in multiple accounts
        duplicate_count = 0
        for account_entry in by_account:
            account_id = account_entry["account_id"]
            for finding in account_entry["findings"]:
                if not isinstance(finding, dict):
                    continue
                resource_type = finding.get("resource_type", "")
                check_type = finding.get("check_type", "")
                if resource_type and check_type:
                    key = (resource_type, check_type)
                    if len(pair_to_accounts.get(key, set())) > 1:
                        duplicate_count += 1

        return duplicate_count
