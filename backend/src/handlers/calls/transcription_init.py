"""TranscriptionInit — starts AWS Transcribe job for a recording (Section 6.3).

Trigger: async Lambda invocation from RecordingProcessor.
Starts an AWS Transcribe job with speaker diarisation, updates call
transcript_status to in_progress.
"""

import json
import os
import time

import boto3
from aws_lambda_powertools.utilities.typing import LambdaContext

from sotto import db
from sotto.logger import logger, metrics, tracer

_transcribe_client = None
_env = os.environ.get("ENVIRONMENT", "dev")
_account_id = os.environ.get("AWS_ACCOUNT_ID", "")
RECORDINGS_BUCKET = f"sotto-recordings-{_account_id}-{_env}"


def _get_transcribe_client():
    global _transcribe_client
    if _transcribe_client is None:
        _transcribe_client = boto3.client("transcribe")
    return _transcribe_client


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
        result = _start_transcription(event)
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
        return {"statusCode": 200, "body": json.dumps(result)}
    except Exception:
        logger.exception(
            "Unhandled error in transcription init",
            extra={
                "tenant_id": event.get("tenant_id"),
                "call_id": event.get("call_id"),
            },
        )
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"})
        raise


def get_transcription_settings(provider: str) -> dict:
    """Return provider-appropriate AWS Transcribe Settings block (spec §5.6.8).

    Teams recordings are stereo MP3 (ch_0=agent, ch_1=client, routed at
    capture time by the bot), so we use ChannelIdentification for
    deterministic, 100%-reliable speaker attribution — no ML guessing.

    All other providers deliver mono recordings; we fall back to
    ShowSpeakerLabels ML diarization with two speakers.

    ShowSpeakerLabels and ChannelIdentification are mutually exclusive
    in AWS Transcribe — one or the other, never both.
    """
    if provider == "teams":
        return {"ChannelIdentification": True}
    return {"ShowSpeakerLabels": True, "MaxSpeakerLabels": 2}


@tracer.capture_method
def _start_transcription(event: dict) -> dict:
    tenant_id = event["tenant_id"]
    call_id = event["call_id"]
    recording_s3_key = event["recording_s3_key"]
    year = event["year"]
    month = event["month"]
    # provider is optional for backward compat — non-Teams providers added it
    # in the RecordingProcessor payload alongside the T-7 work. If missing,
    # default to the non-Teams diarization path.
    provider = event.get("provider", "")

    job_name = f"sotto-{_env}-{call_id}"
    media_uri = f"s3://{RECORDINGS_BUCKET}/{recording_s3_key}"
    output_key = f"{tenant_id}/transcripts/{year}/{month}/{call_id}.json"
    settings = get_transcription_settings(provider)

    logger.debug(
        "Starting transcription job BEFORE",
        extra={
            "job_name": job_name,
            "tenant_id": tenant_id,
            "call_id": call_id,
            "provider": provider,
            "media_uri": media_uri,
            "output_bucket": RECORDINGS_BUCKET,
            "output_key": output_key,
            "settings": settings,
        },
    )
    transcribe_start = time.time()

    _get_transcribe_client().start_transcription_job(
        TranscriptionJobName=job_name,
        Media={"MediaFileUri": media_uri},
        LanguageCode="en-US",
        OutputBucketName=RECORDINGS_BUCKET,
        OutputKey=output_key,
        Settings=settings,
    )

    transcribe_duration_ms = int((time.time() - transcribe_start) * 1000)
    logger.debug(
        "Starting transcription job AFTER",
        extra={
            "job_name": job_name,
            "tenant_id": tenant_id,
            "call_id": call_id,
            "duration_ms": transcribe_duration_ms,
            "status": "success",
        },
    )

    # Update call: transcript_status = in_progress
    logger.debug(
        "Updating call transcript_status",
        extra={
            "table": "sotto-calls",
            "tenant_id": tenant_id,
            "call_id": call_id,
            "operation": "UpdateItem",
        },
    )
    db.update_call(tenant_id, call_id, {"transcript_status": "in_progress"})

    return {"job_name": job_name, "call_id": call_id, "tenant_id": tenant_id}
