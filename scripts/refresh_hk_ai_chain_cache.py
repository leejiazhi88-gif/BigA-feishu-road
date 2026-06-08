#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from datetime import date
from pathlib import Path

import pandas as pd
import tushare as ts

from build_ai_chain_feishu import AI_STOCKS, RAW_DIR


def main() -> None:
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise SystemExit("TUSHARE_TOKEN is required.")
    ts.set_token(token)
    pro = ts.pro_api()
    hk_codes = [item["code"] for item in AI_STOCKS if item["market"] == "港股"]
    start_date = "20240501"
    end_date = date.today().strftime("%Y%m%d")
    for idx, code in enumerate(hk_codes, start=1):
        path = RAW_DIR / "prices" / f"{code.replace('.', '_')}.csv"
        print(f"[{idx}/{len(hk_codes)}] fetching {code}")
        frame = pro.hk_daily(ts_code=code, start_date=start_date, end_date=end_date)
        if frame is None or frame.empty:
            print(f"  no data for {code}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path, index=False)
            latest = pd.to_datetime(frame["trade_date"].astype(str), format="%Y%m%d").max().date()
            print(f"  wrote {len(frame)} rows, latest {latest}")
        if idx < len(hk_codes):
            time.sleep(65)


if __name__ == "__main__":
    main()
