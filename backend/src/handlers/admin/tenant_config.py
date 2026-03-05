"""TenantConfigHandler — GET/PUT /admin/tenant (Admin JWT required).

Read or update tenant configuration.
"""

import json
import time

from aws_lambda_powertools.utilities.typing import LambdaContext

from sotto import db, secrets
from sotto.logger import logger, metrics, tracer

ALLOWED_UPDATE_FIELDS = {"agency_name", "provider_type", "twilio_account_sid", "twilio_phone_number"}
VALID_PROVIDERS = {"twilio", "ringcentral", "zoom", "teams", "8x8"}


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
        logger.exception("Unhandled error in tenant config handler")
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"})
        return _response(500, {"error": "Internal server error"})


def _route(event: dict, start_time: float) -> dict:
    route_key = event.get("routeKey", "")
    tenant_id = _extract_tenant_id(event)

    if not tenant_id:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "unauthorized"})
        return _response(401, {"error": "Missing tenant_id in claims"})

    if route_key == "GET /admin/tenant":
        return _get_tenant(tenant_id, start_time)
    elif route_key == "PUT /admin/tenant":
        return _update_tenant(event, tenant_id, start_time)

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
    return _response(400, {"error": f"Unknown route: {route_key}"})


def _get_tenant(tenant_id: str, start_time: float) -> dict:
    logger.debug("Fetching tenant", extra={"tenant_id": tenant_id})
    tenant = db.get_tenant(tenant_id)
    if not tenant:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "not_found"})
        return _response(404, {"error": "Tenant not found"})

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
    return _response(200, tenant)


def _update_tenant(event: dict, tenant_id: str, start_time: float) -> dict:
    body = _parse_body(event)
    if not body:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "Invalid JSON body"})

    updates = {k: v for k, v in body.items() if k in ALLOWED_UPDATE_FIELDS and v is not None}
    auth_token = body.get("twilio_auth_token")

    if not updates and not auth_token:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": f"No valid fields to update. Allowed: {sorted(ALLOWED_UPDATE_FIELDS | {'twilio_auth_token'})}"})

    if "provider_type" in updates and updates["provider_type"] not in VALID_PROVIDERS:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": f"Invalid provider_type. Must be one of: {sorted(VALID_PROVIDERS)}"})

    # Store auth token in Secrets Manager (never in DynamoDB)
    if auth_token:
        provider = updates.get("provider_type") or "twilio"
        account_sid = updates.get("twilio_account_sid") or ""
        logger.debug(
            "Storing provider credentials in Secrets Manager",
            extra={"tenant_id": tenant_id, "provider": provider},
        )
        secrets.put_provider_credentials(tenant_id, provider, {
            "auth_token": auth_token,
            "account_sid": account_sid,
        })

    if updates:
        updates["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        logger.debug("Updating tenant", extra={"tenant_id": tenant_id, "update_keys": list(updates.keys())})
        updated = db.update_tenant(tenant_id, updates)
    else:
        updated = db.get_tenant(tenant_id)

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
    return _response(200, updated)


def _extract_tenant_id(event: dict) -> str | None:
    claims = (event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {}))
    return claims.get("custom:tenant_id")


def _parse_body(event: dict) -> dict | None:
    body = event.get("body", "") or ""
    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None


def _response(status_code: int, body: dict) -> dict:
    return {"statusCode": status_code, "body": json.dumps(body)}
