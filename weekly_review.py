# -*- coding: utf-8 -*-
"""
每周 A 股收盘复盘生成器（周度动量视角量化复盘）
================================================
定位: 每周日做一次周度复盘, 总结本周板块动量演变 + 情绪轨迹 + 消息面, 给出下周关注。
数据 100% 复用看板已有数据, 不新增接口; 与每日复盘(动量视角)互补:
  每日复盘 = 单日快照(定调/主线/轮动/健康/风险/清单)
  每周复盘 = 周度聚合(本周定调/本周主线/本周轮动/情绪演变/消息面周度/下周关注)

数据源:
  - static/rps_history.json          全行业 RPS 历史(本周窗口板块动量演变, 核心)
  - static/rps_data.json             最新(周五)看板数据
  - static/review_sentiment_history.json 每日情绪周期历史(本周情绪轨迹)
  - static/daily_review.json         周五每日复盘(下周关注沿用其 watch/potential_sectors)
  - static/recommendations.json      周五推荐(消息面周度快照)
  - static/recommendations_early.json 周五早期推荐(消息面周度快照)

输出:
  - static/weekly_review.json        结构化周度复盘数据(前端渲染)
  - static/weekly_review.md          周度复盘文字初稿(人工润色用)

只周日运行: 由调用方(rps_calc._maybe_run_weekly_review)按自然日判断, main() 本身不判断(便于测试)
幂等: weekly_review.json 已含本周 week_id 则跳过
"""
from logutil import get_logger
logger = get_logger(__name__)

import json
import sys
from datetime import datetime
from pathlib import Path

import daily_review as dr

STATIC_DIR = Path(__file__).parent / "static"

# ========================== 配置 ==========================
CONFIG_WEEKLY = {
    "days": 5,             # 本周窗口 = 最近5个交易日(周五复盘, 最新数据为周五收盘)
    "mainline_days": 3,    # 本周在榜≥3天 = 本周主线
    "sustained_days": 4,   # 全周在榜(≥4/5天) = 持续主线
    "new_in_days": 2,      # (V2.0) 新晋判定已改为: 末日仍在榜 且 在榜天数<全周(sustained_days) → 含反复轮动板块
    "strong_avg": 10,      # 周均高分板块数≥10 → 周度强 (RPS95下数量恒~10, 饱和即强)
    "weak_last": 4,        # 周末高分≤4 → 周度弱 (RPS95下数量恒~10, 4以下=收缩)
    "rise": 1,             # 周内高分增幅≥+1 → 走强
    "fall": -1,            # 周内降幅≤-1 → 走弱
    "hot_top": 8,          # 周度热度榜前 N
    "watch_top": 10,       # 下周关注数量上限
    # ---- V2.0 (2026-08-29 周度复盘升级) ----
    "rps_threshold": 90,   # 周度高分板块阈值 (需求全局术语: 高分板块=RPS≥90, 与看板入选阈值一致)
    "hist_weeks": 4,       # 历史水位窗口: 近4周
    "hard_top": 8,         # 硬核主线展示上限
    "decay_floor": 60,     # 动量衰减: RPS5<60 但满足在榜天数 → 灰色弱提示
    "resonance_all": 3,    # 异常信号: ≥3个三周期共振主线集体衰退 → 高风险
    "highfreq_flips": 2,   # 近5日进出榜单≥2次 → 高频轮动
    "drop_high_days": 3,   # 衰退风险: 原主线(在榜≥3天) → 中风险以上
    "drop_core_days": 4,   # 衰退风险: 原持续主线(在榜≥4天) → 高风险
    "amount_chg_warn": 20, # 异常信号: 周成交额环比放大≥20%
    "benchmark_code": "000001.SH",  # 大盘基准: 上证指数(无沪深300缓存, 降级用)
    "cap_top": 2,          # 主线核心龙头: 每板块最多2只
}
# 文件路径动态拼接 STATIC_DIR (与 daily_review 一致, 便于测试重定向输出目录)
# ==========================================================


def _num(v, default=0.0):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ===================== V2.0 辅助函数 (周度复盘升级) =====================

def _style_label(name):
    """板块风格标签 → 逗号连接字符串 (周期/成长/消费/金融/防御/未归类)"""
    styles = dr._style_of(str(name))
    return "、".join(sorted(styles - {"未归类"})) if styles else ""


def _resonance_ok(v, threshold=None):
    """三周期共振: 板块 RPS5/RPS10/RPS20 同时 ≥ 阈值 (缺某周期数据 → False)
    兼容两种键名: 原始历史条目(大写 RPS5/10/20) 与 周度统计 dict(小写 rps5/10/20)"""
    threshold = threshold or CONFIG_WEEKLY["rps_threshold"]
    if not isinstance(v, dict):
        return False
    for k in ("RPS5", "RPS10", "RPS20"):
        val = v.get(k)
        if val is None:
            val = v.get(k.lower())
        if val is None or _num(val) < threshold:
            return False
    return True


def _in_week_set(history, date, period="RPS5", threshold=None):
    """周度窗口内某日在榜板块名集合(需求口径 RPS≥threshold)"""
    threshold = threshold or CONFIG_WEEKLY["rps_threshold"]
    return dr._history_set(history, date, period, threshold)


def _first_on_date(history, name, dates, period="RPS5", threshold=None):
    """板块本周窗口内首次在榜日期 (不在榜 → None)"""
    threshold = threshold or CONFIG_WEEKLY["rps_threshold"]
    for d in dates:
        v = (history.get(d) or {}).get(name) or {}
        if v.get(period) is not None and _num(v.get(period)) >= threshold:
            return d
    return None


def _last_on_date(history, name, dates, period="RPS5", threshold=None):
    """板块本周窗口内最后一次在榜日期 (不在榜 → None)"""
    threshold = threshold or CONFIG_WEEKLY["rps_threshold"]
    for d in reversed(dates):
        v = (history.get(d) or {}).get(name) or {}
        if v.get(period) is not None and _num(v.get(period)) >= threshold:
            return d
    return None


def _week_chg(history, name):
    """板块周涨幅 = 最新交易日 chg5 (5日涨幅, 周度窗口近似)"""
    dates = sorted((history or {}).keys())
    for d in reversed(dates):
        v = (history.get(d) or {}).get(name) or {}
        if v.get("chg5") is not None:
            return round(_num(v.get("chg5")), 2)
    return None


def _sector_ratio(rps_data, name):
    """板块最新扩散达标率 = RPS50≥90 个股数 / 总成分股数 (周度降级为周五口径)"""
    for key in ("rps5", "rps10", "rps20"):
        for s in (rps_data.get(key) or {}).get("in_list", []):
            if s.get("name") == name:
                n = _num(s.get("rps50_above_90"))
                total = _num(s.get("stock_count"))
                if total > 0:
                    return round(n / total * 100, 1)
    return None


def _week_avg_percentile(counts, cur_avg, weeks=None):
    """当前周均高分数 在近 weeks 周(按5交易日为一周) 周均高分中的历史分位
    返回 {window, pct, level} 或 None (历史不足2周)
    分位: 低于当前周均的历史周占比; 水平: <30低 / 30-50中低 / 50-70中高 / ≥70高
    """
    weeks = weeks or CONFIG_WEEKLY["hist_weeks"]
    dates = sorted((counts or {}).keys())
    window = dates[-(weeks * 5):]
    if len(window) < 10:
        return None
    week_avgs = []
    for i in range(0, len(window), 5):
        chunk = window[i:i + 5]
        if len(chunk) >= 3:  # 至少3个交易日才计一周
            week_avgs.append(sum(counts[d] for d in chunk) / len(chunk))
    if len(week_avgs) < 2:
        return None
    below = sum(1 for x in week_avgs if x <= cur_avg)
    pct = int(round(below / len(week_avgs) * 100))
    if pct < 30:
        level = "低水位"
    elif pct < 50:
        level = "中低水位"
    elif pct < 70:
        level = "中高水位"
    else:
        level = "高水位"
    return {"window": "近%d周" % weeks, "pct": pct, "level": level}


def week_id_of(date_str):
    """'20260821' → ISO 周号 '2026W34' (跨年安全)"""
    d = datetime.strptime(str(date_str), "%Y%m%d")
    iso = d.isocalendar()
    return "%dW%02d" % (iso[0], iso[1])


def _week_dates(history, days=None):
    """本周窗口 = history 中最近 days 个交易日(升序)"""
    days = days or CONFIG_WEEKLY["days"]
    dates = sorted((history or {}).keys())
    return dates[-days:] if len(dates) >= days else dates


# ========================== 1. 周度定调 ==========================

def week_tone(history, days=None):
    """周度强弱定调: 每日高分板块数序列(RPS5≥90) 的周均值/周内趋势
    V2.0 (需求 3.1): 三周期并列(RPS5/10/20) + 近4周历史水位 + 判定阈值标注
    → {dates, series:[{date, high_n}], rps5_series, rps10_series, rps20_series,
       first, last, avg, diff, tone, hist_water_level, threshold_note, note}
    """
    th = CONFIG_WEEKLY["rps_threshold"]
    counts = dr.high_rps_counts(history, "RPS5", th)
    dates = _week_dates(history, days)
    series = [{"date": d, "high_n": int(counts.get(d, 0))} for d in dates]
    if not series:
        return {"dates": [], "series": [], "rps5_series": [], "rps10_series": [],
                "rps20_series": [], "first": 0, "last": 0, "avg": 0,
                "diff": 0, "tone": "周度中性", "hist_water_level": None,
                "threshold_note": "", "note": ""}
    first = series[0]["high_n"]
    last = series[-1]["high_n"]
    avg = sum(x["high_n"] for x in series) / len(series)
    diff = last - first
    if avg >= CONFIG_WEEKLY["strong_avg"] and diff >= CONFIG_WEEKLY["rise"]:
        tone = "周度偏强"
    elif avg >= CONFIG_WEEKLY["strong_avg"]:
        tone = "周度强"
    elif last <= CONFIG_WEEKLY["weak_last"] or diff <= CONFIG_WEEKLY["fall"]:
        tone = "周度偏弱"
    else:
        tone = "周度中性"
    # V2.0: 三周期并列序列 (RPS5/10/20 ≥90 每日计数)
    def _series(period):
        c = dr.high_rps_counts(history, period, th)
        return [{"date": d, "high_n": int(c.get(d, 0))} for d in dates]
    # V2.0: 历史水位 (近4周周均高分分位)
    hist_water_level = _week_avg_percentile(counts, avg, CONFIG_WEEKLY["hist_weeks"])
    # V2.0: 判定阈值标注 (纯判定文本, "判定阈值:" 前缀由前端/文案统一加, 避免重复)
    strong_note = "日均高分>=%d 且周内走高" % CONFIG_WEEKLY["strong_avg"]
    weak_note = "日均高分<%d 或周末高分<=%d" % (CONFIG_WEEKLY["strong_avg"], CONFIG_WEEKLY["weak_last"])
    threshold_note = strong_note if "偏强" in tone else weak_note if "偏弱" in tone else \
        "%s; %s" % (strong_note, weak_note)
    note = "周均高分板块%.0f个(首日%d→末日%d, %+d)" % (avg, first, last, diff)
    if hist_water_level:
        note += ", 处于%s %d%% 分位(%s)" % (hist_water_level["window"], hist_water_level["pct"],
                                           hist_water_level["level"])
    if diff > 0:
        note += ", 周内热度走高"
    elif diff < 0:
        note += ", 周内热度回落"
    return {"dates": dates, "series": series,
            "rps5_series": _series("RPS5"), "rps10_series": _series("RPS10"),
            "rps20_series": _series("RPS20"),
            "first": first, "last": last, "avg": round(avg, 1), "diff": diff,
            "tone": tone, "hist_water_level": hist_water_level,
            "threshold_note": threshold_note, "note": note}


# ========================== 2. 本周主线 ==========================

def _week_sector_days(history, dates):
    """本周窗口内每个板块 RPS5≥90 的在榜天数 + 最新 RPS5/10/20 + 排名"""
    th = CONFIG_WEEKLY["rps_threshold"]
    result = {}
    for ind in (set().union(*[set((history[d] or {}).keys()) for d in dates]) if dates else set()):
        days_on = 0
        latest = None
        for d in dates:
            v = (history.get(d) or {}).get(ind) or {}
            if _num(v.get("RPS5")) >= th:
                days_on += 1
            latest = v  # 最后一天覆盖 → 最新值
        latest = latest or {}
        result[ind] = {"days_on": days_on,
                       "rps5": latest.get("RPS5"), "rps10": latest.get("RPS10"),
                       "rps20": latest.get("RPS20"), "rank": latest.get("rank5")}
    return result


def week_mainlines(history, days=None, rps_data=None):
    """本周主线: 窗口内在榜天数≥3 的板块, 含多周期/扩散率/周涨幅/风格/强度分级
    V2.0 (需求 3.2):
      强度: 硬核主线(三周期共振) > 短期主线(仅RPS5在榜≥3天) > 动量衰减(RPS5<60)
      字段: 名称 | 在榜天数 | RPS5 | RPS10 | RPS20 | 周扩散达标率 | 周涨幅 | 风格标签
    → [{name, days_on, rps5, rps10, rps20, ratio, chg, style, strength, level}]
    """
    dates = _week_dates(history, days)
    stats = _week_sector_days(history, dates)
    out = []
    for ind, s in stats.items():
        if dr._is_noise_sector(ind):
            continue
        if s["days_on"] >= CONFIG_WEEKLY["mainline_days"]:
            rps5 = _num(s.get("rps5"))
            reso = _resonance_ok(s)
            if rps5 >= CONFIG_WEEKLY["decay_floor"] and reso:
                strength, level = "硬核主线", "hard"
            elif rps5 >= CONFIG_WEEKLY["decay_floor"]:
                strength, level = "短期主线", "short"
            else:
                strength, level = "动量衰减", "decay"
            out.append({
                "name": ind, "days_on": s["days_on"],
                "rps5": round(rps5, 1),
                "rps10": round(_num(s.get("rps10")), 1) if s.get("rps10") is not None else None,
                "rps20": round(_num(s.get("rps20")), 1) if s.get("rps20") is not None else None,
                "ratio": _sector_ratio(rps_data, ind) if rps_data else None,
                "chg": _week_chg(history, ind),
                "style": _style_label(ind),
                "strength": strength, "level": level,
            })
    # V2.0 排序: 硬核 > 短期 > 动量衰减, 组内按在榜天数 + RPS5
    order = {"hard": 0, "short": 1, "decay": 2}
    out.sort(key=lambda x: (order.get(x["level"], 9), -x["days_on"], -x["rps5"]))
    return out[:CONFIG_WEEKLY["hot_top"] * 2]


# ========================== 3. 本周轮动 ==========================

def week_rotation(history, days=None):
    """本周轮动: 新晋(周初不在榜→周末在榜) / 持续(全周在榜) / 衰退(周初在榜→周末不在榜)
    V2.0 (需求 3.3):
      sustained: + 风格 + 三周期共振标识
      new_in:    + 首次上榜日期 + 反复轮动次数 + 风格
      dropped:   + 退出日期 + 风险等级(高风险=原持续主线/中风险=原短期主线/低风险=脉冲) + 风格
      新增: summary(一句话轮动总结) + high_freq(近5日进出≥2次板块)
    → {summary, new_in:[{name, first_date, days_on, repeat_n, style, rps5}],
       sustained:[{name, days_on, style, resonance, rps5}],
       dropped:[{name, exit_date, days_on, risk_level, risk, style}],
       high_freq:[{name, flips}]}
    """
    dates = _week_dates(history, days)
    if len(dates) < 2:
        return {"summary": "", "new_in": [], "sustained": [], "dropped": [],
                "high_freq": []}
    th = CONFIG_WEEKLY["rps_threshold"]
    first_set = _in_week_set(history, dates[0], "RPS5", th)
    last_set = _in_week_set(history, dates[-1], "RPS5", th)
    stats = _week_sector_days(history, dates)
    new_in, sustained, dropped = [], [], []
    for ind in sorted(last_set):
        s = stats.get(ind, {})
        days_on = s.get("days_on", 0)
        if days_on < CONFIG_WEEKLY["sustained_days"]:
            new_in.append({"name": ind, "first_date": _first_on_date(history, ind, dates),
                           "days_on": days_on, "repeat_n": 0,
                           "style": _style_label(ind),
                           "rps5": round(_num(s.get("rps5")), 1)})
        elif days_on >= CONFIG_WEEKLY["sustained_days"]:
            sustained.append({"name": ind, "days_on": days_on,
                              "style": _style_label(ind),
                              "resonance": _resonance_ok(s),
                              "rps5": round(_num(s.get("rps5")), 1)})
    for ind in sorted(first_set - last_set):
        days_on = stats.get(ind, {}).get("days_on", 0)
        if days_on >= CONFIG_WEEKLY["drop_core_days"]:
            risk_level, risk = "高风险", "原持续主线衰退"
        elif days_on >= CONFIG_WEEKLY["drop_high_days"]:
            risk_level, risk = "中风险", "短期主线衰退"
        else:
            risk_level, risk = "低风险", "脉冲板块衰退"
        dropped.append({"name": ind, "exit_date": _last_on_date(history, ind, dates),
                        "days_on": days_on, "risk_level": risk_level,
                        "risk": risk, "style": _style_label(ind)})
    new_in.sort(key=lambda x: -x["rps5"])
    sustained.sort(key=lambda x: (-x["days_on"], -x["rps5"]))
    dropped.sort(key=lambda x: (-x["days_on"], x["name"]))
    # V2.0: 高频轮动 (近5日 RPS5 在榜状态翻转≥2次)
    high_freq = []
    try:
        high_freq = dr._repeat_rotation(history, window=5,
                                        flips=CONFIG_WEEKLY["highfreq_flips"],
                                        detail=True)
    except Exception:
        high_freq = []
    for hf in high_freq:
        for x in new_in:
            if x["name"] == hf.get("name"):
                x["repeat_n"] = hf.get("flips", 0)
    # V2.0: 一句话轮动总结 (流入新晋/持续赛道, 流出衰退赛道, 风格占优)
    summary = _rotation_summary(sustained, new_in, dropped)
    return {"summary": summary, "new_in": new_in, "sustained": sustained,
            "dropped": dropped, "high_freq": high_freq}


def _rotation_summary(sustained, new_in, dropped):
    """轮动一句话总结: 资金流入(持续+新晋前3) / 流出(衰退前3) / 风格占优"""
    inflow = [x["name"] for x in (sustained[:2] + new_in[:2])]
    outflow = [x["name"] for x in dropped[:3]]
    styles = {}
    for x in (sustained + new_in):
        for st in (x.get("style") or "").replace("、", ",").split(","):
            st = st.strip()
            if st:
                styles[st] = styles.get(st, 0) + 1
    top_style = max(styles.items(), key=lambda kv: kv[1])[0] if styles else ""
    parts = []
    if inflow:
        parts.append("本周资金持续流入%s" % "、".join(inflow))
    if outflow:
        parts.append("从%s流出" % "、".join(outflow))
    if top_style:
        parts.append("%s风格整体占优" % top_style)
    return "；".join(parts) if parts else "本周板块轮动平缓"


# ========================== 4. 本周情绪演变 ==========================

def week_sentiment(sent_history, dates):
    """本周情绪演变: 从每日情绪周期历史取本周窗口记录
    V2.0 (需求 3.4): 每日判定规则注释 + 拐点标注(启动/高点/退潮) + 周情绪总结
    → {series:[{date, phase, high_n, phase_rule}], start_phase, end_phase, trend,
       phase_change, milestones, summary}
    """
    dates = list(dates or [])
    records = [s for s in (sent_history or [])
               if isinstance(s, dict) and s.get("date") in dates]
    records.sort(key=lambda x: str(x.get("date", "")))
    if not records:
        return {"series": [], "start_phase": "", "end_phase": "",
                "trend": "", "phase_change": "", "milestones": {}, "summary": ""}
    start = records[0].get("phase", "")
    end = records[-1].get("phase", "")
    # 情绪演变判定(简化): 起点→终点相位变化
    # 退潮期视为最低情绪刻度(退潮比震荡更冷), 故排在最前; 高潮期最高
    order = ["退潮期", "冰点期", "启动期", "发酵期", "震荡期", "高潮期"]
    def _idx(p):
        for i, x in enumerate(order):
            if x in str(p):
                return i
        return None
    si, ei = _idx(start), _idx(end)
    phase_change = ""
    if si is not None and ei is not None:
        if ei > si:
            phase_change = "升温"
        elif ei < si:
            phase_change = "降温"
        else:
            phase_change = "持平"
    trend = ("情绪从%s到%s(%s)" % (start, end, phase_change)) if phase_change else ("情绪维持%s" % end)
    # V2.0: 逐日判定规则注释 + 归一化 series
    rule_map = {
        "冰点期": "高分≤3且连续回落",
        "启动期": "高分回升+种子增多",
        "发酵期": "核心主线确认",
        "震荡期": "高分高位徘徊",
        "高潮期": "高分≥12且扩散充分",
        "退潮期": "高分连续2日回落",
    }
    series = []
    for r in records:
        ph = r.get("phase", "")
        rule = ""
        for k, v in rule_map.items():
            if k in str(ph):
                rule = v
                break
        series.append({"date": r.get("date"), "phase": ph,
                       "high_n": int(r.get("high_n") or 0), "phase_rule": rule})
    # V2.0: 拐点标注 (启动日/高点日/退潮日)
    milestones = _week_milestones(series)
    # V2.0: 周情绪总结
    summary = _week_sentiment_summary(series, phase_change)
    return {"series": series, "start_phase": start, "end_phase": end,
            "trend": trend, "phase_change": phase_change,
            "milestones": milestones, "summary": summary}


def _week_milestones(series):
    """周情绪拐点: 启动日(首次phase非冰点/退潮)/高点日(high_n峰值)/退潮日(峰值后首次回落)"""
    if not series:
        return {}
    high_ns = [x["high_n"] for x in series]
    peak_n = max(high_ns)
    peak_i = high_ns.index(peak_n)
    low_order = ("冰点期", "退潮期")
    start_date = None
    for x in series:
        if not any(k in str(x["phase"]) for k in low_order):
            start_date = x["date"]
            break
    peak_date = series[peak_i]["date"]
    tideout_date = None
    for i in range(peak_i + 1, len(series)):
        if series[i]["high_n"] < peak_n:
            tideout_date = series[i]["date"]
            break
    out = {}
    if start_date:
        out["start_date"] = start_date
        out["start_label"] = "拐点·情绪启动日"
    if peak_date:
        out["peak_date"] = peak_date
        out["peak_label"] = "拐点·情绪高点日"
    if tideout_date:
        out["tideout_date"] = tideout_date
        out["tideout_label"] = "拐点·情绪退潮日"
    return out


def _week_sentiment_summary(series, phase_change):
    """周情绪一句话总结: 主导阶段 + 大级别拐点有无 + 波动幅度"""
    if not series:
        return ""
    from collections import Counter
    phases = Counter(str(x["phase"]) for x in series)
    main_phase = phases.most_common(1)[0][0] if phases else ""
    high_ns = [x["high_n"] for x in series]
    amp = max(high_ns) - min(high_ns)
    parts = ["本周情绪整体处于%s" % main_phase]
    has_big_turn = phase_change in ("升温", "降温") and abs(amp) >= 3
    parts.append("周内%s大级别拐点" % ("出现" if has_big_turn else "无明显"))
    parts.append("波动幅度%s" % ("较大" if amp >= 4 else "收窄" if amp <= 2 else "适中"))
    return ", ".join(parts) + "。"


# ========================== 5. 消息面周度快照 ==========================

def _fetch_week_fund():
    """周度资金数据 (V2.0 需求 3.5 周度资金子项)
    主力资金: 大盘资金流近5日求和 (缓存 market_fund_flow_cache.json 优先, 实时抓取兜底)
    北向资金: 2024-08 起北向净流入停披露, 仅用 market_status.hsgt_breadth 当日占比降级
    → {main_week_net_yi, main_verdict, north_ratio, north_note, source, note}
    """
    out = {"main_week_net_yi": None, "main_verdict": "",
           "north_ratio": None, "north_note": "", "source": None, "note": ""}
    # 1) 主力周累计: 缓存优先(测试确定/无网络), 实时抓取兜底
    flow = {}
    cached = _load_json(STATIC_DIR / "market_fund_flow_cache.json")
    if isinstance(cached, dict) and cached:
        flow = cached
        out["source"] = "cache"
    if not flow:
        try:
            from akshare_adapter import fetch_market_fund_flow_ak
            flow = fetch_market_fund_flow_ak(n_days=5) or {}
            if flow:
                out["source"] = "akshare"
        except Exception:
            flow = {}
    nets = [v.get("main_net") for v in flow.values() if v.get("main_net") is not None]
    if nets:
        total = sum(nets)  # 元
        out["main_week_net_yi"] = round(total / 1e8, 1)
        out["main_verdict"] = "净流入" if total >= 0 else "净流出"
    # 2) 北向当日广度降级 (净流入停披露)
    try:
        rec = dr.load_recommendations() or {}
        hb = (rec.get("market_status") or {}).get("hsgt_breadth") or {}
        if hb.get("ratio") is not None:
            out["north_ratio"] = round(float(hb["ratio"]), 3)
    except Exception:
        pass
    if out["north_ratio"] is not None:
        out["north_note"] = "北向净流入2024-08起停披露, 以北向成分股上涨占比(周五)替代"
    else:
        out["north_note"] = "北向资金数据缺失"
    if out["main_week_net_yi"] is None:
        out["note"] = "主力资金周累计抓取失败, 缺失如实标注"
    return out


def week_news(recs=None, early_recs=None):
    """周度消息面快照: 复用 daily_review.news_review (周五推荐的消息面聚合)
    V2.0 (需求 3.5): 数据范围标注 + 净影响分评分基准 + 周度资金子项
    注: 每日推荐文件会被覆盖, 无法回溯周内每日消息面, 故用周五快照标注口径
    → news_review 全部字段 + {score_basis, weekly_fund, range_note}
    """
    if recs is None:
        recs = dr.load_recommendations()
    if early_recs is None:
        early_recs = dr.load_early_recommendations()
    base = dr.news_review(recs, early_recs)
    if not isinstance(base, dict):
        return base
    base["score_basis"] = "消息面评分满分50分; 0-15分偏冷, 15-25分中性, 25分以上偏暖"
    base["range_note"] = "周度快照口径: 每日推荐文件会被覆盖, 汇总周五推荐所含消息(周内每日消息不回溯)"
    base["weekly_fund"] = _fetch_week_fund()
    return base


# ========================== 6. 下周关注 ==========================

def week_watch(daily_review, rotation=None):
    """下周关注: 复用周五每日复盘的次日清单 + 潜力板块
    V2.0 (需求 3.7): 分三类 + 优先级 + 跟踪规则
      focus(重点跟踪★): 高质量种子 + 主线分歧核心板块
      observe(普通观察): 临界晋级 + 潜力板块
      avoid(风险回避⚠): 本周衰退的高位主线板块(中/高风险)
    保留旧键 critical/diverge_watch/seed_high/potential (兼容旧前端)
    → {critical, diverge_watch, seed_high, potential,
       focus:[{name, reason, rule, priority}], observe:[...], avoid:[...]}
    """
    if not isinstance(daily_review, dict):
        return {"critical": [], "diverge_watch": [], "seed_high": [], "potential": [],
                "focus": [], "observe": [], "avoid": []}
    watch = daily_review.get("watch") or {}
    critical = (watch.get("critical") or [])[:CONFIG_WEEKLY["watch_top"]]
    diverge = (watch.get("diverge_watch") or [])[:CONFIG_WEEKLY["watch_top"]]
    seed_high = (watch.get("seed_high") or [])[:CONFIG_WEEKLY["watch_top"]]
    potential = (daily_review.get("potential_sectors") or [])[:CONFIG_WEEKLY["watch_top"]]
    # V2.0: 三类关注组装
    # rps5 补齐: focus/avoid 数据源(daily_review watch / rotation.dropped)不携带 RPS5,
    # 从 rps_history 取各板块最后一次在榜的 RPS5 补上, 避免字段恒空
    last_rps5 = {}
    try:
        _hist = _load_json(STATIC_DIR / "rps_history.json") or {}
        for _d, _sectors in _hist.items():
            for _nm, _rec in (_sectors or {}).items():
                if isinstance(_rec, dict) and _rec.get("RPS5") is not None:
                    last_rps5[_nm] = round(_num(_rec.get("RPS5")), 1)
    except Exception:
        last_rps5 = {}
    def _item(x, reason, rule, priority):
        return {"name": x.get("name"), "reason": reason, "rule": rule,
                "priority": priority,
                "rps5": last_rps5.get(x.get("name")) or x.get("rps5"),
                "ratio": x.get("ratio")}
    focus, observe, avoid = [], [], []
    for x in seed_high:
        focus.append(_item(x, "高质量种子(首日入选+扩散达标)",
                           "RPS20持续在榜, 若下周RPS5重回90以上视为分歧结束, 主线回归", "★"))
    for x in diverge:
        focus.append(_item(x, "主线分歧核心板块(RPS20在榜RPS5掉)",
                           "良性低吸观察, 跌破20日线回避, 缩量企稳可试错", "★"))
    for x in critical:
        observe.append(_item(x, "临界晋级(RPS≈阈值, 连升)",
                             "突破入选阈值确认后进入主线观察, 未突破不追", "普通"))
    for x in potential:
        observe.append(_item(x, "低位企稳/潜力板块",
                             "需要放量启动确认, 缩量横盘继续等待", "普通"))
    # 风险回避: 本周衰退的高位主线 (rotation.dropped 中 中/高风险)
    rot = rotation if isinstance(rotation, dict) else {}
    for x in (rot.get("dropped") or []):
        if x.get("risk_level") in ("高风险", "中风险"):
            avoid.append(_item(x, "本周衰退(%s, 曾持续在榜%d天)" % (x.get("risk", ""), x.get("days_on", 0)),
                               "高位衰退赛道谨慎追高, 等待量能收缩企稳后再评估", "⚠"))
    out = {
        "critical": critical, "diverge_watch": diverge,
        "seed_high": seed_high, "potential": potential,
        "focus": focus, "observe": observe, "avoid": avoid,
    }
    return out


# ========================== V2.0 新增模块 (需求 3.1广度 / 4.1-4.5) ==========================

def week_market_breadth(dates=None):
    """周市场广度 (V2.0 需求 3.1 子卡片): 周均上涨占比 / 周成交额+环比 / 周均涨跌停 / 周最高连板
    涨跌占比+成交额: daily_cache 全市场行情逐日聚合(周度真值)
    涨跌停/连板: recommendations.market_status 周五口径(历史未缓存, 如实标注)
    → {up_ratio, down_ratio, week_amount_yi, amount_chg, avg_limit_up,
       avg_limit_down, max_board, source, note}
    """
    history = _load_json(STATIC_DIR / "rps_history.json") or {}
    if dates is None:
        dates = _week_dates(history)
    dates = list(dates or [])
    out = {"up_ratio": None, "down_ratio": None, "week_amount_yi": None,
           "amount_chg": None, "avg_limit_up": None, "avg_limit_down": None,
           "max_board": None, "source": None, "note": ""}

    def _day_stats(d):
        rows = _load_json(STATIC_DIR / "daily_cache" / ("%s.json" % d))
        if not isinstance(rows, list):
            return None
        up = down = 0
        amt = 0.0
        for r in rows:
            if not isinstance(r, dict):
                continue
            pct = r.get("pct_chg")
            if pct is not None:
                p = float(pct)
                if p > 0:
                    up += 1
                elif p < 0:
                    down += 1
            amt += float(r.get("amount") or 0)
        return {"up": up, "down": down, "amt": amt}

    week = [s for s in (_day_stats(d) for d in dates) if s]
    ups = sum(s["up"] for s in week)
    downs = sum(s["down"] for s in week)
    amts = [s["amt"] for s in week if s["amt"] > 0]
    if week and (ups + downs) > 0:
        out["up_ratio"] = round(ups / (ups + downs), 3)
        out["down_ratio"] = round(downs / (ups + downs), 3)
    if amts:
        out["week_amount_yi"] = round(sum(amts) / 1e8, 1)
        # 环比上周 (前5个交易日窗口)
        all_dates = sorted(history.keys())
        if dates:
            try:
                i = all_dates.index(dates[0])
            except ValueError:
                i = -1
            if i >= 5:
                prev_amts = [s["amt"] for d in all_dates[i - 5:i]
                             for s in [_day_stats(d)] if s and s["amt"] > 0]
                if prev_amts:
                    prev_yi = sum(prev_amts) / 1e8
                    if prev_yi > 0:
                        out["amount_chg"] = round((out["week_amount_yi"] - prev_yi) / prev_yi * 100, 1)
    # 涨跌停/连板: 周五口径 (历史未缓存, 标注)
    rec = dr.load_recommendations() or {}
    ms = rec.get("market_status") or {}
    if isinstance(ms, dict):
        if ms.get("limit_up_count") is not None:
            out["avg_limit_up"] = int(_num(ms.get("limit_up_count")))
        if ms.get("limit_down_count") is not None:
            out["avg_limit_down"] = int(_num(ms.get("limit_down_count")))
    try:
        mb = dr.fetch_market_breadth()
        if mb and mb.get("max_board") is not None:
            out["max_board"] = int(_num(mb.get("max_board")))
    except Exception:
        pass
    if out["up_ratio"] is not None or out["week_amount_yi"] is not None:
        out["source"] = "daily_cache" + ("+recommendations" if out["avg_limit_up"] is not None else "")
        out["note"] = "涨跌停/最高连板为周五口径(历史未缓存); 涨跌占比与成交额为周度逐日聚合" if out["avg_limit_up"] is not None else \
            "涨跌停数据缺失(周五口径); 涨跌占比与成交额为周度逐日聚合"
    else:
        out["note"] = "周度广度数据不足(日线缓存或推荐状态缺失)"
    return out


def weekly_core_conclusion(tone, mainlines, rotation, sentiment, watch):
    """本周一句话核心结论 (V2.0 需求 4.1 置顶全宽卡片)
    组成要素: 情绪阶段 + 核心主线 + 热度水位 + 风格偏向 + 下周大方向
    → {text, phase, mainlines, heat, style, next}
    """
    sent = sentiment or {}
    end_phase = sent.get("end_phase") or ""
    if not end_phase:
        sm = str(sent.get("summary") or "")
        for ph in ("冰点期", "启动期", "震荡期", "发酵期", "高潮期", "退潮期"):
            if ph in sm:
                end_phase = ph
                break
    end_phase = end_phase or "震荡期"
    mains = mainlines or []
    hard = [x.get("name") for x in mains if x.get("level") == "hard"][:3]
    names = hard or [x.get("name") for x in mains[:3]]
    if hard:
        main_txt = "、".join(hard) + "为持续硬核主线"
    elif names:
        main_txt = "、".join(names) + "为短期主线"
    else:
        main_txt = "主线不明确"
    # 热度水位 (tone.note 含历史分位 + 周内趋势)
    tn = str((tone or {}).get("note") or "")
    if "走高" in tn:
        heat_txt = "市场热度小幅抬升"
    elif "回落" in tn:
        heat_txt = "市场热度回落"
    else:
        heat_txt = "市场热度平稳"
    # 风格偏向 (主线风格分布取众数)
    styles = {}
    for x in mains[:6]:
        for st in (x.get("style") or "").replace("、", ",").split(","):
            st = st.strip()
            if st:
                styles[st] = styles.get(st, 0) + 1
    top_style = max(styles.items(), key=lambda kv: kv[1])[0] if styles else ""
    # 下周大方向 (watch.focus + avoid)
    w = watch or {}
    focus = [x.get("name") for x in (w.get("focus") or [])[:2]]
    avoid_n = len(w.get("avoid") or [])
    nxt = []
    if focus:
        nxt.append("下周优先观察%s的低吸机会" % "、".join(focus))
    if avoid_n:
        nxt.append("高位衰退赛道(%d个)谨慎追高" % avoid_n)
    parts = ["本周情绪%s" % end_phase, main_txt, heat_txt]
    if top_style:
        parts[-1] += "，%s风格占优" % top_style
    text = "，".join(parts) + ("；" + "，".join(nxt) + "。" if nxt else "。")
    return {"text": text, "phase": end_phase, "mainlines": names,
            "heat": heat_txt, "style": top_style, "next": "；".join(nxt)}


def week_position_guide(sentiment):
    """周度仓位指引卡片 (V2.0 需求 4.2): 按周情绪末段阶段映射仓位
    高潮期: 总仓位7-8成, 主线为主; 发酵/震荡: 5-6成, 主线+试错;
    启动/退潮: 3-4成, 防守为主; 冰点期: 2成以下, 空仓观望
    → {phase, total, mainline, trial, direction, reason}
    """
    sent = sentiment or {}
    phase = sent.get("end_phase") or ""
    if not phase:
        sm = str(sent.get("summary") or "")
        for ph in ("冰点期", "启动期", "震荡期", "发酵期", "高潮期", "退潮期"):
            if ph in sm:
                phase = ph
                break
    phase = phase or "震荡期"
    if "高潮" in phase:
        total, mainline, trial, direction = 8, 5, 1, "持有主线, 分批兑现"
    elif "发酵" in phase:
        total, mainline, trial, direction = 6, 3, 2, "加仓主线"
    elif "震荡" in phase:
        total, mainline, trial, direction = 5, 2, 2, "主线+试错, 高抛低吸"
    elif "启动" in phase:
        total, mainline, trial, direction = 4, 2, 1, "试探性布局"
    elif "退潮" in phase:
        total, mainline, trial, direction = 3, 1, 0, "减仓防守"
    else:  # 冰点期
        total, mainline, trial, direction = 2, 1, 0, "空仓观望"
    reason = "周情绪%s, 建议总仓位%d成(主线%d成/试错%d成)" % (phase, total, mainline, trial)
    return {"phase": phase, "total": total, "mainline": mainline,
            "trial": trial, "direction": direction, "reason": reason}


def last_week_check(mainlines=None, rotation=None, history=None):
    """上周复盘结果验证闭环 (V2.0 需求 4.3): 读上周 weekly_review.json 的「下周关注」,
    对照本周实际: 晋级(本周主线/持续在榜) / 衰退(本周调出) / 震荡(仍在但未晋级)
    → {has_data, prev_week_id, prev_date, items:[{name, expected, verdict, ok}],
       views:[{view, verdict}], hit_rate, summary}
    """
    prev = _load_json(STATIC_DIR / "weekly_review.json")
    if not isinstance(prev, dict):
        return {"has_data": False, "note": "上周复盘缺失, 无法验证闭环"}
    if history is not None:
        # 若本周复盘文件恰为同周(重新生成), 视为无上周数据
        cur = week_id_of(str((history and sorted(history.keys()) or [""])[-1])) if history else ""
        if prev.get("week_id") == cur:
            return {"has_data": False, "note": "上周复盘缺失(同周重算)"}
    p_watch = prev.get("watch") or {}
    items_src = (p_watch.get("focus") or []) + (p_watch.get("observe") or [])
    if not items_src:  # 旧版字段兼容
        items_src = (p_watch.get("seed_high") or []) + (p_watch.get("critical") or [])
    if not items_src:
        return {"has_data": False, "note": "上周复盘无关注板块数据"}
    mains = mainlines or []
    main_names = [x.get("name") for x in mains]
    dropped_names = {x.get("name") for x in ((rotation or {}).get("dropped") or [])}
    items, ok = [], 0
    for x in items_src[:5]:
        nm = x.get("name")
        if not nm:
            continue
        exp = x.get("reason") or x.get("priority") or "重点跟踪"
        if nm in main_names:
            verdict, is_ok = "晋级", True
        elif nm in dropped_names:
            verdict, is_ok = "衰退", False
        else:
            verdict, is_ok = "震荡", False
        items.append({"name": nm, "expected": exp, "verdict": verdict, "ok": is_ok})
        if is_ok:
            ok += 1
    hit_rate = round(ok / len(items) * 100, 1) if items else None
    views = [{"view": "%s(上周关注)" % it["name"], "verdict": it["verdict"]}
             for it in items[:3]]
    summary = "上周关注%d个板块, 本周%d个晋级, 观点准确率%s" % (
        len(items), ok, ("%.1f%%" % hit_rate) if hit_rate is not None else "—")
    return {"has_data": True, "prev_week_id": prev.get("week_id", ""),
            "prev_date": prev.get("update_date", ""),
            "items": items, "views": views, "hit_rate": hit_rate, "summary": summary}


def _rps_week_high(history, name):
    """板块 RPS5 是否为近5交易日新高 (周线顶背离判定)"""
    return dr._rps_new_high(history, name, window=5)


def weekly_abnormal_risks(rotation=None, mainlines=None, history=None,
                          tone=None, breadth=None):
    """周度异常风险信号 (V2.0 需求 4.4): 满足任一即展示, 无风险默认隐藏
    1 集体退潮(high):    本周≥3个原持续主线(高风险衰退)集体衰退
    2 周线顶背离(high):  板块 RPS 周内创新高但扩散达标率<50%
    3 量价背离(mid):     周成交额环比放大≥20% 但高分板块周均数量不增
    4 高位滞涨(mid):     高位主线周涨幅<1% (放量数据缺失, 以滞涨近似预警)
    → {signals: [{type, title, level, level_text, desc}], note}
    """
    signals = []
    rot = rotation or {}
    dropped = rot.get("dropped") or []
    # 1) 集体退潮: 高风险(原持续主线)衰退≥3个
    core_drop = [x for x in dropped if x.get("risk_level") == "高风险"]
    if len(core_drop) >= CONFIG_WEEKLY["resonance_all"]:
        signals.append({"type": "group_tideout", "title": "集体退潮", "level": "high",
                        "desc": "本周%d个原持续主线集体衰退(%s), 高位风险集中释放, 主线真空期回避追高"
                                % (len(core_drop), "、".join(x.get("name", "") for x in core_drop[:4]))})
    # 2) 周线顶背离: RPS 周新高 但 扩散达标率<50%
    for x in (mainlines or [])[:10]:
        nm, ratio = x.get("name"), x.get("ratio")
        if ratio is not None and ratio < 50 and _rps_week_high(history, nm):
            signals.append({"type": "weekly_divergence", "title": "周线顶背离", "level": "high",
                            "desc": "%s RPS周内创新高但扩散达标率仅%.1f%%, 价量背离, 谨防冲高回落"
                                    % (nm, ratio)})
            break
    # 3) 量价背离: 周成交额环比放大≥20% 但高分周均数量不升
    bd = breadth or {}
    chg = bd.get("amount_chg")
    tone_diff = (tone or {}).get("diff")
    if chg is not None and chg >= CONFIG_WEEKLY["amount_chg_warn"] and tone_diff is not None and tone_diff <= 0:
        signals.append({"type": "volume_price_divergence", "title": "量价背离", "level": "mid",
                        "desc": "周成交额环比放大%.0f%%但高分板块数量%+d, 放量不涨, 警惕资金对倒"
                                % (chg, tone_diff)})
    # 4) 高位滞涨: 高位主线(硬核/短期)周涨幅<1% (板块成交额未缓存, 以涨幅滞涨近似)
    for x in (mainlines or [])[:10]:
        if x.get("level") in ("hard", "short") and x.get("chg") is not None and x.get("chg") < 1.0:
            signals.append({"type": "stagnation", "title": "高位滞涨", "level": "mid",
                            "desc": "%s高位周涨幅仅%+.1f%%, 滞涨警惕获利兑现(放量数据缺失, 以涨幅近似)"
                                    % (x.get("name", ""), x.get("chg"))})
            break
    level_txt = {"low": "低风险", "mid": "中风险", "high": "高风险"}
    for s in signals:
        s["level_text"] = level_txt.get(s["level"], s["level"])
    return {"signals": signals,
            "note": "触发条件: 集体退潮≥3个 / 周线顶背离(RPS新高+扩散<50%) / 量价背离(成交额+20%高分不增) / 高位滞涨(涨幅<1%)"}


def week_core_targets(mainlines, recs=None, max_per=2):
    """主线核心龙头映射 (V2.0 需求 4.5): 持续硬核主线 → 推荐中对应核心龙头
    与 daily_review.core_targets 同款宽容匹配 (板块名 ⊂ 个股sector 或反向)
    → {targets: [{sector, stocks: [{name, code}]}], note}
    """
    max_per = max_per or CONFIG_WEEKLY["cap_top"]
    if recs is None:
        recs = dr.load_recommendations() or {}
    by_sector = {}
    for s in recs.get("stocks") or []:
        secs = s.get("sectors") or ([s.get("sector")] if s.get("sector") else [])
        for sec in secs:
            if sec:
                by_sector.setdefault(str(sec), []).append(s)
    mains = mainlines or []
    hard_names = [x.get("name") for x in mains if x.get("level") == "hard"]
    if not hard_names:
        hard_names = [x.get("name") for x in mains[:3]]
    targets = []
    for nm in hard_names[:CONFIG_WEEKLY["hard_top"]]:
        if not nm:
            continue
        items = []
        for sec, stocks in by_sector.items():
            if nm == sec or nm in sec or sec in nm:
                items.extend(stocks)
        if items:
            targets.append({"sector": nm, "stocks": [
                {"name": s.get("name"), "code": s.get("ts_code")}
                for s in items[:max_per]]})
    note = "本周推荐未覆盖硬核主线板块, 暂无核心龙头映射" if not targets else ""
    return {"targets": targets, "note": note}


# ========================== 推荐准确率周度复盘 ==========================

def _trade_dates_after(calendar, date, n):
    """date 在升序日历中的第 n 个交易日 (不在日历或越界 → None)"""
    try:
        i = list(calendar).index(date)
    except ValueError:
        return None
    j = i + n
    if j >= len(calendar):
        return None
    return calendar[j]


def _daily_close_map(date):
    """static/daily_cache/{date}.json → {ts_code: close} (缺失 → {})"""
    d = _load_json(STATIC_DIR / "daily_cache" / ("%s.json" % date))
    out = {}
    if isinstance(d, list):
        for r in d:
            if isinstance(r, dict) and r.get("ts_code") and r.get("close") is not None:
                out[str(r["ts_code"])] = float(r["close"])
    return out


def _horizon_stats(calendar, samples):
    """样本 持有3日/5日 平均涨幅 (daily_cache 回溯; 数据缺失样本跳过)
    日历内 pred_date 之后第3/5个交易日的收盘相对预测日收盘涨幅
    → {avg_gain3, avg_gain5, horizon3_n, horizon5_n}
    """
    cache = {}

    def _closes(d):
        if d not in cache:
            cache[d] = _daily_close_map(d)
        return cache[d]

    vals3, vals5 = [], []
    for x in (samples or []):
        code, pred = x.get("code"), x.get("pred_date")
        if not code or not pred:
            continue
        c0 = _closes(str(pred)).get(str(code))
        if c0 is None:
            continue
        t3 = _trade_dates_after(calendar, str(pred), 3)
        if t3:
            c3 = _closes(t3).get(str(code))
            if c3 is not None:
                vals3.append((c3 / c0 - 1) * 100)
        t5 = _trade_dates_after(calendar, str(pred), 5)
        if t5:
            c5 = _closes(t5).get(str(code))
            if c5 is not None:
                vals5.append((c5 / c0 - 1) * 100)
    return {
        "avg_gain3": round(sum(vals3) / len(vals3), 2) if vals3 else None,
        "avg_gain5": round(sum(vals5) / len(vals5), 2) if vals5 else None,
        "horizon3_n": len(vals3), "horizon5_n": len(vals5),
    }


def _benchmark_week_gain(code=None):
    """基准指数周涨幅 (index_cache/{code}.json, 近5交易日窗口; 数据不足 → None)
    周度复盘无沪深300缓存 → 降级用上证指数 (CONFIG_WEEKLY.benchmark_code)"""
    code = code or CONFIG_WEEKLY["benchmark_code"]
    data = _load_json(STATIC_DIR / "index_cache" / ("%s.json" % code))
    if not isinstance(data, list):
        return None
    rows = sorted((r for r in data if r.get("close") is not None),
                  key=lambda r: str(r.get("trade_date", "")))
    if len(rows) < 6:
        return None
    return round((float(rows[-1]["close"]) / float(rows[-6]["close"]) - 1) * 100, 2)


def _stock_sector_map():
    """推荐股票 code → 所属板块 (recommendations.json stocks; 供最佳/最差案例补板块)"""
    rec = dr.load_recommendations() or {}
    out = {}
    for s in rec.get("stocks") or []:
        code = s.get("ts_code")
        if not code:
            continue
        sec = s.get("sector") or (s.get("sectors") or [None])[0] or ""
        out[str(code)] = str(sec)
    return out


def week_rec_accuracy(history=None):
    """本周推荐准确率: 从 prediction_review.json 中筛选本周预测日的验证结果
    V2.0 (需求 3.6):
      1) 新增 持有3日/5日 平均涨幅 (daily_cache 回溯)
      2) 分置信度统计 (高/中/低: 样本数/命中率/平均涨幅)
      3) 基准对比: 基准指数周涨幅 + 超额收益 (avg_gain5 周度口径最可比, 缺失降级次日)
      4) 最佳/最差案例补充所属板块
    返回周度命中率/平均涨幅/多周期收益/分置信度/基准对比/最佳最差个股
    """
    review = _load_json(STATIC_DIR / "prediction_review.json")
    if not review or not review.get("summary"):
        return {"has_data": False, "note": "暂无预测准确率数据"}
    week_dates = set(_week_dates(history))
    # 完整验证列表(all 含每日校准全部已验证样本, 与每日口径一致=次日收盘≥预测日现价)
    # 优先用 all 避免 recent(仅最近15条)截断导致本周样本遗漏
    full = review.get("all")
    if not full:
        full = review.get("recent") or []
    # 本周验证的预测 (pred_date 在本周窗口内)
    week_preds = [x for x in full if x.get("pred_date") in week_dates]
    if not week_preds:
        # 本周无验证样本(可能本周推荐尚未到次日验证窗口)
        # 退而用全部已验证样本做整体展示
        week_preds = full
    if not week_preds:
        return {"has_data": False, "note": "暂无已验证预测样本"}
    hits = [x for x in week_preds if x.get("hit")]
    hit_rate = round(len(hits) / len(week_preds), 3) if week_preds else 0
    avg_gain = round(sum(x.get("gain", 0) for x in week_preds) / len(week_preds), 2)
    # V2.0: 持有3日/5日平均涨幅 (日历 = rps_history 全部交易日)
    calendar = sorted((history or {}).keys())
    horizon = _horizon_stats(calendar, week_preds)
    # 分置信度
    by_conf = {}
    for lv in ("高", "中", "低"):
        sub = [x for x in week_preds if x.get("confidence") == lv]
        if sub:
            hr = round(sum(1 for x in sub if x.get("hit")) / len(sub), 3)
            ag = round(sum(x.get("gain", 0) for x in sub) / len(sub), 2)
            h3 = _horizon_stats(calendar, sub)
            by_conf[lv] = {"samples": len(sub), "hit_rate": hr, "avg_gain": ag,
                           "avg_gain3": h3["avg_gain3"], "avg_gain5": h3["avg_gain5"]}
    # V2.0: 基准对比 (上证指数周涨幅降级, 超额 = 推荐5日持有涨幅 - 基准)
    bench_gain = _benchmark_week_gain()
    base_gain = horizon["avg_gain5"] if horizon["avg_gain5"] is not None else avg_gain
    excess = round(base_gain - bench_gain, 2) if (bench_gain is not None
                                                  and base_gain is not None) else None
    benchmark = {"bench_name": "上证指数", "bench_code": CONFIG_WEEKLY["benchmark_code"],
                 "bench_gain": bench_gain, "excess": excess,
                 "basis": "5日持有" if horizon["avg_gain5"] is not None else "次日"}
    # V2.0: 最佳/最差案例补板块
    sector_map = _stock_sector_map()
    sorted_hits = sorted(week_preds, key=lambda x: -x.get("gain", 0))
    best = [{"name": x.get("name"), "code": x.get("code"), "gain": x.get("gain"),
             "confidence": x.get("confidence"), "pred_date": x.get("pred_date"),
             "sector": sector_map.get(str(x.get("code")), "")}
            for x in sorted_hits[:3]]
    worst = [{"name": x.get("name"), "code": x.get("code"), "gain": x.get("gain"),
              "confidence": x.get("confidence"), "pred_date": x.get("pred_date"),
              "sector": sector_map.get(str(x.get("code")), "")}
             for x in sorted(week_preds, key=lambda x: x.get("gain", 0))[:3]]
    return {
        "has_data": True,
        "week_samples": len(week_preds),
        "week_hit_rate": hit_rate,
        "week_avg_gain": avg_gain,
        "avg_gain3": horizon["avg_gain3"],
        "avg_gain5": horizon["avg_gain5"],
        "horizon3_n": horizon["horizon3_n"],
        "horizon5_n": horizon["horizon5_n"],
        "benchmark": benchmark,
        "by_confidence": by_conf,
        "best": best,
        "worst": worst,
        "horizon": review.get("horizon", 1),
        "note": "本周预测中已完成次日验证的样本统计; 3日/5日收益从日线缓存回溯, 样本不足时为空",
    }


# ========================== 汇总 + 渲染 ==========================

def build_weekly_review(history=None, rps_data=None, daily_review=None,
                        recs=None, early_recs=None, sent_history=None):
    """汇总周度复盘统计 → dict
    缺省自动读取各数据文件; 缺失数据用空值降级(不阻断)
    """
    if history is None:
        history = _load_json(STATIC_DIR / "rps_history.json") or {}
    if rps_data is None:
        rps_data = _load_json(STATIC_DIR / "rps_data.json") or {}
    if daily_review is None:
        daily_review = _load_json(STATIC_DIR / "daily_review.json") or {}
    if sent_history is None:
        sent_history = _load_json(STATIC_DIR / "review_sentiment_history.json") or []
    update_date = str(rps_data.get("update_date") or
                      (sorted((history or {}).keys())[-1] if history else ""))
    dates = _week_dates(history)
    tone = week_tone(history)
    mainlines = week_mainlines(history, rps_data=rps_data)
    rotation = week_rotation(history)
    sentiment = week_sentiment(sent_history, dates)
    news = week_news(recs, early_recs)
    watch = week_watch(daily_review, rotation=rotation)
    # V2.0 新增模块 (需求 3.1 广度 / 4.1-4.5)
    breadth = week_market_breadth(dates)
    core_concl = weekly_core_conclusion(tone, mainlines, rotation, sentiment, watch)
    pos_guide = week_position_guide(sentiment)
    last_check = last_week_check(mainlines=mainlines, rotation=rotation, history=history)
    abnormal = weekly_abnormal_risks(rotation=rotation, mainlines=mainlines,
                                     history=history, tone=tone, breadth=breadth)
    targets = week_core_targets(mainlines, recs=recs)
    return {
        "week_id": week_id_of(update_date) if update_date else "",
        "update_date": update_date,
        "range": dates,
        "days": len(dates),
        "tone": tone,
        "mainlines": mainlines,
        "rotation": rotation,
        "sentiment": sentiment,
        "news": news,
        "rec_accuracy": week_rec_accuracy(history),
        "watch": watch,
        # V2.0 新增 (需求 4.1-4.5 + 3.1 周市场广度)
        "core_conclusion": core_concl,
        "position_guide": pos_guide,
        "market_breadth": breadth,
        "last_week_check": last_check,
        "abnormal_risks": abnormal,
        "core_targets": targets,
    }


def _fmt_sector_list(lst, max_n=8):
    if not lst:
        return "无"
    names = [x.get("name", "") for x in lst] if isinstance(lst[0], dict) else list(lst)
    shown = "、".join(names[:max_n])
    if len(names) > max_n:
        shown += " 等%d个" % len(names)
    return shown


def render_markdown(st):
    """周度复盘统计 → markdown 文字初稿 (V2.0 卡片结构与字段同步)"""
    u = st.get("update_date", "")
    L = []
    L.append("# RPS 看板每周复盘（%s, %s）" % (st.get("week_id", ""), u))
    L.append("")
    # 置顶: 一句话核心结论 (需求 4.1)
    cc = st.get("core_conclusion") or {}
    if cc.get("text"):
        L.append("> **本周核心结论**: %s" % cc["text"])
        L.append("")
    # 一、周度定调
    tone = st.get("tone") or {}
    L.append("## 【本周定调】")
    if tone.get("series"):
        seq = " → ".join("%d" % x["high_n"] for x in tone["series"])
        L.append("周度强度: **%s**; 本周%d个交易日高分板块数: %s; %s"
                 % (tone.get("tone", "周度中性"), len(tone["series"]), seq, tone.get("note", "")))
        if tone.get("threshold_note"):
            L.append("判定阈值: %s" % tone["threshold_note"])
    else:
        L.append("周度强度: **%s** (无本周历史数据)" % tone.get("tone", "周度中性"))
    # 周度仓位指引 (需求 4.2)
    pg = st.get("position_guide") or {}
    if pg.get("total"):
        L.append("仓位指引: **%s** | 建议总仓位 **%d成**(主线%d成/试错%d成) | 操作方向: %s"
                 % (pg.get("phase", ""), pg.get("total", 0), pg.get("mainline", 0),
                    pg.get("trial", 0), pg.get("direction", "")))
    # 周市场广度 (需求 3.1)
    bd = st.get("market_breadth") or {}
    bd_parts = []
    if bd.get("up_ratio") is not None:
        bd_parts.append("上涨占比%.0f%%/下跌占比%.0f%%" % (bd["up_ratio"] * 100, bd["down_ratio"] * 100))
    if bd.get("week_amount_yi") is not None:
        chg = ("环比%+.0f%%" % bd["amount_chg"]) if bd.get("amount_chg") is not None else "环比—"
        bd_parts.append("周成交额%.0f亿(%s)" % (bd["week_amount_yi"], chg))
    if bd.get("avg_limit_up") is not None:
        bd_parts.append("周均涨跌停%d/%d" % (bd["avg_limit_up"], bd.get("avg_limit_down") or 0))
    if bd.get("max_board") is not None:
        bd_parts.append("周最高连板%d板" % bd["max_board"])
    if bd_parts:
        L.append("市场广度: %s" % " | ".join(bd_parts))
        if bd.get("note"):
            L.append("> 口径: %s" % bd["note"])
    # 二、本周主线
    L.append("")
    L.append("## 【本周主线】")
    if st.get("mainlines"):
        for x in st["mainlines"]:
            tags = []
            if x.get("strength"):
                tags.append(x["strength"])
            if x.get("style"):
                tags.append(x["style"])
            ratio_txt = "扩散%.0f%%" % x["ratio"] if x.get("ratio") is not None else "扩散—"
            chg_txt = ("周涨幅%+.1f%%" % x["chg"]) if x.get("chg") is not None else "周涨幅—"
            L.append("- **%s**(在榜%d天, RPS5=%.0f, RPS10=%s, RPS20=%s): %s, %s, %s%s"
                     % (x["name"], x["days_on"], x["rps5"],
                        ("%.0f" % x["rps10"]) if x.get("rps10") is not None else "—",
                        ("%.0f" % x["rps20"]) if x.get("rps20") is not None else "—",
                        ratio_txt, chg_txt, "、".join(tags), "【硬核】" if x.get("level") == "hard" else ""))
    else:
        L.append("- 本周无持续性主线(缺乏连续上榜板块)")
    # 三、本周轮动
    L.append("")
    L.append("## 【本周轮动】")
    rot = st.get("rotation") or {}
    if rot.get("summary"):
        L.append("- **轮动总结**: %s" % rot["summary"])
    if rot.get("sustained"):
        L.append("- **持续主线**(全周在榜): %s"
                 % "、".join("%s(%d天%s)" % (x["name"], x["days_on"],
                                             "【三周期共振】" if x.get("resonance") else "")
                             for x in rot["sustained"][:8]))
    if rot.get("new_in"):
        L.append("- **本周新晋**(周初不在榜→周末在榜): %s"
                 % "、".join("%s(首日%s,在榜%d天%s)" % (x["name"], x.get("first_date", "?"),
                                                    x["days_on"],
                                                    (",反复%d次" % x["repeat_n"]) if x.get("repeat_n") else "")
                             for x in rot["new_in"][:8]))
    if rot.get("dropped"):
        L.append("- **本周衰退**(周初在榜→周末不在榜): %s"
                 % "、".join("%s(退出%s,在榜%d天,%s)" % (x["name"], x.get("exit_date", "?"),
                                                    x["days_on"], x.get("risk_level", ""))
                             for x in rot["dropped"][:8]))
    if rot.get("high_freq"):
        L.append("- **高频轮动**(近5日进出榜单≥%d次, 谨慎追高): %s"
                 % (CONFIG_WEEKLY["highfreq_flips"],
                    "、".join("%s(%d次)" % (x["name"], x["flips"]) for x in rot["high_freq"][:6])))
    if not rot.get("sustained") and not rot.get("new_in") and not rot.get("dropped"):
        L.append("- 无明显轮动")
    # 周度异常风险 (需求 4.4, 无风险隐藏)
    abn = st.get("abnormal_risks") or {}
    if abn.get("signals"):
        L.append("- **异常风险信号**:")
        for s in abn["signals"]:
            L.append("  - [%s] %s: %s" % (s.get("level_text", ""), s.get("title", ""), s.get("desc", "")))
    # 四、情绪演变
    L.append("")
    L.append("## 【本周情绪演变】")
    sent = st.get("sentiment") or {}
    if sent.get("series"):
        seq = "、".join("%s(%s)" % (x.get("date", ""), x.get("phase", "")) for x in sent["series"])
        L.append("%s; 逐日: %s" % (sent.get("trend", ""), seq))
        if sent.get("milestones"):
            ms = sent["milestones"]
            L.append("拐点: %s" % " | ".join(
                "%s=%s" % (k, v) for k, v in
                {"启动日": ms.get("start_date"), "高点日": ms.get("peak_date"),
                 "退潮日": ms.get("tideout_date")}.items() if v))
        if sent.get("summary"):
            L.append("周情绪总结: %s" % sent["summary"])
    else:
        L.append("- 无情绪历史数据")
    # 五、消息面周度
    L.append("")
    L.append("## 【消息面周度快照】(周五推荐)")
    nws = st.get("news")
    if nws and nws.get("has_news"):
        L.append("消息面环境: **%s**; 推荐%d只中%d只命中, 净影响%+d分"
                 % (nws.get("env") or "中性", nws.get("stock_count", 0),
                    nws.get("hit_count", 0), nws.get("score_sum", 0)))
        if nws.get("score_basis"):
            L.append("- 评分基准: %s" % nws["score_basis"])
        if nws.get("official_pos_list"):
            L.append("- **权威利好**: %s" % "、".join("%s(%d条)" % (x["name"], x["count"])
                                                    for x in nws["official_pos_list"]))
        if nws.get("official_neg_list"):
            L.append("- **权威利空**: %s" % "、".join("%s(%d条)" % (x["name"], x["count"])
                                                    for x in nws["official_neg_list"]))
        if nws.get("rumor_list"):
            L.append("- **传闻消息**(仅提示): %s" % "、".join("%s" % x["name"] for x in nws["rumor_list"]))
        wf = (nws.get("weekly_fund") or {})
        fund_parts = []
        if wf.get("main_week_net_yi") is not None:
            fund_parts.append("主力资金周累计%s%.0f亿" % (wf.get("main_verdict", ""), abs(wf["main_week_net_yi"])))
        if wf.get("north_ratio") is not None:
            fund_parts.append("北向成分股上涨占比%.0f%%" % (wf["north_ratio"] * 100))
        if fund_parts:
            L.append("- 周度资金: %s" % " | ".join(fund_parts))
    else:
        L.append("- 暂无消息面信号")
    # 推荐准确率周度复盘 (命中率四舍五入与前端 toFixed 一致)
    L.append("")
    L.append("## 【本周推荐准确率】")
    acc = st.get("rec_accuracy") or {}
    if acc.get("has_data"):
        hr = acc.get("week_hit_rate", 0)
        ag = acc.get("week_avg_gain", 0)
        L.append("- 本周已验证%d样本, 命中率 **%d%%**, 次日平均涨幅 **%+.1f%%**"
                 % (acc.get("week_samples", 0), int(round(hr * 100 + 0.0001)), ag))
        gain3, gain5 = acc.get("avg_gain3"), acc.get("avg_gain5")
        if gain5 is not None:
            L.append("- 持有3日平均%+.1f%%(n=%d) / 持有5日平均%+.1f%%(n=%d)"
                     % (gain3 or 0, acc.get("horizon3_n", 0), gain5, acc.get("horizon5_n", 0)))
        bench = acc.get("benchmark") or {}
        if bench.get("bench_gain") is not None:
            L.append("- 基准对比: 本周推荐%s平均%+.1f%% | %s周涨幅%+.1f%% | 超额收益%+.1f%%"
                     % (bench.get("basis", "次日"), ag, bench.get("bench_name", ""),
                        bench["bench_gain"], bench.get("excess") or 0))
        by_conf = acc.get("by_confidence") or {}
        conf_parts = []
        for lv in ("高", "中", "低"):
            v = by_conf.get(lv)
            if v:
                conf_parts.append("%s%d样本命中率%d%%(平均%+.1f%%)"
                                  % (lv, v["samples"], int(round(v["hit_rate"] * 100 + 0.0001)), v["avg_gain"]))
        if conf_parts:
            L.append("- 分置信度: %s" % "；".join(conf_parts))
        if acc.get("best"):
            L.append("- **最佳预测**: %s" % "、".join(
                "%s(+%.1f%%)%s" % (x["name"], x["gain"], ("[%s]" % x["sector"]) if x.get("sector") else "")
                for x in acc["best"]))
        if acc.get("worst"):
            L.append("- **最差预测**: %s" % "、".join(
                "%s(%.1f%%)%s" % (x["name"], x["gain"], ("[%s]" % x["sector"]) if x.get("sector") else "")
                for x in acc["worst"]))
        if acc.get("note"):
            L.append("- %s" % acc["note"])
    else:
        L.append("- 暂无本周推荐准确率数据")
    # 上周复盘验证闭环 (需求 4.3)
    lwc = st.get("last_week_check") or {}
    if lwc.get("has_data"):
        L.append("")
        L.append("## 【上周复盘 · 结果验证】")
        L.append("- %s" % lwc.get("summary", ""))
        for it in lwc.get("items", [])[:5]:
            L.append("  - %s → 本周: **%s**" % (it["name"], it["verdict"]))
    # 主线核心龙头映射 (需求 4.5)
    ct = st.get("core_targets") or {}
    if ct.get("targets"):
        L.append("")
        L.append("## 【主线核心龙头映射】")
        for t in ct["targets"]:
            L.append("- %s: %s" % (t["sector"],
                                   "、".join("%s(%s)" % (s["name"], s["code"]) for s in t["stocks"])))
    # 六、下周关注
    L.append("")
    L.append("## 【下周关注】")
    w = st.get("watch") or {}
    shown = False
    if w.get("focus"):
        shown = True
        L.append("- **★ 重点跟踪**:")
        for x in w["focus"][:8]:
            L.append("  - %s | 跟踪规则: %s" % (x["name"], x.get("rule", "")))
    if w.get("observe"):
        shown = True
        L.append("- **普通观察**:")
        for x in w["observe"][:8]:
            L.append("  - %s | 跟踪规则: %s" % (x["name"], x.get("rule", "")))
    if w.get("avoid"):
        shown = True
        L.append("- **⚠ 风险回避**:")
        for x in w["avoid"][:8]:
            L.append("  - %s | %s" % (x["name"], x.get("reason", "")))
    if not shown:
        L.append("- 无周五复盘数据, 暂无关注")
    L.append("")
    L.append("---")
    L.append("*免责声明: 本复盘为 RPS 动量量化复盘, 仅供研究参考, 不构成投资建议。*")
    L.append("")
    L.append("<!-- AUTO_GEN_END 以下为人工备注区, 自动生成不会覆盖 -->")
    L.append("")
    return "\n".join(L)


def save_weekly(st, md_text):
    """保存 json + md 到 static/ (保留 md 人工备注区)"""
    STATIC_DIR.mkdir(exist_ok=True)
    jp = STATIC_DIR / "weekly_review.json"
    mp = STATIC_DIR / "weekly_review.md"
    manual_block = ""
    if mp.exists():
        try:
            old = mp.read_text(encoding="utf-8")
            if dr.AUTO_GEN_MARKER in old:
                manual_block = old.split(dr.AUTO_GEN_MARKER, 1)[1]
        except Exception:
            manual_block = ""
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1, allow_nan=False)
    with open(mp, "w", encoding="utf-8") as f:
        f.write(md_text + manual_block)
    return jp, mp


def main(force=False):
    """主入口: 读取看板数据生成周度复盘, 幂等(本周已生成且非 force 则跳过)
    周日判断由调用方(rps_calc._maybe_run_weekly_review)负责
    """
    rps_data = _load_json(STATIC_DIR / "rps_data.json")
    history = _load_json(STATIC_DIR / "rps_history.json") or {}
    if not rps_data and not history:
        print("  [x] 看板数据缺失, 跳过周度复盘生成")
        return
    update_date = str(rps_data.get("update_date") or
                      (sorted(history.keys())[-1] if history else ""))
    week_id = week_id_of(update_date) if update_date else ""
    if not force:
        exist = _load_json(STATIC_DIR / "weekly_review.json")
        if exist and exist.get("week_id") == week_id:
            print("  [i] 本周复盘已生成 (%s), 跳过" % week_id)
            return
    st = build_weekly_review(history=history, rps_data=rps_data,
                             daily_review=_load_json(STATIC_DIR / "daily_review.json"),
                             sent_history=_load_json(STATIC_DIR / "review_sentiment_history.json"))
    md = render_markdown(st)
    jp, mp = save_weekly(st, md)
    print("  [OK] 每周复盘已生成: %s / %s (%s)" % (jp.name, mp.name, week_id))
    print(md)


if __name__ == "__main__":
    logger.info("===== weekly_review 开始 =====")
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    force = "--force" in sys.argv
    try:
        main(force=force)
    finally:
        logger.info("===== weekly_review 结束 =====")
