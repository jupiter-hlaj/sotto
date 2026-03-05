# Sotto — Session Playbook

## How Every Session Works

1. Open a fresh Claude conversation
2. Paste the docs listed for that step (copy the entire file contents)
3. Paste the exact prompt for that step
4. Claude builds. When done, move to the next step.

**Always paste docs BEFORE the prompt. Order matters.**

**Doc shorthand used in prompts:**
- "overview doc" → `sotto-00-overview.md`
- "infrastructure doc" → `sotto-01-infrastructure.md`
- "backend doc" → `sotto-02-backend.md`
- "logging doc" → `sotto-03-logging-cicd.md`
- "security/frontend doc" → `sotto-04-security-frontend.md`

---

---

## STEP 1 — Infrastructure Foundation

**Paste these docs first:**
- `sotto-00-overview.md`
- `sotto-01-infrastructure.md`

**Then paste this prompt:**

```
I am building Sotto — an AI call intelligence platform for insurance agencies.
I have pasted the overview doc and the infrastructure doc above.

Build the complete SAM infrastructure foundation. This means:
- backend/template.yaml containing all DynamoDB tables, S3 buckets, SQS queues
  (including DLQs), and Cognito User Pools exactly as specified in the docs.
  Do NOT add any Lambda functions yet — infrastructure only.
- backend/samconfig.toml configured for both dev and prod stacks.

Rules:
- Infrastructure only. No Lambda functions in this step.
- Resource names must be suffixed with the environment parameter (e.g. sotto-tenants-dev).
- All DynamoDB tables need the GSIs specified in the docs.
- PITR enabled on all tables in prod only.
- SQS DLQs must be created alongside their main queues.
- All S3 buckets must have public access blocked and SSE-S3 encryption.
- Do not invent anything not in the docs. Ask if anything is unclear.

When done, list every file created and confirm the next step is Step 2.
```

---

---

## STEP 2 — Common Layer

**Paste these docs first:**
- `sotto-00-overview.md`
- `sotto-01-infrastructure.md`
- `sotto-02-backend.md`
- `sotto-03-logging-cicd.md`

**Then paste this prompt:**

```
I am building Sotto — an AI call intelligence platform for insurance agencies.
I have pasted the overview doc, the infrastructure doc, the backend doc, and
the logging doc above.

Build the CommonLayer Python package. This is the shared code that every Lambda
function will use. Create all files under:
backend/src/layers/common/python/sotto/

Files to build:
- __init__.py
- logger.py — Powertools Logger, Tracer, Metrics setup as specified in Section 8
  of the logging doc. MVP logging is maximally verbose — follow Section 8.5 of
  the logging doc exactly.
- db.py — DynamoDB client with helper methods for every table operation described
  in the Lambda function specs (get_tenant, get_agent, create_call, update_call, etc.)
- s3.py — S3 client helpers for recording upload and transcript read/write
- secrets.py — Secrets Manager helper that retrieves provider credentials by tenant_id
- models.py — All Pydantic models: NormalizedCallEvent, Tenant, Agent, Call,
  NumberMapping, WSConnection, FeatureFlag, as described in the docs
- feature_flags.py — is_enabled(flag_name, tenant_id, db_client) function
- adapters/base.py — Abstract base adapter class
- adapters/twilio.py — Twilio webhook adapter (MVP — build this one fully)
- adapters/ringcentral.py — Stub only (raise NotImplementedError)
- adapters/zoom.py — Stub only
- adapters/teams.py — Stub only
- adapters/eightbyeight.py — Stub only
- requirements.txt for the layer

Also add the CommonLayer resource to backend/template.yaml.

Rules:
- Follow the MVP logging contract from Section 8.5 of the logging doc.
- Every function and method must log entry, decisions, and exit.
- Pydantic models must match the DynamoDB schemas exactly from the infrastructure doc.
- Do not build any Lambda handlers yet — layer only.
- Do not invent anything not in the docs. Ask if anything is unclear.

When done, list every file created and confirm the next step is Step 3.
```

---

---

## STEP 3 — Webhook Entry Point

**Paste these docs first:**
- `sotto-00-overview.md`
- `sotto-01-infrastructure.md`
- `sotto-02-backend.md`
- `sotto-03-logging-cicd.md`

**Then paste this prompt:**

```
I am building Sotto — an AI call intelligence platform for insurance agencies.
I have pasted the overview doc, the infrastructure doc, the backend doc, and
the logging doc above.
The CommonLayer from Step 2 already exists.

Build the ProviderWebhookHandler Lambda. This is the public entry point that
receives webhooks from all phone providers.

Files to build:
- backend/src/handlers/webhooks/provider_webhook.py

Also update backend/template.yaml to add:
- The ProviderWebhookHandler Lambda function with its IAM role
- The HTTP API Gateway with the POST /webhooks/{provider} route (public, no auth)
  as specified in Section 4.5 of the infrastructure doc
- The GET /health route pointing to a basic inline response

Rules:
- The handler must respond 200 within 3 seconds — push everything to SQS immediately.
- Twilio signature validation must be implemented using RequestValidator.
- If signature validation fails return 403 and log a warning (never log the payload).
- Use the ADAPTER_MAP pattern from the backend doc to select the correct adapter by provider.
- Follow the MVP logging contract from Section 8.5 of the logging doc.
- Log every step: entry, provider detected, signature validation result, adapter
  selected, SQS send, exit.
- Write unit tests in backend/src/tests/test_provider_webhook.py that mock
  a valid Twilio signature and assert the SQS message is sent correctly.
- Do not invent anything not in the docs. Ask if anything is unclear.

When done, list every file created or modified and confirm the next step is Step 4.
```

---

---

## STEP 4 — Call Processing Pipeline

**Paste these docs first:**
- `sotto-00-overview.md`
- `sotto-02-backend.md`
- `sotto-03-logging-cicd.md`

**Then paste this prompt:**

```
I am building Sotto — an AI call intelligence platform for insurance agencies.
I have pasted the overview doc, the backend doc, and the logging doc above.
Steps 1-3 are already built.

Build the call processing pipeline — three Lambda functions that take a call from
raw webhook event to completed transcript.

Files to build:
- backend/src/handlers/calls/recording_processor.py
- backend/src/handlers/calls/transcription_init.py
- backend/src/handlers/calls/transcription_result.py

Also update backend/template.yaml to add:
- RecordingProcessor Lambda with SQS trigger on sotto-call-events-{env}
- TranscriptionInit Lambda
- TranscriptionResultProcessor Lambda with EventBridge trigger for
  aws.transcribe events where TranscriptionJobStatus is COMPLETED or FAILED
- IAM roles for each function scoped exactly as described in the docs

Rules:
- RecordingProcessor streams large recordings directly to S3 using multipart
  upload — do not load the full file into memory.
- AWS Transcribe job names must follow the pattern sotto-{env}-{call_id}.
- TranscriptionResultProcessor extracts call_id from the job name.
- Each function must update the call record status at each pipeline stage.
- Each function must push a WebSocket notification using the ws_publisher
  utility from the common layer.
- Follow the MVP logging contract from Section 8.5 of the logging doc. Log every
  pipeline transition, every external API call before and after, every DynamoDB write.
- Handle failures gracefully — log with full context, re-raise so SQS retries.
- Do not build AISummarizer yet — that is Step 5.
- Do not invent anything not in the docs. Ask if anything is unclear.

When done, list every file created or modified and confirm the next step is Step 5.
```

---

---

## STEP 5 — AI Summarizer + Feature Flags

**Paste these docs first:**
- `sotto-00-overview.md`
- `sotto-02-backend.md`
- `sotto-03-logging-cicd.md`

**Then paste this prompt:**

```
I am building Sotto — an AI call intelligence platform for insurance agencies.
I have pasted the overview doc, the backend doc, and the logging doc above.
Steps 1-4 are already built.

Build the AISummarizer Lambda and seed the initial feature flags.

Files to build:
- backend/src/handlers/ai/summarizer.py
- backend/scripts/seed_feature_flags.py — a one-time script that seeds the
  initial feature flags into DynamoDB as specified in Section 10.4 of the
  logging doc

Also update backend/template.yaml to add:
- AISummarizer Lambda with its IAM role including bedrock:InvokeModel permission

Rules:
- AISummarizer must check the ai_summary feature flag before calling Bedrock.
  If the flag is disabled for the tenant's tier, skip summarization, set
  status to complete, and return — do not call Bedrock.
- Use the exact prompt template from the backend doc. Do not rewrite it.
- If Bedrock fails for any reason, log the error but still set the call status
  to complete — the transcript is still valuable without a summary.
- Use claude-haiku-4-5-20251001 via AWS Bedrock. Not claude-sonnet-4-6, not Opus.
- Parse the Bedrock JSON response into summary string and action_items list.
- Push a WebSocket notification when summary is ready.
- Follow the MVP logging contract from Section 8.5 of the logging doc exactly.
- Do not invent anything not in the docs. Ask if anything is unclear.

When done, list every file created or modified and confirm the next step is Step 6.
```

---

---

## STEP 6 — WebSocket Infrastructure

**Paste these docs first:**
- `sotto-00-overview.md`
- `sotto-01-infrastructure.md`
- `sotto-02-backend.md`
- `sotto-03-logging-cicd.md`

**Then paste this prompt:**

```
I am building Sotto — an AI call intelligence platform for insurance agencies.
I have pasted the overview doc, the infrastructure doc, the backend doc, and
the logging doc above.
Steps 1-5 are already built.

Build the WebSocket infrastructure — the real-time push system that activates
the agent's Cockpit when a call event occurs.

Files to build:
- backend/src/handlers/websocket/connect.py
- backend/src/handlers/websocket/disconnect.py
- backend/src/handlers/websocket/default.py
- backend/src/layers/common/python/sotto/ws_publisher.py — the push_to_agent
  utility used by RecordingProcessor, TranscriptionResultProcessor, AISummarizer

Also update backend/template.yaml to add:
- WebSocket API Gateway with $connect, $disconnect, $default routes as specified
  in Section 4.6 of the infrastructure doc
- WSConnect, WSDisconnect, WSDefault Lambda functions with IAM roles
- execute-api:ManageConnections permission on relevant Lambda roles

Rules:
- WSConnect must validate the JWT from the ?token= query parameter before
  accepting the connection. Return 401 to reject invalid tokens.
- WSConnect must set a TTL of 86400 seconds (24 hours) on the connection record.
- WSDisconnect must delete the connection record from DynamoDB.
- WSDefault must respond to {action: "ping"} with {action: "pong"}.
- ws_publisher.push_to_agent must handle the 410 Gone error gracefully —
  if a connection is stale, delete it from DynamoDB and log a warning.
- Follow the MVP logging contract from Section 8.5 of the logging doc exactly.
- Do not invent anything not in the docs. Ask if anything is unclear.

When done, list every file created or modified and confirm the next step is Step 7.
```

---

---

## STEP 7 — Admin API

**Paste these docs first:**
- `sotto-00-overview.md`
- `sotto-02-backend.md`
- `sotto-03-logging-cicd.md`

**Then paste this prompt:**

```
I am building Sotto — an AI call intelligence platform for insurance agencies.
I have pasted the overview doc, the backend doc, and the logging doc above.
Steps 1-6 are already built.

Build the Admin API — the endpoints used by agency admins to set up and
manage their account.

Files to build:
- backend/src/handlers/admin/signup.py
- backend/src/handlers/admin/tenant_config.py
- backend/src/handlers/admin/agent_invite.py
- backend/src/handlers/admin/agent_confirm.py
- backend/src/handlers/admin/number_mapping.py
- backend/src/handlers/admin/rollout_manager.py
- backend/src/handlers/internal/health_check.py

Also update backend/template.yaml to add:
- All admin Lambda functions with their routes and IAM roles
- Cognito JWT authorizer on the HTTP API for Admin routes
- The Cognito pre-token generation trigger Lambda that injects tenant_id,
  agent_id, and role into JWT claims

Rules:
- All admin routes require a valid JWT with Cognito group = Admins.
- AdminSignup is the only public admin route — no JWT required.
- AgentInvite must create a Cognito user, add them to the Agents group,
  and set custom:tenant_id on the user attribute.
- NumberMappingHandler handles GET, POST, PUT, DELETE for /admin/numbers.
  On POST, validate that the agent_id belongs to the same tenant.
- RolloutManager is internal only — IAM auth, not Cognito JWT.
- Follow the MVP logging contract from Section 8.5 of the logging doc exactly.
- Do not invent anything not in the docs. Ask if anything is unclear.

When done, list every file created or modified and confirm the next step is Step 8.
```

---

---

## STEP 8 — Agent API

**Paste these docs first:**
- `sotto-00-overview.md`
- `sotto-02-backend.md`
- `sotto-03-logging-cicd.md`

**Then paste this prompt:**

```
I am building Sotto — an AI call intelligence platform for insurance agencies.
I have pasted the overview doc, the backend doc, and the logging doc above.
Steps 1-7 are already built.

Build the Agent API — the endpoints used by agents in the Chrome extension
and admin portal to access calls, transcripts, and save notes.

Files to build:
- backend/src/handlers/agents/call_history.py
- backend/src/handlers/agents/call_detail.py
- backend/src/handlers/agents/notes.py

Also update backend/template.yaml to add:
- All agent Lambda functions with their routes and IAM roles
- Cognito JWT authorizer on agent routes (group = Agents)

Rules:
- CallHistoryHandler serves both GET /calls (agents, own calls only) and
  GET /admin/calls (admins, all agents). Check JWT group to determine scope.
- Agents must only see their own calls. Enforce this via the GSI query —
  never fetch all calls and filter in code.
- CallDetailHandler returns the full call including transcript content.
  If transcript_s3_key is set, read the transcript from S3 and include it.
- NotesHandler must verify the call belongs to the requesting agent before
  updating. Return 403 if not.
- Follow the MVP logging contract from Section 8.5 of the logging doc exactly.
- Write unit tests for the notes ownership check.
- Do not invent anything not in the docs. Ask if anything is unclear.

When done, list every file created or modified and confirm the next step is Step 9.
```

---

---

## STEP 9 — CI/CD Pipeline

**Paste these docs first:**
- `sotto-00-overview.md`
- `sotto-03-logging-cicd.md`

**Then paste this prompt:**

```
I am building Sotto — an AI call intelligence platform for insurance agencies.
I have pasted the overview doc and the logging doc above.
Steps 1-8 are already built — the backend is complete.

Build the complete GitHub Actions CI/CD pipeline.

Files to build:
- .github/workflows/pr-checks.yml
- .github/workflows/deploy-dev.yml
- .github/workflows/deploy-prod.yml
- .github/workflows/rollout-promote.yml
- .github/workflows/rollback.yml
- backend/Makefile with commands: build, test, lint, local, deploy-dev, logs-webhook
- backend/requirements-dev.txt

Rules:
- Use OIDC authentication to AWS. No long-lived AWS access keys stored as secrets.
- pr-checks must run: ruff lint, ruff format check, bandit security scan,
  pytest with coverage, and sam validate. All must pass for the PR to merge.
- deploy-dev auto-triggers on every push to main. No manual approval.
- deploy-prod requires the prod GitHub Environment approval gate before proceeding.
  It deploys, then monitors the CloudWatch error rate alarm for 10 minutes.
  If the alarm fires, the job fails (rollback is then triggered manually).
- rollout-promote and rollback are manual workflow_dispatch only.
- deploy-dev and deploy-prod must output the API URL after deploy and run
  a smoke test hitting GET /health.
- Build the frontend deploy steps in deploy-dev and deploy-prod as specified
  in the logging doc (S3 sync + CloudFront invalidation).
- Do not invent anything not in the docs. Ask if anything is unclear.

When done, list every file created or modified and confirm the next step is Step 10.
```

---

---

## STEP 10 — Deployment Strategy (Canary + Rollout)

**Paste these docs first:**
- `sotto-00-overview.md`
- `sotto-03-logging-cicd.md`

**Then paste this prompt:**

```
I am building Sotto — an AI call intelligence platform for insurance agencies.
I have pasted the overview doc and the logging doc above.
Steps 1-9 are already built.

Implement the deployment strategy — canary Lambda deploys and per-tenant
staged rollout via feature flags.

Changes to make:
- Update backend/template.yaml to add DeploymentPreference to all Lambda
  functions using Canary10Percent5Minutes in prod and AllAtOnce in dev,
  with the CloudWatch alarm as the rollback trigger.
- Add all CloudWatch alarms defined in Section 8.5 of the logging doc to
  backend/template.yaml (prod environment only).
- Add the SNS alert topic to backend/template.yaml.
- Verify RolloutManager Lambda from Step 7 correctly implements the promote
  and rollback actions from Section 10.3 of the logging doc.
- Update backend/scripts/seed_feature_flags.py from Step 5 to ensure all
  four initial feature flags from Section 10.4 of the logging doc are seeded
  correctly.

Rules:
- DeploymentPreference must reference the CloudWatch alarm so that if the
  alarm fires during a canary deploy, CodeDeploy automatically rolls back.
- The SNS topic must receive alerts from all CloudWatch alarms.
- Do not change any Lambda function code — this step is infrastructure only.
- Do not invent anything not in the docs. Ask if anything is unclear.

When done, list every file modified and confirm the next step is Step 11.
```

---

---

## STEP 11 — Chrome Extension (The Cockpit)

**Paste these docs first:**
- `sotto-00-overview.md`
- `sotto-04-security-frontend.md`

**Then paste this prompt:**

```
I am building Sotto — an AI call intelligence platform for insurance agencies.
I have pasted the overview doc and the security/frontend doc above.
Steps 1-10 are already built — the backend and CI/CD are complete.

Build the Chrome extension — the Cockpit side panel that agents use all day.

Files to build:
- frontend/extension/manifest.json
- frontend/extension/src/background/service_worker.js
- frontend/extension/src/sidepanel/index.html
- frontend/extension/src/sidepanel/sidepanel.js
- frontend/extension/src/sidepanel/sidepanel.css
- frontend/extension/src/content/epic_injector.js
- frontend/extension/src/shared/auth.js
- frontend/extension/src/shared/api.js
- frontend/extension/package.json

Rules:
- Use Chrome Manifest V3. Side Panel API for the Cockpit panel.
- The service worker maintains a persistent WebSocket connection with
  exponential backoff reconnect (1s, 2s, 4s, 8s, max 30s).
- JWT tokens expire after 1 hour. The service worker must refresh the
  Cognito token before the WebSocket token expires. Use a timer.
- Handle 410 Gone from the WebSocket gracefully — reconnect immediately.
- The side panel must handle all states: idle, ringing, active, ended,
  transcript_ready, complete — as described in Section 12.3 of the doc.
- The Epic injector must use defensive DOM selectors with fallbacks.
  Document every selector with a comment explaining what it targets.
- Store auth tokens in chrome.storage.session (not variables — service
  workers terminate when idle).
- Do not invent anything not in the docs. Ask if anything is unclear.

When done, list every file created and confirm the next step is Step 12.
```

---

---

## STEP 12 — Admin Portal

**Paste these docs first:**
- `sotto-00-overview.md`
- `sotto-04-security-frontend.md`

**Then paste this prompt:**

```
I am building Sotto — an AI call intelligence platform for insurance agencies.
I have pasted the overview doc and the security/frontend doc above.
Steps 1-11 are already built.

Build the Admin Portal — the React web app agency admins use to set up and
manage their Sotto account.

Files to build:
- frontend/admin-portal/package.json
- frontend/admin-portal/vite.config.js
- frontend/admin-portal/index.html
- frontend/admin-portal/src/App.jsx
- frontend/admin-portal/src/services/api.js
- frontend/admin-portal/src/pages/Signup.jsx
- frontend/admin-portal/src/pages/Dashboard.jsx
- frontend/admin-portal/src/pages/Agents.jsx
- frontend/admin-portal/src/pages/NumberMappings.jsx
- frontend/admin-portal/src/pages/CallHistory.jsx
- frontend/admin-portal/src/pages/CallDetail.jsx
- frontend/admin-portal/src/pages/Settings.jsx

Rules:
- Use React + Tailwind CSS. No other UI framework.
- Cognito JWT auth on every API request via Authorization: Bearer header.
  Auto-refresh token before expiry using the refresh token.
- NumberMappings page is the most critical — it must show a table of all
  numbers and extensions from the provider, with a dropdown per row to assign
  to an agent. Changes must save immediately on selection (no save-all button).
- CallHistory must show call status clearly and auto-refresh every 30 seconds.
- CallDetail must include an audio player for the recording (use a pre-signed
  S3 URL fetched from the API), the full transcript, AI summary, action items,
  and a notes textarea that auto-saves on blur.
- Redirect unauthenticated users to /login on every protected route.
- Do not invent anything not in the docs. Ask if anything is unclear.

When done, list every file created. The build sequence is complete.
Recommend running the full end-to-end test described in Step 12 of the
build sequence in sotto-00-overview.md.
```

---

---

## Debugging Sessions (No Build Doc Needed)

**Paste only:**
- `sotto-00-overview.md`

**Then describe the problem:**

```
I am debugging an issue with Sotto. Here is what happened:

[Paste CloudWatch logs or error description]

The relevant component is [Lambda function name / extension / portal].
The call_id is [if applicable].
The tenant_id is [if applicable].

Do not build anything. Help me diagnose the root cause and suggest a fix.
```

---

## If Claude Goes Off-Spec

Paste this to redirect it:

```
Stop. You are building something not specified in the docs.
Reread the task doc and build only what was asked.
Do not add features, abstractions, or future-proofing not explicitly described.
```
