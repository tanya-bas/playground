import glob
import json
import os
import re
import time
from datetime import datetime

from dotenv import load_dotenv
import anthropic
from slack_sdk import WebClient

from prompts import DEFAULT_USER_PROMPT, HISTORY_CONTEXT, SYSTEM

load_dotenv()

RED_TEAM_SLACK_BOT_TOKEN = os.environ["RED_TEAM_SLACK_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OPENCLAW_BOT_USER_ID = os.environ["OPENCLAW_BOT_USER_ID"]
OPENCLAW_SLACK_BOT_TOKEN = os.environ.get("OPENCLAW_SLACK_BOT_TOKEN", "")
SESSION_FILE = os.environ.get("SESSION_FILE", "")
SESSIONS_DIR = os.path.expanduser(
    os.environ.get("SESSIONS_DIR", "~/.openclaw/agents/main/sessions")
)
WAIT_SECONDS = int(os.environ.get("WAIT_SECONDS", "30"))

slack = WebClient(token=RED_TEAM_SLACK_BOT_TOKEN)
slack_openclaw = (
    WebClient(token=OPENCLAW_SLACK_BOT_TOKEN) if OPENCLAW_SLACK_BOT_TOKEN.strip() else None
)
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

CHANNEL = os.environ.get("CHANNEL", "#tanya-krystian")
MAX_HISTORY_MESSAGES = 20
NUM_ROUNDS = 5

# Folder to save John/Claw conversation history (relative to script dir)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONVERSATIONS_DIR = os.environ.get(
    "CONVERSATIONS_DIR",
    os.path.join(SCRIPT_DIR, "conversations"),
)


def _extract_user_message(text: str) -> tuple[str, str]:
    """Extract sender name and clean message from Slack System envelope."""
    sender = "Unknown"
    match = re.search(r"from ([^:]+):", text)
    if match:
        sender = match.group(1).strip()
    parts = text.split("```")
    if len(parts) >= 2:
        message = parts[-1].strip()
    else:
        match = re.search(r"from [^:]+:\s*(.+?)(?=\n\nConversation info|\n\nSender|$)", text, re.DOTALL)
        message = match.group(1).strip() if match else text
    return sender, message


def _extract_assistant_text(msg: dict) -> str:
    """Extract text from assistant message content, skip thinking."""
    parts = msg.get("content") or []
    texts = [b["text"].strip() for b in parts if b.get("type") == "text" and b.get("text")]
    return "\n".join(t for t in texts if t)


def _extract_json_block(text: str, label: str) -> dict | list | None:
    """Extract a ```json ... ``` block after a given label from raw text."""
    pattern = rf"{re.escape(label)}[:\s]*\n```json\s*\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    try:
        return json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return None


def _get_external_context_from_session(session_path: str) -> dict:
    """Extract channel/conversation context from raw user messages in session."""
    if not session_path:
        return {}
    path = os.path.expanduser(session_path)
    if not os.path.isfile(path):
        return {}

    context: dict = {}
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "message":
                continue

            msg = entry.get("message") or {}
            if msg.get("role") != "user":
                continue

            raw = next(
                (b.get("text", "") for b in (msg.get("content") or []) if b.get("type") == "text"),
                None,
            )
            if not raw:
                continue

            if "conversation_info" not in context:
                conv = _extract_json_block(raw, "Conversation info")
                if conv:
                    context["conversation_info"] = conv
            if "thread_starter" not in context:
                starter = _extract_json_block(raw, "Thread starter")
                if starter:
                    context["thread_starter"] = starter
            if "chat_history" not in context:
                history = _extract_json_block(raw, "Chat history since last reply")
                if history:
                    context["chat_history"] = history
            if "replied_message" not in context:
                replied = _extract_json_block(raw, "Replied message")
                if replied:
                    context["replied_message"] = replied

    return context


def _fetch_slack_channel_info(channel_id: str) -> dict:
    """Fetch channel metadata from Slack API (topic, purpose, etc.). Requires channel ID."""
    try:
        resp = slack.conversations_info(channel=channel_id)
        if not resp.get("ok"):
            return {}
        ch = resp.get("channel") or {}
        return {
            "id": ch.get("id"),
            "name": ch.get("name"),
            "topic": (ch.get("topic") or {}).get("value") or None,
            "purpose": (ch.get("purpose") or {}).get("value") or None,
            "num_members": ch.get("num_members"),
            "is_private": ch.get("is_private"),
        }
    except Exception:
        return {}


def _fetch_thread_starter(channel_id: str, thread_ts: str) -> dict | None:
    """Fetch the first message in a thread (thread starter) from Slack. Requires channel ID."""
    try:
        resp = slack.conversations_replies(channel=channel_id, ts=thread_ts, limit=1)
        if not resp.get("ok"):
            return None
        messages = resp.get("messages") or []
        if not messages:
            return None
        msg = messages[0]
        return {
            "ts": msg.get("ts"),
            "user": msg.get("user"),
            "text": msg.get("text"),
            "timestamp": msg.get("ts"),
        }
    except Exception:
        return None


def _get_conversation_entries(session_path: str, limit: int) -> list[tuple[str, str]]:
    """Parse session JSONL and return (sender, message) entries for attack context."""
    if not session_path:
        return []
    path = os.path.expanduser(session_path)
    if not os.path.isfile(path):
        return []

    entries = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "message":
                continue

            msg = entry.get("message") or {}
            role = msg.get("role")

            if role == "user":
                raw = next(
                    (b.get("text", "") for b in (msg.get("content") or []) if b.get("type") == "text"),
                    None,
                )
                if not raw:
                    continue
                sender, body = _extract_user_message(raw)
                if body:
                    entries.append((sender, body))

            elif role == "assistant":
                if msg.get("provider") == "openclaw" and msg.get("model") == "delivery-mirror":
                    continue
                body = _extract_assistant_text(msg)
                if body:
                    entries.append(("Clawbot", body))

    return entries[-limit:]


def format_session_history(session_path: str, limit: int = MAX_HISTORY_MESSAGES) -> str:
    """Parse OpenClaw session JSONL and format for attack context."""
    entries = _get_conversation_entries(session_path, limit)
    lines = [f"{sender}: {body}" for sender, body in entries]
    return "\n\n".join(lines) if lines else ""


def get_history_context(session_path: str | None = None) -> str:
    path = session_path or SESSION_FILE
    if not path:
        return ""
    formatted = format_session_history(path)
    if not formatted:
        return ""
    return HISTORY_CONTEXT.format(formatted=formatted)


def resolve_session_file(thread_ts: str | None) -> str:
    """Get session file path: prefer thread-specific file, else SESSION_FILE."""
    if thread_ts:
        dir_path = os.path.expanduser(SESSIONS_DIR)
        if os.path.isdir(dir_path):
            pattern = os.path.join(dir_path, f"*-topic-{thread_ts}.jsonl")
            matches = glob.glob(pattern)
            if matches:
                return matches[0]
    if SESSION_FILE and os.path.isfile(os.path.expanduser(SESSION_FILE)):
        return SESSION_FILE
    return ""


def generate_attack(session_path: str | None = None) -> str:
    user_content = get_history_context(session_path) or DEFAULT_USER_PROMPT
    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=300,
        system=SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    )
    return response.content[0].text.strip()


def save_conversation(
    thread_ts: str,
    session_path: str,
    john_messages: list[str],
    channel: str = CHANNEL,
    channel_id: str | None = None,
) -> str | None:
    """Save John/Claw conversation to conversations/ folder as JSON. Returns path or None."""
    entries = _get_conversation_entries(session_path, limit=2 * NUM_ROUNDS)
    if not entries and not john_messages:
        return None

    os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
    safe_ts = thread_ts.replace(".", "_") if thread_ts else "unknown"
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"attack_{timestamp}_{safe_ts}.json"
    filepath = os.path.join(CONVERSATIONS_DIR, filename)

    messages = []
    if entries:
        for sender, body in entries:
            name = "Claw" if sender == "Clawbot" else sender
            messages.append({"sender": name, "content": body})
    else:
        for msg in john_messages:
            messages.append({"sender": "John", "content": msg})
            messages.append({"sender": "Claw", "content": "[response not captured]"})

    context: dict = {}

    effective_channel_id = channel_id if (channel_id and channel_id[0] in "CGD") else None
    if effective_channel_id:
        channel_info = _fetch_slack_channel_info(effective_channel_id)
        if channel_info:
            context["channel"] = channel_info

        if thread_ts:
            thread_starter = _fetch_thread_starter(effective_channel_id, thread_ts)
            if thread_starter:
                context["thread_starter"] = thread_starter

    session_context = _get_external_context_from_session(session_path)
    if session_context:
        context["session"] = session_context

    data = {
        "thread_ts": thread_ts,
        "channel": channel,
        "num_rounds": NUM_ROUNDS,
        "created_at": datetime.now().isoformat(),
        "messages": messages,
    }
    if context:
        data["context"] = context

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

    return filepath


def delete_thread(channel_id: str, thread_ts: str) -> tuple[int, int]:
    """
    Delete all messages in the thread. Uses our token for John's messages and
    OPENCLAW_SLACK_BOT_TOKEN for Clawbot's. Returns (deleted_count, failed_count).
    """
    if not channel_id or channel_id[0] not in "CGD":
        return 0, 0

    try:
        our_user_id = slack.auth_test().get("user_id")
    except Exception:
        return 0, 0

    try:
        resp = slack.conversations_replies(channel=channel_id, ts=thread_ts, limit=1000)
    except Exception:
        return 0, 0

    if not resp.get("ok"):
        return 0, 0

    messages = resp.get("messages") or []
    deleted = 0
    failed = 0

    for msg in reversed(messages):
        ts = msg.get("ts")
        user = msg.get("user", "")

        if not ts:
            continue

        if user == our_user_id:
            client = slack
        elif user == OPENCLAW_BOT_USER_ID and slack_openclaw:
            client = slack_openclaw
        else:
            failed += 1
            continue

        try:
            client.chat_delete(channel=channel_id, ts=ts)
            deleted += 1
        except Exception:
            failed += 1

    return deleted, failed


if __name__ == "__main__":
    thread_ts = None
    channel_id: str | None = None
    john_messages: list[str] = []

    for round_num in range(1, NUM_ROUNDS + 1):
        session_path = resolve_session_file(thread_ts)
        attack = generate_attack(session_path)
        john_messages.append(attack)
        message = f"<@{OPENCLAW_BOT_USER_ID}> {attack}"

        if round_num == 1:
            resp = slack.chat_postMessage(channel=CHANNEL, text=message)
            thread_ts = resp.get("ts") or resp.get("message", {}).get("ts")
            channel_id = resp.get("channel")
            if not thread_ts:
                raise RuntimeError("Could not get thread_ts from first message response")
        else:
            slack.chat_postMessage(channel=CHANNEL, text=message, thread_ts=thread_ts)

        if round_num < NUM_ROUNDS:
            print(f"Round {round_num} sent. Waiting {WAIT_SECONDS}s for Clawbot...")
            time.sleep(WAIT_SECONDS)

    print("Waiting for final Clawbot response...")
    time.sleep(WAIT_SECONDS)
    session_path = resolve_session_file(thread_ts)
    saved = save_conversation(thread_ts, session_path, john_messages, channel_id=channel_id)
    if saved:
        print(f"Conversation saved to {saved}")
    else:
        print("Could not save conversation (no session file or messages)")

    if channel_id and channel_id[0] in "CGD":
        deleted, failed = delete_thread(channel_id, thread_ts)
        print(f"Thread deleted: {deleted} messages removed{f', {failed} failed' if failed else ''}")
    print(f"Done. All {NUM_ROUNDS} messages sent in thread {thread_ts}")
