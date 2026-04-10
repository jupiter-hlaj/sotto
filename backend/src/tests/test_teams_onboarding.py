"""Unit tests for Teams onboarding flow (M2 / T-3).

Covers both:
  - `handlers.teams.graph_client` — Microsoft Graph API wrapper (spec §4.1–§4.3)
  - `handlers.teams.onboarding`   — TeamsOnboarding Lambda handler (spec §4.4)

Follows the same conventions as `test_provider_webhook.py`:
  - `unittest.mock.patch` (no moto)
  - env vars set at module scope before importing the handler
  - a lightweight `MockContext` stand-in for the Lambda runtime context
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

# Set env vars before importing the handler (Powertools reads some at import time).
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "sotto")
os.environ.setdefault("POWERTOOLS_METRICS_NAMESPACE", "Sotto")
os.environ.setdefault("PORTAL_BASE_URL", "https://portal-dev.sotto.cloud")

from handlers.teams import graph_client  # noqa: E402
from handlers.teams.graph_client import GraphClientError  # noqa: E402
from handlers.teams.onboarding import handler  # noqa: E402


# ── Shared fixtures ──────────────────────────────────────────────


class MockContext:
    function_name = "sotto-teams-onboarding-dev"
    function_version = "$LATEST"
    invoked_function_arn = (
        "arn:aws:lambda:us-east-1:123456789012:function:sotto-teams-onboarding-dev"
    )
    aws_request_id = "test-request-id"
    memory_limit_in_mb = 256

    def get_remaining_time_in_millis(self):
        return 25000


FAKE_AZURE_CREDS = {
    "app_client_id": "fake-azure-app-client-id",
    "app_client_secret": "fake-azure-app-client-secret",
}

SOTTO_TENANT_ID = "tenant-001"
MS_TENANT_ID = "ms-tenant-abc-123"
FAKE_POLICY_ID = "policy-xyz"
FAKE_ACCESS_TOKEN = "eyJ0-fake-jwt"

MOCK_TENANT_RECORD = {
    "tenant_id": SOTTO_TENANT_ID,
    "status": "active",
    "provider_type": "teams",
}


def _make_callback_event(state=SOTTO_TENANT_ID, ms_tenant=MS_TENANT_ID, admin_consent="True"):
    """Build an HttpApi v2 event that mimics Microsoft's adminconsent callback."""
    query = {}
    if state is not None:
        query["state"] = state
    if ms_tenant is not None:
        query["tenant"] = ms_tenant
    if admin_consent is not None:
        query["admin_consent"] = admin_consent

    return {
        "version": "2.0",
        "routeKey": "GET /teams/oauth/callback",
        "rawPath": "/teams/oauth/callback",
        "rawQueryString": "",
        "headers": {},
        "queryStringParameters": query or None,
        "requestContext": {
            "requestId": "test-request-id",
            "domainName": "api-dev.sotto.cloud",
            "http": {
                "method": "GET",
                "path": "/teams/oauth/callback",
                "sourceIp": "1.2.3.4",
            },
        },
        "isBase64Encoded": False,
    }


# ── graph_client tests ───────────────────────────────────────────


@patch("handlers.teams.graph_client.secrets.get_azure_app_credentials", return_value=FAKE_AZURE_CREDS)
@patch("handlers.teams.graph_client.requests.post")
def test_get_access_token_success(mock_post, mock_creds):
    """client_credentials grant returns 200 → token extracted from response body."""
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = {
        "token_type": "Bearer",
        "expires_in": 3599,
        "access_token": FAKE_ACCESS_TOKEN,
    }
    mock_post.return_value = mock_response

    token = graph_client.get_access_token(MS_TENANT_ID)

    assert token == FAKE_ACCESS_TOKEN
    # Verify URL included the correct MS tenant ID.
    call = mock_post.call_args
    assert MS_TENANT_ID in call.args[0]
    # Verify body contained grant_type and client_credentials.
    sent_body = call.kwargs["data"]
    assert sent_body["grant_type"] == "client_credentials"
    assert sent_body["client_id"] == FAKE_AZURE_CREDS["app_client_id"]
    assert sent_body["client_secret"] == FAKE_AZURE_CREDS["app_client_secret"]
    assert sent_body["scope"] == "https://graph.microsoft.com/.default"


@patch("handlers.teams.graph_client.secrets.get_azure_app_credentials", return_value=FAKE_AZURE_CREDS)
@patch("handlers.teams.graph_client.requests.post")
def test_get_access_token_non_200_raises(mock_post, mock_creds):
    """Non-200 response from token endpoint → GraphClientError."""
    mock_post.return_value = MagicMock(status_code=401, text="unauthorized")

    with pytest.raises(GraphClientError) as exc_info:
        graph_client.get_access_token(MS_TENANT_ID)

    assert exc_info.value.status_code == 401
    assert exc_info.value.operation == "get_access_token"


@patch("handlers.teams.graph_client.secrets.get_azure_app_credentials", return_value=FAKE_AZURE_CREDS)
@patch("handlers.teams.graph_client.requests.post")
def test_create_policy_required_during_call_is_false(mock_post, mock_creds):
    """Critical: requiredDuringCall MUST be False (spec §4.2).

    This is the safety flag that prevents Teams from terminating live customer
    calls when the bot has a transient failure. If this test ever fails it is
    almost certainly a regression that would cause catastrophic production
    incidents on any insurance agency using the product.
    """
    mock_response = MagicMock(status_code=201)
    mock_response.json.return_value = {"id": FAKE_POLICY_ID, "displayName": "Sotto Call Recording"}
    mock_post.return_value = mock_response

    policy_id = graph_client.create_compliance_recording_policy(FAKE_ACCESS_TOKEN)

    assert policy_id == FAKE_POLICY_ID
    payload = mock_post.call_args.kwargs["json"]
    assert payload["complianceRecordingApplications"][0]["requiredDuringCall"] is False
    assert payload["complianceRecordingApplications"][0]["requiredBeforeCallEstablishment"] is False
    assert payload["enabled"] is True
    # Auth header must carry the token.
    assert mock_post.call_args.kwargs["headers"]["Authorization"] == f"Bearer {FAKE_ACCESS_TOKEN}"


@patch("handlers.teams.graph_client.secrets.get_azure_app_credentials", return_value=FAKE_AZURE_CREDS)
@patch("handlers.teams.graph_client.requests.post")
def test_create_policy_non_success_raises(mock_post, mock_creds):
    mock_post.return_value = MagicMock(status_code=403, text="insufficient privileges")

    with pytest.raises(GraphClientError) as exc_info:
        graph_client.create_compliance_recording_policy(FAKE_ACCESS_TOKEN)

    assert exc_info.value.status_code == 403
    assert exc_info.value.operation == "create_compliance_recording_policy"


@patch("handlers.teams.graph_client.requests.post")
def test_assign_policy_to_user_success(mock_post):
    mock_post.return_value = MagicMock(status_code=200)

    graph_client.assign_policy_to_user(FAKE_ACCESS_TOKEN, "ms-user-001")

    # URL must include the user ID.
    call = mock_post.call_args
    assert "ms-user-001" in call.args[0]
    assert call.kwargs["json"] == {"policyName": "Sotto Call Recording"}


@patch("handlers.teams.graph_client.requests.post")
def test_assign_policy_to_user_failure_raises(mock_post):
    mock_post.return_value = MagicMock(status_code=404, text="user not found")

    with pytest.raises(GraphClientError) as exc_info:
        graph_client.assign_policy_to_user(FAKE_ACCESS_TOKEN, "missing-user")

    assert exc_info.value.status_code == 404


# ── onboarding handler tests ─────────────────────────────────────


@patch("handlers.teams.onboarding.db")
def test_missing_state_returns_400(mock_db):
    """No `state` param → 400, never touches DynamoDB."""
    result = handler(_make_callback_event(state=None), MockContext())

    assert result["statusCode"] == 400
    mock_db.get_tenant.assert_not_called()


@patch("handlers.teams.onboarding.db")
def test_unknown_state_returns_400(mock_db):
    """`state` does not match any tenant → 400 (CSRF protection)."""
    mock_db.get_tenant.return_value = None

    result = handler(_make_callback_event(), MockContext())

    assert result["statusCode"] == 400
    mock_db.update_tenant.assert_not_called()


@patch("handlers.teams.onboarding.db")
def test_admin_declined_redirects_to_error(mock_db):
    """admin_consent=False → 302 redirect with reason=declined, no Graph calls."""
    mock_db.get_tenant.return_value = MOCK_TENANT_RECORD

    result = handler(_make_callback_event(admin_consent="False"), MockContext())

    assert result["statusCode"] == 302
    location = result["headers"]["Location"]
    assert "portal-dev.sotto.cloud/settings" in location
    assert "teams=error" in location
    assert "reason=declined" in location
    mock_db.update_tenant.assert_not_called()


@patch("handlers.teams.onboarding.db")
def test_admin_consent_missing_treated_as_declined(mock_db):
    mock_db.get_tenant.return_value = MOCK_TENANT_RECORD

    result = handler(_make_callback_event(admin_consent=None), MockContext())

    assert result["statusCode"] == 302
    assert "reason=declined" in result["headers"]["Location"]


@patch("handlers.teams.onboarding.graph_client")
@patch("handlers.teams.onboarding.db")
def test_happy_path_two_assignable_agents(mock_db, mock_graph):
    """Full success: 2 agents have ms_user_id, both assigned, tenant marked connected."""
    mock_db.get_tenant.return_value = MOCK_TENANT_RECORD
    mock_db.list_agents.return_value = [
        {"agent_id": "a1", "ms_user_id": "ms-user-1", "status": "active"},
        {"agent_id": "a2", "ms_user_id": "ms-user-2", "status": "active"},
    ]
    mock_graph.get_access_token.return_value = FAKE_ACCESS_TOKEN
    mock_graph.create_compliance_recording_policy.return_value = FAKE_POLICY_ID

    result = handler(_make_callback_event(), MockContext())

    assert result["statusCode"] == 302
    location = result["headers"]["Location"]
    assert "teams=connected" in location
    assert "agents_assigned=2" in location
    assert "agents_total=2" in location
    assert "agents_failed=0" in location

    # Graph API was called correctly.
    mock_graph.get_access_function = None  # sanity: no such attribute
    mock_graph.get_access_token.assert_called_once_with(MS_TENANT_ID)
    mock_graph.create_compliance_recording_policy.assert_called_once_with(FAKE_ACCESS_TOKEN)
    assert mock_graph.assign_policy_to_user.call_count == 2

    # DB: tenant should be updated twice — once to record ms_tenant_id+start,
    # once to mark teams_enabled=True at the end.
    assert mock_db.update_tenant.call_count == 2
    final_update = mock_db.update_tenant.call_args_list[-1]
    final_updates_kwarg = final_update.args[1] if len(final_update.args) > 1 else final_update.kwargs.get("updates")
    # The update_tenant signature is (tenant_id, updates); access via positional.
    assert final_update.args[0] == SOTTO_TENANT_ID
    assert final_update.args[1]["teams_enabled"] is True
    assert final_update.args[1]["teams_policy_id"] == FAKE_POLICY_ID
    # Both agents marked assigned.
    assert mock_db.update_agent.call_count == 2


@patch("handlers.teams.onboarding.graph_client")
@patch("handlers.teams.onboarding.db")
def test_agents_without_ms_user_id_are_skipped(mock_db, mock_graph):
    """Agents without ms_user_id yet are not assigned (normal state at first connect)."""
    mock_db.get_tenant.return_value = MOCK_TENANT_RECORD
    mock_db.list_agents.return_value = [
        {"agent_id": "a1", "status": "active"},  # no ms_user_id
        {"agent_id": "a2", "ms_user_id": "ms-user-2"},
    ]
    mock_graph.get_access_token.return_value = FAKE_ACCESS_TOKEN
    mock_graph.create_compliance_recording_policy.return_value = FAKE_POLICY_ID

    result = handler(_make_callback_event(), MockContext())

    assert result["statusCode"] == 302
    location = result["headers"]["Location"]
    assert "agents_assigned=1" in location
    assert "agents_total=2" in location
    mock_graph.assign_policy_to_user.assert_called_once()


@patch("handlers.teams.onboarding.graph_client")
@patch("handlers.teams.onboarding.db")
def test_partial_assignment_failure_still_succeeds(mock_db, mock_graph):
    """If one agent assignment fails, tenant is still marked connected.

    Rationale: the tenant IS connected to Teams; individual agent assignments
    can be retried. Failing the whole onboarding on a single bad agent would
    leave the tenant in an unusable state.
    """
    mock_db.get_tenant.return_value = MOCK_TENANT_RECORD
    mock_db.list_agents.return_value = [
        {"agent_id": "a1", "ms_user_id": "ms-user-1"},
        {"agent_id": "a2", "ms_user_id": "ms-user-2"},
    ]
    mock_graph.get_access_token.return_value = FAKE_ACCESS_TOKEN
    mock_graph.create_compliance_recording_policy.return_value = FAKE_POLICY_ID
    # First agent OK, second agent fails.
    mock_graph.assign_policy_to_user.side_effect = [
        None,
        GraphClientError("assign_policy_to_user", 404, "user not found"),
    ]

    result = handler(_make_callback_event(), MockContext())

    assert result["statusCode"] == 302
    location = result["headers"]["Location"]
    assert "teams=connected" in location
    assert "agents_assigned=1" in location
    assert "agents_failed=1" in location

    # Tenant should still be marked teams_enabled=True.
    final_update = mock_db.update_tenant.call_args_list[-1]
    assert final_update.args[1]["teams_enabled"] is True


@patch("handlers.teams.onboarding.graph_client")
@patch("handlers.teams.onboarding.db")
def test_token_acquisition_failure_redirects_to_error(mock_db, mock_graph):
    mock_db.get_tenant.return_value = MOCK_TENANT_RECORD
    mock_graph.get_access_token.side_effect = GraphClientError("get_access_token", 401, "bad secret")

    result = handler(_make_callback_event(), MockContext())

    assert result["statusCode"] == 302
    assert "reason=token_failed" in result["headers"]["Location"]
    mock_graph.create_compliance_recording_policy.assert_not_called()


@patch("handlers.teams.onboarding.graph_client")
@patch("handlers.teams.onboarding.db")
def test_policy_creation_failure_redirects_to_error(mock_db, mock_graph):
    mock_db.get_tenant.return_value = MOCK_TENANT_RECORD
    mock_graph.get_access_token.return_value = FAKE_ACCESS_TOKEN
    mock_graph.create_compliance_recording_policy.side_effect = GraphClientError(
        "create_compliance_recording_policy", 403, "insufficient privileges"
    )

    result = handler(_make_callback_event(), MockContext())

    assert result["statusCode"] == 302
    assert "reason=policy_failed" in result["headers"]["Location"]
    mock_graph.assign_policy_to_user.assert_not_called()
    # Tenant should NOT be marked teams_enabled — only the initial ms_tenant_id write.
    for call in mock_db.update_tenant.call_args_list:
        assert "teams_enabled" not in call.args[1]
