"""Notifier subsystem — pluggable notification channels."""

import os

from cloud_janitor.core.notifiers.base import Notifier
from cloud_janitor.core.notifiers.slack import SlackWebhookNotifier


def build_notifiers_from_env() -> list[Notifier]:
    """Build configured notifiers from environment variables.

    Returns an empty list when no notification channels are configured.
    """
    notifiers: list[Notifier] = []
    slack_url = os.environ.get("JANITOR_SLACK_WEBHOOK_URL")
    if slack_url:
        notifiers.append(SlackWebhookNotifier(slack_url))
    return notifiers


__all__ = ["Notifier", "SlackWebhookNotifier", "build_notifiers_from_env"]
