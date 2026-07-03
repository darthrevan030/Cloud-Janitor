"""GCP provider stub for the Cloud Janitor MCP server.

This module provides the GCPProvider class as a placeholder for future
Google Cloud Platform integration. All methods raise NotImplementedError.
"""

import logging
from typing import Optional

from cloud_janitor.mcp_server.backends import CloudProvider

logger = logging.getLogger(__name__)


class GCPProvider(CloudProvider):
    """Stub provider for Google Cloud Platform. Not yet implemented.

    All methods raise NotImplementedError with descriptive messages.
    This class serves as a placeholder for future GCP integration.
    """

    def __init__(self) -> None:
        logger.warning(
            "GCP support is not yet implemented. "
            "This provider is a placeholder for future integration."
        )

    def get_cost_data(self, resource_type: Optional[str] = None, min_idle_days: int = 7) -> dict:
        """Return idle/orphaned resource data from GCP.

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
        raise NotImplementedError("GCPProvider.get_cost_data() is not yet implemented")

    def get_security_data(self, check_type: Optional[str] = None) -> dict:
        """Return security findings from GCP Security Command Center.

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
        raise NotImplementedError("GCPProvider.get_security_data() is not yet implemented")

    def check_dependencies(self, resource_id: str) -> dict:
        """Check resource dependency graph in GCP.

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
        raise NotImplementedError("GCPProvider.check_dependencies() is not yet implemented")
