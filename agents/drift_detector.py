"""DriftDetector — Compares scan snapshots over time and generates LLM narrative.

Uses atomic writes with filelock for thread-safe snapshot persistence.
Generates plain-English drift narratives via claude-haiku-4-5 through OpenRouter.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock, Timeout

from llm_client import get_client, DEFAULT_MODEL

MAX_SNAPSHOTS: int = 30
LOCK_TIMEOUT: int = 10
STALE_TMP_AGE_SECONDS: int = 60

NARRATIVE_PROMPT_TEMPLATE: str = """You are a cloud infrastructure drift analyst. Summarize the following changes between two scan snapshots in 2-3 plain English sentences.

Previous scan ID: {previous_scan_id}
Current scan ID: {current_scan_id}

New findings (not in previous scan): {new_count}
Resolved findings (were in previous, now gone): {resolved_count}
Waste delta: ${waste_delta:.2f}/month ({waste_direction})
Critical finding count change: {critical_delta} ({critical_direction})

New finding details:
{new_findings_summary}

Resolved finding details:
{resolved_findings_summary}

Write a concise 2-3 sentence narrative describing what changed, whether things improved or worsened, and any notable patterns. Respond with ONLY the narrative text, no JSON or formatting."""


class DriftDetector:
    """Compares scan snapshots over time and generates LLM narrative."""

    def __init__(
        self,
        history_path: Path | None = None,
        max_snapshots: int = MAX_SNAPSHOTS,
        model: str = DEFAULT_MODEL,
    ):
        if history_path is None:
            project_root = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            self._history_path = project_root / "scan_history.json"
        else:
            self._history_path = Path(history_path)

        self._max_snapshots = max_snapshots
        self._model = model
        self._lock_path = Path(str(self._history_path) + ".lock")
        self._tmp_path = Path(str(self._history_path) + ".tmp")

    def save_snapshot(
        self, scan_id: str, findings: list[dict], anomalies: list[dict], total_waste: float
    ) -> None:
        """Append a snapshot to scan_history.json atomically.

        Writes to a .tmp file then renames. Uses filelock for thread safety.
        Rotates to keep only the last max_snapshots entries.
        Logs errors to stderr, never raises.
        """
        try:
            # Clean up stale .tmp files older than 60 seconds (Req 14.6)
            self._cleanup_stale_tmp()

            lock = FileLock(self._lock_path, timeout=LOCK_TIMEOUT)
            try:
                lock.acquire()

                # Load existing history
                history = self._load_history()

                # Create new snapshot
                snapshot = {
                    "scan_id": scan_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "findings": findings,
                    "anomalies": anomalies,
                    "total_waste": float(total_waste),
                }

                # Append and rotate (Req 8.3)
                history.append(snapshot)
                if len(history) > self._max_snapshots:
                    history = history[-self._max_snapshots:]

                # Atomic write: write to .tmp then rename (Req 8.2)
                self._tmp_path.write_text(
                    json.dumps(history, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                self._tmp_path.replace(self._history_path)

            finally:
                lock.release()

        except Timeout:
            print(
                "[DriftDetector] Error saving snapshot: could not acquire file lock within timeout",
                file=sys.stderr,
            )
        except Exception as exc:
            print(
                f"[DriftDetector] Error saving snapshot: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    def detect(self, findings: list[dict]) -> dict:
        """Compare latest two snapshots and generate drift narrative.

        Args:
            findings: Current findings list (used for context but comparison
                      is done between the two most recent snapshots in history).

        Returns:
            Dict with drift analysis or {"drift": None, "reason": ...} on failure.
        """
        try:
            history = self._load_history()

            # Req 8.1: Fewer than 2 snapshots → insufficient history
            if len(history) < 2:
                return {"drift": None, "reason": "insufficient history"}

            previous = history[-2]
            current = history[-1]

            # Match findings by (resource_id, check_type) pair (Req 8.7)
            prev_keys = self._finding_keys(previous.get("findings", []))
            curr_keys = self._finding_keys(current.get("findings", []))

            new_keys = curr_keys - prev_keys
            resolved_keys = prev_keys - curr_keys

            # Build new/resolved finding lists
            curr_findings = current.get("findings", [])
            prev_findings = previous.get("findings", [])

            new_findings = [
                f for f in curr_findings
                if (f.get("resource_id", ""), f.get("check_type", "")) in new_keys
            ]
            resolved_findings = [
                f for f in prev_findings
                if (f.get("resource_id", ""), f.get("check_type", "")) in resolved_keys
            ]

            # Req 8.6: waste_delta = current - previous
            waste_delta = float(current.get("total_waste", 0.0)) - float(previous.get("total_waste", 0.0))

            # Req 8.8: critical_delta
            curr_critical = sum(
                1 for f in curr_findings
                if str(f.get("severity", "")).upper() == "CRITICAL"
            )
            prev_critical = sum(
                1 for f in prev_findings
                if str(f.get("severity", "")).upper() == "CRITICAL"
            )
            critical_delta = curr_critical - prev_critical

            # Req 8.9: Generate LLM narrative
            narrative = self._generate_narrative(
                previous_scan_id=previous.get("scan_id", "unknown"),
                current_scan_id=current.get("scan_id", "unknown"),
                new_findings=new_findings,
                resolved_findings=resolved_findings,
                waste_delta=waste_delta,
                critical_delta=critical_delta,
            )

            # Req 8.10: Return complete drift report
            return {
                "new_findings": new_findings,
                "resolved_findings": resolved_findings,
                "waste_delta": waste_delta,
                "critical_delta": critical_delta,
                "narrative": narrative,
                "compared_scans": [previous.get("scan_id", ""), current.get("scan_id", "")],
            }

        except Exception as exc:
            print(
                f"[DriftDetector] Error detecting drift: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return {"drift": None, "reason": "error"}

    def _load_history(self) -> list[dict]:
        """Load scan history from disk. Returns [] if file missing or invalid."""
        if not self._history_path.exists():
            return []
        try:
            data = json.loads(self._history_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            return []
        except (json.JSONDecodeError, OSError):
            return []

    def _finding_keys(self, findings: list[dict]) -> set[tuple[str, str]]:
        """Extract (resource_id, check_type) pairs from a findings list."""
        keys = set()
        for f in findings:
            if isinstance(f, dict):
                rid = f.get("resource_id", "")
                ct = f.get("check_type", "")
                if rid and ct:
                    keys.add((rid, ct))
        return keys

    def _cleanup_stale_tmp(self) -> None:
        """Delete .tmp file if it exists and is older than 60 seconds."""
        try:
            if self._tmp_path.exists():
                age = time.time() - self._tmp_path.stat().st_mtime
                if age > STALE_TMP_AGE_SECONDS:
                    self._tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _generate_narrative(
        self,
        previous_scan_id: str,
        current_scan_id: str,
        new_findings: list[dict],
        resolved_findings: list[dict],
        waste_delta: float,
        critical_delta: int,
    ) -> str:
        """Generate a 2-3 sentence LLM narrative describing drift.

        Returns a fallback message on LLM failure.
        """
        try:
            # Summarize findings for the prompt
            new_summary = self._summarize_findings(new_findings, max_items=5)
            resolved_summary = self._summarize_findings(resolved_findings, max_items=5)

            waste_direction = "more waste" if waste_delta >= 0 else "less waste"
            critical_direction = "more critical" if critical_delta >= 0 else "fewer critical"

            prompt = NARRATIVE_PROMPT_TEMPLATE.format(
                previous_scan_id=previous_scan_id,
                current_scan_id=current_scan_id,
                new_count=len(new_findings),
                resolved_count=len(resolved_findings),
                waste_delta=waste_delta,
                waste_direction=waste_direction,
                critical_delta=critical_delta,
                critical_direction=critical_direction,
                new_findings_summary=new_summary or "None",
                resolved_findings_summary=resolved_summary or "None",
            )

            client = get_client()
            response = client.chat.completions.create(
                model=self._model,
                max_tokens=256,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a concise cloud drift analyst. Write only plain text narratives.",
                    },
                    {"role": "user", "content": prompt},
                ],
            )

            narrative = response.choices[0].message.content.strip()
            if narrative:
                return narrative

            return self._fallback_narrative(
                new_findings, resolved_findings, waste_delta, critical_delta
            )

        except Exception as exc:
            print(
                f"[DriftDetector] Error generating narrative: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return self._fallback_narrative(
                new_findings, resolved_findings, waste_delta, critical_delta
            )

    def _summarize_findings(self, findings: list[dict], max_items: int = 5) -> str:
        """Create a brief text summary of findings for the LLM prompt."""
        if not findings:
            return ""
        lines = []
        for f in findings[:max_items]:
            rid = f.get("resource_id", "unknown")
            ct = f.get("check_type", "unknown")
            sev = f.get("severity", "unknown")
            lines.append(f"- {rid} ({ct}, severity={sev})")
        if len(findings) > max_items:
            lines.append(f"- ... and {len(findings) - max_items} more")
        return "\n".join(lines)

    def _fallback_narrative(
        self,
        new_findings: list[dict],
        resolved_findings: list[dict],
        waste_delta: float,
        critical_delta: int,
    ) -> str:
        """Generate a deterministic fallback narrative when LLM is unavailable."""
        parts = []
        if new_findings:
            parts.append(f"{len(new_findings)} new finding(s) detected")
        if resolved_findings:
            parts.append(f"{len(resolved_findings)} finding(s) resolved")
        if waste_delta != 0:
            direction = "increased" if waste_delta > 0 else "decreased"
            parts.append(f"waste {direction} by ${abs(waste_delta):.2f}/month")
        if critical_delta != 0:
            direction = "increased" if critical_delta > 0 else "decreased"
            parts.append(f"critical findings {direction} by {abs(critical_delta)}")
        if not parts:
            return "No significant drift detected between scans."
        return ". ".join(parts) + "."
