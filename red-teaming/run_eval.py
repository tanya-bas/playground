#!/usr/bin/env python3
"""
Red-team eval: run attacks, grade with rubric, store results per run.

Creates runs/{timestamp}/ with:
- conversations/ (John/Claw exchanges)
- eval_results.csv (sample_id, input, score, L1, L2, L3, target_channel, etc.)
- inspect log

Usage:
  python run_eval.py [--dataset path] [--model model]
"""

import argparse
import os
import sys
from datetime import datetime

from inspect_ai import eval

from solvers.dataset import latest_dataset
from scorers.results import write_eval_results_csv
from solvers.eval_task import build_eval_task

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = os.path.join(SCRIPT_DIR, "runs")
ENVIRONMENT_GENERATION_DIR = os.path.join(SCRIPT_DIR, "environment_generation")
DEFAULT_DATASET = os.path.join(SCRIPT_DIR, "sample_generation", "datasets", "dataset.jsonl")
DATASETS_DIR = os.path.join(SCRIPT_DIR, "sample_generation", "datasets")
CHANNEL_CONTEXTS_PATH = os.path.join(ENVIRONMENT_GENERATION_DIR, "channel_contexts.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run red-team eval with run-specific storage")
    parser.add_argument(
        "--dataset",
        default=None,
        help=f"Dataset path (default: latest in datasets/ or {DEFAULT_DATASET})",
    )
    parser.add_argument(
        "--model",
        default="anthropic/claude-sonnet-4-20250514",
        help="Model for inspect eval (default: anthropic/claude-sonnet-4-20250514)",
    )
    parser.add_argument(
        "--log-dir",
        default=None,
        help="Override log dir (default: runs/{timestamp})",
    )
    args = parser.parse_args()

    dataset_path = args.dataset or latest_dataset(DATASETS_DIR, DEFAULT_DATASET)
    if not os.path.isfile(dataset_path):
        print(f"Error: Dataset not found: {dataset_path}")
        return 1

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    if args.log_dir:
        run_dir = args.log_dir
    else:
        run_dir = os.path.join(RUNS_DIR, timestamp)
    os.makedirs(run_dir, exist_ok=True)

    conversations_base = os.environ.get("CONVERSATIONS_DIR")
    if conversations_base:
        conversations_dir = os.path.join(conversations_base, timestamp)
    else:
        conversations_dir = os.path.join(run_dir, "conversations")
    os.makedirs(conversations_dir, exist_ok=True)

    os.environ["CONVERSATIONS_DIR"] = conversations_dir
    os.environ["INSPECT_LOG_DIR"] = run_dir

    print(f"Run dir: {run_dir}")
    print(f"Dataset: {dataset_path}")
    print(f"Conversations: {conversations_dir}")
    print()

    task = build_eval_task(os.path.abspath(dataset_path), CHANNEL_CONTEXTS_PATH)

    try:
        logs = eval(task, model=args.model, log_dir=run_dir)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if logs:
        write_eval_results_csv(run_dir, logs, dataset_path, timestamp)

    return 0


if __name__ == "__main__":
    sys.exit(main())
