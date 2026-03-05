import { useEffect, useState } from "react";
import { listNumbers, createNumber, updateNumber, deleteNumber, listAgents } from "../services/api";

export default function NumberMappings() {
  const [mappings, setMappings] = useState([]);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ identifier: "", agent_id: "", identifier_type: "did", label: "" });
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [savingId, setSavingId] = useState(null);

  async function loadData() {
    try {
      const [numData, agentData] = await Promise.all([
        listNumbers().catch(() => ({ mappings: [] })),
        listAgents().catch(() => ({ agents: [] })),
      ]);
      setMappings(numData?.mappings || []);
      setAgents(agentData?.agents || []);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadData(); }, []);

  async function handleAdd(e) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await createNumber(form);
      setForm({ identifier: "", agent_id: "", identifier_type: "did", label: "" });
      setShowAdd(false);
      loadData();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleAgentChange(identifier, agentId) {
    setSavingId(identifier);
    try {
      await updateNumber(identifier, { agent_id: agentId });
      setMappings((prev) =>
        prev.map((m) => (m.identifier === identifier ? { ...m, agent_id: agentId } : m))
      );
    } catch (err) {
      alert(`Failed to update: ${err.message}`);
    } finally {
      setSavingId(null);
    }
  }

  async function handleDelete(identifier) {
    if (!confirm(`Delete mapping for ${identifier}?`)) return;
    try {
      await deleteNumber(identifier);
      setMappings((prev) => prev.filter((m) => m.identifier !== identifier));
    } catch (err) {
      alert(`Failed to delete: ${err.message}`);
    }
  }

  if (loading) return <p className="text-gray-500">Loading...</p>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Number Mappings</h1>
          <p className="text-sm text-gray-500 mt-1">Assign phone numbers and extensions to agents.</p>
        </div>
        <button
          onClick={() => setShowAdd(!showAdd)}
          className="bg-indigo-600 text-white px-4 py-2 rounded text-sm font-medium hover:bg-indigo-700"
        >
          {showAdd ? "Cancel" : "Add Mapping"}
        </button>
      </div>

      {showAdd && (
        <form onSubmit={handleAdd} className="bg-white shadow rounded-lg p-4 mb-6 space-y-3">
          {error && <p className="text-red-600 text-sm">{error}</p>}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
            <div>
              <label className="block text-sm font-medium mb-1">Identifier</label>
              <input
                type="text"
                required
                value={form.identifier}
                onChange={(e) => setForm((f) => ({ ...f, identifier: e.target.value }))}
                placeholder="+15551234567"
                className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Type</label>
              <select
                value={form.identifier_type}
                onChange={(e) => setForm((f) => ({ ...f, identifier_type: e.target.value }))}
                className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                <option value="did">DID (Phone Number)</option>
                <option value="extension">Extension</option>
                <option value="email">Email</option>
                <option value="sip">SIP URI</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Label</label>
              <input
                type="text"
                required
                value={form.label}
                onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))}
                placeholder="Main office line"
                className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Agent</label>
              <select
                required
                value={form.agent_id}
                onChange={(e) => setForm((f) => ({ ...f, agent_id: e.target.value }))}
                className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                <option value="">Select agent...</option>
                {agents.map((a) => (
                  <option key={a.agent_id} value={a.agent_id}>{a.name} ({a.email})</option>
                ))}
              </select>
            </div>
          </div>
          <button
            type="submit"
            disabled={submitting}
            className="bg-indigo-600 text-white px-4 py-2 rounded text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
          >
            {submitting ? "Adding..." : "Add Mapping"}
          </button>
        </form>
      )}

      {mappings.length === 0 ? (
        <p className="text-gray-500 text-sm">No number mappings configured. Add your first mapping to start routing calls.</p>
      ) : (
        <div className="bg-white shadow rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-500 uppercase text-xs">
              <tr>
                <th className="px-4 py-3">Identifier</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Label</th>
                <th className="px-4 py-3">Assigned Agent</th>
                <th className="px-4 py-3 w-20"></th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {mappings.map((m) => (
                <tr key={m.identifier} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-mono text-sm">{m.identifier}</td>
                  <td className="px-4 py-3">
                    <span className="px-2 py-0.5 bg-gray-100 rounded text-xs">{m.identifier_type}</span>
                  </td>
                  <td className="px-4 py-3">{m.label}</td>
                  <td className="px-4 py-3">
                    <select
                      value={m.agent_id || ""}
                      onChange={(e) => handleAgentChange(m.identifier, e.target.value)}
                      disabled={savingId === m.identifier}
                      className="border rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:opacity-50"
                    >
                      <option value="">Unassigned</option>
                      {agents.map((a) => (
                        <option key={a.agent_id} value={a.agent_id}>{a.name}</option>
                      ))}
                    </select>
                    {savingId === m.identifier && (
                      <span className="ml-2 text-xs text-gray-400">Saving...</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => handleDelete(m.identifier)}
                      className="text-red-500 hover:text-red-700 text-xs"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
