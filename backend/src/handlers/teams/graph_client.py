"""Microsoft Graph API client — Teams Phone onboarding helpers.

Thin wrapper around the three Graph API calls needed by the TeamsOnboarding
Lambda (spec §4.1, §4.2, §4.3):

1. Acquire an access token via the client_credentials grant.
2. Create a ComplianceRecordingPolicy on the customer's Teams tenant.
3. Assign the policy to an individual user (per-agent, not tenant-wide).

This module is intentionally minimal — no in-memory token caching, no retry
logic beyond what `requests` does by default. The TeamsOnboarding Lambda is a
one-shot OAuth callback handler: it acquires a token once, does 1 + N Graph
calls in sequence, and returns a redirect. The C# bot container (T-4) has its
own token cache with per-tenant keys; that concern does NOT live here.

Never logs the access token, client_secret, or any full Graph response body.
Logs domain fields only (ms_tenant_id, ms_user_id, http status, duration_ms).
"""

import time

import requests

from sotto import secrets
from sotto.logger import logger, tracer

# Graph API endpoints — see spec §4.1, §4.2, §4.3.
_TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{ms_tenant_id}/oauth2/v2.0/token"
_POLICY_URL = "https://graph.microsoft.com/beta/teamwork/complianceRecordingPolicies"
_ASSIGN_URL_TEMPLATE = (
    "https://graph.microsoft.com/v1.0/users/{ms_user_id}/assignComplianceRecordingPolicy"
)

# Default policy display name — stored on the tenant record as teams_policy_id
# so we can locate it later for removal during off-boarding.
DEFAULT_POLICY_DISPLAY_NAME = "Sotto Call Recording"

# Timeouts (seconds). Lambda has a 30s budget total; leave headroom for the
# assignComplianceRecordingPolicy loop over N agents.
_TOKEN_TIMEOUT = 10
_GRAPH_TIMEOUT = 15


class GraphClientError(Exception):
    """Raised when a Graph API call returns a non-success status.

    Attributes:
        status_code: HTTP status code from Graph (or 0 for transport errors).
        operation: Short label for the failed operation (for logs).
        body: Response body text (truncated to 500 chars, may contain error details
              but never contains tokens or secrets).
    """

    def __init__(self, operation: str, status_code: int, body: str = ""):
        self.operation = operation
        self.status_code = status_code
        self.body = (body or "")[:500]
        super().__init__(f"{operation} failed: HTTP {status_code}")


@tracer.capture_method
def get_access_token(ms_tenant_id: str) -> str:
    """Fetch an access token for the given Microsoft 365 tenant via client_credentials.

    Uses the GLOBAL Sotto Azure AD app credentials from Secrets Manager (spec §3.5).
    No per-tenant secrets — the same app_client_id / app_client_secret work for every
    customer tenant because the app is a multi-tenant registration.

    Args:
        ms_tenant_id: The customer's Microsoft 365 tenant ID (GUID), received
            from the adminconsent callback and stored on the DynamoDB tenant record.

    Returns:
        The raw `access_token` string. Caller must pass it as `Bearer {token}`.

    Raises:
        GraphClientError: On non-200 response from the token endpoint.
        requests.RequestException: On transport failures.
    """
    app_creds = secrets.get_azure_app_credentials()
    token_url = _TOKEN_URL_TEMPLATE.format(ms_tenant_id=ms_tenant_id)

    logger.debug(
        "Requesting Graph access token",
        extra={"ms_tenant_id": ms_tenant_id, "grant_type": "client_credentials"},
    )
    start = time.time()

    response = requests.post(
        token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": app_creds["app_client_id"],
            "client_secret": app_creds["app_client_secret"],
            "scope": "https://graph.microsoft.com/.default",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=_TOKEN_TIMEOUT,
    )
    duration_ms = int((time.time() - start) * 1000)

    logger.debug(
        "Graph token response",
        extra={
            "ms_tenant_id": ms_tenant_id,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )

    if response.status_code != 200:
        # Body may contain error_description but never the secret we sent.
        raise GraphClientError("get_access_token", response.status_code, response.text)

    body = response.json()
    token = body.get("access_token")
    if not token:
        raise GraphClientError("get_access_token", 200, "missing access_token in response")
    return token


@tracer.capture_method
def create_compliance_recording_policy(
    access_token: str,
    display_name: str = DEFAULT_POLICY_DISPLAY_NAME,
) -> str:
    """Create a ComplianceRecordingPolicy on the customer's Teams tenant (spec §4.2).

    `requiredDuringCall` and `requiredBeforeCallEstablishment` are BOTH false —
    see spec §4.2 for the detailed rationale. Changing these would cause Teams
    to terminate live customer calls on any transient bot failure, which is
    unacceptable for a production insurance phone system.

    Args:
        access_token: Token from `get_access_token()`.
        display_name: Policy display name shown in Teams Admin Center. Defaults
            to the constant used by the onboarding handler; override only in tests.

    Returns:
        The `id` of the created policy, to be stored on the tenant record as
        `teams_policy_id`.

    Raises:
        GraphClientError: On non-success response from Graph.
    """
    app_creds = secrets.get_azure_app_credentials()

    payload = {
        "displayName": display_name,
        "enabled": True,
        "complianceRecordingApplications": [
            {
                "app": {"id": app_creds["app_client_id"]},
                "requiredDuringCall": False,
                "requiredBeforeCallEstablishment": False,
            }
        ],
    }

    logger.debug(
        "Creating Graph compliance recording policy",
        extra={"display_name": display_name},
    )
    start = time.time()

    response = requests.post(
        _POLICY_URL,
        json=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        timeout=_GRAPH_TIMEOUT,
    )
    duration_ms = int((time.time() - start) * 1000)

    logger.debug(
        "Graph create policy response",
        extra={
            "display_name": display_name,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )

    # Graph returns 201 Created for new policies; treat any 2xx as success
    # because the beta endpoint occasionally flips between 200 and 201.
    if response.status_code >= 300:
        raise GraphClientError("create_compliance_recording_policy", response.status_code, response.text)

    body = response.json()
    policy_id = body.get("id")
    if not policy_id:
        raise GraphClientError(
            "create_compliance_recording_policy", response.status_code, "missing id in response"
        )
    return policy_id


@tracer.capture_method
def assign_policy_to_user(
    access_token: str,
    ms_user_id: str,
    policy_name: str = DEFAULT_POLICY_DISPLAY_NAME,
) -> None:
    """Assign a ComplianceRecordingPolicy to a single Teams user (spec §4.3).

    Per-agent assignment (not tenant-wide) so that admins and back-office staff
    who do not take client calls are not recorded. The onboarding handler calls
    this in a loop, once per agent that has `ms_user_id` populated.

    Args:
        access_token: Token from `get_access_token()`.
        ms_user_id: The Microsoft user object ID (GUID) of the agent in their
            home Teams tenant. Stored on the DynamoDB agent record as `ms_user_id`.
        policy_name: The `displayName` of a previously created policy. Must match
            what was passed to `create_compliance_recording_policy`.

    Raises:
        GraphClientError: On non-success response from Graph.
    """
    url = _ASSIGN_URL_TEMPLATE.format(ms_user_id=ms_user_id)
    payload = {"policyName": policy_name}

    logger.debug(
        "Assigning Graph compliance recording policy to user",
        extra={"ms_user_id": ms_user_id, "policy_name": policy_name},
    )
    start = time.time()

    response = requests.post(
        url,
        json=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        timeout=_GRAPH_TIMEOUT,
    )
    duration_ms = int((time.time() - start) * 1000)

    logger.debug(
        "Graph assign policy response",
        extra={
            "ms_user_id": ms_user_id,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )

    # assignComplianceRecordingPolicy returns 200 OK on success with no body.
    if response.status_code >= 300:
        raise GraphClientError("assign_policy_to_user", response.status_code, response.text)
