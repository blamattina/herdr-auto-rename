#!/usr/bin/env python3
"""herdr auto-rename: label the agent, its tab, and its workspace.

Hooked to herdr's pane.agent_status_changed event. It parses the running agent's
own transcript (Claude / Codex / Pi JSONL — falling back to terminal scrollback
for other agents) into clean role-tagged messages, then names three things at
different altitudes:

  agent  — the moment-to-moment action
  tab    — the agent's current task
  workspace — the user's overall goal

Naming is throttled (min interval + conversation growth + in-flight guard) and
stable (it's shown the current label and keeps it when still accurate), so labels
stay calm and cheap rather than churning on every turn.
"""
import glob
import json
import os
import re
import subprocess
import time

HOME = os.path.expanduser("~")
HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")
PLUGIN_ROOT = os.environ.get("HERDR_PLUGIN_ROOT", ".")
CONFIG_DIR = os.environ.get("HERDR_PLUGIN_CONFIG_DIR", PLUGIN_ROOT)
STATE_DIR = os.environ.get("HERDR_PLUGIN_STATE_DIR", "/tmp/herdr-auto-rename-state")

DEFAULTS = {
    "generator": "command claude --print --no-session-persistence",
    "context_lines": 40,          # pane-read fallback window
    "delay_seconds": 2,           # let the transcript flush after the transition
    "max_label_length": 24,
    "min_interval_seconds": 60,   # throttle: min seconds between passes per pane
    "min_growth": 4,              # throttle: min new messages since last pass
    "min_messages": 2,            # don't name a barely-started session
    "head_user_messages": 2,      # context: first N user messages (anchor goal)
    "tail_messages": 6,           # context: last N messages (current topic)
    "message_max_chars": 600,     # per-message excerpt cap
}

# A label is verb-first ("Fixing", "Add"); prose opens with a function word.
STOPWORD_OPENERS = {
    "a", "an", "the", "this", "that", "these", "those", "its", "it", "my",
    "our", "your", "their", "his", "her", "i", "im", "ive", "id", "ill", "you",
    "youre", "youve", "we", "were", "weve", "they", "theyre", "theyve", "he",
    "she", "them", "and", "or", "but", "so", "because", "since", "while", "when",
    "after", "before", "although", "though", "if", "unless", "until", "whether",
    "as", "based", "given", "currently", "now", "then", "here", "there", "also",
    "however", "meanwhile", "first", "next", "finally", "just", "still",
    "actually", "basically", "essentially", "overall", "instead", "maybe",
    "perhaps", "sure", "okay", "ok", "well", "hmm", "sorry", "yes", "no", "yeah",
    "looks", "seems", "appears", "lets", "let", "please", "thanks", "heres",
    "theres", "whats", "thats", "dont", "cant", "wont", "is", "are", "was",
    "will", "would", "can", "could", "should", "may", "might", "must", "do",
    "does", "did", "has", "have", "had",
}


# ---------------------------------------------------------------- config / io
def load_config():
    cfg = dict(DEFAULTS)
    path = os.path.join(CONFIG_DIR, "config.toml")
    if not os.path.isfile(path):
        path = os.path.join(PLUGIN_ROOT, "config.toml")
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key not in cfg:
                    continue
                value = value.strip().strip('"').strip("'").strip()
                if isinstance(DEFAULTS[key], int):
                    try:
                        cfg[key] = int(value)
                    except ValueError:
                        pass
                elif value:
                    cfg[key] = value
    except OSError:
        pass
    return cfg


def read_json_file(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def write_json_file(path, obj):
    try:
        with open(path, "w") as fh:
            json.dump(obj, fh)
    except OSError:
        pass


def herdr(*args):
    try:
        return subprocess.run(
            [HERDR, *args], capture_output=True, text=True, check=False
        ).stdout
    except OSError:
        return ""


def herdr_json(*args):
    try:
        return json.loads(herdr(*args) or "{}")
    except json.JSONDecodeError:
        return {}


# ------------------------------------------------------- transcript discovery
def transcript_candidates(agent):
    if agent == "claude":
        return glob.glob(os.path.join(HOME, ".claude/projects/*/*.jsonl"))
    if agent == "codex":
        return glob.glob(os.path.join(HOME, ".codex/sessions/**/rollout-*.jsonl"),
                         recursive=True)
    if agent == "pi":
        return glob.glob(os.path.join(HOME, ".pi/agent/sessions/*/*.jsonl"))
    return []


def transcript_cwd(path):
    """All three formats record the launch cwd near the top, top-level or under
    a `payload` (codex). Scan the first lines until we find it."""
    try:
        with open(path) as fh:
            for i, line in enumerate(fh):
                if i > 30:
                    break
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj.get("cwd"), str):
                    return obj["cwd"]
                payload = obj.get("payload")
                if isinstance(payload, dict) and isinstance(payload.get("cwd"), str):
                    return payload["cwd"]
    except OSError:
        pass
    return None


def find_transcript(agent, cwd, max_age=6 * 3600):
    """Most-recently-modified session file whose recorded cwd matches the pane,
    among files touched in the last few hours."""
    now = time.time()
    scored = []
    for path in transcript_candidates(agent):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if now - mtime > max_age:
            continue
        scored.append((mtime, path))
    scored.sort(reverse=True)
    for _, path in scored:
        if transcript_cwd(path) == cwd:
            return path
    return None


def _flatten_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text") or block.get("input_text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def extract_messages(agent, path):
    """Role-tagged (role, text) messages, skipping tool calls/results, thinking
    blocks, and framework-injected context wrapped in angle-bracket tags."""
    messages = []
    try:
        fh = open(path)
    except OSError:
        return messages
    with fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if agent == "codex":
                if obj.get("type") != "response_item":
                    continue
                payload = obj.get("payload", {})
                if not isinstance(payload, dict) or payload.get("type") != "message":
                    continue
                role, content = payload.get("role"), payload.get("content")
            else:  # claude, pi
                kind = obj.get("type")
                if agent == "claude" and kind not in ("user", "assistant"):
                    continue
                if agent == "pi" and kind != "message":
                    continue
                message = obj.get("message", {})
                if not isinstance(message, dict):
                    continue
                role, content = message.get("role"), message.get("content")
            if role not in ("user", "assistant"):
                continue
            text = _flatten_content(content).strip()
            if not text or (text.startswith("<") and ">" in text[:200]):
                continue
            messages.append((role, text))
    return messages


def build_context(messages, cfg):
    """First user messages anchor the goal; trailing messages capture the current
    topic. Deduped and per-message truncated."""
    head = [m for m in messages if m[0] == "user"][:cfg["head_user_messages"]]
    tail = messages[-cfg["tail_messages"]:] if cfg["tail_messages"] else []
    seen = set()
    parts = []
    for role, text in head + tail:
        excerpt = text[:cfg["message_max_chars"]]
        key = role + ":" + excerpt
        if key in seen:
            continue
        seen.add(key)
        parts.append("{}: {}".format(role, excerpt))
    return "\n".join(parts)


def clean_pane(text, context_lines):
    """Fallback context: strip ANSI, box-drawing, and TUI markers from scrollback."""
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[─━│┃╭╮╰╯┌┐└┘├┤┬┴┼]+", "", line)
        line = re.sub(r"^[\s⏵›❯⎿⏺✻※•│]+", "", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines[-context_lines:])


# ----------------------------------------------------------------- generation
def sanitize(label):
    if not label or ". " in label:
        return ""
    words = label.split()
    if len(words) > 5:
        return ""
    if words[0].lower().strip(",.:;!?'\"") in STOPWORD_OPENERS:
        return ""
    return label


def generate(prompt, generator, max_len):
    try:
        result = subprocess.run(
            ["bash", "-c", '{} "$1"'.format(generator), "--", prompt],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return ""
    lines = result.stdout.splitlines()
    first = lines[0] if lines else ""
    first = re.sub(r"\s+", " ", first.replace('"', "").replace("'", "")).strip()
    return sanitize(first)[:max_len]


def build_prompt(role_desc, examples, current, context, max_len):
    lines = [
        "You name a {} for a developer running coding agents.".format(role_desc),
        "From the conversation excerpt, output ONLY a 2-4 word, verb-first label "
        "(e.g. {}).".format(examples),
        "Capitalize only the first word; keep acronyms uppercase. Stay under {} "
        "characters. No quotes, no punctuation, no explanation.".format(max_len),
    ]
    if current:
        lines.append("The current label is: {}".format(current))
        lines.append("If it still fits, reply with it EXACTLY.")
    lines.append("")
    lines.append("Conversation excerpt:")
    lines.append(context)
    return "\n".join(lines)


def relabel(role_desc, examples, current, context, cfg):
    """Generate a label and return it only if it's a usable change."""
    name = generate(
        build_prompt(role_desc, examples, current, context, cfg["max_label_length"]),
        cfg["generator"], cfg["max_label_length"],
    )
    if not name or name == current:
        return ""
    return name


# ----------------------------------------------------------------------- main
def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    cfg = load_config()

    try:
        data = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON", "")).get("data", {})
    except json.JSONDecodeError:
        return
    pane_id = data.get("pane_id", "")
    if not pane_id or data.get("agent_status", "") != "working":
        return

    pane_state_path = os.path.join(STATE_DIR, "pane-" + pane_id.replace(":", "--") + ".json")
    state = read_json_file(pane_state_path, {})
    now = time.time()

    # Throttle: skip if a pass is in flight or the interval hasn't elapsed.
    expiry = cfg["min_interval_seconds"] + 60
    if state.get("in_flight") and now - state["in_flight"] < expiry:
        return
    if state.get("last_attempt") and now - state["last_attempt"] < cfg["min_interval_seconds"]:
        return

    time.sleep(cfg["delay_seconds"])

    pane = herdr_json("pane", "get", pane_id).get("result", {}).get("pane", {})
    agent = pane.get("agent", "")
    cwd = pane.get("cwd") or pane.get("foreground_cwd") or ""
    tab_id = data.get("tab_id", "") or pane.get("tab_id", "")
    workspace_id = data.get("workspace_id", "") or pane.get("workspace_id", "")

    # Build context from the agent transcript; fall back to cleaned scrollback.
    messages = []
    transcript = find_transcript(agent, cwd) if cwd else None
    if transcript:
        messages = extract_messages(agent, transcript)
    if messages:
        context = build_context(messages, cfg)
        progress = len(messages)
    else:
        context = clean_pane(
            herdr("pane", "read", pane_id, "--source", "recent-unwrapped",
                  "--lines", str(cfg["context_lines"])),
            cfg["context_lines"],
        )
        progress = context.count("\n") + 1 if context else 0

    if not context.strip() or progress < cfg["min_messages"]:
        return

    # Growth gate: after the first pass, require new conversation before renaming.
    if state.get("last_attempt") and progress - state.get("progress", 0) < cfg["min_growth"]:
        return

    state["in_flight"] = now
    write_json_file(pane_state_path, state)

    # Agent: the moment-to-moment action.
    agent_name = relabel(
        "coding agent by its current action",
        "'Editing rename.py', 'Running tests', 'Reading config'",
        pane.get("label", ""), context, cfg,
    )
    if agent_name:
        herdr("agent", "rename", pane_id, agent_name)

    # Tab: the agent's current task. Only touch a default numeric label or one we
    # set ourselves, so a name you chose is never clobbered.
    if tab_id:
        tab_state = os.path.join(STATE_DIR, "tab-name-" + tab_id.replace(":", "--"))
        tab_label = herdr_json("tab", "get", tab_id).get(
            "result", {}).get("tab", {}).get("label", "")
        tab_prev = _read_text(tab_state)
        if tab_label.isdigit() or tab_label == tab_prev:
            tab_name = relabel(
                "tab by the agent's current task",
                "'Tuning label prompts', 'Fixing Java env', 'Adding auth flow'",
                tab_label, context, cfg,
            )
            if tab_name:
                herdr("tab", "rename", tab_id, tab_name)
                _write_text(tab_state, tab_name)

    # Workspace: the user's overall goal. Named once per workspace.
    if workspace_id:
        ws_named = os.path.join(STATE_DIR, "ws-named-" + workspace_id.replace(":", "--"))
        if not os.path.exists(ws_named):
            ws_current = herdr_json("workspace", "get", workspace_id).get(
                "result", {}).get("workspace", {}).get("label", "")
            ws_name = relabel(
                "workspace by the user's overall goal, inferred from their requests",
                "'Build herdr plugin', 'Fix CI pipeline'",
                ws_current, context, cfg,
            )
            if ws_name:
                herdr("workspace", "rename", workspace_id, ws_name)
                _write_text(ws_named, ws_name)

    state.update(last_attempt=now, progress=progress, in_flight=0)
    write_json_file(pane_state_path, state)


def _read_text(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return ""


def _write_text(path, text):
    try:
        with open(path, "w") as fh:
            fh.write(text)
    except OSError:
        pass


if __name__ == "__main__":
    main()
