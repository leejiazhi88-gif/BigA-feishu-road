#!/usr/bin/env python3
"""Repair single-quarter profit forecasts in an existing Feishu sheet.

`report_rc.np` is often a cumulative or full-year estimate even when its
`quarter` label is Q1/Q2/Q3.  This tool therefore uses only annual Q4
consensus as the annual anchor, then allocates that annual amount by verified
historical quarterly seasonality.  It never writes a cumulative estimate into
a single-quarter cell.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_ai_chain_feishu import (  # noqa: E402
    DEFAULT_FEISHU_ENV,
    RAW_DIR,
    actual_quarter_profit_yi,
    column_name,
    get_feishu_token,
    historical_quarter_weights,
    request_json,
)

SPREADSHEET = "UJJesUWvehxTbNtNpV2cHuDcnIc"
SHEET_ID = "IgjNz6"
SHEET_TITLE = "2026-09-07（数据条）"
TARGET_HEADERS = [
    "2026Q3净利润(亿元)",
    "2026Q4净利润(亿元)",
    "2027Q1净利润(亿元)",
    "2027Q2净利润(亿元)",
    "2027Q3净利润(亿元)",
    "2027Q4净利润(亿元)",
    "2028Q1净利润(亿元)",
    "2028Q2净利润(亿元)",
    "2028Q3净利润(亿元)",
    "2028Q4净利润(亿元)",
]


def parse_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not text:
        return None
    # Actual cells can be formatted as `12.3（上个Q预测10.5）`.
    try:
        return float(text.split("（", 1)[0])
    except ValueError:
        return None


def read_income(code: str) -> pd.DataFrame:
    path = RAW_DIR / "income" / f"{code.replace('.', '_')}.csv"
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path, dtype={"end_date": str, "ann_date": str, "f_ann_date": str})
    frame["n_income_attr_p"] = pd.to_numeric(frame.get("n_income_attr_p"), errors="coerce")
    return frame


def annual_consensus_from_cache(code: str, year: int) -> float | None:
    """Use one latest annual estimate per broker, then take the median."""
    path = RAW_DIR / "report_rc" / f"{code.replace('.', '_')}.csv"
    if not path.exists():
        return None
    frame = pd.read_csv(path, dtype={"quarter": str, "report_date": str})
    if frame.empty or not {"quarter", "np", "report_date"}.issubset(frame.columns):
        return None
    frame["np"] = pd.to_numeric(frame["np"], errors="coerce")
    frame["report_date_num"] = pd.to_numeric(frame["report_date"], errors="coerce")
    frame = frame[(frame["quarter"] == f"{year}Q4") & frame["np"].notna()].copy()
    if frame.empty:
        return None
    broker = frame.get("org_name", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip()
    author = frame.get("author_name", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip()
    frame["_broker"] = broker.where(broker.ne(""), author)
    frame.loc[frame["_broker"].eq(""), "_broker"] = frame.index.astype(str)
    frame = frame.sort_values("report_date_num").drop_duplicates("_broker", keep="last")
    value = float(frame["np"].median() / 10000.0)
    return value if np.isfinite(value) else None


def quarter_weights(income: pd.DataFrame) -> dict[int, float]:
    weights = historical_quarter_weights(actual_quarter_profit_yi(income))
    total = sum(weights.values())
    return {quarter: weights[quarter] / total for quarter in range(1, 5)} if total else {quarter: 0.25 for quarter in range(1, 5)}


def split_annual(annual: float, weights: dict[int, float]) -> list[float]:
    result = [round(annual * weights[q], 1) for q in range(1, 4)]
    result.append(round(annual - sum(result), 1))
    return result


def annualize_h1(actuals: dict[str, float], weights: dict[int, float]) -> float | None:
    h1 = sum(value for value in (actuals.get("2026Q1"), actuals.get("2026Q2")) if value is not None)
    h1_weight = weights[1] + weights[2]
    if h1_weight > 0.05 and abs(h1) > 0.001:
        return h1 / h1_weight
    return None


def latest_historical_annual(actuals: dict[str, float]) -> float | None:
    for year in (2025, 2024):
        quarters = [actuals.get(f"{year}Q{quarter}") for quarter in range(1, 5)]
        if all(value is not None for value in quarters):
            return float(sum(quarters))
    return None


def build_repaired_values(
    row: list[Any], indices: dict[str, int]
) -> tuple[list[float | str], dict[str, float | str], str]:
    code = str(row[indices["代码"]]).strip()
    income = read_income(code)
    actuals = actual_quarter_profit_yi(income)
    weights = quarter_weights(income)

    annual: dict[int, float | None] = {}
    anchor_source: dict[int, str] = {}
    for year in (2026, 2027, 2028):
        supplied = parse_number(row[indices[f"{year}净利润(预测,亿元)"]])
        if supplied is not None:
            annual[year] = supplied
            anchor_source[year] = "sheet annual consensus"
        else:
            annual[year] = annual_consensus_from_cache(code, year)
            anchor_source[year] = "cached annual consensus" if annual[year] else "unavailable"

    # Fill only missing annual anchors.  Estimates are consciously conservative:
    # current year is annualized from reported H1 using the company's own
    # seasonality; later years remain flat until a broker annual estimate exists.
    if annual[2026] is None:
        annual[2026] = annualize_h1(actuals, weights) or latest_historical_annual(actuals)
        anchor_source[2026] = (
            "2026 H1 actual annualized by historical seasonality"
            if annual[2026] is not None
            else "unavailable"
        )
    for year in (2027, 2028):
        if annual[year] is None:
            prior = annual[year - 1]
            annual[year] = prior
            anchor_source[year] = (
                f"flat carry-forward from {year - 1} due to no annual consensus"
                if prior is not None
                else "unavailable"
            )

    values: list[float | str] = []
    actual_q1 = parse_number(row[indices["2026Q1净利润(亿元)"]])
    actual_q2 = parse_number(row[indices["2026Q2净利润(亿元)"]])
    q12_actual = sum(value for value in (actual_q1, actual_q2) if value is not None)
    if annual[2026] is not None:
        remaining = annual[2026] - q12_actual
        # Keep negative H2 when the annual anchor implies one.  Replacing it
        # with a positive seasonal value would silently break the annual total.
        q3 = round(remaining * weights[3] / (weights[3] + weights[4]), 1)
        q4 = round(remaining - q3, 1)
        values.extend([q3, q4])
    else:
        values.extend(["", ""])

    for year in (2027, 2028):
        if annual[year] is None:
            values.extend([""] * 4)
        else:
            values.extend(split_annual(float(annual[year]), weights))

    audit = {
        "code": code,
        "annual_2026": annual[2026] or "",
        "annual_2027": annual[2027] or "",
        "annual_2028": annual[2028] or "",
        "source_2026": anchor_source[2026],
        "source_2027": anchor_source[2027],
        "source_2028": anchor_source[2028],
        "weights": ",".join(f"Q{q}:{weights[q]:.3f}" for q in range(1, 5)),
    }
    fallback_sources = [
        f"{year}:{anchor_source[year]}"
        for year in (2026, 2027, 2028)
        if anchor_source[year] not in {"sheet annual consensus", "cached annual consensus"}
    ]
    note = ""
    if fallback_sources:
        note = "季度预测口径修复：" + "；".join(fallback_sources)
    return values, audit, note


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feishu-env", default=DEFAULT_FEISHU_ENV)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    token = get_feishu_token(args.feishu_env)
    response = request_json(
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{SPREADSHEET}/values/{SHEET_ID}!A1:AO200",
        token=token,
    )
    rows = response.get("data", {}).get("valueRange", {}).get("values", [])
    if not rows:
        raise RuntimeError("Target sheet is empty.")
    headers = [str(value) if value is not None else "" for value in rows[0]]
    indices = {header: index for index, header in enumerate(headers) if header}
    required = {"名称", "代码", "2026Q1净利润(亿元)", "2026Q2净利润(亿元)", *TARGET_HEADERS}
    required.update(f"{year}净利润(预测,亿元)" for year in (2026, 2027, 2028))
    missing = sorted(required - set(indices))
    if missing:
        raise RuntimeError(f"Unexpected target layout; missing headers: {missing}")

    output: list[list[float | str]] = []
    repaired_notes: list[list[str]] = []
    audit: list[dict[str, float | str]] = []
    for row in rows[1:113]:
        if not row or not str(row[indices["名称"]]).strip():
            output.append([""] * len(TARGET_HEADERS))
            repaired_notes.append([""])
            continue
        padded = row + [""] * max(0, len(headers) - len(row))
        repaired, detail, repair_note = build_repaired_values(padded, indices)
        output.append(repaired)
        old_note = str(padded[indices["备注"]]).strip() if len(padded) > indices["备注"] else ""
        repaired_notes.append([f"{old_note}；{repair_note}" if old_note and repair_note else repair_note or old_note])
        audit.append({"name": padded[indices["名称"]], **detail, "values": repaired})

    unresolved = [item for item in audit if any(value == "" for value in item["values"])]
    fallback_rows = [item["name"] for item, note in zip(audit, repaired_notes) if note[0] and "季度预测口径修复" in note[0]]
    print(json.dumps({
        "sheet": SHEET_TITLE,
        "rows": len(audit),
        "target": "AF:AO",
        "unresolved_rows": len(unresolved),
        "unresolved": [item["name"] for item in unresolved],
        "fallback_rows": fallback_rows,
        "sample": audit[:5],
    }, ensure_ascii=False, indent=2))
    if not args.apply:
        return

    start_col = column_name(indices[TARGET_HEADERS[0]] + 1)
    end_col = column_name(indices[TARGET_HEADERS[-1]] + 1)
    request_json(
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{SPREADSHEET}/values",
        token=token,
        method="PUT",
        body={
            "valueRange": {
                "range": f"{SHEET_ID}!{start_col}2:{end_col}{len(output) + 1}",
                "values": output,
            }
        },
    )
    note_col = column_name(indices["备注"] + 1)
    request_json(
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{SPREADSHEET}/values",
        token=token,
        method="PUT",
        body={
            "valueRange": {
                "range": f"{SHEET_ID}!{note_col}2:{note_col}{len(repaired_notes) + 1}",
                "values": repaired_notes,
            }
        },
    )
    Path("exports/ai_chain/quarter_profit_repair_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Updated {SHEET_TITLE}!{start_col}2:{end_col}{len(output) + 1}")


if __name__ == "__main__":
    main()
