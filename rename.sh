#!/usr/bin/env bash
set -euo pipefail

HERDR="${HERDR_BIN_PATH:-herdr}"
PLUGIN_ROOT="${HERDR_PLUGIN_ROOT:-.}"
CONFIG_DIR="${HERDR_PLUGIN_CONFIG_DIR:-$PLUGIN_ROOT}"
STATE_DIR="${HERDR_PLUGIN_STATE_DIR:-/tmp/herdr-auto-rename-state}"
mkdir -p "$STATE_DIR"

GENERATOR="command claude --print"
CONTEXT_LINES=40
DELAY_SECONDS=5
MAX_LABEL_LENGTH=24

# Prefer user config in the durable HERDR_PLUGIN_CONFIG_DIR (survives plugin
# updates); fall back to the default config.toml bundled in the plugin repo.
CONFIG="${CONFIG_DIR}/config.toml"
[ -f "$CONFIG" ] || CONFIG="${PLUGIN_ROOT}/config.toml"
if [ -f "$CONFIG" ]; then
  val=$(awk -F'=' '/^generator[[:space:]]*=/{gsub(/^[[:space:]"]+|[[:space:]"]+$/,"",$2); print $2}' "$CONFIG" | head -1)
  [ -n "$val" ] && GENERATOR="$val"
  val=$(awk -F'=' '/^context_lines[[:space:]]*=/{gsub(/[[:space:]]/,"",$2); print $2}' "$CONFIG" | head -1)
  [ -n "$val" ] && CONTEXT_LINES="$val"
  val=$(awk -F'=' '/^delay_seconds[[:space:]]*=/{gsub(/[[:space:]]/,"",$2); print $2}' "$CONFIG" | head -1)
  [ -n "$val" ] && DELAY_SECONDS="$val"
  val=$(awk -F'=' '/^max_label_length[[:space:]]*=/{gsub(/[[:space:]]/,"",$2); print $2}' "$CONFIG" | head -1)
  [ -n "$val" ] && MAX_LABEL_LENGTH="$val"
fi

# Run the generator on a prompt and return a single cleaned, length-capped label.
generate() {
  bash -c "$GENERATOR \"\$1\"" -- "$1" 2>/dev/null \
    | head -1 | tr -d "\"'" | sed 's/^[[:space:]]*//' | cut -c1-"${MAX_LABEL_LENGTH}" || true
}

EVENT_JSON="${HERDR_PLUGIN_EVENT_JSON:-}"
[ -z "$EVENT_JSON" ] && exit 0

pane_id=$(printf '%s' "$EVENT_JSON" | python3 -c \
  'import sys,json; d=json.load(sys.stdin); print(d.get("data",{}).get("pane_id",""))' 2>/dev/null || true)
workspace_id=$(printf '%s' "$EVENT_JSON" | python3 -c \
  'import sys,json; d=json.load(sys.stdin); print(d.get("data",{}).get("workspace_id",""))' 2>/dev/null || true)
tab_id=$(printf '%s' "$EVENT_JSON" | python3 -c \
  'import sys,json; d=json.load(sys.stdin); print(d.get("data",{}).get("tab_id",""))' 2>/dev/null || true)
agent_status=$(printf '%s' "$EVENT_JSON" | python3 -c \
  'import sys,json; d=json.load(sys.stdin); print(d.get("data",{}).get("agent_status",""))' 2>/dev/null || true)

[ -z "$pane_id" ] && exit 0
[ "$agent_status" != "working" ] && exit 0

sleep "$DELAY_SECONDS"

pane_output=$("$HERDR" pane read "$pane_id" --source recent-unwrapped --lines "$CONTEXT_LINES" 2>/dev/null || true)
[ -z "$pane_output" ] && exit 0

# Agent name: the moment-to-moment action, refreshed on every working transition.
agent_prompt="Write a 2-4 word, verb-first label for what this coding agent is doing at this exact moment — the immediate action, not the broader task. Start with a present-participle verb (e.g. 'Editing rename.sh', 'Running tests', 'Reading config'). Capitalize only the first word; keep acronyms uppercase. Stay under ${MAX_LABEL_LENGTH} characters. Output ONLY the label — no punctuation, no quotes, no explanation:

${pane_output}"

agent_name=$(generate "$agent_prompt")
[ -n "$agent_name" ] && "$HERDR" agent rename "$pane_id" "$agent_name" 2>/dev/null || true

# Tab name: the agent's current task (the unit of work, broader than the action).
# Track the label we set so it stays current without ever clobbering a name you
# chose yourself — we only touch a default numeric label or our own prior value.
[ -z "$tab_id" ] && tab_id=$("$HERDR" pane get "$pane_id" 2>/dev/null | python3 -c \
  'import sys,json; print(json.load(sys.stdin).get("result",{}).get("pane",{}).get("tab_id",""))' 2>/dev/null || true)
if [ -n "$tab_id" ]; then
  tab_state="${STATE_DIR}/tab-name-${tab_id//:/--}"
  tab_label=$("$HERDR" tab get "$tab_id" 2>/dev/null | python3 -c \
    'import sys,json; print(json.load(sys.stdin).get("result",{}).get("tab",{}).get("label",""))' 2>/dev/null || true)
  tab_prev=$(cat "$tab_state" 2>/dev/null || true)
  case "$tab_label" in
    *[!0-9]*|'') is_default=no ;;  # non-numeric (or empty) is not a default label
    *) is_default=yes ;;
  esac
  if [ "$is_default" = yes ] || [ "$tab_label" = "$tab_prev" ]; then
    tab_prompt="Write a 2-4 word, verb-first label for the specific task this coding agent is currently working on — the unit of work, broader than its moment-to-moment action but narrower than the user's overall goal (e.g. 'Tuning label prompts', 'Fixing Java env', 'Adding auth flow'). Capitalize only the first word; keep acronyms uppercase. Stay under ${MAX_LABEL_LENGTH} characters. Output ONLY the label — no punctuation, no quotes, no explanation:

${pane_output}"
    tab_name=$(generate "$tab_prompt")
    if [ -n "$tab_name" ]; then
      "$HERDR" tab rename "$tab_id" "$tab_name" 2>/dev/null || true
      printf '%s' "$tab_name" > "$tab_state"
    fi
  fi
fi

# Workspace name: the user's overall task/intent. Set once per pane.
ws_lock="${STATE_DIR}/workspace-renamed-${pane_id//:/--}"
if [ ! -f "$ws_lock" ] && [ -n "$workspace_id" ]; then
  touch "$ws_lock"
  ws_prompt="Based on what the user has asked for in this session, write a 2-4 word label for the user's overall goal — what they are ultimately trying to accomplish, inferred from their requests, not the agent's current activity (e.g. 'Build herdr plugin', 'Fix CI pipeline'). Capitalize only the first word; keep acronyms uppercase. Stay under ${MAX_LABEL_LENGTH} characters. Output ONLY the label — no punctuation, no quotes, no explanation:

${pane_output}"

  ws_name=$(generate "$ws_prompt")
  [ -n "$ws_name" ] && "$HERDR" workspace rename "$workspace_id" "$ws_name" 2>/dev/null || true
fi
