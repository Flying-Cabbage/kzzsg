from __future__ import annotations

import csv
import json
import math
import os
import random
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd
import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
PUBLIC_DIR = ROOT_DIR / "public"
DATA_DIR = PUBLIC_DIR / "data"
OVERRIDE_FILE = ROOT_DIR / "manual_overrides.csv"
JSON_FILE = DATA_DIR / "bonds_latest.json"
CSV_FILE = DATA_DIR / "bonds_latest.csv"

DEFAULT_LOOKAHEAD_DAYS = int(os.getenv("DEFAULT_LOOKAHEAD_DAYS", "45"))
FACE_VALUE_PER_LOT = 1000.0
BOARD_LOT_SHARES = 100

# GitHub Actions 云端机器访问部分行情源时偶发 RemoteDisconnected/限流。
# 这里做两层处理：先重试 AkShare；失败后改用东方财富 push2 直接接口补正股行情。
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://quote.eastmoney.com/",
}


def now_cn() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))


def now_iso_cn() -> str:
    return now_cn().replace(microsecond=0).isoformat(sep=" ")


def today_cn() -> date:
    return now_cn().date()


def safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    if x is None:
        return default
    if isinstance(x, (int, float, np.integer, np.floating)):
        if pd.isna(x):
            return default
        return float(x)
    s = str(x).strip().replace(",", "").replace("%", "")
    if s in {"", "-", "--", "nan", "NaN", "None", "null", "NaT"}:
        return default
    try:
        return float(s)
    except Exception:
        return default


def safe_str(x: Any) -> str:
    if x is None:
        return ""
    try:
        if isinstance(x, float) and pd.isna(x):
            return ""
    except Exception:
        pass
    s = str(x).strip()
    if s in {"nan", "NaN", "None", "NaT", "null"}:
        return ""
    return s


def normalize_stock_code(code: Any) -> str:
    s = safe_str(code)
    if not s:
        return ""
    if s.endswith(".0"):
        s = s[:-2]
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:]
    return digits.zfill(6) if digits else ""


def parse_date_any(x: Any) -> Optional[date]:
    if x is None:
        return None
    if isinstance(x, pd.Timestamp):
        if pd.isna(x):
            return None
        return x.date()
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    if isinstance(x, (float, np.floating)) and pd.isna(x):
        return None
    s = safe_str(x)
    if not s:
        return None
    if s.endswith(".0"):
        s = s[:-2]
    if s.isdigit() and len(s) == 8:
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except Exception:
            return None
    try:
        ts = pd.to_datetime(s, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.date()
    except Exception:
        return None


def date_to_str(d: Optional[date]) -> str:
    return d.isoformat() if d else ""


def get_col(row: pd.Series, *names: str, default: Any = None) -> Any:
    for name in names:
        if name in row and not pd.isna(row[name]):
            return row[name]
    return default


def prev_business_day(d: date) -> date:
    x = d - timedelta(days=1)
    while x.weekday() >= 5:
        x -= timedelta(days=1)
    return x


def ceil_to_board_lot(shares: float) -> int:
    if not shares or shares <= 0:
        return 0
    return int(math.ceil(shares / BOARD_LOT_SHARES) * BOARD_LOT_SHARES)


def rating_bonus(rating: str) -> float:
    table = {
        "AAA": 5.0,
        "AA+": 3.5,
        "AA": 2.2,
        "AA-": 0.8,
        "A+": -0.8,
        "A": -1.8,
        "A-": -3.0,
        "BBB+": -5.0,
        "BBB": -7.0,
    }
    return table.get(safe_str(rating).upper(), 0.0)


def estimate_bond_listing_price(
    convert_value: Optional[float],
    rating: str,
    stock_change_pct: Optional[float],
    issue_size_yi: Optional[float],
) -> float:
    """简化估价模型：只用于页面默认排序和初筛，不代表真实上市价格。"""
    cv = convert_value if convert_value and convert_value > 0 else 100.0
    cv_part = max(-8.0, min(38.0, 0.72 * (cv - 100.0)))

    chg = stock_change_pct if stock_change_pct is not None else 0.0
    momentum_part = max(-3.0, min(3.0, 0.30 * chg))

    scale_part = 0.0
    if issue_size_yi:
        if issue_size_yi <= 5:
            scale_part = 2.0
        elif issue_size_yi >= 25:
            scale_part = -2.0
        elif issue_size_yi >= 15:
            scale_part = -1.0

    price = 112.0 + cv_part + rating_bonus(rating) + momentum_part + scale_part
    return round(max(90.0, min(157.3, price)), 2)


def load_manual_overrides() -> Dict[str, Dict[str, Any]]:
    overrides: Dict[str, Dict[str, Any]] = {}
    if not OVERRIDE_FILE.exists():
        return overrides
    with OVERRIDE_FILE.open("r", encoding="utf-8-sig", newline="") as f:
        useful_lines = (line for line in f if not line.lstrip().startswith("#") and line.strip())
        reader = csv.DictReader(useful_lines)
        for row in reader:
            code = safe_str(row.get("bond_code"))
            if not code:
                continue
            overrides[code] = {
                "record_date": parse_date_any(row.get("record_date")),
                "allot_per_share": safe_float(row.get("allot_per_share")),
                "expected_price": safe_float(row.get("expected_price")),
                "remark": safe_str(row.get("remark")),
            }
    return overrides


@dataclass
class BuildResult:
    rows: List[Dict[str, Any]]
    errors: List[str]
    warnings: List[str]


def retry_call(name: str, fn: Callable[[], Any], tries: int = 3, base_sleep: float = 2.0) -> Any:
    last_exc: Optional[Exception] = None
    for i in range(tries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if i < tries - 1:
                time.sleep(base_sleep * (i + 1) + random.uniform(0.2, 1.2))
    raise RuntimeError(f"{name} 连续 {tries} 次失败：{last_exc}")


def chunked(items: Sequence[str], n: int) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


def eastmoney_secid(code: str) -> str:
    code = normalize_stock_code(code)
    # 沪市主板/科创板常见代码 6/9 开头；其余深市 0/2/3 开头。
    if code.startswith(("5", "6", "7", "9")):
        return f"1.{code}"
    return f"0.{code}"


def fetch_stock_quotes_eastmoney(stock_codes: Sequence[str]) -> pd.DataFrame:
    """东方财富 push2 直连备用源，只抓当前转债涉及的正股，避免全市场接口被断开。"""
    codes = sorted({normalize_stock_code(c) for c in stock_codes if normalize_stock_code(c)})
    if not codes:
        return pd.DataFrame()

    records: List[Dict[str, Any]] = []
    fields = "f12,f14,f2,f3,f20,f21,f24,f25"
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"

    with requests.Session() as session:
        session.headers.update(REQUEST_HEADERS)
        for group in chunked(codes, 80):
            secids = ",".join(eastmoney_secid(c) for c in group)
            params = {
                "fltt": "2",
                "invt": "2",
                "secids": secids,
                "fields": fields,
                "ut": "fa5fd1943c7b386f172d6893dbfba10b",
                "_": str(int(time.time() * 1000)),
            }

            def _request() -> Dict[str, Any]:
                resp = session.get(url, params=params, timeout=20)
                resp.raise_for_status()
                return resp.json()

            data = retry_call("东方财富正股行情备用源", _request, tries=3, base_sleep=1.5)
            diff = (data.get("data") or {}).get("diff") or []
            for item in diff:
                price = safe_float(item.get("f2"))
                pct = safe_float(item.get("f3"))
                total_mv = safe_float(item.get("f20"))
                float_mv = safe_float(item.get("f21"))
                pct_60d = safe_float(item.get("f24"))
                pct_ytd = safe_float(item.get("f25"))
                records.append(
                    {
                        "代码": normalize_stock_code(item.get("f12")),
                        "最新价": None if price is None or price < 0 else price,
                        "涨跌幅": pct,
                        "总市值": total_mv,
                        "流通市值": float_mv,
                        "60日涨跌幅": pct_60d,
                        "年初至今涨跌幅": pct_ytd,
                        "正股简称_stock": safe_str(item.get("f14")),
                    }
                )
            time.sleep(random.uniform(0.2, 0.6))

    return pd.DataFrame(records).drop_duplicates("代码") if records else pd.DataFrame()


def fetch_stock_quotes(stock_codes: Sequence[str], warnings: List[str]) -> Optional[pd.DataFrame]:
    import akshare as ak

    try:
        stock_df = retry_call("ak.stock_zh_a_spot_em", ak.stock_zh_a_spot_em, tries=3, base_sleep=2.0)
        if stock_df is not None and len(stock_df) > 0:
            stock_df = stock_df.copy()
            if "代码" in stock_df.columns:
                stock_df["代码"] = stock_df["代码"].map(normalize_stock_code)
            return stock_df
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"ak.stock_zh_a_spot_em 暂时失败，已切换备用行情源：{exc}")

    try:
        fallback_df = fetch_stock_quotes_eastmoney(stock_codes)
        if fallback_df is not None and len(fallback_df) > 0:
            warnings.append("正股行情已通过东方财富备用源生成。")
            return fallback_df
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"东方财富备用行情源也失败：{exc}")

    return None


def fetch_and_normalize() -> BuildResult:
    import akshare as ak

    errors: List[str] = []
    warnings: List[str] = []
    rows: List[Dict[str, Any]] = []

    em_df: Optional[pd.DataFrame] = None
    ths_df: Optional[pd.DataFrame] = None

    try:
        em_df = retry_call("ak.bond_zh_cov", ak.bond_zh_cov, tries=3, base_sleep=2.0)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"ak.bond_zh_cov 获取失败：{exc}")

    try:
        ths_df = retry_call("ak.bond_zh_cov_info_ths", ak.bond_zh_cov_info_ths, tries=3, base_sleep=2.0)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"ak.bond_zh_cov_info_ths 获取失败，配售码等字段可能不完整：{exc}")

    if em_df is None and ths_df is None:
        raise RuntimeError("可转债数据源全部获取失败：" + "；".join(errors + warnings))

    df = em_df.copy() if em_df is not None else ths_df.copy()

    if ths_df is not None and "债券代码" in df.columns and "债券代码" in ths_df.columns:
        keep = [c for c in ["债券代码", "原股东配售码", "原股东配售认购代码", "每股获配额"] if c in ths_df.columns]
        if len(keep) > 1:
            df = df.merge(
                ths_df[keep].drop_duplicates("债券代码"),
                on="债券代码",
                how="left",
                suffixes=("", "_ths"),
            )

    if "正股代码" in df.columns:
        stock_codes = [normalize_stock_code(c) for c in df["正股代码"].dropna().tolist()]
    else:
        stock_codes = []
    stock_df = fetch_stock_quotes(stock_codes, warnings)

    if stock_df is not None and "代码" in stock_df.columns and "正股代码" in df.columns:
        stock_df = stock_df.copy()
        stock_df["代码"] = stock_df["代码"].map(normalize_stock_code)
        df["正股代码"] = df["正股代码"].map(normalize_stock_code)
        spot_keep = [
            c
            for c in ["代码", "最新价", "涨跌幅", "总市值", "流通市值", "60日涨跌幅", "年初至今涨跌幅", "正股简称_stock"]
            if c in stock_df.columns
        ]
        if len(spot_keep) > 1:
            df = df.merge(
                stock_df[spot_keep].drop_duplicates("代码"),
                left_on="正股代码",
                right_on="代码",
                how="left",
                suffixes=("", "_stock"),
            )
    else:
        warnings.append("正股实时行情未获取成功，页面仍会显示转债基础数据，但安全垫/正股金额可能为空。")

    overrides = load_manual_overrides()

    for _, row in df.iterrows():
        bond_code = safe_str(get_col(row, "债券代码", "SECURITY_CODE"))
        if not bond_code:
            continue

        bond_name = safe_str(get_col(row, "债券简称", "SECURITY_NAME_ABBR"))
        stock_code = normalize_stock_code(get_col(row, "正股代码", "CONVERT_STOCK_CODE"))
        stock_name = safe_str(get_col(row, "正股简称", "SECURITY_SHORT_NAME", "正股简称_stock"))
        subscribe_date = parse_date_any(get_col(row, "申购日期", "APPLY_DATE"))
        record_date = parse_date_any(get_col(row, "原股东配售-股权登记日", "股权登记日", "RECORD_DATE"))
        if not record_date and subscribe_date:
            record_date = prev_business_day(subscribe_date)

        allot = safe_float(
            get_col(row, "原股东配售-每股配售额", "每股获配额", "每股获配额_ths", "ALLOTMENT_RATIO")
        )

        stock_price = safe_float(get_col(row, "最新价", "正股价", "CURRENT_PRICE", "正股最新价"))
        stock_change = safe_float(get_col(row, "涨跌幅", "正股涨跌幅"))
        market_cap = safe_float(get_col(row, "总市值"))
        float_market_cap = safe_float(get_col(row, "流通市值"))
        change_60d = safe_float(get_col(row, "60日涨跌幅"))
        change_ytd = safe_float(get_col(row, "年初至今涨跌幅"))
        convert_price = safe_float(get_col(row, "转股价", "转股价格", "CONVERT_PRICE"))
        convert_value = safe_float(get_col(row, "转股价值"))
        if (not convert_value or convert_value <= 0) and stock_price and convert_price and convert_price > 0:
            convert_value = stock_price / convert_price * 100.0

        issue_size_yi = safe_float(get_col(row, "发行规模", "实际发行量", "计划发行量", "BOND_ISSUE_SCALE"))
        if issue_size_yi and issue_size_yi > 10000:
            issue_size_yi = issue_size_yi / 1e8

        rating = safe_str(get_col(row, "信用评级", "CREDIT_RATING"))
        purchase_code = safe_str(get_col(row, "申购代码", "APPLY_CODE"))
        allot_code = safe_str(get_col(row, "原股东配售码", "原股东配售认购代码", "原股东配售认购代码_ths", "ALLOTMENT_CODE"))
        listing_date = parse_date_any(get_col(row, "上市时间", "上市日期", "LISTING_DATE"))
        win_rate = safe_float(get_col(row, "中签率"))

        manual_expected = None
        remark = ""
        if bond_code in overrides:
            override = overrides[bond_code]
            if override.get("record_date"):
                record_date = override["record_date"]
            if override.get("allot_per_share"):
                allot = override["allot_per_share"]
            manual_expected = override.get("expected_price")
            remark = override.get("remark") or ""

        model_price = estimate_bond_listing_price(convert_value, rating, stock_change, issue_size_yi)
        expected_price = manual_expected or model_price

        rows.append(
            {
                "bond_code": bond_code,
                "bond_name": bond_name,
                "stock_code": stock_code,
                "stock_name": stock_name,
                "subscribe_date": date_to_str(subscribe_date),
                "record_date": date_to_str(record_date),
                "listing_date": date_to_str(listing_date),
                "purchase_code": purchase_code,
                "allot_code": allot_code,
                "allot_per_share": allot,
                "stock_price": stock_price,
                "stock_change_pct": stock_change,
                "stock_change_60d_pct": change_60d,
                "stock_change_ytd_pct": change_ytd,
                "market_cap": market_cap,
                "float_market_cap": float_market_cap,
                "convert_price": convert_price,
                "convert_value": convert_value,
                "issue_size_yi": issue_size_yi,
                "rating": rating,
                "win_rate_pct": win_rate,
                "expected_price_model": model_price,
                "expected_price_manual": manual_expected,
                "expected_price": expected_price,
                "remark": remark,
            }
        )

    return BuildResult(rows=rows, errors=errors, warnings=warnings)


def compute_preview_rows(rows: List[Dict[str, Any]], lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS) -> List[Dict[str, Any]]:
    t = today_cn()
    end = t + timedelta(days=max(1, lookahead_days))
    out: List[Dict[str, Any]] = []
    for r in rows:
        record_date = parse_date_any(r.get("record_date"))
        subscribe_date = parse_date_any(r.get("subscribe_date"))
        basis_date = record_date or subscribe_date
        if not basis_date or not (t <= basis_date <= end):
            continue
        allot = safe_float(r.get("allot_per_share"), 0.0) or 0.0
        stock_price = safe_float(r.get("stock_price"), 0.0) or 0.0
        exp_price = safe_float(r.get("expected_price"), 0.0) or 0.0
        need_shares = ceil_to_board_lot(FACE_VALUE_PER_LOT / allot) if allot > 0 else 0
        stock_cost = need_shares * stock_price if need_shares and stock_price else 0.0
        expected_profit = FACE_VALUE_PER_LOT * (exp_price - 100.0) / 100.0 if exp_price else 0.0
        safety = expected_profit / stock_cost * 100.0 if stock_cost else 0.0
        item = dict(r)
        item.update(
            {
                "need_shares_for_1_lot": need_shares,
                "stock_cost_for_1_lot": round(stock_cost, 2),
                "expected_profit_for_1_lot": round(expected_profit, 2),
                "safety_cushion_pct_for_1_lot": round(safety, 2),
                "days_to_record": (basis_date - t).days,
            }
        )
        out.append(item)
    out.sort(key=lambda x: (x.get("days_to_record", 9999), -x.get("safety_cushion_pct_for_1_lot", 0)))
    return out


def build_payload(result: BuildResult) -> Dict[str, Any]:
    return {
        "schema_version": 3,
        "app": "可转债抢权配售监控",
        "build_time": now_iso_cn(),
        "today": today_cn().isoformat(),
        "timezone": "Asia/Shanghai",
        "source": [
            "akshare.bond_zh_cov",
            "akshare.bond_zh_cov_info_ths",
            "akshare.stock_zh_a_spot_em",
            "eastmoney.push2 fallback",
        ],
        "errors": result.errors,
        "warnings": result.warnings,
        "defaults": {
            "lookahead_days": DEFAULT_LOOKAHEAD_DAYS,
            "face_value_per_lot": FACE_VALUE_PER_LOT,
            "board_lot_shares": BOARD_LOT_SHARES,
        },
        "rows": result.rows,
        "preview_rows": compute_preview_rows(result.rows),
    }


def write_outputs(result: BuildResult) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = build_payload(result)
    tmp_json = JSON_FILE.with_suffix(".tmp")
    tmp_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_json.replace(JSON_FILE)

    csv_fields = [
        "bond_code",
        "bond_name",
        "stock_code",
        "stock_name",
        "record_date",
        "subscribe_date",
        "listing_date",
        "purchase_code",
        "allot_code",
        "allot_per_share",
        "stock_price",
        "stock_change_pct",
        "convert_price",
        "convert_value",
        "issue_size_yi",
        "rating",
        "win_rate_pct",
        "expected_price_model",
        "expected_price_manual",
        "expected_price",
        "remark",
    ]
    with CSV_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result.rows)


def write_error_output(exc: Exception) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 3,
        "app": "可转债抢权配售监控",
        "build_time": now_iso_cn(),
        "today": today_cn().isoformat(),
        "timezone": "Asia/Shanghai",
        "source": [
            "akshare.bond_zh_cov",
            "akshare.bond_zh_cov_info_ths",
            "akshare.stock_zh_a_spot_em",
            "eastmoney.push2 fallback",
        ],
        "errors": [str(exc)],
        "warnings": [],
        "defaults": {
            "lookahead_days": DEFAULT_LOOKAHEAD_DAYS,
            "face_value_per_lot": FACE_VALUE_PER_LOT,
            "board_lot_shares": BOARD_LOT_SHARES,
        },
        "rows": [],
        "preview_rows": [],
    }
    JSON_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    try:
        result = fetch_and_normalize()
        write_outputs(result)
        print(f"OK: generated {len(result.rows)} raw rows at {JSON_FILE}")
        if result.errors:
            print("ERRORS:")
            for e in result.errors:
                print("-", e)
        if result.warnings:
            print("WARNINGS:")
            for w in result.warnings:
                print("-", w)
    except Exception as exc:  # noqa: BLE001
        write_error_output(exc)
        print("ERROR:", exc)
        # 可转债主数据全部失败时才让 Actions 失败。
        raise


if __name__ == "__main__":
    main()
