#!/usr/bin/env bash
set -euo pipefail

# --- Config (change if needed) ---
SERVICE_ID="${SERVICE_ID:-com.sjelinsky.led-scroller}"
PLIST_PATH="${PLIST_PATH:-$HOME/Library/LaunchAgents/$SERVICE_ID.plist}"
DOMAIN="gui/$UID"

# --- Helpers ---
info(){ printf "[%s] %s\n" "$(date +'%H:%M:%S')" "$*"; }
die(){ printf "ERROR: %s\n" "$*" >&2; exit 1; }

# --- Pre-flight ---
[[ -f "$PLIST_PATH" ]] || die "Plist not found at: $PLIST_PATH"

info "Using service: $DOMAIN/$SERVICE_ID"
info "Using plist:   $PLIST_PATH"

# --- Restart sequence ---
info "1) bootout (unload) any existing job…"
launchctl bootout "$DOMAIN/$SERVICE_ID" >/dev/null 2>&1 || true

info "2) bootstrap (load) from plist…"
launchctl bootstrap "$DOMAIN" "$PLIST_PATH"

info "3) enable the job…"
launchctl enable "$DOMAIN/$SERVICE_ID"

info "4) kickstart (restart) the job…"
launchctl kickstart -k "$DOMAIN/$SERVICE_ID"

# --- Status ---
echo
info "Status:"
if launchctl print "$DOMAIN/$SERVICE_ID" >/dev/null 2>&1; then
  info "✓ Loaded: $DOMAIN/$SERVICE_ID"
else
  die "Job does not appear loaded."
fi

