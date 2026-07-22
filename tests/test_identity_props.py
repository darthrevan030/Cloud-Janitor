"""Property-based tests for core/identity.py — resolve_actor() contract.

Uses Hypothesis to validate universal correctness properties across
randomly generated inputs and exception types.

**Validates: Requirements 1.3, 1.4, 1.5**
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    EndpointConnectionError,
    NoCredentialsError,
    NoRegionError,
)
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from cloud_janitor.core.identity import (
    ActorResolution,
    IdentityResolutionError,
    resolve_actor,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Common boto3/botocore/network exceptions that can occur during STS calls.
# Each element is a zero-arg callable that returns an exception instance.
_BOTO_EXCEPTION_FACTORIES = [
    lambda: NoCredentialsError(),
    lambda: NoRegionError(env_var="AWS_DEFAULT_REGION", config_var="region"),
    lambda: EndpointConnectionError(endpoint_url="https://sts.us-east-1.amazonaws.com"),
    lambda: ConnectionClosedError(endpoint_url="https://sts.us-east-1.amazonaws.com"),
    lambda: ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Access denied"}},
        "GetCallerIdentity",
    ),
    lambda: ClientError(
        {"Error": {"Code": "ExpiredTokenException", "Message": "Token expired"}},
        "GetCallerIdentity",
    ),
    lambda: BotoCoreError(),
    lambda: OSError("Network is unreachable"),
    lambda: TimeoutError("Connection timed out"),
    lambda: RuntimeError("Unexpected internal error"),
]

boto_exception_strategy = st.sampled_from(_BOTO_EXCEPTION_FACTORIES).map(
    lambda factory: factory()
)

# Strategy for fallback strings (any non-empty text)
fallback_strategy = st.text(min_size=1, max_size=100)

# Strategy for sandbox-mode environment configurations.
# Sandbox mode is: JANITOR_BACKEND != "aws" OR AWS_ENDPOINT_URL contains localhost/127.0.0.1
sandbox_env_strategy = st.one_of(
    # Case 1: JANITOR_BACKEND is not "aws" (fixture, localstack, empty, random)
    st.fixed_dictionaries({
        "JANITOR_BACKEND": st.sampled_from(["fixture", "localstack", "", "demo", "test"]),
        "AWS_ENDPOINT_URL": st.one_of(
            st.just(""),
            st.just("http://localhost:4566"),
            st.just("http://127.0.0.1:4566"),
            st.just("https://vpce-abc123.sts.us-east-1.vpce.amazonaws.com"),
        ),
    }),
    # Case 2: JANITOR_BACKEND is "aws" but endpoint is localhost/127.0.0.1
    st.fixed_dictionaries({
        "JANITOR_BACKEND": st.just("aws"),
        "AWS_ENDPOINT_URL": st.one_of(
            st.just("http://localhost:4566"),
            st.just("http://127.0.0.1:4566"),
            st.just("https://localhost:4566/custom"),
            st.just("http://127.0.0.1:9999"),
        ),
    }),
)

# Strategy for JANITOR_ACTOR env var (may or may not be set)
janitor_actor_strategy = st.one_of(
    st.just(None),  # not set
    st.text(min_size=1, max_size=80),  # set to some value
)


# ---------------------------------------------------------------------------
# Property 1: resolve_actor() Real-AWS Raising Contract
# ---------------------------------------------------------------------------


class TestRealAWSRaisingContract:
    """Property 1: In real-AWS mode, if the STS call raises any exception,
    resolve_actor() MUST raise IdentityResolutionError.

    This ensures the fail-closed security contract: no unverified identity
    can ever be silently returned when targeting real AWS.

    **Validates: Requirements 1.3, 1.4**
    """

    @settings(max_examples=100, deadline=None)
    @given(
        exc=boto_exception_strategy,
        fallback=fallback_strategy,
    )
    def test_any_sts_exception_raises_identity_resolution_error(self, exc, fallback):
        """For any exception type drawn from common boto3/network exceptions,
        when patching _make_client to return a mock that raises that exception,
        resolve_actor(fallback) always raises IdentityResolutionError in real-AWS mode.
        """
        # Set up real-AWS mode environment:
        # JANITOR_BACKEND=aws, no localhost/127.0.0.1 in AWS_ENDPOINT_URL
        env = {
            "JANITOR_BACKEND": "aws",
            "AWS_ENDPOINT_URL": "",
        }

        mock_sts_client = MagicMock()
        mock_sts_client.get_caller_identity.side_effect = exc

        with (
            patch.dict("os.environ", env, clear=False),
            patch(
                "cloud_janitor.core.identity._make_client",
                return_value=mock_sts_client,
            ),
        ):
            # Remove AWS_ENDPOINT_URL if empty to avoid key presence issues
            import os
            if "AWS_ENDPOINT_URL" in os.environ and os.environ["AWS_ENDPOINT_URL"] == "":
                # patch.dict already set it to "", which is fine for our logic
                pass

            try:
                result = resolve_actor(fallback)
                # If we get here, the property is violated: we should have raised
                assert False, (
                    f"resolve_actor() returned {result!r} instead of raising "
                    f"IdentityResolutionError when STS raised {exc!r}"
                )
            except IdentityResolutionError:
                pass  # Expected — property holds
            except Exception as unexpected:
                # Any other exception type also violates the contract:
                # resolve_actor must wrap ALL exceptions into IdentityResolutionError
                assert False, (
                    f"resolve_actor() raised {type(unexpected).__name__}({unexpected!r}) "
                    f"instead of IdentityResolutionError when STS raised {exc!r}"
                )

    @settings(max_examples=50, deadline=None)
    @given(
        exc=boto_exception_strategy,
        fallback=fallback_strategy,
    )
    def test_real_aws_with_custom_non_localhost_endpoint_still_raises(self, exc, fallback):
        """Even when AWS_ENDPOINT_URL is set to a non-localhost custom endpoint
        (e.g. FIPS, VPC endpoint), as long as JANITOR_BACKEND=aws, an STS failure
        MUST still raise IdentityResolutionError — the endpoint is NOT treated as
        LocalStack just because the env var is present.
        """
        env = {
            "JANITOR_BACKEND": "aws",
            "AWS_ENDPOINT_URL": "https://vpce-abc123.sts.us-east-1.vpce.amazonaws.com",
        }

        mock_sts_client = MagicMock()
        mock_sts_client.get_caller_identity.side_effect = exc

        with (
            patch.dict("os.environ", env, clear=False),
            patch(
                "cloud_janitor.core.identity._make_client",
                return_value=mock_sts_client,
            ),
        ):
            try:
                result = resolve_actor(fallback)
                assert False, (
                    f"resolve_actor() returned {result!r} instead of raising "
                    f"IdentityResolutionError with FIPS/VPC endpoint"
                )
            except IdentityResolutionError:
                pass  # Expected


# ---------------------------------------------------------------------------
# Property 2: Sandbox Identity Fallback Invariant
# ---------------------------------------------------------------------------


class TestSandboxIdentityFallbackInvariant:
    """Property 2: In sandbox mode, resolve_actor(fallback) MUST always return
    an ActorResolution with verified=False and actor equal to either
    JANITOR_ACTOR env var (if set) or the fallback string. STS is NEVER called.

    This ensures sandbox/fixture/LocalStack mode never blocks on STS and
    never returns a verified=True result.

    **Validates: Requirements 1.5**
    """

    @settings(max_examples=200, deadline=None)
    @given(
        fallback=fallback_strategy,
        sandbox_env=sandbox_env_strategy,
        janitor_actor=janitor_actor_strategy,
    )
    def test_sandbox_returns_unverified_fallback_and_never_calls_sts(
        self, fallback, sandbox_env, janitor_actor
    ):
        """For any fallback string and any sandbox-mode environment configuration,
        the result is always unverified and matches the expected actor value.
        STS is NEVER called (assert mock not called).
        """
        env = dict(sandbox_env)
        if janitor_actor is not None:
            env["JANITOR_ACTOR"] = janitor_actor
        else:
            # Ensure JANITOR_ACTOR is not set
            env.pop("JANITOR_ACTOR", None)

        # We need to ensure JANITOR_ACTOR is actually unset when janitor_actor is None.
        # patch.dict with clear=False won't remove keys, so handle carefully.
        keys_to_remove = []
        if janitor_actor is None:
            keys_to_remove.append("JANITOR_ACTOR")

        mock_make_client = MagicMock()

        with (
            patch.dict("os.environ", env, clear=False),
            patch(
                "cloud_janitor.core.identity._make_client",
                mock_make_client,
            ),
        ):
            import os
            # Ensure JANITOR_ACTOR is truly absent when we don't want it
            if janitor_actor is None and "JANITOR_ACTOR" in os.environ:
                del os.environ["JANITOR_ACTOR"]

            result = resolve_actor(fallback)

            # Assert: result is an ActorResolution
            assert isinstance(result, ActorResolution), (
                f"Expected ActorResolution, got {type(result).__name__}"
            )

            # Assert: verified is always False in sandbox mode
            assert result.verified is False, (
                f"Sandbox mode returned verified=True with env={sandbox_env}"
            )

            # Assert: actor matches JANITOR_ACTOR if set, else fallback
            if janitor_actor is not None:
                assert result.actor == janitor_actor, (
                    f"Expected actor={janitor_actor!r} (from JANITOR_ACTOR), "
                    f"got {result.actor!r}"
                )
            else:
                assert result.actor == fallback, (
                    f"Expected actor={fallback!r} (fallback), got {result.actor!r}"
                )

            # Assert: STS client was NEVER constructed
            mock_make_client.assert_not_called()

    @settings(max_examples=50, deadline=None)
    @given(
        fallback=fallback_strategy,
        sandbox_env=sandbox_env_strategy,
    )
    def test_sandbox_never_raises_identity_resolution_error(
        self, fallback, sandbox_env
    ):
        """In sandbox mode, resolve_actor() must NEVER raise
        IdentityResolutionError, regardless of what _make_client would do
        (it shouldn't even be called).
        """
        env = dict(sandbox_env)
        # Remove JANITOR_ACTOR to test fallback path
        env.pop("JANITOR_ACTOR", None)

        # Configure _make_client to raise if called (it shouldn't be)
        mock_make_client = MagicMock(
            side_effect=RuntimeError("STS should not be called in sandbox mode!")
        )

        with (
            patch.dict("os.environ", env, clear=False),
            patch(
                "cloud_janitor.core.identity._make_client",
                mock_make_client,
            ),
        ):
            import os
            if "JANITOR_ACTOR" in os.environ:
                del os.environ["JANITOR_ACTOR"]

            # This must NOT raise
            result = resolve_actor(fallback)

            assert result.verified is False
            assert result.actor == fallback
            mock_make_client.assert_not_called()
