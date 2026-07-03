"""Provider-agnostic backend module for the Cloud Janitor MCP server.

This module defines the CloudProvider abstract base class that all cloud
backends must implement. Provider selection is controlled via the
JANITOR_BACKEND environment variable.
"""

from abc import ABC, abstractmethod
from typing import Optional


class CloudProvider(ABC):
    """Abstract base class for cloud data providers.

    All cloud backends (fixture, AWS, GCP, Azure) must inherit from this
    class and implement all abstract methods. Python will raise TypeError
    at instantiation time if any abstract method is left unimplemented.
    """

    @abstractmethod
    def get_cost_data(self, resource_type: Optional[str] = None, min_idle_days: int = 7) -> dict:
        """Return idle/orphaned resource data.

        Args:
            resource_type: Filter by type (e.g. "elasticache", "ebs", "ec2").
                None means return all resource types.
            min_idle_days: Minimum idle days threshold. Only resources with
                idle_days >= this value are included.

        Returns:
            A dict with structure:
                {
                    "resources": [...],
                    "total_monthly_waste": float
                }
            Where total_monthly_waste == round(sum of monthly_cost for
            all returned resources, 2).
        """
        ...

    @abstractmethod
    def get_security_data(self, check_type: Optional[str] = None) -> dict:
        """Return security findings.

        Args:
            check_type: Filter by check type (e.g. "security_group",
                "encryption", "public_access"). None means return all.

        Returns:
            A dict with structure:
                {
                    "findings": [...],
                    "critical_count": int
                }
            Where critical_count equals the number of findings with
            severity == "CRITICAL".
        """
        ...

    @abstractmethod
    def check_dependencies(self, resource_id: str) -> dict:
        """Check resource dependency graph.

        Args:
            resource_id: Cloud resource ID to check.

        Returns:
            A dict with structure:
                {
                    "has_dependencies": bool,
                    "dependents": [...]
                }
            Where has_dependencies is True if and only if
            len(dependents) > 0.
        """
        ...


# Import concrete providers after CloudProvider is defined to avoid circular imports.
from mcp_server.backends.fixture_provider import FixtureProvider  # noqa: E402
from mcp_server.backends.aws_provider import AWSProvider  # noqa: E402
from mcp_server.backends.gcp_provider import GCPProvider  # noqa: E402
from mcp_server.backends.azure_provider import AzureProvider  # noqa: E402

__all__ = [
    "CloudProvider",
    "FixtureProvider",
    "AWSProvider",
    "GCPProvider",
    "AzureProvider",
]
