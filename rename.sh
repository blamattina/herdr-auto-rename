#!/usr/bin/env bash
set -euo pipefail

HERDR="${HERDR_BIN_PATH:-herdr}"
PLUGIN_ROOT="${HERDR_PLUGIN_ROOT:-.}"
STATE_DIR="${HERDR_PLUGIN_STATE_DIR:-/tmp/herdr-auto-rename-state}"
mkdir -p "$STATE_DIR"

GENERATOR="command claude --print"
CONTEXT_LINES=40
DELAY_SECONDS=5

CONFIG="${PLUGIN_ROOT}/config.toml"
if [ -f "$CONFIG" ]; then
  val=$(awk -F'=' '/^generator[[:space:]]*=/{gsub(/^[[:space:]"]+|[[:space:]"]+$/,"",$2); print $2}' "$CONFIG" | head -1)
  [ -n "$val" ] && GENERATOR="$val"
  val=$(awk -F'=' '/^context_lines[[:space:]]*=/{gsub(/[[:space:]]/,"",$2); print $2}' "$CONFIG" | head -1)
  [ -n "$val" ] && CONTEXT_LINES="$val"
  val=$(awk -F'=' '/^delay_seconds[[:space:]]*=/{gsub(/[[:space:]]/,"",$2); print $2}' "$CONFIG" | head -1)
  [ -n "$val" ] && DELAY_SECONDS="$val"
fi

EVENT_JSON="${HERDR_PLUGIN_EVENT_JSON:-}"
[ -z "$EVENT_JSON" ] && exit 0

pane_id=$(printf '%s' "$EVENT_JSON" | python3 -c \
  'import sys,json; d=json.load(sys.stdin); print(d.get("data",{}).get("pane_id",""))' 2>/dev/null || true)
workspace_id=$(printf '%s' "$EVENT_JSON" | python3 -c \
  'import sys,json; d=json.load(sys.stdin); print(d.get("data",{}).get("workspace_id",""))' 2>/dev/null || true)
agent_status=$(printf '%s' "$EVENT_JSON" | python3 -c \
  'import sys,json; d=json.load(sys.stdin); print(d.get("data",{}).get("agent_status",""))' 2>/dev/null || true)

[ -z "$pane_id" ] && exit 0
[ "$agent_status" != "working" ] && exit 0

sleep "$DELAY_SECONDS"

pane_output=$("$HERDR" pane read "$pane_id" --source recent-unwrapped --lines "$CONTEXT_LINES" 2>/dev/null || true)
[ -z "$pane_output" ] && exit 0

# Agent name: evolves every working transition — what is it doing right now
agent_prompt="In 3-5 words, label what this coding agent is doing right now. Output ONLY the label — no punctuation, no quotes, no explanation:

${pane_output}"

agent_name=$(bash -c "$GENERATOR \"\$1\"" -- "$agent_prompt" 2>/dev/null | head -1 | tr -d "\"'" | sed 's/^[[:space:]]*//' | cut -c1-40 || true)
[ -n "$agent_name" ] && "$HERDR" agent rename "$pane_id" "$agent_name" 2>/dev/null || true

# Workspace name: set once — what is this session for overall
safe_id="${pane_id//:/--}"
ws_lock="${STATE_DIR}/workspace-renamed-${safe_id}"
if [ ! -f "$ws_lock" ] && [ -n "$workspace_id" ]; then
  touch "$ws_lock"
  ws_prompt="In 3-5 words, describe the overall goal of this coding session. Output ONLY the label — no punctuation, no quotes, no explanation:

${pane_output}"

  ws_name=$(bash -c "$GENERATOR \"\$1\"" -- "$ws_prompt" 2>/dev/null | head -1 | tr -d "\"'" | sed 's/^[[:space:]]*//' | cut -c1-40 || true)
  [ -n "$ws_name" ] && "$HERDR" workspace rename "$workspace_id" "$ws_name" 2>/dev/null || true
fi
