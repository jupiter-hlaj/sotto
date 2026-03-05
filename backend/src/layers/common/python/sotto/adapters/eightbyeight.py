"""8x8 adapter — stub."""

from sotto.adapters.base import BaseAdapter
from sotto.models import NormalizedCallEvent


class EightByEightAdapter(BaseAdapter):

    def validate_signature(self, headers: dict, body: str, url: str) -> bool:
        raise NotImplementedError("8x8 adapter not yet implemented")

    def normalize(self, payload: dict) -> NormalizedCallEvent:
        raise NotImplementedError("8x8 adapter not yet implemented")

    def is_call_ended(self, payload: dict) -> bool:
        raise NotImplementedError("8x8 adapter not yet implemented")
