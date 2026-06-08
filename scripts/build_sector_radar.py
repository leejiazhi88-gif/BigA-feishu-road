#!/usr/bin/env python3
"""Build a Shenwan 2021 level-2 sector radar from Tushare data."""

from __future__ import annotations

import argparse
import getpass
import html
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import tushare as ts


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
EXPORT_DIR = ROOT / "exports"
REPORT_DIR = ROOT / "reports"
L2_CLASSIFY_PATH = RAW_DIR / "sw2021_l2_classify.csv"
SW_DAILY_DIR = RAW_DIR / "sw_daily"
MEMBER_DIR = RAW_DIR / "sw_members"
ASHARE_DAILY_DIR = RAW_DIR / "ashare_daily"
FINANCIAL_DIR = RAW_DIR / "financials"
BENCHMARK_CODE = "801003.SI"
BENCHMARK_NAME = "申万Ａ指"
STAGE_ORDER = [
    "潜伏观察",
    "早期启动",
    "趋势确认",
    "主升扩散",
    "高位拥挤",
    "钝化转弱",
    "退潮回避",
]
ACTION_ORDER = {
    "优先挖掘": 0,
    "继续跟踪核心": 1,
    "观察验证": 2,
    "持有但不追": 3,
    "收紧风控": 4,
    "回避": 5,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="20180101")
    parser.add_argument("--end-date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--refresh", action="store_true", help="Refetch cached daily data.")
    parser.add_argument("--pause", type=float, default=0.08, help="Seconds between API calls.")
    parser.add_argument(
        "--breadth-days",
        type=int,
        default=210,
        help="Calendar days of A-share daily history used for member breadth.",
    )
    return parser.parse_args()


def ensure_dirs() -> None:
    for path in (
        RAW_DIR,
        SW_DAILY_DIR,
        MEMBER_DIR,
        ASHARE_DAILY_DIR,
        FINANCIAL_DIR,
        EXPORT_DIR,
        REPORT_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def tushare_client():
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        token = getpass.getpass("TUSHARE_TOKEN: ").strip()
    if not token:
        raise SystemExit("TUSHARE_TOKEN is required.")
    ts.set_token(token)
    return ts.pro_api()


def call_with_retry(func, *args, **kwargs) -> pd.DataFrame:
    last_error = None
    for attempt in range(4):
        try:
            result = func(*args, **kwargs)
            if result is None:
                return pd.DataFrame()
            return result.copy()
        except Exception as exc:  # Tushare raises multiple transport error types.
            last_error = exc
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"Tushare call failed after retries: {last_error}") from last_error


def fetch_l2_classify(pro, refresh: bool) -> pd.DataFrame:
    if L2_CLASSIFY_PATH.exists() and not refresh:
        return pd.read_csv(L2_CLASSIFY_PATH, dtype=str)
    l2 = call_with_retry(
        pro.index_classify,
        level="L2",
        src="SW2021",
        fields="index_code,industry_name,level,industry_code,parent_code,is_pub",
    )
    l2["ts_code"] = l2["index_code"].astype(str)
    l2 = l2[l2["is_pub"].fillna("1").astype(str) == "1"].copy()
    l2.to_csv(L2_CLASSIFY_PATH, index=False)
    return l2


def fetch_sw_daily(
    pro,
    ts_code: str,
    start_date: str,
    end_date: str,
    refresh: bool,
    pause: float,
) -> pd.DataFrame:
    cache_path = SW_DAILY_DIR / f"{ts_code.replace('.', '_')}.csv"
    if cache_path.exists() and not refresh:
        cached = pd.read_csv(cache_path, dtype={"trade_date": str})
        if not cached.empty:
            first = datetime.strptime(str(cached["trade_date"].min()), "%Y%m%d").date()
            last = str(cached["trade_date"].max())
            requested_start = datetime.strptime(start_date, "%Y%m%d").date()
            covers_requested_start = first <= requested_start + timedelta(days=10)
            if covers_requested_start and last >= end_date:
                return cached

    frame = call_with_retry(
        pro.sw_daily,
        ts_code=ts_code,
        start_date=start_date,
        end_date=end_date,
        fields=(
            "ts_code,trade_date,name,open,high,low,close,change,pct_change,"
            "vol,amount,pe,pb,float_mv,total_mv"
        ),
    )
    if frame.empty:
        return frame
    frame["trade_date"] = frame["trade_date"].astype(str)
    frame.sort_values("trade_date").to_csv(cache_path, index=False)
    time.sleep(pause)
    return frame


def load_market_history(pro, l2: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    codes = l2["ts_code"].dropna().astype(str).tolist()
    frames = [
        fetch_sw_daily(pro, BENCHMARK_CODE, args.start_date, args.end_date, args.refresh, args.pause)
    ]
    for index, code in enumerate(codes, start=1):
        print(f"Fetching Shenwan L2 {index:03d}/{len(codes)} {code}")
        try:
            frame = fetch_sw_daily(pro, code, args.start_date, args.end_date, args.refresh, args.pause)
        except RuntimeError as exc:
            print(f"Skipping {code}: {exc}")
            continue
        if not frame.empty:
            frames.append(frame)
    history = pd.concat(frames, ignore_index=True)
    history["trade_date"] = pd.to_datetime(history["trade_date"], format="%Y%m%d")
    for column in ("close", "pct_change", "amount", "pe", "pb", "float_mv"):
        history[column] = pd.to_numeric(history[column], errors="coerce")
    history = history.sort_values(["ts_code", "trade_date"]).drop_duplicates(
        ["ts_code", "trade_date"], keep="last"
    )
    return history


def fetch_current_members(pro, l2_code: str, refresh: bool, pause: float) -> pd.DataFrame:
    cache_path = MEMBER_DIR / f"{l2_code.replace('.', '_')}.csv"
    if cache_path.exists() and not refresh:
        return pd.read_csv(cache_path, dtype=str)
    members = call_with_retry(
        pro.index_member_all,
        l2_code=l2_code,
        is_new="Y",
        fields="l1_code,l2_code,l2_name,ts_code,name,in_date,out_date,is_new",
    )
    if members.empty:
        return members
    members.to_csv(cache_path, index=False)
    time.sleep(pause)
    return members


def load_current_members(pro, l2: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    frames = []
    codes = l2["ts_code"].dropna().astype(str).tolist()
    for index, code in enumerate(codes, start=1):
        print(f"Fetching Shenwan members {index:03d}/{len(codes)} {code}")
        try:
            members = fetch_current_members(pro, code, args.refresh, args.pause)
        except RuntimeError as exc:
            print(f"Skipping members {code}: {exc}")
            continue
        if not members.empty:
            frames.append(members)
    if not frames:
        return pd.DataFrame(columns=["l2_code", "ts_code", "name"])
    members = pd.concat(frames, ignore_index=True).drop_duplicates(["l2_code", "ts_code"])
    members["in_date"] = pd.to_datetime(members["in_date"], format="%Y%m%d", errors="coerce")
    members["out_date"] = pd.to_datetime(members["out_date"], format="%Y%m%d", errors="coerce")
    return members


def trade_date_cache_path(trade_date: pd.Timestamp) -> Path:
    return ASHARE_DAILY_DIR / f"{trade_date.strftime('%Y%m%d')}.csv"


def fetch_ashare_daily(pro, trade_date: pd.Timestamp, refresh: bool, pause: float) -> pd.DataFrame:
    cache_path = trade_date_cache_path(trade_date)
    if cache_path.exists() and not refresh:
        return pd.read_csv(cache_path, dtype={"trade_date": str})
    frame = call_with_retry(
        pro.daily,
        trade_date=trade_date.strftime("%Y%m%d"),
        fields="ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount",
    )
    if not frame.empty:
        frame["trade_date"] = frame["trade_date"].astype(str)
        frame.to_csv(cache_path, index=False)
    time.sleep(pause)
    return frame


def load_ashare_history(
    pro,
    trade_dates: List[pd.Timestamp],
    args: argparse.Namespace,
) -> pd.DataFrame:
    frames = []
    for index, trade_date in enumerate(trade_dates, start=1):
        print(f"Fetching A-share daily {index:03d}/{len(trade_dates)} {trade_date:%Y%m%d}")
        frame = fetch_ashare_daily(pro, trade_date, args.refresh, args.pause)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    daily = pd.concat(frames, ignore_index=True)
    daily["trade_date"] = pd.to_datetime(daily["trade_date"], format="%Y%m%d")
    for column in ("high", "low", "close", "pct_chg", "amount"):
        daily[column] = pd.to_numeric(daily[column], errors="coerce")
    return daily.sort_values(["ts_code", "trade_date"]).drop_duplicates(["ts_code", "trade_date"])


def breadth_trade_dates(featured: pd.DataFrame, days: int) -> List[pd.Timestamp]:
    latest = featured["trade_date"].max()
    first = latest - pd.Timedelta(days=days)
    dates = featured.loc[featured["trade_date"] >= first, "trade_date"].drop_duplicates()
    return sorted(pd.Timestamp(value) for value in dates)


def calculate_breadth(
    daily: pd.DataFrame,
    members: pd.DataFrame,
    latest_date: pd.Timestamp,
) -> pd.DataFrame:
    if daily.empty or members.empty:
        return pd.DataFrame(columns=["ts_code"])
    stock = daily.copy()
    group = stock.groupby("ts_code", group_keys=False)
    for window in (20, 60, 120):
        stock[f"ma_{window}"] = group["close"].transform(
            lambda series: series.rolling(window, min_periods=max(8, window // 2)).mean()
        )
    stock["ret_20"] = group["close"].pct_change(20)
    stock["ret_60"] = group["close"].pct_change(60)
    stock["high_60_prev"] = group["high"].transform(
        lambda series: series.rolling(60, min_periods=30).max().shift(1)
    )
    latest = stock[stock["trade_date"] == latest_date].copy()
    latest["above_20"] = latest["close"] > latest["ma_20"]
    latest["above_60"] = latest["close"] > latest["ma_60"]
    latest["above_120"] = latest["close"] > latest["ma_120"]
    latest["new_high_60"] = latest["close"] >= latest["high_60_prev"]
    joined = members[["l2_code", "l2_name", "ts_code"]].merge(latest, on="ts_code", how="left")
    rows = []
    for l2_code, frame in joined.groupby("l2_code"):
        valid = frame[frame["close"].notna()].copy()
        member_count = frame["ts_code"].nunique()
        covered = valid["ts_code"].nunique()
        ret_60 = valid["ret_60"].dropna()
        leader_gap = np.nan
        if not ret_60.empty:
            leader_gap = ret_60.quantile(0.9) - ret_60.median()
        rows.append(
            {
                "ts_code": l2_code,
                "member_count": member_count,
                "breadth_coverage": covered / member_count if member_count else np.nan,
                "above_20_ratio": valid["above_20"].mean(),
                "above_60_ratio": valid["above_60"].mean(),
                "above_120_ratio": valid["above_120"].mean(),
                "new_high_60_ratio": valid["new_high_60"].mean(),
                "positive_ret_20_ratio": (valid["ret_20"] > 0).mean(),
                "positive_ret_60_ratio": (valid["ret_60"] > 0).mean(),
                "median_member_ret_20": valid["ret_20"].median(),
                "median_member_ret_60": valid["ret_60"].median(),
                "leader_gap_60": leader_gap,
            }
        )
    breadth = pd.DataFrame(rows)
    if breadth.empty:
        return breadth
    breadth["breadth_score"] = clamp_score(
        26 * breadth["above_60_ratio"]
        + 18 * breadth["above_120_ratio"]
        + 18 * breadth["positive_ret_60_ratio"]
        + 14 * breadth["above_20_ratio"]
        + 14 * breadth["positive_ret_20_ratio"]
        + 10 * breadth["new_high_60_ratio"]
    )
    breadth["breadth_note"] = breadth.apply(make_breadth_note, axis=1)
    return breadth


def report_period_candidates(latest_date: pd.Timestamp) -> List[str]:
    periods = []
    year = latest_date.year
    for candidate_year in range(year, year - 3, -1):
        for month_day in ("1231", "0930", "0630", "0331"):
            period = pd.Timestamp(f"{candidate_year}{month_day}")
            if period <= latest_date - pd.Timedelta(days=35):
                periods.append(period.strftime("%Y%m%d"))
    return periods


def fetch_financial_period(pro, period: str, refresh: bool) -> pd.DataFrame:
    cache_path = FINANCIAL_DIR / f"fina_indicator_{period}.csv"
    if cache_path.exists() and not refresh:
        return pd.read_csv(cache_path, dtype={"ann_date": str, "end_date": str})
    frame = call_with_retry(
        pro.fina_indicator_vip,
        period=period,
        fields=(
            "ts_code,ann_date,end_date,q_sales_yoy,q_netprofit_yoy,dt_netprofit_yoy,"
            "q_ocf_to_sales,roe_dt"
        ),
    )
    if not frame.empty:
        frame["ann_date"] = frame["ann_date"].astype(str)
        frame["end_date"] = frame["end_date"].astype(str)
        frame.sort_values(["ts_code", "ann_date"]).to_csv(cache_path, index=False)
    return frame


def latest_financial_pair(pro, latest_date: pd.Timestamp, refresh: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    frames = []
    for period in report_period_candidates(latest_date):
        print(f"Fetching financial indicators {period}")
        try:
            frame = fetch_financial_period(pro, period, refresh)
        except RuntimeError as exc:
            print(f"Skipping financial period {period}: {exc}")
            continue
        if not frame.empty:
            frames.append(frame)
        if len(frames) == 2:
            break
    if len(frames) < 2:
        return pd.DataFrame(), pd.DataFrame()
    return frames[0], frames[1]


def dedupe_financials(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    cleaned = frame.copy()
    for column in ("q_sales_yoy", "q_netprofit_yoy", "dt_netprofit_yoy", "q_ocf_to_sales", "roe_dt"):
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
    return cleaned.sort_values(["ts_code", "ann_date"]).drop_duplicates("ts_code", keep="last")


def calculate_financial_summary(
    latest_financials: pd.DataFrame,
    prior_financials: pd.DataFrame,
    members: pd.DataFrame,
) -> pd.DataFrame:
    if latest_financials.empty or members.empty:
        return pd.DataFrame(columns=["ts_code"])
    latest = dedupe_financials(latest_financials)
    prior = dedupe_financials(prior_financials)[["ts_code", "q_netprofit_yoy"]].rename(
        columns={"q_netprofit_yoy": "prior_q_netprofit_yoy"}
    )
    joined = members[["l2_code", "l2_name", "ts_code"]].merge(latest, on="ts_code", how="left")
    joined = joined.merge(prior, on="ts_code", how="left")
    joined["profit_improved"] = joined["q_netprofit_yoy"] > joined["prior_q_netprofit_yoy"]
    rows = []
    for l2_code, frame in joined.groupby("l2_code"):
        sales_valid = frame["q_sales_yoy"].dropna()
        profit_valid = frame["q_netprofit_yoy"].dropna()
        improve_valid = frame.dropna(subset=["q_netprofit_yoy", "prior_q_netprofit_yoy"])
        cash_valid = frame["q_ocf_to_sales"].dropna()
        member_count = frame["ts_code"].nunique()
        rows.append(
            {
                "ts_code": l2_code,
                "financial_member_count": member_count,
                "financial_coverage": profit_valid.shape[0] / member_count if member_count else np.nan,
                "sales_positive_ratio": (sales_valid > 0).mean(),
                "profit_positive_ratio": (profit_valid > 0).mean(),
                "profit_improve_ratio": improve_valid["profit_improved"].mean(),
                "cashflow_positive_ratio": (cash_valid > 0).mean(),
                "median_q_sales_yoy": sales_valid.median(),
                "median_q_netprofit_yoy": profit_valid.median(),
            }
        )
    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    summary["finance_score"] = clamp_score(
        24 * summary["sales_positive_ratio"]
        + 30 * summary["profit_positive_ratio"]
        + 26 * summary["profit_improve_ratio"]
        + 20 * summary["cashflow_positive_ratio"]
    )
    summary["finance_note"] = summary.apply(make_finance_note, axis=1)
    return summary


def safe_percentile(series: pd.Series, lookback: int = 756) -> pd.Series:
    def rank_last(values: np.ndarray) -> float:
        valid = values[np.isfinite(values)]
        if len(valid) < 30:
            return np.nan
        return float((valid <= valid[-1]).sum() / len(valid))

    return series.rolling(lookback, min_periods=60).apply(rank_last, raw=True)


def add_features(history: pd.DataFrame) -> pd.DataFrame:
    history = history.copy()
    benchmark = history[history["ts_code"] == BENCHMARK_CODE][["trade_date", "close"]].rename(
        columns={"close": "benchmark_close"}
    )
    benchmark = benchmark.sort_values("trade_date")
    for window in (20, 60, 120):
        benchmark[f"bench_ret_{window}"] = benchmark["benchmark_close"].pct_change(window)
    l2 = history[history["ts_code"] != BENCHMARK_CODE].merge(benchmark, on="trade_date", how="left")
    l2 = l2.sort_values(["ts_code", "trade_date"])
    group = l2.groupby("ts_code", group_keys=False)
    l2["ret_20"] = group["close"].pct_change(20)
    l2["ret_60"] = group["close"].pct_change(60)
    l2["ret_120"] = group["close"].pct_change(120)
    l2["rs_20"] = l2["ret_20"] - l2["bench_ret_20"]
    l2["rs_60"] = l2["ret_60"] - l2["bench_ret_60"]
    l2["rs_120"] = l2["ret_120"] - l2["bench_ret_120"]
    l2["ema_20"] = group["close"].transform(lambda s: s.ewm(span=20, adjust=False).mean())
    l2["ema_60"] = group["close"].transform(lambda s: s.ewm(span=60, adjust=False).mean())
    l2["ema_120"] = group["close"].transform(lambda s: s.ewm(span=120, adjust=False).mean())
    l2["above_ma_count"] = (
        (l2["close"] > l2["ema_20"]).astype(int)
        + (l2["close"] > l2["ema_60"]).astype(int)
        + (l2["close"] > l2["ema_120"]).astype(int)
    )
    l2["daily_ret"] = group["close"].pct_change()
    l2["vol_20"] = group["daily_ret"].transform(lambda s: s.rolling(20, min_periods=12).std())
    l2["vol_60"] = group["daily_ret"].transform(lambda s: s.rolling(60, min_periods=30).std())
    l2["amount_ma_20"] = group["amount"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    l2["amount_ma_60"] = group["amount"].transform(lambda s: s.rolling(60, min_periods=20).mean())
    l2["amount_ratio"] = l2["amount_ma_20"] / l2["amount_ma_60"]
    l2["pe_temp"] = group["pe"].transform(safe_percentile)
    l2["pb_temp"] = group["pb"].transform(safe_percentile)
    date_group = l2.groupby("trade_date")
    l2["amount_share"] = l2["amount"] / date_group["amount"].transform("sum")
    l2["amount_share_ma20"] = group["amount_share"].transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )
    l2["amount_share_ma60"] = group["amount_share"].transform(
        lambda s: s.rolling(60, min_periods=20).mean()
    )
    l2["amount_share_ratio"] = l2["amount_share_ma20"] / l2["amount_share_ma60"]
    for metric in ("rs_20", "rs_60", "rs_120", "amount_share", "amount_share_ratio", "vol_20"):
        l2[f"{metric}_rank"] = date_group[metric].rank(pct=True)
    l2["rs_60_rank_lag20"] = group["rs_60_rank"].shift(20)
    l2["rank_improve_20"] = l2["rs_60_rank"] - l2["rs_60_rank_lag20"]
    return l2


def clamp_score(value: pd.Series) -> pd.Series:
    return value.clip(lower=0, upper=100).fillna(0)


def make_breadth_note(row: pd.Series) -> str:
    notes: List[str] = []
    if row["above_60_ratio"] >= 0.65:
        notes.append("中期转强成分多")
    elif row["above_60_ratio"] <= 0.35:
        notes.append("宽度偏窄")
    if row["new_high_60_ratio"] >= 0.18:
        notes.append("突破扩散")
    if row["leader_gap_60"] >= 0.28:
        notes.append("龙头领先过大")
    return "；".join(notes[:2]) or "宽度中性"


def make_finance_note(row: pd.Series) -> str:
    notes: List[str] = []
    if row["profit_positive_ratio"] >= 0.6:
        notes.append("利润正增长覆盖高")
    elif row["profit_positive_ratio"] <= 0.35:
        notes.append("利润覆盖偏弱")
    if row["profit_improve_ratio"] >= 0.58:
        notes.append("利润同比改善扩散")
    if row["cashflow_positive_ratio"] <= 0.35:
        notes.append("现金流质量待验")
    return "；".join(notes[:2]) or "财务景气中性"


def score_latest(
    featured: pd.DataFrame,
    classify: pd.DataFrame,
    breadth: pd.DataFrame,
    finance: pd.DataFrame,
) -> pd.DataFrame:
    latest_date = featured["trade_date"].max()
    latest = featured[featured["trade_date"] == latest_date].copy()
    latest = latest.merge(
        classify[["ts_code", "industry_name", "parent_code"]],
        on="ts_code",
        how="left",
    )
    latest = latest.merge(breadth, on="ts_code", how="left")
    latest = latest.merge(finance, on="ts_code", how="left")
    latest["breadth_score"] = latest["breadth_score"].fillna(0)
    latest["finance_score"] = latest["finance_score"].fillna(50)
    latest["trend_score"] = clamp_score(
        42 * latest["rs_60_rank"]
        + 23 * latest["rs_120_rank"]
        + 15 * latest["rs_20_rank"]
        + 20 * (latest["above_ma_count"] / 3)
    )
    latest["startup_score"] = clamp_score(
        24 * latest["rank_improve_20"].clip(lower=0) / 0.35
        + 20 * latest["rs_20_rank"]
        + 16 * latest["amount_share_ratio_rank"]
        + 12 * (latest["above_ma_count"] / 3)
        + 8 * (1 - latest["rs_120_rank"])
        + 20 * (latest["breadth_score"] / 100)
    )
    valuation_temp = latest[["pe_temp", "pb_temp"]].mean(axis=1).fillna(0.5)
    latest["overheat_score"] = clamp_score(
        28 * latest["rs_20_rank"]
        + 22 * latest["amount_share_ratio_rank"]
        + 18 * latest["vol_20_rank"]
        + 17 * valuation_temp
        + 15 * latest["rs_60_rank"]
    )
    latest["weakening_score"] = clamp_score(
        35 * (latest["rs_60_rank"] > 0.65).astype(float) * (1 - latest["rs_20_rank"])
        + 30 * (latest["rank_improve_20"] < -0.12).astype(float)
        + 15 * (latest["above_ma_count"] <= 1).astype(float)
        + 10 * (latest["amount_share_ratio"] < 0.92).astype(float)
        + 10 * (latest["breadth_score"] < 36).astype(float)
    )
    latest["radar_score"] = clamp_score(
        0.31 * latest["trend_score"]
        + 0.25 * latest["startup_score"]
        + 0.18 * latest["breadth_score"]
        + 0.14 * latest["finance_score"]
        - 0.14 * latest["overheat_score"]
        - 0.10 * latest["weakening_score"]
        + 18
    )
    latest["stage"] = latest.apply(classify_stage, axis=1)
    latest["action"] = latest.apply(classify_action, axis=1)
    latest["trade_date"] = latest["trade_date"].dt.strftime("%Y-%m-%d")
    latest["status_note"] = latest.apply(make_status_note, axis=1)
    latest["confirm_note"] = latest.apply(make_confirmation_note, axis=1)
    latest["refute_note"] = latest.apply(make_refutation_note, axis=1)
    return latest.sort_values(["radar_score", "startup_score"], ascending=False)


def classify_stage(row: pd.Series) -> str:
    if row["trend_score"] < 25 and row["startup_score"] < 35:
        return "退潮回避"
    if row["weakening_score"] >= 55 and row["trend_score"] >= 45:
        return "钝化转弱"
    if row["overheat_score"] >= 78 and row["trend_score"] >= 68:
        return "高位拥挤"
    if row["trend_score"] >= 80 and row["startup_score"] >= 58 and row["breadth_score"] >= 55:
        return "主升扩散"
    if row["trend_score"] >= 65 and row["breadth_score"] >= 40:
        return "趋势确认"
    if row["startup_score"] >= 66 and row["trend_score"] >= 42 and row["breadth_score"] >= 42:
        return "早期启动"
    return "潜伏观察"


def classify_action(row: pd.Series) -> str:
    if row["stage"] == "早期启动":
        return "优先挖掘"
    if row["stage"] in {"趋势确认", "主升扩散"}:
        return "继续跟踪核心"
    if row["stage"] == "高位拥挤":
        return "持有但不追"
    if row["stage"] == "钝化转弱":
        return "收紧风控"
    if row["stage"] == "退潮回避":
        return "回避"
    return "观察验证"


def make_status_note(row: pd.Series) -> str:
    notes: List[str] = []
    if row["rank_improve_20"] >= 0.18:
        notes.append("60日相对排名改善快")
    if row["amount_share_ratio"] >= 1.15:
        notes.append("成交关注度升温")
    if row["above_ma_count"] == 3:
        notes.append("20/60/120日趋势同向")
    if row["overheat_score"] >= 78:
        notes.append("短期热度偏高")
    if row["breadth_score"] >= 62:
        notes.append("成分宽度跟上")
    if row["finance_score"] >= 62:
        notes.append("财务景气有支持")
    if row["weakening_score"] >= 55:
        notes.append("强势后质量转弱")
    if row["rs_20"] < 0 and row["rs_60"] > 0:
        notes.append("短线弱于中期趋势")
    return "；".join(notes[:3]) or "等待更多共振证据"


def make_confirmation_note(row: pd.Series) -> str:
    notes: List[str] = []
    if row["breadth_score"] >= 62:
        notes.append("宽度扩散")
    elif row["breadth_score"] >= 45:
        notes.append("宽度开始跟")
    if row["finance_score"] >= 62:
        notes.append("财务景气支持")
    elif row["profit_positive_ratio"] >= 0.52:
        notes.append("利润覆盖改善")
    if row["rank_improve_20"] >= 0.18:
        notes.append("盘面排名抬升")
    return "；".join(notes[:3]) or "盘面先动，确认不足"


def make_refutation_note(row: pd.Series) -> str:
    notes: List[str] = []
    if row["breadth_score"] < 38:
        notes.append("宽度未跟上")
    if row["leader_gap_60"] >= 0.28:
        notes.append("龙头独舞风险")
    if row["finance_score"] < 44:
        notes.append("财务景气偏弱")
    if row["financial_coverage"] < 0.45:
        notes.append("财务覆盖不足")
    if row["overheat_score"] >= 78:
        notes.append("短期热度高")
    return "；".join(notes[:3]) or "暂无显著反证"


def prior_scores(
    featured: pd.DataFrame,
    classify: pd.DataFrame,
    breadth: pd.DataFrame,
    finance: pd.DataFrame,
    sessions_back: int,
) -> pd.DataFrame:
    dates = sorted(featured["trade_date"].drop_duplicates())
    if len(dates) <= sessions_back:
        return pd.DataFrame()
    prior_date = dates[-(sessions_back + 1)]
    prior_featured = featured[featured["trade_date"] <= prior_date]
    return score_latest(prior_featured, classify, breadth, finance)[["ts_code", "stage"]].rename(
        columns={"stage": f"stage_{sessions_back}d_ago"}
    )


def build_migrations(
    scores: pd.DataFrame,
    featured: pd.DataFrame,
    classify: pd.DataFrame,
    breadth: pd.DataFrame,
    finance: pd.DataFrame,
) -> pd.DataFrame:
    migrations = scores[
        ["ts_code", "industry_name", "trade_date", "stage", "action", "radar_score", "status_note"]
    ].copy()
    for sessions in (5, 20):
        prior = prior_scores(featured, classify, breadth, finance, sessions)
        if not prior.empty:
            migrations = migrations.merge(prior, on="ts_code", how="left")
    migrations["migration"] = migrations.apply(
        lambda row: f"{row.get('stage_20d_ago', '无历史')} -> {row['stage']}", axis=1
    )
    return migrations.sort_values("radar_score", ascending=False)


def export_tables(scores: pd.DataFrame, migrations: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    score_columns = [
        "ts_code",
        "industry_name",
        "trade_date",
        "stage",
        "action",
        "radar_score",
        "trend_score",
        "startup_score",
        "breadth_score",
        "finance_score",
        "overheat_score",
        "weakening_score",
        "ret_20",
        "ret_60",
        "ret_120",
        "rs_20",
        "rs_60",
        "rs_120",
        "rank_improve_20",
        "amount_share",
        "amount_share_ratio",
        "pe",
        "pb",
        "pe_temp",
        "pb_temp",
        "member_count",
        "breadth_coverage",
        "above_20_ratio",
        "above_60_ratio",
        "above_120_ratio",
        "new_high_60_ratio",
        "positive_ret_20_ratio",
        "positive_ret_60_ratio",
        "median_member_ret_20",
        "median_member_ret_60",
        "leader_gap_60",
        "financial_member_count",
        "financial_coverage",
        "sales_positive_ratio",
        "profit_positive_ratio",
        "profit_improve_ratio",
        "cashflow_positive_ratio",
        "median_q_sales_yoy",
        "median_q_netprofit_yoy",
        "status_note",
        "breadth_note",
        "finance_note",
        "confirm_note",
        "refute_note",
    ]
    startup = scores[
        (
            scores["stage"].isin(["早期启动", "趋势确认", "主升扩散"])
            | ((scores["startup_score"] >= 52) & (scores["breadth_score"] >= 34))
        )
        & (scores["overheat_score"] < 78)
        & (scores["weakening_score"] < 55)
    ].sort_values(["startup_score", "radar_score"], ascending=False)
    risks = scores[
        scores["stage"].isin(["高位拥挤", "钝化转弱"])
        | (scores["overheat_score"] >= 78)
    ].sort_values(["weakening_score", "overheat_score"], ascending=False)
    tables = {
        "scores": scores[score_columns].copy(),
        "startup": startup[score_columns].head(20).copy(),
        "risks": risks[score_columns].head(20).copy(),
        "migrations": migrations.copy(),
    }
    tables["scores"].to_csv(EXPORT_DIR / "sector_scores_latest.csv", index=False)
    tables["startup"].to_csv(EXPORT_DIR / "startup_candidates_latest.csv", index=False)
    tables["risks"].to_csv(EXPORT_DIR / "risk_warnings_latest.csv", index=False)
    tables["migrations"].to_csv(EXPORT_DIR / "stage_migrations_latest.csv", index=False)
    return tables


def fmt_score(value: float) -> str:
    return "-" if pd.isna(value) else f"{value:.0f}"


def fmt_pct(value: float) -> str:
    return "-" if pd.isna(value) else f"{value * 100:.1f}%"


def badge(text: str) -> str:
    css = {
        "潜伏观察": "watch",
        "早期启动": "startup",
        "趋势确认": "trend",
        "主升扩散": "trend",
        "高位拥挤": "hot",
        "钝化转弱": "risk",
        "退潮回避": "avoid",
    }.get(text, "watch")
    return f'<span class="badge {css}">{html.escape(text)}</span>'


def table_rows(frame: pd.DataFrame, include_risk: bool = False) -> str:
    rows = []
    for row in frame.itertuples():
        rows.append(
            "<tr>"
            f'<td><a href="#sector-{html.escape(row.ts_code)}">{html.escape(row.industry_name)}</a></td>'
            f"<td>{badge(row.stage)}</td>"
            f"<td>{html.escape(row.action)}</td>"
            f"<td>{fmt_score(row.radar_score)}</td>"
            f"<td>{fmt_score(row.startup_score)}</td>"
            f"<td>{fmt_score(row.trend_score)}</td>"
            f"<td>{fmt_score(row.breadth_score)}</td>"
            f"<td>{fmt_score(row.finance_score)}</td>"
            f"<td>{fmt_score(row.overheat_score)}</td>"
            + (f"<td>{fmt_score(row.weakening_score)}</td>" if include_risk else "")
            + f"<td>{html.escape(str(row.confirm_note))}</td>"
            + f"<td>{html.escape(str(row.refute_note))}</td>"
            "</tr>"
        )
    return "".join(rows)


def render_sector_cards(scores: pd.DataFrame) -> str:
    focus = pd.concat(
        [
            scores.sort_values(["startup_score", "radar_score"], ascending=False).head(8),
            scores.sort_values(["weakening_score", "overheat_score"], ascending=False).head(6),
        ],
        ignore_index=True,
    ).drop_duplicates("ts_code")
    cards = []
    for row in focus.itertuples():
        cards.append(
            f"""
            <article class="sector-card" id="sector-{html.escape(row.ts_code)}">
              <div class="card-title">
                <div>
                  <h3>{html.escape(row.industry_name)}</h3>
                  <p>{html.escape(row.ts_code)} | {badge(row.stage)} | {html.escape(row.action)}</p>
                </div>
                <strong>{fmt_score(row.radar_score)}</strong>
              </div>
              <p class="verdict">{html.escape(row.status_note)}</p>
              <div class="metric-grid">
                <div><span>启动</span><b>{fmt_score(row.startup_score)}</b></div>
                <div><span>趋势</span><b>{fmt_score(row.trend_score)}</b></div>
                <div><span>宽度</span><b>{fmt_score(row.breadth_score)}</b></div>
                <div><span>财务</span><b>{fmt_score(row.finance_score)}</b></div>
                <div><span>过热</span><b>{fmt_score(row.overheat_score)}</b></div>
                <div><span>钝化</span><b>{fmt_score(row.weakening_score)}</b></div>
                <div><span>站上60日线</span><b>{fmt_pct(row.above_60_ratio)}</b></div>
                <div><span>利润正增长</span><b>{fmt_pct(row.profit_positive_ratio)}</b></div>
                <div><span>确认</span><b>{html.escape(str(row.confirm_note))}</b></div>
                <div><span>反证</span><b>{html.escape(str(row.refute_note))}</b></div>
              </div>
            </article>
            """
        )
    return "".join(cards)


def stage_summary(scores: pd.DataFrame) -> str:
    counts = scores["stage"].value_counts()
    blocks = []
    for stage in STAGE_ORDER:
        blocks.append(
            f"<div><span>{html.escape(stage)}</span><strong>{int(counts.get(stage, 0))}</strong></div>"
        )
    return "".join(blocks)


def render_report(scores: pd.DataFrame, tables: Dict[str, pd.DataFrame]) -> Path:
    trade_date = scores["trade_date"].iloc[0]
    compact_date = trade_date.replace("-", "")
    latest_path = REPORT_DIR / "latest.html"
    dated_path = REPORT_DIR / f"{compact_date}.html"
    top_startup = tables["startup"].head(10)
    top_risk = tables["risks"].head(10)
    migrations = tables["migrations"].copy()
    migrations = migrations[migrations["stage_20d_ago"].fillna("") != migrations["stage"]].head(12)
    migration_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.industry_name)}</td>"
        f"<td>{html.escape(str(row.stage_20d_ago))}</td>"
        f"<td>{badge(row.stage)}</td>"
        f"<td>{html.escape(row.action)}</td>"
        f"<td>{html.escape(str(row.status_note))}</td>"
        "</tr>"
        for row in migrations.itertuples()
    )
    report = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>申万二级盘面雷达 {trade_date}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #17202a;
      --muted: #5c6670;
      --paper: #f5f2eb;
      --panel: #fffdf8;
      --line: #d8d0c1;
      --teal: #0f766e;
      --green: #2e7d32;
      --amber: #a16207;
      --red: #b42318;
      --blue: #2457a6;
    }}
    * {{ box-sizing: border-box; letter-spacing: 0; }}
    body {{ margin: 0; background: var(--paper); color: var(--ink); font: 15px/1.5 -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", sans-serif; }}
    main {{ max-width: 1280px; margin: 0 auto; padding: 28px 24px 56px; }}
    header {{ display: grid; gap: 10px; border-bottom: 1px solid var(--line); padding-bottom: 22px; }}
    h1, h2, h3, p {{ margin: 0; }}
    h1 {{ font-size: 34px; }}
    h2 {{ font-size: 21px; margin: 28px 0 12px; }}
    a {{ color: inherit; }}
    .lede {{ color: var(--muted); max-width: 850px; }}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(135px, 1fr)); gap: 10px; margin-top: 18px; }}
    .summary div, .note {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px; }}
    .summary span, .metric-grid span {{ color: var(--muted); display: block; font-size: 12px; }}
    .summary strong {{ font-size: 25px; }}
    .split {{ display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); }}
    .table-wrap {{ overflow: auto; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
    table {{ width: 100%; min-width: 1040px; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 10px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 12px; background: #eee6d8; position: sticky; top: 0; }}
    td:nth-child(4), td:nth-child(5), td:nth-child(6), td:nth-child(7), td:nth-child(8) {{ font-variant-numeric: tabular-nums; }}
    .badge {{ display: inline-flex; align-items: center; min-height: 24px; border-radius: 999px; padding: 2px 9px; font-size: 12px; white-space: nowrap; }}
    .watch {{ background: #ece8df; color: #4b5563; }}
    .startup {{ background: #d7f0e9; color: var(--teal); }}
    .trend {{ background: #dbe8fb; color: var(--blue); }}
    .hot {{ background: #fff0c7; color: var(--amber); }}
    .risk, .avoid {{ background: #fbe0dd; color: var(--red); }}
    .cards {{ display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }}
    .sector-card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }}
    .card-title {{ display: flex; justify-content: space-between; gap: 14px; align-items: flex-start; }}
    .card-title strong {{ font-size: 34px; line-height: 1; color: var(--teal); }}
    .card-title p, .verdict, footer {{ color: var(--muted); }}
    .verdict {{ min-height: 44px; margin: 12px 0; }}
    .metric-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }}
    .metric-grid div {{ border-top: 1px solid var(--line); padding-top: 8px; min-width: 0; }}
    .metric-grid b {{ display: block; overflow-wrap: anywhere; }}
    .note {{ margin-top: 14px; }}
    footer {{ margin-top: 30px; border-top: 1px solid var(--line); padding-top: 14px; }}
    @media (max-width: 720px) {{
      main {{ padding: 18px 12px 40px; }}
      h1 {{ font-size: 28px; }}
      .split {{ grid-template-columns: 1fr; }}
      .metric-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>申万二级盘面雷达</h1>
    <p class="lede">{trade_date} | 盘面先发现，宽度验真假，财务景气先做长期确认。资金持续性与公告催化层尚未接入，因此本页先把“确认”和“反证”摊开给你看。</p>
    <section class="summary">{stage_summary(scores)}</section>
  </header>
  <section>
    <h2>今日结论</h2>
    <div class="split">
      <div>
        <h2>启动线索榜</h2>
        <div class="table-wrap"><table>
          <thead><tr><th>赛道</th><th>阶段</th><th>动作</th><th>雷达</th><th>启动</th><th>趋势</th><th>宽度</th><th>财务</th><th>过热风险</th><th>确认</th><th>反证</th></tr></thead>
          <tbody>{table_rows(top_startup)}</tbody>
        </table></div>
      </div>
      <div>
        <h2>过热与钝化警报</h2>
        <div class="table-wrap"><table>
          <thead><tr><th>赛道</th><th>阶段</th><th>动作</th><th>雷达</th><th>启动</th><th>趋势</th><th>宽度</th><th>财务</th><th>过热风险</th><th>钝化风险</th><th>确认</th><th>反证</th></tr></thead>
          <tbody>{table_rows(top_risk, include_risk=True)}</tbody>
        </table></div>
      </div>
    </div>
    <p class="note">分数口径：`启动`、`趋势`、`宽度`、`财务` 越高越支持研究；`过热风险` 与 `钝化风险` 越高越需要谨慎。动作口径：`优先挖掘` 适合继续下钻个股；`持有但不追` 代表趋势仍可能延续但新开仓赔率下降。</p>
  </section>
  <section>
    <h2>阶段迁移</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>赛道</th><th>20交易日前</th><th>当前</th><th>动作</th><th>迁移证据</th></tr></thead>
      <tbody>{migration_rows}</tbody>
    </table></div>
  </section>
  <section>
    <h2>重点赛道卡片</h2>
    <div class="cards">{render_sector_cards(scores)}</div>
  </section>
  <footer>
    导出表位于 `exports/`。基准使用 {BENCHMARK_NAME}；宽度按当前申万二级成分股扩散度衡量；财务摘要按最近两个可得报告期的成分股财务指标聚合；资金与消息证据层待接入。本报告用于研究和仓位复盘，不替代交易纪律。
  </footer>
</main>
</body>
</html>
"""
    latest_path.write_text(report, encoding="utf-8")
    dated_path.write_text(report, encoding="utf-8")
    return latest_path


def main() -> None:
    args = parse_args()
    ensure_dirs()
    pro = tushare_client()
    classify = fetch_l2_classify(pro, args.refresh)
    history = load_market_history(pro, classify, args)
    featured = add_features(history)
    members = load_current_members(pro, classify, args)
    ashare_dates = breadth_trade_dates(featured, args.breadth_days)
    ashare_history = load_ashare_history(pro, ashare_dates, args)
    latest_date = featured["trade_date"].max()
    breadth = calculate_breadth(ashare_history, members, latest_date)
    latest_financials, prior_financials = latest_financial_pair(pro, latest_date, args.refresh)
    finance = calculate_financial_summary(latest_financials, prior_financials, members)
    scores = score_latest(featured, classify, breadth, finance)
    migrations = build_migrations(scores, featured, classify, breadth, finance)
    tables = export_tables(scores, migrations)
    report_path = render_report(scores, tables)
    print(f"Built report: {report_path}")
    print(f"Latest trade date: {scores['trade_date'].iloc[0]}")
    print(f"Scored sectors: {len(scores)}")


if __name__ == "__main__":
    main()
