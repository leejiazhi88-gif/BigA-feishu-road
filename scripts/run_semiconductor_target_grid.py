#!/usr/bin/env python3
"""Run a grid of semiconductor target definitions and compare backtest lift."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
import shutil
from typing import List, Tuple

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "bin" / "python"
BACKTEST = ROOT / "scripts" / "backtest_semiconductor_horizon.py"
EXPORT_DIR = ROOT / "exports" / "semiconductor_backtest"
GRID_DIR = ROOT / "exports" / "semiconductor_target_grid"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=2500)
    parser.add_argument("--l2", type=float, default=400)
    return parser.parse_args()


def target_specs() -> List[Tuple[str, int, int, float]]:
    return [
        ("absolute", 21, 60, 0.05),
        ("absolute", 61, 90, 0.08),
        ("absolute", 91, 120, 0.10),
        ("relative", 21, 60, 0.05),
        ("relative", 61, 90, 0.08),
        ("relative", 91, 120, 0.10),
    ]


def target_id(kind: str, start: int, end: int, threshold: float) -> str:
    return f"{kind}_{start}_{end}_{int(threshold * 100):02d}pct"


def run_target(kind: str, start: int, end: int, threshold: float, args: argparse.Namespace) -> pd.DataFrame:
    identifier = target_id(kind, start, end, threshold)
    command = [
        str(PYTHON),
        str(BACKTEST),
        "--target-kind",
        kind,
        "--horizon-start",
        str(start),
        "--horizon-end",
        str(end),
        "--target-return",
        str(threshold),
        "--l2",
        str(args.l2),
        "--steps",
        str(args.steps),
        "--model-profile",
        "target_grid",
    ]
    print(f"Running {identifier}")
    subprocess.run(command, cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    metrics = pd.read_csv(EXPORT_DIR / "metrics.csv")
    summary = pd.read_csv(EXPORT_DIR / "summary.csv")
    predictions = pd.read_csv(EXPORT_DIR / "predictions.csv")
    target_dir = GRID_DIR / identifier
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in ("metrics.csv", "summary.csv", "predictions.csv", "calibration.csv", "thresholds.csv", "latest_prediction.csv"):
        source = EXPORT_DIR / name
        if source.exists():
            shutil.copy2(source, target_dir / name)
    metrics["target_id"] = identifier
    metrics["target_kind"] = kind
    metrics["horizon_start"] = start
    metrics["horizon_end"] = end
    metrics["threshold"] = threshold
    metrics["walk_forward_event_rate"] = summary["walk_forward_test_event_rate"].iloc[0]
    metrics["prediction_rows"] = len(predictions)
    return metrics


def summarize(metrics: pd.DataFrame) -> pd.DataFrame:
    baseline = metrics[metrics["model"] == "expanding_rate_baseline"][
        ["target_id", "brier", "log_loss"]
    ].rename(columns={"brier": "baseline_brier", "log_loss": "baseline_log_loss"})
    compared = metrics.merge(baseline, on="target_id", how="left")
    compared["brier_lift"] = compared["baseline_brier"] - compared["brier"]
    compared["log_loss_lift"] = compared["baseline_log_loss"] - compared["log_loss"]
    models = compared[~compared["model"].str.contains("baseline")].copy()
    best = models.sort_values(["target_id", "brier_lift"], ascending=[True, False]).groupby(
        "target_id", as_index=False
    ).head(1)
    return best.sort_values("brier_lift", ascending=False)


def main() -> None:
    args = parse_args()
    GRID_DIR.mkdir(parents=True, exist_ok=True)
    frames = [run_target(kind, start, end, threshold, args) for kind, start, end, threshold in target_specs()]
    metrics = pd.concat(frames, ignore_index=True)
    metrics.to_csv(GRID_DIR / "all_metrics.csv", index=False)
    summary = summarize(metrics)
    summary.to_csv(GRID_DIR / "best_by_target.csv", index=False)
    print(summary[[
        "target_id",
        "model",
        "walk_forward_event_rate",
        "brier",
        "baseline_brier",
        "brier_lift",
        "log_loss_lift",
        "roc_auc",
    ]].to_string(index=False))
    print(f"Outputs: {GRID_DIR}")


if __name__ == "__main__":
    main()
