"""Unit tests for core/notifiers — SlackWebhookNotifier and build_notifiers_from_env."""

import urllib.error
from unittest.mock import patch, MagicMock

from cloud_janitor.core.notifiers import build_notifiers_from_env, SlackWebhookNotifier
from cloud_janitor.core.notifiers.base import Notifier


class TestSlackWebhookNotifier:
    """Tests for SlackWebhookNotifier.notify()."""

    def test_success_returns_true(self):
        """A 200 response from urlopen returns True."""
        notifier = SlackWebhookNotifier("https://hooks.slack.com/test")
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            result = notifier.notify("test summary", [{"severity": "HIGH", "resource_id": "vol-1", "description": "test"}])
        assert result is True
        mock_urlopen.assert_called_once()

    def test_non_2xx_returns_false(self):
        """A non-2xx response returns False without raising."""
        notifier = SlackWebhookNotifier("https://hooks.slack.com/test")
        mock_resp = MagicMock()
        mock_resp.status = 403
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = notifier.notify("summary", [{"severity": "CRITICAL", "resource_id": "sg-1"}])
        assert result is False

    def test_url_error_returns_false(self):
        """A URLError (e.g. DNS failure) returns False without raising."""
        notifier = SlackWebhookNotifier("https://hooks.slack.com/test")

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("DNS")):
            result = notifier.notify("summary", [{"severity": "HIGH", "resource_id": "vol-1"}])
        assert result is False

    def test_timeout_returns_false(self):
        """An OSError/timeout returns False without raising."""
        notifier = SlackWebhookNotifier("https://hooks.slack.com/test")

        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            result = notifier.notify("summary", [])
        assert result is False

    def test_is_instance_of_notifier(self):
        """SlackWebhookNotifier is a Notifier."""
        notifier = SlackWebhookNotifier("https://hooks.slack.com/test")
        assert isinstance(notifier, Notifier)

    def test_single_attempt_only(self):
        """urlopen is called exactly once — no internal retry loop."""
        notifier = SlackWebhookNotifier("https://hooks.slack.com/test")

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("fail")) as mock_urlopen:
            notifier.notify("summary", [{"severity": "HIGH", "resource_id": "x"}])
        assert mock_urlopen.call_count == 1


class TestBuildNotifiersFromEnv:
    """Tests for build_notifiers_from_env()."""

    def test_returns_empty_when_env_unset(self):
        """No JANITOR_SLACK_WEBHOOK_URL → empty list."""
        with patch.dict("os.environ", {}, clear=False):
            # Ensure the key is not set
            import os
            os.environ.pop("JANITOR_SLACK_WEBHOOK_URL", None)
            result = build_notifiers_from_env()
        assert result == []

    def test_returns_slack_notifier_when_env_set(self):
        """JANITOR_SLACK_WEBHOOK_URL set → one SlackWebhookNotifier."""
        with patch.dict("os.environ", {"JANITOR_SLACK_WEBHOOK_URL": "https://hooks.slack.com/x"}):
            result = build_notifiers_from_env()
        assert len(result) == 1
        assert isinstance(result[0], SlackWebhookNotifier)

    def test_returns_empty_when_env_is_empty_string(self):
        """An empty string for JANITOR_SLACK_WEBHOOK_URL → empty list."""
        with patch.dict("os.environ", {"JANITOR_SLACK_WEBHOOK_URL": ""}):
            result = build_notifiers_from_env()
        assert result == []
