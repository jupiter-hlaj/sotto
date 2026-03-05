"""DynamoDB client and helpers for every table operation."""

import os
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Attr, Key

from sotto.logger import logger, tracer

_env = os.environ.get("ENVIRONMENT", "dev")

# Table names — resolved from environment suffix
TENANTS_TABLE = f"sotto-tenants-{_env}"
AGENTS_TABLE = f"sotto-agents-{_env}"
NUMBER_MAPPINGS_TABLE = f"sotto-number-mappings-{_env}"
CALLS_TABLE = f"sotto-calls-{_env}"
WS_CONNECTIONS_TABLE = f"sotto-ws-connections-{_env}"
FEATURE_FLAGS_TABLE = f"sotto-feature-flags-{_env}"
DEPLOYMENTS_TABLE = f"sotto-deployments-{_env}"

_resource = None


def _get_resource():
    global _resource
    if _resource is None:
        _resource = boto3.resource("dynamodb")
    return _resource


def _table(name: str):
    return _get_resource().Table(name)


# ── Tenants ──────────────────────────────────────────────────

@tracer.capture_method
def get_tenant(tenant_id: str) -> Optional[dict]:
    logger.debug("DynamoDB get_tenant", extra={"table": TENANTS_TABLE, "tenant_id": tenant_id, "operation": "GetItem"})
    resp = _table(TENANTS_TABLE).get_item(Key={"tenant_id": tenant_id})
    return resp.get("Item")


@tracer.capture_method
def put_tenant(item: dict) -> None:
    logger.debug("DynamoDB put_tenant", extra={"table": TENANTS_TABLE, "tenant_id": item.get("tenant_id"), "operation": "PutItem"})
    _table(TENANTS_TABLE).put_item(Item=item)


@tracer.capture_method
def update_tenant(tenant_id: str, updates: dict) -> dict:
    expr_parts, attr_names, attr_values = [], {}, {}
    for i, (k, v) in enumerate(updates.items()):
        alias = f"#k{i}"
        placeholder = f":v{i}"
        expr_parts.append(f"{alias} = {placeholder}")
        attr_names[alias] = k
        attr_values[placeholder] = v
    update_expr = "SET " + ", ".join(expr_parts)
    logger.debug("DynamoDB update_tenant", extra={"table": TENANTS_TABLE, "tenant_id": tenant_id, "operation": "UpdateItem", "keys": list(updates.keys())})
    return _table(TENANTS_TABLE).update_item(
        Key={"tenant_id": tenant_id},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=attr_names,
        ExpressionAttributeValues=attr_values,
        ReturnValues="ALL_NEW",
    )["Attributes"]


@tracer.capture_method
def list_tenants_by_tier(deployment_tier: str) -> list[dict]:
    """Query status-index to find tenants, then filter by deployment_tier."""
    logger.debug("DynamoDB list_tenants_by_tier", extra={"table": TENANTS_TABLE, "deployment_tier": deployment_tier, "operation": "Scan"})
    resp = _table(TENANTS_TABLE).scan(
        FilterExpression=Key("deployment_tier").eq(deployment_tier),
    )
    return resp.get("Items", [])


# ── Agents ───────────────────────────────────────────────────

@tracer.capture_method
def get_agent(tenant_id: str, agent_id: str) -> Optional[dict]:
    logger.debug("DynamoDB get_agent", extra={"table": AGENTS_TABLE, "tenant_id": tenant_id, "agent_id": agent_id, "operation": "GetItem"})
    resp = _table(AGENTS_TABLE).get_item(Key={"tenant_id": tenant_id, "agent_id": agent_id})
    return resp.get("Item")


@tracer.capture_method
def put_agent(item: dict) -> None:
    logger.debug("DynamoDB put_agent", extra={"table": AGENTS_TABLE, "tenant_id": item.get("tenant_id"), "agent_id": item.get("agent_id"), "operation": "PutItem"})
    _table(AGENTS_TABLE).put_item(Item=item)


@tracer.capture_method
def update_agent(tenant_id: str, agent_id: str, updates: dict) -> dict:
    expr_parts, attr_names, attr_values = [], {}, {}
    for i, (k, v) in enumerate(updates.items()):
        alias = f"#k{i}"
        placeholder = f":v{i}"
        expr_parts.append(f"{alias} = {placeholder}")
        attr_names[alias] = k
        attr_values[placeholder] = v
    update_expr = "SET " + ", ".join(expr_parts)
    logger.debug("DynamoDB update_agent", extra={"table": AGENTS_TABLE, "tenant_id": tenant_id, "agent_id": agent_id, "operation": "UpdateItem"})
    return _table(AGENTS_TABLE).update_item(
        Key={"tenant_id": tenant_id, "agent_id": agent_id},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=attr_names,
        ExpressionAttributeValues=attr_values,
        ReturnValues="ALL_NEW",
    )["Attributes"]


@tracer.capture_method
def get_agent_by_email(email: str) -> Optional[dict]:
    logger.debug("DynamoDB get_agent_by_email", extra={"table": AGENTS_TABLE, "operation": "Query", "index": "email-index"})
    resp = _table(AGENTS_TABLE).query(
        IndexName="email-index",
        KeyConditionExpression=Key("email").eq(email),
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


@tracer.capture_method
def get_agent_by_cognito_sub(cognito_sub: str) -> Optional[dict]:
    logger.debug("DynamoDB get_agent_by_cognito_sub", extra={"table": AGENTS_TABLE, "operation": "Query", "index": "cognito-index"})
    resp = _table(AGENTS_TABLE).query(
        IndexName="cognito-index",
        KeyConditionExpression=Key("cognito_sub").eq(cognito_sub),
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


@tracer.capture_method
def list_agents(tenant_id: str) -> list[dict]:
    logger.debug("DynamoDB list_agents", extra={"table": AGENTS_TABLE, "tenant_id": tenant_id, "operation": "Query"})
    resp = _table(AGENTS_TABLE).query(
        KeyConditionExpression=Key("tenant_id").eq(tenant_id),
    )
    return resp.get("Items", [])


# ── Number Mappings ──────────────────────────────────────────

@tracer.capture_method
def get_number_mapping(tenant_id: str, identifier: str) -> Optional[dict]:
    logger.debug("DynamoDB get_number_mapping", extra={"table": NUMBER_MAPPINGS_TABLE, "tenant_id": tenant_id, "operation": "GetItem"})
    resp = _table(NUMBER_MAPPINGS_TABLE).get_item(Key={"tenant_id": tenant_id, "identifier": identifier})
    return resp.get("Item")


@tracer.capture_method
def put_number_mapping(item: dict) -> None:
    logger.debug("DynamoDB put_number_mapping", extra={"table": NUMBER_MAPPINGS_TABLE, "tenant_id": item.get("tenant_id"), "operation": "PutItem"})
    _table(NUMBER_MAPPINGS_TABLE).put_item(Item=item)


@tracer.capture_method
def update_number_mapping(tenant_id: str, identifier: str, updates: dict) -> dict:
    expr_parts, attr_names, attr_values = [], {}, {}
    for i, (k, v) in enumerate(updates.items()):
        alias = f"#k{i}"
        placeholder = f":v{i}"
        expr_parts.append(f"{alias} = {placeholder}")
        attr_names[alias] = k
        attr_values[placeholder] = v
    update_expr = "SET " + ", ".join(expr_parts)
    logger.debug("DynamoDB update_number_mapping", extra={"table": NUMBER_MAPPINGS_TABLE, "tenant_id": tenant_id, "operation": "UpdateItem"})
    return _table(NUMBER_MAPPINGS_TABLE).update_item(
        Key={"tenant_id": tenant_id, "identifier": identifier},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=attr_names,
        ExpressionAttributeValues=attr_values,
        ReturnValues="ALL_NEW",
    )["Attributes"]


@tracer.capture_method
def delete_number_mapping(tenant_id: str, identifier: str) -> None:
    logger.debug("DynamoDB delete_number_mapping", extra={"table": NUMBER_MAPPINGS_TABLE, "tenant_id": tenant_id, "operation": "DeleteItem"})
    _table(NUMBER_MAPPINGS_TABLE).delete_item(Key={"tenant_id": tenant_id, "identifier": identifier})


@tracer.capture_method
def scan_number_mapping_by_identifier(identifier: str) -> Optional[dict]:
    """Scan number mappings table to find a tenant by identifier (e.g. phone number).

    This performs a full table scan with a filter — acceptable for MVP with
    a small number of tenants.  A GSI on ``identifier`` would be better at scale.
    """
    logger.debug(
        "DynamoDB scan_number_mapping_by_identifier",
        extra={"table": NUMBER_MAPPINGS_TABLE, "operation": "Scan"},
    )
    resp = _table(NUMBER_MAPPINGS_TABLE).scan(
        FilterExpression=Attr("identifier").eq(identifier),
    )
    items = resp.get("Items", [])
    return items[0] if items else None


@tracer.capture_method
def list_number_mappings(tenant_id: str) -> list[dict]:
    logger.debug("DynamoDB list_number_mappings", extra={"table": NUMBER_MAPPINGS_TABLE, "tenant_id": tenant_id, "operation": "Query"})
    resp = _table(NUMBER_MAPPINGS_TABLE).query(
        KeyConditionExpression=Key("tenant_id").eq(tenant_id),
    )
    return resp.get("Items", [])


# ── Calls ────────────────────────────────────────────────────

@tracer.capture_method
def create_call(item: dict) -> None:
    # Set synthetic agent_date_key for GSI
    if item.get("tenant_id") and item.get("agent_id"):
        item["agent_date_key"] = f"{item['tenant_id']}#{item['agent_id']}"
    logger.debug("DynamoDB create_call", extra={"table": CALLS_TABLE, "tenant_id": item.get("tenant_id"), "call_id": item.get("call_id"), "operation": "PutItem"})
    _table(CALLS_TABLE).put_item(Item=item)


@tracer.capture_method
def update_call(tenant_id: str, call_id: str, updates: dict) -> dict:
    expr_parts, attr_names, attr_values = [], {}, {}
    for i, (k, v) in enumerate(updates.items()):
        alias = f"#k{i}"
        placeholder = f":v{i}"
        expr_parts.append(f"{alias} = {placeholder}")
        attr_names[alias] = k
        attr_values[placeholder] = v
    update_expr = "SET " + ", ".join(expr_parts)
    logger.debug("DynamoDB update_call", extra={"table": CALLS_TABLE, "tenant_id": tenant_id, "call_id": call_id, "operation": "UpdateItem", "keys": list(updates.keys())})
    return _table(CALLS_TABLE).update_item(
        Key={"tenant_id": tenant_id, "call_id": call_id},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=attr_names,
        ExpressionAttributeValues=attr_values,
        ReturnValues="ALL_NEW",
    )["Attributes"]


@tracer.capture_method
def get_call(tenant_id: str, call_id: str) -> Optional[dict]:
    logger.debug("DynamoDB get_call", extra={"table": CALLS_TABLE, "tenant_id": tenant_id, "call_id": call_id, "operation": "GetItem"})
    resp = _table(CALLS_TABLE).get_item(Key={"tenant_id": tenant_id, "call_id": call_id})
    return resp.get("Item")


@tracer.capture_method
def query_calls_by_agent(tenant_id: str, agent_id: str, limit: int = 50) -> list[dict]:
    agent_date_key = f"{tenant_id}#{agent_id}"
    logger.debug("DynamoDB query_calls_by_agent", extra={"table": CALLS_TABLE, "agent_date_key": agent_date_key, "operation": "Query", "index": "agent-date-index"})
    resp = _table(CALLS_TABLE).query(
        IndexName="agent-date-index",
        KeyConditionExpression=Key("agent_date_key").eq(agent_date_key),
        ScanIndexForward=False,
        Limit=limit,
    )
    return resp.get("Items", [])


@tracer.capture_method
def query_calls_by_tenant(tenant_id: str, limit: int = 100) -> list[dict]:
    logger.debug("DynamoDB query_calls_by_tenant", extra={"table": CALLS_TABLE, "tenant_id": tenant_id, "operation": "Query"})
    resp = _table(CALLS_TABLE).query(
        KeyConditionExpression=Key("tenant_id").eq(tenant_id),
        ScanIndexForward=False,
        Limit=limit,
    )
    return resp.get("Items", [])


# ── WebSocket Connections ────────────────────────────────────

@tracer.capture_method
def put_ws_connection(item: dict) -> None:
    logger.debug("DynamoDB put_ws_connection", extra={"table": WS_CONNECTIONS_TABLE, "connection_id": item.get("connection_id"), "agent_id": item.get("agent_id"), "operation": "PutItem"})
    _table(WS_CONNECTIONS_TABLE).put_item(Item=item)


@tracer.capture_method
def get_ws_connections_for_agent(agent_id: str) -> list[dict]:
    logger.debug("DynamoDB get_ws_connections_for_agent", extra={"table": WS_CONNECTIONS_TABLE, "agent_id": agent_id, "operation": "Query", "index": "agent-index"})
    resp = _table(WS_CONNECTIONS_TABLE).query(
        IndexName="agent-index",
        KeyConditionExpression=Key("agent_id").eq(agent_id),
    )
    return resp.get("Items", [])


@tracer.capture_method
def delete_ws_connection(connection_id: str) -> None:
    logger.debug("DynamoDB delete_ws_connection", extra={"table": WS_CONNECTIONS_TABLE, "connection_id": connection_id, "operation": "DeleteItem"})
    _table(WS_CONNECTIONS_TABLE).delete_item(Key={"connection_id": connection_id})


# ── Feature Flags ────────────────────────────────────────────

@tracer.capture_method
def get_feature_flag(flag_name: str) -> Optional[dict]:
    logger.debug("DynamoDB get_feature_flag", extra={"table": FEATURE_FLAGS_TABLE, "flag_name": flag_name, "operation": "GetItem"})
    resp = _table(FEATURE_FLAGS_TABLE).get_item(Key={"flag_name": flag_name})
    return resp.get("Item")


# ── Deployments ──────────────────────────────────────────────

@tracer.capture_method
def create_deployment(item: dict) -> None:
    logger.debug("DynamoDB create_deployment", extra={"table": DEPLOYMENTS_TABLE, "deployment_id": item.get("deployment_id"), "operation": "PutItem"})
    _table(DEPLOYMENTS_TABLE).put_item(Item=item)


@tracer.capture_method
def update_deployment(deployment_id: str, updates: dict) -> dict:
    expr_parts, attr_names, attr_values = [], {}, {}
    for i, (k, v) in enumerate(updates.items()):
        alias = f"#k{i}"
        placeholder = f":v{i}"
        expr_parts.append(f"{alias} = {placeholder}")
        attr_names[alias] = k
        attr_values[placeholder] = v
    update_expr = "SET " + ", ".join(expr_parts)
    logger.debug("DynamoDB update_deployment", extra={"table": DEPLOYMENTS_TABLE, "deployment_id": deployment_id, "operation": "UpdateItem"})
    return _table(DEPLOYMENTS_TABLE).update_item(
        Key={"deployment_id": deployment_id},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=attr_names,
        ExpressionAttributeValues=attr_values,
        ReturnValues="ALL_NEW",
    )["Attributes"]


@tracer.capture_method
def get_deployment(deployment_id: str) -> Optional[dict]:
    logger.debug("DynamoDB get_deployment", extra={"table": DEPLOYMENTS_TABLE, "deployment_id": deployment_id, "operation": "GetItem"})
    resp = _table(DEPLOYMENTS_TABLE).get_item(Key={"deployment_id": deployment_id})
    return resp.get("Item")
