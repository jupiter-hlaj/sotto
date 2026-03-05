"""PreTokenGeneration — Cognito pre-token trigger.

Injects custom claims (tenant_id, agent_id, role) into JWT tokens.
"""

import time

from aws_lambda_powertools.utilities.typing import LambdaContext

from sotto import db
from sotto.logger import logger, metrics, tracer


@logger.inject_lambda_context(log_event=True)
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
        return _handle_pre_token(event, start_time)
    except Exception:
        logger.exception("Unhandled error in pre-token trigger")
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"})
        # Return event unchanged — don't block auth on errors
        return event


def _handle_pre_token(event: dict, start_time: float) -> dict:
    user_attributes = event.get("request", {}).get("userAttributes", {})
    cognito_sub = user_attributes.get("sub", "")
    groups = event.get("request", {}).get("groupConfiguration", {}).get("groupsToOverride", [])

    claims_to_add = {}

    # Determine role from group membership
    if "Admins" in groups:
        claims_to_add["custom:role"] = "Admin"
    elif "Agents" in groups:
        claims_to_add["custom:role"] = "Agent"

    # Try to get tenant_id and agent_id from the agent record
    if cognito_sub:
        logger.debug("Looking up agent by cognito_sub", extra={"cognito_sub": cognito_sub})
        agent = db.get_agent_by_cognito_sub(cognito_sub)

        if agent:
            claims_to_add["custom:tenant_id"] = agent["tenant_id"]
            claims_to_add["custom:agent_id"] = agent["agent_id"]
            logger.debug(
                "Agent found for token enrichment",
                extra={"tenant_id": agent["tenant_id"], "agent_id": agent["agent_id"]},
            )
        else:
            # Admin users may not have agent records — use custom:tenant_id from user attributes
            tenant_id = user_attributes.get("custom:tenant_id", "")
            if tenant_id:
                claims_to_add["custom:tenant_id"] = tenant_id
                logger.debug("Using tenant_id from user attributes", extra={"tenant_id": tenant_id})

    # Inject claims into the token
    event.setdefault("response", {})
    event["response"]["claimsOverrideDetails"] = {
        "claimsToAddOrOverride": claims_to_add,
    }

    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug(
        "Handler completed",
        extra={"duration_ms": duration_ms, "result_status": "ok", "claims_added": list(claims_to_add.keys())},
    )
    return event
