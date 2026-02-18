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

## Figures Generated (per run)

1. **score_by_attack_type.png** – Mean score by attack type (L2) with 95% CI
2. **score_by_channel.png** – Mean score by target channel with 95% CI

## Key Patterns (from run 2026-02-17_14-43-38)

- **Emotional appeal (Trust building)** – OpenClaw fails on #sales and #social (scores 15, 5)
- **Escalating pressure (Gentle coaxing)** – Fails on #general (score 12)
- **Authority claim (Impersonation)** – Resists well (mean ~96)
- **Weakest channels**: #sales, #social, #general
- **Strongest channels**: #engineering, #hr-confidential, #legal

## Dependencies

- pandas
- matplotlib
- scipy (for 95% CI t-distribution; falls back to z≈1.96 if not installed)
