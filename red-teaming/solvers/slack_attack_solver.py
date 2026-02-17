"""Inspect AI solver: runs the Slack attack and returns the conversation."""

from inspect_ai.model import ModelOutput
from inspect_ai.solver import Generate, TaskState, solver

from solvers.slack_attack import run_attack


@solver
def slack_attack_solver():
    """Custom solver: runs the 5-round Slack attack and returns the conversation."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        first_message = state.input_text.strip()
        sample_id = getattr(state, "sample_id", None) or (state.metadata or {}).get("sample_id", "")
        try:
            conversation = run_attack(first_message=first_message, sample_id=sample_id or None)
        except Exception as e:
            conversation = f"[Attack failed: {e}]"

        state.output = ModelOutput.from_content(
            model="clawbot",
            content=conversation,
        )
        state.completed = True
        return state

    return solve
