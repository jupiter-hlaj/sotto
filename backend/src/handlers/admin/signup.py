"""AdminSignup — POST /admin/signup (no auth).

Creates a new tenant: Cognito user in Admins group + DynamoDB tenant record.
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
        return _handle_signup(event, start_time)
    except Exception:
        logger.exception("Unhandled error in admin signup")
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"})
        return _response(500, {"error": "Internal server error"})


def _handle_signup(event: dict, start_time: float) -> dict:
    body = _parse_body(event)
    if not body:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "Invalid JSON body"})

    agency_name = body.get("agency_name", "").strip()
    admin_email = body.get("admin_email", "").strip().lower()
    password = body.get("password", "")

    if not agency_name or not admin_email or not password:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "agency_name, admin_email, and password are required"})

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", admin_email):
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "bad_request"})
        return _response(400, {"error": "Invalid email format"})

    cognito = _get_cognito()
    tenant_id = str(uuid.uuid4())
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Create Cognito user
    logger.debug("Creating Cognito user", extra={"admin_email": admin_email, "tenant_id": tenant_id})
    try:
        create_resp = cognito.admin_create_user(
            UserPoolId=USER_POOL_ID,
            Username=admin_email,
            UserAttributes=[
                {"Name": "email", "Value": admin_email},
                {"Name": "email_verified", "Value": "true"},
                {"Name": "custom:tenant_id", "Value": tenant_id},
                {"Name": "custom:role", "Value": "Admin"},
            ],
            MessageAction="SUPPRESS",
        )
    except cognito.exceptions.UsernameExistsException:
        logger.warning("Email already registered in Cognito", extra={"admin_email": admin_email})
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "conflict"})
        return _response(409, {"error": "Email already registered"})

    cognito_sub = None
    for attr in create_resp["User"]["Attributes"]:
        if attr["Name"] == "sub":
            cognito_sub = attr["Value"]
            break

    logger.debug("Cognito user created", extra={"cognito_sub": cognito_sub, "tenant_id": tenant_id})

    # Set permanent password
    logger.debug("Setting user password", extra={"tenant_id": tenant_id})
    cognito.admin_set_user_password(
        UserPoolId=USER_POOL_ID,
        Username=admin_email,
        Password=password,
        Permanent=True,
    )

    # Add to Admins group
    logger.debug("Adding user to Admins group", extra={"tenant_id": tenant_id})
    cognito.admin_add_user_to_group(
        UserPoolId=USER_POOL_ID,
        Username=admin_email,
        GroupName="Admins",
    )

    # Create tenant record
    tenant_item = {
        "tenant_id": tenant_id,
        "agency_name": agency_name,
        "admin_email": admin_email,
        "status": "active",
        "plan": "trial",
        "provider_type": "twilio",
        "deployment_tier": "full",
        "created_at": now,
        "updated_at": now,
    }
    logger.debug("Creating tenant record", extra={"tenant_id": tenant_id})
    db.put_tenant(tenant_item)

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
    return _response(201, {
        "tenant_id": tenant_id,
        "admin_cognito_sub": cognito_sub,
        "message": "Tenant created successfully",
    })


def _parse_body(event: dict) -> dict | None:
    body = event.get("body", "") or ""
    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None


def _response(status_code: int, body: dict) -> dict:
    return {"statusCode": status_code, "body": json.dumps(body)}
