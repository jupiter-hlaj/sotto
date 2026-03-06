"""RolloutManager — POST /internal/rollout (IAM auth only).

Promotes or rolls back tenant deployment tiers in batch.
"""

import json
import time

from aws_lambda_powertools.utilities.typing import LambdaContext

from sotto import db
from sotto.logger import logger, metrics, tracer

VALID_TIERS = {"beta", "live_test", "full"}
VALID_ACTIONS = {"promote", "rollback"}


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
        return _handle_rollout(event, start_time)
    except Exception:
        logger.exception("Unhandled error in rollout manager")
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"})
        return _response(500, {"error": "Internal server error"})


def _handle_rollout(event: dict, start_time: float) -> dict:
    body = _parse_body(event)
    if not body:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "Invalid JSON body"})

    action = body.get("action", "")
    from_tier = body.get("from_tier", "")
    to_tier = body.get("to_tier", "")
    deployment_id = body.get("deployment_id", "")

    if action not in VALID_ACTIONS:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": f"action must be one of: {sorted(VALID_ACTIONS)}"})

    if from_tier not in VALID_TIERS or to_tier not in VALID_TIERS:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": f"from_tier and to_tier must be one of: {sorted(VALID_TIERS)}"})

    if not deployment_id:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "deployment_id is required"})

    if from_tier == to_tier:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "from_tier and to_tier must differ"})

    logger.debug(
        "Rollout action",
        extra={"action": action, "from_tier": from_tier, "to_tier": to_tier, "deployment_id": deployment_id},
    )

    if action == "promote":
        return _promote(from_tier, to_tier, deployment_id, start_time)
    else:
        return _rollback(from_tier, to_tier, deployment_id, start_time)


def _promote(from_tier: str, to_tier: str, deployment_id: str, start_time: float) -> dict:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Find all tenants in from_tier
    logger.debug("Querying tenants for promotion", extra={"from_tier": from_tier})
    tenants = db.list_tenants_by_tier(from_tier)

    updated_count = 0
    for tenant in tenants:
        tid = tenant["tenant_id"]
        logger.debug("Promoting tenant", extra={"tenant_id": tid, "from_tier": from_tier, "to_tier": to_tier})
        db.update_tenant(tid, {"deployment_tier": to_tier, "updated_at": now})
        updated_count += 1

    # Update deployment record
    deployment = db.get_deployment(deployment_id)
    if deployment:
        logger.debug("Updating deployment record", extra={"deployment_id": deployment_id})
        db.update_deployment(deployment_id, {"status": to_tier, "promoted_at": now})

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
    return _response(200, {
        "action": "promote",
        "from_tier": from_tier,
        "to_tier": to_tier,
        "tenants_updated": updated_count,
        "deployment_id": deployment_id,
    })


def _rollback(from_tier: str, to_tier: str, deployment_id: str, start_time: float) -> dict:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Rollback: move tenants from to_tier back to from_tier
    logger.debug("Querying tenants for rollback", extra={"to_tier": to_tier})
    tenants = db.list_tenants_by_tier(to_tier)

    updated_count = 0
    for tenant in tenants:
        tid = tenant["tenant_id"]
        logger.debug("Rolling back tenant", extra={"tenant_id": tid, "from_tier": to_tier, "to_tier": from_tier})
        db.update_tenant(tid, {"deployment_tier": from_tier, "updated_at": now})
        updated_count += 1

    # Mark deployment as rolled back
    deployment = db.get_deployment(deployment_id)
    if deployment:
        logger.debug("Marking deployment as rolled_back", extra={"deployment_id": deployment_id})
        db.update_deployment(deployment_id, {"status": "rolled_back", "rolled_back_at": now})

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
    return _response(200, {
        "action": "rollback",
        "from_tier": to_tier,
        "to_tier": from_tier,
        "tenants_updated": updated_count,
        "deployment_id": deployment_id,
    })


def _parse_body(event: dict) -> dict | None:
    body = event.get("body", "") or ""
    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None


def _response(status_code: int, body: dict) -> dict:
    return {"statusCode": status_code, "body": json.dumps(body, default=str)}
