"""RingCentral adapter — stub."""

from sotto.adapters.base import BaseAdapter
from sotto.models import NormalizedCallEvent


class RingCentralAdapter(BaseAdapter):

    def validate_signature(self, headers: dict, body: str, url: str) -> bool:
        raise NotImplementedError("RingCentral adapter not yet implemented")

    def normalize(self, payload: dict) -> NormalizedCallEvent:
        raise NotImplementedError("RingCentral adapter not yet implemented")

    def is_call_ended(self, payload: dict) -> bool:
        raise NotImplementedError("RingCentral adapter not yet implemented")
