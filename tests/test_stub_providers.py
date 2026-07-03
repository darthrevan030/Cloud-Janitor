"""Unit tests for GCP and Azure stub providers.

Validates Requirements 11.1, 11.2, 11.3, 11.4:
- Stub providers emit WARNING log on instantiation
- Stub provider methods raise NotImplementedError with provider+method name
- Providers remain instantiable after warning (no exception on init)
"""

import logging

import pytest

from cloud_janitor.mcp_server.backends.gcp_provider import GCPProvider
from cloud_janitor.mcp_server.backends.azure_provider import AzureProvider


class TestGCPProviderInstantiation:
    """Req 11.1: GCPProvider emits WARNING on instantiation."""

    def test_instantiation_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            GCPProvider()

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) >= 1, "Expected at least one WARNING log on GCPProvider init"
        assert "GCP" in warning_records[0].message
        assert "not yet implemented" in warning_records[0].message

    def test_remains_instantiable_after_warning(self) -> None:
        """Req 11.4: Provider remains instantiable — no exception on init."""
        provider = GCPProvider()
        assert provider is not None
        assert isinstance(provider, GCPProvider)


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


class TestGCPProviderMethodsRaiseNotImplementedError:
    """Req 11.3: Each stub method raises NotImplementedError with provider+method name."""

    def setup_method(self) -> None:
        self.provider = GCPProvider()

    def test_get_cost_data_raises(self) -> None:
        with pytest.raises(NotImplementedError, match="GCPProvider"):
            self.provider.get_cost_data()

    def test_get_cost_data_message_contains_method_name(self) -> None:
        with pytest.raises(NotImplementedError, match="get_cost_data"):
            self.provider.get_cost_data()

    def test_get_security_data_raises(self) -> None:
        with pytest.raises(NotImplementedError, match="GCPProvider"):
            self.provider.get_security_data()

    def test_get_security_data_message_contains_method_name(self) -> None:
        with pytest.raises(NotImplementedError, match="get_security_data"):
            self.provider.get_security_data()

    def test_check_dependencies_raises(self) -> None:
        with pytest.raises(NotImplementedError, match="GCPProvider"):
            self.provider.check_dependencies("some-resource-id")

    def test_check_dependencies_message_contains_method_name(self) -> None:
        with pytest.raises(NotImplementedError, match="check_dependencies"):
            self.provider.check_dependencies("some-resource-id")


class TestAzureProviderMethodsRaiseNotImplementedError:
    """Req 11.3: Each stub method raises NotImplementedError with provider+method name."""

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


class TestNotImplementedErrorMessageFormat:
    """Verify the full message format contains both provider class name AND method name."""

    @pytest.mark.parametrize(
        "provider_cls,method_name,args",
        [
            (GCPProvider, "get_cost_data", ()),
            (GCPProvider, "get_security_data", ()),
            (GCPProvider, "check_dependencies", ("res-123",)),
            (AzureProvider, "get_cost_data", ()),
            (AzureProvider, "get_security_data", ()),
            (AzureProvider, "check_dependencies", ("res-123",)),
        ],
    )
    def test_message_contains_provider_and_method(
        self, provider_cls: type, method_name: str, args: tuple
    ) -> None:
        provider = provider_cls()
        with pytest.raises(NotImplementedError) as exc_info:
            getattr(provider, method_name)(*args)

        message = str(exc_info.value)
        assert provider_cls.__name__ in message, (
            f"Expected '{provider_cls.__name__}' in error message, got: {message}"
        )
        assert method_name in message, (
            f"Expected '{method_name}' in error message, got: {message}"
        )
