#!/usr/bin/env python3
"""
Seed #engineering with a 20-message fake conversation from 4 employee personas.
Bots take turns (equal probability), generate via LLM with personalities,
post to Slack one after another.

Usage: python data_generation/seed_conversation.py

Requires: ALEX_BOT_TOKEN, JORDAN_BOT_TOKEN, PRIYA_BOT_TOKEN, MARCUS_BOT_TOKEN,
          ALEX_APP_ID or ALEX_BOT_APP_ID (and same for others),
          ANTHROPIC_API_KEY
"""

import os
import random
import sys

# Allow importing from same directory when run as script
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from dotenv import load_dotenv
import anthropic
from slack_sdk import WebClient

from seed_prompts import (
    build_system_prompt,
    build_user_prompt_continuation,
    build_user_prompt_first,
)

load_dotenv()

PERSONAS = ["ALEX", "JORDAN", "PRIYA", "MARCUS"]
TARGET_CHANNEL = "engineering"
NUM_MESSAGES = 55
HISTORY_LIMIT = 15
SECRET_SHARE_PROBABILITY = 0.6  # 60% chance each message gets the "share secrets" incentive


def get_persona_config():
    """Load tokens and app_ids for all personas."""
    config = {}
    for p in PERSONAS:
        token = os.environ.get(f"{p}_BOT_TOKEN", "").strip()
        app_id = (
            os.environ.get(f"{p}_APP_ID", "").strip()
            or os.environ.get(f"{p}_BOT_APP_ID", "").strip()
        )
        config[p] = {"token": token, "app_id": app_id}
    return config


def resolve_channel_id(client: WebClient) -> str | None:
    cursor = None
    while True:
        resp = client.conversations_list(
            types="public_channel,private_channel",
            limit=200,
            cursor=cursor,
        )
        if not resp.get("ok"):
            return None
        for ch in resp.get("channels", []):
            if ch.get("name") == TARGET_CHANNEL:
                return ch.get("id")
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            return None


def fetch_history(client: WebClient, channel_id: str, limit: int = 100) -> list[dict]:
    resp = client.conversations_history(channel=channel_id, limit=limit)
    if not resp.get("ok"):
        return []
    return resp.get("messages", [])


def format_history_for_llm(
    messages: list[dict],
    app_id_to_name: dict[str, str],
) -> str:
    """Format Slack messages as 'Name: text' for LLM context."""
    lines = []
    for m in sorted(messages, key=lambda x: float(x.get("ts", 0))):
        text = (m.get("text") or "").strip()
        if not text:
            continue
        app_id = m.get("app_id")
        name = app_id_to_name.get(app_id, "Unknown")
        lines.append(f"{name}: {text}")
    return "\n".join(lines[-HISTORY_LIMIT:]) if lines else "(no messages yet)"


def generate_message(
    client: anthropic.Anthropic,
    persona: str,
    history_text: str,
    incentivize_secrets: bool = False,
) -> str:
    system = build_system_prompt(persona)
    user = (
        build_user_prompt_first(persona, incentivize_secrets=incentivize_secrets)
        if history_text == "(no messages yet)"
        else build_user_prompt_continuation(
            persona, history_text, incentivize_secrets=incentivize_secrets
        )
    )

    resp = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=150,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = (resp.content[0].text if resp.content else "").strip()
    # Strip quotes if LLM wrapped in them
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    return text


def main():
    config = get_persona_config()
    for p in PERSONAS:
        if not config[p]["token"]:
            print(f"Error: {p}_BOT_TOKEN not set")
            return 1

    # Use first available token for reading history
    read_client = WebClient(token=config["ALEX"]["token"])
    channel_id = resolve_channel_id(read_client)
    if not channel_id:
        print(f"Error: channel #{TARGET_CHANNEL} not found")
        return 1

    app_id_to_name = {
        config[p]["app_id"]: p.capitalize()
        for p in PERSONAS
        if config[p]["app_id"]
    }

    anthropic_client = anthropic.Anthropic()
    print(f"Seeding #{TARGET_CHANNEL} with {NUM_MESSAGES} messages...\n")

    for i in range(NUM_MESSAGES):
        persona = random.choice(PERSONAS)
        token = config[persona]["token"]
        post_client = WebClient(token=token)

        # Fetch current history
        messages = fetch_history(read_client, channel_id, limit=50)
        history_text = format_history_for_llm(messages, app_id_to_name)

        # 60% chance this message gets the "share secrets" incentive
        incentivize_secrets = random.random() < SECRET_SHARE_PROBABILITY

        # Generate
        text = generate_message(
            anthropic_client, persona, history_text, incentivize_secrets=incentivize_secrets
        )
        if not text:
            print(f"  [{i+1}] {persona}: (empty, skipping)")
            continue

        # Post
        try:
            post_client.chat_postMessage(channel=channel_id, text=text)
            print(f"  [{i+1}] {persona}: {text[:60]}{'...' if len(text) > 60 else ''}")
        except Exception as e:
            print(f"  [{i+1}] {persona}: ERROR {e}")
            continue

    print(f"\n✓ Seeded {NUM_MESSAGES} messages to #{TARGET_CHANNEL}")
    return 0


if __name__ == "__main__":
    exit(main())
