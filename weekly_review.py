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
    "new_in_days": 2,      # 本周在榜天数≤2 且 最新在榜 = 新晋板块
    "strong_avg": 10,      # 周均高分板块数≥10 → 周度强 (RPS95下数量恒~10, 饱和即强)
    "weak_last": 4,        # 周末高分≤4 → 周度弱 (RPS95下数量恒~10, 4以下=收缩)
    "rise": 1,             # 周内高分增幅≥+1 → 走强
    "fall": -1,            # 周内降幅≤-1 → 走弱
    "hot_top": 8,          # 周度热度榜前 N
    "watch_top": 10,       # 下周关注数量上限
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
    """周度强弱定调: 每日高分板块数序列(RPS5≥95) 的周均值/周内趋势
    → {dates, series:[{date, high_n}], first, last, avg, diff, tone, note}
    """
    counts = dr.high_rps_counts(history, "RPS5", 95)
    dates = _week_dates(history, days)
    series = [{"date": d, "high_n": int(counts.get(d, 0))} for d in dates]
    if not series:
        return {"dates": [], "series": [], "first": 0, "last": 0, "avg": 0,
                "diff": 0, "tone": "周度中性", "note": ""}
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
    note = "周均高分板块%.0f个(首日%d→末日%d, %+d)" % (avg, first, last, diff)
    if diff > 0:
        note += ", 周内热度走高"
    elif diff < 0:
        note += ", 周内热度回落"
    return {"dates": dates, "series": series, "first": first, "last": last,
            "avg": round(avg, 1), "diff": diff, "tone": tone, "note": note}


# ========================== 2. 本周主线 ==========================

def _week_sector_days(history, dates):
    """本周窗口内每个板块 RPS5≥95 的在榜天数 + 最新 RPS5/排名"""
    result = {}
    for ind in (set().union(*[set((history[d] or {}).keys()) for d in dates]) if dates else set()):
        days_on = 0
        latest_rps, latest_rank = None, None
        for d in dates:
            v = (history.get(d) or {}).get(ind) or {}
            if _num(v.get("RPS5")) >= 95:
                days_on += 1
            latest_rps = _num(v.get("RPS5"), latest_rps)
            latest_rank = _num(v.get("rank5"), latest_rank)
        result[ind] = {"days_on": days_on, "rps5": latest_rps, "rank": latest_rank}
    return result


def week_mainlines(history, days=None):
    """本周主线: 窗口内在榜天数≥threshold 的板块, 按在榜天数+RPS5 排序
    → [{name, days_on, rps5, rank}]
    """
    dates = _week_dates(history, days)
    stats = _week_sector_days(history, dates)
    out = []
    for ind, s in stats.items():
        if dr._is_noise_sector(ind):
            continue
        if s["days_on"] >= CONFIG_WEEKLY["mainline_days"]:
            out.append({"name": ind, "days_on": s["days_on"],
                        "rps5": round(_num(s["rps5"]), 1), "rank": int(_num(s["rank"]))})
    out.sort(key=lambda x: (-x["days_on"], -x["rps5"]))
    return out[:CONFIG_WEEKLY["hot_top"]]


# ========================== 3. 本周轮动 ==========================

def week_rotation(history, days=None):
    """本周轮动: 新晋(周初不在榜→周末在榜) / 持续(全周在榜) / 衰退(周初在榜→周末不在榜)
    → {new_in:[{name, days_on, rps5}], sustained:[...], dropped:[{name, days_on}]}
    """
    dates = _week_dates(history, days)
    if len(dates) < 2:
        return {"new_in": [], "sustained": [], "dropped": []}
    first_set = dr._history_set(history, dates[0], "RPS5", 95)
    last_set = dr._history_set(history, dates[-1], "RPS5", 95)
    stats = _week_sector_days(history, dates)
    new_in, sustained, dropped = [], [], []
    for ind in sorted(last_set):
        s = stats.get(ind, {})
        if s.get("days_on", 0) <= CONFIG_WEEKLY["new_in_days"]:
            new_in.append({"name": ind, "days_on": s.get("days_on", 0),
                           "rps5": round(_num(s.get("rps5")), 1)})
        elif s.get("days_on", 0) >= CONFIG_WEEKLY["sustained_days"]:
            sustained.append({"name": ind, "days_on": s.get("days_on", 0),
                              "rps5": round(_num(s.get("rps5")), 1)})
    for ind in sorted(first_set - last_set):
        dropped.append({"name": ind, "days_on": stats.get(ind, {}).get("days_on", 0)})
    new_in.sort(key=lambda x: -x["rps5"])
    sustained.sort(key=lambda x: -x["rps5"])
    dropped.sort(key=lambda x: -x["days_on"])
    return {"new_in": new_in, "sustained": sustained, "dropped": dropped}


# ========================== 4. 本周情绪演变 ==========================

def week_sentiment(sent_history, dates):
    """本周情绪演变: 从每日情绪周期历史取本周窗口记录
    → {series:[{date, phase, high_n}], start_phase, end_phase, trend}
    """
    dates = list(dates or [])
    records = [s for s in (sent_history or [])
               if isinstance(s, dict) and s.get("date") in dates]
    records.sort(key=lambda x: str(x.get("date", "")))
    if not records:
        return {"series": [], "start_phase": "", "end_phase": "",
                "trend": "", "phase_change": ""}
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
    return {"series": records, "start_phase": start, "end_phase": end,
            "trend": trend, "phase_change": phase_change}


# ========================== 5. 消息面周度快照 ==========================

def week_news(recs=None, early_recs=None):
    """周度消息面快照: 复用 daily_review.news_review (周五推荐的消息面聚合)
    注: 每日推荐文件会被覆盖, 无法回溯周内每日消息面, 故用周五快照标注
    """
    if recs is None:
        recs = dr.load_recommendations()
    if early_recs is None:
        early_recs = dr.load_early_recommendations()
    return dr.news_review(recs, early_recs)


# ========================== 6. 下周关注 ==========================

def week_watch(daily_review):
    """下周关注: 复用周五每日复盘的次日清单 + 潜力板块
    → {critical, diverge_watch, seed_high, potential, note}
    """
    if not isinstance(daily_review, dict):
        return {}
    watch = daily_review.get("watch") or {}
    out = {
        "critical": (watch.get("critical") or [])[:CONFIG_WEEKLY["watch_top"]],
        "diverge_watch": (watch.get("diverge_watch") or [])[:CONFIG_WEEKLY["watch_top"]],
        "seed_high": (watch.get("seed_high") or [])[:CONFIG_WEEKLY["watch_top"]],
        "potential": (daily_review.get("potential_sectors") or [])[:CONFIG_WEEKLY["watch_top"]],
    }
    return out


# ========================== 推荐准确率周度复盘 ==========================

def week_rec_accuracy(history=None):
    """本周推荐准确率: 从 prediction_review.json 中筛选本周预测日的验证结果
    返回周度命中率/平均涨幅/分置信度/最佳最差个股
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
    # 分置信度
    by_conf = {}
    for lv in ("高", "中", "低"):
        sub = [x for x in week_preds if x.get("confidence") == lv]
        if sub:
            hr = round(sum(1 for x in sub if x.get("hit")) / len(sub), 3)
            ag = round(sum(x.get("gain", 0) for x in sub) / len(sub), 2)
            by_conf[lv] = {"samples": len(sub), "hit_rate": hr, "avg_gain": ag}
    sorted_hits = sorted(week_preds, key=lambda x: -x.get("gain", 0))
    best = [{"name": x.get("name"), "code": x.get("code"), "gain": x.get("gain"),
             "confidence": x.get("confidence"), "pred_date": x.get("pred_date")}
            for x in sorted_hits[:3]]
    worst = [{"name": x.get("name"), "code": x.get("code"), "gain": x.get("gain"),
              "confidence": x.get("confidence"), "pred_date": x.get("pred_date")}
             for x in sorted(week_preds, key=lambda x: x.get("gain", 0))[:3]]
    return {
        "has_data": True,
        "week_samples": len(week_preds),
        "week_hit_rate": hit_rate,
        "week_avg_gain": avg_gain,
        "by_confidence": by_conf,
        "best": best,
        "worst": worst,
        "horizon": review.get("horizon", 1),
        "note": "本周预测中已完成次日验证的样本统计",
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
    mainlines = week_mainlines(history)
    rotation = week_rotation(history)
    sentiment = week_sentiment(sent_history, dates)
    news = week_news(recs, early_recs)
    watch = week_watch(daily_review)
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
    """周度复盘统计 → markdown 文字初稿"""
    u = st.get("update_date", "")
    L = []
    L.append("# RPS 看板每周复盘（%s, %s）" % (st.get("week_id", ""), u))
    L.append("")
    # 一、周度定调
    tone = st.get("tone") or {}
    L.append("## 【本周定调】")
    if tone.get("series"):
        seq = " → ".join("%d" % x["high_n"] for x in tone["series"])
        L.append("周度强度: **%s**; 本周%d个交易日高分板块数: %s; %s"
                 % (tone.get("tone", "周度中性"), len(tone["series"]), seq, tone.get("note", "")))
    else:
        L.append("周度强度: **%s** (无本周历史数据)" % tone.get("tone", "周度中性"))
    # 二、本周主线
    L.append("")
    L.append("## 【本周主线】")
    if st.get("mainlines"):
        for x in st["mainlines"]:
            L.append("- **%s**: 本周在榜%d天, RPS5=%.0f, 排名第%d"
                     % (x["name"], x["days_on"], x["rps5"], x["rank"]))
    else:
        L.append("- 本周无持续性主线(缺乏连续上榜板块)")
    # 三、本周轮动
    L.append("")
    L.append("## 【本周轮动】")
    rot = st.get("rotation") or {}
    if rot.get("sustained"):
        L.append("- **持续主线**(全周在榜): %s"
                 % "、".join("%s(%d天)" % (x["name"], x["days_on"]) for x in rot["sustained"][:8]))
    if rot.get("new_in"):
        L.append("- **本周新晋**(周初不在榜→周末在榜): %s"
                 % "、".join("%s(%d天,RPS%.0f)" % (x["name"], x["days_on"], x["rps5"]) for x in rot["new_in"][:8]))
    if rot.get("dropped"):
        L.append("- **本周衰退**(周初在榜→周末不在榜): %s"
                 % "、".join("%s(在榜%d天)" % (x["name"], x["days_on"]) for x in rot["dropped"][:8]))
    if not rot.get("sustained") and not rot.get("new_in") and not rot.get("dropped"):
        L.append("- 无明显轮动")
    # 四、情绪演变
    L.append("")
    L.append("## 【本周情绪演变】")
    sent = st.get("sentiment") or {}
    if sent.get("series"):
        seq = "、".join("%s(%s)" % (x.get("date", ""), x.get("phase", "")) for x in sent["series"])
        L.append("%s; 逐日: %s" % (sent.get("trend", ""), seq))
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
        if nws.get("official_pos_list"):
            L.append("- **权威利好**: %s" % "、".join("%s(%d条)" % (x["name"], x["count"])
                                                    for x in nws["official_pos_list"]))
        if nws.get("official_neg_list"):
            L.append("- **权威利空**: %s" % "、".join("%s(%d条)" % (x["name"], x["count"])
                                                    for x in nws["official_neg_list"]))
        if nws.get("rumor_list"):
            L.append("- **传闻消息**(仅提示): %s" % "、".join("%s" % x["name"] for x in nws["rumor_list"]))
    else:
        L.append("- 暂无消息面信号")
    # 推荐准确率周度复盘 (命中率四舍五入与前端 toFixed 一致)
    L.append("")
    L.append("## 【本周推荐准确率】")
    acc = st.get("rec_accuracy") or {}
    if acc.get("has_data"):
        hr = acc.get("week_hit_rate", 0)
        ag = acc.get("week_avg_gain", 0)
        L.append("- 本周已验证%d样本, 命中率 **%d%%**, 平均涨幅 **%+.1f%%**"
                 % (acc.get("week_samples", 0), int(round(hr * 100 + 0.0001)), ag))
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
            L.append("- **最佳预测**: %s" % "、".join("%s(+%.1f%%)" % (x["name"], x["gain"]) for x in acc["best"]))
        if acc.get("worst"):
            L.append("- **最差预测**: %s" % "、".join("%s(%.1f%%)" % (x["name"], x["gain"]) for x in acc["worst"]))
        if acc.get("note"):
            L.append("- %s" % acc["note"])
    else:
        L.append("- 暂无本周推荐准确率数据")
    # 六、下周关注
    L.append("")
    L.append("## 【下周关注】")
    w = st.get("watch") or {}
    if w.get("critical"):
        L.append("- **临界晋级**(RPS≈阈值, 连升): %s"
                 % "、".join("%s(%.0f)" % (c["name"], c["rps5"]) for c in w["critical"][:8]))
    if w.get("seed_high"):
        L.append("- **高质量种子**(首日入选+扩散达标): %s"
                 % "、".join("%s(%.0f%%)" % (s["name"], s["ratio"]) for s in w["seed_high"][:8]))
    if w.get("potential"):
        L.append("- **潜力板块**(RPS5∈[88,95) 内部达标): %s"
                 % "、".join("%s(%.0f)" % (p["name"], p["rps5"]) for p in w["potential"][:8]))
    if w.get("diverge_watch"):
        L.append("- **主线分歧**(RPS20在榜RPS5掉, 良性低吸观察): %s"
                 % "、".join("%s(掉%d天)" % (x["name"], x["drop_days"]) for x in w["diverge_watch"][:8]))
    if not w:
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
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    force = "--force" in sys.argv
    main(force=force)
