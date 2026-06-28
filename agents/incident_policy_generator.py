"""IncidentPolicyGenerator — Generates preventive scan policies from incident descriptions.

Uses claude-haiku-4-5 via OpenRouter to analyze incident descriptions and produce
structured policy JSON files that inform future scanning rules.
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from llm_client import get_client, DEFAULT_MODEL

VALID_CHECK_TYPES: set[str] = {"security_group", "encryption", "public_access", "idle_resource"}
VALID_RESOURCE_TYPES: set[str] = {"elasticache", "ebs", "ec2"}
POLICY_ID_PATTERN: re.Pattern = re.compile(r"^[a-z0-9\-]+$")
MAX_DESCRIPTION_LENGTH: int = 2000
MIN_POLICIES: int = 3
MAX_POLICIES: int = 5

PROMPT_TEMPLATE: str = """You are a cloud security policy generator. Based on the incident description below, generate preventive scan policies that would detect the conditions leading to this incident before it happens.

Incident description:
{incident_description}

Generate between 3 and 5 policy objects. Each policy must have:
- "policy_id": a lowercase slug string (letters, numbers, hyphens only), e.g. "policy-sg-open-redis-port"
- "policy_name": a short human-readable name
- "resource_types": a non-empty list from ["elasticache", "ebs", "ec2"]
- "check_type": one of "security_group", "encryption", "public_access", "idle_resource"
- "check_logic_description": plain English description of what to check
- "rationale": why this policy prevents the incident
- "query": a natural language query for scanning

Respond with ONLY a JSON array of policy objects, no markdown formatting or explanation."""


class IncidentPolicyGenerator:
    """Generates preventive scan policies from incident descriptions."""

    def __init__(self, model: str = DEFAULT_MODEL, policies_dir: Path | None = None):
        self._model = model
        if policies_dir is None:
            self._policies_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "policies"
        else:
            self._policies_dir = Path(policies_dir)

    def generate(self, incident_description: str) -> list[dict]:
        """Generate preventive scan policies from an incident description.

        Args:
            incident_description: Plain text describing a past incident or near-miss.
                Empty/whitespace returns [].
                Longer than 2000 chars is truncated before LLM call.

        Returns:
            A list of 3-5 policy dicts, or [] on any error.
        """
        try:
            # Input validation: empty/whitespace → []
            if not incident_description or not incident_description.strip():
                return []

            # Compute incident_hash from original un-truncated description
            incident_hash = hashlib.sha256(incident_description.encode("utf-8")).hexdigest()[:8]

            # Check for existing policies matching incident_hash
            existing = self._find_existing_policies(incident_hash)
            if existing:
                return existing

            # Truncate if needed (>2000 chars)
            description_for_llm = incident_description
            if len(incident_description) > MAX_DESCRIPTION_LENGTH:
                description_for_llm = incident_description[:MAX_DESCRIPTION_LENGTH]

            # Call LLM to generate policies
            policies = self._call_llm(description_for_llm)

            # If fewer than 3, retry once
            if len(policies) < MIN_POLICIES:
                retry_policies = self._call_llm(description_for_llm)
                if len(retry_policies) > len(policies):
                    policies = retry_policies

            # Cap at 5
            policies = policies[:MAX_POLICIES]

            # Ensure unique policy_ids
            seen_ids: set[str] = set()
            unique_policies: list[dict] = []
            for p in policies:
                pid = p.get("policy_id", "")
                if pid not in seen_ids:
                    seen_ids.add(pid)
                    unique_policies.append(p)
            policies = unique_policies

            # Enrich each policy with metadata
            generated_at = datetime.now(timezone.utc).isoformat()
            for policy in policies:
                policy["generated_at"] = generated_at
                policy["incident_hash"] = incident_hash
                policy["version"] = 1

            # Write policies to disk
            self._write_policies(policies)

            return policies

        except Exception as exc:
            print(
                f"[IncidentPolicyGenerator] Error generating policies: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return []

    def list_policies(self) -> list[dict]:
        """List all saved policies from the policies/ directory.

        Returns:
            A list of policy dicts loaded from disk, or [] on error.
        """
        try:
            if not self._policies_dir.exists():
                return []

            policies = []
            for f in sorted(self._policies_dir.glob("*.json")):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        policies.append(data)
                except (json.JSONDecodeError, OSError):
                    continue

            return policies
        except Exception as exc:
            print(
                f"[IncidentPolicyGenerator] Error listing policies: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return []

    def _find_existing_policies(self, incident_hash: str) -> list[dict]:
        """Check if policies with this incident_hash already exist on disk.

        Returns the existing policies if found, empty list otherwise.
        """
        if not self._policies_dir.exists():
            return []

        existing = []
        try:
            for f in self._policies_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data.get("incident_hash") == incident_hash:
                        existing.append(data)
                except (json.JSONDecodeError, OSError):
                    continue
        except Exception:
            return []

        return existing

    def _call_llm(self, description: str) -> list[dict]:
        """Call LLM to generate policies from the incident description.

        Returns validated policy dicts (may be fewer than MIN_POLICIES).
        """
        client = get_client()
        prompt = PROMPT_TEMPLATE.format(incident_description=description)

        response = client.chat.completions.create(
            model=self._model,
            max_tokens=1024,
            messages=[
                {
                    "role": "system",
                    "content": "You are a JSON-only policy generator. Return only valid JSON arrays.",
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

        if not isinstance(parsed, list):
            return []

        # Validate each policy
        validated = []
        for item in parsed:
            policy = self._validate_policy(item)
            if policy is not None:
                validated.append(policy)

        return validated

    def _validate_policy(self, item: dict) -> dict | None:
        """Validate a single policy dict from LLM output.

        Returns cleaned policy dict or None if invalid.
        """
        if not isinstance(item, dict):
            return None

        policy_id = item.get("policy_id", "")
        policy_name = item.get("policy_name", "")
        resource_types = item.get("resource_types", [])
        check_type = item.get("check_type", "")
        check_logic_description = item.get("check_logic_description", "")
        rationale = item.get("rationale", "")
        query = item.get("query", "")

        # Validate policy_id format
        if not isinstance(policy_id, str) or not POLICY_ID_PATTERN.match(policy_id):
            print(
                f"[IncidentPolicyGenerator] Skipping policy with unsafe ID: {repr(policy_id)}",
                file=sys.stderr,
            )
            return None

        # Validate policy_name
        if not isinstance(policy_name, str) or not policy_name.strip():
            return None

        # Validate check_type
        if not isinstance(check_type, str) or check_type not in VALID_CHECK_TYPES:
            return None

        # Validate resource_types: must be non-empty list with valid values only
        if not isinstance(resource_types, list) or not resource_types:
            return None
        valid_resources = [r for r in resource_types if isinstance(r, str) and r in VALID_RESOURCE_TYPES]
        if not valid_resources:
            return None

        # Validate other string fields
        if not isinstance(check_logic_description, str) or not check_logic_description.strip():
            return None
        if not isinstance(rationale, str) or not rationale.strip():
            return None
        if not isinstance(query, str) or not query.strip():
            return None

        return {
            "policy_id": policy_id,
            "policy_name": policy_name.strip(),
            "resource_types": valid_resources,
            "check_type": check_type,
            "check_logic_description": check_logic_description.strip(),
            "rationale": rationale.strip(),
            "query": query.strip(),
        }

    def _write_policies(self, policies: list[dict]) -> None:
        """Write policy dicts to disk as individual JSON files.

        Creates policies/ directory if needed.
        On I/O error, cleans up any partially written files and re-raises
        so the caller's except block can return [].
        """
        # Create directory if needed
        self._policies_dir.mkdir(parents=True, exist_ok=True)

        written_files: list[Path] = []
        try:
            for policy in policies:
                policy_id = policy["policy_id"]
                file_path = self._policies_dir / f"{policy_id}.json"
                file_path.write_text(
                    json.dumps(policy, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                written_files.append(file_path)
        except OSError as exc:
            # Clean up any partially written files
            for f in written_files:
                try:
                    f.unlink(missing_ok=True)
                except OSError:
                    pass
            raise exc
