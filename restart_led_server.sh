#!/usr/bin/env bash
set -euo pipefail

# =========================
# Config — tweak if needed
# =========================
APP_DIR="${APP_DIR:-$HOME/Documents/LEDMatrix/led_text_app}"
SERVICE_ID="${SERVICE_ID:-com.led.scroller}"

# If you're running gunicorn manually (not via launchctl), set these:
VENV_BIN="${VENV_BIN:-$APP_DIR/.venv/bin}"      # path to your venv's bin
GUNICORN_BIN="${GUNICORN_BIN:-$VENV_BIN/gunicorn}"
GUNICORN_BIND="${GUNICORN_BIND:-0.0.0.0:5070}"  # prod HTTP bind for gunicorn

# Flask dev server (only used for graceful /stop)
DEV_HOST="${DEV_HOST:-127.0.0.1}"
DEV_PORT="${DEV_PORT:-5050}"     # dev server port (when app.py is run directly)
PROD_PORT="${PROD_PORT:-5070}"   # gunicorn port (when running in production)

# WSGI target
WSGI_APP="${WSGI_APP:-app:app}"  # module:app for gunicorn

# launchctl plist location (what you used when you created it)
PLIST_PATH="${PLIST_PATH:-$HOME/Library/LaunchAgents/$SERVICE_ID.plist}"

# How long to wait for graceful stops
GRACE_TIMEOUT="${GRACE_TIMEOUT:-5}"

# =========================
# Helpers
# =========================
have_cmd() { command -v "$1" >/dev/null 2>&1; }

info()  { echo "[$(date +'%H:%M:%S')] $*"; }
warn()  { echo "[$(date +'%H:%M:%S')] WARN: $*" >&2; }
error() { echo "[$(date +'%H:%M:%S')] ERROR: $*" >&2; exit 1; }

port_in_use() {
  local port="$1"
  if have_cmd lsof; then
    lsof -iTCP:"$port" -sTCP:LISTEN -n -P >/dev/null 2>&1
  else
    # Fallback check with nc (may not be installed)
    nc -z "$DEV_HOST" "$port" >/dev/null 2>&1
  fi
}

pids_on_port() {
  local port="$1"
  if have_cmd lsof; then
    lsof -ti tcp:"$port" -sTCP:LISTEN || true
  else
    # No reliable fallback without lsof; return empty
    true
  fi
}

curl_stop() {
  local port="$1"
  if ! have_cmd curl; then
    warn "curl not found; skipping graceful /stop on port $port"
    return 1
  fi
  info "Attempting graceful stop via /stop on port $port ..."
  curl -m 2 -fsS -X POST "http://$DEV_HOST:$port/stop" >/dev/null || return 1
  sleep 1
  return 0
}

launchctl_loaded() {
  launchctl print "gui/$UID/$SERVICE_ID" >/dev/null 2>&1
}

stop_launchctl() {
  if launchctl_loaded; then
    info "Stopping launchctl service: $SERVICE_ID"
    # bootout unloads the job
    launchctl bootout "gui/$UID/$SERVICE_ID" >/dev/null 2>&1 || true
    sleep 1
  else
    info "launchctl service not loaded: $SERVICE_ID"
  fi
}

start_launchctl() {
  # bootstrap loads (and starts) from the plist
  if [[ ! -f "$PLIST_PATH" ]]; then
    error "Plist not found at $PLIST_PATH. Set PLIST_PATH or create the plist first."
  fi
  info "Starting launchctl service from $PLIST_PATH"
  launchctl bootstrap "gui/$UID" "$PLIST_PATH"
  # force (re)start
  launchctl kickstart -k "gui/$UID/$SERVICE_ID" >/dev/null 2>&1 || true
}

restart_launchctl() {
  stop_launchctl
  start_launchctl
}

stop_gunicorn() {
  local port="$1"
  local pids
  pids="$(pids_on_port "$port")"
  if [[ -z "${pids:-}" ]]; then
    info "No gunicorn process listening on port $port"
    return 0
  fi
  info "Stopping gunicorn (port $port), PIDs: $pids"
  # Try graceful TERM
  kill $pids || true

  local waited=0
  while [[ $waited -lt $GRACE_TIMEOUT ]]; do
    sleep 1; waited=$((waited+1))
    if [[ -z "$(pids_on_port "$port")" ]]; then
      info "gunicorn stopped"
      return 0
    fi
  done

  warn "gunicorn still running after $GRACE_TIMEOUTs; sending KILL"
  kill -9 $pids || true
  return 0
}

start_gunicorn() {
  [[ -x "$GUNICORN_BIN" ]] || error "gunicorn not found at $GUNICORN_BIN — set GUNICORN_BIN or VENV_BIN"

  info "Starting gunicorn: $WSGI_APP on $GUNICORN_BIND"
  (cd "$APP_DIR" && exec "$GUNICORN_BIN" -w 2 -b "$GUNICORN_BIND" "$WSGI_APP" --daemon)
  sleep 1
  if port_in_use "${GUNICORN_BIND##*:}"; then
    info "gunicorn is up on $GUNICORN_BIND"
  else
    warn "gunicorn did not start on $GUNICORN_BIND — check logs"
  fi
}

graceful_stop_any() {
  # Try both ports, in case you’re running dev or prod
  local ok=1
  if port_in_use "$DEV_PORT"; then
    curl_stop "$DEV_PORT" && ok=0 || true
  fi
  if port_in_use "$PROD_PORT"; then
    curl_stop "$PROD_PORT" && ok=0 || true
  fi
  return $ok
}

# =========================
# Commands
# =========================
cmd="${1:-restart}"

case "$cmd" in
  stop)
    info "Stopping server …"
    graceful_stop_any || info "Graceful stop failed or not running; proceeding to hard stop."
    if launchctl_loaded; then
      stop_launchctl
    else
      stop_gunicorn "$PROD_PORT"
      # also try dev port (if you started Flask directly)
      if port_in_use "$DEV_PORT"; then
        warn "Dev server is still listening on $DEV_PORT; you may need to Ctrl-C the foreground process."
      fi
    fi
    info "Stopped."
    ;;

  start)
    info "Starting server …"
    if [[ -f "$PLIST_PATH" ]]; then
      start_launchctl
    else
      start_gunicorn
    fi
    ;;

  restart|reload)
    info "Restarting server …"
    # Step 1: graceful
    graceful_stop_any || true

    # Step 2: stop underlying process/service
    if [[ -f "$PLIST_PATH" ]]; then
      restart_launchctl
    else
      stop_gunicorn "$PROD_PORT"
      start_gunicorn
    fi

    info "Restart complete."
    ;;

  status)
    if launchctl_loaded; then
      info "launchctl: $SERVICE_ID is loaded"
    else
      info "launchctl: $SERVICE_ID is NOT loaded"
    fi
    if port_in_use "$PROD_PORT"; then
      info "Port $PROD_PORT: LISTENING"
    else
      info "Port $PROD_PORT: not listening"
    fi
    if port_in_use "$DEV_PORT"; then
      info "Port $DEV_PORT: LISTENING (dev server?)"
    fi
    ;;

  *)
    cat <<EOF
Usage: $(basename "$0") [start|stop|restart|reload|status]

Environment overrides:
  APP_DIR           (default: $APP_DIR)
  SERVICE_ID        (default: $SERVICE_ID)
  PLIST_PATH        (default: $PLIST_PATH)
  VENV_BIN          (default: $VENV_BIN)
  GUNICORN_BIN      (default: $GUNICORN_BIN)
  GUNICORN_BIND     (default: $GUNICORN_BIND)
  DEV_PORT          (default: $DEV_PORT)
  PROD_PORT         (default: $PROD_PORT)
  WSGI_APP          (default: $WSGI_APP)
EOF
    exit 2
    ;;
esac

