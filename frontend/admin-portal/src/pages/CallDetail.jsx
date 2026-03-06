import { useEffect, useState, useRef } from "react";
import { useParams, Link } from "react-router-dom";
import { getCall, updateNotes, getRecordingUrl } from "../services/api";

const STATUS_COLORS = {
  recording: "bg-yellow-100 text-yellow-800",
  transcribing: "bg-blue-100 text-blue-800",
  summarizing: "bg-purple-100 text-purple-800",
  complete: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
};

export default function CallDetail() {
  const { callId } = useParams();
  const [call, setCall] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notes, setNotes] = useState("");
  const [notesSaved, setNotesSaved] = useState(false);
  const [audioUrl, setAudioUrl] = useState(null);
  const notesRef = useRef(null);

  useEffect(() => {
    async function load() {
      try {
        const data = await getCall(callId);
        setCall(data);
        setNotes(data.notes || "");
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [callId]);

  useEffect(() => {
    async function loadAudio() {
      try {
        const data = await getRecordingUrl(callId);
        if (data?.url) setAudioUrl(data.url);
      } catch {
        // Recording URL endpoint may not exist yet
      }
    }
    if (call && call.status !== "recording") loadAudio();
  }, [call, callId]);

  async function handleNotesBlur() {
    if (notes === (call?.notes || "")) return;
    try {
      await updateNotes(callId, notes);
      setNotesSaved(true);
      setTimeout(() => setNotesSaved(false), 2000);
    } catch {
      // silent fail on auto-save
    }
  }

  if (loading) return <p className="text-gray-500">Loading...</p>;
  if (error) return <p className="text-red-600">{error}</p>;
  if (!call) return <p className="text-gray-500">Call not found.</p>;

  return (
    <div>
      <Link to="/calls" className="text-indigo-600 hover:underline text-sm mb-4 inline-block">&larr; Back to calls</Link>

      <div className="flex items-center gap-3 mb-6">
        <h1 className="text-2xl font-bold">{call.from_number || "Unknown Caller"}</h1>
        <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[call.status] || "bg-gray-100"}`}>
          {call.status}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6 text-sm">
        <Detail label="Direction" value={call.direction || "--"} />
        <Detail label="Duration" value={formatDuration(call.duration_sec)} />
        <Detail label="Provider" value={call.provider || "--"} />
        <Detail label="Date" value={call.created_at ? new Date(call.created_at).toLocaleString() : "--"} />
      </div>

      {/* Recording Player */}
      <section className="bg-white shadow rounded-lg p-4 mb-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Recording</h2>
          {audioUrl && (
            <a
              href={audioUrl}
              download={`call-${callId}.mp3`}
              className="text-sm text-indigo-600 hover:underline"
            >
              Download MP3
            </a>
          )}
        </div>
        {audioUrl ? (
          <audio controls className="w-full" src={audioUrl}>
            Your browser does not support the audio element.
          </audio>
        ) : (
          <p className="text-gray-400 text-sm">
            {call.status === "recording" ? "Recording in progress..." : "Recording not available."}
          </p>
        )}
      </section>

      {/* Transcript */}
      <section className="bg-white shadow rounded-lg p-4 mb-6">
        <h2 className="text-lg font-semibold mb-3">Transcript</h2>
        {call.transcript_error && (
          <p className="text-red-500 text-sm mb-2">{call.transcript_error}</p>
        )}
        {call.transcript?.segments?.length > 0 ? (
          <div className="space-y-2 max-h-96 overflow-y-auto">
            {call.transcript.segments.map((seg, i) => (
              <div key={i} className="text-sm">
                <span className="font-medium text-indigo-700">{seg.speaker}:</span>{" "}
                <span className="text-gray-700">{seg.text}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-gray-400 text-sm">
            {call.transcript_status === "in_progress"
              ? "Transcription in progress..."
              : call.transcript_status === "pending"
              ? "Waiting for transcription..."
              : "No transcript available."}
          </p>
        )}
      </section>

      {/* AI Summary */}
      <section className="bg-white shadow rounded-lg p-4 mb-6">
        <h2 className="text-lg font-semibold mb-3">AI Summary</h2>
        {call.summary ? (
          <p className="text-sm text-gray-700 whitespace-pre-wrap">{call.summary}</p>
        ) : (
          <p className="text-gray-400 text-sm">
            {call.status === "summarizing" ? "Generating summary..." : "No summary available."}
          </p>
        )}
      </section>

      {/* Action Items */}
      {call.action_items && (
        <section className="bg-white shadow rounded-lg p-4 mb-6">
          <h2 className="text-lg font-semibold mb-3">Action Items</h2>
          <p className="text-sm text-gray-700 whitespace-pre-wrap">{call.action_items}</p>
        </section>
      )}

      {/* Notes */}
      <section className="bg-white shadow rounded-lg p-4 mb-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Notes</h2>
          {notesSaved && <span className="text-green-600 text-xs">Saved</span>}
        </div>
        <textarea
          ref={notesRef}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          onBlur={handleNotesBlur}
          rows={4}
          placeholder="Add notes about this call..."
          className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-y"
        />
      </section>
    </div>
  );
}

function Detail({ label, value }) {
  return (
    <div>
      <p className="text-gray-500 text-xs uppercase">{label}</p>
      <p className="font-medium">{value}</p>
    </div>
  );
}

function formatDuration(sec) {
  if (!sec && sec !== 0) return "--";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
