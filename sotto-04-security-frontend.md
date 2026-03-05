## 11. Security Specification

### 11.1 IAM Roles

Each Lambda function has its own IAM role. Never share roles between functions. The SAM template defines inline policies scoped to exactly what each function needs.

**ProviderWebhookHandler role allows:**
- `dynamodb:GetItem` on number-mappings table (to resolve tenant from DID)
- `sqs:SendMessage` on call-events queue
- `secretsmanager:GetSecretValue` on `sotto/*/twilio_*` pattern
- `xray:PutTraceSegments`, `xray:PutTelemetryRecords`
- `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents`

**RecordingProcessor role allows:**
- Everything ProviderWebhookHandler allows, plus:
- `dynamodb:PutItem`, `dynamodb:UpdateItem` on calls table
- `dynamodb:GetItem` on agents table
- `s3:PutObject` on recordings bucket (scoped to `*/{tenant_id}/*` if possible, else bucket-level)
- `execute-api:ManageConnections` on WebSocket API (to push to agents)

**AISummarizer role allows:**
- `dynamodb:UpdateItem` on calls table
- `s3:GetObject` on recordings bucket
- `bedrock:InvokeModel` on `claude-haiku-4-5-20251001`
- `execute-api:ManageConnections` on WebSocket API

### 11.2 Cognito Configuration

**Admin User Pool:**
- Password policy: min 12 chars, require uppercase, lowercase, numbers, symbols
- MFA: optional (encourage but don't force at MVP)
- Custom attributes: `custom:tenant_id` (read-only after set)
- Groups: `Admins`
- Pre-token generation Lambda trigger: inject `tenant_id` and `role` into JWT claims

**Agent User Pool:**
- Same as Admin pool
- Groups: `Agents`
- Custom attributes: `custom:tenant_id`, `custom:agent_id`

**JWT Claims (injected by pre-token trigger):**
```json
{
  "sub": "cognito-user-uuid",
  "email": "agent@agency.com",
  "custom:tenant_id": "uuid-tenant",
  "custom:agent_id": "uuid-agent",
  "cognito:groups": ["Agents"]
}
```

### 11.3 Webhook Security

For Twilio webhooks, validate the `X-Twilio-Signature` header on every request. The validation requires:
- The full URL of the webhook endpoint (not just the path)
- The POST body parameters sorted
- The Twilio auth token from Secrets Manager

Do not process any webhook that fails signature validation. Return 403. Log a warning with provider, IP, and user-agent (not the payload).

### 11.4 Secrets Manager Key Structure

```
sotto/{tenant_id}/twilio_auth_token          — Twilio auth token
sotto/{tenant_id}/ringcentral_client_secret  — RC client secret
sotto/{tenant_id}/zoom_client_secret         — Zoom client secret
sotto/{tenant_id}/teams_client_secret        — Teams client secret
sotto/{tenant_id}/8x8_api_key                — 8x8 API key
sotto/system/cognito_client_secret           — Cognito app client secret
```

### 11.5 S3 Security

- All buckets: `BlockPublicAcls: true`, `BlockPublicPolicy: true`, `IgnorePublicAcls: true`, `RestrictPublicBuckets: true`
- Server-side encryption: `SSE-S3` (AES-256) on all buckets
- Lifecycle policies as specified in Section 4.8
- Pre-signed URLs for recording playback in the admin portal (15-minute expiry)

---

## 12. Chrome Extension — Specification

### 12.1 Manifest (MV3)

```json
{
  "manifest_version": 3,
  "name": "Cockpit — AI Call Intelligence",
  "version": "1.0.0",
  "permissions": ["sidePanel", "storage", "identity"],
  "host_permissions": [
    "https://*.useappliedepic.com/*",
    "https://*.sotto.io/*"
  ],
  "background": {
    "service_worker": "src/background/service_worker.js"
  },
  "side_panel": {
    "default_path": "src/sidepanel/index.html"
  },
  "content_scripts": [
    {
      "matches": ["https://*.useappliedepic.com/*"],
      "js": ["src/content/epic_injector.js"],
      "run_at": "document_idle"
    }
  ],
  "action": {
    "default_title": "Open Cockpit"
  }
}
```

### 12.2 Service Worker (Background)

The service worker maintains the WebSocket connection:

1. On install: open the side panel
2. On `chrome.action.onClicked`: toggle side panel
3. WebSocket connection:
   - Connect to `wss://{WS_API_URL}?token={cognito_jwt}`
   - On `call_recorded` event: notify side panel, ring animation
   - On `transcript_ready` event: send transcript to side panel
   - On `summary_ready` event: send summary to side panel
   - Reconnect on disconnect (exponential backoff: 1s, 2s, 4s, 8s, max 30s)
   - Keepalive: send `{action: "ping"}` every 5 minutes

4. Message passing: `chrome.runtime.sendMessage` to communicate with side panel

### 12.3 Side Panel (Cockpit)

States: `idle` → `ringing` → `active` → `ended` → `transcript_ready` → `complete`

**Idle state:** "Waiting for call..." with connection status indicator.

**Ringing:** Animate, show caller number, show matched client (if found via Epic injector).

**Active:** Show call timer. Show "End of call will appear here" placeholder.

**Transcript ready:** Render transcript lines. Enable notes textarea. Enable Save button.

**Complete:** Show summary (if enabled for tenant tier). Show action items. Saved confirmation.

### 12.4 Epic DOM Injector (Content Script)

The content script runs on Applied Epic pages and:

1. Watches for DOM mutations (client records loading)
2. When a client record is detected, extract the client name and phone numbers
3. Send extracted data to background service worker: `chrome.runtime.sendMessage({type: "epic_client_data", ...})`
4. When a call comes in: inject a clickable phone button into the Epic UI
5. Phone button click: log the call initiation (future: launch call via provider API)

**DOM selectors for Applied Epic** (these will need maintenance as Epic updates):
- Client name: look for `[data-field="ClientName"]` or `h1.client-name` (inspect and document actual selectors)
- Phone numbers: look for `[data-field="Phone"]` elements

---

## 13. Admin Portal — Specification

### 13.1 Routes

```
/                    → redirect to /login or /dashboard
/login               → Cognito hosted UI or custom login form
/signup              → New agency signup form
/dashboard           → Overview: call stats, recent calls
/agents              → List agents, invite new agent, deactivate agent
/numbers             → Number-to-agent mappings (the critical routing config)
/calls               → Full call history with search/filter
/calls/:call_id      → Single call detail: recording player, transcript, notes
/settings            → Tenant config: provider connection, billing
```

### 13.2 API Client

Use Cognito Amplify JS or manual JWT management. Every API request must include:
```
Authorization: Bearer {cognito_id_token}
```

Token refresh: auto-refresh using Cognito refresh token before expiry.

### 13.3 Key UX Requirements

- **Number mappings page:** This is the most important setup page. Show a table of all numbers/extensions from the connected provider, with a dropdown to assign each to an agent. Changes save immediately (no "save all" button).
- **Call history:** Show call status clearly (`recording`, `transcribing`, `complete`, `failed`). Auto-refresh every 30 seconds. Click a row to open call detail.
- **Call detail:** Show recording audio player, transcript (speaker-attributed), AI summary, action items, notes textarea with auto-save on blur.

---

## 14. Local Development Setup

### 14.1 Prerequisites

```bash
# Required tools
brew install aws-sam-cli
brew install awscli
pip install aws-lambda-powertools[all] boto3 pydantic pytest ruff
npm install -g @aws-amplify/cli
```

### 14.2 Local Environment

Create `backend/.env.local` (never commit this file):

```env
AWS_PROFILE=sotto-dev
AWS_REGION=ca-central-1
ENVIRONMENT=local
LOG_LEVEL=DEBUG
TENANTS_TABLE=sotto-tenants-dev
AGENTS_TABLE=sotto-agents-dev
CALLS_TABLE=sotto-calls-dev
NUMBER_MAPPINGS_TABLE=sotto-number-mappings-dev
WS_CONNECTIONS_TABLE=sotto-ws-connections-dev
FEATURE_FLAGS_TABLE=sotto-feature-flags-dev
SQS_CALL_EVENTS_URL=https://sqs.ca-central-1.amazonaws.com/{account}/sotto-call-events-dev
```

### 14.3 Makefile Commands

```makefile
.PHONY: build test lint local deploy-dev

build:
	sam build --template backend/template.yaml --use-container

test:
	pytest backend/src/tests/ -v --tb=short

lint:
	ruff check backend/src/
	ruff format --check backend/src/

local:
	sam local start-api --template backend/template.yaml --env-vars backend/.env.local

deploy-dev:
	sam deploy --stack-name sotto-dev --guided

logs-webhook:
	aws logs tail /aws/lambda/sotto-ProviderWebhookHandler-dev --follow
```

### 14.4 Testing the Webhook Locally

Use the Twilio CLI to forward webhooks to localhost:
```bash
twilio phone-numbers:update +15551234567 --voice-url http://localhost:3000/webhooks/twilio
```

Or use ngrok:
```bash
ngrok http 3000
# Then update Twilio webhook URL to the ngrok URL
```

---

