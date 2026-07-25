"""GCP provider for the Cloud Janitor MCP server.

Queries live GCP infrastructure via the google-cloud client libraries.
All three methods return dicts whose schema exactly matches the fixture files
so agents and tests need no special-casing between backends.

Credential resolution uses Application Default Credentials (ADC) via
google.auth.default(). Set up credentials with one of:
  - gcloud auth application-default login (interactive / local dev)
  - GOOGLE_APPLICATION_CREDENTIALS env var (service account key file)
  - GCE/GKE/Cloud Run metadata server (auto-detected on GCP infra)

Required GCP APIs (must be enabled in the project):
  - Compute Engine API (disks, firewall rules)
  - Cloud Memorystore for Redis API
  - Cloud Monitoring API (idle-day detection)
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from cloud_janitor.mcp_server.backends import CloudProvider

logger = logging.getLogger(__name__)


class GCPProvider(CloudProvider):
    """CloudProvider backed by live GCP APIs.

    google-cloud libraries are imported lazily at instantiation time so the
    MCP server can start without the GCP SDK when a different backend is selected.
    """

    def __init__(self, project_id: Optional[str] = None) -> None:
        """Initialise the GCP provider with ADC credential resolution.

        Args:
            project_id: GCP project ID. Resolution order:
                1. This constructor argument (if provided)
                2. The project returned by google.auth.default()
                3. GCP_PROJECT_ID environment variable

        Raises:
            ImportError: If google-auth / google-cloud libraries are not
                installed (the 'gcp' extras group).
            RuntimeError: If ADC credentials cannot be resolved, or if no
                project_id can be determined from any source.
        """
        # --- Lazy import: fail fast with actionable message if SDK missing ---
        try:
            import google.auth  # noqa: F401
            import google.auth.exceptions  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "GCP SDK not installed. Install with: "
                "pip install 'cloud-janitor[gcp]'"
            ) from exc

        # --- Credential resolution via Application Default Credentials ---
        try:
            credentials, adc_project = google.auth.default()
        except google.auth.exceptions.DefaultCredentialsError as exc:
            raise RuntimeError(
                "GCP credentials could not be resolved via Application Default "
                "Credentials (ADC). Run 'gcloud auth application-default login' "
                "or set GOOGLE_APPLICATION_CREDENTIALS to a service account key file."
            ) from exc

        self._credentials = credentials

        # --- Project ID resolution: constructor arg > ADC > env var ---
        resolved_project = (
            project_id
            or adc_project
            or os.environ.get("GCP_PROJECT_ID")
        )
        if not resolved_project:
            raise RuntimeError(
                "GCP project ID could not be determined. Provide it via: "
                "1) the project_id constructor argument, "
                "2) ADC-resolved project (gcloud config set project <ID>), or "
                "3) the GCP_PROJECT_ID environment variable."
            )
        self._project_id = resolved_project

        logger.info("GCPProvider initialised for project: %s", self._project_id)

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
        """Return security findings from GCP.

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
