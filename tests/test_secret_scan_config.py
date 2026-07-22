"""CI smoke tests for secret scanning configuration (DX-1).

Validates that:
- .pre-commit-config.yaml exists and references gitleaks
- CI workflow includes a secret-scan job with fetch-depth: 0
- CI build job's needs list includes secret-scan
- README.md documents pre-commit install and baseline regeneration

Optionally: if gitleaks is available locally, runs it against a synthetic
fixture containing a fake AWS key to verify detection works.

Requirements: 1.1, 1.2, 1.3, 1.5
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestPreCommitConfig:
    """Requirement 1.1: .pre-commit-config.yaml references gitleaks."""

    def test_pre_commit_config_exists(self) -> None:
        """The config file must exist at the repo root."""
        config_path = PROJECT_ROOT / ".pre-commit-config.yaml"
        assert config_path.exists(), (
            f".pre-commit-config.yaml not found at {config_path}"
        )

    def test_pre_commit_config_references_gitleaks(self) -> None:
        """The config must include a gitleaks hook entry."""
        config_path = PROJECT_ROOT / ".pre-commit-config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        assert "repos" in config, "Config missing 'repos' key"
        repos = config["repos"]

        # Find a repo entry that references gitleaks
        gitleaks_repos = [
            r for r in repos
            if "gitleaks" in r.get("repo", "")
        ]
        assert len(gitleaks_repos) >= 1, (
            f"No gitleaks repo found in .pre-commit-config.yaml. "
            f"Repos: {[r.get('repo') for r in repos]}"
        )

        # Verify the hook ID is 'gitleaks'
        gitleaks_repo = gitleaks_repos[0]
        hook_ids = [h["id"] for h in gitleaks_repo.get("hooks", [])]
        assert "gitleaks" in hook_ids, (
            f"gitleaks repo found but no 'gitleaks' hook ID. Hook IDs: {hook_ids}"
        )


class TestCIWorkflow:
    """Requirements 1.2, 1.3: CI workflow includes secret-scan job."""

    def _load_ci_workflow(self) -> dict:
        """Load and parse the CI workflow YAML."""
        ci_path = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
        assert ci_path.exists(), f"CI workflow not found at {ci_path}"
        return yaml.safe_load(ci_path.read_text(encoding="utf-8"))

    def test_secret_scan_job_exists(self) -> None:
        """The CI workflow must have a 'secret-scan' job."""
        workflow = self._load_ci_workflow()
        jobs = workflow.get("jobs", {})
        assert "secret-scan" in jobs, (
            f"'secret-scan' job not found in ci.yml. Jobs: {list(jobs.keys())}"
        )

    def test_secret_scan_uses_fetch_depth_zero(self) -> None:
        """The secret-scan job's checkout step must use fetch-depth: 0."""
        workflow = self._load_ci_workflow()
        secret_scan = workflow["jobs"]["secret-scan"]
        steps = secret_scan.get("steps", [])

        # Find the checkout step
        checkout_steps = [
            s for s in steps
            if "actions/checkout" in s.get("uses", "")
        ]
        assert len(checkout_steps) >= 1, (
            "No actions/checkout step found in secret-scan job"
        )

        checkout = checkout_steps[0]
        fetch_depth = checkout.get("with", {}).get("fetch-depth")
        assert fetch_depth == 0, (
            f"secret-scan checkout must use fetch-depth: 0, got: {fetch_depth}"
        )

    def test_secret_scan_uses_gitleaks_action(self) -> None:
        """The secret-scan job must use the gitleaks/gitleaks-action."""
        workflow = self._load_ci_workflow()
        secret_scan = workflow["jobs"]["secret-scan"]
        steps = secret_scan.get("steps", [])

        gitleaks_steps = [
            s for s in steps
            if "gitleaks" in s.get("uses", "")
        ]
        assert len(gitleaks_steps) >= 1, (
            f"No gitleaks action step found in secret-scan job. "
            f"Steps: {[s.get('uses', s.get('run', '?')) for s in steps]}"
        )

    def test_build_job_needs_secret_scan(self) -> None:
        """The build job must depend on secret-scan."""
        workflow = self._load_ci_workflow()
        build = workflow["jobs"].get("build", {})
        needs = build.get("needs", [])

        assert "secret-scan" in needs, (
            f"build job's 'needs' must include 'secret-scan'. Current needs: {needs}"
        )


class TestReadmeDocumentation:
    """Requirement 1.5: README documents pre-commit install and baseline."""

    def _read_readme(self) -> str:
        """Read the README content."""
        readme_path = PROJECT_ROOT / "README.md"
        assert readme_path.exists(), "README.md not found at project root"
        return readme_path.read_text(encoding="utf-8")

    def test_readme_mentions_pre_commit_install(self) -> None:
        """README must document the pre-commit install command."""
        content = self._read_readme()
        assert "pre-commit install" in content, (
            "README.md does not contain 'pre-commit install' — "
            "developers won't know how to set up the hook"
        )

    def test_readme_mentions_gitleaksignore(self) -> None:
        """README must document how to handle false positives via .gitleaksignore."""
        content = self._read_readme()
        assert ".gitleaksignore" in content, (
            "README.md does not mention '.gitleaksignore' — "
            "developers won't know how to handle false positives"
        )

    def test_readme_mentions_baseline_regeneration(self) -> None:
        """README must document how to regenerate the baseline."""
        content = self._read_readme()
        assert "gitleaks detect" in content, (
            "README.md does not mention 'gitleaks detect' — "
            "baseline regeneration instructions missing"
        )


class TestGitleaksFunctional:
    """Optional: if gitleaks is available, verify it detects a synthetic secret."""

    @pytest.fixture
    def gitleaks_binary(self) -> str | None:
        """Return the gitleaks binary path, or None if not installed."""
        return shutil.which("gitleaks")

    def test_gitleaks_detects_synthetic_aws_key(self, gitleaks_binary) -> None:
        """Shell out to gitleaks against a fixture with a fake AWS key.

        Skipped (not failed) if gitleaks is not installed locally.
        """
        if gitleaks_binary is None:
            pytest.skip("gitleaks binary not found on PATH — skipping functional test")

        # Create a temporary file with a synthetic AWS access key pattern
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Initialize a git repo (gitleaks requires one)
            subprocess.run(
                ["git", "init"], cwd=tmp_dir,
                capture_output=True, check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=tmp_dir, capture_output=True, check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=tmp_dir, capture_output=True, check=True,
            )

            # Write a file containing a synthetic AWS key
            secret_file = Path(tmp_dir) / "leaked.py"
            secret_file.write_text(
                '# This file contains a synthetic secret for testing\n'
                'AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n'
                'AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"\n'
            )

            # Stage and commit
            subprocess.run(
                ["git", "add", "."], cwd=tmp_dir,
                capture_output=True, check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "add secret", "--no-verify"],
                cwd=tmp_dir, capture_output=True, check=True,
            )

            # Run gitleaks detect
            result = subprocess.run(
                [gitleaks_binary, "detect", "--source", tmp_dir, "--no-banner"],
                capture_output=True, text=True,
            )

            # gitleaks should exit non-zero (findings detected)
            assert result.returncode != 0, (
                f"gitleaks did not detect the synthetic AWS key. "
                f"Exit code: {result.returncode}\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )
