"""NumberMappingHandler — CRUD for /admin/numbers (Admin JWT required).

Maps phone numbers, extensions, emails, and SIP URIs to agents.
"""

import json
import time

from aws_lambda_powertools.utilities.typing import LambdaContext

from sotto import db
from sotto.logger import logger, metrics, tracer

VALID_IDENTIFIER_TYPES = {"did", "extension", "email", "sip"}


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
        logger.exception("Unhandled error in number mapping handler")
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"})
        return _response(500, {"error": "Internal server error"})


def _route(event: dict, start_time: float) -> dict:
    tenant_id = _extract_tenant_id(event)
    if not tenant_id:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "unauthorized"})
        return _response(401, {"error": "Missing tenant_id in claims"})

    route_key = event.get("routeKey", "")

    if route_key == "GET /admin/numbers":
        return _list_mappings(tenant_id, start_time)
    elif route_key == "POST /admin/numbers":
        return _create_mapping(event, tenant_id, start_time)
    elif route_key == "PUT /admin/numbers/{identifier}":
        return _update_mapping(event, tenant_id, start_time)
    elif route_key == "DELETE /admin/numbers/{identifier}":
        return _delete_mapping(event, tenant_id, start_time)

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
    return _response(400, {"error": f"Unknown route: {route_key}"})


def _list_mappings(tenant_id: str, start_time: float) -> dict:
    logger.debug("Listing number mappings", extra={"tenant_id": tenant_id})
    mappings = db.list_number_mappings(tenant_id)

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
    return _response(200, {"mappings": mappings})


def _create_mapping(event: dict, tenant_id: str, start_time: float) -> dict:
    body = _parse_body(event)
    if not body:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "Invalid JSON body"})

    identifier = body.get("identifier", "").strip()
    agent_id = body.get("agent_id", "").strip()
    identifier_type = body.get("identifier_type", "").strip()
    label = body.get("label", "").strip()

    if not identifier or not agent_id or not identifier_type or not label:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "identifier, agent_id, identifier_type, and label are required"})

    if identifier_type not in VALID_IDENTIFIER_TYPES:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": f"identifier_type must be one of: {sorted(VALID_IDENTIFIER_TYPES)}"})

    # Validate agent belongs to same tenant
    logger.debug("Validating agent ownership", extra={"tenant_id": tenant_id, "agent_id": agent_id})
    agent = db.get_agent(tenant_id, agent_id)
    if not agent:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "agent_id does not belong to this tenant"})

    # Check for existing mapping
    existing = db.get_number_mapping(tenant_id, identifier)
    if existing:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "conflict"})
        return _response(409, {"error": "Mapping already exists for this identifier"})

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    mapping_item = {
        "tenant_id": tenant_id,
        "identifier": identifier,
        "agent_id": agent_id,
        "identifier_type": identifier_type,
        "label": label,
        "created_at": now,
    }

    logger.debug("Creating number mapping", extra={"tenant_id": tenant_id, "identifier_type": identifier_type})
    db.put_number_mapping(mapping_item)

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
    return _response(201, mapping_item)


def _update_mapping(event: dict, tenant_id: str, start_time: float) -> dict:
    identifier = (event.get("pathParameters") or {}).get("identifier", "")
    if not identifier:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "identifier path parameter is required"})

    body = _parse_body(event)
    if not body:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "Invalid JSON body"})

    # Check mapping exists
    existing = db.get_number_mapping(tenant_id, identifier)
    if not existing:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "not_found"})
        return _response(404, {"error": "Mapping not found"})

    updates = {}
    if "label" in body:
        updates["label"] = body["label"].strip()
    if "agent_id" in body:
        new_agent_id = body["agent_id"].strip()
        # Validate new agent belongs to same tenant
        logger.debug("Validating new agent ownership", extra={"tenant_id": tenant_id, "agent_id": new_agent_id})
        agent = db.get_agent(tenant_id, new_agent_id)
        if not agent:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
            return _response(400, {"error": "agent_id does not belong to this tenant"})
        updates["agent_id"] = new_agent_id

    if not updates:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "No valid fields to update (label, agent_id)"})

    logger.debug("Updating number mapping", extra={"tenant_id": tenant_id, "identifier": identifier})
    updated = db.update_number_mapping(tenant_id, identifier, updates)

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
    return _response(200, updated)


def _delete_mapping(event: dict, tenant_id: str, start_time: float) -> dict:
    identifier = (event.get("pathParameters") or {}).get("identifier", "")
    if not identifier:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "identifier path parameter is required"})

    # Check mapping exists
    existing = db.get_number_mapping(tenant_id, identifier)
    if not existing:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "not_found"})
        return _response(404, {"error": "Mapping not found"})

    logger.debug("Deleting number mapping", extra={"tenant_id": tenant_id, "identifier": identifier})
    db.delete_number_mapping(tenant_id, identifier)

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
    return _response(200, {"message": "Mapping deleted"})


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
    return {"statusCode": status_code, "body": json.dumps(body, default=str)}
