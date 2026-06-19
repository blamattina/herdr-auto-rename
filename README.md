# herdr-auto-rename

A [herdr](https://herdr.dev) plugin that automatically names your agent panes,
tabs, and workspaces based on what the agent is actually doing — using a
coding-agent CLI to generate the labels.

Instead of a sidebar full of `worktree-quiet-stone-b6f5`, you get
`Debugging push failure`.

## How it works

The plugin hooks herdr's `pane.agent_status_changed` event. Each time an agent
transitions to `working`, it:

1. Reads the running agent's own **transcript** — Claude, Codex, and Pi JSONL are
   parsed into clean role-tagged messages (tool calls, thinking, and injected
   harness noise stripped). Other agents fall back to terminal scrollback.
2. Builds a context excerpt: the first user messages anchor the goal, the
   trailing messages capture the current topic.
3. Asks a configurable coding-agent CLI to summarize it.

It names three things at different altitudes:

4. The **agent** — what it's doing *right now*, the moment-to-moment action
5. The **tab** — the high-level *area* of the agent's current task, as a terse
   1-2 word topic. Only overwrites a default numeric label (`1`, `2`, `3`...) or a
   name it set itself, so tabs you've named yourself are left alone
6. The **workspace** — the *user's* overall goal, synthesized from the whole arc
   of your requests (not recent activity). Named once per workspace, and only once
   there are enough requests to infer a real goal — until then it keeps its
   repo/cwd default rather than locking in a thin or one-off opening task

Naming is **throttled** (a minimum interval plus a conversation-growth gate, with
an in-flight guard) and **stable** (the model is shown the current label and
keeps it when it still fits), so labels stay calm and cheap instead of churning
on every turn. Each label is truncated to `max_label_length`.

When a pane closes, a `pane.closed` hook clears its saved state.

## Install

```bash
herdr plugin install blamattina/herdr-auto-rename
```

Or link a local clone for development:

```bash
git clone https://github.com/blamattina/herdr-auto-rename.git
herdr plugin link ./herdr-auto-rename
```

Then reload:

```bash
herdr server reload-config
```

## Configure

The repo's `config.toml` holds the defaults. To override them durably — so your
changes survive `herdr plugin install` updates — copy it into the plugin's config
directory and edit that copy:

```bash
cp config.toml "$(herdr plugin config-dir blamattina.auto-rename)/config.toml"
```

The plugin reads `$HERDR_PLUGIN_CONFIG_DIR/config.toml` first and falls back to the
bundled default. The settings:

```toml
# Shell command prefix used to generate the label. Receives the prompt as its
# last argument. The default disables Claude's session persistence so the
# summarizer's own calls don't write transcripts that pollute transcript
# discovery. Swap for any CLI that accepts a prompt string.
#   generator = "command claude --print --no-session-persistence"
#   generator = "llm"
generator = "command claude --print --no-session-persistence"

# Max characters for a generated label. Also passed to the prompt, so the model
# aims for this length and labels are hard-truncated to it as a safety net.
max_label_length = 24

# Throttle: min seconds between naming passes per pane, and min new conversation
# messages since the last pass. Together they keep labels calm and cheap.
min_interval_seconds = 60
min_growth = 4

# A workspace is named once, from the user's overall goal. Wait for at least this
# many user requests before naming, so the goal is synthesized from the whole
# session rather than a thin or one-off opening request.
min_goal_requests = 3

# Context for the agent/tab altitudes: how many recent messages to feed (the
# current activity) and the per-message excerpt cap. The workspace goal is
# inferred separately from the whole arc of user requests.
tail_messages = 8
message_max_chars = 600

# Wait before sampling (lets the transcript flush); scrollback lines for the
# fallback path when an agent transcript can't be parsed.
delay_seconds = 2
context_lines = 40
```

The `generator` is any CLI that accepts a prompt as its final argument and prints
a short response. The plugin takes the first line, strips quotes, validates it
isn't prose, and truncates to `max_label_length` characters. Labels are short
verb-first phrases (e.g. `Fixing devcontainer Java`).

## Requirements

- herdr 0.7.0+
- `python3` (3.9+; runs the plugin)
- A coding-agent CLI on your `PATH` (Claude Code by default)

## License

MIT
