"""AWS provider implementation for the Cloud Janitor MCP server.

This module provides the AWSProvider class that will eventually query
live AWS infrastructure via boto3. Currently all methods are stubs that
raise NotImplementedError with descriptive messages.

boto3 is imported lazily — only when AWSProvider is instantiated — so
the MCP server can run without the AWS SDK when using other backends.
"""

from typing import Optional

from mcp_server.backends import CloudProvider


class AWSProvider(CloudProvider):
    """Provider that uses boto3 to query live AWS infrastructure.

    All methods currently raise NotImplementedError. This class serves as
    a documented starting point for implementing live AWS integration.

    boto3 is imported lazily at instantiation time. If boto3 is not
    installed, ImportError is raised with installation instructions.
    """

    def __init__(self, region: Optional[str] = None):
        """Initialize the AWS provider.

        Args:
            region: AWS region to use. Defaults to boto3's default region
                resolution (AWS_DEFAULT_REGION env var or ~/.aws/config).

        Raises:
            ImportError: If boto3 is not installed.
        """
        try:
            import boto3  # noqa: F401
        except ImportError:
            raise ImportError(
                "boto3 is required for the AWS backend but is not installed. "
                "Install it with: pip install boto3>=1.34.0"
            )
        self._region = region

    def get_cost_data(self, resource_type: Optional[str] = None, min_idle_days: int = 7) -> dict:
        """Return idle/orphaned resource data from AWS Cost Explorer.

        Required IAM Permissions:
            - ce:GetCostAndUsage
            - ce:GetCostForecast
            - ec2:DescribeVolumes
            - ec2:DescribeInstances
            - elasticache:DescribeCacheClusters
            - cloudwatch:GetMetricStatistics

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

        Raises:
            NotImplementedError: This method is not yet implemented.
        """
        raise NotImplementedError(
            "AWSProvider.get_cost_data() is not yet implemented. "
            "Use JANITOR_BACKEND=fixture for local development."
        )

    def get_security_data(self, check_type: Optional[str] = None) -> dict:
        """Return security findings from AWS Config and Security Hub.

        Required IAM Permissions:
            - securityhub:GetFindings
            - config:GetComplianceDetailsByConfigRule
            - ec2:DescribeSecurityGroups
            - elasticache:DescribeReplicationGroups
            - ec2:DescribeVolumes (for encryption checks)

        Args:
            check_type: Filter by check type (e.g. "security_group",
                "encryption", "public_access"). None means return all.

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
            "AWSProvider.get_security_data() is not yet implemented. "
            "Use JANITOR_BACKEND=fixture for local development."
        )

    def check_dependencies(self, resource_id: str) -> dict:
        """Check resource dependency graph using AWS Config relationships.

        Required IAM Permissions:
            - config:GetResourceConfigHistory
            - config:ListDiscoveredResources
            - ec2:DescribeNetworkInterfaces
            - elasticache:DescribeCacheClusters
            - ec2:DescribeVolumes

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
            "AWSProvider.check_dependencies() is not yet implemented. "
            "Use JANITOR_BACKEND=fixture for local development."
        )
