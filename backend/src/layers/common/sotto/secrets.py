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
def get_azure_app_credentials() -> dict:
    """Retrieve the GLOBAL Azure AD app credentials used by the Teams bot.

    Unlike provider credentials (which are per-tenant), the Azure app is a single
    multi-tenant registration owned by Sotto. The client_id and client_secret are
    the same for every customer; the per-customer ms_tenant_id is stored on the
    DynamoDB tenant record, not in Secrets Manager.

    Expected secret key pattern (bootstrap per environment, spec §3.5):
        sotto/azure/app_client_id      → plain string, the Azure app Application (client) ID
        sotto/azure/app_client_secret  → plain string, the Azure app client secret

    Returns:
        {"app_client_id": "...", "app_client_secret": "..."}

    Never logs the secret values. Raises ClientError on failure (callers re-raise).
    """
    client = _get_client()
    logger.debug(
        "Fetching Azure app credentials",
        extra={"secret_names": ["sotto/azure/app_client_id", "sotto/azure/app_client_secret"]},
    )
    try:
        id_resp = client.get_secret_value(SecretId="sotto/azure/app_client_id")
        secret_resp = client.get_secret_value(SecretId="sotto/azure/app_client_secret")
    except ClientError as exc:
        logger.exception(
            "Failed to retrieve Azure app credentials",
            extra={"error": str(exc)},
        )
        raise

    logger.debug("Azure app credentials retrieved")
    return {
        "app_client_id": id_resp["SecretString"],
        "app_client_secret": secret_resp["SecretString"],
    }


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
