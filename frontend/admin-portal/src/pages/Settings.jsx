import { useEffect, useState } from "react";
import { getTenant, updateTenant } from "../services/api";

const PROVIDERS = ["twilio", "ringcentral", "zoom", "teams", "8x8"];

export default function Settings() {
  const [tenant, setTenant] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [form, setForm] = useState({
    agency_name: "",
    provider_type: "twilio",
    twilio_account_sid: "",
    twilio_phone_number: "",
    twilio_auth_token: "",
  });

  useEffect(() => {
    async function load() {
      try {
        const data = await getTenant();
        setTenant(data);
        setForm({
          agency_name: data.agency_name || "",
          provider_type: data.provider_type || "twilio",
          twilio_account_sid: data.twilio_account_sid || "",
          twilio_phone_number: data.twilio_phone_number || "",
          twilio_auth_token: "",
        });
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  function update(field) {
    return (e) => setForm((f) => ({ ...f, [field]: e.target.value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setMessage("");
    setSaving(true);

    // Only send changed fields
    const body = {};
    if (form.agency_name !== (tenant?.agency_name || "")) body.agency_name = form.agency_name;
    if (form.provider_type !== (tenant?.provider_type || "")) body.provider_type = form.provider_type;
    if (form.twilio_account_sid) body.twilio_account_sid = form.twilio_account_sid;
    if (form.twilio_phone_number) body.twilio_phone_number = form.twilio_phone_number;
    if (form.twilio_auth_token) body.twilio_auth_token = form.twilio_auth_token;

    if (Object.keys(body).length === 0) {
      setMessage("No changes to save.");
      setSaving(false);
      return;
    }

    try {
      const updated = await updateTenant(body);
      setTenant(updated);
      setForm((f) => ({ ...f, twilio_auth_token: "" }));
      setMessage("Settings saved.");
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <p className="text-gray-500">Loading...</p>;

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-bold mb-6">Settings</h1>

      <form onSubmit={handleSubmit} autoComplete="off" className="bg-white shadow rounded-lg p-6 space-y-4">
        {error && <p className="text-red-600 text-sm">{error}</p>}
        {message && <p className="text-green-600 text-sm">{message}</p>}

        <div>
          <label className="block text-sm font-medium mb-1">Agency Name</label>
          <input
            type="text"
            value={form.agency_name}
            onChange={update("agency_name")}
            className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">Provider</label>
          <select
            value={form.provider_type}
            onChange={update("provider_type")}
            className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            {PROVIDERS.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </div>

        {form.provider_type === "twilio" && (
          <>
            <div>
              <label className="block text-sm font-medium mb-1">Twilio Account SID</label>
              <input
                type="text"
                autoComplete="off"
                value={form.twilio_account_sid}
                onChange={update("twilio_account_sid")}
                placeholder="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                className="w-full border rounded px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Twilio Phone Number</label>
              <input
                type="text"
                autoComplete="off"
                value={form.twilio_phone_number}
                onChange={update("twilio_phone_number")}
                placeholder="+15551234567"
                className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Twilio Auth Token</label>
              <input
                type="password"
                autoComplete="new-password"
                value={form.twilio_auth_token}
                onChange={update("twilio_auth_token")}
                placeholder="Enter to update (stored in Secrets Manager)"
                className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
              <p className="text-xs text-gray-400 mt-1">Leave blank to keep existing token. Stored securely in AWS Secrets Manager.</p>
            </div>
          </>
        )}

        {tenant && (
          <div className="border-t pt-4 mt-4 grid grid-cols-2 gap-4 text-sm text-gray-500">
            <div>
              <p className="text-xs uppercase text-gray-400">Plan</p>
              <p className="font-medium text-gray-700">{tenant.plan}</p>
            </div>
            <div>
              <p className="text-xs uppercase text-gray-400">Deployment Tier</p>
              <p className="font-medium text-gray-700">{tenant.deployment_tier}</p>
            </div>
            <div>
              <p className="text-xs uppercase text-gray-400">Status</p>
              <p className="font-medium text-gray-700">{tenant.status}</p>
            </div>
            <div>
              <p className="text-xs uppercase text-gray-400">Created</p>
              <p className="font-medium text-gray-700">{new Date(tenant.created_at).toLocaleDateString()}</p>
            </div>
          </div>
        )}

        <button
          type="submit"
          disabled={saving}
          className="bg-indigo-600 text-white px-4 py-2 rounded text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
        >
          {saving ? "Saving..." : "Save Settings"}
        </button>
      </form>
    </div>
  );
}
