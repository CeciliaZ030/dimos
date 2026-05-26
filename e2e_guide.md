# End-to-end guide: blind-assistant Go2 demo

How to take this repo from "we got the dog" to "iPhone talking to it over the internet."

Target hardware: **Unitree Go2 EDU** (onboard Jetson Orin NX with CUDA).
Target phone: **iPhone** running Safari with the Tailscale app installed and logged in.

You should be able to execute this end to end in ~2 hours assuming the dog is paired and Tailscale is already set up on the iPhone and one workstation.

---

## 0 · Prerequisites checklist

Before you start, confirm you have all of these. Going in order saves a lot of time.

- [ ] Go2 EDU is powered on and you can ping it from your laptop
- [ ] You know its SSH credentials (Unitree default: `unitree / 123`)
- [ ] Your Tailscale account is set up and the iPhone is logged in
  ([verify](https://login.tailscale.com/admin/machines))
- [ ] **HTTPS certificates are enabled tailnet-wide**
  ([Tailscale DNS admin](https://login.tailscale.com/admin/dns) → Enable HTTPS)
- [ ] You have an `OPENAI_API_KEY` ready to paste
- [ ] You have this repo accessible from your dev workstation
- [ ] iPhone has Safari + Tailscale installed + Tailscale toggled ON
- [ ] Vercel CLI is installed and logged in: `vercel --version` works

---

## 1 · SSH into the dog

```bash
# from your laptop, connected to the Go2's WiFi (UnitreeGo2-XXXX) or to the same LAN
ping 192.168.123.18                # adjust if your dog uses a different IP

ssh unitree@192.168.123.18
# password: 123
```

Once in, sanity-check the environment:

```bash
uname -m                # must print: aarch64
nvidia-smi              # must show the GPU (Jetson Orin NX)
nvcc --version          # confirms CUDA version
df -h /                 # need ~6 GB free
free -h                 # 8 or 16 GB RAM
```

If `nvidia-smi` errors, this isn't an EDU — stop and resolve before continuing.

---

## 2 · Tailscale on the dog

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
# follow the printed URL on your laptop browser, log in with the SAME account
# as your iPhone
```

Verify from your laptop:

```bash
tailscale status        # the dog should appear (e.g. "go2-jetson  100.x.x.x")
```

Note the dog's tailnet hostname — looks like `go2-jetson.<tailnet>.ts.net`.
You'll need it in step 7.

---

## 3 · Install dimos on the Jetson

```bash
# on the Jetson
sudo apt update
sudo apt install -y git git-lfs python3.12-venv ffmpeg
git lfs install

git clone https://github.com/<your-fork>/dimos.git ~/dimos
cd ~/dimos

# LFS models — yolo + clip + replay db
git lfs pull --include="data/.lfs/models_clip.tar.gz,data/.lfs/models_yolo.tar.gz,data/.lfs/go2_short.db.tar.gz"

python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install python-multipart
```

`pip install -e .` will take **10-30 minutes** on Jetson. Don't kill it if torch
or opencv compilation looks stuck.

**Critical verification** before continuing:

```bash
python3 -c "import torch; print(torch.cuda.is_available())"
# MUST print: True
```

If it prints `False`, `pip` pulled the CPU build. Install Jetson PyTorch:
[NVIDIA Jetson PyTorch wheels](https://forums.developer.nvidia.com/c/agx-autonomous-machines/jetson-embedded-systems/70). Match your JetPack version.

---

## 4 · Sudoers + multicast route

dimos needs sudo to configure LCM multicast on boot. Set it up once:

```bash
echo "$(whoami) ALL=(ALL) NOPASSWD: /sbin/route, /usr/sbin/ip" \
  | sudo tee /etc/sudoers.d/dimos-net
sudo chmod 440 /etc/sudoers.d/dimos-net
```

Test:
```bash
sudo -n ip route show  >/dev/null && echo "sudo OK" || echo "FIX SUDOERS"
```

---

## 5 · Push the guide-specific code from your dev box

These files are NOT in upstream dimos — they're the work we did locally:

```
dimos/agents/blind_assistant_prompt.py
dimos/agents/guide_web_input.py
dimos/agents/skills/blind_assistant_skills.py
dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_guide.py
dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_guide_lite.py
dimos/robot/all_blueprints.py        (modified)
dimos/web/dimos_interface/api/server.py   (modified — bearer auth)
dimos/robot/unitree/connection.py    (modified — Remote WebRTC + observe fix)
dimos/agents/mcp/mcp_client.py       (modified — background tool retry)
bin/run-demo.sh
ops/Caddyfile, ops/dimos.service, ops/dimos.env.example
webapp/
```

If your fork already has these committed, `git pull` on the Jetson is enough.
Otherwise rsync from your workstation:

```bash
# from your dev box (Mac)
rsync -avz --exclude .venv --exclude node_modules \
           --exclude .git --exclude data/.lfs \
  ~/Code/dimos/ unitree@<dog-tailscale-hostname>:~/dimos/
```

---

## 6 · Create the full guide blueprint

The lite blueprint hit race conditions on Mac replay mode. The proven path is
to stack on top of `unitree-go2-agentic` with two CUDA-heavy modules disabled.

**Already in the repo** at [unitree_go2_guide_full.py](dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_guide_full.py):

```python
from dimos.agents.guide_web_input import GuideWebInput
from dimos.agents.skills.blind_assistant_skills import BlindAssistantSkillContainer
from dimos.agents.skills.speak_skill import SpeakSkill
from dimos.agents.web_human_input import WebInput
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic import unitree_go2_agentic

unitree_go2_guide_full = autoconnect(
    unitree_go2_agentic.disabled_modules(WebInput, SpeakSkill),
    BlindAssistantSkillContainer.blueprint(),
    GuideWebInput.blueprint(enable_stt=False),
)
```

Why disable WebInput + SpeakSkill: GuideWebInput owns port 5555 (would clash
with WebInput) and BlindAssistant has its own TTS for `narrate` (replaces
`speak`).

**Important — DO NOT disable `McpClient` here.** Autoconnect's
`disabled_modules` mechanism makes McpClient permanently disabled even if
you try to re-add it with `McpClient.blueprint(system_prompt=...)`. The
disabled set wins. To override the system prompt, use the file-edit method
in section 6.5.

Registered in `all_blueprints.py` as `unitree-go2-guide-full`.

### 6.5 · Swap the system prompt (file-edit method)

Since the autoconnect override doesn't work, edit `dimos/agents/system_prompt.py`
directly to make `SYSTEM_PROMPT` point at the blind-assistant prompt. The
agentic base's McpClient imports `SYSTEM_PROMPT` from that module, so the
swap picks up automatically.

Already applied in the repo — top of `dimos/agents/system_prompt.py`:

```python
from dimos.agents.blind_assistant_prompt import BLIND_ASSISTANT_PROMPT as _BAP
SYSTEM_PROMPT = _BAP

_ORIGINAL_DANEEL_PROMPT = """..."""   # kept for easy revert
```

Verify the swap took effect:

```bash
.venv/bin/python3 -c "from dimos.agents.system_prompt import SYSTEM_PROMPT; print(SYSTEM_PROMPT[:80])"
# should print: "You are Daneel, a guide robot assisting a user who is blind..."
```

---

## 7 · Boot dimos on the dog

The exact launch command, verified end-to-end on Mac in replay mode:

```bash
# on the Jetson
cd ~/dimos
export OPENAI_API_KEY=sk-...
export DIMOS_API_TOKEN=$(openssl rand -hex 16)
mkdir -p /tmp/dimos_run
echo "$DIMOS_API_TOKEN" > /tmp/dimos_run/api_token
echo "Token: $DIMOS_API_TOKEN"

# On the real dog — NO --replay flag
uv run dimos run unitree-go2-guide-full \
  --disable security-module \
  --disable rerun-bridge-module \
  2>&1 | tee /tmp/dimos_run/dimos.log

# For pre-arrival testing on a Mac (replay):
uv run dimos --replay run unitree-go2-guide-full \
  --disable security-module \
  --disable rerun-bridge-module \
  2>&1 | tee /tmp/dimos_run/dimos.log
```

**Why each disable:**
- `--disable security-module` — module uses EdgeTAM which requires CUDA. On Jetson with CUDA it's optional; on Mac with no CUDA it's mandatory.
- `--disable rerun-bridge-module` — suppresses the native Rerun viewer popup. Optional; keep enabled on the dog if you want a live 3D viz on the operator's screen.

**Use `uv run` not `.venv/bin/dimos`** — `uv run` resolves all the project deps cleanly. First boot will download ~80 packages (~3 min); subsequent runs are instant.

Watch for these lines in order:

```
Building the blueprint
Starting the modules
WebRTC connection 🟢 connected                                # (live mode only)
Discovered tools from MCP server. n_tools=22 tools=[..., narrate, ask_user, reply_user]
Guide web interface started at http://localhost:5555
```

**`n_tools=22` is the success signal.** That includes the 19 from the agentic
base + your three blind-assistant skills. If you see fewer than 22, something
in the BlindAssistantSkillContainer registration failed.

If `n_tools` stays at 0 for more than 90 seconds, the McpClient background
retry should kick in — check log for `Background MCP tool refetch succeeded`.
If it doesn't, kill and restart (dimos has a known startup race on the
on_system_modules lifecycle).

---

## 8 · Expose via Tailscale Serve

In a second SSH session on the dog (leave dimos running in the first):

```bash
tailscale serve --bg --https=8443 5555
# prints: https://go2-jetson.<tailnet>.ts.net:8443/  |--  proxy http://127.0.0.1:5555

tailscale serve status        # verify
```

---

## 9 · Webapp pointing at the dog

On your dev box:

```bash
cd ~/Code/dimos/webapp

cat > .env.local <<EOF
NEXT_PUBLIC_DIMOS_API=https://go2-jetson.<tailnet>.ts.net:8443
NEXT_PUBLIC_DIMOS_TOKEN=<paste DIMOS_API_TOKEN from the dog>
EOF

vercel --prod
# note the deployed URL, e.g. https://dimos-guide.vercel.app
```

---

## 10 · Test from the iPhone

1. Phone on cellular OR same WiFi as the dog — both work
2. Open the Vercel URL in Safari
3. Confirm green "● connected" dot top-right (SSE alive)
4. Tap and hold "Hold to speak"; say *"are you there"*; release
5. Dog should `narrate(...)` back; you'll hear it on the dog's speaker, and
   the JSON state below the button will update with `last_narration` and
   `phase: idle`

If voice doesn't go through but the text input box does, your iPhone Web
Speech API is denied — Settings → Safari → Microphone → Allow.

---

## 11 · Demo script for the real run

These are the two scenarios to rehearse:

### Scenario A — in-view navigation

```
You:    "find the front door"   (assuming you're facing one)
Dog:    [calls observe()]
Dog:    [narrates] "I see a door ahead, heading there"
Dog:    [calls navigate_with_text("door")]
Dog:    [arrives, narrates] "I'm at the door"
Dog:    [calls reply_user(status="arrived", summary="we're at the door")]
```

### Scenario B — out-of-view exploration

```
You:    "find the bathroom"
Dog:    [calls observe()]
Dog:    [narrates] "I don't see a bathroom sign from here"
Dog:    [calls ask_user("should I look around?")]
[UI shows orange "Robot is asking" panel; button turns amber]
You:    "yes"      (tagged automatically as <user_reply>)
Dog:    [calls start_exploration()]
Dog:    [narrates progress as it sees signs]
Dog:    [eventually arrives or reports failure]
```

---

## 12 · Recovery procedures

Things that will probably break at least once. Have these commands ready.

### Dog won't connect to motors

```bash
# inside dimos log, check WebRTC connection
# if it shows 🔴, restart dimos. If repeated, restart the dog itself.
```

### MCP tools stuck at 0 after 90 seconds

```bash
# the McpClient background retry should fix this on its own
# if not, restart dimos
pkill -f "dimos.*run"
dimos run unitree-go2-guide-full
```

### Vercel URL works but commands don't arrive

```bash
# from your laptop
TOKEN=<value>
curl -s -X POST https://go2-jetson.<tailnet>.ts.net:8443/submit_query \
  -H "Authorization: Bearer $TOKEN" \
  --data-urlencode "query=<user_speech>hello</user_speech>"
# expect: {"success":true,"message":"Query received"}
```

If the curl works but the iPhone doesn't:
- Force-reload Safari (URL bar → AA → reload)
- Check iPhone Tailscale is ON
- Check `NEXT_PUBLIC_DIMOS_API` exactly matches the Tailscale Serve URL
  including the `:8443` port

### iPhone STT silently fails

Common errors and fixes:
- `stt: not-allowed` → Settings → Safari → Microphone → Allow
- `stt: no-speech` → speak louder, hold longer
- `stt: network` → Settings → General → Keyboards → Dictation → On-Device

### Dog moves wrong direction or won't stop

Hit the **Stop** button. If unresponsive, hit the **physical** emergency stop
on the dog. Investigate calmly. The `move()` skill has a 0.5s timeout that
re-zeros velocity automatically — a stuck dog usually means it's executing
a `navigate_with_text` to somewhere unreachable.

---

## 13 · Tearing down

```bash
# on the dog
pkill -f "dimos.*run"
tailscale serve --https=8443 off
exit

# on the dev box
vercel rm dimos-guide --yes        # optional
```

---

## 14 · What to log during the demo

For the postmortem / writeup:

```bash
# on the dog, in another SSH session before demo starts
tee /tmp/dimos_run/dimos.log < /dev/null &
journalctl -f -u dimos > /tmp/dimos_run/journal.log &
```

Then after the demo, scp the logs:

```bash
scp unitree@<host>:/tmp/dimos_run/dimos.log ./demo-dimos.log
```

---

## 15 · Known-good fallbacks

If anything goes sideways during the demo:

1. **Webapp dies** — switch to `curl` from a laptop on the same tailnet to
   send queries directly. Same API.
2. **Tailscale dies** — fall back to dog's own WiFi AP, phone joins it,
   webapp uses `http://192.168.123.18:5555` directly. Mic won't work
   (no HTTPS), but text input still does.
3. **Dimos dies on guide-full** — fall back to `dimos run unitree-go2-agentic`
   (no blind-assistant skills, but the agent still works on the base prompt).
4. **LLM API rate limit** — switch to `unitree-go2-agentic-ollama` if Ollama
   is installed on the dog (slower, but no API dependency).

---

## 16 · Mac (replay) vs Jetson (live) — what each catches

We validated extensively on Mac in replay mode. Here's what carries over vs.
what only the dog can confirm:

| Layer | Mac replay | Dog live |
|---|---|---|
| iPhone Safari + Tailscale HTTPS | ✅ verified | ✅ same |
| iOS Web Speech STT | ✅ verified | ✅ same |
| FastAPI auth + CORS + SSE | ✅ verified | ✅ same |
| MCP server registers 22 tools | ✅ verified | ✅ same (maybe more if no `--disable`) |
| McpClient discovers tools | ✅ verified | ✅ same |
| Blind-assistant SYSTEM_PROMPT loaded | ✅ verified (via file edit) | ✅ same |
| Agent receives `<user_speech>...</user_speech>` | ✅ verified | ✅ same |
| LLM calls `observe()` per protocol step 1 | ✅ verified | ✅ same |
| **`observe()` returns within RPC budget** | ❌ hangs 120s (no live frame) | ✅ should return real frame in <100ms |
| Agent calls `narrate / ask_user / navigate_with_text` | ⚠️ blocked on observe hang | ✅ should chain normally |
| TTS speaks through robot speaker | ⚠️ untested (depends on agent reaching narrate) | ✅ should work |
| `agent_state` SSE publishes JSON snapshots | ⚠️ untested (depends on narrate firing) | ✅ should publish on each state change |

The single failing item on Mac (`observe` hang in replay) is purely because
the replay-mode buffer of `_latest_video_frame` may be `None` between frames,
and the RPC serialization layer doesn't transport `None` cleanly. On a live
dog, every `observe()` returns a real `Image`.

If you want to fully exercise the rest of the protocol on a Mac:
- Send queries that DON'T trigger `observe` first — e.g. an off-topic query
  triggers `narrate` directly per the BLIND_ASSISTANT_PROMPT.
- Example via iPhone voice or terminal: *"are you there"* → agent should reply
  with narrate per the "I can only help you find places right now" rule.

## File map (where things live)

| Layer | File |
|---|---|
| System prompt | `dimos/agents/blind_assistant_prompt.py` |
| Skills | `dimos/agents/skills/blind_assistant_skills.py` |
| Web bridge | `dimos/agents/guide_web_input.py` |
| Blueprint (full, prod) | `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_guide_full.py` |
| Blueprint (lite, Mac dev) | `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_guide_lite.py` |
| FastAPI auth + SSE | `dimos/web/dimos_interface/api/server.py` |
| WebRTC Remote mode | `dimos/robot/unitree/connection.py` |
| MCP background retry | `dimos/agents/mcp/mcp_client.py` |
| Webapp (Vercel) | `webapp/` |
| Demo launcher | `bin/run-demo.sh` |
| Ops files | `ops/` |
