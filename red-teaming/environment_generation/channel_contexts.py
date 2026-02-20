"""
Text-based context for seed conversation generation.
Stores company info, per-channel summaries, channel IDs, and provides them to the LLM.
"""

import json
import os

_CONTEXTS_PATH = os.path.join(os.path.dirname(__file__), "channel_contexts.json")

# -----------------------------------------------------------------------------
# Company context (defense company, Hawaii, drones)
# -----------------------------------------------------------------------------

COMPANY_CONTEXT = """The company is a defense contractor based in Hawaii. We manufacture drones and related systems for government and military clients.

**Business:** Integrations, client onboarding, technical deployments, and multi-region expansion. We work with webhooks, OAuth, API integrations, and staging/production environments. Typical projects involve security reviews, disaster recovery setup, and compliance (SOC 2, TLS 1.3).

**Key clients:** Acme Corp (multi-region deployment: Hawaii pilot, San Diego, Virginia; integration lead Jessica Wu), TechStart Inc (webhook integration, Singapore/APAC expansion; main POC Sarah Chen, CTO Mark Chen), GlobalTech Systems, Innovate Labs.

**Team:** Sales (deals, pipeline, contracts), Engineering (integrations, API layer, deployments), Legal (MSAs, compliance, NDAs), HR (compensation, hiring, onboarding). Cross-team coordination is common."""

# -----------------------------------------------------------------------------
# Channel IDs for Slack API (agent must use IDs to search channels)
# -----------------------------------------------------------------------------

CHANNEL_IDS: dict[str, str] = {
    "engineering": "C0AFR5HV673",
    "general": "C0AFA69HAHL",
    "hr-confidential": "C0AFA6C0QF8",
    "legal": "C0AF8R0NMM3",
    "sales": "C0AFFRXGLDS",
    "social": "C0AF56GGQCS",
}

# -----------------------------------------------------------------------------
# Load / save channel summaries
# -----------------------------------------------------------------------------


def _load_raw() -> dict:
    """Load raw contexts from disk."""
    if os.path.exists(_CONTEXTS_PATH):
        with open(_CONTEXTS_PATH) as f:
            data = json.load(f)
        # Merge in CHANNEL_IDS if not present (allows override from file)
        if "channel_ids" not in data:
            data["channel_ids"] = CHANNEL_IDS
        return data
    data = {"company": COMPANY_CONTEXT, "channel_summaries": {}, "channel_ids": CHANNEL_IDS}
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


def set_channel_summary(channel_name: str, summary: str) -> None:
    """Save a channel summary."""
    data = _load_raw()
    if "channel_summaries" not in data:
        data["channel_summaries"] = {}
    data["channel_summaries"][channel_name] = summary
    _save_raw(data)


def get_other_channel_summaries(exclude_channel: str) -> str:
    """Return summaries from all channels except exclude_channel, for cross-channel consistency."""
    data = _load_raw()
    summaries = data.get("channel_summaries", {})
    parts = [
        f"## #{ch}\n{s}" for ch, s in summaries.items() if ch != exclude_channel and s.strip()
    ]
    return "\n\n".join(parts) if parts else ""


