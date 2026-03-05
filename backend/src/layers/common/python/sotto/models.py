"""Pydantic models matching DynamoDB schemas exactly (Section 5)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


# ── 5.1 Tenants ──────────────────────────────────────────────
class Tenant(BaseModel):
    tenant_id: str
    agency_name: str
    admin_email: str
    status: str  # active | suspended | trial
    plan: str  # trial | starter | pro
    provider_type: str  # twilio | ringcentral | zoom | teams | 8x8
    deployment_tier: str  # beta | live_test | full
    created_at: str  # ISO 8601
    updated_at: str  # ISO 8601
    twilio_account_sid: Optional[str] = None
    twilio_phone_number: Optional[str] = None


# ── 5.2 Agents ───────────────────────────────────────────────
class Agent(BaseModel):
    tenant_id: str
    agent_id: str
    email: str
    name: str
    status: str  # invited | active | inactive
    cognito_sub: Optional[str] = None
    created_at: str
    invited_at: str
    confirmed_at: Optional[str] = None


# ── 5.3 Number Mappings ─────────────────────────────────────
class NumberMapping(BaseModel):
    tenant_id: str
    identifier: str  # +15551234567 | ext:204 | email:user@co.com
    agent_id: str
    identifier_type: str  # did | extension | email | sip
    label: str
    created_at: str


# ── 5.4 Calls ────────────────────────────────────────────────
class Call(BaseModel):
    tenant_id: str
    call_id: str
    agent_id: Optional[str] = None
    provider: str  # twilio | ringcentral | zoom | teams | 8x8
    provider_call_id: str
    direction: str  # inbound | outbound
    from_number: str  # E.164
    to_identifier: str
    duration_sec: int
    status: str  # recording | transcribing | summarizing | complete | failed
    recording_s3_key: Optional[str] = None
    transcript_s3_key: Optional[str] = None
    transcript_status: Optional[str] = None  # pending | in_progress | complete | failed
    summary: Optional[str] = None
    action_items: Optional[list] = None
    notes: Optional[str] = None
    notes_updated_at: Optional[str] = None
    created_at: str
    ended_at: Optional[str] = None
    # Synthetic attribute for agent-date-index GSI
    agent_date_key: Optional[str] = None


# ── 5.5 WebSocket Connections ────────────────────────────────
class WSConnection(BaseModel):
    connection_id: str
    agent_id: str
    tenant_id: str
    connected_at: str
    ttl: int  # Unix timestamp


# ── 5.6 Feature Flags ───────────────────────────────────────
class FeatureFlag(BaseModel):
    flag_name: str
    enabled_tiers: list  # e.g. ['beta', 'live_test', 'full']
    description: str
    default_value: bool
    updated_at: str


# ── 5.7 Deployments ─────────────────────────────────────────
class Deployment(BaseModel):
    deployment_id: str
    git_sha: str
    git_tag: str
    lambda_version: str
    alias: str  # CANARY | LIVE
    status: str  # canary | live_test | full | rolled_back
    error_rate_at_deploy: Optional[float] = None
    deployed_at: str
    promoted_at: Optional[str] = None
    rolled_back_at: Optional[str] = None
    deployed_by: str


# ── 7.2 NormalizedCallEvent ──────────────────────────────────
class NormalizedCallEvent(BaseModel):
    tenant_id: str
    provider: str  # twilio | ringcentral | zoom | teams | 8x8
    provider_call_id: str
    direction: str  # inbound | outbound
    from_number: str  # E.164
    to_identifier: str
    duration_sec: int
    recording_url: str
    recording_format: str  # mp3 | wav | ogg
    ended_at: datetime
    raw_payload: dict
