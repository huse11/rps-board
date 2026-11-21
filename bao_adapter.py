# -*- coding: utf-8 -*-
"""
BaoStock 备份数据源适配层 (2026-08-30 新增)
- 定位: A股数据"备份接口" — Tushare 权限缺失 / AKShare 东财被拒时兜底
- 免费免token, 实测服务器可用; 日期格式必须 YYYY-MM-DD
- 提供:
  * fetch_kline_bao   个股日线 (open/high/low/close/vol/amount/pct_chg/turn 换手率),
                       可兜底 rps_calc.daily_map 或 recommend 换手率
  * fetch_fina_bao    财务指标 (毛利率 gpMargin/净利率 npMargin/ROE roeAvg/
                       归母净利 netProfit/净利同比 YOYNI/扣非同比 YOYPNI/资产负债率 liabilityToAsset),
                       比同花顺更全: 含销售毛利率与归母净利绝对数
                       → 返回结构兼容 fetch_fina_f10_em
  * fetch_trade_days  交易日历 (备份 Tushare trade_cal 限频场景)
- 会话: 模块级 login 一次复用 (baostock 要求单线程会话)
"""
import logging
import time
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger("bao_adapter")

try:
    import baostock as bs
    _BAO_OK = True
except Exception as _e:
    bs = None
    _BAO_OK = False
    _BAO_IMPORT_ERR = str(_e)

_BSD_LOGIN = False
_LOGIN_TS = 0
_LOGIN_TTL = 5400  # 1.5小时复用一次登录(服务器端会话)

# ============================================================
# 会话管理 (首次调用自动 login; 失败返回 False 不抛)
# ============================================================
def _ensure_login():
    """确保 baostock 已登录 (会话级, 首次登录后 TTL 内复用)"""
    global _BSD_LOGIN, _LOGIN_TS
    if not _BAO_OK:
        return False
    now = time.time()
    if _BSD_LOGIN and now - _LOGIN_TS < _LOGIN_TTL:
        return True
    try:
        lg = bs.login()
        if lg and lg.error_code == "0":
            _BSD_LOGIN = True
            _LOGIN_TS = now
            return True
        _BSD_LOGIN = False
        logger.warning("baostock 登录失败: %s %s",
                       getattr(lg, "error_code", "?"), getattr(lg, "error_msg", "?"))
        return False
    except Exception as e:
        _BSD_LOGIN = False
        logger.warning("baostock 登录异常: %s", str(e)[:80])
        return False


def _ts_to_bs_code(ts_code):
    """ts_code(600519.SH) → baostock(sh.600519)"""
    parts = str(ts_code or "").split(".")
    if len(parts) != 2 or not parts[0].isdigit():
        return None
    sym, mkt = parts
    return ("sh." if mkt == "SH" else "sz." if mkt == "SZ" else "bj." if mkt == "BJ" else "") + sym


# ============================================================
# 1. 个股日线 (备份 daily 数据)
# ============================================================
def fetch_kline_bao(ts_code, start_date=None, end_date=None, adjustflag="3"):
    """BaoStock 日线 → DataFrame(date,open,high,low,close,preclose,vol,amount,pct_chg,turn)
    date=YYYYMMDD(转); 失败/空返回 None
    """
    if not _ensure_login():
        return None
    bs_code = _ts_to_bs_code(ts_code)
    if not bs_code:
        return None
    try:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg",
            start_date=_fmt_bs_date(start_date or "1900-01-01"),
            end_date=_fmt_bs_date(end_date or "2099-12-31"),
            frequency="d", adjustflag=adjustflag)
        if rs is None or rs.error_code != "0":
            logger.warning("baostock 日线失败(%s): %s", ts_code, getattr(rs, "error_msg", "?"))
            return None
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
    except Exception as e:
        logger.warning("baostock 日线异常(%s): %s", ts_code, str(e)[:80])
        return None
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=rs.fields)
    out = []
    for _, r in df.iterrows():
        try:
            out.append({
                "date": str(r["date"]).replace("-", ""),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "preclose": float(r["preclose"]),
                "vol": float(r["volume"]),
                "amount": float(r["amount"]),
                "pct_chg": float(r["pctChg"]) if r["pctChg"] not in ("", "-") else None,
                "turn": float(r["turn"]) if r["turn"] not in ("", "-") else None,
            })
        except Exception:
            continue
    if not out:
        return None
    return pd.DataFrame(out)


# ============================================================
# 2. 财务指标 (备份 fina_indicator; 比同花顺多 毛利率+归母净利绝对数)
# ============================================================
_FINA_CACHE = {}
_FINA_CACHE_TS = 0
_FINA_TTL = 3600


def fetch_fina_bao(symbol):
    """BaoStock 财务指标 → {ts_code: {end_date, net_profit, netprofit_yoy,
        dt_netprofit_yoy, n_income_attr_parent, debt_to_assets, total_revenue,
        revenue_yoy, gross_margin, net_margin, roe, roic, ocf_ps, bps}}
    字段映射(兼容 fetch_fina_f10_em):
      statDate→end_date, netProfit→net_profit, YOYNI→netprofit_yoy, YOYPNI→dt_netprofit_yoy,
      liabilityToAsset→debt_to_assets, MBRevenue→total_revenue, gpMargin→gross_margin,
      npMargin→net_margin, roeAvg→roe
    缺失: n_income_attr_parent/roic/ocf_ps/bps/营收同比 → None
    (baostock 无扣非绝对数/每股现金流/每股净资产, 由同花顺/东财F10 补充)
    """
    if not _ensure_login():
        return {}
    key = str(symbol).split(".")[0]
    now = time.time()
    hit = _FINA_CACHE.get(key)
    if hit is not None and now - _FINA_CACHE_TS < _FINA_TTL:
        return {_ts_back(key): hit}
    # 纯数字(600519)/带市场(600519.SH) 统一 → baostock 格式 (600519→sh.600519, 2026-08-30 修复)
    ts_full = _ts_back(key)
    bs_code = _ts_to_bs_code(ts_full)
    if not bs_code:
        return {}
    # 当前年月 → 最新季度; 财报披露滞后时在循环内回退上一季度
    _dt = datetime.fromtimestamp(now)
    year = _dt.year
    quarter = max(1, (_dt.month - 1) // 3 + 1)
    rows = []
    try:
        # 最新季度优先; 若取不到(财报未披露)回退上一季度
        # profit/growth/balance 三个接口统一用同一个成功季度 (2026-08-30 修复)
        q_used = quarter
        for q in (quarter, max(1, quarter - 1)):
            rs = bs.query_profit_data(code=bs_code, year=year, quarter=q)
            if rs is None or rs.error_code != "0":
                continue
            pr = []
            while rs.next():
                pr.append(rs.get_row_data())
            if pr:
                rows.append(("profit", q, pr[0]))
                q_used = q
                break
        if q_used:
            gs = bs.query_growth_data(code=bs_code, year=year, quarter=q_used)
            if gs and gs.error_code == "0":
                g_row = None
                while gs.next():
                    g_row = gs.get_row_data()
                if g_row:
                    rows.append(("growth", q_used, g_row))
            bl = bs.query_balance_data(code=bs_code, year=year, quarter=q_used)
            if bl and bl.error_code == "0":
                b_row = None
                while bl.next():
                    b_row = bl.get_row_data()
                if b_row:
                    rows.append(("balance", q_used, b_row))
    except Exception as e:
        logger.warning("baostock 财务异常(%s): %s", symbol, str(e)[:80])
        return {}
    if not rows:
        return {}
    d = {}
    for kind, q, r in rows:
        fields = {"profit": ["code", "pubDate", "statDate", "roeAvg", "npMargin", "gpMargin",
                             "netProfit", "epsTTM", "MBRevenue", "totalShare", "liqaShare"],
                  "growth": ["code", "pubDate", "statDate", "YOYEquity", "YOYAsset", "YOYNI",
                             "YOYEPSBasic", "YOYPNI"],
                  "balance": ["code", "pubDate", "statDate", "currentRatio", "quickRatio",
                              "cashRatio", "YOYLiability", "liabilityToAsset", "assetToEquity"]}
        vals = dict(zip(fields[kind], r))
        if kind == "profit":
            d.update({"end_date": (vals.get("statDate") or "").replace("-", ""),
                      "net_profit": _f(vals.get("netProfit")),
                      "gross_margin": _pct(vals.get("gpMargin")),
                      "net_margin": _pct(vals.get("npMargin")),
                      "roe": _pct(vals.get("roeAvg")),
                      "total_revenue": _f(vals.get("MBRevenue"))})
        elif kind == "growth":
            d.update({"netprofit_yoy": _pct(vals.get("YOYNI")),
                      "dt_netprofit_yoy": _pct(vals.get("YOYPNI"))})
        elif kind == "balance":
            d.update({"debt_to_assets": _pct(vals.get("liabilityToAsset"))})
            d["_quarter"] = q
    if not d or "end_date" not in d:
        return {}
    out = {
        "end_date": d.get("end_date", ""),
        "net_profit": d.get("net_profit"),
        "netprofit_yoy": d.get("netprofit_yoy"),
        "dt_netprofit_yoy": d.get("dt_netprofit_yoy"),
        "n_income_attr_parent": None,   # baostock 无扣非绝对数
        "debt_to_assets": d.get("debt_to_assets"),
        "total_revenue": d.get("total_revenue"),
        "revenue_yoy": None,            # 无营收同比
        "gross_margin": d.get("gross_margin"),
        "net_margin": d.get("net_margin"),
        "roe": d.get("roe"),
        "roic": None,                   # 无 ROIC
        "ocf_ps": None,                 # 无每股现金流
        "bps": None,                    # 无每股净资产
    }
    ts_code = _ts_back(key)
    _FINA_CACHE[key] = dict(out)
    _FINA_CACHE_TS = now
    if len(_FINA_CACHE) > 3000:
        _FINA_CACHE.clear()
    return {ts_code: out}


def _fmt_bs_date(d):
    """YYYYMMDD → YYYY-MM-DD (baostock 强制带横线格式, 2026-08-30 实测)"""
    s = str(d).replace("-", "")
    if len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return str(d)


def _f(v):
    try:
        f_v = float(v)
        if f_v != f_v or f_v in (float("inf"), float("-inf")):
            return None
        return round(f_v, 2)
    except (TypeError, ValueError):
        return None


def _pct(v):
    """BaoStock 返回小数(如 0.1795=17.95%) → 百分比数值
    注意: 不能先 _f(round 2位) 再乘100 (0.8955→0.9→90.0 失真),
    直接 float×100 后再 round (2026-08-30 修复)
    """
    try:
        f_v = float(v)
        if f_v != f_v or f_v in (float("inf"), float("-inf")):
            return None
        return round(f_v * 100, 2)
    except (TypeError, ValueError):
        return None


def _ts_back(sym):
    """6位数字 → ts_code (600519→600519.SH, 000001→000001.SZ, 8/4开头→BJ)"""
    s = str(sym).zfill(6)
    if s.startswith(("6", "9")):
        return s + ".SH"
    if s.startswith(("4", "8", "920")):
        return s + ".BJ"
    return s + ".SZ"


# ============================================================
# 3. 交易日历 (备份 trade_cal)
# ============================================================
def fetch_trade_days(start_date, end_date):
    """交易日 → [YYYYMMDD] (仅 trading day); 失败返回 None"""
    if not _ensure_login():
        return None
    try:
        rs = bs.query_trade_dates(
            start_date=_fmt_bs_date(start_date),
            end_date=_fmt_bs_date(end_date))
        if rs is None or rs.error_code != "0":
            return None
        out = []
        while rs.next():
            r = rs.get_row_data()
            if len(r) >= 2 and r[1] == "1":
                out.append(r[0].replace("-", ""))
        return sorted(out) or None
    except Exception as e:
        logger.warning("baostock 交易日失败: %s", str(e)[:80])
        return None


def bao_status():
    if _BAO_OK:
        return {"ok": True, "login": _BSD_LOGIN}
    return {"ok": False, "error": _BAO_IMPORT_ERR}


if __name__ == "__main__":
    print("BaoStock 状态:", bao_status())
    print("\n=== 1. 日线 (茅台) ===")
    d = fetch_kline_bao("600519.SH", "20260820", "20260828")
    print(f"  {'✅ ' + str(len(d)) + ' 条' if d is not None else '❌ 失败'}")
    if d is not None:
        print("  首条:", d.iloc[0].to_dict())

    print("\n=== 2. 财务 (茅台) ===")
    f = fetch_fina_bao("600519.SH")
    v = f.get("600519.SH") if f else None
    if v:
        print(f"  ✅ 报告期={v['end_date']} 负债率={v['debt_to_assets']}% 净利率={v['net_margin']}% "
              f"毛利率={v['gross_margin']}% ROE={v['roe']}% 净利同比={v['netprofit_yoy']}% 归母净利={v['net_profit']}")
    else:
        print("  ❌ 无数据")

    print("\n=== 3. 交易日 (8-24 ~ 8-29) ===")
    td = fetch_trade_days("20260824", "20260829")
    print(f"  {td if td else '❌ 失败'}")