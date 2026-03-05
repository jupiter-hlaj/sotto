"""NotesHandler — PUT /calls/{call_id}/notes (Agent JWT).

Updates agent notes on a call. Ownership check: call must belong to the
requesting agent's tenant_id AND agent_id.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from aws_lambda_powertools.utilities.typing import LambdaContext

from sotto import db
from sotto.logger import logger, metrics, tracer


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
        return _handle(event, start_time)
    except Exception:
        logger.exception("Unhandled error in notes handler")
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"})
        return _response(500, {"error": "Internal server error"})


def _handle(event: dict, start_time: float) -> dict:
    claims = _extract_claims(event)
    tenant_id = claims.get("custom:tenant_id")
    agent_id = claims.get("custom:agent_id")

    if not tenant_id or not agent_id:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "unauthorized"})
        return _response(401, {"error": "Missing tenant_id or agent_id in claims"})

    call_id = event.get("pathParameters", {}).get("call_id")
    if not call_id:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "Missing call_id path parameter"})

    body = _parse_body(event)
    if body is None or "notes" not in body:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "Request body must contain 'notes' field"})

    notes_text = body["notes"]
    if not isinstance(notes_text, str):
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "'notes' must be a string"})

    # Verify ownership
    logger.debug("Verifying call ownership", extra={"tenant_id": tenant_id, "agent_id": agent_id, "call_id": call_id})
    call = db.get_call(tenant_id, call_id)

    if not call:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "not_found"})
        return _response(404, {"error": "Call not found"})

    if call.get("tenant_id") != tenant_id:
        logger.debug("Tenant mismatch on notes update", extra={"call_tenant": call.get("tenant_id"), "jwt_tenant": tenant_id})
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "forbidden"})
        return _response(403, {"error": "Access denied"})

    if call.get("agent_id") != agent_id:
        logger.debug("Agent mismatch on notes update", extra={"call_agent": call.get("agent_id"), "jwt_agent": agent_id})
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "forbidden"})
        return _response(403, {"error": "Access denied"})

    # Update notes
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    logger.debug("Updating notes", extra={"tenant_id": tenant_id, "call_id": call_id, "agent_id": agent_id})
    updated = db.update_call(tenant_id, call_id, {"notes": notes_text, "notes_updated_at": now})

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
    return _response(200, updated)


def _extract_claims(event: dict) -> dict:
    return event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})


def _parse_body(event: dict) -> dict | None:
    body = event.get("body", "") or ""
    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None


def _response(status_code: int, body: dict) -> dict:
    return {"statusCode": status_code, "body": json.dumps(body, default=str)}
