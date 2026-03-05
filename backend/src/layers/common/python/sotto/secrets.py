"""Secrets Manager access — never store secrets in env vars or DynamoDB."""

import json

import boto3
from botocore.exceptions import ClientError

from sotto.logger import logger, tracer

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("secretsmanager")
    return _client


@tracer.capture_method
def get_provider_credentials(tenant_id: str, provider: str) -> dict:
    """Retrieve provider credentials from Secrets Manager.

    Key pattern: sotto/{tenant_id}/{provider}_auth_token
    Returns parsed JSON dict of credentials.
    """
    secret_name = f"sotto/{tenant_id}/{provider}_auth_token"
    logger.debug(
        "Fetching secret",
        extra={"secret_name": secret_name, "tenant_id": tenant_id, "provider": provider},
    )
    try:
        response = _get_client().get_secret_value(SecretId=secret_name)
        secret_string = response["SecretString"]
        logger.debug(
            "Secret retrieved",
            extra={"secret_name": secret_name, "tenant_id": tenant_id},
        )
        try:
            return json.loads(secret_string)
        except json.JSONDecodeError:
            return {"token": secret_string}
    except ClientError as exc:
        logger.exception(
            "Failed to retrieve secret",
            extra={"secret_name": secret_name, "tenant_id": tenant_id, "error": str(exc)},
        )
        raise


@tracer.capture_method
def put_provider_credentials(tenant_id: str, provider: str, credentials: dict) -> None:
    """Create or update provider credentials in Secrets Manager.

    Key pattern: sotto/{tenant_id}/{provider}_auth_token
    Creates the secret if it doesn't exist, updates if it does.
    """
    secret_name = f"sotto/{tenant_id}/{provider}_auth_token"
    secret_value = json.dumps(credentials)

    logger.debug(
        "Storing secret",
        extra={"secret_name": secret_name, "tenant_id": tenant_id, "provider": provider},
    )

    client = _get_client()
    try:
        client.create_secret(Name=secret_name, SecretString=secret_value)
        logger.debug(
            "Secret created",
            extra={"secret_name": secret_name, "tenant_id": tenant_id},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceExistsException":
            client.update_secret(SecretId=secret_name, SecretString=secret_value)
            logger.debug(
                "Secret updated",
                extra={"secret_name": secret_name, "tenant_id": tenant_id},
            )
        else:
            logger.exception(
                "Failed to store secret",
                extra={"secret_name": secret_name, "tenant_id": tenant_id, "error": str(exc)},
            )
            raise
