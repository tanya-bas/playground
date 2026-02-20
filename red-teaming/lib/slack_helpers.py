"""Shared Slack API helpers used across environment generation, sample generation, and eval."""

import json
import os

from slack_sdk import WebClient
from tenacity import retry, stop_after_attempt, wait_exponential

SLACK_RETRY_ATTEMPTS = int(os.environ.get("SLACK_RETRY_ATTEMPTS", "3"))
SLACK_RETRY_MIN_WAIT = float(os.environ.get("SLACK_RETRY_MIN_WAIT", "2"))
SLACK_RETRY_MAX_WAIT = float(os.environ.get("SLACK_RETRY_MAX_WAIT", "30"))


def retry_slack_call(fn):
    """Execute a Slack API call with exponential-backoff retry on timeout/connection errors."""
    @retry(
        stop=stop_after_attempt(SLACK_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=SLACK_RETRY_MIN_WAIT, max=SLACK_RETRY_MAX_WAIT),
    )
    def _do():
        return fn()
    return _do()


def resolve_channel_id(
    client: WebClient,
    channel: str,
    *,
    types: str = "public_channel,private_channel",
) -> str | None:
    """Resolve channel name (with or without #) or raw ID to Slack channel ID.

    If ``channel`` already looks like a Slack ID (starts with C/G/D and rest is
    alphanumeric), it is returned as-is.
    """
    channel = (channel or "").strip()
    if not channel:
        return None
    if channel[0] in "CGD" and channel[1:].replace("-", "").isalnum():
        return channel
    name = channel.lstrip("#")
    cursor = None
    while True:
        resp = client.conversations_list(
            types=types,
            exclude_archived=True,
            limit=200,
            cursor=cursor,
        )
        if not resp.get("ok"):
            return None
        for ch in resp.get("channels", []):
            if ch.get("name") == name:
                return ch.get("id")
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            return None


def fetch_full_history(client: WebClient, channel_id: str) -> list[dict]:
    """Fetch all messages from a channel via paginated ``conversations_history``."""
    all_msgs: list[dict] = []
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
    *,
    limit: int | None = 20,
    default_name: str = "Unknown",
    empty_text: str = "(no messages yet)",
) -> str:
    """Format Slack messages as ``Name: text`` for LLM context.

    Args:
        messages: Raw Slack message dicts (must contain ``ts``, ``text``, ``app_id``).
        app_id_to_name: Mapping of Slack app_id to display name.
        limit: Max messages to keep (from the end). ``None`` for no limit.
        default_name: Fallback name when app_id is unknown.
        empty_text: String to return when there are no messages.
    """
    lines = []
    for m in sorted(messages, key=lambda x: float(x.get("ts", 0))):
        text = (m.get("text") or "").strip()
        if not text:
            continue
        app_id = m.get("app_id")
        name = app_id_to_name.get(app_id, default_name) if app_id else default_name
        lines.append(f"{name}: {text}")
    if not lines:
        return empty_text
    if limit is not None:
        lines = lines[-limit:]
    return "\n".join(lines)


def delete_thread(
    slack_client: WebClient,
    channel_id: str,
    thread_ts: str,
    *,
    openclaw_bot_user_id: str = "",
    openclaw_client: WebClient | None = None,
    human_client: WebClient | None = None,
) -> tuple[int, int]:
    """Delete all messages in a Slack thread. Returns ``(deleted, failed)``.

    Uses ``slack_client`` for its own messages, ``openclaw_client`` for Clawbot's,
    and ``human_client`` for human user messages.
    """
    if not channel_id or channel_id[0] not in "CGD":
        return 0, 0

    try:
        bot_user_id = retry_slack_call(lambda: slack_client.auth_test()).get("user_id")
    except Exception:
        return 0, 0

    human_user_id: str | None = None
    if human_client:
        try:
            human_user_id = retry_slack_call(lambda: human_client.auth_test()).get("user_id")
        except Exception:
            pass

    try:
        resp = retry_slack_call(
            lambda: slack_client.conversations_replies(channel=channel_id, ts=thread_ts, limit=1000)
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

        if user == bot_user_id:
            use_client = slack_client
        elif user == openclaw_bot_user_id and openclaw_client:
            use_client = openclaw_client
        elif user == human_user_id and human_client:
            use_client = human_client
        else:
            failed += 1
            continue

        try:
            retry_slack_call(lambda c=use_client: c.chat_delete(channel=channel_id, ts=ts))
            deleted += 1
        except Exception:
            failed += 1

    return deleted, failed


def load_channel_contexts(path: str) -> dict:
    """Load channel_contexts.json. Returns empty dict if file doesn't exist."""
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)
