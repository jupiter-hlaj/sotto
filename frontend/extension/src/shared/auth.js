// Sotto Cockpit — Auth Module
// Token storage (chrome.storage.session), JWT parsing, and Cognito token refresh.
// Login is handled in sidepanel.js using the Cognito SDK (requires window context).

const Auth = (() => {
  // Configure these after deployment — values from SAM stack outputs
  const CONFIG = {
    region: "us-east-1",
    userPoolId: "us-east-1_wwLPR7DXg",
    clientId: "44nd8kjrg6hvnr1hgp132n4u4t",
  };

  const COGNITO_ENDPOINT = `https://cognito-idp.${CONFIG.region}.amazonaws.com/`;

  // ---------- JWT helpers ----------

  function decodeToken(jwt) {
    try {
      const payload = jwt.split(".")[1];
      const decoded = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
      return JSON.parse(decoded);
    } catch {
      return null;
    }
  }

  function isExpired(jwt) {
    const claims = decodeToken(jwt);
    if (!claims || !claims.exp) return true;
    return Date.now() >= claims.exp * 1000;
  }

  function getExpiryMs(jwt) {
    const claims = decodeToken(jwt);
    if (!claims || !claims.exp) return 0;
    return claims.exp * 1000 - Date.now();
  }

  // ---------- Token storage (chrome.storage.session — cleared on browser close) ----------

  async function storeTokens({ idToken, accessToken, refreshToken }) {
    await chrome.storage.session.set({ idToken, accessToken, refreshToken });
  }

  async function getTokens() {
    return chrome.storage.session.get(["idToken", "accessToken", "refreshToken"]);
  }

  async function clearTokens() {
    await chrome.storage.session.remove([
      "idToken",
      "accessToken",
      "refreshToken",
    ]);
  }

  // ---------- User info (chrome.storage.local — persists across sessions) ----------

  async function storeUserInfo({ agentId, tenantId, email }) {
    await chrome.storage.local.set({ agentId, tenantId, email });
  }

  async function getUserInfo() {
    return chrome.storage.local.get(["agentId", "tenantId", "email"]);
  }

  async function clearUserInfo() {
    await chrome.storage.local.remove(["agentId", "tenantId", "email"]);
  }

  // ---------- Token refresh (Cognito REST API — works in service worker) ----------

  async function refreshTokens() {
    const { refreshToken } = await getTokens();
    if (!refreshToken) throw new Error("No refresh token available");

    const response = await fetch(COGNITO_ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target":
          "AWSCognitoIdentityProviderService.InitiateAuth",
      },
      body: JSON.stringify({
        AuthFlow: "REFRESH_TOKEN_AUTH",
        ClientId: CONFIG.clientId,
        AuthParameters: {
          REFRESH_TOKEN: refreshToken,
        },
      }),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.__type || "Token refresh failed");
    }

    const data = await response.json();
    const result = data.AuthenticationResult;

    // Cognito does not return a new refresh token — reuse the existing one
    await storeTokens({
      idToken: result.IdToken,
      accessToken: result.AccessToken,
      refreshToken: refreshToken,
    });

    return result.IdToken;
  }

  return {
    CONFIG,
    decodeToken,
    isExpired,
    getExpiryMs,
    storeTokens,
    getTokens,
    clearTokens,
    storeUserInfo,
    getUserInfo,
    clearUserInfo,
    refreshTokens,
  };
})();

// Expose for importScripts in service worker
if (typeof self !== "undefined" && typeof window === "undefined") {
  self.Auth = Auth;
}
