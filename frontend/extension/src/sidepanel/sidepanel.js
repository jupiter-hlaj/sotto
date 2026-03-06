// Sotto — Side Panel
// Views: login -> main (call list) <-> detail

// ===== State =====
let currentView = "login";
let viewingCallId = null;
let liveCallId = null;
let liveCallState = null;
let calls = [];
let refreshInterval = null;
let pendingCognitoUser = null;

// ===== Init =====
document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();

  const tokens = await Auth.getTokens();
  if (tokens.idToken && !Auth.isExpired(tokens.idToken)) {
    showView("main");
    restoreFromStorage();
  }
});

async function restoreFromStorage() {
  try {
    const stored = await chrome.runtime.sendMessage({ type: "get_state" });
    updateWsIndicator(stored.wsStatus || "connecting");

    if (stored.currentCallId && stored.callState) {
      liveCallId = stored.currentCallId;
      liveCallState = stored.callState;
      updateLiveBanner();
    }
  } catch {
    // Service worker may not be ready yet
  }
}

// ===== Event Binding =====
function bindEvents() {
  document.getElementById("login-form").addEventListener("submit", handleLogin);
  document.getElementById("logout-btn").addEventListener("click", handleLogout);
  document.getElementById("back-btn").addEventListener("click", () => showView("main"));
  document.getElementById("save-notes-btn").addEventListener("click", handleSaveNotes);
  document.getElementById("live-view-btn").addEventListener("click", () => {
    if (liveCallId) showDetail(liveCallId);
  });
  document.getElementById("transcript-toggle").addEventListener("click", toggleTranscript);

  chrome.runtime.onMessage.addListener(handleMessage);
}

// ===== View Management =====
function showView(view) {
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  document.getElementById(`view-${view}`).classList.add("active");
  currentView = view;

  if (view === "main") {
    fetchCallHistory();
    startAutoRefresh();
  } else {
    stopAutoRefresh();
  }
}

function showDetail(callId) {
  viewingCallId = callId;

  // Clear previous detail content
  document.getElementById("detail-number").textContent = "--";
  document.getElementById("detail-direction").textContent = "--";
  document.getElementById("detail-duration").textContent = "--";
  document.getElementById("detail-date").textContent = "--";
  document.getElementById("detail-summary").textContent = "";
  document.getElementById("detail-actions").innerHTML = "";
  document.getElementById("detail-transcript").innerHTML =
    '<p class="placeholder-text">Loading...</p>';
  document.getElementById("detail-notes").value = "";
  document.getElementById("section-summary").classList.add("hidden");
  document.getElementById("section-actions").classList.add("hidden");
  document.getElementById("detail-status").textContent = "";
  document.getElementById("detail-status").className = "status-pill";

  // Reset transcript collapse
  document.getElementById("detail-transcript").classList.remove("collapsed");
  document.getElementById("transcript-toggle").textContent = "Collapse";

  showView("detail");
  fetchCallDetail(callId);
}

// ===== Data Fetching =====
async function fetchCallHistory() {
  try {
    const data = await Api.getCallHistory();
    calls = data.calls || data || [];
    renderCallList();
  } catch (err) {
    console.error("Failed to fetch call history:", err);
  }
}

async function fetchCallDetail(callId) {
  try {
    const call = await Api.getCallDetail(callId);
    renderCallDetail(call);
  } catch (err) {
    console.error("Failed to fetch call detail:", err);
    document.getElementById("detail-transcript").innerHTML =
      '<p class="error-text">Failed to load call details</p>';
  }
}

function startAutoRefresh() {
  stopAutoRefresh();
  refreshInterval = setInterval(fetchCallHistory, 10000);
}

function stopAutoRefresh() {
  if (refreshInterval) {
    clearInterval(refreshInterval);
    refreshInterval = null;
  }
}

// ===== Rendering: Call List =====
function renderCallList() {
  const list = document.getElementById("call-list");
  const empty = document.getElementById("empty-state");

  if (!calls.length) {
    list.innerHTML = "";
    empty.classList.remove("hidden");
    return;
  }

  empty.classList.add("hidden");
  list.innerHTML = calls
    .map(
      (call) => `
    <button class="call-item" data-id="${esc(call.call_id)}">
      <div class="call-item-top">
        <span class="call-from">${esc(formatPhone(call.from_number))}</span>
        <span class="call-time">${esc(relativeTime(call.created_at))}</span>
      </div>
      <div class="call-item-bottom">
        <span class="call-meta">${esc(call.direction || "unknown")} &middot; ${esc(formatDuration(call.duration_sec))}</span>
        <span class="status-pill status-${esc(call.status)}">${esc(call.status)}</span>
      </div>
    </button>
  `
    )
    .join("");

  list.querySelectorAll(".call-item").forEach((item) => {
    item.addEventListener("click", () => showDetail(item.dataset.id));
  });
}

// ===== Rendering: Call Detail =====
function renderCallDetail(call) {
  // Hero
  document.getElementById("detail-number").textContent = formatPhone(call.from_number);
  document.getElementById("detail-direction").textContent = call.direction || "--";
  document.getElementById("detail-duration").textContent = formatDuration(call.duration_sec);
  document.getElementById("detail-date").textContent = call.created_at
    ? new Date(call.created_at).toLocaleString()
    : "--";

  // Status badge
  const badge = document.getElementById("detail-status");
  badge.textContent = call.status;
  badge.className = `status-pill status-${call.status}`;

  // Summary
  const summarySection = document.getElementById("section-summary");
  if (call.summary) {
    document.getElementById("detail-summary").textContent = call.summary;
    summarySection.classList.remove("hidden");
  } else {
    summarySection.classList.add("hidden");
  }

  // Action Items
  const actionsSection = document.getElementById("section-actions");
  const actionsList = document.getElementById("detail-actions");
  actionsList.innerHTML = "";

  if (call.action_items) {
    let items = call.action_items;
    if (typeof items === "string") {
      try {
        items = JSON.parse(items);
      } catch {
        items = [items];
      }
    }
    if (Array.isArray(items) && items.length) {
      actionsList.innerHTML = items.map((item) => `<li>${esc(item)}</li>`).join("");
      actionsSection.classList.remove("hidden");
    } else {
      actionsSection.classList.add("hidden");
    }
  } else {
    actionsSection.classList.add("hidden");
  }

  // Transcript
  renderTranscript(call);

  // Notes
  document.getElementById("detail-notes").value = call.notes || "";
}

function renderTranscript(call) {
  const container = document.getElementById("detail-transcript");
  container.innerHTML = "";

  if (call.transcript_error) {
    container.innerHTML = `<p class="error-text">${esc(call.transcript_error)}</p>`;
    return;
  }

  if (call.transcript && call.transcript.segments && call.transcript.segments.length) {
    call.transcript.segments.forEach((seg) => {
      const div = document.createElement("div");
      const num = seg.speaker.replace("Speaker ", "");
      div.className = `transcript-seg speaker-${num}`;
      div.innerHTML = `<span class="seg-speaker">${esc(seg.speaker)}</span><p class="seg-text">${esc(seg.text)}</p>`;
      container.appendChild(div);
    });
    return;
  }

  // No transcript yet — show contextual message
  if (call.status === "transcribing" || call.transcript_status === "in_progress") {
    container.innerHTML = '<p class="placeholder-text">Transcribing...</p>';
  } else if (call.transcript_status === "pending") {
    container.innerHTML = '<p class="placeholder-text">Waiting for transcription...</p>';
  } else {
    container.innerHTML = '<p class="placeholder-text">No transcript available</p>';
  }
}

// ===== Live Banner =====
function updateLiveBanner() {
  const banner = document.getElementById("live-banner");
  const statusEl = document.getElementById("live-status");

  if (!liveCallId) {
    banner.classList.add("hidden");
    return;
  }

  banner.classList.remove("hidden");

  const labels = {
    ringing: "New call detected",
    active: "Processing recording...",
    transcribing: "Transcribing call...",
    transcript_ready: "Generating summary...",
    summarizing: "Generating summary...",
    complete: "Call complete",
    failed: "Processing failed",
  };

  statusEl.textContent = labels[liveCallState] || "Processing...";
}

// ===== WebSocket Messages =====
function handleMessage(message) {
  switch (message.type) {
    case "call_recorded":
      liveCallId = message.callId;
      liveCallState = "transcribing";
      updateLiveBanner();
      if (currentView === "main") fetchCallHistory();
      break;

    case "transcript_ready":
      if (message.callId === liveCallId) liveCallState = "transcript_ready";
      updateLiveBanner();
      if (currentView === "main") fetchCallHistory();
      if (currentView === "detail" && viewingCallId === message.callId) {
        fetchCallDetail(message.callId);
      }
      break;

    case "transcription_failed":
      if (message.callId === liveCallId) liveCallState = "failed";
      updateLiveBanner();
      if (currentView === "main") fetchCallHistory();
      if (currentView === "detail" && viewingCallId === message.callId) {
        fetchCallDetail(message.callId);
      }
      break;

    case "summary_ready":
      if (message.callId === liveCallId) liveCallState = "complete";
      updateLiveBanner();

      // Auto-dismiss live banner after 5 seconds
      setTimeout(() => {
        if (liveCallState === "complete") {
          liveCallId = null;
          liveCallState = null;
          updateLiveBanner();
        }
      }, 5000);

      if (currentView === "main") fetchCallHistory();
      if (currentView === "detail" && viewingCallId === message.callId) {
        fetchCallDetail(message.callId);
      }
      break;

    case "connection_status":
      updateWsIndicator(message.status);
      break;

    case "auth_expired":
      showView("login");
      break;
  }
}

// ===== Auth =====
async function handleLogin(e) {
  e.preventDefault();
  const email = document.getElementById("email").value.trim();
  const password = document.getElementById("password").value;
  const errorEl = document.getElementById("login-error");
  const btn = document.getElementById("login-btn");

  errorEl.textContent = "";
  btn.disabled = true;
  btn.textContent = "Signing in...";

  try {
    let session;

    if (pendingCognitoUser) {
      // Completing a newPasswordRequired challenge
      const newPassword = document.getElementById("new-password").value;
      if (!newPassword) {
        errorEl.textContent = "Please enter a new password.";
        btn.disabled = false;
        btn.textContent = "Set Password";
        return;
      }
      session = await new Promise((resolve, reject) => {
        pendingCognitoUser.completeNewPasswordChallenge(newPassword, {}, {
          onSuccess: resolve,
          onFailure: reject,
        });
      });
      pendingCognitoUser = null;
      document.getElementById("new-password-group").classList.add("hidden");
      document.getElementById("new-password").removeAttribute("required");
    } else {
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

      session = await new Promise((resolve, reject) => {
        cognitoUser.authenticateUser(authDetails, {
          onSuccess: resolve,
          onFailure: reject,
          newPasswordRequired: () => {
            pendingCognitoUser = cognitoUser;
            document.getElementById("new-password-group").classList.remove("hidden");
            document.getElementById("new-password").setAttribute("required", "");
            errorEl.textContent = "A new password is required.";
            btn.disabled = false;
            btn.textContent = "Set Password";
          },
        });
      });
    }

    const idToken = session.getIdToken().getJwtToken();
    const accessToken = session.getAccessToken().getJwtToken();
    const refreshToken = session.getRefreshToken().getToken();

    await Auth.storeTokens({ idToken, accessToken, refreshToken });

    const claims = Auth.decodeToken(idToken);
    await Auth.storeUserInfo({
      agentId: claims["custom:agent_id"] || "",
      tenantId: claims["custom:tenant_id"] || "",
      email: claims.email || email,
    });

    chrome.runtime.sendMessage({ type: "login_success" });
    showView("main");
  } catch (err) {
    errorEl.textContent = err.message || "Login failed";
  } finally {
    btn.disabled = false;
    if (!pendingCognitoUser) btn.textContent = "Sign In";
  }
}

async function handleLogout() {
  chrome.runtime.sendMessage({ type: "logout" });
  await Auth.clearTokens();
  await Auth.clearUserInfo();
  liveCallId = null;
  liveCallState = null;
  calls = [];
  viewingCallId = null;
  showView("login");
}

// ===== Notes =====
async function handleSaveNotes() {
  if (!viewingCallId) return;
  const btn = document.getElementById("save-notes-btn");
  const notes = document.getElementById("detail-notes").value;

  btn.disabled = true;
  btn.textContent = "Saving...";

  try {
    await Api.updateNotes(viewingCallId, notes);
    btn.textContent = "Saved!";
    setTimeout(() => {
      btn.textContent = "Save Notes";
      btn.disabled = false;
    }, 2000);
  } catch {
    btn.textContent = "Failed";
    setTimeout(() => {
      btn.textContent = "Save Notes";
      btn.disabled = false;
    }, 2000);
  }
}

// ===== Transcript Toggle =====
function toggleTranscript() {
  const container = document.getElementById("detail-transcript");
  const btn = document.getElementById("transcript-toggle");
  container.classList.toggle("collapsed");
  btn.textContent = container.classList.contains("collapsed") ? "Expand" : "Collapse";
}

// ===== UI Helpers =====
function updateWsIndicator(status) {
  const dot = document.getElementById("ws-status");
  if (!dot) return;
  dot.className = `ws-dot ws-${status}`;
  dot.title =
    {
      connected: "Connected",
      disconnected: "Disconnected",
      error: "Connection error",
      auth_error: "Authentication expired",
    }[status] || "Connecting...";
}

function formatPhone(number) {
  if (!number) return "Unknown";
  const match = number.match(/^\+1(\d{3})(\d{3})(\d{4})$/);
  if (match) return `(${match[1]}) ${match[2]}-${match[3]}`;
  return number;
}

function formatDuration(sec) {
  if (!sec && sec !== 0) return "--";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function relativeTime(iso) {
  if (!iso) return "--";
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now - d;
  const min = Math.floor(diffMs / 60000);
  const hr = Math.floor(diffMs / 3600000);
  const day = Math.floor(diffMs / 86400000);

  if (min < 1) return "Just now";
  if (min < 60) return `${min}m ago`;
  if (hr < 24) return `${hr}h ago`;
  if (day < 7) return `${day}d ago`;
  return d.toLocaleDateString();
}

function esc(str) {
  if (str === null || str === undefined) return "";
  const el = document.createElement("span");
  el.textContent = String(str);
  return el.innerHTML;
}
