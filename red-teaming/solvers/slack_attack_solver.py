"""Inspect AI solver: runs the Slack attack and returns the conversation."""

from inspect_ai.model import ModelOutput
from inspect_ai.solver import Generate, TaskState, solver

from solvers.slack_attack import run_attack


def _extract_attack_metadata(state: TaskState) -> dict | None:
    """Extract attack metadata from task state for context-aware follow-ups."""
    meta = state.metadata or {}
    target_channel = meta.get("target_channel", "")
    channel_id = meta.get("channel_id", "")
    target = getattr(state, "target", None)
    if isinstance(target, list):
        target = "\n".join(str(t) for t in target) if target else ""
    elif target is None:
        target = ""
    else:
        target = str(target).strip()

    if not target_channel and not channel_id and not target:
        return None

    return {
        "target_channel": target_channel or "",
        "channel_id": channel_id or "",
        "criterion": target or "",
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
