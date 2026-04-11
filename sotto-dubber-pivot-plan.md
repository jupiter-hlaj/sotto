# Sotto — Dubber Pivot Build Plan

**Status:** active plan. Replaces the old Teams Phone integration milestones (M2 / T-3, T-4, T-5).
**Created:** 2026-04-11
**Authoritative companions:**
- `sotto-teams-pivot-decision.md` — why we pivoted, full reasoning
- `alternate_reality_blueprint.txt` — the product blueprint we're implementing
- `CLAUDE.md` — immutable rules (Python 3.13, arm64, SAM only, secrets in Secrets Manager)

---

## How to read this plan

- **Phases** (D-0 → D-9) are milestones. **Hard stop** at the end of every phase. No starting the next phase without explicit approval.
- **Sub-tasks** inside each phase each map to **one git commit**. Never batched.
- **Acceptance test** = the "definition of done" for the phase. If the test doesn't pass, the phase isn't done.
- **Blocked on** flags external dependencies (Dubber sales, customer action, prior phase).
- **Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked
- This file is the single source of truth. If work happens that isn't on this plan, it's a side quest. Call me on it.

---

## Locked architectural decisions (do not relitigate without flagging)

1. **Modify, not restart.** Existing Steps 1–12 + M3 stay. Dubber slots in alongside Twilio/RingCentral/Zoom/8x8 as a fifth provider, reusing the `recording_already_uploaded` branch from T-7b.
2. **No new AWS services for Engine A.** Live ringing pop reuses the existing API Gateway WebSocket infra from Step 6. **No IoT Core.** The blueprint suggests it; we override.
3. **Sotto never creates `CsTeamsComplianceRecordingPolicy`.** Dubber owns that as the certified recorder. Sotto only manages security group membership via standard Microsoft Graph.
4. **No customer admin credentials in Sotto.** Engine B runs as a Managed Identity inside the customer's own Azure tenant. Sotto holds zero admin secrets per customer.
5. **Per-customer regional S3 vaults** (Engine C) replace the single `sotto-recordings-{account_id}` bucket pattern, with backwards-compatible fallback during rollout.
6. **Multi-tenant Azure AD app stays.** Same registration that survived M1/M1.5/M2. Extended scopes only.

---

## Phase D-0 — Discovery & secrets (mostly done)

**Goal:** Get Dubber registration + credentials in place. Get the 10 sales-discovery answers. Land this plan doc.

| # | Sub-task | Status |
|---|---|---|
| D-0.1 | Register Sotto as Dubber partner; obtain 9 Mashery keys (8 regional prod + sandbox) | [x] |
| D-0.2 | Write 9 secrets to AWS Secrets Manager under `sotto/dubber/{region}` | [x] |
| D-0.3 | Gitignore `dubberkeys.txt`, delete from disk, commit + push | [x] |
| D-0.4 | Sales discovery call with Dubber — 10 questions answered | [!] blocked on user |
| D-0.5 | This plan doc committed and pushed | [~] |

**The 10 sales-discovery questions** (must be answered before D-3):
1. Pricing model and per-unit rate (kills or confirms AUD 35-55 retail math)
2. Partner provisioning API for sub-account creation
3. White-label disclosure rules ("Powered by Dubber"?)
4. Webhooks vs polling for new recordings (determines D-3.3 architecture)
5. Audio format / channel layout (mono vs stereo, sample rate)
6. SLA
7. US-only data residency option for compliance-sensitive customers
8. SOC 2 / HIPAA roadmap
9. Termination data portability
10. Microsoft cert renewal contingencies

**Hard stop:** None — D-1 spike can begin against the sandbox while sales answers are pending. But D-3 cannot start without the sales answers.

---

## Phase D-1 — Sandbox spike (throwaway code, technical viability gate)

**Goal:** Prove the sandbox API works end-to-end against the existing Sotto pipeline. Answer the technical questions a salesperson can't (audio format, metadata shape, latency).

| # | Sub-task | Status |
|---|---|---|
| D-1.1 | Add `scripts/dubber_spike.py` — load `sotto/dubber/sandbox` from Secrets Manager, do OAuth, print token TTL (NOT the token) | [ ] |
| D-1.2 | Extend script: list-recordings endpoint; capture response shape into a markdown report | [ ] |
| D-1.3 | Extend script: download one recording; print file size + content type + sample rate | [ ] |
| D-1.4 | Extend script: pull call metadata for that recording; capture shape | [ ] |
| D-1.5 | Manually drop the downloaded recording through `RecordingProcessor.recording_already_uploaded` end-to-end → Cockpit. Document each handoff in `scripts/D-1-spike-report.md` | [ ] |

**Files touched:**
- `scripts/dubber_spike.py` (new, throwaway)
- `scripts/D-1-spike-report.md` (new, kept as reference)

**Acceptance test:** One real Dubber sandbox recording passes end-to-end through the existing Sotto pipeline (Transcribe → Bedrock → DynamoDB → Cockpit).

**Hard stop:** Technical viability gate. If audio format is wrong, metadata can't be mapped to a Sotto tenant, or latency is unworkable — stop and replan.

**Blocked on:** Nothing. Sandbox is `active` in Dubber.

---

## Phase D-2 — Dead code cleanup + arm64 fix

**Goal:** Remove the M2 OAuth callback path Microsoft killed. Salvage the reusable bits. Fix the long-pending arm64 violation. Make room for Dubber.

| # | Sub-task | Status |
|---|---|---|
| D-2.1 | Extract reusable bits from `handlers/teams/graph_client.py` into common-layer `sotto/azure_graph.py` (token + group membership only; NO compliance recording policy) | [ ] |
| D-2.2 | Delete `backend/src/handlers/teams/onboarding.py` and its M2-specific tests | [ ] |
| D-2.3 | Delete `TeamsOnboardingFunction` from `template.yaml`; delete unused ECS Fargate placeholder params (`VpcId`, `PublicSubnets`, `BotTLSCertificateArn`) | [ ] |
| D-2.4 | Delete the dummy `test-trial-tenant` row from `sotto-tenants-dev` (data only — no commit) | [ ] |
| D-2.5 | Fix `template.yaml` Globals: x86_64 → arm64 per CLAUDE.md (long-pending carry-over) | [ ] |
| D-2.6 | Run full test suite + `sam validate` + `sam build`; fix any breakage | [ ] |

**Files touched:**
- `backend/src/handlers/teams/onboarding.py` (delete)
- `backend/src/handlers/teams/graph_client.py` (gut → reusable bits move to azure_graph.py)
- `backend/src/layers/common/python/sotto/azure_graph.py` (new)
- `backend/src/tests/teams/test_onboarding.py` (delete)
- `template.yaml`

**Acceptance test:** `pytest backend/src/tests/` green; `sam validate` + `sam build` green; CI green; deployed-to-dev still functional for the four existing providers.

**Hard stop:** All tests green, CI green.

**Blocked on:** Nothing.

---

## Phase D-3 — Dubber ingestion path (the heart of the pivot)

**Goal:** Treat Dubber as a fifth provider. Wire it into the existing pipeline via the `recording_already_uploaded` branch from T-7b.

| # | Sub-task | Status |
|---|---|---|
| D-3.1 | Add `dubber` to the Providers enum + `dubber_account_id` / `dubber_region` fields on `sotto-tenants` (migration defaults to None for existing rows) | [ ] |
| D-3.2 | New `backend/src/handlers/dubber/dubber_client.py` — API client. Methods: OAuth, list_recordings, get_recording_metadata, download_url, delete_recording. Loads creds from `sotto/dubber/{region}`. Unit tests with `moto` + `responses` | [ ] |
| D-3.3 | New `backend/src/handlers/dubber/recording_handler.py` — webhook receiver (preferred) OR poller (fallback if Dubber doesn't push). Normalizes into `NormalizedCallEvent`, drops to `sotto-call-events` SQS | [ ] |
| D-3.4 | Wire `DubberRecordingFunction` Lambda + API GW route `POST /dubber/webhook` + IAM perms for `secretsmanager:GetSecretValue` on `sotto/dubber/*` into `template.yaml` | [ ] |
| D-3.5 | Extend `RecordingProcessor.recording_already_uploaded` for the Dubber path: fetch download URL, stream-copy to S3, delete from Dubber. Unit test | [ ] |
| D-3.6 | Provider-specific Transcribe channel labels for Dubber (mono vs stereo — TBD by spike). Update `TranscriptionInit`. Unit test | [ ] |
| D-3.7 | Integration test: simulated Dubber webhook → end-to-end → call appears in Cockpit | [ ] |

**Files touched:**
- `backend/src/layers/common/python/sotto/models.py` (new enum value)
- `backend/src/handlers/dubber/__init__.py` (new)
- `backend/src/handlers/dubber/dubber_client.py` (new)
- `backend/src/handlers/dubber/recording_handler.py` (new)
- `backend/src/handlers/recording_processor.py` (extend)
- `backend/src/handlers/transcription_init.py` (extend)
- `template.yaml`
- tests

**Acceptance test:** A real Dubber sandbox recording → webhook → S3 → Transcribe → Bedrock → Cockpit, with the call linked to a real test tenant.

**Hard stop:** End-to-end works for one real recording from one real tenant.

**Blocked on:** D-1 done (audio format, metadata shape known) AND D-0.4 sales answers (webhook vs polling, audio format).

---

## Phase D-4 — Engine C: Regional vaults

**Goal:** Replace the single global recordings bucket with per-customer regional vaults in their chosen jurisdiction.

| # | Sub-task | Status |
|---|---|---|
| D-4.1 | Add `vault_region`, `vault_bucket_arn` fields to `sotto-tenants`. Migration | [ ] |
| D-4.2 | New `backend/src/handlers/admin/provision_vault.py` — admin-triggered Lambda that creates the regional S3 bucket, applies bucket policy + KMS, writes ARN back to tenant record. Unit test | [ ] |
| D-4.3 | Wire `provision_vault.py` into `template.yaml` + admin API route `POST /tenants/{tenant_id}/vault` | [ ] |
| D-4.4 | Modify `RecordingProcessor` to write to tenant's `vault_bucket_arn`; fall back to global bucket if None (backwards compat) | [ ] |
| D-4.5 | Modify Dubber download-and-delete loop from D-3.5: download → upload to client vault → delete from Dubber | [ ] |
| D-4.6 | Admin portal: add "Vault region" picker (CA / US / UK) to `Settings.jsx` | [ ] |
| D-4.7 | Integration test: provision a vault in `us-east-2`, run a Dubber recording through it, confirm file lands in the new bucket | [ ] |

**Files touched:**
- schema, `sotto/models.py`
- `backend/src/handlers/admin/provision_vault.py` (new)
- `backend/src/handlers/recording_processor.py` (extend)
- `backend/src/handlers/dubber/recording_handler.py` (extend)
- `template.yaml`
- `frontend/admin-portal/src/pages/Settings.jsx` (extend)
- tests

**Acceptance test:** A test tenant with `vault_region=us-east-2` lands a Dubber recording in their own bucket in Ohio, not in the global bucket.

**Hard stop:** **MAJOR HARD STOP — D-3 + D-4 = "baseline Dubber product."** This is the demo-or-kill moment. Wait for explicit go/no-go from user before any Engine A/B work. Premature engine work risks building on a foundation that hasn't been validated against real Dubber + real Microsoft + real customer.

**Blocked on:** D-3 done.

---

## Phase D-5 — Engine A: Live ringing pop + call controls

**Goal:** Microsoft Graph webhook fires on `Ringing`, Lambda pushes to Cockpit via existing API GW WebSockets, Cockpit shows caller info + mute/end/transfer buttons that route back through Lambda to Graph Calls API.

| # | Sub-task | Status |
|---|---|---|
| D-5.1 | Document the extended Azure AD scopes (`Calls.ReadWrite.All`, `Calls.AccessMedia.All`, `User.Read.All`, `Directory.Read.All`, `offline_access`) + admin consent URL format. User performs Azure portal action, we commit the doc | [ ] |
| D-5.2 | New `backend/src/handlers/teams/call_subscription.py` — creates Microsoft Graph subscription for `communications/calls` events scoped to a tenant. Unit test | [ ] |
| D-5.3 | New `backend/src/handlers/teams/call_event_webhook.py` — receives Graph notifications on Ringing state, validates client state, looks up agent, pushes to Cockpit via `sotto-ws-connections`. Reuses Step 6 WebSocket infra. Unit test | [ ] |
| D-5.4 | Wire both Lambdas + API GW routes into `template.yaml` | [ ] |
| D-5.5 | Cockpit: render the ringing-pop card in `sidepanel.js` | [ ] |
| D-5.6 | New `backend/src/handlers/teams/call_control.py` — receives mute/end/transfer commands from Cockpit, calls Graph Calls API. Unit test | [ ] |
| D-5.7 | Wire `call_control` Lambda + API GW route | [ ] |
| D-5.8 | Cockpit: mute/end/transfer buttons + WebSocket round-trip | [ ] |
| D-5.9 | Integration test: simulate Graph webhook → see pop in extension → click mute → verify Graph Calls API was called | [ ] |

**Files touched:**
- `backend/src/handlers/teams/call_subscription.py` (new)
- `backend/src/handlers/teams/call_event_webhook.py` (new)
- `backend/src/handlers/teams/call_control.py` (new)
- `template.yaml`
- `frontend/extension/src/sidepanel/sidepanel.js` (extend)
- tests

**Acceptance test:** Real Teams call rings → Cockpit pops with caller ID → mute button works → call audibly mutes for the agent.

**Hard stop:** Verified end-to-end with a real Teams call against a real Microsoft tenant.

**Blocked on:** D-4 done. Customer-side: extended Azure scopes consented in their tenant.

---

## Phase D-6 — Engine B: Azure Automation provisioning (the trickiest piece)

**Goal:** Customer admin clicks "Connect Teams" once. An Azure Automation account is deployed inside their own Azure tenant with a System-Assigned Managed Identity. The runbook executes `Grant-CsGroupPolicyAssignment` to link a security group to Dubber's compliance recording policy. Sotto never sees customer admin secrets.

| # | Sub-task | Status |
|---|---|---|
| D-6.1 | Author the Bicep template for the Azure Automation account + Managed Identity + runbook (`infrastructure/azure/dubber-bridge.bicep`) | [ ] |
| D-6.2 | Author the runbook PowerShell script — takes `securityGroupObjectId` and `dubberPolicyName` as params (`infrastructure/azure/dubber-bridge.runbook.ps1`) | [ ] |
| D-6.3 | New `backend/src/handlers/teams/onboarding_v2.py` — replaces the dead M2 OAuth callback. Generates the customer-facing "Deploy to Azure" URL with the Bicep template. Returns URL to the admin portal | [ ] |
| D-6.4 | Wire `onboarding_v2.py` into `template.yaml` | [ ] |
| D-6.5 | Admin portal `Settings.jsx`: "Connect Teams" button → opens deploy URL → on success, customer pastes Automation account ID back into Sotto | [ ] |
| D-6.6 | New `backend/src/handlers/teams/group_membership.py` — Lambda that takes (tenant_id, agent_id, action) and triggers the customer's Automation runbook to add/remove an agent from the recording security group. Uses Sotto's `User.Read.All` + `GroupMember.ReadWrite.All` Graph creds. Unit test | [ ] |
| D-6.7 | Wire `group_membership` Lambda + admin API route | [ ] |
| D-6.8 | Manual test: deploy the Bicep into a real M365 trial tenant, link a real security group, verify a recording happens via Dubber | [ ] |

**Files touched:**
- `infrastructure/azure/dubber-bridge.bicep` (new)
- `infrastructure/azure/dubber-bridge.runbook.ps1` (new)
- `backend/src/handlers/teams/onboarding_v2.py` (new — replaces deleted M2 onboarding.py)
- `backend/src/handlers/teams/group_membership.py` (new)
- `template.yaml`
- `frontend/admin-portal/src/pages/Settings.jsx` (extend)
- tests

**Acceptance test:** Real M365 trial tenant deploys Bicep, real recording happens via Dubber after a security group membership change.

**Hard stop:** First real customer-side deployment works without Sotto ever seeing customer admin creds.

**Blocked on:** D-5 done. Customer-side: deploying Bicep into their Azure tenant. **Risk:** Azure Automation + PowerShell runbook is a domain Sotto hasn't touched yet — biggest unknown of the whole pivot.

---

## Phase D-7 — RBAC Control Center

**Goal:** Per-role admin toggles in `Settings.jsx`: Sync-to-Epic, Self-Review, Download Rights, Live-Pop Visibility.

| # | Sub-task | Status |
|---|---|---|
| D-7.1 | Add `roles` field to `sotto-agents` (list of strings); migration sets all existing agents to `["agent"]` | [ ] |
| D-7.2 | Add `tenant_settings` field to `sotto-tenants` for the four toggles, keyed by role | [ ] |
| D-7.3 | Admin API endpoint to read/write `tenant_settings`. Unit test | [ ] |
| D-7.4 | Admin API endpoint to assign/unassign roles to agents. Unit test | [ ] |
| D-7.5 | Wire the four toggles into runtime: `RecordingProcessor` checks Self-Review, AdminAPI checks Download Rights, `call_event_webhook` checks Live-Pop Visibility, AISummarizer checks Sync-to-Epic (forward-ref to D-8) — each = one commit | [ ] |
| D-7.6 | Admin portal: extend `Settings.jsx` with the role × toggle matrix | [ ] |
| D-7.7 | Admin portal: extend `Agents.jsx` with role assignment | [ ] |

**Files touched:**
- schema, `sotto/models.py`
- `backend/src/handlers/admin/api.py` (extend)
- `backend/src/handlers/recording_processor.py` (extend)
- `backend/src/handlers/teams/call_event_webhook.py` (extend)
- `backend/src/handlers/agent_api.py` (extend)
- `frontend/admin-portal/src/pages/Settings.jsx` (extend)
- `frontend/admin-portal/src/pages/Agents.jsx` (extend)
- tests

**Acceptance test:** Toggle Live-Pop Visibility off for "back-office" role; verify back-office agents don't get the ringing pop.

**Hard stop:** All four toggles round-trip end-to-end.

**Blocked on:** D-5 done.

---

## Phase D-8 — Applied Epic write-back

**Goal:** After a call completes, log the recording link + AI summary into the matching Applied Epic client record.

| # | Sub-task | Status |
|---|---|---|
| D-8.1 | Research/document Applied Epic API auth (Epic is on-prem/SOAP for many shops — real unknown, may be blocked on customer-side IT). Doc only | [ ] |
| D-8.2 | New `backend/src/handlers/epic/epic_client.py` — Epic API client. Auth + write activity to client record. Unit test | [ ] |
| D-8.3 | New `backend/src/handlers/epic/post_call_writeback.py` — Lambda triggered after AISummarizer completes. Looks up client by phone number, writes link + summary. Respects "Sync-to-Epic" RBAC toggle from D-7. Unit test | [ ] |
| D-8.4 | Wire `post_call_writeback.py` into `template.yaml` + SQS subscription | [ ] |
| D-8.5 | Manual test against a real Epic instance | [ ] |

**Files touched:**
- `backend/src/handlers/epic/epic_client.py` (new)
- `backend/src/handlers/epic/post_call_writeback.py` (new)
- `template.yaml`
- tests

**Acceptance test:** A completed call shows up in Applied Epic with the recording link attached to the matching client.

**Hard stop:** One real Epic instance, one real call, link visible in Epic.

**Blocked on:** D-7 done. Customer-side: Epic API credentials. **Risk:** Applied Epic API access is famously inconsistent across customer deployments.

---

## Phase D-9 — Billing tied to MS Security Group headcount

**Goal:** Generate invoices based on active membership in the customer's Dubber recording security group, at the AUD 35-55 retail target.

| # | Sub-task | Status |
|---|---|---|
| D-9.1 | Add `subscription` field to `sotto-tenants` with `monthly_per_seat_aud` + `currency` | [ ] |
| D-9.2 | New `backend/src/handlers/billing/usage_snapshot.py` — daily Lambda that pulls security group membership count via Microsoft Graph for each tenant. Snapshots to a new `sotto-billing-snapshots` table. Unit test | [ ] |
| D-9.3 | Add `sotto-billing-snapshots` table to `template.yaml` | [ ] |
| D-9.4 | Wire `usage_snapshot.py` to a daily EventBridge schedule | [ ] |
| D-9.5 | New `backend/src/handlers/billing/invoice_generator.py` — month-end Lambda that aggregates snapshots, generates an invoice. Optionally pushes to Stripe (decision needed). Unit test | [ ] |
| D-9.6 | Wire `invoice_generator.py` + Stripe (if applicable) + admin portal billing page | [ ] |

**Files touched:**
- schema, `sotto/models.py`
- `backend/src/handlers/billing/usage_snapshot.py` (new)
- `backend/src/handlers/billing/invoice_generator.py` (new)
- `template.yaml`
- `frontend/admin-portal/src/pages/Billing.jsx` (new)
- tests

**Acceptance test:** Daily snapshot lands; month-end invoice generates with correct seat count for at least one real tenant.

**Hard stop:** First successful invoice generation against a real tenant.

**Blocked on:** D-6 done (need group membership management to be working). **Decision needed:** payment processor (Stripe? Manual invoice? Other?).

---

## Cross-cutting rules (every phase)

- **Commit boundary = sub-task boundary.** Never batch.
- **CI must stay green.** If a commit breaks CI, the next commit fixes it before any other work.
- **Push after every commit** to `origin/main` so version control is real.
- **Hard stop at every phase boundary.** Wait for explicit user approval.
- **No new AWS services without flagging.** Default to extending what already exists.
- **No customer-facing breaking changes during a phase.** The four existing providers (Twilio/RingCentral/Zoom/8x8) must keep working through D-1 → D-9.
- **Secrets in Secrets Manager only.** Never env vars, never DynamoDB.
- **Powertools logging contract from CLAUDE.md** applies to every new Lambda.
- **No side quests.** If something is broken that isn't on this plan, flag it and move on.

---

## What this plan does NOT cover (out of scope, separate sessions)

- Marketing site, sales collateral, pricing page
- Migration of existing customers from any prior recording solution
- White-label rebranding (if Dubber's contract requires it)
- Multi-language support
- Mobile app (current product is desktop Chrome only)
- Salesforce, HubSpot, or any non-Applied-Epic CRM integration
- Conversational AI / agent assist (real-time coaching)
- Reporting / analytics dashboards beyond the existing call history page
