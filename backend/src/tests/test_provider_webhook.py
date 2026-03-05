"""Unit tests for ProviderWebhookHandler (Step 3)."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

# Set env vars before importing handler (Powertools reads them at import time)
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "sotto")
os.environ.setdefault("POWERTOOLS_METRICS_NAMESPACE", "Sotto")
os.environ.setdefault("SQS_CALL_EVENTS_URL", "https://sqs.us-east-1.amazonaws.com/123456789/sotto-call-events-dev")

from handlers.webhooks.provider_webhook import handler  # noqa: E402


# ── Fixtures ─────────────────────────────────────────────────────

class MockContext:
    function_name = "sotto-provider-webhook-dev"
    function_version = "$LATEST"
    aws_request_id = "test-request-id"
    memory_limit_in_mb = 256

    def get_remaining_time_in_millis(self):
        return 10000


TWILIO_BODY = (
    "CallSid=CA123"
    "&To=%2B15551234567"
    "&From=%2B19876543210"
    "&CallStatus=completed"
    "&RecordingSid=RE123"
    "&RecordingDuration=120"
    "&AccountSid=AC123"
    "&Direction=inbound"
)


def _make_event(provider="twilio", body=TWILIO_BODY, headers=None, route_key=None):
    """Build an HttpApi v2 event for the webhook handler."""
    if headers is None:
        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "x-twilio-signature": "valid_signature",
        }
    return {
        "version": "2.0",
        "routeKey": route_key or f"POST /webhooks/{provider}",
        "rawPath": f"/webhooks/{provider}",
        "rawQueryString": "",
        "headers": headers,
        "requestContext": {
            "requestId": "test-request-id",
            "domainName": "api.example.com",
            "http": {
                "method": "POST",
                "path": f"/webhooks/{provider}",
                "sourceIp": "1.2.3.4",
            },
        },
        "pathParameters": {"provider": provider},
        "body": body,
        "isBase64Encoded": False,
    }


MOCK_MAPPING = {"tenant_id": "tenant-001", "identifier": "+15551234567", "agent_id": "agent-001"}
MOCK_TENANT = {"tenant_id": "tenant-001", "status": "active", "provider_type": "twilio"}


# ── Tests ────────────────────────────────────────────────────────

@patch("handlers.webhooks.provider_webhook._get_sqs")
@patch("handlers.webhooks.provider_webhook.db")
@patch("twilio.request_validator.RequestValidator.validate", return_value=True)
@patch("sotto.secrets.get_provider_credentials", return_value={"token": "test-auth-token"})
def test_valid_twilio_webhook_pushes_to_sqs(
    mock_creds, mock_validate, mock_db, mock_get_sqs
):
    """Valid signature + completed call → SQS SendMessage with NormalizedCallEvent."""
    mock_db.scan_number_mapping_by_identifier.return_value = MOCK_MAPPING
    mock_db.get_tenant.return_value = MOCK_TENANT

    mock_sqs = MagicMock()
    mock_get_sqs.return_value = mock_sqs

    result = handler(_make_event(), MockContext())

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["status"] == "accepted"

    # SQS must have been called exactly once
    mock_sqs.send_message.assert_called_once()
    call_kwargs = mock_sqs.send_message.call_args
    sent_body = json.loads(call_kwargs.kwargs.get("MessageBody") or call_kwargs[1].get("MessageBody"))

    assert sent_body["tenant_id"] == "tenant-001"
    assert sent_body["provider"] == "twilio"
    assert sent_body["provider_call_id"] == "CA123"
    assert sent_body["direction"] == "inbound"
    assert sent_body["from_number"] == "+19876543210"
    assert sent_body["to_identifier"] == "+15551234567"
    assert sent_body["duration_sec"] == 120
    assert sent_body["recording_format"] == "mp3"


@patch("handlers.webhooks.provider_webhook._get_sqs")
@patch("handlers.webhooks.provider_webhook.db")
@patch("twilio.request_validator.RequestValidator.validate", return_value=False)
@patch("sotto.secrets.get_provider_credentials", return_value={"token": "test-auth-token"})
def test_invalid_signature_returns_403(
    mock_creds, mock_validate, mock_db, mock_get_sqs
):
    """Invalid Twilio signature → 403, SQS never called."""
    mock_db.scan_number_mapping_by_identifier.return_value = MOCK_MAPPING
    mock_db.get_tenant.return_value = MOCK_TENANT

    mock_sqs = MagicMock()
    mock_get_sqs.return_value = mock_sqs

    result = handler(_make_event(), MockContext())

    assert result["statusCode"] == 403
    body = json.loads(result["body"])
    assert "signature" in body["error"].lower() or "Invalid" in body["error"]

    mock_sqs.send_message.assert_not_called()


def test_unknown_provider_returns_400():
    """Unknown provider in path → 400, no DB or SQS interaction."""
    result = handler(
        _make_event(provider="unknown_provider"),
        MockContext(),
    )

    assert result["statusCode"] == 400
    body = json.loads(result["body"])
    assert "Unsupported provider" in body["error"]


def test_health_check_returns_ok():
    """GET /health → 200 with status ok."""
    event = {
        "version": "2.0",
        "routeKey": "GET /health",
        "rawPath": "/health",
        "rawQueryString": "",
        "headers": {},
        "requestContext": {
            "requestId": "health-req-id",
            "domainName": "api.example.com",
            "http": {"method": "GET", "path": "/health", "sourceIp": "1.2.3.4"},
        },
        "isBase64Encoded": False,
    }

    result = handler(event, MockContext())

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body == {"status": "ok", "service": "sotto"}


@patch("handlers.webhooks.provider_webhook._get_sqs")
@patch("handlers.webhooks.provider_webhook.db")
@patch("twilio.request_validator.RequestValidator.validate", return_value=True)
@patch("sotto.secrets.get_provider_credentials", return_value={"token": "test-auth-token"})
def test_call_not_ended_returns_acknowledged(
    mock_creds, mock_validate, mock_db, mock_get_sqs
):
    """Webhook for an in-progress call (no RecordingSid) → acknowledged, SQS not called."""
    mock_db.scan_number_mapping_by_identifier.return_value = MOCK_MAPPING
    mock_db.get_tenant.return_value = MOCK_TENANT

    mock_sqs = MagicMock()
    mock_get_sqs.return_value = mock_sqs

    # Body without RecordingSid — call still in progress
    body_no_recording = (
        "CallSid=CA123"
        "&To=%2B15551234567"
        "&From=%2B19876543210"
        "&CallStatus=ringing"
        "&AccountSid=AC123"
        "&Direction=inbound"
    )

    result = handler(_make_event(body=body_no_recording), MockContext())

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["status"] == "acknowledged"

    mock_sqs.send_message.assert_not_called()
