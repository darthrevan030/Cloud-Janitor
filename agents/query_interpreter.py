"""QueryInterpreter — Maps natural language queries to structured scan parameters.

Uses claude-haiku-4-5 via OpenRouter to parse free-text queries into structured
parameters for the FinOps/SecOps audit pipeline.
"""

import copy
import json
import sys

from core.llm_client import get_client, DEFAULT_MODEL

import logging

logger = logging.getLogger(__name__)


VALID_RESOURCE_TYPES: set[str] = {"elasticache", "ebs", "ec2"}
VALID_CHECK_TYPES: set[str] = {"security_group", "encryption", "public_access"}

SAFE_DEFAULT: dict = {
    "resource_types": [],
    "check_types": [],
    "min_idle_days": 7,
    "intent_summary": "Could not interpret query.",
    "confidence": 0.0,
}

PROMPT_TEMPLATE: str = """You are a cloud infrastructure query parser. Convert the user's natural language query into structured scan parameters.

Return ONLY valid JSON with these fields:
- resource_types: list of resource types to scan (valid values: "elasticache", "ebs", "ec2"). Use empty list [] for all types.
- check_types: list of security checks to run (valid values: "security_group", "encryption", "public_access"). Use empty list [] for all checks.
- min_idle_days: integer number of days a resource must be idle to be flagged (default 7, range 0-3650).
- intent_summary: a one-sentence summary (10-200 characters) of what the user wants to find.
- confidence: float 0.0-1.0 indicating how confident you are in parsing this query.

User query: {query}

Respond with ONLY the JSON object, no markdown formatting or explanation."""


class QueryInterpreter:
    """Translates natural language queries into structured scan parameters."""

    def __init__(self, model: str = DEFAULT_MODEL):
        self._model = model

    def interpret(self, query: str) -> dict:
        """Parse a natural language query into structured scan parameters.

        Args:
            query: Free-text query from the user.

        Returns:
            Dict with exactly 5 keys: resource_types, check_types,
            min_idle_days, intent_summary, confidence.
            Returns SAFE_DEFAULT on empty input or any error.
        """
        if not query or not isinstance(query, str) or not query.strip():
            return copy.deepcopy(SAFE_DEFAULT)

        try:
            client = get_client()
            prompt = PROMPT_TEMPLATE.format(query=query.strip())

            response = client.chat.completions.create(
                model=self._model,
                max_tokens=256,
                messages=[
                    {"role": "system", "content": "You are a JSON-only query parser. Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
            )

            raw_content = response.choices[0].message.content
            parsed = json.loads(raw_content)

            return self._validate(parsed)

        except Exception as exc:
            print(
                f"[QueryInterpreter] Error interpreting query: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return copy.deepcopy(SAFE_DEFAULT)

    def _validate(self, parsed: dict) -> dict:
        """Validate and sanitize LLM output into well-formed parameters."""
        resource_types = [
            r for r in parsed.get("resource_types", [])
            if isinstance(r, str) and r in VALID_RESOURCE_TYPES
        ]

        check_types = [
            c for c in parsed.get("check_types", [])
            if isinstance(c, str) and c in VALID_CHECK_TYPES
        ]

        # Clamp min_idle_days: non-negative, max 3650
        try:
            min_idle_days = max(0, min(3650, int(parsed.get("min_idle_days", 7))))
        except (TypeError, ValueError):
            min_idle_days = 7

        # Clamp confidence: [0.0, 1.0]
        try:
            confidence = min(1.0, max(0.0, float(parsed.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5

        # Validate intent_summary: string, 10-200 chars
        intent_summary = str(parsed.get("intent_summary", "Scan requested."))
        if len(intent_summary) < 10:
            intent_summary = intent_summary.ljust(10, ".")
        if len(intent_summary) > 200:
            intent_summary = intent_summary[:200]

        return {
            "resource_types": resource_types,
            "check_types": check_types,
            "min_idle_days": min_idle_days,
            "intent_summary": intent_summary,
            "confidence": confidence,
        }
