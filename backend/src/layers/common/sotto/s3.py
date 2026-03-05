"""S3 operations for recordings and transcripts."""

import json
import os

import boto3
from botocore.exceptions import ClientError

from sotto.logger import logger, tracer

_client = None
_env = os.environ.get("ENVIRONMENT", "dev")
_account_id = os.environ.get("AWS_ACCOUNT_ID", "")
RECORDINGS_BUCKET = f"sotto-recordings-{_account_id}-{_env}"


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("s3")
    return _client


@tracer.capture_method
def upload_recording(
    tenant_id: str,
    call_id: str,
    body: bytes,
    ext: str = "mp3",
    year: str = "",
    month: str = "",
) -> str:
    """Upload a recording file to S3. Returns the S3 key."""
    key = f"{tenant_id}/recordings/{year}/{month}/{call_id}.{ext}"
    logger.debug(
        "S3 upload_recording BEFORE",
        extra={"bucket": RECORDINGS_BUCKET, "key": key, "size_bytes": len(body)},
    )
    _get_client().put_object(
        Bucket=RECORDINGS_BUCKET,
        Key=key,
        Body=body,
        ContentType=f"audio/{ext}",
    )
    logger.debug(
        "S3 upload_recording AFTER",
        extra={"bucket": RECORDINGS_BUCKET, "key": key, "status": "success"},
    )
    return key


@tracer.capture_method
def read_transcript(tenant_id: str, call_id: str, year: str, month: str) -> dict:
    """Read a transcript JSON from S3."""
    key = f"{tenant_id}/transcripts/{year}/{month}/{call_id}.json"
    logger.debug(
        "S3 read_transcript BEFORE",
        extra={"bucket": RECORDINGS_BUCKET, "key": key},
    )
    try:
        resp = _get_client().get_object(Bucket=RECORDINGS_BUCKET, Key=key)
        data = json.loads(resp["Body"].read().decode("utf-8"))
        logger.debug(
            "S3 read_transcript AFTER",
            extra={"bucket": RECORDINGS_BUCKET, "key": key, "status": "success"},
        )
        return data
    except ClientError as exc:
        logger.exception(
            "S3 read_transcript failed",
            extra={"bucket": RECORDINGS_BUCKET, "key": key, "error": str(exc)},
        )
        raise


@tracer.capture_method
def read_transcript_by_key(s3_key: str) -> dict:
    """Read a transcript JSON from S3 using a full key."""
    logger.debug(
        "S3 read_transcript_by_key BEFORE",
        extra={"bucket": RECORDINGS_BUCKET, "key": s3_key},
    )
    try:
        resp = _get_client().get_object(Bucket=RECORDINGS_BUCKET, Key=s3_key)
        data = json.loads(resp["Body"].read().decode("utf-8"))
        logger.debug(
            "S3 read_transcript_by_key AFTER",
            extra={"bucket": RECORDINGS_BUCKET, "key": s3_key, "status": "success"},
        )
        return data
    except ClientError as exc:
        logger.exception(
            "S3 read_transcript_by_key failed",
            extra={"bucket": RECORDINGS_BUCKET, "key": s3_key, "error": str(exc)},
        )
        raise


@tracer.capture_method
def write_transcript(
    tenant_id: str,
    call_id: str,
    transcript: dict,
    year: str = "",
    month: str = "",
) -> str:
    """Write a transcript JSON to S3. Returns the S3 key."""
    key = f"{tenant_id}/transcripts/{year}/{month}/{call_id}.json"
    logger.debug(
        "S3 write_transcript BEFORE",
        extra={"bucket": RECORDINGS_BUCKET, "key": key},
    )
    _get_client().put_object(
        Bucket=RECORDINGS_BUCKET,
        Key=key,
        Body=json.dumps(transcript).encode("utf-8"),
        ContentType="application/json",
    )
    logger.debug(
        "S3 write_transcript AFTER",
        extra={"bucket": RECORDINGS_BUCKET, "key": key, "status": "success"},
    )
    return key
