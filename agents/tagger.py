"""ResourceTagger — Infers environment, team, and owner context from resource metadata.

Uses claude-haiku-4-5 via OpenRouter to analyze resource names, IDs, and metadata
patterns to infer tagging context for untagged or partially tagged cloud resources.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 1.4, 1.8, 1.9, 1.11
"""

import json
import sys

from core.llm_client import get_client, DEFAULT_MODEL

import logging

logger = logging.getLogger(__name__)


VALID_ENVS: set[str] = {"production", "staging", "development", "unknown"}
VALID_RISK_LEVELS: set[str] = {"high", "medium", "low"}

SAFE_DEFAULT: dict = {
    "env": "unknown",
    "team": None,
    "owner": None,
    "risk_level": "low",
    "confidence": 0.0,
}

PROMPT_TEMPLATE: str = """You are a cloud infrastructure tagging expert. Infer environment, team, owner, and risk context from the resource metadata below.

Return ONLY valid JSON with these fields:
- env: one of "production", "staging", "development", "unknown"
- team: a short team name string (e.g. "platform", "backend", "data") or null if undetectable
- owner: a short owner identifier (e.g. "backend-team", "infra-ops") or null if undetectable
- risk_level: one of "high", "medium", "low"
- confidence: float 0.0-1.0 indicating how confident you are in this inference

Resource ID: {resource_id}
Resource Name: {resource_name}
Existing Tags: {existing_tags_json}

Respond with ONLY the JSON object, no markdown formatting or explanation."""

BATCH_PROMPT_TEMPLATE: str = """You are a cloud infrastructure tagging expert. Infer environment, team, owner, and risk context for each resource below.

Return ONLY a valid JSON array where each element corresponds to the resource at the same index. Each element must have:
- env: one of "production", "staging", "development", "unknown"
- team: a short team name string or null if undetectable
- owner: a short owner identifier or null if undetectable
- risk_level: one of "high", "medium", "low"
- confidence: float 0.0-1.0 indicating how confident you are

Resources:
{resources_json}

Respond with ONLY the JSON array, no markdown formatting or explanation."""

BATCH_SIZE: int = 10


class ResourceTagger:
    """Infers environment/team/owner context from resource names and metadata."""

    def __init__(self, model: str = DEFAULT_MODEL, confidence_threshold: float = 0.7):
        self._model = model
        self._confidence_threshold = confidence_threshold

    def infer(self, resource_id: str, resource_name: str, existing_tags: dict | None = None) -> dict:
        """Infer tagging context for a single resource.

        Args:
            resource_id: The AWS resource ID.
            resource_name: Human-readable resource name.
            existing_tags: Already-known tags (skips inference for present fields).

        Returns:
            Dict with exactly 5 keys: env, team, owner, risk_level, confidence.
            Returns SAFE_DEFAULT on any error.
        """
        if existing_tags is None:
            existing_tags = {}

        # Req 5.5: Check if all fields already have non-empty, non-null string values
        existing_env = existing_tags.get("env")
        existing_team = existing_tags.get("team")
        existing_owner = existing_tags.get("owner")

        env_present = isinstance(existing_env, str) and existing_env != ""
        team_present = isinstance(existing_team, str) and existing_team != ""
        owner_present = isinstance(existing_owner, str) and existing_owner != ""

        # If all inference-target fields are already present, skip LLM call
        if env_present and team_present and owner_present:
            return {
                "env": existing_env if existing_env in VALID_ENVS else "unknown",
                "team": existing_team,
                "owner": existing_owner,
                "risk_level": "low",
                "confidence": 1.0,
            }

        try:
            client = get_client()
            prompt = PROMPT_TEMPLATE.format(
                resource_id=resource_id,
                resource_name=resource_name,
                existing_tags_json=json.dumps(existing_tags, default=str),
            )

            response = client.chat.completions.create(
                model=self._model,
                max_tokens=256,
                messages=[
                    {"role": "system", "content": "You are a JSON-only tagging inference engine. Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
            )

            raw_content = response.choices[0].message.content
            parsed = json.loads(raw_content)

            return self._validate_single(parsed, existing_tags)

        except Exception as exc:
            # Req 1.9: log failures to stderr
            print(
                f"[ResourceTagger] Error: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            # Req 1.8: never raise to callers
            return dict(SAFE_DEFAULT)

    def infer_batch(self, resources: list[dict]) -> list[dict]:
        """Batch inference for multiple resources.

        Each resource dict has: {resource_id, resource_name, existing_tags}.
        Returns list of inference dicts in same order as input.
        Splits into chunks of 10, one LLM call per chunk.

        Args:
            resources: List of resource dicts to infer tags for.

        Returns:
            List of inference dicts, one per input resource, preserving order.
            Returns SAFE_DEFAULT for each resource on any error.
        """
        if not resources:
            return []

        results: list[dict] = []

        # Req 5.6: split into chunks of 10
        for chunk_start in range(0, len(resources), BATCH_SIZE):
            chunk = resources[chunk_start:chunk_start + BATCH_SIZE]
            chunk_results = self._infer_chunk(chunk)
            results.extend(chunk_results)

        return results

    def _infer_chunk(self, chunk: list[dict]) -> list[dict]:
        """Process a single chunk of up to 10 resources with one LLM call."""
        try:
            # Build resource descriptions for the prompt
            resources_for_prompt = []
            for r in chunk:
                resources_for_prompt.append({
                    "resource_id": r.get("resource_id", ""),
                    "resource_name": r.get("resource_name", ""),
                    "existing_tags": r.get("existing_tags") or {},
                })

            client = get_client()
            prompt = BATCH_PROMPT_TEMPLATE.format(
                resources_json=json.dumps(resources_for_prompt, default=str),
            )

            response = client.chat.completions.create(
                model=self._model,
                max_tokens=256 * len(chunk),
                messages=[
                    {"role": "system", "content": "You are a JSON-only tagging inference engine. Return only a valid JSON array."},
                    {"role": "user", "content": prompt},
                ],
            )

            raw_content = response.choices[0].message.content
            parsed = json.loads(raw_content)

            if not isinstance(parsed, list):
                # Req 1.9: log unexpected structure
                print(
                    "[ResourceTagger] Error: batch LLM response is not a list",
                    file=sys.stderr,
                )
                return [dict(SAFE_DEFAULT) for _ in chunk]

            # Validate each result, padding with safe defaults if LLM returned fewer
            results = []
            for i, resource in enumerate(chunk):
                existing_tags = resource.get("existing_tags") or {}
                if i < len(parsed) and isinstance(parsed[i], dict):
                    results.append(self._validate_single(parsed[i], existing_tags))
                else:
                    results.append(dict(SAFE_DEFAULT))

            return results

        except Exception as exc:
            # Req 1.9: log failures to stderr
            print(
                f"[ResourceTagger] Error in batch: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            # Req 1.8: never raise — return safe defaults for entire chunk
            return [dict(SAFE_DEFAULT) for _ in chunk]

    def _validate_single(self, parsed: dict, existing_tags: dict) -> dict:
        """Validate and sanitize a single LLM inference result.

        Applies confidence_threshold logic and existing_tags passthrough.
        """
        if existing_tags is None:
            existing_tags = {}

        # Validate env (Req 5.2)
        env = parsed.get("env")
        if not isinstance(env, str) or env not in VALID_ENVS:
            env = "unknown"

        # Validate risk_level (Req 5.7)
        risk_level = parsed.get("risk_level")
        if not isinstance(risk_level, str) or risk_level not in VALID_RISK_LEVELS:
            risk_level = "low"

        # Validate confidence (Req 5.3)
        try:
            confidence = min(1.0, max(0.0, float(parsed.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0

        # Validate team and owner
        team = parsed.get("team")
        if not isinstance(team, str) or not team.strip():
            team = None
        else:
            team = team.strip()

        owner = parsed.get("owner")
        if not isinstance(owner, str) or not owner.strip():
            owner = None
        else:
            owner = owner.strip()

        # Req 5.4: confidence threshold logic
        # If confidence is strictly below threshold → set team and owner to None
        # If confidence equals threshold exactly → preserve inferred values
        if confidence < self._confidence_threshold:
            team = None
            owner = None

        # Req 5.5: existing_tags passthrough
        # Skip inference for fields with non-empty, non-null string values
        existing_env = existing_tags.get("env")
        existing_team = existing_tags.get("team")
        existing_owner = existing_tags.get("owner")

        if isinstance(existing_env, str) and existing_env != "":
            env = existing_env if existing_env in VALID_ENVS else env

        if isinstance(existing_team, str) and existing_team != "":
            team = existing_team

        if isinstance(existing_owner, str) and existing_owner != "":
            owner = existing_owner

        return {
            "env": env,
            "team": team,
            "owner": owner,
            "risk_level": risk_level,
            "confidence": confidence,
        }
