"""Centralized, validated timeout configuration.

Each getter reads its env var fresh (not module-level) so tests can
monkeypatch os.environ without reimporting the module.

Note: _run_pre_remediation_hook_full()'s 60s internal timeout is
deliberately NOT included here — that method is never called by
execute_audit() in production (only its own unit tests exercise it),
so making its timeout configurable would add a public env var with no
effect on any real path. See design.md's "Key Architectural Decisions".
"""

import logging
import os

logger = logging.getLogger(__name__)

_CEILING_SECONDS = 1800  # 30 minutes — see Requirement 2.4

_DEFAULTS = {
    "JANITOR_TF_INIT_TIMEOUT": 120,
    "JANITOR_TF_APPLY_TIMEOUT": 120,
    "JANITOR_TF_VALIDATE_TIMEOUT": 180,
    "JANITOR_HOOK_TIMEOUT": 30,
    "JANITOR_LLM_TIMEOUT": 30,
}


def get_timeout(name: str) -> int:
    """Return the configured timeout for `name`, validated and clamped.

    `name` must be a key of _DEFAULTS. Invalid (non-positive-integer)
    values fall back to the default with a WARNING. Values above the
    ceiling (only applies to Terraform/hook timeouts, not the LLM one,
    which has its own much smaller practical ceiling) are clamped with
    a WARNING.
    """
    default = _DEFAULTS[name]
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError
    except ValueError:
        logger.warning("Invalid %s=%r — falling back to default %ds", name, raw, default)
        return default
    if name != "JANITOR_LLM_TIMEOUT" and value > _CEILING_SECONDS:
        logger.warning("%s=%ds exceeds ceiling %ds — clamping", name, value, _CEILING_SECONDS)
        return _CEILING_SECONDS
    return value
