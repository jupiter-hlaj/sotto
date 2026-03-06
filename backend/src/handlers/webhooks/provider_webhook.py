"""ProviderWebhookHandler — public endpoint for provider webhooks (Section 6.1).

Receives POST /webhooks/{provider}, validates the provider signature,
normalises the payload via the adapter, and pushes to SQS.
Also serves GET /health.
"""

import json
import os
import time
from base64 import b64decode
from urllib.parse import parse_qs

import boto3
from aws_lambda_powertools.utilities.typing import LambdaContext

from sotto import db, secrets
from sotto.adapters import ADAPTER_MAP
from sotto.logger import logger, metrics, tracer

# ── Module-level clients (reused across warm invocations) ────────
_sqs_client = None

SQS_CALL_EVENTS_URL = os.environ.get("SQS_CALL_EVENTS_URL", "")


def _get_sqs():
    global _sqs_client
    if _sqs_client is None:
        _sqs_client = boto3.client("sqs")
    return _sqs_client


# ── Handler ──────────────────────────────────────────────────────

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
        return _route(event, context, start_time)
    except Exception:
        provider = (event.get("pathParameters") or {}).get("provider", "unknown")
        logger.exception(
            "Unhandled error in webhook handler",
            extra={"provider": provider},
        )
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"})
        return _response(500, {"error": "Internal server error"})


def _route(event: dict, context: LambdaContext, start_time: float) -> dict:
    """Dispatch to health check or webhook processing."""
    route_key = event.get("routeKey", "")

    # ── GET /health ──────────────────────────────────────────
    if route_key == "GET /health":
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
        return _response(200, {"status": "ok", "service": "sotto"})

    # ── POST /webhooks/{provider} ────────────────────────────
    return _handle_webhook(event, start_time)


def _handle_webhook(event: dict, start_time: float) -> dict:
    """Core webhook processing pipeline."""
    path_params = event.get("pathParameters") or {}
    provider = path_params.get("provider", "")

    # 1. Validate provider
    if provider not in ADAPTER_MAP:
        logger.warning("Unknown provider", extra={"provider": provider})
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": f"Unsupported provider: {provider}"})

    # 2. Decode body
    body = event.get("body", "") or ""
    if event.get("isBase64Encoded"):
        body = b64decode(body).decode("utf-8")

    # 3. Parse payload (form-encoded for Twilio, JSON for others)
    headers = event.get("headers") or {}
    content_type = headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        parsed = parse_qs(body, keep_blank_values=True)
        payload = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
    else:
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {}

    # 4. Resolve tenant_id from To number via NumberMappings scan
    to_number = payload.get("To", "")
    if not to_number:
        logger.warning("No To number in payload", extra={"provider": provider})
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "Missing To number in payload"})

    logger.debug(
        "Looking up tenant by identifier",
        extra={"provider": provider},
    )
    mapping = db.scan_number_mapping_by_identifier(to_number)
    if not mapping:
        logger.warning("No tenant mapping for number", extra={"provider": provider})
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "not_found"})
        return _response(404, {"error": "No tenant mapping found for this number"})

    tenant_id = mapping["tenant_id"]

    # 5. Verify tenant is active
    logger.debug(
        "Fetching tenant",
        extra={"tenant_id": tenant_id, "provider": provider},
    )
    tenant = db.get_tenant(tenant_id)
    if not tenant or tenant.get("status") != "active":
        logger.warning(
            "Tenant not found or inactive",
            extra={"tenant_id": tenant_id, "provider": provider},
        )
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "forbidden"})
        return _response(403, {"error": "Tenant not active"})

    # 6. Instantiate adapter & validate signature
    adapter_class = ADAPTER_MAP[provider]
    adapter = adapter_class(tenant_id=tenant_id, secrets_client=secrets)

    # Reconstruct webhook URL for signature validation
    domain = event.get("requestContext", {}).get("domainName", "")
    raw_path = event.get("rawPath", "")
    webhook_url = f"https://{domain}{raw_path}"

    source_ip = (event.get("requestContext", {}).get("http") or {}).get("sourceIp", "unknown")

    logger.debug(
        "Validating webhook signature",
        extra={"provider": provider, "tenant_id": tenant_id},
    )
    try:
        adapter.validate_signature(headers, body, webhook_url)
    except (ValueError, Exception):
        logger.warning(
            "Webhook signature validation failed",
            extra={
                "provider": provider,
                "tenant_id": tenant_id,
                "source_ip": source_ip,
            },
        )
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "forbidden"})
        return _response(403, {"error": "Invalid webhook signature"})

    # 7. Check if this is a completed call with recording
    if not adapter.is_call_ended(payload):
        logger.debug(
            "Webhook is not a call-ended event, acknowledging",
            extra={"provider": provider, "tenant_id": tenant_id},
        )
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "acknowledged"})
        return _response(200, {"status": "acknowledged"})

    # 8. Normalize to NormalizedCallEvent
    logger.debug(
        "Normalizing call event",
        extra={"provider": provider, "tenant_id": tenant_id},
    )
    normalized = adapter.normalize(payload)

    # 9. Push to SQS
    queue_name = "sotto-call-events"
    logger.debug(
        "Sending to SQS",
        extra={
            "queue_name": queue_name,
            "event_type": "call_ended",
            "tenant_id": tenant_id,
            "call_id": normalized.provider_call_id,
        },
    )

    _get_sqs().send_message(
        QueueUrl=SQS_CALL_EVENTS_URL,
        MessageBody=normalized.model_dump_json(),
    )

    logger.debug(
        "SQS message sent",
        extra={
            "queue_name": queue_name,
            "tenant_id": tenant_id,
            "call_id": normalized.provider_call_id,
        },
    )

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
    return _response(200, {"status": "accepted"})


def _response(status_code: int, body: dict) -> dict:
    return {"statusCode": status_code, "body": json.dumps(body, default=str)}
