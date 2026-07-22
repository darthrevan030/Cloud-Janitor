"""Input-side redaction of finding data before LLM prompts.

Replaces ARNs, 12-digit AWS account IDs, and known resource IDs with stable
placeholder tokens. Provides rehydration to restore originals after LLM response.
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_ARN_PATTERN = re.compile(r"arn:aws:[a-zA-Z0-9\-]+:[a-zA-Z0-9\-]*:\d{12}:[^\s\"']+")
_ACCOUNT_PATTERN = re.compile(r"\b\d{12}\b")

# Keys whose values are never redacted — these are load-bearing for remediation quality.
_EXCLUDED_KEYS = frozenset({"resource_type", "region", "tags"})


def _collect_resource_ids_from_obj(obj: Any) -> list[str]:
    """Extract resource ID values from dict keys like 'resource_id' in the input structure."""
    found: list[str] = []

    def _walk_collect(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "resource_id" and isinstance(value, str) and value:
                    found.append(value)
                _walk_collect(value)
        elif isinstance(node, list):
            for item in node:
                _walk_collect(item)

    _walk_collect(obj)
    return found


def redact(
    obj: Any, resource_ids: list[str] | None = None
) -> tuple[Any, dict[str, str]]:
    """Replace ARNs, account IDs, and known resource IDs with stable placeholders.

    Walks dicts/lists/strings recursively. Does not touch keys named
    'resource_type', 'region', or 'tags'.

    Args:
        obj: The object to redact (dict, list, str, or other).
        resource_ids: Optional list of known resource IDs to also redact.

    Returns:
        A tuple of (scrubbed copy, mapping from placeholder -> original value).
    """
    mapping: dict[str, str] = {}
    # Reverse lookup: original value -> placeholder token (for reuse on repeats)
    _reverse: dict[str, str] = {}
    # Counters for each placeholder category
    _counters = {"ARN": 0, "ACCOUNT": 0, "RESOURCE": 0}

    # Collect resource IDs from both the explicit parameter and the object itself
    all_resource_ids: list[str] = list(resource_ids) if resource_ids else []
    all_resource_ids.extend(_collect_resource_ids_from_obj(obj))
    # Deduplicate while preserving order (longer IDs first to avoid partial matches)
    seen: set[str] = set()
    unique_ids: list[str] = []
    for rid in sorted(all_resource_ids, key=len, reverse=True):
        if rid not in seen:
            seen.add(rid)
            unique_ids.append(rid)
    all_resource_ids = unique_ids

    def _get_or_create_placeholder(original: str, category: str) -> str:
        """Return the existing placeholder for `original`, or create a new one."""
        if original in _reverse:
            return _reverse[original]
        _counters[category] += 1
        placeholder = f"{category}_{_counters[category]}"
        mapping[placeholder] = original
        _reverse[original] = placeholder
        return placeholder

    def _redact_string(text: str) -> str:
        """Redact sensitive patterns within a single string value."""
        # Order matters: redact ARNs first (they contain account IDs),
        # then resource IDs (which might be substrings), then bare account IDs.

        # 1. Replace ARNs
        def _replace_arn(match: re.Match) -> str:
            return _get_or_create_placeholder(match.group(0), "ARN")

        text = _ARN_PATTERN.sub(_replace_arn, text)

        # 2. Replace known resource IDs (literal string replacement)
        for rid in all_resource_ids:
            if rid and rid in text:
                placeholder = _get_or_create_placeholder(rid, "RESOURCE")
                text = text.replace(rid, placeholder)

        # 3. Replace 12-digit account IDs
        def _replace_account(match: re.Match) -> str:
            return _get_or_create_placeholder(match.group(0), "ACCOUNT")

        text = _ACCOUNT_PATTERN.sub(_replace_account, text)

        return text

    def _walk(node: Any, skip_redaction: bool = False) -> Any:
        """Recursively walk the structure, redacting string values."""
        if skip_redaction:
            # Return the node unchanged (used for excluded keys)
            return node

        if isinstance(node, dict):
            result = {}
            for key, value in node.items():
                if key in _EXCLUDED_KEYS:
                    # Preserve these values unchanged
                    result[key] = _walk(value, skip_redaction=True)
                else:
                    result[key] = _walk(value)
            return result
        elif isinstance(node, list):
            return [_walk(item) for item in node]
        elif isinstance(node, str):
            return _redact_string(node)
        else:
            # Numbers, booleans, None, etc. — pass through unchanged
            return node

    scrubbed = _walk(obj)
    return scrubbed, mapping


def rehydrate(text: str, mapping: dict[str, str]) -> str:
    """Replace every placeholder token in `text` with its original value.

    Each placeholder not found in the text is logged at DEBUG level so operators
    have observability into how often a configured model degrades redaction
    round-trip fidelity.

    Args:
        text: The LLM response text containing placeholder tokens.
        mapping: The redaction map (placeholder -> original value).

    Returns:
        The rehydrated text with placeholders replaced by original values.
    """
    for placeholder, original in mapping.items():
        if placeholder not in text:
            logger.debug(
                "rehydrate(): placeholder %s not found in LLM response text",
                placeholder,
            )
            continue
        text = text.replace(placeholder, original)
    return text
