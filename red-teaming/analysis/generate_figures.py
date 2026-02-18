#!/usr/bin/env python3
"""
Generate figures from red-team eval results.

Usage:
  python analysis/generate_figures.py                    # All runs, output to analysis/figures/
  python analysis/generate_figures.py --run 2026-02-17_14-43-38  # Single run
  python analysis/generate_figures.py --runs-dir /path/to/runs
  python analysis/generate_figures.py --output ./my_figures
"""

import argparse
import os
import sys

# Add parent to path for imports
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPT_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd

from analysis.load_results import RUNS_DIR, load_all_runs, load_run, latest_run_id
from analysis.analyze_patterns import FAILURE_THRESHOLD, analyze_run, summarize_patterns

# Matplotlib with non-interactive backend for script use (optional)
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


def _ensure_output_dir(output_dir: str) -> None:
    os.makedirs(output_dir, exist_ok=True)


def _style_axes(ax, title: str) -> None:
    ax.set_title(title, fontsize=12)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def fig_score_by_attack_type(analysis: dict, output_dir: str, prefix: str = "", label: str = "") -> None:
    """Bar chart: mean score by attack type (L2)."""
    by_l2 = analysis["by_l2"]
    if by_l2.empty:
        return

    means = by_l2["mean_score"].values

    fig, ax = plt.subplots(figsize=(8, 5))
    x = np.arange(len(by_l2))
    ax.bar(x, means, color="#4a90d9", edgecolor="#2d5a8a", linewidth=0.5)
    ax.axhline(y=FAILURE_THRESHOLD, color="#c0392b", linestyle="--", alpha=0.7, label=f"Failure threshold ({FAILURE_THRESHOLD})")
    ax.set_xticks(x)
    ax.set_xticklabels(by_l2["L2"], rotation=25, ha="right")
    ax.set_ylabel("Mean score (0–100)")
    ax.set_ylim(0, 105)
    ax.legend(loc="lower right", fontsize=8)
    title = "Mean score by attack type (L2)"
    if label:
        title += f" — {label}"
    _style_axes(ax, title)
    plt.tight_layout()
    path = os.path.join(output_dir, f"{prefix}score_by_attack_type.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved {path}")


def fig_score_by_channel(analysis: dict, output_dir: str, prefix: str = "", label: str = "") -> None:
    """Bar chart: mean score by target channel."""
    by_ch = analysis["by_channel"]
    if by_ch.empty:
        return

    means = by_ch["mean_score"].values

    fig, ax = plt.subplots(figsize=(8, 5))
    x = np.arange(len(by_ch))
    ax.bar(x, means, color="#27ae60", edgecolor="#1e8449", linewidth=0.5)
    ax.axhline(y=FAILURE_THRESHOLD, color="#c0392b", linestyle="--", alpha=0.7, label=f"Failure threshold ({FAILURE_THRESHOLD})")
    ax.set_xticks(x)
    ax.set_xticklabels([f"#{c}" for c in by_ch["target_channel"]], rotation=25, ha="right")
    ax.set_ylabel("Mean score (0–100)")
    ax.set_ylim(0, 105)
    ax.legend(loc="lower right", fontsize=8)
    title = "Mean score by target channel"
    if label:
        title += f" — {label}"
    _style_axes(ax, title)
    plt.tight_layout()
    path = os.path.join(output_dir, f"{prefix}score_by_channel.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved {path}")


def generate_single_run_figures(analysis: dict, output_dir: str, run_id: str) -> None:
    """Generate figures for a single run (score by attack type and by channel)."""
    if not HAS_MATPLOTLIB:
        print("  Skipping figures (matplotlib not installed). Run: pip install matplotlib")
        return
    prefix = f"single_run_{run_id}_"
    label = f"single run: {run_id}"
    fig_score_by_attack_type(analysis, output_dir, prefix, label=label)
    fig_score_by_channel(analysis, output_dir, prefix, label=label)


def generate_joint_figures(analyses: dict, output_dir: str) -> None:
    """Merge all runs and generate joint figures."""
    if not HAS_MATPLOTLIB:
        return

    dfs = []
    for run_id, a in analyses.items():
        if a["n_samples"] > 0 and "df" in a:
            dfs.append(a["df"])
    if not dfs:
        return

    merged = pd.concat(dfs, ignore_index=True)
    joint_analysis = analyze_run(merged, "joint")
    prefix = "joint_runs_"
    label = "joint runs"
    print("  Joint runs: generating combined figures...")
    fig_score_by_attack_type(joint_analysis, output_dir, prefix, label=label)
    fig_score_by_channel(joint_analysis, output_dir, prefix, label=label)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate figures from red-team eval results")
    parser.add_argument(
        "--run",
        default=None,
        help="Single run ID (e.g. 2026-02-17_14-43-38). Use 'latest' for most recent run. If not set, process all runs.",
    )
    parser.add_argument(
        "--runs-dir",
        default=RUNS_DIR,
        help=f"Runs directory (default: {RUNS_DIR})",
    )
    parser.add_argument(
        "--output",
        default=os.path.join(_SCRIPT_DIR, "figures"),
        help="Output directory for figures",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print pattern summary to stdout",
    )
    args = parser.parse_args()

    _ensure_output_dir(args.output)

    if args.run:
        run_id = latest_run_id(args.runs_dir) if args.run == "latest" else args.run
        if args.run == "latest":
            if not run_id:
                print("Error: No runs found in runs directory.", file=sys.stderr)
                return 1
            print(f"Using latest run: {run_id}")
        run_dir = os.path.join(args.runs_dir, run_id)
        if not os.path.isdir(run_dir):
            print(f"Error: Run directory not found: {run_dir}", file=sys.stderr)
            return 1
        runs = {run_id: load_run(run_dir, run_id)}
    else:
        runs = load_all_runs(args.runs_dir)

    if not runs:
        print("No runs found.", file=sys.stderr)
        return 1

    print(f"Processing {len(runs)} run(s): {list(runs.keys())}")

    analyses = {}
    for run_id, df in runs.items():
        if df is None or df.empty:
            print(f"  {run_id}: no results (run may be in progress or missing CSV/eval logs), skipping")
            continue
        analysis = analyze_run(df, run_id)
        analyses[run_id] = analysis
        if analysis["n_samples"] == 0:
            print(f"  {run_id}: no valid samples, skipping figures")
            continue
        print(f"  {run_id}: {analysis['n_samples']} samples, mean={analysis['overall_mean']:.1f}, failures={analysis['n_failures']}")
        generate_single_run_figures(analysis, args.output, run_id)

    if not analyses or all(a.get("n_samples", 0) == 0 for a in analyses.values()):
        print("No valid run data to plot. Ensure the run has completed and eval_results_*.csv exists.", file=sys.stderr)
        return 1

    # Joint figures: merge all runs when we have 2+ runs with data
    valid_analyses = {k: v for k, v in analyses.items() if v["n_samples"] > 0}
    if len(valid_analyses) >= 2:
        generate_joint_figures(valid_analyses, args.output)

    if args.summary:
        print("\n" + "=" * 60)
        for run_id, analysis in analyses.items():
            if analysis["n_samples"] > 0:
                print(summarize_patterns(analysis))
                print()

    print(f"\nFigures saved to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
