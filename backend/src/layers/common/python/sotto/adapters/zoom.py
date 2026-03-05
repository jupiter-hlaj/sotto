"""Zoom adapter — stub."""

from sotto.adapters.base import BaseAdapter
from sotto.models import NormalizedCallEvent


class ZoomAdapter(BaseAdapter):

    def validate_signature(self, headers: dict, body: str, url: str) -> bool:
        raise NotImplementedError("Zoom adapter not yet implemented")

    def normalize(self, payload: dict) -> NormalizedCallEvent:
        raise NotImplementedError("Zoom adapter not yet implemented")

    def is_call_ended(self, payload: dict) -> bool:
        raise NotImplementedError("Zoom adapter not yet implemented")
