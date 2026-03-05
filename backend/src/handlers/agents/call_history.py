"""CallHistoryHandler — GET /calls (Agent) and GET /admin/calls (Admin).

Returns recent calls scoped by JWT role:
- Agents see only their own calls (agent-date-index GSI, limit 50).
- Admins see all tenant calls (calls table PK query, limit 100).
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
        return _route(event, start_time)
    except Exception:
        logger.exception("Unhandled error in call history handler")
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"})
        return _response(500, {"error": "Internal server error"})


def _route(event: dict, start_time: float) -> dict:
    claims = _extract_claims(event)
    tenant_id = claims.get("custom:tenant_id")

    if not tenant_id:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "unauthorized"})
        return _response(401, {"error": "Missing tenant_id in claims"})

    route_key = event.get("routeKey", "")
    groups = claims.get("cognito:groups", [])
    if isinstance(groups, str):
        groups = [groups]

    logger.debug("Routing call history", extra={"route_key": route_key, "groups": groups, "tenant_id": tenant_id})

    if route_key == "GET /admin/calls":
        if "Admins" not in groups:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "forbidden"})
            return _response(403, {"error": "Admin access required"})
        return _admin_calls(tenant_id, start_time)

    if route_key == "GET /calls":
        agent_id = claims.get("custom:agent_id")
        if not agent_id:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "unauthorized"})
            return _response(401, {"error": "Missing agent_id in claims"})
        return _agent_calls(tenant_id, agent_id, start_time)

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
    return _response(400, {"error": f"Unknown route: {route_key}"})


def _agent_calls(tenant_id: str, agent_id: str, start_time: float) -> dict:
    logger.debug("Querying agent calls", extra={"tenant_id": tenant_id, "agent_id": agent_id})
    calls = db.query_calls_by_agent(tenant_id, agent_id, limit=50)

    items = [_project_call(c) for c in calls]

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok", "count": len(items)})
    return _response(200, {"calls": items})


def _admin_calls(tenant_id: str, start_time: float) -> dict:
    logger.debug("Querying admin calls", extra={"tenant_id": tenant_id})
    calls = db.query_calls_by_tenant(tenant_id, limit=100)

    items = [_project_call(c) for c in calls]

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok", "count": len(items)})
    return _response(200, {"calls": items})


def _project_call(call: dict) -> dict:
    return {
        "call_id": call.get("call_id"),
        "from_number": call.get("from_number"),
        "duration_sec": call.get("duration_sec"),
        "status": call.get("status"),
        "created_at": call.get("created_at"),
        "agent_id": call.get("agent_id"),
    }


def _extract_claims(event: dict) -> dict:
    return event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})


def _response(status_code: int, body: dict) -> dict:
    return {"statusCode": status_code, "body": json.dumps(body)}
