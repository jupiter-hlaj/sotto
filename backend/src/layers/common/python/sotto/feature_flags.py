"""Feature flag evaluation — default-open, fail safe."""

from sotto.logger import logger, tracer


@tracer.capture_method
def is_enabled(flag_name: str, tenant_id: str, db_client) -> bool:
    """Check if a feature flag is enabled for a tenant's deployment tier.

    If the flag is not found in DynamoDB, logs a warning and returns True
    (default-open / fail safe).
    """
    logger.debug(
        "Checking feature flag",
        extra={"flag_name": flag_name, "tenant_id": tenant_id},
    )

    # Get the flag from DynamoDB
    flag = db_client.get_feature_flag(flag_name)
    if flag is None:
        logger.warning(
            "Feature flag not found — defaulting to enabled",
            extra={"flag_name": flag_name, "tenant_id": tenant_id},
        )
        return True

    # Get the tenant to find their deployment tier
    tenant = db_client.get_tenant(tenant_id)
    if tenant is None:
        logger.warning(
            "Tenant not found for feature flag check — defaulting to enabled",
            extra={"flag_name": flag_name, "tenant_id": tenant_id},
        )
        return True

    deployment_tier = tenant.get("deployment_tier", "")
    enabled_tiers = flag.get("enabled_tiers", [])
    result = deployment_tier in enabled_tiers

    logger.debug(
        "Feature flag evaluated",
        extra={
            "flag_name": flag_name,
            "tenant_id": tenant_id,
            "deployment_tier": deployment_tier,
            "enabled_tiers": enabled_tiers,
            "result": result,
        },
    )
    return result
