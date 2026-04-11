"""Unit tests for Teams pipeline plumbing (M3 / T-7).

Covers the three handlers changed in T-7b/T-7c:

  - `handlers.calls.recording_processor` — Teams skip-download branch
    (spec §9 Change 1) and UPDATE-not-CREATE DynamoDB branch
    (spec §9 Change 2). Non-Teams path must be unchanged.
  - `handlers.calls.transcription_init` — `get_transcription_settings`
    provider cascade (spec §5.6.8).
  - `handlers.calls.transcription_result` — `resolve_speaker_label`
    helper (spec §5.6.8).

Conventions match `test_teams_onboarding.py`:
  - `unittest.mock.patch` (no moto)
  - env vars set at module scope before importing the handler
  - a lightweight `MockContext` stand-in for the Lambda runtime context
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

# Env vars must be set before importing the handlers — Powertools reads
# POWERTOOLS_* at import time.
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "sotto")
os.environ.setdefault("POWERTOOLS_METRICS_NAMESPACE", "Sotto")
os.environ.setdefault("AWS_ACCOUNT_ID", "123456789012")
os.environ.setdefault("TRANSCRIPTION_INIT_FUNCTION", "sotto-transcription-init-dev")
os.environ.setdefault("WEBSOCKET_API_ENDPOINT", "")  # disables ws push branch

from handlers.calls import recording_processor  # noqa: E402
from handlers.calls.transcription_init import get_transcription_settings  # noqa: E402
from handlers.calls.transcription_result import resolve_speaker_label  # noqa: E402


class MockContext:
    function_name = "sotto-recording-processor-dev"
    function_version = "$LATEST"
    invoked_function_arn = (
        "arn:aws:lambda:us-east-1:123456789012:function:sotto-recording-processor-dev"
    )
    aws_request_id = "test-request-id"
    memory_limit_in_mb = 256

    def get_remaining_time_in_millis(self):
        return 25000


# ── get_transcription_settings (spec §5.6.8) ────────────────────────


def test_get_transcription_settings_teams_uses_channel_identification():
    settings = get_transcription_settings("teams")
    assert settings == {"ChannelIdentification": True}
    # ChannelIdentification and ShowSpeakerLabels are mutually exclusive
    # in AWS Transcribe — explicitly assert the diarization key is absent.
    assert "ShowSpeakerLabels" not in settings


def test_get_transcription_settings_twilio_uses_speaker_labels():
    settings = get_transcription_settings("twilio")
    assert settings == {"ShowSpeakerLabels": True, "MaxSpeakerLabels": 2}


def test_get_transcription_settings_unknown_provider_falls_back_to_diarization():
    # Safety: any unrecognised or empty provider must fall back to the
    # mono/diarization path, never leak ChannelIdentification into a
    # non-stereo pipeline.
    for provider in ("", "ringcentral", "zoom", "8x8", "unknown"):
        settings = get_transcription_settings(provider)
        assert settings == {"ShowSpeakerLabels": True, "MaxSpeakerLabels": 2}
        assert "ChannelIdentification" not in settings


# ── resolve_speaker_label (spec §5.6.8) ─────────────────────────────


def test_resolve_speaker_label_teams_ch0_is_agent():
    # Bot always routes agent audio to channel 0 at capture time.
    assert resolve_speaker_label("ch_0", "teams", agent_channel=0) == "agent"


def test_resolve_speaker_label_teams_ch1_is_client():
    assert resolve_speaker_label("ch_1", "teams", agent_channel=0) == "client"


def test_resolve_speaker_label_teams_respects_non_default_agent_channel():
    # agent_channel is parameterised for future flexibility. If the bot
    # ever flips to ch_1 = agent, this must still resolve correctly.
    assert resolve_speaker_label("ch_1", "teams", agent_channel=1) == "agent"
    assert resolve_speaker_label("ch_0", "teams", agent_channel=1) == "client"


def test_resolve_speaker_label_non_teams_spk0_is_agent():
    # Pre-Teams heuristic: first speaker = agent. Unchanged behavior.
    assert resolve_speaker_label("spk_0", "twilio") == "agent"
    assert resolve_speaker_label("spk_1", "twilio") == "client"


# ── RecordingProcessor: Teams skip-download branch (spec §9) ───────


def _make_teams_sqs_event(
    *,
    call_id="teams-call-123",
    agent_id="agent-007",
    tenant_id="tenant-001",
    recording_s3_key="tenant-001/recordings/2026/04/teams-call-123.mp3",
    partial=False,
    partial_reason=None,
    ms_call_id="ms-call-abc",
):
    """Build an SQS event matching what the TeamsMediaBot publishes (spec §5.7)."""
    body = {
        "tenant_id": tenant_id,
        "provider": "teams",
        "provider_call_id": ms_call_id,
        "direction": "inbound",
        "from_number": "+15195550100",
        "to_identifier": "+15195550200",
        "duration_sec": 465,
        "recording_url": "",  # not used in pre-uploaded path
        "recording_format": "mp3",
        "ended_at": "2026-04-09T14:31:45Z",
        "raw_payload": {},
        # Teams-specific fields (spec §10 + §5.7)
        "call_id": call_id,
        "agent_id": agent_id,
        "ms_call_id": ms_call_id,
        "recording_already_uploaded": True,
        "recording_s3_key": recording_s3_key,
        "agent_channel": 0,
        "partial": partial,
        "partial_reason": partial_reason,
    }
    return {"Records": [{"body": json.dumps(body)}]}


def _make_twilio_sqs_event(*, call_id=None, tenant_id="tenant-001"):
    """Build an SQS event in the pre-Teams format — no Teams fields set."""
    body = {
        "tenant_id": tenant_id,
        "provider": "twilio",
        "provider_call_id": "CA123",
        "direction": "inbound",
        "from_number": "+15195550100",
        "to_identifier": "+15195550200",
        "duration_sec": 120,
        "recording_url": "https://api.twilio.com/2010-04-01/Accounts/AC/Recordings/RE.mp3",
        "recording_format": "mp3",
        "ended_at": "2026-04-09T14:31:45Z",
        "raw_payload": {},
    }
    if call_id:
        body["call_id"] = call_id
    return {"Records": [{"body": json.dumps(body)}]}


@patch.object(recording_processor, "_invoke_transcription_init")
@patch.object(recording_processor, "_download_and_upload_recording")
@patch.object(recording_processor, "_resolve_agent")
@patch.object(recording_processor, "db")
def test_teams_skip_download_calls_update_not_create(
    mock_db, mock_resolve_agent, mock_download, mock_invoke_ti
):
    """Teams pre-uploaded path: must UPDATE, never CREATE, never download."""
    event = _make_teams_sqs_event()

    recording_processor.handler(event, MockContext())

    # Critical: no download happened (bot already streamed to S3).
    mock_download.assert_not_called()
    # Critical: no phone-number agent lookup (bot pre-resolved via Graph).
    mock_resolve_agent.assert_not_called()
    # Critical: UPDATE the row the bot already created in §5.6.5, not PUT.
    mock_db.create_call.assert_not_called()
    mock_db.update_call.assert_called_once()

    # Verify the update payload carries the fields the bot could not know
    # at call-answer time.
    tenant_id_arg, call_id_arg, updates = mock_db.update_call.call_args.args
    assert tenant_id_arg == "tenant-001"
    assert call_id_arg == "teams-call-123"
    assert updates["status"] == "transcribing"
    assert updates["recording_s3_key"] == "tenant-001/recordings/2026/04/teams-call-123.mp3"
    assert updates["recording_status"] == "upload_complete"
    assert updates["duration_sec"] == 465
    assert updates["partial_recording"] is False
    assert updates["agent_channel"] == 0
    assert updates["ms_call_id"] == "ms-call-abc"

    # TranscriptionInit must still be invoked — with provider=teams so
    # it picks ChannelIdentification.
    mock_invoke_ti.assert_called_once()
    ti_args = mock_invoke_ti.call_args.args
    # Signature: (tenant_id, call_id, recording_s3_key, year, month, provider)
    assert ti_args[0] == "tenant-001"
    assert ti_args[1] == "teams-call-123"
    assert ti_args[2] == "tenant-001/recordings/2026/04/teams-call-123.mp3"
    assert ti_args[5] == "teams"


@patch.object(recording_processor, "_invoke_transcription_init")
@patch.object(recording_processor, "_download_and_upload_recording")
@patch.object(recording_processor, "_resolve_agent")
@patch.object(recording_processor, "db")
def test_teams_partial_recording_still_runs_pipeline(
    mock_db, mock_resolve_agent, mock_download, mock_invoke_ti
):
    """Partial recordings must still flow through — spec §5.6.7."""
    event = _make_teams_sqs_event(partial=True, partial_reason="graceful_shutdown")

    recording_processor.handler(event, MockContext())

    mock_db.update_call.assert_called_once()
    _, _, updates = mock_db.update_call.call_args.args
    assert updates["partial_recording"] is True
    assert updates["partial_reason"] == "graceful_shutdown"

    # Pipeline still progresses despite partial flag.
    mock_invoke_ti.assert_called_once()


@patch.object(recording_processor, "_invoke_transcription_init")
@patch.object(recording_processor, "_download_and_upload_recording")
@patch.object(recording_processor, "_resolve_agent")
@patch.object(recording_processor, "db")
def test_teams_trusts_pre_resolved_agent_id(
    mock_db, mock_resolve_agent, mock_download, mock_invoke_ti
):
    """agent_id from the bot must be trusted; no number-mapping lookup."""
    event = _make_teams_sqs_event(agent_id="agent-pre-resolved")

    recording_processor.handler(event, MockContext())

    # Phone-number-based resolver is meaningless for Teams and must be
    # skipped entirely (spec §5.8).
    mock_resolve_agent.assert_not_called()


# ── RecordingProcessor: non-Teams path unchanged ────────────────────


@patch.object(recording_processor, "_invoke_transcription_init")
@patch.object(recording_processor, "_download_and_upload_recording")
@patch.object(recording_processor, "_resolve_agent")
@patch.object(recording_processor, "db")
def test_twilio_path_still_creates_and_downloads(
    mock_db, mock_resolve_agent, mock_download, mock_invoke_ti
):
    """Regression: non-Teams providers must still go through create_call + download."""
    mock_resolve_agent.return_value = "agent-from-mapping"
    mock_download.return_value = "tenant-001/recordings/2026/04/some-uuid.mp3"

    event = _make_twilio_sqs_event()

    recording_processor.handler(event, MockContext())

    # Old behavior: phone-number resolver runs, download runs, CREATE runs.
    mock_resolve_agent.assert_called_once_with("tenant-001", "+15195550200")
    mock_download.assert_called_once()
    mock_db.create_call.assert_called_once()
    mock_db.update_call.assert_not_called()

    # Created row carries the fields the old path always stored.
    call_item = mock_db.create_call.call_args.args[0]
    assert call_item["tenant_id"] == "tenant-001"
    assert call_item["provider"] == "twilio"
    assert call_item["agent_id"] == "agent-from-mapping"
    assert call_item["status"] == "transcribing"

    # TranscriptionInit invoked with provider=twilio so the settings
    # cascade falls to ShowSpeakerLabels.
    ti_args = mock_invoke_ti.call_args.args
    assert ti_args[5] == "twilio"
