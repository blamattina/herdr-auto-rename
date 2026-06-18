#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="${HERDR_PLUGIN_STATE_DIR:-/tmp/herdr-auto-rename-state}"

EVENT_JSON="${HERDR_PLUGIN_EVENT_JSON:-}"
[ -z "$EVENT_JSON" ] && exit 0

pane_id=$(printf '%s' "$EVENT_JSON" | python3 -c \
  'import sys,json; d=json.load(sys.stdin); print(d.get("data",{}).get("pane_id",""))' 2>/dev/null || true)
[ -z "$pane_id" ] && exit 0

safe_id="${pane_id//:/--}"
rm -f "${STATE_DIR}/workspace-renamed-${safe_id}"
