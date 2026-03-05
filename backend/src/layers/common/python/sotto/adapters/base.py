"""Base adapter ABC — all provider adapters must implement this (Section 7.1)."""

from abc import ABC, abstractmethod

from sotto.models import NormalizedCallEvent


class BaseAdapter(ABC):
    def __init__(self, tenant_id: str, secrets_client):
        self.tenant_id = tenant_id
        self.secrets = secrets_client

    @abstractmethod
    def validate_signature(self, headers: dict, body: str, url: str) -> bool:
        """Validate the webhook signature. Raise ValueError if invalid."""
        pass

    @abstractmethod
    def normalize(self, payload: dict) -> NormalizedCallEvent:
        """Convert provider-specific payload to NormalizedCallEvent."""
        pass

    @abstractmethod
    def is_call_ended(self, payload: dict) -> bool:
        """Return True only if this webhook represents a completed call with recording."""
        pass
