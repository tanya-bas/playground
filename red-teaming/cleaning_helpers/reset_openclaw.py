#!/usr/bin/env python3
"""Reset OpenClaw: delete all sessions, clear memory folder, and clear USER.md.

Stop the gateway first: openclaw gateway stop
Uses OPENCLAW_STATE_DIR (default: ~/.openclaw) for paths.
"""

import os
import shutil

STATE_DIR = os.environ.get("OPENCLAW_STATE_DIR", os.path.expanduser("~/.openclaw"))
WORKSPACE_DIR = os.path.join(STATE_DIR, "workspace")
MEMORY_DIR = os.path.join(WORKSPACE_DIR, "memory")


def main() -> None:
    # Delete all session folders
    agents_dir = os.path.join(STATE_DIR, "agents")
    if os.path.isdir(agents_dir):
        for agent_id in os.listdir(agents_dir):
            sessions_path = os.path.join(agents_dir, agent_id, "sessions")
            if os.path.isdir(sessions_path):
                shutil.rmtree(sessions_path)
                print(f"Deleted sessions: {sessions_path}")

    # Delete all files in the memory folder
    if os.path.isdir(MEMORY_DIR):
        for name in os.listdir(MEMORY_DIR):
            path = os.path.join(MEMORY_DIR, name)
            if os.path.isfile(path):
                os.remove(path)
                print(f"Deleted: {path}")

    # Reset USER.md to empty
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    user_path = os.path.join(WORKSPACE_DIR, "USER.md")
    with open(user_path, "w") as f:
        f.write("")
    print(f"Cleared: {user_path}")

    print("Done.")


if __name__ == "__main__":
    main()
