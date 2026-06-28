"""Azure provider stub for the Cloud Janitor MCP server.

This module provides the AzureProvider class as a placeholder for future
Microsoft Azure integration. All methods raise NotImplementedError.
"""

from typing import Optional

from mcp_server.backends import CloudProvider


class AzureProvider(CloudProvider):
    """Stub provider for Microsoft Azure. Not yet implemented.

    All methods raise NotImplementedError with descriptive messages.
    This class serves as a placeholder for future Azure integration.
    """

    def get_cost_data(self, resource_type: Optional[str] = None, min_idle_days: int = 7) -> dict:
        """Return idle/orphaned resource data from Azure Cost Management.

        Args:
            resource_type: Filter by type. None means return all.
            min_idle_days: Minimum idle days threshold.

        Returns:
            A dict with structure:
                {
                    "resources": [...],
                    "total_monthly_waste": float
                }

        Raises:
            NotImplementedError: This method is not yet implemented.
        """
        raise NotImplementedError(
            "AzureProvider.get_cost_data() is not yet implemented. "
            "Azure support is planned for a future release."
        )

    def get_security_data(self, check_type: Optional[str] = None) -> dict:
        """Return security findings from Azure Defender / Security Center.

        Args:
            check_type: Filter by check type. None means return all.

        Returns:
            A dict with structure:
                {
                    "findings": [...],
                    "critical_count": int
                }

        Raises:
            NotImplementedError: This method is not yet implemented.
        """
        raise NotImplementedError(
            "AzureProvider.get_security_data() is not yet implemented. "
            "Azure support is planned for a future release."
        )

    def check_dependencies(self, resource_id: str) -> dict:
        """Check resource dependency graph in Azure.

        Args:
            resource_id: Cloud resource ID to check.

        Returns:
            A dict with structure:
                {
                    "has_dependencies": bool,
                    "dependents": [...]
                }

        Raises:
            NotImplementedError: This method is not yet implemented.
        """
        raise NotImplementedError(
            "AzureProvider.check_dependencies() is not yet implemented. "
            "Azure support is planned for a future release."
        )
