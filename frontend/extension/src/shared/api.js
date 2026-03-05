// Sotto Cockpit — API Client
// REST client for the Sotto Agent API (call history, call detail, notes).

const Api = (() => {
  // Configure after deployment — sotto-{env}-HttpApiUrl SAM output
  const BASE_URL = ""; // e.g. https://xxx.execute-api.ca-central-1.amazonaws.com/dev

  async function getAuthHeader() {
    const { idToken } = await Auth.getTokens();
    if (!idToken) throw new Error("Not authenticated");

    if (Auth.isExpired(idToken)) {
      const newToken = await Auth.refreshTokens();
      return `Bearer ${newToken}`;
    }
    return `Bearer ${idToken}`;
  }

  async function request(method, path, body) {
    const headers = {
      Authorization: await getAuthHeader(),
      "Content-Type": "application/json",
    };

    const options = { method, headers };
    if (body !== undefined) {
      options.body = JSON.stringify(body);
    }

    const response = await fetch(`${BASE_URL}${path}`, options);

    // Token may have expired between getAuthHeader and the request arriving
    if (response.status === 401) {
      headers.Authorization = `Bearer ${await Auth.refreshTokens()}`;
      const retry = await fetch(`${BASE_URL}${path}`, { ...options, headers });
      if (!retry.ok) throw new Error(`API ${retry.status}`);
      return retry.json();
    }

    if (!response.ok) throw new Error(`API ${response.status}`);
    return response.json();
  }

  return {
    getCallHistory() {
      return request("GET", "/calls");
    },

    getCallDetail(callId) {
      return request("GET", `/calls/${encodeURIComponent(callId)}`);
    },

    updateNotes(callId, notes) {
      return request("PUT", `/calls/${encodeURIComponent(callId)}/notes`, {
        notes,
      });
    },
  };
})();

if (typeof self !== "undefined" && typeof window === "undefined") {
  self.Api = Api;
}
