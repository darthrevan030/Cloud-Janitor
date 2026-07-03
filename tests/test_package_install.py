"""Verify package installability, subpackage imports, and type annotation marker.

Requirements: 3.2, 3.3, 3.5, 3.6, 9.1, 9.5, 10.1, 10.3
"""

from __future__ import annotations

import importlib
import pathlib
from unittest.mock import patch

import pytest
from packaging.version import Version


class TestPackageImport:
    """Test that the top-level package is importable."""

    def test_import_cloud_janitor_succeeds(self) -> None:
        """Importing cloud_janitor should not raise."""
        import cloud_janitor  # noqa: F401

        assert cloud_janitor is not None


class TestVersion:
    """Test __version__ attribute conforms to PEP 440 and matches pyproject.toml."""

    def test_version_is_pep440(self) -> None:
        """__version__ must parse as a valid PEP 440 version."""
        from cloud_janitor import __version__

        parsed = Version(__version__)
        assert str(parsed) == __version__

    def test_version_matches_pyproject(self) -> None:
        """__version__ should match the version declared in pyproject.toml."""
        from cloud_janitor import __version__

        pyproject_path = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
        content = pyproject_path.read_text(encoding="utf-8")
        # Extract version from pyproject.toml [project] section
        for line in content.splitlines():
            if line.strip().startswith("version") and "=" in line:
                expected = line.split("=", 1)[1].strip().strip('"')
                break
        else:
            pytest.fail("Could not find version in pyproject.toml")

        assert __version__ == expected

    def test_version_fallback_on_package_not_found(self) -> None:
        """When importlib.metadata.version raises PackageNotFoundError, fallback to 0.0.0-dev."""
        from importlib.metadata import PackageNotFoundError

        with patch(
            "importlib.metadata.version",
            side_effect=PackageNotFoundError("cloud-janitor"),
        ):
            # Force re-execution of the version lookup by reloading the module
            import cloud_janitor

            importlib.reload(cloud_janitor)
            assert cloud_janitor.__version__ == "0.0.0-dev"

        # Restore correct state
        importlib.reload(cloud_janitor)


class TestSubpackages:
    """Test that all declared subpackages are importable."""

    @pytest.mark.parametrize(
        "subpackage",
        [
            "cloud_janitor.agents",
            "cloud_janitor.core",
            "cloud_janitor.mcp_server",
            "cloud_janitor.orchestrator",
        ],
    )
    def test_subpackage_importable(self, subpackage: str) -> None:
        """Each subpackage should be importable without error."""
        mod = importlib.import_module(subpackage)
        assert mod is not None

    def test_nonexistent_subpackage_raises_module_not_found(self) -> None:
        """Importing a nonexistent subpackage must raise ModuleNotFoundError."""
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("cloud_janitor.nonexistent")


class TestPyTypedMarker:
    """Test that the py.typed marker file exists in the installed package."""

    def test_py_typed_exists_in_package_dir(self) -> None:
        """py.typed marker must exist in the cloud_janitor package directory."""
        import cloud_janitor

        package_dir = pathlib.Path(cloud_janitor.__file__).parent
        py_typed = package_dir / "py.typed"
        assert py_typed.exists(), f"py.typed not found at {py_typed}"

    def test_py_typed_is_empty(self) -> None:
        """py.typed marker should be a zero-byte file."""
        import cloud_janitor

        package_dir = pathlib.Path(cloud_janitor.__file__).parent
        py_typed = package_dir / "py.typed"
        assert py_typed.stat().st_size == 0, "py.typed should be 0 bytes"
