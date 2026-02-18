"""Load eval results from CSV files or Inspect AI .eval logs."""

import csv
import glob
import os
import re
from pathlib import Path

import pandas as pd

RUNS_DIR = os.path.join(os.path.dirname(__file__), "..", "runs")
SCORE_COL = "score"


def _find_csv_in_run(run_dir: str) -> str | None:
    """Find eval_results CSV in a run directory."""
    for f in os.listdir(run_dir):
        if f.startswith("eval_results") and f.endswith(".csv"):
            return os.path.join(run_dir, f)
    return None


def _find_eval_logs_in_run(run_dir: str) -> list[str]:
    """Find .eval log files in a run directory."""
    return glob.glob(os.path.join(run_dir, "*.eval"))


def load_run_from_csv(csv_path: str, run_id: str) -> pd.DataFrame | None:
    """Load a single run from its eval_results CSV."""
    try:
        df = pd.read_csv(csv_path, encoding="utf-8")
    except Exception as e:
        print(f"Warning: Could not read {csv_path}: {e}")
        return None

    if df.empty:
        return None

    # Normalize column names (some CSVs use different names)
    col_map = {
        "L1": "L1",
        "L2": "L2",
        "L3": "L3",
        "target_channel": "target_channel",
        "channel_id": "channel_id",
    }
    for old, new in col_map.items():
        if old not in df.columns and new not in df.columns:
            df[new] = ""

    # Find score column
    score_col = None
    for c in df.columns:
        if "score" in c.lower() and "stderr" not in c.lower():
            score_col = c
            break
    if score_col is None and "score" in df.columns:
        score_col = "score"

    if score_col:
        df[SCORE_COL] = pd.to_numeric(df[score_col], errors="coerce")
    else:
        df[SCORE_COL] = float("nan")

    df["run_id"] = run_id
    return df


def load_run_from_eval(run_dir: str, run_id: str) -> pd.DataFrame | None:
    """Load a run from Inspect AI .eval log files."""
    try:
        from inspect_ai.analysis import samples_df
    except ImportError:
        return None

    eval_files = _find_eval_logs_in_run(run_dir)
    if not eval_files:
        return None

    try:
        df = samples_df(eval_files, quiet=True)
    except Exception as e:
        print(f"Warning: Could not read eval logs from {run_dir}: {e}")
        return None

    if df.empty:
        return None

    # Find score column (model_graded_qa or similar)
    score_col = None
    for c in df.columns:
        if "score" in c.lower() and "model_graded" in c.lower() and "stderr" not in c.lower():
            score_col = c
            break
    if score_col is None:
        for c in df.columns:
            if "score" in c.lower() and "stderr" not in c.lower():
                score_col = c
                break

    if score_col:
        # Handle score format (might be "97" or dict)
        raw = df[score_col]
        scores = []
        for v in raw:
            if pd.isna(v):
                scores.append(float("nan"))
            elif isinstance(v, (int, float)):
                scores.append(float(v))
            elif isinstance(v, str):
                # Try to extract number (e.g. "97" or "SCORE: 97")
                m = re.search(r"(\d+)", str(v))
                scores.append(float(m.group(1)) if m else float("nan"))
            else:
                scores.append(float("nan"))
        df[SCORE_COL] = scores
    else:
        df[SCORE_COL] = float("nan")

    # Metadata columns (from inspect_ai sample metadata)
    for key in ("L1", "L2", "L3", "target_channel", "channel_id"):
        if key not in df.columns and f"metadata_{key}" in df.columns:
            df[key] = df[f"metadata_{key}"].fillna("")
        elif key not in df.columns:
            df[key] = ""

    df["run_id"] = run_id
    return df


def load_run(run_dir: str, run_id: str | None = None) -> pd.DataFrame | None:
    """Load a single run from CSV (preferred) or .eval logs."""
    run_id = run_id or os.path.basename(run_dir)

    csv_path = _find_csv_in_run(run_dir)
    if csv_path:
        return load_run_from_csv(csv_path, run_id)

    return load_run_from_eval(run_dir, run_id)


def load_all_runs(runs_dir: str | None = None) -> dict[str, pd.DataFrame]:
    """Load all runs from runs_dir. Returns {run_id: DataFrame}."""
    runs_dir = runs_dir or RUNS_DIR
    if not os.path.isdir(runs_dir):
        return {}

    result = {}
    for name in sorted(os.listdir(runs_dir)):
        path = os.path.join(runs_dir, name)
        if not os.path.isdir(path):
            continue
        df = load_run(path, name)
        if df is not None and not df.empty:
            result[name] = df
    return result


def merge_runs(runs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Merge multiple run DataFrames into one with run_id column."""
    if not runs:
        return pd.DataFrame()
    return pd.concat(runs.values(), ignore_index=True)
