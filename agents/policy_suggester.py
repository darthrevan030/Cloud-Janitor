"""PolicySuggester — Recommends additional policy checks based on findings patterns.

Uses claude-haiku-4-5 via OpenRouter to analyze scan findings and suggest
relevant security and cost checks the user may have missed.
"""

import json
import sys

from llm_client import get_client, DEFAULT_MODEL

VALID_PRIORITIES: set[str] = {"high", "medium", "low"}

# Known check_types that can be inferred from suggestion content
KNOWN_CHECK_TYPES: list[str] = [
    "security_group",
    "encryption",
    "public_access",
    "idle_resource",
]

PROMPT_TEMPLATE: str = """You are a cloud security and cost optimization advisor. Based on the scan findings below, suggest additional policy checks the user should enable.

Current scan findings:
{findings_json}

Already checked (do NOT suggest these): {already_checked}

Return a JSON array of suggestion objects. Each suggestion must have:
- "suggestion_id": a slug string (e.g. "check-rds-encryption"), non-empty
- "title": short display name, max 80 characters, non-empty
- "rationale": one sentence explaining why this check is valuable, max 200 characters, non-empty
- "query": a natural language query that could be passed to a scan interpreter, non-empty
- "priority": one of "high", "medium", or "low"
- "check_type": the category this suggestion relates to (one of: "security_group", "encryption", "public_access", "idle_resource")

Return between 3 and 5 suggestions. Do NOT suggest checks related to: {already_checked}

Respond with ONLY the JSON array, no markdown formatting or explanation."""

EMPTY_FINDINGS_PROMPT: str = """You are a cloud security and cost optimization advisor. The user has not run any scans yet or has no findings. Suggest general-purpose policy checks they should enable for a healthy AWS environment.

Return a JSON array of suggestion objects. Each suggestion must have:
- "suggestion_id": a slug string (e.g. "check-rds-encryption"), non-empty
- "title": short display name, max 80 characters, non-empty
- "rationale": one sentence explaining why this check is valuable, max 200 characters, non-empty
- "query": a natural language query that could be passed to a scan interpreter, non-empty
- "priority": one of "high", "medium", or "low"
- "check_type": the category this suggestion relates to (one of: "security_group", "encryption", "public_access", "idle_resource")

Return between 3 and 5 suggestions covering common security and cost checks.

Respond with ONLY the JSON array, no markdown formatting or explanation."""

# Default suggestions returned when findings is empty and LLM is unavailable
DEFAULT_SUGGESTIONS: list[dict] = [
    {
        "suggestion_id": "check-unencrypted-ebs",
        "title": "Check for unencrypted EBS volumes",
        "rationale": "Unencrypted volumes risk data exposure if disks are compromised.",
        "query": "Find EBS volumes without encryption enabled",
        "priority": "high",
        "check_type": "encryption",
    },
    {
        "suggestion_id": "check-open-security-groups",
        "title": "Check for overly permissive security groups",
        "rationale": "Open security groups expose services to the internet unnecessarily.",
        "query": "Find security groups with 0.0.0.0/0 ingress on sensitive ports",
        "priority": "high",
        "check_type": "security_group",
    },
    {
        "suggestion_id": "check-public-access",
        "title": "Check for publicly accessible resources",
        "rationale": "Public resources may leak data or provide attack vectors.",
        "query": "Find resources with public access enabled",
        "priority": "medium",
        "check_type": "public_access",
    },
    {
        "suggestion_id": "check-idle-ec2",
        "title": "Check for idle EC2 instances",
        "rationale": "Idle instances waste money without providing value.",
        "query": "Find EC2 instances idle for more than 30 days",
        "priority": "medium",
        "check_type": "idle_resource",
    },
    {
        "suggestion_id": "check-idle-elasticache",
        "title": "Check for idle ElastiCache clusters",
        "rationale": "Unused cache clusters incur costs with no active connections.",
        "query": "Find ElastiCache clusters with no connections for 30 days",
        "priority": "low",
        "check_type": "idle_resource",
    },
]


class PolicySuggester:
    """Suggests additional policy checks based on scan findings patterns."""

    def __init__(self, model: str = DEFAULT_MODEL):
        self._model = model

    def suggest(self, findings: list[dict], already_checked: list[str]) -> list[dict]:
        """Suggest additional policies based on scan findings.

        Args:
            findings: The findings list from findings_store.json.
            already_checked: List of check_types already run
                (e.g. ["security_group", "encryption"]).

        Returns:
            A flat list of 0-5 suggestion dicts, each with:
            suggestion_id, title, rationale, query, priority.
            Returns [] on any exception.
        """
        try:
            suggestions = self._get_suggestions(findings, already_checked)

            # Post-process filter: remove suggestions whose check_type
            # matches an entry in already_checked (code-level enforcement)
            if already_checked:
                suggestions = self._filter_already_checked(suggestions, already_checked)

            # Validate and truncate to at most 5
            validated = []
            for s in suggestions:
                v = self._validate_suggestion(s)
                if v is not None:
                    validated.append(v)
                if len(validated) >= 5:
                    break

            return validated

        except Exception as exc:
            print(
                f"[PolicySuggester] Error generating suggestions: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return []

    def _get_suggestions(self, findings: list[dict], already_checked: list[str]) -> list[dict]:
        """Call LLM to get raw suggestions, or return defaults for empty findings."""
        if not findings:
            return self._get_empty_findings_suggestions(already_checked)

        client = get_client()
        # Limit findings sent to LLM to avoid token overflow
        findings_subset = findings[:20]
        findings_json = json.dumps(findings_subset, indent=2, default=str)

        prompt = PROMPT_TEMPLATE.format(
            findings_json=findings_json,
            already_checked=", ".join(already_checked) if already_checked else "none",
        )

        response = client.chat.completions.create(
            model=self._model,
            max_tokens=512,
            messages=[
                {
                    "role": "system",
                    "content": "You are a JSON-only policy advisor. Return only valid JSON arrays.",
                },
                {"role": "user", "content": prompt},
            ],
        )

        raw_content = response.choices[0].message.content
        # Strip markdown code fences if present
        raw_content = raw_content.strip()
        if raw_content.startswith("```"):
            lines = raw_content.split("\n")
            # Remove first and last lines (code fences)
            lines = [l for l in lines if not l.strip().startswith("```")]
            raw_content = "\n".join(lines)

        parsed = json.loads(raw_content)

        if not isinstance(parsed, list):
            return []

        return parsed

    def _get_empty_findings_suggestions(self, already_checked: list[str]) -> list[dict]:
        """Return general-purpose suggestions when findings is empty.

        Tries LLM first; falls back to hardcoded defaults.
        """
        try:
            client = get_client()

            response = client.chat.completions.create(
                model=self._model,
                max_tokens=512,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a JSON-only policy advisor. Return only valid JSON arrays.",
                    },
                    {"role": "user", "content": EMPTY_FINDINGS_PROMPT},
                ],
            )

            raw_content = response.choices[0].message.content
            raw_content = raw_content.strip()
            if raw_content.startswith("```"):
                lines = raw_content.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                raw_content = "\n".join(lines)

            parsed = json.loads(raw_content)
            if isinstance(parsed, list) and len(parsed) >= 1:
                return parsed
        except Exception:
            pass

        # Fallback to hardcoded defaults
        return list(DEFAULT_SUGGESTIONS)

    def _filter_already_checked(
        self, suggestions: list[dict], already_checked: list[str]
    ) -> list[dict]:
        """Remove suggestions whose check_type matches already_checked entries.

        This is the code-level post-processing filter required by requirement 4.2.
        It checks:
        1. Explicit 'check_type' field if present in the suggestion
        2. Infers check_type from suggestion_id and title content
        """
        already_set = set(already_checked)
        filtered = []

        for s in suggestions:
            check_type = self._infer_check_type(s)
            if check_type and check_type in already_set:
                continue
            filtered.append(s)

        return filtered

    def _infer_check_type(self, suggestion: dict) -> str | None:
        """Infer the check_type from a suggestion dict.

        Checks explicit check_type field first, then infers from content.
        """
        # Check explicit field
        explicit = suggestion.get("check_type", "")
        if isinstance(explicit, str) and explicit in KNOWN_CHECK_TYPES:
            return explicit

        # Infer from suggestion_id and title
        text = (
            suggestion.get("suggestion_id", "")
            + " "
            + suggestion.get("title", "")
            + " "
            + suggestion.get("query", "")
        ).lower()

        if any(kw in text for kw in ["security_group", "security group", "ingress", "egress", "port", "firewall"]):
            return "security_group"
        if any(kw in text for kw in ["encrypt", "kms", "unencrypted"]):
            return "encryption"
        if any(kw in text for kw in ["public_access", "public access", "publicly", "public"]):
            return "public_access"
        if any(kw in text for kw in ["idle", "unused", "unattached", "orphan"]):
            return "idle_resource"

        return None

    def _validate_suggestion(self, suggestion: dict) -> dict | None:
        """Validate a suggestion dict and return cleaned version or None if invalid.

        Each valid suggestion has exactly 5 keys:
        suggestion_id, title, rationale, query, priority.
        """
        if not isinstance(suggestion, dict):
            return None

        suggestion_id = suggestion.get("suggestion_id", "")
        title = suggestion.get("title", "")
        rationale = suggestion.get("rationale", "")
        query = suggestion.get("query", "")
        priority = suggestion.get("priority", "")

        # All fields must be non-empty strings
        if not isinstance(suggestion_id, str) or not suggestion_id.strip():
            return None
        if not isinstance(title, str) or not title.strip():
            return None
        if not isinstance(rationale, str) or not rationale.strip():
            return None
        if not isinstance(query, str) or not query.strip():
            return None
        if not isinstance(priority, str) or priority not in VALID_PRIORITIES:
            return None

        # Enforce length limits
        title = title.strip()[:80]
        rationale = rationale.strip()[:200]

        # Return exactly the 5 required keys
        return {
            "suggestion_id": suggestion_id.strip(),
            "title": title,
            "rationale": rationale,
            "query": query.strip(),
            "priority": priority,
        }
