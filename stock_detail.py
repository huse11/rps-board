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
# fq 后缀: qfq(前复权) / hfq(后复权) / 空(不复权)
TX_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s%s,%s,,,%d%s"
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
    """输入 '601020' / '601020.SH' / '1.601020' → 规范 ts_code(601020.SH)
    非法输入(空串/非6位数字/多段式/未知市场)返回 None → 调用方降级为数据获取失败
    """
    if code is None:
        return None
    code = str(code).strip().upper()
    if not code:
        return None
    if "." in code:
        parts = code.split(".")
        if len(parts) == 2 and parts[0] in ("1", "0", "83") and parts[1].isdigit():
            code = parts[1]  # 东财 secid 格式 "1.601020"
        elif len(parts) == 2 and parts[0].isdigit() and parts[1] in ("SH", "SZ", "BJ"):
            return parts[0] + "." + parts[1]
        else:
            return None
    if not code.isdigit() or len(code) != 6:
        return None
    if code.startswith(("6", "9", "5")):
        return code + ".SH"
    return code + ".SZ"


def em_secid(ts_code):
    """ts_code → 东财 secid (1.=沪 0.=深/北); 非法输入返回 None"""
    parts = str(ts_code or "").split(".")
    if len(parts) != 2 or not parts[0].isdigit() or parts[1] not in ("SH", "SZ", "BJ"):
        return None
    symbol, mkt = parts
    return ("1." if mkt == "SH" else "0.") + symbol


def tx_prefix(ts_code):
    parts = str(ts_code or "").split(".")
    if len(parts) != 2 or not parts[0].isdigit() or parts[1] not in ("SH", "SZ", "BJ"):
        return None
    symbol, mkt = parts
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

def market_status(rt):
    """行情状态标签: 盘中 / 收盘 / 盘后 / 停牌 (纯展示, 仅看盘)
    停牌: 无行情数据或成交量为0
    盘中: 交易日 9:30-11:30 / 13:00-15:00
    盘后: 收盘后至 18:00 (晚间数据)
    其余(周末/盘前等) → 收盘
    """
    if not rt or rt.get("price") is None or (rt.get("volume") or 0) == 0:
        return "停牌"
    now = datetime.now()
    if now.weekday() >= 5:
        return "收盘"
    hm = now.hour * 60 + now.minute
    if (9 * 60 + 30) <= hm <= (11 * 60 + 30) or (13 * 60) <= hm <= (15 * 60):
        return "盘中"
    if (15 * 60) < hm <= (18 * 60):
        return "盘后"
    return "收盘"


def fetch_realtime(code, force=False):
    """个股实时行情 → dict
    字段: name/code/price/change/changePercent/open/high/low/volumeRatio/
          turnover/amplitude/totalAmount/totalMarket/circ_mv/pe_ttm/pb/
          preClose/volume/marketStatus
    """
    ts = to_ts_code(code)
    if not ts:
        return None
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
    high = _num(data.get("f44"))
    low = _num(data.get("f45"))
    # 振幅 = (最高-最低)/昨收*100
    amplitude = round((high - low) / pre_close * 100.0, 2) if (high is not None and low is not None and pre_close) else None
    out = {
        "name": data.get("f58") or ts.split(".")[0],
        "code": data.get("f57") or ts,
        "price": price,
        "change": change,
        "changePercent": pct,
        "open": _num(data.get("f46")),
        "high": high,
        "low": low,
        "volumeRatio": _num(data.get("f50")),
        "turnover": _num(data.get("f168")),
        "amplitude": amplitude,
        "totalAmount": _num(data.get("f48")),
        "totalMarket": _num(data.get("f116")),
        "circ_mv": _num(data.get("f117")),  # 流通市值
        "pe_ttm": _num(data.get("f162")),
        "pb": _num(data.get("f167")),
        "preClose": pre_close,
        "volume": _num(data.get("f47")),
        # 盘后价格: 收盘后与现价一致(东财不单独给盘后价, 用收盘价近似)
        "afterPrice": price,
        "marketStatus": None,  # 占位, 下方统一填充
    }
    out["marketStatus"] = market_status(out)
    _cache_set(key, out)
    return out


# ============================================================
# B. K线 (东财 kline get, 多周期)
# ============================================================

PERIOD_KLT = {"time": 1, "multi": 101, "day": 101, "week": 102, "month": 103}
PERIOD_LMT = {"time": 240, "multi": 60, "day": 250, "week": 120, "month": 120}


# ------------------------------------------------------------
# 腾讯行情兜底 (东财 push2his 域名池不可达时启用, 保证K线/分时数据可用)
# ------------------------------------------------------------

def _tx_prefix(ts_code):
    parts = str(ts_code or "").split(".")
    if len(parts) != 2 or not parts[0].isdigit():
        return None
    return {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(parts[1], "sh")


def _tx_fetch_kline(ts, period, lmt, fqt="qfq"):
    """腾讯K线兜底 → 东财格式 bars 列表, 失败返回 None
    腾讯 fqkline 响应: data.{code}.{qfq|hfq}{day|week|month} = [[date, open, close, high, low, vol(手)], ...]
    注意列顺序: 开/收/高/低 (与东财 开/高/低/收 不同); volume 单位手 → 转股
    fqt: qfq(前复权, 腾讯前缀 qfq) / hfq(后复权, 前缀 hfq) / none(不复权, 无前缀)
    """
    prefix = _tx_prefix(ts)
    symbol = ts.split(".")[0]
    tx_p = {"day": "day", "week": "week", "month": "month"}.get(period, "day")
    # 腾讯 fqkline 复权后缀: qfq/hfq/空(不复权); 不复权接口带尾逗号且数据在 day 键
    if fqt == "none":
        url = TX_KLINE_URL % (prefix, symbol, tx_p, lmt, ",")
    else:
        url = TX_KLINE_URL % (prefix, symbol, tx_p, lmt, "," + ({"qfq": "qfq", "hfq": "hfq"}.get(fqt, "qfq")))
    try:
        d = _http_get_json(url, timeout=10)
    except Exception:
        return None
    dd = ((d or {}).get("data") or {}).get(prefix + symbol) or {}
    if fqt == "none":
        rows = dd.get(tx_p) or []
    else:
        fq = {"qfq": "qfq", "hfq": "hfq"}.get(fqt, "qfq")
        rows = dd.get(fq + tx_p) or dd.get(tx_p) or []
    bars = []
    for p in rows:
        if len(p) < 6:
            continue
        close = _num(p[2])
        vol = (_num(p[5]) or 0) * 100  # 手 → 股, 与东财 volume 单位一致
        bars.append({
            "time": p[0],
            "open": _num(p[1]),
            "close": close,
            "high": _num(p[3]),
            "low": _num(p[4]),
            "volume": vol,
            "amount": round(close * vol, 0) if close and vol else None,  # 成交额近似(东财无此字段)
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


def fetch_kline(code, period="day", fqt="qfq", force=False, days=5):
    """K线数据 → {period, fqt, bars:[{time,open,high,low,close,volume,amount}], last_close}
    period: time(分时线) / multi(多日分时) / day(日K) / week(周K) / month(月K)
    fqt: qfq(前复权) / hfq(后复权) / none(不复权)
    days: 仅 multi 生效, 最近 N 个交易日的分时拼接天数 (默认 5)
    time/multi → {period, fqt, days:[{date, points:[{time,price,avg,volume}]}], last_close}
    day/week/month → {period, fqt, bars:[{time,open,high,low,close,volume,amount}], last_close}
    """
    ts = to_ts_code(code)
    if not ts:
        return None
    if period not in PERIOD_KLT:
        period = "day"
    if fqt not in ("qfq", "hfq", "none"):
        fqt = "qfq"
    days = max(1, min(int(days or 5), 20))
    key = ("kl:%s:%s:%s:d%d" % (ts, period, fqt, days)) if period == "multi" \
        else "kl:%s:%s:%s" % (ts, period, fqt)
    if not force:
        hit = _cache_get(key, ttl=600)
        if hit is not None:
            return hit
    if period in ("time", "multi"):
        # 分时/多日: 真正的逐分钟分时数据 (东财 trends2 ndays), 按交易日分组首尾拼接
        n = 1 if period == "time" else days
        rows = fetch_multiday_trends(ts, n, force=force)
        if not rows:
            # 降级: 多日分时源(push2his 系)临时不可达 → 退回当日分时 (push2delay / 腾讯兜底),
            # 保证个股详情默认"多日"Tab 不白屏 (days 单元素标记 degraded 供前端提示)
            one = fetch_trends(ts, force=force)  # 东财单日分时, 含完整日期
            if not one and period == "time":
                one = _tx_fetch_trends_rows(ts)  # 单日分时腾讯兜底 (time 仅 HH:MM)
            if one:
                date = str(one[0].get("time", ""))[:10]
                rows = [{"date": date if len(date) == 10 else "最近交易日",
                         "points": one, "degraded": True}]
        if not rows:
            return None
        out = {"period": period, "fqt": fqt, "days": rows,
               "last_close": rows[-1]["points"][-1]["price"] if rows and rows[-1]["points"] else None}
        _cache_set(key, out)
        return out
    klt = PERIOD_KLT[period]
    lmt = PERIOD_LMT.get(period, 250)
    fqt_v = {"qfq": 1, "hfq": 2, "none": 0}[fqt]
    url = ("https://%s/api/qt/stock/kline/get?secid=%s&klt=%d&fqt=%d&lmt=%d&end=20500101"
           "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61")
    d = _em_get(url, EM_HIS_DOMAINS, (em_secid(ts), klt, fqt_v, lmt))
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
        # 东财 push2his 域名池不可达时 → 腾讯兜底 (分时用 minute, 其余用 fqkline; 腾讯仅支持前复权/不复权)
        bars = _tx_fetch_kline(ts, period, lmt, fqt) or []
    out = {"period": period, "fqt": fqt, "bars": bars, "last_close": bars[-1]["close"] if bars else None}
    _cache_set(key, out)
    return out


def fetch_multiday_trends(code, days=5, force=False):
    """多日分时 → [{date:"YYYY-MM-DD", points:[{time,price,avg,volume}]}], 按交易日正序分组
    数据源: 东财 trends2 (ndays 单次上限 5, 且无日期偏移参数 → 无法翻页取更早交易日;
    超出上限的天数退化为最近 5 日, 不产生乱序/重复脏数据); 失败/无数据返回 []
    """
    ts = to_ts_code(code)
    if not ts:
        return []
    days = max(1, min(int(days or 5), 20))
    key = "mt:%s:%d" % (ts, days)
    if not force:
        hit = _cache_get(key, ttl=120)
        if hit is not None:
            return hit
    # 多日分时仅 push2his 系支持 (push2delay 只返回最近 1 天, 会误伤多日拼接)
    trend_domains = [d for d in EM_HIS_DOMAINS if "delay" not in d]
    url = ("https://%s/api/qt/stock/trends2/get?secid=%s&ndays=%d&iscr=0"
           "&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
           "&fields2=f51,f52,f53,f54,f55,f56,f57,f58")
    chunk = min(days, 5)  # trends2 单次最多返回 5 个交易日
    d = _em_get(url, trend_domains, (em_secid(ts), chunk))
    rows = ((d or {}).get("data") or {}).get("trends") or []
    day_map = {}
    order = []
    for row in rows:
        p = str(row).split(",")
        if len(p) < 8:
            continue
        full = p[0]  # 东财 trends2 f51: "YYYY-MM-DD HH:MM"
        date = str(full)[:10]
        if not date or len(str(full)) < 11:
            continue
        day_map.setdefault(date, []).append({
            "time": full,
            "price": _num(p[2]),
            "avg": _num(p[7]),
            "volume": _num(p[5]),
        })
        if date not in order:
            order.append(date)
    out = [{"date": d, "points": day_map[d]} for d in order]
    if out:
        _cache_set(key, out)
    return out


def fetch_trends(code, force=False):
    """分时数据(当天每分钟) → {time, price, avg, volume} 列表"""
    ts = to_ts_code(code)
    if not ts:
        return None
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
        if len(p) < 8:
            continue
        # 东财 trends2 列序: f51时间 f52开盘 f53收盘(最新价) f54最高 f55最低 f56成交量 f57成交额 f58均价
        rows.append({
            "time": p[0],
            "price": _num(p[2]),
            "avg": _num(p[7]),
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
    if not ts:
        return []
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


def calc_bs_marks(bars, fast=5, slow=10):
    """B/S 历史信号点: MA(fast) 上穿 MA(slow) → B(金叉), 下穿 → S(死叉)
    纯历史指标信号标记(仅供查看, 不可点击/不可交易):
      - 连续重复信号做过滤: 同一方向拐点只标记首次, 图表更干净
    返回 [{"time", "type": "B"|"S"}] 按时间正序
    """
    if not bars:
        return []
    closes = [b["close"] for b in bars]
    if len(closes) < slow + 1:
        return []
    ma_f = calc_ma(closes, fast)
    ma_s = calc_ma(closes, slow)
    marks = []
    prev_cross = None
    for i in range(slow, len(closes)):
        mf, ms = ma_f[i], ma_s[i]
        mf0, ms0 = ma_f[i - 1], ma_s[i - 1]
        if mf is None or ms is None or mf0 is None or ms0 is None:
            continue
        if mf0 <= ms0 and mf > ms:
            cross = "B"
        elif mf0 >= ms0 and mf < ms:
            cross = "S"
        else:
            cross = None
        if cross is not None:
            if cross != prev_cross:  # 去噪: 同方向连续只标记一次
                marks.append({"time": bars[i]["time"], "type": cross})
            prev_cross = cross
    return marks


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
    prev_add_raw, prev_sell_raw = False, False  # 去噪: 连续同方向信号只标记首次(记录原始条件, 持续抑制)
    for row in fflow_rows:
        youzi = (row["big"] or 0) + (row["mid"] or 0)
        sanhu = row["small"] or 0
        zhuil = (row["super"] or 0) + (row["big"] or 0)
        if youzi >= 0:
            streak_youzi = streak_youzi + 1 if streak_youzi >= 0 else 1
        else:
            streak_youzi = streak_youzi - 1 if streak_youzi <= 0 else -1
        main_pos = zhuil > 0
        add_raw = (streak_youzi >= 2 and main_pos)
        sell_raw = (streak_youzi <= -2 and prev_main_positive is True and not main_pos)
        add = add_raw and not prev_add_raw
        sell = sell_raw and not prev_sell_raw
        prev_add_raw, prev_sell_raw = add_raw, sell_raw
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
    prev_break = 0  # 去噪: 起爆点连续出现只标记首次
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
        brk = 1 if (booster == 1 and j_val is not None and j_val > 80 and not prev_break) else 0
        out.append({
            "color": color,
            "booster": booster,
            "flame": flame,
            "break": brk,
            "volRatio": round(vol_r, 2) if vol_r else None,
        })
        prev_booster = booster
        prev_break = brk
    return out


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
    if not ts:
        return []
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
    if not ts:
        return None
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
# AI 综合分析: 合并综评 Tab 全部零散卡片为单页个股总分析
# 维度: 速览徽章 / 基本面 / 核心亮点 / 风险提示 / 技术面 /
#       资金面 / 买卖建议 / 关键价位 / 板块热度
# ============================================================

def _val_level_pe(pe):
    """PE 估值档位 → 标签 + 分数"""
    if pe is None or pe <= 0:
        return "亏损/未披露", 0
    if pe < 15: return "严重低估", 5
    if pe < 30: return "低估", 4
    if pe < 50: return "合理", 3
    if pe < 80: return "偏高", 2
    if pe < 150: return "高估", 1
    return "泡沫区", 0


def _val_level_pb(pb):
    """PB 估值档位 → 标签"""
    if pb is None or pb <= 0: return "--"
    if pb < 1: return "破净"
    if pb < 2: return "偏低"
    if pb < 4: return "合理"
    if pb < 8: return "偏高"
    return "高溢价"


def _sector_membership(ts_code):
    """个股所属 RPS 强势板块列表 → [(板块名, 档位集合)]"""
    rps = _load_json("static/rps_data.json") or {}
    result = []
    for tier_key, tier_name in [("rps5", "RPS5"), ("rps10", "RPS10"), ("rps20", "RPS20")]:
        tier = rps.get(tier_key) or {}
        for which in ["in_list", "out_list"]:
            for sec in (tier.get(which) or []):
                for s in (sec.get("stocks") or []):
                    if s.get("ts_code") == ts_code:
                        entry = {"name": sec.get("name", "--"), "tier": tier_name,
                                 "status": "在榜" if which == "in_list" else "调出"}
                        if entry not in result:
                            result.append(entry)
    return result


def _pe_history_percentile(pe_est, pe_hist):
    """当前 PE 在历史区间中的百分位 (0%=最低估, 100%=最高估)"""
    if not pe_hist or pe_est is None:
        return None
    vals = [float(h["value"]) for h in pe_hist if h.get("value") is not None]
    if not vals:
        return None
    below = sum(1 for v in vals if v <= pe_est)
    return round(below / len(vals) * 100, 1)


def fetch_ai_analysis(code):
    """
    个股 AI 综合总分析 → 单页结构化报告
    替代 综评 Tab 中 5 个零散卡片(评分/风险/回测/印象/价值)
    返回 dict:
      overview, fundamentals, highlights, risks, technical, capital,
      action, key_levels, sector_heat, generated_at
    """
    ts = to_ts_code(code)
    if not ts:
        return None
    realtime = fetch_realtime(ts) or {}
    kline = fetch_kline(ts, "day") or {"bars": []}
    fflow = fetch_fund_flow(ts) or []
    bars = kline.get("bars") or []
    closes = [b["close"] for b in bars if b.get("close") is not None]
    highs = [b["high"] for b in bars if b.get("high") is not None]
    lows = [b["low"] for b in bars if b.get("low") is not None]
    vols = [b.get("volume") or 0 for b in bars]

    # ---------- 0. 中间计算: 技术指标 ----------
    n = len(closes)
    ma5 = calc_ma(closes, 5)[-1] if n >= 5 else None
    ma10 = calc_ma(closes, 10)[-1] if n >= 10 else None
    ma20 = calc_ma(closes, 20)[-1] if n >= 20 else None
    ma60 = calc_ma(closes, 60)[-1] if n >= 60 else None
    last_c = closes[-1] if closes else None

    boll20 = calc_boll(closes, 20)
    boll_last = boll20[-1] if (n >= 20 and boll20) else None
    macd_arr = calc_macd(closes) if n >= 35 else []
    macd_last = macd_arr[-1] if macd_arr else None
    kdj_arr = calc_kdj(highs, lows, closes) if n >= 9 else []
    kdj_last = kdj_arr[-1] if kdj_arr else None
    rsi_arr = calc_rsi(closes, 6) if n >= 7 else []
    rsi_last = rsi_arr[-1] if rsi_arr and rsi_arr[-1] is not None else None

    # 均线多头/空头
    if ma5 and ma10 and ma20:
        ma_align = "多头排列" if (ma5 > ma10 > ma20) else ("空头排列" if (ma5 < ma10 < ma20) else "交织")
    else:
        ma_align = "数据不足"

    # MACD 信号
    if macd_last:
        if macd_last["dif"] > macd_last["dea"] and macd_last["hist"] > 0:
            macd_sig = "红柱放大(多头)" if n > 1 and macd_arr[-2]["hist"] <= macd_last["hist"] else "红柱缩小"
        elif macd_last["dif"] < macd_last["dea"] and macd_last["hist"] < 0:
            macd_sig = "绿柱放大(空头)" if n > 1 and abs(macd_arr[-2]["hist"]) <= abs(macd_last["hist"]) else "绿柱缩小"
        else:
            macd_sig = "拐头区域"
        macd_zero = "零轴上" if macd_last["dif"] > 0 else "零轴下"
    else:
        macd_sig, macd_zero = "--", "--"

    # KDJ 信号
    if kdj_last:
        if kdj_last["j"] > 90:
            kdj_sig = "超买(J>90)"
        elif kdj_last["j"] < 10:
            kdj_sig = "超卖(J<10)"
        elif kdj_last["k"] > kdj_last["d"] and n > 1 and kdj_arr[-2]["k"] <= kdj_arr[-2]["d"]:
            kdj_sig = "金叉形成"
        elif kdj_last["k"] < kdj_last["d"] and n > 1 and kdj_arr[-2]["k"] >= kdj_arr[-2]["d"]:
            kdj_sig = "死叉形成"
        elif kdj_last["k"] > kdj_last["d"]:
            kdj_sig = "偏多运行"
        else:
            kdj_sig = "偏空运行"
    else:
        kdj_sig = "--"

    # RSI 信号
    if rsi_last is not None:
        if rsi_last > 80:
            rsi_sig = "严重超买"
        elif rsi_last > 70:
            rsi_sig = "超买区间"
        elif rsi_last < 20:
            rsi_sig = "严重超卖"
        elif rsi_last < 30:
            rsi_sig = "超卖区间"
        else:
            rsi_sig = "中性区域"
    else:
        rsi_sig = "--"

    # BOLL 位置
    if boll_last and last_c and boll_last["upper"]:
        if last_c >= boll_last["upper"]:
            boll_sig = "突破上轨(强势/警惕回调)"
        elif last_c <= boll_last["lower"]:
            boll_sig = "跌破下轨(弱势/或有反弹)"
        elif last_c >= boll_last["mid"]:
            boll_sig = "中轨上方(偏强)"
        else:
            boll_sig = "中轨下方(偏弱)"
    else:
        boll_sig = "--"

    # ---------- 0b. 中间计算: 资金面 ----------
    main_today = fflow[-1]["main"] if fflow else None
    main_3d = sum((r["main"] or 0) for r in fflow[-3:]) if len(fflow) >= 3 else None
    main_5d = sum((r["main"] or 0) for r in fflow[-5:]) if len(fflow) >= 5 else None
    # 主力连续流入天数
    main_streak = 0
    for r in reversed(fflow):
        if (r.get("main") or 0) > 0:
            main_streak += 1
        else:
            break
    # 小单(散户)反向指标: 连续散户流入=跟风, 连续流出=洗盘
    small_5d = sum((r["small"] or 0) for r in fflow[-5:]) if len(fflow) >= 5 else None

    # 量比 + 换手健康度
    vr = realtime.get("volumeRatio")
    tr = realtime.get("turnover")
    if vr is not None and tr is not None:
        if 1.0 <= vr <= 2.5 and 2.0 <= tr <= 10:
            vol_health = "量价健康"
        elif vr > 3.0 and tr > 12:
            vol_health = "异常放量(警惕分歧)"
        elif vr < 0.7 and tr < 1.0:
            vol_health = "极度缩量(无人问津)"
        else:
            vol_health = "量能一般"
    else:
        vol_health = "--"

    # ---------- 0c. 中间计算: 回测 + 阶段涨幅 ----------
    bt = fetch_backtest(ts, kline)
    rise_rate = None
    avg_inc = None
    if bt and bt.get("riseRate") != "--":
        try:
            rise_rate = float(str(bt["riseRate"]).replace("%", ""))
        except (TypeError, ValueError):
            pass
    if bt and bt.get("avgIncrease") != "--":
        try:
            avg_inc = float(str(bt["avgIncrease"]).replace("%", ""))
        except (TypeError, ValueError):
            pass

    pct5 = _pct_chg(closes[-1], closes[-1 - 5]) if n > 5 else None
    pct20 = _pct_chg(closes[-1], closes[-1 - 20]) if n > 20 else None
    pct60 = _pct_chg(closes[-1], closes[-1 - 60]) if n > 60 else None

    # ---------- 0d. 中间计算: 板块热度 + 估值历史 ----------
    sectors = _sector_membership(ts)
    strong_sectors = [s for s in sectors if s["status"] == "在榜"]
    sector_tiers = set(s["tier"] for s in strong_sectors)
    if len(sector_tiers) >= 3:
        sector_heat = "🔥 三档共振(极强势板块)"
    elif len(sector_tiers) == 2:
        sector_heat = "⭐ 双档共振(强势板块)"
    elif len(sector_tiers) == 1:
        sector_heat = "✅ 单档入选(关注)"
    elif sectors:
        sector_heat = "📉 已从强势板块调出"
    else:
        sector_heat = "— 非强势板块个股"

    value = fetch_value(ts, realtime, kline) or {}
    pe = realtime.get("pe_ttm")
    pb = realtime.get("pb")
    pe_pct = _pe_history_percentile(pe, value.get("peHistory") or [])
    pe_label, pe_score = _val_level_pe(pe)
    pb_label = _val_level_pb(pb)

    # ---------- 1. overview 速览徽章 ----------
    name = realtime.get("name") or ts
    price = realtime.get("price")
    chg_pct = realtime.get("changePercent")
    score = fetch_score(ts, realtime, kline, fflow) or {}
    total_score = score.get("totalScore", 0) or 0

    # 买卖建议分级 (综合评分 + 技术 + 资金 三维度, 非直接投资建议)
    rec_level = "观望"
    rec_color = "warn"
    rec_reasons = []
    tech_bull = 0
    if ma_align == "多头排列": tech_bull += 2
    if ma_align == "交织": tech_bull += 1
    if macd_sig in ("红柱放大(多头)",): tech_bull += 1
    if kdj_sig in ("金叉形成", "偏多运行"): tech_bull += 1
    if boll_sig in ("中轨上方(偏强)", "突破上轨(强势/警惕回调)"): tech_bull += 1
    if rsi_sig in ("超卖区间", "严重超卖"): tech_bull += 1
    if rsi_sig in ("超买区间", "严重超买"): tech_bull -= 1

    cap_bull = 0
    if main_today and main_today > 0: cap_bull += 1
    if main_3d and main_3d > 0: cap_bull += 1
    if main_5d and main_5d > 0: cap_bull += 1
    if main_streak >= 3: cap_bull += 1
    if vol_health == "量价健康": cap_bull += 1
    if vol_health == "异常放量(警惕分歧)": cap_bull -= 1

    fund_bull = pe_score  # 0~5

    bull_sum = total_score * 0.30 + tech_bull * 6 + cap_bull * 5 + fund_bull * 4
    bear_sum = 0
    if chg_pct is not None and chg_pct <= -5: bear_sum += 10
    if ma_align == "空头排列": bear_sum += 10
    if kdj_sig == "死叉形成": bear_sum += 6
    if macd_sig == "绿柱放大(空头)": bear_sum += 6
    if main_streak == 0 and main_today and main_today < 0: bear_sum += 5
    if pe_score == 0 and pe is not None and pe > 0: bear_sum += 6

    net_score = bull_sum - bear_sum
    if net_score >= 30:
        rec_level = "买入"
        rec_color = "good"
    elif net_score >= 12:
        rec_level = "谨慎买入"
        rec_color = "good"
    elif net_score >= -8:
        rec_level = "观望"
        rec_color = "warn"
    elif net_score >= -22:
        rec_level = "谨慎减仓"
        rec_color = "bad"
    else:
        rec_level = "卖出"
        rec_color = "bad"

    overview = {
        "name": name,
        "code": ts,
        "price": price,
        "changePercent": chg_pct,
        "totalScore": round(total_score, 1),
        "beatStock": score.get("beatStock", "--"),
        "recommend": {"level": rec_level, "color": rec_color, "netScore": round(net_score, 1)},
        "marketStatus": realtime.get("marketStatus"),
        "todaySummary": _build_ai_text(realtime, fflow, score.get("factors", []), closes, bars),
    }

    # ---------- 2. fundamentals 基本面速览 ----------
    total_mv = realtime.get("totalMarket")
    circ_mv = realtime.get("circ_mv")
    if total_mv:
        if total_mv >= 1e12: mv_label = "超大盘(>万亿)"
        elif total_mv >= 1e11: mv_label = "大盘(千亿~万亿)"
        elif total_mv >= 2e10: mv_label = "中盘(200亿~千亿)"
        elif total_mv >= 5e9: mv_label = "小盘(50亿~200亿)"
        else: mv_label = "微盘(<50亿)"
    else:
        mv_label = "--"

    circ_ratio = None
    if total_mv and circ_mv and total_mv > 0:
        circ_ratio = round(circ_mv / total_mv * 100, 1)

    fundamentals = {
        "marketCap": {"value": total_mv, "txt": _fmt_wan(total_mv), "label": mv_label},
        "circCap": {"value": circ_mv, "txt": _fmt_wan(circ_mv),
                    "ratio": circ_ratio,
                    "ratioTxt": ("全流通" if circ_ratio and circ_ratio >= 95 else
                                 ("限售较多" if circ_ratio and circ_ratio < 60 else "正常"))
                    if circ_ratio else "--"},
        "pe": {"value": pe, "txt": _fmt_pct(pe, 2).replace("%", "") if pe is not None else "--",
               "label": pe_label, "score": pe_score,
               "percentile": pe_pct,
               "percentileTxt": ("历史 %s%% 分位" % pe_pct) if pe_pct is not None else "--"},
        "pb": {"value": pb, "txt": ("%.2f" % pb) if pb else "--", "label": pb_label},
        "turnover": {"value": tr, "txt": _fmt_pct(tr, 2),
                     "label": "活跃" if (tr and tr >= 5) else ("温和" if tr and tr >= 1.5 else "冷清")
                     if tr else "--"},
        "volumeRatio": {"value": vr, "txt": ("%.2f" % vr) if vr else "--",
                        "label": vol_health},
    }

    # ---------- 3. highlights 核心亮点 (最多 6 条) ----------
    highlights = []
    if strong_sectors:
        tier_str = "、".join(sorted(sector_tiers)) if sector_tiers else "强势"
        sec_names = list(dict.fromkeys(s["name"] for s in strong_sectors))[:2]
        highlights.append({
            "icon": "🔥", "title": "板块热度加持",
            "body": "所属板块「%s」处于 %s 强势榜(%s)，板块行情联动上涨概率更高。" %
                    ("、".join(sec_names), tier_str, sector_heat),
        })
    if ma_align == "多头排列":
        highlights.append({
            "icon": "📈", "title": "均线多头排列",
            "body": "MA5>MA10>MA20 三条均线多头向上，短期趋势强势，均线系统提供天然支撑。",
        })
    if macd_sig == "红柱放大(多头)":
        highlights.append({
            "icon": "💹", "title": "MACD 动能强劲",
            "body": "MACD 在%s，DIF 与 DEA 开口扩大、红柱持续放大，上行动能充足。" % macd_zero,
        })
    if main_streak >= 3:
        highlights.append({
            "icon": "💰", "title": "主力连续吸筹",
            "body": "主力资金已连续 %d 日净流入(近5日合计 %s)，机构持续看好，资金面形成正反馈。" %
                    (main_streak, _fmt_wan(main_5d)),
        })
    elif main_today and main_today > 0 and (main_3d or 0) > 0:
        highlights.append({
            "icon": "💵", "title": "主力资金回流",
            "body": "今日主力净流入 %s，近3日合计 %s，资金面边际转好。" %
                    (_fmt_wan(main_today), _fmt_wan(main_3d)),
        })
    if pe_score >= 4:
        highlights.append({
            "icon": "🏷️", "title": "估值安全边际充足",
            "body": "当前 PE(TTM) %s 倍，处于「%s」区间%s，估值具有吸引力。" %
                    (fundamentals["pe"]["txt"], pe_label,
                     ("(历史 %s%% 分位)" % pe_pct) if pe_pct is not None else ""),
        })
    if vol_health == "量价健康":
        highlights.append({
            "icon": "📊", "title": "量价配合良好",
            "body": "量比 %.2f、换手率 %s%%，成交处于健康区间，量能与走势匹配度高。" %
                    (vr or 0, tr or 0),
        })
    if rise_rate is not None and rise_rate >= 55:
        highlights.append({
            "icon": "🎯", "title": "历史胜率优异",
            "body": "近 %d 个交易日单日上涨概率 %s%%、平均涨幅 %s%%，买入后次日正收益历史占比较高。" %
                    ((bt.get("days") or 0), rise_rate, avg_inc if avg_inc else 0),
        })
    if pct60 is not None and pct60 > 0 and n >= 60:
        # 趋势强度(与斜率)
        slope_good = pct20 is not None and pct5 is not None and pct20 > 0 and pct5 >= 0
        if slope_good:
            highlights.append({
                "icon": "🚀", "title": "中期趋势向上",
                "body": "60日涨 %+.2f%%、20日 %+.2f%%、5日 %+.2f%%，阶梯式上行，斜率健康无加速赶顶迹象。" %
                        (pct60, pct20, pct5),
            })
    if boll_sig == "中轨上方(偏强)":
        highlights.append({
            "icon": "🎢", "title": "布林通道运行良好",
            "body": "股价在 BOLL 中轨与上轨之间运行，通道结构完好，中期强势格局延续。",
        })
    if rsi_sig in ("超卖区间", "严重超卖"):
        highlights.append({
            "icon": "⚡", "title": "短期超卖或反弹",
            "body": "RSI(6) 处于 %s，短期抛压释放较为充分，技术性反弹概率提升。" % rsi_sig,
        })
    # 去重限长
    _dedup_items(highlights, 6)

    # ---------- 4. risks 风险提示 (最多 6 条) ----------
    risks = []
    if ma_align == "空头排列":
        risks.append({
            "icon": "🔻", "title": "均线空头压制",
            "body": "MA5<MA10<MA20 空头排列，股价被短期均线层层压制，每一次反弹均容易遇均线回落。",
        })
    if macd_sig == "绿柱放大(空头)":
        risks.append({
            "icon": "📉", "title": "MACD 空头动能释放",
            "body": "MACD 在%s绿柱持续放大，中期下行趋势尚未出现明确止跌信号。" % macd_zero,
        })
    if kdj_sig == "死叉形成":
        risks.append({
            "icon": "⚠️", "title": "KDJ 死叉",
            "body": "KDJ 指标刚刚形成死叉，短期调整压力加大，需警惕进一步下探。",
        })
    if main_today is not None and main_today < 0 and main_streak == 0:
        out_txt = ""
        if main_5d is not None and main_5d < 0:
            out_txt = "近5日累计流出 %s，" % _fmt_wan(abs(main_5d))
        risks.append({
            "icon": "💸", "title": "主力资金出逃",
            "body": "今日主力净流出 %s，%s资金面偏空，机构筹码在派发。" %
                    (_fmt_wan(abs(main_today)), out_txt),
        })
    if small_5d is not None and small_5d > 0 and (main_5d or 0) < 0:
        risks.append({
            "icon": "🐑", "title": "散户接盘迹象",
            "body": "近5日小单(散户)净流入 %s，而主力净流出 %s，或为主力出货散户承接的典型形态。" %
                    (_fmt_wan(small_5d), _fmt_wan(abs(main_5d))),
        })
    if pe_score == 0 and pe is not None and pe > 150:
        risks.append({
            "icon": "💎", "title": "估值严重泡沫化",
            "body": "PE(TTM) 高达 %.0f 倍，处于泡沫区。一旦业绩增速不及预期，估值回归的杀跌幅度极大。" % pe,
        })
    elif pe_score <= 1 and pe is not None and pe > 0:
        risks.append({
            "icon": "📏", "title": "估值偏高",
            "body": "PE(TTM) %.0f 倍处于「%s」区间，对业绩增速的容错空间较小。" % (pe, pe_label),
        })
    if vol_health == "异常放量(警惕分歧)":
        risks.append({
            "icon": "📣", "title": "量能异常放大",
            "body": "当日量比 %.2f、换手 %s%% 远超常态，多空分歧剧烈，高位追涨风险极大。" % (vr or 0, tr or 0),
        })
    if boll_sig == "跌破下轨(弱势/或有反弹)":
        risks.append({
            "icon": "🌪️", "title": "跌破布林下轨",
            "body": "股价跌破 BOLL 下轨，短期进入极端弱势区，虽可能反弹但不宜盲目抄底。",
        })
    if rsi_sig in ("超买区间", "严重超买"):
        risks.append({
            "icon": "🔥", "title": "短期严重超买",
            "body": "RSI(6) 处于 %s 区域，多头拥挤度高，获利盘随时可能集中兑现引发回调。" % rsi_sig,
        })
    if sectors and all(s["status"] == "调出" for s in sectors):
        risks.append({
            "icon": "📤", "title": "板块热度退潮",
            "body": "所属板块已全部从 RPS 强势榜调出，板块热度明显退潮，个股联动上涨难度加大。",
        })
    if chg_pct is not None and chg_pct <= -5:
        risks.append({
            "icon": "⛔", "title": "当日大幅下挫",
            "body": "今日下跌 %.2f%%，跌幅较大且若伴随放量，则短期情绪恶化，惯性下探概率提升。" % abs(chg_pct),
        })
    _dedup_items(risks, 6)
    if not risks:
        risks.append({"icon": "✅", "title": "暂无显性风险",
                      "body": "从当前技术面、资金面、估值维度未发现重大警报信号，但仍需结合大盘整体环境综合判断。"})

    # ---------- 5. technical 技术面分析摘要 ----------
    technical = {
        "maAlign": ma_align,
        "ma": {"MA5": round(ma5, 2) if ma5 else None,
               "MA10": round(ma10, 2) if ma10 else None,
               "MA20": round(ma20, 2) if ma20 else None,
               "MA60": round(ma60, 2) if ma60 else None},
        "macd": {"signal": macd_sig, "zone": macd_zero,
                 "dif": round(macd_last["dif"], 3) if macd_last else None,
                 "dea": round(macd_last["dea"], 3) if macd_last else None,
                 "hist": round(macd_last["hist"], 3) if macd_last else None},
        "kdj": {"signal": kdj_sig,
                "k": round(kdj_last["k"], 1) if kdj_last else None,
                "d": round(kdj_last["d"], 1) if kdj_last else None,
                "j": round(kdj_last["j"], 1) if kdj_last else None},
        "rsi": {"signal": rsi_sig, "value": round(rsi_last, 1) if rsi_last is not None else None},
        "boll": {"signal": boll_sig,
                 "upper": round(boll_last["upper"], 2) if boll_last and boll_last["upper"] else None,
                 "mid": round(boll_last["mid"], 2) if boll_last and boll_last["mid"] else None,
                 "lower": round(boll_last["lower"], 2) if boll_last and boll_last["lower"] else None},
        "stageRise": {"5D": round(pct5, 2) if pct5 is not None else None,
                      "20D": round(pct20, 2) if pct20 is not None else None,
                      "60D": round(pct60, 2) if pct60 is not None else None},
    }

    # ---------- 6. capital 资金面解读 ----------
    capital = {
        "mainToday": {"value": main_today, "txt": _fmt_wan(main_today)},
        "main3d": {"value": main_3d, "txt": _fmt_wan(main_3d)},
        "main5d": {"value": main_5d, "txt": _fmt_wan(main_5d)},
        "streak": main_streak,
        "volHealth": vol_health,
        "interpret": _capital_interpret(main_today, main_3d, main_5d, main_streak, vol_health, small_5d),
    }

    # ---------- 7. action 买卖操作建议 ----------
    # 关键价位
    sup_20 = boll_last["lower"] if boll_last and boll_last["lower"] else (ma20 * 0.97 if ma20 else None)
    res_20 = boll_last["upper"] if boll_last and boll_last["upper"] else (ma20 * 1.03 if ma20 else None)
    ma5_p = round(ma5, 2) if ma5 else None
    ma10_p = round(ma10, 2) if ma10 else None
    ma20_p = round(ma20, 2) if ma20 else None
    ma60_p = round(ma60, 2) if ma60 else None

    # 触发价/止损价 (基于关键位, 非投资建议)
    if last_c:
        if rec_level in ("买入", "谨慎买入"):
            # 建议买入区间: MA5 附近 ~ 盘中回踩 MA10
            if ma5_p and ma10_p:
                buy_zone = "%.2f ~ %.2f" % (min(ma5_p, ma10_p), max(ma5_p, ma10_p) * 1.005)
            else:
                buy_zone = "现价附近(%.2f)" % last_c
            stop_loss = "%.2f" % (last_c * 0.95)  # 固定 5% 止损
            hold_period = "5~15 个交易日" if rec_level == "谨慎买入" else "2~8 周"
        elif rec_level in ("谨慎减仓", "卖出"):
            if ma5_p and ma10_p:
                sell_zone = "%.2f ~ %.2f" % (min(ma5_p, ma10_p), max(ma5_p, ma10_p))
            else:
                sell_zone = "反弹至现价(%.2f)附近" % last_c
            buy_zone = "卖出/减仓信号, 暂不买入"  # 修复 2026-08-29: 此前未赋值导致 UnboundLocalError
            stop_loss = None
            hold_period = "立即处理"
        else:
            buy_zone = "等待信号明朗"
            stop_loss = "持仓者以 MA20(%.2f) 为界" % ma20_p if ma20_p else "--"
            hold_period = "边走边看"
    else:
        buy_zone, stop_loss, hold_period = "--", "--", "--"

    # 综合建议理由
    reason_parts = []
    if strong_sectors:
        reason_parts.append("板块热度" + ("共振" if len(sector_tiers) >= 2 else "加持"))
    if tech_bull >= 3:
        reason_parts.append("技术面 %d 项偏多" % tech_bull)
    elif tech_bull <= 0:
        reason_parts.append("技术面偏空")
    if cap_bull >= 2:
        reason_parts.append("资金面连续流入")
    elif cap_bull < 0:
        reason_parts.append("资金面流出明显")
    if pe_score >= 4:
        reason_parts.append("估值较低")
    elif pe_score <= 1:
        reason_parts.append("估值偏高")
    if not reason_parts:
        reason_parts.append("多空信号交织")

    action = {
        "recommend": rec_level,
        "color": rec_color,
        "reason": "、".join(reason_parts),
        "buyZone": buy_zone,
        "stopLoss": stop_loss,
        "holdPeriod": hold_period,
        "disclaimer": "以上建议由量化指标自动生成，仅供编程学习参考，不构成任何投资建议。",
    }

    # ---------- 8. key_levels 关键价位 ----------
    key_levels = {
        "current": round(last_c, 2) if last_c else None,
        "resistance": [
            {"name": "MA5", "value": ma5_p},
            {"name": "MA10", "value": ma10_p},
            {"name": "BOLL上轨", "value": round(boll_last["upper"], 2) if boll_last and boll_last["upper"] else None},
            {"name": "前高(5日)", "value": round(max(highs[-5:]), 2) if len(highs) >= 5 else None},
        ],
        "support": [
            {"name": "MA10", "value": ma10_p},
            {"name": "MA20", "value": ma20_p},
            {"name": "MA60", "value": ma60_p},
            {"name": "BOLL下轨", "value": round(boll_last["lower"], 2) if boll_last and boll_last["lower"] else None},
            {"name": "前低(5日)", "value": round(min(lows[-5:]), 2) if len(lows) >= 5 else None},
        ],
    }

    # ---------- 9. sector_heat 板块热度联动 ----------
    sec_dedup = {}
    for s in sectors:
        k = (s["name"], s["status"])
        if k not in sec_dedup:
            sec_dedup[k] = []
        sec_dedup[k].append(s["tier"])
    sector_list = []
    for (nm, st), tiers in sec_dedup.items():
        sector_list.append({"name": nm, "status": st, "tiers": "、".join(sorted(tiers))})

    sector_heat_out = {
        "level": sector_heat,
        "tierCount": len(sector_tiers),
        "membership": sector_list,
    }

    return {
        "overview": overview,
        "fundamentals": fundamentals,
        "highlights": highlights,
        "risks": risks,
        "technical": technical,
        "capital": capital,
        "action": action,
        "keyLevels": key_levels,
        "sectorHeat": sector_heat_out,
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _dedup_items(items, max_count):
    """按标题去重 + 截断"""
    seen = set()
    out = []
    for it in items:
        if it.get("title") in seen:
            continue
        seen.add(it["title"])
        out.append(it)
        if len(out) >= max_count:
            break
    items[:] = out


def _capital_interpret(main_today, main_3d, main_5d, streak, vol_health, small_5d):
    """资金面一句话解读"""
    if streak >= 3 and (main_5d or 0) > 0:
        return "主力持续吸筹(%d 日连入，近 5 日合计 %s)，叠加%s，资金面强力看多。" % (
            streak, _fmt_wan(main_5d), vol_health)
    if main_today and main_today > 0 and (main_3d or 0) > 0:
        return "主力近 3 日连续回流(合计 %s)，短期资金面转暖，关注能否形成中期趋势。" % _fmt_wan(main_3d)
    if (main_5d or 0) < 0 and (small_5d or 0) > 0:
        return "主力 5 日净流出 %s，而散户净买入 %s，筹码从机构向散户转移，需谨慎。" % (
            _fmt_wan(abs(main_5d)), _fmt_wan(small_5d))
    if main_today is not None and main_today < 0:
        return "今日主力净流出 %s，资金面偏空。%s" % (_fmt_wan(abs(main_today)),
                                                     ("量能特征: %s" % vol_health) if vol_health != "--" else "")
    return "资金面整体中性，%s。" % vol_health


# ============================================================
# 汇总入口: 组装详情弹窗一次可用的全部数据
# ============================================================

def build_detail_payload(code):
    """聚合个股详情数据 (前端弹窗一次请求; 各子模块失败均降级为空, 不阻断)"""
    ts = to_ts_code(code)
    if not ts:
        return None
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
