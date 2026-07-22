"""Unit tests for core/timeouts.py — centralized timeout configuration.

Tests cover:
- One test per env var confirming correct default
- Non-numeric, zero, negative, above-ceiling values for each TF/hook var
- JANITOR_LLM_TIMEOUT above 1800 is NOT clamped (exempt from ceiling)

Requirements: 2.1, 2.2, 2.3, 2.4
"""

from __future__ import annotations

import logging

import pytest

from cloud_janitor.core.timeouts import _CEILING_SECONDS, _DEFAULTS, get_timeout


# ---------------------------------------------------------------------------
# Default values — one test per env var
# ---------------------------------------------------------------------------


class TestDefaults:
    """Each env var returns its correct default when not set."""

    def test_tf_init_timeout_default(self, monkeypatch):
        monkeypatch.delenv("JANITOR_TF_INIT_TIMEOUT", raising=False)
        assert get_timeout("JANITOR_TF_INIT_TIMEOUT") == 120

    def test_tf_apply_timeout_default(self, monkeypatch):
        monkeypatch.delenv("JANITOR_TF_APPLY_TIMEOUT", raising=False)
        assert get_timeout("JANITOR_TF_APPLY_TIMEOUT") == 120

    def test_tf_validate_timeout_default(self, monkeypatch):
        monkeypatch.delenv("JANITOR_TF_VALIDATE_TIMEOUT", raising=False)
        assert get_timeout("JANITOR_TF_VALIDATE_TIMEOUT") == 180

    def test_hook_timeout_default(self, monkeypatch):
        monkeypatch.delenv("JANITOR_HOOK_TIMEOUT", raising=False)
        assert get_timeout("JANITOR_HOOK_TIMEOUT") == 30

    def test_llm_timeout_default(self, monkeypatch):
        monkeypatch.delenv("JANITOR_LLM_TIMEOUT", raising=False)
        assert get_timeout("JANITOR_LLM_TIMEOUT") == 30

    def test_defaults_dict_has_all_5_entries(self):
        """Verify the _DEFAULTS table is complete."""
        expected_keys = {
            "JANITOR_TF_INIT_TIMEOUT",
            "JANITOR_TF_APPLY_TIMEOUT",
            "JANITOR_TF_VALIDATE_TIMEOUT",
            "JANITOR_HOOK_TIMEOUT",
            "JANITOR_LLM_TIMEOUT",
        }
        assert set(_DEFAULTS.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Valid values — returns parsed integer
# ---------------------------------------------------------------------------


class TestValidValues:
    """Valid positive integer values within ceiling are returned as-is."""

    @pytest.mark.parametrize("name", list(_DEFAULTS.keys()))
    def test_valid_integer_returned(self, monkeypatch, name):
        monkeypatch.setenv(name, "60")
        assert get_timeout(name) == 60

    @pytest.mark.parametrize("name", list(_DEFAULTS.keys()))
    def test_value_of_1_is_valid(self, monkeypatch, name):
        monkeypatch.setenv(name, "1")
        assert get_timeout(name) == 1

    @pytest.mark.parametrize("name", [
        "JANITOR_TF_INIT_TIMEOUT",
        "JANITOR_TF_APPLY_TIMEOUT",
        "JANITOR_TF_VALIDATE_TIMEOUT",
        "JANITOR_HOOK_TIMEOUT",
    ])
    def test_value_at_ceiling_is_not_clamped(self, monkeypatch, name):
        monkeypatch.setenv(name, str(_CEILING_SECONDS))
        assert get_timeout(name) == _CEILING_SECONDS


# ---------------------------------------------------------------------------
# Non-numeric values → fallback to default
# ---------------------------------------------------------------------------


class TestNonNumericValues:
    """Non-numeric env var values fall back to the default with WARNING."""

    @pytest.mark.parametrize("name", list(_DEFAULTS.keys()))
    @pytest.mark.parametrize("bad_value", ["abc", "twelve", "1.5", "10s", "", " ", "NaN", "inf"])
    def test_non_numeric_falls_back_to_default(self, monkeypatch, caplog, name, bad_value):
        monkeypatch.setenv(name, bad_value)

        with caplog.at_level(logging.WARNING):
            result = get_timeout(name)

        assert result == _DEFAULTS[name]
        assert any("Invalid" in record.message or "falling back" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Zero and negative values → fallback to default
# ---------------------------------------------------------------------------


class TestZeroAndNegativeValues:
    """Zero and negative integers fall back to default with WARNING."""

    @pytest.mark.parametrize("name", list(_DEFAULTS.keys()))
    def test_zero_falls_back_to_default(self, monkeypatch, caplog, name):
        monkeypatch.setenv(name, "0")

        with caplog.at_level(logging.WARNING):
            result = get_timeout(name)

        assert result == _DEFAULTS[name]

    @pytest.mark.parametrize("name", list(_DEFAULTS.keys()))
    @pytest.mark.parametrize("neg_value", ["-1", "-100", "-999999"])
    def test_negative_falls_back_to_default(self, monkeypatch, caplog, name, neg_value):
        monkeypatch.setenv(name, neg_value)

        with caplog.at_level(logging.WARNING):
            result = get_timeout(name)

        assert result == _DEFAULTS[name]


# ---------------------------------------------------------------------------
# Above-ceiling values for Terraform/hook vars → clamped to 1800
# ---------------------------------------------------------------------------


class TestCeilingClamping:
    """Values above 1800 for TF/hook vars are clamped; LLM is exempt."""

    @pytest.mark.parametrize("name", [
        "JANITOR_TF_INIT_TIMEOUT",
        "JANITOR_TF_APPLY_TIMEOUT",
        "JANITOR_TF_VALIDATE_TIMEOUT",
        "JANITOR_HOOK_TIMEOUT",
    ])
    @pytest.mark.parametrize("over_ceiling", ["1801", "3600", "99999", "999999999"])
    def test_above_ceiling_clamped_to_1800(self, monkeypatch, caplog, name, over_ceiling):
        monkeypatch.setenv(name, over_ceiling)

        with caplog.at_level(logging.WARNING):
            result = get_timeout(name)

        assert result == _CEILING_SECONDS
        assert any("ceiling" in record.message or "clamping" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# JANITOR_LLM_TIMEOUT above 1800 is NOT clamped (exempt from ceiling)
# ---------------------------------------------------------------------------


class TestLlmTimeoutExemptFromCeiling:
    """JANITOR_LLM_TIMEOUT is exempt from the 1800s ceiling."""

    @pytest.mark.parametrize("value,expected", [
        ("1801", 1801),
        ("3600", 3600),
        ("7200", 7200),
        ("99999", 99999),
    ])
    def test_llm_timeout_above_ceiling_not_clamped(self, monkeypatch, caplog, value, expected):
        monkeypatch.setenv("JANITOR_LLM_TIMEOUT", value)

        with caplog.at_level(logging.WARNING):
            result = get_timeout("JANITOR_LLM_TIMEOUT")

        assert result == expected
        # No clamping warning should be emitted for LLM timeout
        assert not any("ceiling" in record.message or "clamping" in record.message for record in caplog.records)

    def test_llm_timeout_zero_still_falls_back(self, monkeypatch):
        """LLM timeout is exempt from ceiling but NOT from zero/negative validation."""
        monkeypatch.setenv("JANITOR_LLM_TIMEOUT", "0")
        assert get_timeout("JANITOR_LLM_TIMEOUT") == _DEFAULTS["JANITOR_LLM_TIMEOUT"]

    def test_llm_timeout_negative_still_falls_back(self, monkeypatch):
        monkeypatch.setenv("JANITOR_LLM_TIMEOUT", "-5")
        assert get_timeout("JANITOR_LLM_TIMEOUT") == _DEFAULTS["JANITOR_LLM_TIMEOUT"]

    def test_llm_timeout_non_numeric_still_falls_back(self, monkeypatch):
        monkeypatch.setenv("JANITOR_LLM_TIMEOUT", "fast")
        assert get_timeout("JANITOR_LLM_TIMEOUT") == _DEFAULTS["JANITOR_LLM_TIMEOUT"]


# ---------------------------------------------------------------------------
# Invalid name raises KeyError
# ---------------------------------------------------------------------------


class TestInvalidName:
    """Passing an unknown name raises KeyError (not a silent failure)."""

    def test_unknown_name_raises_key_error(self):
        with pytest.raises(KeyError):
            get_timeout("NONEXISTENT_TIMEOUT_VAR")
