"""RemediationExplainer — Generates plain-English explanations of remediation plans.

Uses claude-haiku-4-5 via OpenRouter to produce concise, approachable explanations
describing why a finding is risky, what the Terraform fix will do, and what rollback
restores. Designed for the approval UI panel.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 1.2, 1.8, 1.9, 1.11
"""

import json
import sys

from core.llm_client import get_client, DEFAULT_MODEL

import logging

logger = logging.getLogger(__name__)


SAFE_DEFAULT: dict = {
    "risk_explanation": "Explanation unavailable.",
    "what_terraform_does": "Explanation unavailable.",
    "what_rollback_restores": "Explanation unavailable.",
}

PROMPT_TEMPLATE: str = """You are a cloud infrastructure expert writing explanations for a non-technical approval panel.

Given a security/cost finding and its Terraform remediation + rollback code, produce a JSON object with exactly three keys:

1. "risk_explanation": Why this finding is dangerous or wasteful. 2-3 sentences, plain English.
2. "what_terraform_does": What the remediation Terraform HCL will change in the infrastructure. 2-3 sentences, plain English.
3. "what_rollback_restores": What the rollback Terraform HCL will restore if needed. 1-2 sentences, plain English.

Resource ID: {resource_id}
Finding: {finding_json}
Remediation HCL:
```hcl
{remediation_hcl}
```
Rollback HCL:
```hcl
{rollback_hcl}
```

Respond with ONLY the JSON object, no markdown formatting or explanation."""


class RemediationExplainer:
    """Generates plain-English explanations for remediation plans."""

    def __init__(self, model: str = DEFAULT_MODEL):
        self._model = model

    def explain(
        self,
        resource_id: str,
        finding: dict,
        remediation_hcl: str,
        rollback_hcl: str,
    ) -> dict:
        """Generate explanation for a remediation plan.

        Args:
            resource_id: The resource being remediated.
            finding: The finding dict that triggered remediation.
            remediation_hcl: The generated Terraform HCL for the fix.
            rollback_hcl: The generated Terraform HCL for rollback.

        Returns:
            Dict with exactly 3 keys: risk_explanation, what_terraform_does,
            what_rollback_restores. Each value is a non-empty string.
            Returns SAFE_DEFAULT when inputs are empty/whitespace or on any error.
        """
        # Requirement 3.6: empty/whitespace HCL → return safe default without calling LLM
        if not remediation_hcl or not remediation_hcl.strip():
            return dict(SAFE_DEFAULT)
        if not rollback_hcl or not rollback_hcl.strip():
            return dict(SAFE_DEFAULT)

        try:
            client = get_client()
            prompt = PROMPT_TEMPLATE.format(
                resource_id=resource_id,
                finding_json=json.dumps(finding, default=str),
                remediation_hcl=remediation_hcl.strip(),
                rollback_hcl=rollback_hcl.strip(),
            )

            response = client.chat.completions.create(
                model=self._model,
                max_tokens=400,  # Requirement 3.5
                messages=[
                    {
                        "role": "system",
                        "content": "You are a JSON-only infrastructure explainer. Return only valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
            )

            raw_content = response.choices[0].message.content
            parsed = json.loads(raw_content)

            return self._validate(parsed)

        except Exception as exc:
            # Requirement 1.9: log failures to stderr
            print(
                f"[RemediationExplainer] Error: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            # Requirement 1.8: never raise to callers
            return dict(SAFE_DEFAULT)

    def _validate(self, parsed: dict) -> dict:
        """Validate LLM output into well-formed explanation dict.

        Ensures all 3 keys exist with non-empty string values.
        Falls back to SAFE_DEFAULT values for any missing/invalid key.
        """
        result = {}
        for key in ("risk_explanation", "what_terraform_does", "what_rollback_restores"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                result[key] = value.strip()
            else:
                result[key] = SAFE_DEFAULT[key]

        return result
