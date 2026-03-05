const API_URL = import.meta.env.VITE_API_URL || "http://localhost:3000";

// ── Token Management ────────────────────────────────────────

let refreshTimer = null;

function getTokens() {
  return {
    idToken: localStorage.getItem("sotto_id_token"),
    refreshToken: localStorage.getItem("sotto_refresh_token"),
    accessToken: localStorage.getItem("sotto_access_token"),
  };
}

export function setTokens({ idToken, refreshToken, accessToken, expiresIn }) {
  localStorage.setItem("sotto_id_token", idToken);
  if (refreshToken) localStorage.setItem("sotto_refresh_token", refreshToken);
  if (accessToken) localStorage.setItem("sotto_access_token", accessToken);
  scheduleRefresh(expiresIn || 3600);
}

export function clearTokens() {
  localStorage.removeItem("sotto_id_token");
  localStorage.removeItem("sotto_refresh_token");
  localStorage.removeItem("sotto_access_token");
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = null;
}

export function isAuthenticated() {
  return !!localStorage.getItem("sotto_id_token");
}

function parseJwt(token) {
  try {
    return JSON.parse(atob(token.split(".")[1]));
  } catch {
    return null;
  }
}

export function getTenantId() {
  const token = localStorage.getItem("sotto_id_token");
  if (!token) return null;
  const claims = parseJwt(token);
  return claims?.["custom:tenant_id"] || null;
}

// Schedule token refresh 5 minutes before expiry
function scheduleRefresh(expiresInSec) {
  if (refreshTimer) clearTimeout(refreshTimer);
  const refreshInMs = Math.max((expiresInSec - 300) * 1000, 60_000);
  refreshTimer = setTimeout(refreshTokens, refreshInMs);
}

async function refreshTokens() {
  const { refreshToken } = getTokens();
  if (!refreshToken) {
    clearTokens();
    window.location.href = "/login";
    return;
  }
  // Cognito OAuth2 token refresh — requires UserPoolClientId
  // This would call the Cognito token endpoint; for now, redirect to login on expiry
  clearTokens();
  window.location.href = "/login";
}

// ── HTTP Client ─────────────────────────────────────────────

async function request(method, path, { body, retry = true } = {}) {
  const { idToken } = getTokens();
  const headers = { "Content-Type": "application/json" };
  if (idToken) headers["Authorization"] = `Bearer ${idToken}`;

  const opts = { method, headers };
  if (body !== undefined) opts.body = JSON.stringify(body);

  const res = await fetch(`${API_URL}${path}`, opts);

  if (res.status === 401 && retry) {
    await refreshTokens();
    return request(method, path, { body, retry: false });
  }

  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = new Error(data?.error || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}

// ── API Methods ─────────────────────────────────────────────

// Auth (no token required)
export const signup = (body) => request("POST", "/admin/signup", { body });

// Tenant
export const getTenant = () => request("GET", "/admin/tenant");
export const updateTenant = (body) => request("PUT", "/admin/tenant", { body });

// Agents
export const listAgents = () => request("GET", "/admin/agents");
export const inviteAgent = (body) =>
  request("POST", "/admin/agents/invite", { body });

// Number Mappings
export const listNumbers = () => request("GET", "/admin/numbers");
export const createNumber = (body) =>
  request("POST", "/admin/numbers", { body });
export const updateNumber = (identifier, body) =>
  request("PUT", `/admin/numbers/${encodeURIComponent(identifier)}`, { body });
export const deleteNumber = (identifier) =>
  request("DELETE", `/admin/numbers/${encodeURIComponent(identifier)}`);

// Calls (admin view)
export const listCalls = () => request("GET", "/admin/calls");
export const getCall = (callId) => request("GET", `/calls/${callId}`);
export const updateNotes = (callId, notes) =>
  request("PUT", `/calls/${callId}/notes`, { body: { notes } });

// Recording presigned URL (backend endpoint TBD)
export const getRecordingUrl = (callId) =>
  request("GET", `/calls/${callId}/recording-url`);
