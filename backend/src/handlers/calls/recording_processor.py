"""RecordingProcessor — downloads recording from provider, uploads to S3 (Section 6.2).

Trigger: SQS sotto-call-events-{env} (batch size 1).
Deserialises NormalizedCallEvent, resolves agent, creates call record,
streams recording to S3, then invokes TranscriptionInit.
"""

import json
import os
import time
import uuid

import boto3
import requests
from aws_lambda_powertools.utilities.typing import LambdaContext

from sotto import db, s3, secrets
from sotto.logger import logger, metrics, tracer
from sotto.models import NormalizedCallEvent
from sotto import ws_publisher

_lambda_client = None
_apigw_client = None
_env = os.environ.get("ENVIRONMENT", "dev")
_account_id = os.environ.get("AWS_ACCOUNT_ID", "")
TRANSCRIPTION_INIT_FUNCTION = os.environ.get("TRANSCRIPTION_INIT_FUNCTION", "")
WEBSOCKET_API_ENDPOINT = os.environ.get("WEBSOCKET_API_ENDPOINT", "")
RECORDINGS_BUCKET = f"sotto-recordings-{_account_id}-{_env}"

# Multipart upload threshold: 5 MB minimum part size for S3
_MIN_PART_SIZE = 5 * 1024 * 1024
_STREAM_CHUNK_SIZE = 1024 * 1024  # 1 MB read chunks


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


def _get_s3_client():
    return boto3.client("s3")


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
        for record in event.get("Records", []):
            _process_record(record)

        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
        return {"statusCode": 200, "body": json.dumps({"status": "processed"})}
    except Exception:
        logger.exception("Unhandled error in recording processor")
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"})
        raise  # Re-raise so SQS retries


@tracer.capture_method
def _process_record(record: dict) -> None:
    body = record.get("body", "")
    call_event = NormalizedCallEvent.model_validate_json(body)
    tenant_id = call_event.tenant_id
    call_id = str(uuid.uuid4())

    logger.debug(
        "Processing call event",
        extra={
            "tenant_id": tenant_id,
            "provider": call_event.provider,
            "provider_call_id": call_event.provider_call_id,
            "call_id": call_id,
        },
    )

    # Resolve agent_id from NumberMappings
    agent_id = _resolve_agent(tenant_id, call_event.to_identifier)

    # Create call record with status=recording
    now_iso = call_event.ended_at.isoformat()
    year = call_event.ended_at.strftime("%Y")
    month = call_event.ended_at.strftime("%m")

    call_item = {
        "tenant_id": tenant_id,
        "call_id": call_id,
        "agent_id": agent_id,
        "provider": call_event.provider,
        "provider_call_id": call_event.provider_call_id,
        "direction": call_event.direction,
        "from_number": call_event.from_number,
        "to_identifier": call_event.to_identifier,
        "duration_sec": call_event.duration_sec,
        "status": "recording",
        "transcript_status": "pending",
        "created_at": now_iso,
        "ended_at": now_iso,
    }

    logger.debug(
        "Creating call record",
        extra={"table": "sotto-calls", "tenant_id": tenant_id, "call_id": call_id, "operation": "PutItem"},
    )
    db.create_call(call_item)

    # Download recording from provider and stream to S3
    recording_s3_key = _download_and_upload_recording(
        tenant_id=tenant_id,
        call_id=call_id,
        call_event=call_event,
        year=year,
        month=month,
    )

    # Update call: set recording key, status=transcribing
    logger.debug(
        "Updating call status to transcribing",
        extra={"table": "sotto-calls", "tenant_id": tenant_id, "call_id": call_id, "operation": "UpdateItem"},
    )
    db.update_call(tenant_id, call_id, {
        "recording_s3_key": recording_s3_key,
        "status": "transcribing",
    })

    # Push call_recorded event to agent via WebSocket
    if agent_id and WEBSOCKET_API_ENDPOINT:
        ws_publisher.push_to_agent(
            agent_id=agent_id,
            tenant_id=tenant_id,
            event={"event": "call_recorded", "call_id": call_id, "tenant_id": tenant_id},
            apigw_client=_get_apigw_client(),
        )

    # Invoke TranscriptionInit
    _invoke_transcription_init(tenant_id, call_id, recording_s3_key, year, month)


@tracer.capture_method
def _resolve_agent(tenant_id: str, to_identifier: str) -> str | None:
    logger.debug(
        "Resolving agent from number mapping",
        extra={"table": "sotto-number-mappings", "tenant_id": tenant_id, "operation": "GetItem"},
    )
    mapping = db.get_number_mapping(tenant_id, to_identifier)
    if not mapping:
        logger.warning(
            "No number mapping found for identifier, agent_id will be None",
            extra={"tenant_id": tenant_id},
        )
        return None
    agent_id = mapping.get("agent_id")
    logger.debug("Agent resolved", extra={"tenant_id": tenant_id, "agent_id": agent_id})
    return agent_id


@tracer.capture_method
def _download_and_upload_recording(
    tenant_id: str,
    call_id: str,
    call_event: NormalizedCallEvent,
    year: str,
    month: str,
) -> str:
    """Stream recording from provider URL directly to S3 via multipart upload."""
    ext = call_event.recording_format
    s3_key = f"{tenant_id}/recordings/{year}/{month}/{call_id}.{ext}"

    # Get provider credentials for authenticated download
    logger.debug(
        "Fetching provider credentials BEFORE",
        extra={"tenant_id": tenant_id, "provider": call_event.provider},
    )
    creds = secrets.get_provider_credentials(tenant_id, call_event.provider)
    logger.debug(
        "Fetching provider credentials AFTER",
        extra={"tenant_id": tenant_id, "provider": call_event.provider, "status": "success"},
    )

    # Build auth for provider download
    auth = _build_provider_auth(call_event.provider, creds)

    # Stream download from provider
    logger.debug(
        "Downloading recording BEFORE",
        extra={"tenant_id": tenant_id, "call_id": call_id, "provider": call_event.provider},
    )
    download_start = time.time()
    resp = requests.get(call_event.recording_url, auth=auth, stream=True, timeout=55)
    resp.raise_for_status()
    download_duration_ms = int((time.time() - download_start) * 1000)
    logger.debug(
        "Recording download stream opened",
        extra={"tenant_id": tenant_id, "call_id": call_id, "duration_ms": download_duration_ms},
    )

    # S3 multipart upload — stream directly without loading full file into memory
    s3_client = _get_s3_client()
    logger.debug(
        "S3 multipart upload BEFORE",
        extra={"bucket": RECORDINGS_BUCKET, "key": s3_key},
    )
    upload_start = time.time()

    mpu = s3_client.create_multipart_upload(
        Bucket=RECORDINGS_BUCKET,
        Key=s3_key,
        ContentType=f"audio/{ext}",
    )
    upload_id = mpu["UploadId"]

    parts = []
    part_number = 1
    buffer = b""
    total_bytes = 0

    try:
        for chunk in resp.iter_content(chunk_size=_STREAM_CHUNK_SIZE):
            buffer += chunk
            total_bytes += len(chunk)

            if len(buffer) >= _MIN_PART_SIZE:
                part = s3_client.upload_part(
                    Bucket=RECORDINGS_BUCKET,
                    Key=s3_key,
                    UploadId=upload_id,
                    PartNumber=part_number,
                    Body=buffer,
                )
                parts.append({"ETag": part["ETag"], "PartNumber": part_number})
                part_number += 1
                buffer = b""

        # Upload remaining data
        if buffer:
            part = s3_client.upload_part(
                Bucket=RECORDINGS_BUCKET,
                Key=s3_key,
                UploadId=upload_id,
                PartNumber=part_number,
                Body=buffer,
            )
            parts.append({"ETag": part["ETag"], "PartNumber": part_number})

        s3_client.complete_multipart_upload(
            Bucket=RECORDINGS_BUCKET,
            Key=s3_key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
    except Exception:
        # Abort multipart upload on failure to avoid orphaned parts
        s3_client.abort_multipart_upload(
            Bucket=RECORDINGS_BUCKET,
            Key=s3_key,
            UploadId=upload_id,
        )
        raise

    upload_duration_ms = int((time.time() - upload_start) * 1000)
    logger.debug(
        "S3 multipart upload AFTER",
        extra={
            "bucket": RECORDINGS_BUCKET,
            "key": s3_key,
            "total_bytes": total_bytes,
            "parts": len(parts),
            "duration_ms": upload_duration_ms,
            "status": "success",
        },
    )

    return s3_key


def _build_provider_auth(provider: str, creds: dict):
    """Build requests auth tuple for provider recording download."""
    if provider == "twilio":
        return (creds.get("account_sid", ""), creds.get("token", ""))
    # Other providers: use bearer token via header (requests auth tuple)
    token = creds.get("token", "")
    if token:
        return _BearerAuth(token)
    return None


class _BearerAuth(requests.auth.AuthBase):
    def __init__(self, token: str):
        self._token = token

    def __call__(self, r):
        r.headers["Authorization"] = f"Bearer {self._token}"
        return r


@tracer.capture_method
def _invoke_transcription_init(
    tenant_id: str,
    call_id: str,
    recording_s3_key: str,
    year: str,
    month: str,
) -> None:
    payload = {
        "tenant_id": tenant_id,
        "call_id": call_id,
        "recording_s3_key": recording_s3_key,
        "year": year,
        "month": month,
    }

    logger.debug(
        "Invoking TranscriptionInit BEFORE",
        extra={"function": TRANSCRIPTION_INIT_FUNCTION, "tenant_id": tenant_id, "call_id": call_id},
    )
    invoke_start = time.time()
    _get_lambda_client().invoke(
        FunctionName=TRANSCRIPTION_INIT_FUNCTION,
        InvocationType="Event",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    invoke_duration_ms = int((time.time() - invoke_start) * 1000)
    logger.debug(
        "Invoking TranscriptionInit AFTER",
        extra={
            "function": TRANSCRIPTION_INIT_FUNCTION,
            "tenant_id": tenant_id,
            "call_id": call_id,
            "duration_ms": invoke_duration_ms,
            "status": "success",
        },
    )
