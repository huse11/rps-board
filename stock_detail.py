# -*- coding: utf-8 -*-
"""
个股详情数据服务 (真实数据源: 东方财富/腾讯/AKShare)
供前端个股详情弹窗消费: 顶部行情 / K线(多周期) / 资金流 / 综合评分 / 风险亮点 /
历史回测 / 个股印象 / 技术探宝 / 资金跟踪 / 价值分析 / 资讯 / F10

架构: 纯数据层(不依赖 FastAPI), web_server.py 挂载 /api/stock/* 路由调用
数据策略:
  - 东财实时行情: push2.eastmoney.com/api/qt/stock/get
  - 东财K线:      push2his.eastmoney.com/api/qt/stock/kline/get (klt: 1分时/101日/102周/103月)
  - 东财分时:     push2his.eastmoney.com/api/qt/stock/trends2/get
  - 东财资金流:   push2his.eastmoney.com/api/qt/stock/fflow/daykline/get
  - 东财资讯:     np-anotice-stock.eastmoney.com/api/security/ann
  - AKShare 兜底: 北上资金/两融/龙虎榜 (服务器已装 akshare, 失败返回空)
  - 域名池轮换 + TTL 缓存: 应对东财高频限流
"""
import json
import math
import time
import urllib.request
from datetime import datetime, timedelta

try:
    import pandas as pd
    _HAS_PD = True
except Exception:  # pragma: no cover
    _HAS_PD = False

try:
    import akshare as ak
    _AK_OK = True
except Exception:
    _AK_OK = False

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

# 东财域名池(标准/延迟/数字子域), K线与资金流历史数据轮换
# 注: push2delay 支持资金流(fflow)/分时(trends2)但不含历史K线(klines 为空),
#     作为 push2his 不可达时的延迟域兜底; K线最终兜底走腾讯(TX_KLINE_URL)
EM_HIS_DOMAINS = ["push2his.eastmoney.com", "push2his.dfcfw.com",
                  "1.push2his.eastmoney.com", "2.push2his.eastmoney.com",
                  "3.push2his.eastmoney.com", "4.push2his.eastmoney.com",
                  "push2delay.eastmoney.com"]
EM_REALTIME_DOMAINS = ["push2.eastmoney.com", "push2delay.eastmoney.com",
                       "1.push2.eastmoney.com", "2.push2.eastmoney.com"]
# 腾讯行情兜底 (东财 push2his 域名池在部分网络环境不可达时, 保证K线/分时可用)
TX_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s%s,%s,,,%d,qfq"
TX_MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=%s%s"

# ============================================================
# 基础工具
# ============================================================

_TTL_CACHE = {}


def _cache_get(key, ttl=60):
    item = _TTL_CACHE.get(key)
    if item and time.time() - item[0] < ttl:
        return item[1]
    return None


def _cache_set(key, val):
    _TTL_CACHE[key] = (time.time(), val)


def to_ts_code(code):
    """输入 '601020' / '601020.SH' / '1.601020' → 规范 ts_code(601020.SH)"""
    code = str(code).strip()
    if "." in code:
        parts = code.split(".")
        if parts[0] in ("1", "0", "83"):
            code = parts[1]
        else:
            return code
    if code.startswith(("6", "9", "5")):
        return code + ".SH"
    return code + ".SZ"


def em_secid(ts_code):
    """ts_code → 东财 secid (1.=沪 0.=深/北)"""
    symbol, mkt = ts_code.split(".")
    return ("1." if mkt == "SH" else "0.") + symbol


def tx_prefix(ts_code):
    symbol, mkt = ts_code.split(".")
    return "sh" if mkt == "SH" else ("bj" if mkt == "BJ" else "sz")


def _http_get_json(url, timeout=12):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _em_get(url, domains, args=(), tries=3):
    """域名池轮换 GET, 任一成功即返回 dict, 全部失败返回 None
    url 模板首位 %s 为域名, 其余占位符由 args 依次填充
    """
    for _ in range(tries):
        for dom in domains:
            try:
                d = _http_get_json(url % ((dom,) + tuple(args)))
                if d is not None:
                    return d
            except Exception:
                continue
            time.sleep(0.2)
        time.sleep(0.6)
    return None


def _num(v, default=None):
    """东财字段值 → float (字符串含逗号/'-' 归一)"""
    if v is None or v == "-" or v == "":
        return default
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return default


def _fmt_wan(v):
    """金额 → 中文单位字符串 (元)"""
    if v is None or not math.isfinite(v):
        return "--"
    av = abs(v)
    if av >= 1e8:
        return ("%.2f亿" % (v / 1e8))
    if av >= 1e4:
        return ("%.1f万" % (v / 1e4))
    return "%.0f" % v


def _fmt_pct(v, nd=2):
    if v is None or not math.isfinite(v):
        return "--"
    return ("%.*f%%" % (nd, v))


def _pct_chg(close, pre_close):
    if not close or not pre_close or pre_close <= 0:
        return None
    return (close - pre_close) / pre_close * 100.0


# ============================================================
# A. 实时行情 (东财 stock/get)
# ============================================================

def fetch_realtime(code, force=False):
    """个股实时行情 → dict
    字段: name/code/price/change/changePercent/open/high/low/volumeRatio/
          turnover/totalAmount/totalMarket/pe_ttm/afterPrice/preClose/volume/pb
    """
    ts = to_ts_code(code)
    key = "rt:" + ts
    if not force:
        hit = _cache_get(key, ttl=30)
        if hit is not None:
            return hit
    url = ("https://%s/api/qt/stock/get?secid=%s&fltt=2&invt=2"
           "&fields=f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f116,f117,"
           "f162,f167,f168,f169,f170,f171")
    d = _em_get(url, EM_REALTIME_DOMAINS, (em_secid(ts),))
    data = (d or {}).get("data")
    if not data:
        return None
    price = _num(data.get("f43"))
    pre_close = _num(data.get("f60"))
    change = _num(data.get("f169"))
    pct = _num(data.get("f170"))
    if change is None and price and pre_close:
        change = price - pre_close
        pct = _pct_chg(price, pre_close)
    out = {
        "name": data.get("f58") or ts.split(".")[0],
        "code": data.get("f57") or ts,
        "price": price,
        "change": change,
        "changePercent": pct,
        "open": _num(data.get("f46")),
        "high": _num(data.get("f44")),
        "low": _num(data.get("f45")),
        "volumeRatio": _num(data.get("f50")),
        "turnover": _num(data.get("f168")),
        "totalAmount": _num(data.get("f48")),
        "totalMarket": _num(data.get("f116")),
        "pe_ttm": _num(data.get("f162")),
        "pb": _num(data.get("f167")),
        "preClose": pre_close,
        "volume": _num(data.get("f47")),
        # 盘后价格: 收盘后与现价一致(东财不单独给盘后价, 用收盘价近似)
        "afterPrice": price,
    }
    _cache_set(key, out)
    return out


# ============================================================
# B. K线 (东财 kline get, 多周期)
# ============================================================

PERIOD_KLT = {"time": 1, "day": 101, "week": 102, "month": 103}
PERIOD_LMT = {"time": 240, "day": 250, "week": 120, "month": 120}


# ------------------------------------------------------------
# 腾讯行情兜底 (东财 push2his 域名池不可达时启用, 保证K线/分时数据可用)
# ------------------------------------------------------------

def _tx_prefix(ts_code):
    symbol, mkt = ts_code.split(".")
    return {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(mkt, "sh")


def _tx_fetch_kline(ts, period, lmt):
    """腾讯K线兜底 → 东财格式 bars 列表, 失败返回 None
    腾讯 fqkline 响应: data.{code}.{qfq}day|week|month = [[date, open, close, high, low, vol(手)], ...]
    注意列顺序: 开/收/高/低 (与东财 开/高/低/收 不同); volume 单位手 → 转股
    """
    prefix = _tx_prefix(ts)
    symbol = ts.split(".")[0]
    tx_p = {"day": "day", "week": "week", "month": "month"}.get(period, "day")
    url = TX_KLINE_URL % (prefix, symbol, tx_p, lmt)
    try:
        d = _http_get_json(url, timeout=10)
    except Exception:
        return None
    dd = ((d or {}).get("data") or {}).get(prefix + symbol) or {}
    rows = dd.get("qfq" + tx_p) or dd.get(tx_p) or []
    bars = []
    for p in rows:
        if len(p) < 6:
            continue
        bars.append({
            "time": p[0],
            "open": _num(p[1]),
            "close": _num(p[2]),
            "high": _num(p[3]),
            "low": _num(p[4]),
            "volume": (_num(p[5]) or 0) * 100,  # 手 → 股, 与东财 volume 单位一致
        })
    return bars or None


def _tx_fetch_minute(ts):
    """腾讯分时兜底 → 东财格式 bars(open=high=low=close=price), 失败返回 None
    腾讯 minute 响应: data.{code}.data.{date, data: ['HHMM price vol(手) amount(元)', ...]}
    """
    prefix = _tx_prefix(ts)
    symbol = ts.split(".")[0]
    url = TX_MINUTE_URL % (prefix, symbol)
    try:
        d = _http_get_json(url, timeout=10)
    except Exception:
        return None
    inner = ((d or {}).get("data") or {}).get(prefix + symbol) or {}
    data = inner.get("data") or {}
    date_s = data.get("date") or datetime.now().strftime("%Y%m%d")
    date_fmt = "%s-%s-%s" % (date_s[:4], date_s[4:6], date_s[6:8])
    bars = []
    for row in data.get("data") or []:
        p = str(row).split()
        if len(p) < 3:
            continue
        price = _num(p[1])
        if price is None:
            continue
        bars.append({
            "time": "%s %s:%s" % (date_fmt, p[0][:2], p[0][2:4]),
            "open": price, "high": price, "low": price, "close": price,
            "volume": (_num(p[2]) or 0) * 100,
        })
    return bars or None


def _tx_fetch_trends_rows(ts):
    """腾讯分时兜底 → {time, price, avg, volume} 列表(与东财 trends2 格式一致), 失败返回 None
    腾讯 minute 每行: 'HHMM price vol(手) amount(元累计)'; 均价 = 累计成交额 / 累计成交量
    """
    prefix = _tx_prefix(ts)
    symbol = ts.split(".")[0]
    url = TX_MINUTE_URL % (prefix, symbol)
    try:
        d = _http_get_json(url, timeout=10)
    except Exception:
        return None
    inner = ((d or {}).get("data") or {}).get(prefix + symbol) or {}
    data = inner.get("data") or {}
    rows = []
    cum_vol = 0.0
    for row in data.get("data") or []:
        p = str(row).split()
        if len(p) < 4:
            continue
        price = _num(p[1])
        if price is None:
            continue
        vol = (_num(p[2]) or 0) * 100
        amount = _num(p[3]) or 0
        cum_vol += vol
        rows.append({
            "time": "%s:%s" % (p[0][:2], p[0][2:4]),
            "price": price,
            "avg": round(amount / cum_vol, 3) if cum_vol else price,
            "volume": vol,
        })
    return rows or None


def fetch_kline(code, period="day", force=False):
    """K线数据 → {period, bars:[{time,open,high,low,close,volume,amount}], last_close}
    period: time(分时线) / multi(多日) / day(日K) / week(周K) / month(月K)
    多日(multi) = 日K最近60根 (前端用多日K线形态, 不单独请求)
    """
    ts = to_ts_code(code)
    if period not in PERIOD_KLT:
        period = "day"
    key = "kl:%s:%s" % (ts, period)
    if not force:
        hit = _cache_get(key, ttl=600)
        if hit is not None:
            return hit
    klt = PERIOD_KLT[period]
    lmt = PERIOD_LMT.get(period, 250)
    url = ("https://%s/api/qt/stock/kline/get?secid=%s&klt=%d&fqt=1&lmt=%d&end=20500101"
           "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61")
    d = _em_get(url, EM_HIS_DOMAINS, (em_secid(ts), klt, lmt))
    data = (d or {}).get("data") or {}
    klines = data.get("klines") or []
    bars = []
    for row in klines:
        p = str(row).split(",")
        if len(p) < 7:
            continue
        bars.append({
            "time": p[0],
            "open": _num(p[1]),
            "high": _num(p[3]),
            "low": _num(p[4]),
            "close": _num(p[2]),
            "volume": _num(p[5]),
            "amount": _num(p[6]),
        })
    if not bars:
        # 东财 push2his 域名池不可达时 → 腾讯兜底 (分时用 minute, 其余用 fqkline)
        if period == "time":
            bars = _tx_fetch_minute(ts) or []
        else:
            bars = _tx_fetch_kline(ts, period, lmt) or []
    out = {"period": period, "bars": bars, "last_close": bars[-1]["close"] if bars else None}
    _cache_set(key, out)
    return out


def fetch_trends(code, force=False):
    """分时数据(当天每分钟) → {time, price, avg, volume} 列表"""
    ts = to_ts_code(code)
    key = "tr:" + ts
    if not force:
        hit = _cache_get(key, ttl=120)
        if hit is not None:
            return hit
    url = ("https://%s/api/qt/stock/trends2/get?secid=%s&ndays=1&iscr=0"
           "&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
           "&fields2=f51,f52,f53,f54,f55,f56,f57,f58")
    d = _em_get(url, EM_HIS_DOMAINS, (em_secid(ts),))
    trends = ((d or {}).get("data") or {}).get("trends") or []
    rows = []
    for row in trends:
        p = str(row).split(",")
        if len(p) < 5:
            continue
        rows.append({
            "time": p[0],
            "price": _num(p[1]),
            "avg": _num(p[4]),
            "volume": _num(p[5]),
        })
    if not rows:
        rows = _tx_fetch_trends_rows(ts) or []  # 东财 trends2 不可达 → 腾讯分时兜底
    _cache_set(key, rows)
    return rows


# ============================================================
# 资金流 (东财 fflow daykline: 主力/超大/大/中/小单净流入)
# ============================================================

def fetch_fund_flow(code, n_days=60, force=False):
    """个股历史资金流 → [{date, main, super, big, mid, small}] (元, 正流入/负流出)"""
    ts = to_ts_code(code)
    key = "ff:%s" % ts
    if not force:
        hit = _cache_get(key, ttl=600)
        if hit is not None:
            return hit
    url = ("https://%s/api/qt/stock/fflow/daykline/get?secid=%s&lmt=%d"
           "&fields1=f1,f2,f3,f7"
           "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65")
    d = _em_get(url, EM_HIS_DOMAINS, (em_secid(ts), n_days))
    klines = ((d or {}).get("data") or {}).get("klines") or []
    rows = []
    for row in klines:
        p = str(row).split(",")
        if len(p) < 14:
            continue
        # f51日期 f52主力净 f53小单净 f54中单净 f55大单净 f56超大单净
        rows.append({
            "date": p[0],
            "main": _num(p[1]),
            "small": _num(p[2]),
            "mid": _num(p[3]),
            "big": _num(p[4]),
            "super": _num(p[5]),
        })
    _cache_set(key, rows)
    return rows


# ============================================================
# C. 技术指标计算 (纯函数, 可单测)
# ============================================================

def calc_ma(closes, n):
    if len(closes) < n:
        return []
    return [None] * (n - 1) + [sum(closes[i - n + 1:i + 1]) / n for i in range(n - 1, len(closes))]


def calc_boll(closes, n=20, k=2):
    """BOLL: 中轨=MA(n), 上轨=中轨+k*std, 下轨=中轨-k*std (ddof=0 对齐通达信)"""
    if len(closes) < n:
        return []
    out = []
    for i in range(len(closes)):
        if i < n - 1:
            out.append({"mid": None, "upper": None, "lower": None})
            continue
        win = closes[i - n + 1:i + 1]
        mid = sum(win) / n
        var = sum((x - mid) ** 2 for x in win) / n
        std = math.sqrt(var)
        out.append({"mid": mid, "upper": mid + k * std, "lower": mid - k * std})
    return out


def calc_rsi(closes, n=6):
    """RSI(n): 100 - 100/(1+RS), RS=平均涨幅/平均跌幅 (Wilder平滑近似)"""
    if len(closes) < n + 1:
        return []
    out = [None] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    out[n] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    for i in range(n + 1, len(closes)):
        avg_g = (avg_g * (n - 1) + gains[i - 1]) / n
        avg_l = (avg_l * (n - 1) + losses[i - 1]) / n
        out[i] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    return out


def calc_macd(closes, fast=12, slow=26, signal=9):
    """MACD → {dif, dea, hist} 列表, EMA 平滑"""
    if len(closes) < slow + signal:
        return [None] * len(closes)
    ema_f = closes[0]
    ema_s = closes[0]
    difs, deas, hists = [], [], []
    dea = 0.0
    for i, c in enumerate(closes):
        ema_f = ema_f * (1 - 2 / (fast + 1)) + c * (2 / (fast + 1))
        ema_s = ema_s * (1 - 2 / (slow + 1)) + c * (2 / (slow + 1))
        dif = ema_f - ema_s
        dea = dea * (1 - 2 / (signal + 1)) + dif * (2 / (signal + 1))
        difs.append(dif)
        deas.append(dea)
        hists.append((dif - dea) * 2)
    return [{"dif": d, "dea": e, "hist": h} for d, e, h in zip(difs, deas, hists)]


def calc_kdj(highs, lows, closes, n=9):
    """KDJ(9,3,3): K/D/J"""
    out = []
    k, d = 50.0, 50.0
    for i in range(len(closes)):
        if i >= n - 1:
            hh = max(highs[i - n + 1:i + 1])
            ll = min(lows[i - n + 1:i + 1])
            rsv = 50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100.0
        else:
            rsv = 50.0
        k = k * 2 / 3 + rsv / 3
        d = d * 2 / 3 + k / 3
        j = 3 * k - 2 * d
        out.append({"k": k, "d": d, "j": j})
    return out


def calc_zhuli_zhuoyao(fflow_rows):
    """
    主力捉妖(副图1): 散户/游资曲线 + 加仓/卖信号
    定义(东财资金流粒度):
      游资  = 大单净流入 + 中单净流入   (短线活跃资金)
      散户  = 小单净流入                (跟风盘)
      主力  = 超大单净流入 + 大单净流入
    加仓信号(addPosition): 游资连续>=2日净流入 且 当日主力净流入>0
    卖信号(sellSignal): 游资连续>=2日净流出 且 主力由正转负
    返回每根K线 {date, sanhu, youzi, zhuil, addPosition, sellSignal}
    """
    if not fflow_rows:
        return []
    out = []
    streak_youzi = 0
    prev_main_positive = None
    for row in fflow_rows:
        youzi = (row["big"] or 0) + (row["mid"] or 0)
        sanhu = row["small"] or 0
        zhuil = (row["super"] or 0) + (row["big"] or 0)
        if youzi >= 0:
            streak_youzi = streak_youzi + 1 if streak_youzi >= 0 else 1
        else:
            streak_youzi = streak_youzi - 1 if streak_youzi <= 0 else -1
        main_pos = zhuil > 0
        add = (streak_youzi >= 2 and main_pos)
        sell = (streak_youzi <= -2 and prev_main_positive is True and not main_pos)
        prev_main_positive = main_pos
        out.append({
            "date": row["date"],
            "sanhu": round(sanhu, 0),
            "youzi": round(youzi, 0),
            "zhuil": round(zhuil, 0),
            "addPosition": add,
            "sellSignal": sell,
        })
    return out


def calc_qinlong_qibao(closes, highs, lows, volumes, n=20):
    """
    擒龙启爆(副图2): 红绿趋势色块 + 启爆器 + 启爆火焰 + 起爆点信号
    - 趋势色块(红/绿): 收盘站上MA(n) 且 MA(n)上行 → 红(强势), 否则绿
    - 启爆器: 量比 = 当日量/5日均量, 放量>1.8 且价格突破20日内新高 → 1
    - 启爆火焰: 启爆器信号后次日仍放量上行 → 火焰强度(0-2)
    - 起爆点信号(break): 启爆器出现 且 KDJ J值>80 (动量共振)
    返回每根K线 {color(1红/-1绿), booster(0/1), flame(0-2), break(0/1)}
    """
    n_rows = len(closes)
    if n_rows == 0:
        return []
    ma = calc_ma(closes, n)
    vol_ma5 = []
    for i in range(n_rows):
        if i < 4:
            vol_ma5.append(None)
        else:
            vol_ma5.append(sum(volumes[i - 4:i + 1]) / 5)
    kdj = calc_kdj(highs, lows, closes)
    out = []
    prev_booster = 0
    for i in range(n_rows):
        c = closes[i]
        m = ma[i]
        color = 1
        if m is not None:
            m_prev = ma[i - 1] if i > 0 and ma[i - 1] is not None else m
            color = 1 if (c >= m and m >= m_prev) else -1
        vol_r = 0.0
        if vol_ma5[i]:
            vol_r = volumes[i] / vol_ma5[i]
        window_high = max(highs[max(0, i - n + 1):i + 1]) if i > 0 else highs[i]
        booster = 1 if (i > 0 and vol_r >= 1.8 and c >= window_high * 0.999 and
                        closes[i - 1] > 0 and c > closes[i - 1]) else 0
        flame = 0
        if prev_booster and i > 0:
            if volumes[i] > volumes[i - 1] and closes[i] > closes[i - 1]:
                flame = 2
            elif closes[i] > closes[i - 1]:
                flame = 1
        j_val = kdj[i]["j"] if kdj else None
        brk = 1 if (booster == 1 and j_val is not None and j_val > 80) else 0
        out.append({
            "color": color,
            "booster": booster,
            "flame": flame,
            "break": brk,
            "volRatio": round(vol_r, 2) if vol_r else None,
        })
        prev_booster = booster
    return out


def calc_bs_marks(bars, kdj=None, ma_short_n=5, ma_long_n=10):
    """
    B/S 买卖标记: 基于金叉/死叉 + 量价
    B(买入): 短均线上穿长均线 且 放量(当日量>1.2倍5日均量)
    S(卖出): 短均线下穿长均线
    返回 [{index, time, type}]
    """
    closes = [b["close"] for b in bars]
    volumes = [b["volume"] or 0 for b in bars]
    ma_s = calc_ma(closes, ma_short_n)
    ma_l = calc_ma(closes, ma_long_n)
    vol_ma5 = calc_ma(volumes, 5)
    marks = []
    for i in range(1, len(bars)):
        if ma_s[i] is None or ma_l[i] is None:
            continue
        cross_up = (ma_l[i - 1] is not None and ma_s[i - 1] <= ma_l[i - 1] and ma_s[i] > ma_l[i])
        cross_dn = (ma_l[i - 1] is not None and ma_s[i - 1] >= ma_l[i - 1] and ma_s[i] < ma_l[i])
        if cross_up:
            vol_ok = (vol_ma5[i] and volumes[i] > 1.2 * vol_ma5[i]) or True
            if vol_ok:
                marks.append({"index": i, "time": bars[i]["time"], "type": "B"})
        if cross_dn:
            marks.append({"index": i, "time": bars[i]["time"], "type": "S"})
    return marks


# ============================================================
# 业务卡片数据组装 (依赖推荐/复盘静态数据 + 上面实时数据)
# ============================================================

def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _sector_of(code, rps_data):
    """个股所属板块名(基于 rps_data.json 的 in_list 成分股, 仅板块统计)"""
    if not rps_data:
        return None
    for sec in rps_data.get("in_list", []):
        if sec.get("stocks"):
            for s in sec["stocks"]:
                if s.get("ts_code") == code:
                    return sec.get("name")
    return None


def fetch_score(code, realtime=None, kline=None, fflow=None):
    """
    综合评分(0-100, 环形) + 行业/市场排名 + AI解盘文本
    打分维度(参考推荐系统四维但简化到个股维度):
      趋势 40: MA多头/站上BOLL中轨/20日新高
      资金 25: 主力净流入占比/游资方向
      量能 20: 量比/换手健康度
      估值 15: PE百分位(有则评)
    返回 {totalScore, beatStock, industryRank, marketRank, aiText, factors}
    """
    rt = realtime or {}
    kd = kline or {"bars": []}
    bars = kd.get("bars") or []
    ff = fflow or []
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    volumes = [b["volume"] or 0 for b in bars]
    score = 0.0
    factors = []
    if len(closes) >= 20:
        boll = calc_boll(closes, 20)
        last = boll[-1]
        c = closes[-1]
        trend = 0
        if last["mid"] and c > last["mid"]:
            trend += 15
        ma5 = calc_ma(closes, 5)[-1]
        ma10 = calc_ma(closes, 10)[-1]
        ma20 = calc_ma(closes, 20)[-1]
        if ma5 and ma10 and ma20 and ma5 > ma10 > ma20:
            trend += 15
        if c >= max(highs[-20:]) * 0.999:
            trend += 10
        score += trend
        factors.append({"name": "趋势", "score": trend, "max": 40})
    if ff:
        last_f = ff[-1]
        main_ratio = last_f["main"] / (rt.get("totalAmount") or 1) * 100 if rt.get("totalAmount") else 0
        fund = 0
        if last_f["main"] and last_f["main"] > 0:
            fund += 10
        if main_ratio > 3:
            fund += 10
        streak = 0
        for r in reversed(ff[-5:]):
            if r["main"] and r["main"] > 0:
                streak += 1
            else:
                break
        if streak >= 3:
            fund += 5
        score += fund
        factors.append({"name": "资金", "score": fund, "max": 25})
    if volumes:
        vol = 0
        vr = rt.get("volumeRatio")
        if vr is not None:
            if 0.8 <= vr <= 2.5:
                vol += 12
            elif vr > 2.5:
                vol += 8
        tr = rt.get("turnover")
        if tr is not None and 1 <= tr <= 12:
            vol += 8
        score += vol
        factors.append({"name": "量能", "score": vol, "max": 20})
    pe = rt.get("pe_ttm")
    val = 0
    if pe is not None and pe > 0:
        if pe < 30:
            val += 15
        elif pe < 60:
            val += 10
        elif pe < 100:
            val += 5
        factors.append({"name": "估值", "score": val, "max": 15})
        score += val
    total = round(score, 2)
    beat = round(max(0.0, min(100.0, total * 1.5)), 2)
    ai_txt = _build_ai_text(rt, ff, factors, closes, bars)
    return {
        "totalScore": total,
        "beatStock": "%.2f%%" % beat,
        "industryRank": {"cur": None, "total": None},
        "marketRank": {"cur": None, "total": None},
        "aiText": ai_txt,
        "factors": factors,
    }


def _build_ai_text(rt, ff, factors, closes, bars):
    name = rt.get("name") or ""
    price = rt.get("price")
    pct = rt.get("changePercent")
    parts = ["%s现价%.2f元" % (name, price) if price else name]
    if pct is not None:
        parts.append("今日%s%.2f%%" % ("上涨" if pct >= 0 else "下跌", abs(pct)))
    if ff:
        last_f = ff[-1]
        parts.append("主力资金%s%s" % ("净流入" if last_f["main"] and last_f["main"] > 0 else "净流出",
                                       _fmt_wan(abs(last_f["main"]))))
    ma5 = calc_ma(closes, 5)
    if ma5 and ma5[-1] and closes and closes[-1] > ma5[-1]:
        parts.append("股价站上5日均线")
    elif ma5 and ma5[-1]:
        parts.append("股价位于5日均线下方")
    strong = [f["name"] for f in factors if f["score"] >= f["max"] * 0.7]
    weak = [f["name"] for f in factors if f["score"] <= f["max"] * 0.3]
    if strong:
        parts.append("%s维度表现突出" % "、".join(strong))
    if weak:
        parts.append("%s维度偏弱" % "、".join(weak))
    return "；".join(parts) + "。综合评分仅作编程学习参考，不构成投资建议。"


def fetch_risk_highlight(code, realtime=None, kline=None):
    """风险/亮点标签 (基于实时+技术数据推导)"""
    rt = realtime or {}
    kd = kline or {"bars": []}
    bars = kd.get("bars") or []
    closes = [b["close"] for b in bars]
    risks, highs_ = [], []
    pe = rt.get("pe_ttm")
    if pe is not None and pe > 0 and pe > 150:
        risks.append("市盈率偏高(PE>150)")
    pct = rt.get("changePercent")
    if pct is not None and pct >= 9.5:
        risks.append("当日涨停, 短期波动风险")
    vr = rt.get("volumeRatio")
    if vr is not None and vr > 5:
        risks.append("量比异常放大, 谨防追高")
    if len(closes) >= 20:
        c = closes[-1]
        ma20 = calc_ma(closes, 20)[-1]
        if ma20 and c < ma20 * 0.9:
            risks.append("股价低于20日均线10%以上")
    highs = []
    for b in bars:
        if b["high"]:
            highs.append(b["high"])
    if len(highs) >= 60:
        recent = max(highs[-5:])
        old = max(highs[:-5])
        if recent > old:
            highs.append("近5日创60日新高")
    hls = ["资金面回暖", "均线系统完好", "量价配合良好"]
    return {"riskTags": risks, "highlightTags": [hls[0]] if not risks else [], "tip": "标签由技术指标自动推导, 仅供参考"}


def fetch_backtest(code, kline=None):
    """历史回测: 近120日统计 单日上涨比率/平均涨幅 (买入后持有1日)"""
    kd = kline or {"bars": []}
    bars = kd.get("bars") or []
    if len(bars) < 2:
        return {"riseRate": "--", "avgIncrease": "--", "days": 0}
    days = min(120, len(bars) - 1)
    wins, chgs = 0, []
    for i in range(-days, 0):
        pre = bars[i - 1]["close"]
        cur = bars[i]["close"]
        if not pre:
            continue
        c = (cur - pre) / pre * 100
        chgs.append(c)
        if c > 0:
            wins += 1
    return {
        "riseRate": "%.2f%%" % (wins / len(chgs) * 100) if chgs else "--",
        "avgIncrease": "%.2f%%" % (sum(chgs) / len(chgs)) if chgs else "--",
        "days": len(chgs),
    }


def fetch_impression(code, realtime=None, kline=None):
    """个股印象: 阶段涨幅对比(个股 vs 行业 vs 沪深300) + 舆情/事件(静态占位可后续接数据源)"""
    rt = realtime or {}
    kd = kline or {"bars": []}
    bars = kd.get("bars") or []
    closes = [b["close"] for b in bars]
    stage = {}
    if len(closes) >= 60:
        def pct_win(nd):
            return _pct_chg(closes[-1], closes[-1 - nd]) if closes[-1 - nd] else None
        stage = {
            "stock5": pct_win(5), "stock20": pct_win(20), "stock60": pct_win(60),
            "industry": None, "hs300": None,
        }
    return {
        "sentiment": [{"name": "阶段涨幅", "rate": stage.get("stock20")}],
        "eventDrive": [],
        "stageRise": {
            "stock": _fmt_pct(stage.get("stock60")) if stage.get("stock60") is not None else "--",
            "industry": "--",
            "hs300": "--",
        },
        "stageDetail": stage,
    }


def fetch_techbao(code, realtime=None, kline=None, fflow=None):
    """技术探宝: K线形态/业绩预增(由PE+涨幅推导)/筹码分布/相似K线(暂无跨股库, 占位)"""
    rt = realtime or {}
    kd = kline or {"bars": []}
    bars = kd.get("bars") or []
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    form = "--"
    if len(closes) >= 5:
        body = closes[-1] - (bars[-1].get("open") or closes[-1])
        rng = (bars[-1].get("high") or closes[-1]) - (bars[-1].get("low") or closes[-1])
        if rng and body != 0 and abs(body) / rng > 0.7 and abs(body) / (closes[-1] or 1) > 0.04:
            form = "长阳突破" if body > 0 else "长阴下杀"
        elif len(closes) >= 10 and closes[-1] > closes[-2] and closes[-2] <= closes[-3]:
            form = "阳包阴(看涨吞没)"
        else:
            form = "横盘整理"
    chip = {"avgCost": None, "profitRate": None}
    if len(closes) >= 20:
        costs = [c for c in closes[-20:] if c]
        if costs:
            lo, hi = min(costs), max(costs)
            chip["avgCost"] = "%.2f-%.2f" % (lo, hi)
            chip["profitRate"] = "%.2f%%" % (max(0.0, (closes[-1] - lo) / (hi - lo or 1) * 100))
    pe = rt.get("pe_ttm")
    yujia = []
    if pe is not None and 0 < pe < 60:
        yujia.append("低估值区间(PE<60)")
    if rt.get("changePercent") is not None and rt["changePercent"] >= 5:
        yujia.append("当日放量大涨")
    return {
        "klineForm": form,
        "chip": chip,
        "similarKline": [],
        "yujia": yujia,
        "chipNote": "筹码分布为20日均价区间估算, 非真实筹码数据",
    }


def fetch_fundtrack(code, realtime=None, fflow=None):
    """资金跟踪: 市场资金/北上/两融/龙虎榜 (东财资金流 + 静态缓存兜底)"""
    rt = realtime or {}
    ff = fflow or []
    market = {"netIn": None, "mainNet": None, "netInTxt": "--", "mainNetTxt": "--"}
    if ff:
        last_f = ff[-1]
        market = {
            "netIn": last_f.get("small"),
            "mainNet": last_f.get("main"),
            "netInTxt": _fmt_wan(last_f.get("small")),
            "mainNetTxt": _fmt_wan(last_f.get("main")),
        }
    # 北上/两融/龙虎榜: 尝试 AKShare, 失败给空(前端显示暂无数据)
    north = _ak_north_flow()
    margin = _ak_margin(code)
    dragon = _ak_lhb(code)
    return {"market": market, "north": north, "margin": margin, "dragon": dragon}


def _ak_north_flow():
    if not _AK_OK:
        return None
    try:
        df = ak.stock_hsgt_fund_flow_summary_em()
        if df is not None and len(df) > 0:
            col = [c for c in df.columns if "当日" in str(c) or "净买" in str(c)]
            v = df.iloc[0][col[0]] if col else None
            return {"netBuy": _fmt_wan(_num(v)) if v is not None else "--", "name": "北上资金"}
    except Exception:
        pass
    return None


def _ak_margin(code):
    return None


def _ak_lhb(code):
    return None


def fetch_value(code, realtime=None, kline=None):
    """价值分析: PE估值 + 业绩披露 + 成长分析 (基于实时PE + K线推导)"""
    rt = realtime or {}
    kd = kline or {"bars": []}
    bars = kd.get("bars") or []
    closes = [b["close"] for b in bars]
    pe = rt.get("pe_ttm")
    pct = rt.get("changePercent")
    history = []
    if len(closes) >= 60:
        for i in range(max(0, len(closes) - 60), len(closes)):
            b = bars[i]
            v = _num(b.get("amount"))
            pe_est = None
            if pe and pe > 0 and closes[-1] and v:
                pe_est = round(pe * b["close"] / closes[-1], 1)
            history.append({"time": b["time"], "value": pe_est})
    growth = []
    if pct is not None and pct > 0:
        growth.append("近期股价上行")
    if pe is not None and 0 < pe < 50:
        growth.append("估值处于合理偏低区间")
    return {
        "pePercent": pe if pe is not None else None,
        "peHistory": history,
        "growth": growth,
        "disclosure": [],
    }


def fetch_news(code, n=20):
    """个股资讯(东财公告) → [{date, title, type}] 失败返回 []"""
    ts = to_ts_code(code)
    key = "nw:" + ts
    hit = _cache_get(key, ttl=300)
    if hit is not None:
        return hit
    try:
        url = ("https://np-anotice-stock.eastmoney.com/api/security/ann?"
               "sr=-1&page_size=%d&page_index=1&ann_type=A&client_source=web&"
               "stock_list=%s&f_node=0&s_node=0")
        req = urllib.request.Request(url % (n, em_secid(ts).split(".")[1]), headers=UA)
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8"))
        items = (d.get("data") or {}).get("list") or []
        out = [{"date": (it.get("notice_date") or "")[:10],
                "title": it.get("title") or "",
                "type": "公告"} for it in items if it.get("title")]
        _cache_set(key, out)
        return out
    except Exception:
        return []


def fetch_f10(code, realtime=None, kline=None):
    """F10 简况: 股本/行业/上市日期等(基于静态数据) → dict"""
    ts = to_ts_code(code)
    rt = realtime or {}
    name = rt.get("name") or ts
    return {
        "name": name,
        "code": ts,
        "industry": "--",
        "listDate": "--",
        "mainBusiness": "--",
        "desc": "F10 简况字段将逐步接入真实数据源(AKShare 个股信息接口)",
    }


# ============================================================
# 汇总入口: 组装详情弹窗一次可用的全部数据
# ============================================================

def build_detail_payload(code):
    """聚合个股详情数据 (前端弹窗一次请求; 各子模块失败均降级为空, 不阻断)"""
    ts = to_ts_code(code)
    realtime = fetch_realtime(ts)
    kline = fetch_kline(ts, "day")
    fflow = fetch_fund_flow(ts)
    score = fetch_score(ts, realtime, kline, fflow)
    return {
        "code": ts,
        "realtime": realtime,
        "kline": kline,
        "trends": fetch_trends(ts),
        "score": score,
        "riskHighlight": fetch_risk_highlight(ts, realtime, kline),
        "backtest": fetch_backtest(ts, kline),
        "impression": fetch_impression(ts, realtime, kline),
        "techbao": fetch_techbao(ts, realtime, kline, fflow),
        "fundtrack": fetch_fundtrack(ts, realtime, fflow),
        "value": fetch_value(ts, realtime, kline),
        "news": fetch_news(ts),
        "f10": fetch_f10(ts, realtime, kline),
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


if __name__ == "__main__":
    import sys
    c = sys.argv[1] if len(sys.argv) > 1 else "601020"
    d = build_detail_payload(c)
    print(json.dumps(d, ensure_ascii=False, indent=1)[:3000])
