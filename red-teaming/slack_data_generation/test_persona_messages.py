"""
Post or delete messages from a persona (Alex, Jordan, Marcus, Priya) in seed channels.

Usage:
  PERSONA=alex python data_generation/test_persona_messages.py post
  PERSONA=jordan python data_generation/test_persona_messages.py delete

Requires: {PERSONA}_BOT_TOKEN, and {PERSONA}_BOT_APP_ID for delete.
Persona bots need scopes: chat:write, chat:write.public, channels:read, channels:history,
groups:read, groups:history.
"""

import os
import sys

from dotenv import load_dotenv
from slack_sdk import WebClient

load_dotenv()

DEFAULT_CHANNELS = [
    "#sales",
    "#engineering",
    "#hr-confidential",
    "#legal",
    "#general",
    "#social",
]


def get_config():
    persona = os.environ.get("PERSONA", "alex").strip().upper()
    token = os.environ.get(f"{persona}_BOT_TOKEN", "").strip()
    app_id = (
        os.environ.get(f"{persona}_APP_ID", "").strip()
        or os.environ.get(f"{persona}_BOT_APP_ID", "").strip()
    )
    raw = os.environ.get("SEED_CHANNELS", "")
    channels = (
        [c.strip() for c in raw.split(",") if c.strip()]
        if raw.strip()
        else DEFAULT_CHANNELS
    )
    return persona, token, app_id, channels


def resolve_channel_ids(client: WebClient) -> dict[str, str]:
    name_to_id: dict[str, str] = {}
    cursor = None
    while True:
        resp = client.conversations_list(
            types="public_channel,private_channel",
            limit=200,
            cursor=cursor,
        )
        if not resp.get("ok"):
            break
        for ch in resp.get("channels", []):
            name = ch.get("name")
            ch_id = ch.get("id")
            if name and ch_id:
                name_to_id[name] = ch_id
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return name_to_id


def fetch_channel_history(client: WebClient, channel_id: str, limit: int = 100):
    resp = client.conversations_history(channel=channel_id, limit=limit)
    if not resp.get("ok"):
        return []
    return resp.get("messages", [])


def cmd_post(persona: str, token: str, channels: list[str]) -> int:
    if not token:
        print(f"Error: {persona}_BOT_TOKEN not set in .env")
        return 1

    client = WebClient(token=token)
    msg = f"Hey team, {persona.capitalize()} here"

    print(f"Posting from {persona} to {len(channels)} channels...\n")
    for ch in channels:
        name = ch.lstrip("#")
        try:
            resp = client.chat_postMessage(channel=name, text=msg)
            print(f"  ✓ #{name}" if resp.get("ok") else f"  ✗ #{name}: {resp.get('error', 'unknown')}")
        except Exception as e:
            print(f"  ✗ #{name}: {e}")
    return 0


def cmd_delete(persona: str, token: str, app_id: str, channels: list[str]) -> int:
    if not token:
        print(f"Error: {persona}_BOT_TOKEN not set in .env")
        return 1
    if not app_id:
        print(f"Error: {persona}_APP_ID or {persona}_BOT_APP_ID not set")
        return 1

    client = WebClient(token=token)
    name_to_id = resolve_channel_ids(client)
    channel_ids = []
    for ch in channels:
        name = ch.lstrip("#")
        ch_id = name_to_id.get(name)
        if ch_id:
            channel_ids.append((name, ch_id))
        else:
            print(f"  Warning: channel #{name} not found, skipping")

    print(f"Deleting {persona}'s messages...")
    deleted = 0
    for name, ch_id in channel_ids:
        for msg in fetch_channel_history(client, ch_id):
            if msg.get("app_id") == app_id and msg.get("ts"):
                try:
                    client.chat_delete(channel=ch_id, ts=msg["ts"])
                    deleted += 1
                    print(f"  ✓ #{name}: deleted {msg['ts']}")
                except Exception as e:
                    print(f"  ✗ #{name} {msg['ts']}: {e}")

    print(f"\nDeleted {deleted} message(s)\nVerifying...")
    remaining = 0
    for name, ch_id in channel_ids:
        count = sum(1 for m in fetch_channel_history(client, ch_id, 50) if m.get("app_id") == app_id)
        if count:
            remaining += count
            print(f"  ✗ #{name}: {count} message(s) still present")
        else:
            print(f"  ✓ #{name}: no {persona} messages")

    if remaining:
        print(f"\nVerification failed: {remaining} message(s) still in channels")
        return 1
    print(f"\n✓ Deletion verified")
    return 0


def main():
    persona, token, app_id, channels = get_config()
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "").lower()

    if cmd == "post":
        return cmd_post(persona, token, channels)
    if cmd == "delete":
        return cmd_delete(persona, token, app_id, channels)

    print("Usage: PERSONA=<name> python test_persona_messages.py post|delete")
    return 1


if __name__ == "__main__":
    sys.exit(main())
