"""Base notifier interface."""

from abc import ABC, abstractmethod


class Notifier(ABC):
    """Abstract base class for notification channels."""

    @abstractmethod
    def notify(self, summary: str, findings: list[dict]) -> bool:
        """Send a notification. Returns True on success, False on failure."""
        ...
