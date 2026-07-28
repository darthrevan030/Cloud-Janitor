"""Slack webhook notifier — single attempt, 10s timeout, no retry."""

import json
import urllib.error
import urllib.request

from cloud_janitor.core.notifiers.base import Notifier


class SlackWebhookNotifier(Notifier):
    """Posts a summary of escalated findings to a Slack incoming webhook."""

    def __init__(self, webhook_url: str) -> None:
        self._url = webhook_url

    def notify(self, summary: str, findings: list[dict]) -> bool:
        """Single attempt, 10s timeout, no internal retry loop.

        A failed attempt is reported back to the Scheduler as False —
        retry/backoff/muting policy lives in the Scheduler's circuit
        breaker, not duplicated here.
        """
        lines = "\n".join(
            f"- [{f.get('severity')}] {f.get('resource_id')}: {f.get('description', '')}"
            for f in findings[:20]
        )
        payload = json.dumps({"text": f"{summary}\n{lines}"}).encode("utf-8")
        req = urllib.request.Request(
            self._url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return bool(200 <= resp.status < 300)
        except (urllib.error.URLError, OSError):
            return False
