# -*- coding: utf-8 -*-
"""
AKShare 数据源适配层 v2.0 (服务器实测后定版)
- 东财 push2 端点在阿里云服务器被拒 (RemoteDisconnected), 改用 腾讯/sina 源
- 腾讯 stock_zh_a_spot_tx 一个接口同时覆盖 daily_basic + moneyflow (主力净流入)
- 五大维度对应接口:
  * 指数日线  → stock_zh_index_daily_em        (东财kline, 服务器可达)
  * 个股资金  → stock_zh_a_spot_tx.zljlr        (腾讯, 替代 moneyflow 全市场批量)
  * 实时行情  → stock_zh_a_spot_tx              (腾讯, 替代 daily_basic: PE/换手率/量比/流通市值)
  * 大盘资金  → stock_market_fund_flow          (东财datacenter, 大盘主力净流入按日)
- 返回结构与 recommend.py 中 fetch_*_em 函数一致, 可直接替换
- 单位约定 (经茅台校准):
  * zljlr 主力净流入: 万元 → 调用方需元时 ×1e4
  * ltsz 流通市值: 亿元 → 调用方需万元时 ×1e4
"""
import json
import logging
import math
import time
import pandas as pd
from datetime import datetime, timedelta

logger = logging.getLogger("akshare_adapter")

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


def _norm_col(name):
    """列名归一化: 全角括号→半角, 去空格, 去单位差异 (同花顺接口随报告期全角/半角切换)"""
    if name is None:
        return ""
    s = str(name).replace("（", "(").replace("）", ")").replace(" ", "")
    return s


def _pick_col(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    # 兜底: 全角/半角括号差异归一化匹配 (同花顺等接口列名随调用参数变化)
    norm = {_norm_col(c): c for c in df.columns}
    for c in candidates:
        if _norm_col(c) in norm:
            return norm[_norm_col(c)]
    return None


def _pick_idx(series, candidates):
    """同花顺指标行(Series) 按候选列名取真实列 (兼容 df/row 两种调用)
    含括号归一化匹配(全角→半角), 兼容接口列名随报告期变化
    """
    for c in candidates:
        if c in series.index:
            return c
    norm = {_norm_col(c): c for c in series.index}
    for c in candidates:
        if _norm_col(c) in norm:
            return norm[_norm_col(c)]
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
    except Exception as e:
        logger.warning("腾讯全市场行情拉取失败: %s", e)
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
    except Exception as e:
        logger.warning("指数日线 em 源失败(%s): %s", symbol, e)
        try:
            df = ak.stock_zh_index_daily(symbol=symbol)
        except Exception as e2:
            logger.warning("指数日线 兜底源失败(%s): %s", symbol, e2)
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
# 3. 实时行情指标 (替代 daily_basic) — 腾讯全市场批量
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
# 4. 大盘资金流向 (强化大盘环境评估, 替代已停披露的北向资金)
# ============================================================
def fetch_market_fund_flow_ak(n_days=5):
    """大盘主力净流入历史 → {trade_date: {"main_net": 元, "main_pct": %}}
    用于强化大盘环境评估 (主力净流入正=资金做多)
    主源: 东财大盘资金流历史 (push2his, 部分网络被拒)
    兜底: 腾讯全市场主力净流入求和 (最新交易日真实值, 万→元), 覆盖 push2his 被拒场景
    失败返回 {}
    """
    if not _AK_OK:
        return {}
    cache = {}
    try:
        df = ak.stock_market_fund_flow()
    except Exception as e:
        logger.warning("大盘资金流拉取失败(将走腾讯兜底): %s", e)
        df = None
    if df is not None and len(df) > 0:
        date_col = _pick_col(df, ["日期", "date"])
        net_col = _pick_col(df, ["主力净流入-净额", "主力净流入"])
        pct_col = _pick_col(df, ["主力净流入-净占比"])
        if date_col is not None and net_col is not None:
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
            for td, net, pct in rows:
                cache[td] = {"main_net": net, "main_pct": pct}
    if cache:
        return cache
    # 兜底: 腾讯全市场主力净流入求和 (最新交易日真实值, 万→元)
    try:
        spot = _fetch_spot_tx()
        if spot is not None and "zljlr" in spot.columns and len(spot) > 100:
            d = datetime.now()
            while d.weekday() >= 5:  # 周末回退到周五
                d -= timedelta(days=1)
            td = d.strftime("%Y%m%d")
            total = float(pd.to_numeric(spot["zljlr"], errors="coerce").sum()) * 1e4  # 万元 → 元
            cache[td] = {"main_net": total, "main_pct": 0.0}
    except Exception as e:
        logger.warning("大盘资金流腾讯兜底失败: %s", e)
    return cache


# ============================================================
# 5. 龙虎榜净买额 (第四层资金面防御性信号, 2026-08-23 新增)
# ============================================================
def fetch_lhb_ak(latest_date):
    """龙虎榜明细 → {ts_code: {"lhb_net": 元, "reason": 上榜原因}}
    主源: 东财 stock_lhb_detail_em (当日全市场上榜个股)
    lhb_net < 0 → 上榜出货(防御性扣分/标签); lhb_net > 0 → 净买入(仅标签, 不额外加分)
    非交易日/无上榜/接口失败 → 返回 {} (缺失跳过, 不误杀)
    """
    if not _AK_OK:
        return {}
    td = str(latest_date).replace("-", "")
    try:
        df = ak.stock_lhb_detail_em(start_date=td, end_date=td)
    except Exception as e:
        logger.warning("龙虎榜拉取失败(%s): %s", td, e)
        return {}
    if df is None or len(df) == 0:
        return {}
    code_col = _pick_col(df, ["代码", "股票代码"])
    net_col = _pick_col(df, ["龙虎榜净买额"])
    reason_col = _pick_col(df, ["上榜原因"])
    if code_col is None or net_col is None:
        logger.warning("龙虎榜缺少 代码/龙虎榜净买额 列, 跳过")
        return {}
    result = {}
    for _, r in df.iterrows():
        code = str(r[code_col]).strip()
        if not code or code == "nan":
            continue
        net = _safe_float(r[net_col])  # 元
        if net == 0:
            continue  # 0 值无信号意义
        ts_code = _code_to_ts_code(code)
        reason = ""
        if reason_col:
            try:
                reason = str(r[reason_col])
            except Exception:
                reason = ""
        result[ts_code] = {"lhb_net": net, "reason": reason}
    return result


# ============================================================
# 6. 北向资金广度 (大盘环境提示, 2026-08-23 新增)
# ============================================================
def fetch_hsgt_ak():
    """沪深股通(北向)当日广度 → {"north_up": N, "north_down": N, "north_ratio": float}
    注意: 2024-08 起北向实时"成交净买额/资金净流入"停止披露(恒为0), 不可用;
          仅用"上涨数/下跌数"计算北向成分股上涨占比, 作大盘环境提示(不否决)
    失败返回 {}
    """
    if not _AK_OK:
        return {}
    try:
        df = ak.stock_hsgt_fund_flow_summary_em()
    except Exception as e:
        logger.warning("北向资金流拉取失败: %s", e)
        return {}
    if df is None or len(df) == 0:
        return {}
    dir_col = _pick_col(df, ["资金方向", "市场类型"])
    up_col = _pick_col(df, ["上涨数"])
    down_col = _pick_col(df, ["下跌数"])
    if dir_col is None or up_col is None or down_col is None:
        return {}
    north_up = 0
    north_down = 0
    for _, r in df.iterrows():
        try:
            d = str(r[dir_col]).strip()
        except Exception:
            continue
        if d != "北向":
            continue  # 仅统计北向 (南向为港股通, 忽略)
        north_up += int(_safe_float(r[up_col]))
        north_down += int(_safe_float(r[down_col]))
    total = north_up + north_down
    if total <= 0:
        return {}
    return {
        "north_up": north_up,
        "north_down": north_down,
        "north_ratio": round(north_up / total, 4),
    }


# ============================================================
# 7. 个股新闻 (消息面: 金融新闻真实性甄别, 2026-08-23)
# ============================================================
def fetch_stock_news_ak(symbol):
    """东财个股新闻 → [{"title","content","time","source","url"}]
    symbol: 6位数字代码 (如 "600519"); 失败/空返回 []
    供消息面评分: "文章来源"用于真实性甄别, "新闻标题"用于利好/利空情绪分类
    """
    if not _AK_OK:
        return []
    try:
        df = ak.stock_news_em(symbol=str(symbol).zfill(6))
    except Exception as e:
        logger.warning("个股新闻拉取失败(%s): %s", symbol, e)
        return []
    if df is None or len(df) == 0:
        return []
    t_col = _pick_col(df, ["新闻标题", "标题"])
    c_col = _pick_col(df, ["新闻内容", "内容"])
    time_col = _pick_col(df, ["发布时间"])
    src_col = _pick_col(df, ["文章来源", "来源"])
    url_col = _pick_col(df, ["新闻链接", "链接"])
    out = []
    for _, r in df.iterrows():
        try:
            out.append({
                "title": str(r[t_col]) if t_col else "",
                "content": str(r[c_col])[:500] if c_col else "",
                "time": str(r[time_col]) if time_col else "",
                "source": str(r[src_col]) if src_col else "",
                "url": str(r[url_col]) if url_col else "",
            })
        except Exception:
            continue
    return out


# ============================================================
# 8. 人气榜 (散户情绪/关注度, 2026-08-23)
# ============================================================
def fetch_hot_rank_ak():
    """东财人气榜 → {ts_code: rank} (rank=1 最热, 全市场前100)
    命中候选且排名靠前 → 散户关注度提示标签【人气榜TOP】(仅提示不否决)
    失败返回 {}
    """
    if not _AK_OK:
        return {}
    try:
        df = ak.stock_hot_rank_em()
    except Exception as e:
        logger.warning("人气榜拉取失败: %s", e)
        return {}
    if df is None or len(df) == 0:
        return {}
    code_col = _pick_col(df, ["代码"])
    rank_col = _pick_col(df, ["当前排名", "排名"])
    if code_col is None or rank_col is None:
        return {}
    result = {}
    for _, r in df.iterrows():
        code = str(r[code_col]).strip()
        if not code or code == "nan":
            continue
        ts_code = _code_to_ts_code(code)
        try:
            result[ts_code] = int(_safe_float(r[rank_col]))
        except Exception:
            continue
    return result


# ============================================================
# 9. 大宗交易 (主力/产业资本真实行为, 2026-08-23)
# ============================================================
def fetch_dzjy_ak(latest_date):
    """当日大宗交易 → {ts_code: {"count": N, "total_amt": 元, "avg_discount": float, "inst_buy": bool}}
    avg_discount: 平均折溢率 (负=折价, 正=溢价); inst_buy: 是否有"机构专用"席位买入
    非交易日/无大宗/接口失败 → 返回 {} (缺失跳过, 不误杀)
    """
    if not _AK_OK:
        return {}
    td = str(latest_date).replace("-", "")
    try:
        df = ak.stock_dzjy_mrmx(symbol="A股", start_date=td, end_date=td)
    except Exception as e:
        logger.warning("大宗交易拉取失败(%s): %s", td, e)
        return {}
    if df is None or len(df) == 0:
        return {}
    code_col = _pick_col(df, ["证券代码"])
    disc_col = _pick_col(df, ["折溢率"])
    amt_col = _pick_col(df, ["成交额"])
    buyer_col = _pick_col(df, ["买方营业部"])
    if code_col is None:
        return {}
    result = {}
    for _, r in df.iterrows():
        code = str(r[code_col]).strip()
        if not code or code == "nan":
            continue
        ts_code = _code_to_ts_code(code)
        disc = _safe_float(r[disc_col]) if disc_col else 0.0
        amt = _safe_float(r[amt_col]) if amt_col else 0.0
        buyer = str(r[buyer_col]) if buyer_col else ""
        item = result.setdefault(ts_code, {"count": 0, "total_amt": 0.0, "avg_discount": 0.0, "inst_buy": False})
        item["count"] += 1
        item["total_amt"] += amt
        item["avg_discount"] += disc
        if "机构专用" in buyer:
            item["inst_buy"] = True
    for v in result.values():
        if v["count"]:
            v["avg_discount"] = round(v["avg_discount"] / v["count"], 4)
    return result


# ============================================================
# 10. 市场广度 (每日复盘 V2.0: 涨跌家数/涨跌停/两市成交额/最高连板, 2026-08-29)
# ============================================================
def fetch_market_breadth_ak():
    """全市场广度数据 → {up_count, down_count, limit_up, limit_down,
                         total_amount, max_board}
    up/down/limit/total_amount: 腾讯全市场实时行情 (服务器验证可达, 复用 spot 缓存)
      - zdf 涨跌幅(%)  → up(>0) / down(<0) / limit_up(≥9.8) / limit_down(≤-9.8)
        (与 recommend.py 涨停近似口径一致; 20cm/30cm 板涨幅≥9.8 即计为涨停)
      - turnover 成交额(万元) 求和 → 两市成交额(亿元), 单位自适应
    max_board(最高连板): 东财涨停池 stock_zt_pool_em, 失败 → None (独立降级, 不阻断)
    失败返回 {} (调用方降级)
    """
    if not _AK_OK:
        return {}
    out = {"up_count": None, "down_count": None, "limit_up": None,
           "limit_down": None, "total_amount": None, "max_board": None}
    # 1) 全市场涨跌家数 / 涨跌停 / 两市成交额 (腾讯源, 复用 _SPOT_TX_CACHE)
    try:
        df = _fetch_spot_tx()
        if df is not None and len(df) > 0:
            pct_col = _pick_col(df, ["zdf", "涨跌幅", "pct"])
            amt_col = _pick_col(df, ["turnover", "成交额", "amount"])
            up = down = lu = ld = 0
            amt_raw = 0.0
            for _, r in df.iterrows():
                p = _safe_float(r[pct_col]) if pct_col else 0.0
                if p > 0:
                    up += 1
                elif p < 0:
                    down += 1
                if p >= 9.8:
                    lu += 1
                elif p <= -9.8:
                    ld += 1
                if amt_col:
                    amt_raw += _safe_float(r[amt_col])
            out.update({"up_count": up, "down_count": down,
                        "limit_up": lu, "limit_down": ld})
            if amt_col:
                # 单位自适应: 腾讯 turnover=万元(两市合计~1e8万); 其他源可能为 元/亿元
                if amt_raw >= 1e10:       # 元口径(两市~1e12) → 亿元
                    out["total_amount"] = round(amt_raw / 1e8, 1)
                elif amt_raw >= 1e6:      # 万元口径(两市~1e8) → 亿元
                    out["total_amount"] = round(amt_raw / 1e4, 1)
                else:                     # 亿元口径
                    out["total_amount"] = round(amt_raw, 1)
    except Exception as e:
        logger.warning("市场广度-全市场统计失败: %s", e)
    # 2) 最高连板: 东财涨停池 (独立降级, 失败仅该字段缺失)
    try:
        today = datetime.now().strftime("%Y%m%d")
        zt = ak.stock_zt_pool_em(date=today)
        if zt is not None and len(zt) > 0:
            lb_col = _pick_col(zt, ["连板数", "连板"])
            if lb_col:
                vals = pd.to_numeric(zt[lb_col], errors="coerce").dropna()
                if len(vals):
                    out["max_board"] = int(vals.max())
    except Exception as e:
        logger.warning("市场广度-涨停池失败(最高连板缺失): %s", e)
    return out


# ============================================================
# 12. 个股财务指标准确版 (同花顺, 2026-08-30 新增接入)
# ============================================================
_THS_FINA_CACHE = {}
_THS_FINA_CACHE_TS = 0
_THS_FINA_TTL = 3600  # 1小时内复用(财务数据低频变化)


def fetch_fina_ths_ak(symbol):
    """同花顺财务分析指标 (替代 fina_indicator, 服务器实测 0.4s/只 稳定可达)
    返回 {ts_code: {end_date, net_profit, netprofit_yoy, dt_netprofit_yoy,
                    n_income_attr_parent, debt_to_assets, total_revenue, revenue_yoy,
                    gross_margin, net_margin, roe, roic, ocf_ps, bps}}
    字段映射 (同花顺列名 → 兼容 fetch_fina_f10_em 结构):
      ?: 返回 dict; 失败返回 {}
    """
    if not _AK_OK:
        return {}
    sym = str(symbol).split(".")[0].zfill(6)
    now = time.time()
    hit = _THS_FINA_CACHE.get(sym)
    if hit is not None and now - _THS_FINA_CACHE_TS < _THS_FINA_TTL:
        ts_code = _code_to_ts_code(sym)
        return {ts_code: hit}
    try:
        # 起始年用去年: 同花顺接口 start_year=当前年 只返回当年少量列(资产负债率等全 None);
        # 用去年 → 返回该年至今全部报告期完整列, 最新披露期仍在首行 (2026-08-30 服务器实测)
        df = ak.stock_financial_analysis_indicator(symbol=sym, start_year=str(datetime.now().year - 1))
    except Exception as e:
        logger.warning("同花顺财务指标拉取失败(%s): %s", sym, str(e)[:60])
        return {}
    if df is None or len(df) == 0:
        return {}
    # 同花顺接口按报告期升序排列(首行=最早, 末行=最新, 2026-08-30 实测)
    # 因此最新披露期在末行 iloc[-1]; 此接口销售毛利率列恒 NaN(无该数据, 由 F10 提供)
    row = df.iloc[-1]  # 最新一期(末行)
    def _f(v):
        try:
            f = float(v)
            if f != f or f in (float("inf"), float("-inf")):
                return None
            return round(f, 2)
        except (TypeError, ValueError):
            return None
    # 取值 helper: _pick_idx 返回的是"列名", 必须先 row[列名] 取值再转 float
    # (2026-08-30 修复: 原先 _f(_pick_idx(...)) 把列名字符串当数值→恒 None)
    def _cell(series, candidates):
        col = _pick_idx(series, candidates)
        if col is None:
            return None
        try:
            return _f(series[col])
        except Exception:
            return None
    # 资产负债率/净利率/毛利率/ROE/每股现金流/每股净资产(调整后)
    debt   = _cell(row, ["资产负债率(%)"])
    net_m  = _cell(row, ["销售净利率(%)"])
    gross  = _cell(row, ["销售毛利率(%)"])
    roe    = _cell(row, ["加权净资产收益率(%)", "净资产收益率(%)"])
    ocf    = _cell(row, ["每股经营性现金流(元)"])
    bps    = _cell(row, ["每股净资产_调整后(元)"])
    np_yoy = _cell(row, ["净利润增长率(%)"])
    rev_yoy = _cell(row, ["主营业务收入增长率(%)"])
    deduct = _cell(row, ["扣除非经常性损益后的净利润(元)"])
    main_profit = _cell(row, ["主营业务利润(元)"])
    out = {
        "end_date": "",  # 同花顺返回日期列在 [0], 单独取
        "net_profit": deduct,        # 无总净利绝对数, 用扣非净利(真实值, 标注)
        "netprofit_yoy": np_yoy,
        "dt_netprofit_yoy": None,
        "n_income_attr_parent": deduct,
        "debt_to_assets": debt,
        "total_revenue": None,
        "revenue_yoy": rev_yoy,
        "gross_margin": gross,
        "net_margin": net_m,
        "roe": roe,
        "roic": None,                # 同花顺此接口无 ROIC
        "ocf_ps": ocf,
        "bps": bps,
    }
    try:
        d_col = _pick_col(df, ["日期"])
        if d_col is not None:
            out["end_date"] = str(row[d_col]).replace("-", "")[:8]
    except Exception:
        pass
    ts_code = _code_to_ts_code(sym)
    _THS_FINA_CACHE[sym] = dict(out)
    _THS_FINA_CACHE_TS = now
    if len(_THS_FINA_CACHE) > 2000:
        _THS_FINA_CACHE.clear()
    return {ts_code: out}


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

    print(f"\n=== 3. 实时行情 ===")
    db = fetch_daily_basic_ak("20260808")
    print(f"  {'✅ ' + str(len(db)) + ' 只' if db else '❌ 失败'}")
    # 取茅台校验
    mt_db = db.get("600519.SH") if db else None
    if mt_db:
        print(f"  茅台: PE={mt_db['pe_ttm']}, 流通市值={mt_db['circ_mv']/1e4:.1f}亿, 换手={mt_db['turnover_rate']}, 量比={mt_db['volume_ratio']}")

    print(f"\n=== 4. 大盘资金流向 ===")
    mff = fetch_market_fund_flow_ak(n_days=5)
    print(f"  {'✅ ' + str(len(mff)) + ' 日' if mff else '❌ 失败'}")
    for td, v in sorted(mff.items()):
        print(f"  {td}: 主力净流入 {v['main_net']/1e8:.2f}亿 ({v['main_pct']:.2f}%)")
