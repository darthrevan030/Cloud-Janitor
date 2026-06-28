"""AnomalyDetector — Flags suspicious resources not caught by rule-based checks.

Uses claude-haiku-4-5 via OpenRouter to analyze resources for unusual patterns,
naming anomalies, region mismatches, and cost outliers that rule-based FinOps/SecOps
agents would miss.
"""

import json
import sys

from llm_client import get_client, DEFAULT_MODEL

VALID_SEVERITIES: set[str] = {"high", "medium", "low"}

REQUIRED_ANOMALY_KEYS: set[str] = {
    "anomaly_id",
    "resource_id",
    "anomaly_type",
    "description",
    "severity",
    "evidence",
}

MAX_ANOMALIES: int = 20

PROMPT_TEMPLATE: str = """You are a cloud infrastructure anomaly detector. Analyze the following resources for suspicious patterns that rule-based checks might miss.

Look for:
- Unusual port configurations
- Naming anomalies (inconsistent naming conventions)
- Region mismatches (resources in unexpected regions)
- Cost outliers (resources with unexpectedly high costs)
- Configuration drift (settings that deviate from common patterns)
- Orphaned or dangling references

Resources to analyze:
{resources_json}

Return a JSON array of anomaly objects. Each anomaly must have:
- "anomaly_id": a slug string (e.g. "anomaly-unusual-port-sg-123"), non-empty
- "resource_id": the resource ID this anomaly relates to (must match one of the input resources), non-empty
- "anomaly_type": category string (e.g. "unusual_port", "naming_anomaly", "region_mismatch", "cost_outlier"), non-empty
- "description": plain English explanation, 1-2 sentences, non-empty
- "severity": one of "high", "medium", or "low"
- "evidence": specific detail that triggered this anomaly, non-empty

If no anomalies are found, return an empty JSON array: []

Respond with ONLY the JSON array, no markdown formatting or explanation."""


class AnomalyDetector:
    """Detects anomalies beyond rule-based findings in cloud resources."""

    def __init__(self, model: str = DEFAULT_MODEL):
        self._model = model

    def detect(self, resources: list[dict], findings: list[dict]) -> list[dict]:
        """Detect anomalies beyond rule-based findings.

        Args:
            resources: Raw resource list from get_cost_data() and get_security_data().
            findings: Already-identified findings from FinOps + SecOps agents.

        Returns:
            A flat list of up to 20 anomaly dicts, each with:
            anomaly_id, resource_id, anomaly_type, description, severity, evidence.
            Returns [] on any exception or when no unflagged resources exist.
        """
        try:
            # Build set of already-flagged resource IDs (Req 6.1)
            already_flagged_ids = {
                f["resource_id"] for f in findings if "resource_id" in f
            }

            # Filter out resources already flagged
            unflagged_resources = [
                r for r in resources
                if r.get("id", r.get("resource_id")) not in already_flagged_ids
            ]

            # Empty resources → return [] without calling LLM (Req 6.5)
            if not unflagged_resources:
                return []

            # Always call LLM when unflagged resources exist (Req 6.5)
            anomalies = self._call_llm(unflagged_resources)

            # Post-filter: ensure no anomaly resource_id exists in findings (Req 6.1)
            filtered = [
                a for a in anomalies
                if a.get("resource_id") not in already_flagged_ids
            ]

            # Validate each anomaly schema (Req 6.2, 6.3)
            validated = []
            for anomaly in filtered:
                v = self._validate_anomaly(anomaly)
                if v is not None:
                    validated.append(v)
                if len(validated) >= MAX_ANOMALIES:
                    break

            return validated

        except Exception as exc:
            print(
                f"[AnomalyDetector] Error detecting anomalies: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return []

    def _call_llm(self, resources: list[dict]) -> list[dict]:
        """Call LLM to analyze resources for anomalies.

        Returns parsed list of anomaly dicts, or [] on failure.
        """
        # Limit resources sent to LLM to avoid token overflow
        resources_subset = resources[:30]
        resources_json = json.dumps(resources_subset, indent=2, default=str)

        prompt = PROMPT_TEMPLATE.format(resources_json=resources_json)

        client = get_client()
        response = client.chat.completions.create(
            model=self._model,
            max_tokens=1024,
            messages=[
                {
                    "role": "system",
                    "content": "You are a JSON-only anomaly detector. Return only valid JSON arrays.",
                },
                {"role": "user", "content": prompt},
            ],
        )

        raw_content = response.choices[0].message.content

        # Strip markdown code fences if present
        raw_content = raw_content.strip()
        if raw_content.startswith("```"):
            lines = raw_content.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            raw_content = "\n".join(lines)

        parsed = json.loads(raw_content)

        # Req 6.6: Invalid structure → return []
        if not isinstance(parsed, list):
            return []

        return parsed

    def _validate_anomaly(self, anomaly: dict) -> dict | None:
        """Validate an anomaly dict and return cleaned version or None if invalid.

        Each valid anomaly must have exactly 6 required keys:
        anomaly_id, resource_id, anomaly_type, description, severity, evidence.
        Severity must be one of: high, medium, low.
        """
        if not isinstance(anomaly, dict):
            return None

        # Check all required keys are present and non-empty strings
        for key in REQUIRED_ANOMALY_KEYS:
            value = anomaly.get(key)
            if not isinstance(value, str) or not value.strip():
                return None

        # Validate severity (Req 6.3)
        severity = anomaly["severity"].strip().lower()
        if severity not in VALID_SEVERITIES:
            return None

        return {
            "anomaly_id": anomaly["anomaly_id"].strip(),
            "resource_id": anomaly["resource_id"].strip(),
            "anomaly_type": anomaly["anomaly_type"].strip(),
            "description": anomaly["description"].strip(),
            "severity": severity,
            "evidence": anomaly["evidence"].strip(),
        }
