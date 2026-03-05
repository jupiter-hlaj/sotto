"""WebSocket event publisher — utility for pushing events to agents (Section 6.9)."""

import json

from botocore.exceptions import ClientError

from sotto import db
from sotto.logger import logger, tracer


@tracer.capture_method
def push_to_agent(agent_id: str, tenant_id: str, event: dict, apigw_client) -> bool:
    """Find agent's WebSocket connection(s) and send an event.

    Returns True if sent to at least one connection, False if agent not connected.
    """
    logger.debug(
        "WS push_to_agent BEFORE",
        extra={"agent_id": agent_id, "tenant_id": tenant_id, "event_type": event.get("event")},
    )

    connections = db.get_ws_connections_for_agent(agent_id)
    if not connections:
        logger.debug(
            "Agent not connected — skipping WS push",
            extra={"agent_id": agent_id, "tenant_id": tenant_id},
        )
        return False

    payload = json.dumps(event).encode("utf-8")
    sent = False

    for conn in connections:
        connection_id = conn["connection_id"]
        logger.debug(
            "WS sending to connection",
            extra={"agent_id": agent_id, "connection_id": connection_id, "event_type": event.get("event")},
        )
        try:
            apigw_client.post_to_connection(
                ConnectionId=connection_id,
                Data=payload,
            )
            sent = True
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code == "GoneException":
                logger.debug(
                    "Stale WS connection — deleting",
                    extra={"connection_id": connection_id, "agent_id": agent_id},
                )
                db.delete_ws_connection(connection_id)
            else:
                logger.exception(
                    "WS push failed",
                    extra={"connection_id": connection_id, "agent_id": agent_id, "error": str(exc)},
                )

    logger.debug(
        "WS push_to_agent AFTER",
        extra={"agent_id": agent_id, "sent": sent, "connections_tried": len(connections)},
    )
    return sent
