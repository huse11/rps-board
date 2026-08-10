# -*- coding: utf-8 -*-
"""
AKShare 数据源适配层 v2.0 (服务器实测后定版)
- 东财 push2 端点在阿里云服务器被拒 (RemoteDisconnected), 改用 腾讯/sina 源
- 腾讯 stock_zh_a_spot_tx 一个接口同时覆盖 daily_basic + moneyflow (主力净流入)
- 五大维度对应接口:
  * 指数日线  → stock_zh_index_daily_em        (东财kline, 服务器可达)
  * 个股资金  → stock_zh_a_spot_tx.zljlr        (腾讯, 替代 moneyflow 全市场批量)
  * 北向资金  → stock_hsgt_hist_em              (东财datacenter, 近期港交所限披露可能NaN)
  * 实时行情  → stock_zh_a_spot_tx              (腾讯, 替代 daily_basic: PE/换手率/量比/流通市值)
  * 大盘资金  → stock_market_fund_flow          (东财datacenter, 大盘主力净流入按日)
- 返回结构与 recommend.py 中 fetch_*_em 函数一致, 可直接替换
- 单位约定 (经茅台校准):
  * zljlr 主力净流入: 万元 → 调用方需元时 ×1e4
  * ltsz 流通市值: 亿元 → 调用方需万元时 ×1e4
"""
import json
import math
import time
import pandas as pd
from datetime import datetime, timedelta

try:
    import akshare as ak
    _AK_VERSION = getattr(ak, "__version__", "unknown")
    _AK_OK = True
except Exception as _e:
    ak = None
    _AK_VERSION = None
    _AK_OK = False
    _AK_IMPORT_ERR = str(_e)

# 腾讯 spot 缓存 (同一次 recommend 流程内避免重复拉取)
_SPOT_TX_CACHE = None
_SPOT_TX_CACHE_TS = 0
_SPOT_TX_TTL = 300  # 5分钟内复用


def ak_status():
    if _AK_OK:
        return {"ok": True, "version": _AK_VERSION}
    return {"ok": False, "error": _AK_IMPORT_ERR}


def _code_to_ts_code(code):
    """腾讯 code (sh600519/sz000001/bj920000) → ts_code (600519.SH)"""
    s = str(code).strip().lower()
    for pfx, mkt in [("sh", "SH"), ("sz", "SZ"), ("bj", "BJ")]:
        if s.startswith(pfx):
            return f"{s[len(pfx):]}.{mkt}"
    # 纯数字 → 按前缀推断
    c = s.zfill(6)
    if c.startswith(("6", "9")):
        return f"{c}.SH"
    if c.startswith(("4", "8")) or c.startswith("920"):
        return f"{c}.BJ"
    return f"{c}.SZ"


def _safe_float(val, default=0.0):
    if val is None or val == "" or val == "-":
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


def _pick_col(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None


# ============================================================
# 腾讯实时行情 (核心数据源, 覆盖 daily_basic + moneyflow)
# ============================================================
def _fetch_spot_tx(force=False):
    """拉取腾讯全市场实时行情, 带内存缓存
    返回 DataFrame(code, name, price, pe_ttm, hsl换手率%, lb量比, ltsz流通市值亿, zljlr主力净流入万, zllr主力流入万, zllc主力流出万)
    失败返回 None
    """
    global _SPOT_TX_CACHE, _SPOT_TX_CACHE_TS
    if not _AK_OK:
        return None
    now = time.time()
    if not force and _SPOT_TX_CACHE is not None and (now - _SPOT_TX_CACHE_TS) < _SPOT_TX_TTL:
        return _SPOT_TX_CACHE
    try:
        df = ak.stock_zh_a_spot_tx()
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    _SPOT_TX_CACHE = df
    _SPOT_TX_CACHE_TS = now
    return df


# ============================================================
# 1. 指数日线 (替代 index_daily)
# ============================================================
_INDEX_SYMBOL_MAP = {
    "000001.SH": "sh000001",
    "000300.SH": "sh000300",
    "399001.SZ": "sz399001",
    "399006.SZ": "sz399006",
}


def fetch_index_daily_ak(index_code, start, end):
    """AKShare 指数日线 → DataFrame(date, open, high, low, close, vol, amount)
    date=YYYYMMDD; 失败返回 None
    """
    if not _AK_OK:
        return None
    symbol = _INDEX_SYMBOL_MAP.get(index_code)
    if not symbol:
        sym, mkt = index_code.split(".")
        symbol = ("sh" if mkt == "SH" else "sz") + sym
    try:
        df = ak.stock_zh_index_daily_em(symbol=symbol)
    except Exception:
        try:
            df = ak.stock_zh_index_daily(symbol=symbol)
        except Exception:
            return None
    if df is None or len(df) == 0:
        return None
    date_col = _pick_col(df, ["date", "日期", "trade_date"])
    if date_col is None:
        return None
    rows = []
    beg = str(start).replace("-", "")
    end_d = str(end).replace("-", "")
    oc = _pick_col(df, ["open", "开盘"])
    cc = _pick_col(df, ["close", "收盘"])
    hc = _pick_col(df, ["high", "最高"])
    lc = _pick_col(df, ["low", "最低"])
    vc = _pick_col(df, ["volume", "成交量", "vol"])
    ac = _pick_col(df, ["amount", "成交额"])
    for _, r in df.iterrows():
        try:
            d = str(r[date_col])[:10].replace("-", "")
            if d < beg or d > end_d:
                continue
            rows.append({
                "date": d,
                "open": _safe_float(r[oc]) if oc else 0.0,
                "close": _safe_float(r[cc]) if cc else 0.0,
                "high": _safe_float(r[hc]) if hc else 0.0,
                "low": _safe_float(r[lc]) if lc else 0.0,
                "vol": _safe_float(r[vc]) if vc else 0.0,
                "amount": _safe_float(r[ac]) if ac else 0.0,
            })
        except Exception:
            continue
    if not rows:
        return None
    return pd.DataFrame(rows)


# ============================================================
# 2. 个股资金流向 (替代 moneyflow) — 腾讯全市场批量
# ============================================================
def fetch_moneyflow_ak(latest_date):
    """腾讯主力净流入 (替代 fetch_moneyflow_em)
    返回 {ts_code: [{"trade_date": latest_date, "buy_sm_amount": 元, "sell_sm_amount": 元}]}
    zljlr(主力净流入万) >0→buy, <0→sell(绝对值); 转元 ×1e4
    失败返回 {}
    """
    if not _AK_OK:
        return {}
    df = _fetch_spot_tx()
    if df is None:
        return {}
    code_col = _pick_col(df, ["code"])
    net_col = _pick_col(df, ["zljlr"])  # 主力净流入(万元)
    if code_col is None or net_col is None:
        return {}
    result = {}
    for _, r in df.iterrows():
        code = str(r[code_col]).strip()
        if not code or code == "nan":
            continue
        ts_code = _code_to_ts_code(code)
        net_wan = _safe_float(r[net_col])  # 万元
        net_yuan = net_wan * 1e4            # → 元
        buy = net_yuan if net_yuan > 0 else 0.0
        sell = abs(net_yuan) if net_yuan < 0 else 0.0
        result[ts_code] = [{"trade_date": latest_date, "buy_sm_amount": buy, "sell_sm_amount": sell}]
    return result


# ============================================================
# 3. 北向资金 (替代 moneyflow_hsgt)
# ============================================================
def fetch_hsgt_ak(latest_date, n_days=5):
    """AKShare 北向资金历史 → {trade_date: {"buy_value": 净流入元}}
    港交所2024年8月起暂停实时北向披露, 近期值可能NaN→0
    失败返回 {}
    """
    if not _AK_OK:
        return {}
    try:
        df = ak.stock_hsgt_hist_em(symbol="北向资金")
    except Exception:
        return {}
    if df is None or len(df) == 0:
        return {}
    date_col = _pick_col(df, ["日期", "date", "trade_date"])
    # 优先 "当日成交净买额", 其次 "当日资金流入"
    net_col = _pick_col(df, ["当日成交净买额", "当日资金流入", "当日资金净流入", "资金净流入"])
    if date_col is None or net_col is None:
        return {}
    rows = []
    for _, r in df.iterrows():
        try:
            td = str(r[date_col])[:10].replace("-", "")
            if not td:
                continue
            net = _safe_float(r[net_col])  # 元
            rows.append((td, net))
        except Exception:
            continue
    rows = sorted(rows, key=lambda x: x[0])[-n_days:]
    cache = {}
    for td, net in rows:
        cache[td] = {"buy_value": net}
    return cache


# ============================================================
# 4. 实时行情指标 (替代 daily_basic) — 腾讯全市场批量
# ============================================================
def fetch_daily_basic_ak(latest_date):
    """腾讯实时行情 (替代 fetch_daily_basic_em)
    返回 {ts_code: {pe_ttm, circ_mv(万元), turnover_rate, volume_ratio}}
    ltsz(流通市值亿)→万元 ×1e4; pe亏损→None
    失败返回 {}
    """
    if not _AK_OK:
        return {}
    df = _fetch_spot_tx()
    if df is None:
        return {}
    code_col = _pick_col(df, ["code"])
    pe_col = _pick_col(df, ["pe_ttm"])
    mv_col = _pick_col(df, ["ltsz"])
    tr_col = _pick_col(df, ["hsl"])
    vr_col = _pick_col(df, ["lb"])
    if code_col is None:
        return {}
    result = {}
    for _, r in df.iterrows():
        code = str(r[code_col]).strip()
        if not code or code == "nan":
            continue
        ts_code = _code_to_ts_code(code)
        # PE: 亏损返回负值/0 → None
        pe_raw = r[pe_col] if pe_col else None
        if pe_raw is None or pe_raw == "-" or pe_raw == "":
            pe = None
        else:
            pe_val = _safe_float(pe_raw, default=-1)
            pe = None if pe_val <= 0 else pe_val
        # 流通市值: 亿 → 万元
        circ_mv = None
        if mv_col:
            mv_raw = r[mv_col]
            if mv_raw not in (None, "-", ""):
                circ_mv = _safe_float(mv_raw) * 1e4  # 亿→万元
        result[ts_code] = {
            "pe_ttm": pe,
            "circ_mv": circ_mv,
            "turnover_rate": _safe_float(r[tr_col]) if (tr_col and r[tr_col] not in (None, "-", "")) else None,
            "volume_ratio": _safe_float(r[vr_col]) if (vr_col and r[vr_col] not in (None, "-", "")) else None,
        }
    return result


# ============================================================
# 5. 大盘资金流向 (新增, 强化大盘环境评估)
# ============================================================
def fetch_market_fund_flow_ak(n_days=5):
    """大盘主力净流入历史 → {trade_date: {"main_net": 元, "main_pct": %}}
    用于强化大盘环境评估 (主力净流入正=资金做多)
    失败返回 {}
    """
    if not _AK_OK:
        return {}
    try:
        df = ak.stock_market_fund_flow()
    except Exception:
        return {}
    if df is None or len(df) == 0:
        return {}
    date_col = _pick_col(df, ["日期", "date"])
    net_col = _pick_col(df, ["主力净流入-净额", "主力净流入"])
    pct_col = _pick_col(df, ["主力净流入-净占比"])
    if date_col is None or net_col is None:
        return {}
    rows = []
    for _, r in df.iterrows():
        try:
            td = str(r[date_col])[:10].replace("-", "")
            if not td:
                continue
            net = _safe_float(r[net_col])  # 元
            pct = _safe_float(r[pct_col]) if pct_col else 0.0
            rows.append((td, net, pct))
        except Exception:
            continue
    rows = sorted(rows, key=lambda x: x[0])[-n_days:]
    cache = {}
    for td, net, pct in rows:
        cache[td] = {"main_net": net, "main_pct": pct}
    return cache


# ============================================================
# 自检入口
# ============================================================
if __name__ == "__main__":
    print(f"AKShare 状态: {ak_status()}")
    if not _AK_OK:
        raise SystemExit(1)

    # 茅台校准 (单位确认): sh600519 流通市值~1.5-2万亿, ltsz应显示~15000-20000(亿)
    print("\n=== 单位校准 (茅台 sh600519) ===")
    df = _fetch_spot_tx(force=True)
    if df is not None:
        mt = df[df["code"] == "sh600519"]
        if len(mt) > 0:
            r = mt.iloc[0]
            print(f"  茅台 ltsz={r.get('ltsz')} (亿元? 万元?)")
            print(f"  茅台 zljlr={r.get('zljlr')} (万元? 元?)")
            print(f"  茅台 pe_ttm={r.get('pe_ttm')}, hsl={r.get('hsl')}, lb={r.get('lb')}")
            print(f"  ※ ltsz≈15000-20000→亿元; zljlr≈数万→万元")

    print(f"\n=== 1. 指数日线 (上证) ===")
    d = fetch_index_daily_ak("000001.SH", "20260701", "20260809")
    print(f"  {'✅ ' + str(len(d)) + ' 条' if d is not None else '❌ 失败'}")

    print(f"\n=== 2. 个股资金流向 ===")
    mf = fetch_moneyflow_ak("20260808")
    print(f"  {'✅ ' + str(len(mf)) + ' 只' if mf else '❌ 失败'}")
    if mf:
        top = sorted(mf.items(), key=lambda x: x[1][0]["buy_sm_amount"], reverse=True)[:3]
        for c, v in top:
            print(f"  {c}: 主力净流入 {v[0]['buy_sm_amount']/1e8:.2f}亿")

    print(f"\n=== 3. 北向资金 ===")
    hsgt = fetch_hsgt_ak("20260808", n_days=5)
    print(f"  {'✅ ' + str(len(hsgt)) + ' 日' if hsgt else '❌ 失败'}")
    for td, v in sorted(hsgt.items()):
        print(f"  {td}: 北向 {v['buy_value']/1e8:.2f}亿")

    print(f"\n=== 4. 实时行情 ===")
    db = fetch_daily_basic_ak("20260808")
    print(f"  {'✅ ' + str(len(db)) + ' 只' if db else '❌ 失败'}")
    # 取茅台校验
    mt_db = db.get("600519.SH") if db else None
    if mt_db:
        print(f"  茅台: PE={mt_db['pe_ttm']}, 流通市值={mt_db['circ_mv']/1e4:.1f}亿, 换手={mt_db['turnover_rate']}, 量比={mt_db['volume_ratio']}")

    print(f"\n=== 5. 大盘资金流向 ===")
    mff = fetch_market_fund_flow_ak(n_days=5)
    print(f"  {'✅ ' + str(len(mff)) + ' 日' if mff else '❌ 失败'}")
    for td, v in sorted(mff.items()):
        print(f"  {td}: 主力净流入 {v['main_net']/1e8:.2f}亿 ({v['main_pct']:.2f}%)")
