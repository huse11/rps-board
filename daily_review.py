# -*- coding: utf-8 -*-
"""
每日 A 股收盘复盘生成器（动量视角量化复盘）
================================================
定位: 所有结论基于 RPS 看板数据, 有明确数值支撑, 与选股/推荐功能完全打通, 数据 100% 复用。

数据源(全部复用看板已有数据, 不新增接口):
  - static/rps_data.json      三档入选/调出板块(含连续上榜天数、60日RPS50达标数、成分股)
  - static/rps_history.json   全行业 RPS 历史(用于对比昨日/临界晋级/动量衰减)
  - static/index_cache/*.json 指数日线缓存(上证/创业板 20 日线)
  - static/recommendations.json 大盘环境结论(可选复用)

输出:
  - static/daily_review.json  结构化复盘数据(前端渲染)
  - static/daily_review.md    复盘文字初稿(人工润色用)

六大方向: ①整体定调 → ②主线识别 → ③轮动解读 → ④健康度验证 → ⑤风险预警 → ⑥次日清单
进阶: 情绪周期定位 + 风格归因分析
"""
import json
import os
from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"

# ========================== 配置 ==========================
CONFIG_REVIEW = {
    "high_rps_threshold": 90,        # RPS≥90 视为高分板块(与看板入选阈值一致)
    "tier1_top": 10,                 # 第一梯队 Top10
    "tier2_top": 30,                 # 第二梯队 11~30
    "mature_continuous": 5,          # 连续上榜≥5天 = 成熟主线
    "diverge_ratio": 0.15,           # 顶背离: Top10 板块内部达标率 < 15%
    "expand_ratio": 0.3,             # 扩散健康: 内部达标率 ≥ 30%
    "decay_days": 3,                 # 动量衰减观察窗口(天)
    "critical_low": 88,              # 临界晋级池: RPS 处于 [88, 阈值] 区间
    "batch_out": 5,                  # 批量调出预警阈值(个)
    "high_burst": 25,                # 情绪高潮: 高分板块 ≥25
    "high_ice": 8,                   # 情绪冰点: 高分板块 ≤8
    "seed_phase": 4,                 # 情绪启动: 种子板块 ≥4
    "jump_top": 5,                   # 排名跃升榜前 N
}

# 风格归因关键词(板块名 → 风格)
STYLE_KEYWORDS = {
    "科技": ["半导体", "软件", "IT", "通信", "电子", "计算机", "互联网", "元器件", "芯片", "传媒", "游戏", "数字经济", "数据"],
    "消费": ["食品", "饮料", "白酒", "零售", "家电", "汽车", "旅游", "酒店", "服装", "农业", "养殖", "纺织", "家居", "美容"],
    "医药": ["医药", "医疗", "生物", "中药", "化学制药", "疫苗", "健康"],
    "金融": ["银行", "保险", "证券", "金融", "多元"],
    "周期": ["有色", "钢铁", "煤炭", "石油", "化工", "建材", "黄金", "小金属", "稀有", "稀土", "水泥", "化纤", "橡胶"],
    "制造": ["机械", "电气", "仪器", "军工", "航空", "船舶", "专用设备", "通用设备", "自动化", "机器人", "激光"],
    "新能源": ["光伏", "锂电", "电池", "储能", "风电", "新能源", "氢能"],
    "公用": ["电力", "水务", "环保", "燃气", "交通", "物流", "港口", "航运"],
}
# ==========================================================


def _num(v, default=0.0):
    """安全转 float, 空值/异常返回 default"""
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _load_json(path):
    """读取 JSON 文件, 失败返回 None"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_rps_data():
    return _load_json(STATIC_DIR / "rps_data.json")


def load_history():
    return _load_json(STATIC_DIR / "rps_history.json")


def load_recommendations():
    return _load_json(STATIC_DIR / "recommendations.json")


# ========================== 方向1: 市场整体强弱定调 ==========================

def high_rps_counts(history, period="RPS5", threshold=90):
    """统计每个交易日 RPS≥threshold 的板块数量 → {date: count}(按日期升序)"""
    if not history:
        return {}
    counts = {}
    for date, sectors in history.items():
        n = sum(1 for v in sectors.values()
                if v.get(period) is not None and _num(v.get(period)) >= threshold)
        counts[date] = n
    return dict(sorted(counts.items()))


def index_ma20_status(index_cache_file="000001.SH.json"):
    """读取指数日线缓存, 返回 {ok, close, ma20, above, days} 或 None"""
    data = _load_json(STATIC_DIR / "index_cache" / index_cache_file)
    if not data or not isinstance(data, list) or len(data) < 20:
        return None
    closes = [_num(x.get("close")) for x in data if x.get("close") is not None]
    if len(closes) < 20:
        return None
    ma20 = sum(closes[-20:]) / 20
    return {"ok": True, "close": closes[-1], "ma20": ma20,
            "above": closes[-1] >= ma20, "days": len(closes)}


def market_tone(rps_data, history, threshold=90):
    """市场整体强弱定调 → {tone, high5, high10, high20, diff5, index, note}"""
    c5 = high_rps_counts(history, "RPS5", threshold)
    c10 = high_rps_counts(history, "RPS10", threshold)
    c20 = high_rps_counts(history, "RPS20", threshold)
    dates = sorted(c5.keys())
    high5 = c5[dates[-1]] if dates else 0
    high10 = c10[dates[-1]] if dates else 0
    high20 = c20[dates[-1]] if dates else 0
    diff5 = high5 - c5[dates[-2]] if len(dates) >= 2 else 0
    idx = index_ma20_status()
    # 三档平均: 用于强/弱判定
    avg_high = (high5 + high10 + high20) / 3.0 if (high5 + high10 + high20) else 0
    if diff5 >= 5 and (idx is None or idx["above"]):
        tone = "强"
    elif diff5 <= -5 or (high5 + high10 + high20) <= 15:
        tone = "弱"
    else:
        tone = "中性"
    note = []
    if diff5 > 0:
        note.append("高分板块数量增加, 市场强度提升")
    elif diff5 < 0:
        note.append("高分板块数量减少, 市场强度回落")
    if idx is not None:
        note.append("上证指数%s20日线(%.0f点)" % ("站上" if idx["above"] else "跌破", idx["ma20"]))
    return {"tone": tone, "high5": high5, "high10": high10, "high20": high20,
            "diff5": diff5, "avg_high": avg_high, "index": idx,
            "note": "；".join(note) if note else ""}


# ========================== 方向2: 板块梯队与主线确认 ==========================

def _in_sectors(level):
    """取某档位(如 rps_data['rps5']) 的入选板块列表 + 名字集合"""
    names = {x.get("name") for x in level.get("in_list", [])}
    return names, level.get("in_list", [])


def strength_ratio(sector):
    """板块内部强度: 60日RPS50≥90 占比 = rps50_above_90 / stock_count"""
    total = _num(sector.get("stock_count"))
    above = _num(sector.get("rps50_above_90"))
    if total <= 0:
        return 0.0
    return above / total


def resonance_analysis(rps5, rps10, rps20):
    """三周期共振法 → {core, pulse, diverge} 各含板块名+详情"""
    n5, in5 = _in_sectors(rps5)
    n10, _ = _in_sectors(rps10)
    n20, _ = _in_sectors(rps20)
    core = [s for s in in5 if s["name"] in n10 and s["name"] in n20]          # 三档共振
    pulse = [s for s in in5 if s["name"] not in n10 and s["name"] not in n20]  # 仅RPS5
    # 主线分歧: RPS10/20 在榜但 RPS5 不在
    diverge_names = (set(n10) | set(n20)) - set(n5)
    return {"core": core, "pulse": pulse,
            "diverge": [{"name": nm} for nm in sorted(diverge_names)]}


def tier_analysis(in_list):
    """梯队分层: Top10 / 11~30 / 临界"""
    tier1, tier2, tier3 = [], [], []
    for s in in_list:
        r = _num(s.get("rank"), 999)
        if r <= CONFIG_REVIEW["tier1_top"]:
            tier1.append(s)
        elif r <= CONFIG_REVIEW["tier2_top"]:
            tier2.append(s)
        else:
            tier3.append(s)
    return {"tier1": tier1, "tier2": tier2, "tier3": tier3}


# ========================== 方向3: 板块轮动动向解读 ==========================

def rotation_analysis(rps5, rps10, rps20):
    """调入/调出/排名跃升 → {new_in, rank_jump, out_all}"""
    _, in5 = _in_sectors(rps5)
    _, in10 = _in_sectors(rps10)
    _, in20 = _in_sectors(rps20)
    new_in = [s for s in in5 if _num(s.get("continuous")) <= 1]      # 今日新上榜(连续=1)
    jumps = sorted([s for s in in5 if _num(s.get("rank_change")) > 0],
                   key=lambda x: -_num(x.get("rank_change")))[:CONFIG_REVIEW["jump_top"]]
    out_all = []
    for level in (rps5, rps10, rps20):
        for s in level.get("out_list", []):
            if s.get("name") not in out_all:
                out_all.append(s["name"])
    return {"new_in": new_in, "rank_jump": jumps, "out_all": out_all,
            "out_count": len(out_all)}


# ========================== 方向4: 题材/板块健康度验证 ==========================

def health_analysis(in_list):
    """扩散度 + 持续性 → {expand, mature, seed}"""
    expand, mature, seed = [], [], []
    for s in in_list:
        ratio = strength_ratio(s)
        if ratio >= CONFIG_REVIEW["expand_ratio"]:
            expand.append({"name": s["name"], "ratio": round(ratio * 100, 1)})
        if _num(s.get("continuous")) >= CONFIG_REVIEW["mature_continuous"] and _num(s.get("rank")) <= 20:
            mature.append(s["name"])
        if _num(s.get("continuous")) <= 1:
            seed.append(s["name"])
    return {"expand": expand, "mature": mature, "seed": seed}


# ========================== 方向5: 市场风险与退潮预警 ==========================

def _style_of(name):
    """板块名 → 风格分类(第一个命中的关键词)"""
    for style, kws in STYLE_KEYWORDS.items():
        for kw in kws:
            if kw in name:
                return style
    return "其他"


def risk_analysis(rps5, rps10, rps20, history):
    """风险预警 → {batch_out, diverge, momentum_decay, warnings:[str]}"""
    _, in5 = _in_sectors(rps5)
    warnings = []
    out = [s for s in rps5.get("out_list", [])]
    # 1) 批量调出预警
    batch_out = {}
    if len(out) >= CONFIG_REVIEW["batch_out"]:
        for s in out:
            st = _style_of(s["name"])
            batch_out[st] = batch_out.get(st, 0) + 1
        top_style = max(batch_out, key=batch_out.get)
        if batch_out[top_style] >= 3:
            warnings.append("批量调出预警: 今日调出%d个板块, 集中在「%s」(%d个), 该方向短期回避"
                            % (len(out), top_style, batch_out[top_style]))
    # 2) 高位顶背离预警
    diverge = []
    for s in in5:
        if _num(s.get("rank")) <= 10 and strength_ratio(s) < CONFIG_REVIEW["diverge_ratio"]:
            diverge.append({"name": s["name"], "ratio": round(strength_ratio(s) * 100, 1)})
    if diverge:
        warnings.append("高位顶背离: %s 排名靠前但内部个股达标率低(%.0f%%), 指数虚高警惕回落"
                        % (diverge[0]["name"], diverge[0]["ratio"]))
    # 3) 整体动量衰减预警
    c5 = high_rps_counts(history, "RPS5")
    momentum_decay = False
    dates = sorted(c5.keys())
    if len(dates) >= CONFIG_REVIEW["decay_days"]:
        recent = [c5[d] for d in dates[-CONFIG_REVIEW["decay_days"]:]]
        if recent == sorted(recent, reverse=True) and recent[0] > recent[-1]:
            momentum_decay = True
            warnings.append("动量衰减预警: 高分板块数量连续%d天减少(%s→%s), 市场进入退潮期, 降低仓位"
                            % (CONFIG_REVIEW["decay_days"] - 1, recent[0], recent[-1]))
    return {"batch_out": batch_out, "diverge": diverge,
            "momentum_decay": momentum_decay, "warnings": warnings}


# ========================== 方向6: 次日跟踪清单 ==========================

def watchlist(rps5, rps10, rps20, history, threshold=90):
    """次日清单 → {seed, critical, diverge_watch}"""
    _, in5 = _in_sectors(rps5)
    seed = [s["name"] for s in in5 if _num(s.get("continuous")) <= 1]
    # 临界晋级池: RPS 接近阈值 + 排名连续2日上升(需要历史)
    critical = []
    if history:
        dates = sorted(history.keys())
        today, prev = dates[-1], dates[-2]
        for ind, v in history[today].items():
            r = _num(v.get("RPS5"))
            if CONFIG_REVIEW["critical_low"] <= r < threshold:
                pv = history[prev].get(ind, {})
                if _num(pv.get("rank5")) and _num(v.get("rank5")) < _num(pv.get("rank5")):
                    critical.append({"name": ind, "rps5": round(r, 1)})
        critical.sort(key=lambda x: -x["rps5"])
    # 主线分歧池: RPS5 不在榜但 RPS20 在榜
    n5 = {s["name"] for s in in5}
    n20 = {s["name"] for s in rps20.get("in_list", [])}
    diverge_watch = sorted(n20 - n5)
    return {"seed": seed, "critical": critical, "diverge_watch": diverge_watch}


# ========================== 进阶: 情绪周期 + 风格归因 ==========================

def sentiment_phase(rps5, rps10, rps20, history):
    """情绪周期定位: 启动/发酵/高潮/退潮/冰点"""
    _, in5 = _in_sectors(rps5)
    seed_n = sum(1 for s in in5 if _num(s.get("continuous")) <= 1)
    out_n = len(rps5.get("out_list", []))
    core_n = len(resonance_analysis(rps5, rps10, rps20)["core"])
    c5 = high_rps_counts(history, "RPS5")
    dates = sorted(c5.keys())
    high_n = c5[dates[-1]] if dates else 0
    phase = "震荡期"
    if high_n <= CONFIG_REVIEW["high_ice"]:
        phase = "冰点期"
    elif out_n >= CONFIG_REVIEW["batch_out"] and core_n <= 3:
        phase = "退潮期"
    elif high_n >= CONFIG_REVIEW["high_burst"]:
        phase = "高潮期"
    elif core_n >= 3 and seed_n >= CONFIG_REVIEW["seed_phase"]:
        phase = "发酵期"
    elif seed_n >= CONFIG_REVIEW["seed_phase"]:
        phase = "启动期"
    return {"phase": phase, "seed_n": seed_n, "out_n": out_n,
            "core_n": core_n, "high_n": high_n}


def style_analysis(rps5, rps10, rps20):
    """风格归因: 按板块名关键词统计三类榜单的风格分布"""
    def _style_count(level):
        cnt = {}
        for s in level.get("in_list", []):
            st = _style_of(s["name"])
            cnt[st] = cnt.get(st, 0) + 1
        return cnt
    return {"rps5": _style_count(rps5), "rps10": _style_count(rps10),
            "rps20": _style_count(rps20)}


# ========================== 汇总 + 渲染 ==========================

def build_review_stats(rps_data, history=None):
    """汇总全部复盘统计 → dict"""
    if not rps_data:
        raise ValueError("rps_data 为空")
    r5, r10, r20 = rps_data.get("rps5", {}), rps_data.get("rps10", {}), rps_data.get("rps20", {})
    _, in5 = _in_sectors(r5)
    _, in10 = _in_sectors(r10)
    _, in20 = _in_sectors(r20)
    stats = {
        "update_date": rps_data.get("update_date", ""),
        "prev_date": rps_data.get("prev_date", ""),
        "total_industries": rps_data.get("total_industries", 0),
        "threshold": rps_data.get("threshold", 90),
        "market": market_tone(rps_data, history),
        "resonance": _slim_sections(resonance_analysis(r5, r10, r20)),
        "tier": _slim_tiers(tier_analysis(in5)),
        "rotation": _slim_sections(rotation_analysis(r5, r10, r20)),
        "health": health_analysis(in5),
        "risk": risk_analysis(r5, r10, r20, history),
        "watch": watchlist(r5, r10, r20, history),
        "sentiment": sentiment_phase(r5, r10, r20, history),
        "style": style_analysis(r5, r10, r20),
        "market_status_rec": (load_recommendations() or {}).get("market_status", ""),
    }
    return stats


def _slim_sections(d):
    """dict 内所有 sector 列表 → 精简字段"""
    out = {}
    for k, v in d.items():
        if isinstance(v, list):
            out[k] = [_slim_sector(x) if isinstance(x, dict) and "name" in x else x for x in v]
        else:
            out[k] = v
    return out


def _slim_tiers(t):
    return {k: [_slim_sector(x) for x in v] for k, v in t.items()}


def _slim_sector(s):
    """板块条目 → 精简字段(去 stocks 等大字段, 前端足够用)"""
    return {"name": s.get("name", ""),
            "RPS5": _num(s.get("RPS5")),
            "rank": int(_num(s.get("rank"), 0)),
            "rank_change": int(_num(s.get("rank_change"))),
            "continuous": int(_num(s.get("continuous"))),
            "ratio": round(strength_ratio(s) * 100, 1),
            "stock_count": int(_num(s.get("stock_count"))),
            "rps50_above_90": int(_num(s.get("rps50_above_90")))}


def _fmt_sector_list(lst, max_n=8):
    """板块名列表 → 文案(超长截断)"""
    if not lst:
        return "无"
    names = [x.get("name", "") for x in lst] if isinstance(lst[0], dict) else list(lst)
    if not names:
        return "无"
    shown = "、".join(names[:max_n])
    if len(names) > max_n:
        shown += " 等%d个" % len(names)
    return shown


def _fmt_market_status(ms):
    """recommendations.json 的 market_status dict → 一句话摘要"""
    if not isinstance(ms, dict):
        return ""
    safe = ms.get("is_safe")
    parts = ["大盘环境合格" if safe else "大盘环境不合格"]
    if ms.get("advance_ratio") is not None:
        parts.append("上涨占比%.0f%%" % (_num(ms.get("advance_ratio")) * 100))
    if ms.get("note"):
        parts.append(str(ms.get("note")))
    return "；".join(parts)


def _ratio_text(s):
    return "%.0f%%" % (strength_ratio(s) * 100)


def render_markdown(stats):
    """复盘统计 → markdown 文字初稿"""
    m = stats["market"]
    res = stats["resonance"]
    tier = stats["tier"]
    rot = stats["rotation"]
    heal = stats["health"]
    risk = stats["risk"]
    watch = stats["watch"]
    sent = stats["sentiment"]
    style = stats["style"]
    u = stats["update_date"]
    L = []
    L.append("# RPS 看板每日复盘（%s）" % u)
    L.append("")
    # 一、整体定调
    L.append("## 一、市场整体定调")
    L.append("今日市场动量**%s**：RPS5≥90 板块 %d 个（较前日 %+d）、RPS10≥90 板块 %d 个、RPS20≥90 板块 %d 个。%s"
             % (m["tone"], m["high5"], m["diff5"], m["high10"], m["high20"], m["note"]))
    if stats.get("market_status_rec"):
        ms_txt = _fmt_market_status(stats["market_status_rec"])
        if ms_txt:
            L.append("推荐系统大盘环境判定：**%s**。" % ms_txt)
    L.append("")
    # 二、主线识别
    L.append("## 二、板块强弱梯队与主线")
    if res["core"]:
        L.append("- **核心主线**（三周期共振 %d 个）：%s" % (len(res["core"]), _fmt_sector_list(res["core"])))
    if res["pulse"]:
        L.append("- **短期脉冲**（仅 RPS5 上榜 %d 个）：%s" % (len(res["pulse"]), _fmt_sector_list(res["pulse"])))
    if res["diverge"]:
        L.append("- **主线分歧**（RPS10/20 在榜、RPS5 回落）：%s" % _fmt_sector_list(res["diverge"]))
    if tier["tier1"]:
        L.append("- **第一梯队**（Top%d）：%s" % (CONFIG_REVIEW["tier1_top"], _fmt_sector_list(tier["tier1"])))
    if tier["tier2"]:
        L.append("- **第二梯队**（11~%d 支线/补涨）：%s" % (CONFIG_REVIEW["tier2_top"], _fmt_sector_list(tier["tier2"])))
    # 内部强度验证: 核心主线的达标率
    if res["core"]:
        ratio_txt = "、".join("%s(%.0f%%)" % (s["name"], strength_ratio(s) * 100) for s in res["core"][:6])
        L.append("- **内部强度验证**（60日RPS50达标率）：%s" % ratio_txt)
    L.append("")
    # 三、轮动解读
    L.append("## 三、板块轮动动向")
    if rot["new_in"]:
        L.append("- **今日新调入**（%d 个）：%s" % (len(rot["new_in"]), _fmt_sector_list(rot["new_in"])))
    if rot["rank_jump"]:
        jumps = "、".join("%s(%+d)" % (s["name"], int(_num(s.get("rank_change")))) for s in rot["rank_jump"])
        L.append("- **排名跃升前5**：%s" % jumps)
    if rot["out_count"]:
        L.append("- **今日调出**（%d 个）：%s" % (rot["out_count"], _fmt_sector_list(rot["out_all"])))
    style_top = sorted(style["rps5"].items(), key=lambda x: -x[1])[:3]
    if style_top:
        L.append("- **风格分布**：%s" % "；".join("%s%d个" % (k, v) for k, v in style_top))
    L.append("")
    # 四、健康度
    L.append("## 四、题材/板块健康度")
    if heal["expand"]:
        L.append("- **扩散健康**（内部达标率≥30%%）：%s" % _fmt_sector_list(heal["expand"]))
    if heal["mature"]:
        L.append("- **成熟主线**（连续上榜≥%d天）：%s" % (CONFIG_REVIEW["mature_continuous"], _fmt_sector_list(heal["mature"])))
    if heal["seed"]:
        L.append("- **种子板块**（首日入选）：%s" % _fmt_sector_list(heal["seed"]))
    if not heal["expand"] and not heal["mature"] and not heal["seed"]:
        L.append("- 暂无入选板块数据")
    L.append("")
    # 五、风险预警
    L.append("## 五、风险与退潮预警")
    if risk["warnings"]:
        for w in risk["warnings"]:
            L.append("- 风险信号: %s" % w)
    else:
        L.append("- 无风险信号: 未检测到批量调出、高位顶背离、动量衰减，市场结构健康")
    L.append("")
    # 六、次日清单
    L.append("## 六、次日跟踪清单")
    if watch["seed"]:
        L.append("- **种子观察池**（次日连榜则确认强度）：%s" % _fmt_sector_list(watch["seed"]))
    if watch["critical"]:
        crit = "、".join("%s(%.0f)" % (c["name"], c["rps5"]) for c in watch["critical"][:8])
        L.append("- **临界晋级池**（RPS≈阈值且排名连续上升）：%s" % crit)
    if watch["diverge_watch"]:
        L.append("- **主线分歧池**（RPS20 在榜、RPS5 回落，分歧低吸观察）：%s" % _fmt_sector_list(watch["diverge_watch"]))
    if not watch["seed"] and not watch["critical"] and not watch["diverge_watch"]:
        L.append("- 暂无跟踪标的")
    L.append("")
    # 进阶
    L.append("## 进阶观察")
    L.append("- **情绪周期**：%s（种子%d/调出%d/高分%d）" % (sent["phase"], sent["seed_n"], sent["out_n"], sent["high_n"]))
    L.append("- **免责声明**：本复盘为 RPS 动量量化复盘，仅供研究参考，不构成投资建议。")
    return "\n".join(L)


def save_review(stats, md_text):
    """保存 json + md 到 static/"""
    STATIC_DIR.mkdir(exist_ok=True)
    json_path = STATIC_DIR / "daily_review.json"
    md_path = STATIC_DIR / "daily_review.md"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_text)
    return json_path, md_path


def main(force=False):
    """主入口: 读取 rps_data/history 生成复盘, 幂等(同日不重复生成)"""
    rps_data = load_rps_data()
    if not rps_data:
        print("  ❌ rps_data.json 缺失, 跳过复盘生成")
        return
    history = load_history()
    if not history:
        print("  ⚠️ rps_history.json 缺失, 部分对比维度将降级")
    # 幂等: 当天已生成则跳过
    if not force:
        exist = _load_json(STATIC_DIR / "daily_review.json")
        if exist and exist.get("update_date") == rps_data.get("update_date"):
            print("  [i] 当日复盘已生成 (%s), 跳过" % rps_data.get("update_date"))
            return
    stats = build_review_stats(rps_data, history)
    md = render_markdown(stats)
    json_path, md_path = save_review(stats, md)
    print("  [OK] 每日复盘已生成: %s / %s" % (json_path.name, md_path.name))
    print(md)


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    force = "--force" in sys.argv
    main(force=force)
