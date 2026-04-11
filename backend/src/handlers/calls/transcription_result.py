"""TranscriptionResultProcessor — handles Transcribe completion events (Section 6.4).

Trigger: EventBridge rule matching aws.transcribe events
         (TranscriptionJobStatus: COMPLETED | FAILED).
Parses transcript, updates call record, invokes AISummarizer async.
"""

import json
import os
import time

import boto3
from aws_lambda_powertools.utilities.typing import LambdaContext

from sotto import db, s3
from sotto import ws_publisher
from sotto.logger import logger, metrics, tracer

_lambda_client = None
_apigw_client = None
_env = os.environ.get("ENVIRONMENT", "dev")
AI_SUMMARIZER_FUNCTION = os.environ.get("AI_SUMMARIZER_FUNCTION", "")
WEBSOCKET_API_ENDPOINT = os.environ.get("WEBSOCKET_API_ENDPOINT", "")


def _get_lambda_client():
    global _lambda_client
    if _lambda_client is None:
        _lambda_client = boto3.client("lambda")
    return _lambda_client


def _get_apigw_client():
    global _apigw_client
    if _apigw_client is None:
        _apigw_client = boto3.client(
            "apigatewaymanagementapi",
            endpoint_url=WEBSOCKET_API_ENDPOINT,
        )
    return _apigw_client


def resolve_speaker_label(label: str, provider: str, agent_channel: int = 0) -> str:
    """Map a raw AWS Transcribe speaker/channel label to 'agent' | 'client' (spec §5.6.8).

    Teams (stereo + ChannelIdentification): labels are 'ch_0' / 'ch_1'.
    The bot routes agent audio to agent_channel (always 0) at capture
    time, so the mapping is deterministic — no ML guessing.

    All other providers (mono + ShowSpeakerLabels diarization): labels
    are 'spk_0' / 'spk_1'. 'spk_0' is typically the first speaker to
    talk; we treat it as the agent. This is a heuristic, not a
    guarantee — the existing behavior pre-Teams.
    """
    if provider == "teams":
        return "agent" if label == f"ch_{agent_channel}" else "client"
    return "agent" if label == "spk_0" else "client"


@logger.inject_lambda_context(log_event=True, correlation_id_path="requestContext.requestId")
@tracer.capture_lambda_handler(capture_response=True)
@metrics.log_metrics(capture_cold_start_metric=True)
def handler(event: dict, context: LambdaContext) -> dict:
    start_time = time.time()
    logger.debug(
        "Handler entered",
        extra={
            "function_name": context.function_name,
            "function_version": context.function_version,
            "aws_request_id": context.aws_request_id,
            "memory_limit_mb": context.memory_limit_in_mb,
            "remaining_time_ms": context.get_remaining_time_in_millis(),
            "event_keys": list(event.keys()),
        },
    )

    try:
        _process_transcription_event(event)
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
        return {"statusCode": 200, "body": json.dumps({"status": "processed"})}
    except Exception:
        logger.exception(
            "Unhandled error in transcription result processor",
            extra={"event_detail": event.get("detail", {})},
        )
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"})
        raise


@tracer.capture_method
def _process_transcription_event(event: dict) -> None:
    detail = event.get("detail", {})
    job_name = detail.get("TranscriptionJobName", "")
    job_status = detail.get("TranscriptionJobStatus", "")

    # Extract call_id from job name: sotto-{env}-{call_id}
    prefix = f"sotto-{_env}-"
    if not job_name.startswith(prefix):
        logger.warning("Unexpected job name format", extra={"job_name": job_name})
        return
    call_id = job_name[len(prefix):]

    logger.debug(
        "Processing transcription result",
        extra={"job_name": job_name, "call_id": call_id, "job_status": job_status},
    )

    # We need tenant_id to update the call — look it up from DynamoDB
    # The call_id is the SK; we need to scan or use a GSI.
    # Since call_id is unique, we can scan with a filter. For MVP this is acceptable.
    call = _find_call_by_id(call_id)
    if not call:
        logger.error("Call not found for transcription result", extra={"call_id": call_id})
        return

    tenant_id = call["tenant_id"]

    if job_status == "FAILED":
        _handle_failed(tenant_id, call_id, call)
    elif job_status == "COMPLETED":
        _handle_completed(tenant_id, call_id, call)


@tracer.capture_method
def _find_call_by_id(call_id: str) -> dict | None:
    """Find a call record by call_id across all tenants.

    Uses status-index GSI to find the call. Since call_id is the sort key
    on the main table, we scan with a filter for MVP.
    """
    import boto3
    from sotto.db import CALLS_TABLE

    logger.debug(
        "Scanning for call by call_id",
        extra={"table": "sotto-calls", "call_id": call_id, "operation": "Scan"},
    )
    table = boto3.resource("dynamodb").Table(CALLS_TABLE)
    from boto3.dynamodb.conditions import Attr
    resp = table.scan(
        FilterExpression=Attr("call_id").eq(call_id),
    )
    items = resp.get("Items", [])
    return items[0] if items else None


@tracer.capture_method
def _handle_failed(tenant_id: str, call_id: str, call: dict) -> None:
    logger.warning(
        "Transcription job failed",
        extra={"tenant_id": tenant_id, "call_id": call_id},
    )
    logger.debug(
        "Updating call for transcription failure",
        extra={"table": "sotto-calls", "tenant_id": tenant_id, "call_id": call_id, "operation": "UpdateItem"},
    )
    db.update_call(tenant_id, call_id, {
        "transcript_status": "failed",
        "status": "failed",
    })

    # Push transcription_failed event to agent via WebSocket
    agent_id = call.get("agent_id")
    if agent_id and WEBSOCKET_API_ENDPOINT:
        ws_publisher.push_to_agent(
            agent_id=agent_id,
            tenant_id=tenant_id,
            event={"event": "transcription_failed", "call_id": call_id, "tenant_id": tenant_id},
            apigw_client=_get_apigw_client(),
        )


@tracer.capture_method
def _handle_completed(tenant_id: str, call_id: str, call: dict) -> None:
    # Derive transcript S3 key from recording key
    recording_key = call.get("recording_s3_key", "")
    # recording key: {tenant_id}/recordings/{year}/{month}/{call_id}.{ext}
    # transcript key: {tenant_id}/transcripts/{year}/{month}/{call_id}.json
    parts = recording_key.split("/")
    if len(parts) >= 4:
        year = parts[2]
        month = parts[3]
    else:
        year = ""
        month = ""

    transcript_s3_key = f"{tenant_id}/transcripts/{year}/{month}/{call_id}.json"

    # Read transcript JSON from S3
    logger.debug(
        "Reading transcript from S3",
        extra={"tenant_id": tenant_id, "call_id": call_id, "key": transcript_s3_key},
    )
    read_start = time.time()
    raw_transcript = s3.read_transcript_by_key(transcript_s3_key)
    read_duration_ms = int((time.time() - read_start) * 1000)
    logger.debug(
        "Transcript read from S3",
        extra={"tenant_id": tenant_id, "call_id": call_id, "duration_ms": read_duration_ms},
    )

    # Parse AWS Transcribe output into structured segments
    segments = _parse_transcript(raw_transcript)

    # Update call record
    logger.debug(
        "Updating call for transcription completion",
        extra={"table": "sotto-calls", "tenant_id": tenant_id, "call_id": call_id, "operation": "UpdateItem"},
    )
    db.update_call(tenant_id, call_id, {
        "transcript_s3_key": transcript_s3_key,
        "transcript_status": "complete",
        "status": "summarizing",
    })

    # Push transcript_ready event to agent via WebSocket
    agent_id = call.get("agent_id")
    if agent_id and WEBSOCKET_API_ENDPOINT:
        ws_publisher.push_to_agent(
            agent_id=agent_id,
            tenant_id=tenant_id,
            event={"event": "transcript_ready", "call_id": call_id, "tenant_id": tenant_id},
            apigw_client=_get_apigw_client(),
        )

    # Invoke AISummarizer async
    _invoke_ai_summarizer(tenant_id, call_id, segments)


@tracer.capture_method
def _parse_transcript(raw: dict) -> list[dict]:
    """Parse AWS Transcribe JSON output into list of {speaker, text, start_time, end_time}."""
    segments = []
    results = raw.get("results", {})

    # Speaker labels from Transcribe
    speaker_labels = results.get("speaker_labels", {})
    speaker_segments = speaker_labels.get("segments", [])

    for segment in speaker_segments:
        speaker = segment.get("speaker_label", "unknown")
        items = segment.get("items", [])
        if not items:
            continue

        words = []
        start_time = items[0].get("start_time", "0")
        end_time = items[-1].get("end_time", "0")

        for item in items:
            # Match each speaker-labelled item to the transcript items
            # to get the actual text content
            pass

        # Fallback: build text from transcript items with matching times
        segment_start = float(start_time)
        segment_end = float(end_time)

        # Get text from the main transcript items
        text_parts = []
        for transcript_item in results.get("items", []):
            item_start = float(transcript_item.get("start_time", "0") or "0")
            item_end = float(transcript_item.get("end_time", "0") or "0")
            content = transcript_item.get("alternatives", [{}])[0].get("content", "")
            item_type = transcript_item.get("type", "")

            if item_type == "punctuation":
                if text_parts:
                    text_parts[-1] = text_parts[-1] + content
                continue

            if item_start >= segment_start and item_end <= segment_end:
                text_parts.append(content)

        text = " ".join(text_parts).strip()
        if text:
            segments.append({
                "speaker": speaker,
                "text": text,
                "start_time": start_time,
                "end_time": end_time,
            })

    return segments


@tracer.capture_method
def _invoke_ai_summarizer(tenant_id: str, call_id: str, segments: list[dict]) -> None:
    if not AI_SUMMARIZER_FUNCTION:
        logger.debug(
            "AI_SUMMARIZER_FUNCTION not configured, skipping",
            extra={"tenant_id": tenant_id, "call_id": call_id},
        )
        return

    payload = {
        "tenant_id": tenant_id,
        "call_id": call_id,
        "transcript_segments": segments,
    }

    logger.debug(
        "Invoking AISummarizer BEFORE",
        extra={
            "function": AI_SUMMARIZER_FUNCTION,
            "tenant_id": tenant_id,
            "call_id": call_id,
            "segment_count": len(segments),
        },
    )
    invoke_start = time.time()
    _get_lambda_client().invoke(
        FunctionName=AI_SUMMARIZER_FUNCTION,
        InvocationType="Event",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    invoke_duration_ms = int((time.time() - invoke_start) * 1000)
    logger.debug(
        "Invoking AISummarizer AFTER",
        extra={
            "function": AI_SUMMARIZER_FUNCTION,
            "tenant_id": tenant_id,
            "call_id": call_id,
            "duration_ms": invoke_duration_ms,
            "status": "success",
        },
    )
