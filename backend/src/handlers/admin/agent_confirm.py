"""AgentConfirm — POST /admin/agents/confirm (Agent JWT required).

Updates agent record to active status after first login.
"""

import json
import time

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
        return _handle_confirm(event, start_time)
    except Exception:
        logger.exception("Unhandled error in agent confirm")
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"})
        return _response(500, {"error": "Internal server error"})


def _handle_confirm(event: dict, start_time: float) -> dict:
    claims = _extract_claims(event)
    tenant_id = claims.get("custom:tenant_id")
    cognito_sub = claims.get("sub")

    if not tenant_id or not cognito_sub:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "unauthorized"})
        return _response(401, {"error": "Missing tenant_id or sub in claims"})

    # Look up agent by cognito_sub to find the agent_id
    logger.debug("Looking up agent by cognito_sub", extra={"tenant_id": tenant_id, "cognito_sub": cognito_sub})
    agent = db.get_agent_by_cognito_sub(cognito_sub)

    if not agent:
        # Fall back: look up by email from JWT
        email = claims.get("email", "")
        if email:
            logger.debug("Falling back to email lookup", extra={"tenant_id": tenant_id})
            agent = db.get_agent_by_email(email)

    if not agent or agent.get("tenant_id") != tenant_id:
        logger.warning("Agent not found for confirmation", extra={"tenant_id": tenant_id, "cognito_sub": cognito_sub})
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "not_found"})
        return _response(404, {"error": "Agent not found"})

    if agent.get("status") == "active":
        logger.debug("Agent already confirmed", extra={"tenant_id": tenant_id, "agent_id": agent["agent_id"]})
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
        return _response(200, {"agent_id": agent["agent_id"], "status": "active", "message": "Already confirmed"})

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    updates = {
        "status": "active",
        "cognito_sub": cognito_sub,
        "confirmed_at": now,
    }

    logger.debug("Confirming agent", extra={"tenant_id": tenant_id, "agent_id": agent["agent_id"]})
    db.update_agent(tenant_id, agent["agent_id"], updates)

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
    return _response(200, {"agent_id": agent["agent_id"], "status": "active"})


def _extract_claims(event: dict) -> dict:
    return event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})


def _response(status_code: int, body: dict) -> dict:
    return {"statusCode": status_code, "body": json.dumps(body, default=str)}
