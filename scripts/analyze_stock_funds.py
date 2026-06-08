#!/usr/bin/env python3
"""Build stock-level fund-flow diagnostics and charts from Tushare data."""

from __future__ import annotations

import argparse
import getpass
import html
import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Callable, Dict, Iterable, List

import numpy as np
import pandas as pd
import tushare as ts


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "stock_funds"
EXPORT_DIR = ROOT / "exports" / "stock_funds"
REPORT_DIR = ROOT / "reports" / "stock_funds"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ts-code", default="002463.SZ")
    parser.add_argument("--name", default="沪电股份")
    parser.add_argument("--start-date", default="20180101")
    parser.add_argument("--end-date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--pause", type=float, default=0.15)
    return parser.parse_args()


def ensure_dirs() -> None:
    for path in (RAW_DIR, EXPORT_DIR, REPORT_DIR):
        path.mkdir(parents=True, exist_ok=True)


def tushare_client():
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        token = getpass.getpass("TUSHARE_TOKEN: ").strip()
    if not token:
        raise SystemExit("TUSHARE_TOKEN is required.")
    ts.set_token(token)
    return ts.pro_api()


def call_with_retry(func: Callable, *args, **kwargs) -> pd.DataFrame:
    last_error = None
    for attempt in range(4):
        try:
            result = func(*args, **kwargs)
            if result is None:
                return pd.DataFrame()
            return result.copy()
        except Exception as exc:
            last_error = exc
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"Tushare call failed after retries: {last_error}") from last_error


def year_ranges(start_date: str, end_date: str) -> Iterable[tuple[str, str]]:
    start_year = int(start_date[:4])
    end_year = int(end_date[:4])
    for year in range(start_year, end_year + 1):
        left = max(start_date, f"{year}0101")
        right = min(end_date, f"{year}1231")
        if left <= right:
            yield left, right


def cache_name(ts_code: str, api_name: str) -> Path:
    return RAW_DIR / ts_code.replace(".", "_") / f"{api_name}.csv"


def fetch_by_year(
    pro,
    api_name: str,
    ts_code: str,
    start_date: str,
    end_date: str,
    refresh: bool,
    pause: float,
) -> pd.DataFrame:
    path = cache_name(ts_code, api_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not refresh:
        return pd.read_csv(path, dtype={"trade_date": str})

    frames = []
    api = getattr(pro, api_name)
    for left, right in year_ranges(start_date, end_date):
        print(f"Fetching {api_name} {ts_code} {left}-{right}")
        try:
            frame = call_with_retry(api, ts_code=ts_code, start_date=left, end_date=right)
        except RuntimeError as exc:
            print(f"Skipping {api_name} {left}-{right}: {exc}")
            continue
        if not frame.empty:
            frames.append(frame)
        time.sleep(pause)
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not data.empty and "trade_date" in data.columns:
        data["trade_date"] = data["trade_date"].astype(str)
        data = data.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    data.to_csv(path, index=False)
    return data


def to_numeric(frame: pd.DataFrame, skip: set[str] | None = None) -> pd.DataFrame:
    skip = skip or set()
    for column in frame.columns:
        if column not in skip:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def rolling_rank_last(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    def rank(values: np.ndarray) -> float:
        valid = values[np.isfinite(values)]
        if len(valid) < min_periods:
            return np.nan
        return float((valid <= valid[-1]).sum() / len(valid))

    return series.rolling(window, min_periods=min_periods).apply(rank, raw=True)


def compute_indicators(daily: pd.DataFrame, basic: pd.DataFrame, moneyflow: pd.DataFrame, margin: pd.DataFrame) -> pd.DataFrame:
    daily = daily.copy()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"], format="%Y%m%d")
    daily = to_numeric(daily, {"ts_code", "trade_date"}).sort_values("trade_date")

    data = daily[["ts_code", "trade_date", "open", "high", "low", "close", "pct_chg", "vol", "amount"]].copy()
    data["amount_wan"] = data["amount"] / 10.0
    data["return_1d"] = data["close"].pct_change()
    data["amount_ma20"] = data["amount"].rolling(20, min_periods=10).mean()
    data["amount_ratio_20_60"] = (
        data["amount"].rolling(20, min_periods=10).mean()
        / data["amount"].rolling(60, min_periods=30).mean()
    )

    if not basic.empty:
        basic = basic.copy()
        basic["trade_date"] = pd.to_datetime(basic["trade_date"], format="%Y%m%d")
        basic = to_numeric(basic, {"ts_code", "trade_date"}).sort_values("trade_date")
        basic_cols = [
            "trade_date",
            "turnover_rate",
            "turnover_rate_f",
            "volume_ratio",
            "pe_ttm",
            "pb",
            "circ_mv",
            "float_share",
            "free_share",
        ]
        data = data.merge(basic[[c for c in basic_cols if c in basic.columns]], on="trade_date", how="left")

    if not moneyflow.empty:
        moneyflow = moneyflow.copy()
        moneyflow["trade_date"] = pd.to_datetime(moneyflow["trade_date"], format="%Y%m%d")
        moneyflow = to_numeric(moneyflow, {"ts_code", "trade_date"}).sort_values("trade_date")
        flow_cols = [
            "trade_date",
            "buy_lg_amount",
            "sell_lg_amount",
            "buy_elg_amount",
            "sell_elg_amount",
            "net_mf_amount",
        ]
        data = data.merge(moneyflow[[c for c in flow_cols if c in moneyflow.columns]], on="trade_date", how="left")
        data["large_net_amount"] = data.get("buy_lg_amount", 0) - data.get("sell_lg_amount", 0)
        data["elg_net_amount"] = data.get("buy_elg_amount", 0) - data.get("sell_elg_amount", 0)
        data["main_net_amount"] = data["large_net_amount"] + data["elg_net_amount"]
        data["main_net_pct_amount"] = data["main_net_amount"] / data["amount_wan"].replace(0, np.nan)
        data["net_mf_pct_amount"] = data["net_mf_amount"] / data["amount_wan"].replace(0, np.nan)
        for window in (5, 10, 20, 60):
            data[f"main_net_{window}d"] = data["main_net_amount"].rolling(window, min_periods=max(3, window // 2)).sum()
            data[f"main_net_pct_{window}d"] = data["main_net_pct_amount"].rolling(window, min_periods=max(3, window // 2)).mean()
            data[f"net_mf_{window}d"] = data["net_mf_amount"].rolling(window, min_periods=max(3, window // 2)).sum()
    else:
        data["main_net_amount"] = np.nan
        data["main_net_pct_amount"] = np.nan

    if not margin.empty:
        margin = margin.copy()
        margin["trade_date"] = pd.to_datetime(margin["trade_date"], format="%Y%m%d")
        margin = to_numeric(margin, {"ts_code", "trade_date"}).sort_values("trade_date")
        margin_cols = ["trade_date", "rzye", "rzmre", "rzche", "rqye", "rzrqye"]
        data = data.merge(margin[[c for c in margin_cols if c in margin.columns]], on="trade_date", how="left")
        data["financing_net_buy"] = data.get("rzmre", 0) - data.get("rzche", 0)
        data["financing_balance_chg"] = data.get("rzye", pd.Series(index=data.index, dtype=float)).diff()
        data["financing_balance_pct_mv"] = data.get("rzye", np.nan) / (data.get("circ_mv", np.nan) * 10000)
        for window in (5, 20, 60):
            data[f"financing_net_buy_{window}d"] = data["financing_net_buy"].rolling(window, min_periods=max(3, window // 2)).sum()
            data[f"financing_balance_chg_{window}d"] = data["financing_balance_chg"].rolling(window, min_periods=max(3, window // 2)).sum()

    direction = np.sign(data["close"].diff()).fillna(0.0)
    data["obv"] = (direction * data["vol"]).cumsum()
    money_flow_multiplier = ((data["close"] - data["low"]) - (data["high"] - data["close"])) / (
        data["high"] - data["low"]
    ).replace(0, np.nan)
    data["cmf_20"] = (money_flow_multiplier * data["vol"]).rolling(20, min_periods=10).sum() / data["vol"].rolling(
        20, min_periods=10
    ).sum()
    typical_price = (data["high"] + data["low"] + data["close"]) / 3
    raw_money_flow = typical_price * data["vol"]
    pos_flow = raw_money_flow.where(typical_price.diff() > 0, 0.0)
    neg_flow = raw_money_flow.where(typical_price.diff() < 0, 0.0)
    money_ratio = pos_flow.rolling(14, min_periods=8).sum() / neg_flow.rolling(14, min_periods=8).sum().replace(0, np.nan)
    data["mfi_14"] = 100 - 100 / (1 + money_ratio)

    for horizon in (5, 10, 20, 60):
        data[f"future_ret_{horizon}d"] = data["close"].shift(-horizon) / data["close"] - 1
    for column in (
        "main_net_pct_amount",
        "main_net_pct_20d",
        "financing_net_buy_20d",
        "financing_balance_pct_mv",
        "amount_ratio_20_60",
        "turnover_rate_f",
        "cmf_20",
        "mfi_14",
    ):
        if column in data.columns:
            data[f"{column}_rank_252"] = rolling_rank_last(data[column], 252, 80)
    return data


def correlation_table(data: pd.DataFrame) -> pd.DataFrame:
    indicators = [
        "main_net_pct_amount",
        "main_net_pct_5d",
        "main_net_pct_20d",
        "main_net_20d",
        "net_mf_20d",
        "financing_net_buy_5d",
        "financing_net_buy_20d",
        "financing_balance_chg_20d",
        "financing_balance_pct_mv",
        "amount_ratio_20_60",
        "turnover_rate_f",
        "cmf_20",
        "mfi_14",
    ]
    horizons = [5, 10, 20, 60]
    rows = []
    for indicator in indicators:
        if indicator not in data.columns:
            continue
        for horizon in horizons:
            target = f"future_ret_{horizon}d"
            valid = data[[indicator, target]].replace([np.inf, -np.inf], np.nan).dropna()
            if len(valid) < 80:
                continue
            ranked = valid.rank(method="average")
            rows.append(
                {
                    "indicator": indicator,
                    "future_horizon": horizon,
                    "samples": len(valid),
                    "pearson_corr": valid[indicator].corr(valid[target], method="pearson"),
                    "spearman_corr": ranked[indicator].corr(ranked[target], method="pearson"),
                }
            )
    table = pd.DataFrame(rows)
    if table.empty:
        return table
    return table.sort_values(["future_horizon", "spearman_corr"], ascending=[True, False])


def summarize_latest(data: pd.DataFrame) -> Dict[str, str]:
    latest = data.dropna(subset=["close"]).iloc[-1]
    prev20 = data.dropna(subset=["close"]).tail(20)
    main20 = latest.get("main_net_20d", np.nan)
    fin20 = latest.get("financing_net_buy_20d", np.nan)
    flow_rank = latest.get("main_net_pct_20d_rank_252", np.nan)
    financing_rank = latest.get("financing_balance_pct_mv_rank_252", np.nan)
    return {
        "latest_date": latest["trade_date"].strftime("%Y-%m-%d"),
        "close": f"{latest['close']:.2f}",
        "return_20d": f"{(latest['close'] / prev20['close'].iloc[0] - 1) * 100:.1f}%" if len(prev20) >= 2 else "NA",
        "main_net_20d": f"{main20 / 10000:.2f}亿元" if pd.notna(main20) else "NA",
        "main_net_pct_rank": f"{flow_rank * 100:.0f}分位" if pd.notna(flow_rank) else "NA",
        "financing_net_20d": f"{fin20 / 100000000:.2f}亿元" if pd.notna(fin20) else "NA",
        "financing_balance_rank": f"{financing_rank * 100:.0f}分位" if pd.notna(financing_rank) else "NA",
    }


INDICATOR_LABELS = {
    "main_net_pct_amount": "当日主力净额/成交额",
    "main_net_pct_5d": "近5日主力净流入强度",
    "main_net_pct_20d": "近20日主力净流入强度",
    "main_net_20d": "近20日主力净流入金额",
    "net_mf_20d": "近20日全口径净流入金额",
    "financing_net_buy_5d": "近5日融资净买入",
    "financing_net_buy_20d": "近20日融资净买入",
    "financing_balance_chg_20d": "近20日融资余额变化",
    "financing_balance_pct_mv": "融资余额/流通市值",
    "amount_ratio_20_60": "成交额20日均值/60日均值",
    "turnover_rate_f": "自由流通换手率",
    "cmf_20": "CMF20量价资金流",
    "mfi_14": "MFI14资金流强弱",
}


def chart_series(data: pd.DataFrame, columns: List[str]) -> List[Dict[str, object]]:
    rows = []
    for _, row in data.iterrows():
        item = {"date": row["trade_date"].strftime("%Y-%m-%d")}
        for column in columns:
            value = row.get(column, np.nan)
            item[column] = None if pd.isna(value) or not np.isfinite(value) else round(float(value), 6)
        rows.append(item)
    return rows


def write_html(args: argparse.Namespace, data: pd.DataFrame, corr: pd.DataFrame, summary: Dict[str, str]) -> Path:
    code_slug = args.ts_code.replace(".", "_")
    path = REPORT_DIR / f"{code_slug}_funds.html"
    columns = [
        "close",
        "main_net_20d",
        "main_net_pct_20d",
        "financing_net_buy_20d",
        "financing_balance_pct_mv",
        "amount_ratio_20_60",
        "cmf_20",
        "mfi_14",
    ]
    view = data.tail(520).copy()
    payload = json.dumps(chart_series(view, columns), ensure_ascii=False)
    heatmap_payload = json.dumps(
        corr[["indicator", "future_horizon", "spearman_corr"]].to_dict("records") if not corr.empty else [],
        ensure_ascii=False,
    )
    corr_rows = corr.head(40).to_dict("records") if not corr.empty else []
    corr_html = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(row['indicator']))}</td>"
        f"<td>{html.escape(INDICATOR_LABELS.get(str(row['indicator']), ''))}</td>"
        f"<td>{int(row['future_horizon'])}日</td>"
        f"<td>{int(row['samples'])}</td>"
        f"<td>{row['spearman_corr']:.3f}</td>"
        f"<td>{row['pearson_corr']:.3f}</td>"
        "</tr>"
        for row in corr_rows
    )
    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{html.escape(args.name)} 资金流观察</title>
<style>
:root {{ --paper:#f7f3eb; --ink:#17202a; --muted:#5d6875; --line:#d8cdbc; --blue:#2468b2; --red:#b9423a; --green:#1f8a70; }}
body {{ margin:0; background:var(--paper); color:var(--ink); font:15px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB",sans-serif; }}
main {{ max-width:1180px; margin:0 auto; padding:30px 24px 56px; }}
h1 {{ font-size:30px; margin:0 0 8px; }}
h2 {{ font-size:20px; margin:30px 0 12px; }}
p {{ margin:0; color:var(--muted); }}
.summary {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin:20px 0 8px; }}
.metric {{ border:1px solid var(--line); background:rgba(255,255,255,.42); border-radius:8px; padding:12px 14px; }}
.metric b {{ display:block; font-size:20px; margin-top:4px; }}
.chart {{ border:1px solid var(--line); border-radius:8px; background:rgba(255,255,255,.45); margin:12px 0 16px; padding:12px; }}
canvas {{ width:100%; height:280px; display:block; }}
.heatmap {{ border:1px solid var(--line); border-radius:8px; background:rgba(255,255,255,.45); padding:12px; overflow:auto; }}
.heatmap table {{ min-width:760px; border:0; background:transparent; }}
.heatmap td,.heatmap th {{ text-align:center; border:1px solid rgba(216,205,188,.8); }}
.heatmap td:first-child,.heatmap th:first-child {{ text-align:left; position:sticky; left:0; background:#f7f3eb; }}
.cell {{ color:#111; font-variant-numeric:tabular-nums; }}
table {{ width:100%; border-collapse:collapse; background:rgba(255,255,255,.35); border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
th,td {{ padding:10px 12px; border-bottom:1px solid var(--line); text-align:left; }}
th {{ color:var(--muted); font-weight:700; }}
.note {{ margin-top:14px; }}
</style>
</head>
<body>
<main>
<h1>{html.escape(args.name)}（{html.escape(args.ts_code)}）资金流观察</h1>
<p>曲线只回答“资金行为和价格有没有同向/领先关系”，不直接等同买卖建议。数据截至 {summary['latest_date']}。</p>
<section class="summary">
<div class="metric">最新收盘<b>{summary['close']}</b></div>
<div class="metric">近20日涨跌<b>{summary['return_20d']}</b></div>
<div class="metric">近20日主力净额<b>{summary['main_net_20d']}</b></div>
<div class="metric">主力强度分位<b>{summary['main_net_pct_rank']}</b></div>
<div class="metric">近20日融资净买<b>{summary['financing_net_20d']}</b></div>
<div class="metric">融资余额分位<b>{summary['financing_balance_rank']}</b></div>
</section>

<h2>价格 vs 主力净流入</h2>
<div class="chart"><canvas id="chartMain"></canvas></div>
<h2>价格 vs 融资资金</h2>
<div class="chart"><canvas id="chartMargin"></canvas></div>
<h2>价格 vs 量价资金强度</h2>
<div class="chart"><canvas id="chartTech"></canvas></div>

<h2>资金指标领先相关性热力图</h2>
<div class="heatmap" id="heatmap"></div>

<h2>指标领先未来收益相关性</h2>
<table>
<thead><tr><th>指标</th><th>中文含义</th><th>未来收益窗口</th><th>样本</th><th>Spearman</th><th>Pearson</th></tr></thead>
<tbody>{corr_html}</tbody>
</table>
<p class="note">读法：正相关表示该指标越高，后续对应窗口收益通常越高；负相关相反。Spearman 更看重排序关系，适合金融数据的非线性和极端值。</p>
</main>
<script>
const DATA = {payload};
const HEATMAP = {heatmap_payload};
function scale(values, pad=0.08) {{
  const xs = values.filter(v => v !== null && Number.isFinite(v));
  if (!xs.length) return [0, 1];
  let min = Math.min(...xs), max = Math.max(...xs);
  if (min === max) {{ min -= 1; max += 1; }}
  const gap = (max - min) * pad;
  return [min - gap, max + gap];
}}
function draw(canvasId, titleLeft, leftKey, rightSeries) {{
  const canvas = document.getElementById(canvasId);
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr; canvas.height = rect.height * dpr;
  const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr);
  const w = rect.width, h = rect.height, l = 54, r = 64, t = 24, b = 34;
  ctx.clearRect(0,0,w,h);
  ctx.strokeStyle = '#d8cdbc'; ctx.lineWidth = 1;
  for (let i=0;i<5;i++) {{
    const y = t + (h-t-b)*i/4;
    ctx.beginPath(); ctx.moveTo(l,y); ctx.lineTo(w-r,y); ctx.stroke();
  }}
  const leftVals = DATA.map(d => d[leftKey]);
  const [lmin,lmax] = scale(leftVals);
  const allRight = rightSeries.flatMap(s => DATA.map(d => d[s.key]));
  const [rmin,rmax] = scale(allRight);
  const x = i => l + (w-l-r) * i / Math.max(1, DATA.length - 1);
  const yl = v => t + (h-t-b) * (1 - (v-lmin)/(lmax-lmin));
  const yr = v => t + (h-t-b) * (1 - (v-rmin)/(rmax-rmin));
  function line(key, color, yfn) {{
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
    let started = false;
    DATA.forEach((d,i) => {{
      const v = d[key];
      if (v === null || !Number.isFinite(v)) {{ started = false; return; }}
      const px = x(i), py = yfn(v);
      if (!started) {{ ctx.moveTo(px,py); started = true; }} else {{ ctx.lineTo(px,py); }}
    }});
    ctx.stroke();
  }}
  line(leftKey, '#17202a', yl);
  rightSeries.forEach(s => line(s.key, s.color, yr));
  ctx.fillStyle = '#5d6875'; ctx.font = '12px -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif';
  ctx.fillText(titleLeft, l, 14);
  let legendX = l + 140;
  rightSeries.forEach(s => {{ ctx.fillStyle = s.color; ctx.fillText(s.name, legendX, 14); legendX += ctx.measureText(s.name).width + 18; }});
  ctx.fillStyle = '#5d6875';
  ctx.fillText(DATA[0]?.date || '', l, h-10);
  ctx.fillText(DATA[DATA.length-1]?.date || '', w-r-82, h-10);
}}
function redraw() {{
  draw('chartMain', '黑线：收盘价', 'close', [
    {{key:'main_net_20d', name:'20日主力净额', color:'#b9423a'}},
    {{key:'main_net_pct_20d', name:'20日主力净额/成交额', color:'#1f8a70'}}
  ]);
  draw('chartMargin', '黑线：收盘价', 'close', [
    {{key:'financing_net_buy_20d', name:'20日融资净买', color:'#2468b2'}},
    {{key:'financing_balance_pct_mv', name:'融资余额/流通市值', color:'#b9423a'}}
  ]);
  draw('chartTech', '黑线：收盘价', 'close', [
    {{key:'amount_ratio_20_60', name:'成交额20/60', color:'#2468b2'}},
    {{key:'cmf_20', name:'CMF20', color:'#1f8a70'}},
    {{key:'mfi_14', name:'MFI14', color:'#b9423a'}}
  ]);
  drawHeatmap();
}}
function heatColor(v) {{
  if (v === null || !Number.isFinite(v)) return '#eee8dd';
  const x = Math.max(-0.18, Math.min(0.18, v)) / 0.18;
  if (x >= 0) {{
    const a = Math.round(245 - 70*x), b = Math.round(238 - 120*x), c = Math.round(228 - 130*x);
    return `rgb(${{a}},${{b}},${{c}})`;
  }}
  const y = -x;
  const a = Math.round(235 - 115*y), b = Math.round(240 - 105*y), c = Math.round(236 - 75*y);
  return `rgb(${{a}},${{b}},${{c}})`;
}}
function drawHeatmap() {{
  const host = document.getElementById('heatmap');
  if (!host || host.dataset.done) return;
  const horizons = [5,10,20,60];
  const names = Array.from(new Set(HEATMAP.map(d => d.indicator)));
  const label = {json.dumps(INDICATOR_LABELS, ensure_ascii=False)};
  const lookup = new Map(HEATMAP.map(d => [`${{d.indicator}}_${{d.future_horizon}}`, d.spearman_corr]));
  let out = '<table><thead><tr><th>指标</th>' + horizons.map(h => `<th>未来${{h}}日</th>`).join('') + '</tr></thead><tbody>';
  for (const name of names) {{
    out += `<tr><td>${{label[name] || name}}</td>`;
    for (const h of horizons) {{
      const v = lookup.get(`${{name}}_${{h}}`);
      const text = Number.isFinite(v) ? v.toFixed(3) : '-';
      out += `<td class="cell" style="background:${{heatColor(v)}}">${{text}}</td>`;
    }}
    out += '</tr>';
  }}
  out += '</tbody></table>';
  host.innerHTML = out;
  host.dataset.done = '1';
}}
window.addEventListener('resize', redraw);
redraw();
</script>
</body>
</html>
"""
    path.write_text(html_doc, encoding="utf-8")
    return path


def main() -> None:
    args = parse_args()
    ensure_dirs()
    pro = tushare_client()
    daily = fetch_by_year(pro, "daily", args.ts_code, args.start_date, args.end_date, args.refresh, args.pause)
    basic = fetch_by_year(pro, "daily_basic", args.ts_code, args.start_date, args.end_date, args.refresh, args.pause)
    moneyflow = fetch_by_year(pro, "moneyflow", args.ts_code, args.start_date, args.end_date, args.refresh, args.pause)
    margin = fetch_by_year(pro, "margin_detail", args.ts_code, args.start_date, args.end_date, args.refresh, args.pause)
    data = compute_indicators(daily, basic, moneyflow, margin)
    corr = correlation_table(data)

    code_slug = args.ts_code.replace(".", "_")
    indicator_path = EXPORT_DIR / f"{code_slug}_fund_indicators.csv"
    corr_path = EXPORT_DIR / f"{code_slug}_fund_correlations.csv"
    data.to_csv(indicator_path, index=False)
    corr.to_csv(corr_path, index=False)
    report_path = write_html(args, data, corr, summarize_latest(data))
    print(f"Wrote {indicator_path}")
    print(f"Wrote {corr_path}")
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
