"""AgentInvite — POST /admin/agents/invite (Admin JWT required).

Creates a new agent: Cognito user in Agents group + DynamoDB agent record.
"""

import json
import os
import re
import time
import uuid

import boto3
from aws_lambda_powertools.utilities.typing import LambdaContext

from sotto import db
from sotto.logger import logger, metrics, tracer

_cognito_client = None

USER_POOL_ID = os.environ.get("USER_POOL_ID", "")


def _get_cognito():
    global _cognito_client
    if _cognito_client is None:
        _cognito_client = boto3.client("cognito-idp")
    return _cognito_client


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
        route_key = event.get("routeKey", "")
        if route_key == "GET /admin/agents":
            return _handle_list(event, start_time)
        return _handle_invite(event, start_time)
    except Exception:
        logger.exception("Unhandled error in agent invite")
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"})
        return _response(500, {"error": "Internal server error"})


def _handle_list(event: dict, start_time: float) -> dict:
    tenant_id = _extract_tenant_id(event)
    if not tenant_id:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "unauthorized"})
        return _response(401, {"error": "Missing tenant_id in claims"})

    logger.debug("Listing agents", extra={"tenant_id": tenant_id})
    agents = db.list_agents(tenant_id)

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok", "count": len(agents)})
    return _response(200, {"agents": agents})


def _handle_invite(event: dict, start_time: float) -> dict:
    tenant_id = _extract_tenant_id(event)
    if not tenant_id:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "unauthorized"})
        return _response(401, {"error": "Missing tenant_id in claims"})

    body = _parse_body(event)
    if not body:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "Invalid JSON body"})

    email = body.get("email", "").strip().lower()
    name = body.get("name", "").strip()

    if not email or not name:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "email and name are required"})

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "Invalid email format"})

    # Check if agent already exists for this tenant
    existing = db.get_agent_by_email(email)
    if existing and existing.get("tenant_id") == tenant_id:
        logger.warning("Agent already exists", extra={"tenant_id": tenant_id, "email": email})
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "conflict"})
        return _response(409, {"error": "Agent with this email already exists for this tenant"})

    cognito = _get_cognito()
    agent_id = str(uuid.uuid4())
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Create Cognito user with temporary password (Cognito sends invite email)
    logger.debug("Creating Cognito user for agent", extra={"tenant_id": tenant_id, "agent_id": agent_id})
    try:
        cognito.admin_create_user(
            UserPoolId=USER_POOL_ID,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
                {"Name": "custom:tenant_id", "Value": tenant_id},
                {"Name": "custom:role", "Value": "Agent"},
            ],
            DesiredDeliveryMediums=["EMAIL"],
        )
    except cognito.exceptions.UsernameExistsException:
        logger.warning("Email already exists in Cognito", extra={"tenant_id": tenant_id, "email": email})
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "conflict"})
        return _response(409, {"error": "Email already registered in authentication system"})

    logger.debug("Cognito user created for agent", extra={"tenant_id": tenant_id, "agent_id": agent_id})

    # Add to Agents group
    logger.debug("Adding user to Agents group", extra={"tenant_id": tenant_id, "agent_id": agent_id})
    cognito.admin_add_user_to_group(
        UserPoolId=USER_POOL_ID,
        Username=email,
        GroupName="Agents",
    )

    # Create agent record in DynamoDB
    agent_item = {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "email": email,
        "name": name,
        "status": "invited",
        "created_at": now,
        "invited_at": now,
    }
    logger.debug("Creating agent record", extra={"tenant_id": tenant_id, "agent_id": agent_id})
    db.put_agent(agent_item)

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
    return _response(201, {"agent_id": agent_id, "status": "invited"})


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
