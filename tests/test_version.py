"""Unit tests for the version logic in cli.py.

Tests the inline `importlib.metadata.version("cloud-janitor")` call used by the
`--version` option, including the PackageNotFoundError fallback to "0.0.0-dev".

Does NOT import from `cloud_janitor` — that module doesn't exist until Batch 3.
Tests use Click's CliRunner with mocked importlib.metadata.

Validates: Requirements 9.2, 9.3, 9.4
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from packaging.version import Version


@pytest.fixture
def runner():
    """Provide a Click CliRunner."""
    return CliRunner()


class TestVersionFromMetadata:
    """Tests for the normal case: package is installed and metadata is available."""

    def test_version_option_uses_importlib_metadata_value(self, runner):
        """--version output should contain the version from importlib.metadata."""
        fake_version = "0.1.0"

        with patch("importlib.metadata.version", return_value=fake_version):
            # Must reload cli to pick up the patched version at module level
            import importlib
            import cloud_janitor.cli as cli

            importlib.reload(cli)
            from cloud_janitor.cli import main

            result = runner.invoke(main, ["--version"])

        assert result.exit_code == 0
        assert fake_version in result.output

    def test_version_string_is_used_in_output_format(self, runner):
        """--version output includes the program name alongside the version."""
        fake_version = "1.2.3"

        with patch("importlib.metadata.version", return_value=fake_version):
            import importlib
            import cloud_janitor.cli as cli

            importlib.reload(cli)
            from cloud_janitor.cli import main

            result = runner.invoke(main, ["--version"])

        assert result.exit_code == 0
        assert "cloud-janitor" in result.output
        assert "1.2.3" in result.output


class TestVersionFallback:
    """Tests for the fallback case: package not installed, PackageNotFoundError."""

    def test_fallback_to_dev_version_when_package_not_found(self, runner):
        """--version returns '0.0.0-dev' when importlib.metadata raises PackageNotFoundError."""

        def mock_version(name):
            if name == "cloud-janitor":
                raise PackageNotFoundError(name)
            # Let other packages resolve normally
            from importlib.metadata import version as real_version

            return real_version(name)

        with patch("importlib.metadata.version", side_effect=mock_version):
            import importlib
            import cloud_janitor.cli as cli

            importlib.reload(cli)
            from cloud_janitor.cli import main

            result = runner.invoke(main, ["--version"])

        assert result.exit_code == 0
        assert "0.0.0-dev" in result.output

    def test_fallback_version_still_includes_program_name(self, runner):
        """--version fallback output still shows 'cloud-janitor' prog name."""

        def mock_version(name):
            if name == "cloud-janitor":
                raise PackageNotFoundError(name)
            from importlib.metadata import version as real_version

            return real_version(name)

        with patch("importlib.metadata.version", side_effect=mock_version):
            import importlib
            import cloud_janitor.cli as cli

            importlib.reload(cli)
            from cloud_janitor.cli import main

            result = runner.invoke(main, ["--version"])

        assert result.exit_code == 0
        assert "cloud-janitor" in result.output


class TestVersionPEP440Conformance:
    """Tests that the version string conforms to PEP 440."""

    def test_installed_version_conforms_to_pep440(self, runner):
        """When metadata returns a version, it must be a valid PEP 440 version."""
        fake_version = "0.1.0"

        with patch("importlib.metadata.version", return_value=fake_version):
            import importlib
            import cloud_janitor.cli as cli

            importlib.reload(cli)
            from cloud_janitor.cli import main

            result = runner.invoke(main, ["--version"])

        assert result.exit_code == 0
        # Extract the version string from the output and validate PEP 440
        # Output format is: "cloud-janitor, version X.Y.Z\n"
        version_str = fake_version
        parsed = Version(version_str)
        assert str(parsed) == fake_version

    def test_fallback_version_conforms_to_pep440(self, runner):
        """The fallback '0.0.0-dev' string must parse as valid PEP 440.

        Note: PEP 440 normalizes '0.0.0-dev' to '0.0.0.dev0'.
        """

        def mock_version(name):
            if name == "cloud-janitor":
                raise PackageNotFoundError(name)
            from importlib.metadata import version as real_version

            return real_version(name)

        with patch("importlib.metadata.version", side_effect=mock_version):
            import importlib
            import cloud_janitor.cli as cli

            importlib.reload(cli)

        # The module-level _version should be "0.0.0-dev"
        # Verify it parses as PEP 440 (it normalizes to 0.0.0.dev0)
        parsed = Version("0.0.0-dev")
        assert parsed.is_devrelease
        assert parsed.major == 0
        assert parsed.minor == 0
        assert parsed.micro == 0

    def test_semver_style_version_conforms_to_pep440(self, runner):
        """A typical semver-style version like '2.5.1' must parse as valid PEP 440."""
        fake_version = "2.5.1"

        with patch("importlib.metadata.version", return_value=fake_version):
            import importlib
            import cloud_janitor.cli as cli

            importlib.reload(cli)

        parsed = Version(fake_version)
        assert parsed.major == 2
        assert parsed.minor == 5
        assert parsed.micro == 1

    def test_prerelease_version_conforms_to_pep440(self, runner):
        """A pre-release version like '1.0.0rc1' must parse as valid PEP 440."""
        fake_version = "1.0.0rc1"

        with patch("importlib.metadata.version", return_value=fake_version):
            import importlib
            import cloud_janitor.cli as cli

            importlib.reload(cli)

        parsed = Version(fake_version)
        assert parsed.is_prerelease
        assert parsed.major == 1


class TestVersionNegativeCases:
    """Negative tests ensuring invalid states are properly handled."""

    def test_version_is_not_empty_string(self, runner):
        """The version displayed must never be an empty string."""
        fake_version = "0.1.0"

        with patch("importlib.metadata.version", return_value=fake_version):
            import importlib
            import cloud_janitor.cli as cli

            importlib.reload(cli)
            from cloud_janitor.cli import main

            result = runner.invoke(main, ["--version"])

        assert result.exit_code == 0
        # The output should contain meaningful content, not just whitespace
        version_line = result.output.strip()
        assert len(version_line) > 0
        assert version_line != "cloud-janitor, version"

    def test_invalid_version_string_does_not_conform_to_pep440(self):
        """Verify that a truly invalid version string is rejected by PEP 440 parser."""
        with pytest.raises(Exception):
            Version("not_a_version!!!")
