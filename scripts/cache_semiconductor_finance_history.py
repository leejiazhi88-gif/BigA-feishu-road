#!/usr/bin/env python3
"""Build conservative report-period finance features for semiconductor backtests."""

from __future__ import annotations

import argparse
import getpass
import os
import time
from pathlib import Path
from typing import List

import pandas as pd
import tushare as ts


ROOT = Path(__file__).resolve().parents[1]
MEMBER_PATH = ROOT / "data" / "raw" / "semiconductor_members" / "members_history.csv"
RAW_DIR = ROOT / "data" / "raw" / "semiconductor_finance_periods"
PROCESSED_DIR = ROOT / "data" / "processed"
OUTPUT_PATH = PROCESSED_DIR / "semiconductor_finance_history.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2017)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def tushare_client():
    token = os.environ.get("TUSHARE_TOKEN") or getpass.getpass("TUSHARE_TOKEN: ").strip()
    if not token:
        raise SystemExit("TUSHARE_TOKEN is required.")
    ts.set_token(token)
    return ts.pro_api()


def call_with_retry(func, *args, **kwargs) -> pd.DataFrame:
    error = None
    for attempt in range(4):
        try:
            frame = func(*args, **kwargs)
            return frame.copy() if frame is not None else pd.DataFrame()
        except Exception as exc:
            error = exc
            time.sleep(0.7 * (attempt + 1))
    raise RuntimeError(f"Tushare call failed: {error}") from error


def periods(args: argparse.Namespace) -> List[str]:
    result = []
    for year in range(args.start_year, args.end_year + 1):
        for suffix in ("0331", "0630", "0930", "1231"):
            result.append(f"{year}{suffix}")
    return result


def finance_cache_path(period: str) -> Path:
    return RAW_DIR / f"fina_indicator_{period}.csv"


def fetch_period(pro, period: str, refresh: bool) -> pd.DataFrame:
    path = finance_cache_path(period)
    if path.exists() and not refresh:
        return pd.read_csv(path, dtype={"ann_date": str, "end_date": str})
    frame = call_with_retry(
        pro.fina_indicator_vip,
        period=period,
        fields="ts_code,ann_date,end_date,q_sales_yoy,q_netprofit_yoy,q_ocf_to_sales,roe_dt",
    )
    if not frame.empty:
        frame.to_csv(path, index=False)
    return frame


def load_members() -> pd.DataFrame:
    members = pd.read_csv(MEMBER_PATH, dtype=str)
    members["in_date"] = pd.to_datetime(members["in_date"], format="%Y%m%d", errors="coerce")
    members["out_date"] = pd.to_datetime(members["out_date"], format="%Y%m%d", errors="coerce")
    return members


def summarize_period(frame: pd.DataFrame, members: pd.DataFrame, period: str) -> dict | None:
    if frame.empty:
        return None
    finance = frame.copy()
    finance["ann_date"] = pd.to_datetime(finance["ann_date"], format="%Y%m%d", errors="coerce")
    finance["end_date"] = pd.to_datetime(finance["end_date"], format="%Y%m%d", errors="coerce")
    for column in ("q_sales_yoy", "q_netprofit_yoy", "q_ocf_to_sales", "roe_dt"):
        finance[column] = pd.to_numeric(finance[column], errors="coerce")
    finance = finance.sort_values(["ts_code", "ann_date"]).drop_duplicates("ts_code", keep="last")
    period_end = pd.Timestamp(period)
    active = members[(members["in_date"] <= period_end) & (members["out_date"].isna() | (members["out_date"] >= period_end))]
    joined = active[["ts_code"]].drop_duplicates().merge(finance, on="ts_code", how="left")
    profit = joined["q_netprofit_yoy"].dropna()
    sales = joined["q_sales_yoy"].dropna()
    cash = joined["q_ocf_to_sales"].dropna()
    roe = joined["roe_dt"].dropna()
    if profit.empty:
        return None
    return {
        "available_date": joined["ann_date"].dropna().max(),
        "finance_period": period,
        "semi_finance_member_count": joined["ts_code"].nunique(),
        "semi_finance_coverage": len(profit) / joined["ts_code"].nunique(),
        "semi_finance_sales_positive": (sales > 0).mean(),
        "semi_finance_profit_positive": (profit > 0).mean(),
        "semi_finance_cash_positive": (cash > 0).mean(),
        "semi_finance_roe_positive": (roe > 0).mean(),
        "semi_finance_sales_median": sales.median(),
        "semi_finance_profit_median": profit.median(),
    }


def add_profit_improvement(summary: pd.DataFrame) -> pd.DataFrame:
    summary = summary.sort_values("finance_period").copy()
    summary["semi_finance_profit_positive_delta"] = summary["semi_finance_profit_positive"].diff()
    summary["semi_finance_profit_median_delta"] = summary["semi_finance_profit_median"].diff()
    return summary


def main() -> None:
    args = parse_args()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    pro = tushare_client()
    members = load_members()
    rows = []
    for period in periods(args):
        print(f"Fetching finance {period}")
        row = summarize_period(fetch_period(pro, period, args.refresh), members, period)
        if row:
            rows.append(row)
    summary = add_profit_improvement(pd.DataFrame(rows))
    summary.to_csv(OUTPUT_PATH, index=False)
    print(summary.tail(8).to_string(index=False))
    print(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
