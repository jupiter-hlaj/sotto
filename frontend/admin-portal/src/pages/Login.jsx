import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { setTokens } from "../services/api";

const COGNITO_DOMAIN = import.meta.env.VITE_COGNITO_DOMAIN || "";
const CLIENT_ID = import.meta.env.VITE_USER_POOL_CLIENT_ID || "";
const API_URL = import.meta.env.VITE_API_URL || "http://localhost:3000";

export default function Login() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      // Cognito InitiateAuth via the hosted UI or SRP flow
      // For MVP: direct Cognito API call using USER_PASSWORD_AUTH
      const res = await fetch(
        `https://cognito-idp.${import.meta.env.VITE_AWS_REGION || "ca-central-1"}.amazonaws.com/`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
          },
          body: JSON.stringify({
            AuthFlow: "USER_PASSWORD_AUTH",
            ClientId: CLIENT_ID,
            AuthParameters: { USERNAME: email, PASSWORD: password },
          }),
        }
      );

      const data = await res.json();

      if (data.ChallengeName === "NEW_PASSWORD_REQUIRED") {
        setError("Password change required. Please contact your administrator.");
        setLoading(false);
        return;
      }

      if (data.__type || !data.AuthenticationResult) {
        setError(data.message || "Login failed");
        setLoading(false);
        return;
      }

      const { IdToken, RefreshToken, AccessToken, ExpiresIn } = data.AuthenticationResult;
      setTokens({
        idToken: IdToken,
        refreshToken: RefreshToken,
        accessToken: AccessToken,
        expiresIn: ExpiresIn,
      });
      navigate("/dashboard");
    } catch (err) {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="w-full max-w-sm">
        <h1 className="text-2xl font-bold text-center mb-6">Sotto</h1>
        <form onSubmit={handleSubmit} className="bg-white shadow rounded-lg p-6 space-y-4">
          <h2 className="text-lg font-semibold">Sign in</h2>
          {error && <p className="text-red-600 text-sm">{error}</p>}
          <div>
            <label className="block text-sm font-medium mb-1">Email</label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Password</label>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="w-full bg-indigo-600 text-white py-2 rounded text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
          >
            {loading ? "Signing in..." : "Sign in"}
          </button>
          <p className="text-center text-sm text-gray-500">
            New agency? <Link to="/signup" className="text-indigo-600 hover:underline">Sign up</Link>
          </p>
        </form>
      </div>
    </div>
  );
}
