"""WSConnect — handles WebSocket $connect route (Section 6.6).

Validates JWT from query param, stores connection in DynamoDB.
Returns 200 to accept, 401 to reject.
"""

import json
import os
import time

import boto3
import requests as http_requests
from jose import JWTError, jwt
from aws_lambda_powertools.utilities.typing import LambdaContext

from sotto import db
from sotto.logger import logger, metrics, tracer

_env = os.environ.get("ENVIRONMENT", "dev")
_region = os.environ.get("AWS_REGION", "us-east-1")
_user_pool_id = os.environ.get("COGNITO_USER_POOL_ID", "")

# JWKS cache — reused across warm invocations
_jwks: dict | None = None


def _get_jwks() -> dict:
    global _jwks
    if _jwks is not None:
        return _jwks

    jwks_url = f"https://cognito-idp.{_region}.amazonaws.com/{_user_pool_id}/.well-known/jwks.json"
    logger.debug("Fetching JWKS BEFORE", extra={"jwks_url": jwks_url})
    fetch_start = time.time()
    resp = http_requests.get(jwks_url, timeout=5)
    resp.raise_for_status()
    _jwks = resp.json()
    fetch_duration_ms = int((time.time() - fetch_start) * 1000)
    logger.debug(
        "Fetching JWKS AFTER",
        extra={"duration_ms": fetch_duration_ms, "key_count": len(_jwks.get("keys", []))},
    )
    return _jwks


def _validate_token(token: str) -> dict:
    """Validate a Cognito JWT and return its claims.

    Raises JWTError or ValueError on invalid tokens.
    """
    jwks = _get_jwks()

    # Get the kid from the token header
    unverified_header = jwt.get_unverified_header(token)
    kid = unverified_header.get("kid")

    # Find the matching key
    rsa_key = None
    for key in jwks.get("keys", []):
        if key["kid"] == kid:
            rsa_key = key
            break

    if rsa_key is None:
        raise ValueError("No matching key found in JWKS")

    issuer = f"https://cognito-idp.{_region}.amazonaws.com/{_user_pool_id}"

    claims = jwt.decode(
        token,
        rsa_key,
        algorithms=["RS256"],
        issuer=issuer,
        options={"verify_aud": False},
    )
    return claims


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
        return _handle_connect(event, start_time)
    except Exception:
        logger.exception("Unhandled error in WSConnect")
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"})
        return {"statusCode": 500}


def _handle_connect(event: dict, start_time: float) -> dict:
    request_context = event.get("requestContext", {})
    connection_id = request_context.get("connectionId", "")

    # Extract JWT from query string
    query_params = event.get("queryStringParameters") or {}
    token = query_params.get("token", "")

    if not token:
        logger.warning("No token in query params", extra={"connection_id": connection_id})
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "unauthorized"})
        return {"statusCode": 401}

    # Validate JWT
    logger.debug("Validating JWT BEFORE", extra={"connection_id": connection_id})
    try:
        claims = _validate_token(token)
    except (JWTError, ValueError, Exception) as exc:
        logger.warning(
            "JWT validation failed",
            extra={"connection_id": connection_id, "error": type(exc).__name__},
        )
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "unauthorized"})
        return {"statusCode": 401}

    logger.debug("JWT validated", extra={"connection_id": connection_id})

    # Extract agent_id and tenant_id from claims
    agent_id = claims.get("custom:agent_id") or claims.get("sub", "")
    tenant_id = claims.get("custom:tenant_id", "")

    if not agent_id or not tenant_id:
        logger.warning(
            "Missing required claims in JWT",
            extra={"connection_id": connection_id, "has_sub": bool(agent_id), "has_tenant_id": bool(tenant_id)},
        )
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "unauthorized"})
        return {"statusCode": 401}

    # Store connection
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ttl = int(time.time()) + 86400  # 24 hours

    conn_item = {
        "connection_id": connection_id,
        "agent_id": agent_id,
        "tenant_id": tenant_id,
        "connected_at": now_iso,
        "ttl": ttl,
    }

    logger.debug(
        "Storing WS connection",
        extra={
            "table": "sotto-ws-connections",
            "connection_id": connection_id,
            "agent_id": agent_id,
            "tenant_id": tenant_id,
            "operation": "PutItem",
        },
    )
    db.put_ws_connection(conn_item)

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
    return {"statusCode": 200}
