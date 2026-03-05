// Sotto Cockpit — Side Panel
// UI state machine: idle -> ringing -> active -> ended -> transcript_ready -> complete

const STATES = {
  LOGIN: "login",
  IDLE: "idle",
  RINGING: "ringing",
  ACTIVE: "active",
  ENDED: "ended",
  TRANSCRIPT_READY: "transcript_ready",
  COMPLETE: "complete",
};

let currentState = STATES.LOGIN;
let currentCallId = null;
let ringTimeout = null;
let activeTimeout = null;
let callTimerInterval = null;
let callTimerStart = null;

// =========================================================
// Init
// =========================================================

document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();

  const tokens = await Auth.getTokens();
  if (tokens.idToken && !Auth.isExpired(tokens.idToken)) {
    // Authenticated — restore state from storage
    const stored = await chrome.runtime.sendMessage({ type: "get_state" });
    currentCallId = stored.currentCallId || null;
    updateConnectionIndicator(stored.wsStatus || "connecting");

    const mapped = mapStoredState(stored.callState);
    if (mapped && currentCallId) {
      setState(mapped, stored);
    } else {
      setState(STATES.IDLE);
    }
  } else {
    setState(STATES.LOGIN);
  }
});

function mapStoredState(callState) {
  return (
    {
      ringing: STATES.RINGING,
      active: STATES.ACTIVE,
      ended: STATES.ENDED,
      transcript_ready: STATES.TRANSCRIPT_READY,
      complete: STATES.COMPLETE,
      failed: STATES.ENDED,
    }[callState] || null
  );
}

// =========================================================
// Event binding
// =========================================================

function bindEvents() {
  document.getElementById("login-form").addEventListener("submit", handleLogin);
  document.getElementById("logout-btn").addEventListener("click", handleLogout);
  document
    .getElementById("save-notes-btn")
    .addEventListener("click", handleSaveNotes);
  document
    .getElementById("dismiss-btn")
    .addEventListener("click", handleDismiss);

  chrome.runtime.onMessage.addListener(handleMessage);
}

// =========================================================
// Auth
// =========================================================

async function handleLogin(e) {
  e.preventDefault();
  const email = document.getElementById("email").value.trim();
  const password = document.getElementById("password").value;
  const errorEl = document.getElementById("login-error");
  const btn = document.getElementById("login-submit");

  errorEl.textContent = "";
  btn.disabled = true;
  btn.textContent = "Signing in...";

  try {
    // Use the Cognito SDK (loaded via vendor script tag) for SRP auth
    const userPool = new AmazonCognitoIdentity.CognitoUserPool({
      UserPoolId: Auth.CONFIG.userPoolId,
      ClientId: Auth.CONFIG.clientId,
    });

    const cognitoUser = new AmazonCognitoIdentity.CognitoUser({
      Username: email,
      Pool: userPool,
    });

    const authDetails = new AmazonCognitoIdentity.AuthenticationDetails({
      Username: email,
      Password: password,
    });

    const session = await new Promise((resolve, reject) => {
      cognitoUser.authenticateUser(authDetails, {
        onSuccess: resolve,
        onFailure: reject,
      });
    });

    const idToken = session.getIdToken().getJwtToken();
    const accessToken = session.getAccessToken().getJwtToken();
    const refreshToken = session.getRefreshToken().getToken();

    await Auth.storeTokens({ idToken, accessToken, refreshToken });

    // Extract user info from JWT claims
    const claims = Auth.decodeToken(idToken);
    await Auth.storeUserInfo({
      agentId: claims["custom:agent_id"] || "",
      tenantId: claims["custom:tenant_id"] || "",
      email: claims.email || email,
    });

    // Tell service worker to open WebSocket
    chrome.runtime.sendMessage({ type: "login_success" });

    setState(STATES.IDLE);
  } catch (err) {
    errorEl.textContent = err.message || "Login failed";
  } finally {
    btn.disabled = false;
    btn.textContent = "Sign In";
  }
}

async function handleLogout() {
  chrome.runtime.sendMessage({ type: "logout" });
  await Auth.clearTokens();
  await Auth.clearUserInfo();
  currentCallId = null;
  clearTimers();
  setState(STATES.LOGIN);
}

// =========================================================
// State machine
// =========================================================

function setState(state, extra) {
  currentState = state;
  clearTimers();

  // Hide all panels
  document.querySelectorAll(".state-panel").forEach((el) => {
    el.classList.add("hidden");
  });

  // Header visible when authenticated
  document
    .getElementById("header")
    .classList.toggle("hidden", state === STATES.LOGIN);

  // Show the correct panel
  if (
    state === STATES.TRANSCRIPT_READY ||
    state === STATES.COMPLETE
  ) {
    document.getElementById("panel-detail").classList.remove("hidden");
  } else {
    const panelId =
      state === STATES.LOGIN
        ? "panel-login"
        : `panel-${state}`;
    const panel = document.getElementById(panelId);
    if (panel) panel.classList.remove("hidden");
  }

  // State-specific logic
  switch (state) {
    case STATES.RINGING:
      onRinging();
      break;
    case STATES.ACTIVE:
      onActive();
      break;
    case STATES.ENDED:
      onEnded(extra);
      break;
    case STATES.TRANSCRIPT_READY:
      onTranscriptReady();
      break;
    case STATES.COMPLETE:
      onComplete(extra);
      break;
  }
}

// =========================================================
// State handlers
// =========================================================

function onRinging() {
  // Auto-transition: ringing (3s) -> active (2s) -> ended
  ringTimeout = setTimeout(() => {
    chrome.storage.session.set({ callState: "active" });
    setState(STATES.ACTIVE);
  }, 3000);
}

function onActive() {
  // Show a brief processing timer
  callTimerStart = Date.now();
  const timerEl = document.getElementById("call-timer");
  callTimerInterval = setInterval(() => {
    const secs = Math.floor((Date.now() - callTimerStart) / 1000);
    const m = String(Math.floor(secs / 60)).padStart(2, "0");
    const s = String(secs % 60).padStart(2, "0");
    timerEl.textContent = `${m}:${s}`;
  }, 1000);

  activeTimeout = setTimeout(() => {
    chrome.storage.session.set({ callState: "ended" });
    setState(STATES.ENDED);
  }, 2000);
}

function onEnded(extra) {
  const statusEl = document.getElementById("ended-status");
  if (extra && extra.callState === "failed") {
    statusEl.textContent = "Transcription failed";
  } else {
    statusEl.textContent = "Transcribing...";
  }
}

async function onTranscriptReady() {
  document.getElementById("summary-section").classList.add("hidden");
  document.getElementById("action-items-section").classList.add("hidden");
  await loadCallDetail();
}

async function onComplete(extra) {
  document.getElementById("summary-section").classList.remove("hidden");
  document.getElementById("action-items-section").classList.remove("hidden");
  await loadCallDetail();

  // If summary came from the WebSocket event (stored in session), use it as fallback
  if (extra && extra.lastSummary) {
    const summaryEl = document.getElementById("summary-text");
    if (!summaryEl.textContent) {
      summaryEl.textContent = extra.lastSummary;
    }
  }
  if (extra && extra.lastActionItems && extra.lastActionItems.length) {
    const listEl = document.getElementById("action-items-list");
    if (!listEl.children.length) {
      renderActionItems(extra.lastActionItems);
    }
  }
}

// =========================================================
// API calls
// =========================================================

async function loadCallDetail() {
  if (!currentCallId) return;

  try {
    const call = await Api.getCallDetail(currentCallId);
    renderCallDetail(call);
  } catch (err) {
    console.error("Failed to load call detail:", err);
  }
}

function renderCallDetail(call) {
  document.getElementById("detail-from").textContent =
    call.from_number || "Unknown";
  document.getElementById("detail-direction").textContent =
    call.direction || "--";
  document.getElementById("detail-duration").textContent = formatDuration(
    call.duration_sec
  );

  // Transcript
  const transcriptEl = document.getElementById("transcript-content");
  transcriptEl.innerHTML = "";
  if (call.transcript && call.transcript.segments) {
    for (const seg of call.transcript.segments) {
      const line = document.createElement("div");
      line.className = `transcript-line speaker-${seg.speaker.toLowerCase()}`;
      line.innerHTML = `<span class="speaker">${escapeHtml(seg.speaker)}:</span> ${escapeHtml(seg.text)}`;
      transcriptEl.appendChild(line);
    }
  }

  // Summary
  if (call.summary) {
    document.getElementById("summary-text").textContent = call.summary;
  }
  if (call.action_items) {
    const items =
      typeof call.action_items === "string"
        ? JSON.parse(call.action_items)
        : call.action_items;
    renderActionItems(items);
  }

  // Notes
  if (call.notes) {
    document.getElementById("notes-textarea").value = call.notes;
  }
}

function renderActionItems(items) {
  const listEl = document.getElementById("action-items-list");
  listEl.innerHTML = "";
  for (const item of items) {
    const li = document.createElement("li");
    li.textContent = item;
    listEl.appendChild(li);
  }
}

// =========================================================
// Notes
// =========================================================

async function handleSaveNotes() {
  if (!currentCallId) return;

  const btn = document.getElementById("save-notes-btn");
  const notes = document.getElementById("notes-textarea").value;

  btn.disabled = true;
  btn.textContent = "Saving...";

  try {
    await Api.updateNotes(currentCallId, notes);
    btn.textContent = "Saved!";
    setTimeout(() => {
      btn.textContent = "Save Notes";
      btn.disabled = false;
    }, 2000);
  } catch {
    btn.textContent = "Save Failed";
    setTimeout(() => {
      btn.textContent = "Save Notes";
      btn.disabled = false;
    }, 2000);
  }
}

// =========================================================
// Dismiss / new call
// =========================================================

function handleDismiss() {
  currentCallId = null;
  chrome.storage.session.remove([
    "currentCallId",
    "currentTenantId",
    "callState",
    "lastSummary",
    "lastActionItems",
  ]);
  document.getElementById("notes-textarea").value = "";
  document.getElementById("transcript-content").innerHTML = "";
  document.getElementById("summary-text").textContent = "";
  document.getElementById("action-items-list").innerHTML = "";
  setState(STATES.IDLE);
}

// =========================================================
// Messages from service worker
// =========================================================

function handleMessage(message) {
  switch (message.type) {
    case "call_recorded":
      currentCallId = message.callId;
      setState(STATES.RINGING);
      break;

    case "transcript_ready":
      setState(STATES.TRANSCRIPT_READY);
      break;

    case "transcription_failed":
      document.getElementById("ended-status").textContent =
        "Transcription failed";
      break;

    case "summary_ready":
      setState(STATES.COMPLETE, {
        lastSummary: message.summary,
        lastActionItems: message.actionItems,
      });
      break;

    case "connection_status":
      updateConnectionIndicator(message.status);
      break;

    case "auth_expired":
      setState(STATES.LOGIN);
      break;

    case "epic_client_update":
      updateEpicClient(message.data);
      break;
  }
}

// =========================================================
// UI helpers
// =========================================================

function updateConnectionIndicator(status) {
  const dot = document.getElementById("connection-status");
  if (!dot) return;
  dot.className = `status-dot ${status}`;
  dot.title =
    {
      connected: "Connected",
      disconnected: "Disconnected",
      error: "Connection error",
      auth_error: "Authentication expired",
    }[status] || "Connecting...";
}

function updateEpicClient(data) {
  if (!data || !data.clientName) return;
  const nameEl = document.getElementById("epic-client-name");
  if (nameEl) {
    nameEl.textContent = data.clientName;
    document.getElementById("epic-client-match").classList.remove("hidden");
  }
}

function clearTimers() {
  if (ringTimeout) {
    clearTimeout(ringTimeout);
    ringTimeout = null;
  }
  if (activeTimeout) {
    clearTimeout(activeTimeout);
    activeTimeout = null;
  }
  if (callTimerInterval) {
    clearInterval(callTimerInterval);
    callTimerInterval = null;
  }
}

function formatDuration(seconds) {
  if (!seconds && seconds !== 0) return "--:--";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function escapeHtml(text) {
  const el = document.createElement("span");
  el.textContent = text;
  return el.innerHTML;
}
