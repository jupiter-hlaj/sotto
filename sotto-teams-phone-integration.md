# Sotto — MS Teams Phone Integration
## Technical Specification

**Feed this document to Claude at the start of any Teams integration build session.**

---

## 0. Context & Competitive Situation

BlueC's CTO confirmed in internal communications (April 7, 2026) that:

1. Microsoft certification costs ~$100K/year — **they are not pursuing it**
2. They have a working ACS Bot but are **stuck on auto-add** (getting the bot to join every call automatically)
3. Their confidence that the ACS approach can handle inbound, outbound, AND internal calls is **low**
4. They have prospect demos scheduled but **cannot close deals yet**

This document describes the implementation that solves exactly what they are stuck on. The auto-add mechanism they are hunting for is Microsoft's **Policy-Based Compliance Recording**, which is available to any registered Azure AD application — no certification required. The $100K is for Microsoft marketplace listing and ISV partnership benefits, not API access.

**The competitive edge is not just building the bot faster — it is making onboarding a 2-click experience for the customer admin, which is what blueC has not solved.**

---

## 0.1 What This Looks Like in Practice

Before the technical deep dive, here is the actual experience for the two people who interact with this system: the agency admin and the insurance agent.

### Admin Experience — One-Time Setup (~2 Minutes)

1. Admin logs into Sotto admin portal → Settings → Phone Provider
2. Clicks **"Connect Microsoft Teams"**
3. Browser redirects to Microsoft's standard OAuth consent screen — admin signs in with their Microsoft 365 Global Admin account
4. Microsoft shows the list of permissions Sotto is requesting. Admin clicks **Accept**.
5. Browser redirects back to Sotto. Behind the scenes (invisible to the admin):
   - Sotto stores their Microsoft tenant ID on their tenant record
   - Sotto obtains an access token and creates a ComplianceRecordingPolicy on their Microsoft tenant via Graph API
   - The policy is assigned to every active Sotto agent who has linked their Microsoft account
6. Settings page shows: **"Microsoft Teams connected. Recording active for N agents."**

**No PowerShell commands. No IT support ticket. No SBC (Session Border Controller) reconfiguration. No Azure portal work. No app installs.**

After initial setup, the admin's ongoing experience:
- When a new agent is invited to Sotto, the recording policy is automatically assigned after the agent links their Microsoft account
- Call history in the admin portal works identically to Twilio or RingCentral — Teams calls appear with the same transcript, AI summary, and action items
- Partial recordings (from bot restarts or crashes) are visually flagged in the call detail view so the admin knows the transcript may be incomplete

### Agent Experience — One-Time Linking (~30 Seconds)

1. Agent receives and accepts a Sotto invitation (existing flow, identical to today)
2. If their agency has Teams connected, there is one additional step: **"Sign in with your Microsoft 365 account"**
3. Agent clicks, signs into their work Microsoft account (minimal permissions — just enough to read their user ID)
4. Done. Their Microsoft identity is linked to their Sotto agent record. The recording policy takes effect immediately.

### Agent Experience — Every Call (Zero Action Required)

1. Agent makes or receives a call through Teams Phone — inbound, outbound, or internal, doesn't matter
2. Teams sees the agent is covered by a compliance recording policy
3. Teams automatically invites Sotto's bot to the call — **the agent sees nothing, does nothing, clicks nothing**
4. The bot joins silently, plays a brief announcement: *"This call is being recorded for quality and compliance purposes"*
5. The bot captures dual-channel audio (agent on one channel, client on the other) for the entire duration of the call
6. The call ends naturally — the agent hangs up like normal
7. Within approximately 60 seconds after the call ends:
   - The recording is uploaded to S3
   - AWS Transcribe produces a transcript with deterministic speaker labels ("agent" and "client" — no guessing)
   - Amazon Bedrock generates a 2-3 sentence summary and a list of action items
   - A WebSocket push delivers everything to the Cockpit Chrome extension
8. Agent opens Applied Epic in Chrome → the Cockpit side panel shows the transcript, summary, and action items — **identical experience to a call recorded via Twilio or RingCentral**

**What the agent never sees or touches:**
- No "start recording" button to remember
- No bot invitation to accept or dismiss
- No app to install on their Teams client
- No difference in the Cockpit UI between a Teams call and a call from any other provider

### What Happens When Things Go Wrong

| Scenario | What Happens | Impact on the Agent's Live Call |
|---|---|---|
| Bot is slow to join the call | Call starts and proceeds immediately (`requiredDuringCall: false`) | None — call is live, recording starts a few seconds late |
| Bot crashes mid-call | Call continues uninterrupted; bot uploads a partial recording during graceful shutdown if possible | None — agent's call is not dropped. They get a partial transcript. |
| Bot crashes hard (killed without warning) | Call continues; orphan detector flags the stuck call within 15 minutes | None — Cockpit shows "Recording unavailable" instead of a spinner |
| Bot service is completely down | All calls proceed normally, just unrecorded | None — the agent's phone system is fully independent of the bot |
| Customer's Microsoft admin revokes consent | Policy stops working, no new calls are recorded, existing recordings are unaffected | None — calls continue, just unrecorded until re-consent |

**The critical design choice:** `requiredDuringCall` is set to `false`. This means the bot is optional — if it fails for any reason, the agent's live call with a customer is never terminated. A missed recording is far less costly than killing a live call with a policyholder. BlueC's ACS (Azure Communication Services) approach does not have this safety net because ACS sits in the call path itself.

### Why This Beats blueC's Approach

| | Sotto (Policy-Based Recording) | blueC (ACS Bot) |
|---|---|---|
| **Admin onboarding** | 2 clicks — OAuth consent screen | IT project — SBC reconfiguration, network changes |
| **Call types covered** | All (inbound, outbound, internal, meetings) | Limited — only calls routed through ACS infrastructure |
| **Agent action required** | None | Unclear — may require call routing changes |
| **Auto-join mechanism** | Microsoft enforces at platform level | None — blueC is stuck on this exact problem |
| **Impact on live calls if bot fails** | Zero — call continues, recording lost | Risk of call disruption if ACS is in the call path |
| **Customer IT involvement** | None after consent | SBC vendor coordination, firewall rules, routing changes |

---

## 1. How Microsoft Teams Policy-Based Recording Works

This is the mechanism that solves auto-add.

### 1.1 The Policy-Based Recording Flow

```
[Customer Admin grants OAuth consent to Sotto]
        ↓
[Sotto creates a ComplianceRecordingPolicy on their tenant via Graph API]
        ↓
[Policy is assigned to agents (users) in their tenant]
        ↓
Agent makes or receives any call
        ↓
[Teams automatically invites Sotto's bot to the call — no user action required]
        ↓
[Bot answers, receives audio stream, records to S3]
        ↓
[Existing Sotto pipeline: SQS → Transcribe → Bedrock → WebSocket → Cockpit]
```

### 1.2 Call Type Coverage

This is blueC's exact stuck point. Policy-based recording covers **all call types** for any user the policy is assigned to:

| Call Type | Covered | Notes |
|---|---|---|
| Inbound PSTN (external calling agent) | Yes | Teams Phone number |
| Outbound PSTN (agent calling external) | Yes | Agent dials out via Teams |
| Internal Teams-to-Teams | Yes | Agent calling colleague |
| Teams meeting (scheduled) | Yes | If policy user is a participant |
| Teams meeting (ad hoc) | Yes | |

The policy is user-scoped, not call-type-scoped. Every call the covered user touches gets the bot.

### 1.3 The Bot Invite Mechanism

When a policy-assigned user's call starts:

1. Teams sends an HTTP POST to the bot's registered `notificationUrl`
2. The payload contains an incoming call object with `recordingStatus: "recordingRequested"`
3. The bot answers the call via `POST /communications/calls/{id}/answer`
4. Teams negotiates a media session (ICE/SRTP) with the bot
5. Bot receives dual-channel audio (both sides of the conversation) as an RTP stream
6. On call end, Teams sends a `terminated` notification to the bot's webhook

This is fundamentally different from ACS Bot, which requires you to initiate or intercept calls. Policy-based recording is passive — Teams calls you.

### 1.4 Why Not ACS Bot

Azure Communication Services (ACS) Bot is the approach blueC is using. Its limitations:

- ACS requires calls to be **initiated through ACS** infrastructure. If a Teams Phone user calls a client directly through Teams, ACS is not in the path.
- Getting ACS into the path of existing Teams Phone calls requires SBC (Session Border Controller) configuration changes on the customer's network — this is an IT project, not a 2-click onboarding
- ACS does not have a policy mechanism to auto-join existing calls
- This is why blueC is stuck on inbound/outbound/internal coverage

Policy-based recording has none of these limitations. It is the correct tool.

---

## 2. Architecture Overview

### 2.1 New Components

Two new components are added to the existing Sotto architecture:

```
┌─────────────────────────────────────────────────────────────┐
│                    NEW: Teams Integration Layer              │
│                                                              │
│  ┌──────────────────┐      ┌────────────────────────────┐   │
│  │  TeamsOnboarding │      │     TeamsMediaBot          │   │
│  │  Lambda          │      │     ECS Fargate Service    │   │
│  │                  │      │                            │   │
│  │  - OAuth consent │      │  - Signaling webhook       │   │
│  │  - Policy create │      │  - Media stream (RTP)      │   │
│  │  - Tenant setup  │      │  - Audio → S3 upload       │   │
│  └──────────────────┘      └────────────────────────────┘   │
│           │                            │                     │
└───────────┼────────────────────────────┼─────────────────────┘
            │                            │
            ↓                            ↓
     DynamoDB (tenant)              SQS: sotto-call-events

                                         ↓
                        ┌─────────────────────────────────┐
                        │  EXISTING Sotto Pipeline         │
                        │                                  │
                        │  RecordingProcessor              │
                        │  → TranscriptionInit             │
                        │  → TranscriptionResultProcessor  │
                        │  → AISummarizer                  │
                        │  → WebSocket → Cockpit           │
                        └─────────────────────────────────┘
```

### 2.2 Integration Point

The bot's only job is to get audio into S3 and push a `NormalizedCallEvent` onto `sotto-call-events-{env}`. Everything from `RecordingProcessor` onward is **unchanged**. The existing pipeline does not know or care that the recording came from Teams rather than Twilio.

This is the architectural payoff of Sotto's adapter pattern.

---

## 3. Component 1 — Azure AD Multi-Tenant App

### 3.1 What It Is

A single Azure AD application registration owned by you (Sotto). It is registered once in your Azure tenant. Customers do not create Azure apps — they authorize yours via standard OAuth consent.

### 3.2 Why One App, Not Per-Customer

Every other SaaS integration with Microsoft 365 works this way (Salesforce, HubSpot, etc.). The multi-tenant model means:
- You manage one client ID and one client secret
- Customers grant your app access to their tenant via admin consent
- You receive their Microsoft tenant ID after consent; access tokens are obtained on-demand via client_credentials (Section 4.1)
- No customer has to register anything in Azure

### 3.3 Required Application Permissions

These are **application permissions** (not delegated) — they apply to the app acting on its own, not on behalf of a logged-in user. Requires tenant admin consent.

| Permission | Why |
|---|---|
| `Calls.AccessMedia.All` | Receive the audio/video stream during a call |
| `Calls.JoinGroupCall.All` | Join a call when Teams invites the bot |
| `Calls.JoinGroupCallAsGuest.All` | Join as a guest participant (compliance recording role) |
| `CallRecords.Read.All` | Read call metadata after the call ends |
| `OnlineMeetings.ReadWrite.All` | Required for meeting scenarios |

### 3.4 Bot Registration

In addition to the Azure AD app, the bot must be registered in the **Azure Bot Service**:
- Bot type: `azurebot`
- Messaging endpoint: `https://bots.sotto.cloud/api/calling/notifications`
- Enable: Microsoft Teams channel
- Enable: Calling feature (required for media access)

This is a one-time setup. The bot endpoint points to your ECS service (via ALB).

### 3.5 What Gets Stored in Secrets Manager

```
sotto/azure/app_client_id          # Your Azure AD app client ID (global, one value for all customers)
sotto/azure/app_client_secret      # Your Azure AD app client secret (global, rotate annually)
```

**No per-tenant secrets.** Because the bot uses client_credentials (see Section 4.1), access tokens are derived on-demand from the global credentials + the customer's `ms_tenant_id`. The `ms_tenant_id` is stored on the tenant's DynamoDB record — it is not sensitive and does not belong in Secrets Manager.

---

## 4. Component 2 — Onboarding Flow (The Competitive Edge)

This is what blueC does not have. Their current state requires IT admin intervention and manual PowerShell. This makes it a 2-click experience.

### 4.1 Flow

```
1. Admin visits Sotto admin portal → Settings → Phone Provider → "Connect Microsoft Teams"

2. Sotto redirects to Microsoft admin consent URL:
   https://login.microsoftonline.com/common/adminconsent
   ?client_id={sotto_azure_app_client_id}
   &redirect_uri=https://api.sotto.cloud/teams/oauth/callback
   &state={sotto_tenant_id}  ← ties the consent back to the right agency

3. Admin signs in with their Microsoft 365 Global Admin account
   Microsoft shows consent screen listing the permissions above
   Admin clicks "Accept"

4. Microsoft redirects to Sotto's callback URL with:
   ?admin_consent=True&tenant={ms_tenant_id}&state={sotto_tenant_id}
   ← Note: NO auth code. adminconsent only grants consent. Tokens come separately (see below).

5. TeamsOnboardingHandler Lambda:
   a. Verifies admin_consent=True (if False or missing, show error — admin declined)
   b. Stores ms_tenant_id on tenant record in DynamoDB: teams_enabled=true, ms_tenant_id=...
   c. Obtains access token via client_credentials grant (see "How the bot gets tokens" below)
   d. Creates ComplianceRecordingPolicy via Graph API (see 4.2) using token from step 5c
   e. Assigns policy to all active agents in their tenant (see 4.3)
   f. Redirects admin to settings page with success state

6. Admin portal shows: "Microsoft Teams connected. Recording active for N agents."
```

**How the bot gets tokens (client_credentials grant):**

Sotto's bot uses **application permissions** (not delegated). After admin consent is granted, the bot obtains access tokens on-demand using its own credentials — no per-user login, no stored refresh tokens:

```http
POST https://login.microsoftonline.com/{ms_tenant_id}/oauth2/v2.0/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
&client_id={sotto_azure_app_client_id}
&client_secret={sotto_azure_app_client_secret}
&scope=https://graph.microsoft.com/.default
```

Response:
```json
{
  "token_type": "Bearer",
  "expires_in": 3599,
  "access_token": "eyJ0..."
}
```

**No refresh token is issued for client_credentials.** When a token expires, request a new one identically. The bot caches tokens in memory per-tenant keyed by `ms_tenant_id` with expiry, re-requesting ~5 minutes before expiry to avoid mid-call failures.

This means:
- **No per-tenant tokens stored in Secrets Manager** — only the global `app_client_id` and `app_client_secret` are secrets
- The `ms_tenant_id` stored in DynamoDB on the tenant record is everything the bot needs
- Token refresh is trivial — no refresh token rotation, no storage updates

Total admin time: under 2 minutes. No PowerShell. No IT ticket.

### 4.2 Creating the Recording Policy via Graph API

```http
POST https://graph.microsoft.com/beta/teamwork/complianceRecordingPolicies
Authorization: Bearer {access_token}
Content-Type: application/json

{
  "displayName": "Sotto Call Recording",
  "enabled": true,
  "complianceRecordingApplications": [
    {
      "app": {
        "id": "{sotto_azure_app_client_id}"
      },
      "requiredDuringCall": false,
      "requiredBeforeCallEstablishment": false
    }
  ]
}
```

**`requiredDuringCall: false`** — if the bot drops off mid-call, the call continues uninterrupted. If this were `true`, Teams would **terminate the agent's live call** any time the bot has a transient failure (network blip, OOM, deployment) — catastrophic for a production phone system. Always `false` for insurance agencies.

**`requiredBeforeCallEstablishment: false`** — the call proceeds even if the bot is slow to join. If `true`, calls would fail entirely when the bot has high latency or is scaling up. Always `false`.

Save the returned policy ID to DynamoDB on the tenant record.

### 4.3 Assigning Policy to Agents

For each agent in the tenant:

```http
POST https://graph.microsoft.com/v1.0/users/{ms_user_id}/assignComplianceRecordingPolicy
Authorization: Bearer {access_token}
Content-Type: application/json

{
  "policyName": "Sotto Call Recording"
}
```

**Why per-agent, not tenant-wide:** Insurance agencies have admins and back-office staff who don't take client calls and don't need recording. Per-agent assignment also means Sotto's number mapping table is the source of truth for who gets recorded, consistent with all other providers.

### 4.4 TeamsOnboardingHandler Lambda Spec

**File:** `src/handlers/teams/onboarding.py`
**Trigger:** `GET /teams/oauth/callback` (API Gateway)
**Auth:** None (public, OAuth callback endpoint)
**Timeout:** 30s

Responsibilities:
1. Validate `state` parameter matches a real `tenant_id` in DynamoDB (CSRF protection — prevents spoofed callbacks)
2. Verify `admin_consent=True` in query params — if absent or False, the admin declined; return error to portal
3. Record `ms_tenant_id` from the callback URL on the DynamoDB tenant record
4. Obtain an access token via client_credentials grant (see Section 4.1) using the global Azure app credentials from Secrets Manager
5. Create ComplianceRecordingPolicy via Graph API (see 4.2)
6. Fetch agent list from Sotto `sotto-agents-{env}` table for this tenant
7. For each agent with `ms_user_id` set: assign recording policy (see 4.3)
8. Update tenant record: `teams_enabled=true`, `teams_policy_id=...`, `teams_connected_at=...`
9. Redirect admin to settings page with success state

**No token storage required.** The bot obtains fresh tokens on-demand via client_credentials using the global app secrets. See Section 4.1 for the token flow.

---

## 5. Component 3 — TeamsMediaBot (ECS Fargate)

### 5.0 Microsoft's Reference Sample

Microsoft's official sample for this exact bot type:

**Repo:** [microsoftgraph/microsoft-graph-comms-samples](https://github.com/microsoftgraph/microsoft-graph-comms-samples)
**Sample:** [Samples/V1.0Samples/LocalMediaSamples/PolicyRecordingBot](https://github.com/microsoftgraph/microsoft-graph-comms-samples/tree/master/Samples/V1.0Samples/LocalMediaSamples/PolicyRecordingBot)

> **Note on naming:** Microsoft renamed this from `ComplianceRecordingBot` to `PolicyRecordingBot`. If blueC is searching for the old name, they may be looking at outdated or missing samples. The current sample targets `graph.microsoft.com/v1.0`.

> **Critical caveat: the sample does NOT actually record.** It demonstrates how to receive the media stream (the hard part) but does not include saving audio to storage. Sotto's implementation needs to add: audio buffering → MP3 encoding → S3 upload → SQS publish. Everything in this document from Section 5.6 onward is what the sample leaves out.

The sample is the starting point for the bot container (Step T-4). Build on it rather than from scratch.

### 5.1 Why Not Lambda

Lambda cannot receive Teams media streams. Reasons:

| Lambda Constraint | Why It Breaks Media |
|---|---|
| No persistent TCP/UDP connections | Teams media session uses SRTP over UDP, requires persistent socket |
| 15-minute max runtime | 30-minute call would terminate the function |
| No inbound UDP | Lambda only receives HTTP — cannot receive RTP packets |
| Cold start latency | Teams expects the bot to answer within seconds of notification |

ECS Fargate is the correct choice:
- Containers run continuously — no cold starts
- Full TCP/UDP socket access
- Scales on concurrent call count
- No EC2 management (serverless containers)
- Fits Sotto's existing AWS infrastructure philosophy

This is Sotto's **one and only exception** to the Lambda-for-everything rule, and it is justified.

### 5.2 Why C# for the Bot Container

Microsoft's official real-time media SDK — `Microsoft.Graph.Communications.Calls.Media` — is **.NET/C# only**. It handles:
- ICE candidate negotiation
- SRTP key exchange and decryption
- RTP packet reassembly into audio frames
- Speaker identification from separate audio streams

Implementing this from scratch in Python would require reimplementing significant portions of the WebRTC/SRTP stack. That is weeks of work, is error-prone, and would give you an unmaintained custom implementation of a complex protocol.

**Decision: the bot container is C# (.NET 8). Everything else in Sotto remains Python 3.13.**

This is a pragmatic, not ideological, choice. The bot container is isolated — it has a clean interface (receives a call, uploads an MP3 to S3, sends an SQS message). The rest of Sotto never knows it is C#.

### 5.3 Bot Architecture

```
ECS Fargate Service: sotto-teams-bot-{env}
│
├── HTTP Server (port 443 via ALB)
│   └── POST /api/calling/notifications  ← Teams sends all bot events here
│
├── SignalingController
│   ├── OnIncomingCall()     → Answer call, establish media session
│   ├── OnCallUpdated()      → Handle hold, transfer, participant changes
│   └── OnCallTerminated()   → Finalize recording, upload to S3, send SQS
│
└── MediaSession (per active call)
    ├── AudioSocket          → Receives raw PCM audio from Teams
    ├── AudioBuffer          → Buffers frames, writes to temp file
    └── RecordingUploader    → Streams completed recording to S3
```

### 5.4 Signaling: Handling the Incoming Call Notification

Teams sends a POST to the bot's notification URL when a policy-assigned user starts a call:

```json
{
  "value": [{
    "changeType": "created",
    "resource": "/communications/calls/{call-id}",
    "resourceData": {
      "id": "{call-id}",
      "state": "incoming",
      "direction": "incoming",
      "source": {
        "identity": { "user": { "id": "{ms-user-id}" } }
      },
      "targets": [...],
      "recordingStatus": "recordingRequested"
    }
  }]
}
```

Bot responds by answering the call. **Important on `mediaConfig`:** There are two types:
- `serviceHostedMediaConfig` — Microsoft hosts the media. **The bot receives NO audio stream.** Wrong choice.
- `appHostedMediaConfig` — The bot hosts its own media endpoint and receives the raw RTP/SRTP stream. **This is what Sotto needs.**

In the C# SDK (which you are using), this is handled automatically when you have an `ILocalMediaSession` configured. The SDK's `Answer()` call uses `appHostedMediaConfig` internally. You do not construct this manually:

```csharp
// In the C# SDK — the SDK generates the correct appHostedMediaConfig internally
await _client.Calls[call.Id].Answer(
    modalities: new List<Modality> { Modality.Audio },
    callbackUri: _botConfig.NotificationUrl,
    mediaConfig: _mediaSession.MediaConfig  // SDK generates this from the local media session
);
```

**Incoming notification JWT validation:** Every notification POST from Teams includes an `Authorization: Bearer <token>` header. Validate it before processing — an unvalidated webhook is an open exploit:

```csharp
// In NotificationController, before processing any notification:
var token = Request.Headers["Authorization"].ToString().Replace("Bearer ", "");
var isValid = await _botAuthProvider.ValidateTokenAsync(token);
if (!isValid) return Unauthorized();
```

The C# bot framework's `AuthenticationProvider` validates the JWT against Microsoft's OIDC metadata endpoint (`https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration`). Use the SDK's built-in validation — do not roll your own.

### 5.5 Recording Announcement (Legal Requirement)

Many US states and all Canadian provinces require informing call participants they are being recorded. This is also Microsoft's stated requirement for compliance recording applications.

After answering the call, play a prompt before audio capture begins:

```http
POST https://graph.microsoft.com/v1.0/communications/calls/{call-id}/playPrompt
Authorization: Bearer {access_token}

{
  "prompts": [{
    "@odata.type": "#microsoft.graph.mediaPrompt",
    "mediaInfo": {
      "@odata.type": "#microsoft.graph.mediaInfo",
      "uri": "https://bots.sotto.cloud/assets/recording-notice.wav",
      "resourceId": "recording-notice-v1"
    }
  }]
}
```

The audio file (`recording-notice.wav`) is hosted on S3/CloudFront. Content: "This call is being recorded for quality and compliance purposes."

**Announcement timing — what actually happens:** The audio capture (`AudioSocket`) starts when the media session is established, which is part of answering the call — before `playPrompt` is called. This means the announcement itself is captured in the recording and will appear at the start of the transcript.

**Decision: accept this.** Do not add a delay or discard-until-announcement-complete logic. The announcement in the transcript is harmless — it's just "This call is being recorded for quality and compliance purposes." AWS Transcribe will transcribe it accurately. The transcript parser (or the Cockpit UI) can trivially detect and skip it if needed. Adding recording-gate logic (a boolean flag that discards PCM frames until `playPrompt` completes) adds complexity with no real benefit.

**Why this matters for Sotto's market:** Insurance agencies operate in a heavily regulated environment. Making the announcement automatic and non-skippable removes it from agent responsibility and reduces compliance risk for the agency.

### 5.6 Audio Capture and S3 Upload

This section covers everything the Microsoft sample leaves out.

---

#### 5.6.1 Dual-Channel Audio — Why and How

The Microsoft sample uses a **mixed** audio stream: all participants blended into one mono track, with speaker diarization (`ShowSpeakerLabels`) left to AWS Transcribe's ML model to figure out who said what. For a two-party call this works acceptably, but it fails in two scenarios common in insurance: crosstalk (both parties speaking at once — Transcribe guesses wrong) and accented or quiet voices (diarization confidence drops).

The correct approach for Sotto: configure the SDK to deliver **unmixed per-participant audio** and produce a **stereo MP3 where channel 0 = agent, channel 1 = client**. AWS Transcribe's `ChannelIdentification` feature then produces a deterministic, 100% reliable speaker attribution — no ML guessing, no confidence scores, no misattribution.

**Configuring the AudioSocket for unmixed audio:**

The default `AudioSocket` configuration in the sample receives a mixed stream. Change it:

```csharp
// When building the media session in your call handler:
var audioSocketSettings = new AudioSocketSettings
{
    StreamDirections = StreamDirection.Receiveonly,
    // Request unmixed audio — separate buffer per active participant
    ReceiveUnmixedMeetingAudio = true
};
var audioSocket = new AudioSocket(audioSocketSettings);
```

With `ReceiveUnmixedMeetingAudio = true`, each `AudioMediaBuffer` received on the socket includes a `ActiveSpeakerParticipantId` property identifying which participant the audio belongs to.

**Participant ID to channel assignment:**

When the bot answers the call (in `OnIncomingCall`), the call object includes a participants list. At answer time, identify which participant is the Sotto-tracked agent:

```csharp
// At call answer time, resolve channel assignment:
var agentMsUserId = await ResolveAgentMsUserId(tenantId, call.Source.Identity.User.Id);
var participantChannelMap = new ConcurrentDictionary<string, int>();

// Agent = channel 0, everyone else = channel 1
// For PSTN calls there are exactly two parties. For conference calls with 3+,
// all non-agent participants are mixed into channel 1.
foreach (var participant in call.Participants)
{
    var userId = participant.Info.Identity.User?.Id
                 ?? participant.Info.Identity.Phone?.Id;  // PSTN callers have phone identity
    participantChannelMap[userId] = (userId == agentMsUserId) ? 0 : 1;
}
```

Store `participantChannelMap` on the `CallHandler` instance for the duration of the call.

**Per-participant audio buffers:**

```csharp
// Two concurrent buffers, one per channel:
private readonly ConcurrentQueue<(long Timestamp, short[] Samples)> _channel0 = new();
private readonly ConcurrentQueue<(long Timestamp, short[] Samples)> _channel1 = new();

protected void OnAudioMediaReceived(object sender, AudioMediaBufferEventArgs e)
{
    var buffer = e.Buffer;
    var participantId = buffer.ActiveSpeakerParticipantId;

    if (!_participantChannelMap.TryGetValue(participantId, out var channel))
        channel = 1; // Unknown participant → client channel

    var samples = ConvertToShortArray(buffer.Data, buffer.Length);

    // Route through AppendSamples() (Section 5.6.2) for tiered buffering.
    // Do NOT enqueue directly to _channel0/_channel1 — AppendSamples handles
    // the memory threshold check and spill-to-disk logic.
    AppendSamples(channel, samples);
}
```

---

#### 5.6.2 PCM Buffering Strategy

**The problem with pure in-memory buffering:**

16-bit PCM at 16kHz mono = 32KB/second per channel. Two channels = 64KB/second. A 30-minute call = ~112MB of raw PCM in memory. With up to 10–20 concurrent calls per Fargate task (2GB memory), that is 1.1–2.2GB of audio buffers alone — an OOM waiting to happen.

**The problem with pure disk buffering:**

Writing every frame to disk adds I/O overhead on every audio packet (arriving ~50 times per second per channel). On a busy task, this creates contention.

**Decision: Tiered buffering with a 40MB threshold per call.**

```
Per-call audio memory < 40MB  →  stay in memory (covers ~10 minutes of stereo audio)
Per-call audio memory ≥ 40MB  →  spill overflow to a temp file in /tmp
```

Implementation:

```csharp
private const long MemoryThresholdBytes = 40 * 1024 * 1024; // 40MB
private long _bufferedBytes = 0;
private bool _spillingToDisk = false;
private FileStream? _spillFileChannel0 = null;
private FileStream? _spillFileChannel1 = null;
private readonly string _spillPath0;
private readonly string _spillPath1;

// Constructor:
// _spillPath0 = Path.Combine("/tmp", $"{callId}_ch0.pcm");
// _spillPath1 = Path.Combine("/tmp", $"{callId}_ch1.pcm");

private void AppendSamples(int channel, short[] samples)
{
    var bytes = samples.Length * 2; // 2 bytes per short
    Interlocked.Add(ref _bufferedBytes, bytes);

    if (_bufferedBytes > MemoryThresholdBytes && !_spillingToDisk)
    {
        _spillingToDisk = true;
        // Open separate per-channel spill files
        // Each stores raw PCM frames with timestamps prepended (8 bytes timestamp + samples)
        _spillFileChannel0 = new FileStream(_spillPath0, FileMode.Create, FileAccess.Write);
        _spillFileChannel1 = new FileStream(_spillPath1, FileMode.Create, FileAccess.Write);
        // Flush existing in-memory queues to disk
        FlushChannelToDisk(_channel0, _spillFileChannel0);
        FlushChannelToDisk(_channel1, _spillFileChannel1);
    }

    if (_spillingToDisk)
    {
        var file = channel == 0 ? _spillFileChannel0! : _spillFileChannel1!;
        WriteFrameToDisk(file, samples);
    }
    else
    {
        if (channel == 0) _channel0.Enqueue((CurrentTimestamp(), samples));
        else              _channel1.Enqueue((CurrentTimestamp(), samples));
    }
}
```

**Why two separate channel files, not one interleaved file:** Alignment (Section 5.6.3) must happen at call end by merging both channels with timestamp matching. If you write interleaved during capture without alignment, silence gaps produce drift. Keep channels separate throughout; interleave only after alignment at call end. The `AlignAndInterleave` function in 5.6.3 reads from either the in-memory queues or the spill files depending on `_spillingToDisk`.

**Why /tmp:** Fargate ephemeral storage is up to 200GB, configurable per task definition. Set `EphemeralStorage.SizeInGiB = 21` (minimum, covers ~3 hours of audio per concurrent call at full utilization). This is defined in the task definition and has no additional cost beyond the Fargate task itself.

---

#### 5.6.3 Channel Alignment and Silence Padding

The two participant streams will have gaps. If the agent is silent for 5 seconds while the client speaks, channel 0 receives no buffers for that period. When you interleave the channels at the end, without padding the channels drift apart — the client's words end up temporally misaligned with the agent's, making the stereo transcript timing wrong.

**Rule: for every audio packet received on one channel, if the other channel has no packet within the same timestamp window, emit a zero-value (silence) frame for the silent channel.**

```csharp
private const int FrameSizeMs = 20;  // SDK delivers 20ms frames
private const int SampleRate = 16000;
private const int SamplesPerFrame = SampleRate * FrameSizeMs / 1000;  // 320 samples

private void AlignAndInterleave(
    IEnumerable<(long Timestamp, short[] Samples)> ch0,
    IEnumerable<(long Timestamp, short[] Samples)> ch1,
    Stream output)
{
    var silence = new short[SamplesPerFrame];  // all zeros

    using var e0 = ch0.GetEnumerator();
    using var e1 = ch1.GetEnumerator();
    bool has0 = e0.MoveNext(), has1 = e1.MoveNext();

    while (has0 || has1)
    {
        short[] frame0 = silence, frame1 = silence;

        if (has0 && has1)
        {
            var diff = e0.Current.Timestamp - e1.Current.Timestamp;
            if (Math.Abs(diff) <= FrameSizeMs * 10_000) // within one frame (100ns ticks)
            {
                frame0 = e0.Current.Samples; has0 = e0.MoveNext();
                frame1 = e1.Current.Samples; has1 = e1.MoveNext();
            }
            else if (diff < 0) { frame0 = e0.Current.Samples; has0 = e0.MoveNext(); }
            else                { frame1 = e1.Current.Samples; has1 = e1.MoveNext(); }
        }
        else if (has0) { frame0 = e0.Current.Samples; has0 = e0.MoveNext(); }
        else           { frame1 = e1.Current.Samples; has1 = e1.MoveNext(); }

        // Interleave: L sample, R sample, L sample, R sample...
        for (int i = 0; i < SamplesPerFrame; i++)
        {
            WriteShort(output, frame0.Length > i ? frame0[i] : (short)0);
            WriteShort(output, frame1.Length > i ? frame1[i] : (short)0);
        }
    }
}
```

---

#### 5.6.4 Encoding: Stereo MP3

**Why MP3 and not WAV or FLAC:**
- WAV stereo 30 min = ~112MB. Acceptable, but unnecessary storage and transfer cost at scale.
- FLAC (lossless) stereo 30 min ≈ 55–70MB. Better, but adds encoding CPU cost and FLAC libraries in C# are less battle-tested than MP3.
- MP3 stereo at 32kbps joint stereo (adequate for voice): 30 min = ~7MB. AWS Transcribe works cleanly with 32kbps+ MP3 voice. Quality loss is irrelevant for speech recognition.

**Decision: MP3 stereo, 32kbps, 16kHz, using NAudio + LAME wrapper.**

```xml
<!-- teams-bot.csproj -->
<PackageReference Include="NAudio" Version="2.2.1" />
<PackageReference Include="NAudio.Lame" Version="2.1.0" />
```

```csharp
private async Task<string> EncodeAndUpload(
    Stream interleavedPcm,
    string tenantId,
    string callId,
    DateTime callDate)
{
    var s3Key = $"{tenantId}/recordings/{callDate:yyyy}/{callDate:MM}/{callId}.mp3";

    interleavedPcm.Position = 0;

    var uploadId = await InitiateMultipartUpload(s3Key);
    var parts = new List<PartETag>();
    int partNumber = 1;

    // LameMP3FileWriter is a WRITER — you write PCM into it, it outputs MP3.
    // The output stream (ms) accumulates encoded MP3 bytes.
    // When ms reaches the S3 minimum part size (5MB), flush it as a multipart part.
    using var ms = new MemoryStream();
    var waveFormat = new WaveFormat(16000, 16, 2);  // 16kHz, 16-bit, stereo
    using var reader = new RawSourceWaveStream(interleavedPcm, waveFormat);
    using var mp3Writer = new LameMP3FileWriter(ms, waveFormat, 32);  // 32kbps

    byte[] pcmBuffer = new byte[320 * 2 * 2]; // 20ms of stereo 16-bit PCM
    int bytesRead;
    while ((bytesRead = reader.Read(pcmBuffer, 0, pcmBuffer.Length)) > 0)
    {
        mp3Writer.Write(pcmBuffer, 0, bytesRead);  // PCM in → MP3 bytes accumulate in ms

        if (ms.Length >= 5 * 1024 * 1024)          // S3 minimum part size
        {
            ms.Position = 0;
            parts.Add(await UploadPart(s3Key, uploadId, partNumber++, ms));
            ms.SetLength(0);
        }
    }

    mp3Writer.Flush();  // Flush LAME's internal buffers — critical, last frames may be buffered

    if (ms.Length > 0)
    {
        ms.Position = 0;
        parts.Add(await UploadPart(s3Key, uploadId, partNumber, ms));
    }

    await CompleteMultipartUpload(s3Key, uploadId, parts);
    return s3Key;
}
```

---

#### 5.6.5 Call State Tracking in DynamoDB (Crash Recovery Foundation)

**The core problem:** If the Fargate task dies mid-call (OOM, instance failure, deployment), the audio buffers are gone. Without state tracking, nobody knows the call was ever attempted.

**Write to DynamoDB the moment the bot answers the call** — before any audio is captured:

```csharp
// In OnCallAnswered(), immediately after the media session is established:
await _dynamoDb.PutItemAsync("sotto-calls-{env}", new Dictionary<string, AttributeValue>
{
    ["tenant_id"]          = new AttributeValue { S = tenantId },
    ["call_id"]            = new AttributeValue { S = callId },
    ["ms_call_id"]         = new AttributeValue { S = msCallId },
    ["status"]             = new AttributeValue { S = "recording" },
    ["recording_status"]   = new AttributeValue { S = "in_progress" },
    ["bot_task_id"]        = new AttributeValue { S = _ecsTaskId },  // Resolved at startup (see below)
    ["agent_id"]           = new AttributeValue { S = agentId ?? "unknown" },
    ["started_at"]         = new AttributeValue { S = DateTime.UtcNow.ToString("O") },
    ["provider"]           = new AttributeValue { S = "teams" },
    ["created_at"]         = new AttributeValue { S = DateTime.UtcNow.ToString("O") },
});
```

Update to `recording_status: "upload_complete"` only after the S3 multipart upload is fully confirmed and the SQS message is successfully sent.

This means at any point in time, a DynamoDB scan for `recording_status = "in_progress"` records older than 2 hours reveals calls the bot failed to complete.

**`_ecsTaskId` — resolved at bot startup, not from an env var.** Fargate does NOT inject `ECS_TASK_ID` as an environment variable. Instead, Fargate exposes a metadata endpoint. Fetch the task ID once at startup and cache it:

```csharp
// In Program.cs or service startup — call once, store as a static/singleton:
private static string _ecsTaskId = "unknown";

public static async Task ResolveEcsTaskId()
{
    var metadataUri = Environment.GetEnvironmentVariable("ECS_CONTAINER_METADATA_URI_V4");
    if (metadataUri == null) return;  // Not running in Fargate (local dev)

    using var http = new HttpClient();
    var json = await http.GetStringAsync($"{metadataUri}/task");
    var doc = JsonDocument.Parse(json);
    var taskArn = doc.RootElement.GetProperty("TaskARN").GetString();
    // TaskARN format: arn:aws:ecs:region:account:task/cluster/task-id
    _ecsTaskId = taskArn?.Split('/').Last() ?? "unknown";
}
```

---

#### 5.6.6 Graceful Shutdown — SIGTERM Handling

ECS sends `SIGTERM` to the container before stopping a task (scaling in, deployment, task replacement). The default ECS stop timeout is 30 seconds. This is enough time to finish encoding whatever audio has been captured and upload it as a partial recording.

**Set ECS stop timeout to 120 seconds** in the task definition. This gives encoding time for calls in progress (120 seconds encodes ~15+ minutes of audio with headroom).

```yaml
# In SAM template.yaml TeamsBotTaskDef:
StopTimeout: 120
```

**Catch SIGTERM in the bot:**

```csharp
// In Program.cs or service startup:
var cts = new CancellationTokenSource();
Console.CancelKeyPress += (_, e) => { e.Cancel = true; cts.Cancel(); };

AppDomain.CurrentDomain.ProcessExit += async (_, _) =>
{
    // SIGTERM arrives here as ProcessExit
    await _callManager.ShutdownGracefully(cts.Token);
};
```

**`ShutdownGracefully`:**
```csharp
public async Task ShutdownGracefully(CancellationToken ct)
{
    // Stop accepting new calls immediately
    _acceptingCalls = false;

    // For each active call: encode whatever has been captured and upload as partial
    var tasks = _activeCalls.Values.Select(call => call.FinalizeAsync(partial: true));
    await Task.WhenAll(tasks);
}
```

**`FinalizeAsync(partial: bool)`** — same code path as a normal call end, but sets a flag:
- Encodes whatever PCM is in the buffer (may be truncated mid-sentence)
- Uploads to S3 at the same key pattern
- Sends SQS message with `"partial": true`
- Updates DynamoDB: `recording_status: "partial_upload"`, `partial_reason: "graceful_shutdown"`

**Why not refuse SIGTERM and wait for calls to end naturally?** ECS will SIGKILL after the stop timeout regardless. A partial recording is better than no recording. Also, ECS deployments happen regularly — refusing to shut down would make deployments block for the duration of active calls.

---

#### 5.6.7 Orphan Detection — Crash Recovery

SIGTERM is not always received. An OOM kill (`SIGKILL`), an EC2 instance failure, or an unhandled exception that terminates the process without cleanup leaves calls stuck at `recording_status: "in_progress"` in DynamoDB.

**Add a scheduled Lambda: `TeamsOrphanDetector`**

Runs every 15 minutes via EventBridge:

```python
# src/handlers/teams/orphan_detector.py

ORPHAN_THRESHOLD_MINUTES = 120  # Call in_progress for > 2 hours = orphaned

def handler(event, context):
    cutoff = datetime.utcnow() - timedelta(minutes=ORPHAN_THRESHOLD_MINUTES)

    orphans = db.query_calls_by_status(
        status="recording",
        recording_status="in_progress",
        started_before=cutoff
    )

    for call in orphans:
        logger.warning("Orphaned call detected",
                       extra={"call_id": call["call_id"],
                              "tenant_id": call["tenant_id"],
                              "bot_task_id": call.get("bot_task_id"),
                              "started_at": call["started_at"]})

        db.update_call(call["tenant_id"], call["call_id"], {
            "status": "failed",
            "recording_status": "failed",
            "failure_reason": "bot_crash_orphan",
            "failed_at": datetime.utcnow().isoformat()
        })

        # Push WebSocket notification to agent so Cockpit shows
        # "Recording unavailable for this call" rather than spinning forever
        ws_publisher.publish(
            tenant_id=call["tenant_id"],
            agent_id=call.get("agent_id"),
            event_type="recording_failed",
            payload={"call_id": call["call_id"], "reason": "bot_crash"}
        )
```

SAM resource:

```yaml
TeamsOrphanDetectorFunction:
  Type: AWS::Serverless::Function
  Properties:
    FunctionName: !Sub sotto-TeamsOrphanDetector-${Environment}
    CodeUri: src/handlers/teams/
    Handler: orphan_detector.handler
    Timeout: 60
    Events:
      Schedule:
        Type: Schedule
        Properties:
          Schedule: rate(15 minutes)
    Policies:
      - DynamoDBCrudPolicy:
          TableName: !Ref CallsTable
      - Statement:
          Effect: Allow
          Action: execute-api:ManageConnections
          Resource: !Sub arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${WebSocketApi}/*
```

**Note on partial S3 content from a crash:** When a Fargate task is SIGKILL'd mid-multipart-upload, the in-progress multipart upload is abandoned (not completed or aborted). S3 retains the uploaded parts indefinitely, accruing storage cost. Add an S3 lifecycle rule to abort incomplete multipart uploads after 24 hours:

```yaml
# In RecordingsBucket lifecycle configuration (already in template.yaml):
- ID: AbortIncompleteMultipartUploads
  Status: Enabled
  AbortIncompleteMultipartUpload:
    DaysAfterInitiation: 1
```

---

#### 5.6.8 TranscriptionInit — Channel Identification Cascade

This is a downstream change required by the dual-channel stereo approach.

The existing `TranscriptionInit` uses:
```python
Settings={
    'ShowSpeakerLabels': True,
    'MaxSpeakerLabels': 2,
}
```

For Teams stereo recordings, this must change to:
```python
Settings={
    'ChannelIdentification': True,
    # Note: ShowSpeakerLabels and ChannelIdentification are mutually exclusive in AWS Transcribe
}
```

`TranscriptionInit` must detect provider and apply the correct setting:

```python
def get_transcription_settings(provider: str) -> dict:
    if provider == "teams":
        # Stereo file: ch_0 = agent, ch_1 = client — deterministic attribution
        return {"ChannelIdentification": True}
    else:
        # All other providers: mono recording, use ML diarization
        return {"ShowSpeakerLabels": True, "MaxSpeakerLabels": 2}
```

The `NormalizedCallEvent` already carries `provider`, so no schema change is needed for this.

**Transcript output difference:** With `ChannelIdentification`, AWS Transcribe produces two transcript streams tagged `ch_0` and `ch_1` rather than `spk_0` / `spk_1`. The `TranscriptionResultProcessor` must handle both formats. Since `agent_channel: 0` is always the agent for Teams calls, label mapping is:

```python
def resolve_speaker_label(label: str, provider: str, agent_channel: int = 0) -> str:
    if provider == "teams":
        return "agent" if label == f"ch_{agent_channel}" else "client"
    else:
        # For non-Teams: spk_0 is typically the first speaker — heuristic only
        return "agent" if label == "spk_0" else "client"
```

Add `agent_channel: int = 0` to `NormalizedCallEvent` (defaults to 0, only meaningful for Teams).

### 5.7 The SQS Message (Handoff to Existing Pipeline)

```json
{
  "provider": "teams",
  "tenant_id": "agency-uuid",
  "call_id": "sotto-generated-uuid",
  "ms_call_id": "microsoft-call-id",
  "agent_id": "resolved-from-ms-user-id",
  "direction": "inbound",
  "caller_number": "+15195550100",
  "called_number": "+15195550200",
  "started_at": "2026-04-09T14:23:00Z",
  "ended_at": "2026-04-09T14:31:45Z",
  "duration_seconds": 465,
  "recording_s3_key": "agency-uuid/recordings/2026/04/{call_id}.mp3",
  "recording_already_uploaded": true,
  "agent_channel": 0,
  "partial": false,
  "partial_reason": null
}
```

**Field notes:**

- **`recording_already_uploaded: true`** — tells `RecordingProcessor` to skip the download step and go straight to `TranscriptionInit`. All other providers set this to `false`.
- **`agent_channel: 0`** — tells `TranscriptionResultProcessor` which stereo channel is the agent. Always `0` for Teams (agent is always routed to channel 0 at capture time). Used by `resolve_speaker_label()` to produce "agent"/"client" labels in the transcript.
- **`partial: false`** — set to `true` by SIGTERM graceful shutdown or any other early termination. When `true`, `RecordingProcessor` logs a warning and marks the call record with `partial_recording: true`. The transcript and summary still proceed — a partial recording is better than nothing and the agent can add manual notes for what was missed.
- **`partial_reason`** — one of `null`, `"graceful_shutdown"`, `"network_loss"`. Stored on the call record for debugging and visible in the admin portal call detail view.

### 5.8 Resolving Agent Identity

The bot receives a Microsoft user ID (`ms_user_id`) for the Teams user on the call. To resolve this to a Sotto `agent_id`:

- DynamoDB `sotto-agents-{env}` table gains a new attribute: `ms_user_id`
- Set during agent onboarding (see Section 7)
- Bot queries: `SELECT agent_id WHERE ms_user_id = '{ms_user_id}' AND tenant_id = '{tenant_id}'`
- Uses the existing `agent-index` GSI or a new `ms-user-index` GSI

If no match found: log warning, set `agent_id = null`, still record the call. Same behavior as other providers when number mapping is missing.

### 5.9 Scaling

ECS service auto-scaling policy:
- **Metric:** Custom CloudWatch metric `ActiveCallCount` published by the bot
- **Scale out:** Add 1 task per 10 concurrent calls
- **Scale in:** Remove tasks when `ActiveCallCount` drops below threshold for 5 minutes
- **Minimum tasks:** 1 (always warm, no cold start delay when a call comes in)
- **Maximum tasks:** Configure per environment (start with 5 for dev, 20 for prod)

Each Fargate task handles multiple concurrent calls (the media SDK is multi-threaded). One task can handle approximately 10–20 concurrent calls depending on task CPU/memory.

---

## 6. TeamsAdapter

### 6.1 Purpose

The `TeamsAdapter` normalizes Teams webhook payloads into `NormalizedCallEvent`. However, because the media bot handles recording and uploads directly to S3 (bypassing the normal webhook → SQS → download flow), the adapter's role is reduced to:
- Validating the incoming notification signature
- Extracting metadata for the SQS message

The bot itself constructs the `NormalizedCallEvent` directly rather than going through `ProviderWebhookHandler`. The adapter is still registered in the adapter registry for future use (metadata-only webhooks, call record lookups).

### 6.2 Adapter Implementation

**File:** `src/layers/common/python/sotto/adapters/teams.py`

```python
import hmac
import hashlib
from .base import BaseAdapter
from sotto.models import NormalizedCallEvent

class TeamsAdapter(BaseAdapter):
    """
    Teams adapter handles metadata normalization only.
    Recording capture is handled by the TeamsMediaBot (ECS).
    """

    PROVIDER = "teams"

    def validate_signature(self, payload: bytes, headers: dict) -> bool:
        # Teams uses JWT validation on the Authorization header
        # The bot framework validates the token via Microsoft's OIDC metadata
        # Validation is handled in the bot service itself (C#), not here
        # This method exists for interface compliance
        return True

    def normalize(self, payload: dict) -> NormalizedCallEvent | None:
        """
        Called only for metadata webhooks (call records, etc.)
        Media recording events come directly from the bot via SQS.
        """
        value = payload.get("value", [{}])[0]
        resource_data = value.get("resourceData", {})
        call_id = resource_data.get("id")

        if not call_id:
            return None

        return NormalizedCallEvent(
            provider=self.PROVIDER,
            provider_call_id=call_id,
            event_type="call_updated",
            raw_payload=payload,
        )

    def is_call_ended(self, event: NormalizedCallEvent) -> bool:
        state = event.raw_payload.get("value", [{}])[0]\
                     .get("resourceData", {}).get("state", "")
        return state == "terminated"
```

---

## 7. Agent Onboarding — Microsoft User ID Mapping

When an agent is invited to Sotto (existing `AgentInvite` flow), a new step is added for Teams-enabled tenants:

1. After agent accepts Sotto invite, prompt them to sign in with their Microsoft 365 account
2. This is a **delegated** OAuth flow (not admin consent — the agent signs in themselves)
3. Scopes: `User.Read` (just enough to get their Microsoft user ID)
4. Store `ms_user_id` on their agent record in DynamoDB

```python
# Added to AgentConfirm handler, if tenant.teams_enabled:
ms_user_id = get_ms_user_id_from_token(ms_access_token)
db.update_agent(tenant_id, agent_id, {"ms_user_id": ms_user_id})
```

This is how the bot resolves Teams caller identity to a Sotto agent. It requires one extra sign-in step from the agent but it is a one-time action.

---

## 8. AWS Infrastructure

### 8.1 New Resources (added to SAM template.yaml)

#### ECS Fargate Cluster

```yaml
TeamsBotCluster:
  Type: AWS::ECS::Cluster
  Properties:
    ClusterName: !Sub sotto-bots-${Environment}
    CapacityProviders: [FARGATE]
    ClusterSettings:
      - Name: containerInsights
        Value: enabled
```

#### ECR Repository

```yaml
TeamsBotRepository:
  Type: AWS::ECR::Repository
  Properties:
    RepositoryName: !Sub sotto-teams-bot-${Environment}
    ImageScanningConfiguration:
      ScanOnPush: true
```

#### ECS Task Definition

```yaml
TeamsBotTaskDef:
  Type: AWS::ECS::TaskDefinition
  Properties:
    Family: !Sub sotto-teams-bot-${Environment}
    Cpu: 1024        # 1 vCPU — adjust based on concurrent call load testing
    Memory: 2048     # 2 GB
    NetworkMode: awsvpc
    RequiresCompatibilities: [FARGATE]
    RuntimePlatform:
      CpuArchitecture: X86_64    # C# .NET 8 on x86. Not ARM — .NET media libs have x86 deps.
      OperatingSystemFamily: LINUX
    ExecutionRoleArn: !GetAtt TeamsBotExecutionRole.Arn
    TaskRoleArn: !GetAtt TeamsBotTaskRole.Arn
    ContainerDefinitions:
      - Name: teams-bot
        Image: !Sub ${AWS::AccountId}.dkr.ecr.${AWS::Region}.amazonaws.com/sotto-teams-bot-${Environment}:latest
        PortMappings:
          - ContainerPort: 8080
            Protocol: tcp
        Environment:
          - Name: ENVIRONMENT
            Value: !Ref Environment
          - Name: SQS_CALL_EVENTS_URL
            Value: !Ref CallEventsQueue
          - Name: RECORDINGS_BUCKET
            Value: !Ref RecordingsBucket
          - Name: AGENTS_TABLE
            Value: !Ref AgentsTable
          - Name: TENANTS_TABLE
            Value: !Ref TenantsTable
          - Name: CALLS_TABLE
            Value: !Ref CallsTable
        Secrets:
          - Name: AZURE_APP_CLIENT_ID
            ValueFrom: !Sub arn:aws:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:sotto/azure/app_client_id
          - Name: AZURE_APP_CLIENT_SECRET
            ValueFrom: !Sub arn:aws:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:sotto/azure/app_client_secret
        # Note: EphemeralStorage is defined at the task level, not container level (see below)
        LogConfiguration:
          LogDriver: awslogs
          Options:
            awslogs-group: !Sub /aws/ecs/sotto-teams-bot-${Environment}
            awslogs-region: !Ref AWS::Region
            awslogs-stream-prefix: bot
```

#### Application Load Balancer

```yaml
TeamsBotALB:
  Type: AWS::ElasticLoadBalancingV2::LoadBalancer
  Properties:
    Name: !Sub sotto-bots-${Environment}
    Scheme: internet-facing
    Type: application
    Subnets: !Ref PublicSubnets         # VPC required for ECS — see note below
    SecurityGroups: [!Ref BotALBSG]

TeamsBotTargetGroup:
  Type: AWS::ElasticLoadBalancingV2::TargetGroup
  Properties:
    Port: 8080
    Protocol: HTTP
    TargetType: ip                       # Required for Fargate awsvpc networking
    VpcId: !Ref VpcId
    HealthCheckPath: /health
    HealthCheckIntervalSeconds: 30

TeamsBotListener:
  Type: AWS::ElasticLoadBalancingV2::Listener
  Properties:
    LoadBalancerArn: !Ref TeamsBotALB
    Port: 443
    Protocol: HTTPS
    Certificates:
      - CertificateArn: !Ref BotTLSCertificateArn   # ACM certificate for bots.sotto.cloud
    DefaultActions:
      - Type: forward
        TargetGroupArn: !Ref TeamsBotTargetGroup
```

> **VPC Note:** The ECS service requires a VPC (unlike the rest of Sotto). This is the one exception to the no-VPC rule. Use 2 public subnets across 2 AZs. No NAT Gateway needed — Fargate tasks get public IPs and reach AWS services (S3, SQS, Secrets Manager) via public endpoints with IAM auth. Keep the design simple: no private subnets, no NAT.

#### ECS Service with Auto-Scaling

```yaml
TeamsBotService:
  Type: AWS::ECS::Service
  Properties:
    ServiceName: !Sub sotto-teams-bot-${Environment}
    Cluster: !Ref TeamsBotCluster
    TaskDefinition: !Ref TeamsBotTaskDef
    LaunchType: FARGATE
    DesiredCount: 1
    NetworkConfiguration:
      AwsvpcConfiguration:
        AssignPublicIp: ENABLED
        Subnets: !Ref PublicSubnets
        SecurityGroups: [!Ref BotTaskSG]
    LoadBalancers:
      - ContainerName: teams-bot
        ContainerPort: 8080
        TargetGroupArn: !Ref TeamsBotTargetGroup

TeamsBotAutoScaling:
  Type: AWS::ApplicationAutoScaling::ScalableTarget
  Properties:
    ServiceNamespace: ecs
    ResourceId: !Sub service/${TeamsBotCluster}/${TeamsBotService.Name}
    ScalableDimension: ecs:service:DesiredCount
    MinCapacity: 1
    MaxCapacity: 20

TeamsBotScalingPolicy:
  Type: AWS::ApplicationAutoScaling::ScalingPolicy
  Properties:
    PolicyType: TargetTrackingScaling
    TargetTrackingScalingPolicyConfiguration:
      TargetValue: 10.0           # Target 10 active calls per task
      CustomizedMetricSpecification:
        MetricName: ActiveCallCount
        Namespace: Sotto/TeamBot
        Statistic: Sum
```

#### TeamsOnboarding Lambda

```yaml
TeamsOnboardingFunction:
  Type: AWS::Serverless::Function
  Properties:
    FunctionName: !Sub sotto-TeamsOnboarding-${Environment}
    CodeUri: src/handlers/teams/
    Handler: onboarding.handler
    Timeout: 30
    Policies:
      - DynamoDBCrudPolicy:
          TableName: !Ref TenantsTable
      - DynamoDBCrudPolicy:
          TableName: !Ref AgentsTable
      - AWSSecretsManagerGetSecretValuePolicy:
          SecretArn: !Sub arn:aws:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:sotto/azure/*
          # Global Azure app credentials only — no per-tenant secrets needed (client_credentials flow)
    Events:
      OAuthCallback:
        Type: HttpApi
        Properties:
          ApiId: !Ref HttpApi
          Path: /teams/oauth/callback
          Method: GET
```

#### Missing Resources — Add to template.yaml

These are referenced by the resources above but not defined. All must be added.

**SAM template Parameters (add to existing `Parameters:` block):**

```yaml
VpcId:
  Type: AWS::EC2::VPC::Id
  Description: VPC for ECS bot service (manually bootstrapped)

PublicSubnets:
  Type: List<AWS::EC2::Subnet::Id>
  Description: Two public subnets in different AZs for bot service

BotTLSCertificateArn:
  Type: String
  Description: ACM certificate ARN for bots.sotto.cloud (manually provisioned)
```

**Security Groups:**

```yaml
BotALBSG:
  Type: AWS::EC2::SecurityGroup
  Properties:
    GroupDescription: ALB for Teams bot — allow inbound HTTPS from Microsoft
    VpcId: !Ref VpcId
    SecurityGroupIngress:
      - IpProtocol: tcp
        FromPort: 443
        ToPort: 443
        CidrIp: 0.0.0.0/0  # Microsoft Teams signaling can come from any IP
    SecurityGroupEgress:
      - IpProtocol: tcp
        FromPort: 8080
        ToPort: 8080
        DestinationSecurityGroupId: !Ref BotTaskSG

BotTaskSG:
  Type: AWS::EC2::SecurityGroup
  Properties:
    GroupDescription: ECS tasks for Teams bot
    VpcId: !Ref VpcId
    SecurityGroupIngress:
      - IpProtocol: tcp
        FromPort: 8080
        ToPort: 8080
        SourceSecurityGroupId: !Ref BotALBSG
    SecurityGroupEgress:
      # HTTPS — Graph API, Secrets Manager, token endpoint
      - IpProtocol: tcp
        FromPort: 443
        ToPort: 443
        CidrIp: 0.0.0.0/0
      # UDP — Teams RTP/SRTP media streams (ICE + SRTP over UDP)
      # Microsoft Teams media uses UDP 3478-3481 and 49152-65535
      - IpProtocol: udp
        FromPort: 3478
        ToPort: 3481
        CidrIp: 0.0.0.0/0
      - IpProtocol: udp
        FromPort: 49152
        ToPort: 65535
        CidrIp: 0.0.0.0/0
```

> **UDP egress is not optional.** Teams media sessions use SRTP over UDP. If UDP egress is blocked, the bot will answer the call (via HTTPS) but receive no audio — a silent recording. The UDP port ranges above are Microsoft's documented Teams media ports.

**ECS Execution Role (allows ECS to pull ECR image and write logs):**

```yaml
TeamsBotExecutionRole:
  Type: AWS::IAM::Role
  Properties:
    AssumeRolePolicyDocument:
      Statement:
        - Effect: Allow
          Principal:
            Service: ecs-tasks.amazonaws.com
          Action: sts:AssumeRole
    ManagedPolicyArns:
      - arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
    Policies:
      - PolicyName: SecretsAccess
        PolicyDocument:
          Statement:
            # Allow ECS to inject secrets into container env at startup
            - Effect: Allow
              Action: secretsmanager:GetSecretValue
              Resource:
                - !Sub arn:aws:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:sotto/azure/*
```

**EphemeralStorage for spill files (add to TaskDefinition):**

```yaml
# Add to TeamsBotTaskDef under Properties:
EphemeralStorage:
  SizeInGiB: 21  # Minimum is 21 — covers ~3 hours of audio per concurrent call
```

**CloudWatch Log Group:**

```yaml
TeamsBotLogGroup:
  Type: AWS::Logs::LogGroup
  Properties:
    LogGroupName: !Sub /aws/ecs/sotto-teams-bot-${Environment}
    RetentionInDays: 30
```

**Update ALB listener to reference correct cert parameter:**

```yaml
# Fix in TeamsBotListener — change:
CertificateArn: !Ref BotTLSCertificate
# To:
CertificateArn: !Ref BotTLSCertificateArn
```

#### Secrets Manager Bootstrap (manual, one-time)

```
sotto/azure/app_client_id       # Your Azure AD app client ID
sotto/azure/app_client_secret   # Your Azure AD app client secret (rotate annually)
```

No per-tenant secrets. The customer's `ms_tenant_id` is stored on their DynamoDB tenant record.

### 8.2 DynamoDB Schema Additions

**`sotto-tenants-{env}`** — new attributes:

| Attribute | Type | Description |
|---|---|---|
| `teams_enabled` | Boolean | Whether Teams integration is active |
| `ms_tenant_id` | String | Microsoft tenant ID (GUID) |
| `teams_policy_id` | String | ID of the created ComplianceRecordingPolicy |
| `teams_connected_at` | String | ISO timestamp of when admin granted consent |

**New GSI on `sotto-tenants-{env}`:**

```yaml
ms-tenant-index:
  KeySchema:
    - AttributeName: ms_tenant_id
      KeyType: HASH
  Projection:
    ProjectionType: INCLUDE
    NonKeyAttributes: [tenant_id, teams_enabled, teams_policy_id]
```

Used by the bot to resolve a Microsoft tenant ID (received in the incoming call JWT) to a Sotto `tenant_id`. Without this GSI, the bot would need a full table scan on every call.

**`sotto-agents-{env}`** — new attributes:

| Attribute | Type | Description |
|---|---|---|
| `ms_user_id` | String | Microsoft 365 user ID (GUID) |
| `teams_policy_assigned` | Boolean | Whether recording policy has been assigned |

**New GSI on `sotto-agents-{env}`:**

```yaml
ms-user-index:
  KeySchema:
    - AttributeName: ms_user_id
      KeyType: HASH
    - AttributeName: tenant_id
      KeyType: RANGE
  Projection:
    ProjectionType: INCLUDE
    NonKeyAttributes: [agent_id, agent_status]
```

Used by the bot to resolve `ms_user_id` → `agent_id` on each call.

**`sotto-calls-{env}`** — new attributes (Teams-specific, set by the bot):

| Attribute | Type | Description |
|---|---|---|
| `ms_call_id` | String | Microsoft's native call ID (GUID) |
| `bot_task_id` | String | ECS task ARN/ID that handled this call (for debugging) |
| `recording_status` | String | `in_progress` → `upload_complete` \| `partial_upload` \| `failed` |
| `partial_recording` | Boolean | `true` if recording was truncated (SIGTERM, crash) |
| `partial_reason` | String | `null` \| `graceful_shutdown` \| `network_loss` |
| `agent_channel` | Number | Stereo channel containing agent audio (always `0` for Teams) |

These attributes are only populated for `provider = "teams"`. All other providers leave them unset. The `recording_status` attribute enables the orphan detector (Section 5.6.7) to find stuck calls.

### 8.3 IAM Roles

**TeamsBotTaskRole** — permissions for the running bot container:

```yaml
TeamsBotTaskRole:
  Type: AWS::IAM::Role
  Properties:
    AssumeRolePolicyDocument:
      Statement:
        - Effect: Allow
          Principal:
            Service: ecs-tasks.amazonaws.com
          Action: sts:AssumeRole
    Policies:
      - PolicyName: TeamsBotPolicy
        PolicyDocument:
          Statement:
            - Effect: Allow
              Action: s3:PutObject
              Resource: !Sub arn:aws:s3:::sotto-recordings-${AWS::AccountId}/*
            - Effect: Allow
              Action: sqs:SendMessage
              Resource: !GetAtt CallEventsQueue.Arn
            - Effect: Allow
              Action:
                - dynamodb:GetItem
                - dynamodb:Query
              Resource:
                - !GetAtt AgentsTable.Arn
                - !Sub ${AgentsTable.Arn}/index/ms-user-index
                - !GetAtt TenantsTable.Arn
                - !Sub ${TenantsTable.Arn}/index/ms-tenant-index
            - Effect: Allow
              Action:
                - dynamodb:PutItem
                - dynamodb:UpdateItem
              Resource:
                - !GetAtt CallsTable.Arn
            - Effect: Allow
              Action: secretsmanager:GetSecretValue
              Resource:
                # Global Azure app credentials only — no per-tenant secrets needed
                - !Sub arn:aws:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:sotto/azure/*
            - Effect: Allow
              Action: cloudwatch:PutMetricData
              Resource: "*"
              Condition:
                StringEquals:
                  cloudwatch:namespace: Sotto/TeamBot
```

---

## 9. RecordingProcessor Changes

Two changes to the existing `RecordingProcessor`:

**Change 1 — Skip download for pre-uploaded recordings:**

```python
# In recording_processor.py, after deserializing the NormalizedCallEvent:

if event.recording_already_uploaded:
    # Teams bot already uploaded to S3 and created the DynamoDB call record (see 5.6.5).
    # Skip download. recording_s3_key is already set by the bot.
    logger.info("Recording pre-uploaded by bot, skipping download",
                extra={"provider": event.provider, "call_id": call_id,
                       "partial": event.partial})
    recording_s3_key = event.recording_s3_key
else:
    # Twilio/RingCentral/etc: download from provider URL
    recording_s3_key = download_and_upload_recording(event, tenant_id, call_id)
```

**Change 2 — UPDATE not CREATE for Teams call records:**

The bot creates the DynamoDB call record the moment it answers the call (Section 5.6.5), before any audio is captured. `RecordingProcessor` must NOT create it again — that would overwrite `bot_task_id`, `started_at`, and other bot-set fields.

```python
if event.recording_already_uploaded:
    # Record already exists — update it rather than creating
    db.update_call(tenant_id, call_id, {
        "status": "transcribing",
        "recording_s3_key": recording_s3_key,
        "recording_status": "upload_complete",
        "ended_at": event.ended_at,
        "duration_seconds": event.duration_seconds,
        "partial_recording": event.partial,
        "partial_reason": event.partial_reason,
        "agent_channel": event.agent_channel,
    })
else:
    # Non-Teams providers: create the record as before
    db.create_call(tenant_id, call_id, agent_id, event, recording_s3_key)
```

All other `RecordingProcessor` behavior (WebSocket notification, transcription trigger) is unchanged.

---

## 10. NormalizedCallEvent Model Addition

```python
# In sotto/models.py — add these fields to NormalizedCallEvent:

class NormalizedCallEvent(BaseModel):
    # ... existing fields ...

    recording_already_uploaded: bool = False
    # When True, RecordingProcessor skips download and uses recording_s3_key directly.
    # Set by TeamsMediaBot after it uploads to S3. All non-Teams providers leave False.

    recording_s3_key: str | None = None
    # Populated by TeamsMediaBot when recording_already_uploaded=True.
    # Format: {tenant_id}/recordings/{year}/{month}/{call_id}.mp3

    agent_channel: int = 0
    # For stereo recordings (Teams only): which MP3 channel contains the agent audio.
    # Always 0 for Teams — channel 0 = agent, channel 1 = client.
    # Used by TranscriptionResultProcessor to map ch_0/ch_1 → "agent"/"client".
    # Ignored by all non-Teams providers (their recordings are mono).

    partial: bool = False
    # True if the recording was terminated before the call ended (bot crash, SIGTERM, etc.)
    # Downstream pipeline still runs — partial transcript is better than nothing.

    partial_reason: str | None = None
    # One of: None | "graceful_shutdown" | "network_loss"
    # Stored on the call record for debugging and admin portal display.
```

---

## 11. What This Is NOT

Be precise in prospect conversations.

**This implementation does NOT provide:**
- HIPAA Business Associate Agreement coverage for call recordings via Microsoft's compliance framework
- SOC 2 Type II compliance recording certification
- Microsoft-certified compliance recording partner status
- Guarantee of 100% recording capture (with `requiredDuringCall: false`, the call proceeds unrecorded if the bot fails to join — this is intentional to avoid killing live calls)

**This DOES provide:**
- Automatic recording of all calls for Teams Phone users covered by the policy
- Same transcript + AI notes experience as Twilio/RingCentral providers
- No IT involvement after initial admin consent
- No per-call user action required
- Coverage of inbound, outbound, and internal Teams calls

For insurance agencies without HIPAA obligations (most P&C agencies), this is entirely sufficient. For agencies with strict compliance requirements, note the limitation proactively.

### 11.1 Compliance Roadmap — HIPAA

**HIPAA (Health Insurance Portability and Accountability Act)** governs the handling of Protected Health Information (PHI) — medical records, health conditions, treatment details. It applies to covered entities (healthcare providers, health plans) and their **business associates** (any vendor that handles PHI on their behalf).

**Does HIPAA apply to Sotto's target market?**

Most P&C (Property & Casualty) insurance agencies — auto, home, commercial — do not handle PHI. HIPAA is irrelevant to them. However, agencies that write health insurance or life insurance with medical underwriting may have calls where clients discuss medical conditions. Those recordings contain PHI, and HIPAA applies.

**Current state of the architecture:**

| Area | What's Already in Place | HIPAA Gap |
|---|---|---|
| Encryption in transit | SRTP for Teams media, HTTPS for all API calls | None — meets HIPAA transit encryption requirement |
| Encryption at rest | AWS default encryption on S3 and DynamoDB (AES-256) | Auditors prefer **customer-managed KMS keys** rather than AWS-managed keys, so you control rotation and revocation |
| Secret storage | Secrets Manager for Azure credentials, no secrets in env vars | None — meets HIPAA requirement |
| Access control | IAM least-privilege roles, JWT auth on all APIs | Missing **S3 access logging** — no audit trail of who accessed which recording |
| Data retention | Not defined | Need a **retention and deletion policy** — how long recordings are kept, automated lifecycle rules to delete after N days, and proof of deletion |
| BAA coverage | AWS BAA available (sign in AWS Artifact) | Need BAA with **Microsoft** covering the custom bot's use of Teams media streams — this may require a separate Microsoft Partner agreement |
| Minimum necessary | Agents see only their own calls; admins see all tenant calls | May need more granular admin access controls (role-based access within the admin portal) |
| Network isolation | ECS tasks in public subnets, Lambda uses AWS-managed networking | Auditors may require **VPC endpoints** for S3, SQS, DynamoDB, and Secrets Manager so data never traverses the public internet (even though it's encrypted) |
| Audit trail | CloudWatch logs for all Lambda invocations, ECS container logs | Missing **application-level audit log** — who viewed which transcript, who played which recording, who changed which setting |
| Breach notification | Not implemented | Need automated detection and alerting if recordings are accessed outside normal patterns |

**What to build when a HIPAA-covered prospect appears:**

1. **KMS customer-managed keys** — Create a KMS key per tenant (or a shared key with key policy restrictions). Apply to S3 bucket encryption and DynamoDB table encryption. Add `kms:Decrypt` and `kms:GenerateDataKey` permissions to the relevant IAM roles. This is additive — no architecture changes, just encryption configuration.

2. **S3 access logging** — Enable server access logging on the recordings bucket, writing logs to a separate logging bucket. This records every `GetObject`, `PutObject`, and `DeleteObject` with timestamp, requester ARN, and source IP.

3. **S3 lifecycle policy for data retention** — Add a lifecycle rule to delete recordings after a configurable number of days (e.g., 90 days, 1 year — depends on the customer's policy). Make this configurable per tenant in DynamoDB and enforced via S3 lifecycle rules or a scheduled Lambda.

4. **VPC endpoints** — Add gateway endpoints for S3 and DynamoDB, and interface endpoints for SQS, Secrets Manager, and CloudWatch Logs. This keeps all data traffic on AWS's private network. Cost: ~$7/month per interface endpoint per AZ.

5. **Application-level audit log** — Add a DynamoDB table (`sotto-audit-log-{env}`) that records: who accessed what, when, from where. Every call detail view, transcript read, recording playback, and settings change gets a row. Immutable (no update/delete permissions for the application).

6. **BAA with Microsoft** — Contact Microsoft Partner support to confirm BAA coverage for custom compliance recording bots using Graph Communications API. This is a business/legal step, not a technical one.

7. **Admin portal RBAC** — Add role-based access within the admin portal: `owner` (full access), `manager` (view calls, manage agents), `viewer` (view calls only). Prevents a billing admin from accessing call recordings they don't need.

**Estimated effort to add HIPAA layer:** 1-2 weeks of implementation + legal time for the Microsoft BAA.

### 11.2 Compliance Roadmap — SOC 2

**SOC 2 (System and Organization Controls 2)** is an audit framework that evaluates a company's controls across five Trust Service Criteria: Security, Availability, Processing Integrity, Confidentiality, and Privacy. It is not a technical specification — it's an organizational audit performed by a licensed CPA firm. You cannot "build SOC 2 into code." You prove to an auditor that your company has appropriate controls in place.

**When you need it:** When enterprise prospects include "SOC 2 Type II report" in their vendor security questionnaire. This is common with large insurance brokerages and MGAs (Managing General Agents). Smaller independent agencies rarely ask for it.

**SOC 2 Type I vs Type II:**
- **Type I:** Point-in-time assessment — "do these controls exist today?" Faster to get (~2-3 months of prep + audit). Good enough for initial enterprise sales.
- **Type II:** Assessment over a period (typically 6-12 months) — "have these controls been operating effectively over time?" The gold standard. Requires you to have been running the controls for months before the audit window.

**Current state of the architecture vs SOC 2 Trust Service Criteria:**

#### Security (required for all SOC 2 audits)

| Control Area | What's Already in Place | Gap |
|---|---|---|
| Access control | IAM roles, JWT auth, Cognito user pools | Need **MFA enforced** on admin accounts (Cognito supports this — just enable it) |
| Encryption | SRTP in transit, AES-256 at rest, HTTPS everywhere | None — solid |
| Network security | Security groups with least-privilege rules, no open SSH | Need documented **security group review process** |
| Vulnerability management | ECR image scanning on push | Need regular **dependency scanning** (Dependabot/Snyk on both Python and C# codebases) |
| Logging and monitoring | CloudWatch logs, container insights, Powertools structured logging | Need **centralized alerting** — CloudWatch alarms for error rates, failed auth attempts, orphan detector findings |
| Incident response | Not documented | Need a written **incident response plan** — who gets paged, escalation path, communication templates |
| Change management | CI/CD pipeline with PR checks, branch protection | Need documented **change approval process** (the CI/CD pipeline is the mechanism — just document it as a control) |

#### Availability

| Control Area | What's Already in Place | Gap |
|---|---|---|
| Redundancy | ECS across 2 AZs, DynamoDB multi-AZ by default, S3 11 nines durability | None — architecture is solid |
| Auto-scaling | ECS auto-scaling on ActiveCallCount, Lambda scales automatically | None |
| Disaster recovery | DynamoDB PITR in prod, S3 versioning (if enabled) | Need documented **DR plan** with RTO/RPO targets and tested restore procedures |
| Uptime monitoring | Health check on ALB | Need **external uptime monitoring** (e.g., Datadog synthetic, Pingdom) and a **status page** for customers |

#### Confidentiality

| Control Area | What's Already in Place | Gap |
|---|---|---|
| Data classification | Not documented | Need a **data classification policy** — call recordings are confidential, PII in transcripts, etc. |
| Data retention | Not defined | Need documented **retention and deletion policies** per data type |
| Data access | Agents see own calls, admins see tenant calls | Need **access logging** (who viewed what — same as HIPAA requirement) |

#### Processing Integrity

| Control Area | What's Already in Place | Gap |
|---|---|---|
| Idempotency | SQS retry with DLQ, idempotent handlers | None — good design |
| Error handling | Structured error responses, no unhandled exceptions | None |
| Data validation | Input validation on all API endpoints | Need documented **data quality checks** (e.g., recording file integrity verification after S3 upload) |

#### Privacy (only if you include this criterion)

| Control Area | What's Already in Place | Gap |
|---|---|---|
| Consent | Recording announcement played on every call | None for recording consent |
| PII handling | Phone numbers not logged, no recording URLs in logs | Need a **privacy policy** and documented PII inventory |
| Data subject requests | Not implemented | Need ability to **delete all data for a specific caller** on request (right to deletion) |

**What to build/document when pursuing SOC 2:**

**Technical (build):**
1. **MFA on admin Cognito accounts** — Enable in Cognito user pool settings. One config change.
2. **Centralized alerting** — CloudWatch alarms for: Lambda error rate > 1%, orphan detector findings > 0, failed auth attempts > 10/hour, ECS task unhealthy. Route to SNS → PagerDuty/Slack.
3. **External uptime monitoring** — Add synthetic health checks against the API and bot ALB.
4. **Dependency scanning** — Enable Dependabot on the repo for both Python and C# dependencies.
5. **Access logging** — Same as HIPAA item #5 (application-level audit log).
6. **Data retention lifecycle** — Same as HIPAA item #3.
7. **Caller data deletion endpoint** — Admin API endpoint to delete all calls, recordings, and transcripts associated with a phone number. For right-to-deletion requests.

**Organizational (document, not code):**
1. **Incident response plan** — Written runbook: detection → triage → containment → resolution → postmortem.
2. **Change management policy** — Document the existing CI/CD process as a formal control (PR required, checks must pass, branch protection on main).
3. **Data classification policy** — Define what's confidential (recordings, transcripts), what's internal (config, metrics), what's public (nothing).
4. **DR plan** — Document RTO/RPO targets, test DynamoDB PITR restore quarterly, test S3 cross-region replication if added.
5. **Security group review cadence** — Quarterly review of all security group rules, documented.
6. **Vendor management** — Document AWS and Microsoft as sub-processors with their own SOC 2 reports (both have them).
7. **Employee security training** — Annual security awareness training for all staff with access to production.

**Estimated timeline:**
- **SOC 2 Type I:** ~3 months (1 month technical gaps, 1 month documentation, 1 month audit)
- **SOC 2 Type II:** Begin the observation period after Type I controls are in place. Audit window is typically 6 months. Total: ~9-12 months from start.
- **Cost:** Auditor fees range $20K-$50K for a startup-scale Type II. Tools (Vanta, Drata, Secureframe) that automate evidence collection cost $10K-$25K/year but dramatically reduce prep time.

### 11.3 Compliance Summary — What to Tell Prospects

**If a P&C agency asks about compliance:**
> "All data is encrypted in transit and at rest on AWS infrastructure. Call recordings are stored in your isolated tenant space. We use IAM least-privilege access controls and structured audit logging. For P&C agencies, this meets industry standard security requirements."

**If a health/life insurance agency asks about HIPAA:**
> "Our architecture is built on HIPAA-eligible AWS services and supports customer-managed encryption keys, access logging, and configurable data retention. We can sign a BAA and enable the HIPAA controls for your tenant. Let's discuss your specific requirements."
>
> (Then scope and build the HIPAA layer items from 11.1 before onboarding them.)

**If an enterprise prospect asks about SOC 2:**
> "We are pursuing SOC 2 Type II certification. Our infrastructure includes encryption at rest and in transit, IAM least-privilege, CI/CD change management, multi-AZ redundancy, and structured logging. We can share our security documentation and our timeline to audit completion."
>
> (Then decide if the deal size justifies accelerating the SOC 2 timeline.)

---

## 12. Development & Testing Environment

### 12.1 Free Dev Tenant — Microsoft 365 Developer Program

**URL:** [developer.microsoft.com/en-us/microsoft-365/dev-program](https://developer.microsoft.com/en-us/microsoft-365/dev-program)

This is the primary free environment for building and testing the entire Teams integration before touching a real customer tenant.

**What you get:**
- 25 user licenses with full Microsoft 365 **E5** (includes Teams, Azure AD, and Teams Phone System add-on)
- Your own isolated `yourdomain.onmicrosoft.com` Microsoft tenant that you control completely
- Full Teams Admin Center access (verify compliance recording policies appear correctly under Voice → Recording Policies)
- 90-day subscription, automatically renewed as long as you are actively using it for development

**Eligibility note:** Microsoft periodically tightens signup requirements. As of early 2026, linking a GitHub account typically qualifies. Check current requirements at signup — a Visual Studio subscription also qualifies.

**What you can test for free with this tenant:**

| Test | Coverage | Notes |
|---|---|---|
| Azure AD app registration | Full | Register the multi-tenant app here or in a separate Azure account |
| OAuth admin consent flow | Full | You are the Global Admin — walk through the exact consent screen your customers will see |
| ComplianceRecordingPolicy creation | Full | Create via Graph API, verify it appears in Teams Admin Center |
| Policy assignment to users | Full | Assign to test agent users, verify assignment in Admin Center |
| Bot joining calls | Full | Internal Teams-to-Teams calls trigger the compliance policy identically to real calls |
| Dual-channel audio capture | Full | Two users in the same tenant produce two separate audio streams |
| Recording pipeline end-to-end | Full | S3 upload, SQS, Transcribe, AI summary, WebSocket push to Cockpit |
| Inbound/outbound PSTN | Not free | Real phone calls require a Microsoft Calling Plan (~$8/user/month) or Direct Routing — not needed for MVP testing |

PSTN testing can be deferred. Internal Teams-to-Teams calls exercise the exact same bot-join mechanism that policy-based recording uses for PSTN calls — the policy trigger is identical.

### 12.2 Azure Free Tier

**URL:** [azure.microsoft.com/free](https://azure.microsoft.com/free)

- **$200 credit** for the first 30 days
- Azure AD app registration is **always free** (no credit needed)
- Azure Bot Service registration is **free**
- The Azure side of this integration (app registration + bot service) costs nothing — all spend is on the AWS side (ECS Fargate tasks, S3 storage, SQS)

### 12.3 Local Development Setup

**ngrok** ([ngrok.com](https://ngrok.com)) — free tier available. Creates a public HTTPS URL tunneled to your local machine so Microsoft Teams can reach a bot running on your laptop.

```
ngrok http 8080
# Output: https://abc123.ngrok-free.app → localhost:8080
```

Set this as the Azure Bot Service notification URL during development. Replace with the real ALB URL in Step T-5 when deploying to AWS.

**Recommended local dev workflow:**

1. Sign up for M365 Developer Program → get your `yourdomain.onmicrosoft.com` tenant
2. Register the Azure AD multi-tenant app in that tenant (or any Azure account)
3. Start ngrok, point Azure Bot Service notification URL at the ngrok URL
4. Run the bot locally (`dotnet run`)
5. Open two browser windows: one logged in as the test agent account, one as the test client account
6. Make a Teams call between the two accounts
7. Bot receives the compliance recording notification, joins the call, records
8. Verify the MP3 appears in S3, SQS message sent, transcript produced

**Cost to develop through Step T-4:** $0 (free M365 dev tenant + free Azure app registration + free ngrok + minimal AWS dev costs)

---

## 13. Build Sequence

Build in this order. Each step is independently testable.

**Step T-1: Azure AD App Registration (one-time, manual)**
- [ ] Register multi-tenant app in Azure portal (app type: Web, multi-tenant)
- [ ] Add required application permissions (Section 3.3) and grant admin consent in YOUR own tenant first
- [ ] Register in Azure Bot Service: enable Microsoft Teams channel + Calling feature
- [ ] Set notification URL to a temporary ngrok URL for development (will update to ALB URL in T-5)
- [ ] Bootstrap Secrets Manager: store `sotto/azure/app_client_id` and `sotto/azure/app_client_secret`
- [ ] Verify: call the client_credentials token endpoint manually with your own tenant_id — confirm you get a valid access_token

**Step T-2: DynamoDB Schema Updates**
*(Must come before T-3 — the onboarding Lambda reads/writes these new fields)*
- [ ] Add `ms_user_id`, `teams_policy_assigned` to agents table definition in `template.yaml`
- [ ] Add `teams_enabled`, `ms_tenant_id`, `teams_policy_id`, `teams_connected_at` to tenants table definition
- [ ] Add `ms-user-index` GSI to agents table in `template.yaml`
- [ ] Add `ms-tenant-index` GSI to tenants table in `template.yaml`
- [ ] Add `bot_task_id`, `recording_status`, `partial_recording`, `partial_reason`, `agent_channel`, `ms_call_id` to calls table definition
- [ ] Add VPC/subnet/cert parameters to `template.yaml` Parameters block
- [ ] Deploy to dev, verify GSI exists

**Step T-3: TeamsOnboarding Lambda**
- [ ] Implement `/teams/connect` route: returns the adminconsent redirect URL with correct params
- [ ] Implement `/teams/oauth/callback` handler: verify `admin_consent=True`, store `ms_tenant_id`, get token via client_credentials, create policy, assign to agents
- [ ] Add both routes to SAM template
- [ ] Write unit tests: mock the Graph API calls, assert DynamoDB updates are correct
- [ ] Test end-to-end: use your own Microsoft 365 tenant, walk through consent flow, verify policy appears in Teams Admin Center (`Get-CsTeamsComplianceRecordingPolicy` in PowerShell or check Teams Admin Center → Voice → Recording policies)

**Step T-4: TeamsMediaBot Container (C#)**
- [ ] Clone Microsoft's PolicyRecordingBot sample: https://github.com/microsoftgraph/microsoft-graph-comms-samples/tree/master/Samples/V1.0Samples/LocalMediaSamples/PolicyRecordingBot
- [ ] Create `/teams-bot` directory in repo root, initialize .NET 8 Web project
- [ ] Add NuGet packages: `Microsoft.Graph.Communications.Calls.Media`, `NAudio`, `NAudio.Lame`, `AWSSDK.S3`, `AWSSDK.SQS`, `AWSSDK.DynamoDBv2`, `AWSSDK.CloudWatch`
- [ ] Implement `NotificationController`: receive notifications, validate Authorization JWT (Section 5.4)
- [ ] Implement `CallHandler`: answer calls (using SDK, not raw HTTP), open AudioSocket with `ReceiveUnmixedMeetingAudio=true`
- [ ] Implement token cache: per-tenant in-memory token cache with 5-minute pre-expiry refresh using client_credentials
- [ ] Implement tiered audio buffering (Section 5.6.2): in-memory → /tmp spill files at 40MB
- [ ] Implement channel alignment (Section 5.6.3)
- [ ] Implement stereo MP3 encoding + S3 multipart upload (Section 5.6.4)
- [ ] Implement SIGTERM handler and `ShutdownGracefully` (Section 5.6.6)
- [ ] Implement CloudWatch `ActiveCallCount` metric publishing
- [ ] Implement DynamoDB call record create (on answer) and update (on finalize)
- [ ] Implement SQS message publish with all new fields
- [ ] Write Dockerfile (multi-stage build):
  ```dockerfile
  # Stage 1: Build
  FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
  WORKDIR /src
  COPY *.csproj .
  RUN dotnet restore
  COPY . .
  RUN dotnet publish -c Release -o /app/publish

  # Stage 2: Runtime
  FROM mcr.microsoft.com/dotnet/aspnet:8.0
  # libmp3lame0 is the native LAME library — required by NAudio.Lame for MP3 encoding on Linux
  RUN apt-get update && apt-get install -y --no-install-recommends libmp3lame0 && rm -rf /var/lib/apt/lists/*
  WORKDIR /app
  COPY --from=build /app/publish .
  EXPOSE 8080
  ENTRYPOINT ["dotnet", "TeamsBot.dll"]
  ```
- [ ] Test locally with ngrok: make a call on Teams-enabled test tenant, verify bot joins, announcement plays, MP3 appears in S3, SQS message sent

**Step T-5: AWS Infrastructure**
- [ ] Bootstrap VPC with 2 public subnets across 2 AZs (manual, like S3 artifact buckets)
- [ ] Request ACM certificate for `bots.sotto.cloud`, validate via DNS
- [ ] Add all new SAM resources to `template.yaml`: ECS cluster, task definition (with EphemeralStorage), service, ALB, ECR, target group, listener, log group, security groups, execution role, task role, auto-scaling
- [ ] Add `TeamsOnboardingFunction` and `TeamsOrphanDetectorFunction` to `template.yaml`
- [ ] Update Azure Bot Service notification URL from ngrok to `https://bots.sotto.cloud/api/calling/notifications`
- [ ] Deploy to dev

**Step T-6: CI/CD Integration**
- [ ] Add bot container build step to `pr-checks.yml` (build only, no push)
- [ ] Add bot container build + ECR push + ECS forced redeploy to `deploy-dev.yml` and `deploy-prod.yml`
- [ ] Test: merge to main → bot image builds → ECR push → ECS service updates → verify health check passes

**Step T-7: RecordingProcessor + NormalizedCallEvent Updates**
- [ ] Add new fields to `NormalizedCallEvent` in `models.py` (Section 10)
- [ ] Add conditional download skip in `RecordingProcessor` (Section 9, Change 1)
- [ ] Add conditional UPDATE vs CREATE for Teams call records (Section 9, Change 2)
- [ ] Add `get_transcription_settings(provider)` to `TranscriptionInit` (Section 5.6.8)
- [ ] Add `resolve_speaker_label()` to `TranscriptionResultProcessor` (Section 5.6.8)
- [ ] Write unit tests for all new code paths
- [ ] Deploy and verify end-to-end: Teams call → S3 → Transcribe → Cockpit with correct speaker labels

**Step T-8: Agent Onboarding Update**
- [ ] Add Microsoft 365 sign-in step to `AgentConfirm` flow (only if `tenant.teams_enabled`)
- [ ] Store `ms_user_id` on agent record
- [ ] Trigger recording policy assignment for newly confirmed agent

**Step T-9: Admin Portal Update**
- [ ] Add "Connect Microsoft Teams" button to Settings page (calls `/teams/connect` to get redirect URL)
- [ ] Show Teams connection status: connected / N agents covered / connected date
- [ ] Handle OAuth callback redirect with success/error state display
- [ ] Show `partial_recording` indicator on call detail page

**Step T-10: End-to-End Validation**
- [ ] Inbound PSTN: external number calls Teams Phone agent → recorded, transcribed, in Cockpit ✓
- [ ] Outbound PSTN: Teams Phone agent dials external number → recorded ✓
- [ ] Internal: Teams user calls another Teams user (both on policy) → both copies recorded ✓
- [ ] Verify recording announcement plays and appears at start of transcript
- [ ] Verify transcript has correct `agent`/`client` labels (not `spk_0`/`spk_1`)
- [ ] Verify partial recording flag when bot is SIGTERM'd mid-call
- [ ] Verify orphan detector marks stuck calls after 2 hours
- [ ] Verify calls for users NOT on policy are NOT recorded
- [ ] Verify admin users (not in Sotto agent list) are NOT on policy

---

## 14. Open Questions to Resolve Before Building

1. **~~Domain for bot endpoint~~** — **Resolved.** Domain is `sotto.cloud`. Bot endpoint is `https://bots.sotto.cloud/api/calling/notifications`. API OAuth callback is `https://api.sotto.cloud/teams/oauth/callback`. DNS records and ACM certificate for `bots.sotto.cloud` must be provisioned before Step T-5. Use ngrok locally during development (Steps T-1 through T-4), swap to the real URL in T-5.

2. **Recording announcement audio file:** Who records the legal notice prompt? Needs to exist as a `.wav` file before T-4 testing. Options: professional voice talent, TTS tool, or a placeholder recording to unblock development.

3. **~~Token refresh strategy~~** — **Resolved.** Using client_credentials grant, tokens expire in 1 hour and are simply re-requested. No refresh tokens, no Secrets Manager updates. Cache per-tenant in memory with expiry. Non-issue.

4. **Multi-region:** Sotto currently deploys to one region. Teams media sessions have audio quality implications for geographically distant bots (added latency on the media path). At MVP scale, single-region is acceptable. Flag to customers in Canada/UK if latency becomes audible. Track as a known limitation to address when multi-region is warranted.

5. **Agent `ms_user_id` bootstrapping for existing agents:** When Teams is first connected, existing agents don't have `ms_user_id` set yet. The recording policy will be assigned at the Microsoft tenant level, but the bot can't resolve callers to Sotto agents until each agent completes the Microsoft sign-in step.

   **Recommended decision:** Assign the recording policy immediately on connect. The bot records all policy-assigned calls regardless. For calls where `ms_user_id` resolution fails, the call is still recorded and transcribed — it just won't have an `agent_id` on the record (same behavior as other providers when a number mapping is missing). Agents who haven't linked their Microsoft account will see their calls appear as "unassigned" in the admin view until they link.

6. **Graph API policy assignment endpoint verification:** The `assignComplianceRecordingPolicy` endpoint used in Section 4.3 is documented in the Microsoft Teams PowerShell module. Verify the exact Graph API v1.0 or beta equivalent is available and behaves as expected before building the onboarding Lambda. If it's only available via PowerShell, the onboarding flow needs to invoke PowerShell or use the Teams Admin Center endpoint. Test this during Step T-1.

7. **How the bot receives notifications (no per-tenant subscription needed):** The bot's notification URL is registered once in Azure Bot Service. Microsoft Teams routes policy-based recording notifications to that URL automatically for any tenant that has granted consent and has the compliance recording policy active. No per-tenant webhook subscription management is required. This is simpler than standard Graph change notifications.

---

## 15. Glossary

Every acronym and technical term used in this document, defined in plain language.

### Microsoft / Azure Terms

| Term | What It Is |
|---|---|
| **ACS (Azure Communication Services)** | Microsoft's cloud telephony toolkit for building custom calling/messaging apps. BlueC uses this for their recording bot. The problem: ACS only records calls that flow *through* ACS infrastructure. If an agent makes a regular Teams call, ACS isn't in the path and can't see or record it. This is why blueC is stuck — they can't auto-record all call types. |
| **SBC (Session Border Controller)** | A network device (hardware or virtual) that sits between a company's phone system and the outside phone network (PSTN). To make ACS work with existing Teams Phone calls, you'd have to reconfigure the customer's SBC to reroute calls through ACS first. That means IT involvement, firewall changes, vendor coordination — the opposite of a simple onboarding. Sotto's approach bypasses SBC entirely. |
| **Azure AD (Azure Active Directory)** | Microsoft's identity and access management service. Every Microsoft 365 organization has an Azure AD tenant. When we say "Azure AD app," we mean an application registered in Azure AD that can request permissions to act on behalf of (or within) a Microsoft 365 organization. Now officially renamed to "Microsoft Entra ID" but universally still called Azure AD. |
| **Graph API (Microsoft Graph)** | Microsoft's unified REST API for interacting with Microsoft 365 services — users, calendar, Teams, calls, policies, etc. All of Sotto's interaction with the customer's Microsoft tenant (creating policies, assigning them to users, answering calls) goes through Graph API. |
| **OAuth / OAuth 2.0** | An industry-standard authorization protocol. When the admin clicks "Accept" on Microsoft's consent screen, that's OAuth in action — the admin is granting Sotto's app permission to act within their Microsoft tenant. Sotto uses the `client_credentials` grant type, which means the app authenticates as itself (not as a specific user). |
| **Admin Consent** | A specific OAuth flow where a Microsoft 365 Global Admin grants an application permission to access their entire organization's data. Different from regular user consent — admin consent applies organization-wide and is required for the high-privilege permissions Sotto needs (call access, recording policy management). |
| **Compliance Recording Policy** | A Microsoft Teams policy that automatically adds a recording bot to every call made by users the policy is assigned to. This is the core mechanism that solves auto-join. It's enforced at the Teams platform level — no user action, no SBC changes, no ACS routing. The admin creates it once via Graph API and assigns it to agents. |
| **Teams Phone** | Microsoft's cloud-based phone system (formerly called "Phone System"). Allows Microsoft 365 users to make and receive PSTN calls (real phone calls to/from external phone numbers) through Teams. This is distinct from Teams-to-Teams calls — Teams Phone adds actual phone number connectivity. |
| **Multi-Tenant App** | An Azure AD application configured to accept sign-ins from users in *any* Microsoft 365 organization, not just the one where the app is registered. Sotto registers one app in its own Azure tenant; every customer organization authorizes that same app. Standard SaaS pattern — identical to how Salesforce, HubSpot, and every other Microsoft 365 integration works. |

### Telephony & Media Terms

| Term | What It Is |
|---|---|
| **PSTN (Public Switched Telephone Network)** | The global system of phone lines and switches that carries traditional phone calls. When an insurance agent dials a client's cell phone from Teams, that call goes over the PSTN. "Inbound PSTN" = someone calling the agent's Teams number. "Outbound PSTN" = the agent calling an external number. |
| **RTP (Real-time Transport Protocol)** | The protocol used to deliver audio and video over IP networks. When the bot receives a call's audio stream from Teams, the audio arrives as RTP packets — small chunks of audio data sent many times per second. |
| **SRTP (Secure RTP)** | RTP with encryption. Teams encrypts the audio stream between its servers and the bot using SRTP so nobody can intercept and listen to the call in transit. The Microsoft media SDK handles the encryption/decryption automatically. |
| **ICE (Interactive Connectivity Establishment)** | A protocol for finding the best network path between two endpoints (here, between Teams and the bot). When the bot answers a call, ICE negotiation determines how the audio packets will travel. The SDK handles this automatically. |
| **PCM (Pulse-Code Modulation)** | Raw, uncompressed digital audio. The audio the bot receives from Teams is PCM — the most basic digital representation of sound. It's large (32KB/second per channel) but lossless. The bot converts this to MP3 before uploading to reduce storage costs. |
| **Stereo / Dual-Channel** | Two separate audio tracks in one file. In Sotto's Teams recordings, channel 0 (left) contains only the agent's voice and channel 1 (right) contains only the client's voice. This allows AWS Transcribe to produce perfectly accurate speaker attribution — no guessing about who said what. |
| **Diarization** | The process of figuring out "who spoke when" in an audio recording. For mono (single-channel) recordings from Twilio/RingCentral, AWS Transcribe uses ML-based diarization — it guesses which voice belongs to which speaker. For Teams stereo recordings, diarization is unnecessary because each speaker is already on a separate channel. |

### AWS Terms

| Term | What It Is |
|---|---|
| **ECS (Elastic Container Service)** | AWS service for running Docker containers. Sotto uses ECS Fargate (serverless containers) for the Teams bot because the bot needs persistent network connections that Lambda can't provide. |
| **Fargate** | AWS's serverless compute engine for containers. You define a container image and resource requirements; AWS handles the underlying servers. No EC2 instances to manage. The Teams bot runs on Fargate. |
| **ECR (Elastic Container Registry)** | AWS's Docker image registry. The bot's container image is built in CI/CD, pushed to ECR, and ECS pulls it from there when launching tasks. |
| **ALB (Application Load Balancer)** | AWS load balancer that distributes incoming HTTPS traffic across multiple ECS tasks. Microsoft Teams sends bot notifications to the ALB, which routes them to a healthy bot container. |
| **S3 (Simple Storage Service)** | AWS object storage. All recordings (MP3 files) and transcripts (JSON files) are stored in S3. |
| **SQS (Simple Queue Service)** | AWS message queue. The bot sends a message to SQS after uploading a recording, which triggers the existing Sotto pipeline (transcription → AI summary → WebSocket push to Cockpit). |
| **DynamoDB** | AWS's NoSQL database. Sotto stores all structured data here — tenants, agents, calls, number mappings, WebSocket connections, feature flags. |
| **GSI (Global Secondary Index)** | A DynamoDB feature that lets you query a table by a different key than the primary key. For example, `ms-tenant-index` lets the bot look up a Sotto tenant by Microsoft tenant ID, and `ms-user-index` lets it resolve a Microsoft user ID to a Sotto agent. |
| **SAM (Serverless Application Model)** | AWS's framework for defining serverless infrastructure as code (YAML templates). Sotto's entire backend is defined in a SAM `template.yaml`. All new Teams resources are added to this same template. |
| **ACM (AWS Certificate Manager)** | AWS service for provisioning SSL/TLS certificates. The bot's ALB needs an HTTPS certificate for `bots.sotto.cloud`, provisioned through ACM. |
| **VPC (Virtual Private Cloud)** | An isolated network within AWS. Most of Sotto doesn't use a VPC (Lambda functions use AWS's managed networking). The ECS bot service is the sole exception — ECS requires a VPC. Uses 2 public subnets, no NAT Gateway. |
| **NAT Gateway** | A managed network device that lets resources in a private subnet reach the internet. Sotto's VPC does NOT use one — the ECS tasks sit in public subnets with public IPs, keeping the architecture simple and cost-free (NAT Gateways cost ~$32/month + data charges). |
| **Secrets Manager** | AWS service for storing and rotating secrets (API keys, passwords). Sotto stores exactly two Azure secrets here: the app client ID and client secret. No per-tenant secrets. |
| **EventBridge** | AWS's serverless event bus. Used to trigger the orphan detector Lambda on a schedule (every 15 minutes) and to receive AWS Transcribe job completion events. |
| **DLQ (Dead Letter Queue)** | A secondary SQS queue where messages go after failing processing multiple times. Prevents poison messages from blocking the main queue. Sotto's call event queue retries 3 times before sending to the DLQ. |
| **PITR (Point-in-Time Recovery)** | A DynamoDB backup feature that lets you restore a table to any second within the last 35 days. Enabled in production only. |

### Security & Auth Terms

| Term | What It Is |
|---|---|
| **JWT (JSON Web Token)** | A compact, signed token used for authentication. When Teams sends a notification to the bot, it includes a JWT in the `Authorization` header. The bot validates this JWT to prove the request actually came from Microsoft, not an attacker. Sotto's own APIs also use JWTs (from Cognito) for agent and admin authentication. |
| **OIDC (OpenID Connect)** | An identity layer on top of OAuth 2.0. Microsoft publishes OIDC metadata (public keys, endpoints) that the bot uses to validate incoming JWTs. |
| **HMAC (Hash-based Message Authentication Code)** | A method for verifying that a message hasn't been tampered with, using a shared secret key. Twilio and RingCentral use HMAC-based signatures on their webhooks. Teams uses JWT validation instead. |
| **client_credentials grant** | An OAuth 2.0 flow where an application authenticates as itself (using its own client ID and client secret), not on behalf of a user. This is how Sotto's bot gets access tokens for a customer's Microsoft tenant — it sends its credentials + the customer's tenant ID, and gets back a token. No user login needed. No refresh tokens. |
| **E.164** | The international phone number format: `+` followed by country code and number (e.g., `+15195550100`). Sotto normalizes all phone numbers to E.164. |

### Bot & Container Terms

| Term | What It Is |
|---|---|
| **Azure Bot Service** | Microsoft's hosted service for registering bots. The bot is registered here once with a notification URL pointing to Sotto's ALB. When Teams needs to notify the bot about a call, it uses this registration to know where to send the notification. |
| **appHostedMediaConfig** | The configuration mode where the bot hosts its own media endpoint and receives the raw audio stream. This is what Sotto uses. The alternative (`serviceHostedMediaConfig`) means Microsoft hosts the media and the bot gets no audio — wrong choice. |
| **SIGTERM** | A Unix signal sent to a process asking it to shut down gracefully. ECS sends SIGTERM to the bot container before stopping it (for scaling, deployments, etc.). The bot catches this and uploads whatever audio it has as a partial recording before exiting. |
| **SIGKILL** | A Unix signal that immediately terminates a process with no chance to clean up. If the bot doesn't exit within 120 seconds after SIGTERM, ECS sends SIGKILL. Any in-progress recordings are lost (the orphan detector catches these). |
| **NAudio / NAudio.Lame** | C# libraries for audio processing. NAudio handles reading/writing audio formats. NAudio.Lame wraps the LAME MP3 encoder for converting raw PCM audio to MP3. Requires the native `libmp3lame0` library installed in the Docker container. |
| **ngrok** | A tool that creates a public HTTPS URL tunneled to your local machine. Used during development so Microsoft Teams can reach the bot running on a developer's laptop. Replaced by the real ALB URL in production. |

### Sotto-Specific Terms

| Term | What It Is |
|---|---|
| **Cockpit** | Sotto's Chrome extension that displays as a side panel next to Applied Epic (the insurance CRM). Shows call transcripts, AI summaries, and action items in real time. |
| **NormalizedCallEvent** | Sotto's internal data model that represents a completed call, regardless of which provider it came from. Every provider adapter converts raw webhook data into this model. The Teams bot constructs one directly after recording. |
| **Adapter Pattern** | Sotto's architecture for supporting multiple phone providers. Each provider (Twilio, RingCentral, Teams, etc.) has an adapter class that normalizes its webhooks into a `NormalizedCallEvent`. The rest of the pipeline doesn't know or care which provider the call came from. |
| **Applied Epic** | The CRM (customer relationship management) software used by insurance agencies to manage policies, clients, and workflows. Sotto's Cockpit sits beside it in Chrome. |

### Compliance & Insurance Industry Terms

| Term | What It Is |
|---|---|
| **HIPAA (Health Insurance Portability and Accountability Act)** | US federal law governing the protection of Protected Health Information (PHI). Applies to healthcare providers, health plans, and their business associates. Most P&C insurance agencies are NOT subject to HIPAA — it only matters if the agency handles health or life insurance where medical details are discussed on calls. |
| **PHI (Protected Health Information)** | Any health-related information that can be tied to a specific person — diagnoses, treatments, medications, health conditions. If a caller says "I need to update my policy because I was diagnosed with diabetes," that's PHI. Call recordings containing PHI trigger HIPAA requirements. |
| **BAA (Business Associate Agreement)** | A legal contract required by HIPAA between a covered entity (the agency) and any vendor that handles PHI on their behalf (Sotto). The BAA defines what the vendor can and cannot do with PHI, and what happens in a breach. AWS offers a standard BAA (sign via AWS Artifact). Microsoft's BAA coverage for custom bot use of Teams media streams requires separate confirmation. |
| **SOC 2 (System and Organization Controls 2)** | An audit framework developed by the AICPA (American Institute of CPAs) that evaluates a company's controls across five areas: Security, Availability, Processing Integrity, Confidentiality, and Privacy. It is NOT a technical certification — it's an organizational audit performed by a licensed CPA firm. Enterprise prospects often require a SOC 2 Type II report before signing. |
| **SOC 2 Type I vs Type II** | Type I is a point-in-time snapshot — "do the controls exist today?" Type II covers a period (typically 6-12 months) — "have the controls been working consistently over time?" Type II is the gold standard. Type I is faster to get and can unblock early enterprise deals. |
| **KMS (Key Management Service)** | AWS service for creating and managing encryption keys. HIPAA auditors prefer customer-managed KMS keys (where you control rotation and revocation) over AWS-managed default encryption. Adds a layer of provable control over who can decrypt data. |
| **MFA (Multi-Factor Authentication)** | Requiring a second form of verification (phone code, authenticator app) in addition to a password. SOC 2 auditors expect MFA on all admin accounts. Cognito supports MFA — just needs to be enabled. |
| **RBAC (Role-Based Access Control)** | Restricting system access based on a user's role. Example: a `viewer` role can see call transcripts but can't change settings or delete recordings. Needed for both HIPAA (minimum necessary rule) and SOC 2 (access control). |
| **PII (Personally Identifiable Information)** | Any data that can identify a specific person — name, phone number, email, Social Security number. Call recordings and transcripts contain PII by nature (caller's voice, phone number, potentially their name). Relevant to privacy regulations and SOC 2's Privacy criterion. |
| **RTO (Recovery Time Objective)** | How long you can afford to be down after a disaster before it's unacceptable. Example: "4-hour RTO" means the system must be restored within 4 hours of an outage. SOC 2 auditors want this documented. |
| **RPO (Recovery Point Objective)** | How much data loss is acceptable in a disaster. Example: "1-hour RPO" means you can afford to lose up to 1 hour of data. DynamoDB PITR gives a 1-second RPO (can restore to any point in the last 35 days). |
| **P&C (Property & Casualty)** | The branch of insurance covering physical assets (property) and liability (casualty). Includes auto, homeowners, commercial, workers' comp. This is Sotto's primary market. P&C agencies typically do NOT handle PHI and are not subject to HIPAA. |
| **MGA (Managing General Agent)** | A specialized insurance intermediary with underwriting authority granted by insurers. MGAs are often larger and more enterprise-oriented than independent agencies. They are more likely to require SOC 2 reports from vendors. |
| **CRM (Customer Relationship Management)** | Software for managing client relationships and business data. In insurance, the dominant CRM is Applied Epic. Sotto's Cockpit Chrome extension sits beside it. |
