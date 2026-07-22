"""Backend reachability preflight for Cloud Janitor.

Used by Orchestrator.execute_audit() to fail fast on an unreachable
backend, and by the Streamlit UI to render a readiness indicator —
both call the exact same function so they can never disagree.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal

_PROBE_TIMEOUT_SECONDS = 5

# Distinguishes *why* a probe failed, not just *that* it failed — an
# operator (or the UI) needs a different remediation for "start
# LocalStack" than for "fix your AWS credentials." Collapsing both into
# one flat io_failure category was flagged in design review as making
# the preflight less actionable than a plain exception would have been.
HealthMode = Literal["healthy", "unreachable", "invalid_credentials"]


@dataclass
class HealthStatus:
    reachable: bool
    environment: str  # "sandbox_localstack" | "real_aws" — which probe ran
    mode: HealthMode  # "healthy" | "unreachable" | "invalid_credentials"
    detail: str
    endpoint: str


def _is_real_aws_mode() -> bool:
    """Determine if we are targeting real AWS (not LocalStack/fixture).

    Uses the SAME substring-check logic as core/identity.py — checking
    for "localhost" or "127.0.0.1" in the endpoint, NOT merely testing
    whether AWS_ENDPOINT_URL is set. The latter would misclassify FIPS,
    VPC, and PrivateLink endpoints as LocalStack.
    """
    endpoint = os.environ.get("AWS_ENDPOINT_URL", "")
    is_localstack = "localhost" in endpoint or "127.0.0.1" in endpoint
    return os.environ.get("JANITOR_BACKEND", "fixture") == "aws" and not is_localstack


def check_backend_health() -> HealthStatus:
    """Probe the active backend. Never raises — failures are encoded in the result."""
    if _is_real_aws_mode():
        return _check_sts()
    return _check_localstack()


def _check_localstack() -> HealthStatus:
    """Positive-match check, mirroring Makefile:9/:36 exactly.

    The Makefile precedent greps for '"ec2": "available"' — a positive
    match — not for the absence of "unavailable". LocalStack's health
    payload has intermediate states (e.g. "starting") that a negative-match
    check would incorrectly treat as reachable, potentially green-lighting
    a scan against a still-booting LocalStack.
    """
    endpoint = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
    url = f"{endpoint.rstrip('/')}/_localstack/health"
    try:
        with urllib.request.urlopen(url, timeout=_PROBE_TIMEOUT_SECONDS) as resp:
            if resp.status != 200:
                return HealthStatus(False, "sandbox_localstack", "unreachable", f"HTTP {resp.status}", url)
            body = json.loads(resp.read().decode("utf-8"))
            if body.get("services", {}).get("ec2") != "available":
                return HealthStatus(
                    False, "sandbox_localstack", "unreachable",
                    f"ec2 service not available (status: {body.get('services', {}).get('ec2')!r})", url,
                )
            return HealthStatus(True, "sandbox_localstack", "healthy", "ok", url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return HealthStatus(False, "sandbox_localstack", "unreachable", str(exc), url)


def _check_sts() -> HealthStatus:
    """Probe real AWS via STS GetCallerIdentity with an explicit, short timeout.

    Constructs a botocore Config with connect_timeout=5, read_timeout=5
    and passes it to _make_client() so the probe fails fast rather than
    hanging for boto3's much larger, retry-multiplied default timeout.
    """
    from botocore.config import Config
    from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

    from cloud_janitor.mcp_server.backends.aws_provider import _make_client

    timeout_config = Config(connect_timeout=_PROBE_TIMEOUT_SECONDS, read_timeout=_PROBE_TIMEOUT_SECONDS)
    try:
        client = _make_client("sts", region=None, config=timeout_config)
        client.get_caller_identity()
        return HealthStatus(True, "real_aws", "healthy", "ok", "sts:GetCallerIdentity")
    except (ClientError, NoCredentialsError) as exc:
        # Reached AWS (or a credential resolver ran) but was rejected —
        # this is a credentials/permissions problem, not a network one.
        return HealthStatus(False, "real_aws", "invalid_credentials", str(exc), "sts:GetCallerIdentity")
    except (EndpointConnectionError, OSError, TimeoutError) as exc:
        # Never reached AWS at all — network/DNS/timeout.
        return HealthStatus(False, "real_aws", "unreachable", str(exc), "sts:GetCallerIdentity")
    except Exception as exc:  # noqa: BLE001 — unexpected shape, still "not reachable"
        return HealthStatus(False, "real_aws", "unreachable", str(exc), "sts:GetCallerIdentity")
