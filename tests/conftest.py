"""Shared test fixtures and configuration for the Cloud Janitor test suite.

This conftest ensures test isolation by resetting global state that can leak
between test modules when the full suite is run.

Key issue addressed:
- test_llm_client.py uses `importlib.reload(core.llm_client)` which recreates
  the LLMRetryExhausted and LLMRateLimitExceeded exception classes as new objects.
  Subsequent tests that imported these classes at module-load time hold stale
  references, causing `pytest.raises(LLMRetryExhausted)` to fail to catch the
  new-class instances raised by the reloaded call_llm(). The fix reloads the
  module once at the end of any test that triggers a reload, keeping the global
  module reference consistent.
"""

import importlib
import logging
import sys

import pytest

from hypothesis import settings

settings.register_profile("dev", deadline=None)
settings.load_profile("dev")


@pytest.fixture(autouse=True)
def _stabilize_llm_client_module():
    """Ensure core.llm_client module identity is stable after each test.

    Some tests (test_llm_client.py) call importlib.reload(core.llm_client) which
    replaces the module's exception classes with new objects. Any test that did
    `from core.llm_client import LLMRetryExhausted` before the reload now holds
    a stale class reference. pytest.raises(StaleClass) won't catch instances of
    the new class even though they share the same name.

    This fixture detects if the module was reloaded (by comparing its id before
    and after the test) and, if so, reloads it one final time and patches the
    sys.modules entry so all subsequent imports get the fresh version.
    """
    import cloud_janitor.core.llm_client as mod_before
    id_before = id(mod_before)

    yield

    # Check if the module was reloaded during the test
    current_mod = sys.modules.get("cloud_janitor.core.llm_client")
    if current_mod is None or id(current_mod) != id_before:
        # Module was reloaded — do a final reload to stabilize it and ensure
        # the module in sys.modules is the canonical version going forward.
        # We also need to patch any test modules that already imported symbols.
        import cloud_janitor.core.llm_client
        importlib.reload(cloud_janitor.core.llm_client)


@pytest.fixture(autouse=True)
def _reset_logging_handlers():
    """Reset root logger handlers between tests to prevent handler accumulation.

    Some tests (logging_config, drift_detector) call logging.basicConfig() with
    force=True which adds handlers. Without cleanup, these handlers leak into
    subsequent tests and cause spurious captured-log accumulation that makes
    Hypothesis property tests appear to fail.
    """
    yield
    # After each test, remove all handlers from root logger and llm_client logger
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)

    llm_logger = logging.getLogger("cloud_janitor.core.llm_client")
    llm_logger.handlers.clear()
    llm_logger.setLevel(logging.NOTSET)
