"""Cloud Janitor — AI-native infrastructure remediation tool."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__: str = version("cloud-janitor")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"
