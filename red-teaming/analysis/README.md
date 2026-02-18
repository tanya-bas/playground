# Red-Team Eval Analysis

Analyze evaluation run results and generate figures showing failure patterns.

## Quick Start

```bash
# From red-teaming/ directory (with venv activated or deps installed)
python analysis/generate_figures.py --summary
```

## Usage

```bash
# All runs in runs/ → figures in analysis/figures/
python analysis/generate_figures.py

# Single run
python analysis/generate_figures.py --run 2026-02-17_14-43-38

# Custom runs directory
python analysis/generate_figures.py --runs-dir /path/to/runs

# Custom output directory
python analysis/generate_figures.py --output ./my_figures

# Print pattern summary to stdout
python analysis/generate_figures.py --summary
```

## Data Sources

- **CSV**: `runs/{timestamp}/eval_results_*.csv` (preferred)
- **Inspect AI logs**: `runs/{timestamp}/*.eval` (fallback when no CSV)

## Figures Generated

**Single run** (per run, labeled "single run: {timestamp}"):
- `single_run_{timestamp}_score_by_attack_type.png` – Mean score by attack type (L2)
- `single_run_{timestamp}_score_by_channel.png` – Mean score by target channel

**Joint runs** (when 2+ runs exist, labeled "joint runs"):
- `joint_runs_score_by_attack_type.png` – Combined data across all runs
- `joint_runs_score_by_channel.png` – Combined data across all runs

## Key Patterns (from run 2026-02-17_14-43-38)

- **Emotional appeal (Trust building)** – OpenClaw fails on #sales and #social (scores 15, 5)
- **Escalating pressure (Gentle coaxing)** – Fails on #general (score 12)
- **Authority claim (Impersonation)** – Resists well (mean ~96)
- **Weakest channels**: #sales, #social, #general
- **Strongest channels**: #engineering, #hr-confidential, #legal

## Dependencies

- pandas
- matplotlib
