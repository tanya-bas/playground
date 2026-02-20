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

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
from slack_sdk import WebClient

from lib.constants import PERSONAS
from lib.slack_helpers import retry_slack_call, resolve_channel_id, delete_thread

load_dotenv()

RED_TEAM_SLACK_BOT_TOKEN = os.environ.get("RED_TEAM_SLACK_BOT_TOKEN", "")
SLACK_USER_TOKEN = os.environ.get("SLACK_USER_TOKEN", "")
OPENCLAW_BOT_USER_ID = os.environ.get("OPENCLAW_BOT_USER_ID", "")
OPENCLAW_SLACK_BOT_TOKEN = os.environ.get("OPENCLAW_SLACK_BOT_TOKEN", "")

slack = WebClient(token=RED_TEAM_SLACK_BOT_TOKEN)
slack_user = WebClient(token=SLACK_USER_TOKEN) if SLACK_USER_TOKEN.strip() else None
slack_openclaw = (
    WebClient(token=OPENCLAW_SLACK_BOT_TOKEN) if OPENCLAW_SLACK_BOT_TOKEN.strip() else None
)


def _build_persona_user_to_client() -> dict[str, WebClient]:
    """Map user_id -> WebClient for each persona bot that has a token set."""
    mapping: dict[str, WebClient] = {}
    for name in PERSONAS:
        token = os.environ.get(f"{name}_BOT_TOKEN", "").strip()
        if not token:
            continue
        try:
            client = WebClient(token=token)
            user_id = client.auth_test().get("user_id")
            if user_id:
                mapping[user_id] = client
        except Exception:
            pass
    return mapping


def _delete_thread(channel_id: str, thread_ts: str) -> tuple[int, int]:
    """Thin wrapper around shared delete_thread with module-level clients."""
    return delete_thread(
        slack, channel_id, thread_ts,
        openclaw_bot_user_id=OPENCLAW_BOT_USER_ID,
        openclaw_client=slack_openclaw,
        human_client=slack_user,
    )


def find_dm_with_openclaw() -> str | None:
    """Find the DM channel ID for the conversation with OpenClaw. Uses SLACK_USER_TOKEN."""
    if not slack_user or not OPENCLAW_BOT_USER_ID:
        return None
    try:
        cursor = None
        while True:
            resp = retry_slack_call(
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
        our_user_id = retry_slack_call(lambda: slack_user.auth_test()).get("user_id")
    except Exception:
        return 0, 0

    # Collect all messages (top-level + thread replies)
    all_messages: list[tuple[str, str]] = []  # (ts, user)
    cursor = None
    seen_ts = set()

    while True:
        try:
            resp = retry_slack_call(
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
                    replies_resp = retry_slack_call(
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
            retry_slack_call(lambda c=use_client: c.chat_delete(channel=dm_id, ts=ts))
            deleted += 1
        except Exception:
            failed += 1

    return deleted, failed


def _resolve_channel_id(channel: str) -> str | None:
    """Resolve channel name (#foo) or ID to channel ID (including mpim/im)."""
    return resolve_channel_id(
        slack, channel, types="public_channel,private_channel,mpim,im"
    )


def delete_channel_all(channel_id: str) -> tuple[int, int]:
    """Delete all messages in the channel (top-level + all thread replies). Returns (deleted, failed)."""
    if not channel_id or channel_id[0] not in "CGD":
        return 0, 0

    # Build user_id -> client mapping for all known tokens
    user_to_client: dict[str, WebClient] = {}

    try:
        bot_user_id = retry_slack_call(lambda: slack.auth_test()).get("user_id")
        if bot_user_id:
            user_to_client[bot_user_id] = slack
    except Exception:
        pass

    if slack_user:
        try:
            human_user_id = retry_slack_call(lambda: slack_user.auth_test()).get("user_id")
            if human_user_id:
                user_to_client[human_user_id] = slack_user
        except Exception:
            pass

    if slack_openclaw and OPENCLAW_BOT_USER_ID:
        user_to_client[OPENCLAW_BOT_USER_ID] = slack_openclaw

    persona_clients = _build_persona_user_to_client()
    user_to_client.update(persona_clients)

    candidates = [slack] if RED_TEAM_SLACK_BOT_TOKEN else []
    candidates.extend(persona_clients.values())
    if not candidates:
        return 0, 0

    all_messages: list[tuple[str, str]] = []  # (ts, user)
    reader = candidates[0]

    for candidate in candidates:
        cursor = None
        seen_ts: set[str] = set()
        test_messages: list[tuple[str, str]] = []
        found = False

        while True:
            try:
                resp = retry_slack_call(
                    lambda c=candidate: c.conversations_history(
                        channel=channel_id, limit=200, cursor=cursor or ""
                    )
                )
            except Exception:
                break
            if not resp.get("ok"):
                break
            if resp.get("messages"):
                found = True
            for msg in resp.get("messages") or []:
                ts = msg.get("ts")
                user = msg.get("user", "")
                if not ts or ts in seen_ts:
                    continue
                seen_ts.add(ts)
                test_messages.append((ts, user))

                if msg.get("thread_ts") and msg.get("thread_ts") != ts:
                    pass
                else:
                    try:
                        replies_resp = retry_slack_call(
                            lambda c=candidate: c.conversations_replies(
                                channel=channel_id, ts=ts, limit=1000
                            )
                        )
                        if replies_resp.get("ok"):
                            for r in replies_resp.get("messages") or []:
                                rt = r.get("ts")
                                ru = r.get("user", "")
                                if rt and rt not in seen_ts:
                                    seen_ts.add(rt)
                                    test_messages.append((rt, ru))
                    except Exception:
                        pass
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break

        if found:
            all_messages = test_messages
            break

    all_messages.sort(key=lambda x: x[0], reverse=True)

    deleted = 0
    failed = 0
    for ts, user in all_messages:
        use_client = user_to_client.get(user)
        if not use_client:
            failed += 1
            continue

        try:
            retry_slack_call(lambda c=use_client: c.chat_delete(channel=channel_id, ts=ts))
            deleted += 1
        except Exception:
            failed += 1

    return deleted, failed


def list_recent_threads(channel_id: str, limit: int = 30) -> None:
    """List recent thread starters in the channel."""
    try:
        resp = retry_slack_call(
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

    channel_id = _resolve_channel_id(args.channel)
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

    deleted, failed = _delete_thread(channel_id, thread_ts)
    print(f"Deleted {deleted} message(s), {failed} skipped (need OPENCLAW_SLACK_BOT_TOKEN for Clawbot).")


if __name__ == "__main__":
    main()
