"""Build the Inspect AI eval task for the slack attack solver."""

from inspect_ai import Task
from inspect_ai.dataset import json_dataset
from inspect_ai.scorer import mean, model_graded_qa, stderr

from solvers.dataset import enrich_dataset_with_channel_context
from scorers.rubric import GRADING_TEMPLATE, build_grading_instructions
from solvers.slack_attack_solver import slack_attack_solver


def build_eval_task(dataset_path: str, channel_contexts_path: str) -> Task:
    """Build the extraction_attack task with the given dataset."""
    enriched_path = enrich_dataset_with_channel_context(dataset_path, channel_contexts_path)
    return Task(
        dataset=json_dataset(enriched_path),
        solver=slack_attack_solver(),
        scorer=model_graded_qa(
            template=GRADING_TEMPLATE,
            instructions=build_grading_instructions(),
            grade_pattern=r"SCORE:\s*(\d+)",
            include_history=True,
            model="anthropic/claude-3-5-sonnet-20241022",
        ),
        metrics=[mean(), stderr()],
    )
