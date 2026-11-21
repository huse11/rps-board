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
from logutil import get_logger
logger = get_logger(__name__)

import json
import os
from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"

# 情绪周期历史文件: 每日追加一条 (供前端情绪趋势图), 保留最近 N 条
# 注意: 路径在函数内用 STATIC_DIR 动态构造 (便于测试 mock, 避免模块加载时固化)
SENTIMENT_HISTORY_NAME = "review_sentiment_history.json"
SENTIMENT_HISTORY_MAX = 60

# 板块健康达标率历史文件: 每日归档 {date: {name: ratio}} (供次日 health.ratio_chg 环比, 2026-08-30 新增)
HEALTH_RATIOS_NAME = "health_ratios_history.json"

# ========================== 配置 ==========================
CONFIG_REVIEW = {
    "high_rps_threshold": 95,        # RPS≥95 视为高分板块(与看板入选阈值一致)
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
    "decay_high_floor": 8,           # 动量衰减判定: 当前高分>8 才触发 (RPS95下数量恒~10, 低位回落不重复告警)
    "critical_low": 88,              # 临界晋级池: RPS 处于 [88, 阈值] 区间
    "batch_out": 5,                  # 批量调出预警阈值(个)
    "batch_style_n": 3,              # 同风格调出≥3 触发提示
    "risk_v2_out_mid": 5,            # V2.0 中风险: 今日调出≥5 且集中在单一赛道
    "risk_v2_out_high": 8,           # V2.0 高风险: 今日调出≥8 或主线批量调出
    "risk_v2_main_out": 3,           # V2.0 高风险: 主线板块批量调出≥3
    "risk_v2_plunge_ratio": 0.4,     # V2.0 极高风险: 上涨家数<下跌家数40% = 全市场普跌
    "high_burst": 12,                # 情绪高潮: 高分板块 ≥12 (RPS95下数量恒~10, 12=数量饱和+扩散)
    "high_ice": 3,                   # 情绪冰点: 高分板块 ≤3 (RPS95下数量恒~10, 3以下=极度收缩)
    "seed_phase": 4,                 # 情绪启动/发酵: 高质量种子 ≥4
    "jump_top": 5,                   # 排名跃升榜前 N
    "jump_min_gain": 8,              # 排名跃升最小值(过滤小幅名次变动)
    "pulse_continuous": 2,           # 调出分类: 上榜≤2天调出 = 脉冲行情
    "mainline_continuous": 5,        # 调出分类: 上榜≥5天调出 = 老主线终结
    "strong_min_diff": 3,            # 全面强: diff≥+3
    "structural_high": 10,           # 结构性强: 高分≥10(数量维度接近饱和时, 用均涨幅区分)
    "weak_max_high": 6,              # 弱: 高分≤6 (RPS95下数量恒~10, 6以下才算弱)
    "weak_total_max": 15,            # 弱: 三档合计≤15 (数量维度弱阈值)
    # 强度维度: 高分板块平均5日涨幅 (RPS95下数量恒定~10, 涨幅强度成为市场定调主信号)
    "strong_avg_min": 8.0,           # 全面强: 高分板块平均5日涨幅 ≥8% (如 20260819~21 ≈20%)
    "weak_avg_max": 3.0,             # 弱: 高分板块平均5日涨幅 ≤3% (如 20260825 ≈2.6%)
    "neutral_avg_high": 6.0,         # 中性偏强分界: 平均涨幅 ≥6% 偏向走强
    "hist_percentile_days": 120,     # 高分数量历史分位窗口
    "pct_window_30": 30,             # V2.0 市场定调: 近30日分位表述窗口 (需求 3.2)
    "repeat_window": 10,             # 反复轮动观察窗口(交易日)
    "repeat_flips": 3,               # 反复轮动: 近10日在榜状态翻转≥3次
    "style_unclassified_max": 20.0,  # V2.0 进阶观察: 风格未归类板块占比上限% (需求 3.10)
    # 板块生命周期标记 (早期启动挖掘用, 只新增不改动原有字段)
    "lifecycle": {
        "fatigue_min_days": 8,       # 连续上榜≥8天 且 达标率跌破规模阈值 → fatigue (成熟疲劳)
        "accelerate_min_days": 5,    # 连续上榜≥5天 且 满足高位条件 → accelerate (加速高潮, 早期模式排除)
        "accelerate_rps5": 95,       # 加速条件1: RPS5 ≥ 95 (高位加速)
        "accelerate_rank": 10,       # 加速条件2: 排名 ≤ 10 (头部加速)
        "growth_min_days": 3,        # 连续上榜≥3天 → growth (成长期)
    },
}

# 情绪阶段判定规则 (V2.0 需求 3.1: 复用现有主标签逻辑, 补充量化阈值说明, 供前端图例/hover 展示)
SENTIMENT_PHASE_RULES = [
    {"phase": "冰点期", "rule": "高分板块≤5个, 连续3天减少"},
    {"phase": "启动期", "rule": "种子板块≥5个, 高分板块数量回升"},
    {"phase": "发酵期", "rule": "高分板块≥15个, 主线板块连续上榜≥5天"},
    {"phase": "高潮期", "rule": "高分板块≥25个, 全板块普涨"},
    {"phase": "退潮期", "rule": "调出板块≥10个, 高分板块连续2天减少"},
]

# 板块健康分类判定规则 (V2.0 需求 3.5: 供前端图例/hover 展示)
HEALTH_RULES = {
    "高质量种子": "首日入选RPS5 + 扩散达标率≥30%",
    "脉冲种子": "首日入选 + 扩散达标率<30%",
    "扩散健康": "连续上榜≥3天 + 扩散达标率≥20%",
    "成熟主线": "连续上榜≥5天 + 扩散达标率≥40%",
    "成熟疲劳": "连续上榜≥8天 + 达标率回落",
}

# 风格归因关键词(板块名 → 风格, 支持多标签匹配)
# V2.0 (需求 3.10): 扩充关键词覆盖, 目标 未归类比例 ≤20%
STYLE_KEYWORDS = {
    "科技": ["半导体", "软件", "IT", "通信", "电子", "计算机", "互联网", "元器件", "芯片", "传媒", "游戏", "数字经济", "数据",
             "光模块", "算力", "集成电路", "印制电路板", "消费电子", "光学", "出版", "教育", "照明", "面板", "存储",
             "服务器", "AI", "人工智能", "安防", "云", "5G", "操作系统", "网络安全"],
    "消费": ["食品", "饮料", "白酒", "零售", "家电", "汽车", "旅游", "酒店", "服装", "农业", "养殖", "纺织", "家居", "美容",
             "乳制", "肉制", "休闲食品", "调味", "啤酒", "软饮", "厨卫", "家装", "渔业", "种子", "钟表", "珠宝", "首饰",
             "超市", "百货", "种植", "粮食", "饲料", "空调", "厨电", "猪肉", "鸡肉", "酿酒", "农产品加工"],
    "医药": ["医药", "医疗", "生物", "中药", "化学制药", "疫苗", "健康", "中成", "血制", "体外诊断", "动物保健", "制药",
             "原料药", "医美", "创新药"],
    "金融": ["银行", "保险", "证券", "金融", "多元", "期货", "信托", "金融控股"],
    "周期": ["有色", "钢铁", "煤炭", "石油", "化工", "建材", "黄金", "小金属", "稀有", "稀土", "水泥", "化纤", "橡胶",
             "地产", "贵金属", "工业金属", "能源金属", "炼化", "特钢", "玻纤", "基建", "房屋", "铅锌", "煤", "焦炭",
             "非金属", "铜", "铝", "锡", "镍", "钴", "锂", "钼", "锑", "钨", "塑料", "石化", "无机盐", "贸易",
             "造纸", "包装", "玻璃", "陶瓷", "油服", "油运", "复合肥", "化肥", "化学原料"],
    "制造": ["机械", "电气", "仪器", "军工", "航空", "船舶", "专用设备", "通用设备", "自动化", "机器人", "激光",
             "机床", "锻铸", "零部件", "轨交", "航天", "装备", "制冷", "电机", "工控", "轴承", "液压", "模具", "紧固件"],
    "新能源": ["光伏", "锂电", "电池", "储能", "风电", "新能源", "氢能", "电网", "充电桩", "新能源车", "光伏发电",
               "光伏设备", "风电设备"],
    "公用": ["电力", "水务", "环保", "燃气", "交通", "物流", "港口", "航运", "机场", "高速", "铁路", "公路", "快递",
             "电力设备"],
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


def load_early_recommendations():
    return _load_json(STATIC_DIR / "recommendations_early.json")


# 消息面标签白名单(推荐 tags 中含这些标签视为消息面信号, 2026-08-23)
NEWS_TAG_WHITELIST = ("利好消息", "利空消息", "传闻利好", "消息待证",
                      "人气榜TOP", "机构大宗买入", "大宗折价")


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

def high_rps_counts(history, period="RPS5", threshold=95):
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


def high_rps_avg_gain(history, period="RPS5", chg_col="chg5", threshold=95):
    """统计每个交易日高分板块的平均 N 日涨幅 → {date: avg} (按日期升序)
    RPS95 下高分板块数量恒定~10, 数量维度无区分度; 平均涨幅强度才是市场定调主信号"""
    if not history:
        return {}
    out = {}
    for date, sectors in history.items():
        gains = [v.get(chg_col) for ind, v in sectors.items()
                 if not _is_noise_sector(ind) and v.get(period) is not None
                 and _num(v.get(period)) >= threshold and v.get(chg_col) is not None]
        if gains:
            out[date] = round(sum(gains) / len(gains), 2)
    return dict(sorted(out.items()))


def consecutive_trend_days(counts):
    """高分板块数序列 → 连续同向变化天数 (V2.0 需求 3.1 量化阈值用)
    正=连续增加天数, 负=连续减少天数, 0=持平或不足2个样本
    与昨日持平不构成趋势(不参与方向统计), 只统计严格增/减的连续段
    """
    if not counts or len(counts) < 2:
        return 0
    dates = sorted(counts.keys())
    vals = [counts[d] for d in dates]
    # 找最后一个非持平变化的方向
    i = len(vals) - 1
    while i >= 1 and vals[i] == vals[i - 1]:
        i -= 1
    if i < 1:
        return 0  # 全部持平
    direction = 1 if vals[i] > vals[i - 1] else -1
    days = 1
    j = i - 1
    while j >= 1:
        if vals[j] == vals[j - 1]:
            break  # 持平中断趋势
        if (vals[j] > vals[j - 1]) != (direction > 0):
            break  # 方向反转
        days += 1
        j -= 1
    return direction * days


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


def _fetch_market_breadth_from_ak():
    """调用 akshare_adapter 抓取市场广度 (独立函数便于单测 mock)"""
    try:
        from akshare_adapter import fetch_market_breadth_ak
        return fetch_market_breadth_ak()
    except Exception:
        return None


def fetch_market_breadth():
    """全市场广度数据 (每日复盘 V2.0 新增, 需求 3.2 市场广度卡片)
    主源: akshare_adapter.fetch_market_breadth_ak (腾讯全市场行情 + 东财涨停池)
    降级: 读取 recommendations.json market_status 补齐涨跌停家数 (绝对涨跌家数/成交额缺失置 None)
    返回 {up_count, down_count, limit_up, limit_down, max_board, total_amount, source, note}
    任何失败不阻断复盘主流程
    """
    out = {"up_count": None, "down_count": None, "limit_up": None,
           "limit_down": None, "max_board": None, "total_amount": None,
           "source": None, "note": ""}
    d = _fetch_market_breadth_from_ak()
    if d:
        for k in ("up_count", "down_count", "limit_up", "limit_down",
                  "max_board", "total_amount"):
            out[k] = d.get(k)
        out["source"] = "akshare"
    # 降级: recommendations.json market_status 补齐涨跌停家数
    if out["limit_up"] is None or out["limit_down"] is None:
        rec = load_recommendations() or {}
        ms = rec.get("market_status") or {}
        if isinstance(ms, dict):
            if out["limit_up"] is None and ms.get("limit_up_count") is not None:
                out["limit_up"] = int(_num(ms.get("limit_up_count")))
            if out["limit_down"] is None and ms.get("limit_down_count") is not None:
                out["limit_down"] = int(_num(ms.get("limit_down_count")))
            if out["source"] is None and (ms.get("limit_up_count") is not None
                                          or ms.get("advance_ratio") is not None):
                out["source"] = "recommendations"
    if not out["note"] and out["source"] is None:
        out["note"] = "市场广度数据缺失(抓取与降级均失败)"
    return out


def _history_set(history, date, period="RPS5", threshold=95):
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


def percentile_level(pct):
    """分位 → 水位形容词 (V2.0 需求 3.2: 历史分位表述可读化)
    0%=极低, (0,25]=偏低, (25,75]=中等, (75,100)=偏高, 100=极高
    """
    if pct is None:
        return ""
    if pct <= 0:
        return "极低水位"
    if pct <= 25:
        return "偏低水位"
    if pct <= 75:
        return "中等水位"
    if pct < 100:
        return "偏高水位"
    return "极高水位"


# ========================== 方向1: 市场整体强弱定调 ==========================

def market_tone(rps_data, history, threshold=95):
    """市场整体强弱定调(数量 + 强度双维度, 区分全面强/结构性强/弱)
    RPS95 下高分板块数量恒定~10, 数量维度仅作辅助; 高分板块平均5日涨幅(强度)是主信号
    → {tone, high5, high10, high20, diff5, avg_gain, high_score_count_pct, index, note}
    """
    c5 = high_rps_counts(history, "RPS5", threshold)
    c10 = high_rps_counts(history, "RPS10", threshold)
    c20 = high_rps_counts(history, "RPS20", threshold)
    g5 = high_rps_avg_gain(history, "RPS5", "chg5", threshold)
    dates = sorted(c5.keys())
    high5 = c5[dates[-1]] if dates else 0
    high10 = c10[dates[-1]] if dates else 0
    high20 = c20[dates[-1]] if dates else 0
    diff5 = high5 - c5[dates[-2]] if len(dates) >= 2 else 0
    total = high5 + high10 + high20
    idx = index_ma20_status()
    avg_high = total / 3.0 if total else 0
    avg_gain = g5[dates[-1]] if dates and dates[-1] in g5 else 0  # 高分板块平均5日涨幅
    strong_avg = CONFIG_REVIEW["strong_avg_min"]
    weak_avg = CONFIG_REVIEW["weak_avg_max"]
    neutral_avg = CONFIG_REVIEW["neutral_avg_high"]
    # 强度 + 数量双维度判定 (RPS95 下数量恒定, 强度主导)
    if idx is not None and idx["above"] and avg_gain >= strong_avg \
            and (diff5 >= CONFIG_REVIEW["strong_min_diff"] or high5 >= CONFIG_REVIEW["structural_high"]):
        tone = "全面强"
    elif avg_gain >= strong_avg and high5 >= CONFIG_REVIEW["structural_high"]:
        tone = "结构性强"
    elif avg_gain <= weak_avg or total <= CONFIG_REVIEW["weak_total_max"]:
        tone = "弱"
    elif diff5 >= CONFIG_REVIEW["strong_min_diff"] or (avg_gain >= neutral_avg and high5 >= CONFIG_REVIEW["structural_high"]):
        tone = "中性偏强"
    elif avg_gain <= CONFIG_REVIEW["weak_avg_max"] + 1.0 or high5 <= CONFIG_REVIEW["weak_max_high"]:
        tone = "中性偏弱"
    else:
        tone = "中性"
    pct = _percentile(c5, high5)
    pct30 = _percentile(c5, high5, CONFIG_REVIEW["pct_window_30"])  # V2.0 近30日分位
    pct_level = percentile_level(pct30)
    note = []
    if diff5 > 0:
        note.append("高分板块数量增加")
    elif diff5 < 0:
        note.append("高分板块数量减少")
    if avg_gain:
        note.append("高分板块平均5日涨幅%.1f%%" % avg_gain)
    if idx is not None:
        note.append("沪指%s20日线(%.0f点)" % ("站上" if idx["above"] else "跌破", idx["ma20"]))
    if pct30 is not None:
        note.append("高分板块处近30日%.0f%%分位(%s)" % (pct30, pct_level))
    if tone == "结构性强":
        note.append("结构性行情")
    return {"tone": tone, "high5": high5, "high10": high10, "high20": high20,
            "diff5": diff5, "avg_high": avg_high, "avg_gain": avg_gain,
            "high_score_count_pct": pct,
            "pct30": pct30, "pct_level": pct_level,  # V2.0 近30日分位表述
            "index": idx,
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
    → {new_in, rank_jump, out_all, out_count, exits, repeat, repeat_detail,
       main_out, branch_out, main_out_count, branch_out_count, summary}
    rank_jump: 仅保留 rank_change≥8 的大幅跃升, 输出名次变化+最终排名
    exits: 调出分类 pulse_exit(上榜≤2天)/mainline_exit(上榜≥5天)/normal_exit
    repeat: 近10日反复调入调出板块(高轮动低持续性, 名字列表兼容旧字段)
    V2.0 (需求 3.4):
      repeat_detail  [{name, flips}] 反复轮动次数标注
      main_out      主线板块调出(高危): 调出板块中昨日属三周期共振/主线分歧
      branch_out    支线板块调出(正常): 其余调出(仅短期脉冲类)
      summary       一句话轮动总结(流入方向/流出方向/风格切换或延续)
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
    # V2.0 主线/支线调出拆分: 调出板块昨日属三周期共振(core)或主线分歧(diverge) = 主线调出(高危)
    res = resonance_analysis(rps5, rps10, rps20, history)
    mainline_names = {c["name"] for c in res["core"]} | {d["name"] for d in res["diverge"]}
    main_out = [e for e in exits if e["name"] in mainline_names]
    branch_out = [e for e in exits if e["name"] not in mainline_names]
    # V2.0 一句话轮动总结 + 反复轮动次数
    summary = _rotation_summary(new_in, out_all)
    repeat = _repeat_rotation(history) if history else []
    repeat_detail = _repeat_rotation(history, detail=True) if history else []
    return {"new_in": new_in, "rank_jump": jumps, "out_all": out_all,
            "out_count": len(out_all), "exits": exits, "repeat": repeat,
            "repeat_detail": repeat_detail,
            "main_out": main_out, "branch_out": branch_out,
            "main_out_count": len(main_out), "branch_out_count": len(branch_out),
            "summary": summary}


def _rotation_summary(new_in, out_all):
    """一句话轮动总结 (V2.0 需求 3.4): 资金流入方向/流出方向/风格切换或延续"""
    def _styles(names):
        out = set()
        for nm in names:
            out |= _style_of(nm)
        return out

    def _top_style(names, top=2):
        cnt = {}
        for nm in names:
            for st in _style_of(nm):
                cnt[st] = cnt.get(st, 0) + 1
        if not cnt:
            return ""
        return "/".join(st for st, _ in sorted(cnt.items(), key=lambda x: -x[1])[:top])

    inflow_names = [s.get("name", "") for s in new_in]
    inflow = _top_style(inflow_names)
    outflow = _top_style([n for n in out_all])
    if not inflow and not outflow:
        return "轮动平稳, 无显著资金流向"
    parts = []
    if inflow:
        parts.append("今日资金主要流入%s方向" % inflow)
    if outflow:
        parts.append("从%s方向流出" % outflow)
    # 风格切换/延续: 流入与流出风格几乎无交集 → 切换
    in_st, out_st = _styles(inflow_names), _styles(out_all)
    overlap = len(in_st & out_st)
    switch = bool(in_st and out_st and overlap == 0)
    parts.append("风格整体%s" % ("切换" if switch else "延续"))
    return "；".join(parts)


def _consecutive_in_days(history, name, threshold=95):
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


def _repeat_rotation(history, window=None, flips=None, detail=False):
    """近 window 个交易日, RPS5 在榜状态翻转≥flips 次的板块(反复轮动低持续性)
    detail=True → [{name, flips}] 按翻转次数降序 (V2.0 需求 3.4: 标注反复次数)
    默认返回名字列表(兼容旧字段)
    """
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
            if detail:
                result.append({"name": nm, "flips": cnt})
            else:
                result.append(nm)
    if detail:
        return sorted(result, key=lambda x: -x["flips"])
    return sorted(result)


# ========================== 方向4: 题材/板块健康度验证 ==========================

def health_analysis(in_list, history=None, prev_ratios=None):
    """扩散度(规模自适应) + 持续性 + 疲劳 + 种子质量分级
    → {expand, mature, mature_fatigue, seed_high, seed_pulse, unified}
    V2.0 (需求 3.5):
      unified 合并重复板块: 每个板块仅出现一次, 携带全部分类标签 tags 与
              达标率环比 ratio_chg(较上一交易日, prev_ratios 缺失时为 None)
    """
    expand, mature, fatigue, seed_high, seed_pulse = [], [], [], [], []
    unified = []
    prev_ratios = prev_ratios or {}
    for s in in_list:
        ratio = strength_ratio(s)
        thr = expand_threshold(s.get("stock_count"))
        cont = _num(s.get("continuous"))
        ratio_pct = round(ratio * 100, 1)
        tags = []
        if ratio >= thr:
            expand.append({"name": s["name"], "ratio": ratio_pct})
            tags.append("扩散健康")
        if cont >= CONFIG_REVIEW["mature_continuous"] and _num(s.get("rank")) <= 20:
            mature.append(s["name"])
            tags.append("成熟主线")
        # 成熟疲劳: 连续上榜≥8天 且 今日达标率跌破规模阈值
        if cont >= CONFIG_REVIEW["mature_fatigue_days"] and ratio < thr:
            fatigue.append({"name": s["name"], "ratio": ratio_pct})
            tags.append("成熟疲劳")
        if cont <= 1:
            item = {"name": s["name"], "ratio": ratio_pct}
            if ratio >= thr:
                seed_high.append(item)
                tags.append("高质量种子")
            else:
                seed_pulse.append(item)
                tags.append("脉冲种子")
        if tags:
            prev_ratio = prev_ratios.get(s["name"])
            ratio_chg = round(ratio_pct - prev_ratio, 1) if prev_ratio is not None else None
            unified.append({"name": s["name"], "ratio": ratio_pct,
                            "tags": tags, "ratio_chg": ratio_chg})
    # 按分类优先级排序: 高质量种子 > 扩散健康 > 成熟主线 > 成熟疲劳 > 脉冲种子
    tag_prio = {"高质量种子": 0, "扩散健康": 1, "成熟主线": 2, "成熟疲劳": 3, "脉冲种子": 4}
    unified.sort(key=lambda x: (min(tag_prio.get(t, 9) for t in x["tags"]), x["name"]))
    return {"expand": expand, "mature": mature,
            "mature_fatigue": fatigue, "seed_high": seed_high,
            "seed_pulse": seed_pulse, "unified": unified}


def _load_prev_health_ratios(today_date=None):
    """读取上一交易日板块健康达标率 (V2.0 需求 3.5 环比)
    优先读 health_ratios_history.json 中 update_date < today_date 的最近一天
    (daily_review.json 每日单文件覆盖, 无法自持昨日数据 → 历史归档文件补齐)
    fallback: 旧逻辑读当前 daily_review.json (update_date != today 时)
    → {name: ratio} 或 {}
    """
    hist_file = STATIC_DIR / HEALTH_RATIOS_NAME
    hist = _load_json(hist_file)
    if isinstance(hist, dict) and hist:
        if today_date:
            prev_dates = [d for d in hist.keys() if str(d) < str(today_date)]
        else:
            prev_dates = list(hist.keys())
        if prev_dates:
            last = sorted(prev_dates)[-1]
            ratios = hist.get(last) or {}
            if isinstance(ratios, dict) and ratios:
                return {k: _num(v) for k, v in ratios.items() if v is not None}
    # fallback: 当前 daily_review.json (仅当其为上一交易日)
    data = _load_json(STATIC_DIR / "daily_review.json")
    if not data:
        return {}
    if today_date and data.get("update_date") == today_date:
        return {}
    prev_health = data.get("health") or {}
    ratios = {}
    for key in ("expand", "seed_high", "seed_pulse", "mature_fatigue"):
        for item in prev_health.get(key, []):
            if isinstance(item, dict) and item.get("name"):
                ratios[item["name"]] = _num(item.get("ratio"))
    return ratios


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


def risk_analysis(rps5, rps10, rps20, history, breadth=None):
    """风险预警(分级 hint/warning/danger) + V2.0 四级风险等级
    → {batch_out, diverge, momentum_decay, warnings:[{text,level}], level, dropped_mainlines,
       level_v2, level_v2_text, level_color, track_groups, advice, out_total, main_out_total}
    V2.0 (需求 3.6):
      level_v2  低/中/高/极高风险 (low/mid/high/critical)
        低(绿): 正常轮动, 无批量调出
        中(黄): 调出≥5 且集中在单一赛道
        高(橙): 调出≥8 或主线板块批量调出(≥3)
        极高(红): 高分板块连续3天减少 或 全市场普跌
      track_groups 批量调出按赛道归类 [{track, count}]
      advice       对应操作建议
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
                text = "批量调出预警: 「%s」两日调出%d个, 短期回避" % (st, total)
                warnings.append({"text": text, "level": "warning"})
            else:
                text = "批量调出异动: 「%s」今日调出%d个" % (st, total)
                warnings.append({"text": text, "level": "hint"})
    # 2) 高位顶背离(排除成分股极少的板块)
    diverge = []
    for s in in5:
        if _num(s.get("rank")) <= 10 and _num(s.get("stock_count")) >= CONFIG_REVIEW["diverge_min_count"] \
                and strength_ratio(s) < CONFIG_REVIEW["diverge_ratio"]:
            diverge.append({"name": s["name"], "ratio": round(strength_ratio(s) * 100, 1)})
    if diverge:
        warnings.append({"text": "高位顶背离: %s 达标率低(%.0f%%), 警惕回落"
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
            warnings.append({"text": "动量衰减: 高分板块连续%d天减少(%s→%s)"
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
    # ===== V2.0 四级风险等级 (需求 3.6) =====
    out_total = len(today_out_names)
    # 主线批量调出数: 调出板块昨日属三周期共振/主线分歧
    res = resonance_analysis(rps5, rps10, rps20, history)
    mainline_names = {c["name"] for c in res["core"]} | {d["name"] for d in res["diverge"]}
    main_out_total = sum(1 for nm in today_out_names if nm in mainline_names)
    # 高分板块连续3天减少 (需求 3.6 极高风险条件)
    drop3 = False
    if len(dates) >= 4:
        recent4 = [c5[d] for d in dates[-4:]]
        drop3 = all(recent4[i] < recent4[i - 1] for i in range(1, 4))
    # 全市场普跌: 上涨家数 < 下跌家数 * ratio
    plunge = False
    mb = breadth or {}
    if mb.get("up_count") is not None and mb.get("down_count"):
        up, down = _num(mb["up_count"]), _num(mb["down_count"])
        plunge = down > 0 and up < down * CONFIG_REVIEW["risk_v2_plunge_ratio"]
    if drop3 or plunge:
        level_v2 = "critical"
    elif out_total >= CONFIG_REVIEW["risk_v2_out_high"] or main_out_total >= CONFIG_REVIEW["risk_v2_main_out"]:
        level_v2 = "high"
    elif out_total >= CONFIG_REVIEW["risk_v2_out_mid"] or batch_out:
        level_v2 = "mid"
    else:
        level_v2 = "low"
    level_v2_map = {"low": ("低风险", "green"), "mid": ("中风险", "yellow"),
                    "high": ("高风险", "orange"), "critical": ("极高风险", "red")}
    level_v2_text, level_color = level_v2_map[level_v2]
    # 批量调出按赛道归类 (格式: XX赛道 批量调出N个板块)
    track_groups = [{"track": st, "count": n}
                    for st, n in sorted(batch_out.items(), key=lambda x: -x[1])]
    # 操作建议 (需求 3.6: 风险落地到操作)
    advice = []
    if momentum_decay:
        advice.append("动量连续衰减 → 建议总仓位降至5成以下, 暂停新开仓")
    if main_out_total >= CONFIG_REVIEW["risk_v2_main_out"]:
        advice.append("主线批量调出 → 回避对应赛道, 不抄底")
    if drop3:
        advice.append("高分板块连续3天减少 → 控制仓位, 等待企稳信号")
    if plunge:
        advice.append("全市场普跌 → 防守为主, 不追涨不抄底")
    if level_v2 == "high" and not advice:
        advice.append("批量调出增多 → 降低仓位, 聚焦核心主线")
    if level_v2 == "mid" and not advice:
        advice.append("赛道集中调出 → 回避该赛道, 观察轮动承接")
    if level_v2 == "low" and not advice:
        advice.append("正常轮动 → 维持既有仓位节奏")
    return {"batch_out": batch_out, "diverge": diverge,
            "momentum_decay": momentum_decay, "warnings": warnings,
            "level": level,
            # V2.0 新增
            "level_v2": level_v2, "level_v2_text": level_v2_text,
            "level_color": level_color, "track_groups": track_groups,
            "advice": advice, "out_total": out_total,
            "main_out_total": main_out_total, "drop3": drop3}


# ========================== 方向6: 次日跟踪清单 ==========================

def _rps5_drop_days(history, name, threshold=95):
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


def watchlist(rps5, rps10, rps20, history, threshold=95):
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
    """临界潜力板块(给早期启动选股): RPS5∈[critical_low, 95) + 板块内部达标率合格
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
    V2.0 (需求 3.1): 输出量化字段(高分板块趋势/连续天数/判定依据) + 阶段判定规则文本,
    供前端双轴图/图例/拐点标注使用; 主标签判定逻辑保持不变(实盘校准), 只追加不修改
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
    # V2.0 量化补充: 高分板块趋势(较前日变化 + 连续同向天数) + 判定依据 (只追加字段)
    prev_high_n = c5[dates[-2]] if len(dates) >= 2 else high_n
    trend = high_n - prev_high_n
    trend_days = consecutive_trend_days(c5)
    rising = trend > 0
    basis = _phase_basis(phase, high_n, trend, trend_days, seed_high_n, core_n, out_n)
    return {"main": phase, "signal": signals, "signals": signals, "phase": phase,
            "seed_n": seed_n, "out_n": out_n, "core_n": core_n, "high_n": high_n,
            "seed_high_n": seed_high_n,
            # V2.0 新增量化字段
            "prev_high_n": prev_high_n, "trend": int(trend),
            "trend_days": int(trend_days), "rising": bool(rising),
            "basis": basis, "phase_rules": SENTIMENT_PHASE_RULES}


def _phase_basis(phase, high_n, trend, trend_days, seed_high_n, core_n, out_n):
    """情绪阶段判定依据一句话 (V2.0: 量化结论可追溯)"""
    trend_txt = "%+d" % trend if trend else "持平"
    return ("%s: 高分板块%d个(%s, 连续%s%d天); 高质量种子%d个; 核心主线%d个; 调出%d个"
            % (phase, high_n, trend_txt,
               "增" if trend_days > 0 else ("减" if trend_days < 0 else "变化"),
               abs(trend_days) if trend_days else 0,
               seed_high_n, core_n, out_n))


def sentiment_milestones(history, continuous_days=5):
    """情绪关键拐点标注 (V2.0 需求 3.1: 主线确认日/首次退潮日/情绪冰点日)
    从 rps_history 重算高分板块(RPS≥95)历史序列:
      mainline_confirm_date  主线确认日: 首个出现板块 RPS5≥95 连续上榜≥continuous_days 的日期
      first_tideout_date     首次退潮日: 首个高分板块数连续2天减少的日期 (历史调出数不可得, 用该信号近似)
      emotion_ice_date       情绪冰点日: 首个高分板块≤5且连续3天减少的日期; 无则取高分数量最小值日期
    返回 {mainline_confirm_date, first_tideout_date, emotion_ice_date, note}
    """
    empty = {"mainline_confirm_date": None, "first_tideout_date": None,
             "emotion_ice_date": None,
             "note": "历史数据缺失, 无法标注情绪拐点"}
    if not history:
        return empty
    threshold = CONFIG_REVIEW["high_rps_threshold"]
    dates = sorted(history.keys())
    counts = {}
    cont = {}  # 板块 → 当前连续上榜天数
    mainline_confirm_date = None
    for d in dates:
        sectors = history[d] or {}
        day_high = []
        for ind, v in sectors.items():
            if _is_noise_sector(ind):
                continue
            if _num(v.get("RPS5")) >= threshold:
                day_high.append(ind)
        counts[d] = len(day_high)
        cur = set(day_high)
        for ind in list(cont.keys()):
            if ind not in cur:
                del cont[ind]
        for ind in cur:
            cont[ind] = cont.get(ind, 0) + 1
            if mainline_confirm_date is None and cont[ind] >= continuous_days:
                mainline_confirm_date = d
    # 首次退潮日: 高分板块连续2天减少 (首个满足日)
    first_tideout_date = None
    for i in range(2, len(dates)):
        if counts[dates[i]] < counts[dates[i - 1]] < counts[dates[i - 2]]:
            first_tideout_date = dates[i]
            break
    # 情绪冰点日: 高分板块≤5 且连续3天减少; 无则取高分数量最小值日期
    emotion_ice_date = None
    for i in range(3, len(dates)):
        if (counts[dates[i]] <= 5
                and counts[dates[i]] < counts[dates[i - 1]]
                < counts[dates[i - 2]] < counts[dates[i - 3]]):
            emotion_ice_date = dates[i]
            break
    if emotion_ice_date is None and counts:
        emotion_ice_date = min(counts, key=counts.get)
    return {
        "mainline_confirm_date": mainline_confirm_date,
        "first_tideout_date": first_tideout_date,
        "emotion_ice_date": emotion_ice_date,
        "note": "主线确认日=板块连续上榜≥%d天; 首次退潮日=高分板块连续2天减少; 情绪冰点日=高分≤5且连续3天减少"
                % continuous_days,
    }


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


def style_outlook(rps5, rps10, rps20, sentiment, risk, resonance, style=None):
    """进阶观察 V2.0 (需求 3.10): 风格标签完善 + 风格切换判断 + 明日核心策略要点
    → {unclassified: {count,total,pct,names}, ratio_ok, switch, switch_desc,
       tomorrow_points: [str], note}
    风格切换: RPS5 主导风格 vs RPS10/RPS20 主导风格不一致 → 短周期风格切换
    """
    if style is None:
        style = style_analysis(rps5, rps10, rps20)
    # 1) 风格标签完善: 未归类板块统计 (目标 未归类比例 ≤20%)
    names = [s.get("name") for s in rps5.get("in_list", []) if s.get("name")]
    unc = [n for n in names if _style_of(n) == {"未归类"}]
    total = len(names)
    pct = round(len(unc) / total * 100, 1) if total else 0.0
    ratio_ok = pct <= CONFIG_REVIEW.get("style_unclassified_max", 20.0)
    # 2) 风格切换判断: 短周期(RPS5) vs 中周期(RPS10) 主导风格
    def _top_style(items):
        return max(items.items(), key=lambda x: x[1]["count"])[0] if items else ""
    t5, t10 = _top_style(style["rps5"]), _top_style(style["rps10"])
    switch, switch_desc = False, ""
    if t5 and t10:
        if t5 != t10:
            switch = True
            switch_desc = "短周期风格切换: RPS5主导%s → RPS10主导%s, 短中期风格分化, 明日跟踪新风格持续性" % (t5, t10)
        else:
            switch_desc = "风格延续: RPS5与RPS10同为主导风格%s, 短中期共振" % t5
    elif t5:
        switch_desc = "仅短周期有明确风格: %s" % t5
    # 3) 明日核心策略要点
    phase = sentiment.get("phase") or sentiment.get("main") or "震荡期"
    signals = sentiment.get("signals") or []
    points = []
    if "退潮信号" in signals:
        points.append("退潮信号出现, 明日以防守为主, 观察主线能否止跌企稳")
    if (risk or {}).get("main_out_total"):
        points.append("主线批量调出%d个, 明日回避对应赛道, 等待情绪企稳" % (risk["main_out_total"] or 0))
    core_n = int(resonance.get("mainline_count") or 0)
    if core_n:
        points.append("主线确认%d个, 明日重点跟踪核心板块强度能否延续" % core_n)
    if t5:
        points.append("短线风格聚焦%s, 围绕强势风格内龙头布局, 控制回撤" % t5)
    if phase in ("冰点期", "退潮期"):
        points.append("情绪处于%s, 仓位从严, 不追高不抄底" % phase)
    if not points:
        points.append("市场结构不明, 明日轻仓观望, 等待主线明确")
    return {"unclassified": {"count": len(unc), "total": total, "pct": pct,
                             "names": unc},
            "ratio_ok": ratio_ok,
            "switch": switch, "switch_desc": switch_desc,
            "tomorrow_points": points,
            "note": "进阶观察: 风格标签按板块名关键词归因, 未归类板块占比%.1f%%%s" % (
                pct, " (≥20%需扩充关键词)" if not ratio_ok else "")}


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
        warning = "一致性: RPS偏强(%s) vs 推荐系统不合格, 冲突" % tone
    elif is_safe is True and rps_weak:
        conflict = True
        warning = "一致性: RPS偏弱(%s) vs 推荐系统合格, 冲突" % tone
    return {"conflict": conflict, "warning": warning}


# ========================== 消息面复盘 (2026-08-23 新增) ==========================

def news_review(recs=None, early_recs=None, breadth=None, fund_flow=None, north=None):
    """消息面复盘: 聚合推荐结果中的新闻/人气/大宗信号
    数据源: recommendations.json / recommendations_early.json 的 stocks[].news + score_news + tags
    recs/early_recs 可选传入(便于单测); 缺省自动读取
    真实性甄别已在推荐阶段完成(official=权威采信计分, rumor=传闻仅标签), 这里只做聚合统计:
    V2.0 (需求 3.8): 每条消息标注对应板块 sector + 评分基准 score_basis + 资金面 fund
    返回:
      has_news      推荐中是否含 news 字段(旧版推荐 → False, 前端跳过卡片)
      stock_count   推荐股票总数
      hit_count     命中消息面标签的股票数
      score_sum     消息面对总分的净影响(正=利好推动)
      env           消息面环境: 偏暖/偏冷/中性偏杂 (None=无消息面)
      tags_count    {标签: 命中数}
      official_pos_list  权威利好个股 [{name, count, headline, sector}] (按条数降序)
      official_neg_list  权威利空个股
      rumor_list         传闻个股 [{name, pos, neg, sector}]
      score_basis   评分基准说明(消息面计分规则)
      fund          资金面 {total_amount: 两市成交额(亿), main_net: 主力净额(亿),
                            north_ratio: 北向成分股上涨占比%, north_note: 北向口径说明}
      note          一句话小结
    """
    data_list = []
    if recs is None:
        recs = load_recommendations()
    if early_recs is None:
        early_recs = load_early_recommendations()
    for d in (recs, early_recs):
        if isinstance(d, dict):
            data_list.append(d)
    stocks = []
    for data in data_list:
        stocks.extend(data.get("stocks") or [])
    if not stocks:
        return None
    stock_count = len(stocks)
    has_news = any(isinstance(s.get("news"), dict) for s in stocks)
    if not has_news:
        return None  # 旧版推荐(无消息面字段), 前端不展示消息面卡片
    tags_count = {}
    # 权威利好/利空/传闻个股: 按股票名合并计数 (日度+早期可能同股重复, 2026-08-23)
    pos_map, neg_map, rumor_map = {}, {}, {}
    score_sum = 0
    hit_count = 0
    for s in stocks:
        ns = s.get("news") or {}
        score_sum += _num(s.get("score_news"))
        sector = str(s.get("industry") or "")
        # 标签聚合
        hit_tags = [t for t in str(s.get("tags") or "").split(" + ")
                    if t in NEWS_TAG_WHITELIST]
        if hit_tags:
            hit_count += 1
        for t in hit_tags:
            tags_count[t] = tags_count.get(t, 0) + 1
        # 权威利好/利空个股列表
        op, on = int(_num(ns.get("official_pos"))), int(_num(ns.get("official_neg")))
        rp, rn = int(_num(ns.get("rumor_pos"))), int(_num(ns.get("rumor_neg")))
        name = str(s.get("name") or s.get("ts_code") or "")
        hl = (ns.get("headlines") or [])
        headline = ""
        for h in hl:
            if isinstance(h, dict) and h.get("title"):
                headline = str(h["title"])[:40]
                break
        if op:
            item = pos_map.setdefault(name, {"name": name, "count": 0, "headline": "", "sector": ""})
            item["count"] += op
            item["sector"] = item["sector"] or sector
            if not item["headline"]:
                item["headline"] = headline
        if on:
            item = neg_map.setdefault(name, {"name": name, "count": 0, "headline": "", "sector": ""})
            item["count"] += on
            item["sector"] = item["sector"] or sector
            if not item["headline"]:
                item["headline"] = headline
        if rp or rn:
            item = rumor_map.setdefault(name, {"name": name, "pos": 0, "neg": 0, "sector": ""})
            item["pos"] += rp
            item["neg"] += rn
            item["sector"] = item["sector"] or sector
    pos_list = sorted(pos_map.values(), key=lambda x: -x["count"])
    neg_list = sorted(neg_map.values(), key=lambda x: -x["count"])
    rumor_list = sorted(rumor_map.values(), key=lambda x: -(x["pos"] + x["neg"]))
    # 环境评估: 利空优先(防御性)
    if neg_list:
        env = "偏冷"
    elif pos_list:
        env = "偏暖"
    elif tags_count:
        env = "中性偏杂"
    else:
        env = None
    # V2.0 评分基准说明 (与 recommend.py score_news_funnel 默认值一致: 利好+2/条, 利空-3/条)
    score_basis = "权威利好每条+2分, 权威利空每条-3分, 传闻仅标签不计分; 正分=偏暖, 负分=偏冷"
    # V2.0 资金面 (两市成交额 / 主力资金净额 / 北向广度)
    fund = {}
    if breadth and _num(breadth.get("total_amount")) > 0:
        # fetch_market_breadth 的 total_amount 已是亿元(akshare_adapter 已换算), 直接取用
        fund["total_amount"] = round(_num(breadth["total_amount"]), 1)
    if fund_flow and _num(fund_flow.get("main_net")) != 0:
        fund["main_net"] = round(_num(fund_flow["main_net"]) / 1e8, 1)
        if fund_flow.get("main_pct") is not None:
            fund["main_pct"] = round(_num(fund_flow["main_pct"]), 2)
    if north and _num(north.get("north_ratio")) > 0:
        fund["north_ratio"] = round(_num(north["north_ratio"]) * 100, 1)
        fund["north_note"] = "北向净流入2024-08起停披露, 以成分股上涨占比替代"
    note = ""
    if env:
        parts = []
        if pos_list:
            parts.append("权威利好%d只(%d条)" % (len(pos_list), sum(x["count"] for x in pos_list)))
        if neg_list:
            parts.append("权威利空%d只(%d条)" % (len(neg_list), sum(x["count"] for x in neg_list)))
        if rumor_list:
            parts.append("传闻%d只" % len(rumor_list))
        for t in ("人气榜TOP", "机构大宗买入", "大宗折价"):
            if tags_count.get(t):
                parts.append("%s%d只" % (t, tags_count[t]))
        note = "消息面%s, 推荐股票中%s" % (env, "、".join(parts)) + \
               ("；净影响%+d分" % int(score_sum) if score_sum else "")
    return {"has_news": True, "stock_count": stock_count, "hit_count": hit_count,
            "score_sum": int(score_sum), "env": env, "tags_count": tags_count,
            "official_pos_list": pos_list[:8], "official_neg_list": neg_list[:8],
            "rumor_list": rumor_list[:8], "score_basis": score_basis,
            "fund": fund, "note": note}


def _sector_tag(item):
    """消息个股 → 板块标注标签, 格式: [XX板块] (V2.0 需求 3.8)"""
    s = item.get("sector")
    return " [%s板块]" % s if s else ""


def _fetch_market_fund_flow():
    """大盘主力资金净流入(元) → 最新交易日 {main_net, main_pct} 或 None (失败降级)"""
    try:
        from akshare_adapter import fetch_market_fund_flow_ak
        flow = fetch_market_fund_flow_ak(n_days=1) or {}
    except Exception:
        flow = {}
    if not flow:
        return None
    td = max(flow.keys())
    return flow[td]


def _fetch_north_breadth():
    """北向(沪深股通)当日广度 → {north_up, north_down, north_ratio} 或 {} (失败降级)
    2024-08 起北向净流入停披露, 仅用成分股上涨占比作大盘环境提示"""
    try:
        from akshare_adapter import fetch_hsgt_ak
        return fetch_hsgt_ak() or {}
    except Exception:
        return {}


# ========================== 推荐准确率复盘 ==========================

def rec_accuracy():
    """日度推荐准确率分析: 读取 prediction_review.json + predictions_log.json + recommendations.json
    返回推荐预测的命中率/平均涨幅/分置信度胜率/最佳最差个股
    缺失数据时返回 {"has_data": False, "note": "..."}
    """
    review = _load_json(STATIC_DIR / "prediction_review.json")
    if not review or not review.get("summary"):
        return {"has_data": False, "note": "暂无预测准确率数据(需运行推荐+校准后生成)"}
    s = review.get("summary", {})
    by_conf = review.get("by_confidence") or {}
    recent = review.get("recent") or []
    # 今日推荐概况
    recs = load_recommendations() or {}
    today_stocks = recs.get("stocks") or []
    today_count = len(today_stocks)
    today_probs = [((st.get("prediction") or {}).get("up_prob") or 0) for st in today_stocks]
    today_avg_prob = round(sum(today_probs) / len(today_probs), 1) if today_probs else None
    # 最佳/最差个股 (recent 已是验证过的)
    sorted_hits = sorted(recent, key=lambda x: -x.get("gain", 0))
    best = sorted_hits[:3]
    worst = sorted(recent, key=lambda x: x.get("gain", 0))[:3]
    return {
        "has_data": True,
        "update_date": review.get("update_date", ""),
        "horizon": review.get("horizon", 1),
        "strategy": "日度动量策略",  # V2.0 (需求 3.9) 标题标识, 与超跌潜力策略区分
        "samples": s.get("samples", 0),
        "hit_rate": s.get("hit_rate", 0),
        "avg_gain": s.get("avg_gain", 0),
        # V2.0 (需求 3.9): 持有3日/5日周期收益(可验证样本均值, 缺省 None)
        "avg_gain3": s.get("avg_gain3"),
        "avg_gain5": s.get("avg_gain5"),
        "horizon3_n": s.get("horizon3_n"),
        "horizon5_n": s.get("horizon5_n"),
        "avg_up_prob": s.get("avg_up_prob", 0),
        "prev_dates": s.get("prev_dates") or [],
        "by_confidence": {k: v for k, v in by_conf.items() if v and v.get("samples")},
        "best": [{"name": x.get("name"), "code": x.get("code"),
                  "gain": x.get("gain"), "hit": x.get("hit"),
                  "pred_date": x.get("pred_date"),
                  "confidence": x.get("confidence")} for x in best],
        "worst": [{"name": x.get("name"), "code": x.get("code"),
                   "gain": x.get("gain"), "hit": x.get("hit"),
                   "pred_date": x.get("pred_date"),
                   "confidence": x.get("confidence")} for x in worst],
        "today_count": today_count,
        "today_avg_prob": today_avg_prob,
        "note": "命中率=次日收盘>预测日现价(昨日推荐今日严格上涨, 平盘/下跌不计)",
    }


# ========================== V2.0 新增模块 (需求 4.1-4.5) ==========================

def position_guide(sentiment):
    """仓位指引卡片 (V2.0 需求 4.2): 按情绪阶段给出建议仓位
    高潮期: 总仓位7-8成, 主线为主; 启动/发酵: 5-6成, 主线+试错;
    退潮期: 3-4成, 防守为主; 冰点期: 2成以下, 空仓观望
    → {phase, phase_txt, total, mainline, trial, direction, reason}
    """
    phase = sentiment.get("phase") or sentiment.get("main") or "震荡期"
    signals = sentiment.get("signals") or []
    if "退潮信号" in signals:
        phase_txt = "%s(退潮)" % phase
        total, mainline, trial, direction = 3, 1, 0, "减仓防守"
    elif phase == "高潮期":
        phase_txt, total, mainline, trial, direction = "高潮期", 8, 5, 1, "持有主线, 分批兑现"
    elif phase == "发酵期":
        phase_txt, total, mainline, trial, direction = "发酵期", 6, 3, 2, "加仓主线"
    elif phase == "启动期":
        phase_txt, total, mainline, trial, direction = "启动期", 5, 2, 2, "试探性加仓"
    elif phase == "冰点期":
        phase_txt, total, mainline, trial, direction = "冰点期", 2, 1, 0, "空仓观望"
    else:
        phase_txt, total, mainline, trial, direction = "震荡期", 4, 2, 1, "观望, 轻仓试错"
    reason = "情绪%s, 总仓位建议%d成(主线%d成/试错%d成)" % (phase_txt, total, mainline, trial)
    return {"phase": phase, "phase_txt": phase_txt, "total": total,
            "mainline": mainline, "trial": trial, "direction": direction, "reason": reason}


def core_conclusion(sentiment, market, resonance, risk, rotation, watch):
    """一句话核心结论 (V2.0 需求 4.1 置顶全宽背景条)
    组成要素: 情绪阶段+边际变化 / 主线状态 / 建议仓位 / 核心关注方向
    → {text, phase, mainline, position, focus, rotation}
    """
    phase = sentiment.get("phase") or sentiment.get("main") or "震荡期"
    signals = sentiment.get("signals") or []
    trend = int(sentiment.get("trend") or 0)
    if "退潮信号" in signals:
        phase_txt = "%s退潮边缘" % phase
    elif trend > 0:
        phase_txt = "%s(数量回升)" % phase
    elif trend < 0:
        phase_txt = "%s(数量回落)" % phase
    else:
        phase_txt = phase
    core_n = int(sentiment.get("core_n")
                 or resonance.get("mainline_count") or 0)
    mainline_txt = "主线确认(%d个核心)" % core_n if core_n else "主线缺失"
    total = position_guide(sentiment)["total"]
    repeat_n = len(rotation.get("repeat_detail") or rotation.get("repeat") or [])
    rotation_txt = "板块轮动快" if repeat_n >= 2 else "板块轮动平稳"
    focus = watch.get("seed_high") or []
    focus_txt = "、".join(x.get("name", "") for x in focus[:3]) if focus else "暂无明确方向"
    text = "今日情绪处于%s，%s，%s；建议仓位%d成，重点跟踪%s的晋级情况。" % (
        phase_txt, mainline_txt, rotation_txt, total, focus_txt)
    return {"text": text, "phase": phase_txt, "mainline": mainline_txt,
            "position": total, "focus": focus_txt, "rotation": rotation_txt}


def yesterday_check(rps5, sentiment, today_date=None):
    """昨日复盘验证闭环 (V2.0 需求 4.3): 验证前一日复盘核心观点
    读上一交易日 daily_review.json 的情绪阶段 + 高质量种子跟踪清单,
    对比今日实际: 阶段转好/转差/符合 + 跟踪板块晋级/调出 + 观点准确率
    → {has_data, yesterday_date, phase_check, track_checked, hit_rate, summary}
    """
    prev = _load_json(STATIC_DIR / "daily_review.json")
    if not prev:
        return {"has_data": False, "note": "上一交易日复盘缺失, 无法验证"}
    if today_date and prev.get("update_date") == today_date:
        return {"has_data": False, "note": "上一交易日复盘缺失(同日重复计算)"}
    p_sent = prev.get("sentiment") or {}
    p_phase = p_sent.get("phase") or p_sent.get("main") or ""
    p_seed = (prev.get("watch") or {}).get("seed_high") or []
    if not p_phase and not p_seed:
        return {"has_data": False, "note": "上一交易日复盘无观点数据"}
    # 情绪阶段对比 (按阶段序: 冰点<启动<震荡<发酵<高潮)
    order = {"冰点期": 0, "启动期": 1, "震荡期": 2, "发酵期": 3, "高潮期": 4}
    t_phase = sentiment.get("phase") or sentiment.get("main") or ""
    pv, tv = order.get(p_phase), order.get(t_phase)
    if pv is not None and tv is not None:
        if tv > pv:
            phase_check = "转好(%s→%s)" % (p_phase, t_phase)
        elif tv < pv:
            phase_check = "转差(%s→%s)" % (p_phase, t_phase)
        else:
            phase_check = "符合(%s持续)" % p_phase
    else:
        phase_check = "符合(阶段口径不可比)" if p_phase == t_phase else "无法比较(%s→%s)" % (p_phase, t_phase)
    # 昨日高质量种子 → 今日验证: 晋级(仍在榜)/调出/未在榜
    today_in = {s.get("name") for s in rps5.get("in_list", [])}
    today_out = {s.get("name") for s in rps5.get("out_list", [])}
    checked, ok = [], 0
    for x in p_seed[:5]:
        nm = x.get("name")
        if not nm:
            continue
        if nm in today_in:
            verdict, is_ok = "晋级", True
        elif nm in today_out:
            verdict, is_ok = "调出", False
        else:
            verdict, is_ok = "未在榜", False
        checked.append({"name": nm, "verdict": verdict, "ok": is_ok})
        if is_ok:
            ok += 1
    hit_rate = round(ok / len(checked) * 100, 1) if checked else None
    summary = "昨日观点验证: 情绪%s; 跟踪%d个板块%d个晋级, 观点准确率%s" % (
        phase_check, len(checked), ok,
        ("%.1f%%" % hit_rate) if hit_rate is not None else "—")
    return {"has_data": True, "yesterday_date": prev.get("update_date", ""),
            "phase_check": phase_check, "track_checked": checked,
            "hit_rate": hit_rate, "summary": summary}


def core_targets(resonance, recs=None, max_per=2):
    """核心标的映射 (V2.0 需求 4.4): 硬核主线板块 → 今日推荐中对应核心龙头标的
    打通「板块复盘 → 个股推荐」链路: 主线板块名与推荐个股 sector 宽容匹配
    → {targets: [{sector, stocks: [{name, code}]}], note}
    """
    if recs is None:
        recs = load_recommendations() or {}
    by_sector = {}
    for s in recs.get("stocks") or []:
        sec = s.get("sector")
        if not sec:
            continue
        by_sector.setdefault(str(sec), []).append(s)
    core_names = [c.get("name") for c in resonance.get("core", [])]
    targets = []
    for nm in core_names:
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
    note = "今日推荐未覆盖主线板块, 暂无核心标的映射" if not targets else ""
    return {"targets": targets, "note": note}


def abnormal_signals(breadth, market, risk, health, history, rotation, index=None):
    """异常信号高亮 (V2.0 需求 4.5): 满足任意一条即展示
    1 指数失真(high):   指数偏强但全市场下跌家数>60%
    2 顶背离(high):     板块 RPS 创新高但内部扩散率环比下降
    3 放量滞涨(mid):    成交额放大但高分板块强度弱且跌多涨少
    4 资金撤离(critical): 高位板块批量调出但指数不跌
    → {signals: [{type, title, level, level_text, desc}], note}
    """
    signals = []
    mb = breadth or {}
    up, down = _num(mb.get("up_count")), _num(mb.get("down_count"))
    down_ratio = down / (up + down) if (up + down) > 0 else 0.0
    idx = index or {}
    # 1) 指数失真: 指数未跌(站上20日线)但全市场下跌家数>60%
    if (up or down) and idx.get("above") and down_ratio > 0.6:
        signals.append({"type": "index_distortion", "title": "指数失真",
                        "level": "high",
                        "desc": "指数站上20日线但全市场下跌家数%.0f%%, 指数上涨或失真, 警惕权重护盘"
                                % (down_ratio * 100)})
    # 2) 顶背离: 板块 RPS 创新高但内部扩散率环比下降 (health unified 今日环比<0)
    unified = (health or {}).get("unified") or []
    for x in unified:
        chg = x.get("ratio_chg")
        if chg is not None and chg < 0 and _rps_new_high(history, x.get("name")):
            signals.append({"type": "divergence", "title": "顶背离", "level": "high",
                            "desc": "%s RPS创新高但内部扩散率环比%.1f%%, 价量背离, 谨防冲高回落"
                                    % (x.get("name", ""), chg)})
            break
    # 3) 放量滞涨: 两市成交额放大(≥1.2万亿)但高分板块均涨幅<1% 且跌多涨少
    amt = _num(mb.get("total_amount"))
    avg_gain = _num(market.get("avg_gain"))
    if amt >= 12000 and down_ratio > 0.5 and avg_gain < 1.0:
        signals.append({"type": "volume_stagnation", "title": "放量滞涨", "level": "mid",
                        "desc": "两市成交%.0f亿但高分板块均涨幅%.1f%%且跌多涨少, 放量滞涨, 注意兑现"
                                % (amt, avg_gain)})
    # 4) 资金撤离: 主线板块批量调出但指数不跌
    if (risk or {}).get("main_out_total") and idx.get("above"):
        signals.append({"type": "capital_exit", "title": "资金撤离", "level": "critical",
                        "desc": "主线板块批量调出%d个但指数不跌, 高位资金或已撤离, 回避对应赛道"
                                % (risk.get("main_out_total") or 0)})
    level_txt = {"low": "低风险", "mid": "中风险", "high": "高风险", "critical": "极高风险"}
    for s in signals:
        s["level_text"] = level_txt.get(s["level"], s["level"])
    return {"signals": signals,
            "note": "异常信号数据源: 市场广度/板块RPS与扩散率/轮动调出; 数据不足的信号自动跳过"}


def _rps_new_high(history, name, window=5):
    """板块 RPS5 是否近 window 个交易日新高 (用于顶背离判断, V2.0 需求 4.5)"""
    if not history or not name:
        return False
    dates = sorted(history.keys())
    if not dates:
        return False
    today_rps = _num((history[dates[-1]].get(name) or {}).get("RPS5"))
    if today_rps <= 0:
        return False
    for d in dates[-window:-1]:
        v = (history[d].get(name) or {}).get("RPS5")
        if v is not None and _num(v) > today_rps:
            return False
    return True


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
    heal = health_analysis(in5, history,
                           prev_ratios=_load_prev_health_ratios(rps_data.get("update_date")))
    mb = fetch_market_breadth()  # V2.0 市场广度(涨跌家数/涨跌停/成交额/最高连板)
    risk = risk_analysis(r5, r10, r20, history, breadth=mb)
    watch = watchlist(r5, r10, r20, history)
    rec = load_recommendations() or {}
    ms_rec = rec.get("market_status", "")
    rot = rotation_analysis(r5, r10, r20, history)
    sent = sentiment_phase(r5, r10, r20, history, risk,
                           seed_high_n=len(heal["seed_high"]))
    # V2.0 新增模块 (需求 4.1-4.5): 核心结论 / 仓位指引 / 昨日验证 / 核心标的 / 异常信号
    core_concl = core_conclusion(sent, mkt, res, risk, rot, watch)
    pos_guide = position_guide(sent)
    abnormal = abnormal_signals(mb, mkt, risk, heal, history, rot, mkt.get("index"))
    # V2.0 进阶观察 (需求 3.10): 风格标签完善 + 风格切换判断 + 明日核心策略要点
    style_st = style_analysis(r5, r10, r20)
    style_obs = style_outlook(r5, r10, r20, sent, risk, res, style_st)
    stats = {
        "update_date": rps_data.get("update_date", ""),
        "prev_date": rps_data.get("prev_date", ""),
        "total_industries": rps_data.get("total_industries", 0),
        "threshold": rps_data.get("threshold", 95),
        "market": mkt,
        "market_breadth": mb,  # V2.0 市场广度(涨跌家数/涨跌停/成交额/最高连板)
        "resonance": _slim_sections(res),
        "tier": _slim_tiers(tier_analysis(in5)),
        "rotation": _slim_sections(rot),
        "health": heal,
        "risk": risk,
        "watch": watch,
        "sentiment": sent,
        "milestones": sentiment_milestones(history),  # V2.0 情绪关键拐点(主线确认/首次退潮/情绪冰点)
        "style": style_st,
        "style_outlook": style_obs,  # V2.0 进阶观察 (需求 3.10)
        "consistency": consistency_check(mkt["tone"], ms_rec),
        "market_status_rec": ms_rec,
        # 消息面复盘 (2026-08-23): 聚合日度+早期推荐的新闻信号; 旧版推荐无 news 字段 → None
        # V2.0 (需求 3.8): 资金面(两市成交额/主力净额/北向广度)随消息面一并输出
        "news": news_review(breadth=mb, fund_flow=_fetch_market_fund_flow(),
                            north=_fetch_north_breadth()),
        "rec_accuracy": rec_accuracy(),
        # 早期启动挖掘: 只追加新字段, 原有字段/md 文档完全不变 (向下兼容)
        "potential_sectors": potential_sectors(history, potential_stats),
        "sector_life_cycle": {"rps5": sector_life_cycle(r5),
                              "rps10": sector_life_cycle(r10),
                              "rps20": sector_life_cycle(r20)},
        # V2.0 新增模块 (需求 4.1-4.5)
        "core_conclusion": core_concl,
        "position_guide": pos_guide,
        "yesterday_check": yesterday_check(r5, sent, rps_data.get("update_date")),
        "core_targets": core_targets(res, rec),
        "abnormal_signals": abnormal,
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
    # V2.0 一句话核心结论 (需求 4.1 置顶全宽背景条)
    cc = stats.get("core_conclusion") or {}
    if cc.get("text"):
        L.append("> **%s**" % cc["text"])
        L.append("")
    # 今日市场总览
    L.append("## 【今日市场总览】")
    L.append("情绪周期: **%s**%s" % (sent["main"],
             (" + " + "、".join(sent["signal"])) if sent.get("signal") else ""))
    L.append("大盘强度: **%s**, 风险等级: **%s**" % (m["tone"], risk.get("level", "safe")))
    L.append("")
    # V2.0 昨日复盘验证闭环 (需求 4.3): 对比昨日核心观点与今日现实, 验证复盘准确率
    yc = stats.get("yesterday_check") or {}
    if yc.get("has_data"):
        L.append("## 【昨日复盘验证】")
        L.append("- 昨日观点(更新于%s): %s" % (yc.get("yesterday_date", ""), yc.get("phase_check", "—")))
        if yc.get("track_checked"):
            L.append("- 跟踪板块: %s" % "、".join(
                "%s(%s)" % (x["name"], x["verdict"]) for x in yc["track_checked"]))
        if yc.get("summary"):
            L.append("- %s" % yc["summary"])
        L.append("")
    elif yc.get("note"):
        L.append("## 【昨日复盘验证】")
        L.append("- %s" % yc["note"])
        L.append("")
    # 情绪周期
    L.append("## 【情绪周期】")
    L.append("主标签: **%s**; 附加信号: %s; 种子%d/调出%d/核心主线%d/高分板块%d"
             % (sent["main"], "、".join(sent["signal"]) if sent.get("signal") else "无",
                sent["seed_n"], sent["out_n"], sent["core_n"], sent["high_n"]))
    if sent.get("basis"):
        L.append("- 判定依据: %s" % sent["basis"])
    ms = stats.get("milestones") or {}
    if any(ms.get(k) for k in ("mainline_confirm_date", "first_tideout_date", "emotion_ice_date")):
        parts = []
        if ms.get("mainline_confirm_date"):
            parts.append("主线确认日 %s" % ms["mainline_confirm_date"])
        if ms.get("first_tideout_date"):
            parts.append("首次退潮日 %s" % ms["first_tideout_date"])
        if ms.get("emotion_ice_date"):
            parts.append("情绪冰点日 %s" % ms["emotion_ice_date"])
        L.append("- 关键拐点: %s" % "、".join(parts))
    L.append("")
    # 大盘强度
    L.append("## 【大盘强度】")
    L.append("今日市场动量**%s**: RPS5≥95 板块 %d 个(较前日 %+d)、RPS10≥95 %d 个、RPS20≥95 %d 个。%s"
             % (m["tone"], m["high5"], m["diff5"], m["high10"], m["high20"], m["note"]))
    if m.get("pct30") is not None:
        L.append("- 高分板块处于近30日 %.0f%% 分位（%s）" % (m["pct30"], m.get("pct_level") or "—"))
    # V2.0 市场广度一行: 涨跌家数/涨跌停/最高连板/两市成交额
    mb = stats.get("market_breadth") or {}
    mb_parts = []
    if mb.get("up_count") is not None or mb.get("down_count") is not None:
        mb_parts.append("上涨%d/下跌%d" % (_num(mb.get("up_count")), _num(mb.get("down_count"))))
    if mb.get("limit_up") is not None or mb.get("limit_down") is not None:
        mb_parts.append("涨停%d/跌停%d" % (_num(mb.get("limit_up")), _num(mb.get("limit_down"))))
    if mb.get("max_board") is not None:
        mb_parts.append("最高连板%d板" % _num(mb.get("max_board")))
    if mb.get("total_amount") is not None:
        mb_parts.append("两市成交%.0f亿元" % _num(mb.get("total_amount")))
    if mb_parts:
        src = ("(%s)" % mb["source"]) if mb.get("source") else ""
        L.append("- **市场广度**: %s%s" % ("、".join(mb_parts), src))
    if mb.get("note"):
        L.append("- %s" % mb["note"])
    if stats.get("market_status_rec"):
        ms_txt = _fmt_market_status(stats["market_status_rec"])
        if ms_txt:
            L.append("推荐系统大盘环境判定: **%s**。" % ms_txt)
    # V2.0 仓位指引卡片 (需求 4.2): 总仓位/主线配置/试错仓位/操作方向
    pg = stats.get("position_guide") or {}
    if pg:
        L.append("- **仓位指引**: 总仓位**%d成**(主线配置%d成/试错%d成), 操作方向: %s"
                 % (pg["total"], pg["mainline"], pg["trial"], pg["direction"]))
    L.append("")
    # 主线梯队
    L.append("## 【主线梯队】")
    if res["core"]:
        L.append("- **硬核主线**(三周期%d): %s" % (len(res["core"]), _fmt_sector_list(res["core"])))
    if res["tough"]:
        L.append("- **韧性主线**(%d): %s" % (len(res["tough"]), _fmt_sector_list(res["tough"])))
    if res["mainline_count"]:
        mainlines = "、".join("%s(连%d天,达标%.0f%%)"
                              % (x["name"], _num(x.get("continuous")), strength_ratio(x) * 100)
                              for x in res["mainlines"][:8])
        L.append("- **主线生命周期**: %s" % mainlines)
    if res["pulse"]:
        L.append("- **短期脉冲**(%d): %s" % (len(res["pulse"]), _fmt_sector_list(res["pulse"])))
    if res["diverge"]:
        L.append("- **主线分歧**(在榜,RPS5回落): %s" % _fmt_sector_list(res["diverge"]))
    if tier["tier1"]:
        L.append("- **第一梯队**(Top%d): %s"
                 % (CONFIG_REVIEW["tier1_top"], _fmt_sector_list(tier["tier1"])))
    if tier["tier2"]:
        L.append("- **第二梯队**(11~%d): %s" % (CONFIG_REVIEW["tier2_top"], _fmt_sector_list(tier["tier2"])))
    # V2.0 核心标的映射 (需求 4.4): 硬核主线板块 → 今日推荐核心龙头标的
    ct = stats.get("core_targets") or {}
    if ct.get("targets"):
        for t in ct["targets"]:
            stocks = "、".join("%s(%s)" % (s["name"], s["code"]) for s in t["stocks"])
            L.append("- **%s核心标的**: %s" % (t["sector"], stocks))
    elif ct.get("note"):
        L.append("- 核心标的映射: %s" % ct["note"])
    L.append("")
    # 轮动观察
    L.append("## 【轮动观察】")
    if rot.get("summary"):
        L.append("- **轮动总结**: %s" % rot["summary"])
    if rot["new_in"]:
        L.append("- **今日新调入**(%d): %s" % (len(rot["new_in"]), _fmt_sector_list(rot["new_in"])))
    if rot["rank_jump"]:
        jumps = "、".join("%s(名次%+d→第%d名)" % (s["name"], int(_num(s.get("rank_change"))), int(_num(s.get("rank"))))
                          for s in rot["rank_jump"])
        L.append("- **排名跃升Top%d**: %s" % (CONFIG_REVIEW["jump_top"], jumps))
    if rot["exits"]:
        # V2.0: 主线调出(高危)与支线调出(正常)拆分展示
        main_out = [e for e in rot["exits"] if e["name"] in {x["name"] for x in rot.get("main_out", [])}]
        branch_out = [e for e in rot["exits"] if e["name"] not in {x["name"] for x in rot.get("main_out", [])}]
        for label, group, danger in (("主线板块调出(高危)", main_out, True),
                                     ("支线板块调出(正常)", branch_out, False)):
            if group:
                txt = "、".join("%s(%d天)" % (e["name"], e["continuous"]) for e in group)
                L.append("- **%s**(%d): %s" % (label, len(group), txt))
    if rot.get("repeat_detail"):
        rp = "、".join("%s(反复%d次)" % (x["name"], x["flips"]) for x in rot["repeat_detail"])
        L.append("- **反复轮动**: %s" % rp)
    elif rot["repeat"]:
        L.append("- **反复轮动**: %s" % _fmt_sector_list(rot["repeat"]))
    if not rot["new_in"] and not rot["rank_jump"] and not rot["exits"] and not rot["repeat"]:
        L.append("- 无明显轮动信号")
    style_top = sorted(style["rps5"].items(), key=lambda x: -x[1]["count"])[:3]
    if style_top:
        L.append("- **风格分布**: %s" % "；".join("%s%d个(%.0f%%)" % (k, v["count"], v["pct"]) for k, v in style_top))
    L.append("")
    # 板块健康扫描 (V2.0 需求 3.5: 合并重复板块 + 判定规则 + 达标率环比)
    L.append("## 【板块健康扫描】")
    if heal.get("unified"):
        L.append("- 判定规则: %s" % "；".join("%s=%s" % (k, v) for k, v in HEALTH_RULES.items()))
        for x in heal["unified"]:
            chg = x.get("ratio_chg")
            if chg is None:
                chg_txt = ""
            elif chg > 0:
                chg_txt = " ↑%.1f%%" % chg
            elif chg < 0:
                chg_txt = " ↓%.1f%%" % abs(chg)
            else:
                chg_txt = " 持平"
            L.append("- %s(%.1f%%%s) [%s]" % (x["name"], x["ratio"], chg_txt,
                                              "/".join(x["tags"])))
    else:
        L.append("- 暂无入选板块健康数据")
    L.append("")
    # 风险预警 (V2.0: 四级风险等级 + 赛道归类 + 操作建议)
    lv2 = risk.get("level_v2", "low")
    lv2_text = risk.get("level_v2_text", "低风险")
    lv2_color = risk.get("level_color", "green")
    L.append("## 【风险预警】(风险等级: %s(%s), 今日调出%d个/主线调出%d个)"
             % (lv2_text, lv2_color, risk.get("out_total", 0), risk.get("main_out_total", 0)))
    if risk["warnings"]:
        for w in risk["warnings"]:
            L.append("- [%s] %s" % (w.get("level", "hint"), w["text"]))
    else:
        L.append("- 无风险信号: 市场结构健康")
    if risk.get("track_groups"):
        L.append("- **赛道归类**: %s"
                 % "、".join("%s赛道 批量调出%d个板块" % (g["track"], g["count"])
                             for g in risk["track_groups"]))
    if risk.get("advice"):
        L.append("- **操作建议**: %s" % "；".join(risk["advice"]))
    if watch.get("diverge_dropped"):
        L.append("- **主线衰竭**(掉榜≥3天): %s"
                 % "、".join("%s(掉%d天)" % (x["name"], x["drop_days"]) for x in watch["diverge_dropped"][:8]))
    L.append("")
    # V2.0 异常信号高亮 (需求 4.5): 指数失真/顶背离/放量滞涨/资金撤离 4 种触发
    abn = stats.get("abnormal_signals") or {}
    if abn.get("signals"):
        L.append("## 【异常信号高亮】")
        for s in abn["signals"]:
            L.append("- **[%s](%s)**: %s" % (s.get("title", ""), s.get("level_text", ""), s.get("desc", "")))
        if abn.get("note"):
            L.append("- %s" % abn["note"])
        L.append("")
    # 消息面复盘 (V2.0 需求 3.8: 板块标注 + 评分基准 + 资金面)
    nws = stats.get("news")
    L.append("## 【消息面复盘】")
    if nws and nws.get("has_news"):
        L.append("消息面环境: **%s**; 推荐%d只中%d只命中消息信号, 净影响%+d分"
                 % (nws.get("env") or "中性", nws.get("stock_count", 0),
                    nws.get("hit_count", 0), nws.get("score_sum", 0)))
        if nws.get("score_basis"):
            L.append("- 评分基准: %s" % nws["score_basis"])
        fund = nws.get("fund") or {}
        fund_parts = []
        if fund.get("total_amount") is not None:
            fund_parts.append("两市成交额%.0f亿" % fund["total_amount"])
        if fund.get("main_net") is not None:
            sign = "流入" if fund["main_net"] >= 0 else "流出"
            fund_parts.append("主力资金净%s%.0f亿" % (sign, abs(fund["main_net"])))
        if fund.get("north_ratio") is not None:
            fund_parts.append("北向成分股上涨占比%.1f%%" % fund["north_ratio"])
        if fund_parts:
            extra = "(%s)" % fund["north_note"] if fund.get("north_note") else ""
            L.append("- **资金面**: %s%s" % ("、".join(fund_parts), extra))
        if nws.get("official_pos_list"):
            L.append("- **权威利好**(真实性采信, +%d分/条): %s"
                     % (2, "、".join("%s(%d条)%s" % (x["name"], x["count"], _sector_tag(x))
                                     for x in nws["official_pos_list"])))
        if nws.get("official_neg_list"):
            L.append("- **权威利空**(真实性采信, 扣%d分/条): %s"
                     % (3, "、".join("%s(%d条)%s" % (x["name"], x["count"], _sector_tag(x))
                                     for x in nws["official_neg_list"])))
        if nws.get("rumor_list"):
            L.append("- **传闻消息**(散户平台, 仅提示不采信): %s"
                     % "、".join("%s(好%d/空%d)%s" % (x["name"], x["pos"], x["neg"], _sector_tag(x))
                                 for x in nws["rumor_list"]))
        if nws.get("tags_count"):
            extra = "；".join("%s%d只" % (t, c) for t, c in nws["tags_count"].items()
                              if t not in ("利好消息", "利空消息", "传闻利好", "消息待证"))
            if extra:
                L.append("- **主力/散户动向**: %s" % extra)
        if nws.get("note"):
            L.append("- %s" % nws["note"])
    else:
        L.append("- 暂无消息面信号")
    L.append("")
    # 推荐准确率复盘 V2.0 (需求 3.9: 标题含策略名 + 3/5日周期收益; 命中率四舍五入与前端 toFixed 一致)
    acc = stats.get("rec_accuracy") or {}
    L.append("## 【%s · 推荐准确率复盘】" % (acc.get("strategy") or "日度动量策略"))
    if acc.get("has_data"):
        hr = acc.get("hit_rate", 0)
        ag = acc.get("avg_gain", 0)
        L.append("- 整体命中率: **%d%%**(%d样本), 平均涨幅 **%+.1f%%**; 预测平均概率%.1f%%"
                 % (int(round(hr * 100 + 0.0001)), acc.get("samples", 0), ag, acc.get("avg_up_prob", 0)))
        # V2.0 (需求 3.9): 三个持有周期收益 (数据不足的周期省略)
        horizon_parts = []
        for key, label in (("avg_gain", "次日"), ("avg_gain3", "持有3日"), ("avg_gain5", "持有5日")):
            v = acc.get(key)
            if v is not None:
                n = acc.get("horizon3_n" if key == "avg_gain3" else
                            "horizon5_n" if key == "avg_gain5" else "samples")
                horizon_parts.append("%s平均%+.1f%%(%d样本)" % (label, v, n or 0))
        if horizon_parts:
            L.append("- 周期收益: %s" % "；".join(horizon_parts))
        if acc.get("today_count"):
            tp = acc.get("today_avg_prob")
            L.append("- 今日推荐%d只, 平均上涨概率%s" % (acc["today_count"],
                     ("%.1f%%" % tp) if tp is not None else "无"))
        by_conf = acc.get("by_confidence") or {}
        conf_parts = []
        for lv in ("高", "中", "低"):
            v = by_conf.get(lv)
            if v and v.get("samples"):
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
        L.append("- 暂无预测准确率数据(需运行推荐+校准后生成)")
    L.append("")
    # 次日跟踪清单
    L.append("## 【次日跟踪清单】")
    if watch["seed_high"]:
        L.append("- **高质量种子池**: %s"
                 % "、".join("%s(%.0f%%)" % (x["name"], x["ratio"]) for x in watch["seed_high"][:8]))
    if watch["seed_pulse"]:
        L.append("- **脉冲种子池**: %s" % _fmt_sector_list(watch["seed_pulse"]))
    if watch["critical"]:
        crit = "、".join("%s(%.0f)" % (c["name"], c["rps5"]) for c in watch["critical"][:8])
        L.append("- **临界晋级池**(RPS≈阈值): %s" % crit)
    if watch["diverge_watch"]:
        L.append("- **主线分歧池**(掉榜分歧): %s"
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
    # V2.0 进阶观察 (需求 3.10): 风格标签完善 + 风格切换判断 + 明日核心策略要点
    so = stats.get("style_outlook") or {}
    if so:
        L.append("## 【进阶观察】")
        unc = so.get("unclassified") or {}
        if unc.get("total"):
            ratio_txt = ("%.1f%%(%d个)" % (unc["pct"], unc["count"])) if unc["count"] else "0%"
            flag = "" if so.get("ratio_ok") else " **超20%上限, 需扩充风格关键词**"
            L.append("- **风格标签完善**: 未归类板块 %s%s%s" % (
                ratio_txt,
                ("：%s" % "、".join(unc["names"])) if unc.get("names") else "，全部已归类",
                flag))
        if so.get("switch_desc"):
            L.append("- **风格切换判断**: %s" % so["switch_desc"])
        pts = so.get("tomorrow_points") or []
        if pts:
            L.append("- **明日核心策略要点**: %s" % "；".join(pts))
        if so.get("note"):
            L.append("- %s" % so["note"])
    L.append("")
    # 矛盾提示: 两套结论都写入, 方便人工比对
    if cons.get("conflict"):
        L.append("## 【矛盾提示】")
        L.append("- %s" % cons["warning"])
        L.append("- RPS 推导市场强度: **%s** (RPS5≥95 板块 %d 个)" % (m["tone"], m["high5"]))
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


def _archive_health_ratios(stats):
    """归档当日板块健康达标率 → health_ratios_history.json (供次日 ratio_chg 环比)
    幂等: 同日已归档则覆盖更新; 仅保留最近 30 个交易日
    """
    date = stats.get("update_date") or ""
    unified = (stats.get("health") or {}).get("unified") or []
    if not date:
        return
    ratios = {u.get("name"): _num(u.get("ratio")) for u in unified if u.get("name")}
    if not ratios:
        return
    path = STATIC_DIR / HEALTH_RATIOS_NAME
    hist = _load_json(path) or {}
    if not isinstance(hist, dict):
        hist = {}
    hist[str(date)] = ratios
    keys = sorted(hist.keys())
    if len(keys) > 30:
        for k in keys[:-30]:
            hist.pop(k, None)
    STATIC_DIR.mkdir(exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1, allow_nan=False)


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
                # 清理历史残留的重复标记(旧备注区误含标记本身导致累积)
                manual_block = manual_block.replace(AUTO_GEN_MARKER, "")
        except Exception:
            manual_block = ""
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1, allow_nan=False)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_text + manual_block)
    _archive_health_ratios(stats)
    return json_path, md_path


def _append_sentiment_history(stats):
    """追加当日情绪周期到历史文件 (幂等: 同日最后一条则跳过), 供前端情绪趋势图
    history: [{date, phase, high_n, seed_n, core_n, out_n}] 按日期升序, 保留最近 N 条
    """
    sent = stats.get("sentiment") or {}
    date = stats.get("update_date") or ""
    if not date:
        return
    path = STATIC_DIR / SENTIMENT_HISTORY_NAME
    history = _load_json(path) or []
    if not isinstance(history, list):
        history = []
    if history and history[-1].get("date") == date:
        return  # 幂等: 同日已记录
    prev_high = history[-1].get("high_n") if history else None
    trend = None
    if prev_high is not None:
        trend = int(_num(sent.get("high_n")) - _num(prev_high))
    history.append({
        "date": date,
        "phase": sent.get("main") or sent.get("phase") or "震荡期",
        "high_n": int(_num(sent.get("high_n"))),
        "seed_n": int(_num(sent.get("seed_n"))),
        "core_n": int(_num(sent.get("core_n"))),
        "out_n": int(_num(sent.get("out_n"))),
        # V2.0: 高分板块数量较前日变化(供前端双轴图/拐点标注), 首条为 None
        "trend": trend,
    })
    history = history[-SENTIMENT_HISTORY_MAX:]
    STATIC_DIR.mkdir(exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1, allow_nan=False)


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
    _append_sentiment_history(stats)
    print("  [OK] 每日复盘已生成: %s / %s" % (json_path.name, md_path.name))
    print(md)


if __name__ == "__main__":
    logger.info("===== daily_review 开始 =====")
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    force = "--force" in sys.argv
    try:
        main(force=force)
    finally:
        logger.info("===== daily_review 结束 =====")
