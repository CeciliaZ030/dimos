/**
 * Dimos backend client.
 *
 * Centralizes the API contract between this Svelte frontend and the dimos
 * Python backend (FastAPI on :5555, exposed publicly via Tailscale Serve).
 *
 * Configuration via Vite env vars:
 *   VITE_DIMOS_API    full base URL, e.g. https://go2-jetson.foo.ts.net:8443
 *                     defaults to http://${hostname}:5555 (legacy same-host
 *                     dev behaviour)
 *   VITE_DIMOS_TOKEN  bearer token configured on the dimos server via
 *                     DIMOS_API_TOKEN env var. Empty disables auth (local
 *                     dev only).
 */

export function getServerUrl(): string {
  // Vite replaces import.meta.env.* at build time. Falling back to the
  // same-host:5555 pattern keeps existing local-dev behaviour working.
  const fromEnv = import.meta.env.VITE_DIMOS_API as string | undefined;
  if (fromEnv && fromEnv.length > 0) return fromEnv.replace(/\/$/, '');
  const hostname = window.location.hostname;
  return `http://${hostname}:5555`;
}

export function getToken(): string {
  return (import.meta.env.VITE_DIMOS_TOKEN as string | undefined) ?? '';
}

function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

/** Append `?token=...` for endpoints that can't accept headers (EventSource). */
export function withTokenQuery(url: string): string {
  const t = getToken();
  if (!t) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}token=${encodeURIComponent(t)}`;
}

export type SubmitTag = 'user_speech' | 'user_reply' | 'user_command';

/** POST `/submit_query` with a tagged user message. */
export async function submitQuery(text: string, tag: SubmitTag = 'user_speech'): Promise<void> {
  const body = new FormData();
  body.append('query', `<${tag}>${text}</${tag}>`);
  const res = await fetch(`${getServerUrl()}/submit_query`, {
    method: 'POST',
    headers: authHeaders(),
    body,
  });
  if (!res.ok) {
    throw new Error(`submit_query ${res.status}`);
  }
}

/** Convenience: send the "stop" command. */
export const submitStop = (): Promise<void> => submitQuery('stop', 'user_command');

export type AgentState = {
  ts?: number;
  intent?: string;
  phase?: string;
  current_skill?: { name?: string; args?: unknown; state?: string } | null;
  last_observation?: string;
  last_narration?: string;
  awaiting_user?: string | null;
};

/**
 * Subscribe to the `agent_state` SSE stream. Returns a disposer.
 *
 * Token is appended as a query param because EventSource cannot set headers.
 */
export function subscribeAgentState(
  onSnapshot: (s: AgentState) => void,
  onError?: (e: Event) => void,
): () => void {
  const url = withTokenQuery(`${getServerUrl()}/text_stream/agent_state`);
  const es = new EventSource(url);
  es.onmessage = (e) => {
    try {
      onSnapshot(JSON.parse(e.data) as AgentState);
    } catch {
      // ignore ping / non-JSON keepalives
    }
  };
  if (onError) es.onerror = onError;
  return () => es.close();
}

/* -------------------------------------------------------------------- */
/* Browser-side STT (replaces server /upload_audio).                     */
/*                                                                       */
/* Uses Web Speech API: iOS Safari + Chrome/Edge support it; Firefox    */
/* does not. iOS does on-device recognition when "Enable Dictation →    */
/* On-Device" is set in iOS Settings.                                    */
/* -------------------------------------------------------------------- */

type SRConstructor = new () => any;
declare global {
  interface Window {
    SpeechRecognition?: SRConstructor;
    webkitSpeechRecognition?: SRConstructor;
  }
}

export function isSTTSupported(): boolean {
  return typeof window !== 'undefined'
    && !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

export type SpeechSession = {
  stop(): void;
};

export function startSpeechSession(callbacks: {
  onPartial?: (text: string) => void;
  onFinal: (text: string) => void;
  onError?: (code: string) => void;
}): SpeechSession {
  const SR = window.SpeechRecognition ?? window.webkitSpeechRecognition;
  if (!SR) {
    callbacks.onError?.('not-supported');
    return { stop: () => undefined };
  }
  const rec = new SR();
  rec.continuous = false;
  rec.interimResults = true;
  rec.lang = navigator.language || 'en-US';
  rec.maxAlternatives = 1;

  let finalTranscript = '';

  rec.onresult = (event: any) => {
    let interim = '';
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const r = event.results[i];
      if (r.isFinal) finalTranscript += r[0].transcript;
      else interim += r[0].transcript;
    }
    callbacks.onPartial?.(finalTranscript + interim);
  };

  rec.onerror = (event: any) => {
    callbacks.onError?.(event.error || 'unknown');
  };

  rec.onend = () => {
    const text = finalTranscript.trim();
    if (text) callbacks.onFinal(text);
  };

  try {
    rec.start();
  } catch (e) {
    callbacks.onError?.(String(e));
  }

  return {
    stop() {
      try {
        rec.stop();
      } catch {
        /* already stopped */
      }
    },
  };
}
