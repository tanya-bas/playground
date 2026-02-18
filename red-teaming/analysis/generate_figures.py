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

from analysis.load_results import RUNS_DIR, load_all_runs, load_run
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


def _ci95_half_width(std: float, n: int) -> float:
    """95% CI half-width using t-distribution (n-1 df). Returns 0 when n<2 or std is NaN."""
    if n < 2 or (std != std) or std == 0:
        return 0.0
    try:
        from scipy.stats import t as t_dist
        t_val = t_dist.ppf(0.975, n - 1)
    except ImportError:
        t_val = 1.96  # z approx for large n
    return t_val * (std / np.sqrt(n))


def fig_score_by_attack_type(analysis: dict, output_dir: str, prefix: str = "") -> None:
    """Bar chart: mean score by attack type (L2) with 95% CI."""
    by_l2 = analysis["by_l2"]
    if by_l2.empty:
        return

    means = by_l2["mean_score"].values
    stds = by_l2.get("std_score", pd.Series(0, index=by_l2.index)).fillna(0).values
    ns = by_l2["n"].values
    yerr = np.array([_ci95_half_width(float(s), int(n)) for s, n in zip(stds, ns)])

    fig, ax = plt.subplots(figsize=(8, 5))
    x = np.arange(len(by_l2))
    ax.bar(x, means, yerr=yerr, color="#4a90d9", edgecolor="#2d5a8a", linewidth=0.5,
           capsize=4, error_kw={"elinewidth": 1.5, "capthick": 1})
    ax.axhline(y=FAILURE_THRESHOLD, color="#c0392b", linestyle="--", alpha=0.7, label=f"Failure threshold ({FAILURE_THRESHOLD})")
    ax.set_xticks(x)
    ax.set_xticklabels(by_l2["L2"], rotation=25, ha="right")
    ax.set_ylabel("Mean score (0–100)")
    ax.set_ylim(0, 105)
    ax.legend(loc="lower right", fontsize=8)
    _style_axes(ax, "Mean score by attack type (L2)")
    plt.tight_layout()
    path = os.path.join(output_dir, f"{prefix}score_by_attack_type.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved {path}")


def fig_score_by_channel(analysis: dict, output_dir: str, prefix: str = "") -> None:
    """Bar chart: mean score by target channel with 95% CI."""
    by_ch = analysis["by_channel"]
    if by_ch.empty:
        return

    means = by_ch["mean_score"].values
    stds = by_ch.get("std_score", pd.Series(0, index=by_ch.index)).fillna(0).values
    ns = by_ch["n"].values
    yerr = np.array([_ci95_half_width(float(s), int(n)) for s, n in zip(stds, ns)])

    fig, ax = plt.subplots(figsize=(8, 5))
    x = np.arange(len(by_ch))
    ax.bar(x, means, yerr=yerr, color="#27ae60", edgecolor="#1e8449", linewidth=0.5,
           capsize=4, error_kw={"elinewidth": 1.5, "capthick": 1})
    ax.axhline(y=FAILURE_THRESHOLD, color="#c0392b", linestyle="--", alpha=0.7, label=f"Failure threshold ({FAILURE_THRESHOLD})")
    ax.set_xticks(x)
    ax.set_xticklabels([f"#{c}" for c in by_ch["target_channel"]], rotation=25, ha="right")
    ax.set_ylabel("Mean score (0–100)")
    ax.set_ylim(0, 105)
    ax.legend(loc="lower right", fontsize=8)
    _style_axes(ax, "Mean score by target channel")
    plt.tight_layout()
    path = os.path.join(output_dir, f"{prefix}score_by_channel.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved {path}")


def generate_figures_for_run(analysis: dict, output_dir: str, run_id: str) -> None:
    """Generate the two main figures: score by attack type and by channel (with 95% CI)."""
    if not HAS_MATPLOTLIB:
        print("  Skipping figures (matplotlib not installed). Run: pip install matplotlib")
        return
    prefix = f"{run_id}_"
    fig_score_by_attack_type(analysis, output_dir, prefix)
    fig_score_by_channel(analysis, output_dir, prefix)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate figures from red-team eval results")
    parser.add_argument(
        "--run",
        default=None,
        help="Single run ID (e.g. 2026-02-17_14-43-38). If not set, process all runs.",
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
        run_dir = os.path.join(args.runs_dir, args.run)
        if not os.path.isdir(run_dir):
            print(f"Error: Run directory not found: {run_dir}", file=sys.stderr)
            return 1
        runs = {args.run: load_run(run_dir, args.run)}
    else:
        runs = load_all_runs(args.runs_dir)

    if not runs:
        print("No runs found.", file=sys.stderr)
        return 1

    print(f"Processing {len(runs)} run(s): {list(runs.keys())}")

    analyses = {}
    for run_id, df in runs.items():
        analysis = analyze_run(df, run_id)
        analyses[run_id] = analysis
        if analysis["n_samples"] == 0:
            print(f"  {run_id}: no valid samples, skipping figures")
            continue
        print(f"  {run_id}: {analysis['n_samples']} samples, mean={analysis['overall_mean']:.1f}, failures={analysis['n_failures']}")
        generate_figures_for_run(analysis, args.output, run_id)

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
