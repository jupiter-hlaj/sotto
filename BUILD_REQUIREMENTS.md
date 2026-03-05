# Sotto — AI Call Intelligence Platform — Build Requirements
**Feed this document to Claude at the start of each build session.**

---

## Product Name: Sotto

**Sotto** comes from the Italian musical term *sotto voce* — meaning "under the voice" or "in a quiet voice." In music it describes a passage performed softly, present but unobtrusive.

This is exactly what the product does. It sits quietly beside the agent in their browser while they work. It doesn't interrupt, doesn't demand attention, doesn't replace their existing tools. It simply listens, captures everything, and surfaces what matters — the moment it's needed. The agent stays focused on the client. Sotto handles the rest.

The name also carries a secondary meaning relevant to the product's architecture: *sotto* as "beneath" or "underneath." Sotto runs beneath the surface of the agent's workflow — invisible infrastructure that makes their work better without being in the way.

It is not a tool agents think about. It is a tool that thinks for them.

---

## 0. How to Use This Document

This document is the complete specification for building the AI Call Intelligence Platform. When starting a new Claude session, paste this document in full. Claude will have everything needed to build any component without re-explaining context.

**Key rules for Claude:**
- Build exactly what is specified. Do not add unrequested features.
- Follow the build sequence in Section 15. Do not skip ahead.
- Every Lambda function must use AWS Lambda Powertools (logger, tracer, metrics) as specified in Section 8.
- Never store secrets in code or environment variables directly — always use Secrets Manager.
- All code is Python 3.13 unless explicitly stated otherwise.
- Use SAM (AWS Serverless Application Model) for all infrastructure. No CDK, no Terraform.
- Ask for clarification before making architectural decisions not covered here.
- **MVP logging rule: every function is maximally verbose. See Section 8.5 for the explicit MVP logging contract.**

---

## 0.5 Technology Decision Rationale

Every component was chosen deliberately. This section explains the reasoning so that future decisions are consistent with the philosophy of this system.

### AWS Lambda (Serverless Functions)
**Why:** There is no baseline traffic. Insurance agencies make calls during business hours only. A traditional server running 24/7 would be idle 70% of the time and you'd be paying for it. Lambda charges only for actual invocations — when a call happens. At MVP scale (tens of agencies, hundreds of calls per day), the cost is effectively zero. Lambda also scales automatically with no intervention. The alternative (ECS containers, EC2) would require provisioning, monitoring, and paying for idle capacity — all overhead with no benefit at this stage.

### AWS SAM (Infrastructure as Code)
**Why:** The owner of this project has already built multiple systems (IPRS, Chimera, BlueBaby) using SAM. Consistency across projects matters — you don't want to context-switch between frameworks. SAM is also the most native AWS IaC tool for serverless; it compiles down to CloudFormation but provides Lambda-specific abstractions (events, layers, deployment preferences) that CDK and Terraform require more boilerplate to achieve. SAM is simpler for the scope of this project.

### DynamoDB (Database)
**Why:** There is no relational query pattern in this system that requires a relational database. The access patterns are all key-based lookups: get tenant by ID, get calls for agent X, get connection for agent Y. DynamoDB handles all of these natively and at any scale without database administration, connection pooling, schema migrations, or maintenance windows. RDS PostgreSQL would add a VPC (latency + cost), connection limits, and operational overhead. DynamoDB removes the database from the list of things that can go wrong.

### SQS (Message Queue)
**Why:** Twilio requires your webhook endpoint to respond within 3 seconds or it retries the webhook — potentially sending duplicate events. The recording download, S3 upload, and DynamoDB write cannot reliably complete in 3 seconds on a cold Lambda start. SQS decouples the problem: the webhook handler accepts the event and responds immediately (under 100ms), and the heavy processing happens asynchronously at whatever pace is needed. SQS also provides built-in retry logic and dead-letter queues for failed processing — if the recording download fails, the message re-queues automatically.

### API Gateway WebSocket API
**Why:** The Cockpit Chrome extension needs to know the moment a call ends so it can show the transcript. The options were: (1) polling — extension repeatedly asks "is there a transcript yet?" every N seconds, (2) WebSocket — server pushes to the extension the instant the transcript is ready. Polling wastes API calls and creates latency (up to N seconds of delay). WebSocket is the right tool for real-time push from server to client. API Gateway's WebSocket API is fully managed — no socket server to run or scale.

### AWS Cognito (Authentication)
**Why:** Building auth from scratch is dangerous — session management, password hashing, token expiry, refresh tokens, and email verification all have security implications if implemented incorrectly. Cognito handles all of this. It also integrates natively with API Gateway JWT authorizers, meaning authenticated routes are secured at the infrastructure level — not just in application code. The owner has already used Cognito in the BlueBaby project and understands how it works. Using it here avoids re-learning a new auth system.

### AWS Transcribe (Speech-to-Text)
**Why:** No dependency on a third-party API. If OpenAI Whisper or Google Speech-to-Text had an outage or changed pricing, transcription would break. AWS Transcribe is billed per second of audio, integrates natively with S3 and EventBridge, supports speaker diarization (identifying who said what), and does not require sending audio data outside of AWS. This keeps all customer call data inside a single AWS account with known data residency — important for insurance compliance.

### AWS Bedrock / Claude (AI Summaries)
**Why:** Bedrock provides access to Claude (claude-haiku-4-5-20251001 for cost efficiency) via an AWS-native API. Like Transcribe, this keeps data within AWS and avoids a separate third-party API dependency. claude-haiku-4-5-20251001 is fast and cheap — appropriate for summarizing a call transcript (a task that does not require the full capability of Opus). AI summaries are a Phase 1 feature gated behind a feature flag, so they can be turned off per-tenant without affecting the rest of the system.

### AWS Secrets Manager (Credentials)
**Why:** Phone provider API keys (Twilio auth tokens, RingCentral secrets) are per-tenant sensitive credentials. Storing them in DynamoDB (even encrypted) would mean application code with DynamoDB access can read any tenant's credentials — a security problem. Environment variables are baked into Lambda configurations and visible in the AWS console to anyone with Lambda read access. Secrets Manager provides fine-grained IAM access control, automatic rotation hooks, and audit logging of every access. The right tool for secrets.

### AWS Lambda Powertools (Logging/Tracing/Metrics)
**Why:** Without structured logging, debugging a production issue means reading unformatted text logs trying to correlate events across multiple Lambda invocations. Powertools outputs structured JSON — every log line is a parseable object with consistent fields (tenant_id, call_id, agent_id, request_id). CloudWatch Insights can then query: "show me all log lines for call_id = X across all Lambda functions." X-Ray tracing (also via Powertools) shows the complete timeline of a call event flowing through the system. This is not optional — it is the difference between debugging in minutes and debugging in hours.

### Chrome Side Panel API (Extension UI)
**Why:** The traditional approach for a browser extension UI is a popup (appears when you click the extension icon) or a content script (injects UI directly into the page). Neither is right here. A popup closes when the agent clicks anywhere else. A content script injecting UI into Applied Epic is brittle and could break when Epic updates their DOM. The Chrome Side Panel API (Manifest V3) creates a persistent panel on the right side of the browser window that stays open while the agent works. It is an official Chrome API, it does not touch the page DOM, and it persists across tab navigation. This is the correct tool.

### GitHub Actions (CI/CD)
**Why:** The owner already uses GitHub Actions in the Chimera project with OIDC authentication to AWS. Consistency again. GitHub Actions is free for public repos and reasonably priced for private. It integrates directly with the GitHub repository — no separate CI tool to configure or pay for. The OIDC integration means no long-lived AWS credentials stored as secrets — GitHub authenticates to AWS dynamically per-run.

### No VPC
**Why:** A VPC would be required if connecting Lambda to resources that are inside a VPC (like RDS). All services used here (DynamoDB, S3, SQS, Transcribe, Bedrock, API Gateway) are AWS-managed services accessible via public endpoints with IAM authentication. Adding a VPC would require: NAT Gateway (~$45/month per AZ), VPC endpoints for each service (optional but recommended), increased Lambda cold start times, and subnet management. The security model here relies on IAM roles and Secrets Manager — not network isolation. A VPC adds cost and complexity with no security benefit for this specific architecture.

### Trunk-Based Development (main branch = deployable)
**Why:** Feature branches are used for development, but `main` is always the deployable branch. There are no long-lived `staging` or `develop` branches. The reason is that merging long-lived branches is a source of bugs and conflicts. Small, frequent merges to `main` (behind feature flags if not ready for production) keep the codebase integrated and the feedback loop short. The per-tenant feature flag system handles "this isn't ready for everyone yet" — not a separate branch.

---

## 1. Project Overview

**Product:** AI Call Intelligence Platform (internal codename: Sotto)

**What it does:** A fully managed SaaS platform that records phone calls for insurance agency staff, transcribes them automatically, and surfaces the transcript alongside AI-generated notes in a Chrome browser extension (the "Cockpit") that lives beside Applied Epic — the CRM most agencies use. Agents never switch windows. Everything happens automatically.

**Core user journey:**
1. Insurance agency admin signs up, connects their phone provider, invites agents.
2. Agent installs Chrome extension, signs in once.
3. When agent's phone rings (any device, any provider), the Cockpit side panel activates automatically.
4. Call ends. Transcript appears within seconds. Agent adds notes. Saves.

**Business context:**
- Target: small-to-mid insurance agencies (5–50 agents)
- Primary competitor: legacy on-premise Windows-only software with manual deployment
- Key differentiator: fully managed SaaS, browser-native, provider-agnostic, zero IT required

---

## 2. Tech Stack Decisions

| Layer | Technology | Rationale |
|---|---|---|
| Backend runtime | Python 3.13 | Existing project consistency |
| IaC | AWS SAM | Existing project consistency (Chimera, BlueBaby) |
| CI/CD | GitHub Actions | Existing project consistency |
| Auth | AWS Cognito | Used in BlueBaby, understood |
| Database | DynamoDB | Serverless, no provisioned DB to manage |
| Object storage | S3 | Recordings, transcripts, frontend assets |
| Transcription | AWS Transcribe | Native AWS, no third-party dependency |
| AI summaries | AWS Bedrock (Claude claude-haiku-4-5-20251001) | Cost-efficient for summaries |
| Real-time push | API Gateway WebSocket API | Serverless WebSocket, no servers to run |
| Secrets | AWS Secrets Manager | Never env vars or DynamoDB for credentials |
| Logging | AWS Lambda Powertools | Structured JSON, X-Ray, metrics in one library |
| Tracing | AWS X-Ray (via Powertools) | Distributed trace across Lambda chain |
| Feature flags | DynamoDB (per-tenant) | Simple, no extra service, full control |
| Canary deploys | AWS CodeDeploy + SAM DeploymentPreference | Native SAM support |
| Frontend | React + Tailwind CSS | Admin portal |
| Extension | Chrome MV3 (Manifest V3) | Current Chrome extension standard |

**What is deliberately NOT used:**
- VPC: Adds latency, NAT gateway cost, complexity. Not needed — all services are AWS-native.
- RDS: DynamoDB is sufficient and removes DB management overhead.
- ECS/EKS: Serverless Lambda handles all workloads at this scale.
- Redis/ElastiCache: DynamoDB with proper TTL covers all caching needs at MVP.

---

## 3. Repository Structure

```
sotto/
├── .github/
│   └── workflows/
│       ├── pr-checks.yml          # Runs on every PR: lint, test, SAM validate
│       ├── deploy-dev.yml         # Auto-deploy to dev on merge to main
│       ├── deploy-prod.yml        # Deploy to prod on release tag (v*.*.*)
│       ├── rollout-promote.yml    # Manual: promote deployment tier
│       └── rollback.yml           # Manual: emergency rollback
├── backend/
│   ├── template.yaml              # SAM template — ALL infrastructure defined here
│   ├── samconfig.toml             # SAM deploy config (dev + prod stacks)
│   ├── Makefile                   # Common commands: build, test, local, deploy
│   ├── requirements-dev.txt       # Dev dependencies (pytest, ruff, bandit)
│   └── src/
│       ├── layers/
│       │   └── common/
│       │       └── python/        # Lambda layer — shared code
│       │           ├── sotto/
│       │           │   ├── __init__.py
│       │           │   ├── logger.py        # Powertools logger config
│       │           │   ├── db.py            # DynamoDB client + helpers
│       │           │   ├── s3.py            # S3 client + helpers
│       │           │   ├── secrets.py       # Secrets Manager helpers
│       │           │   ├── models.py        # Pydantic models for all data types
│       │           │   ├── feature_flags.py # Per-tenant feature flag resolution
│       │           │   └── adapters/
│       │           │       ├── base.py          # Abstract base adapter
│       │           │       ├── twilio.py        # Twilio webhook normalizer
│       │           │       ├── ringcentral.py   # RingCentral normalizer
│       │           │       ├── zoom.py          # Zoom Phone normalizer
│       │           │       ├── teams.py         # MS Teams normalizer
│       │           │       └── eightbyeight.py  # 8x8 normalizer
│       │           └── requirements.txt
│       └── handlers/
│           ├── webhooks/
│           │   └── provider_webhook.py     # Entry point for all provider webhooks
│           ├── calls/
│           │   ├── recording_processor.py  # Downloads + stores recording to S3
│           │   ├── transcription_init.py   # Starts AWS Transcribe job
│           │   └── transcription_result.py # Handles Transcribe completion
│           ├── ai/
│           │   └── summarizer.py           # Bedrock/Claude summary + action items
│           ├── websocket/
│           │   ├── connect.py              # WS $connect route
│           │   ├── disconnect.py           # WS $disconnect route
│           │   └── default.py              # WS $default route
│           ├── admin/
│           │   ├── signup.py               # Create tenant + admin Cognito user
│           │   ├── tenant_config.py        # Get/update tenant configuration
│           │   ├── agent_invite.py         # Send agent invitation
│           │   ├── agent_confirm.py        # Confirm agent setup
│           │   ├── number_mapping.py       # CRUD: phone number → agent mapping
│           │   └── rollout_manager.py      # Internal: update deployment tiers
│           ├── agents/
│           │   ├── call_history.py         # Get call list for agent
│           │   ├── call_detail.py          # Get single call with transcript
│           │   └── notes.py               # Save/update call notes
│           └── internal/
│               └── health_check.py         # GET /health — for ALB/monitoring
├── frontend/
│   ├── admin-portal/
│   │   ├── src/
│   │   │   ├── App.jsx
│   │   │   ├── pages/
│   │   │   │   ├── Signup.jsx
│   │   │   │   ├── Dashboard.jsx
│   │   │   │   ├── Agents.jsx
│   │   │   │   ├── NumberMappings.jsx
│   │   │   │   └── CallHistory.jsx
│   │   │   ├── components/
│   │   │   └── services/
│   │   │       └── api.js              # API client with Cognito JWT
│   │   ├── package.json
│   │   └── vite.config.js
│   └── extension/
│       ├── manifest.json               # Chrome MV3 manifest
│       ├── src/
│       │   ├── background/
│       │   │   └── service_worker.js   # WebSocket connection manager
│       │   ├── sidepanel/
│       │   │   ├── index.html
│       │   │   ├── sidepanel.js        # Cockpit UI logic
│       │   │   └── sidepanel.css
│       │   ├── content/
│       │   │   └── epic_injector.js    # Applied Epic DOM integration
│       │   └── shared/
│       │       ├── auth.js             # Cognito auth helpers
│       │       └── api.js              # Backend API client
│       └── package.json
└── docs/
    ├── adr/                            # Architecture Decision Records
    └── runbooks/                       # Operational runbooks
```

---

## 4. AWS Infrastructure — Complete Specification

### 4.1 Environments

Two environments: `dev` and `prod`. They are **separate AWS CloudFormation stacks** with separate resources. There is no shared infrastructure between them.

- Stack name dev: `sotto-dev`
- Stack name prod: `sotto-prod`
- All resource names are suffixed with the environment: `sotto-tenants-dev`, `sotto-tenants-prod`

### 4.2 SAM Template Parameters

The `template.yaml` must accept these parameters:

```yaml
Parameters:
  Environment:
    Type: String
    AllowedValues: [dev, prod]
    Default: dev
  LogLevel:
    Type: String
    Default: DEBUG
    AllowedValues: [DEBUG, INFO, WARNING, ERROR]
  CognitoAdminEmail:
    Type: String
    Description: Initial admin email for Cognito User Pool
```

### 4.3 Global Lambda Configuration

All Lambda functions in the SAM template inherit these globals:

```yaml
Globals:
  Function:
    Runtime: python3.13
    Architectures: [arm64]          # Graviton2 — cheaper + faster
    MemorySize: 256
    Timeout: 30
    Tracing: Active                  # X-Ray
    Environment:
      Variables:
        ENVIRONMENT: !Ref Environment
        LOG_LEVEL: !Ref LogLevel
        POWERTOOLS_SERVICE_NAME: sotto
        POWERTOOLS_METRICS_NAMESPACE: Sotto
        AWS_ACCOUNT_ID: !Ref AWS::AccountId
    Layers:
      - !Ref CommonLayer
    Tags:
      Project: sotto
      Environment: !Ref Environment
  Api:
    TracingEnabled: true
    Cors:
      AllowMethods: "'GET,POST,PUT,DELETE,OPTIONS'"
      AllowHeaders: "'Content-Type,Authorization'"
      AllowOrigin: "'*'"
```

### 4.4 Lambda Layer

The `CommonLayer` contains all shared code under `src/layers/common/python/`. It includes:
- `sotto` package (logger, db, s3, secrets, models, feature_flags, adapters)
- Third-party packages: `aws-lambda-powertools[all]`, `pydantic`, `boto3`, `requests`

The layer ARN is referenced by all Lambda functions via `!Ref CommonLayer`.

### 4.5 API Gateway — HTTP API

One HTTP API with Cognito JWT authorizer. Routes:

**Public (no auth):**
- `POST /webhooks/{provider}` — provider webhook entry point (Twilio, RC, Zoom, etc.)
- `GET /health` — health check

**Admin (requires Cognito JWT, group: Admins):**
- `POST /admin/signup` — create tenant + admin account
- `GET /admin/tenant` — get tenant config
- `PUT /admin/tenant` — update tenant config
- `POST /admin/agents/invite` — invite an agent
- `GET /admin/agents` — list all agents
- `DELETE /admin/agents/{agent_id}` — deactivate agent
- `GET /admin/numbers` — list number mappings
- `POST /admin/numbers` — create number→agent mapping
- `PUT /admin/numbers/{mapping_id}` — update mapping
- `DELETE /admin/numbers/{mapping_id}` — delete mapping
- `GET /admin/calls` — call history (all agents)

**Agent (requires Cognito JWT, group: Agents):**
- `GET /calls` — call history (own calls only)
- `GET /calls/{call_id}` — call detail + transcript
- `PUT /calls/{call_id}/notes` — save notes

**Internal (requires IAM auth, called only by other Lambdas/GitHub Actions):**
- `POST /internal/rollout` — update tenant deployment tiers

### 4.6 API Gateway — WebSocket API

Separate WebSocket API with routes:
- `$connect` — validate JWT in query param, store connection
- `$disconnect` — remove connection record
- `$default` — handle keepalive pings

The WebSocket endpoint URL is stored in Parameter Store and injected into the Chrome extension build.

### 4.7 SQS Queues

Two SQS queues for async processing:

1. `sotto-call-events-{env}` — receives normalized call events from adapter Lambda
   - Visibility timeout: 60s
   - DLQ: `sotto-call-events-dlq-{env}` (max receive count: 3)

2. `sotto-transcription-results-{env}` — receives AWS Transcribe completion events
   - Visibility timeout: 30s
   - DLQ: `sotto-transcription-results-dlq-{env}` (max receive count: 3)

### 4.8 S3 Buckets

**sotto-recordings-{account_id}-{env}:**
- Purpose: Call recordings (MP3/WAV) and transcripts (JSON)
- Versioning: Enabled
- Lifecycle: Move to S3 Infrequent Access after 90 days, Glacier after 365 days
- Encryption: SSE-S3
- Public access: Blocked entirely
- Path structure: `{tenant_id}/recordings/{year}/{month}/{call_id}.mp3`
- Path structure: `{tenant_id}/transcripts/{year}/{month}/{call_id}.json`

**sotto-artifacts-{account_id}-{env}:**
- Purpose: SAM deployment artifacts, Lambda zip files
- Versioning: Enabled
- Lifecycle: Delete versions older than 90 days

**sotto-portal-{account_id}-{env}:**
- Purpose: Admin portal static assets (React build)
- Static website hosting: Enabled
- Served via CloudFront

### 4.9 CloudFront Distribution

Single CloudFront distribution serving the admin portal:
- Origin: `sotto-portal-{account_id}-{env}` S3 bucket
- HTTPS only (redirect HTTP → HTTPS)
- Cache behavior: cache static assets, no-cache for `index.html`
- Error pages: 404 → `/index.html` (SPA routing)

---

## 5. DynamoDB Schema — Complete Specification

**Important:** All tables use on-demand (PAY_PER_REQUEST) billing. No provisioned capacity.

All tables have Point-In-Time Recovery (PITR) enabled in prod only.

### 5.1 Table: `sotto-tenants-{env}`

Stores one record per agency.

| Attribute | Type | Description |
|---|---|---|
| `tenant_id` (PK) | String | UUID v4 |
| `agency_name` | String | Display name |
| `admin_email` | String | Primary admin email |
| `status` | String | `active` \| `suspended` \| `trial` |
| `plan` | String | `trial` \| `starter` \| `pro` |
| `provider_type` | String | `twilio` \| `ringcentral` \| `zoom` \| `teams` \| `8x8` |
| `deployment_tier` | String | `beta` \| `live_test` \| `full` — controls feature flags |
| `created_at` | String | ISO 8601 timestamp |
| `updated_at` | String | ISO 8601 timestamp |
| `twilio_account_sid` | String | Twilio account SID (not the auth token — that's in Secrets Manager) |
| `twilio_phone_number` | String | Provisioned Twilio number (if applicable) |

**GSI: `status-index`** — PK: `status`, SK: `created_at`
- Used by: admin tools, rollout manager

**Access patterns:**
- Get tenant by ID: GetItem on `tenant_id`
- List all active tenants: Query GSI `status-index` with `status = active`
- Update tenant config: UpdateItem on `tenant_id`

### 5.2 Table: `sotto-agents-{env}`

Stores one record per agent per tenant.

| Attribute | Type | Description |
|---|---|---|
| `tenant_id` (PK) | String | UUID — tenant this agent belongs to |
| `agent_id` (SK) | String | UUID v4 |
| `email` | String | Agent email (unique within tenant) |
| `name` | String | Display name |
| `status` | String | `invited` \| `active` \| `inactive` |
| `cognito_sub` | String | Cognito user sub (set after agent confirms) |
| `created_at` | String | ISO 8601 |
| `invited_at` | String | ISO 8601 |
| `confirmed_at` | String | ISO 8601 (null until confirmed) |

**GSI: `email-index`** — PK: `email`, SK: `tenant_id`
- Used by: webhook router (find agent by email for providers that report by email)

**GSI: `cognito-index`** — PK: `cognito_sub`
- Used by: all authenticated agent requests (JWT → cognito_sub → agent record)

### 5.3 Table: `sotto-number-mappings-{env}`

Maps phone numbers and extensions to agents.

| Attribute | Type | Description |
|---|---|---|
| `tenant_id` (PK) | String | UUID |
| `identifier` (SK) | String | Phone number (`+15551234567`) or extension (`ext:204`) or email (`email:user@co.com`) |
| `agent_id` | String | UUID — which agent this maps to |
| `identifier_type` | String | `did` \| `extension` \| `email` \| `sip` |
| `label` | String | Human-readable label (e.g., "Sarah's direct line") |
| `created_at` | String | ISO 8601 |

**Access patterns:**
- Resolve identifier to agent: GetItem on (`tenant_id`, `identifier`)
- List all mappings for tenant: Query on `tenant_id`

### 5.4 Table: `sotto-calls-{env}`

One record per call.

| Attribute | Type | Description |
|---|---|---|
| `tenant_id` (PK) | String | UUID |
| `call_id` (SK) | String | UUID v4 (our internal ID, not provider's) |
| `agent_id` | String | UUID of the agent who answered |
| `provider` | String | `twilio` \| `ringcentral` etc. |
| `provider_call_id` | String | Provider's native call ID |
| `direction` | String | `inbound` \| `outbound` |
| `from_number` | String | E.164 caller number |
| `to_identifier` | String | DID or extension that was called |
| `duration_sec` | Number | Call duration in seconds |
| `status` | String | `recording` \| `transcribing` \| `summarizing` \| `complete` \| `failed` |
| `recording_s3_key` | String | Full S3 key to recording file |
| `transcript_s3_key` | String | Full S3 key to transcript JSON |
| `transcript_status` | String | `pending` \| `in_progress` \| `complete` \| `failed` |
| `summary` | String | AI-generated summary (null until complete) |
| `action_items` | List | AI-extracted action items |
| `notes` | String | Agent-written notes |
| `notes_updated_at` | String | ISO 8601 |
| `created_at` | String | ISO 8601 — call start time |
| `ended_at` | String | ISO 8601 — call end time |

**GSI: `agent-date-index`** — PK: `tenant_id#agent_id`, SK: `created_at`
- Used by: agent call history (filtered by agent and date range)
- Note: Composite PK format is `{tenant_id}#{agent_id}` as a single string

**GSI: `status-index`** — PK: `tenant_id`, SK: `status`
- Used by: monitoring, finding stuck calls

**Access patterns:**
- Get call by ID: GetItem on (`tenant_id`, `call_id`)
- Agent's calls (recent first): Query GSI `agent-date-index`, ScanIndexForward=False
- Calls pending transcription: Query GSI `status-index` with `status = recording`

### 5.5 Table: `sotto-ws-connections-{env}`

WebSocket connection registry. Records are short-lived.

| Attribute | Type | Description |
|---|---|---|
| `connection_id` (PK) | String | API Gateway connection ID |
| `agent_id` | String | UUID |
| `tenant_id` | String | UUID |
| `connected_at` | String | ISO 8601 |
| `ttl` | Number | Unix timestamp — 24 hours after connection |

**GSI: `agent-index`** — PK: `agent_id`
- Used by: call event publisher (find active connection for an agent)

TTL is enabled on the `ttl` attribute to auto-delete stale records.

### 5.6 Table: `sotto-feature-flags-{env}`

Controls per-feature rollout by tenant deployment tier.

| Attribute | Type | Description |
|---|---|---|
| `flag_name` (PK) | String | e.g., `ai_summary`, `epic_dom_injection` |
| `enabled_tiers` | List | e.g., `['beta', 'live_test', 'full']` |
| `description` | String | Human-readable description |
| `default_value` | Boolean | What to return if flag not found |
| `updated_at` | String | ISO 8601 |

**Access patterns:**
- Check flag for tenant: GetItem by `flag_name`, then check if tenant's `deployment_tier` is in `enabled_tiers`
- List all flags: Scan (rarely used, admin only)

### 5.7 Table: `sotto-deployments-{env}`

Tracks prod deployments for rollout management.

| Attribute | Type | Description |
|---|---|---|
| `deployment_id` (PK) | String | UUID v4 (generated by CI/CD) |
| `git_sha` | String | Full commit SHA |
| `git_tag` | String | Release tag (e.g., `v1.2.3`) |
| `lambda_version` | String | Published Lambda version number |
| `alias` | String | Current active alias (`CANARY`, `LIVE`) |
| `status` | String | `canary` \| `live_test` \| `full` \| `rolled_back` |
| `error_rate_at_deploy` | Number | Baseline error rate before deploy |
| `deployed_at` | String | ISO 8601 |
| `promoted_at` | String | ISO 8601 (null until promoted) |
| `rolled_back_at` | String | ISO 8601 (null unless rolled back) |
| `deployed_by` | String | GitHub Actions run ID |

---

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

## 8. Logging & Observability — Complete Specification

### 8.1 AWS Lambda Powertools Setup

**Every Lambda function** must use this pattern. No exceptions.

**In the common layer** (`sotto/logger.py`):

```python
import os
from aws_lambda_powertools import Logger, Tracer, Metrics
from aws_lambda_powertools.metrics import MetricUnit

logger = Logger(
    service="sotto",
    level=os.environ.get("LOG_LEVEL", "DEBUG"),
)

tracer = Tracer(service="sotto")
metrics = Metrics(namespace="Sotto", service="sotto")
```

**Every Lambda handler file:**

```python
from sotto.logger import logger, tracer, metrics
from aws_lambda_powertools.utilities.typing import LambdaContext

@logger.inject_lambda_context(log_event=True, correlation_id_path="requestContext.requestId")
@tracer.capture_lambda_handler(capture_response=True)
@metrics.log_metrics(capture_cold_start_metric=True)
def handler(event: dict, context: LambdaContext) -> dict:
    ...
```

### 8.5 MVP Verbose Logging Contract

**This section overrides all other logging guidance for the MVP phase.**

During MVP, the system must be maximally chatty. The goal is to be able to reconstruct exactly what happened in any call, for any tenant, at any point in time, purely from logs — without needing to reproduce the issue.

**Every Lambda function must log the following, unconditionally:**

```python
# 1. Function entry — always first line of handler
logger.debug("Handler entered", extra={
    "function_name": context.function_name,
    "function_version": context.function_version,
    "aws_request_id": context.aws_request_id,
    "memory_limit_mb": context.memory_limit_in_mb,
    "remaining_time_ms": context.get_remaining_time_in_millis(),
    "event_keys": list(event.keys()),   # Log key names but not full event (Powertools logs full event via log_event=True)
})

# 2. Every branch decision
logger.debug("Feature flag evaluated", extra={
    "flag": "ai_summary",
    "tenant_tier": tier,
    "result": is_enabled,
    "tenant_id": tenant_id,
})

# 3. Every external call — before AND after
logger.debug("Calling AWS Transcribe StartTranscriptionJob", extra={
    "job_name": job_name,
    "s3_input_key": s3_key,
    "language_code": "en-US",
    "speaker_labels": True,
})
# ... make the call ...
logger.debug("AWS Transcribe job started", extra={
    "job_name": job_name,
    "job_status": response['TranscriptionJob']['TranscriptionJobStatus'],
    "duration_ms": elapsed_ms,
})

# 4. Every DynamoDB operation
logger.debug("Writing call record to DynamoDB", extra={
    "table": table_name,
    "tenant_id": tenant_id,
    "call_id": call_id,
    "status": "transcribing",
})

# 5. Every SQS send
logger.debug("Pushing event to SQS", extra={
    "queue": queue_url.split("/")[-1],   # queue name only, not full URL
    "event_type": event.get("event_type"),
    "tenant_id": tenant_id,
    "call_id": call_id,
})

# 6. Every WebSocket push attempt
logger.debug("Pushing WebSocket event to agent", extra={
    "agent_id": agent_id,
    "connection_id": connection_id,
    "event_type": event_type,
})

# 7. Function exit — always last line before return
logger.debug("Handler completed", extra={
    "duration_ms": int((time.time() - start_time) * 1000),
    "result_status": response_status,
})
```

**Every function must also log a clean INFO-level pipeline event at each major transition:**

```python
logger.info("Call pipeline: recording saved", extra={
    "tenant_id": tenant_id,
    "call_id": call_id,
    "agent_id": agent_id,
    "stage": "recording_saved",
    "next_stage": "transcription",
    "recording_size_bytes": file_size,
    "call_duration_sec": duration,
})
```

**On every exception — log the full context before re-raising or returning an error:**

```python
except Exception as e:
    logger.exception("Unexpected error in RecordingProcessor", extra={
        "tenant_id": tenant_id,
        "call_id": call_id,
        "agent_id": agent_id,
        "provider": provider,
        "error_type": type(e).__name__,
        "error_message": str(e),
        "sqs_message_id": message_id,
        "retry_attempt": retry_count,
    })
    raise  # Let SQS retry handle it
```

**The philosophy:** In production you can always turn down verbosity by changing `LOG_LEVEL` in Parameter Store (no redeploy needed). You cannot go back in time and add more logging to investigate an issue that already happened. Log everything now, filter later.

---

### 8.2 What to Log at Each Level

**DEBUG (dev only — not for prod by default):**
- Full incoming event payload
- DynamoDB GetItem/Query inputs and results
- Every external API call (URL, method, response code, duration)
- Internal function entry/exit with parameters
- S3 upload/download operations with sizes

**INFO (prod default — every important business event):**
- Function started with sanitized context (tenant_id, call_id — no PII like phone numbers unless needed for debugging)
- Call processing pipeline transitions (recording → transcribing → complete)
- WebSocket push (agent_id, event type, success/failure)
- Feature flag evaluation result (flag name, tier, result)
- New tenant signup
- Agent invitation sent
- Notes saved

**WARNING:**
- Webhook signature validation failed (include provider, IP, reason)
- Agent not found for identifier (include identifier type, not the value)
- AWS Transcribe job took longer than expected
- Retrying failed external API call (attempt number, error)
- WebSocket connection not found for agent (agent may not be connected)
- Feature flag not found (returning default)

**ERROR:**
- Recording download failed after all retries
- DynamoDB write failed
- Transcription job failed
- Bedrock API call failed
- WebSocket send failed

### 8.3 Structured Log Format

Every log message must include these fields in the `extra` dict where available:

```python
logger.info(
    "Call recording saved to S3",
    extra={
        "tenant_id": tenant_id,
        "call_id": call_id,
        "agent_id": agent_id,          # if known
        "provider": provider,
        "s3_key": s3_key,
        "duration_ms": elapsed_ms,     # for performance logging
        "function_version": context.function_version,
    }
)
```

**Never log:**
- Full phone numbers in prod (log last 4 digits only: `****4567`)
- Recording URLs (contain auth tokens)
- JWT tokens
- Secrets Manager values
- Full transcript content at INFO level (too verbose, use DEBUG)

### 8.4 CloudWatch Log Groups

Every Lambda function has a dedicated log group: `/aws/lambda/sotto-{FunctionName}-{env}`

Log retention: 30 days in dev, 90 days in prod.

### 8.5 CloudWatch Alarms

Define these alarms in the SAM template (prod only):

| Alarm | Metric | Threshold | Action |
|---|---|---|---|
| `sotto-webhook-errors-{env}` | Lambda errors on `ProviderWebhookHandler` | >5 errors in 5 min | SNS notify |
| `sotto-recording-dlq-{env}` | Messages in `sotto-call-events-dlq` | >0 messages | SNS notify + trigger rollback consideration |
| `sotto-transcription-failures-{env}` | Lambda errors on `TranscriptionResultProcessor` | >3 errors in 10 min | SNS notify |
| `sotto-high-latency-{env}` | Lambda P95 duration on `RecordingProcessor` | >30s | SNS notify |
| `sotto-error-rate-{env}` | Lambda error rate across all functions | >1% for 5 min | SNS notify + auto-trigger rollback |

The `sotto-error-rate` alarm triggers the `rollback.yml` GitHub Actions workflow via an SNS→Lambda→GitHub API call chain.

### 8.6 X-Ray Tracing

X-Ray is enabled at the Lambda function level (`Tracing: Active` in SAM globals).

Use `@tracer.capture_method` on any method that calls external services:

```python
@tracer.capture_method
def download_recording(self, url: str, auth: tuple) -> bytes:
    ...
```

Use subsegments for clarity:

```python
with tracer.provider.in_subsegment("dynamodb_write"):
    table.put_item(Item=call_record)
```

---

## 9. CI/CD Pipeline — Complete Specification

### 9.1 GitHub Repository Setup

**Branches:**
- `main` — always deployable, protected branch, requires PR + passing checks
- `feature/*` — development branches
- No long-lived `staging` or `develop` branches — trunk-based development

**Branch protection rules for `main`:**
- Require PR with at least 1 review
- Require all status checks to pass: `pr-checks / lint`, `pr-checks / test`, `pr-checks / validate`
- No force push
- No deletion

**GitHub Secrets required (set in repo Settings → Secrets):**

```
AWS_ACCOUNT_ID            — AWS account number
AWS_REGION                — e.g., ca-central-1 (Canada)
DEV_OIDC_ROLE_ARN         — IAM role ARN for GitHub Actions dev deploys
PROD_OIDC_ROLE_ARN        — IAM role ARN for GitHub Actions prod deploys
COGNITO_ADMIN_EMAIL_DEV   — Initial admin email for dev Cognito
COGNITO_ADMIN_EMAIL_PROD  — Initial admin email for prod Cognito
SNS_ALERT_EMAIL           — Email for CloudWatch alarm notifications
```

**GitHub Environments:**
- `dev` — no approval required, auto-deploy on merge to main
- `prod` — requires manual approval from one owner before deploy proceeds

### 9.2 Workflow: `pr-checks.yml`

**Trigger:** `pull_request` targeting `main`

```yaml
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.13' }
      - run: pip install ruff bandit
      - run: ruff check backend/src/          # Linting
      - run: ruff format --check backend/src/ # Formatting check
      - run: bandit -r backend/src/ -ll       # Security scan

  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.13' }
      - run: pip install -r backend/requirements-dev.txt
      - run: pytest backend/src/tests/ -v --tb=short --cov=backend/src --cov-report=xml
      - uses: codecov/codecov-action@v4   # Optional coverage reporting

  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/setup-sam@v2
      - run: sam validate --template backend/template.yaml --lint
```

### 9.3 Workflow: `deploy-dev.yml`

**Trigger:** `push` to `main`

```yaml
name: Deploy to Dev

on:
  push:
    branches: [main]

permissions:
  id-token: write
  contents: read

jobs:
  deploy-backend:
    runs-on: ubuntu-latest
    environment: dev
    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.DEV_OIDC_ROLE_ARN }}
          aws-region: ${{ secrets.AWS_REGION }}

      - uses: aws-actions/setup-sam@v2
      - uses: actions/setup-python@v5
        with: { python-version: '3.13' }

      - name: SAM Build
        run: sam build --template backend/template.yaml --use-container

      - name: SAM Deploy (dev)
        run: |
          sam deploy \
            --stack-name sotto-dev \
            --s3-bucket sotto-artifacts-${{ secrets.AWS_ACCOUNT_ID }}-dev \
            --parameter-overrides Environment=dev LogLevel=DEBUG \
            --no-confirm-changeset \
            --no-fail-on-empty-changeset \
            --region ${{ secrets.AWS_REGION }}

      - name: Run smoke tests
        run: |
          API_URL=$(aws cloudformation describe-stacks \
            --stack-name sotto-dev \
            --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
            --output text)
          curl -f "$API_URL/health" || exit 1

  deploy-frontend:
    runs-on: ubuntu-latest
    needs: deploy-backend
    environment: dev
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.DEV_OIDC_ROLE_ARN }}
          aws-region: ${{ secrets.AWS_REGION }}
      - uses: actions/setup-node@v4
        with: { node-version: '20' }
      - run: cd frontend/admin-portal && npm ci && npm run build
      - name: Sync to S3
        run: |
          BUCKET=sotto-portal-${{ secrets.AWS_ACCOUNT_ID }}-dev
          aws s3 sync frontend/admin-portal/dist/ s3://$BUCKET --delete
          CF_ID=$(aws cloudformation describe-stacks \
            --stack-name sotto-dev \
            --query "Stacks[0].Outputs[?OutputKey=='CloudFrontId'].OutputValue" \
            --output text)
          aws cloudfront create-invalidation --distribution-id $CF_ID --paths "/*"
```

### 9.4 Workflow: `deploy-prod.yml`

**Trigger:** Push of a version tag (`v*.*.*`) OR manual `workflow_dispatch`

```yaml
name: Deploy to Production

on:
  push:
    tags: ['v[0-9]+.[0-9]+.[0-9]+']
  workflow_dispatch:
    inputs:
      deployment_reason:
        description: 'Reason for manual deploy'
        required: true

permissions:
  id-token: write
  contents: read

jobs:
  deploy-prod:
    runs-on: ubuntu-latest
    environment: prod          # Requires manual approval in GitHub UI
    steps:
      - uses: actions/checkout@v4

      - name: Generate deployment ID
        id: deploy-id
        run: echo "deployment_id=$(uuidgen)" >> $GITHUB_OUTPUT

      - name: Configure AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.PROD_OIDC_ROLE_ARN }}
          aws-region: ${{ secrets.AWS_REGION }}

      - uses: aws-actions/setup-sam@v2
      - uses: actions/setup-python@v5
        with: { python-version: '3.13' }

      - name: SAM Build
        run: sam build --template backend/template.yaml --use-container

      - name: SAM Deploy (prod — canary strategy)
        run: |
          sam deploy \
            --stack-name sotto-prod \
            --s3-bucket sotto-artifacts-${{ secrets.AWS_ACCOUNT_ID }}-prod \
            --parameter-overrides Environment=prod LogLevel=INFO \
            --no-confirm-changeset \
            --no-fail-on-empty-changeset \
            --region ${{ secrets.AWS_REGION }}
        # SAM DeploymentPreference in template.yaml handles canary routing

      - name: Record deployment
        run: |
          aws dynamodb put-item \
            --table-name sotto-deployments-prod \
            --item '{
              "deployment_id": {"S": "${{ steps.deploy-id.outputs.deployment_id }}"},
              "git_sha": {"S": "${{ github.sha }}"},
              "git_tag": {"S": "${{ github.ref_name }}"},
              "status": {"S": "canary"},
              "deployed_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
              "deployed_by": {"S": "${{ github.run_id }}"}
            }'

      - name: Monitor canary for 10 minutes
        run: |
          # Check CloudWatch error rate alarm every 60s for 10 minutes
          for i in $(seq 1 10); do
            ALARM_STATE=$(aws cloudwatch describe-alarms \
              --alarm-names "sotto-error-rate-prod" \
              --query "MetricAlarms[0].StateValue" \
              --output text)
            echo "Minute $i: Error rate alarm = $ALARM_STATE"
            if [ "$ALARM_STATE" = "ALARM" ]; then
              echo "ERROR RATE ALARM TRIGGERED — initiating rollback"
              exit 1
            fi
            sleep 60
          done
          echo "Canary period passed. Deployment stable."

      - name: Deploy frontend
        if: success()
        run: |
          cd frontend/admin-portal
          npm ci
          VITE_API_URL=$(aws cloudformation describe-stacks \
            --stack-name sotto-prod \
            --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
            --output text)
          VITE_API_URL=$VITE_API_URL npm run build
          BUCKET=sotto-portal-${{ secrets.AWS_ACCOUNT_ID }}-prod
          aws s3 sync dist/ s3://$BUCKET --delete
          CF_ID=$(aws cloudformation describe-stacks \
            --stack-name sotto-prod \
            --query "Stacks[0].Outputs[?OutputKey=='CloudFrontId'].OutputValue" \
            --output text)
          aws cloudfront create-invalidation --distribution-id $CF_ID --paths "/*"
```

### 9.5 Workflow: `rollout-promote.yml`

**Trigger:** Manual `workflow_dispatch`

```yaml
name: Promote Rollout Tier

on:
  workflow_dispatch:
    inputs:
      from_tier:
        description: 'Current tier to promote FROM'
        required: true
        type: choice
        options: [beta, live_test]
      to_tier:
        description: 'Tier to promote TO'
        required: true
        type: choice
        options: [live_test, full]
      deployment_id:
        description: 'Deployment ID to promote'
        required: true

jobs:
  promote:
    runs-on: ubuntu-latest
    environment: prod
    steps:
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.PROD_OIDC_ROLE_ARN }}
          aws-region: ${{ secrets.AWS_REGION }}
      - name: Call RolloutManager Lambda
        run: |
          aws lambda invoke \
            --function-name sotto-RolloutManager-prod \
            --payload '{"action":"promote","from_tier":"${{ inputs.from_tier }}","to_tier":"${{ inputs.to_tier }}","deployment_id":"${{ inputs.deployment_id }}"}' \
            --cli-binary-format raw-in-base64-out \
            /tmp/response.json
          cat /tmp/response.json
```

### 9.6 Workflow: `rollback.yml`

**Trigger:** Manual `workflow_dispatch` OR triggered automatically by CloudWatch alarm via SNS→Lambda→GitHub API

```yaml
name: Emergency Rollback

on:
  workflow_dispatch:
    inputs:
      deployment_id:
        description: 'Deployment ID to roll back'
        required: true
      reason:
        description: 'Reason for rollback'
        required: true

jobs:
  rollback:
    runs-on: ubuntu-latest
    environment: prod
    steps:
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.PROD_OIDC_ROLE_ARN }}
          aws-region: ${{ secrets.AWS_REGION }}

      - name: Execute rollback via SAM
        run: |
          # SAM CodeDeploy rollback — reverts Lambda alias to previous version
          aws deploy stop-deployment \
            --deployment-id $(aws deploy list-deployments \
              --application-name sotto-prod \
              --deployment-group-name sotto-prod-DeploymentGroup \
              --query "deployments[0]" \
              --output text) \
            --auto-rollback-enabled

      - name: Update deployment record
        run: |
          aws dynamodb update-item \
            --table-name sotto-deployments-prod \
            --key '{"deployment_id": {"S": "${{ inputs.deployment_id }}"}}' \
            --update-expression "SET #s = :s, rolled_back_at = :t" \
            --expression-attribute-names '{"#s": "status"}' \
            --expression-attribute-values '{":s":{"S":"rolled_back"},":t":{"S":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}}'

      - name: Notify team
        run: |
          aws sns publish \
            --topic-arn arn:aws:sns:${{ secrets.AWS_REGION }}:${{ secrets.AWS_ACCOUNT_ID }}:sotto-alerts-prod \
            --subject "ROLLBACK EXECUTED — ${{ inputs.reason }}" \
            --message "Deployment ${{ inputs.deployment_id }} rolled back. Reason: ${{ inputs.reason }}. Run ID: ${{ github.run_id }}"
```

---

## 10. Deployment Strategy — Per-Tenant Staged Rollout

### 10.1 Lambda Deployment Configuration (SAM)

In `template.yaml`, every Lambda function that needs canary deployment uses:

```yaml
ProviderWebhookHandler:
  Type: AWS::Serverless::Function
  Properties:
    ...
    AutoPublishAlias: live
    DeploymentPreference:
      Type: !If [IsProd, Canary10Percent5Minutes, AllAtOnce]
      Alarms:
        - !Ref ErrorRateAlarm
      Hooks:
        PreTraffic: !Ref PreTrafficHook
      TriggerConfigurations:
        - TriggerEvents: [DeploymentRollback]
          TriggerName: RollbackNotification
          TriggerTargetArn: !Ref AlertTopic
```

`Canary10Percent5Minutes` means: route 10% of traffic to the new version for 5 minutes. If alarms don't fire, shift 100% to new version. If alarms fire, auto-rollback.

### 10.2 Per-Tenant Feature Flag System

**How it works:**

1. Each tenant in DynamoDB has `deployment_tier`: `beta | live_test | full`
2. Feature flags in `sotto-feature-flags-{env}` define which tiers can access which features
3. At runtime, Lambda checks flags before executing new code paths

**Example flag check** (in `sotto/feature_flags.py`):

```python
def is_enabled(flag_name: str, tenant_id: str, db_client) -> bool:
    """Check if a feature flag is enabled for this tenant."""
    # Get tenant's deployment tier
    tenant = db_client.get_tenant(tenant_id)
    tier = tenant.get('deployment_tier', 'full')

    # Get flag definition
    flag = db_client.get_feature_flag(flag_name)
    if not flag:
        logger.warning(f"Feature flag {flag_name} not found, returning default=True")
        return True

    enabled_tiers = flag.get('enabled_tiers', ['full'])
    result = tier in enabled_tiers

    logger.debug(
        f"Feature flag evaluated",
        extra={"flag": flag_name, "tier": tier, "result": result}
    )
    return result
```

**Usage in Lambda:**

```python
if feature_flags.is_enabled('ai_summary', tenant_id, db):
    summary = await summarizer.run(transcript)
```

### 10.3 Rollout Sequence for a New Release

```
Day 0:  Tag release (v1.2.0)
        → CI deploys to prod as new Lambda version
        → CodeDeploy routes 10% traffic to new version
        → Monitor 5 minutes → if clean → 100% traffic shifted
        → All tenants still on their current feature flag tier

Day 1:  (Manual) Run rollout-promote.yml: beta → live_test
        → RolloutManager updates all `beta` tenants to `live_test` tier
        → New features now visible to live_test tenants

Day 3:  (Manual) Run rollout-promote.yml: live_test → full
        → All tenants now on full tier
        → New features available to everyone

Rollback: Run rollback.yml at any point
          → Reverts Lambda alias (infrastructure)
          → Run rollout-promote in reverse to revert feature flags
```

### 10.4 Feature Flags Initial Setup

Seed these flags in DynamoDB on first deploy:

| `flag_name` | `enabled_tiers` | `description` |
|---|---|---|
| `ai_summary` | `['beta']` | AI call summarization via Bedrock |
| `epic_dom_injection` | `['beta', 'live_test']` | Applied Epic DOM phone number injection |
| `caller_id_matching` | `['beta', 'live_test', 'full']` | Match caller to client record |
| `action_items` | `['beta']` | Extract action items from transcript |

---

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

## 15. Build Sequence

Build in this exact order. Each step depends on the previous.

**Step 1: Repository & Infrastructure Foundation**
- [ ] Create GitHub repository with branch protection
- [ ] Create AWS IAM OIDC identity provider for GitHub Actions
- [ ] Create IAM roles for dev and prod (with OIDC trust policy)
- [ ] Create artifact S3 buckets manually (bootstrap — SAM needs them to exist before first deploy)
- [ ] Write `backend/template.yaml` — all DynamoDB tables, S3 buckets, Cognito User Pools only (no Lambdas yet)
- [ ] Write `backend/samconfig.toml`
- [ ] Deploy dev stack: `sam deploy --guided --stack-name sotto-dev`
- [ ] Verify all tables and buckets created in AWS console

**Step 2: Common Layer**
- [ ] Build `sotto` Python package in `src/layers/common/python/sotto/`
- [ ] Implement `logger.py` (Powertools setup)
- [ ] Implement `db.py` (DynamoDB client with helper methods for each table)
- [ ] Implement `models.py` (Pydantic models: NormalizedCallEvent, Tenant, Agent, Call, etc.)
- [ ] Implement `adapters/base.py` (abstract adapter)
- [ ] Implement `adapters/twilio.py` (Twilio adapter — MVP)
- [ ] Add layer to `template.yaml`
- [ ] Write unit tests for adapter normalization

**Step 3: Webhook Entry Point**
- [ ] Implement `ProviderWebhookHandler`
- [ ] Add to `template.yaml` with SQS trigger and IAM role
- [ ] Write unit tests (mock Twilio signature, assert SQS message sent)
- [ ] Test locally with `sam local start-api`

**Step 4: Call Processing Pipeline**
- [ ] Implement `RecordingProcessor` (SQS consumer)
- [ ] Implement `TranscriptionInit`
- [ ] Implement `TranscriptionResultProcessor` (EventBridge consumer)
- [ ] Implement `AISummarizer` (with feature flag check)
- [ ] Add EventBridge rule for Transcribe completion events
- [ ] Write integration test: feed a test audio file, verify transcript appears in DynamoDB

**Step 5: WebSocket Infrastructure**
- [ ] Implement `WSConnect`, `WSDisconnect`, `WSDefault`
- [ ] Add WebSocket API to `template.yaml`
- [ ] Implement `sotto/ws_publisher.py` utility
- [ ] Integrate `ws_publisher` into `RecordingProcessor` and `TranscriptionResultProcessor`
- [ ] Test: connect a WebSocket client, trigger a test event, verify message received

**Step 6: Auth & Admin API**
- [ ] Implement `AdminSignup`
- [ ] Implement `AgentInvite` and `AgentConfirm`
- [ ] Implement `NumberMappingHandler`
- [ ] Implement `TenantConfigHandler`
- [ ] Add Cognito User Pools to `template.yaml`
- [ ] Configure Cognito pre-token generation trigger
- [ ] Test full signup → invite agent → agent confirms flow

**Step 7: Agent API**
- [ ] Implement `CallHistoryHandler`
- [ ] Implement `CallDetailHandler`
- [ ] Implement `NotesHandler`
- [ ] Write tests for all agent API endpoints

**Step 8: CI/CD Pipeline**
- [ ] Write `pr-checks.yml`
- [ ] Write `deploy-dev.yml`
- [ ] Write `deploy-prod.yml`
- [ ] Write `rollout-promote.yml`
- [ ] Write `rollback.yml`
- [ ] Set all required GitHub Secrets
- [ ] Create GitHub Environments (dev, prod) with protection rules
- [ ] Push to main and verify auto-deploy to dev works end-to-end

**Step 9: Deployment Strategy**
- [ ] Add `DeploymentPreference` to all Lambdas in `template.yaml`
- [ ] Create CloudWatch alarms
- [ ] Implement `RolloutManager` Lambda
- [ ] Seed initial feature flags in DynamoDB
- [ ] Test canary deploy: deploy a change, verify 10% routing, verify alarm monitoring

**Step 10: Chrome Extension**
- [ ] Set up extension project structure
- [ ] Implement `manifest.json`
- [ ] Implement `service_worker.js` (WebSocket manager + auth)
- [ ] Implement `sidepanel.js` and `index.html` (Cockpit UI)
- [ ] Implement `epic_injector.js` (DOM injection)
- [ ] Load extension unpacked in Chrome
- [ ] Test end-to-end: trigger test call → verify Cockpit activates → verify transcript appears

**Step 11: Admin Portal**
- [ ] Set up Vite + React project
- [ ] Implement auth (Cognito login)
- [ ] Implement all pages (Dashboard, Agents, Numbers, Calls, Settings)
- [ ] Deploy to S3 + CloudFront
- [ ] Test full admin flow: signup → connect Twilio → invite agent → view call history

**Step 12: End-to-End Testing & Hardening**
- [ ] Full end-to-end test with real Twilio trial number
- [ ] Verify all CloudWatch alarms configured and firing correctly
- [ ] Verify rollback workflow works
- [ ] Verify per-tenant feature flag rollout works
- [ ] Review all IAM policies for least privilege
- [ ] Verify all secrets are in Secrets Manager, none in env vars

---

## 16. Common Pitfalls to Avoid

1. **Twilio requires a response within 3 seconds.** The `ProviderWebhookHandler` must respond 200 immediately and push everything to SQS. Never do synchronous processing in the webhook handler.

2. **AWS Transcribe job names must be unique.** Use `sotto-{env}-{call_id}` and never reuse. If retrying a failed transcription, generate a new call_id.

3. **WebSocket connections expire after 2 hours** (API Gateway limit). The extension must reconnect. Handle `410 Gone` errors from `execute-api:ManageConnections` — this means the connection is stale, delete it from DynamoDB.

4. **Cognito JWTs expire after 1 hour.** The extension must use the refresh token to get a new ID token before the WebSocket token expires. Implement a timer in the service worker.

5. **SQS visibility timeout must be longer than Lambda timeout.** If Lambda times out processing a message, the message becomes visible again and gets retried. Set visibility timeout to 2× the Lambda timeout.

6. **DynamoDB GSI projection.** If a GSI only projects certain attributes and you need more, you'll get `nil` values. Either project `ALL` attributes (costs more) or do a second GetItem to fetch the full record.

7. **SAM `AutoPublishAlias` creates a new version on every deploy.** Lambda versions are immutable. If you need to change just environment variables without a code change, you still get a new version. This is expected behavior.

8. **Applied Epic DOM selectors will break when Epic updates their UI.** Document all selectors, write them defensively (try multiple fallback selectors), and monitor for failures via CloudWatch.

9. **Chrome MV3 service workers terminate when idle.** Do not store state in service worker variables — use `chrome.storage.session` for ephemeral state and `chrome.storage.local` for persistent state.

10. **Recording downloads can be large (50+ MB for long calls).** The `RecordingProcessor` Lambda needs enough memory and a long enough timeout. Stream the download directly to S3 using multipart upload rather than loading the entire file into memory.
