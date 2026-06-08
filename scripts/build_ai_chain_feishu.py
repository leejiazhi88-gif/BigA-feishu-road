#!/usr/bin/env python3
"""Write an A/H AI industry-chain stock table into a Feishu spreadsheet."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import tushare as ts


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "ai_chain"
EXPORT_DIR = ROOT / "exports" / "ai_chain"
DEFAULT_FEISHU_ENV = "/Users/fuguiplus/Documents/Codex/2026-04-30/new-chat/xiaoniuma-feishu/.env"
DEFAULT_WIKI_URL = "https://my.feishu.cn/wiki/UxDvwwezLihFS1kVmhHcqwypnQc"


HEADERS = [
    "名称",
    "市场",
    "产业链环节",
    "细分方向",
    "代码",
    "公司产品简介",
    "供应链确定性",
    "市值(亿元)",
    "PE(TTM)",
    "2026 PE(预测)",
    "2027 PE(预测)",
    "2028 PE(预测)",
    "2025净利润(实际/预测,亿元)",
    "2026净利润(预测,亿元)",
    "2027净利润(预测,亿元)",
    "2028净利润(预测,亿元)",
    "2025净利润同比%(实际/预测)",
    "2026净利润同比%(预测)",
    "2027净利润同比%(预测)",
    "2028净利润同比%(预测)",
    "最新交易日",
    "当日涨幅%",
    "10日涨幅%",
    "30日涨幅%",
    "60日涨幅%",
    "90日涨幅%",
    "250日涨幅%",
    "预测来源",
    "数据状态",
    "备注",
]

SEGMENT_ORDER = {
    "GPU/CPU/NVLink/核心芯片": 1,
    "HBM/内存/存储": 2,
    "整机/机柜/ODM": 3,
    "光模块/CPO/硅光": 4,
    "先进封装/CoWoS/ABF": 5,
    "电源/配电/BBU": 6,
    "高速PCB/CCL": 7,
    "连接器/线缆/背板": 8,
    "MLCC/被动件/结构件": 9,
    "液冷": 10,
    "算力服务/IDC/云": 90,
    "非Rubin直接链/AI应用": 99,
}

BASE_SEGMENT_TO_RUBIN_CHAIN = {
    "算力芯片": "GPU/CPU/NVLink/核心芯片",
    "内存": "HBM/内存/存储",
    "光通信/CPO": "光模块/CPO/硅光",
    "PCB": "高速PCB/CCL",
    "ABF载板": "先进封装/CoWoS/ABF",
    "电源": "电源/配电/BBU",
    "液冷": "液冷",
    "MLCC": "MLCC/被动件/结构件",
    "模型/应用": "非Rubin直接链/AI应用",
    "端侧AI/机器人": "非Rubin直接链/AI应用",
}


AI_STOCKS: List[Dict[str, str]] = [
    # A-share compute chips and semiconductor.
    {"market": "A股", "code": "688256.SH", "segment": "算力芯片", "theme": "AI训练/推理芯片", "role": "国产AI芯片核心标的"},
    {"market": "A股", "code": "688041.SH", "segment": "算力芯片", "theme": "CPU/GPGPU", "role": "国产高性能处理器"},
    {"market": "A股", "code": "688047.SH", "segment": "算力芯片", "theme": "CPU/IP生态", "role": "国产CPU与基础软硬件生态"},
    {"market": "A股", "code": "688521.SH", "segment": "算力芯片", "theme": "芯片IP/设计服务", "role": "芯片IP平台与定制设计"},
    {"market": "A股", "code": "603501.SH", "segment": "算力芯片", "theme": "传感器/模拟", "role": "AI终端与智能硬件上游"},
    {"market": "A股", "code": "688008.SH", "segment": "内存", "theme": "内存接口/服务器芯片", "role": "服务器内存接口芯片"},
    {"market": "A股", "code": "300474.SZ", "segment": "算力芯片", "theme": "GPU/图显", "role": "国产图形处理器"},
    {"market": "A股", "code": "688525.SH", "segment": "内存", "theme": "AI服务器存储", "role": "存储模组与企业级存储"},
    {"market": "A股", "code": "603986.SH", "segment": "内存", "theme": "存储芯片设计/NOR/DRAM", "role": "NOR与利基DRAM设计，参股长鑫存储"},
    {"market": "A股", "code": "300223.SZ", "segment": "内存", "theme": "车规存储/SRAM/DRAM", "role": "车规SRAM/DRAM存储芯片"},
    {"market": "A股", "code": "688766.SH", "segment": "内存", "theme": "存储芯片设计/NOR", "role": "中小容量NOR Flash"},
    {"market": "A股", "code": "688110.SH", "segment": "内存", "theme": "存储芯片设计/SLC NAND", "role": "国产SLC NAND存储芯片"},
    {"market": "A股", "code": "301308.SZ", "segment": "内存", "theme": "存储模组/企业级SSD", "role": "企业级SSD与存储模组"},
    {"market": "A股", "code": "001309.SZ", "segment": "内存", "theme": "存储模组/主控", "role": "存储主控与嵌入式存储模组"},
    {"market": "A股", "code": "300475.SZ", "segment": "内存", "theme": "HBM/存储分销", "role": "SK海力士存储分销与HBM涨价受益"},
    {"market": "A股", "code": "688123.SH", "segment": "内存", "theme": "存储配套芯片/SPD", "role": "内存SPD与DDR5配套芯片"},
    {"market": "A股", "code": "600584.SH", "segment": "内存", "theme": "存储封测/先进封装", "role": "存储芯片封测与先进封装"},
    {"market": "A股", "code": "002371.SZ", "segment": "内存", "theme": "存储上游设备/刻蚀沉积", "role": "DRAM/NAND产线刻蚀与沉积设备"},
    {"market": "A股", "code": "688120.SH", "segment": "内存", "theme": "存储上游设备/CMP", "role": "CMP设备，DRAM/NAND制造环节"},
    # Servers, IDC and operators.
    {"market": "A股", "code": "000977.SZ", "segment": "算力基础设施", "theme": "AI服务器", "role": "AI服务器整机"},
    {"market": "A股", "code": "601138.SH", "segment": "算力基础设施", "theme": "AI服务器/代工", "role": "云厂商AI服务器制造"},
    {"market": "A股", "code": "603019.SH", "segment": "算力基础设施", "theme": "服务器/HPC", "role": "高性能计算与服务器"},
    {"market": "A股", "code": "000063.SZ", "segment": "算力基础设施", "theme": "通信/服务器", "role": "通信设备、服务器与算力网络"},
    {"market": "A股", "code": "600941.SH", "segment": "算力基础设施", "theme": "运营商云", "role": "算力网络与云资源"},
    {"market": "A股", "code": "601728.SH", "segment": "算力基础设施", "theme": "运营商云", "role": "IDC、云与算力服务"},
    {"market": "A股", "code": "600050.SH", "segment": "算力基础设施", "theme": "运营商云", "role": "通信云与行业AI"},
    {"market": "A股", "code": "300442.SZ", "segment": "算力基础设施", "theme": "IDC", "role": "数据中心基础设施"},
    {"market": "A股", "code": "300383.SZ", "segment": "算力基础设施", "theme": "IDC/云", "role": "数据中心与云服务"},
    {"market": "A股", "code": "600845.SH", "segment": "算力基础设施", "theme": "工业云/IDC", "role": "工业软件云和数据中心"},
    # Optical modules and CPO.
    {"market": "A股", "code": "300308.SZ", "segment": "光通信/CPO", "theme": "高速光模块", "role": "AI算力光模块龙头"},
    {"market": "A股", "code": "300502.SZ", "segment": "光通信/CPO", "theme": "高速光模块", "role": "800G/1.6T光模块"},
    {"market": "A股", "code": "300394.SZ", "segment": "光通信/CPO", "theme": "光器件", "role": "高速光器件与封装"},
    {"market": "A股", "code": "002281.SZ", "segment": "光通信/CPO", "theme": "光芯片/模块", "role": "光通信模块和芯片"},
    {"market": "A股", "code": "300548.SZ", "segment": "光通信/CPO", "theme": "光模块", "role": "数通光模块"},
    {"market": "A股", "code": "000988.SZ", "segment": "光通信/CPO", "theme": "光器件/激光", "role": "光器件与激光加工"},
    {"market": "A股", "code": "688498.SH", "segment": "光通信/CPO", "theme": "光芯片", "role": "高速激光器芯片"},
    {"market": "A股", "code": "300620.SZ", "segment": "光通信/CPO", "theme": "光器件", "role": "光器件与铌酸锂调制器"},
    # PCB and materials.
    {"market": "A股", "code": "002463.SZ", "segment": "PCB", "theme": "AI服务器PCB", "role": "高阶服务器PCB"},
    {"market": "A股", "code": "002916.SZ", "segment": "PCB", "theme": "PCB/封装基板", "role": "通信与服务器PCB"},
    {"market": "A股", "code": "300476.SZ", "segment": "PCB", "theme": "AI服务器PCB", "role": "高速PCB与算力板卡"},
    {"market": "A股", "code": "600183.SH", "segment": "PCB", "theme": "覆铜板", "role": "高速覆铜板材料"},
    {"market": "A股", "code": "002938.SZ", "segment": "PCB", "theme": "PCB", "role": "消费电子与服务器PCB"},
    {"market": "A股", "code": "603228.SH", "segment": "PCB", "theme": "PCB", "role": "通信与服务器PCB"},
    {"market": "A股", "code": "688183.SH", "segment": "PCB", "theme": "PCB", "role": "高速多层PCB"},
    {"market": "A股", "code": "603386.SH", "segment": "MLCC", "theme": "MLCC", "role": "被动元件与MLCC国产替代"},
    {"market": "A股", "code": "300408.SZ", "segment": "MLCC", "theme": "MLCC/被动元件", "role": "MLCC、片式电阻等被动元件"},
    {"market": "A股", "code": "002859.SZ", "segment": "MLCC", "theme": "MLCC/电子陶瓷", "role": "MLCC和电子陶瓷材料"},
    {"market": "A股", "code": "002913.SZ", "segment": "ABF载板", "theme": "IC载板", "role": "封装基板与高阶载板"},
    {"market": "A股", "code": "600183.SH", "segment": "ABF载板", "theme": "封装基板材料", "role": "封装基板材料与高频覆铜板"},
    # Cooling and power.
    {"market": "A股", "code": "002837.SZ", "segment": "液冷", "theme": "数据中心温控", "role": "液冷与机房温控"},
    {"market": "A股", "code": "301018.SZ", "segment": "液冷", "theme": "液冷温控", "role": "数据中心液冷与热管理"},
    {"market": "A股", "code": "300499.SZ", "segment": "液冷", "theme": "液冷", "role": "服务器液冷设备"},
    {"market": "A股", "code": "002335.SZ", "segment": "电源", "theme": "UPS/数据中心电源", "role": "数据中心电源系统"},
    {"market": "A股", "code": "300274.SZ", "segment": "电源", "theme": "储能/电源", "role": "算力基础设施能源侧"},
    # Applications and models.
    {"market": "A股", "code": "002230.SZ", "segment": "模型/应用", "theme": "大模型/语音AI", "role": "中文语音与行业大模型"},
    {"market": "A股", "code": "300418.SZ", "segment": "模型/应用", "theme": "AIGC/海外应用", "role": "AI应用与内容生成"},
    {"market": "A股", "code": "688111.SH", "segment": "模型/应用", "theme": "办公AI", "role": "办公软件AI化"},
    {"market": "A股", "code": "300033.SZ", "segment": "模型/应用", "theme": "金融AI", "role": "金融数据与AI投顾应用"},
    {"market": "A股", "code": "300229.SZ", "segment": "模型/应用", "theme": "NLP/政企AI", "role": "自然语言处理与知识图谱"},
    {"market": "A股", "code": "300624.SZ", "segment": "模型/应用", "theme": "创意软件AI", "role": "AIGC创意工具"},
    {"market": "A股", "code": "300364.SZ", "segment": "模型/应用", "theme": "AI内容", "role": "数字内容和IP"},
    {"market": "A股", "code": "300058.SZ", "segment": "模型/应用", "theme": "营销AI", "role": "AI营销与内容生成"},
    # Edge AI and robotics.
    {"market": "A股", "code": "300124.SZ", "segment": "端侧AI/机器人", "theme": "工业自动化", "role": "机器人控制与工业AI"},
    {"market": "A股", "code": "002475.SZ", "segment": "端侧AI/机器人", "theme": "AI终端制造", "role": "AI手机/可穿戴制造"},
    {"market": "A股", "code": "300433.SZ", "segment": "端侧AI/机器人", "theme": "AI终端结构件", "role": "智能终端玻璃与模组"},
    {"market": "A股", "code": "002600.SZ", "segment": "端侧AI/机器人", "theme": "AI终端组件", "role": "消费电子与AI硬件组件"},
    {"market": "A股", "code": "688322.SH", "segment": "端侧AI/机器人", "theme": "3D视觉", "role": "机器人与AI终端感知"},
    {"market": "A股", "code": "688207.SH", "segment": "端侧AI/机器人", "theme": "机器视觉", "role": "AI视觉算法与应用"},
    {"market": "A股", "code": "002236.SZ", "segment": "端侧AI/机器人", "theme": "机器视觉/安防AI", "role": "视觉AI硬件与方案"},
    {"market": "A股", "code": "002415.SZ", "segment": "端侧AI/机器人", "theme": "视觉AI", "role": "视觉AI与物联感知"},
    # Hong Kong.
    {"market": "港股", "code": "00700.HK", "segment": "模型/应用", "theme": "云/大模型/应用", "role": "腾讯云、混元大模型、AI应用生态"},
    {"market": "港股", "code": "09988.HK", "segment": "模型/应用", "theme": "云/大模型", "role": "阿里云、通义大模型与电商AI"},
    {"market": "港股", "code": "09888.HK", "segment": "模型/应用", "theme": "搜索/大模型", "role": "文心大模型与搜索AI"},
    {"market": "港股", "code": "01024.HK", "segment": "模型/应用", "theme": "AI内容/推荐", "role": "短视频推荐与AIGC应用"},
    {"market": "港股", "code": "01810.HK", "segment": "端侧AI/机器人", "theme": "AI终端/汽车", "role": "AI手机、IoT与智能汽车"},
    {"market": "港股", "code": "03690.HK", "segment": "模型/应用", "theme": "本地生活AI", "role": "配送调度和商家AI工具"},
    {"market": "港股", "code": "09618.HK", "segment": "模型/应用", "theme": "零售/物流AI", "role": "供应链、云和零售AI"},
    {"market": "港股", "code": "03888.HK", "segment": "模型/应用", "theme": "办公/游戏AI", "role": "WPS、游戏和办公AI"},
    {"market": "港股", "code": "03896.HK", "segment": "算力基础设施", "theme": "云计算", "role": "金山云算力和云服务"},
    {"market": "港股", "code": "09698.HK", "segment": "算力基础设施", "theme": "IDC", "role": "数据中心基础设施"},
    {"market": "港股", "code": "00941.HK", "segment": "算力基础设施", "theme": "运营商云", "role": "算力网络和云资源"},
    {"market": "A股", "code": "688981.SH", "segment": "算力芯片", "theme": "晶圆制造", "role": "先进/成熟制程晶圆制造"},
    {"market": "港股", "code": "01347.HK", "segment": "算力芯片", "theme": "晶圆制造", "role": "特色工艺和功率半导体制造"},
    {"market": "A股", "code": "688385.SH", "segment": "算力芯片", "theme": "芯片设计", "role": "安全芯片、FPGA与存储控制"},
    {"market": "港股", "code": "02382.HK", "segment": "端侧AI/机器人", "theme": "光学", "role": "AI终端摄像与光学模组"},
    {"market": "港股", "code": "02018.HK", "segment": "端侧AI/机器人", "theme": "声学/触觉", "role": "AI终端声学和传感部件"},
    {"market": "港股", "code": "06682.HK", "segment": "模型/应用", "theme": "企业AI平台", "role": "企业级AI平台与行业模型"},
    {"market": "港股", "code": "09880.HK", "segment": "端侧AI/机器人", "theme": "人形机器人", "role": "人形机器人与服务机器人"},
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki-url", default=DEFAULT_WIKI_URL)
    parser.add_argument("--spreadsheet-token", default="")
    parser.add_argument("--sheet-title", default="")
    parser.add_argument("--feishu-env", default=DEFAULT_FEISHU_ENV)
    parser.add_argument("--start-date", default="20240501")
    parser.add_argument("--end-date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--pause", type=float, default=0.08)
    parser.add_argument("--max-rows", type=int, default=199)
    return parser.parse_args()


def ensure_dirs() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def load_env(path: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def request_json(url: str, token: Optional[str] = None, method: str = "GET", body: Optional[dict] = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("code") not in (0, None):
        raise RuntimeError(f"API call failed: {payload}")
    return payload


def get_feishu_token(env_path: str) -> str:
    env = load_env(env_path)
    payload = request_json(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        method="POST",
        body={"app_id": env["FEISHU_APP_ID"], "app_secret": env["FEISHU_APP_SECRET"]},
    )
    return payload["tenant_access_token"]


def wiki_token_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path.rstrip("/")
    return path.split("/")[-1]


def resolve_spreadsheet(token: str, wiki_url: str) -> tuple[str, str]:
    node_token = wiki_token_from_url(wiki_url)
    payload = request_json(
        "https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node?"
        + urllib.parse.urlencode({"token": node_token}),
        token=token,
    )
    node = payload["data"]["node"]
    if node.get("obj_type") != "sheet":
        raise RuntimeError(f"Wiki node is {node.get('obj_type')}, not a spreadsheet.")
    spreadsheet = node["obj_token"]
    meta = request_json(
        f"https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{spreadsheet}/sheets/query",
        token=token,
    )
    sheet_id = meta["data"]["sheets"][0]["sheet_id"]
    return spreadsheet, sheet_id


def tushare_client():
    token = os.environ.get("TUSHARE_TOKEN") or getpass.getpass("TUSHARE_TOKEN: ").strip()
    if not token:
        raise SystemExit("TUSHARE_TOKEN is required.")
    ts.set_token(token)
    return ts.pro_api()


def call_with_retry(func, *args, **kwargs) -> pd.DataFrame:
    last_error = None
    for attempt in range(4):
        try:
            result = func(*args, **kwargs)
            return pd.DataFrame() if result is None else result.copy()
        except Exception as exc:
            last_error = exc
            time.sleep(0.7 * (attempt + 1))
    print(f"Warning: Tushare call failed: {last_error}")
    return pd.DataFrame()


def fetch_cached(path: Path, refresh: bool, loader) -> pd.DataFrame:
    if path.exists() and not refresh:
        try:
            return pd.read_csv(path, dtype={"trade_date": str, "report_date": str})
        except pd.errors.EmptyDataError:
            return pd.DataFrame()
    frame = loader()
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return frame


def fetch_price_history(pro, market: str, code: str, start_date: str, end_date: str, refresh: bool) -> pd.DataFrame:
    path = RAW_DIR / "prices" / f"{code.replace('.', '_')}.csv"
    if market == "港股" and path.exists() and path.stat().st_size <= 1 and not refresh:
        return fetch_hk_yahoo_history(code, False)
    api = pro.hk_daily if market == "港股" else pro.daily
    frame = fetch_cached(
        path,
        refresh,
        lambda: call_with_retry(api, ts_code=code, start_date=start_date, end_date=end_date),
    )
    if frame.empty:
        if market == "港股":
            return fetch_hk_yahoo_history(code, False)
        return frame
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    return frame.sort_values("trade_date").drop_duplicates("trade_date", keep="last")


def fetch_hk_yahoo_history(code: str, refresh: bool) -> pd.DataFrame:
    path = RAW_DIR / "prices" / f"{code.replace('.', '_')}_yahoo.csv"
    if path.exists() and not refresh:
        frame = pd.read_csv(path, dtype={"trade_date": str})
    else:
        symbol = f"{int(code.split('.')[0]):04d}.HK"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=2y&interval=1d"
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            payload = json.loads(urllib.request.urlopen(request, timeout=30).read().decode("utf-8"))
            result = payload["chart"]["result"][0]
            timestamps = result.get("timestamp", [])
            closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
            frame = pd.DataFrame(
                {
                    "trade_date": [datetime.utcfromtimestamp(ts).strftime("%Y%m%d") for ts in timestamps],
                    "close": closes,
                }
            )
            frame = frame.dropna(subset=["close"])
        except Exception as exc:
            print(f"Warning: Yahoo HK chart failed for {code}: {exc}")
            frame = pd.DataFrame(columns=["trade_date", "close"])
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)
    if frame.empty:
        return frame
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    return frame.sort_values("trade_date").drop_duplicates("trade_date", keep="last")


def fetch_daily_basic(pro, code: str, start_date: str, end_date: str, refresh: bool) -> pd.DataFrame:
    path = RAW_DIR / "daily_basic" / f"{code.replace('.', '_')}.csv"
    frame = fetch_cached(
        path,
        refresh,
        lambda: call_with_retry(pro.daily_basic, ts_code=code, start_date=start_date, end_date=end_date),
    )
    if frame.empty:
        return frame
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d")
    for column in ("total_mv", "pe_ttm"):
        frame[column] = pd.to_numeric(frame.get(column), errors="coerce")
    return frame.sort_values("trade_date").drop_duplicates("trade_date", keep="last")


def fetch_report_rc(pro, code: str, refresh: bool) -> pd.DataFrame:
    path = RAW_DIR / "report_rc" / f"{code.replace('.', '_')}.csv"
    frame = fetch_cached(
        path,
        refresh,
        lambda: call_with_retry(pro.report_rc, ts_code=code, start_date="20250101", end_date=date.today().strftime("%Y%m%d")),
    )
    if frame.empty:
        return frame
    for column in ("np", "pe"):
        frame[column] = pd.to_numeric(frame.get(column), errors="coerce")
    frame["report_date"] = frame["report_date"].astype(str)
    return frame


def fetch_income(pro, code: str, refresh: bool) -> pd.DataFrame:
    path = RAW_DIR / "income" / f"{code.replace('.', '_')}.csv"
    frame = fetch_cached(
        path,
        refresh,
        lambda: call_with_retry(
            pro.income,
            ts_code=code,
            start_date="20240101",
            end_date=date.today().strftime("%Y%m%d"),
            fields="ts_code,ann_date,f_ann_date,end_date,report_type,n_income_attr_p",
        ),
    )
    if frame.empty:
        return frame
    frame["end_date"] = frame["end_date"].astype(str)
    frame["ann_date"] = frame["ann_date"].astype(str)
    frame["f_ann_date"] = frame.get("f_ann_date", "").astype(str)
    frame["n_income_attr_p"] = pd.to_numeric(frame.get("n_income_attr_p"), errors="coerce")
    return frame


def latest_name_maps(pro) -> tuple[Dict[str, str], Dict[str, str]]:
    stock_basic = call_with_retry(
        pro.stock_basic,
        exchange="",
        list_status="L",
        fields="ts_code,name",
    )
    hk_basic = call_with_retry(pro.hk_basic)
    a_names = dict(zip(stock_basic.get("ts_code", []), stock_basic.get("name", [])))
    hk_names = dict(zip(hk_basic.get("ts_code", []), hk_basic.get("name", [])))
    return a_names, hk_names


def pct_return(history: pd.DataFrame, days: int) -> Optional[float]:
    if history.empty or len(history) <= days:
        return None
    latest = history["close"].iloc[-1]
    base = history["close"].iloc[-(days + 1)]
    if pd.isna(latest) or pd.isna(base) or base == 0:
        return None
    return float((latest / base - 1) * 100)


def yearly_return(history: pd.DataFrame, year: int) -> Optional[float]:
    if history.empty:
        return None
    year_frame = history[history["trade_date"].dt.year == year]
    if year_frame.empty:
        return None
    first = year_frame["close"].iloc[0]
    last = year_frame["close"].iloc[-1]
    if pd.isna(first) or pd.isna(last) or first == 0:
        return None
    return float((last / first - 1) * 100)


def fmt_num(value: Optional[float], digits: int = 1):
    if value is None or pd.isna(value) or not np.isfinite(value):
        return ""
    return round(float(value), digits)


def fmt_int(value: Optional[float]):
    if value is None or pd.isna(value) or not np.isfinite(value):
        return ""
    return int(round(float(value)))


def fmt_fixed_decimal(value: object, digits: int = 1) -> str:
    if value == "" or value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(number):
        return ""
    return f"{number:.{digits}f}"


def fmt_pct_value(value: Optional[float], digits: int = 1):
    if value is None or pd.isna(value) or not np.isfinite(value):
        return ""
    return round(float(value) / 100.0, digits + 2)


def actual_profit_yi(income: pd.DataFrame, year: int) -> Optional[float]:
    if income.empty:
        return None
    subset = income[income["end_date"] == f"{year}1231"].copy()
    subset = subset[pd.notna(subset.get("n_income_attr_p"))]
    if subset.empty:
        return None
    subset["_report_rank"] = np.where(subset.get("report_type").astype(str) == "1", 0, 1)
    subset = subset.sort_values(["_report_rank", "f_ann_date", "ann_date"], ascending=[True, False, False])
    value = float(subset.iloc[0]["n_income_attr_p"] / 100000000.0)
    return value if np.isfinite(value) else None


def profit_cells(report: pd.DataFrame, income: pd.DataFrame, market_cap_yi: Optional[float]) -> tuple[Dict[int, str], Dict[int, str], Dict[int, str], str]:
    profit_values: Dict[int, Optional[float]] = {}
    pes: Dict[int, str] = {}
    counts = []
    for year in (2024, 2025):
        actual = actual_profit_yi(income, year)
        profit_values[year] = actual

    for year in (2025, 2026, 2027, 2028):
        if year == 2025 and profit_values.get(year) is not None:
            counts.append(f"{year}:实际")
        elif report.empty or "quarter" not in report.columns:
            profit_values.setdefault(year, None)
        else:
            subset = report[report["quarter"].astype(str) == f"{year}Q4"].copy()
            subset = subset[pd.notna(subset.get("np"))]
            if not subset.empty:
                profit_yi = float(subset["np"].median() / 10000.0)
                profit_values[year] = profit_yi
                counts.append(f"{year}:{len(subset)}")
            else:
                profit_values.setdefault(year, None)

        profit_yi = profit_values.get(year)
        pe = market_cap_yi / profit_yi if market_cap_yi and profit_yi and profit_yi > 0 else np.nan
        pes[year] = fmt_num(pe) if np.isfinite(pe) else ""

    profits: Dict[int, str] = {year: fmt_num(profit_values.get(year)) for year in (2025, 2026, 2027, 2028)}
    yoy: Dict[int, str] = {}
    for year in (2025, 2026, 2027, 2028):
        current = profit_values.get(year)
        previous = profit_values.get(year - 1)
        if current is None or previous is None or pd.isna(current) or pd.isna(previous) or previous == 0:
            yoy[year] = ""
        else:
            yoy[year] = fmt_pct_value((current / previous - 1.0) * 100)

    source = "report_rc中位数(" + ",".join(counts) + ")" if counts else ""
    return profits, yoy, pes, source


def certainty_for_item(item: Dict[str, str]) -> str:
    code = item["code"]
    segment = item["segment"]
    direct_supply = {
        "002463.SZ",  # 沪电股份：AI服务器高阶PCB供应链确定性较高
        "300308.SZ",  # 中际旭创：高速光模块龙头
        "300502.SZ",  # 新易盛：高速光模块龙头
        "601138.SH",  # 工业富联：AI服务器制造链条确定性较高
    }
    high_related_segments = {"内存", "PCB", "MLCC", "ABF载板", "电源", "液冷", "光通信/CPO"}
    if code in direct_supply:
        return "确定"
    if segment in high_related_segments:
        return "高相关"
    return "主题映射"


def rubin_chain_segment_for_item(item: Dict[str, str]) -> str:
    segment = item["segment"]
    theme = item.get("theme", "")
    role = item.get("role", "")
    text = f"{theme} {role}"

    if segment == "算力基础设施":
        if any(keyword in text for keyword in ("AI服务器", "服务器", "HPC", "通信设备")):
            return "整机/机柜/ODM"
        if any(keyword in text for keyword in ("IDC", "云", "运营商", "数据中心")):
            return "算力服务/IDC/云"

    return BASE_SEGMENT_TO_RUBIN_CHAIN.get(segment, segment)


def row_market_cap(row: List[object]) -> float:
    if len(row) <= 7:
        return 0.0
    try:
        return float(row[7])
    except (TypeError, ValueError):
        return 0.0


def build_rows(pro, args: argparse.Namespace) -> List[List[object]]:
    a_names, hk_names = latest_name_maps(pro)
    rows = []
    seen = set()
    ordered_items = sorted(
        enumerate(AI_STOCKS),
        key=lambda pair: (SEGMENT_ORDER.get(pair[1]["segment"], 99), pair[0]),
    )
    for _, item in ordered_items:
        code = item["code"]
        row_key = (code, item["segment"], item["theme"])
        if row_key in seen:
            continue
        seen.add(row_key)
        market = item["market"]
        rubin_segment = rubin_chain_segment_for_item(item)
        print(f"Building {market} {code}")
        price = fetch_price_history(pro, market, code, args.start_date, args.end_date, args.refresh)
        latest_date = price["trade_date"].iloc[-1].strftime("%Y-%m-%d") if not price.empty else ""
        returns = {days: pct_return(price, days) for days in (1, 10, 30, 60, 90, 250)}

        market_cap_yi = None
        pe_ttm = None
        forecast_profits = {2025: "", 2026: "", 2027: "", 2028: ""}
        profit_yoy = {2025: "", 2026: "", 2027: "", 2028: ""}
        forecast_pes = {2025: "", 2026: "", 2027: "", 2028: ""}
        forecast_source = ""
        status = "OK"
        note = ""
        if market == "A股":
            basic = fetch_daily_basic(pro, code, args.start_date, args.end_date, args.refresh)
            if not basic.empty:
                latest_basic = basic.iloc[-1]
                market_cap_yi = float(latest_basic["total_mv"] / 10000) if pd.notna(latest_basic.get("total_mv")) else None
                pe_ttm = float(latest_basic["pe_ttm"]) if pd.notna(latest_basic.get("pe_ttm")) else None
            report = fetch_report_rc(pro, code, args.refresh)
            income = fetch_income(pro, code, args.refresh)
            forecast_profits, profit_yoy, forecast_pes, forecast_source = profit_cells(report, income, market_cap_yi)
            if not forecast_source:
                status = "缺预测"
                note = "未取到2025-2028 Q4券商预测"
            time.sleep(args.pause)
        else:
            status = "港股缺估值/预测"
            note = "Tushare当前仅填港股行情涨幅；市值、PE、预测待接入港股估值/一致预期源"

        name_map = hk_names if market == "港股" else a_names
        name = name_map.get(code, "")
        rows.append(
            [
                name,
                market,
                rubin_segment,
                item["theme"],
                code,
                item["role"],
                item.get("certainty", certainty_for_item(item)),
                fmt_int(market_cap_yi),
                fmt_num(pe_ttm),
                forecast_pes[2026],
                forecast_pes[2027],
                forecast_pes[2028],
                fmt_fixed_decimal(forecast_profits[2025]),
                fmt_fixed_decimal(forecast_profits[2026]),
                fmt_fixed_decimal(forecast_profits[2027]),
                fmt_fixed_decimal(forecast_profits[2028]),
                profit_yoy[2025],
                profit_yoy[2026],
                profit_yoy[2027],
                profit_yoy[2028],
                latest_date,
                fmt_num(returns[1]),
                fmt_num(returns[10]),
                fmt_num(returns[30]),
                fmt_num(returns[60]),
                fmt_num(returns[90]),
                fmt_num(returns[250]),
                forecast_source,
                status,
                note,
            ]
        )
        time.sleep(args.pause)
    rows.sort(key=lambda row: (SEGMENT_ORDER.get(row[2], 999), -row_market_cap(row), str(row[4])))
    return rows[: args.max_rows]


def write_sheet(token: str, spreadsheet: str, sheet_id: str, values: List[List[object]]) -> None:
    max_rows = 200
    width = len(HEADERS)
    clear_width = max(width, 29)
    padded = [HEADERS] + values
    while len(padded) < max_rows:
        padded.append([""] * clear_width)
    padded = [row[:width] + [""] * (clear_width - len(row[:width])) for row in padded[:max_rows]]
    range_name = f"{sheet_id}!A1:{column_name(clear_width)}{max_rows}"
    body = {"valueRange": {"range": range_name, "values": padded}}
    request_json(
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet}/values",
        token=token,
        method="PUT",
        body=body,
    )
    apply_sheet_styles(token, spreadsheet, sheet_id, values, max_rows, clear_width)


def apply_sheet_styles(token: str, spreadsheet: str, sheet_id: str, values: List[List[object]], max_rows: int, clear_width: int) -> None:
    styles = [
        {"ranges": [f"{sheet_id}!A2:{column_name(clear_width)}{max_rows}"], "style": {"backColor": "#FFFFFF"}},
        {"ranges": [f"{sheet_id}!H2:H{max_rows}"], "style": {"formatter": "0"}},
        {"ranges": [f"{sheet_id}!M2:P{max_rows}"], "style": {"formatter": "@"}},
        {"ranges": [f"{sheet_id}!Q2:T{max_rows}"], "style": {"formatter": "0.00%"}},
        {"ranges": [f"{sheet_id}!G2:L{max_rows}"], "style": {"foreColor": "#000000"}},
    ]

    palette = [
        "#EAF4FF",
        "#EAF7EA",
        "#FFF4E5",
        "#F3ECFF",
        "#E9F7F6",
        "#FFF0F3",
        "#F4F1E8",
        "#EEF2F7",
    ]
    segment_colors: Dict[str, str] = {}
    color_ranges: Dict[str, List[str]] = {}
    for idx, row in enumerate(values, start=2):
        segment = row[2] if len(row) > 2 else ""
        if not segment:
            continue
        if segment not in segment_colors:
            segment_colors[segment] = palette[len(segment_colors) % len(palette)]
        color = segment_colors[segment]
        color_ranges.setdefault(color, []).append(f"{sheet_id}!A{idx}:{column_name(clear_width)}{idx}")
    for color, ranges in color_ranges.items():
        styles.append({"ranges": ranges, "style": {"backColor": color}})

    green_ranges: List[str] = []
    red_ranges: List[str] = []
    for row_idx, row in enumerate(values, start=2):
        for col_idx in range(9, 13):
            value = row[col_idx - 1] if len(row) >= col_idx else ""
            if isinstance(value, (int, float)) and value < 30:
                green_ranges.append(f"{sheet_id}!{column_name(col_idx)}{row_idx}:{column_name(col_idx)}{row_idx}")
        last_pe_value = row[11] if len(row) > 11 else ""
        if isinstance(last_pe_value, (int, float)) and last_pe_value > 50:
            red_ranges.append(f"{sheet_id}!L{row_idx}:L{row_idx}")
    if green_ranges:
        styles.append({"ranges": green_ranges, "style": {"foreColor": "#00B050"}})
    if red_ranges:
        styles.append({"ranges": red_ranges, "style": {"foreColor": "#C00000"}})

    request_json(
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet}/styles_batch_update",
        token=token,
        method="PUT",
        body={"data": styles},
    )


def column_name(index: int) -> str:
    chars = []
    while index:
        index, rem = divmod(index - 1, 26)
        chars.append(chr(ord("A") + rem))
    return "".join(reversed(chars))


def find_or_create_sheet(token: str, spreadsheet: str, title: str) -> str:
    meta = request_json(
        f"https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{spreadsheet}/sheets/query",
        token=token,
    )
    sheets = meta["data"]["sheets"]
    for sheet in sheets:
        if sheet["title"] == title:
            return sheet["sheet_id"]

    payload = request_json(
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet}/sheets_batch_update",
        token=token,
        method="POST",
        body={"requests": [{"addSheet": {"properties": {"title": title, "index": len(sheets)}}}]},
    )
    return payload["data"]["replies"][0]["addSheet"]["properties"]["sheetId"]


def main() -> None:
    args = parse_args()
    ensure_dirs()
    pro = tushare_client()
    rows = build_rows(pro, args)
    export_path = EXPORT_DIR / "ai_chain_stocks.csv"
    pd.DataFrame(rows, columns=HEADERS).to_csv(export_path, index=False)
    feishu_token = get_feishu_token(args.feishu_env)
    if args.spreadsheet_token:
        spreadsheet = args.spreadsheet_token
    else:
        spreadsheet, sheet_id = resolve_spreadsheet(feishu_token, args.wiki_url)
    if args.sheet_title:
        sheet_id = find_or_create_sheet(feishu_token, spreadsheet, args.sheet_title)
    elif not args.spreadsheet_token:
        # sheet_id already resolved from wiki URL
        pass
    else:
        sheet_id = request_json(
            f"https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{spreadsheet}/sheets/query",
            token=feishu_token,
        )["data"]["sheets"][0]["sheet_id"]
    write_sheet(feishu_token, spreadsheet, sheet_id, rows)
    print(f"Wrote {len(rows)} rows to Feishu sheet {sheet_id}")
    print(f"Exported {export_path}")


if __name__ == "__main__":
    main()
