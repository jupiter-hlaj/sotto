"""CallDetailHandler — GET /calls/{call_id} (Agent JWT).

Returns full call record. If transcript_s3_key is set, fetches and includes
parsed transcript content from S3.
"""

import json
import os
import time

import boto3
from aws_lambda_powertools.utilities.typing import LambdaContext

from sotto import db, s3
from sotto.logger import logger, metrics, tracer

_s3_client = None
_env = os.environ.get("ENVIRONMENT", "dev")
_account_id = os.environ.get("AWS_ACCOUNT_ID", "")
RECORDINGS_BUCKET = f"sotto-recordings-{_account_id}-{_env}"


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


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
        route_key = event.get("routeKey", "")
        if route_key == "GET /calls/{call_id}/recording-url":
            return _handle_recording_url(event, start_time)
        return _handle(event, start_time)
    except Exception:
        logger.exception("Unhandled error in call detail handler")
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"})
        return _response(500, {"error": "Internal server error"})


def _handle(event: dict, start_time: float) -> dict:
    claims = _extract_claims(event)
    tenant_id = claims.get("custom:tenant_id")

    if not tenant_id:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "unauthorized"})
        return _response(401, {"error": "Missing tenant_id in claims"})

    call_id = event.get("pathParameters", {}).get("call_id")
    if not call_id:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "Missing call_id path parameter"})

    logger.debug("Fetching call detail", extra={"tenant_id": tenant_id, "call_id": call_id})
    call = db.get_call(tenant_id, call_id)

    if not call:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "not_found"})
        return _response(404, {"error": "Call not found"})

    if call.get("tenant_id") != tenant_id:
        logger.debug("Tenant mismatch on call detail", extra={"call_tenant": call.get("tenant_id"), "jwt_tenant": tenant_id})
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "forbidden"})
        return _response(403, {"error": "Access denied"})

    # Build response — strip internal fields, add transcript if available
    result = _build_call_response(call)

    transcript_key = call.get("transcript_s3_key")
    if transcript_key:
        logger.debug("Fetching transcript from S3", extra={"tenant_id": tenant_id, "call_id": call_id, "s3_key": transcript_key})
        try:
            raw = s3.read_transcript_by_key(transcript_key)
            result["transcript"] = _parse_transcript(raw)
        except Exception:
            logger.exception("Failed to read transcript from S3", extra={"tenant_id": tenant_id, "call_id": call_id, "s3_key": transcript_key})
            result["transcript"] = None
            result["transcript_error"] = "Failed to load transcript"

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
    return _response(200, result)


def _handle_recording_url(event: dict, start_time: float) -> dict:
    claims = _extract_claims(event)
    tenant_id = claims.get("custom:tenant_id")
    if not tenant_id:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "unauthorized"})
        return _response(401, {"error": "Missing tenant_id in claims"})

    call_id = event.get("pathParameters", {}).get("call_id")
    if not call_id:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "Missing call_id path parameter"})

    call = db.get_call(tenant_id, call_id)
    if not call or call.get("tenant_id") != tenant_id:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "not_found"})
        return _response(404, {"error": "Call not found"})

    recording_key = call.get("recording_s3_key")
    if not recording_key:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "not_found"})
        return _response(404, {"error": "No recording available"})

    logger.debug("Generating presigned URL", extra={"tenant_id": tenant_id, "call_id": call_id})
    url = _get_s3_client().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": RECORDINGS_BUCKET,
            "Key": recording_key,
            "ResponseContentDisposition": f'attachment; filename="call-{call_id}.mp3"',
        },
        ExpiresIn=900,
    )

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
    return _response(200, {"url": url, "expires_in": 900})


def _parse_transcript(raw: dict) -> dict:
    """Convert raw AWS Transcribe JSON into {segments: [{speaker, text}]}."""
    segments = []
    for seg in raw.get("results", {}).get("audio_segments", []):
        label = seg.get("speaker_label", "spk_0")
        try:
            speaker_num = int(label.replace("spk_", "")) + 1
        except ValueError:
            speaker_num = 1
        segments.append({
            "speaker": f"Speaker {speaker_num}",
            "text": seg.get("transcript", ""),
            "start_time": seg.get("start_time"),
            "end_time": seg.get("end_time"),
        })
    return {"segments": segments}


def _build_call_response(call: dict) -> dict:
    # Return all call fields except recording_s3_key (contains auth-sensitive path info)
    safe_fields = {
        "call_id", "tenant_id", "agent_id", "provider", "provider_call_id",
        "direction", "from_number", "to_identifier", "duration_sec", "status",
        "transcript_status", "summary", "action_items", "notes",
        "notes_updated_at", "created_at", "ended_at",
    }
    return {k: v for k, v in call.items() if k in safe_fields}


def _extract_claims(event: dict) -> dict:
    return event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})


def _response(status_code: int, body: dict) -> dict:
    return {"statusCode": status_code, "body": json.dumps(body, default=str)}
