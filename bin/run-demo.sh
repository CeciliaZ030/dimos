#!/usr/bin/env bash
# Boot the guide-lite demo: dimos + webapp + tailscale serve.
# Retries dimos until the MCP race resolves (tools register within 30s).
#
# Usage:  bin/run-demo.sh
# Stop:   bin/run-demo.sh stop

set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

BLUEPRINT="${BLUEPRINT:-unitree-go2-guide-lite}"
DIMOS_PORT="${DIMOS_PORT:-5555}"
WEBAPP_PORT="${WEBAPP_PORT:-3000}"
TAILNET_HTTPS_DIMOS="${TAILNET_HTTPS_DIMOS:-8443}"
TAILNET_HTTPS_WEBAPP="${TAILNET_HTTPS_WEBAPP:-443}"
TOOL_REGISTRATION_TIMEOUT="${TOOL_REGISTRATION_TIMEOUT:-30}"
MAX_RETRIES="${MAX_RETRIES:-10}"

RUN_DIR="${RUN_DIR:-/tmp/dimos_run}"
mkdir -p "$RUN_DIR"
DIMOS_LOG="$RUN_DIR/dimos.log"
WEBAPP_LOG="$RUN_DIR/webapp.log"
TOKEN_FILE="$RUN_DIR/api_token"

red()   { printf "\033[31m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
amber() { printf "\033[33m%s\033[0m\n" "$*"; }
blue()  { printf "\033[34m%s\033[0m\n" "$*"; }

stop_all() {
  amber "stopping demo..."
  pkill -f "dimos.*run $BLUEPRINT" 2>/dev/null || true
  pkill -f "next dev" 2>/dev/null || true
  tailscale serve --https="$TAILNET_HTTPS_DIMOS" off 2>/dev/null || true
  tailscale serve --https="$TAILNET_HTTPS_WEBAPP" off 2>/dev/null || true
  green "stopped."
}

if [[ "${1:-}" == "stop" ]]; then
  stop_all
  exit 0
fi

# ---- 0) token
if [[ ! -s "$TOKEN_FILE" ]]; then
  openssl rand -hex 16 > "$TOKEN_FILE"
fi
TOKEN="$(cat "$TOKEN_FILE")"
blue "API token: $TOKEN  (file: $TOKEN_FILE)"

# ---- 1) preflight: required env, ports free, sudoers for route
command -v dimos >/dev/null 2>&1 || command -v .venv/bin/dimos >/dev/null 2>&1 \
  || { red "dimos CLI not found — activate venv or install"; exit 1; }
DIMOS_BIN="$(command -v dimos 2>/dev/null || echo .venv/bin/dimos)"

[[ -n "${OPENAI_API_KEY:-}" ]] || { red "OPENAI_API_KEY not set"; exit 1; }

if sudo -n route -h >/dev/null 2>&1; then
  : # NOPASSWD configured
elif sudo -n true 2>/dev/null; then
  : # cached sudo
else
  amber "sudo may prompt for route configuration"
fi

# ---- 2) boot dimos in a retry loop until tools register
blue "booting dimos ($BLUEPRINT)..."
for attempt in $(seq 1 "$MAX_RETRIES"); do
  pkill -f "dimos.*run $BLUEPRINT" 2>/dev/null || true
  sleep 2

  nohup env DIMOS_API_TOKEN="$TOKEN" "$DIMOS_BIN" --replay run "$BLUEPRINT" \
    > "$DIMOS_LOG" 2>&1 &
  DIMOS_PID=$!
  echo "$DIMOS_PID" > "$RUN_DIR/dimos.pid"

  amber "  attempt $attempt: pid=$DIMOS_PID  watching for 'Discovered tools'..."

  start=$SECONDS
  ok=""
  while (( SECONDS - start < TOOL_REGISTRATION_TIMEOUT )); do
    if ! kill -0 "$DIMOS_PID" 2>/dev/null; then
      red "  pid $DIMOS_PID died — see $DIMOS_LOG"
      break
    fi
    if grep -q "Discovered tools from MCP server" "$DIMOS_LOG" 2>/dev/null; then
      n=$(grep "Discovered tools from MCP server" "$DIMOS_LOG" | tail -1 \
        | sed -E 's/.*n_tools=([0-9]+).*/\1/')
      if [[ -n "$n" && "$n" -gt 0 ]]; then
        green "  ✓ tools registered (n_tools=$n) on attempt $attempt"
        ok=1
        break
      fi
    fi
    sleep 1
  done

  if [[ "$ok" == "1" ]]; then
    break
  fi
  amber "  attempt $attempt failed — restarting"
done

if [[ "$ok" != "1" ]]; then
  red "dimos never registered tools after $MAX_RETRIES attempts. Check $DIMOS_LOG"
  exit 1
fi

# ---- 3) bring up tailscale serve (idempotent)
blue "configuring tailscale serve..."
TAILNET_HOST="$(tailscale status --self --json 2>/dev/null \
  | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["Self"]["DNSName"].rstrip("."))' 2>/dev/null \
  || echo "$(hostname)")"

# serve dimos backend on :8443, webapp on :443
tailscale serve --https="$TAILNET_HTTPS_DIMOS"  "$DIMOS_PORT"  >/dev/null 2>&1 || true
tailscale serve --bg --https="$TAILNET_HTTPS_DIMOS"  "$DIMOS_PORT"  >/dev/null 2>&1 || true
tailscale serve --https="$TAILNET_HTTPS_WEBAPP" "$WEBAPP_PORT" >/dev/null 2>&1 || true
tailscale serve --bg --https="$TAILNET_HTTPS_WEBAPP" "$WEBAPP_PORT" >/dev/null 2>&1 || true

API_URL="https://${TAILNET_HOST}:${TAILNET_HTTPS_DIMOS}"
WEBAPP_URL="https://${TAILNET_HOST}"

# ---- 4) write webapp env + boot dev server if not already running
if ! lsof -nP -iTCP:"$WEBAPP_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  blue "starting webapp dev server..."
  cat > webapp/.env.local <<EOF
NEXT_PUBLIC_DIMOS_API=$API_URL
NEXT_PUBLIC_DIMOS_TOKEN=$TOKEN
EOF
  ( cd webapp && nohup npm run dev > "$WEBAPP_LOG" 2>&1 & echo $! > "$RUN_DIR/webapp.pid" )
  disown
  start=$SECONDS
  while (( SECONDS - start < 30 )); do
    grep -q "Ready in" "$WEBAPP_LOG" 2>/dev/null && break
    sleep 1
  done
fi

# ---- 5) sanity probe
TOKEN="$(cat "$TOKEN_FILE")"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY
streams=$(curl -fsS -H "Authorization: Bearer $TOKEN" "$API_URL/text_streams" 2>/dev/null || echo "FAIL")
if [[ "$streams" == *"agent_state"* ]]; then
  green "  ✓ dimos reachable via tailnet HTTPS"
else
  amber "  ! could not reach $API_URL/text_streams — check tailscale"
fi

# ---- 6) print connection details
echo
green "===================== READY ====================="
echo "  webapp:  $WEBAPP_URL"
echo "  api:     $API_URL"
echo "  token:   $TOKEN"
echo "  log:     $DIMOS_LOG"
echo "  pid:     $(cat "$RUN_DIR/dimos.pid")"
echo
echo "  open the webapp URL in iPhone Safari (Tailscale ON)"
echo "  stop with: bin/run-demo.sh stop"
echo "  tail logs: tail -f $DIMOS_LOG"
green "================================================="
