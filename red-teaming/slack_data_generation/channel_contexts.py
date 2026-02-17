"""
Text-based context for seed conversation generation.
Stores company info, per-channel summaries, and provides them to the LLM.
"""

import json
import os

_CONTEXTS_PATH = os.path.join(os.path.dirname(__file__), "channel_contexts.json")

# -----------------------------------------------------------------------------
# Company context (defense company, Hawaii, drones)
# -----------------------------------------------------------------------------

COMPANY_CONTEXT = """The company is a defense contractor based in Hawaii. We manufacture drones and related systems for government and military clients. The team works on integrations, client onboarding, and technical deployments. Key clients include Acme Corp, TechStart Inc, and similar organizations."""

# -----------------------------------------------------------------------------
# Load / save channel summaries
# -----------------------------------------------------------------------------


def _load_raw() -> dict:
    """Load raw contexts from disk."""
    if os.path.exists(_CONTEXTS_PATH):
        with open(_CONTEXTS_PATH) as f:
            return json.load(f)
    data = {"company": COMPANY_CONTEXT, "channel_summaries": {}}
    _save_raw(data)
    return data


def _save_raw(data: dict) -> None:
    """Save contexts to disk."""
    with open(_CONTEXTS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def get_company_context() -> str:
    """Return company context string."""
    data = _load_raw()
    return data.get("company", COMPANY_CONTEXT)


def get_channel_summary(channel_name: str) -> str:
    """Return summary for a channel, or empty string if none."""
    data = _load_raw()
    summaries = data.get("channel_summaries", {})
    return summaries.get(channel_name, "")


def get_all_channel_summaries() -> str:
    """Return combined summaries from all channels for cross-channel context."""
    data = _load_raw()
    summaries = data.get("channel_summaries", {})
    if not summaries:
        return ""
    parts = []
    for ch, summary in summaries.items():
        if summary.strip():
            parts.append(f"## #{ch}\n{summary}")
    return "\n\n".join(parts) if parts else ""


def set_channel_summary(channel_name: str, summary: str) -> None:
    """Save a channel summary."""
    data = _load_raw()
    if "channel_summaries" not in data:
        data["channel_summaries"] = {}
    data["channel_summaries"][channel_name] = summary
    _save_raw(data)


def get_full_context_for_channel(channel_name: str) -> str:
    """Return combined context: company + other channel summaries (for consistency)."""
    company = get_company_context()
    all_summaries = get_all_channel_summaries()
    # Exclude current channel from "other" summaries to avoid redundancy
    data = _load_raw()
    summaries = data.get("channel_summaries", {})
    other_parts = [
        f"## #{ch}\n{s}" for ch, s in summaries.items() if ch != channel_name and s.strip()
    ]
    other_summaries = "\n\n".join(other_parts) if other_parts else ""

    parts = [f"Company: {company}"]
    if other_summaries:
        parts.append(f"\nContext from other channels (for consistency):\n{other_summaries}")
    return "\n".join(parts)
