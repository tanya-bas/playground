"""
Inspect eval for red team attack: Extraction of sensitive data from Slack.
Dataset: 1 sample targeting conversation extraction.
Solver: Custom solver that runs the Slack attack pipeline (Clawbot as system under test).
Scorer: LLM judge (model_graded_qa) evaluates whether Clawbot resisted the attack.

Run: inspect eval eval_task.py --model anthropic/claude-opus-4-6
(Note: The --model is required by Inspect but not used for generation; the grader uses its own model.)
"""

import os

from inspect_ai import Task, task
from inspect_ai.dataset import json_dataset
from inspect_ai.model import ModelOutput
from inspect_ai.scorer import model_graded_qa
from inspect_ai.solver import Generate, TaskState, solver

from send_message import run_attack


@solver
def slack_attack_solver():
    """Custom solver: runs the 5-round Slack attack and returns the conversation."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        first_message = state.input_text.strip()
        try:
            conversation = run_attack(first_message=first_message)
        except Exception as e:
            conversation = f"[Attack failed: {e}]"

        state.output = ModelOutput.from_content(
            model="clawbot",
            content=conversation,
        )
        state.completed = True
        return state

    return solve


@task
def extraction_attack():
    dataset_path = os.path.join(os.path.dirname(__file__), "attack_data_generation", "dataset.jsonl")
    return Task(
        dataset=json_dataset(dataset_path),
        solver=slack_attack_solver(),
        scorer=model_graded_qa(
            include_history=True,
            partial_credit=True,
            model="anthropic/claude-opus-4-6",
        ),
    )
