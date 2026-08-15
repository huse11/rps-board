# -*- coding: utf-8 -*-
"""
每日 A 股收盘复盘生成器（动量视角量化复盘 v2）
================================================
定位: 所有结论基于 RPS 看板数据, 有明确数值支撑, 与选股/推荐功能完全打通, 数据 100% 复用。
v2 优化点(2026-08): 定调双约束(变化量+绝对水位)、硬核/韧性主线分级、梯队达标率过滤、
  轮动分类(脉冲/主线调出+反复轮动)、健康度规模自适应阈值+成熟疲劳、风险分级(hint/warning/danger)、
  次日清单分池+掉榜天数、情绪周期主标签+复合信号、风格多标签+占比、RPS与推荐大盘一致性校验。

数据源(全部复用看板已有数据, 不新增接口):
  - static/rps_data.json      三档入选/调出板块(含连续上榜天数、60日RPS50达标数、成分股)
  - static/rps_history.json   全行业 RPS 历史(用于对比昨日/临界晋级/动量衰减/历史分位/反复轮动)
  - static/index_cache/*.json 指数日线缓存(上证/创业板 20 日线)
  - static/recommendations.json 大盘环境结论(可选, 用于一致性校验)

输出:
  - static/daily_review.json  结构化复盘数据(前端渲染)
  - static/daily_review.md    复盘文字初稿(人工润色用)

六大方向: ①整体定调 → ②主线识别 → ③轮动解读 → ④健康度验证 → ⑤风险预警 → ⑥次日清单
进阶: 情绪周期定位(主标签+附加信号) + 风格归因(多标签) + 一致性校验
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
    "tier1_min_ratio": 0.15,         # 第一梯队最低内部达标率(过滤小体量脉冲)
    "tier2_min_ratio": 0.10,         # 第二梯队最低内部达标率
    "mature_continuous": 5,          # 连续上榜≥5天 = 成熟主线
    "mature_fatigue_days": 8,        # 连续上榜≥8天 且达标率低于规模阈值 = 成熟疲劳
    "diverge_ratio": 0.15,           # 顶背离: Top10 板块内部达标率 < 15%
    "diverge_min_count": 12,         # 顶背离前置: 板块成分股≥12 才告警(排除极小板块)
    "expand_small_limit": 15,        # 小板块: 成分股<15
    "expand_large_limit": 40,        # 大板块: 成分股>40
    "expand_ratio_small": 0.40,      # 小板块扩散健康阈值
    "expand_ratio_mid": 0.30,        # 中板块扩散健康阈值
    "expand_ratio_large": 0.20,      # 大板块扩散健康阈值
    "decay_days": 3,                 # 动量衰减观察窗口(天)
    "decay_high_floor": 15,          # 动量衰减判定: 当前高分>15 才触发(低位回落不重复告警)
    "critical_low": 88,              # 临界晋级池: RPS 处于 [88, 阈值] 区间
    "batch_out": 5,                  # 批量调出预警阈值(个)
    "batch_style_n": 3,              # 同风格调出≥3 触发提示
    "high_burst": 25,                # 情绪高潮: 高分板块 ≥25
    "high_ice": 8,                   # 情绪冰点: 高分板块 ≤8
    "seed_phase": 4,                 # 情绪启动/发酵: 高质量种子 ≥4
    "jump_top": 5,                   # 排名跃升榜前 N
    "jump_min_gain": 8,              # 排名跃升最小值(过滤小幅名次变动)
    "pulse_continuous": 2,           # 调出分类: 上榜≤2天调出 = 脉冲行情
    "mainline_continuous": 5,        # 调出分类: 上榜≥5天调出 = 老主线终结
    "strong_min_diff": 3,            # 全面强: diff≥+3
    "strong_min_high": 20,           # 全面强: 今日高分≥20
    "structural_high": 25,           # 结构性强: 高分≥25(沪指破20日线也允许)
    "weak_min_diff": -5,             # 弱: diff≤-5
    "weak_max_high": 14,             # 弱: 且高分≤14
    "weak_total_max": 10,            # 弱: 三档合计≤10
    "hist_percentile_days": 120,     # 高分数量历史分位窗口
    "repeat_window": 10,             # 反复轮动观察窗口(交易日)
    "repeat_flips": 3,               # 反复轮动: 近10日在榜状态翻转≥3次
    # 板块生命周期标记 (早期启动挖掘用, 只新增不改动原有字段)
    "lifecycle": {
        "fatigue_min_days": 8,       # 连续上榜≥8天 且 达标率跌破规模阈值 → fatigue (成熟疲劳)
        "accelerate_min_days": 5,    # 连续上榜≥5天 且 满足高位条件 → accelerate (加速高潮, 早期模式排除)
        "accelerate_rps5": 95,       # 加速条件1: RPS5 ≥ 95 (高位加速)
        "accelerate_rank": 10,       # 加速条件2: 排名 ≤ 10 (头部加速)
        "growth_min_days": 3,        # 连续上榜≥3天 → growth (成长期)
    },
}

# 风格归因关键词(板块名 → 风格, 支持多标签匹配)
STYLE_KEYWORDS = {
    "科技": ["半导体", "软件", "IT", "通信", "电子", "计算机", "互联网", "元器件", "芯片", "传媒", "游戏", "数字经济", "数据",
             "光模块", "算力", "集成电路", "印制电路板", "消费电子", "光学"],
    "消费": ["食品", "饮料", "白酒", "零售", "家电", "汽车", "旅游", "酒店", "服装", "农业", "养殖", "纺织", "家居", "美容",
             "乳制", "肉制", "休闲食品", "调味", "啤酒", "软饮", "厨卫", "家装", "渔业"],
    "医药": ["医药", "医疗", "生物", "中药", "化学制药", "疫苗", "健康", "中成", "血制", "体外诊断", "动物保健", "制药"],
    "金融": ["银行", "保险", "证券", "金融", "多元"],
    "周期": ["有色", "钢铁", "煤炭", "石油", "化工", "建材", "黄金", "小金属", "稀有", "稀土", "水泥", "化纤", "橡胶",
             "地产", "贵金属", "工业金属", "能源金属", "炼化", "特钢", "玻纤", "基建", "房屋", "铅锌"],
    "制造": ["机械", "电气", "仪器", "军工", "航空", "船舶", "专用设备", "通用设备", "自动化", "机器人", "激光",
             "机床", "锻铸", "零部件", "轨交", "航天", "装备"],
    "新能源": ["光伏", "锂电", "电池", "储能", "风电", "新能源", "氢能", "电网"],
    "公用": ["电力", "水务", "环保", "燃气", "交通", "物流", "港口", "航运", "机场", "高速"],
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


# ========================== 工具 ==========================

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


def expand_threshold(stock_count):
    """板块内部达标率扩散健康阈值(随成分股规模自适应, 解决小板块虚高/大板块过严)"""
    n = _num(stock_count)
    if n < CONFIG_REVIEW["expand_small_limit"]:
        return CONFIG_REVIEW["expand_ratio_small"]
    if n <= CONFIG_REVIEW["expand_large_limit"]:
        return CONFIG_REVIEW["expand_ratio_mid"]
    return CONFIG_REVIEW["expand_ratio_large"]


def _style_of(name):
    """板块名 → 命中的全部风格(多标签), 未命中返回 未归类"""
    matched = set()
    for style, kws in STYLE_KEYWORDS.items():
        for kw in kws:
            if kw in str(name):
                matched.add(style)
    return matched or {"未归类"}


def _is_noise_sector(name):
    """噪声板块过滤: 名称含 ST/退/停牌 等关键词, 历史分位统计时剔除, 避免噪声污染"""
    nm = str(name or "")
    return any(kw in nm for kw in ("ST", "退", "停", "*", "其他"))

def high_rps_counts(history, period="RPS5", threshold=90):
    """统计每个交易日 RPS≥threshold 的板块数量 → {date: count}(按日期升序)
    过滤 ST/退市/停牌等噪声板块, 保证历史分位统计干净"""
    if not history:
        return {}
    counts = {}
    for date, sectors in history.items():
        n = sum(1 for ind, v in sectors.items()
                if not _is_noise_sector(ind)
                and v.get(period) is not None and _num(v.get(period)) >= threshold)
        counts[date] = n
    return dict(sorted(counts.items()))


def index_ma20_status(index_cache_file="000001.SH.json"):
    """读取指数日线缓存, 返回 {ok, close, ma20, above, days} 或 None
    缓存可能为降序(Tushare index_daily 返回降序), 统一按日期升序后取尾部为最新
    """
    data = _load_json(STATIC_DIR / "index_cache" / index_cache_file)
    if not data or not isinstance(data, list) or len(data) < 20:
        return None
    data = sorted(data, key=lambda x: str(x.get("trade_date") or x.get("date") or ""))
    closes = [_num(x.get("close")) for x in data if x.get("close") is not None]
    if len(closes) < 20:
        return None
    ma20 = sum(closes[-20:]) / 20
    return {"ok": True, "close": closes[-1], "ma20": ma20,
            "above": closes[-1] >= ma20, "days": len(closes)}


def _history_set(history, date, period="RPS5", threshold=90):
    """历史某日 RPS≥threshold 的板块名集合(过滤噪声板块)"""
    if not history or date not in history:
        return set()
    return {ind for ind, v in history[date].items()
            if not _is_noise_sector(ind)
            and v.get(period) is not None and _num(v.get(period)) >= threshold}


def _percentile(counts, today_count, window=None):
    """今日高分数量在近 window 个交易日中的历史分位(小于今日的天数占比 * 100)"""
    window = window or CONFIG_REVIEW["hist_percentile_days"]
    dates = sorted(counts.keys())[-window:]
    if len(dates) < 2:
        return None
    vals = [counts[d] for d in dates]
    less = sum(1 for v in vals if v < today_count)
    return round(less / len(vals) * 100, 1)


# ========================== 方向1: 市场整体强弱定调 ==========================

def market_tone(rps_data, history, threshold=90):
    """市场整体强弱定调(变化量 + 绝对水位双约束, 区分全面强/结构性强)
    → {tone, high5, high10, high20, diff5, high_score_count_pct, index, note}
    """
    c5 = high_rps_counts(history, "RPS5", threshold)
    c10 = high_rps_counts(history, "RPS10", threshold)
    c20 = high_rps_counts(history, "RPS20", threshold)
    dates = sorted(c5.keys())
    high5 = c5[dates[-1]] if dates else 0
    high10 = c10[dates[-1]] if dates else 0
    high20 = c20[dates[-1]] if dates else 0
    diff5 = high5 - c5[dates[-2]] if len(dates) >= 2 else 0
    total = high5 + high10 + high20
    idx = index_ma20_status()
    avg_high = total / 3.0 if total else 0
    # 双约束判定(变化量 + 绝对水位)
    if diff5 >= CONFIG_REVIEW["strong_min_diff"] and high5 >= CONFIG_REVIEW["strong_min_high"] \
            and (idx is None or idx["above"]):
        tone = "全面强"
    elif high5 >= CONFIG_REVIEW["structural_high"]:
        tone = "结构性强"
    elif diff5 <= CONFIG_REVIEW["weak_min_diff"] and high5 <= CONFIG_REVIEW["weak_max_high"]:
        tone = "弱"
    elif total <= CONFIG_REVIEW["weak_total_max"]:
        tone = "弱"
    elif diff5 > 0 and high5 >= 15:
        tone = "中性偏强"
    elif diff5 < 0 or high5 <= CONFIG_REVIEW["weak_max_high"]:
        tone = "中性偏弱"
    else:
        tone = "中性"
    pct = _percentile(c5, high5)
    note = []
    if diff5 > 0:
        note.append("高分板块数量增加, 市场强度提升")
    elif diff5 < 0:
        note.append("高分板块数量减少, 市场强度回落")
    if pct is not None:
        note.append("高分板块数处于近%d日历史%.0f%%分位" % (CONFIG_REVIEW["hist_percentile_days"], pct))
    if idx is not None:
        note.append("上证指数%s20日线(%.0f点)" % ("站上" if idx["above"] else "跌破", idx["ma20"]))
    if tone == "结构性强":
        note.append("沪指虽弱, 但板块赚钱效应集中, 属结构性行情")
    return {"tone": tone, "high5": high5, "high10": high10, "high20": high20,
            "diff5": diff5, "avg_high": avg_high,
            "high_score_count_pct": pct, "index": idx,
            "note": "；".join(note) if note else ""}


# ========================== 方向2: 板块梯队与主线确认 ==========================

def _prev_date(history):
    """history 中最后一个交易日的前一个交易日"""
    dates = sorted(history.keys())
    if len(dates) < 2:
        return None
    return dates[-2]


def resonance_analysis(rps5, rps10, rps20, history=None):
    """三周期共振法(硬核/韧性主线分级)
    → {core(硬核), tough(韧性), pulse, diverge, mainline_count, mainlines}
    硬核主线: RPS5∩RPS10∩RPS20 三档同时上榜
    韧性主线: RPS10∩RPS20 在榜, RPS5 仅掉 1 个交易日(允许短期分歧, 不直接踢出主线)
    主线分歧: RPS10/20 在榜但 RPS5 不在
    """
    n5, in5 = _in_sectors(rps5)
    n10, in10 = _in_sectors(rps10)
    n20, _ = _in_sectors(rps20)
    core = [s for s in in5 if s["name"] in n10 and s["name"] in n20]          # 硬核: 三档共振
    # 韧性主线: 昨日 RPS5 在榜(掉1天), 今天 RPS10/20 仍在榜
    prev_rps5 = _history_set(history, _prev_date(history), "RPS5") if history else set()
    core_names = {c["name"] for c in core}
    tough = [s for s in in10 if s["name"] in n20 and s["name"] not in n5
             and s["name"] in prev_rps5 and s["name"] not in core_names]
    pulse = [s for s in in5 if s["name"] not in n10 and s["name"] not in n20]  # 仅RPS5
    diverge_names = (set(n10) | set(n20)) - set(n5)
    mainlines = core + tough
    return {"core": core, "tough": tough,
            "pulse": pulse,
            "diverge": [{"name": nm} for nm in sorted(diverge_names)],
            "mainline_count": len(mainlines),
            "mainlines": mainlines}


def tier_analysis(in_list):
    """梯队分层(附带最低内部达标率过滤, 剔除小体量脉冲强势板块)
    Top10 / 11~30 / 其他, 不满足达标率的板块不进梯队
    """
    tier1, tier2, tier3 = [], [], []
    for s in in_list:
        r = _num(s.get("rank"), 999)
        ratio = strength_ratio(s)
        if r <= CONFIG_REVIEW["tier1_top"]:
            if ratio >= CONFIG_REVIEW["tier1_min_ratio"]:
                tier1.append(s)
        elif r <= CONFIG_REVIEW["tier2_top"]:
            if ratio >= CONFIG_REVIEW["tier2_min_ratio"]:
                tier2.append(s)
        else:
            tier3.append(s)
    return {"tier1": tier1, "tier2": tier2, "tier3": tier3}


# ========================== 方向3: 板块轮动动向解读 ==========================

def rotation_analysis(rps5, rps10, rps20, history=None):
    """调入/调出/排名跃升/反复轮动
    → {new_in, rank_jump, out_all, out_count, exits, repeat}
    rank_jump: 仅保留 rank_change≥8 的大幅跃升, 输出名次变化+最终排名
    exits: 调出分类 pulse_exit(上榜≤2天)/mainline_exit(上榜≥5天)/normal_exit
    repeat: 近10日反复调入调出板块(高轮动低持续性)
    """
    _, in5 = _in_sectors(rps5)
    _, in10 = _in_sectors(rps10)
    _, in20 = _in_sectors(rps20)
    new_in = [s for s in in5 if _num(s.get("continuous")) <= 1]      # 今日新上榜(连续=1)
    jumps = sorted([s for s in in5 if _num(s.get("rank_change")) >= CONFIG_REVIEW["jump_min_gain"]],
                   key=lambda x: -_num(x.get("rank_change")))[:CONFIG_REVIEW["jump_top"]]
    # 调出汇总(三档去重) + 调出分类
    out_all, exits = [], []
    seen = set()
    for level in (rps5, rps10, rps20):
        for s in level.get("out_list", []):
            name = s.get("name")
            if name in seen:
                continue
            seen.add(name)
            out_all.append(name)
            # 调出前连续在榜天数: 优先从 history 计算(out_list 的 continuous 恒为0)
            cont = _consecutive_in_days(history, name) if history else _num(s.get("continuous"))
            if cont <= CONFIG_REVIEW["pulse_continuous"]:
                etype = "pulse_exit"
            elif cont >= CONFIG_REVIEW["mainline_continuous"]:
                etype = "mainline_exit"
            else:
                etype = "normal_exit"
            exits.append({"name": name, "continuous": int(cont), "exit_type": etype})
    # 近10日反复调入调出(从历史 RPS5 在榜状态翻转次数判断)
    repeat = _repeat_rotation(history) if history else []
    return {"new_in": new_in, "rank_jump": jumps, "out_all": out_all,
            "out_count": len(out_all), "exits": exits, "repeat": repeat}


def _consecutive_in_days(history, name, threshold=90):
    """板块调出前连续在榜天数(被调出板块今天已不在榜, 从最新一个交易日往前数)"""
    if not history:
        return 0
    dates = sorted(history.keys())
    days = 0
    for d in reversed(dates[:-1]):  # 排除今天(今天已被调出不在榜)
        v = history[d].get(name, {})
        if _num(v.get("RPS5")) < threshold:
            break
        days += 1
    return days


def _repeat_rotation(history, window=None, flips=None):
    """近 window 个交易日, RPS5 在榜状态翻转≥flips 次的板块(反复轮动低持续性)"""
    window = window or CONFIG_REVIEW["repeat_window"]
    flips = flips or CONFIG_REVIEW["repeat_flips"]
    dates = sorted(history.keys())[-window:]
    if len(dates) < window:
        return []
    names = set()
    for d in dates:
        names.update(history[d].keys())
    result = []
    for nm in names:
        series = []
        for d in dates:
            v = history[d].get(nm, {})
            series.append(_num(v.get("RPS5")) >= CONFIG_REVIEW["high_rps_threshold"])
        cnt = 0
        for i in range(1, len(series)):
            if series[i] != series[i - 1]:
                cnt += 1
        if cnt >= flips:
            result.append(nm)
    return sorted(result)


# ========================== 方向4: 题材/板块健康度验证 ==========================

def health_analysis(in_list, history=None):
    """扩散度(规模自适应) + 持续性 + 疲劳 + 种子质量分级
    → {expand, mature, mature_fatigue, seed_high, seed_pulse}
    """
    expand, mature, fatigue, seed_high, seed_pulse = [], [], [], [], []
    for s in in_list:
        ratio = strength_ratio(s)
        thr = expand_threshold(s.get("stock_count"))
        cont = _num(s.get("continuous"))
        if ratio >= thr:
            expand.append({"name": s["name"], "ratio": round(ratio * 100, 1)})
        if cont >= CONFIG_REVIEW["mature_continuous"] and _num(s.get("rank")) <= 20:
            mature.append(s["name"])
        # 成熟疲劳: 连续上榜≥8天 且 今日达标率跌破规模阈值
        if cont >= CONFIG_REVIEW["mature_fatigue_days"] and ratio < thr:
            fatigue.append({"name": s["name"], "ratio": round(ratio * 100, 1)})
        if cont <= 1:
            item = {"name": s["name"], "ratio": round(ratio * 100, 1)}
            if ratio >= thr:
                seed_high.append(item)
            else:
                seed_pulse.append(item)
    return {"expand": expand, "mature": mature,
            "mature_fatigue": fatigue, "seed_high": seed_high,
            "seed_pulse": seed_pulse}


# ========================== 方向5: 市场风险与退潮预警 ==========================

def _two_day_drop_names(rps5, history):
    """两日掉榜板块: 今日 out_list + 昨日在榜今日不在榜(去重)"""
    names = [s.get("name") for s in rps5.get("out_list", [])]
    if history:
        dates = sorted(history.keys())
        if len(dates) >= 2:
            prev, today = dates[-2], dates[-1]
            prev_set = _history_set(history, prev, "RPS5")
            today_set = _history_set(history, today, "RPS5")
            for nm in prev_set - today_set:
                if nm not in names:
                    names.append(nm)
    return names


def risk_analysis(rps5, rps10, rps20, history):
    """风险预警(分级 hint/warning/danger)
    → {batch_out, diverge, momentum_decay, warnings:[{text,level}], level, dropped_mainlines}
    """
    _, in5 = _in_sectors(rps5)
    warnings = []
    # 1) 批量调出(连续两日同风格累计≥N → warning; 仅单日 → hint)
    today_out_names = [s.get("name") for s in rps5.get("out_list", [])]
    today_style, prev_style = {}, {}
    for nm in today_out_names:
        for st in _style_of(nm):
            today_style[st] = today_style.get(st, 0) + 1
    drop_names = _two_day_drop_names(rps5, history)
    prev_drop_names = [nm for nm in drop_names if nm not in today_out_names]
    for nm in prev_drop_names:
        for st in _style_of(nm):
            prev_style[st] = prev_style.get(st, 0) + 1
    batch_out = {}
    for st in set(today_style) | set(prev_style):
        total = today_style.get(st, 0) + prev_style.get(st, 0)
        if total >= CONFIG_REVIEW["batch_style_n"]:
            batch_out[st] = total
            if today_style.get(st, 0) > 0 and prev_style.get(st, 0) > 0:
                text = "批量调出预警: 连续两日「%s」方向累计调出%d个板块, 该方向短期回避" % (st, total)
                warnings.append({"text": text, "level": "warning"})
            else:
                text = "批量调出异动: 今日「%s」方向调出%d个板块, 单日异动, 观察次日确认" % (st, total)
                warnings.append({"text": text, "level": "hint"})
    # 2) 高位顶背离(排除成分股极少的板块)
    diverge = []
    for s in in5:
        if _num(s.get("rank")) <= 10 and _num(s.get("stock_count")) >= CONFIG_REVIEW["diverge_min_count"] \
                and strength_ratio(s) < CONFIG_REVIEW["diverge_ratio"]:
            diverge.append({"name": s["name"], "ratio": round(strength_ratio(s) * 100, 1)})
    if diverge:
        warnings.append({"text": "高位顶背离: %s 排名靠前但内部个股达标率低(%.0f%%), 指数虚高警惕回落"
                                % (diverge[0]["name"], diverge[0]["ratio"]), "level": "warning"})
    # 3) 动量衰减(连续3日递减 且 当前高分>15, 低位回落不重复告警)
    c5 = high_rps_counts(history, "RPS5")
    dates = sorted(c5.keys())
    momentum_decay = False
    if len(dates) >= CONFIG_REVIEW["decay_days"]:
        recent = [c5[d] for d in dates[-CONFIG_REVIEW["decay_days"]:]]
        if recent == sorted(recent, reverse=True) and recent[0] > recent[-1] \
                and recent[0] > CONFIG_REVIEW["decay_high_floor"]:
            momentum_decay = True
            warnings.append({"text": "动量衰减预警: 高分板块数量连续%d天减少(%s→%s), 市场进入退潮期, 降低仓位"
                                    % (CONFIG_REVIEW["decay_days"] - 1, recent[0], recent[-1]),
                             "level": "warning"})
    # 风险等级汇总: 2+ warning=danger / 1 warning=warning / 仅 hint=hint / 无信号=safe
    levels = [w["level"] for w in warnings]
    if levels.count("warning") + levels.count("danger") >= 2:
        level = "danger"
    elif "warning" in levels or "danger" in levels:
        level = "warning"
    elif levels:
        level = "hint"
    else:
        level = "safe"
    return {"batch_out": batch_out, "diverge": diverge,
            "momentum_decay": momentum_decay, "warnings": warnings,
            "level": level}


# ========================== 方向6: 次日跟踪清单 ==========================

def _rps5_drop_days(history, name, threshold=90):
    """板块 RPS5 连续掉榜天数(今天起往前数, 今天也算)"""
    if not history:
        return 0
    dates = sorted(history.keys())
    days = 0
    for d in reversed(dates):
        v = history[d].get(name, {})
        if _num(v.get("RPS5")) >= threshold:
            break
        days += 1
    return days


def watchlist(rps5, rps10, rps20, history, threshold=90):
    """次日清单(分池 + 掉榜天数 + 达标率过滤)
    → {seed_high, seed_pulse, critical, diverge_watch, diverge_dropped}
    """
    _, in5 = _in_sectors(rps5)
    seed_high, seed_pulse = [], []
    for s in in5:
        if _num(s.get("continuous")) > 1:
            continue
        ratio = strength_ratio(s)
        item = {"name": s["name"], "ratio": round(ratio * 100, 1)}
        if ratio >= expand_threshold(s.get("stock_count")):
            seed_high.append(item)
        else:
            seed_pulse.append(item)
    # 临界晋级池: RPS 接近阈值 + 排名连续2日上升 + 达标率过滤(从 rps_data 找达标率)
    critical = []
    if history:
        dates = sorted(history.keys())
        today, prev = dates[-1], dates[-2]
        in_by_name = {s["name"]: s for s in in5}
        for ind, v in history[today].items():
            r = _num(v.get("RPS5"))
            if CONFIG_REVIEW["critical_low"] <= r < threshold:
                pv = history[prev].get(ind, {})
                if _num(pv.get("rank5")) and _num(v.get("rank5")) < _num(pv.get("rank5")):
                    sector = in_by_name.get(ind, {})
                    ratio = strength_ratio(sector)
                    if ratio >= expand_threshold(sector.get("stock_count")):
                        critical.append({"name": ind, "rps5": round(r, 1)})
        critical.sort(key=lambda x: -x["rps5"])
    # 主线分歧池: RPS20 在榜、RPS5 掉榜; 标记掉榜天数+性质, ≥3 天移出跟踪池
    n5 = {s["name"] for s in in5}
    n20 = {s["name"] for s in rps20.get("in_list", [])}
    diverge_watch, dropped = [], []
    for nm in sorted(n20 - n5):
        days = _rps5_drop_days(history, nm, threshold)
        if days >= 3:
            dropped.append({"name": nm, "drop_days": days,
                            "diverge_note": "逐步衰竭(掉≥%d天)" % days})
        else:
            diverge_watch.append({"name": nm, "drop_days": days,
                                  "diverge_note": "良性分歧(掉%d天)" % days})
    return {"seed_high": seed_high, "seed_pulse": seed_pulse,
            "critical": critical, "diverge_watch": diverge_watch,
            "diverge_dropped": dropped,
            "diverge_note": "主线分歧池: 掉1~2天=良性分歧(低吸观察), 掉≥3天=逐步衰竭(移出跟踪)"}


# ========================== 早期启动挖掘: 潜力板块 + 板块生命周期 ==========================
# (新增字段, 只追加不改动原有字段; 供 recommend.py / recommend_weekly.py 的
#  EARLY_STAGE_START 分支构建"刚启动"候选池)

def potential_sectors(history, potential_stats=None, threshold=None):
    """临界潜力板块(给早期启动选股): RPS5∈[critical_low, 90) + 板块内部达标率合格
    → [{name, rps5, ratio, rank5, rank_change, continuous, stock_count}]
    候选池前半部分数据源: RPS 接近阈值但未入选高分板块 = "刚要启动"的潜力板块
    potential_stats: rps_calc 提供的临界板块成分股统计 (非入选板块无 stocks 数据,
                     达标率只能由 rps_calc 从 daily_map 计算后传入; 缺失则该板块无法
                     确认达标率 → 不入选, 避免误报)
    """
    threshold = threshold or CONFIG_REVIEW["high_rps_threshold"]
    lo = CONFIG_REVIEW["critical_low"]
    if not history:
        return []
    dates = sorted(history.keys())
    today = dates[-1]
    stats_by_name = {}
    for x in potential_stats or []:
        if isinstance(x, dict) and x.get("name"):
            stats_by_name[str(x["name"])] = x
    out = []
    for ind, v in history[today].items():
        if _is_noise_sector(ind):
            continue
        r = _num(v.get("RPS5"))
        if not (lo <= r < threshold):
            continue
        st = stats_by_name.get(str(ind), {})
        total = _num(st.get("stock_count"))
        above = _num(st.get("rps50_above_90"))
        if total <= 0:
            continue  # 无成分股统计(旧版复盘数据), 无法确认达标率 → 不入选
        ratio = above / total
        if ratio < expand_threshold(total):
            continue  # 板块内部达标率不合格(未形成板块效应)
        out.append({
            "name": ind,
            "rps5": round(r, 1),
            "ratio": round(ratio * 100, 1),
            "rank5": round(_num(st.get("rank5")), 1),
            "rank_change": int(_num(st.get("rank_change"))),
            "continuous": int(_num(st.get("continuous"))),
            "stock_count": int(total),
        })
    out.sort(key=lambda x: -x["rps5"])
    return out


def sector_life_cycle(level):
    """板块生命周期标记: {板块名: base/growth/accelerate/fatigue}
    输入为某档位结构(如 rps5), 取 in_list 板块逐条判定:
      fatigue    连续上榜≥8天 且 达标率跌破规模阈值 (成熟疲劳, 行情尾声)
      accelerate 连续上榜≥5天 且 (RPS5≥95 或 排名≤10) (加速高潮, 早期模式排除)
      growth     连续上榜≥3天 (成长期)
      base       其余 (起步/蓄势, 早期模式重点)
    """
    lc = CONFIG_REVIEW["lifecycle"]
    _, in5 = _in_sectors(level)
    result = {}
    for s in in5:
        name = str(s.get("name") or "")
        if not name:
            continue
        cont = _num(s.get("continuous"))
        ratio = strength_ratio(s)
        thr = expand_threshold(s.get("stock_count"))
        rps = _num(s.get("RPS5", s.get("RPS10", s.get("RPS20"))))
        rank = _num(s.get("rank"))
        if cont >= lc["fatigue_min_days"] and ratio < thr:
            result[name] = "fatigue"
        elif cont >= lc["accelerate_min_days"] and (rps >= lc["accelerate_rps5"] or rank <= lc["accelerate_rank"]):
            result[name] = "accelerate"
        elif cont >= lc["growth_min_days"]:
            result[name] = "growth"
        else:
            result[name] = "base"
    return result


# ========================== 进阶: 情绪周期 + 风格归因 + 一致性 ==========================

def sentiment_phase(rps5, rps10, rps20, history, risk_info=None, seed_high_n=0):
    """情绪周期: 主标签 + 附加信号(允许复合状态, 不再强制单标签)
    主标签: 冰点/高潮/发酵/启动/震荡
    附加信号: 退潮信号(高潮后资金出逃等复合场景)
    """
    _, in5 = _in_sectors(rps5)
    seed_n = sum(1 for s in in5 if _num(s.get("continuous")) <= 1)
    out_n = len(rps5.get("out_list", []))
    core_n = len(resonance_analysis(rps5, rps10, rps20, history)["core"])
    c5 = high_rps_counts(history, "RPS5")
    dates = sorted(c5.keys())
    high_n = c5[dates[-1]] if dates else 0
    # 主标签判定
    if high_n <= CONFIG_REVIEW["high_ice"]:
        phase = "冰点期"
    elif high_n >= CONFIG_REVIEW["high_burst"]:
        phase = "高潮期"
    elif core_n >= 3 and seed_high_n >= CONFIG_REVIEW["seed_phase"]:
        phase = "发酵期"
    elif seed_high_n >= CONFIG_REVIEW["seed_phase"]:
        phase = "启动期"
    else:
        phase = "震荡期"
    # 附加信号(可叠加)
    signals = []
    two_day_drop = _two_day_drop_names(rps5, history)
    style_cnt = {}
    for nm in two_day_drop:
        for st in _style_of(nm):
            style_cnt[st] = style_cnt.get(st, 0) + 1
    batch_signal = any(c >= CONFIG_REVIEW["batch_style_n"] for c in style_cnt.values())
    decay_ok = (risk_info or {}).get("momentum_decay")
    if (batch_signal and core_n <= 3) or decay_ok:
        signals.append("退潮信号")
    return {"main": phase, "signal": signals, "signals": signals, "phase": phase,
            "seed_n": seed_n, "out_n": out_n, "core_n": core_n, "high_n": high_n,
            "seed_high_n": seed_high_n}


def style_analysis(rps5, rps10, rps20):
    """风格归因: 多标签匹配, 输出各风格命中板块数 + 占比"""
    def _style_stats(level):
        counts = {}
        for s in level.get("in_list", []):
            for st in _style_of(s["name"]):
                counts[st] = counts.get(st, 0) + 1
        total = len(level.get("in_list", [])) or 1
        return {st: {"count": c, "pct": round(c / total * 100, 1)}
                for st, c in sorted(counts.items(), key=lambda x: -x[1])}
    return {"rps5": _style_stats(rps5), "rps10": _style_stats(rps10),
            "rps20": _style_stats(rps20)}


def consistency_check(tone, market_status_rec):
    """RPS 推导环境 vs 推荐系统大盘结论 一致性校验
    → {conflict, warning}
    """
    if not isinstance(market_status_rec, dict):
        return {"conflict": False, "warning": ""}
    rps_strong = tone in ("全面强", "结构性强", "中性偏强")
    rps_weak = tone in ("弱", "中性偏弱")
    is_safe = market_status_rec.get("is_safe")
    conflict = False
    warning = ""
    if is_safe is False and rps_strong:
        conflict = True
        warning = "一致性校验: RPS 推导环境偏强(%s), 但推荐系统判定大盘不合格, 结论冲突, 注意甄别" % tone
    elif is_safe is True and rps_weak:
        conflict = True
        warning = "一致性校验: RPS 推导环境偏弱(%s), 但推荐系统判定大盘合格, 结论冲突, 注意甄别" % tone
    return {"conflict": conflict, "warning": warning}


# ========================== 汇总 + 渲染 ==========================

def build_review_stats(rps_data, history=None, potential_stats=None):
    """汇总全部复盘统计 → dict
    potential_stats: rps_calc 提供的临界潜力板块成分股统计 (早期启动挖掘用,
                     缺失时 potential_sectors 无法确认达标率 → 输出空列表, 不影响原有字段)
    """
    if not rps_data:
        raise ValueError("rps_data 为空")
    r5, r10, r20 = rps_data.get("rps5", {}), rps_data.get("rps10", {}), rps_data.get("rps20", {})
    _, in5 = _in_sectors(r5)
    mkt = market_tone(rps_data, history)
    res = resonance_analysis(r5, r10, r20, history)
    heal = health_analysis(in5, history)
    risk = risk_analysis(r5, r10, r20, history)
    watch = watchlist(r5, r10, r20, history)
    sent = sentiment_phase(r5, r10, r20, history, risk,
                           seed_high_n=len(heal["seed_high"]))
    rec = load_recommendations() or {}
    ms_rec = rec.get("market_status", "")
    stats = {
        "update_date": rps_data.get("update_date", ""),
        "prev_date": rps_data.get("prev_date", ""),
        "total_industries": rps_data.get("total_industries", 0),
        "threshold": rps_data.get("threshold", 90),
        "market": mkt,
        "resonance": _slim_sections(res),
        "tier": _slim_tiers(tier_analysis(in5)),
        "rotation": _slim_sections(rotation_analysis(r5, r10, r20, history)),
        "health": heal,
        "risk": risk,
        "watch": watch,
        "sentiment": sent,
        "style": style_analysis(r5, r10, r20),
        "consistency": consistency_check(mkt["tone"], ms_rec),
        "market_status_rec": ms_rec,
        # 早期启动挖掘: 只追加新字段, 原有字段/md 文档完全不变 (向下兼容)
        "potential_sectors": potential_sectors(history, potential_stats),
        "sector_life_cycle": {"rps5": sector_life_cycle(r5),
                              "rps10": sector_life_cycle(r10),
                              "rps20": sector_life_cycle(r20)},
    }
    return stats


def _slim_sections(d):
    """dict 内所有 sector 列表 → 精简字段(仅精简板块条目, 保留 exits/repeat 等结构)"""
    out = {}
    for k, v in d.items():
        if isinstance(v, list):
            out[k] = [_slim_sector(x) if isinstance(x, dict) and _is_sector(x) else x for x in v]
        else:
            out[k] = v
    return out


def _is_sector(x):
    """判断是否为板块条目(含 RPS 档位字段或成分股大字段)"""
    return any(key in x for key in ("RPS5", "RPS10", "RPS20", "stocks"))


def _slim_tiers(t):
    return {k: [_slim_sector(x) for x in v] for k, v in t.items()}


def _slim_sector(s):
    """板块条目 → 精简字段(去 stocks 等大字段, 前端足够用)"""
    rps = s.get("RPS5", s.get("RPS10", s.get("RPS20")))
    return {"name": s.get("name", ""),
            "RPS": _num(rps),
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


def render_markdown(stats):
    """复盘统计 → markdown 文字初稿(分段结构)"""
    m = stats["market"]
    res = stats["resonance"]
    tier = stats["tier"]
    rot = stats["rotation"]
    heal = stats["health"]
    risk = stats["risk"]
    watch = stats["watch"]
    sent = stats["sentiment"]
    style = stats["style"]
    cons = stats["consistency"]
    u = stats["update_date"]
    L = []
    L.append("# RPS 看板每日复盘（%s）" % u)
    L.append("")
    # 今日市场总览
    L.append("## 【今日市场总览】")
    L.append("情绪周期: **%s**%s" % (sent["main"],
             (" + " + "、".join(sent["signal"])) if sent.get("signal") else ""))
    L.append("大盘强度: **%s**, 风险等级: **%s**" % (m["tone"], risk.get("level", "safe")))
    L.append("")
    # 情绪周期
    L.append("## 【情绪周期】")
    L.append("主标签: **%s**; 附加信号: %s; 种子%d/调出%d/核心主线%d/高分板块%d"
             % (sent["main"], "、".join(sent["signal"]) if sent.get("signal") else "无",
                sent["seed_n"], sent["out_n"], sent["core_n"], sent["high_n"]))
    L.append("")
    # 大盘强度
    L.append("## 【大盘强度】")
    L.append("今日市场动量**%s**: RPS5≥90 板块 %d 个(较前日 %+d, 近%d日历史%.0f%%分位)、RPS10≥90 %d 个、RPS20≥90 %d 个。%s"
             % (m["tone"], m["high5"], m["diff5"], CONFIG_REVIEW["hist_percentile_days"],
                m.get("high_score_count_pct") or 0, m["high10"], m["high20"], m["note"]))
    if stats.get("market_status_rec"):
        ms_txt = _fmt_market_status(stats["market_status_rec"])
        if ms_txt:
            L.append("推荐系统大盘环境判定: **%s**。" % ms_txt)
    L.append("")
    # 主线梯队
    L.append("## 【主线梯队】")
    if res["core"]:
        L.append("- **硬核主线**(三周期共振 %d 个): %s"
                 % (len(res["core"]), _fmt_sector_list(res["core"])))
    if res["tough"]:
        L.append("- **韧性主线**(RPS5仅掉1天 %d 个): %s"
                 % (len(res["tough"]), _fmt_sector_list(res["tough"])))
    if res["mainline_count"]:
        mainlines = "、".join("%s(连%d天,达标%.0f%%)"
                              % (x["name"], _num(x.get("continuous")), strength_ratio(x) * 100)
                              for x in res["mainlines"][:8])
        L.append("- **主线生命周期**: %s" % mainlines)
    if res["pulse"]:
        L.append("- **短期脉冲**(仅RPS5上榜 %d 个): %s" % (len(res["pulse"]), _fmt_sector_list(res["pulse"])))
    if res["diverge"]:
        L.append("- **主线分歧**(RPS10/20在榜、RPS5回落): %s" % _fmt_sector_list(res["diverge"]))
    if tier["tier1"]:
        L.append("- **第一梯队**(Top%d, 达标率≥%d%%): %s"
                 % (CONFIG_REVIEW["tier1_top"], CONFIG_REVIEW["tier1_min_ratio"] * 100,
                    _fmt_sector_list(tier["tier1"])))
    if tier["tier2"]:
        L.append("- **第二梯队**(11~%d 支线/补涨): %s" % (CONFIG_REVIEW["tier2_top"], _fmt_sector_list(tier["tier2"])))
    L.append("")
    # 轮动观察
    L.append("## 【轮动观察】")
    if rot["new_in"]:
        L.append("- **今日新调入**(%d 个): %s" % (len(rot["new_in"]), _fmt_sector_list(rot["new_in"])))
    if rot["rank_jump"]:
        jumps = "、".join("%s(名次%+d→第%d名)" % (s["name"], int(_num(s.get("rank_change"))), int(_num(s.get("rank"))))
                          for s in rot["rank_jump"])
        L.append("- **排名大幅跃升Top%d**(≥%d名): %s"
                 % (CONFIG_REVIEW["jump_top"], CONFIG_REVIEW["jump_min_gain"], jumps))
    if rot["exits"]:
        for e in rot["exits"]:
            et = {"pulse_exit": "脉冲调出(上榜≤2天)", "mainline_exit": "主线调出(上榜≥5天)",
                  "normal_exit": "普通调出"}.get(e["exit_type"], e["exit_type"])
            L.append("- **%s**: %s(连续%d天)" % (et, e["name"], e["continuous"]))
    if rot["repeat"]:
        L.append("- **反复轮动**(近10日多次调入调出, 持续性差): %s" % _fmt_sector_list(rot["repeat"]))
    if not rot["new_in"] and not rot["rank_jump"] and not rot["exits"] and not rot["repeat"]:
        L.append("- 无明显轮动信号")
    style_top = sorted(style["rps5"].items(), key=lambda x: -x[1]["count"])[:3]
    if style_top:
        L.append("- **风格分布**: %s" % "；".join("%s%d个(%.0f%%)" % (k, v["count"], v["pct"]) for k, v in style_top))
    L.append("")
    # 板块健康扫描
    L.append("## 【板块健康扫描】")
    if heal["seed_high"]:
        L.append("- **高质量种子**(首日入选+扩散达标): %s"
                 % "、".join("%s(%.0f%%)" % (x["name"], x["ratio"]) for x in heal["seed_high"][:8]))
    if heal["seed_pulse"]:
        L.append("- **脉冲种子**(首日入选但扩散不足): %s" % _fmt_sector_list(heal["seed_pulse"]))
    if heal["expand"]:
        L.append("- **扩散健康**(达标率≥规模阈值): %s"
                 % "、".join("%s(%.0f%%)" % (x["name"], x["ratio"]) for x in heal["expand"][:8]))
    if heal["mature"]:
        L.append("- **成熟主线**(连续上榜≥%d天): %s"
                 % (CONFIG_REVIEW["mature_continuous"], _fmt_sector_list(heal["mature"])))
    if heal["mature_fatigue"]:
        L.append("- **成熟疲劳**(连续上榜≥%d天且达标率下滑): %s"
                 % (CONFIG_REVIEW["mature_fatigue_days"],
                    "、".join("%s(%.0f%%)" % (x["name"], x["ratio"]) for x in heal["mature_fatigue"][:6])))
    if not heal["expand"] and not heal["mature"] and not heal["seed_high"]:
        L.append("- 暂无入选板块健康数据")
    L.append("")
    # 风险预警
    L.append("## 【风险预警】(风险等级: %s)" % risk.get("level", "safe"))
    if risk["warnings"]:
        for w in risk["warnings"]:
            L.append("- [%s] %s" % (w.get("level", "hint"), w["text"]))
    else:
        L.append("- 无风险信号: 市场结构健康")
    if watch.get("diverge_dropped"):
        L.append("- **主线衰竭**(RPS5掉榜≥3天, 移出跟踪): %s"
                 % "、".join("%s(掉%d天)" % (x["name"], x["drop_days"]) for x in watch["diverge_dropped"][:8]))
    L.append("")
    # 次日跟踪清单
    L.append("## 【次日跟踪清单】")
    if watch["seed_high"]:
        L.append("- **高质量种子池**(次日连榜则确认强度): %s"
                 % "、".join("%s(%.0f%%)" % (x["name"], x["ratio"]) for x in watch["seed_high"][:8]))
    if watch["seed_pulse"]:
        L.append("- **脉冲种子池**(需扩散确认): %s" % _fmt_sector_list(watch["seed_pulse"]))
    if watch["critical"]:
        crit = "、".join("%s(%.0f)" % (c["name"], c["rps5"]) for c in watch["critical"][:8])
        L.append("- **临界晋级池**(RPS≈阈值且排名连续上升, 达标率达标): %s" % crit)
    if watch["diverge_watch"]:
        L.append("- **主线分歧池**(RPS20在榜、RPS5掉榜, 良性分歧低吸观察): %s"
                 % "、".join("%s(掉%d天)" % (x["name"], x["drop_days"]) for x in watch["diverge_watch"][:8]))
    if not watch["seed_high"] and not watch["seed_pulse"] and not watch["critical"] and not watch["diverge_watch"]:
        L.append("- 暂无跟踪标的")
    L.append("")
    # 风格分布
    L.append("## 【风格分布】")
    for period in ("rps5", "rps10", "rps20"):
        items = style.get(period, {})
        if items:
            txt = "；".join("%s%d个(%.0f%%)" % (k, v["count"], v["pct"]) for k, v in items.items())
            L.append("- %s: %s" % (period.upper(), txt))
        else:
            L.append("- %s: 无" % period.upper())
    L.append("")
    # 矛盾提示: 两套结论都写入, 方便人工比对
    if cons.get("conflict"):
        L.append("## 【矛盾提示】")
        L.append("- %s" % cons["warning"])
        L.append("- RPS 推导市场强度: **%s** (RPS5≥90 板块 %d 个)" % (m["tone"], m["high5"]))
        ms_txt = _fmt_market_status(stats.get("market_status_rec"))
        L.append("- recommendations.json 大盘判定: **%s**" % (ms_txt or "无数据"))
        L.append("")
    L.append("---")
    L.append("*免责声明: 本复盘为 RPS 动量量化复盘, 仅供研究参考, 不构成投资建议。*")
    L.append("")
    L.append("<!-- AUTO_GEN_END 以下为人工备注区, 自动生成不会覆盖 -->")
    return "\n".join(L)


# 人工备注区起始标记: 自动生成内容只覆盖标记以上部分, 标记以下的人工笔记原样保留
AUTO_GEN_MARKER = "<!-- AUTO_GEN_END"


def save_review(stats, md_text):
    """保存 json + md 到 static/
    md 分两块: 自动生成块(标记以上) + 人工备注块(标记以下, 原样保留不覆盖)
    """
    STATIC_DIR.mkdir(exist_ok=True)
    json_path = STATIC_DIR / "daily_review.json"
    md_path = STATIC_DIR / "daily_review.md"
    manual_block = ""
    if md_path.exists():
        try:
            old = md_path.read_text(encoding="utf-8")
            if AUTO_GEN_MARKER in old:
                manual_block = old.split(AUTO_GEN_MARKER, 1)[1]
        except Exception:
            manual_block = ""
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1, allow_nan=False)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_text + manual_block)
    return json_path, md_path


def main(force=False, potential_stats=None):
    """主入口: 读取 rps_data/history 生成复盘, 幂等(同日不重复生成)
    potential_stats: rps_calc 提供的临界潜力板块成分股统计 (早期启动挖掘用)
    """
    rps_data = load_rps_data()
    if not rps_data:
        print("  [x] rps_data.json 缺失, 跳过复盘生成")
        return
    history = load_history()
    if not history:
        print("  [!] rps_history.json 缺失, 部分对比维度将降级")
    # 幂等: 校验输出文件内的日期字段, 避免旧文件导致跳过
    # (升级后旧文件缺 potential_sectors → 强制重新生成, 保证早期启动字段可用)
    if not force:
        exist = _load_json(STATIC_DIR / "daily_review.json")
        if exist and exist.get("update_date") == rps_data.get("update_date") \
                and "potential_sectors" in exist:
            print("  [i] 当日复盘已生成 (%s), 跳过" % rps_data.get("update_date"))
            return
    stats = build_review_stats(rps_data, history, potential_stats)
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
