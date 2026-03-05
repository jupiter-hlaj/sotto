import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listCalls, getTenant } from "../services/api";

const STATUS_COLORS = {
  recording: "bg-yellow-100 text-yellow-800",
  transcribing: "bg-blue-100 text-blue-800",
  summarizing: "bg-purple-100 text-purple-800",
  complete: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
};

export default function Dashboard() {
  const [tenant, setTenant] = useState(null);
  const [calls, setCalls] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([getTenant().catch(() => null), listCalls().catch(() => ({ calls: [] }))])
      .then(([t, c]) => {
        setTenant(t);
        setCalls(c?.calls || []);
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p className="text-gray-500">Loading...</p>;

  const stats = {
    total: calls.length,
    complete: calls.filter((c) => c.status === "complete").length,
    failed: calls.filter((c) => c.status === "failed").length,
    inProgress: calls.filter((c) => ["recording", "transcribing", "summarizing"].includes(c.status)).length,
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-1">Dashboard</h1>
      {tenant && <p className="text-gray-500 mb-6">{tenant.agency_name}</p>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <StatCard label="Total Calls" value={stats.total} />
        <StatCard label="Complete" value={stats.complete} color="text-green-600" />
        <StatCard label="In Progress" value={stats.inProgress} color="text-blue-600" />
        <StatCard label="Failed" value={stats.failed} color="text-red-600" />
      </div>

      <h2 className="text-lg font-semibold mb-3">Recent Calls</h2>
      {calls.length === 0 ? (
        <p className="text-gray-500 text-sm">No calls yet.</p>
      ) : (
        <div className="bg-white shadow rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-500 uppercase text-xs">
              <tr>
                <th className="px-4 py-3">From</th>
                <th className="px-4 py-3">Duration</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Date</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {calls.slice(0, 10).map((call) => (
                <tr key={call.call_id} className="hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <Link to={`/calls/${call.call_id}`} className="text-indigo-600 hover:underline">
                      {call.from_number || "Unknown"}
                    </Link>
                  </td>
                  <td className="px-4 py-3">{formatDuration(call.duration_sec)}</td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[call.status] || "bg-gray-100"}`}>
                      {call.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-500">{new Date(call.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, color = "" }) {
  return (
    <div className="bg-white shadow rounded-lg p-4">
      <p className="text-sm text-gray-500">{label}</p>
      <p className={`text-2xl font-bold ${color}`}>{value}</p>
    </div>
  );
}

function formatDuration(sec) {
  if (!sec && sec !== 0) return "--";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
