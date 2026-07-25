"""Integration tests for tfsec policy gate in hooks/pre-remediation.sh.

Validates: Requirements 6.1, 6.2, 6.3

Tests cover:
- 6.1: tfsec returns non-zero on HIGH finding → hook blocks remediation
- 6.2: tfsec not on PATH, JANITOR_REQUIRE_TFSEC unset → hook proceeds with warning
- 6.3: tfsec not on PATH, JANITOR_REQUIRE_TFSEC=1 → hook blocks remediation
"""

import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path



PROJECT_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = str(PROJECT_ROOT / "hooks" / "pre-remediation.sh").replace("\\", "/")

# On Windows, prefer Git Bash
GIT_BASH = Path(r"C:\Program Files\Git\usr\bin\bash.exe")
BASH = str(GIT_BASH) if GIT_BASH.exists() else shutil.which("bash") or "bash"


def _make_stub_script(directory: Path, name: str, exit_code: int = 0, stdout: str = "") -> Path:
    """Create an executable bash stub script in the given directory.

    Returns the path to the created script.
    """
    script_path = directory / name
    lines = ["#!/usr/bin/env bash"]
    if stdout:
        lines.append(f'echo "{stdout}"')
    lines.append(f"exit {exit_code}")
    script_path.write_text("\n".join(lines) + "\n")
    # Make executable (needed on Unix/Git Bash)
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script_path


def _create_valid_tf_file(directory: Path) -> Path:
    """Create a minimal valid .tf file for testing."""
    tf_file = directory / "remediation.tf"
    tf_file.write_text(
        'resource "aws_s3_bucket" "test" {\n'
        '  bucket = "my-test-bucket"\n'
        "}\n"
    )
    return tf_file


def _run_hook(
    remediation_file: str,
    rollback_file: str,
    env: dict,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Execute the pre-remediation hook script with given args and environment."""
    return subprocess.run(
        [BASH, HOOK_SCRIPT, remediation_file, rollback_file],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
    )


class TestTfsecHighFindingBlocks:
    """Requirement 6.1: tfsec returning non-zero on a HIGH finding blocks the hook."""

    def test_hook_exits_nonzero_when_tfsec_finds_high_severity(self):
        """When tfsec is on PATH and exits non-zero (HIGH finding), the hook
        must exit with a non-zero code, blocking the remediation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            # Create stub directory for our fake binaries
            stub_bin = tmp_path / "bin"
            stub_bin.mkdir()

            # Create a terraform stub that always succeeds (init, validate, fmt)
            _make_stub_script(stub_bin, "terraform", exit_code=0, stdout="Terraform v1.5.0")

            # Create a tfsec stub that exits non-zero (simulates HIGH finding)
            _make_stub_script(stub_bin, "tfsec", exit_code=1, stdout="HIGH: S3 bucket without encryption")

            # Create valid .tf files for remediation and rollback
            remediation_file = _create_valid_tf_file(tmp_path)
            rollback_file = tmp_path / "rollback.tf"
            rollback_file.write_text(
                'resource "aws_s3_bucket" "rollback" {\n'
                '  bucket = "my-rollback-bucket"\n'
                "}\n"
            )

            # Build a controlled PATH: our stubs first, then basic system utils
            stub_bin_str = str(stub_bin).replace("\\", "/")
            env = {
                "PATH": f"{stub_bin_str}:/usr/bin:/bin",
                "TF_CMD": "terraform",
                "HOME": os.environ.get("HOME", os.environ.get("USERPROFILE", "/tmp")),
                "TMPDIR": tmp_dir,
                "TEMP": tmp_dir,
                "TMP": tmp_dir,
            }

            result = _run_hook(
                str(remediation_file).replace("\\", "/"),
                str(rollback_file).replace("\\", "/"),
                env=env,
            )

            # Hook MUST exit non-zero (blocked by tfsec)
            assert result.returncode != 0, (
                f"Hook should have been BLOCKED by tfsec HIGH finding but exited 0.\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )

    def test_hook_output_mentions_tfsec_policy_failure(self):
        """When tfsec blocks, the hook output should mention the tfsec policy check."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            stub_bin = tmp_path / "bin"
            stub_bin.mkdir()

            _make_stub_script(stub_bin, "terraform", exit_code=0, stdout="Terraform v1.5.0")
            _make_stub_script(stub_bin, "tfsec", exit_code=1, stdout="HIGH: Unencrypted volume")

            remediation_file = _create_valid_tf_file(tmp_path)
            rollback_file = tmp_path / "rollback.tf"
            rollback_file.write_text('resource "aws_s3_bucket" "rb" { bucket = "rb" }\n')

            stub_bin_str = str(stub_bin).replace("\\", "/")
            env = {
                "PATH": f"{stub_bin_str}:/usr/bin:/bin",
                "TF_CMD": "terraform",
                "HOME": os.environ.get("HOME", os.environ.get("USERPROFILE", "/tmp")),
                "TMPDIR": tmp_dir,
                "TEMP": tmp_dir,
                "TMP": tmp_dir,
            }

            result = _run_hook(
                str(remediation_file).replace("\\", "/"),
                str(rollback_file).replace("\\", "/"),
                env=env,
            )

            # Output should mention tfsec policy check failure
            combined_output = result.stdout + result.stderr
            assert "tfsec" in combined_output.lower() or "BLOCKED" in combined_output, (
                f"Expected tfsec/BLOCKED mention in output.\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )


class TestTfsecMissingProceedsWithWarning:
    """Requirement 6.2: tfsec not on PATH, JANITOR_REQUIRE_TFSEC unset → proceed with warning."""

    def test_hook_exits_zero_without_tfsec_when_not_required(self):
        """When tfsec is NOT on PATH and JANITOR_REQUIRE_TFSEC is not set,
        the hook should still exit 0 (proceed) with a warning."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            stub_bin = tmp_path / "bin"
            stub_bin.mkdir()

            # Only terraform stub — NO tfsec on PATH
            _make_stub_script(stub_bin, "terraform", exit_code=0, stdout="Terraform v1.5.0")

            remediation_file = _create_valid_tf_file(tmp_path)
            rollback_file = tmp_path / "rollback.tf"
            rollback_file.write_text('resource "aws_s3_bucket" "rb" { bucket = "rb" }\n')

            stub_bin_str = str(stub_bin).replace("\\", "/")
            # Explicitly do NOT set JANITOR_REQUIRE_TFSEC
            env = {
                "PATH": f"{stub_bin_str}:/usr/bin:/bin",
                "TF_CMD": "terraform",
                "HOME": os.environ.get("HOME", os.environ.get("USERPROFILE", "/tmp")),
                "TMPDIR": tmp_dir,
                "TEMP": tmp_dir,
                "TMP": tmp_dir,
            }

            result = _run_hook(
                str(remediation_file).replace("\\", "/"),
                str(rollback_file).replace("\\", "/"),
                env=env,
            )

            # Hook MUST exit 0 (proceed despite no tfsec)
            assert result.returncode == 0, (
                f"Hook should proceed (exit 0) when tfsec is missing and not required.\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )

    def test_hook_output_warns_about_missing_tfsec(self):
        """When tfsec is missing but not required, the hook should emit a warning
        about skipping the policy check."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            stub_bin = tmp_path / "bin"
            stub_bin.mkdir()

            _make_stub_script(stub_bin, "terraform", exit_code=0, stdout="Terraform v1.5.0")

            remediation_file = _create_valid_tf_file(tmp_path)
            rollback_file = tmp_path / "rollback.tf"
            rollback_file.write_text('resource "aws_s3_bucket" "rb" { bucket = "rb" }\n')

            stub_bin_str = str(stub_bin).replace("\\", "/")
            env = {
                "PATH": f"{stub_bin_str}:/usr/bin:/bin",
                "TF_CMD": "terraform",
                "HOME": os.environ.get("HOME", os.environ.get("USERPROFILE", "/tmp")),
                "TMPDIR": tmp_dir,
                "TEMP": tmp_dir,
                "TMP": tmp_dir,
            }

            result = _run_hook(
                str(remediation_file).replace("\\", "/"),
                str(rollback_file).replace("\\", "/"),
                env=env,
            )

            # Output should mention that tfsec is not installed / skipping
            combined_output = result.stdout + result.stderr
            assert "tfsec not installed" in combined_output or "skipping" in combined_output.lower(), (
                f"Expected warning about missing tfsec in output.\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )


class TestTfsecMissingRequiredBlocks:
    """Requirement 6.3: tfsec not on PATH, JANITOR_REQUIRE_TFSEC=1 → hook blocks."""

    def test_hook_exits_nonzero_when_tfsec_required_but_missing(self):
        """When tfsec is NOT on PATH and JANITOR_REQUIRE_TFSEC=1 is set,
        the hook must exit non-zero (block remediation)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            stub_bin = tmp_path / "bin"
            stub_bin.mkdir()

            # Only terraform stub — NO tfsec
            _make_stub_script(stub_bin, "terraform", exit_code=0, stdout="Terraform v1.5.0")

            remediation_file = _create_valid_tf_file(tmp_path)
            rollback_file = tmp_path / "rollback.tf"
            rollback_file.write_text('resource "aws_s3_bucket" "rb" { bucket = "rb" }\n')

            stub_bin_str = str(stub_bin).replace("\\", "/")
            # Set JANITOR_REQUIRE_TFSEC=1 to enforce tfsec
            env = {
                "PATH": f"{stub_bin_str}:/usr/bin:/bin",
                "TF_CMD": "terraform",
                "JANITOR_REQUIRE_TFSEC": "1",
                "HOME": os.environ.get("HOME", os.environ.get("USERPROFILE", "/tmp")),
                "TMPDIR": tmp_dir,
                "TEMP": tmp_dir,
                "TMP": tmp_dir,
            }

            result = _run_hook(
                str(remediation_file).replace("\\", "/"),
                str(rollback_file).replace("\\", "/"),
                env=env,
            )

            # Hook MUST exit non-zero (blocked because tfsec required but missing)
            assert result.returncode != 0, (
                f"Hook should BLOCK (exit non-zero) when JANITOR_REQUIRE_TFSEC=1 "
                f"and tfsec is not installed.\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )

    def test_hook_output_mentions_tfsec_required(self):
        """When blocked due to missing required tfsec, output should explain why."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            stub_bin = tmp_path / "bin"
            stub_bin.mkdir()

            _make_stub_script(stub_bin, "terraform", exit_code=0, stdout="Terraform v1.5.0")

            remediation_file = _create_valid_tf_file(tmp_path)
            rollback_file = tmp_path / "rollback.tf"
            rollback_file.write_text('resource "aws_s3_bucket" "rb" { bucket = "rb" }\n')

            stub_bin_str = str(stub_bin).replace("\\", "/")
            env = {
                "PATH": f"{stub_bin_str}:/usr/bin:/bin",
                "TF_CMD": "terraform",
                "JANITOR_REQUIRE_TFSEC": "1",
                "HOME": os.environ.get("HOME", os.environ.get("USERPROFILE", "/tmp")),
                "TMPDIR": tmp_dir,
                "TEMP": tmp_dir,
                "TMP": tmp_dir,
            }

            result = _run_hook(
                str(remediation_file).replace("\\", "/"),
                str(rollback_file).replace("\\", "/"),
                env=env,
            )

            # Output should mention tfsec required / JANITOR_REQUIRE_TFSEC
            combined_output = result.stdout + result.stderr
            assert "JANITOR_REQUIRE_TFSEC" in combined_output or "tfsec required" in combined_output.lower(), (
                f"Expected mention of JANITOR_REQUIRE_TFSEC or 'tfsec required' in output.\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )

    def test_hook_does_not_proceed_when_tfsec_required_but_missing(self):
        """Negative test: the hook must NOT output the success message when blocked."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            stub_bin = tmp_path / "bin"
            stub_bin.mkdir()

            _make_stub_script(stub_bin, "terraform", exit_code=0, stdout="Terraform v1.5.0")

            remediation_file = _create_valid_tf_file(tmp_path)
            rollback_file = tmp_path / "rollback.tf"
            rollback_file.write_text('resource "aws_s3_bucket" "rb" { bucket = "rb" }\n')

            stub_bin_str = str(stub_bin).replace("\\", "/")
            env = {
                "PATH": f"{stub_bin_str}:/usr/bin:/bin",
                "TF_CMD": "terraform",
                "JANITOR_REQUIRE_TFSEC": "1",
                "HOME": os.environ.get("HOME", os.environ.get("USERPROFILE", "/tmp")),
                "TMPDIR": tmp_dir,
                "TEMP": tmp_dir,
                "TMP": tmp_dir,
            }

            result = _run_hook(
                str(remediation_file).replace("\\", "/"),
                str(rollback_file).replace("\\", "/"),
                env=env,
            )

            # The success message should NOT appear
            assert "Both plans valid" not in result.stdout, (
                f"Hook should NOT report success when tfsec is required but missing.\n"
                f"stdout: {result.stdout}"
            )
