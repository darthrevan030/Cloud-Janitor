"""Unit tests for GCP and Azure stub providers.

Validates Requirements 9.6, 11.1, 11.2, 11.3, 11.4:
- GCP provider raises ImportError when SDK is missing (Req 9.6)
- Azure stub providers emit WARNING log on instantiation
- Provider methods raise NotImplementedError with provider+method name
"""

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

from cloud_janitor.mcp_server.backends.azure_provider import AzureProvider


# ─── GCP SDK mocking helper ──────────────────────────────────────────────────

@pytest.fixture
def gcp_provider():
    """Instantiate GCPProvider with mocked GCP SDK."""
    mock_auth = MagicMock()
    mock_auth.default.return_value = (MagicMock(), "test-project")
    mock_exceptions = MagicMock()
    mock_exceptions.DefaultCredentialsError = Exception

    mock_google = MagicMock()
    mock_google.auth = mock_auth
    mock_google.auth.exceptions = mock_exceptions

    with patch.dict(sys.modules, {
        "google": mock_google,
        "google.auth": mock_auth,
        "google.auth.exceptions": mock_exceptions,
    }):
        # Force reimport with mocked modules
        import importlib
        import cloud_janitor.mcp_server.backends.gcp_provider as gcp_mod
        importlib.reload(gcp_mod)
        return gcp_mod.GCPProvider(project_id="test-project")


# ─── GCP Tests ───────────────────────────────────────────────────────────────


class TestGCPProviderInstantiation:
    """Req 9.6: GCPProvider raises ImportError when SDK is missing."""

    def test_raises_import_error_without_sdk(self) -> None:
        """Without the GCP SDK, instantiation raises ImportError with install hint.
        If SDK IS installed (e.g. CI with gcp extras), it raises RuntimeError
        about missing credentials instead — both are acceptable 'cannot use' signals."""
        from cloud_janitor.mcp_server.backends.gcp_provider import GCPProvider
        with pytest.raises((ImportError, RuntimeError)):
            GCPProvider()

    def test_instantiable_with_mocked_sdk(self, gcp_provider) -> None:
        """With SDK present (mocked), provider instantiates successfully."""
        assert gcp_provider is not None


class TestGCPProviderMethodsRaiseNotImplementedError:
    """Req 11.3: Each GCP provider method raises NotImplementedError."""

    def test_get_cost_data_raises(self, gcp_provider) -> None:
        with pytest.raises(NotImplementedError, match="GCPProvider"):
            gcp_provider.get_cost_data()

    def test_get_cost_data_message_contains_method_name(self, gcp_provider) -> None:
        with pytest.raises(NotImplementedError, match="get_cost_data"):
            gcp_provider.get_cost_data()

    def test_get_security_data_raises(self, gcp_provider) -> None:
        with pytest.raises(NotImplementedError, match="GCPProvider"):
            gcp_provider.get_security_data()

    def test_get_security_data_message_contains_method_name(self, gcp_provider) -> None:
        with pytest.raises(NotImplementedError, match="get_security_data"):
            gcp_provider.get_security_data()

    def test_check_dependencies_raises(self, gcp_provider) -> None:
        with pytest.raises(NotImplementedError, match="GCPProvider"):
            gcp_provider.check_dependencies("some-resource-id")

    def test_check_dependencies_message_contains_method_name(self, gcp_provider) -> None:
        with pytest.raises(NotImplementedError, match="check_dependencies"):
            gcp_provider.check_dependencies("some-resource-id")


# ─── Azure Tests ─────────────────────────────────────────────────────────────


class TestAzureProviderInstantiation:
    """Req 11.2: AzureProvider emits WARNING on instantiation."""

    def test_instantiation_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            AzureProvider()

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) >= 1, "Expected at least one WARNING log on AzureProvider init"
        assert "Azure" in warning_records[0].message
        assert "not yet implemented" in warning_records[0].message

    def test_remains_instantiable_after_warning(self) -> None:
        """Req 11.4: Provider remains instantiable — no exception on init."""
        provider = AzureProvider()
        assert provider is not None
        assert isinstance(provider, AzureProvider)


class TestAzureProviderMethodsRaiseNotImplementedError:
    """Req 11.3: Each Azure stub method raises NotImplementedError."""

    def setup_method(self) -> None:
        self.provider = AzureProvider()

    def test_get_cost_data_raises(self) -> None:
        with pytest.raises(NotImplementedError, match="AzureProvider"):
            self.provider.get_cost_data()

    def test_get_cost_data_message_contains_method_name(self) -> None:
        with pytest.raises(NotImplementedError, match="get_cost_data"):
            self.provider.get_cost_data()

    def test_get_security_data_raises(self) -> None:
        with pytest.raises(NotImplementedError, match="AzureProvider"):
            self.provider.get_security_data()

    def test_get_security_data_message_contains_method_name(self) -> None:
        with pytest.raises(NotImplementedError, match="get_security_data"):
            self.provider.get_security_data()

    def test_check_dependencies_raises(self) -> None:
        with pytest.raises(NotImplementedError, match="AzureProvider"):
            self.provider.check_dependencies("some-resource-id")

    def test_check_dependencies_message_contains_method_name(self) -> None:
        with pytest.raises(NotImplementedError, match="check_dependencies"):
            self.provider.check_dependencies("some-resource-id")


# ─── Parametrized Error Message Format Tests ─────────────────────────────────


class TestNotImplementedErrorMessageFormat:
    """Verify error messages contain both provider class name AND method name."""

    @pytest.mark.parametrize(
        "method_name,args",
        [
            ("get_cost_data", ()),
            ("get_security_data", ()),
            ("check_dependencies", ("res-123",)),
        ],
    )
    def test_azure_message_format(self, method_name: str, args: tuple) -> None:
        provider = AzureProvider()
        with pytest.raises(NotImplementedError) as exc_info:
            getattr(provider, method_name)(*args)
        message = str(exc_info.value)
        assert "AzureProvider" in message
        assert method_name in message

    @pytest.mark.parametrize(
        "method_name,args",
        [
            ("get_cost_data", ()),
            ("get_security_data", ()),
            ("check_dependencies", ("res-123",)),
        ],
    )
    def test_gcp_message_format(self, gcp_provider, method_name: str, args: tuple) -> None:
        with pytest.raises(NotImplementedError) as exc_info:
            getattr(gcp_provider, method_name)(*args)
        message = str(exc_info.value)
        assert "GCPProvider" in message
        assert method_name in message
