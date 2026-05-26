"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_DIMOS_API ?? "";
const TOKEN = process.env.NEXT_PUBLIC_DIMOS_TOKEN ?? "";

type AgentState = {
  ts?: number;
  intent?: string;
  phase?: string;
  current_skill?: { name?: string; args?: unknown; state?: string } | null;
  last_observation?: string;
  last_narration?: string;
  awaiting_user?: string | null;
};

export default function Page() {
  const [state, setState] = useState<AgentState>({});
  const [recording, setRecording] = useState(false);
  const [connected, setConnected] = useState(false);
  const [lastError, setLastError] = useState<string>("");

  const recRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  useEffect(() => {
    if (!API) return;
    const url = `${API}/text_stream/agent_state?token=${encodeURIComponent(TOKEN)}`;
    const es = new EventSource(url);
    es.onopen = () => setConnected(true);
    es.onmessage = (e) => {
      try {
        setState(JSON.parse(e.data));
      } catch {
        // ignore non-JSON keepalives
      }
    };
    es.onerror = () => setConnected(false);
    return () => es.close();
  }, []);

  const sendText = useCallback(async (text: string, tag: "user_speech" | "user_reply" | "user_command") => {
    setLastError("");
    try {
      const fd = new FormData();
      fd.append("query", `<${tag}>${text}</${tag}>`);
      const r = await fetch(`${API}/submit_query`, {
        method: "POST",
        headers: { Authorization: `Bearer ${TOKEN}` },
        body: fd,
      });
      if (!r.ok) setLastError(`submit_query ${r.status}`);
    } catch (e) {
      setLastError(String(e));
    }
  }, []);

  const startRecording = useCallback(async () => {
    setLastError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      // iOS Safari requires audio/mp4. Other browsers accept webm.
      const candidates = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"];
      const mimeType = candidates.find((m) => MediaRecorder.isTypeSupported(m)) ?? "";
      const rec = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);

      chunksRef.current = [];
      rec.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
      };
      rec.onstop = async () => {
        streamRef.current?.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || "audio/mp4" });
        if (blob.size < 1000) {
          setLastError("recording too short");
          return;
        }
        const ext = (rec.mimeType || "audio/mp4").includes("webm") ? "webm" : "mp4";
        const fd = new FormData();
        fd.append("file", blob, `rec.${ext}`);
        try {
          const r = await fetch(`${API}/upload_audio?token=${encodeURIComponent(TOKEN)}`, {
            method: "POST",
            body: fd,
          });
          if (!r.ok) {
            setLastError(`upload_audio ${r.status}`);
            return;
          }
          const data = await r.json();
          const text = (data.text || data.transcript || "").trim();
          if (text) {
            const tag = state.awaiting_user ? "user_reply" : "user_speech";
            await sendText(text, tag);
          } else {
            setLastError("empty transcript");
          }
        } catch (e) {
          setLastError(String(e));
        }
      };
      rec.start();
      recRef.current = rec;
      setRecording(true);
    } catch (e) {
      setLastError(`mic: ${String(e)}`);
    }
  }, [sendText, state.awaiting_user]);

  const stopRecording = useCallback(() => {
    if (recRef.current && recRef.current.state !== "inactive") {
      recRef.current.stop();
    }
    setRecording(false);
  }, []);

  const stopButton = useCallback(() => {
    sendText("stop", "user_command");
  }, [sendText]);

  return (
    <main style={{ minHeight: "100vh", padding: 24, display: "flex", flexDirection: "column", gap: 16 }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1 style={{ fontSize: 18, fontWeight: 600 }}>Dimos Guide</h1>
        <span style={{ fontSize: 12, color: connected ? "#4ade80" : "#f87171" }}>
          {connected ? "● connected" : "○ reconnecting"}
        </span>
      </header>

      <section style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12, marginTop: 24 }}>
        <button
          onTouchStart={(e) => {
            e.preventDefault();
            startRecording();
          }}
          onTouchEnd={(e) => {
            e.preventDefault();
            stopRecording();
          }}
          onMouseDown={startRecording}
          onMouseUp={stopRecording}
          onMouseLeave={() => recording && stopRecording()}
          style={{
            width: 220,
            height: 220,
            borderRadius: "50%",
            border: "none",
            background: recording ? "#dc2626" : state.awaiting_user ? "#f59e0b" : "#1e3a8a",
            color: "white",
            fontSize: 18,
            fontWeight: 600,
            touchAction: "none",
            transition: "background 0.1s",
          }}
          aria-label={recording ? "Recording — release to send" : "Hold to speak"}
        >
          {recording ? "Listening…" : state.awaiting_user ? "Tap to reply" : "Hold to speak"}
        </button>

        <button
          onClick={stopButton}
          style={{
            marginTop: 8,
            padding: "12px 24px",
            borderRadius: 8,
            border: "1px solid #6b7280",
            background: "transparent",
            color: "#e5e5e5",
          }}
        >
          Stop
        </button>
      </section>

      {state.awaiting_user ? (
        <section style={{ padding: 12, background: "#1f1f1f", borderRadius: 8, border: "1px solid #f59e0b" }}>
          <div style={{ fontSize: 11, color: "#f59e0b", textTransform: "uppercase", marginBottom: 4 }}>
            Robot is asking
          </div>
          <div>{state.awaiting_user}</div>
        </section>
      ) : null}

      {state.last_narration ? (
        <section style={{ padding: 12, background: "#1f1f1f", borderRadius: 8 }}>
          <div style={{ fontSize: 11, color: "#9ca3af", textTransform: "uppercase", marginBottom: 4 }}>
            Robot said
          </div>
          <div>{state.last_narration}</div>
        </section>
      ) : null}

      <section style={{ marginTop: "auto", fontSize: 11, color: "#6b7280" }}>
        <div>phase: {state.phase ?? "—"}</div>
        <div>skill: {state.current_skill?.name ?? "—"} ({state.current_skill?.state ?? "—"})</div>
        <div>sees: {state.last_observation ?? "—"}</div>
        {lastError && <div style={{ color: "#f87171", marginTop: 6 }}>error: {lastError}</div>}
      </section>

      <details style={{ fontSize: 11, color: "#6b7280" }}>
        <summary>Raw state JSON</summary>
        <pre>{JSON.stringify(state, null, 2)}</pre>
      </details>
    </main>
  );
}
