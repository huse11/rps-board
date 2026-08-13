# -*- coding: utf-8 -*-
"""
每日推荐股票打分模块 v3.0 —— 五层漏斗筛选 + 综合评分
整体架构(漏斗式逐层过滤, 候选股严格限定在 RPS 入选板块成分股范围内):
  第一层 大盘环境: 上证指数/创业板指 vs 20日均线, 不合格直接终止选股
  第二层 候选池:   100% 来自入选板块成分股, 每股统计归属强势板块数(多题材共振)
  第三层 基本面:   硬性一票否决 (ST/停牌/低价/小市值/PE异常/亏损/高负债)
  第四层 资金面:   换手率区间/量比/成交额/主力资金方向 全部核心项必达
  第五层 技术共振: 7项独立指标达标 ≥3 项判为通过
  综合评分: 板块热度40(权重最高) + 基本面20 + 资金面20 + 技术面20 = 100分
  按综合得分降序输出, 全部通过五层筛选的股票进入推荐列表
- 所有阈值集中在 CONFIG 配置字典, 无需改代码即可调整选股严格度
- 数据策略(真实+降级):
  * index_daily(指数日线): 真实拉取, 失败默认大盘安全(避免误否决)
  * fina_indicator(净利润/资产负债率): 按 period 批量, 失败降级 PE>0 / 东财替代
  * moneyflow(主力净流入): 按 trade_date 批量, 失败降级近似估算 / AKShare / 东财替代
  * moneyflow_hsgt(北向资金): 失败跳过(加分项, 不影响否决)
- 输出: static/recommendations.json (schema_version=3)

重要免责声明: 量化条件仅为技术逻辑参考,不构成任何投资建议;
股市波动极大,中短线亏损概率高,严禁杠杆借贷炒股;
本工具仅做个人研究使用,严禁对外收费荐股牟利。
"""
import json
import math
import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

import rps_calc
from rps_calc import pool, STATIC_DIR
import predict  # 预测化模块: 6 维度概率 + 置信度 + 止损位 + 每日校准

# AKShare 免费数据源 (腾讯/东财, 替代 Tushare premium + 手动东财 push2)
# 未安装时 _AK_OK=False, 各 fetch_*_ak 返回 None/{}, 自动降级到下方东财手动接口
try:
    import akshare_adapter as _ak
    _AK_OK = getattr(_ak, "_AK_OK", False)
except Exception:
    _ak = None
    _AK_OK = False

# 预建个股日线索引 {ts_code: DataFrame[date,open,high,low,close,vol,amount] 按date升序}
# 由 recommend_stocks 在打分前构建, 使 _get_ohlc_series/_get_recent_vols 等从 O(n_days×全表扫描) → O(1)查找
# 未构建时(单元测试) 回退到逐日扫描 daily_map 的慢路径
_DAILY_BY_CODE = None


def build_daily_by_code(daily_map, codes, latest_date, n_days=80):
    """预建 {ts_code: 个股日线索引DataFrame}, 仅含候选 codes, 取近 n_days 日
    用 pandas 向量化一次性构建, 替代逐股逐日扫描 (提速~100倍)
    返回 {ts_code: DataFrame(date,open,high,low,close,vol,amount) 按date升序}
    """
    codes_set = set(codes)
    avail = sorted([d for d in daily_map.keys() if d <= latest_date])[-n_days:]
    frames = []
    for date in avail:
        df = daily_map[date]
        sub = df[df["ts_code"].isin(codes_set)].copy()
        if len(sub) == 0:
            continue
        sub["_date"] = date
        keep = ["_date", "ts_code", "open", "high", "low", "close", "vol", "amount"]
        frames.append(sub[[c for c in keep if c in sub.columns]])
    if not frames:
        return {}
    all_df = pd.concat(frames, ignore_index=True)
    result = {}
    for code, g in all_df.groupby("ts_code"):
        g = g.sort_values("_date").reset_index(drop=True)
        g = g.rename(columns={"_date": "date"})
        result[code] = g
    return result

REC_FILE = STATIC_DIR / "recommendations.json"
# 五层漏斗筛选 + 综合评分 — 全部阈值可配置(无需改代码即可调整选股严格度)
CONFIG = {
    # 第一层: 大盘环境全局校验
    "market": {
        "index_codes": ["000001.SH", "399006.SZ"],  # 上证指数 + 创业板指
        "index_names": {"000001.SH": "上证指数", "399006.SZ": "创业板指"},
        "ma_period": 20,           # 指数 20 日均线
        "need_above": 1,           # 至少 1 个指数站稳 20 日线 (可配置为 2)
        "default_safe": True,      # 指数数据完全缺失时默认安全(避免接口故障误否决全市场)
        "check_sentiment": True,   # 是否做市场情绪校验(涨跌比/跌停数)
        "limit_down_max": 50,      # 跌停家数上限(超过判为极端普跌)
        "advance_ratio_min": 0.30, # 上涨家数占比下限
    },
    # 第二层: 候选股票池 (核心约束 = RPS 入选板块成分股)
    "pool": {
        "min_sector_count": 1,     # 至少属于 1 个强势板块
    },
    # 第三层: 基本面排雷 (硬性一票否决)
    "fundamental": {
        "min_price": 2.0,          # 最低收盘价(低价仙股剔除)
        "min_circ_mv": 10e8,       # 最低流通市值 10亿 (daily_basic.circ_mv 单位万元 → 10亿=10万万元)
        "max_pe": 200,             # PE-TTM 上限(远超行业上限剔除)
        "max_debt_ratio": 80,      # 资产负债率上限 % (远超合理区间剔除)
        "max_pledge_ratio": 50,    # 股权质押比例上限 % (数据缺失不剔除)
        "max_goodwill_ratio": 30,  # 商誉占净资产上限 % (数据缺失不剔除)
    },
    # 第四层: 资金面达标校验 (全部核心项必达)
    "capital": {
        "turnover_min": 2.0,       # 换手率下限 % (保证有资金关注)
        "turnover_max": 25.0,      # 换手率上限 % (规避出货风险)
        "vol_ratio_min": 1.1,      # 量比下限 (近期量能较前期放大)
        "amount_min": 5e7,         # 单日成交额下限 5000万 (保证流动性)
    },
    # 第五层: 多技术指标共振
    "technical": {
        "hit_min": 3,              # 达标指标数 ≥3 项判为共振通过
        "rps50_threshold": 80,     # 个股 RPS50 中期强势阈值
    },
    # 综合评分权重 (总分 100)
    "score": {
        "sector": 40,              # 板块热度分(权重最高, 归属强势板块数)
        "fundamental": 20,         # 基本面分
        "capital": 20,             # 资金面分
        "technical": 20,           # 技术面分
        "min_score": 70,           # 综合得分门槛: <70 的股票不进入推荐列表(前端不显示)
    },
}
INDEX_CODES = CONFIG["market"]["index_codes"]
INDEX_CACHE_DIR = STATIC_DIR / "index_cache"
FINA_CACHE_FILE = STATIC_DIR / "fina_indicator_cache.json"
MFLOW_CACHE_FILE = STATIC_DIR / "moneyflow_cache.json"
HSGT_CACHE_FILE = STATIC_DIR / "moneyflow_hsgt_cache.json"


# ============================================================
# 1. 基础排雷 (前置强制剔除, 所有策略共用)
# ============================================================

def is_st_stock(name):
    """名称含 ST/*ST/退 → 强制剔除"""
    if not name:
        return False
    n = str(name)
    return ("ST" in n) or ("*ST" in n) or ("退" in n)


def is_new_stock(list_date, latest_date, min_trade_days=60):
    """次新股: 上市不足 min_trade_days 个交易日(约1.4倍自然日换算)"""
    if not list_date or not latest_date:
        return False  # 无上市日期数据, 不剔除(避免误杀)
    try:
        ld = datetime.strptime(str(list_date)[:8], "%Y%m%d")
        td = datetime.strptime(str(latest_date)[:8], "%Y%m%d")
        return (td - ld).days < int(min_trade_days * 1.4)
    except Exception:
        return False


def _get_recent_vols(daily_map, code, latest_date, n=6):
    """取近n日的成交量(手)序列 (优先用预建索引)"""
    if _DAILY_BY_CODE is not None and code in _DAILY_BY_CODE:
        g = _DAILY_BY_CODE[code]
        sub = g[g["date"] <= latest_date].tail(n)
        vols = []
        for _, r in sub.iterrows():
            v = r.get("vol")
            if v is not None and not pd.isna(v):
                vols.append(float(v))
        return vols
    avail = sorted([d for d in daily_map.keys() if d <= latest_date])[-n:]
    vols = []
    for date in avail:
        df = daily_map[date]
        row = df[df["ts_code"] == code]
        if len(row) == 0:
            continue
        v = row.iloc[0].get("vol")
        if v is not None and not pd.isna(v):
            vols.append(float(v))
    return vols


# ============================================================
# 4. 技术指标计算 (第五层技术共振复用)
# ============================================================

def calc_ma_series(closes, n):
    """计算 MA n 期均线 (返回最近值或 None)"""
    if len(closes) < n:
        return None
    return float(np.mean(closes[-n:]))


def calc_macd(closes):
    """计算 MACD: 返回 (dif, dea, hist, prev_dif, prev_dea) 用于判断金叉
    用 pandas ewm 实现 EMA12/EMA26/DEA9
    """
    if len(closes) < 30:
        return None
    s = pd.Series(closes, dtype=float)
    ema12 = s.ewm(span=12, adjust=False).mean()
    ema26 = s.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = (dif - dea) * 2
    if len(dif) < 2:
        return None
    return {
        "dif": float(dif.iloc[-1]),
        "dea": float(dea.iloc[-1]),
        "hist": float(hist.iloc[-1]),
        "prev_dif": float(dif.iloc[-2]),
        "prev_dea": float(dea.iloc[-2]),
    }


def calc_kdj(highs, lows, closes, n=9):
    """计算 KDJ: 返回 (k, d, j, prev_j) 用于判断低位金叉
    RSV = (close - min(low,n)) / (max(high,n) - min(low,n)) * 100
    K = SMA(RSV, 3, 1)  (即 EMA alpha=1/3)
    D = SMA(K, 3, 1)
    J = 3K - 2D
    """
    if len(closes) < n + 3:
        return None
    rsv_arr = []
    for i in range(n - 1, len(closes)):
        h = highs[i - n + 1: i + 1]
        l = lows[i - n + 1: i + 1]
        c = closes[i]
        hh = max(h)
        ll = min(l)
        if hh == ll:
            rsv_arr.append(50.0)
        else:
            rsv_arr.append((c - ll) / (hh - ll) * 100)

    # SMA(X, N, 1) = (prev*2 + today) / 3 (Tushare 通达信 SMA(M=1))
    k = 50.0
    d = 50.0
    k_arr = []
    d_arr = []
    for rsv in rsv_arr:
        k = (k * 2 + rsv) / 3
        d = (d * 2 + k) / 3
        k_arr.append(k)
        d_arr.append(d)
    j = 3 * k - 2 * d
    prev_j = 3 * k_arr[-2] - 2 * d_arr[-2] if len(k_arr) >= 2 else j
    return {"k": float(k), "d": float(d), "j": float(j), "prev_j": float(prev_j)}


def calc_obv(closes, vols):
    """OBV 能量潮: 上涨日累加量, 下跌日减量
    返回 (obv_today, obv_3d_ago) 用于判断是否同步向上
    """
    if len(closes) < 4:
        return None
    obv = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + vols[i])
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - vols[i])
        else:
            obv.append(obv[-1])
    return {"today": float(obv[-1]), "prev_3d": float(obv[-4]) if len(obv) >= 4 else float(obv[0])}


def calc_obv_series(closes, vols, n=60):
    """OBV 能量潮序列(用于顶背离检测): 返回 {today, max_60d, price_max_60d}
    顶背离 = 股价创 n 日新高 但 OBV 未创 n 日新高(量价背离, 见顶信号)
    """
    if len(closes) < 4 or len(closes) != len(vols):
        return None
    obv = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + vols[i])
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - vols[i])
        else:
            obv.append(obv[-1])
    window = obv[-n:] if len(obv) >= n else obv
    price_window = closes[-n:] if len(closes) >= n else closes
    return {
        "today": float(obv[-1]),
        "max_n": float(max(window)),
        "price_max_n": float(max(price_window)),
    }


def calc_boll(closes, n=20, k=2):
    """BOLL 布林带: 中轨=MA(n), 上轨=MA+k*std, 下轨=MA-k*std
    返回 {mid, upper, lower} 最近值; 数据不足返回 None
    std 用 ddof=0(总体标准差, 对齐通达信 BOLL)
    """
    if len(closes) < n:
        return None
    s = pd.Series(closes, dtype=float)
    ma = s.rolling(n).mean()
    std = s.rolling(n).std(ddof=0)
    mid = ma.iloc[-1]
    if pd.isna(mid) or pd.isna(std.iloc[-1]):
        return None
    return {"mid": float(mid), "upper": float(mid + k * std.iloc[-1]), "lower": float(mid - k * std.iloc[-1])}


def calc_cci(highs, lows, closes, n=14):
    """CCI 商品路径指标:
    TP=(H+L+C)/3; MA=TP的n日MA; MD=TP的n日平均绝对偏差
    CCI = (TP - MA) / (0.015 * MD)
    返回 {cci, prev_cci} 用于判断 >+100 / 拐头; 数据不足返回 None
    """
    if len(closes) < n + 1:
        return None
    tp = pd.Series([(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)], dtype=float)
    ma = tp.rolling(n).mean()
    md = tp.rolling(n).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    if pd.isna(ma.iloc[-1]) or pd.isna(md.iloc[-1]) or md.iloc[-1] == 0:
        return None
    cci_today = (tp.iloc[-1] - ma.iloc[-1]) / (0.015 * md.iloc[-1])
    cci_prev = (tp.iloc[-2] - ma.iloc[-2]) / (0.015 * md.iloc[-2]) if not pd.isna(ma.iloc[-2]) and not pd.isna(md.iloc[-2]) and md.iloc[-2] != 0 else cci_today
    return {"cci": float(cci_today), "prev_cci": float(cci_prev)}


def calc_rsi(closes, n=6):
    """RSI 相对强弱指标(Wilder 平滑 = EMA alpha=1/n):
    delta=close.diff(); gain=max(delta,0); loss=max(-delta,0)
    RSI = 100 - 100/(1 + SMMA(gain)/SMMA(loss))
    返回 {rsi, prev_rsi} 用于判断拐头; 数据不足返回 None
    """
    if len(closes) < n + 1:
        return None
    s = pd.Series(closes, dtype=float)
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder 平滑 = EMA(span 相当于 alpha=1/n)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.fillna(100.0)  # loss=0 时 RSI=100(全涨)
    if len(rsi) < 2 or pd.isna(rsi.iloc[-1]):
        return None
    return {"rsi": float(rsi.iloc[-1]), "prev_rsi": float(rsi.iloc[-2]) if not pd.isna(rsi.iloc[-2]) else float(rsi.iloc[-1])}


def _get_ohlc_series(daily_map, code, latest_date, n=80):
    """取近 n 日的 close/high/low/vol 序列(按日期升序)
    优先用预建索引 _DAILY_BY_CODE (O(1)查找); 未构建时回退逐日扫描(慢, 测试用)
    """
    if _DAILY_BY_CODE is not None and code in _DAILY_BY_CODE:
        g = _DAILY_BY_CODE[code]
        sub = g[g["date"] <= latest_date].tail(n)
        closes, highs, lows, vols = [], [], [], []
        for _, r in sub.iterrows():
            c = r.get("close")
            if c is None or pd.isna(c):
                continue
            closes.append(float(c))
            highs.append(float(r.get("high") or c))
            lows.append(float(r.get("low") or c))
            vols.append(float(r.get("vol") or 0))
        return closes, highs, lows, vols
    # 慢路径: 逐日扫描 daily_map (单元测试用)
    avail = sorted([d for d in daily_map.keys() if d <= latest_date])[-n:]
    closes, highs, lows, vols = [], [], [], []
    for date in avail:
        df = daily_map[date]
        row = df[df["ts_code"] == code]
        if len(row) == 0:
            continue
        r = row.iloc[0]
        c = r.get("close")
        if c is None or pd.isna(c):
            continue
        closes.append(float(c))
        highs.append(float(r.get("high") or c))
        lows.append(float(r.get("low") or c))
        vols.append(float(r.get("vol") or 0))
    return closes, highs, lows, vols


# ============================================================
# 5b. 东方财富免费数据源替代层 (Tushare premium 失败时启用, 0成本)
# ============================================================
import urllib.request as _urllib

_EM_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
           "Referer": "https://quote.eastmoney.com/"}
_EM_KLINE_DOMAINS = ["push2his.eastmoney.com", "1.push2his.eastmoney.com",
                     "2.push2his.eastmoney.com", "push2delay.eastmoney.com"]


def _em_get_json(url, timeout=12):
    req = _urllib.Request(url, headers=_EM_UA)
    with _urllib.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _em_safe_float(val):
    """安全转 float: '-'/'None'/空 → 0.0 (东财停牌/无交易返回'-')"""
    if val is None or val == "-" or val == "":
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _em_secid_for_index(index_code):
    """指数 ts_code → 东财 secid (000001.SH→1.000001, 000300.SH→1.000300)"""
    symbol, _ = index_code.split(".")
    return "1." + symbol


def fetch_index_daily_em(index_code, start, end):
    """东财指数K线 (替代 index_daily)
    返回 DataFrame(date,open,high,low,close,vol,amount) 与 Tushare 同构
    域名池轮换: push2his → 1/2.push2his → push2delay
    """
    secid = _em_secid_for_index(index_code)
    beg = start.replace("-", "")
    end_d = end.replace("-", "")
    d = None
    for dom in _EM_KLINE_DOMAINS:
        url = (f"https://{dom}/api/qt/stock/kline/get"
               f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
               f"&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=0"
               f"&beg={beg}&end={end_d}")
        try:
            d = _em_get_json(url, timeout=10)
            if d:
                break
        except Exception:
            continue
    if not d:
        return None
    klines = (d.get("data") or {}).get("klines") or []
    if not klines:
        return None
    rows = []
    for line in klines:
        p = line.split(",")
        if len(p) < 7:
            continue
        rows.append({"date": p[0].replace("-", ""), "open": float(p[1]), "close": float(p[2]),
                     "high": float(p[3]), "low": float(p[4]), "vol": float(p[5]), "amount": float(p[6])})
    return pd.DataFrame(rows)


def fetch_moneyflow_em(latest_date, page_size=100, max_pages=80, codes=None):
    """东财个股资金流向排行 (替代 moneyflow), 分页拉取
    返回 {ts_code: [{"trade_date":..,"buy_sm_amount":..,"sell_sm_amount":..}]} (元)
    f62=主力净流入(元): >0 设为 buy_sm_amount, <0 设为 sell_sm_amount(绝对值)
    codes: 可选候选股; 传入时仅保留候选股并找齐即提前退出
           (按 f62 净流入降序排列, 候选强资金股多在前几页, AKShare腾讯源通常1调用覆盖此为兜底)
    """
    codes_set = set(codes) if codes else None
    result = {}
    mkt_map = {0: "SZ", 1: "SH", 83: "BJ"}
    for pn in range(1, max_pages + 1):
        url = (f"https://push2.eastmoney.com/api/qt/clist/get"
               f"?pn={pn}&pz={page_size}&po=1&np=1&fltt=2&invt=2&fid=f62"
               f"&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
               f"&fields=f12,f13,f62")
        try:
            d = _em_get_json(url, timeout=10)
        except Exception:
            # 域名失败,重试一次
            try:
                d = _em_get_json(url.replace("push2.", "push2delay."), timeout=10)
            except Exception:
                break
        diff = (d.get("data") or {}).get("diff") or []
        if not diff:
            break
        for s in diff:
            code = str(s.get("f12") or "")
            if not code:
                continue
            mkt = mkt_map.get(s.get("f13"))
            if not mkt:
                if code.startswith(("4", "8", "920")):
                    mkt = "BJ"
                elif code.startswith("6"):
                    mkt = "SH"
                else:
                    mkt = "SZ"
            ts_code = f"{code}.{mkt}"
            # 候选股感知: 非候选股跳过
            if codes_set is not None and ts_code not in codes_set:
                continue
            net = _em_safe_float(s.get("f62"))
            # 拆成 buy/sell: 净流入>0→买入方, <0→卖出方(绝对值)
            buy = net if net > 0 else 0.0
            sell = abs(net) if net < 0 else 0.0
            result[ts_code] = [{"trade_date": latest_date, "buy_sm_amount": buy, "sell_sm_amount": sell}]
        # 候选股全部找齐 → 提前退出
        if codes_set is not None and len(result) >= len(codes_set):
            break
        if len(diff) < page_size:
            break  # 最后一页
    return result


def fetch_hsgt_em(latest_date, n_days=5):
    """东财沪深港通历史 (替代 moneyflow_hsgt)
    返回 {trade_date: {"buy_value": 北向净流入元}}
    """
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
           "?reportName=RPT_MUTUAL_DEAL_HISTORY"
           "&columns=MUTUAL_TYPE,TRADE_DATE,FUND_INFLOW,BUY_AMT,SELL_AMT"
           f"&pageSize={n_days * 2}&pageNo=1"
           "&sortColumns=TRADE_DATE&sortTypes=-1"
           "&filter=(MUTUAL_TYPE=%22001%22)(MUTUAL_TYPE=%22003%22)")
    # 上面的 filter 语法可能不支持 OR, 用两次请求分别取沪股通+深股通
    cache = {}
    for mt in ("001", "003"):  # 001=沪股通 003=深股通
        u = (url.split("&filter=")[0] +
             f"&filter=(MUTUAL_TYPE=%22{mt}%22)")
        try:
            d = _em_get_json(u, timeout=12)
        except Exception:
            continue
        result = d.get("result")
        if not result:
            continue
        for row in (result.get("data") or []):
            td_raw = str(row.get("TRADE_DATE") or "")[:10].replace("-", "")
            if not td_raw:
                continue
            inflow = row.get("FUND_INFLOW")
            # FUND_INFLOW 为 null 时用 BUY_AMT - SELL_AMT 兜底
            if inflow is None:
                buy = row.get("BUY_AMT")
                sell = row.get("SELL_AMT")
                if buy is not None and sell is not None:
                    inflow = float(buy) - float(sell)
                else:
                    continue
            # 沪+深累加为北向总净流入
            cache.setdefault(td_raw, {"buy_value": 0.0})
            cache[td_raw]["buy_value"] += float(inflow)
    return cache


def fetch_fina_em(latest_date, codes=None):
    """东财财务指标 (替代 fina_indicator), 批量拉取
    RPT_LICO_FN_CPD: PARENT_NETPROFIT(归母净利润), BASIC_EPS, DEDUCT_BASIC_EPS(扣非EPS)
    返回 {ts_code: {end_date, net_profit, n_income_attr_parent, debt_to_assets}}
    扣非净利润 ≈ DEDUCT_BASIC_EPS × (PARENT_NETPROFIT/BASIC_EPS); 资产负债率缺失=None
    codes: 可选候选股 ts_code 集合; 传入时仅保留候选股并分页找齐即提前退出
           (评分循环只处理 RPS 入选板块成分股, 拉全市场5000只财务是浪费, 候选股通常数百只)
    """
    codes_set = set(codes) if codes else None
    cache = {}
    # 分页拉取最新一期(ISNEW=1)全市场, pageSize=500 适应 API 可能的返回上限
    for pn in range(1, 40):
        url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
               "?reportName=RPT_LICO_FN_CPD"
               "&columns=SECURITY_CODE,SECURITY_NAME_ABBR,REPORTDATE,PARENT_NETPROFIT,BASIC_EPS,DEDUCT_BASIC_EPS,ISNEW"
               "&pageSize=500&pageNo=" + str(pn) +
               "&sortColumns=REPORTDATE&sortTypes=-1"
               "&filter=(ISNEW=%221%22)")
        try:
            d = _em_get_json(url, timeout=15)
        except Exception:
            break
        result = d.get("result")
        if not result:
            break
        data = result.get("data") or []
        if not data:
            break
        for row in data:
            code = str(row.get("SECURITY_CODE") or "")
            if not code:
                continue
            # 代码→ts_code (东财不返市场,按前缀推断)
            if code.startswith("6") or code.startswith("9"):
                ts_code = code + ".SH"
            else:
                ts_code = code + ".SZ"
            # 候选股感知: 非候选股跳过 (评分只处理 RPS 入选板块成分股, 全市场财务数据是浪费)
            if codes_set is not None and ts_code not in codes_set:
                continue
            np_val = row.get("PARENT_NETPROFIT")
            eps = row.get("BASIC_EPS")
            deps = row.get("DEDUCT_BASIC_EPS")
            # 推算扣非净利润: shares = net_profit / eps; deducted = deduct_eps × shares
            npp = None
            if np_val is not None and eps and abs(float(eps)) > 0.001:
                try:
                    npp = float(deps) * (float(np_val) / float(eps)) if deps is not None else None
                except Exception:
                    npp = None
            cache[ts_code] = {
                "end_date": str(row.get("REPORTDATE") or "")[:10].replace("-", ""),
                "net_profit": float(np_val) if np_val is not None else None,
                "n_income_attr_parent": npp,
                "debt_to_assets": None,  # 东财此报表无资产负债率, 降级跳过 debt 检查
            }
        # 候选股全部找齐 → 提前退出外层分页 (避免拉完全市场 ~10 页, 候选股通常散布在前几页)
        if codes_set is not None and len(cache) >= len(codes_set):
            break
        if len(data) < 500:
            break
    return cache


def fetch_daily_basic_em(latest_date, page_size=100, max_pages=80, codes=None):
    """东财实时行情指标 (替代 daily_basic), 分页拉取
    返回 {ts_code: {pe_ttm, circ_mv(万元), turnover_rate, volume_ratio}}
    f8=换手率% f10=量比 f9=市盈率(动态) f21=流通市值(元→万元)
    codes: 可选候选股 ts_code 集合; 传入时仅保留候选股并找齐即提前退出 (AKShare 通常1调用覆盖, 此为兜底)
    """
    codes_set = set(codes) if codes else None
    result = {}
    mkt_map = {0: "SZ", 1: "SH", 83: "BJ"}
    for pn in range(1, max_pages + 1):
        url = (f"https://push2.eastmoney.com/api/qt/clist/get"
               f"?pn={pn}&pz={page_size}&po=1&np=1&fltt=2&invt=2"
               f"&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
               f"&fields=f12,f13,f8,f10,f9,f21")
        try:
            d = _em_get_json(url, timeout=10)
        except Exception:
            try:
                d = _em_get_json(url.replace("push2.", "push2delay."), timeout=10)
            except Exception:
                break
        diff = (d.get("data") or {}).get("diff") or []
        if not diff:
            break
        for s in diff:
            code = str(s.get("f12") or "")
            if not code:
                continue
            mkt = mkt_map.get(s.get("f13"))
            if not mkt:
                if code.startswith(("4", "8", "920")):
                    mkt = "BJ"
                elif code.startswith("6"):
                    mkt = "SH"
                else:
                    mkt = "SZ"
            ts_code = f"{code}.{mkt}"
            # 候选股感知: 非候选股跳过
            if codes_set is not None and ts_code not in codes_set:
                continue
            # f9 市盈率 "-" 表示亏损 → None; 其他字段用 _em_safe_float
            pe_raw = s.get("f9")
            pe = None if pe_raw in (None, "-", "") else _em_safe_float(pe_raw)
            circ_mv_raw = s.get("f21")
            circ_mv_wan = _em_safe_float(circ_mv_raw) / 1e4 if circ_mv_raw not in (None, "-", "") else None
            result[ts_code] = {
                "pe_ttm": pe,
                "circ_mv": circ_mv_wan,
                "turnover_rate": _em_safe_float(s.get("f8")) if s.get("f8") not in (None, "-", "") else None,
                "volume_ratio": _em_safe_float(s.get("f10")) if s.get("f10") not in (None, "-", "") else None,
            }
        # 候选股全部找齐 → 提前退出
        if codes_set is not None and len(result) >= len(codes_set):
            break
        if len(diff) < page_size:
            break
    return result


# ============================================================
# 6. v2 数据获取层 (真实+降级, 缓存优先, 东财替代)
# ============================================================

def fetch_index_daily(index_codes, trade_days):
    """拉取指数日线(上证/沪深300), 缓存 static/index_cache/{code}.json
    返回 {index_code: DataFrame(date,open,high,low,close,vol,amount)}
    失败返回 {} 由 check_market_env 降级为默认安全(避免误否决)
    """
    INDEX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # 指数取更长历史(≥200自然日≈140交易日), 确保 MA60 可算 (AKShare可能只返近期数据)
    start = (datetime.now() - timedelta(days=200)).strftime("%Y%m%d")
    end = trade_days[-1] if trade_days else datetime.now().strftime("%Y%m%d")
    result = {}
    for code in index_codes:
        cache_path = INDEX_CACHE_DIR / f"{code}.json"
        if cache_path.exists():
            try:
                df = pd.read_json(cache_path, dtype={"ts_code": str})
                # 缓存需≥60条才可用(MA60计算需要); 且必须已含最新交易日, 否则重新拉取当日数据
                if len(df) >= 60:
                    col = "trade_date" if "trade_date" in df.columns else ("date" if "date" in df.columns else None)
                    last_date = str(df[col].max()) if col else ""
                    if last_date >= end:
                        result[code] = df
                        continue
            except Exception:
                pass
        try:
            df = pool.call_any("index_daily", ts_code=code, start_date=start, end_date=end)
            if df is not None and len(df) > 0:
                df.to_json(cache_path, orient="records", force_ascii=False)
                result[code] = df
                print(f"  ✅ index_daily {code}: {len(df)} 条")
                continue
        except Exception as e:
            print(f"  ⚠️ index_daily {code} Tushare失败: {str(e)[:50]}, 尝试 AKShare/东财替代...")
        # AKShare 替代 (腾讯/东财kline, 服务器可达)
        if _AK_OK:
            try:
                df = _ak.fetch_index_daily_ak(code, start, end)
                if df is not None and len(df) > 0:
                    df.to_json(cache_path, orient="records", force_ascii=False)
                    result[code] = df
                    print(f"  ✅ index_daily {code} (AKShare): {len(df)} 条")
                    continue
            except Exception as e2:
                print(f"  ⚠️ index_daily {code} AKShare失败: {str(e2)[:50]}")
        # 东财手动替代 (最后兜底)
        try:
            df = fetch_index_daily_em(code, start, end)
            if df is not None and len(df) > 0:
                df.to_json(cache_path, orient="records", force_ascii=False)
                result[code] = df
                print(f"  ✅ index_daily {code} (东财替代): {len(df)} 条")
                continue
        except Exception as e3:
            print(f"  ⚠️ index_daily {code} 东财替代也失败: {str(e3)[:50]}")
        print(f"  ⚠️ index_daily {code} 全失败(降级默认安全)")
    return result


def fetch_fina_indicator_batch(latest_date, codes=None):
    """fina_indicator 按 period 批量拉取(净利润/扣非/资产负债率)
    缓存 static/fina_indicator_cache.json: {ts_code: {end_date, net_profit, n_income_attr_parent, debt_to_assets}}
    失败返回 {} 由 screen_fundamental 降级 PE>0
    codes: 可选候选股; 传入时缓存命中后过滤到候选股, 未命中时 Tushare/东财仅拉候选股 (评分只处理 RPS 入选板块成分股)
    """
    codes_set = set(codes) if codes else None
    if FINA_CACHE_FILE.exists():
        try:
            with open(FINA_CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
            if cache:
                # 候选股感知: 缓存命中后过滤到候选股 (减少内存, 避免加载全市场5000只财务)
                if codes_set:
                    cache = {c: v for c, v in cache.items() if c in codes_set}
                # 旧缓存缺 netprofit_yoy 字段(周度深度排雷需要) → 视为过期重建
                if all("netprofit_yoy" in v for v in cache.values()):
                    return cache
                cache = {}
        except Exception:
            pass
    cache = {}
    # 尝试最近 3 个季度 period(季报披露有滞后)
    yr = int(latest_date[:4])
    periods = []
    for offset in range(4):
        y = yr - offset
        for q in ("1231", "0930", "0630", "0331"):
            periods.append(f"{y}{q}")
    periods = periods[:6]  # 最近6期
    for period in periods:
        try:
            df = pool.call_any("fina_indicator", period=period)
            if df is None or len(df) == 0:
                continue
            for _, r in df.iterrows():
                code = r.get("ts_code")
                if not code:
                    continue
                # 候选股感知: 非候选股跳过 (fina 只用于候选股硬门槛排雷, 全市场拉取是浪费)
                if codes_set is not None and code not in codes_set:
                    continue
                # 最新期优先(已按 period 倒序尝试); 已存则作为上一期(周度深度排雷需要近2期增速)
                if code not in cache:
                    cache[code] = {
                        "end_date": str(r.get("end_date") or ""),
                        "net_profit": float(r["net_profit"]) if pd.notna(r.get("net_profit")) else None,
                        "netprofit_yoy": float(r["netprofit_yoy"]) if pd.notna(r.get("netprofit_yoy")) else None,
                        "dt_netprofit_yoy": float(r["dt_netprofit_yoy"]) if pd.notna(r.get("dt_netprofit_yoy")) else None,
                        "n_income_attr_parent": float(r["n_income_attr_parent"]) if pd.notna(r.get("n_income_attr_parent")) else None,
                        "debt_to_assets": float(r["debt_to_assets"]) if pd.notna(r.get("debt_to_assets")) else None,
                        "prev_end_date": "", "prev_net_profit": None, "prev_netprofit_yoy": None,
                    }
                elif not cache[code].get("prev_net_profit") and cache[code].get("end_date"):
                    # 上一期数据(用于周度业绩持续性校验: 近2期同比增速)
                    cache[code]["prev_end_date"] = str(r.get("end_date") or "")
                    cache[code]["prev_net_profit"] = float(r["net_profit"]) if pd.notna(r.get("net_profit")) else None
                    cache[code]["prev_netprofit_yoy"] = float(r["netprofit_yoy"]) if pd.notna(r.get("netprofit_yoy")) else None
            print(f"  ✅ fina_indicator period={period}: {len(df)} 条 (候选股保留 {len(cache)})")
            # 候选股全部找齐 → 退出 period 循环 (无需遍历全部6期)
            if codes_set is not None and len(cache) >= len(codes_set):
                break
            if len(cache) > 500:  # 已拿到足够数据, 不再继续
                break
        except Exception as e:
            print(f"  ⚠️ fina_indicator period={period} 失败: {str(e)[:50]}")
            continue
    # 东财替代: Tushare 全失败时启用 (传入候选股 codes 提前退出分页)
    if not cache:
        try:
            em_cache = fetch_fina_em(latest_date, codes=codes)
            if em_cache:
                cache = em_cache
                print(f"  ✅ fina_indicator (东财替代): {len(cache)} 条")
        except Exception as e:
            print(f"  ⚠️ fina_indicator 东财替代失败: {str(e)[:50]}")
    if cache:
        try:
            with open(FINA_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception:
            pass
    return cache


def fetch_moneyflow_batch(latest_date, daily_map, n_days=3, codes=None):
    """moneyflow 按 trade_date 批量拉取(主力净流入)
    缓存 static/moneyflow_cache.json: {ts_code: [{trade_date, buy_sm_amount, sell_sm_amount}]}
    近 n_days 日, 失败返回 {} 由 screen_capital 降级 calc_net_inflow_3d
    codes: 可选候选股; 传入时缓存/拉取结果过滤到候选股 (评分只处理 RPS 入选板块成分股)
    """
    codes_set = set(codes) if codes else None
    avail = sorted([d for d in daily_map.keys() if d <= latest_date])[-n_days:]
    if MFLOW_CACHE_FILE.exists():
        try:
            with open(MFLOW_CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    else:
        cache = {}
    # 候选股感知: 缓存过滤到候选股 (减少内存, 全市场5000只资金流是浪费)
    if codes_set:
        cache = {c: v for c, v in cache.items() if c in codes_set}
    need_dates = [d for d in avail if d not in {x["trade_date"] for v in cache.values() for x in v}]
    for date in need_dates:
        try:
            df = pool.call_any("moneyflow", trade_date=date)
            if df is None or len(df) == 0:
                continue
            for _, r in df.iterrows():
                code = r.get("ts_code")
                if not code:
                    continue
                # 候选股感知: 非候选股跳过 (资金面评分只针对候选股)
                if codes_set is not None and code not in codes_set:
                    continue
                cache.setdefault(code, [])
                # 去重: 同一 trade_date 只保留一条
                cache[code] = [x for x in cache[code] if x["trade_date"] != date]
                cache[code].append({
                    "trade_date": date,
                    "buy_sm_amount": float(r["buy_sm_amount"]) if pd.notna(r.get("buy_sm_amount")) else 0.0,
                    "sell_sm_amount": float(r["sell_sm_amount"]) if pd.notna(r.get("sell_sm_amount")) else 0.0,
                })
            print(f"  ✅ moneyflow {date}: {len(df)} 条 (候选股保留 {len(cache)})")
        except Exception as e:
            print(f"  ⚠️ moneyflow {date} Tushare失败: {str(e)[:50]}")
    # AKShare 腾讯源: Tushare 失败且最新日无数据时, 取全市场主力净流入 (优先)
    latest_covered = latest_date in {x["trade_date"] for v in cache.values() for x in v}
    if not latest_covered and _AK_OK:
        try:
            ak_mflow = _ak.fetch_moneyflow_ak(latest_date)
            if ak_mflow:
                kept = 0
                for code, rows in ak_mflow.items():
                    # 候选股感知: 腾讯源1调用返全市场, 仅保留候选股 (避免缓存5000只)
                    if codes_set is not None and code not in codes_set:
                        continue
                    cache[code] = [x for x in cache.get(code, []) if x["trade_date"] != latest_date]
                    cache[code].extend(rows)
                    kept += 1
                print(f"  ✅ moneyflow (AKShare腾讯): 全市场 {len(ak_mflow)} → 候选股 {kept} 只")
                latest_covered = True
        except Exception as e:
            print(f"  ⚠️ moneyflow AKShare失败: {str(e)[:50]}")
    # 东财手动替代: AKShare 也失败时兜底 (传入候选股 codes 提前退出分页)
    if not latest_covered:
        try:
            em_mflow = fetch_moneyflow_em(latest_date, codes=codes)
            if em_mflow:
                for code, rows in em_mflow.items():
                    cache[code] = [x for x in cache.get(code, []) if x["trade_date"] != latest_date]
                    cache[code].extend(rows)
                print(f"  ✅ moneyflow (东财替代): {len(em_mflow)} 只股票主力净流入")
        except Exception as e:
            print(f"  ⚠️ moneyflow 东财替代失败: {str(e)[:50]}")
    if cache:
        try:
            with open(MFLOW_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception:
            pass
    return cache


def fetch_hsgt_flow(latest_date, daily_map, n_days=5):
    """moneyflow_hsgt 北向资金近 n 日(全市场, 非按个股)
    缓存 static/moneyflow_hsgt_cache.json: {trade_date: {buy_value, sell_value}}
    失败返回 {} (北向为加分项, 缺失跳过)
    """
    avail = sorted([d for d in daily_map.keys() if d <= latest_date])[-n_days:]
    if HSGT_CACHE_FILE.exists():
        try:
            with open(HSGT_CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    else:
        cache = {}
    need_dates = [d for d in avail if d not in cache]
    for date in need_dates:
        try:
            df = pool.call_any("moneyflow_hsgt", trade_date=date)
            if df is None or len(df) == 0:
                continue
            r = df.iloc[0]
            cache[date] = {
                "buy_value": float(r.get("north_money") or 0) * 1e4 if pd.notna(r.get("north_money")) else 0.0,
            }
            print(f"  ✅ moneyflow_hsgt {date}")
        except Exception as e:
            print(f"  ⚠️ moneyflow_hsgt {date} Tushare失败: {str(e)[:50]}")
    # AKShare 替代: Tushare 失败时取北向资金历史 (优先)
    if not cache and _AK_OK:
        try:
            ak_hsgt = _ak.fetch_hsgt_ak(latest_date, n_days)
            if ak_hsgt:
                cache.update(ak_hsgt)
                print(f"  ✅ moneyflow_hsgt (AKShare): {len(cache)} 日北向资金 (近期港交所限披露可能为0)")
        except Exception as e:
            print(f"  ⚠️ moneyflow_hsgt AKShare失败: {str(e)[:50]}")
    # 东财手动替代: AKShare 也失败时兜底
    if not cache:
        try:
            em_hsgt = fetch_hsgt_em(latest_date, n_days)
            if em_hsgt:
                cache.update(em_hsgt)
                print(f"  ✅ moneyflow_hsgt (东财替代): {len(cache)} 日北向资金")
        except Exception as e:
            print(f"  ⚠️ moneyflow_hsgt 东财替代失败: {str(e)[:50]}")
    if cache:
        try:
            with open(HSGT_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception:
            pass
    return cache


# ============================================================
# 7. 第一层: 大盘环境全局校验 (20日均线, 不合格终止选股)
# ============================================================

def check_market_env(index_map, daily_map, latest_date):
    """第一层: 大盘环境全局校验
    规则(可配置 CONFIG["market"]):
      - 上证指数/创业板指最新收盘 vs 各自 20 日均线
      - 至少 need_above 个指数站稳 20 日线 (默认 1)
      - 可选情绪校验: 上涨家数占比 < advance_ratio_min, 或跌停 > limit_down_max
    返回 (ok, market_status):
      ok: 大盘合格继续选股 / 不合格终止
      market_status: {is_safe, rule, reason, indexes[{code,name,close,ma,above}],
                      advance_ratio, limit_down_count, note}
    """
    cfg = CONFIG["market"]
    index_names = cfg.get("index_names", {})
    rows = []
    above_cnt = 0
    data_ok = True
    for code in cfg["index_codes"]:
        idx_df = index_map.get(code)
        name = index_names.get(code, code)
        if idx_df is None or len(idx_df) == 0:
            rows.append({"code": code, "name": name, "close": None, "ma": None, "above": None})
            data_ok = False
            continue
        df = idx_df.sort_values("trade_date") if "trade_date" in idx_df.columns else idx_df
        closes = [float(c) for c in df["close"].tolist() if pd.notna(c)]
        if len(closes) < cfg["ma_period"]:
            rows.append({"code": code, "name": name, "close": None, "ma": None, "above": None})
            data_ok = False
            continue
        today = closes[-1]
        ma = float(np.mean(closes[-cfg["ma_period"]:]))
        above = today > ma
        if above:
            above_cnt += 1
        rows.append({
            "code": code, "name": name,
            "close": round(today, 2), "ma": round(ma, 2), "above": above,
        })

    # 规则判定: 至少 need_above 个指数站稳 20 日线
    ok = above_cnt >= cfg["need_above"]
    reason = ""
    if not ok:
        below = [r["name"] for r in rows if r.get("above") is False]
        if below:
            reason = " / ".join(below) + f" 跌破{cfg['ma_period']}日线"
        elif cfg.get("default_safe", True):
            ok = True  # 指数数据完全缺失 → 默认安全, 避免接口故障误否决全市场
            reason = "指数数据缺失, 默认安全"
        else:
            reason = f"指数数据不足, 无法确认站稳{cfg['ma_period']}日线"

    # 市场情绪校验 (可选, 不单独否决: 仅在指数合格时二次把关)
    adv, dec, limit_down = 0, 0, 0
    if latest_date in daily_map:
        for _, r in daily_map[latest_date].iterrows():
            chg = r.get("pct_chg")
            if pd.notna(chg):
                if float(chg) > 0:
                    adv += 1
                elif float(chg) < 0:
                    dec += 1
                if float(chg) <= -9.8:
                    limit_down += 1
    advance_ratio = (adv / (adv + dec)) if (adv + dec) > 0 else None
    if ok and cfg.get("check_sentiment", True) and latest_date in daily_map:
        if advance_ratio is not None and advance_ratio < cfg["advance_ratio_min"]:
            ok = False
            reason = f"上涨家数占比 {advance_ratio:.0%} < {cfg['advance_ratio_min']:.0%}"
        if limit_down > cfg["limit_down_max"]:
            ok = False
            reason = f"跌停 {limit_down} 家 > {cfg['limit_down_max']} 家 (极端普跌)"

    market_status = {
        "is_safe": ok,
        "rule": f"至少 {cfg['need_above']} 个指数站上 {cfg['ma_period']} 日线",
        "reason": reason,
        "indexes": rows,
        "advance_ratio": round(advance_ratio, 2) if advance_ratio is not None else None,
        "limit_down_count": limit_down,
        "note": "大盘环境不合格, 终止选股" if not ok else "大盘环境合格, 继续选股",
    }
    return ok, market_status


# ============================================================
# 8. 第三层: 基本面排雷 (硬性一票否决)
# ============================================================

def _reject_rows(sub_df, rejects, reason):
    """将 sub_df 的每只股票记为剔除并记录原因"""
    for _, r in sub_df.iterrows():
        rejects.append({"ts_code": r["ts_code"], "name": r.get("name", ""), "reason": reason})


def screen_fundamental(stocks_df, fina_map, cfg=None):
    """第三层: 基本面排雷 (硬性一票否决, 满足任意一条即排除)
    规则(可配置 CONFIG["fundamental"]):
      - ST/*ST/退市 (is_st_stock)
      - 当日停牌 (vol<=0 或 price 缺失)
      - 收盘价 < min_price (低价仙股)
      - 流通市值 < min_circ_mv (过小市值)
      - PE-TTM ≤ 0 或 > max_pe (盈利风险)
      - 净利润 < 0 (fina_map; fina 缺失降级 PE>0 判定, 不误杀)
      - 资产负债率 > max_debt_ratio
      - 股权质押 > max_pledge_ratio / 商誉 > max_goodwill_ratio (数据缺失则跳过, 不误杀)
    返回 (passed_df, rejects): rejects=[{ts_code, name, reason}]
    """
    cfg = (cfg or CONFIG)["fundamental"]
    df = stocks_df.copy()
    rejects = []

    # ST/退市
    st_mask = df["name"].apply(is_st_stock)
    _reject_rows(df[st_mask], rejects, "ST/退市")
    df = df[~st_mask]
    # 停牌
    susp = df["vol"].fillna(0) <= 0
    _reject_rows(df[susp], rejects, "当日停牌")
    df = df[~susp]
    # 股价过低
    lp = df["price"].notna() & (df["price"] < cfg["min_price"])
    _reject_rows(df[lp], rejects, f"股价<{cfg['min_price']}元")
    df = df[~lp]
    # 流通市值过小 (circ_mv 单位万元; 10亿=100000万元)
    mv_min_wan = cfg["min_circ_mv"] / 1e4
    small_mv = df["circ_mv"].notna() & (df["circ_mv"] < mv_min_wan)
    _reject_rows(df[small_mv], rejects, f"流通市值<{cfg['min_circ_mv']/1e8:.0f}亿")
    df = df[~small_mv]
    # PE-TTM 盈利风险
    pe_bad = df["pe_ttm"].notna() & ((df["pe_ttm"] <= 0) | (df["pe_ttm"] > cfg["max_pe"]))
    _reject_rows(df[pe_bad], rejects, "PE-TTM≤0或超上限")
    df = df[~pe_bad]
    # 财务指标 (fina_map: 净利润/资产负债率; 缺失不剔除)
    if fina_map and len(df) > 0:
        keep = []
        for code in df["ts_code"]:
            fr = fina_map.get(code)
            if fr is None:
                keep.append(code)
                continue
            np_val = fr.get("net_profit")
            if np_val is not None and float(np_val) < 0:
                rejects.append({"ts_code": code, "name": df[df["ts_code"] == code]["name"].iloc[0],
                                "reason": "净利润为负"})
                continue
            debt = fr.get("debt_to_assets")
            if debt is not None and float(debt) > cfg["max_debt_ratio"]:
                rejects.append({"ts_code": code, "name": df[df["ts_code"] == code]["name"].iloc[0],
                                "reason": f"资产负债率>{cfg['max_debt_ratio']}%"})
                continue
            keep.append(code)
        df = df[df["ts_code"].isin(keep)]
    return df, rejects


# ============================================================
# 9. 第四层: 资金面达标校验 (全部核心项必达)
# ============================================================

def _calc_vol_ratio_fallback(daily_map, code, latest_date):
    """量比兜底: daily_basic 缺失时从日线成交量计算 (当日量/5日均量)"""
    vols = _get_recent_vols(daily_map, code, latest_date, n=6)
    if len(vols) >= 2:
        today_v = vols[-1]
        ma5v = sum(vols[:-1]) / len(vols[:-1]) if vols[:-1] else 0
        if ma5v > 0:
            return today_v / ma5v
    return None


def screen_capital(stocks_df, daily_map, latest_date, mflow_map, cfg=None):
    """第四层: 资金面达标校验 (全部核心项必达, 任一不满足即淘汰)
    规则(可配置 CONFIG["capital"]):
      - 换手率 ∈ [turnover_min, turnover_max] (下限资金关注 / 上限规避出货)
      - 量比 > vol_ratio_min (量能较前期放大; daily_basic 缺失从日线成交量兜底)
      - 单日成交额 > amount_min (daily_map 最新日 amount, 千元→元)
      - 主力资金: 当日净额>0 或 近3日累计净流入>0 (mflow_map; 缺失跳过, 不误杀)
    返回 (passed_df, cap_details): cap_details={code:{turnover, vol_ratio, amount, net_inflow_3d}}
    """
    cfg = (cfg or CONFIG)["capital"]
    df = stocks_df.copy()
    cap_details = {}
    keep = []
    # 最新交易日成交额映射 (千元 → 元)
    day_amt = {}
    if latest_date in daily_map:
        for _, r in daily_map[latest_date].iterrows():
            tc = r.get("ts_code")
            if tc and pd.notna(r.get("amount")):
                day_amt[tc] = float(r["amount"]) * 1000
    for _, s in df.iterrows():
        code = s["ts_code"]
        # 换手率区间
        tr = s.get("turnover_rate")
        if tr is None or pd.isna(tr) or not (cfg["turnover_min"] <= float(tr) <= cfg["turnover_max"]):
            continue
        # 量比
        vr = s.get("vol_ratio")
        if vr is None or pd.isna(vr):
            vr = _calc_vol_ratio_fallback(daily_map, code, latest_date)
        if vr is None or float(vr) <= cfg["vol_ratio_min"]:
            continue
        # 单日成交额
        amt = day_amt.get(code, 0)
        if amt < cfg["amount_min"]:
            continue
        # 主力资金方向 (缺失跳过)
        net3 = None
        if mflow_map and code in mflow_map:
            rows = sorted(mflow_map[code], key=lambda x: x["trade_date"])[-3:]
            nets = [r["buy_sm_amount"] - r["sell_sm_amount"] for r in rows]
            if nets:
                net3 = sum(nets)
        if net3 is not None and net3 <= 0:
            continue
        cap_details[code] = {
            "turnover": round(float(tr), 2),
            "vol_ratio": round(float(vr), 2),
            "amount": round(amt, 0),
            "net_inflow_3d": net3,
        }
        keep.append(code)
    return df[df["ts_code"].isin(keep)], cap_details


# ============================================================
# 10. 第二层: 候选股票池 (核心约束 = RPS 入选板块成分股 + 板块共振计数)
# ============================================================

def build_member_sector_map(in_list_records):
    """构建 成分股→强势板块 映射 {ts_code: [板块名,...]} (第二层核心)
    同一板块跨 RPS 档位(rps5/10/20)重复入选时保留重复条目 → 板块共振强度:
      sector_count = len(列表) = 该股所属板块入选的 RPS 档位数 (1~3)
      多档位同时强势 = 热点共振度更高 (如"黄金"三档都入选 → 黄金股计数=3)
    前端展示所属板块时按去重后的板块名 (见 recommend_stocks 中 sector_names)
    in_list_records: 板块记录数组(跨档位汇总), 每条含 name + stocks(成分股数组)
    """
    mapping = {}
    for rec in in_list_records or []:
        name = str(rec.get("name") or "").strip()
        for m in rec.get("stocks", []) or []:
            if isinstance(m, dict):
                tc = m.get("ts_code")
            else:
                tc = m
            if not tc:
                continue
            tc = str(tc)
            mapping.setdefault(tc, [])
            if name:
                mapping[tc].append(name)  # 不去重: 跨档位重复 = 共振强度
    return mapping


# ============================================================
# 11. 第五层: 多技术指标共振 (达标 ≥ hit_min 项判为通过)
# ============================================================

def screen_technical(stocks_df, daily_map, latest_date, cfg=None, rps50_map=None):
    """第五层: 多技术指标共振 (达标 ≥ hit_min 项判为通过)
    指标(7项, 可独立判定):
      1 均线多头: MA5 > MA10 > MA20 > MA60 (短中期趋势全部向上)
      2 MACD多头: DIF > DEA
      3 KDJ健康: J ∈ [50,80] 多头区域未超买, 或低位金叉 (J上穿K 且 J<50)
      4 突破形态: 收盘价创近20日新高 (突破前期平台压力位)
      5 量价配合: 当日上涨(pct>0) 且 量比>1.1 (量价齐升)
      6 中期强势: 个股RPS50 ≥ rps50_threshold (rps50_map 提供; 缺失跳过)
      7 均线支撑: 收盘价 > max(MA5, MA10, MA20, MA60)
    返回 (passed_df, tech_details): tech_details={code:{hits:[指标名], hit_count}}
    """
    cfg = (cfg or CONFIG)["technical"]
    df = stocks_df.copy()
    tech_details = {}
    keep = []
    for _, s in df.iterrows():
        code = s["ts_code"]
        closes, highs, lows, vols = _get_ohlc_series(daily_map, code, latest_date, n=80)
        if len(closes) < 20:
            continue  # 数据不足无法判断, 保守淘汰
        hits = []
        today_close = closes[-1]
        ma5 = calc_ma_series(closes, 5)
        ma10 = calc_ma_series(closes, 10)
        ma20 = calc_ma_series(closes, 20)
        ma60 = calc_ma_series(closes, 60)
        # 1 均线多头
        if ma5 and ma10 and ma20 and ma60 and ma5 > ma10 > ma20 > ma60:
            hits.append("均线多头")
        # 2 MACD多头
        macd = calc_macd(closes)
        if macd and macd["dif"] > macd["dea"]:
            hits.append("MACD多头")
        # 3 KDJ健康
        kdj = calc_kdj(highs, lows, closes)
        if kdj:
            j = kdj["j"]
            if 50 <= j <= 80:
                hits.append("KDJ健康")
            elif j > kdj["k"] and kdj["prev_j"] <= kdj["k"] and j < 50:
                hits.append("KDJ低位金叉")
        # 4 突破形态: 收盘创近20日新高 (不含当日)
        if len(closes) >= 21 and today_close > max(closes[-20:-1]):
            hits.append("20日新高")
        # 5 量价配合
        vr = s.get("vol_ratio")
        if vr is None or pd.isna(vr):
            vr = _calc_vol_ratio_fallback(daily_map, code, latest_date)
        pct = s.get("pct_chg")
        if (pct is not None and not pd.isna(pct) and float(pct) > 0) and vr is not None and float(vr) > 1.1:
            hits.append("量价齐升")
        # 6 中期强势 RPS50
        if rps50_map and code in rps50_map and rps50_map[code] >= cfg["rps50_threshold"]:
            hits.append("RPS50强势")
        # 7 均线支撑
        mas = [m for m in (ma5, ma10, ma20, ma60) if m is not None]
        if mas and today_close > max(mas):
            hits.append("均线支撑")
        if len(hits) >= cfg["hit_min"]:
            tech_details[code] = {"hits": hits, "hit_count": len(hits)}
            keep.append(code)
    return df[df["ts_code"].isin(keep)], tech_details


# ============================================================
# 6. 主流程 v2.0 —— "果断买入"五维共振
# ============================================================

# 模块级缓存: 最近一次大盘状态(供 save_recommendations 写入 banner)
_LAST_MARKET_STATUS = None


def score_sector_funnel(sector_count, cfg=None):
    """板块热度分 (权重最高 CONFIG["score"]["sector"]=40):
    归属强势板块数越多得分越高: 1个=20, 2个=30, ≥3个=40"""
    max_s = (cfg or CONFIG)["score"]["sector"]
    if sector_count >= 3:
        return max_s
    if sector_count == 2:
        return int(max_s * 0.75)
    if sector_count == 1:
        return int(max_s * 0.5)
    return 0


def score_fundamental_funnel(s, fina_row, cfg=None):
    """基本面 20 分 (缺失子项记0分, 不淘汰):
      - 净利润>0: 5分 (fina缺失降级 PE>0: 3分)
      - PE-TTM 0-50: 5分; 50-200: 3分
      - 流通市值 50-600亿: 5分; >600亿: 2分
      - 资产负债率 ≤55%: 5分; 55-70%: 2分
    """
    score = 0
    pe = s.get("pe_ttm")
    mv = s.get("circ_mv")
    np_val = fina_row.get("net_profit") if fina_row else None
    debt = fina_row.get("debt_to_assets") if fina_row else None
    if np_val is not None and float(np_val) > 0:
        score += 5
    elif pe is not None and not pd.isna(pe) and float(pe) > 0:
        score += 3
    if pe is not None and not pd.isna(pe):
        p = float(pe)
        if 0 < p <= 50:
            score += 5
        elif p <= 200:
            score += 3
    if mv is not None and not pd.isna(mv):
        mv_yi = float(mv) / 10000
        if 50 <= mv_yi <= 600:
            score += 5
        elif mv_yi > 600:
            score += 2
    if debt is not None and not pd.isna(debt):
        d = float(debt)
        if d <= 55:
            score += 5
        elif d <= 70:
            score += 2
    return min(score, 20)


def score_capital_funnel(cap, cfg=None):
    """资金面 20 分 (基于第四层达标明细):
      - 换手率 8-15%: 5分; 4-8%: 3分
      - 量比 1.5-3.0: 5分; >3.0: 2分
      - 近3日主力净流入 >1亿: 5分; >1千万: 3分; >0: 2分
      - 单日成交额 >3亿: 5分; >1亿: 3分; >5000万: 2分
    """
    score = 0
    tr = cap.get("turnover")
    if tr is not None:
        if 8 <= tr <= 15:
            score += 5
        elif 4 <= tr < 8:
            score += 3
    vr = cap.get("vol_ratio")
    if vr is not None:
        if 1.5 <= vr <= 3.0:
            score += 5
        elif vr > 3.0:
            score += 2
    net3 = cap.get("net_inflow_3d")
    if net3 is not None:
        if net3 > 1e8:
            score += 5
        elif net3 > 1e7:
            score += 3
        elif net3 > 0:
            score += 2
    amt = cap.get("amount")
    if amt is not None:
        if amt > 3e8:
            score += 5
        elif amt > 1e8:
            score += 3
        elif amt > 5e7:
            score += 2
    return min(score, 20)


def score_technical_funnel(hit_count, cfg=None):
    """技术面 20 分: 达标指标数 × 7, 上限 20"""
    return min(20, int(hit_count) * 7)


def build_recommend_tags(sector_names, tech_hits, sector_count=None):
    """生成核心推荐标签, 如 "3板块共振 + MACD多头 + 量价齐升"
    sector_names: 所属强势板块名列表(去重, 前端展示)
    sector_count: 板块共振档位数(跨RPS5/10/20档位, 标签/评分用); 缺省=len(sector_names)
    tech_hits: 达标技术指标名列表
    """
    tags = []
    n = sector_count if sector_count is not None else len(sector_names)
    if n >= 2:
        tags.append(f"{n}板块共振")
    elif n == 1:
        tags.append(f"{sector_names[0]}热点" if sector_names else "板块热点")
    priority = ["20日新高", "量价齐升", "MACD多头", "KDJ低位金叉",
                "均线多头", "RPS50强势", "KDJ健康", "均线支撑"]
    for p in priority:
        if p in tech_hits:
            tags.append(p)
        if len(tags) >= 3:
            break
    if not tags:
        tags.append("多指标共振")
    return " + ".join(tags)


def filter_by_min_score(results, min_score=None):
    """综合得分门槛过滤: 只保留 score_total >= min_score 的推荐 (低于门槛的不进入推荐列表, 前端不显示)
    min_score 缺省取 CONFIG["score"]["min_score"] (默认 70)
    """
    if min_score is None:
        min_score = float(CONFIG["score"].get("min_score", 70))
    return [r for r in results if r["score_total"] >= min_score]


def _extract_member_codes(in_list_records):
    """从 RPS 入选板块记录中提取成分股 ts_code 集合
    in_list_records: 板块记录数组, 每条含 "stocks" 字段(成分股数组, 元素为 dict 含 ts_code 或直接是代码)
    返回: {ts_code} — 保证推荐股票 100% 来自入选板块的明确成分股名单
    """
    codes = set()
    for rec in in_list_records or []:
        for m in rec.get("stocks", []) or []:
            if isinstance(m, dict):
                tc = m.get("ts_code")
            else:
                tc = m
            if tc:
                codes.add(str(tc))
    return codes


def _build_sector_cont_map(in_list_records):
    """板块名 → 连续上榜天数 (跨 RPS 档位取最大)"""
    out = {}
    for rec in in_list_records or []:
        nm = str(rec.get("name") or "").strip()
        if not nm:
            continue
        try:
            c = int(rec.get("continuous") or 0)
        except (TypeError, ValueError):
            c = 0
        if c > out.get(nm, 0):
            out[nm] = c
    return out


def _pred_features(code, s, cap, tech, rps50_map, daily_by_code, sector_cont_map, market_status):
    """提取预测特征 (供 predict.build_prediction 使用, 全部来自现有数据零新增接口)"""
    df = daily_by_code.get(code) if daily_by_code else None
    closes, lows = [], []
    if df is not None and len(df):
        closes = [float(c) for c in df["close"].tolist()]
        lows = [float(x) for x in df["low"].tolist()]
    chg3 = chg5 = consec_up = None
    if len(closes) >= 4:
        chg3 = (closes[-1] / closes[-4] - 1) * 100
    if len(closes) >= 6:
        chg5 = (closes[-1] / closes[-6] - 1) * 100
    if len(closes) >= 2:
        consec_up = 0
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] > closes[i - 1]:
                consec_up += 1
            else:
                break
    sector_names = s.get("sector_names") or []
    continuous = max([sector_cont_map.get(nm, 0) for nm in sector_names] or [0])
    cap = cap or {}
    tech = tech or {}
    return {
        "rps50": rps50_map.get(code),
        "sector_count": int(s.get("sector_count") or 0),
        "continuous": continuous,
        "chg3": chg3, "chg5": chg5, "consec_up": consec_up,
        "vol_ratio": cap.get("vol_ratio") if cap.get("vol_ratio") is not None else s.get("vol_ratio"),
        "turnover": cap.get("turnover") if cap.get("turnover") is not None else s.get("turnover_rate"),
        "net_inflow_3d": cap.get("net_inflow_3d"),
        "pct_chg": s.get("pct_chg"),
        "price": s.get("price"),
        "pe_ttm": s.get("pe_ttm"),
        "tech_hits": tech.get("hits", []),
        "hit_count": tech.get("hit_count", 0),
        "lows": lows[-20:] if lows else [],
        "advance_ratio": market_status.get("advance_ratio") if market_status else None,
    }


def _select_member_pool(industry_df, in_list_industries, member_codes=None):
    """构建选股池: 只保留入选板块成分股
    - member_codes 提供时: 严格用成分股名单 (industry_df 的 ts_code ∈ member_codes)
    - 否则回退: 按行业名匹配 (industry_df.industry ∈ in_list_industries)
    返回 (members, codes, mode): mode="成分股名单" / "行业名匹配"
    """
    if member_codes:
        mc = set(member_codes)
        members = industry_df[industry_df["ts_code"].isin(mc)]
        return members, list(members["ts_code"].unique()), "成分股名单"
    members = industry_df[industry_df["industry"].isin(in_list_industries)]
    return members, list(members["ts_code"].unique()), "行业名匹配"


def build_rps50_map(daily_map, latest_date):
    """全市场个股 RPS50 (50日相对强弱) 映射 {ts_code: RPS值}
    欧奈尔体系: 按近50个交易日累计涨幅在全市场排名, 涨幅越高 RPS 越大
    RPS50 = (1 - (rank-1)/N) * 100, 第1名=100
    数据缺失/交易日不足 → 返回 {} (第五层该指标自动跳过)
    """
    dates = sorted([d for d in daily_map.keys() if d <= latest_date])
    if len(dates) < 2:
        return {}
    n = min(50, len(dates) - 1)
    d_old, d_new = dates[-1 - n], dates[-1]
    old = daily_map[d_old][["ts_code", "close"]].rename(columns={"close": "close_old"})
    new = daily_map[d_new][["ts_code", "close"]].rename(columns={"close": "close_new"})
    merged = old.merge(new, on="ts_code")
    merged = merged[(merged["close_old"] > 0) & merged["close_new"].notna()]
    if merged.empty:
        return {}
    total = len(merged)
    merged["chg50"] = merged["close_new"] / merged["close_old"] - 1
    merged["rank"] = merged["chg50"].rank(ascending=False, method="min")
    merged["RPS50"] = (1 - (merged["rank"] - 1) / total) * 100
    return {r["ts_code"]: round(float(r["RPS50"]), 1) for _, r in merged.iterrows()}


def recommend_stocks(industry_df, daily_map, latest_date, in_list_records, member_codes=None):
    """主推荐流程 v3.0 —— 五层漏斗筛选 + 综合评分
    输入:
      industry_df: 含 ts_code/name/industry/list_date
      daily_map: {date: DataFrame of daily}
      latest_date: 最新交易日 YYYYMMDD
      in_list_records: 全部 RPS 入选板块记录 (每条含 name + stocks 成分股), 跨 RPS5/10/20 三档汇总
      member_codes: 可选, 入选板块成分股 ts_code 集合 (默认从 in_list_records 提取)
    流程:
      第一层 大盘环境: 上证/创业板指 vs 20日线, 不合格直接终止 (输出空推荐 + 原因)
      第二层 候选池:   100% 来自入选板块成分股, 统计每股归属强势板块数(多题材共振)
      第三层 基本面:   一票否决 (ST/停牌/低价/小市值/PE异常/亏损/高负债)
      第四层 资金面:   换手率/量比/成交额/主力资金 全部必达
      第五层 技术共振: 7项独立指标达标 ≥3 项判为通过
      综合评分: 板块40 + 基本面20 + 资金20 + 技术20 = 100, 按综合得分降序
    """
    global _LAST_MARKET_STATUS, _DAILY_BY_CODE
    print("\n" + "=" * 55)
    print("  每日推荐股票 v3.0 (五层漏斗筛选 + 综合评分)")
    print("=" * 55)

    if not in_list_records:
        print("  ⚠️ 无 RPS 入选板块, 跳过推荐")
        _LAST_MARKET_STATUS = None
        return []

    # 汇总三档位入选板块: 名称集合 + 成分股名单
    in_list_names = {str(rec.get("name") or "").strip() for rec in in_list_records if rec.get("name")}
    if member_codes is None:
        member_codes = _extract_member_codes(in_list_records)
    if not member_codes:
        print("  ⚠️ 入选板块无成分股名单, 回退按行业名匹配")
    print(f"  入选板块: {len(in_list_names)} 个 | 成分股: {len(member_codes)} 只")

    # ============ 第一层: 大盘环境全局校验 ============
    print("\n[第一层] 大盘环境全局校验 (上证/创业板指 vs 20日均线)...")
    trade_days = sorted(daily_map.keys())
    index_map = fetch_index_daily(INDEX_CODES, trade_days)
    market_ok, market_status = check_market_env(index_map, daily_map, latest_date)
    _LAST_MARKET_STATUS = market_status
    for r in market_status["indexes"]:
        c, m = r.get("close"), r.get("ma")
        state = "站上" if r.get("above") else "跌破" if r.get("above") is False else "无数据"
        print(f"    {r['name']}: 收盘{c if c is not None else '—'} / MA{m if m is not None else '—'} ({state})")
    print(f"    上涨占比: {market_status['advance_ratio']} | 跌停: {market_status['limit_down_count']}家")
    if not market_ok:
        print(f"  ⛔ 大盘环境不满足: {market_status['reason']} → 终止选股, 前端展示「大盘环境不满足, 暂无推荐」")
        return []
    print(f"  ✅ 大盘环境合格, 继续选股")

    # ============ 第二层: 候选股票池构建 (核心约束) ============
    print("\n[第二层] 候选股票池构建 (100% 来自入选板块成分股 + 板块共振计数)...")
    sector_map = build_member_sector_map(in_list_records)
    members, codes, pool_mode = _select_member_pool(industry_df, in_list_names, member_codes)
    print(f"  选股池: {len(codes)} 只 ({pool_mode})")
    # 预建个股日线索引 (提速~100倍, 替代逐股逐日扫描 daily_map)
    _DAILY_BY_CODE = build_daily_by_code(daily_map, codes, latest_date, n_days=80)
    print(f"  预建日线索引: {len(_DAILY_BY_CODE)} 只 × 80日")

    if latest_date not in daily_map:
        print(f"  ⚠️ latest_date {latest_date} 不在 daily_map, 跳过")
        _LAST_MARKET_STATUS = None
        return []
    df_latest = daily_map[latest_date]
    latest_idx = {r["ts_code"]: r for _, r in df_latest.iterrows() if r["ts_code"] in codes}
    codes_set = set(codes)

    # daily_basic: PE/流通市值/换手率/量比 (候选股感知: 仅保留候选股, 减少内存)
    _t0 = datetime.now()
    basic_idx = {}
    try:
        df_basic = pool.call_any("daily_basic", trade_date=latest_date)
        if df_basic is not None and len(df_basic) > 0:
            # Tushare daily_basic 返全市场, 过滤到候选股
            basic_idx = {r["ts_code"]: r for _, r in df_basic.iterrows() if r["ts_code"] in codes_set}
        print(f"  ✅ daily_basic: 全市场 → 候选股 {len(basic_idx)} 条 ({(datetime.now()-_t0).total_seconds():.1f}s)")
    except Exception as e:
        print(f"  ⚠️ daily_basic Tushare失败: {str(e)[:50]}, 尝试 AKShare/东财替代...")
    if not basic_idx and _AK_OK:
        try:
            ak_basic = _ak.fetch_daily_basic_ak(latest_date)
            if ak_basic:
                basic_idx = {c: v for c, v in ak_basic.items() if c in codes_set}
            print(f"  ✅ daily_basic (AKShare腾讯): 全市场 → 候选股 {len(basic_idx)} 条 ({(datetime.now()-_t0).total_seconds():.1f}s)")
        except Exception as e:
            print(f"  ⚠️ daily_basic AKShare失败: {str(e)[:50]}")
    if not basic_idx:
        try:
            em_basic = fetch_daily_basic_em(latest_date, codes=codes)
            if em_basic:
                basic_idx = em_basic
            print(f"  ✅ daily_basic (东财替代): 候选股 {len(basic_idx)} 条 ({(datetime.now()-_t0).total_seconds():.1f}s)")
        except Exception as e:
            print(f"  ⚠️ daily_basic 东财替代失败: {str(e)[:50]}")

    # 构建候选池 DataFrame (第二层: 每股统计归属强势板块数)
    rows = []
    for code in codes:
        if code not in latest_idx:
            continue
        lr = latest_idx[code]
        m = members[members["ts_code"] == code].iloc[0]
        br = basic_idx.get(code, {})
        rows.append({
            "ts_code": code,
            "name": str(m.get("name", "")),
            "industry": str(m.get("industry", "")),
            "price": float(lr.get("close")) if pd.notna(lr.get("close")) else None,
            "pct_chg": float(lr.get("pct_chg")) if pd.notna(lr.get("pct_chg")) else None,
            "vol": int(lr["vol"]) if pd.notna(lr.get("vol")) else 0,
            "pe_ttm": float(br.get("pe_ttm")) if br is not None and pd.notna(br.get("pe_ttm")) else None,
            "circ_mv": float(br.get("circ_mv")) if br is not None and pd.notna(br.get("circ_mv")) else None,
            "turnover_rate": float(br.get("turnover_rate")) if br is not None and pd.notna(br.get("turnover_rate")) else None,
            "vol_ratio": float(br.get("volume_ratio")) if br is not None and pd.notna(br.get("volume_ratio")) else None,
        })
    stocks_df = pd.DataFrame(rows)
    if stocks_df.empty:
        print("  ⚠️ 候选池为空")
        _LAST_MARKET_STATUS = None
        return []

    # 板块共振计数 (第二层核心: 跨RPS档位共振强度, 数量越高热点认可度越强)
    # sector_count = 所属板块入选的 RPS 档位数(1~3, 含重复条目长度)
    # sector_names  = 去重后的板块名(前端展示所属热点板块)
    stocks_df["sector_count"] = stocks_df["ts_code"].apply(lambda c: len(sector_map.get(c, [])))
    stocks_df["sector_names"] = stocks_df["ts_code"].apply(
        lambda c: list(dict.fromkeys(sector_map.get(c, []))))
    min_sc = CONFIG["pool"]["min_sector_count"]
    before = len(stocks_df)
    stocks_df = stocks_df[stocks_df["sector_count"] >= min_sc]
    print(f"  板块共振过滤: {before} -> {len(stocks_df)} 只 (每股须属 ≥{min_sc} 个入选板块)")
    if stocks_df.empty:
        print("  ⚠️ 无股票属于入选板块成分股, 终止")
        return []
    print(f"  共振分布(板块数→只数): {dict(stocks_df['sector_count'].value_counts().sort_index())}")

    # ============ 第三层: 基本面排雷 (硬性一票否决) ============
    print("\n[第三层] 基本面排雷 (硬性一票否决)...")
    _t1 = datetime.now()
    fina_map = fetch_fina_indicator_batch(latest_date, codes=codes)
    print(f"  财务指标: 候选股 {len(fina_map)} 只 ({(datetime.now()-_t1).total_seconds():.1f}s)")
    stocks_df, rejects = screen_fundamental(stocks_df, fina_map)
    print(f"  ⛔ 基本面剔除 {len(rejects)} 只:")
    for r in rejects[:10]:
        print(f"    - {r['ts_code']} {r['name']}: {r['reason']}")
    if len(rejects) > 10:
        print(f"    ... 其余 {len(rejects) - 10} 只")
    print(f"  ✅ 通过基本面: {len(stocks_df)} 只")
    if stocks_df.empty:
        print("  ⚠️ 基本面排雷后无候选, 终止")
        return []

    # ============ 第四层: 资金面达标校验 (全部核心项必达) ============
    print("\n[第四层] 资金面达标校验 (换手区间/量比/成交额/主力资金 全必达)...")
    _t2 = datetime.now()
    mflow_map = fetch_moneyflow_batch(latest_date, daily_map, n_days=3, codes=codes)
    print(f"  主力资金: 候选股 {len(mflow_map)} 只 ({(datetime.now()-_t2).total_seconds():.1f}s)")
    before = len(stocks_df)
    stocks_df, cap_details = screen_capital(stocks_df, daily_map, latest_date, mflow_map)
    print(f"  资金面达标: {before} -> {len(stocks_df)} 只")
    if stocks_df.empty:
        print("  ⚠️ 资金面无一达标, 终止")
        return []

    # ============ 第五层: 多技术指标共振 (达标 ≥3 项通过) ============
    print("\n[第五层] 多技术指标共振 (7项独立指标, 达标 ≥3 通过)...")
    rps50_map = build_rps50_map(daily_map, latest_date)
    print(f"  全市场 RPS50 映射: {len(rps50_map)} 只")
    before = len(stocks_df)
    stocks_df, tech_details = screen_technical(stocks_df, daily_map, latest_date, rps50_map=rps50_map)
    print(f"  技术共振通过: {before} -> {len(stocks_df)} 只")
    if stocks_df.empty:
        print("  ⚠️ 技术共振无一通过, 终止")
        return []

    # ============ 综合评分与最终推荐排序 ============
    print(f"\n[综合评分] 通过五层漏斗 {len(stocks_df)} 只 → 四维评分 (板块40/基本面20/资金20/技术20) + 预测化")
    sector_cont_map = _build_sector_cont_map(in_list_records)
    results = []
    for _, s in stocks_df.iterrows():
        code = s["ts_code"]
        sectors = s["sector_names"] or []  # 去重板块名(前端展示)
        sector_count = int(s["sector_count"])  # 跨RPS档位共振数(评分用)
        fina_row = fina_map.get(code)
        cap = cap_details.get(code, {})
        tech = tech_details.get(code, {})
        score_sector = score_sector_funnel(sector_count)
        score_fund = score_fundamental_funnel(s, fina_row)
        score_cap = score_capital_funnel(cap)
        score_tech = score_technical_funnel(tech.get("hit_count", 0))
        total = score_sector + score_fund + score_cap + score_tech
        # 预测化: 6 维度概率 + 置信度 + 止损位 + 预测逻辑 (不依赖额外接口)
        pred_features = _pred_features(code, s, cap, tech, rps50_map, _DAILY_BY_CODE,
                                       sector_cont_map, market_status)
        prediction = predict.build_prediction(pred_features, market_status)
        results.append({
            "ts_code": code,
            "name": s["name"],
            "industry": s["industry"],
            "sectors": sectors,
            "sector_count": sector_count,
            "tags": build_recommend_tags(sectors, tech.get("hits", []), sector_count),
            "price": round(s["price"], 2) if s.get("price") is not None else None,
            "pct_chg": round(s["pct_chg"], 2) if s.get("pct_chg") is not None else None,
            "pe_ttm": s.get("pe_ttm"),
            "circ_mv": round(s["circ_mv"] / 10000, 2) if s.get("circ_mv") else None,  # 亿元
            "score_total": total,
            "score_sector": score_sector,
            "score_fundamental": score_fund,
            "score_capital": score_cap,
            "score_technical": score_tech,
            "fundamental": {
                "net_profit": fina_row.get("net_profit") if fina_row else None,
                "debt_to_assets": fina_row.get("debt_to_assets") if fina_row else None,
            },
            "capital": cap,
            "technical": tech,
            "prediction": prediction,
        })
    results.sort(key=lambda x: -x["score_total"])

    # 综合得分门槛: < min_score 的股票不进入推荐列表 (前端不显示)
    shown = filter_by_min_score(results)
    dropped = len(results) - len(shown)

    # 分数分布诊断 (确认五层筛选后仍有区分度, 避免误判 "代码 bug")
    all_totals = [r["score_total"] for r in results]
    if all_totals:
        buckets = {"<60": 0, "60-70": 0, "70-80": 0, "80-90": 0, "90+": 0}
        for t in all_totals:
            if t < 60: buckets["<60"] += 1
            elif t < 70: buckets["60-70"] += 1
            elif t < 80: buckets["70-80"] += 1
            elif t < 90: buckets["80-90"] += 1
            else: buckets["90+"] += 1
        print(f"  综合得分分布: {buckets}")
        print(f"  最高分: {max(all_totals)} | 中位数: {sorted(all_totals)[len(all_totals) // 2]}")
    print(f"\n[结果] 漏斗通过 {len(results)} 只, 门槛≥{CONFIG['score'].get('min_score', 70):.0f}分后显示 {len(shown)} 只 (剔除 {dropped} 只), 按综合得分降序")

    # 预测化每日校准: 核对历史预测实际涨跌, 产出预测效果复盘 (失败不阻断)
    try:
        review = predict.calc_prediction_accuracy(daily_map, latest_date)
        if review:
            s_ = review["summary"]
            print(f"  [校准] 预测效果复盘: 样本{s_['samples']} | {predict.PRED_HORIZON}日上涨命中率 {s_['hit_rate']:.0%} | 平均涨幅 {s_['avg_gain']:+.1f}%")
    except Exception as e:
        print(f"  ⚠️ 预测校准失败(不影响推荐): {str(e)[:60]}")
    return shown


def _sanitize_json(obj):
    """递归将 NaN/Inf/numpy 类型转为 JSON 合法值, 保证浏览器 JSON.parse 不报错。

    背景: recommend_stocks 中 rows 的 None 经 pd.DataFrame 升级为 float NaN,
    随后 s.get("pe_ttm") 返回 numpy.float64('nan'), json.dump 默认写出 "NaN"
    (违反 JSON 规范), 浏览器 JSON.parse 抛 "Unexpected token 'N'"。
    此处统一归一化: NaN/Inf → None, numpy 标量 → Python 原生类型。
    """
    if obj is None:
        return None
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        v = float(obj)
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_json(v) for v in obj]
    return obj


def save_recommendations(rec_list, latest_date, prev_date=""):
    """保存推荐结果到 static/recommendations.json (schema v3)
    v3: 五层漏斗筛选 + 综合评分(板块40/基本面20/资金20/技术20)
        market_status 结构: {is_safe, rule, reason, indexes[], advance_ratio, limit_down_count, note}
    """
    market_status = _LAST_MARKET_STATUS or {
        "is_safe": None, "rule": "至少 1 个指数站上 20 日线",
        "reason": "指数数据缺失", "indexes": [],
        "advance_ratio": None, "limit_down_count": None,
        "note": "指数数据缺失, 无法校验大盘环境",
    }
    multi_sector_count = sum(1 for r in rec_list if r.get("sector_count", 0) >= 2)
    # 预测化: 市场环境档位 (震荡市动量权重最高; 偏弱时前端提示控制仓位)
    try:
        regime = predict.market_regime(market_status)
    except Exception:
        regime = {"regime": "震荡市", "prob_adjust": 0, "note": ""}
    prediction_status = {
        "regime": regime["regime"],
        "note": regime["note"],
        "horizon": predict.PRED_HORIZON,
        "warning": regime["regime"] == "偏弱"
        or (market_status.get("advance_ratio") is not None and market_status["advance_ratio"] < 0.4),
    }
    output = {
        "schema_version": 3,
        "update_date": latest_date,
        "prev_date": prev_date,
        "market_status": market_status,
        "multi_sector_count": multi_sector_count,
        "prediction_status": prediction_status,
        "disclaimer": "⚠️ 全部为量化规则/软件算法逻辑参考,绝对不构成任何个股投资建议;中短线交易天然高风险,任何公式都无法100%胜率,系统必须绑定强制止损;本工具仅个人研究使用,严禁对外收费荐股、批量推送盈利承诺,违反监管法规。严禁杠杆借贷炒股。",
        "count": len(rec_list),
        "stocks": rec_list,
    }
    output = _sanitize_json(output)  # NaN/Inf/numpy → None/原生, 保证浏览器可解析
    with open(REC_FILE, "w", encoding="utf-8") as f:
        # allow_nan=False: 兜底, 若 sanitizer 漏网 NaN 直接抛错而非静默写出非法 JSON
        json.dump(output, f, ensure_ascii=False, indent=2, allow_nan=False)
    # 预测化: 记录当日预测 (供每日校准核对实际涨跌)
    try:
        n_pred = predict.record_predictions(rec_list, latest_date)
        if n_pred:
            print(f"  ✅ 预测日志: 记录 {n_pred} 只预测 (保存至 {predict.LOG_FILE.name})")
    except Exception as e:
        print(f"  ⚠️ 预测日志记录失败(不影响推荐): {str(e)[:60]}")
    print(f"  ✅ 已保存: {REC_FILE} ({len(rec_list)} 只, 多板块共振 {multi_sector_count})")


if __name__ == "__main__":
    # 独立运行: 复用 rps_calc 的 daily_map / industry_df
    trade_days = rps_calc.get_trade_days(n=80)
    industry_df = rps_calc.get_stock_industry()
    daily_map = rps_calc.fetch_daily_batch(trade_days)
    available = [d for d in trade_days if d in daily_map]
    latest_date = available[-1]
    prev_date = available[-2] if len(available) >= 2 else ""
    # RPS 入选板块从已有 rps_data.json 读取 (含成分股 stocks, 供板块共振计数)
    data_file = STATIC_DIR / "rps_data.json"
    in_list_records = []
    if data_file.exists():
        with open(data_file, encoding="utf-8") as f:
            data = json.load(f)
        for k in ("rps5", "rps10", "rps20"):
            in_list_records.extend(data.get(k, {}).get("in_list", []))
    rec_list = recommend_stocks(industry_df, daily_map, latest_date, in_list_records)
    save_recommendations(rec_list, latest_date, prev_date)
