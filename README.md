# herdr-auto-rename

A [herdr](https://herdr.dev) plugin that automatically names your agent panes and
workspaces based on what the agent is actually doing — using a coding-agent CLI to
generate the labels.

Instead of a sidebar full of `worktree-quiet-stone-b6f5`, you get
`Debugging push failure`.

## How it works

The plugin hooks herdr's `pane.agent_status_changed` event. Each time an agent
transitions to `working`, it:

1. Waits a few seconds (so the agent has printed what it's working on)
2. Reads the recent pane output
3. Asks a configurable coding-agent CLI to summarize it
4. Renames the **agent** with what it's doing right now (updates on every turn)
5. Renames the **workspace** with the overall goal of the session (set once)

When a pane closes, a `pane.closed` hook clears its saved state so the next
session in that pane gets a fresh workspace label.

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
# Shell command prefix used to generate the label.
# Receives the prompt as its last argument.
# Default uses Claude Code; swap for any CLI that accepts a prompt string.
#   generator = "command claude --print"
#   generator = "llm"
generator = "command claude --print"

# Lines of pane output to read as context for the label
context_lines = 40

# Seconds to wait after the agent starts working before reading output.
# Gives the agent time to print its task before we sample it.
delay_seconds = 5
```

The `generator` is any CLI that accepts a prompt as its final argument and prints
a short response. The plugin takes the first line of output, strips quotes, and
truncates to 24 characters.

## Requirements

- herdr 0.7.0+
- `python3` (parses the event JSON)
- A coding-agent CLI on your `PATH` (Claude Code by default)

## License

MIT
