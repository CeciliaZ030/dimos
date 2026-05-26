Before I commit a plan, let me check one critical thing — whether the Unitree WebRTC library supports remote (non-LAN) mode. Option B depends on this.

Good — **`WebRTCConnectionMethod.Remote` exists** in `unitree_webrtc_connect/webrtc_driver.py:29`. It routes signaling through Unitree's own cloud using `send_sdp_to_remote_peer(sn, token, public_key)`. This is how the official Unitree Go mobile app talks to your dog from anywhere. Option B is fully viable.

Let me also check what credentials it needs and confirm a few details, then I'll write the plan.

OK, fully scoped. Here's the implementation plan. I'll be specific about what to change in code, what to provision, and what iPhone gotchas to watch for.

## System diagram (the whole shape)

```
                     INTERNET
   ┌─────────────────────────────────────────────────────────────────┐
   │                                                                  │
   │   Unitree cloud                                                  │
   │   (signaling + TURN relay)                                       │
   │           ▲                                                      │
   │  ┌────────┼──────────┐                                          │
   │  │ SDP    │   TURN   │                                          │
   │  │ offer  │   relay  │                                          │
   │  ▼        │          ▼                                          │
   │ ┌──────────────┐    ┌─────────────────────┐                     │
   │ │ Cloud dimos  │◄──►│  iPhone (Safari)    │                     │
   │ │ (small VPS)  │    │  - Vercel PWA       │                     │
   │ │              │    │  - cellular up      │                     │
   │ │ Caddy+TLS    │    │  - hotspot ON       │                     │
   │ │ FastAPI:5555 │    └─────────┬───────────┘                     │
   │ │ agent loop   │              │                                  │
   │ │ skills       │              │ WiFi 2.4GHz                      │
   │ │ WebRTC peer  │              │ 172.20.10.0/28                   │
   │ └──────────────┘              ▼                                  │
   │        ▲           ┌──────────────────┐                          │
   │        │           │     Go2 (STA)    │                          │
   │        └───────────│  - joined iPhone │                          │
   │       WebRTC data  │    hotspot       │                          │
   │       channel via  │  - WebRTC peer   │                          │
   │       Unitree TURN │  - Unitree SDK   │                          │
   │                    └──────────────────┘                          │
   └─────────────────────────────────────────────────────────────────┘

   Three legs, all over public internet:
   1. iPhone → Vercel webapp        (HTTPS, cellular)
   2. iPhone → Cloud dimos API      (HTTPS POST + SSE, cellular)
   3. Cloud dimos ↔ Go2             (WebRTC data channel, TURN-relayed)
```

The dog never touches your VPS directly — both ends connect to Unitree's signaling, exchange SDP, then a TURN-relayed data channel forms over Unitree's relay. You don't run a STUN/TURN server.

## Phase 1 — Pre-work (do this first, before any coding)

**1.1 Register the dog with Unitree's cloud** (one-time, ~10 minutes)
- Install the official **Unitree Go** app on a phone, create an account
- Pair the dog through the app (Bluetooth then WiFi setup)
- Once paired, your Unitree account "owns" this dog's serial number
- Verify by opening the app remotely (on cellular only) and confirming you can see the dog status

**1.2 Get credentials from your Unitree account**
- Note the **serial number** (printed on the dog, e.g. `B42D...`)
- Your Unitree account **email** and **password** (used for OAuth-like token exchange via `fetch_token`)

**1.3 Provision the VPS**
- Any provider: DigitalOcean, Hetzner, Fly.io, Railway. ~$10/mo box is plenty
- Ubuntu 22.04 / 24.04, 2 vCPU, 4 GB RAM, public IPv4
- Open ports 80, 443 (Caddy will handle TLS)
- Buy a domain or use a subdomain you control. e.g. `dog.yourname.dev`

## Phase 2 — Patch dimos for Remote mode

The existing `UnitreeWebRTCConnection` hard-codes `LocalSTA`. Add Remote support.

**File**: [dimos/robot/unitree/connection.py](dimos/robot/unitree/connection.py) around line 93-101

```python
import os

class UnitreeWebRTCConnection(Resource):
    def __init__(
        self,
        ip: str | None = None,
        mode: str = "ai",
        connection_method: str = "LocalSTA",   # or "Remote"
        serial_number: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        super().__init__()
        self.ip = ip
        self.mode = mode

        if connection_method == "Remote":
            sn = serial_number or os.environ["GO2_SERIAL_NUMBER"]
            user = username or os.environ["UNITREE_USERNAME"]
            pwd = password or os.environ["UNITREE_PASSWORD"]
            self.conn = LegionConnection(
                WebRTCConnectionMethod.Remote,
                serialNumber=sn,
                username=user,
                password=pwd,
            )
        else:
            self.conn = LegionConnection(WebRTCConnectionMethod.LocalSTA, ip=self.ip)
```

Then in the blueprint that boots GO2Connection, pass `connection_method="Remote"`. Easiest: read it from an env var:

```python
GO2Connection.blueprint(
    connection_method=os.environ.get("GO2_CONNECTION_METHOD", "LocalSTA"),
)
```

## Phase 3 — Deploy dimos to the VPS

**3.1 Install**
```bash
ssh root@your-vps
apt update && apt install -y python3.12 python3.12-venv git caddy
git clone https://github.com/your-fork/dimos /opt/dimos
cd /opt/dimos
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .
```

**3.2 Environment file** `/opt/dimos/.env`
```bash
GO2_CONNECTION_METHOD=Remote
GO2_SERIAL_NUMBER=B42D...your-dog-sn...
UNITREE_USERNAME=your.email@example.com
UNITREE_PASSWORD=your-unitree-password
OPENAI_API_KEY=sk-...              # for the agent + TTS
DIMOS_API_TOKEN=$(openssl rand -hex 16)   # auth for the webapp
HOST=0.0.0.0
PORT=5555
```

**3.3 Add token auth** — anyone can hit your VPS otherwise. In [dimos/web/dimos_interface/api/server.py](dimos/web/dimos_interface/api/server.py) before the `/submit_query` and `/text_stream/*` handlers, add:

```python
from fastapi import Header, HTTPException
import os

EXPECTED_TOKEN = os.environ.get("DIMOS_API_TOKEN", "")

def require_token(authorization: str = Header(None)):
    if not EXPECTED_TOKEN or authorization != f"Bearer {EXPECTED_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid token")
```
Then add `Depends(require_token)` to your routes.

**3.4 Caddy reverse proxy** — TLS termination + the right CORS so Vercel can call it.

`/etc/caddy/Caddyfile`:
```
dog.yourname.dev {
    @cors_preflight method OPTIONS
    header Access-Control-Allow-Origin "https://your-app.vercel.app"
    header Access-Control-Allow-Headers "Authorization, Content-Type"
    header Access-Control-Allow-Methods "GET, POST, OPTIONS"
    respond @cors_preflight 204

    reverse_proxy localhost:5555 {
        flush_interval -1     # critical for SSE — disables buffering
    }
}
```
Then: `systemctl reload caddy`. Caddy auto-provisions a Let's Encrypt cert.

**3.5 systemd service** `/etc/systemd/system/dimos.service`
```ini
[Unit]
After=network.target

[Service]
WorkingDirectory=/opt/dimos
EnvironmentFile=/opt/dimos/.env
ExecStart=/opt/dimos/.venv/bin/dimos run unitree-go2-agentic
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```
`systemctl enable --now dimos`. Tail with `journalctl -u dimos -f`.

## Phase 4 — Vercel webapp (iPhone-tuned)

Minimum surface: one page, voice button, status display. SSR not required — a static SPA is fine. Skeleton in Next.js App Router:

`app/page.tsx`:
```tsx
"use client";
import { useState, useEffect, useRef } from "react";

const API = process.env.NEXT_PUBLIC_DIMOS_API!;       // https://dog.yourname.dev
const TOKEN = process.env.NEXT_PUBLIC_DIMOS_TOKEN!;   // public for hackathon; secure later

export default function Page() {
  const [state, setState] = useState<any>({});
  const [recording, setRecording] = useState(false);
  const recRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  // SSE for agent_state JSON snapshots
  useEffect(() => {
    const es = new EventSource(
      `${API}/text_stream/agent_state?token=${TOKEN}`  // EventSource can't set headers, use query
    );
    es.onmessage = (e) => setState(JSON.parse(e.data));
    es.onerror = () => console.log("SSE dropped, browser will reconnect");
    return () => es.close();
  }, []);

  async function sendQuery(text: string) {
    await fetch(`${API}/submit_query`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${TOKEN}`, "Content-Type": "application/json" },
      body: JSON.stringify({ query: `<user_speech>${text}</user_speech>` }),
    });
  }

  async function startRecording() {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const rec = new MediaRecorder(stream, { mimeType: "audio/mp4" });   // iOS Safari needs mp4
    chunksRef.current = [];
    rec.ondataavailable = (e) => chunksRef.current.push(e.data);
    rec.onstop = async () => {
      const blob = new Blob(chunksRef.current, { type: "audio/mp4" });
      const fd = new FormData(); fd.append("audio", blob, "rec.mp4");
      const r = await fetch(`${API}/upload_audio?token=${TOKEN}`, { method: "POST", body: fd });
      const { text } = await r.json();
      if (text) sendQuery(text);
    };
    rec.start();
    recRef.current = rec;
    setRecording(true);
  }
  function stopRecording() {
    recRef.current?.stop();
    setRecording(false);
  }

  return (
    <main>
      <button
        onTouchStart={startRecording}
        onTouchEnd={stopRecording}
        style={{ width: 200, height: 200, borderRadius: 100,
                 background: recording ? "red" : "navy", color: "white" }}>
        {recording ? "Listening…" : "Hold to speak"}
      </button>
      <pre>{JSON.stringify(state, null, 2)}</pre>
    </main>
  );
}
```

**iPhone-specific webapp notes** (read every line, these are landmines):

1. **`MediaRecorder` on iOS Safari requires `audio/mp4`**, not `audio/webm`. Most tutorials get this wrong.
2. **`getUserMedia` requires HTTPS + a user gesture** (tap). The button's `onTouchStart` works; auto-starting on page load won't.
3. **`EventSource` can't send custom headers** — that's why the token goes in the query string. Add `?token=` handling on the server.
4. **Safari pauses SSE when the tab is backgrounded.** The connection auto-reconnects on resume, but expect a ~1s gap. Don't rely on state snapshots arriving during background. Show "reconnecting" if `es.onerror` fires.
5. **No Service Worker for offline** unless you really want PWA. For a hackathon, just open Safari and use it as a normal web page. If you want "Add to Home Screen" feel, add a minimal `manifest.json`.
6. **iOS audio echo cancellation is aggressive** — if the dog's TTS comes back through the phone mic somehow, Safari may suppress your speech. Since TTS comes out the dog's speaker (not the phone), this won't happen — but if you also play state audio on the phone, watch for it.
7. **Tap-to-unlock audio**: if you ever play audio in the webapp, the first play must be inside a user gesture handler. Otherwise iOS silently ignores it.

`.env.local`:
```
NEXT_PUBLIC_DIMOS_API=https://dog.yourname.dev
NEXT_PUBLIC_DIMOS_TOKEN=<same hex string as VPS>
```

Deploy: `vercel --prod`. Done.

## Phase 5 — iPhone hotspot configuration

This is the single most fiddly part of the demo. Practice it before showtime.

**Settings → Personal Hotspot:**
- **Allow Others to Join: ON**
- **Maximize Compatibility: ON** ← critical. Forces 2.4 GHz. Go2's WiFi only does 2.4 GHz.
- Set a memorable password — the dog will need it
- Note the network name (matches your iPhone name in Settings → General → About → Name)

**Pair the dog to the hotspot:**
- Open the Unitree Go app on a *second* phone (or same phone if you can briefly disable hotspot)
- Connect that phone to the **dog's AP** (`UnitreeGo2-XXXX`)
- In the app: device WiFi settings → enter your iPhone's hotspot SSID and password
- Dog reboots, joins iPhone hotspot
- Verify: dog's status LED shows connected. In your VPS dimos logs, `WebRTC connection 🟢` should appear.

**Keep the iPhone hotspot alive during demo:**
- Personal Hotspot drops if no clients are connected for ~90s. Once the dog connects this isn't an issue.
- Don't lock the screen during demo. Or: Settings → Display & Brightness → Auto-Lock → Never.
- Don't switch to a non-Safari app — iOS may pause Safari's SSE. If you must, keep Safari foreground.
- Low Power Mode disables the hotspot. Turn it off.

## Phase 6 — End-to-end smoke test

In this order, with each step verified before moving on:

1. **VPS**: `curl https://dog.yourname.dev/text_streams -H "Authorization: Bearer $TOKEN"` returns the stream list.
2. **WebRTC**: `journalctl -u dimos -f` shows `WebRTC connection 🟢 connected` after dog joins iPhone hotspot.
3. **Audio upload**: from Mac, `curl -F audio=@test.m4a "https://dog.yourname.dev/upload_audio?token=$TOKEN"` returns transcript.
4. **Query**: `curl -X POST https://dog.yourname.dev/submit_query -H "Authorization: Bearer $TOKEN" -d '{"query":"<user_speech>say hello</user_speech>"}'` → dog speaks via TTS.
5. **SSE**: `curl -N "https://dog.yourname.dev/text_stream/agent_state?token=$TOKEN"` streams JSON snapshots.
6. **iPhone**: open Vercel URL in Safari, push the button, say "find the bathroom." Watch the dog move + the state JSON update.

## What can break on demo day (and the fix)

| Failure | Likely cause | Fix |
|---|---|---|
| Dog won't join hotspot | iPhone on 5GHz | Maximize Compatibility ON |
| Dog connects but commands don't work | Unitree token expired | Restart dimos service; tokens refresh on boot |
| State JSON stops updating | Safari backgrounded | Bring Safari foreground; SSE auto-reconnects |
| Mic button does nothing | First tap didn't grant mic permission | Reload page, tap once to trigger permission dialog |
| Dog moves the wrong way | `lx/ly` axis flip in `move()` | Already handled in [connection.py:182](dimos/robot/unitree/connection.py) but verify with replay first |
| WebRTC reconnect loop | Cellular drops while dog was connected | Dog auto-reconnects through Unitree's signaling; just wait 10s |
| ~500ms command latency | TURN relay path is long | Acceptable for nav; do *not* use this for real-time teleop |
| `503` from `/submit_query` | Agent locked on previous query | Restart dimos; for the demo, queue queries client-side |

## Latency expectations on cellular

- HTTP POST iPhone → VPS: 50-120 ms
- Agent thinks (LLM call): 800-2500 ms
- Skill dispatches Twist → VPS publishes via WebRTC → Unitree TURN → dog: 100-250 ms
- Dog responds physically: another ~200 ms

**Total user-perceived: ~1.5-3 seconds from voice command to motion.** Fine for "find the bathroom," not for precise teleop. Plan the demo accordingly.

## What I would build first

Don't try the whole stack at once. Order of risk:

1. **Day 1**: VPS + Caddy + dimos in replay mode + Vercel skeleton with SSE. Verify the JSON state shows up on iPhone Safari. No dog yet.
2. **Day 2**: Patch Remote mode, pair dog to iPhone hotspot, get the dog talking via TTS over the cloud connection. No agent skills yet.
3. **Day 3**: Wire the blind-assistant skills, system prompt, run a real "find the X" trip.
4. **Day 4**: Iron out the failure modes from the table above. Practice the hotspot setup until it's muscle memory.

Want me to write the actual `connection.py` patch as an Edit so you can drop it in, or the Vercel project files in full?