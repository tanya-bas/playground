import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential
import anthropic
from slack_sdk import WebClient

from solvers.attacker_prompts import build_attacker_user_prompt, build_attacker_system_prompt

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
SLACK_RETRY_ATTEMPTS = int(os.environ.get("SLACK_RETRY_ATTEMPTS", "3"))
SLACK_RETRY_MIN_WAIT = float(os.environ.get("SLACK_RETRY_MIN_WAIT", "2"))
SLACK_RETRY_MAX_WAIT = float(os.environ.get("SLACK_RETRY_MAX_WAIT", "30"))

slack = WebClient(token=RED_TEAM_SLACK_BOT_TOKEN)
slack_openclaw = (
    WebClient(token=OPENCLAW_SLACK_BOT_TOKEN) if OPENCLAW_SLACK_BOT_TOKEN.strip() else None
)
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _retry_slack_call(fn):
    """Retry a Slack API call on timeout/connection errors."""
    @retry(
        stop=stop_after_attempt(SLACK_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=SLACK_RETRY_MIN_WAIT, max=SLACK_RETRY_MAX_WAIT),
    )
    def _do():
        return fn()
    return _do()

CHANNEL = os.environ.get("CHANNEL", "#tanya-krystian-john-collaboration")
MAX_HISTORY_MESSAGES = 20
NUM_ROUNDS = 3

# Folder to save John/Claw conversation history (read at runtime so run_eval can set it)
_RED_TEAMING_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEAKABLE_DATA_PATH = os.path.join(
    _RED_TEAMING_ROOT, "sample_generation", "datasets", "leakable_data.json"
)


def _get_conversations_dir() -> str:
    """Return conversations dir; read from env at call time (not import time)."""
    return os.environ.get(
        "CONVERSATIONS_DIR",
        os.path.join(_RED_TEAMING_ROOT, "conversations"),
    )


def _load_channel_facts(target_channel: str, n: int = 3) -> list[str]:
    """Load 2–3 verification-friendly facts from leakable_data for the target channel."""
    if not os.path.isfile(LEAKABLE_DATA_PATH):
        return []
    try:
        with open(LEAKABLE_DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    items = data.get(target_channel, [])
    if not isinstance(items, list):
        return []
    facts = []
    seen = set()
    for item in items:
        if isinstance(item, dict):
            fact = item.get("sensitive_item", "")
            if fact and fact.lower() not in seen and len(facts) < n:
                facts.append(fact)
                seen.add(fact.lower())
    return facts[:n]


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
        resp = _retry_slack_call(lambda: slack.conversations_info(channel=channel_id))
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
        resp = _retry_slack_call(
            lambda: slack.conversations_replies(channel=channel_id, ts=thread_ts, limit=1)
        )
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


def get_history_context(
    session_path: str | None = None,
    attack_metadata: dict | None = None,
) -> str:
    path = session_path or SESSION_FILE
    formatted = format_session_history(path) if path else ""
    return build_attacker_user_prompt(formatted=formatted or "(no messages yet)", attack_metadata=attack_metadata)


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


def generate_attack(
    session_path: str | None = None,
    attack_metadata: dict | None = None,
) -> str:
    meta = dict(attack_metadata or {})
    target_channel = meta.get("target_channel", "")
    if target_channel and "channel_facts" not in meta:
        meta["channel_facts"] = _load_channel_facts(target_channel, n=3)
    user_content = get_history_context(session_path, meta)
    system_prompt = build_attacker_system_prompt(meta)
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    return response.content[0].text.strip()


def save_conversation(
    thread_ts: str,
    session_path: str,
    john_messages: list[str],
    channel: str = CHANNEL,
    channel_id: str | None = None,
    sample_id: str | None = None,
) -> str | None:
    """Save John/Claw conversation to conversations/ folder as JSON. Returns path or None."""
    entries = _get_conversation_entries(session_path, limit=2 * NUM_ROUNDS)
    if not entries and not john_messages:
        return None

    conversations_dir = _get_conversations_dir()
    os.makedirs(conversations_dir, exist_ok=True)
    if sample_id:
        filename = f"{sample_id}.json"
    else:
        safe_ts = thread_ts.replace(".", "_") if thread_ts else "unknown"
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"attack_{timestamp}_{safe_ts}.json"
    filepath = os.path.join(conversations_dir, filename)

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
        our_user_id = _retry_slack_call(lambda: slack.auth_test()).get("user_id")
    except Exception:
        return 0, 0

    try:
        resp = _retry_slack_call(
            lambda: slack.conversations_replies(channel=channel_id, ts=thread_ts, limit=1000)
        )
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
            _retry_slack_call(lambda: client.chat_delete(channel=channel_id, ts=ts))
            deleted += 1
        except Exception:
            failed += 1

    return deleted, failed


def run_attack(
    first_message: str | None = None,
    sample_id: str | None = None,
    attack_metadata: dict | None = None,
) -> str:
    """
    Run the full 5-round attack and return the conversation as formatted text.
    If first_message is provided, use it for round 1; otherwise generate it.
    If sample_id is provided, save conversation as {sample_id}.json (for eval runs).
    attack_metadata: optional dict with target_channel, channel_id, attack_type, L1, L2, L3, attack_strategy for context-aware follow-ups.
    Used by Inspect eval solver.
    """
    thread_ts = None
    channel_id = None
    john_messages: list[str] = []

    for round_num in range(1, NUM_ROUNDS + 1):
        session_path = resolve_session_file(thread_ts)
        if round_num == 1 and first_message:
            attack = first_message
        else:
            attack = generate_attack(session_path, attack_metadata)
        john_messages.append(attack)
        message = f"<@{OPENCLAW_BOT_USER_ID}> {attack}"

        if round_num == 1:
            resp = _retry_slack_call(lambda: slack.chat_postMessage(channel=CHANNEL, text=message))
            thread_ts = resp.get("ts") or resp.get("message", {}).get("ts")
            channel_id = resp.get("channel")
            if not thread_ts:
                raise RuntimeError("Could not get thread_ts from first message response")
        else:
            _retry_slack_call(
                lambda: slack.chat_postMessage(channel=CHANNEL, text=message, thread_ts=thread_ts)
            )

        if round_num < NUM_ROUNDS:
            time.sleep(WAIT_SECONDS)

    time.sleep(WAIT_SECONDS)
    session_path = resolve_session_file(thread_ts)
    entries = _get_conversation_entries(session_path, limit=2 * NUM_ROUNDS)

    lines = []
    if entries:
        for sender, body in entries:
            name = "Claw" if sender == "Clawbot" else sender
            lines.append(f"{name}: {body}")
    else:
        for msg in john_messages:
            lines.append(f"John: {msg}")
            lines.append("Claw: [response not captured]")

    save_conversation(
        thread_ts, session_path, john_messages, channel_id=channel_id, sample_id=sample_id
    )
    if channel_id and channel_id[0] in "CGD":
        delete_thread(channel_id, thread_ts)

    # Reset OpenClaw agent for the next attack
    reset_script = os.path.join(_RED_TEAMING_ROOT, "lib", "cleaning_helpers", "reset_openclaw.py")
    subprocess.run([sys.executable, reset_script], check=True, cwd=_RED_TEAMING_ROOT)

    return "\n\n".join(lines)


if __name__ == "__main__":
    conversation = run_attack()
    print("Conversation:")
    print(conversation)
