# Sotto — AI Call Intelligence Platform

Insurance agency SaaS: records agent phone calls → transcribes via AWS Transcribe → generates AI notes via Bedrock (claude-haiku-4-5-20251001) → surfaces transcript + notes in a Chrome side panel (Cockpit) beside Applied Epic CRM. Multi-tenant, provider-agnostic.

## Spec Docs
- `sotto-00-overview.md` — overview, repo structure, pitfalls (Sections 0-3, 15-16)
- `sotto-01-infrastructure.md` — AWS infra spec, DynamoDB schemas (Sections 4-5)
- `sotto-02-backend.md` — Lambda function specs (Section 6), adapter spec (Section 7)
- `sotto-03-logging-cicd.md` — Powertools setup (Section 8), CI/CD (Section 9), rollout strategy (Section 10)
- `sotto-04-security-frontend.md` — security, Chrome extension, admin portal

## Key Paths
- Common layer: `backend/src/layers/common/python/sotto/`
- Handlers: `backend/src/handlers/`
- Tests: `backend/src/tests/`

## Powertools Setup
`sotto/logger.py` exposes these — always import from here, never re-instantiate in handlers:
```python
logger = Logger(service="sotto", level=os.environ.get("LOG_LEVEL", "DEBUG"))
tracer = Tracer(service="sotto")
metrics = Metrics(namespace="Sotto", service="sotto")
```
Every Lambda handler must use all three decorators:
```python
@logger.inject_lambda_context(log_event=True, correlation_id_path="requestContext.requestId")
@tracer.capture_lambda_handler(capture_response=True)
@metrics.log_metrics(capture_cold_start_metric=True)
def handler(event: dict, context: LambdaContext) -> dict: ...
```

## MVP Logging Contract (every function — no exceptions)
1. **Entry** (first line): `logger.debug("Handler entered", extra={function_name, function_version, aws_request_id, memory_limit_mb, remaining_time_ms, event_keys})`
2. **Branch decisions**: log flag name, tier, result, tenant_id
3. **External calls**: log BEFORE (inputs) and AFTER (response status, duration_ms)
4. **DynamoDB ops**: log table, key fields, operation
5. **SQS sends**: log queue name only (not full URL), event_type, tenant_id, call_id
6. **WebSocket pushes**: log agent_id, connection_id, event_type
7. **Exit** (last line): `logger.debug("Handler completed", extra={duration_ms, result_status})`
8. **Exceptions**: `logger.exception(msg, extra={full context})` then re-raise so SQS retries

Never log: full phone numbers · recording URLs (contain auth tokens) · JWTs · Secrets Manager values

## Immutable Rules
- Python 3.13 · arm64 Graviton2 · AWS SAM only (no CDK, no Terraform)
- NEVER store secrets in env vars or DynamoDB — always Secrets Manager
- Secrets key pattern: `sotto/{tenant_id}/{provider}_auth_token`
- No unhandled exceptions — always return structured error response
- Idempotent where possible — safe to replay from SQS
- Twilio: respond 200 within 3 seconds — push to SQS, return immediately
- All DynamoDB tables: PAY_PER_REQUEST billing, PITR enabled in prod only
- Do not invent anything not in the docs — ask if anything is unclear

## Key Domain Values
- Call status: `recording` → `transcribing` → `summarizing` → `complete` | `failed`
- Transcript status: `pending` → `in_progress` → `complete` | `failed`
- Deployment tiers: `beta` | `live_test` | `full`
- Providers: `twilio` | `ringcentral` | `zoom` | `teams` | `8x8`
- Agent status: `invited` | `active` | `inactive`

## Resource Naming (all suffixed with -{env}, env = dev | prod)
- Tables: `sotto-tenants` · `sotto-agents` · `sotto-number-mappings` · `sotto-calls` · `sotto-ws-connections` · `sotto-feature-flags` · `sotto-deployments`
- Queues: `sotto-call-events` (vis:60s, DLQ maxReceive:3) · `sotto-transcription-results` (vis:30s, DLQ)
- S3: `sotto-recordings-{account_id}` · `sotto-artifacts-{account_id}` · `sotto-portal-{account_id}`
- S3 paths: `{tenant_id}/recordings/{year}/{month}/{call_id}.{ext}` · `{tenant_id}/transcripts/{year}/{month}/{call_id}.json`
- Transcribe job: `sotto-{env}-{call_id}` — globally unique, never reuse (generate new call_id on retry)
- Log groups: `/aws/lambda/sotto-{FunctionName}-{env}`

## DynamoDB Quick Reference
| Table | PK | SK | Key GSIs |
|---|---|---|---|
| calls | tenant_id | call_id | `agent-date-index` PK=`agent_date_key` SK=created_at · `status-index` PK=tenant_id SK=status |
| agents | tenant_id | agent_id | `email-index` PK=email SK=tenant_id · `cognito-index` PK=cognito_sub |
| tenants | tenant_id | — | `status-index` PK=status SK=created_at |
| number-mappings | tenant_id | identifier | — |
| ws-connections | connection_id | — | `agent-index` PK=agent_id · TTL on `ttl` attribute |
| feature-flags | flag_name | — | — |
| deployments | deployment_id | — | — |

`agent_date_key` is a synthetic attribute on calls storing `"{tenant_id}#{agent_id}"` as a single string.

## SAM Globals (already in template.yaml — do not repeat per-function)
Runtime: python3.13 · Architectures: [arm64] · Tracing: Active · MemorySize: 256 · Timeout: 30
Env vars: ENVIRONMENT · LOG_LEVEL · POWERTOOLS_SERVICE_NAME=sotto · POWERTOOLS_METRICS_NAMESPACE=Sotto · AWS_ACCOUNT_ID

## Build Progress
- [x] Step 1 — Infrastructure foundation (`template.yaml`, `samconfig.toml`)
- [x] Step 2 — Common layer → `/project:step-02`
- [x] Step 3 — Webhook entry point → `/project:step-03`
- [x] Step 4 — Call processing pipeline → `/project:step-04`
- [x] Step 5 — AI summarizer + feature flags → `/project:step-05`
- [x] Step 6 — WebSocket infrastructure → `/project:step-06`
- [x] Step 7 — Admin API → `/project:step-07`
- [x] Step 8 — Agent API → `/project:step-08`
- [x] Step 9 — CI/CD pipeline → `/project:step-09`
- [x] Step 10 — Deployment strategy (canary + rollout) → `/project:step-10`
- [x] Step 11 — Chrome extension (Cockpit) → `/project:step-11`
- [x] Step 12 — Admin portal → `/project:step-12`

### Step 12 Files Created
- `frontend/admin-portal/package.json`
- `frontend/admin-portal/vite.config.js`
- `frontend/admin-portal/index.html`
- `frontend/admin-portal/tailwind.config.js`
- `frontend/admin-portal/postcss.config.js`
- `frontend/admin-portal/src/main.jsx`
- `frontend/admin-portal/src/index.css`
- `frontend/admin-portal/src/App.jsx`
- `frontend/admin-portal/src/services/api.js`
- `frontend/admin-portal/src/pages/Login.jsx`
- `frontend/admin-portal/src/pages/Signup.jsx`
- `frontend/admin-portal/src/pages/Dashboard.jsx`
- `frontend/admin-portal/src/pages/Agents.jsx`
- `frontend/admin-portal/src/pages/NumberMappings.jsx`
- `frontend/admin-portal/src/pages/CallHistory.jsx`
- `frontend/admin-portal/src/pages/CallDetail.jsx`
- `frontend/admin-portal/src/pages/Settings.jsx`

## Teams Phone Integration (post-Step 12)

Tracking Microsoft Teams Phone integration per `sotto-teams-phone-integration.md`.
Custom-domain runbook: `sotto-custom-domains.md`. Structured as milestones with
hard stops between them.

- [x] **M1 / T-2** — Teams DynamoDB schema (tenants/agents/calls) + T-5 placeholder parameters in `template.yaml`
- [x] **M1.5** — `sotto.cloud` custom domains end-to-end: ACM wildcard cert, API Gateway custom domain, CloudFront alias, Porkbun CNAMEs, CI wiring, runbook. Live at `api-dev.sotto.cloud` + `portal-dev.sotto.cloud`.
- [ ] **M2 / T-3** — TeamsOnboarding Lambda (`src/handlers/teams/onboarding.py`) + Graph API client (`src/handlers/teams/graph_client.py`) + SAM function resource + unit tests. **In progress.**
- [ ] **M3 / T-7** — Pipeline plumbing: `NormalizedCallEvent` Teams fields, `RecordingProcessor` skip-download branch, `TranscriptionInit` channel identification cascade, unit tests.
- [ ] **Deferred to separate sessions:**
  - **T-4** — C# `.NET 8` TeamsMediaBot container (receives SRTP, buffers audio, encodes stereo MP3, uploads to S3, publishes SQS handoff). Microsoft Graph Communications SDK is C#-only.
  - **T-5** — ECS Fargate cluster + ALB + Secrets Manager bootstrap for the bot (VpcId/PublicSubnets/BotTLSCertificateArn parameters already declared in `template.yaml`, default empty).
  - T-4 and T-5 are deferred because they are self-contained sub-projects with their own tool/language context (C#, ECR, Fargate networking). Handling them in separate sessions keeps this session focused.

**Key architectural decisions locked in:**
- Azure AD app is **multi-tenant**, one registration owned by Sotto. Customers consent via OAuth; no per-customer app registration.
- Token flow: `client_credentials` grant, application permissions. No delegated auth, no refresh tokens, no per-tenant token storage.
- Azure app secrets: GLOBAL `sotto/azure/app_client_id` + `sotto/azure/app_client_secret` in Secrets Manager. Per-tenant `ms_tenant_id` lives on the DynamoDB tenant record.
- Compliance recording policy: `requiredDuringCall=false`, `requiredBeforeCallEstablishment=false` — missed recordings are acceptable, dropping live customer calls is not.
- Policy assignment is per-agent (not tenant-wide) so admins/back-office staff aren't recorded.
- OAuth callback URL: `https://api-dev.sotto.cloud/teams/oauth/callback` (dev) and `https://api.sotto.cloud/teams/oauth/callback` (prod).
