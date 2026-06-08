# Shenwan L2 Sector Radar

Local research tool for identifying Shenwan 2021 level-2 sectors that are:

- starting to strengthen before they become crowded
- already in a confirmed trend
- hot enough that new entries deserve caution
- losing trend quality after a strong run

## Run

The pipeline reads `TUSHARE_TOKEN` from the environment. If it is missing, it
prompts for the token without echoing it.

```bash
.venv/bin/python scripts/build_sector_radar.py
```

The first run fetches and caches Shenwan daily sector data, current Shenwan L2
members, recent A-share daily rows for breadth, and financial-indicator report
periods under `data/raw/`. Later runs reuse cached history and refresh the
latest requested window.

## Outputs

- `reports/latest.html`: browsable sector radar
- `reports/YYYYMMDD.html`: dated report snapshot
- `exports/sector_scores_latest.csv`: full Shenwan level-2 score table
- `exports/startup_candidates_latest.csv`: sectors that look early or improving
- `exports/risk_warnings_latest.csv`: sectors that look crowded or weakening
- `exports/stage_migrations_latest.csv`: current stage and recent stage changes

## Current Scope

The current scoring model uses:

- Shenwan level-2 index action: relative trend versus the Shenwan A-share
  index, 20/60/120-session return, ranking improvement, turnover attention,
  volatility, and PE/PB temperature where available
- constituent breadth: member stocks above 20/60/120-session averages, positive
  member returns, fresh 60-session highs, and leader-versus-median spread
- financial prosperity summary: current constituent coverage for revenue
  growth, profit growth, profit improvement versus the prior available report
  period, and operating-cash-flow quality

Funding persistence and event/catalyst evidence are the next validation layers.
The report already separates confirmation from refutation so those evidence
sources can be added without hiding the reasoning in a single score.
