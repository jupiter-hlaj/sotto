"""TeamsOnboarding — GET /teams/oauth/callback (spec §4.4).

Public OAuth callback. When a customer's Microsoft 365 Global Admin accepts
the adminconsent prompt, Microsoft redirects their browser here with:

    ?admin_consent=True&tenant={ms_tenant_id}&state={sotto_tenant_id}

This handler then:
  1. Validates the `state` parameter matches a real Sotto tenant (CSRF protection).
  2. Verifies `admin_consent=True`.
  3. Records `ms_tenant_id` on the tenant's DynamoDB record.
  4. Acquires a Graph API access token via client_credentials.
  5. Creates the ComplianceRecordingPolicy on the customer's Teams tenant.
  6. Assigns the policy to every agent that already has an `ms_user_id`.
  7. Marks the tenant `teams_enabled=true` and redirects the admin back to the
     portal with a success query string.

This is a one-shot interactive flow — the admin's browser is waiting for a
redirect. Total Lambda budget: 30s. Graph calls dominate the wall clock.

Agents without `ms_user_id` populated yet are simply skipped — they will be
assigned the policy later by the AgentConfirm flow once they sign into Sotto
and we learn their Microsoft user ID (see spec §7, handled in M3).

Failures during the per-agent assignment loop are counted but do NOT fail
the onboarding: the tenant is still connected, and we can retry failed agents
later. Fatal failures (token acquisition, policy creation) redirect the admin
back to the portal with an error query string instead of success.
"""

import json
import os
import time
from urllib.parse import urlencode

from aws_lambda_powertools.utilities.typing import LambdaContext

from handlers.teams import graph_client
from handlers.teams.graph_client import DEFAULT_POLICY_DISPLAY_NAME, GraphClientError
from sotto import db
from sotto.logger import logger, metrics, tracer

# Portal base URL — set per-environment in template.yaml (T-3c). Fallback to dev
# so local unit tests that don't set the env var still get a sensible value.
_PORTAL_BASE_URL = os.environ.get("PORTAL_BASE_URL", "https://portal-dev.sotto.cloud")


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
        return _handle_callback(event, start_time)
    except Exception:
        logger.exception("Unhandled error in Teams onboarding callback")
        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug(
            "Handler completed", extra={"duration_ms": duration_ms, "result_status": "error"}
        )
        # Can't redirect safely without a validated state, so return a plain 500.
        return _json_response(500, {"error": "Internal server error"})


def _handle_callback(event: dict, start_time: float) -> dict:
    query_params = event.get("queryStringParameters") or {}
    sotto_tenant_id = query_params.get("state", "")
    ms_tenant_id = query_params.get("tenant", "")
    admin_consent = query_params.get("admin_consent", "")

    # Log the branch decision without leaking the tenant IDs as secrets (they aren't,
    # but we still keep logs minimal).
    logger.debug(
        "Teams onboarding callback received",
        extra={
            "tenant_id": sotto_tenant_id,
            "ms_tenant_id": ms_tenant_id,
            "admin_consent": admin_consent,
        },
    )

    # ── Step 1: CSRF protection — validate state matches a known Sotto tenant ──
    if not sotto_tenant_id:
        logger.warning("Teams onboarding missing state param")
        return _finish(start_time, "missing_state", _json_response(400, {"error": "Missing state"}))

    tenant = db.get_tenant(sotto_tenant_id)
    if not tenant:
        logger.warning(
            "Teams onboarding state did not match a tenant",
            extra={"tenant_id": sotto_tenant_id},
        )
        return _finish(
            start_time, "invalid_state", _json_response(400, {"error": "Invalid state"})
        )

    # ── Step 2: Admin consent check ────────────────────────────────────────────
    # Microsoft sends the literal string "True" (title case). Normalize.
    if admin_consent.lower() != "true":
        logger.info(
            "Teams onboarding: admin declined consent",
            extra={"tenant_id": sotto_tenant_id, "admin_consent": admin_consent},
        )
        return _finish(
            start_time,
            "consent_declined",
            _redirect(_error_url("declined")),
        )

    if not ms_tenant_id:
        logger.warning(
            "Teams onboarding missing ms_tenant_id param",
            extra={"tenant_id": sotto_tenant_id},
        )
        return _finish(
            start_time,
            "missing_ms_tenant_id",
            _redirect(_error_url("missing_tenant")),
        )

    # ── Step 3: Persist ms_tenant_id on the tenant record (before Graph calls) ─
    # We persist BEFORE calling Graph so that if Graph fails we still know the
    # customer attempted onboarding and with which Microsoft tenant — useful for
    # retries and support debugging.
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    db.update_tenant(
        sotto_tenant_id,
        {
            "ms_tenant_id": ms_tenant_id,
            "teams_connect_started_at": now,
        },
    )

    # ── Step 4: Graph access token ─────────────────────────────────────────────
    # Catch broadly — on ANY failure here (Graph error, transport, etc.) we want
    # to redirect the admin back to the portal with a useful error, not surface
    # a raw 500. The exception is logged via logger.exception for diagnosis.
    try:
        access_token = graph_client.get_access_token(ms_tenant_id)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Teams onboarding: failed to acquire Graph token",
            extra={"tenant_id": sotto_tenant_id, "ms_tenant_id": ms_tenant_id},
        )
        return _finish(
            start_time, "token_failed", _redirect(_error_url("token_failed"))
        )

    # ── Step 5: Create the compliance recording policy ─────────────────────────
    try:
        policy_id = graph_client.create_compliance_recording_policy(access_token)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Teams onboarding: failed to create compliance recording policy",
            extra={"tenant_id": sotto_tenant_id, "ms_tenant_id": ms_tenant_id},
        )
        return _finish(
            start_time, "policy_failed", _redirect(_error_url("policy_failed"))
        )

    logger.debug(
        "Teams onboarding: policy created",
        extra={"tenant_id": sotto_tenant_id, "policy_id": policy_id},
    )

    # ── Step 6: Assign the policy to every agent with ms_user_id already set ──
    # Agents without ms_user_id (the normal state at first tenant connect) are
    # skipped — they'll be assigned later via the AgentConfirm flow in M3 when
    # they sign in and we learn their Microsoft user ID.
    agents = db.list_agents(sotto_tenant_id)
    total_agents = len(agents)
    assignable = [a for a in agents if a.get("ms_user_id")]
    assigned_ok = 0
    assign_failures = 0

    logger.debug(
        "Teams onboarding: starting agent policy assignment",
        extra={
            "tenant_id": sotto_tenant_id,
            "total_agents": total_agents,
            "assignable_agents": len(assignable),
        },
    )

    for agent in assignable:
        agent_id = agent["agent_id"]
        ms_user_id = agent["ms_user_id"]
        try:
            graph_client.assign_policy_to_user(access_token, ms_user_id)
            db.update_agent(
                sotto_tenant_id,
                agent_id,
                {"teams_policy_assigned": True, "teams_policy_assigned_at": now},
            )
            assigned_ok += 1
        except GraphClientError:
            logger.exception(
                "Teams onboarding: failed to assign policy to agent (continuing)",
                extra={
                    "tenant_id": sotto_tenant_id,
                    "agent_id": agent_id,
                    "ms_user_id": ms_user_id,
                },
            )
            assign_failures += 1

    # ── Step 7: Mark tenant as connected ──────────────────────────────────────
    db.update_tenant(
        sotto_tenant_id,
        {
            "teams_enabled": True,
            "teams_policy_id": policy_id,
            "teams_policy_name": DEFAULT_POLICY_DISPLAY_NAME,
            "teams_connected_at": now,
        },
    )

    logger.info(
        "Teams onboarding complete",
        extra={
            "tenant_id": sotto_tenant_id,
            "ms_tenant_id": ms_tenant_id,
            "policy_id": policy_id,
            "total_agents": total_agents,
            "assigned_ok": assigned_ok,
            "assign_failures": assign_failures,
        },
    )

    # ── Step 8: Redirect admin back to the portal settings page with success ──
    success_url = _PORTAL_BASE_URL.rstrip("/") + "/settings?" + urlencode(
        {
            "teams": "connected",
            "agents_assigned": assigned_ok,
            "agents_total": total_agents,
            "agents_failed": assign_failures,
        }
    )
    return _finish(start_time, "ok", _redirect(success_url))


# ── Helpers ─────────────────────────────────────────────────────────────────


def _error_url(reason: str) -> str:
    return _PORTAL_BASE_URL.rstrip("/") + "/settings?" + urlencode(
        {"teams": "error", "reason": reason}
    )


def _redirect(location: str) -> dict:
    return {"statusCode": 302, "headers": {"Location": location}, "body": ""}


def _json_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def _finish(start_time: float, result_status: str, response: dict) -> dict:
    duration_ms = int((time.time() - start_time) * 1000)
    logger.debug(
        "Handler completed",
        extra={"duration_ms": duration_ms, "result_status": result_status},
    )
    return response
