#!/usr/bin/env python3
"""Delete Slack messages (DM with OpenClaw or channel thread).

Usage:
  python cleanup_slack_conversation.py --dm   # Delete ALL messages in your DM with OpenClaw
  python cleanup_slack_conversation.py --channel #tanya-krystian --all   # Delete ALL messages in channel
  python cleanup_slack_conversation.py --channel #tanya-krystian --thread-ts 1739782800.123456
  python cleanup_slack_conversation.py --channel #tanya-krystian --list

For --dm: SLACK_USER_TOKEN (xoxp), OPENCLAW_BOT_USER_ID, OPENCLAW_SLACK_BOT_TOKEN
For channels: RED_TEAM_SLACK_BOT_TOKEN, OPENCLAW_BOT_USER_ID; SLACK_USER_TOKEN for human messages
"""

import argparse
import os
import sys

from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential
from slack_sdk import WebClient

load_dotenv()

RED_TEAM_SLACK_BOT_TOKEN = os.environ.get("RED_TEAM_SLACK_BOT_TOKEN", "")
SLACK_USER_TOKEN = os.environ.get("SLACK_USER_TOKEN", "")
OPENCLAW_BOT_USER_ID = os.environ.get("OPENCLAW_BOT_USER_ID", "")
OPENCLAW_SLACK_BOT_TOKEN = os.environ.get("OPENCLAW_SLACK_BOT_TOKEN", "")
SLACK_RETRY_ATTEMPTS = int(os.environ.get("SLACK_RETRY_ATTEMPTS", "3"))
SLACK_RETRY_MIN_WAIT = float(os.environ.get("SLACK_RETRY_MIN_WAIT", "2"))
SLACK_RETRY_MAX_WAIT = float(os.environ.get("SLACK_RETRY_MAX_WAIT", "30"))

slack = WebClient(token=RED_TEAM_SLACK_BOT_TOKEN)
slack_user = WebClient(token=SLACK_USER_TOKEN) if SLACK_USER_TOKEN.strip() else None
slack_openclaw = (
    WebClient(token=OPENCLAW_SLACK_BOT_TOKEN) if OPENCLAW_SLACK_BOT_TOKEN.strip() else None
)


def _retry_slack_call(fn):
    @retry(
        stop=stop_after_attempt(SLACK_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=SLACK_RETRY_MIN_WAIT, max=SLACK_RETRY_MAX_WAIT),
    )
    def _do():
        return fn()
    return _do()


def delete_thread(channel_id: str, thread_ts: str) -> tuple[int, int]:
    """Delete all messages in the thread. Returns (deleted_count, failed_count)."""
    if not channel_id or channel_id[0] not in "CGD":
        return 0, 0

    try:
        bot_user_id = _retry_slack_call(lambda: slack.auth_test()).get("user_id")
    except Exception:
        return 0, 0

    human_user_id: str | None = None
    if slack_user:
        try:
            human_user_id = _retry_slack_call(lambda: slack_user.auth_test()).get("user_id")
        except Exception:
            pass

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

        if user == bot_user_id:
            use_client = slack
        elif user == OPENCLAW_BOT_USER_ID and slack_openclaw:
            use_client = slack_openclaw
        elif user == human_user_id and slack_user:
            use_client = slack_user
        else:
            failed += 1
            continue

        try:
            _retry_slack_call(lambda c=use_client: c.chat_delete(channel=channel_id, ts=ts))
            deleted += 1
        except Exception:
            failed += 1

    return deleted, failed


def find_dm_with_openclaw() -> str | None:
    """Find the DM channel ID for the conversation with OpenClaw. Uses SLACK_USER_TOKEN."""
    if not slack_user or not OPENCLAW_BOT_USER_ID:
        return None
    try:
        cursor = None
        while True:
            resp = _retry_slack_call(
                lambda: slack_user.conversations_list(types="im", limit=200, cursor=cursor or "")
            )
            if not resp.get("ok"):
                return None
            for ch in resp.get("channels") or []:
                if ch.get("user") == OPENCLAW_BOT_USER_ID:
                    return ch.get("id")
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
    except Exception:
        pass
    return None


def delete_dm_openclaw() -> tuple[int, int]:
    """Delete all messages in the DM with OpenClaw. Returns (deleted, failed)."""
    dm_id = find_dm_with_openclaw()
    if not dm_id:
        return 0, 0

    try:
        our_user_id = _retry_slack_call(lambda: slack_user.auth_test()).get("user_id")
    except Exception:
        return 0, 0

    # Collect all messages (top-level + thread replies)
    all_messages: list[tuple[str, str]] = []  # (ts, user)
    cursor = None
    seen_ts = set()

    while True:
        try:
            resp = _retry_slack_call(
                lambda: slack_user.conversations_history(
                    channel=dm_id, limit=200, cursor=cursor or ""
                )
            )
        except Exception:
            break
        if not resp.get("ok"):
            break

        for msg in resp.get("messages") or []:
            ts = msg.get("ts")
            user = msg.get("user", "")
            if not ts or ts in seen_ts:
                continue
            seen_ts.add(ts)
            all_messages.append((ts, user))

            # Fetch thread replies (only for parent messages)
            if msg.get("thread_ts") and msg.get("thread_ts") != ts:
                pass  # This is a reply, skip fetch
            else:
                try:
                    replies_resp = _retry_slack_call(
                        lambda: slack_user.conversations_replies(channel=dm_id, ts=ts, limit=1000)
                    )
                    if replies_resp.get("ok"):
                        for r in replies_resp.get("messages") or []:
                            rt = r.get("ts")
                            ru = r.get("user", "")
                            if rt and rt not in seen_ts:
                                seen_ts.add(rt)
                                all_messages.append((rt, ru))
                except Exception:
                    pass

        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break

    # Sort by ts descending (delete newest first)
    all_messages.sort(key=lambda x: x[0], reverse=True)

    deleted = 0
    failed = 0
    for ts, user in all_messages:
        if user == our_user_id:
            use_client = slack_user
        elif user == OPENCLAW_BOT_USER_ID and slack_openclaw:
            use_client = slack_openclaw
        else:
            failed += 1
            continue

        try:
            _retry_slack_call(lambda c=use_client: c.chat_delete(channel=dm_id, ts=ts))
            deleted += 1
        except Exception:
            failed += 1

    return deleted, failed


def resolve_channel_id(channel: str) -> str | None:
    """Resolve channel name (#foo) or ID (C123...) to channel ID."""
    channel = (channel or "").strip()
    if not channel:
        return None
    if channel[0] in "CGD" and channel[1:].replace("-", "").isalnum():
        return channel
    name = channel.lstrip("#")
    try:
        cursor = None
        while True:
            resp = _retry_slack_call(
                lambda: slack.conversations_list(
                    types="public_channel,private_channel,mpim,im",
                    exclude_archived=True,
                    limit=200,
                    cursor=cursor,
                )
            )
            if not resp.get("ok"):
                return None
            for ch in resp.get("channels") or []:
                if ch.get("name") == name:
                    return ch.get("id")
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
    except Exception:
        pass
    return None


def delete_channel_all(channel_id: str) -> tuple[int, int]:
    """Delete all messages in the channel (top-level + all thread replies). Returns (deleted, failed)."""
    if not channel_id or channel_id[0] not in "CGD":
        return 0, 0

    try:
        bot_user_id = _retry_slack_call(lambda: slack.auth_test()).get("user_id")
    except Exception:
        return 0, 0

    human_user_id: str | None = None
    if slack_user:
        try:
            human_user_id = _retry_slack_call(lambda: slack_user.auth_test()).get("user_id")
        except Exception:
            pass

    all_messages: list[tuple[str, str]] = []  # (ts, user)
    cursor = None
    seen_ts = set()

    while True:
        try:
            resp = _retry_slack_call(
                lambda: slack.conversations_history(
                    channel=channel_id, limit=200, cursor=cursor or ""
                )
            )
        except Exception:
            break
        if not resp.get("ok"):
            break

        for msg in resp.get("messages") or []:
            ts = msg.get("ts")
            user = msg.get("user", "")
            if not ts or ts in seen_ts:
                continue
            seen_ts.add(ts)
            all_messages.append((ts, user))

            if msg.get("thread_ts") and msg.get("thread_ts") != ts:
                pass
            else:
                try:
                    replies_resp = _retry_slack_call(
                        lambda: slack.conversations_replies(channel=channel_id, ts=ts, limit=1000)
                    )
                    if replies_resp.get("ok"):
                        for r in replies_resp.get("messages") or []:
                            rt = r.get("ts")
                            ru = r.get("user", "")
                            if rt and rt not in seen_ts:
                                seen_ts.add(rt)
                                all_messages.append((rt, ru))
                except Exception:
                    pass

        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break

    all_messages.sort(key=lambda x: x[0], reverse=True)

    deleted = 0
    failed = 0
    for ts, user in all_messages:
        if user == bot_user_id:
            use_client = slack
        elif user == OPENCLAW_BOT_USER_ID and slack_openclaw:
            use_client = slack_openclaw
        elif user == human_user_id and slack_user:
            use_client = slack_user
        else:
            failed += 1
            continue

        try:
            _retry_slack_call(lambda c=use_client: c.chat_delete(channel=channel_id, ts=ts))
            deleted += 1
        except Exception:
            failed += 1

    return deleted, failed


def list_recent_threads(channel_id: str, limit: int = 30) -> None:
    """List recent thread starters in the channel."""
    try:
        resp = _retry_slack_call(
            lambda: slack.conversations_history(
                channel=channel_id,
                limit=limit,
            )
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    if not resp.get("ok"):
        print("Could not fetch channel history.", file=sys.stderr)
        sys.exit(1)
    messages = resp.get("messages") or []
    seen_threads = set()
    thread_starts = []
    for msg in messages:
        ts = msg.get("ts", "")
        thread_ts = msg.get("thread_ts") or ts
        if thread_ts not in seen_threads:
            seen_threads.add(thread_ts)
            thread_starts.append((thread_ts, msg))
    if not thread_starts:
        print("No threads in channel.")
        return
    print("Recent threads — use THREAD_TS to delete:\n")
    for thread_ts, msg in thread_starts[:15]:
        text = (msg.get("text") or "")[:60].replace("\n", " ")
        print(f"  THREAD_TS={thread_ts}")
        print(f"    {text}...")


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete Slack messages (DM with OpenClaw or channel thread)")
    parser.add_argument("--dm", action="store_true", help="Delete ALL messages in your DM with OpenClaw")
    parser.add_argument("--channel", "-c", default=os.environ.get("CHANNEL", "#tanya-krystian"))
    parser.add_argument("--all", "-a", action="store_true", help="Delete ALL messages in channel (top-level + threads)")
    parser.add_argument("--thread-ts", "-t", default=os.environ.get("THREAD_TS"))
    parser.add_argument("--list", "-l", action="store_true", help="List recent threads (channels only)")
    args = parser.parse_args()

    if args.dm:
        if not SLACK_USER_TOKEN:
            print("Set SLACK_USER_TOKEN (xoxp) in .env for --dm. See .env.example.", file=sys.stderr)
            sys.exit(1)
        if not OPENCLAW_BOT_USER_ID or not OPENCLAW_SLACK_BOT_TOKEN:
            print("Set OPENCLAW_BOT_USER_ID and OPENCLAW_SLACK_BOT_TOKEN for --dm.", file=sys.stderr)
            sys.exit(1)
        deleted, failed = delete_dm_openclaw()
        print(f"Deleted {deleted} message(s), {failed} skipped.")
        return

    if not RED_TEAM_SLACK_BOT_TOKEN:
        print("Set RED_TEAM_SLACK_BOT_TOKEN in .env", file=sys.stderr)
        sys.exit(1)

    channel_id = resolve_channel_id(args.channel)
    if not channel_id:
        print(f"Could not resolve channel: {args.channel}", file=sys.stderr)
        sys.exit(1)

    if args.list:
        list_recent_threads(channel_id)
        return

    if args.all:
        deleted, failed = delete_channel_all(channel_id)
        print(f"Deleted {deleted} message(s), {failed} skipped (need OPENCLAW_SLACK_BOT_TOKEN for Clawbot).")
        return

    thread_ts = (args.thread_ts or "").strip()
    if not thread_ts:
        print("Provide --thread-ts or THREAD_TS. Use --list to see recent threads.", file=sys.stderr)
        sys.exit(1)

    deleted, failed = delete_thread(channel_id, thread_ts)
    print(f"Deleted {deleted} message(s), {failed} skipped (need OPENCLAW_SLACK_BOT_TOKEN for Clawbot).")


if __name__ == "__main__":
    main()
