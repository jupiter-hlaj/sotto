import { useEffect, useState, useRef } from "react";
import { Link } from "react-router-dom";
import { listCalls } from "../services/api";

const STATUS_COLORS = {
  recording: "bg-yellow-100 text-yellow-800",
  transcribing: "bg-blue-100 text-blue-800",
  summarizing: "bg-purple-100 text-purple-800",
  complete: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
};

export default function CallHistory() {
  const [calls, setCalls] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const intervalRef = useRef(null);

  async function loadCalls() {
    try {
      const data = await listCalls();
      setCalls(data?.calls || []);
    } catch {
      // keep existing data on refresh failure
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadCalls();
    // Auto-refresh every 30 seconds
    intervalRef.current = setInterval(loadCalls, 5_000);
    return () => clearInterval(intervalRef.current);
  }, []);

  const filtered = filter
    ? calls.filter(
        (c) =>
          c.from_number?.includes(filter) ||
          c.status?.includes(filter.toLowerCase()) ||
          c.call_id?.includes(filter)
      )
    : calls;

  if (loading) return <p className="text-gray-500">Loading...</p>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Call History</h1>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-400">Auto-refreshes every 5s</span>
          <input
            type="text"
            placeholder="Filter by number, status, or ID..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="border rounded px-3 py-1.5 text-sm w-64 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
        </div>
      </div>

      {filtered.length === 0 ? (
        <p className="text-gray-500 text-sm">
          {calls.length === 0 ? "No calls recorded yet." : "No calls match your filter."}
        </p>
      ) : (
        <div className="bg-white shadow rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-500 uppercase text-xs">
              <tr>
                <th className="px-4 py-3">From</th>
                <th className="px-4 py-3">Agent</th>
                <th className="px-4 py-3">Duration</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Date</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {filtered.map((call) => (
                <tr key={call.call_id} className="hover:bg-gray-50 cursor-pointer">
                  <td className="px-4 py-3">
                    <Link to={`/calls/${call.call_id}`} className="text-indigo-600 hover:underline">
                      {call.from_number || "Unknown"}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs font-mono">{call.agent_id?.slice(0, 8) || "--"}</td>
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

function formatDuration(sec) {
  if (!sec && sec !== 0) return "--";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
