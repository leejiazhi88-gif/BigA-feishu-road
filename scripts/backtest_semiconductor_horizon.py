#!/usr/bin/env python3
"""Backtest semiconductor sector probabilities for a medium-horizon price target."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW_SW_DIR = ROOT / "data" / "raw" / "sw_daily"
SEMI_BREADTH_PATH = ROOT / "data" / "processed" / "semiconductor_breadth_history.csv"
SEMI_FINANCE_PATH = ROOT / "data" / "processed" / "semiconductor_finance_history.csv"
EXPORT_DIR = ROOT / "exports" / "semiconductor_backtest"
SEMI_CODE = "801081_SI"
BENCH_CODE = "801003_SI"
TARGET_LABEL = "target_label"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-start", default="20180102")
    parser.add_argument("--test-start", default="20220104")
    parser.add_argument("--horizon-start", type=int, default=91)
    parser.add_argument("--horizon-end", type=int, default=120)
    parser.add_argument("--target-return", type=float, default=0.10)
    parser.add_argument(
        "--target-kind",
        choices=("absolute", "relative"),
        default="absolute",
        help="Absolute future mean return or future mean excess return versus the benchmark.",
    )
    parser.add_argument("--test-block", type=int, default=20)
    parser.add_argument("--l2", type=float, default=1.5)
    parser.add_argument("--learning-rate", type=float, default=0.04)
    parser.add_argument("--steps", type=int, default=5000)
    parser.add_argument(
        "--model-profile",
        choices=("full", "target_grid"),
        default="full",
        help="Use fewer core feature variants when scanning many target definitions.",
    )
    return parser.parse_args()


def load_index_history(code: str) -> pd.DataFrame:
    path = RAW_SW_DIR / f"{code}.csv"
    frame = pd.read_csv(path, dtype={"trade_date": str})
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d")
    numeric = ["open", "high", "low", "close", "pct_change", "amount", "pe", "pb"]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.sort_values("trade_date").drop_duplicates("trade_date", keep="last")


def build_sector_context(bench: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for path in RAW_SW_DIR.glob("*.csv"):
        if path.stem == BENCH_CODE:
            continue
        frame = pd.read_csv(path, dtype={"trade_date": str}, usecols=["ts_code", "trade_date", "close", "amount"])
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
        frames.append(frame)
    sectors = pd.concat(frames, ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
    sectors = sectors.merge(bench, on="trade_date", how="inner").sort_values(["ts_code", "trade_date"])
    group = sectors.groupby("ts_code", group_keys=False)
    for window in (20, 60, 120):
        sectors[f"sector_ret_{window}"] = group["close"].pct_change(window)
        sectors[f"sector_rel_{window}"] = sectors[f"sector_ret_{window}"] - sectors[f"bench_ret_{window}"]
    sectors["sector_amount_share"] = sectors["amount"] / sectors.groupby("trade_date")["amount"].transform("sum")
    sectors["sector_amount_share_20"] = group["sector_amount_share"].transform(
        lambda series: series.rolling(20, min_periods=12).mean()
    )
    sectors["sector_amount_share_60"] = group["sector_amount_share"].transform(
        lambda series: series.rolling(60, min_periods=30).mean()
    )
    sectors["sector_amount_share_ratio"] = sectors["sector_amount_share_20"] / sectors["sector_amount_share_60"]
    date_group = sectors.groupby("trade_date")
    for column in ("sector_rel_20", "sector_rel_60", "sector_rel_120", "sector_amount_share_ratio"):
        sectors[f"{column}_rank"] = date_group[column].rank(pct=True)
    context = sectors[sectors["ts_code"] == "801081.SI"][
        [
            "trade_date",
            "sector_rel_20_rank",
            "sector_rel_60_rank",
            "sector_rel_120_rank",
            "sector_amount_share_ratio_rank",
        ]
    ].rename(
        columns={
            "sector_rel_20_rank": "semi_sector_rank_20",
            "sector_rel_60_rank": "semi_sector_rank_60",
            "sector_rel_120_rank": "semi_sector_rank_120",
            "sector_amount_share_ratio_rank": "semi_sector_attention_rank",
        }
    )
    return context


def rolling_rank_last(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    def rank(values: np.ndarray) -> float:
        valid = values[np.isfinite(values)]
        if len(valid) < min_periods:
            return np.nan
        return float((valid <= valid[-1]).sum() / len(valid))

    return series.rolling(window, min_periods=min_periods).apply(rank, raw=True)


def build_dataset(args: argparse.Namespace) -> pd.DataFrame:
    semi = load_index_history(SEMI_CODE).rename(
        columns={
            "close": "semi_close",
            "high": "semi_high",
            "low": "semi_low",
            "amount": "semi_amount",
            "pe": "semi_pe",
            "pb": "semi_pb",
        }
    )
    bench = load_index_history(BENCH_CODE)[["trade_date", "close"]].rename(
        columns={"close": "bench_close"}
    )
    for window in (5, 10, 20, 60, 120):
        bench[f"bench_ret_{window}"] = bench["bench_close"].pct_change(window)
    data = semi.merge(bench, on="trade_date", how="inner").sort_values("trade_date")
    for window in (5, 10, 20, 60, 120):
        data[f"ret_{window}"] = data["semi_close"].pct_change(window)
        data[f"rel_ret_{window}"] = data[f"ret_{window}"] - data[f"bench_ret_{window}"]
    data["daily_ret"] = data["semi_close"].pct_change()
    data["bench_daily_ret"] = data["bench_close"].pct_change()
    data["vol_20"] = data["daily_ret"].rolling(20, min_periods=12).std()
    data["vol_60"] = data["daily_ret"].rolling(60, min_periods=30).std()
    data["bench_vol_20"] = data["bench_daily_ret"].rolling(20, min_periods=12).std()
    data["bench_vol_60"] = data["bench_daily_ret"].rolling(60, min_periods=30).std()
    data["ma_gap_20"] = data["semi_close"] / data["semi_close"].rolling(20, min_periods=12).mean() - 1
    data["ma_gap_60"] = data["semi_close"] / data["semi_close"].rolling(60, min_periods=30).mean() - 1
    data["ma_gap_120"] = data["semi_close"] / data["semi_close"].rolling(120, min_periods=60).mean() - 1
    data["drawdown_120"] = data["semi_close"] / data["semi_high"].rolling(120, min_periods=60).max() - 1
    data["amount_ratio_20_60"] = (
        data["semi_amount"].rolling(20, min_periods=12).mean()
        / data["semi_amount"].rolling(60, min_periods=30).mean()
    )
    data["pe_temp"] = rolling_rank_last(data["semi_pe"], 756, 126)
    data["pb_temp"] = rolling_rank_last(data["semi_pb"], 756, 126)
    data["bench_ma_gap_20"] = data["bench_close"] / data["bench_close"].rolling(20, min_periods=12).mean() - 1
    data["bench_ma_gap_60"] = data["bench_close"] / data["bench_close"].rolling(60, min_periods=30).mean() - 1
    data["bench_ma_gap_120"] = data["bench_close"] / data["bench_close"].rolling(120, min_periods=60).mean() - 1
    if SEMI_BREADTH_PATH.exists():
        breadth = pd.read_csv(SEMI_BREADTH_PATH, parse_dates=["trade_date"])
        data = data.merge(breadth, on="trade_date", how="left")
    if SEMI_FINANCE_PATH.exists():
        finance = pd.read_csv(SEMI_FINANCE_PATH, parse_dates=["available_date"]).sort_values("available_date")
        data = pd.merge_asof(
            data.sort_values("trade_date"),
            finance,
            left_on="trade_date",
            right_on="available_date",
            direction="backward",
        )
    data = data.merge(build_sector_context(bench), on="trade_date", how="left")
    future_columns = []
    for offset in range(args.horizon_start, args.horizon_end + 1):
        name = f"future_close_{offset}"
        data[name] = data["semi_close"].shift(-offset)
        future_columns.append(name)
    data["future_mean_91_120"] = data[future_columns].mean(axis=1)
    data["future_mean_return"] = data["future_mean_91_120"] / data["semi_close"] - 1
    future_benchmark_columns = []
    for offset in range(args.horizon_start, args.horizon_end + 1):
        name = f"future_bench_close_{offset}"
        data[name] = data["bench_close"].shift(-offset)
        future_benchmark_columns.append(name)
    data["future_bench_mean"] = data[future_benchmark_columns].mean(axis=1)
    data["future_bench_mean_return"] = data["future_bench_mean"] / data["bench_close"] - 1
    data["future_mean_excess_return"] = data["future_mean_return"] - data["future_bench_mean_return"]
    target_value = (
        data["future_mean_return"]
        if args.target_kind == "absolute"
        else data["future_mean_excess_return"]
    )
    data[TARGET_LABEL] = (target_value >= args.target_return).astype(float)
    data.loc[data[future_columns].isna().any(axis=1), TARGET_LABEL] = np.nan
    data.loc[data[future_benchmark_columns].isna().any(axis=1), TARGET_LABEL] = np.nan
    return data.drop(columns=future_columns + future_benchmark_columns)


def feature_sets() -> Dict[str, List[str]]:
    price_features = [
        "ret_5",
        "ret_10",
        "ret_20",
        "ret_60",
        "ret_120",
        "rel_ret_20",
        "rel_ret_60",
        "rel_ret_120",
        "vol_20",
        "vol_60",
        "ma_gap_20",
        "ma_gap_60",
        "ma_gap_120",
        "drawdown_120",
        "amount_ratio_20_60",
        "pe_temp",
        "pb_temp",
    ]
    regime_features = price_features + [
        "bench_ret_20",
        "bench_ret_60",
        "bench_ret_120",
        "bench_vol_20",
        "bench_vol_60",
        "bench_ma_gap_20",
        "bench_ma_gap_60",
        "bench_ma_gap_120",
    ]
    breadth_features = [
        "semi_breadth_above_20",
        "semi_breadth_above_60",
        "semi_breadth_above_120",
        "semi_breadth_positive_20",
        "semi_breadth_positive_60",
        "semi_breadth_high_60",
        "semi_breadth_median_ret_20",
        "semi_breadth_median_ret_60",
        "semi_breadth_leader_gap_60",
    ]
    cross_section_features = [
        "semi_sector_rank_20",
        "semi_sector_rank_60",
        "semi_sector_rank_120",
        "semi_sector_attention_rank",
    ]
    finance_features = [
        "semi_finance_coverage",
        "semi_finance_sales_positive",
        "semi_finance_profit_positive",
        "semi_finance_cash_positive",
        "semi_finance_roe_positive",
        "semi_finance_sales_median",
        "semi_finance_profit_median",
        "semi_finance_profit_positive_delta",
        "semi_finance_profit_median_delta",
    ]
    available_breadth = [column for column in breadth_features if SEMI_BREADTH_PATH.exists()]
    sets = {
        "price_only": price_features,
        "price_plus_market_regime": regime_features,
        "price_regime_cross_section": regime_features + cross_section_features,
    }
    if available_breadth:
        sets["price_regime_breadth"] = regime_features + available_breadth
        sets["price_regime_cross_breadth"] = regime_features + cross_section_features + available_breadth
    if SEMI_FINANCE_PATH.exists():
        sets["price_regime_finance"] = regime_features + finance_features
        if available_breadth:
            sets["price_regime_breadth_finance"] = regime_features + available_breadth + finance_features
    return sets


def selected_feature_sets(profile: str) -> Dict[str, List[str]]:
    sets = feature_sets()
    if profile == "target_grid":
        preferred = [
            "price_only",
            "price_plus_market_regime",
            "price_regime_cross_breadth",
        ]
        return {name: sets[name] for name in preferred if name in sets}
    return sets


def standardize(train: pd.DataFrame, test: pd.DataFrame, columns: List[str]) -> Tuple[np.ndarray, np.ndarray]:
    train_x = train[columns].copy()
    test_x = test[columns].copy()
    medians = train_x.median()
    train_x = train_x.fillna(medians)
    test_x = test_x.fillna(medians)
    means = train_x.mean()
    stds = train_x.std(ddof=0).replace(0, 1).fillna(1)
    train_matrix = ((train_x - means) / stds).clip(-6, 6).to_numpy(dtype=float)
    test_matrix = ((test_x - means) / stds).clip(-6, 6).to_numpy(dtype=float)
    return train_matrix, test_matrix


def sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -30, 30)
    return 1 / (1 + np.exp(-clipped))


def fit_logistic(
    x: np.ndarray,
    y: np.ndarray,
    l2: float,
    learning_rate: float,
    steps: int,
) -> np.ndarray:
    design = np.column_stack([np.ones(len(x)), x])
    weights = np.zeros(design.shape[1])
    base = float(np.clip(y.mean(), 1e-5, 1 - 1e-5))
    weights[0] = np.log(base / (1 - base))
    penalty = np.ones_like(weights)
    penalty[0] = 0
    for _ in range(steps):
        prediction = sigmoid(design @ weights)
        gradient = (design.T @ (prediction - y)) / len(y) + l2 * penalty * weights / len(y)
        weights -= learning_rate * gradient
    return weights


def predict_logistic(x: np.ndarray, weights: np.ndarray) -> np.ndarray:
    design = np.column_stack([np.ones(len(x)), x])
    return sigmoid(design @ weights)


def state_probability(train: pd.DataFrame, test: pd.DataFrame, minimum: int = 35) -> np.ndarray:
    train = train.copy()
    test = test.copy()
    regime_columns = [
        ("rel_ret_60", "relative_trend_bucket"),
        ("ma_gap_60", "price_position_bucket"),
        ("drawdown_120", "drawdown_bucket"),
    ]
    global_rate = float(train[TARGET_LABEL].mean())
    keys = []
    for column, bucket_name in regime_columns:
        quantiles = train[column].quantile([0.33, 0.66]).to_numpy()
        bins = [-np.inf, quantiles[0], quantiles[1], np.inf]
        train[bucket_name] = pd.cut(train[column], bins=bins, labels=False, include_lowest=True)
        test[bucket_name] = pd.cut(test[column], bins=bins, labels=False, include_lowest=True)
        keys.append(bucket_name)
    full = train.groupby(keys, dropna=False)[TARGET_LABEL].agg(["mean", "size"]).reset_index()
    pair = train.groupby(keys[:2], dropna=False)[TARGET_LABEL].agg(["mean", "size"]).reset_index()
    single = train.groupby(keys[:1], dropna=False)[TARGET_LABEL].agg(["mean", "size"]).reset_index()
    probabilities = []
    for row in test.itertuples():
        selectors = {key: getattr(row, key) for key in keys}
        choices = [
            (full, keys),
            (pair, keys[:2]),
            (single, keys[:1]),
        ]
        probability = global_rate
        for table, table_keys in choices:
            mask = pd.Series(True, index=table.index)
            for key in table_keys:
                mask &= table[key] == selectors[key]
            matched = table[mask]
            if not matched.empty and int(matched["size"].iloc[0]) >= minimum:
                probability = float(matched["mean"].iloc[0])
                break
        probabilities.append(probability)
    return np.array(probabilities, dtype=float)


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    clipped = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(clipped) + (1 - y) * np.log(1 - clipped)))


def roc_auc(y: np.ndarray, p: np.ndarray) -> float:
    positive = y == 1
    negative = y == 0
    if positive.sum() == 0 or negative.sum() == 0:
        return np.nan
    ranks = pd.Series(p).rank(method="average").to_numpy()
    rank_sum = ranks[positive].sum()
    return float((rank_sum - positive.sum() * (positive.sum() + 1) / 2) / (positive.sum() * negative.sum()))


def calibration_table(predictions: pd.DataFrame) -> pd.DataFrame:
    frame = predictions.copy()
    frame["prob_bin"] = pd.cut(
        frame["model_probability"],
        bins=[-0.001, 0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 1.0],
        labels=["0-10%", "10-20%", "20-35%", "35-50%", "50-65%", "65-80%", "80-100%"],
    )
    grouped = frame.groupby("prob_bin", observed=False)
    return grouped.agg(
        samples=("target", "size"),
        predicted_probability=("model_probability", "mean"),
        realized_rate=("target", "mean"),
        avg_future_mean_return=("future_mean_return", "mean"),
    ).reset_index()


def threshold_table(predictions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for threshold in (0.35, 0.45, 0.55, 0.65):
        selected = predictions[predictions["model_probability"] >= threshold]
        rows.append(
            {
                "threshold": threshold,
                "signals": len(selected),
                "signal_rate": len(selected) / len(predictions),
                "hit_rate": selected["target"].mean() if len(selected) else np.nan,
                "avg_future_mean_return": selected["future_mean_return"].mean() if len(selected) else np.nan,
            }
        )
    return pd.DataFrame(rows)


def metrics(predictions: pd.DataFrame) -> pd.DataFrame:
    y = predictions["target"].to_numpy(dtype=float)
    model_p = predictions["model_probability"].to_numpy(dtype=float)
    baseline_p = predictions["expanding_baseline_probability"].to_numpy(dtype=float)
    rolling_p = predictions["rolling_baseline_probability"].to_numpy(dtype=float)
    rows: List[Dict[str, float]] = []
    for name, probs in (
        (str(predictions["model_name"].iloc[0]), model_p),
        ("expanding_rate_baseline", baseline_p),
        ("rolling_504_rate_baseline", rolling_p),
    ):
        rows.append(
            {
                "model": name,
                "samples": len(predictions),
                "event_rate": y.mean(),
                "mean_probability": probs.mean(),
                "brier": brier(y, probs),
                "log_loss": log_loss(y, probs),
                "roc_auc": roc_auc(y, probs),
            }
        )
    return pd.DataFrame(rows)


def walk_forward_backtest(
    data: pd.DataFrame,
    args: argparse.Namespace,
    model_name: str,
    columns: List[str],
    train_window: int | None = None,
) -> pd.DataFrame:
    usable = data.dropna(subset=columns + [TARGET_LABEL]).copy()
    usable = usable[usable["trade_date"] >= pd.Timestamp(args.train_start)]
    usable = usable.sort_values("trade_date").reset_index(drop=True)
    test_rows = usable[usable["trade_date"] >= pd.Timestamp(args.test_start)].copy()
    predictions = []
    dates = test_rows["trade_date"].tolist()
    for start in range(0, len(dates), args.test_block):
        block_dates = dates[start : start + args.test_block]
        if not block_dates:
            continue
        test = test_rows[test_rows["trade_date"].isin(block_dates)].copy()
        first_test_date = test["trade_date"].min()
        first_test_pos = int(usable.index[usable["trade_date"] == first_test_date][0])
        train_end_pos = first_test_pos - args.horizon_end - 1
        train = usable.iloc[: max(train_end_pos + 1, 0)].copy()
        if train_window:
            train = train.tail(train_window).copy()
        if len(train) < 450 or train[TARGET_LABEL].nunique() < 2:
            continue
        train_x, test_x = standardize(train, test, columns)
        train_y = train[TARGET_LABEL].to_numpy(dtype=float)
        weights = fit_logistic(train_x, train_y, args.l2, args.learning_rate, args.steps)
        test["model_probability"] = predict_logistic(test_x, weights)
        test["expanding_baseline_probability"] = float(train_y.mean())
        rolling_train = train_y[-min(len(train_y), 504) :]
        test["rolling_baseline_probability"] = float(rolling_train.mean())
        test["train_samples"] = len(train)
        test["train_event_rate"] = float(train_y.mean())
        test["model_name"] = model_name
        predictions.append(test)
    if not predictions:
        raise SystemExit("No walk-forward predictions were created. Check dates or target coverage.")
    result = pd.concat(predictions, ignore_index=True)
    result["target"] = result[TARGET_LABEL].astype(int)
    return result[
        [
            "trade_date",
            "semi_close",
            "future_mean_91_120",
            "future_mean_return",
            "future_bench_mean_return",
            "future_mean_excess_return",
            "target",
            "model_name",
            "model_probability",
            "expanding_baseline_probability",
            "rolling_baseline_probability",
            "train_samples",
            "train_event_rate",
        ]
    ]


def walk_forward_state_backtest(data: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    columns = feature_sets()["price_only"]
    usable = data.dropna(subset=columns + [TARGET_LABEL]).copy()
    usable = usable[usable["trade_date"] >= pd.Timestamp(args.train_start)]
    usable = usable.sort_values("trade_date").reset_index(drop=True)
    test_rows = usable[usable["trade_date"] >= pd.Timestamp(args.test_start)].copy()
    predictions = []
    dates = test_rows["trade_date"].tolist()
    for start in range(0, len(dates), args.test_block):
        block_dates = dates[start : start + args.test_block]
        test = test_rows[test_rows["trade_date"].isin(block_dates)].copy()
        first_test_date = test["trade_date"].min()
        first_test_pos = int(usable.index[usable["trade_date"] == first_test_date][0])
        train_end_pos = first_test_pos - args.horizon_end - 1
        train = usable.iloc[: max(train_end_pos + 1, 0)].copy()
        if len(train) < 450 or train[TARGET_LABEL].nunique() < 2:
            continue
        train_y = train[TARGET_LABEL].to_numpy(dtype=float)
        test["model_probability"] = state_probability(train, test)
        test["expanding_baseline_probability"] = float(train_y.mean())
        test["rolling_baseline_probability"] = float(train_y[-min(len(train_y), 504) :].mean())
        test["train_samples"] = len(train)
        test["train_event_rate"] = float(train_y.mean())
        test["model_name"] = "state_bucket_price"
        predictions.append(test)
    result = pd.concat(predictions, ignore_index=True)
    result["target"] = result[TARGET_LABEL].astype(int)
    return result[
        [
            "trade_date",
            "semi_close",
            "future_mean_91_120",
            "future_mean_return",
            "future_bench_mean_return",
            "future_mean_excess_return",
            "target",
            "model_name",
            "model_probability",
            "expanding_baseline_probability",
            "rolling_baseline_probability",
            "train_samples",
            "train_event_rate",
        ]
    ]


def write_outputs(data: pd.DataFrame, predictions_by_model: Dict[str, pd.DataFrame], args: argparse.Namespace) -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    predictions = pd.concat(predictions_by_model.values(), ignore_index=True)
    predictions.to_csv(EXPORT_DIR / "predictions.csv", index=False)
    metric_frames = [metrics(frame) for frame in predictions_by_model.values()]
    metric_frame = pd.concat(metric_frames, ignore_index=True).drop_duplicates("model", keep="first")
    metric_frame.to_csv(EXPORT_DIR / "metrics.csv", index=False)
    calibrations = []
    thresholds = []
    for model_name, frame in predictions_by_model.items():
        calibration = calibration_table(frame)
        calibration.insert(0, "model", model_name)
        calibrations.append(calibration)
        threshold = threshold_table(frame)
        threshold.insert(0, "model", model_name)
        thresholds.append(threshold)
    pd.concat(calibrations, ignore_index=True).to_csv(EXPORT_DIR / "calibration.csv", index=False)
    pd.concat(thresholds, ignore_index=True).to_csv(EXPORT_DIR / "thresholds.csv", index=False)
    target_rows = data.dropna(subset=[TARGET_LABEL])
    summary = pd.DataFrame(
        [
            {
                "target": "mean close of trading days 91-120 vs same-day close >= 10%",
                "target_kind": args.target_kind,
                "horizon_start": args.horizon_start,
                "horizon_end": args.horizon_end,
                "target_return": args.target_return,
                "train_start": args.train_start,
                "test_start": args.test_start,
                "available_label_start": target_rows["trade_date"].min().strftime("%Y-%m-%d"),
                "available_label_end": target_rows["trade_date"].max().strftime("%Y-%m-%d"),
                "all_labeled_samples": len(target_rows),
                "all_labeled_event_rate": target_rows[TARGET_LABEL].mean(),
                "walk_forward_test_samples": len(next(iter(predictions_by_model.values()))),
                "walk_forward_test_event_rate": next(iter(predictions_by_model.values()))["target"].mean(),
            }
        ]
    )
    summary.to_csv(EXPORT_DIR / "summary.csv", index=False)


def latest_prediction(
    data: pd.DataFrame,
    args: argparse.Namespace,
    model_name: str,
    columns: List[str],
    train_window: int | None = None,
) -> pd.DataFrame:
    labeled = data.dropna(subset=columns + [TARGET_LABEL]).copy()
    labeled = labeled[labeled["trade_date"] >= pd.Timestamp(args.train_start)]
    if train_window:
        labeled = labeled.tail(train_window).copy()
    latest = data.dropna(subset=columns).sort_values("trade_date").tail(1).copy()
    train_x, latest_x = standardize(labeled, latest, columns)
    train_y = labeled[TARGET_LABEL].to_numpy(dtype=float)
    weights = fit_logistic(train_x, train_y, args.l2, args.learning_rate, args.steps)
    latest["model_probability"] = predict_logistic(latest_x, weights)
    latest["expanding_baseline_probability"] = float(train_y.mean())
    latest["rolling_baseline_probability"] = float(train_y[-min(len(train_y), 504) :].mean())
    latest["train_samples"] = len(labeled)
    latest["train_event_rate"] = float(train_y.mean())
    latest["model_name"] = model_name
    result = latest[
        [
            "model_name",
            "trade_date",
            "semi_close",
            "model_probability",
            "expanding_baseline_probability",
            "rolling_baseline_probability",
            "train_samples",
            "train_event_rate",
        ]
    ].copy()
    return result


def main() -> None:
    args = parse_args()
    data = build_dataset(args)
    specs = []
    for name, columns in selected_feature_sets(args.model_profile).items():
        specs.append((name, columns, None))
        specs.append((f"{name}_rolling756", columns, 756))
    predictions_by_model = {
        name: walk_forward_backtest(data, args, name, columns, train_window)
        for name, columns, train_window in specs
    }
    predictions_by_model["state_bucket_price"] = walk_forward_state_backtest(data, args)
    write_outputs(data, predictions_by_model, args)
    latest = pd.concat(
        [latest_prediction(data, args, name, columns, train_window) for name, columns, train_window in specs],
        ignore_index=True,
    )
    latest.to_csv(EXPORT_DIR / "latest_prediction.csv", index=False)
    target_text = (
        "absolute mean return"
        if args.target_kind == "absolute"
        else "mean excess return versus benchmark"
    )
    print(
        f"Target: trading-day mean from day {args.horizon_start} to day {args.horizon_end} "
        f"{target_text} >= {args.target_return:.1%}"
    )
    print(pd.concat([metrics(frame) for frame in predictions_by_model.values()]).drop_duplicates("model").to_string(index=False))
    for name, frame in predictions_by_model.items():
        print(f"\nCalibration: {name}")
        print(calibration_table(frame).to_string(index=False))
        print(f"\nProbability thresholds: {name}")
        print(threshold_table(frame).to_string(index=False))
    print("\nLatest available day:")
    print(latest.to_string(index=False))
    print(f"\nOutputs: {EXPORT_DIR}")


if __name__ == "__main__":
    main()
