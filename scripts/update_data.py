#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股可转债发行生命周期监控

数据逻辑：
1) 巨潮公告：用公告标题识别流程节点（预案、股东大会、受理、问询、注册、发行公告、发行结果、上市等）。
2) AKShare/巨潮可转债发行表：补充股权登记日、优先申购日、网上申购日、转股起止日等关键字段。
3) 东方财富可转债基础表：作为已发行/已上市基础数据补充。

输出：
- docs/data/convertibles.json
- docs/data/alerts.json
- docs/data/summary.json
- docs/data/convertibles.csv
"""

from __future__ import annotations

import csv
import datetime as dt
import html
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
DATA_DIR = DOCS / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "1200"))
CNINFO_MAX_PAGES = int(os.getenv("CNINFO_MAX_PAGES", "25"))
CNINFO_PAGE_SIZE = int(os.getenv("CNINFO_PAGE_SIZE", "30"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))

# A股可转债主要公告关键词。两个关键词都抓，避免标题只写“可转债”而没有全称。
ANNOUNCEMENT_KEYWORDS = ["可转换公司债券", "可转债"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.cninfo.com.cn/",
}

STAGE_SCORE = {
    "董事会预案": 10,
    "股东大会通过": 20,
    "交易所受理": 30,
    "已问询": 40,
    "已回复问询": 45,
    "审核通过": 50,
    "已注册等待发行": 60,
    "已确定发行": 70,
    "股权登记临近": 75,
    "今日股权登记": 78,
    "今日申购/配债": 80,
    "发行成功待上市": 90,
    "已上市": 100,
    "转股期": 110,
    "摘牌结束": 120,
}

# 从公告标题识别节点。顺序很重要：更具体的放前面。
TITLE_RULES: List[Tuple[str, str, List[str]]] = [
    ("摘牌结束", "摘牌", ["摘牌", "终止上市"]),
    ("发行成功待上市", "发行结果", ["发行结果公告", "发行结果"]),
    ("已上市", "上市", ["上市公告书", "上市公告", "上市交易公告"]),
    ("已确定发行", "发行公告", ["发行公告", "募集说明书", "募集说明书摘要", "网上路演公告", "发行提示性公告"]),
    ("已注册等待发行", "注册", ["同意注册", "注册批复", "获得中国证监会同意注册", "获得证监会同意注册"]),
    ("审核通过", "审核通过", ["上市委会议通过", "审核通过", "审核结果公告", "提交注册"]),
    ("已回复问询", "回复问询", ["审核问询函回复", "问询函回复", "回复审核问询函", "落实函回复"]),
    ("已问询", "问询", ["审核问询函", "问询函", "落实函"]),
    ("交易所受理", "受理", ["获得受理", "申请获受理", "受理通知"]),
    ("股东大会通过", "股东大会", ["股东大会决议", "股东大会通过"]),
    ("董事会预案", "董事会预案", ["预案", "董事会", "发行方案论证分析报告"]),
]

LIFECYCLE_RULES: List[Tuple[str, List[str]]] = [
    ("强赎/赎回", ["强制赎回", "提前赎回", "赎回实施", "不提前赎回", "不赎回"]),
    ("下修/不下修", ["向下修正", "下修", "不向下修正", "不下修", "修正转股价格"]),
    ("回售", ["回售", "附加回售"]),
    ("付息", ["付息公告", "付息"]),
    ("转股价格调整", ["转股价格调整", "转股价格修正", "转股价调整"]),
]


def today_cn() -> dt.date:
    # GitHub Actions 默认 UTC，这里按 A 股业务口径用北京时间/东八区。
    return dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc).astimezone(
        dt.timezone(dt.timedelta(hours=8))
    ).date()


def now_cn_iso() -> str:
    return dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc).astimezone(
        dt.timezone(dt.timedelta(hours=8))
    ).isoformat(timespec="seconds")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    s = html.unescape(str(value))
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("\u3000", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def parse_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    s = str(value).strip()
    if not s or s.lower() in {"none", "nan", "nat", "--", "-"}:
        return None
    # 时间戳毫秒
    if re.fullmatch(r"\d{13}", s):
        try:
            return dt.datetime.fromtimestamp(int(s) / 1000).date().isoformat()
        except Exception:
            pass
    s = s.replace("年", "-").replace("月", "-").replace("日", "")
    # AKShare/Eastmoney 常见格式：2026-01-01 00:00:00
    m = re.search(r"(20\d{2}|19\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
        try:
            return dt.date(int(y), int(mo), int(d)).isoformat()
        except Exception:
            return None
    return None


def date_obj(value: Any) -> Optional[dt.date]:
    s = parse_date(value)
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s)
    except Exception:
        return None


def days_until(value: Any, base: Optional[dt.date] = None) -> Optional[int]:
    d = date_obj(value)
    if not d:
        return None
    base = base or today_cn()
    return (d - base).days


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            if str(value) == "nan":
                return None
            return float(value)
        except Exception:
            return None
    s = str(value).strip().replace(",", "")
    if not s or s in {"-", "--", "None", "nan", "NaN"}:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def request_json(method: str, url: str, *, params=None, data=None, headers=None, retries: int = 3) -> Optional[dict]:
    last_err = None
    for i in range(retries):
        try:
            resp = requests.request(
                method,
                url,
                params=params,
                data=data,
                headers=headers or HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_err = exc
            time.sleep(1.2 * (i + 1))
    print(f"[warn] request failed: {url} -> {last_err}", file=sys.stderr)
    return None


def classify_title(title: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    t = clean_text(title)
    # 排除纯交易公告/普通债券，尽量只保留可转债相关。
    if "可转债" not in t and "可转换公司债券" not in t:
        return None, None, None
    for stage, tag, keys in TITLE_RULES:
        if any(k in t for k in keys):
            return stage, tag, None
    for event, keys in LIFECYCLE_RULES:
        if any(k in t for k in keys):
            return None, None, event
    return None, "其他", None


def fetch_cninfo_announcements(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    out: Dict[str, Dict[str, Any]] = {}

    for keyword in ANNOUNCEMENT_KEYWORDS:
        for page in range(1, CNINFO_MAX_PAGES + 1):
            payload = {
                "pageNum": page,
                "pageSize": CNINFO_PAGE_SIZE,
                "column": "szse,sse",
                "tabName": "fulltext",
                "plate": "",
                "stock": "",
                "searchkey": keyword,
                "secid": "",
                "category": "",
                "trade": "",
                "seDate": f"{start_date}~{end_date}",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            }
            js = request_json("POST", url, data=payload)
            if not js:
                break
            anns = js.get("announcements") or []
            if not anns:
                break
            for a in anns:
                title = clean_text(a.get("announcementTitle"))
                stage, tag, lifecycle_event = classify_title(title)
                if not stage and not lifecycle_event and tag != "其他":
                    continue
                ann_id = str(a.get("announcementId") or a.get("adjunctUrl") or title)
                adjunct_url = a.get("adjunctUrl") or ""
                full_url = f"https://static.cninfo.com.cn/{adjunct_url}" if adjunct_url else ""
                sec_code = str(a.get("secCode") or "").zfill(6) if a.get("secCode") else ""
                item = {
                    "id": ann_id,
                    "stock_code": sec_code,
                    "stock_name": clean_text(a.get("secName")),
                    "title": title,
                    "date": parse_date(a.get("announcementTime")),
                    "url": full_url,
                    "stage": stage,
                    "tag": tag,
                    "lifecycle_event": lifecycle_event,
                    "source": "巨潮公告",
                }
                out[ann_id] = item
            # 如果本页不足一页，后面通常没有更多。
            if len(anns) < CNINFO_PAGE_SIZE:
                break
            time.sleep(0.2)
    return sorted(out.values(), key=lambda x: (x.get("date") or "", x.get("id") or ""), reverse=True)


def fetch_akshare_issue(start_date: str, end_date: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        import akshare as ak  # type: ignore
    except Exception as exc:
        return [], f"AKShare 未安装或导入失败：{exc}"

    try:
        df = ak.bond_cov_issue_cninfo(start_date=start_date.replace("-", ""), end_date=end_date.replace("-", ""))
        if df is None or len(df) == 0:
            return [], None
        records = []
        for _, row in df.iterrows():
            r = row.to_dict()
            records.append(
                {
                    "bond_code": clean_text(r.get("债券代码")),
                    "bond_name": clean_text(r.get("债券简称")),
                    "announce_date": parse_date(r.get("公告日期")),
                    "issue_start_date": parse_date(r.get("发行起始日")),
                    "issue_end_date": parse_date(r.get("发行终止日")),
                    "planned_issue_amount_million": to_float(r.get("计划发行总量")),
                    "actual_issue_amount_million": to_float(r.get("实际发行总量")),
                    "issue_price": to_float(r.get("发行价格")),
                    "initial_convert_price": to_float(r.get("初始转股价格")),
                    "convert_start_date": parse_date(r.get("转股开始日期")),
                    "convert_end_date": parse_date(r.get("转股终止日期")),
                    "online_subscribe_date": parse_date(r.get("网上申购日期")),
                    "online_subscribe_code": clean_text(r.get("网上申购代码")),
                    "online_subscribe_name": clean_text(r.get("网上申购简称")),
                    "winning_result_date": parse_date(r.get("网上申购中签结果公告日及退款日")),
                    "priority_subscribe_date": parse_date(r.get("优先申购日")),
                    "allotment_price": to_float(r.get("配售价格")),
                    "record_date": parse_date(r.get("债权登记日")),
                    "priority_payment_date": parse_date(r.get("优先申购缴款日")),
                    "convert_code": clean_text(r.get("转股代码")),
                    "market": clean_text(r.get("交易市场")),
                    "bond_full_name": clean_text(r.get("债券名称")),
                    "raw_source": "AKShare-巨潮可转债发行",
                }
            )
        return records, None
    except Exception as exc:
        return [], f"AKShare bond_cov_issue_cninfo 获取失败：{exc}"


def fetch_akshare_spot() -> Tuple[Dict[str, Dict[str, Any]], Optional[str]]:
    try:
        import akshare as ak  # type: ignore
    except Exception as exc:
        return {}, f"AKShare 未安装或导入失败：{exc}"

    try:
        df = ak.bond_zh_hs_cov_spot()
        if df is None or len(df) == 0:
            return {}, None
        result: Dict[str, Dict[str, Any]] = {}
        for _, row in df.iterrows():
            r = {clean_text(k): v for k, v in row.to_dict().items()}
            code = clean_text(r.get("代码") or r.get("bond_id") or r.get("symbol") or r.get("债券代码"))
            if not code:
                continue
            name = clean_text(r.get("名称") or r.get("bond_nm") or r.get("债券简称") or r.get("债券名称"))
            price = to_float(r.get("最新价") or r.get("trade") or r.get("price") or r.get("收盘价"))
            change_pct = to_float(r.get("涨跌幅") or r.get("changepercent") or r.get("涨幅"))
            result[code] = {
                "bond_code": code,
                "bond_name": name,
                "price": price,
                "change_pct": change_pct,
                "raw_source": "AKShare-可转债实时行情",
            }
        return result, None
    except Exception as exc:
        return {}, f"AKShare bond_zh_hs_cov_spot 获取失败：{exc}"


def fetch_eastmoney_cb_base() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    records: List[Dict[str, Any]] = []
    try:
        for page in range(1, 12):
            params = {
                "sortColumns": "PUBLIC_START_DATE",
                "sortTypes": "-1",
                "pageSize": "500",
                "pageNumber": str(page),
                "reportName": "RPT_BOND_CB_LIST",
                "columns": "ALL",
                "source": "WEB",
                "client": "WEB",
            }
            js = request_json("GET", url, params=params, headers=HEADERS)
            if not js or not js.get("result"):
                break
            data = js["result"].get("data") or []
            if not data:
                break
            for r in data:
                records.append(
                    {
                        "bond_code": clean_text(r.get("SECURITY_CODE")),
                        "bond_name": clean_text(r.get("SECURITY_NAME_ABBR")),
                        "stock_code": clean_text(r.get("CONVERT_STOCK_CODE")),
                        "stock_name": clean_text(r.get("SECURITY_SHORT_NAME")),
                        "rating": clean_text(r.get("RATING")),
                        "online_subscribe_date": parse_date(r.get("PUBLIC_START_DATE")),
                        "issue_amount_billion": to_float(r.get("ACTUAL_ISSUE_SCALE")),
                        "winning_rate": to_float(r.get("ONLINE_GENERAL_LWR")),
                        "listing_date": parse_date(r.get("LISTING_DATE")),
                        "expire_date": parse_date(r.get("EXPIRE_DATE")),
                        "bond_expire_year": to_float(r.get("BOND_EXPIRE")),
                        "interest_rate_explain": clean_text(r.get("INTEREST_RATE_EXPLAIN")),
                        "raw_source": "东方财富可转债基础表",
                    }
                )
            if len(data) < 500:
                break
        return records, None
    except Exception as exc:
        return records, f"东方财富可转债基础表获取失败：{exc}"


def best_ann_stage(anns: List[Dict[str, Any]]) -> Tuple[str, int]:
    best = ("", -1)
    for a in anns:
        stage = a.get("stage")
        score = STAGE_SCORE.get(stage or "", -1)
        if score > best[1]:
            best = (stage, score)
    return best if best[0] else ("未知", 0)


def latest_stage_date(anns: List[Dict[str, Any]], stage: str) -> Optional[str]:
    dates = [a.get("date") for a in anns if a.get("stage") == stage and a.get("date")]
    return max(dates) if dates else None


def latest_lifecycle_event(anns: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    candidates = [a for a in anns if a.get("lifecycle_event")]
    if not candidates:
        return None
    return sorted(candidates, key=lambda x: x.get("date") or "", reverse=True)[0]


def next_action_for(item: Dict[str, Any], today: dt.date) -> str:
    stage = item.get("stage") or ""
    dr = item.get("days_to_record")
    ds = item.get("days_to_subscribe")
    if stage == "今日股权登记":
        return "今天收盘前持有正股，才有本次原股东配债资格。"
    if stage == "今日申购/配债":
        return "今天操作原股东配售/网上申购，并确认账户资金。"
    if isinstance(dr, int) and dr > 0:
        return f"距离股权登记日还有 {dr} 天，重点核对正股风险和配债资金。"
    if isinstance(ds, int) and ds > 0:
        return f"距离网上申购/优先配售日还有 {ds} 天。"
    if stage == "已注册等待发行":
        return "已具备发行资格，等待发行公告；发行公告一出才确定股权登记日。"
    if stage == "已确定发行":
        return "发行安排已明确，核对股权登记日、优先配售日和缴款日。"
    if stage == "发行成功待上市":
        return "发行已完成，等待上市公告或上市交易。"
    if stage == "已上市":
        return "已上市交易，后续关注转股期、下修、强赎、回售。"
    if stage in {"董事会预案", "股东大会通过", "交易所受理", "已问询", "已回复问询", "审核通过"}:
        return "仍处于前期流程，先放入储备池跟踪。"
    return "继续观察公告变化。"


def risk_level_for(item: Dict[str, Any]) -> str:
    stage = item.get("stage") or ""
    dr = item.get("days_to_record")
    ds = item.get("days_to_subscribe")
    if stage in {"今日股权登记", "今日申购/配债"}:
        return "最高"
    if isinstance(dr, int) and 0 <= dr <= 3:
        return "高"
    if isinstance(ds, int) and 0 <= ds <= 3:
        return "高"
    if stage == "已确定发行":
        return "高"
    if stage == "已注册等待发行":
        return "中高"
    if stage in {"审核通过", "已回复问询", "已问询"}:
        return "中"
    if stage in {"已上市", "转股期", "摘牌结束"}:
        return "低"
    return "低"


def choose_key(stock_code: str, stock_name: str, bond_code: str = "") -> str:
    if stock_code:
        return f"stock:{stock_code}"
    if stock_name:
        return f"name:{stock_name}"
    if bond_code:
        return f"bond:{bond_code}"
    return f"unknown:{time.time_ns()}"


def merge_data(announcements: List[Dict[str, Any]], issue_rows: List[Dict[str, Any]], em_rows: List[Dict[str, Any]], spot: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    tdy = today_cn()
    projects: Dict[str, Dict[str, Any]] = {}

    anns_by_key: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for a in announcements:
        key = choose_key(a.get("stock_code", ""), a.get("stock_name", ""))
        anns_by_key[key].append(a)

    # 先用公告建项目。
    for key, anns in anns_by_key.items():
        latest = sorted(anns, key=lambda x: x.get("date") or "", reverse=True)[0]
        stage, score = best_ann_stage(anns)
        projects[key] = {
            "stock_code": latest.get("stock_code", ""),
            "stock_name": latest.get("stock_name", ""),
            "bond_code": "",
            "bond_name": "",
            "stage": stage,
            "stage_score": score,
            "announcements": sorted(anns, key=lambda x: x.get("date") or "", reverse=True)[:8],
            "latest_title": latest.get("title", ""),
            "latest_url": latest.get("url", ""),
            "latest_announcement_date": latest.get("date"),
            "board_date": latest_stage_date(anns, "董事会预案"),
            "shareholder_date": latest_stage_date(anns, "股东大会通过"),
            "accepted_date": latest_stage_date(anns, "交易所受理"),
            "inquiry_date": latest_stage_date(anns, "已问询"),
            "reply_date": latest_stage_date(anns, "已回复问询"),
            "approval_date": latest_stage_date(anns, "审核通过"),
            "registered_date": latest_stage_date(anns, "已注册等待发行"),
            "issue_announce_date": latest_stage_date(anns, "已确定发行"),
            "issue_result_date": latest_stage_date(anns, "发行成功待上市"),
            "listing_announce_date": latest_stage_date(anns, "已上市"),
            "lifecycle_event": latest_lifecycle_event(anns),
        }

    # 用 AKShare/巨潮发行表补充发行关键字段。
    for r in issue_rows:
        stock_code = clean_text(r.get("convert_code"))
        bond_code = clean_text(r.get("bond_code"))
        bond_name = clean_text(r.get("bond_name"))
        key = choose_key(stock_code, "", bond_code)
        if key not in projects:
            projects[key] = {
                "stock_code": stock_code,
                "stock_name": "",
                "stage": "已确定发行",
                "stage_score": STAGE_SCORE["已确定发行"],
                "announcements": [],
            }
        p = projects[key]
        p.update(
            {
                "bond_code": bond_code or p.get("bond_code", ""),
                "bond_name": bond_name or p.get("bond_name", ""),
                "market": r.get("market") or p.get("market"),
                "bond_full_name": r.get("bond_full_name") or p.get("bond_full_name"),
                "issue_start_date": r.get("issue_start_date") or p.get("issue_start_date"),
                "issue_end_date": r.get("issue_end_date") or p.get("issue_end_date"),
                "planned_issue_amount_million": r.get("planned_issue_amount_million") or p.get("planned_issue_amount_million"),
                "actual_issue_amount_million": r.get("actual_issue_amount_million") or p.get("actual_issue_amount_million"),
                "issue_price": r.get("issue_price") or p.get("issue_price"),
                "initial_convert_price": r.get("initial_convert_price") or p.get("initial_convert_price"),
                "convert_start_date": r.get("convert_start_date") or p.get("convert_start_date"),
                "convert_end_date": r.get("convert_end_date") or p.get("convert_end_date"),
                "online_subscribe_date": r.get("online_subscribe_date") or p.get("online_subscribe_date"),
                "online_subscribe_code": r.get("online_subscribe_code") or p.get("online_subscribe_code"),
                "online_subscribe_name": r.get("online_subscribe_name") or p.get("online_subscribe_name"),
                "winning_result_date": r.get("winning_result_date") or p.get("winning_result_date"),
                "priority_subscribe_date": r.get("priority_subscribe_date") or p.get("priority_subscribe_date"),
                "allotment_price": r.get("allotment_price") or p.get("allotment_price"),
                "record_date": r.get("record_date") or p.get("record_date"),
                "priority_payment_date": r.get("priority_payment_date") or p.get("priority_payment_date"),
            }
        )
        # 有发行表就说明至少已确定发行；如果有实际发行总量，说明已发行。
        if STAGE_SCORE.get(p.get("stage", ""), 0) < STAGE_SCORE["已确定发行"]:
            p["stage"] = "已确定发行"
            p["stage_score"] = STAGE_SCORE["已确定发行"]
        if r.get("actual_issue_amount_million") and STAGE_SCORE.get(p.get("stage", ""), 0) < STAGE_SCORE["发行成功待上市"]:
            p["stage"] = "发行成功待上市"
            p["stage_score"] = STAGE_SCORE["发行成功待上市"]

    # 东方财富基础表补充上市日期、评级、正股名称等。
    for r in em_rows:
        stock_code = clean_text(r.get("stock_code"))
        key = choose_key(stock_code, clean_text(r.get("stock_name")), clean_text(r.get("bond_code")))
        if key not in projects:
            # 只保留近几年或尚未上市/刚上市的数据，避免历史数据塞满页面。
            sub_date = date_obj(r.get("online_subscribe_date"))
            listing = date_obj(r.get("listing_date"))
            if not sub_date or (tdy - sub_date).days > LOOKBACK_DAYS:
                continue
            projects[key] = {
                "stock_code": stock_code,
                "stock_name": clean_text(r.get("stock_name")),
                "stage": "已上市" if listing and listing <= tdy else "发行成功待上市",
                "stage_score": STAGE_SCORE["已上市"] if listing and listing <= tdy else STAGE_SCORE["发行成功待上市"],
                "announcements": [],
            }
        p = projects[key]
        p.update(
            {
                "stock_code": p.get("stock_code") or stock_code,
                "stock_name": p.get("stock_name") or clean_text(r.get("stock_name")),
                "bond_code": p.get("bond_code") or clean_text(r.get("bond_code")),
                "bond_name": p.get("bond_name") or clean_text(r.get("bond_name")),
                "rating": r.get("rating") or p.get("rating"),
                "online_subscribe_date": p.get("online_subscribe_date") or r.get("online_subscribe_date"),
                "issue_amount_billion": r.get("issue_amount_billion") or p.get("issue_amount_billion"),
                "winning_rate": r.get("winning_rate") or p.get("winning_rate"),
                "listing_date": r.get("listing_date") or p.get("listing_date"),
                "expire_date": r.get("expire_date") or p.get("expire_date"),
                "bond_expire_year": r.get("bond_expire_year") or p.get("bond_expire_year"),
                "interest_rate_explain": r.get("interest_rate_explain") or p.get("interest_rate_explain"),
            }
        )
        listing = date_obj(p.get("listing_date"))
        if listing and listing <= tdy and STAGE_SCORE.get(p.get("stage", ""), 0) < STAGE_SCORE["已上市"]:
            p["stage"] = "已上市"
            p["stage_score"] = STAGE_SCORE["已上市"]
        elif r.get("issue_amount_billion") and STAGE_SCORE.get(p.get("stage", ""), 0) < STAGE_SCORE["发行成功待上市"]:
            p["stage"] = "发行成功待上市"
            p["stage_score"] = STAGE_SCORE["发行成功待上市"]

    # 按日期动态覆盖核心提醒状态。
    for p in projects.values():
        bond_code = clean_text(p.get("bond_code"))
        if bond_code and bond_code in spot:
            p["spot"] = spot[bond_code]

        dr = days_until(p.get("record_date"), tdy)
        ds = days_until(p.get("online_subscribe_date") or p.get("priority_subscribe_date"), tdy)
        p["days_to_record"] = dr
        p["days_to_subscribe"] = ds

        # 转股期：上市后且到了转股开始日期。
        conv_start = date_obj(p.get("convert_start_date"))
        conv_end = date_obj(p.get("convert_end_date"))
        if conv_start and conv_start <= tdy and (not conv_end or conv_end >= tdy):
            if STAGE_SCORE.get(p.get("stage", ""), 0) < STAGE_SCORE["转股期"]:
                p["stage"] = "转股期"
                p["stage_score"] = STAGE_SCORE["转股期"]

        # 抢权/申购日优先展示，不能被“已上市”等历史项目误触发。
        if dr == 0:
            p["stage"] = "今日股权登记"
            p["stage_score"] = STAGE_SCORE["今日股权登记"]
        elif isinstance(dr, int) and 0 < dr <= 5:
            if STAGE_SCORE.get(p.get("stage", ""), 0) < STAGE_SCORE["股权登记临近"]:
                p["stage"] = "股权登记临近"
                p["stage_score"] = STAGE_SCORE["股权登记临近"]
        if ds == 0:
            p["stage"] = "今日申购/配债"
            p["stage_score"] = STAGE_SCORE["今日申购/配债"]

        p["risk_level"] = risk_level_for(p)
        p["next_action"] = next_action_for(p, tdy)
        # 发行规模单位补全：AKShare 为万元，东方财富为亿元。
        if p.get("actual_issue_amount_million") is not None:
            p["actual_issue_amount_billion"] = round(float(p["actual_issue_amount_million"]) / 10000, 4)
        if p.get("planned_issue_amount_million") is not None:
            p["planned_issue_amount_billion"] = round(float(p["planned_issue_amount_million"]) / 10000, 4)

    items = list(projects.values())
    # 排序：今天/临近优先，其次发行阶段优先，其次最新公告日期。
    def sort_key(x: Dict[str, Any]) -> Tuple[int, int, str]:
        risk_rank = {"最高": 5, "高": 4, "中高": 3, "中": 2, "低": 1}.get(x.get("risk_level"), 0)
        return (risk_rank, int(x.get("stage_score") or 0), x.get("latest_announcement_date") or x.get("online_subscribe_date") or "")

    items.sort(key=sort_key, reverse=True)
    return items


def build_summary(items: List[Dict[str, Any]], source_warnings: List[str]) -> Dict[str, Any]:
    stages = defaultdict(int)
    risks = defaultdict(int)
    for x in items:
        stages[x.get("stage") or "未知"] += 1
        risks[x.get("risk_level") or "未知"] += 1

    core = [x for x in items if x.get("stage") in {"今日股权登记", "今日申购/配债", "股权登记临近", "已确定发行", "已注册等待发行"}]
    alerts = [x for x in items if x.get("risk_level") in {"最高", "高", "中高"}]

    return {
        "generated_at": now_cn_iso(),
        "today": today_cn().isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "total": len(items),
        "core_count": len(core),
        "alert_count": len(alerts),
        "stages": dict(sorted(stages.items(), key=lambda kv: STAGE_SCORE.get(kv[0], 0), reverse=True)),
        "risks": dict(risks),
        "source_warnings": source_warnings,
        "source_note": "公告节点来自巨潮资讯公告标题识别；发行、申购、股权登记、转股日期来自 AKShare/巨潮可转债发行表及东方财富可转债基础表。",
    }


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, items: List[Dict[str, Any]]) -> None:
    fields = [
        "stage",
        "risk_level",
        "stock_code",
        "stock_name",
        "bond_code",
        "bond_name",
        "issue_amount_billion",
        "planned_issue_amount_billion",
        "actual_issue_amount_billion",
        "record_date",
        "priority_subscribe_date",
        "online_subscribe_date",
        "priority_payment_date",
        "winning_result_date",
        "listing_date",
        "convert_start_date",
        "convert_end_date",
        "registered_date",
        "issue_announce_date",
        "latest_announcement_date",
        "latest_title",
        "latest_url",
        "next_action",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for item in items:
            writer.writerow({k: item.get(k, "") for k in fields})


def main() -> int:
    end = today_cn()
    start = end - dt.timedelta(days=LOOKBACK_DAYS)
    start_s = start.isoformat()
    end_s = end.isoformat()

    warnings: List[str] = []
    print(f"[info] fetch cninfo announcements {start_s}~{end_s}")
    anns = fetch_cninfo_announcements(start_s, end_s)
    print(f"[info] announcements: {len(anns)}")

    print("[info] fetch akshare/cninfo issue table")
    issue_rows, warn = fetch_akshare_issue(start_s, end_s)
    if warn:
        warnings.append(warn)
    print(f"[info] issue rows: {len(issue_rows)}")

    print("[info] fetch eastmoney cb base")
    em_rows, warn = fetch_eastmoney_cb_base()
    if warn:
        warnings.append(warn)
    print(f"[info] eastmoney rows: {len(em_rows)}")

    print("[info] fetch spot optional")
    spot, warn = fetch_akshare_spot()
    if warn:
        warnings.append(warn)
    print(f"[info] spot rows: {len(spot)}")

    items = merge_data(anns, issue_rows, em_rows, spot)
    summary = build_summary(items, warnings)
    alerts = [x for x in items if x.get("risk_level") in {"最高", "高", "中高"}]

    payload = {
        **summary,
        "items": items,
    }

    write_json(DATA_DIR / "convertibles.json", payload)
    write_json(DATA_DIR / "alerts.json", {**summary, "items": alerts})
    write_json(DATA_DIR / "summary.json", summary)
    write_csv(DATA_DIR / "convertibles.csv", items)

    print(f"[ok] items={len(items)} alerts={len(alerts)} warnings={len(warnings)}")
    for w in warnings:
        print(f"[warn] {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
