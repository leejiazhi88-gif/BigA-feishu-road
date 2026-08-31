#!/usr/bin/env python3
"""Write an A/H AI industry-chain stock table into a Feishu spreadsheet."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import ssl
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
URL_CONTEXT = ssl._create_unverified_context()


QUARTER_PROFIT_YEARS = (2026, 2027, 2028)
QUARTER_PROFIT_HEADERS = [
    f"{year}Q{quarter}净利润(亿元)"
    for year in QUARTER_PROFIT_YEARS
    for quarter in range(1, 5)
]
ACTUAL_VS_FORECAST_PATTERN = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*（上个Q预测\s*([-+]?\d+(?:\.\d+)?)）")


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
] + [
    QUARTER_PROFIT_HEADERS[0],
    "2026Q2锁定预测(亿元)",
    *QUARTER_PROFIT_HEADERS[1:],
    "股息率%",
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
    "AI源头资源": 91,
    "电力类": 92,
    "非Rubin直接链/AI应用": 99,
    "白马成长股": 120,
    "红利低波股": 121,
    "保险白马股": 122,
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
    "AI源头资源": "AI源头资源",
    "电力类": "电力类",
    "模型/应用": "非Rubin直接链/AI应用",
    "端侧AI/机器人": "非Rubin直接链/AI应用",
    "白马成长股": "白马成长股",
    "红利低波股": "红利低波股",
    "保险白马股": "保险白马股",
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
    # AI upstream resources and power operators.
    {"market": "A股", "code": "601899.SH", "segment": "AI源头资源", "theme": "铜/金/锂等资源", "role": "铜金矿资源龙头，AI电力与数据中心铜需求受益"},
    {"market": "A股", "code": "603993.SH", "segment": "AI源头资源", "theme": "铜/钴/钼资源", "role": "铜钴钼资源平台，电气化与算力基建上游"},
    {"market": "A股", "code": "601600.SH", "segment": "AI源头资源", "theme": "铝资源/电解铝", "role": "铝资源与电解铝龙头，电网和数据中心材料上游"},
    {"market": "A股", "code": "600489.SH", "segment": "AI源头资源", "theme": "贵金属/资源", "role": "贵金属资源龙头，资源品配置标的"},
    {"market": "A股", "code": "002460.SZ", "segment": "AI源头资源", "theme": "锂资源", "role": "锂资源与电池材料，储能链上游"},
    {"market": "A股", "code": "002466.SZ", "segment": "AI源头资源", "theme": "锂资源", "role": "锂资源龙头，储能和电力系统上游"},
    {"market": "A股", "code": "600900.SH", "segment": "电力类", "theme": "水电/绿电", "role": "水电运营龙头，AI数据中心长期电力底座"},
    {"market": "A股", "code": "601985.SH", "segment": "电力类", "theme": "核电", "role": "核电运营龙头，稳定基荷电力"},
    {"market": "A股", "code": "003816.SZ", "segment": "电力类", "theme": "核电", "role": "核电运营商，稳定低碳电力供给"},
    {"market": "A股", "code": "600011.SH", "segment": "电力类", "theme": "火电/综合电力", "role": "大型火电与综合能源运营商"},
    {"market": "A股", "code": "600795.SH", "segment": "电力类", "theme": "火电/新能源", "role": "火电与新能源运营，电力需求增长受益"},
    {"market": "A股", "code": "600027.SH", "segment": "电力类", "theme": "火电/综合电力", "role": "大型综合电力运营商"},
    {"market": "A股", "code": "600886.SH", "segment": "电力类", "theme": "水电/火电", "role": "水火电综合运营，稳定电力资产"},
    {"market": "A股", "code": "600905.SH", "segment": "电力类", "theme": "新能源发电", "role": "风光新能源运营，绿电供给侧标的"},
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
    {"market": "A股", "code": "688825.SH", "segment": "HBM/内存/存储", "theme": "HBM/内存/存储", "role": "长鑫科技，HBM与内存存储国产化"},
    {"market": "A股", "code": "603799.SH", "segment": "AI源头资源", "theme": "稀土资源", "role": "华友钴业，钴资源与新能源材料"},
    # Broader A-share white-chip, dividend low-volatility, and insurance watchlist.
    {"market": "A股", "code": "600030.SH", "segment": "白马成长股", "theme": "券商龙头", "role": "头部综合券商，财富管理、投行与资管业务"},
    {"market": "A股", "code": "601211.SH", "segment": "白马成长股", "theme": "券商龙头", "role": "综合券商龙头，资本市场活跃度受益"},
    {"market": "A股", "code": "601688.SH", "segment": "白马成长股", "theme": "券商龙头", "role": "头部券商，机构业务和财富管理能力突出"},
    {"market": "A股", "code": "000776.SZ", "segment": "白马成长股", "theme": "券商龙头", "role": "全国性综合券商"},
    {"market": "A股", "code": "601881.SH", "segment": "白马成长股", "theme": "券商龙头", "role": "大型综合券商"},
    {"market": "A股", "code": "600999.SH", "segment": "白马成长股", "theme": "券商龙头", "role": "招商系综合券商"},
    {"market": "A股", "code": "600519.SH", "segment": "白马成长股", "theme": "高端白酒", "role": "高端白酒龙头，消费核心资产"},
    {"market": "A股", "code": "000858.SZ", "segment": "白马成长股", "theme": "高端白酒", "role": "浓香白酒龙头"},
    {"market": "A股", "code": "000568.SZ", "segment": "白马成长股", "theme": "高端白酒", "role": "高端白酒核心品牌"},
    {"market": "A股", "code": "600809.SH", "segment": "白马成长股", "theme": "次高端白酒", "role": "清香白酒龙头"},
    {"market": "A股", "code": "002304.SZ", "segment": "白马成长股", "theme": "白酒", "role": "全国化白酒品牌"},
    {"market": "A股", "code": "000596.SZ", "segment": "白马成长股", "theme": "白酒", "role": "区域白酒龙头"},
    {"market": "A股", "code": "000333.SZ", "segment": "白马成长股", "theme": "家电制造", "role": "全球家电和工业技术龙头"},
    {"market": "A股", "code": "000651.SZ", "segment": "白马成长股", "theme": "家电制造", "role": "空调与家电龙头"},
    {"market": "A股", "code": "600690.SH", "segment": "白马成长股", "theme": "家电制造", "role": "全球白电与智慧家庭龙头"},
    {"market": "A股", "code": "002050.SZ", "segment": "白马成长股", "theme": "家电零部件", "role": "热管理和制冷零部件龙头"},
    {"market": "A股", "code": "000921.SZ", "segment": "白马成长股", "theme": "家电制造", "role": "白电与黑电制造"},
    {"market": "A股", "code": "002508.SZ", "segment": "白马成长股", "theme": "厨电", "role": "厨电龙头"},
    {"market": "A股", "code": "688808.SH", "segment": "白马成长股", "theme": "机械设备", "role": "测试测量与半导体设备"},
    {"market": "A股", "code": "002594.SZ", "segment": "白马成长股", "theme": "交运设备", "role": "新能源车与智能汽车龙头"},
    {"market": "A股", "code": "000338.SZ", "segment": "白马成长股", "theme": "交运设备", "role": "商用车与动力系统龙头"},
    {"market": "A股", "code": "603259.SH", "segment": "白马成长股", "theme": "医药生物", "role": "创新药研发与商业化"},
    {"market": "A股", "code": "688235.SH", "segment": "白马成长股", "theme": "医药生物", "role": "创新药与全球化商业布局"},
    {"market": "A股", "code": "300760.SZ", "segment": "白马成长股", "theme": "医药生物", "role": "医疗器械龙头"},
    {"market": "A股", "code": "600309.SH", "segment": "白马成长股", "theme": "医药生物", "role": "化工新材料龙头"},
    {"market": "A股", "code": "600036.SH", "segment": "红利低波股", "theme": "银行/红利低波", "role": "零售银行龙头，高分红金融资产"},
    {"market": "A股", "code": "601398.SH", "segment": "红利低波股", "theme": "银行/红利低波", "role": "大型国有银行，高股息资产"},
    {"market": "A股", "code": "601288.SH", "segment": "红利低波股", "theme": "银行/红利低波", "role": "大型国有银行，高股息资产"},
    {"market": "A股", "code": "601939.SH", "segment": "红利低波股", "theme": "银行/红利低波", "role": "大型国有银行，高股息资产"},
    {"market": "A股", "code": "601328.SH", "segment": "红利低波股", "theme": "银行/红利低波", "role": "大型国有银行，高股息资产"},
    {"market": "A股", "code": "601088.SH", "segment": "红利低波股", "theme": "煤炭/红利低波", "role": "煤电一体化能源龙头，高分红资产"},
    {"market": "A股", "code": "601857.SH", "segment": "红利低波股", "theme": "化石能源", "role": "油气龙头，高分红资产"},
    {"market": "A股", "code": "600028.SH", "segment": "红利低波股", "theme": "化石能源", "role": "炼化与石油化工龙头"},
    {"market": "A股", "code": "601919.SH", "segment": "红利低波股", "theme": "交通运输", "role": "航运龙头，现金流与分红属性"},
    {"market": "A股", "code": "601318.SH", "segment": "保险白马股", "theme": "保险/综合金融", "role": "综合金融与保险龙头"},
    {"market": "A股", "code": "601628.SH", "segment": "保险白马股", "theme": "保险/寿险", "role": "寿险龙头"},
    {"market": "A股", "code": "601601.SH", "segment": "保险白马股", "theme": "保险/寿险+财险", "role": "综合保险龙头"},
    {"market": "A股", "code": "601336.SH", "segment": "保险白马股", "theme": "保险/寿险", "role": "寿险公司"},
    {"market": "A股", "code": "601319.SH", "segment": "保险白马股", "theme": "保险/财险", "role": "财险龙头"},
    {"market": "A股", "code": "688981.SH", "segment": "算力芯片", "theme": "晶圆制造", "role": "先进/成熟制程晶圆制造"},
    {"market": "A股", "code": "688385.SH", "segment": "算力芯片", "theme": "芯片设计", "role": "安全芯片、FPGA与存储控制"},
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki-url", default=DEFAULT_WIKI_URL)
    parser.add_argument("--spreadsheet-token", default="")
    parser.add_argument("--sheet-title", default="")
    parser.add_argument("--feishu-env", default=DEFAULT_FEISHU_ENV)
    parser.add_argument("--source-spreadsheet-token", default="")
    parser.add_argument("--source-sheet-id", default="")
    parser.add_argument("--locked-source-spreadsheet-token", default="")
    parser.add_argument("--locked-source-sheet-id", default="")
    parser.add_argument("--drop-hk", action="store_true")
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
    with urllib.request.urlopen(request, timeout=30, context=URL_CONTEXT) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("code") not in (0, None):
        raise RuntimeError(f"API call failed: {payload}")
    return payload


def cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = []
        for part in value:
            if isinstance(part, dict):
                parts.append(str(part.get("text", "")))
            else:
                parts.append(str(part))
        return "".join(parts).strip()
    return str(value).strip()


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
            payload = json.loads(urllib.request.urlopen(request, timeout=30, context=URL_CONTEXT).read().decode("utf-8"))
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
    for column in ("total_mv", "pe_ttm", "dv_ttm"):
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


def default_stock_maps() -> tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, str]]]:
    by_code = {item["code"]: item for item in AI_STOCKS if item.get("code")}
    by_name = {item.get("name", ""): item for item in AI_STOCKS if item.get("name")}
    return by_code, by_name


def manual_code_aliases() -> Dict[str, str]:
    return {
        "海天酱油": "603288.SH",
    }


def load_items_from_source_sheet(token: str, pro, spreadsheet: str, sheet_id: str, drop_hk: bool) -> List[Dict[str, str]]:
    response = request_json(
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet}/values/{sheet_id}!A1:AZ500",
        token=token,
    )
    values = response.get("data", {}).get("valueRange", {}).get("values", [])
    if not values:
        return []
    headers = [cell_text(value) for value in values[0]]
    header_index = {header: index for index, header in enumerate(headers) if header}

    def row_value(row: List[object], title: str) -> str:
        index = header_index.get(title)
        if index is None or len(row) <= index:
            return ""
        return cell_text(row[index])

    a_names, hk_names = latest_name_maps(pro)
    a_code_by_name = {name: code for code, name in a_names.items() if name}
    hk_code_by_name = {name: code for code, name in hk_names.items() if name}
    alias_by_name = manual_code_aliases()
    default_by_code, default_by_name = default_stock_maps()
    source_items: List[Dict[str, str]] = []
    seen_codes = set()
    seen_names = set()
    for row in values[1:]:
        name = row_value(row, "名称")
        if not name:
            continue
        market = row_value(row, "市场")
        if drop_hk and market == "港股":
            continue
        code = row_value(row, "代码")
        if not code:
            code = alias_by_name.get(name, "") or a_code_by_name.get(name, "") or hk_code_by_name.get(name, "")
        if not market:
            if code.endswith((".SH", ".SZ", ".BJ")):
                market = "A股"
            elif code.endswith(".HK"):
                market = "港股"
        if drop_hk and (market == "港股" or code.endswith(".HK")):
            continue
        default_item = default_by_code.get(code) or default_by_name.get(name) or {}
        dedupe_key = code or name
        if dedupe_key in seen_codes or name in seen_names:
            continue
        seen_codes.add(dedupe_key)
        seen_names.add(name)
        segment = row_value(row, "产业链环节") or default_item.get("segment", "")
        theme = row_value(row, "细分方向") or default_item.get("theme", "") or segment
        role = row_value(row, "公司产品简介") or default_item.get("role", "") or theme
        certainty = row_value(row, "供应链确定性") or default_item.get("certainty", "") or ""
        if not certainty and default_item:
            certainty = certainty_for_item(default_item)
        source_items.append(
            {
                "name": name,
                "market": market or "A股",
                "code": code,
                "segment": segment,
                "theme": theme,
                "role": role,
                "certainty": certainty,
                **{
                    f"source_{header}": row_value(row, header)
                    for header in QUARTER_PROFIT_HEADERS
                    if header in header_index
                },
            }
        )
    return source_items


def load_locked_quarter_values(
    token: str, spreadsheet: str, sheet_id: str, quarter_header: str
) -> Dict[str, str]:
    response = request_json(
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet}/values/{sheet_id}!A1:AZ500",
        token=token,
    )
    values = response.get("data", {}).get("valueRange", {}).get("values", [])
    if not values:
        return {}
    headers = [cell_text(value) for value in values[0]]
    header_index = {header: index for index, header in enumerate(headers) if header}
    name_index = header_index.get("名称")
    code_index = header_index.get("代码")
    quarter_index = header_index.get(quarter_header)
    if quarter_index is None:
        return {}
    result: Dict[str, str] = {}
    for row in values[1:]:
        name = cell_text(row[name_index]) if name_index is not None and len(row) > name_index else ""
        code = cell_text(row[code_index]) if code_index is not None and len(row) > code_index else ""
        value = cell_text(row[quarter_index]) if len(row) > quarter_index else ""
        if not value or ACTUAL_VS_FORECAST_PATTERN.search(value):
            continue
        if code:
            result[code] = value
        if name:
            result[name] = value
    return result


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


def fmt_num_value(value: object, digits: int = 1):
    if value == "" or value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(number):
        return ""
    return round(number, digits)


def fmt_pct_fraction(value: Optional[float], digits: int = 4):
    if value is None or pd.isna(value) or not np.isfinite(value):
        return ""
    return round(float(value) / 100.0, digits)


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


def actual_cumulative_profit_yi(income: pd.DataFrame) -> Dict[str, float]:
    if income.empty:
        return {}
    frame = income[pd.notna(income.get("n_income_attr_p"))].copy()
    if frame.empty:
        return {}
    frame["_report_rank"] = np.where(frame.get("report_type").astype(str) == "1", 0, 1)
    frame = frame.sort_values(["end_date", "_report_rank", "f_ann_date", "ann_date"], ascending=[True, True, False, False])
    frame = frame.drop_duplicates("end_date", keep="last")
    result: Dict[str, float] = {}
    for _, row in frame.iterrows():
        end_date = str(row["end_date"])
        if len(end_date) != 8:
            continue
        value = float(row["n_income_attr_p"] / 100000000.0)
        if np.isfinite(value):
            result[end_date] = value
    return result


def actual_quarter_profit_yi(income: pd.DataFrame) -> Dict[str, float]:
    cumulative = actual_cumulative_profit_yi(income)
    result: Dict[str, float] = {}
    quarter_end_month_day = {1: "0331", 2: "0630", 3: "0930", 4: "1231"}
    for year in range(2024, 2029):
        previous = 0.0
        for quarter in range(1, 5):
            end_date = f"{year}{quarter_end_month_day[quarter]}"
            cumulative_value = cumulative.get(end_date)
            if cumulative_value is None:
                continue
            quarter_value = cumulative_value - previous
            result[f"{year}Q{quarter}"] = quarter_value
            previous = cumulative_value
    return result


def forecast_quarter_profit_yi(report: pd.DataFrame) -> Dict[str, float]:
    if report.empty or "quarter" not in report.columns:
        return {}
    frame = report[pd.notna(report.get("np"))].copy()
    if frame.empty:
        return {}
    frame["quarter"] = frame["quarter"].astype(str)
    result: Dict[str, float] = {}
    for quarter, subset in frame.groupby("quarter"):
        if not quarter.endswith(("Q1", "Q2", "Q3", "Q4")):
            continue
        value = float(subset["np"].median() / 10000.0)
        if np.isfinite(value):
            result[quarter] = value
    return result


def latest_actual_announcement_date(income: pd.DataFrame) -> Optional[int]:
    if income.empty or "ann_date" not in income.columns:
        return None
    frame = income[pd.notna(income.get("n_income_attr_p"))].copy()
    if frame.empty:
        return None
    frame["ann_date_num"] = pd.to_numeric(frame["ann_date"], errors="coerce")
    frame = frame[pd.notna(frame["ann_date_num"])]
    if frame.empty:
        return None
    return int(frame["ann_date_num"].max())


def recent_report_frame(report: pd.DataFrame, income: pd.DataFrame) -> pd.DataFrame:
    if report.empty or "report_date" not in report.columns:
        return report
    cutoff = latest_actual_announcement_date(income)
    if cutoff is None:
        return report
    frame = report.copy()
    frame["report_date_num"] = pd.to_numeric(frame["report_date"], errors="coerce")
    recent = frame[frame["report_date_num"] >= cutoff].copy()
    return recent if not recent.empty else report


def prior_report_frame(report: pd.DataFrame, income: pd.DataFrame) -> pd.DataFrame:
    """Return estimates published before the latest actual announcement."""
    if report.empty or "report_date" not in report.columns:
        return report
    cutoff = latest_actual_announcement_date(income)
    if cutoff is None:
        return report
    frame = report.copy()
    frame["report_date_num"] = pd.to_numeric(frame["report_date"], errors="coerce")
    prior = frame[frame["report_date_num"] < cutoff].copy()
    return prior if not prior.empty else report


def quarter_key(year: int, quarter: int) -> str:
    return f"{year}Q{quarter}"


def historical_quarter_weights(actuals: Dict[str, float]) -> Dict[int, float]:
    for year in (2025, 2024):
        values = [actuals.get(quarter_key(year, quarter)) for quarter in range(1, 5)]
        if all(value is not None for value in values):
            total = sum(values)
            if total > 0 and all(value > 0 for value in values):
                weights = {quarter: values[quarter - 1] / total for quarter in range(1, 5)}
                if min(weights.values()) >= 0.05 and max(weights.values()) <= 0.60:
                    return weights
    return {quarter: 0.25 for quarter in range(1, 5)}


def modeled_quarter_profit_yi(actuals: Dict[str, float], forecasts: Dict[str, float], year: int) -> Dict[int, float]:
    weights = historical_quarter_weights(actuals)
    annual_forecast = forecasts.get(quarter_key(year, 4))
    modeled: Dict[int, float] = {}
    cumulative = 0.0

    for quarter in range(1, 4):
        key = quarter_key(year, quarter)
        actual = actuals.get(key)
        if actual is not None:
            modeled[quarter] = actual
            cumulative += actual
            continue
        cumulative_forecast = forecasts.get(key)
        if cumulative_forecast is not None:
            value = cumulative_forecast - cumulative
            modeled[quarter] = value
            cumulative += value

    q4_actual = actuals.get(quarter_key(year, 4))
    if q4_actual is not None:
        modeled[4] = q4_actual
        return modeled

    if annual_forecast is not None:
        remaining_quarters = [quarter for quarter in range(1, 5) if quarter not in modeled]
        remaining_profit = annual_forecast - sum(modeled.values())
        if remaining_quarters and remaining_profit > 0:
            total_weight = sum(weights.get(quarter, 0.0) for quarter in remaining_quarters)
            if total_weight <= 0:
                total_weight = float(len(remaining_quarters))
                weights = {quarter: 1.0 for quarter in remaining_quarters}
            for quarter in remaining_quarters:
                modeled[quarter] = remaining_profit * weights.get(quarter, 0.0) / total_weight

    return modeled


def forecast_for_actual_quarter(actuals: Dict[str, float], forecasts: Dict[str, float], year: int, quarter: int) -> Optional[float]:
    explicit = forecasts.get(quarter_key(year, quarter))
    if explicit is not None:
        if quarter == 1:
            return explicit
        previous_actuals = sum(actuals.get(quarter_key(year, q), 0.0) for q in range(1, quarter))
        return explicit - previous_actuals

    annual_forecast = forecasts.get(quarter_key(year, 4))
    if annual_forecast is None:
        return None
    weights = historical_quarter_weights(actuals)
    return annual_forecast * weights.get(quarter, 0.25)


def quarter_profit_cells(report: pd.DataFrame, income: pd.DataFrame) -> List[str]:
    actuals = actual_quarter_profit_yi(income)
    recent_report = recent_report_frame(report, income)
    prior_report = prior_report_frame(report, income)
    forecasts = forecast_quarter_profit_yi(recent_report)
    prior_forecasts = forecast_quarter_profit_yi(prior_report)
    cells: List[str] = []
    for year in QUARTER_PROFIT_YEARS:
        modeled = modeled_quarter_profit_yi(actuals, forecasts, year)
        for quarter in range(1, 5):
            key = quarter_key(year, quarter)
            actual = actuals.get(key)
            forecast = modeled.get(quarter)
            if actual is not None:
                actual_text = fmt_fixed_decimal(actual)
                prior_forecast = forecast_for_actual_quarter(actuals, prior_forecasts, year, quarter)
                if prior_forecast is not None:
                    cells.append(f"{actual_text}（上个Q预测{fmt_fixed_decimal(prior_forecast)}）")
                else:
                    cells.append(fmt_num(actual))
            elif forecast is not None:
                cells.append(fmt_num(forecast))
            else:
                cells.append("")
    return cells


def preserve_current_year_quarter_forecast(
    cells: List[str], source_item: Dict[str, str], actuals: Dict[str, float]
) -> List[str]:
    """Keep the prior sheet's first unreported current-year quarter estimate."""
    year = date.today().year
    unreported = [
        quarter
        for quarter in range(1, 5)
        if actuals.get(quarter_key(year, quarter)) is None
    ]
    if not unreported:
        return cells
    first_unreported = unreported[0]
    first_year_index = (year - QUARTER_PROFIT_YEARS[0]) * 4
    target_index = first_year_index + first_unreported - 1
    if target_index < 0 or target_index >= len(cells):
        return cells

    old_target = source_item.get(f"source_{year}Q{first_unreported}", "")
    if not old_target or ACTUAL_VS_FORECAST_PATTERN.search(old_target):
        return cells
    try:
        frozen_forecast = float(old_target)
    except (TypeError, ValueError):
        return cells
    if not np.isfinite(frozen_forecast):
        return cells

    result = list(cells)
    result[target_index] = fmt_num(frozen_forecast)

    # Once a quarter reports, compare its actual result with the old estimate.
    for quarter in range(1, first_unreported):
        old_value = source_item.get(f"source_{year}Q{quarter}", "")
        if not old_value:
            continue
        actual_annotation = ACTUAL_VS_FORECAST_PATTERN.search(old_value)
        if actual_annotation:
            old_forecast = float(actual_annotation.group(2))
        else:
            try:
                old_forecast = float(old_value)
            except (TypeError, ValueError):
                continue
        index = first_year_index + quarter - 1
        if index >= len(result) or not isinstance(result[index], str):
            continue
        actual_match = re.match(r"([-+]?\d+(?:\.\d+)?)", result[index])
        if actual_match and np.isfinite(old_forecast):
            result[index] = f"{actual_match.group(1)}（上个Q预测{fmt_fixed_decimal(old_forecast)}）"
    return result


def profit_cells(report: pd.DataFrame, income: pd.DataFrame, market_cap_yi: Optional[float]) -> tuple[Dict[int, str], Dict[int, str], Dict[int, str], str]:
    forecast_report = recent_report_frame(report, income)
    profit_values: Dict[int, Optional[float]] = {}
    pes: Dict[int, str] = {}
    counts = []
    for year in (2024, 2025):
        actual = actual_profit_yi(income, year)
        profit_values[year] = actual

    for year in (2025, 2026, 2027, 2028):
        if year == 2025 and profit_values.get(year) is not None:
            counts.append(f"{year}:实际")
        elif forecast_report.empty or "quarter" not in forecast_report.columns:
            profit_values.setdefault(year, None)
        else:
            subset = forecast_report[forecast_report["quarter"].astype(str) == f"{year}Q4"].copy()
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
    high_related_segments = {"内存", "HBM/内存/存储", "PCB", "MLCC", "ABF载板", "电源", "液冷", "光通信/CPO"}
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


def build_rows(
    pro,
    args: argparse.Namespace,
    items: Optional[List[Dict[str, str]]] = None,
    locked_quarter_values: Optional[Dict[str, str]] = None,
) -> List[List[object]]:
    a_names, hk_names = latest_name_maps(pro)
    rows = []
    seen = set()
    stock_items = items if items is not None else AI_STOCKS
    if items is not None:
        ordered_items = list(enumerate(stock_items))
    else:
        ordered_items = sorted(
            enumerate(stock_items),
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
        dividend_yield = None
        forecast_profits = {2025: "", 2026: "", 2027: "", 2028: ""}
        profit_yoy = {2025: "", 2026: "", 2027: "", 2028: ""}
        forecast_pes = {2025: "", 2026: "", 2027: "", 2028: ""}
        quarter_profit_forecasts = [""] * len(QUARTER_PROFIT_HEADERS)
        forecast_source = ""
        status = "OK"
        note = ""
        if market == "A股":
            basic = fetch_daily_basic(pro, code, args.start_date, args.end_date, args.refresh)
            if not basic.empty:
                latest_basic = basic.iloc[-1]
                market_cap_yi = float(latest_basic["total_mv"] / 10000) if pd.notna(latest_basic.get("total_mv")) else None
                pe_ttm = float(latest_basic["pe_ttm"]) if pd.notna(latest_basic.get("pe_ttm")) else None
                dividend_yield = float(latest_basic["dv_ttm"]) if pd.notna(latest_basic.get("dv_ttm")) else None
            report = fetch_report_rc(pro, code, args.refresh)
            income = fetch_income(pro, code, args.refresh)
            forecast_profits, profit_yoy, forecast_pes, forecast_source = profit_cells(report, income, market_cap_yi)
            quarter_profit_forecasts = quarter_profit_cells(report, income)
            locked_q2_display = ""
            if items is not None:
                locked_q2 = (locked_quarter_values or {}).get(code) or (locked_quarter_values or {}).get(name)
                if locked_q2:
                    try:
                        locked_q2_number = float(locked_q2)
                    except (TypeError, ValueError):
                        locked_q2_number = None
                    actual_q2_value = quarter_profit_forecasts[1] if len(quarter_profit_forecasts) > 1 else ""
                    actual_q2_match = ACTUAL_VS_FORECAST_PATTERN.search(actual_q2_value) if isinstance(actual_q2_value, str) else None
                    if locked_q2_number is not None:
                        if actual_q2_match:
                            locked_q2_display = f"{actual_q2_match.group(1)}（上个Q预测{fmt_fixed_decimal(locked_q2_number)}）"
                            # The normal actual column must not expose a
                            # cumulative/annual report_rc value as a Q2 prior
                            # forecast. The locked column is the audit anchor.
                            quarter_profit_forecasts[1] = fmt_fixed_decimal(float(actual_q2_match.group(1)))
                            if code == "601318.SH":
                                note = "Q3/Q4暂无机构单季预测，按历史季节性模型拆分；全年预测以年度预测列为准"
                        else:
                            locked_q2_display = fmt_num_value(locked_q2_number)
            if not forecast_source:
                status = "缺预测"
                note = "未取到2025-2028 Q4券商预测"
            time.sleep(args.pause)
        else:
            status = "港股缺估值/预测"
            note = "Tushare当前仅填港股行情涨幅；市值、PE、预测待接入港股估值/一致预期源"

        name_map = hk_names if market == "港股" else a_names
        name = item.get("name") or name_map.get(code, "")
        row = [
                name,
                market,
                rubin_segment,
                item["theme"],
                code,
                item["role"],
                item.get("certainty") or certainty_for_item(item),
                fmt_int(market_cap_yi),
                fmt_num(pe_ttm),
                forecast_pes[2026],
                forecast_pes[2027],
                forecast_pes[2028],
                fmt_num_value(forecast_profits[2025]),
                fmt_num_value(forecast_profits[2026]),
                fmt_num_value(forecast_profits[2027]),
                fmt_num_value(forecast_profits[2028]),
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
                quarter_profit_forecasts[0],
                locked_q2_display,
                *quarter_profit_forecasts[1:],
                fmt_pct_fraction(dividend_yield),
            ]
        rows.append(row)
        time.sleep(args.pause)
    if items is None:
        rows.sort(key=lambda row: (SEGMENT_ORDER.get(row[2], 999), -row_market_cap(row), str(row[4])))
    return rows[: args.max_rows]


def write_sheet(token: str, spreadsheet: str, sheet_id: str, values: List[List[object]]) -> None:
    max_rows = 200
    width = len(HEADERS)
    clear_width = max(width, 42)
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
    def header_col(title: str) -> int:
        return HEADERS.index(title) + 1

    market_cap_col = header_col("市值(亿元)")
    locked_quarter_col = header_col("2026Q2锁定预测(亿元)")
    normal_q2_col = header_col("2026Q2净利润(亿元)")
    dividend_col = header_col("股息率%")
    pe_cols = [
        header_col("PE(TTM)"),
        header_col("2026 PE(预测)"),
        header_col("2027 PE(预测)"),
        header_col("2028 PE(预测)"),
    ]
    yoy_cols = [
        header_col("2025净利润同比%(实际/预测)"),
        header_col("2026净利润同比%(预测)"),
        header_col("2027净利润同比%(预测)"),
        header_col("2028净利润同比%(预测)"),
    ]
    styles = [
        {"ranges": [f"{sheet_id}!A2:{column_name(clear_width)}{max_rows}"], "style": {"backColor": "#FFFFFF"}},
        {"ranges": [f"{sheet_id}!{column_name(market_cap_col)}2:{column_name(market_cap_col)}{max_rows}"], "style": {"formatter": "0"}},
        {"ranges": [f"{sheet_id}!{column_name(dividend_col)}2:{column_name(dividend_col)}{max_rows}"], "style": {"formatter": "0.00%"}},
        {"ranges": [f"{sheet_id}!{column_name(yoy_cols[0])}2:{column_name(yoy_cols[-1])}{max_rows}"], "style": {"formatter": "0.00%"}},
        {"ranges": [f"{sheet_id}!G2:{column_name(header_col('2028 PE(预测)'))}{max_rows}"], "style": {"foreColor": "#000000"}},
        {"ranges": [f"{sheet_id}!{column_name(yoy_cols[0])}2:{column_name(yoy_cols[-1])}{max_rows}"], "style": {"foreColor": "#000000"}},
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
    yoy_red_ranges: List[str] = []
    yoy_blue_ranges: List[str] = []
    yoy_high_green_ranges: List[str] = []
    actual_match_blue_ranges: List[str] = []
    actual_beat_green_ranges: List[str] = []
    actual_miss_red_ranges: List[str] = []
    actual_quarter_profit_cols = [
        header_col(QUARTER_PROFIT_HEADERS[0]),  # 2026Q1
        header_col(QUARTER_PROFIT_HEADERS[1]),  # 2026Q2
    ]
    for row_idx, row in enumerate(values, start=2):
        for col_idx in pe_cols:
            value = row[col_idx - 1] if len(row) >= col_idx else ""
            if isinstance(value, (int, float)) and value < 30:
                green_ranges.append(f"{sheet_id}!{column_name(col_idx)}{row_idx}:{column_name(col_idx)}{row_idx}")
        last_pe_col = header_col("2028 PE(预测)")
        last_pe_value = row[last_pe_col - 1] if len(row) >= last_pe_col else ""
        if isinstance(last_pe_value, (int, float)) and last_pe_value > 50:
            red_ranges.append(f"{sheet_id}!{column_name(last_pe_col)}{row_idx}:{column_name(last_pe_col)}{row_idx}")
        for col_idx in yoy_cols:
            value = row[col_idx - 1] if len(row) >= col_idx else ""
            if not isinstance(value, (int, float)):
                continue
            cell_range = f"{sheet_id}!{column_name(col_idx)}{row_idx}:{column_name(col_idx)}{row_idx}"
            if value <= 0:
                yoy_red_ranges.append(cell_range)
            elif 0.10 <= value < 0.30:
                yoy_blue_ranges.append(cell_range)
            elif value >= 0.30:
                yoy_high_green_ranges.append(cell_range)
        for actual_quarter_profit_col in actual_quarter_profit_cols:
            actual_forecast_value = (
                row[actual_quarter_profit_col - 1]
                if len(row) >= actual_quarter_profit_col
                else ""
            )
            if not isinstance(actual_forecast_value, str):
                continue
            match = ACTUAL_VS_FORECAST_PATTERN.search(actual_forecast_value)
            if not match:
                continue
            actual = float(match.group(1))
            forecast = float(match.group(2))
            cell_range = (
                f"{sheet_id}!{column_name(actual_quarter_profit_col)}{row_idx}:"
                f"{column_name(actual_quarter_profit_col)}{row_idx}"
            )
            if forecast == 0:
                if abs(actual) <= 0.05:
                    actual_match_blue_ranges.append(cell_range)
                elif actual > 0:
                    actual_beat_green_ranges.append(cell_range)
                else:
                    actual_miss_red_ranges.append(cell_range)
            else:
                delta_ratio = (actual - forecast) / abs(forecast)
                if abs(delta_ratio) <= 0.10:
                    actual_match_blue_ranges.append(cell_range)
                elif delta_ratio > 0.10:
                    actual_beat_green_ranges.append(cell_range)
                else:
                    actual_miss_red_ranges.append(cell_range)
        locked_value = row[locked_quarter_col - 1] if len(row) >= locked_quarter_col else ""
        if isinstance(locked_value, str):
            locked_match = ACTUAL_VS_FORECAST_PATTERN.search(locked_value)
            if locked_match:
                actual = float(locked_match.group(1))
                forecast = float(locked_match.group(2))
                lock_range = (
                    f"{sheet_id}!{column_name(locked_quarter_col)}{row_idx}:"
                    f"{column_name(locked_quarter_col)}{row_idx}"
                )
                if forecast == 0 or abs((actual - forecast) / abs(forecast)) <= 0.10:
                    actual_match_blue_ranges.append(lock_range)
                elif actual > forecast:
                    actual_beat_green_ranges.append(lock_range)
                else:
                    actual_miss_red_ranges.append(lock_range)
    if green_ranges:
        styles.append({"ranges": green_ranges, "style": {"foreColor": "#00B050"}})
    if red_ranges:
        styles.append({"ranges": red_ranges, "style": {"foreColor": "#C00000"}})
    if yoy_red_ranges:
        styles.append({"ranges": yoy_red_ranges, "style": {"foreColor": "#C00000"}})
    if yoy_blue_ranges:
        styles.append({"ranges": yoy_blue_ranges, "style": {"foreColor": "#0070C0"}})
    if yoy_high_green_ranges:
        styles.append({"ranges": yoy_high_green_ranges, "style": {"foreColor": "#00B050"}})
    if actual_match_blue_ranges:
        styles.append({"ranges": actual_match_blue_ranges, "style": {"backColor": "#D9EAF7"}})
    if actual_beat_green_ranges:
        styles.append({"ranges": actual_beat_green_ranges, "style": {"backColor": "#D9EAD3"}})
    if actual_miss_red_ranges:
        styles.append({"ranges": actual_miss_red_ranges, "style": {"backColor": "#F4CCCC"}})

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
    feishu_token = get_feishu_token(args.feishu_env)
    source_items = None
    if args.source_spreadsheet_token and args.source_sheet_id:
        source_items = load_items_from_source_sheet(
            feishu_token,
            pro,
            args.source_spreadsheet_token,
            args.source_sheet_id,
            args.drop_hk,
        )
        print(f"Loaded {len(source_items)} source items from Feishu sheet {args.source_sheet_id}")
    locked_quarter_values = None
    if args.locked_source_spreadsheet_token and args.locked_source_sheet_id:
        locked_quarter_values = load_locked_quarter_values(
            feishu_token,
            args.locked_source_spreadsheet_token,
            args.locked_source_sheet_id,
            "2026Q2净利润(亿元)",
        )
        print(f"Loaded {len(locked_quarter_values)} locked Q2 forecasts")
    rows = build_rows(pro, args, source_items, locked_quarter_values)
    export_path = EXPORT_DIR / "ai_chain_stocks.csv"
    pd.DataFrame(rows, columns=HEADERS).to_csv(export_path, index=False)
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
