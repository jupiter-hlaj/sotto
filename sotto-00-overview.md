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
