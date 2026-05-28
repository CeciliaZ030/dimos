# Goldie — full techflow

The app is called **Goldie**: a phone-tuned PWA that controls a Unitree Go2 robot dog through DimOS. It has two modes — **voice** (the main path, designed for blind users — every agent reply is spoken back) and **manual** (joystick + buttons, bypasses the LLM). The whole stack has three independent network channels that flow simultaneously, plus an internal LCM message bus on the backend.

## 1. Webapp side (Next.js 16, React 19, TS, Tailwind v4)

### 1a. Page structure

`webapp/src/pages/index.tsx` is the only page. It composes:

- `<Header>` — Goldie badge, **TTS toggle**, **connection chip** (`useStatus` polls `/unitree/status` every 5s, `useStatus.ts:5-22`)
- `<ModeToggle>` — `voice` vs `manual`
- Either `<VoicePanel>` or `<ManualPanel>` depending on mode

Mode-independent state in `index.tsx`:

- `ttsEnabled` (mute switch for spoken replies)
- `recordingRef` (used for barge-in — suppresses incoming TTS while the user is mid-utterance, `index.tsx:32-40`)
- `useAgentFeed` — SSE subscription to agent replies (always on, both modes)

### 1b. Voice flow (the golden path)

This is the most interesting one. Step by step:

**(1) Hold-to-speak** — `<VoiceButton>` uses pointer events (`VoiceButton.tsx:45-50`). On `pointerdown`:

```
handleStart() in index.tsx:72
  → setActionError(null)
  → cancelSpeech()       (kill any reply still playing so it doesn't overlap)
  → unlockSpeech()       (CRITICAL: plays a silent WAV inside the user gesture
                          so iOS will later allow async <audio> playback)
  → start() from useStt
```

**(2) STT** — `useStt` is a thin shell around two providers selected by `NEXT_PUBLIC_STT` (`stt.ts:199-204`):

- **`WebSpeechStt`** (default) — uses the browser's `webkitSpeechRecognition` API. Live partial transcripts via `onInterim`, final transcript on `onend`. Fully client-side, never hits the network until the query is submitted.
- **`UploadStt`** (production iOS path) — records `audio/mp4` via `MediaRecorder`, POSTs to backend `/upload_audio`, gets the transcript back. iOS requires mp4, not webm (`stt.ts:166-169`).

**(3) Submit** — once we have final text, `submitSpeech` in `index.tsx:53-63`:

```
text = "stand up"
  → wrapUserSpeech(text)   → "<user_speech>stand up</user_speech>"
  → dimos.submitQuery(payload)
       → multipart/form-data POST to <API>/submit_query
       → field name: "query" (NOT JSON — the FastAPI server uses Form(...))
       → Authorization: Bearer $NEXT_PUBLIC_DIMOS_TOKEN
       → header `ngrok-skip-browser-warning: true` (avoids ngrok's interstitial)
       → fetchWithRetry: 2 retries, 8s timeout, retries on 5xx + network errors
```

The `<user_speech>…</user_speech>` wrapper is a signal to the backend agent's system prompt that this came from voice (vs typed text).

**(4) Reply stream** — separately, `useAgentFeed` is already listening on `/text_stream/agent_responses` via Server-Sent Events. The client uses `@microsoft/fetch-event-source` (not the native `EventSource`) specifically so it can attach the `Authorization` header (`dimos.ts:145-172`). Each SSE `data:` frame is a JSON envelope:

```json
{"kind": "ai" | "tool" | "system", "text": "..."}
```

`classifyAgentMessage` in `agentMessage.ts:26-49` parses each frame:
- Dedups consecutive identical text
- Drops empty/ping frames
- Has a legacy fallback that treats plain-text as `kind: "ai"`

For each new message:
- Always appended to the on-screen `messages` list (last 5, `useAgentFeed.ts:40`)
- Triggers `onMessage` callback in `index.tsx:34-40`
- `<StatusCard>` shows `tool`/`system` messages in a faint color (status only); `ai` messages bold

**(5) TTS playback** — In `index.tsx:34-40`, only `kind === "ai"` messages get spoken, AND only if `recordingRef.current === false` (barge-in suppression). Then:

```
speak(text) in lib/speech.ts
  → enqueue text
  → pump():
       POST /api/tts {text}                  ← LOCAL Next.js API route
         → server uses OPENAI_API_KEY (never on client)
         → OpenAI gpt-4o-mini-tts, voice "coral", with steering instructions
           "warm, friendly, upbeat female voice, brisk pace"
         → returns audio/mpeg MP3
       → URL.createObjectURL(blob)
       → audioEl.src = url; audioEl.play()
       → wait for `onended`, then pump next item in queue
```

Why this complexity? `speech.ts:1-10` explains it: iOS Safari's `SpeechSynthesis` API silently drops anything not initiated inside a user gesture, and agent replies arrive **async** over SSE — so they never spoke. The fix is a single `<audio>` element that gets "unlocked" by playing a tiny silent WAV in the tap handler (`unlockSpeech`), after which iOS will play later async audio. The queue ensures multiple AI messages don't talk over each other.

`cancelSpeech` aborts both the in-flight fetch and the playing audio (used on barge-in and on the Interrupt button).

### 1c. Quick action flow

Sit / Jump / Stand buttons (`QuickActions.tsx`). These send **natural-language commands through the same `/submit_query` path** — not the direct `/unitree/command` sport endpoint:

```
handleAction({label:"Sit", command:"sit"})
  → dimos.submitQuery("sit")     (no <user_speech> wrapper)
```

The design intent is so the agent can narrate what it's doing ("Okay, sitting now…") through the same SSE → TTS pipeline as voice. Direct `/unitree/command` would skip the agent and skip narration.

### 1d. Manual / joystick flow (totally separate channel)

`useTeleop` in `useTeleop.ts` opens a **Socket.IO** connection (not HTTP) to a **second backend port (7779)** at `NEXT_PUBLIC_DIMOS_VIS`. This is DimOS's visualization server (`WebsocketVisModule`), and it accepts a `move_command` event with ROS-style Twist payloads:

```js
socket.emit("move_command", {
  linear:  { x: vx * 0.6,    y: 0, z: 0 },  // Go2 max forward: 0.6 m/s
  angular: { x: 0, y: 0, z: -turn * 1.0 },  // negative z = clockwise
});
```

In `index.tsx:118-141`:
- On joystick drag: emit immediately, then `setInterval` re-emit every 66ms (~15Hz)
- On release: emit a zero Twist (`teleop.stop()`)

`Joystick.tsx` uses pointer capture and `computeDrive` (`joystick.ts`) which clamps to the pad rim, applies a 12% dead zone, maps screen-down to negative `vx`, etc. This path is real-time teleop — it never touches the LLM agent.

### 1e. Status & connection

- `useStatus` polls `GET /unitree/status` every 5s. Accepts either the real backend's `{status:"online"}` or the mock's `{connected:true}` (`dimos.ts:103-118`).
- `useTeleop` exposes `configured` (env var set) and `connected` (Socket.IO actually connected) so the manual panel can show "teleop link down" (`ManualPanel.tsx:33-37`).

### 1f. Next.js API routes (run on the webapp server, NOT the dimos backend)

- **`/api/tts`** — OpenAI proxy described above. Keeps `OPENAI_API_KEY` server-side. Model and voice are env-overridable (`tts.ts:21-27`).
- **`/api/log`** — Dev-only sink. Every `devLog({event:"agent-msg"|"tts"|"stt-error"|...})` from the frontend POSTs here and gets pretty-printed to the `npm run dev` terminal — so you can see speech transcripts and TTS decisions on your laptop while testing from a phone over ngrok (`log.ts`).
- **`/api/mock/[...path]`** — A complete in-memory mock of the dimos backend (`mock/[...path].ts`). When `NEXT_PUBLIC_DIMOS_API` is unset, the client defaults to `/api/mock`, and you get a scripted SSE script ("On it." → "Navigation goal reached" → "Done — what would you like next?") so the UI is fully runnable with no robot. The mock also handles `submit_query`, `upload_audio`, `unitree/status`, `unitree/command`, `interrupt`.

### 1g. Cross-cutting iOS / PWA details

- `_app.tsx` viewport meta has `maximum-scale=1, viewport-fit=cover` — stops Safari auto-zoom and lets the gradient extend under the notch
- `_document.tsx` declares `apple-mobile-web-app-capable`, manifest, apple-touch-icon — A2HS works without a service worker
- `ngrok-skip-browser-warning` header on every request (`dimos.ts:19-28`) — avoids the free-tier ngrok HTML interstitial that otherwise breaks fetch/SSE through the tunnel

---

## 2. Backend side (Python, DimOS)

The backend is **two HTTP servers and an internal message bus**, plus the dog connection.

### 2a. Server topology

```
        ┌─────────────────────────────────────────────────┐
        │   DimOS backend process (a single Python proc)  │
        │                                                 │
        │   ┌─────────────────────┐                       │
        │   │ FastAPI/uvicorn     │  port 5555            │
        │   │ (RobotWebInterface) │                       │
        │   │  • /submit_query    │                       │
        │   │  • /upload_audio    │                       │
        │   │  • /text_stream/*   │  ← SSE                │
        │   │  • /unitree/*       │                       │
        │   │  • /video_feed/*    │                       │
        │   └──────────┬──────────┘                       │
        │              │                                  │
        │              ▼ rx Subjects                      │
        │        ┌─────────────────┐                      │
        │        │ WebInput module │                      │
        │        │ • query_subject │                      │
        │        │ • audio_subject │──┐                   │
        │        └────────┬────────┘  │                   │
        │                 │           ▼                   │
        │                 │      AudioNormalizer          │
        │                 │      → WhisperNode (STT)      │
        │                 │           │                   │
        │                 ▼           ▼                   │
        │        ┌──────────────────────────┐             │
        │        │ LCM bus (pLCMTransport)  │             │
        │        │ topic: "/human_input"    │             │
        │        │ topic: "/agent"          │             │
        │        └──────────┬───────────────┘             │
        │                   │                             │
        │                   ▼                             │
        │        ┌────────────────────┐                   │
        │        │ LLM Agent process  │                   │
        │        │ (LangChain + MCP   │                   │
        │        │  skills, GPT)      │                   │
        │        └──────────┬─────────┘                   │
        │                   │ ROS Twist                   │
        │                   ▼                             │
        │   ┌─────────────────────┐                       │
        │   │ Socket.IO + uvicorn │  port 7779            │
        │   │ (WebsocketVisModule)│                       │
        │   │  • move_command     │  ← teleop in          │
        │   │  • robot_pose, etc. │  ← vis out            │
        │   └──────────┬──────────┘                       │
        │              ▼                                  │
        │     ROS-style transport (cmd_vel, ...)          │
        │              │                                  │
        └──────────────┼──────────────────────────────────┘
                       ▼
              UnitreeWebRTCConnection
              (LocalSTA or Remote mode via Unitree cloud)
                       │
                       ▼
                    Go2 dog
```

### 2b. FastAPI server (port 5555)

File: `dimos/web/dimos_interface/api/server.py`. Wrapped as `RobotWebInterface` (`robot_web_interface.py`) when the agent uses it.

Key routes (`server.py:242-372`):

| Route | What it does |
|---|---|
| `POST /submit_query` | Reads `query` from form data → pushes onto `query_subject` (an rx Subject). That's it — async. Returns `{success: true}` (`server.py:286-300`). |
| `POST /upload_audio` | Reads the multipart file → `_decode_audio` runs ffmpeg pipe to convert webm/opus → 16kHz mono PCM → builds an `AudioEvent` and pushes onto `audio_subject` (`server.py:302-333`). |
| `GET /text_stream/{key}` | `EventSourceResponse` that pulls from `self.text_queues[key]` and emits SSE frames. Sends a `ping` event every 100ms when the queue is empty (`server.py:191-213, 365-369`). |
| `GET /text_streams` | List of available text streams (used by the client to discover `agent_responses`). |
| `POST /unitree/command` | Reads `{command}` JSON → pushes onto the same `query_subject` (same path as a typed query, no agent bypass). |
| `GET /unitree/status` | Just returns `{status:"online"}`. The connection chip works off this. |
| `GET /video_feed/{key}` | MJPEG over `multipart/x-mixed-replace` for the dog's cameras. Currently unused by the webapp. |

**Auth** (`server.py:245-259`): `DIMOS_API_TOKEN` env var. If set, all protected endpoints require `Authorization: Bearer <token>` OR `?token=<token>` (the latter is for browsers that can't set headers on `EventSource`). If the env is empty, auth is disabled — that's the local-dev mode.

**CORS** is wide open (`allow_origins=["*"]`).

The text-stream plumbing is `rx → Queue → SSE`: each named text stream is an rx Subject; on init the server subscribes each one and puts every emission into a per-stream `Queue` (`server.py:114-123`); the SSE handler pops from that queue.

### 2c. WebInput module — the bridge

File: `dimos/agents/web_human_input.py`. This is what wires the FastAPI server to the LLM agent. On `start()`:

1. **Creates** the `RobotWebInterface` with `text_streams={"agent_responses": Subject()}` and `audio_subject`.
2. **Audio pipeline** (`web_human_input.py:78-87`):
   ```
   audio_subject → AudioNormalizer → WhisperNode (Whisper STT) → text
   ```
   So if you use the upload-STT path on the webapp, it's actually Whisper running on the backend.
3. **Text out** (`web_human_input.py:91-96`):
   ```
   query_subject (typed/voice queries from /submit_query)    ─┐
                                                              ├─→ LCM publish on "/human_input"
   WhisperNode.emit_text() (transcripts from /upload_audio)  ─┘
   ```
4. **Replies in** (`web_human_input.py:57-76`) — this is the part the recent backend commit added:
   ```
   LCM subscribe "/agent"
     for each BaseMessage:
       kind = msg.type   # "human" | "ai" | "tool" | "system"
       if kind == "human": skip   # echo of what the user said
       content = msg.content (flatten list-of-parts if needed)
       agent_responses.on_next(json.dumps({"kind": kind, "text": content}))
   ```
   This is what produces the typed JSON envelope the webapp parses in `classifyAgentMessage`. `tool` messages (e.g. "Navigation goal reached") get rendered as faint status on the phone; `ai` messages get spoken aloud.

### 2d. The LLM agent (separate concern)

The actual agent isn't in this commit set — it lives elsewhere in the codebase and is launched by the DimOS CLI. From the LCM topology you can derive its contract:

- **Subscribes**: LCM `/human_input` (string, e.g. `<user_speech>stand up</user_speech>` or `sit`)
- **Publishes**: LCM `/agent` (LangChain `BaseMessage` objects with `.type` ∈ `{human, ai, tool, system}`)
- **Internally**: runs a LangChain MCP loop over a skill catalog (the `dimos/agents/skills/` tree has e.g. `blind_assistant_skills.py`). When it needs to move the dog, it calls a skill that publishes a Twist on the same transport the teleop endpoint publishes to.

LCM (`pLCMTransport`) is just an in-process pub/sub bus on top of the LCM library — decouples the web layer from the agent layer.

### 2e. Socket.IO vis server (port 7779)

File: `dimos/web/websocket_vis/websocket_vis_module.py`. Separate ASGI app on a separate port. Two purposes:

- **Inbound `move_command`** (`websocket_vis_module.py:332-353`): when the webapp's joystick fires, this handler builds a `Twist` (and `TwistStamped`) and publishes on `tele_cmd_vel` / `movecmd_stamped` — which are wired into the same Twist topic the agent's skills publish to. So manual joystick and the LLM both ultimately push the same Twist messages to the dog.
- **Outbound vis** (`_emit` calls): `robot_pose`, `gps_location`, `path`, `costmap`, etc. — used by a debug visualization the webapp doesn't currently consume.

### 2f. Dog connection (the last leg)

Twist commands flow through DimOS's transport layer down to `UnitreeWebRTCConnection`, which holds a WebRTC data channel to the Go2. From `DOG-PHONE-INTERFACE.md` it's clear there are two modes:

- **`LocalSTA`** — direct WebRTC on the same LAN
- **`Remote`** — signaling routed through Unitree's cloud + TURN-relayed data channel, so the dog and the dimos backend can be on different networks (used in the "iPhone hotspot, VPS in the cloud, dog on the hotspot" demo topology)

The dog returns telemetry (pose, etc.) the same way back into the rx streams.

---

## 3. End-to-end golden path (voice → motion → spoken reply)

For your diagram, this is the sequence to trace:

```
[Phone Safari]  user holds button
              → unlockSpeech (silent WAV) + start STT
              → "find the bathroom"   (Web Speech API, on-device)
              → wrapUserSpeech → "<user_speech>find the bathroom</user_speech>"
              → POST /submit_query (multipart, Bearer token)

[FastAPI :5555]  Form("query") → query_subject.on_next(text)

[WebInput rx]   query_subject → pLCMTransport("/human_input").publish(text)

[LCM bus]       /human_input → LLM Agent

[LLM Agent]     LangChain MCP loop:
                  emits "ai" message: "Heading to the bathroom now."
                  calls nav skill → publishes Twist on cmd_vel
                  emits "tool" message: "Navigation goal reached"
                  emits "ai" message: "We're there."

[LCM bus]       /agent ─┬─→ WebInput._on_agent_message
                        │     → json.dumps({kind, text})
                        │     → agent_responses Subject.on_next(...)
                        │
                        └─→ (Twist for dog flows separately
                             through cmd_vel transport → WebRTC → Go2)

[FastAPI :5555]  text_queues["agent_responses"] ← Subject
                 → SSE: data: {"kind":"ai","text":"Heading…"}\n\n
                 → SSE: data: {"kind":"tool","text":"Navigation goal reached"}\n\n
                 → SSE: data: {"kind":"ai","text":"We're there."}\n\n

[Phone Safari]  useAgentFeed → classifyAgentMessage → setMessages
                onMessage callback:
                  if kind=="ai" && !recording  → speak(text)
                    → POST /api/tts {text}
                    → /api/tts (Next API) → OpenAI gpt-4o-mini-tts → MP3
                    → audio element plays (unlocked earlier)
                  if kind=="tool" → render only, don't speak

[Phone]         User hears "Heading to the bathroom now… We're there."
                Dog has physically moved during this.
```

And the **parallel manual channel** for comparison:

```
[Phone Safari]  joystick drag
              → useTeleop.drive({vx, vy, turn}) @ 15Hz
              → socket.emit("move_command", Twist)  via Socket.IO

[Socket.IO :7779]  move_command handler
              → builds Twist
              → tele_cmd_vel.publish(twist)

[transport]   cmd_vel topic → UnitreeWebRTCConnection → WebRTC data channel → Go2

(No LLM, no SSE, no TTS — pure teleop loop.)
```

---

The three independent channels for the diagram:

1. **HTTP/JSON + SSE (port 5555)** — voice & quick actions → agent → spoken reply
2. **Socket.IO (port 7779)** — joystick → Twist → dog
3. **OpenAI TTS (via local Next.js /api/tts)** — agent text → speech in the phone

…and the internal LCM bus (`/human_input` ↔ `/agent`) is what decouples the web layer from the agent layer on the backend.
