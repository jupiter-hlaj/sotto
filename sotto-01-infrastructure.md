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

