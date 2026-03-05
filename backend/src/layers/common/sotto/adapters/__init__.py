"""Provider adapters — normalize webhooks from different telephony providers."""

from sotto.adapters.base import BaseAdapter
from sotto.adapters.twilio import TwilioAdapter
from sotto.adapters.ringcentral import RingCentralAdapter
from sotto.adapters.zoom import ZoomAdapter
from sotto.adapters.teams import TeamsAdapter
from sotto.adapters.eightbyeight import EightByEightAdapter

ADAPTER_MAP: dict[str, type[BaseAdapter]] = {
    "twilio": TwilioAdapter,
    "ringcentral": RingCentralAdapter,
    "zoom": ZoomAdapter,
    "teams": TeamsAdapter,
    "8x8": EightByEightAdapter,
}

__all__ = [
    "BaseAdapter",
    "TwilioAdapter",
    "RingCentralAdapter",
    "ZoomAdapter",
    "TeamsAdapter",
    "EightByEightAdapter",
    "ADAPTER_MAP",
]
