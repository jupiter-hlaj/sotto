"""Twilio adapter — fully implemented (Section 7.3)."""

from datetime import datetime, timezone
from urllib.parse import parse_qsl

from twilio.request_validator import RequestValidator

from sotto.adapters.base import BaseAdapter
from sotto.logger import logger, tracer
from sotto.models import NormalizedCallEvent


class TwilioAdapter(BaseAdapter):

    @tracer.capture_method
    def validate_signature(self, headers: dict, body: str, url: str) -> bool:
        """Validate Twilio X-Twilio-Signature header."""
        credentials = self.secrets.get_provider_credentials(self.tenant_id, "twilio")
        auth_token = credentials.get("token") or credentials.get("auth_token", "")

        signature = headers.get("x-twilio-signature") or headers.get("X-Twilio-Signature", "")
        if not signature:
            logger.warning(
                "Missing X-Twilio-Signature header",
                extra={"tenant_id": self.tenant_id},
            )
            raise ValueError("Missing X-Twilio-Signature header")

        validator = RequestValidator(auth_token)
        # keep_blank_values=True is required — Twilio includes empty params in signature computation
        params = dict(parse_qsl(body, keep_blank_values=True)) if body else {}

        logger.debug(
            "Twilio signature validation attempt",
            extra={
                "tenant_id": self.tenant_id,
                "url": url,
                "param_keys": sorted(params.keys()),
                "signature_length": len(signature),
                "auth_token_length": len(auth_token),
            },
        )

        if not validator.validate(url, params, signature):
            logger.warning(
                "Invalid Twilio signature",
                extra={"tenant_id": self.tenant_id},
            )
            raise ValueError("Invalid Twilio webhook signature")

        return True

    def is_call_ended(self, payload: dict) -> bool:
        """Return True if call is completed AND has a recording."""
        call_completed = (
            payload.get("CallStatus") == "completed"
            or payload.get("DialCallStatus") == "completed"
        )
        return call_completed and payload.get("RecordingSid") is not None

    @tracer.capture_method
    def normalize(self, payload: dict) -> NormalizedCallEvent:
        """Convert Twilio webhook payload to NormalizedCallEvent."""
        account_sid = payload.get("AccountSid", "")
        recording_sid = payload.get("RecordingSid", "")
        recording_url = (
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}"
            f"/Recordings/{recording_sid}.mp3"
        )

        duration_str = payload.get("RecordingDuration") or payload.get("CallDuration") or "0"
        direction = "inbound" if payload.get("Direction", "").startswith("inbound") else "outbound"

        return NormalizedCallEvent(
            tenant_id=self.tenant_id,
            provider="twilio",
            provider_call_id=payload.get("CallSid", ""),
            direction=direction,
            from_number=payload.get("From", ""),
            to_identifier=payload.get("To", ""),
            duration_sec=int(duration_str),
            recording_url=recording_url,
            recording_format="mp3",
            ended_at=datetime.now(timezone.utc),
            raw_payload=payload,
        )
