"""WSDefault — handles WebSocket $default route (Section 6.8).

Responds to ping messages and logs unexpected actions.
"""

import json
import os
import time

import boto3
from aws_lambda_powertools.utilities.typing import LambdaContext

from sotto.logger import logger, metrics, tracer

_apigw_client = None
_env = os.environ.get("ENVIRONMENT", "dev")
WEBSOCKET_API_ENDPOINT = os.environ.get("WEBSOCKET_API_ENDPOINT", "")


def _get_apigw_client():
    global _apigw_client
    if _apigw_client is None:
        _apigw_client = boto3.client(
            "apigatewaymanagementapi",
            endpoint_url=WEBSOCKET_API_ENDPOINT,
        )
    return _apigw_client


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
        connection_id = event.get("requestContext", {}).get("connectionId", "")
        body_str = event.get("body", "") or ""

        # Parse body
        try:
            body = json.loads(body_str) if body_str else {}
        except json.JSONDecodeError:
            body = {}

        action = body.get("action", "")

        if action == "ping":
            logger.debug("Ping received, sending pong", extra={"connection_id": connection_id})
            _get_apigw_client().post_to_connection(
                ConnectionId=connection_id,
                Data=json.dumps({"action": "pong"}).encode("utf-8"),
            )
        else:
            logger.warning(
                "Unexpected WebSocket action",
                extra={"connection_id": connection_id, "action": action},
            )

        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
        return {"statusCode": 200}
    except Exception:
        logger.exception("Unhandled error in WSDefault")
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"})
        return {"statusCode": 200}
