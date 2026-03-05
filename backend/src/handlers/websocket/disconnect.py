"""WSDisconnect — handles WebSocket $disconnect route (Section 6.7).

Deletes the connection record from DynamoDB. Idempotent — does not fail
if record is already gone.
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
        connection_id = event.get("requestContext", {}).get("connectionId", "")

        logger.debug(
            "Deleting WS connection",
            extra={
                "table": "sotto-ws-connections",
                "connection_id": connection_id,
                "operation": "DeleteItem",
            },
        )
        db.delete_ws_connection(connection_id)

        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "ok"})
        return {"statusCode": 200}
    except Exception:
        logger.exception("Unhandled error in WSDisconnect")
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug("Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"})
        return {"statusCode": 200}  # Always return 200 for disconnect
