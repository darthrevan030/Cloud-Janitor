"""IAM identity resolution for approval/rollback attribution.

Resolves the current caller's identity via AWS STS GetCallerIdentity when
running in real-AWS mode (JANITOR_BACKEND=aws and not pointed at LocalStack).
In sandbox/fixture/LocalStack mode, returns a fallback actor immediately
without making any network call.

Security contract: the ``fallback`` parameter (and the JANITOR_ACTOR env var)
are used verbatim in sandbox mode only. In real-AWS mode, only an STS-verified
ARN is ever returned — any failure raises IdentityResolutionError, forcing the
caller to hard-stop rather than silently proceeding with an unverified identity.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from botocore.config import Config

from cloud_janitor.mcp_server.backends.aws_provider import _make_client

# Matches aws_provider.py's own non-critical-path precedent (_dep_client,
# aws_provider.py:556) — a short, hard timeout so a stalled/unreachable STS
# endpoint cannot hang an approve()/rollback() call under boto3's much
# longer default timeouts.
_STS_TIMEOUT_CONFIG = Config(
    connect_timeout=5, read_timeout=10, retries={"max_attempts": 1}
)


@dataclass
class ActorResolution:
    """Result of identity resolution.

    Attributes:
        actor: The resolved identity string (IAM ARN in real-AWS mode,
            or a fallback string in sandbox mode).
        verified: True if the actor was confirmed via STS GetCallerIdentity
            against real AWS; False for any sandbox/fallback path.
    """

    actor: str
    verified: bool


class IdentityResolutionError(Exception):
    """Raised when caller identity cannot be confirmed against real AWS.

    Callers MUST treat this as a hard stop — no state-changing action
    (approve, rollback, apply) may proceed when this is raised.
    """


def resolve_actor(fallback: str) -> ActorResolution:
    """Resolve the current caller's identity for audit attribution.

    Args:
        fallback: Default actor string used in sandbox mode when
            JANITOR_ACTOR is not set.

    Returns:
        ActorResolution with the resolved actor and verification status.

    Raises:
        IdentityResolutionError: In real-AWS mode, if STS cannot confirm
            the caller's identity (missing credentials, expired session,
            network failure, AccessDenied) or if STS returns a LocalStack
            default account (000000000000).
    """
    endpoint = os.environ.get("AWS_ENDPOINT_URL", "")
    # Matches aws_provider.py's own LocalStack detection (aws_provider.py:104-105):
    # a substring check on the endpoint host, not "the var is merely set".
    # AWS_ENDPOINT_URL is also the generic boto3 mechanism for FIPS endpoints,
    # VPC endpoints, and PrivateLink — treating any non-empty value as LocalStack
    # would silently downgrade a real production deployment on a custom real-AWS
    # endpoint to lenient Sandbox_Mode.
    is_localstack = "localhost" in endpoint or "127.0.0.1" in endpoint
    is_real_aws = (
        os.environ.get("JANITOR_BACKEND", "fixture") == "aws" and not is_localstack
    )

    if not is_real_aws:
        # Sandbox/fixture/LocalStack path: skip the STS call outright rather
        # than relying on the exception path to eventually time out. In
        # fixture/demo mode there is no real AWS path at all, so every
        # approve/rollback would otherwise stall for boto3's default (much
        # longer) timeout before falling back here.
        return ActorResolution(
            actor=os.environ.get("JANITOR_ACTOR", fallback), verified=False
        )

    try:
        sts = _make_client("sts", region=None, config=_STS_TIMEOUT_CONFIG)
        identity = sts.get_caller_identity()
        arn, account = identity["Arn"], identity["Account"]
        if account == "000000000000":
            # Heuristic, not a complete detector: LocalStack Pro supports
            # configuring custom, non-default account IDs, which this check
            # would not catch. Accepted as defense-in-depth for the common/
            # default LocalStack configuration.
            raise IdentityResolutionError(
                "STS returned a LocalStack default identity while "
                "JANITOR_BACKEND=aws targets real AWS — refusing to proceed."
            )
        return ActorResolution(actor=arn, verified=True)
    except IdentityResolutionError:
        raise
    except Exception as exc:
        raise IdentityResolutionError(
            f"Could not confirm caller identity via STS against real AWS: "
            f"{exc}. Refusing to approve/rollback."
        ) from exc
