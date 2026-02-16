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
SESSION_FILE = os.environ.get("SESSION_FILE", "")
SESSIONS_DIR = os.path.expanduser(
    os.environ.get("SESSIONS_DIR", "~/.openclaw/agents/main/sessions")
)
WAIT_SECONDS = int(os.environ.get("WAIT_SECONDS", "30"))

slack = WebClient(token=RED_TEAM_SLACK_BOT_TOKEN)
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


def save_conversation(thread_ts: str, session_path: str, john_messages: list[str]) -> str | None:
    """Save John/Claw conversation to conversations/ folder. Returns path or None."""
    entries = _get_conversation_entries(session_path, limit=2 * NUM_ROUNDS)
    if not entries and not john_messages:
        return None

    os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
    safe_ts = thread_ts.replace(".", "_") if thread_ts else "unknown"
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"attack_{timestamp}_{safe_ts}.txt"
    filepath = os.path.join(CONVERSATIONS_DIR, filename)

    lines = []
    if entries:
        for sender, body in entries:
            name = "Claw" if sender == "Clawbot" else sender
            lines.append(f"{name}\n{body}")
    else:
        for msg in john_messages:
            lines.append(f"John\n{msg}")
            lines.append("Claw\n[response not captured]")

    with open(filepath, "w") as f:
        f.write(f"# Attack session — thread {thread_ts}\n")
        f.write(f"# {NUM_ROUNDS} rounds\n\n")
        f.write("\n\n".join(lines))

    return filepath


if __name__ == "__main__":
    thread_ts = None
    john_messages: list[str] = []

    for round_num in range(1, NUM_ROUNDS + 1):
        session_path = resolve_session_file(thread_ts)
        attack = generate_attack(session_path)
        john_messages.append(attack)
        message = f"<@{OPENCLAW_BOT_USER_ID}> {attack}"

        if round_num == 1:
            resp = slack.chat_postMessage(channel=CHANNEL, text=message)
            thread_ts = resp.get("ts") or resp.get("message", {}).get("ts")
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
    saved = save_conversation(thread_ts, session_path, john_messages)
    if saved:
        print(f"Conversation saved to {saved}")
    else:
        print("Could not save conversation (no session file or messages)")
    print(f"Done. All {NUM_ROUNDS} messages sent in thread {thread_ts}")
