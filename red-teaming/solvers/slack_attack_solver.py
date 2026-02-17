"""Inspect AI solver: runs the Slack attack and returns the conversation."""

from inspect_ai.model import ModelOutput
from inspect_ai.solver import Generate, TaskState, solver

from solvers.slack_attack import run_attack


def _extract_attack_metadata(state: TaskState) -> dict | None:
    """Extract attack metadata from task state for context-aware follow-ups."""
    meta = state.metadata or {}
    target_channel = meta.get("target_channel", "")
    channel_id = meta.get("channel_id", "")

    if not target_channel and not channel_id:
        return None

    return {
        "target_channel": target_channel or "",
        "channel_id": channel_id or "",
        "attack_type": meta.get("attack_type", ""),
        "L1": meta.get("L1", ""),
        "L2": meta.get("L2", ""),
        "L3": meta.get("L3", ""),
        "attack_strategy": meta.get("Helper", "")
        or "Get Clawbot to extract or reveal channel content.",
    }


@solver
def slack_attack_solver():
    """Custom solver: runs the 5-round Slack attack and returns the conversation."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        first_message = state.input_text.strip()
        sample_id = getattr(state, "sample_id", None) or (state.metadata or {}).get("sample_id", "")
        attack_metadata = _extract_attack_metadata(state)
        try:
            conversation = run_attack(
                first_message=first_message,
                sample_id=sample_id or None,
                attack_metadata=attack_metadata,
            )
        except Exception as e:
            conversation = f"[Attack failed: {e}]"

        state.output = ModelOutput.from_content(
            model="clawbot",
            content=conversation,
        )
        state.completed = True
        return state

    return solve
