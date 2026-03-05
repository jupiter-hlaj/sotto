// Sotto Cockpit — Service Worker (Background)
// Manages WebSocket connection, token refresh, and message routing.
// MV3: service workers can terminate when idle — all important state lives in chrome.storage.

importScripts("../shared/auth.js");

// Configure after deployment — sotto-{env}-WebSocketApiEndpoint SAM output
const WS_URL = ""; // e.g. wss://xxx.execute-api.ca-central-1.amazonaws.com/dev

// Ephemeral (OK to lose on worker restart — reconnect handles it)
let ws = null;
let reconnectAttempt = 0;
const MAX_BACKOFF_MS = 30000;

// =========================================================
// Lifecycle
// =========================================================

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});

chrome.runtime.onStartup.addListener(() => {
  tryConnect();
});

// Also try on worker script evaluation (covers restarts after idle termination)
tryConnect();

// =========================================================
// WebSocket
// =========================================================

async function tryConnect() {
  const { idToken } = await Auth.getTokens();
  if (!idToken) return;

  // Refresh if expired before connecting
  if (Auth.isExpired(idToken)) {
    try {
      const freshToken = await Auth.refreshTokens();
      connectWebSocket(freshToken);
    } catch {
      // Refresh failed — can't connect
      setConnectionStatus("auth_error");
    }
    return;
  }

  connectWebSocket(idToken);
}

function connectWebSocket(token) {
  if (ws) {
    ws.close();
    ws = null;
  }

  ws = new WebSocket(`${WS_URL}?token=${token}`);

  ws.onopen = () => {
    reconnectAttempt = 0;
    setConnectionStatus("connected");

    // Keepalive ping every 5 minutes
    chrome.alarms.create("ws-ping", { periodInMinutes: 5 });

    // Schedule token refresh before the 60-minute expiry
    scheduleTokenRefresh(token);
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleWSMessage(data);
    } catch {
      // Ignore malformed messages
    }
  };

  ws.onclose = (event) => {
    ws = null;
    chrome.alarms.clear("ws-ping");

    // 410 Gone / 4410 — stale connection, reconnect immediately with fresh token
    if (event.code === 4410 || event.code === 1008) {
      reconnectAttempt = 0;
      tryConnect();
      return;
    }

    setConnectionStatus("disconnected");
    scheduleReconnect();
  };

  ws.onerror = () => {
    // onclose fires after onerror — reconnect handled there
    setConnectionStatus("error");
  };
}

function scheduleReconnect() {
  // Exponential backoff: 1s, 2s, 4s, 8s, ... max 30s
  const delayMs = Math.min(1000 * Math.pow(2, reconnectAttempt), MAX_BACKOFF_MS);
  reconnectAttempt++;
  setTimeout(() => tryConnect(), delayMs);
}

function scheduleTokenRefresh(token) {
  const expiryMs = Auth.getExpiryMs(token);
  // Refresh 5 minutes before expiry
  const refreshMs = Math.max(expiryMs - 5 * 60 * 1000, 0);
  // chrome.alarms minimum granularity is ~1 minute
  const refreshMinutes = Math.max(refreshMs / 60000, 1);
  chrome.alarms.create("token-refresh", { delayInMinutes: refreshMinutes });
}

// =========================================================
// Alarms
// =========================================================

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === "ws-ping") {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: "ping" }));
    }
  } else if (alarm.name === "token-refresh") {
    try {
      const newToken = await Auth.refreshTokens();
      // Reconnect WebSocket with the fresh token
      connectWebSocket(newToken);
    } catch {
      // Refresh failed — tokens revoked or expired
      await Auth.clearTokens();
      setConnectionStatus("auth_error");
      broadcast({ type: "auth_expired" });
    }
  }
});

// =========================================================
// WebSocket message handler
// =========================================================

async function handleWSMessage(data) {
  if (data.action === "pong") return;

  const { event, call_id, tenant_id } = data;

  if (event === "call_recorded") {
    await chrome.storage.session.set({
      currentCallId: call_id,
      currentTenantId: tenant_id,
      callState: "ringing",
    });
    broadcast({ type: "call_recorded", callId: call_id, tenantId: tenant_id });
  } else if (event === "transcript_ready") {
    await chrome.storage.session.set({ callState: "transcript_ready" });
    broadcast({
      type: "transcript_ready",
      callId: call_id,
      tenantId: tenant_id,
    });
  } else if (event === "transcription_failed") {
    await chrome.storage.session.set({ callState: "failed" });
    broadcast({
      type: "transcription_failed",
      callId: call_id,
      tenantId: tenant_id,
    });
  } else if (event === "summary_ready") {
    await chrome.storage.session.set({
      callState: "complete",
      lastSummary: data.summary || "",
      lastActionItems: data.action_items || [],
    });
    broadcast({
      type: "summary_ready",
      callId: call_id,
      tenantId: tenant_id,
      summary: data.summary,
      actionItems: data.action_items,
    });
  }
}

// =========================================================
// Extension message passing
// =========================================================

function broadcast(message) {
  chrome.runtime.sendMessage(message).catch(() => {
    // Side panel may not be open — that's fine
  });
}

async function setConnectionStatus(status) {
  await chrome.storage.session.set({ wsStatus: status });
  broadcast({ type: "connection_status", status });
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "login_success") {
    tryConnect();
    sendResponse({ ok: true });
  } else if (message.type === "logout") {
    if (ws) {
      ws.close();
      ws = null;
    }
    chrome.alarms.clear("ws-ping");
    chrome.alarms.clear("token-refresh");
    Auth.clearTokens();
    Auth.clearUserInfo();
    chrome.storage.session.remove([
      "currentCallId",
      "currentTenantId",
      "callState",
      "wsStatus",
      "lastSummary",
      "lastActionItems",
      "epicClientData",
    ]);
    sendResponse({ ok: true });
  } else if (message.type === "get_state") {
    chrome.storage.session
      .get(["currentCallId", "callState", "wsStatus", "lastSummary", "lastActionItems"])
      .then(sendResponse);
    return true; // keep channel open for async response
  } else if (message.type === "epic_client_data") {
    chrome.storage.session.set({ epicClientData: message.data });
    broadcast({ type: "epic_client_update", data: message.data });
  }
});
