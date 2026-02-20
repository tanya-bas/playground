"""
Seed a Slack channel with fake conversation from 4 employee personas.
Bots take turns (role-biased probability), generate via LLM, post to Slack.
After seeding, summarizes the channel and saves to channel_contexts.json.

Usage:
  python data_generation/seed_conversation.py [channel]
  SEED_CHANNEL=#sales python data_generation/seed_conversation.py

Channels: engineering, sales, legal, hr-confidential, general, social

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

from channel_contexts import set_channel_summary
from seed_prompts import (
    CHANNEL_PERSONA_WEIGHTS,
    SUMMARIZE_PROMPT,
    build_system_prompt,
    build_user_prompt,
)

load_dotenv()

PERSONAS = ["ALEX", "JORDAN", "PRIYA", "MARCUS"]
HISTORY_LIMIT = 20
NUM_MESSAGES_MIN = 30
NUM_MESSAGES_MAX = 50
MAX_CONSECUTIVE_SAME_PERSON = 2


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


def resolve_channel_id(client: WebClient, channel_name: str) -> str | None:
    """Resolve channel name (without #) to Slack channel ID."""
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
            if ch.get("name") == channel_name:
                return ch.get("id")
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            return None


def fetch_history(client: WebClient, channel_id: str, limit: int = 100) -> list[dict]:
    resp = client.conversations_history(channel=channel_id, limit=limit)
    if not resp.get("ok"):
        return []
    return resp.get("messages", [])


def fetch_full_history(client: WebClient, channel_id: str) -> list[dict]:
    """Fetch all messages (paginate)."""
    all_msgs = []
    cursor = None
    while True:
        kwargs = {"channel": channel_id, "limit": 200}
        if cursor:
            kwargs["cursor"] = cursor
        resp = client.conversations_history(**kwargs)
        if not resp.get("ok"):
            break
        msgs = resp.get("messages", [])
        all_msgs.extend(msgs)
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor or not msgs:
            break
    return all_msgs


def format_history_for_llm(
    messages: list[dict],
    app_id_to_name: dict[str, str],
    limit: int | None = HISTORY_LIMIT,
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
    if not lines:
        return "(no messages yet)"
    if limit is not None:
        lines = lines[-limit:]
    return "\n".join(lines)


def choose_persona(
    channel: str,
    last_two: list[str],
    weights: dict[str, dict[str, int]],
) -> str:
    """Choose persona with role bias, avoiding 3+ consecutive same person."""
    channel_weights = weights.get(channel, {p: 1 for p in PERSONAS})
    choices = list(PERSONAS)
    weights_list = [channel_weights.get(p, 1) for p in choices]

    for _ in range(20):  # Retry to avoid same person 3x in a row
        persona = random.choices(choices, weights=weights_list, k=1)[0]
        if last_two.count(persona) < MAX_CONSECUTIVE_SAME_PERSON:
            return persona
    return random.choice(PERSONAS)


def generate_message(
    client: anthropic.Anthropic,
    persona: str,
    channel: str,
    history_text: str,
) -> str:
    system = build_system_prompt(persona, channel)
    user = build_user_prompt(persona, channel, history_text)

    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=150,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = (resp.content[0].text if resp.content else "").strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    return text


def summarize_channel(
    client: anthropic.Anthropic,
    history_text: str,
    channel_name: str,
) -> str:
    """Use LLM to summarize channel history (high-level, no secrets)."""
    if not history_text.strip():
        return "(no messages)"
    user = f"""Channel: #{channel_name}

Conversation:
{history_text}

Summarize the conversation. {SUMMARIZE_PROMPT}"""
    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": user}],
    )
    return (resp.content[0].text if resp.content else "").strip()


def seed_channel(channel_name: str) -> int:
    """Seed a single channel. Returns 0 on success."""
    config = get_persona_config()
    for p in PERSONAS:
        if not config[p]["token"]:
            print(f"Error: {p}_BOT_TOKEN not set")
            return 1

    read_client = WebClient(token=config["ALEX"]["token"])
    channel_id = resolve_channel_id(read_client, channel_name)
    if not channel_id:
        print(f"Error: channel #{channel_name} not found")
        return 1

    app_id_to_name = {
        config[p]["app_id"]: p.capitalize()
        for p in PERSONAS
        if config[p]["app_id"]
    }

    num_messages = random.randint(NUM_MESSAGES_MIN, NUM_MESSAGES_MAX)
    anthropic_client = anthropic.Anthropic()
    print(f"Seeding #{channel_name} with {num_messages} messages...\n")

    last_two: list[str] = []

    for i in range(num_messages):
        persona = choose_persona(channel_name, last_two, CHANNEL_PERSONA_WEIGHTS)
        last_two = (last_two + [persona])[-2:]

        token = config[persona]["token"]
        post_client = WebClient(token=token)

        messages = fetch_history(read_client, channel_id, limit=50)
        history_text = format_history_for_llm(messages, app_id_to_name)

        text = generate_message(
            anthropic_client, persona, channel_name, history_text,
        )
        if not text:
            print(f"  [{i+1}] {persona}: (empty, skipping)")
            continue

        try:
            post_client.chat_postMessage(channel=channel_id, text=text)
            print(f"  [{i+1}] {persona}: {text[:60]}{'...' if len(text) > 60 else ''}")
        except Exception as e:
            print(f"  [{i+1}] {persona}: ERROR {e}")

    print(f"\n✓ Seeded {num_messages} messages to #{channel_name}")

    # Summarize and save
    print("Summarizing channel...")
    full_messages = fetch_full_history(read_client, channel_id)
    full_history_text = format_history_for_llm(
        full_messages, app_id_to_name, limit=None
    )
    summary = summarize_channel(anthropic_client, full_history_text, channel_name)
    set_channel_summary(channel_name, summary)
    print(f"✓ Saved summary for #{channel_name}\n")
    return 0


def summarize_only(channel_name: str) -> int:
    """Fetch channel history and save summary (no seeding). Used for engineering before seeding others."""
    config = get_persona_config()
    if not config["ALEX"]["token"]:
        print("Error: ALEX_BOT_TOKEN not set")
        return 1

    read_client = WebClient(token=config["ALEX"]["token"])
    channel_id = resolve_channel_id(read_client, channel_name)
    if not channel_id:
        print(f"Error: channel #{channel_name} not found")
        return 1

    app_id_to_name = {
        config[p]["app_id"]: p.capitalize()
        for p in PERSONAS
        if config[p]["app_id"]
    }

    full_messages = fetch_full_history(read_client, channel_id)
    full_history_text = format_history_for_llm(
        full_messages, app_id_to_name, limit=None
    )
    if full_history_text == "(no messages yet)":
        print(f"No messages in #{channel_name}, nothing to summarize.")
        return 0

    anthropic_client = anthropic.Anthropic()
    summary = summarize_channel(anthropic_client, full_history_text, channel_name)
    set_channel_summary(channel_name, summary)
    print(f"✓ Saved summary for #{channel_name}")
    return 0


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--summarize-only" in sys.argv:
        ch = (
            os.environ.get("SEED_CHANNEL", "").strip().lstrip("#")
            or (args[0] if args else "engineering")
        )
        return summarize_only(ch)

    channel = (
        os.environ.get("SEED_CHANNEL", "").strip().lstrip("#")
        or (args[0] if args else "")
    )
    if not channel:
        print("Usage: python seed_conversation.py <channel>")
        print("   or: SEED_CHANNEL=#sales python seed_conversation.py")
        print("   or: python seed_conversation.py --summarize-only [channel]  # engineering by default")
        print("Channels: engineering, sales, legal, hr-confidential, general, social")
        return 1

    return seed_channel(channel)


if __name__ == "__main__":
    exit(main())
