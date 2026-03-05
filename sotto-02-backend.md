## 6. Lambda Functions — Complete Specification

Each function must:
1. Use Powertools Logger, Tracer, and Metrics decorators on the handler
2. Log every significant step with tenant_id, agent_id, call_id in the extra dict
3. Never raise unhandled exceptions — always return a structured error response
4. Be idempotent where possible (safe to retry)

### 6.1 `ProviderWebhookHandler`

**File:** `src/handlers/webhooks/provider_webhook.py`
**Trigger:** `POST /webhooks/{provider}`
**Auth:** None (public endpoint, validate provider signature instead)
**Timeout:** 10s
**Memory:** 256MB

**Responsibilities:**
1. Identify provider from path parameter `{provider}`
2. Validate provider webhook signature (Twilio uses `X-Twilio-Signature`, RC uses HMAC, etc.)
3. Extract `tenant_id` from webhook metadata (Twilio: from the called number via DynamoDB lookup; others: from API key used to register webhook)
4. Instantiate the correct adapter class
5. Call `adapter.normalize(payload)` → returns `NormalizedCallEvent`
6. Push `NormalizedCallEvent` to SQS `sotto-call-events-{env}`
7. Return 200 immediately (Twilio requires fast response)

**Signature validation:**
- Twilio: Use `twilio.request_validator.RequestValidator` with auth token from Secrets Manager
- Others: HMAC-SHA256 validation using shared secret from Secrets Manager
- If validation fails: return 403, log warning with IP and payload

**Important:** This function must return 200 within 3 seconds or Twilio retries. Do all heavy work async via SQS.

**Environment variables:**
- `SQS_CALL_EVENTS_URL` — SQS queue URL
- `NUMBER_MAPPINGS_TABLE` — DynamoDB table name
- `TENANTS_TABLE` — DynamoDB table name

### 6.2 `RecordingProcessor`

**File:** `src/handlers/calls/recording_processor.py`
**Trigger:** SQS `sotto-call-events-{env}` (batch size: 1)
**Timeout:** 60s
**Memory:** 512MB

**Responsibilities:**
1. Deserialize `NormalizedCallEvent` from SQS message
2. Look up `tenant_id` and `number_mappings` to resolve `agent_id`
3. Create call record in DynamoDB `sotto-calls-{env}` with status `recording`
4. Download recording from provider URL (using credentials from Secrets Manager)
5. Upload recording to S3 at `{tenant_id}/recordings/{year}/{month}/{call_id}.{ext}`
6. Update call record: set `recording_s3_key`, status → `transcribing`
7. Push WebSocket notification to agent: `{event: "call_recorded", call_id: ...}`
8. Publish message to SQS `sotto-transcription-results-{env}` to trigger transcription

**If agent not found:** Log warning, still save recording, set `agent_id = null`. Do not fail.
**If recording download fails:** Retry via SQS visibility timeout. After DLQ, alert via SNS.

### 6.3 `TranscriptionInit`

**File:** `src/handlers/calls/transcription_init.py`
**Trigger:** SQS `sotto-call-events-{env}` (separate filter for transcription trigger) OR invoked directly by `RecordingProcessor`
**Timeout:** 30s

**Responsibilities:**
1. Start AWS Transcribe job for the S3 recording key
2. Configure job: `ShowSpeakerLabels=True`, `MaxSpeakerLabels=2`, `LanguageCode=en-US`
3. Set job name to `sotto-{call_id}` (must be unique)
4. Set output to S3: `{tenant_id}/transcripts/{year}/{month}/{call_id}.json`
5. Update call record: `transcript_status = in_progress`
6. AWS Transcribe will call EventBridge when complete — no polling needed

**AWS Transcribe job name format:** `sotto-{env}-{call_id}` (must match what `TranscriptionResultProcessor` expects)

### 6.4 `TranscriptionResultProcessor`

**File:** `src/handlers/calls/transcription_result.py`
**Trigger:** EventBridge rule matching `aws.transcribe` events with `TranscriptionJobStatus = COMPLETED or FAILED`
**Timeout:** 30s

**Responsibilities:**
1. Parse EventBridge event to get job name and status
2. Extract `call_id` from job name (`sotto-{env}-{call_id}`)
3. If FAILED: update call record `transcript_status = failed`, push WS notification
4. If COMPLETED:
   a. Read transcript JSON from S3
   b. Parse into structured format: list of `{speaker, text, start_time, end_time}`
   c. Update call record: `transcript_status = complete`, store transcript in call record or reference S3 key
   d. Update call `status = summarizing`
   e. Push WebSocket notification to agent: `{event: "transcript_ready", call_id, transcript: [...]}`
   f. Trigger `AISummarizer` asynchronously (invoke Lambda async, or SQS)

### 6.5 `AISummarizer`

**File:** `src/handlers/ai/summarizer.py`
**Trigger:** Async Lambda invocation from `TranscriptionResultProcessor`
**Timeout:** 60s
**Memory:** 512MB

**Responsibilities:**
1. Receive `call_id` and `tenant_id`
2. Read transcript from call record
3. Check feature flag `ai_summary` for tenant's `deployment_tier`
4. If flag disabled: skip, update status to `complete`, done
5. If flag enabled:
   a. Build prompt with transcript (see prompt template below)
   b. Call Bedrock `invoke_model` with `claude-haiku-4-5-20251001`
   c. Parse response: extract `summary` (2-3 sentences) and `action_items` (list)
   d. Update call record: `summary`, `action_items`, `status = complete`
   e. Push WebSocket notification: `{event: "summary_ready", call_id, summary, action_items}`

**Prompt template:**
```
You are an assistant for insurance agency staff. Given this call transcript, provide:
1. A 2-3 sentence summary of what was discussed
2. A list of specific action items (things the agent needs to follow up on)

Be concise. Focus on insurance-relevant details. Do not include pleasantries.

Transcript:
{transcript_text}

Respond in JSON: {"summary": "...", "action_items": ["...", "..."]}
```

**If Bedrock fails:** Log error, update status to `complete` anyway (transcript is still valuable without summary). Do not fail the call record.

### 6.6 `WSConnect`

**File:** `src/handlers/websocket/connect.py`
**Trigger:** WebSocket `$connect`
**Timeout:** 10s

**Responsibilities:**
1. Extract JWT from query param `?token={jwt}`
2. Validate JWT against Cognito User Pool (use `python-jose` or `cognitojwt`)
3. Extract `agent_id` and `tenant_id` from JWT claims
4. Store connection in `sotto-ws-connections-{env}`:
   - `connection_id` = API Gateway connection ID (from `event.requestContext.connectionId`)
   - `agent_id`, `tenant_id`, `connected_at`, `ttl` (now + 86400 seconds)
5. Return 200 to accept connection

**If JWT invalid:** Return 401 to reject connection (API Gateway closes it).

### 6.7 `WSDisconnect`

**File:** `src/handlers/websocket/disconnect.py`
**Trigger:** WebSocket `$disconnect`
**Timeout:** 10s

**Responsibilities:**
1. Delete record from `sotto-ws-connections-{env}` by `connection_id`
2. Return 200

### 6.8 `WSDefault`

**File:** `src/handlers/websocket/default.py`
**Trigger:** WebSocket `$default`
**Timeout:** 10s

**Responsibilities:**
1. Handle `{action: "ping"}` messages — respond with `{action: "pong"}`
2. Log unexpected messages as warnings
3. Return 200

### 6.9 `CallEventPublisher` (utility, called by other Lambdas)

**Not a standalone Lambda.** This is a utility function in the common layer: `sotto/ws_publisher.py`

```python
def push_to_agent(agent_id: str, tenant_id: str, event: dict, apigw_client) -> bool:
    """
    Find agent's WebSocket connection and send an event.
    Returns True if sent, False if agent not connected.
    """
```

Used by `RecordingProcessor`, `TranscriptionResultProcessor`, `AISummarizer`.

### 6.10 `AdminSignup`

**File:** `src/handlers/admin/signup.py`
**Trigger:** `POST /admin/signup` (no auth required)
**Timeout:** 15s

**Responsibilities:**
1. Validate input: `agency_name`, `admin_email`, `password`
2. Check `sotto-tenants-{env}` for existing email (via Cognito lookup)
3. Create Cognito user in Admin pool, add to `Admins` group
4. Create tenant record in DynamoDB
5. Return `{tenant_id, admin_cognito_sub, message}`

### 6.11 `AgentInvite`

**File:** `src/handlers/admin/agent_invite.py`
**Trigger:** `POST /admin/agents/invite` (Admin JWT required)
**Timeout:** 15s

**Responsibilities:**
1. Extract `tenant_id` from JWT claims
2. Validate input: `email`, `name`
3. Create agent record in DynamoDB with `status = invited`
4. Create Cognito user (temporary password), add to `Agents` group
5. Set Cognito custom attribute `custom:tenant_id` = tenant_id
6. Cognito sends invitation email with setup link (configure Cognito email template)
7. Return `{agent_id, status: "invited"}`

### 6.12 `NumberMappingHandler`

**File:** `src/handlers/admin/number_mapping.py`
**Trigger:** Various `/admin/numbers` routes
**Timeout:** 10s

**Responsibilities:**
- `GET /admin/numbers`: Query DynamoDB for all mappings for tenant
- `POST /admin/numbers`: Create mapping (`tenant_id`, `identifier`, `agent_id`, `identifier_type`, `label`)
- `PUT /admin/numbers/{id}`: Update mapping
- `DELETE /admin/numbers/{id}`: Delete mapping

For `POST`, validate that `agent_id` belongs to the same tenant.

### 6.13 `CallHistoryHandler`

**File:** `src/handlers/agents/call_history.py`
**Trigger:** `GET /calls` (Agent JWT) and `GET /admin/calls` (Admin JWT)
**Timeout:** 10s

**For agents:** Query GSI `agent-date-index` with `tenant_id#agent_id`, return last 50 calls.
**For admins:** Query calls table by `tenant_id`, return last 100 calls across all agents.

Response includes: `call_id`, `from_number`, `duration_sec`, `status`, `created_at`, `agent_name` (joined from agents table).

### 6.14 `NotesHandler`

**File:** `src/handlers/agents/notes.py`
**Trigger:** `PUT /calls/{call_id}/notes`
**Timeout:** 10s

**Responsibilities:**
1. Extract `tenant_id` and `agent_id` from JWT
2. Verify that the call belongs to this agent and tenant
3. UpdateItem: set `notes` and `notes_updated_at`
4. Return 200

### 6.15 `RolloutManager`

**File:** `src/handlers/admin/rollout_manager.py`
**Trigger:** `POST /internal/rollout` (IAM auth — called only by GitHub Actions)
**Timeout:** 30s

**Input:** `{action: "promote" | "rollback", target_tier: "beta" | "live_test" | "full", deployment_id: "uuid"}`

**For `promote`:**
1. Find all tenants with `deployment_tier` matching the source tier
2. Update them to the target tier in batch
3. Update deployment record status

**For `rollback`:**
1. Look up previous deployment
2. Revert all tenants in the target tier back to previous tier
3. Mark deployment as `rolled_back`

---

## 7. The Universal Adapter — Specification

### 7.1 Base Adapter

**File:** `src/layers/common/python/sotto/adapters/base.py`

```python
from abc import ABC, abstractmethod
from sotto.models import NormalizedCallEvent

class BaseAdapter(ABC):
    def __init__(self, tenant_id: str, secrets_client):
        self.tenant_id = tenant_id
        self.secrets = secrets_client

    @abstractmethod
    def validate_signature(self, headers: dict, body: str, url: str) -> bool:
        """Validate the webhook signature. Raise ValueError if invalid."""
        pass

    @abstractmethod
    def normalize(self, payload: dict) -> NormalizedCallEvent:
        """Convert provider-specific payload to NormalizedCallEvent."""
        pass

    @abstractmethod
    def is_call_ended(self, payload: dict) -> bool:
        """Return True only if this webhook represents a completed call with recording."""
        pass
```

### 7.2 NormalizedCallEvent Model

**File:** `src/layers/common/python/sotto/models.py`

```python
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class NormalizedCallEvent(BaseModel):
    tenant_id: str
    provider: str                    # twilio | ringcentral | zoom | teams | 8x8
    provider_call_id: str            # Provider's native call ID
    direction: str                   # inbound | outbound
    from_number: str                 # E.164 format
    to_identifier: str               # DID, extension, or email
    duration_sec: int
    recording_url: str               # URL to download recording from provider
    recording_format: str            # mp3 | wav | ogg
    ended_at: datetime
    raw_payload: dict                # Original payload for debugging
```

### 7.3 Twilio Adapter

**File:** `src/layers/common/python/sotto/adapters/twilio.py`

- Signature validation: Use `twilio.request_validator.RequestValidator`
- Trigger condition: `payload.get('CallStatus') == 'completed'` and `payload.get('RecordingSid')` exists
- `to_identifier`: Use `payload['To']` (the Twilio number that was called)
- `recording_url`: Construct as `https://api.twilio.com/2010-04-01/Accounts/{sid}/Recordings/{recording_sid}.mp3`
- Auth token for download: retrieve from Secrets Manager key `sotto/{tenant_id}/twilio_auth_token`

### 7.4 Adapter Registry

**File:** `src/handlers/webhooks/provider_webhook.py`

```python
ADAPTER_MAP = {
    'twilio': TwilioAdapter,
    'ringcentral': RingCentralAdapter,
    'zoom': ZoomAdapter,
    'teams': TeamsAdapter,
    '8x8': EightByEightAdapter,
}
```

---

