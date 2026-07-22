"""Content-presence tests for docs/deployment.md.

Validates: Requirements 5.1, 5.3, 5.4, 5.5
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEPLOYMENT_MD = PROJECT_ROOT / "docs" / "deployment.md"


class TestDeploymentGuideExists:
    """docs/deployment.md must exist and be non-empty."""

    def test_file_exists(self):
        assert DEPLOYMENT_MD.exists(), "docs/deployment.md does not exist"

    def test_file_is_not_empty(self):
        content = DEPLOYMENT_MD.read_text(encoding="utf-8")
        assert len(content.strip()) > 0, "docs/deployment.md is empty"


class TestRequiredSectionHeaders:
    """All six required section headers must be present."""

    REQUIRED_HEADERS = [
        "Reference Architecture",
        "EC2 Setup",
        "Identity & Auth",
        "Least-Privilege IAM",
        "BYO-Endpoint & Privacy Posture",
        "Operational Notes",
    ]

    @pytest.fixture()
    def content(self) -> str:
        return DEPLOYMENT_MD.read_text(encoding="utf-8")

    @pytest.mark.parametrize("header", REQUIRED_HEADERS)
    def test_section_header_present(self, content: str, header: str):
        # Check for markdown heading (## Header)
        assert f"## {header}" in content, (
            f"Missing required section header '## {header}' in docs/deployment.md"
        )


class TestLiteralSubstrings:
    """Required literal strings must appear in the deployment guide."""

    @pytest.fixture()
    def content(self) -> str:
        return DEPLOYMENT_MD.read_text(encoding="utf-8")

    def test_contains_read_policy_filename(self, content: str):
        assert "janitor-read-policy.json" in content

    def test_contains_remediation_policy_filename(self, content: str):
        assert "janitor-remediation-policy.json" in content

    def test_contains_remediation_role_arn_env_var(self, content: str):
        assert "JANITOR_REMEDIATION_ROLE_ARN" in content

    def test_contains_llm_retention_policy_reference(self, content: str):
        """Phase 1 SEC-1 privacy posture env var must be referenced."""
        assert "JANITOR_LLM_RETENTION_POLICY" in content

    def test_contains_get_caller_identity_reference(self, content: str):
        """Phase 1 SEC-3 identity verification must be referenced."""
        assert "GetCallerIdentity" in content


class TestNoDuplicateSecurityHardeningSection:
    """The deployment guide must NOT restate the README's Security Hardening heading."""

    def test_no_security_hardening_heading(self):
        content = DEPLOYMENT_MD.read_text(encoding="utf-8")
        # The exact heading "## Security Hardening" should NOT appear —
        # the guide links to the README section but doesn't duplicate it.
        assert "## Security Hardening" not in content, (
            "docs/deployment.md contains '## Security Hardening' as its own section heading. "
            "It should link to README.md's Security Hardening section, not restate it."
        )
