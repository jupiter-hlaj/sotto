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

