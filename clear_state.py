#!/usr/bin/env python3
"""Clear a pane's saved rename state when it closes, so the next session in that
pane gets a fresh workspace label and the tab tracking resets."""
import json
import os

STATE_DIR = os.environ.get("HERDR_PLUGIN_STATE_DIR", "/tmp/herdr-auto-rename-state")


def remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def main():
    event_raw = os.environ.get("HERDR_PLUGIN_EVENT_JSON", "")
    if not event_raw:
        return
    try:
        data = json.loads(event_raw).get("data", {})
    except json.JSONDecodeError:
        return

    pane_id = data.get("pane_id", "")
    if not pane_id:
        return
    remove(os.path.join(STATE_DIR, "workspace-renamed-" + pane_id.replace(":", "--")))

    # Drop the tab name-tracking state if the event carries a tab id.
    tab_id = data.get("tab_id", "")
    if tab_id:
        remove(os.path.join(STATE_DIR, "tab-name-" + tab_id.replace(":", "--")))


if __name__ == "__main__":
    main()
