"""Tests for NotesHandler ownership checks.

Verifies that notes can only be updated when both tenant_id and agent_id
in the JWT claims match the call record.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

# Set env vars before importing handler (Powertools reads them at import time)
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "sotto")
os.environ.setdefault("POWERTOOLS_METRICS_NAMESPACE", "Sotto")

from handlers.agents.notes import handler  # noqa: E402


# ── Fixtures ─────────────────────────────────────────────────────


class MockContext:
    function_name = "sotto-notes-dev"
    function_version = "$LATEST"
    aws_request_id = "test-123"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789:function:sotto-notes-dev"

    def get_remaining_time_in_millis(self):
        return 29000


CALL_RECORD = {
    "tenant_id": "tenant-1",
    "call_id": "call-1",
    "agent_id": "agent-1",
    "status": "complete",
    "from_number": "+15551234567",
    "duration_sec": 120,
    "created_at": "2026-03-01T10:00:00Z",
}


def _make_event(tenant_id: str, agent_id: str, call_id: str, notes: str = "test notes") -> dict:
    return {
        "requestContext": {
            "requestId": "test-req-id",
            "authorizer": {
                "jwt": {
                    "claims": {
                        "custom:tenant_id": tenant_id,
                        "custom:agent_id": agent_id,
                    }
                }
            },
        },
        "pathParameters": {"call_id": call_id},
        "body": json.dumps({"notes": notes}),
    }


# ── Tests ────────────────────────────────────────────────────────


@patch("handlers.agents.notes.db")
def test_notes_update_succeeds_when_ownership_matches(mock_db):
    """Notes update succeeds when agent_id and tenant_id match JWT claims."""
    mock_db.get_call.return_value = CALL_RECORD.copy()
    mock_db.update_call.return_value = {**CALL_RECORD, "notes": "new notes", "notes_updated_at": "2026-03-05T12:00:00Z"}

    event = _make_event("tenant-1", "agent-1", "call-1", "new notes")
    result = handler(event, MockContext())

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["notes"] == "new notes"
    assert body["notes_updated_at"] is not None
    mock_db.update_call.assert_called_once()


@patch("handlers.agents.notes.db")
def test_returns_403_when_agent_id_mismatch(mock_db):
    """Returns 403 when call.agent_id != JWT agent_id."""
    mock_db.get_call.return_value = CALL_RECORD.copy()

    event = _make_event("tenant-1", "agent-999", "call-1")
    result = handler(event, MockContext())

    assert result["statusCode"] == 403
    body = json.loads(result["body"])
    assert "denied" in body["error"].lower()
    mock_db.update_call.assert_not_called()


@patch("handlers.agents.notes.db")
def test_returns_403_when_tenant_id_mismatch(mock_db):
    """Returns 403 when call.tenant_id != JWT tenant_id."""
    different_tenant_call = {**CALL_RECORD, "tenant_id": "tenant-other"}
    mock_db.get_call.return_value = different_tenant_call

    event = _make_event("tenant-1", "agent-1", "call-1")
    result = handler(event, MockContext())

    assert result["statusCode"] == 403
    body = json.loads(result["body"])
    assert "denied" in body["error"].lower()
    mock_db.update_call.assert_not_called()


@patch("handlers.agents.notes.db")
def test_returns_404_when_call_not_found(mock_db):
    """Returns 404 when the call doesn't exist."""
    mock_db.get_call.return_value = None

    event = _make_event("tenant-1", "agent-1", "call-nonexistent")
    result = handler(event, MockContext())

    assert result["statusCode"] == 404
    mock_db.update_call.assert_not_called()
