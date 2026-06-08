#!/usr/bin/env python3
"""Cache historical semiconductor member bars and build a daily breadth table."""

from __future__ import annotations

import argparse
import getpass
import os
import time
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import tushare as ts


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "semiconductor_members"
PROCESSED_DIR = ROOT / "data" / "processed"
MEMBERS_PATH = RAW_DIR / "members_history.csv"
BREADTH_PATH = PROCESSED_DIR / "semiconductor_breadth_history.csv"
L2_CODE = "801081.SI"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="20170101")
    parser.add_argument("--end-date", default="20260521")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--pause", type=float, default=0.08)
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


def ensure_dirs() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def load_members(pro, refresh: bool) -> pd.DataFrame:
    if MEMBERS_PATH.exists() and not refresh:
        frame = pd.read_csv(MEMBERS_PATH, dtype=str)
    else:
        frames = []
        for flag in ("Y", "N"):
            frame = call_with_retry(
                pro.index_member_all,
                l2_code=L2_CODE,
                is_new=flag,
                fields="l1_code,l2_code,l2_name,ts_code,name,in_date,out_date,is_new",
            )
            if not frame.empty:
                frames.append(frame)
        frame = pd.concat(frames, ignore_index=True).drop_duplicates(
            ["ts_code", "in_date", "out_date"], keep="last"
        )
        frame.to_csv(MEMBERS_PATH, index=False)
    frame["in_date"] = pd.to_datetime(frame["in_date"], format="%Y%m%d", errors="coerce")
    frame["out_date"] = pd.to_datetime(frame["out_date"], format="%Y%m%d", errors="coerce")
    return frame


def stock_cache_path(ts_code: str) -> Path:
    return RAW_DIR / f"{ts_code.replace('.', '_')}.csv"


def load_stock_daily(
    pro,
    ts_code: str,
    start_date: str,
    end_date: str,
    refresh: bool,
    pause: float,
) -> pd.DataFrame:
    path = stock_cache_path(ts_code)
    if path.exists() and not refresh:
        return pd.read_csv(path, dtype={"trade_date": str})
    frame = call_with_retry(
        pro.daily,
        ts_code=ts_code,
        start_date=start_date,
        end_date=end_date,
        fields="ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount",
    )
    if not frame.empty:
        frame["trade_date"] = frame["trade_date"].astype(str)
        frame.sort_values("trade_date").to_csv(path, index=False)
    time.sleep(pause)
    return frame


def load_member_bars(pro, members: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    frames = []
    codes = sorted(members["ts_code"].dropna().unique())
    for index, code in enumerate(codes, start=1):
        print(f"Fetching semiconductor member {index:03d}/{len(codes)} {code}")
        frame = load_stock_daily(pro, code, args.start_date, args.end_date, args.refresh, args.pause)
        if not frame.empty:
            frames.append(frame)
    daily = pd.concat(frames, ignore_index=True)
    daily["trade_date"] = pd.to_datetime(daily["trade_date"], format="%Y%m%d")
    for column in ("high", "close"):
        daily[column] = pd.to_numeric(daily[column], errors="coerce")
    return daily.sort_values(["ts_code", "trade_date"]).drop_duplicates(["ts_code", "trade_date"])


def add_stock_features(daily: pd.DataFrame) -> pd.DataFrame:
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
    stock["above_20"] = stock["close"] > stock["ma_20"]
    stock["above_60"] = stock["close"] > stock["ma_60"]
    stock["above_120"] = stock["close"] > stock["ma_120"]
    stock["high_60_breakout"] = stock["close"] >= stock["high_60_prev"]
    return stock


def active_member_bars(stock: pd.DataFrame, members: pd.DataFrame) -> pd.DataFrame:
    joined = stock.merge(members[["ts_code", "in_date", "out_date"]], on="ts_code", how="inner")
    active = joined["trade_date"] >= joined["in_date"]
    active &= joined["out_date"].isna() | (joined["trade_date"] <= joined["out_date"])
    return joined[active].drop_duplicates(["ts_code", "trade_date"])


def build_breadth(active: pd.DataFrame) -> pd.DataFrame:
    rows: List[dict] = []
    for trade_date, frame in active.groupby("trade_date"):
        valid = frame[frame["close"].notna()].copy()
        ret_60 = valid["ret_60"].dropna()
        rows.append(
            {
                "trade_date": trade_date,
                "semi_breadth_member_count": valid["ts_code"].nunique(),
                "semi_breadth_above_20": valid["above_20"].mean(),
                "semi_breadth_above_60": valid["above_60"].mean(),
                "semi_breadth_above_120": valid["above_120"].mean(),
                "semi_breadth_positive_20": (valid["ret_20"] > 0).mean(),
                "semi_breadth_positive_60": (valid["ret_60"] > 0).mean(),
                "semi_breadth_high_60": valid["high_60_breakout"].mean(),
                "semi_breadth_median_ret_20": valid["ret_20"].median(),
                "semi_breadth_median_ret_60": valid["ret_60"].median(),
                "semi_breadth_leader_gap_60": (
                    ret_60.quantile(0.9) - ret_60.median() if not ret_60.empty else np.nan
                ),
            }
        )
    breadth = pd.DataFrame(rows).sort_values("trade_date")
    breadth.to_csv(BREADTH_PATH, index=False)
    return breadth


def main() -> None:
    args = parse_args()
    ensure_dirs()
    pro = tushare_client()
    members = load_members(pro, args.refresh)
    daily = load_member_bars(pro, members, args)
    breadth = build_breadth(active_member_bars(add_stock_features(daily), members))
    print(f"Members: {members['ts_code'].nunique()}")
    print(f"Breadth rows: {len(breadth)}")
    print(f"Output: {BREADTH_PATH}")


if __name__ == "__main__":
    main()
