# -*- coding: utf-8 -*-
"""
超跌反转潜力精选 —— 独立日度策略 (与原有强者恒强动量策略互补)
定位: 全行业范围内筛选「基本面优质 + 股价深度回撤(档位自适应≥25%/35%/45%) + 技术/资金/催化共振即将反弹」的错杀股
策略核心: 漏斗过滤 (深度超跌 → 估值硬约束 → 基本面 → 爆发信号 → 综合评分)
  前置层 大盘环境: 复用原版三态(PASS/WARN/REJECT), 额外排除单边暴跌市(连续3日普跌) [默认忽略]
  第一层 深度超跌硬性门槛(一票否决): 近250日最高收盘价回撤≥当前档位阈值(25%/35%/45%, 随大盘自动适配) + 近10日不创新低 + 价格≥2元 + 近20日日均成交额≥3000万 + 急跌错杀保留/阴跌3个月创新低剔除
  估值层 估值三层硬约束(一票否决): 绝对PE行业分档上限 / 近3年PE分位≤40%(≥70%剔除) / 估值回撤≥股价回撤×70% + 题材爆炒腰斩/周期顶点回落 排雷
  第二层 基本面优质验证(排雷+加分):  ST/退市/立案/连续亏损扩/高负债/高商誉/高质押/净利≤0/现金流≤0/扣非增速<-10%/PEG>2 → 一票否决
                                              + 盈利稳定/现金流健康/龙头/业绩拐点/筹码集中 → 加分
  第三层 爆发前置信号(技术≤4项+资金≤3项+催化≤2项, 命中总数≥3项): 底部企稳形态/均线拐头/量价齐升/突破平台/筹码集中 + 主力净流入 + 换手温和提升 + 北向逆势/机构龙虎榜 + 板块RPS回升/政策利好/风格切换
  第四层 综合评分排序 (超跌25% + 基本面25% + 估值性价比25% + 爆发信号25%) → 每日输出 2~3 只
输出: static/rebound_recommendations.json (schema对齐recommendations.json, 前端可复用表格渲染)
"""
from logutil import get_logger
logger = get_logger(__name__)

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

import rps_calc
from rps_calc import pool, STATIC_DIR
from recommend import (
    CONFIG as _MOM_CFG,
    build_daily_by_code,
    is_st_stock,
    calc_rsi,
    calc_boll,
    fetch_index_daily,
    fetch_fina_indicator_batch,
    fetch_moneyflow_batch,
    fetch_daily_basic_em,
    check_market_env,
    _get_ohlc_series,
)
try:
    import predict as _predict
except Exception:
    _predict = None

REBOUND_FILE = STATIC_DIR / "rebound_recommendations.json"
CANDIDATE_LOG_FILE = STATIC_DIR / "rebound_candidate_log.json"
PE_HIST_CACHE_FILE = STATIC_DIR / "pe_hist_cache.json"  # {ts_code: {fetched_date, dates, pe}} 估值分位/回撤用

# 所有阈值集中配置 (无需改代码即可调参)
CONFIG_REBOUND = {
    # —— 运行模式开关 (与原有动量策略独立) ——
    "run_mode": {
        "REBOUND_ENABLED": True,  # False → 不生成超跌推荐
        "TOP_N": 2,                # 最终输出 Top N (默认2只, 规范 1.1 每日输出默认2只)
        "IGNORE_MARKET_ENV": True, # True → 超跌推荐 不看大盘环境的通过/拒绝判定;
                                   #       但「回撤档位自动适配」与「连续3日普跌暂停」仍生效 (规范 2.2/3.2)
    },
    # —— 前置层: 大盘环境 (复用原版 check_market_env + 超跌专属适配规则) ——
    "market": {
        "index_codes": _MOM_CFG["market"]["index_codes"],
        "index_names": _MOM_CFG["market"]["index_names"],
        "ma_period": 20,
        "need_above": 1,
        "default_safe": True,
        "check_sentiment": True,
        "advance_ratio_min": 0.30,       # 超跌策略略放宽: ≥30% 即通过 (抄底环境比动量更宽容)
        "limit_down_max": 50,            # 跌停数比动量策略放宽10家 (抄底接受跌停稍多)
        # 超跌新增: 排除单边暴跌市 —— 最近3个交易日累计普跌 (上涨占比均值<35% 或 连续3天上涨占比<40%)
        "avoid_crash_mode": True,
        "crash_days": 3,
        "crash_avg_ratio": 0.35,
        # —— 2026-08-29 规范 v2: 回撤档位自动适配 + 连续3日普跌暂停 ——
        "tier_auto_select": True,          # 按大盘强弱自动切换回撤档位 (强势→激进25% / 震荡→标准35% / 偏弱→保守45%)
        "advance_ratio_strong": 0.60,      # 当日上涨占比 ≥ 60% → 强势 → 激进档(25%)
        "advance_ratio_weak": 0.42,        # 当日上涨占比 < 42% → 偏弱 → 保守档(45%)
        "crash_pause_even_ignore": True,   # 即使 IGNORE_MARKET_ENV=True 也执行连续3日普跌暂停 (规范 2.2.4/3.2.1)
        "crash_avg_ratio_pause": 0.30,     # 近3日上涨占比均值 < 30% → 暂停本策略推荐
        # 超跌新增: 震荡/修复市时拉满权重(推荐最多2只); 单边牛市降低到1只 (牛市抄底性价比低)
        "bull_mode_cap": 1,
    },
    # —— 第一层 深度超跌硬性门槛 (全部满足才能进入下一层, 一票否决) ——
    "L1_oversold": {
        "drawdown_window": 250,           # 近 N 个交易日 (约1年) 最高收盘为基准
        # —— 回撤三分档 (2026-08-29 规范 v2, 替代原固定 50%): 激进25%/标准35%/保守45%, 随大盘环境自动适配 ——
        #   激进档(强势市): 估值/基本面要求最高, 只做纯情绪回调的优质龙头
        #   标准档(震荡市, 默认): 估值/基本面要求中等, 性价比最高的日常档位
        #   保守档(弱势市): 估值/基本面要求放宽, 但择时信号要求最高, 只做跌透的极端错杀
        "tiers": {
            "aggressive":   {"min": 25, "deep": 45, "pre_gate": 15},
            "standard":     {"min": 35, "deep": 55, "pre_gate": 22},
            "conservative": {"min": 45, "deep": 65, "pre_gate": 28},
        },
        # 大盘环境标签(strong/steady/weak) → 回撤档位
        "tier_by_market": {"strong": "aggressive", "steady": "standard", "weak": "conservative"},
        # 运行时激活档位 (由 _activate_tier/_select_tier 设置; 默认标准档)
        "active_tier": "standard",
        "drawdown_min_pct": 35,           # 当前激活档位阈值 (与 tiers[active_tier].min 同步, 下游函数直接读取)
        "drawdown_deep_pct": 55,          # 当前激活档位深跌档 (与 tiers[active_tier].deep 同步)
        # 数量兜底: 当前档位选不出合格标的 → 自动放宽一档 (保守→标准→激进), 保证每日至少输出
        "fallback_relax": True,
        "stable_days": 10,                # 近 N 个交易日 不再创阶段新低 (企稳平台判断: 最低价>前(N+M)内的最低)
        "stable_lookback": 60,            # 阶段新低 = 近stable_lookback日最低价 (含自身)
        "min_price": 2,                   # 最新价 ≥ 2 元 (仙股)
        "amount_avg_min": 3e7,            # 近 20 日日均成交额 ≥ 3000 万 (3e7 分)
        "amount_avg_window": 20,
        # PartA 加速: 先用 59 天窗口做 粗回撤门槛, 粗筛不过 → 直接拒绝 (pre_gate 随档位切换; 放宽档位时门槛自动降低)
        "pre_drawdown_gate_pct": 22,
        # —— 下跌性质校验 (规范 2.3, 一票否决): 2个月内快速下跌(急跌,情绪集中释放)保留 / 3个月以上阴跌创新低剔除 ——
        "fall_nature": {
            "enabled": True,
            "fast_days": 40,               # 高点距今 ≤ 40 交易日(约2个月) → 急跌, 保留
            "slow_days": 60,               # 高点距今 > 60 交易日(约3个月) → 慢跌候选, 检查是否阴跌创新低
            "new_low_ratio": 0.97,         # 近 slow_days 最低 < 前 slow_days 最低×0.97 → 持续创新低
            "no_bounce_ratio": 1.02,       # 且 近 slow_days 最高 ≤ 前 slow_days 最低×1.02 → 期间无反弹 → 阴跌剔除
        },
    },
    # —— 第二层 基本面优质验证 (硬性排雷 + 质地加分项阈值) ——
    "L2_fundamental": {
        # —— 排雷 (满足任意一条直接剔除) ——
        "max_debt_ratio": 80,             # 资产负债率 > 80% → 剔除
        "max_goodwill_ratio": 50,         # 商誉/净资产 > 50% → 剔除
        "max_pledge_ratio": 50,           # 股权质押 > 50% → 剔除
        # 连续两个报告期扣非净利润为负且亏损扩大 → 剔除
        "reject_consecutive_loss_widen": True,
        # 近 3 个月有 > 5% 股本解禁 → 剔除 (数据缺失不剔除)
        "reject_big_unlock_recent": True,
        "big_unlock_pct": 5.0,            # > 5% 总股本 → 大额解禁
        "big_unlock_window_days": 90,
        # —— 2026-08-29 规范 v2 新增排雷 (数据缺失不剔除) ——
        "reject_investigate_recent": True,  # 近3个月立案调查 → 剔除
        "investigate_window_days": 90,
        "reject_big_insider_sell": True,    # 近3个月高管大额减持计划 → 剔除
        "insider_sell_window_days": 90,
        "require_2y_kcfjnc_positive": True, # 近2年扣非净利润均为正 (有数据时校验, 缺失跳过)
        # —— 基本面升级硬约束 (2026-08-29 估值陷阱拦截, 有数据时执行) ——
        "require_profit_positive": True,  # 最新期净利(扣非口径缺省归母) ≤0 → 剔除 (纸面盈利/亏损无支撑)
        "min_growth_pct": -10.0,          # 单季扣非净利润同比增速 ≥ -10% (不允许业绩崩盘式下滑)
        "max_peg": 2.0,                   # PEG = PE(TTM)/扣非增速 ≤ 2 (估值透支增长 → 剔除)
        # —— 加分项阈值 (满足越多评分越高) ——
        # 1. 盈利稳定性: 近 3 年扣非净利为正 + 经营活动现金流为正
        # 2. 估值低位: PE(TTM) 历史分位 < 30% (数据缺失时用 <行业 PE×0.7 近似)
        "pe_hist_low_pct": 30,            # PE 历史分位 < 30%
        # 3. 细分行业龙头: 行业内市值 Top20% → 龙头判定 (申万三级行业)
        "leader_top_pct": 20,
        # 4. 业绩拐点: 最新季报净利润同比 > 0 且 上一季 < 0 (转正) 或 最新季 > 上一季+20pct (加速)
        "earnings_turnaround_min_qoq": 20,
        # 5. 机构持仓: 北向/公募 (代码层面以 fina 股东数据或北向持有比例近似, 缺失跳过)
    },
    # —— 估值三层硬约束 (2026-08-29 新增, 置于 L1 超跌门槛之后、基本面 L2 之前) ——
    # 核心: 拦截「股价跌但业绩跌更快 → PE 被动抬高」的价值陷阱, 只留「估值杀到位、基本面没崩」的真错杀
    "L2_valuation": {
        "enabled": True,
        # 3.1 绝对 PE 分档上限 (按行业属性一票否决; 未匹配行业 → 全市场通用兜底)
        "pe_caps": {"周期": 25, "制造": 35, "科技成长": 50, "消费医药": 40, "通用": 60, "未知": 25},
        # 3.2 行业相对 PE 分位 (个股当前 PE 在自身近3年 PE-TTM 序列中的分位)
        "pe_hist_days_3y": 750,           # 3年 ≈ 750 交易日
        "pe_hist_days_1y": 250,           # 1年 ≈ 250 交易日
        "min_hist_len": 250,              # 历史不足该长度 → 跳过 3.2/3.3 (数据缺失不误杀, 只做绝对分档)
        "pe_pct_3y_max": 40,              # 分位 ≤40% 通过
        "pe_pct_3y_bonus": 20,            # 分位 ≤20% 显著低估 → 加分
        "pe_pct_3y_reject": 70,           # 分位 ≥70% → 剔除 (绝对PE不高但在行业内偏贵)
        # 3.3 估值回撤校验: 估值回撤幅度 ≥ 股价回撤幅度 × 0.7 (杀估值才是真超跌)
        "val_dd_ratio_min": 0.7,
        # 高 PE 降档: PE > 行业上限 × 0.8 → 置信度自动降一档 (未超上限但偏高)
        "pe_high_downgrade_ratio": 0.8,
        # 伪超跌排雷 2: 周期股 PE 历史分位极低 = 业绩顶点 → 周期下行, 剔除
        "cycle_pe_low_pct": 20,
        # 伪超跌排雷 1: 近1年区间振幅 ≥ 3 倍 → 题材爆炒后腰斩, 剔除
        "theme_pump_ratio": 3.0,
    },
    # —— 第三层 爆发前置信号识别 (技术面+资金面+催化 三向得分) ——
    "L3_signals": {
        # 2026-08-29 规范 v2: 技术(最多4项) + 资金(最多3项) + 催化(最多2项) 命中总数 ≥3 判定信号有效 (规范 4.x)
        "signal_hit_min": 3,
        "tech_cap": 4,
        "fund_cap": 3,
        "catal_cap": 2,
        # 技术子项: 各自阈值
        "macd_bullish_divergence": True,   # MACD 底背离 (价格新低但MACD未新低)
        "kdj_oversold_cross": 35,          # KDJ K值 < 35 然后金叉向上 (原25过严)
        "ma5_turning_up": True,            # 股价站稳5日均线且 5 日均线拐头向上 (斜率>0)
        "vol_ratio_amp": 1.2,              # 温和放量上涨: 量比 ≥1.2
        "break_descending_trend": True,    # 突破短期下降趋势线
        "break_platform_top": True,        # 突破低位震荡平台上沿
        "chip_concentration": True,        # 低位筹码峰集中 (近似用近20日振幅<近120日振幅)
        # 资金面: 3 项 (服务器上北向/龙虎榜数据常缺失, 命中数按实际数据计算, 缺数据不计分不否决)
        "main_flow_pos_days": 3,           # 近 N 日主力累计净流入且逐日放大
        "turnover_improve_lo": 1.0,        # 换手提升下限: 近5日均值/前20日均值 ≥ 1.0
        "turnover_improve_hi": 5.0,        # 换手提升上限: 近5日均值/前20日均值 ≤ 5.0 (在 1-5 区间视为温和提升到 3%~5%)
        "turnover_target_range": (3.0, 5.0),
        "north_increase_10d": True,        # 北向近 10 日逆势加仓 (缺失数据跳过不扣)
        "lhb_inst_net_buy": True,          # 龙虎榜机构专用席位净买入
        # 催化: ≥1 项加分 (任意1项)
        "sector_rps_recover_min": 70,      # 所属一级板块整体 RPS20 回升 ≥ 70 (从低位启动)
        "policy_industry": True,           # 行业有政策利好/供需改善信号 (通过 sw_industry 映射, 缺失跳过)
        "rotation_to_low": True,           # 市场风格切低位 (近似用高位RPS板块5日涨幅<0 且低位RPS板块5日涨幅>0)
    },
    # —— 第四层 综合评分权重 (25+25+25+25=100, 估值维度 2026-08-29 加入) ——
    "L4_score": {
        "w_oversold": 25,
        "w_fundamental": 25,
        "w_valuation": 25,
        "w_signals": 25,
        "min_total_score": 60,             # 总分 < 60 的不进入推荐列表
        "out_top_n": 2,                    # 每日输出 2 只 (规范 1.1, 可配置)
        # —— 置信度判定 (规范 5.2) ——
        "conf_high_total": 75,             # 总分≥75 且 信号≥5 → 高
        "conf_high_signals": 5,
        "conf_mid_total": 60,              # 总分60-74 且 信号3-4 → 中
        "conf_mid_signals": 3,
    },
}


# ============================================================
# 公共工具
# ============================================================
def _num(v):
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return float(v)
    except Exception:
        return None


def _log_row(row, stage, reason, log_list):
    log_list.append({
        "ts_code": row.get("ts_code", ""),
        "name": row.get("name", ""),
        "stage": stage,
        "reason": reason,
    })


# ============================================================
# 前置层: 大盘环境 (复用 check_market_env + 超跌专属规则)
# ============================================================
def _calc_advance_ratio(daily_map, date):
    """估算当日上涨占比 = (上涨家数+平盘×0.5)/(有交易家数)"""
    if date not in daily_map:
        return None
    df = daily_map[date]
    if df is None or len(df) == 0:
        return None
    if "pct_chg" in df.columns:
        col = df["pct_chg"]
    elif "changepercent" in df.columns:
        col = df["changepercent"]
    else:
        return None
    total = int((col.notna() & (df.get("vol", col).notna() if "vol" in df.columns else col.notna())).sum())
    if total == 0:
        return None
    up = int((col > 0).sum())
    flat = int((col == 0).sum())
    return round((up + flat * 0.5) / total, 4)


def _rebound_market_adjust(index_map, daily_map, latest_date, base_status):
    """基于 check_market_env 的基础状态, 应用超跌适配修正:
       - 暴跌市(连续3日普跌) → REJECT
       - 单边牛市(上涨占比高 + 强势板块数量多) → cap 输出 TOP_N → 1 (仅保留1只)
    返回 (final_is_safe, adjusted_status)
    当 CONFIG_REBOUND["run_mode"]["IGNORE_MARKET_ENV"]=True 时, 本函数恒返回 (True, SAFE 状态) —— 完全不看大盘.
    """
    # ============= 用户最新要求: 超跌潜力推荐 完全跳过大盘环境校验 =============
    if CONFIG_REBOUND.get("run_mode", {}).get("IGNORE_MARKET_ENV", False):
        safe_status = dict(base_status or {}) if base_status else {}
        safe_status["is_safe"] = True
        safe_status.setdefault("level", "pass")
        safe_status.setdefault("rule", "超跌策略: 已启用忽略大盘 (IGNORE_MARKET_ENV=True)")
        safe_status.setdefault("reason", "用户要求: 超跌潜力推荐 不看大盘环境, 任何市场状态下都出推荐")
        safe_status.setdefault("indexes", [])
        return True, safe_status
    # ============= 正常大盘校验分支 (保留, 用户后续改开关可随时切回) =============
    cfg = CONFIG_REBOUND["market"]
    status = dict(base_status or {})
    is_safe = status.get("is_safe", None)
    if is_safe is False:
        return False, status
    # —— 暴跌市检测: 最近 crash_days 交易日 ——
    if cfg.get("avoid_crash_mode", True):
        days = sorted([d for d in daily_map.keys() if d <= latest_date])[-cfg.get("crash_days", 3):]
        ratios = [_calc_advance_ratio(daily_map, d) for d in days]
        ratios = [r for r in ratios if r is not None]
        if len(ratios) >= 2:
            avg = sum(ratios) / len(ratios)
            consec_bad = all(r < 0.40 for r in ratios)
            if avg < cfg.get("crash_avg_ratio", 0.35) or consec_bad:
                status["is_safe"] = False
                status["reason"] = (status.get("reason", "") + " | 超跌策略触发暴跌市保护: 近3日上涨占比均值"
                                    + f"{round(avg*100,1)}%{'(连续3天<40%)' if consec_bad else ''}").strip(" |")
                status["level"] = "reject"
                return False, status
    return True, status


# ============================================================
# 回撤三分档: 大盘档位自动适配 (规范 2.2) + 数量兜底放宽 (规范 7.3)
# ============================================================
def _activate_tier(name):
    """把指定回撤档位写入 L1 激活配置 (drawdown_min_pct/drawdown_deep_pct/pre_drawdown_gate_pct).
    返回该档位 dict. 默认档 = standard (35%)."""
    cfg = CONFIG_REBOUND["L1_oversold"]
    tier = cfg["tiers"][name]
    cfg["active_tier"] = name
    cfg["drawdown_min_pct"] = tier["min"]
    cfg["drawdown_deep_pct"] = tier["deep"]
    cfg["pre_drawdown_gate_pct"] = tier["pre_gate"]
    return tier


def _select_tier(market_status, daily_map=None, latest_date=None):
    """大盘环境 → 自动选择回撤档位 (规范 2.2):
       - 连续3日普跌 (近3日上涨占比均值 < crash_avg_ratio_pause) → 暂停 (返回 pause=True)
       - 当日上涨占比 ≥ advance_ratio_strong → 强势 → 激进档(25%)
       - 当日上涨占比 < advance_ratio_weak   → 偏弱 → 保守档(45%)
       - 其他 / 数据缺失 → 震荡 → 标准档(35%, 默认)
    返回 dict {pause, tier, min, deep, pre_gate, market_label, advance_ratio, reason}
    注意: 即使 IGNORE_MARKET_ENV=True, 连续3日普跌暂停与档位自动适配仍然生效 (规范 2.2.4/3.2.1)。"""
    cfg_mkt = CONFIG_REBOUND["market"]
    cfg1 = CONFIG_REBOUND["L1_oversold"]
    strong_th = float(cfg_mkt.get("advance_ratio_strong", 0.60))
    weak_th = float(cfg_mkt.get("advance_ratio_weak", 0.42))
    crash_th = float(cfg_mkt.get("crash_avg_ratio_pause", 0.30))
    # 当日上涨占比 (优先用 market_status, 缺失则从 daily_map 现算)
    ar = None
    if isinstance(market_status, dict):
        ar = market_status.get("advance_ratio")
    if ar is None and daily_map is not None and latest_date is not None:
        ar = _calc_advance_ratio(daily_map, latest_date)
    # —— 连续3日普跌 → 暂停 (规范 2.2.4) ——
    if cfg_mkt.get("crash_pause_even_ignore", True) and daily_map is not None and latest_date is not None:
        days = sorted([d for d in daily_map.keys() if str(d) <= str(latest_date)])[-int(cfg_mkt.get("crash_days", 3)):]
        ratios = [_calc_advance_ratio(daily_map, d) for d in days]
        ratios = [r for r in ratios if r is not None]
        if len(ratios) >= 2:
            avg = sum(ratios) / len(ratios)
            if avg < crash_th:
                return {"pause": True, "tier": None, "min": None, "deep": None, "pre_gate": None,
                        "market_label": "crash",
                        "advance_ratio": ar,
                        "reason": f"连续{len(ratios)}日普跌(上涨占比均值{avg*100:.0f}%<{crash_th*100:.0f}%)"}
    # —— 档位判定 ——
    if ar is None:
        label = "steady"
    elif ar >= strong_th:
        label = "strong"
    elif ar < weak_th:
        label = "weak"
    else:
        label = "steady"
    tier_name = cfg1["tier_by_market"][label]
    tier = _activate_tier(tier_name)
    return {"pause": False, "tier": tier_name, "min": tier["min"], "deep": tier["deep"],
            "pre_gate": tier["pre_gate"], "market_label": label,
            "advance_ratio": ar, "reason": ""}


# ============================================================
# 下跌性质校验 (规范 2.3, 一票否决): 急跌错杀保留 / 阴跌创新低剔除
# ============================================================
def _is_slow_drift(closes, cfg=None):
    """下跌性质校验 → True = 阴跌(持续创新低, 基本面恶化型下跌) → 剔除
       - 高点距今 ≤ fast_days(40交易日≈2个月) → 快速下跌(情绪急跌错杀) → False 保留
       - 高点距今 > slow_days(60交易日≈3个月) 且 近 slow_days 最低 < 前 slow_days 最低×new_low_ratio
         且 近 slow_days 最高 ≤ 前 slow_days 最低×no_bounce_ratio (期间无反弹) → True 阴跌剔除
       - 数据不足(<120天) → False (不误杀)"""
    c = dict(CONFIG_REBOUND.get("L1_oversold", {}).get("fall_nature") or {})
    if cfg:
        c.update(cfg or {})
    if not c.get("enabled", True):
        return False
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    if n < 120:
        return False
    win = closes[-250:] if n >= 250 else closes
    if len(win) < 2:
        return False
    i_peak = int(np.nanargmax(win))
    days_since_peak = len(win) - 1 - i_peak
    fast_days = int(c.get("fast_days", 40))
    slow_days = int(c.get("slow_days", 60))
    if days_since_peak <= fast_days:
        return False  # 2个月内快速完成下跌 → 情绪急跌, 保留
    if len(win) < slow_days * 2:
        return False
    win_r = win[-slow_days:]
    win_p = win[-(slow_days * 2):-slow_days]
    lo_r = float(np.nanmin(win_r))
    lo_p = float(np.nanmin(win_p))
    hi_r = float(np.nanmax(win_r))
    if lo_r < lo_p * float(c.get("new_low_ratio", 0.97)) and hi_r <= lo_p * float(c.get("no_bounce_ratio", 1.02)):
        return True  # 3个月以上阴跌 + 持续创新低 + 无反弹 → 基本面恶化型下跌
    return False


# ============================================================
# 第一层: 深度超跌硬性门槛 (必须全部满足)
#  —— OOM 安全拆分:
#    L1_PART_A_LITE (60天日线即可): 价格≥2 / 成交额≥3000万 / 停牌剔除 / 数据≥60天
#    L1_PART_B_DRAWDOWN (只为候选抓长历史): 250天回撤≥当前档位阈值 + 10日不创新低 + 下跌性质校验
# ============================================================
def _ts_code_to_symbol(ts_code: str) -> str:
    """ts_code '600519.SH'/'000001.SZ' → 东财/AKShare 纯数字 symbol"""
    return ts_code.split(".")[0]

def _ts_code_to_tx_symbol(ts_code: str) -> str:
    """ts_code '600519.SH'/'000001.SZ'/'8xxxxx.BJ' → 腾讯接口前缀+数字 (sh/sz/bj)"""
    code, _, ex = ts_code.partition(".")
    ex = (ex or "").lower()
    if ex in ("sh", "sz", "bj"):
        return ex + code
    # 无后缀兜底: 6开头→sh, 0/3→sz, 4/8→bj
    if code.startswith(("6", "9")):
        return "sh" + code
    if code.startswith(("4", "8")):
        return "bj" + code
    return "sz" + code

def _fetch_long_panel_per_code(codes, latest_date_yyyymmdd: int,
                               calendar_days_back: int = 520,
                               max_workers: int = 4):
    """只为候选代码抓取 长周期 收盘价/最低价 数组(chronological oldest→most-recent).
    避免对 5500 只×280 天 做 DataFrame 字典导致 OOM (小服务器实测会被 kill).
    数据源降级链 (阿里云实测: 东财 push2his 被断连 RemoteDisconnected, 腾讯/新浪可用):
      stock_zh_a_hist(东财) → stock_zh_a_hist_tx(腾讯) → stock_zh_a_daily(新浪)
    PartB 只需要 close/low 算回撤+企稳; amount(成交额) 在 PartA 已用 60 天日线完成, 此处缺失不影响.
    返回: dict {ts_code: {'close': np.array[float32 M<=520], 'low': np.array[float32], 'amount': np.array[float32], 'n_days': int}}
    """
    try:
        import akshare as ak  # 延迟导入, 避免冷启动无 AKShare 时直接崩
    except Exception:
        ak = None

    dt_str = str(latest_date_yyyymmdd)
    end = f"{dt_str[:4]}{dt_str[4:6]}{dt_str[6:8]}"
    latest_dt = datetime(year=int(dt_str[:4]), month=int(dt_str[4:6]), day=int(dt_str[6:8]))
    start_dt = latest_dt - timedelta(days=calendar_days_back)
    start = start_dt.strftime("%Y%m%d")

    def _fetch_df(sym, sym_tx):
        """降级链抓取, 返回 (df, err)
        重要: 阿里云实测东财 push2his 会被墙(挂起或 RemoteDisconnected), 所以 **腾讯优先**,
        东财只作最后兜底(万一哪天服务器能连东财). 避免东财挂起占满 timeout 导致永远轮不到腾讯.
        """
        if ak is None:
            return None, "akshare_not_importable"
        em_err = tx_err = sina_err = "not_attempted"
        # 1) 腾讯 (需 sh/sz/bj 前缀, 列: date/open/close/high/low/volume/turnover/amount)
        try:
            df = ak.stock_zh_a_hist_tx(symbol=sym_tx, start_date=start,
                                       end_date=end, adjust="qfq")
            if df is not None and len(df) > 0:
                return df, None
            em_err = "tx_empty_rows"
        except Exception as e:
            tx_err = f"{type(e).__name__}:{str(e)[:60]}"
        # 2) 新浪 (英文列 date/open/high/low/close/volume)
        try:
            df = ak.stock_zh_a_daily(symbol=sym_tx, start_date=start,
                                     end_date=end, adjust="qfq")
            if df is not None and len(df) > 0:
                return df, None
            em_err = "sina_empty_rows"
        except Exception as e:
            sina_err = f"{type(e).__name__}:{str(e)[:60]}"
        # 3) 东财兜底 (纯数字; 阿里云上大概率被墙, 但保留以防腾讯/新浪失效)
        try:
            df = ak.stock_zh_a_hist(symbol=sym, period="daily",
                                    start_date=start, end_date=end, adjust="qfq")
            if df is not None and len(df) > 0:
                return df, None
            em_err = "em_empty_rows"
        except Exception as e:
            em_err = f"{type(e).__name__}:{str(e)[:60]}"
        return None, f"tx:[{tx_err}] sina:[{sina_err}] em:[{em_err}]"

    def _one(code: str):
        sym = _ts_code_to_symbol(code)
        sym_tx = _ts_code_to_tx_symbol(code)
        out = {"close": np.zeros(0, dtype=np.float32), "low": np.zeros(0, dtype=np.float32),
               "amount": np.zeros(0, dtype=np.float32), "n_days": 0, "err": None}
        try:
            # —— AKShare 接口防挂: 外层用 线程+timeout 套一层 (避免某数据源被限频后永久挂起) ——
            per_call_timeout = 8  # 秒/只, 覆盖整个降级链 (腾讯单测 <2s, 8s 已足够)
            with ThreadPoolExecutor(max_workers=1) as _one_ex:
                _fu = _one_ex.submit(_fetch_df, sym, sym_tx)
                try:
                    df, _err = _fu.result(timeout=per_call_timeout)
                except Exception as e:
                    out["err"] = f"ak_timeout({per_call_timeout}s):{type(e).__name__}"
                    return code, out
            if df is None or len(df) == 0:
                out["err"] = _err or "ak_empty_rows"
                return code, out
            # 统一列名 (兼容 东财中文 / 腾讯新浪英文)
            col_map = {}
            for raw_col in df.columns:
                k = str(raw_col).strip()
                if k in ("收盘", "收盘-不复权", "收盘价"): col_map["close"] = raw_col
                elif k in ("最低", "最低价"): col_map["low"] = raw_col
                elif k in ("成交额",): col_map["amount"] = raw_col
            if "close" not in col_map:
                for raw_col in df.columns:
                    k = str(raw_col).strip().lower()
                    if k in ("close",): col_map["close"] = raw_col
                    elif k in ("low",): col_map["low"] = raw_col
                    elif k in ("amount", "volume_amount"): col_map["amount"] = raw_col
            if "close" not in col_map:
                out["err"] = f"no_close_col:{list(df.columns)[:6]}"
                return code, out
            closes = pd.to_numeric(df[col_map["close"]], errors="coerce").to_numpy(dtype=np.float32)
            lows = (pd.to_numeric(df[col_map["low"]], errors="coerce").to_numpy(dtype=np.float32)
                    if "low" in col_map else np.copy(closes))
            # 成交额 单位: AKShare 东财是 元 → 换算成 千元 (与 rps_calc.daily_map.amount 单位一致: 千元)
            # 腾讯/新浪无成交额列 → zeros (PartB 不依赖 amount, 只依赖 close/low)
            if "amount" in col_map:
                amts_raw = pd.to_numeric(df[col_map["amount"]], errors="coerce").to_numpy(dtype=np.float64)
                amts = (amts_raw / 1000.0).astype(np.float32)
            else:
                amts = np.zeros(len(closes), dtype=np.float32)
            # 过滤 NaN 行（停牌等）按 close NaN 一起丢弃
            mask = ~np.isnan(closes)
            closes, lows, amts = closes[mask], lows[mask], amts[mask]
            if len(closes) == 0:
                out["err"] = "all_nan_after_mask"
                return code, out
            # 升序: 东财/腾讯 最新日期在最后, 新浪可能在最后; 保险起见按日期列排序
            date_col = None
            for raw_col in df.columns:
                k = str(raw_col).strip()
                if k in ("日期", "date"): date_col = raw_col; break
            if date_col is not None:
                try:
                    dates_str = pd.to_datetime(df[date_col], errors="coerce").to_numpy()
                    dates_str = dates_str[mask]
                    order = np.argsort(dates_str, kind="stable")
                    closes, lows, amts = closes[order], lows[order], amts[order]
                except Exception:
                    pass
            out["close"], out["low"], out["amount"] = closes, lows, amts
            out["n_days"] = len(closes)
            return code, out
        except Exception as e:
            out["err"] = f"{type(e).__name__}:{str(e)[:80]}"
            return code, out

    result = {}
    fail_cnt, ok_cnt = 0, 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_one, c): c for c in codes}
        for fut in as_completed(futs):
            c, v = fut.result()
            if v["n_days"] >= 60:
                ok_cnt += 1
            else:
                fail_cnt += 1
            result[c] = v
    print(f"  [长历史抓取] 请求 {len(codes)} 只 → 成功(≥60天) {ok_cnt}, 不足/失败 {fail_cnt}, 抓取范围 {start}→{end} (约{calendar_days_back}自然日)", flush=True)
    return result


def _screen_L1_partA_lite(rows, daily_by_code, latest_date, candidate_log=None):
    """60天日线可完成的硬性筛选 → 得到候选 (筛掉 90%+ 标的, 避免抓长历史)
    通过:
      (a) 硬性基础: 数据≥20天 / 最新价≥2 / 近20日日均成交额≥3000万 / 当日未停牌
      (b) 粗筛加速(新增): 用 59 天窗口 初步回撤 ≥ cfg.PRE_DRAWDOWN_PCT_GATE(默认22%) 才保留
         —— 只占全市场约 10%, 若粗筛不通过, 抓 250 天长历史也一定达不到 ≥50% (因为250天窗口回撤最大值必然≥59天窗口回撤最大值)
    250天回撤 & 10日不创新低 留到 PartB (那时只对本函数输出的候选 抓长历史)
    """
    cfg = CONFIG_REBOUND["L1_oversold"]
    min_days_needed = int(cfg["amount_avg_window"])  # 默认 20 (算近20日均额)
    pre_dd_gate = float(cfg.get("pre_drawdown_gate_pct", 25.0))  # 59天回撤粗筛阈值(25%), 拒绝 87% 不在下跌通道的票
    passed = []
    for r in rows:
        code = r["ts_code"]
        info = daily_by_code.get(code)
        n_days = len(info) if info is not None else 0
        if info is None or n_days < min_days_needed:
            if candidate_log is not None: _log_row(r, "L1_A_轻筛", f"日K不足(仅有{n_days}天, 需要≥{min_days_needed})", candidate_log)
            continue
        closes = info["close"].astype(float).values
        price_now = float(closes[-1])
        if price_now < cfg["min_price"]:
            if candidate_log is not None: _log_row(r, "L1_A_轻筛", f"最新价{price_now:.2f}<{cfg['min_price']}元", candidate_log)
            continue
        # 当日停牌 (vol==0)
        if "vol" in info.columns:
            vols = info["vol"].astype(float).values
            if len(vols) == 0 or pd.isna(vols[-1]) or float(vols[-1]) <= 0:
                if candidate_log is not None: _log_row(r, "L1_A_轻筛", "当日停牌/成交量0", candidate_log)
                continue
        # 近 20 日日均成交额 (amount 单位千元)
        if "amount" in info.columns:
            aw = cfg["amount_avg_window"]
            amts = info["amount"].astype(float).values[-aw:]
            amts = [a for a in amts if a is not None and not (isinstance(a, float) and np.isnan(a))]
            if amts:
                avg_amt = (sum(amts) / len(amts)) * 1000.0  # 千元 → 元
                if avg_amt < cfg["amount_avg_min"]:
                    if candidate_log is not None: _log_row(r, "L1_A_轻筛", f"近20日日均成交额{avg_amt/1e4:.0f}万<{cfg['amount_avg_min']/1e4:.0f}万", candidate_log)
                    continue
        # ========= PartA 新增: 59天窗口 粗筛回撤 (加速, 拒绝 85~90% 不在下跌通道的票) =========
        # 说明: 250天回撤最大值 >= 59天回撤最大值 (因为更长窗口包含更长时间的高点).
        #       若 59天回撤 < 22%, 则 250天回撤不可能 >= 50% (因为 59天窗口内最高点必然 < 现价×1.282, 即使250天有更高点, 也远不是50%那种级别的高)
        #       实际上对超跌≥50%股: 59天窗口内回撤通常 ≥25~35% (因为主跌段都在最近)
        #       所以粗筛 22% 能把 PartA 通过数从 4879 压到 ~500 只以内, AKShare 抓长历史 2分钟内搞定
        # 对 n_days < 60 的股, 仍用 min 天窗口做粗筛 (严格程度略降, 但不影响PartB再精确判定)
        closes_safe = closes[~np.isnan(closes)]
        if len(closes_safe) >= 10:
            hc_59 = float(np.nanmax(closes_safe))
            if hc_59 > 0:
                dd_59 = (hc_59 - price_now) / hc_59 * 100.0
                if dd_59 < pre_dd_gate:
                    if candidate_log is not None:
                        _log_row(r, "L1_A_轻筛",
                                 f"59天窗口粗回撤{dd_59:.1f}%<{pre_dd_gate:.0f}% (加速筛除非下跌通道股)",
                                 candidate_log)
                    continue
        passed.append(r)
    return passed


def _screen_L1_partB_drawdown(rows, long_panel, candidate_log=None):
    """仅对通过 PartA 的候选 (已有 long_panel) 做 250天回撤 + 10日不创新低 校验
    并写入 r['drawdown_pct'] / r['drawdown_high_close'] / r['deep_drawdown'] 这些下游需要的列
    """
    cfg = CONFIG_REBOUND["L1_oversold"]
    w = cfg["drawdown_window"]
    stable_days = cfg["stable_days"]
    stable_lb = cfg["stable_lookback"]
    passed = []
    for r in rows:
        code = r["ts_code"]
        p = long_panel.get(code)
        closes = None
        if p and p["n_days"] >= 60:
            closes = p["close"]
        if closes is None or len(closes) < 60:
            if candidate_log is not None: _log_row(r, "L1_B_回撤", f"长历史不足(仅{p['n_days'] if p else 0}天)", candidate_log)
            continue
        price_now = float(closes[-1])
        # 1) 250天窗口内最高收盘价 → 回撤≥50%
        window = closes[-w:] if len(closes) >= w else closes
        hc = float(np.nanmax(window))
        if hc <= 0:
            if candidate_log is not None: _log_row(r, "L1_B_回撤", "长历史最高收盘价异常", candidate_log)
            continue
        dd_pct = (hc - price_now) / hc * 100.0
        if dd_pct < cfg["drawdown_min_pct"]:
            if candidate_log is not None: _log_row(r, "L1_B_回撤", f"250天回撤{dd_pct:.1f}%<{cfg['drawdown_min_pct']}%", candidate_log)
            continue
        # 2) 10日不创新低 (基于 low 数组, 缺省用 close)
        lows = p["low"] if (p is not None and p["n_days"] == len(closes) and len(p["low"]) == len(closes)) else closes
        if len(lows) < stable_lb:
            stable_lb_local = len(lows) - stable_days
        else:
            stable_lb_local = stable_lb
        recent_lo = float(np.nanmin(lows[-stable_days:]))
        prev_end = len(lows) - stable_days
        prev_start = max(0, len(lows) - stable_lb_local)
        if prev_end > prev_start:
            prev_lo = float(np.nanmin(lows[prev_start:prev_end]))
            if recent_lo < prev_lo * 0.98:
                if candidate_log is not None: _log_row(r, "L1_B_回撤", f"近{stable_days}日仍创新低(前低{prev_lo:.2f}/近期低{recent_lo:.2f})", candidate_log)
                continue
        # 3) 下跌性质校验 (规范 2.3 一票否决): 3个月以上阴跌 + 持续创新低 + 无反弹 → 基本面恶化型下跌, 剔除
        if _is_slow_drift(closes):
            if candidate_log is not None: _log_row(r, "L1_B_回撤", "阴跌3个月+持续创新低+无反弹(下跌性质不合格)", candidate_log)
            continue
        # 通过 PartB → 把下游需要的列写入 r
        r["drawdown_pct"] = round(dd_pct, 2)
        r["drawdown_high_close"] = round(hc, 2)
        r["deep_drawdown"] = dd_pct >= cfg["drawdown_deep_pct"]
        # 伪超跌排雷1 用: 近1年区间振幅 = 近1年最高收盘 / 近1年最低低点 (题材爆炒后腰斩判定)
        _win_lows = lows[-w:] if len(lows) >= w else lows
        _lo_min = float(np.nanmin(_win_lows))
        r["_pump_ratio"] = round(float(np.nanmax(window)) / _lo_min, 2) if (_lo_min and _lo_min > 0) else None
        passed.append(r)
    return passed


def _screen_L1_oversold(rows, daily_by_code, latest_date, candidate_log=None):
    """遍历 rows 每股, 返回通过 L1 的 df. 规则:
       1) 区间最高收盘价回撤 ≥ 当前激活档位阈值 (25%/35%/45%, 见 tiers/active_tier)
       2) 近 stable_days(10) 个交易日没有创 stable_lookback(60) 日阶段新低
       3) 最新价 ≥ 2 元
       4) 近 20 日日均成交额 ≥ 3000 万
       5) 下跌性质校验: 阴跌3个月+持续创新低+无反弹 → 剔除 (规范 2.3)
    注意: 此函数用于 daily_by_code 已包含 ≥250 天数据的场景(本地/单元测试)
         生产服务器(仅60天日线字典)请走 PartA + PartB 两段式 以避免 OOM
    """
    cfg = CONFIG_REBOUND["L1_oversold"]
    w = cfg["drawdown_window"]
    stable_days = cfg["stable_days"]
    stable_lb = cfg["stable_lookback"]
    passed = []
    for r in rows:
        code = r["ts_code"]
        info = daily_by_code.get(code)
        if info is None or len(info) < 60:
            if candidate_log is not None: _log_row(r, "L1超跌门槛", f"日K数据不足(<60天, {len(info) if info is not None else 0})", candidate_log)
            continue
        closes = info["close"].astype(float).values
        # 最新价格
        price_now = float(closes[-1])
        if price_now < cfg["min_price"]:
            if candidate_log is not None: _log_row(r, "L1超跌门槛", f"最新价{price_now:.2f}<{cfg['min_price']}元", candidate_log)
            continue
        # 1) 近 w 日最高收盘价 → 回撤
        window = closes[-w:] if len(closes) >= w else closes
        hc = float(np.nanmax(window))
        if hc <= 0:
            if candidate_log is not None: _log_row(r, "L1超跌门槛", "区间最高收盘价异常", candidate_log)
            continue
        dd_pct = (hc - price_now) / hc * 100.0
        if dd_pct < cfg["drawdown_min_pct"]:
            if candidate_log is not None: _log_row(r, "L1超跌门槛", f"回撤{dd_pct:.1f}%<{cfg['drawdown_min_pct']}%", candidate_log)
            continue
        # 2) 近 stable_days(10) 日最低 > 前 stable_lb(60) 日最低 (不含近 stable_days 天) → 不再创新低
        if len(closes) < stable_lb:
            stable_lb_local = len(closes) - stable_days
        else:
            stable_lb_local = stable_lb
        recent_lo = float(np.nanmin(closes[-stable_days:]))
        # 之前 stable_lb_local 根 (不含最近 stable_days) 的最低价
        prev_start = max(0, len(closes) - stable_lb_local)
        prev_end = len(closes) - stable_days
        if prev_end <= prev_start:
            pass  # 数据太少, 默认通过
        else:
            prev_lo = float(np.nanmin(closes[prev_start:prev_end]))
            if recent_lo < prev_lo * 0.98:  # 允许2%误差, 否则视为创新低
                if candidate_log is not None: _log_row(r, "L1超跌门槛", f"近{stable_days}日仍创新低(前低{prev_lo:.2f}/近期低{recent_lo:.2f})", candidate_log)
                continue
        # 2b) 下跌性质校验 (规范 2.3 一票否决): 阴跌3个月以上+持续创新低+无反弹 → 剔除
        if _is_slow_drift(closes):
            if candidate_log is not None: _log_row(r, "L1超跌门槛", "阴跌3个月+持续创新低+无反弹(下跌性质不合格)", candidate_log)
            continue
        # 4) 近 20 日日均成交额 ≥ 3000 万 (amount 单位千元 → ×1000)
        if "amount" in info.columns:
            amts = info["amount"].astype(float).values[-cfg["amount_avg_window"]:]
            amts = [a for a in amts if a is not None and not (isinstance(a, float) and np.isnan(a))]
            if amts:
                avg_amt = (sum(amts) / len(amts)) * 1000.0  # 千元 → 元
                if avg_amt < cfg["amount_avg_min"]:
                    if candidate_log is not None: _log_row(r, "L1超跌门槛", f"近20日日均成交额{avg_amt/1e4:.0f}万<{cfg['amount_avg_min']/1e4:.0f}万", candidate_log)
                    continue
        # 通过 L1 → 补充计算列
        r["drawdown_pct"] = round(dd_pct, 2)
        r["drawdown_high_close"] = round(hc, 2)
        r["deep_drawdown"] = dd_pct >= cfg["drawdown_deep_pct"]
        # 伪超跌排雷1 用: 近1年区间振幅 (用收盘价近似最高/最低) — 题材爆炒后腰斩判定
        _win_c = closes[-w:] if len(closes) >= w else closes
        _lo_min = float(np.nanmin(_win_c))
        r["_pump_ratio"] = round(float(np.nanmax(window)) / _lo_min, 2) if (_lo_min and _lo_min > 0) else None
        passed.append(r)
    return passed


# ============================================================
# 估值层 (L1 之后、基本面 L2 之前): 估值三层硬约束 + 伪超跌排雷
# ============================================================
# 行业属性 → 估值分桶 (申万三级/一级行业名关键词匹配, 顺序敏感: 周期→科技成长→消费医药→制造)
_INDUSTRY_PE_CAP_KEYWORDS = [
    ("周期", ["煤炭", "石油", "石化", "化工", "化纤", "化学", "钢铁", "有色", "金属", "稀土", "贵金属",
              "锂矿", "建材", "水泥", "玻璃", "焦炭", "橡胶", "塑料", "磷", "氟", "燃气", "航运", "港口",
              "油服", "油气", "采掘", "火电", "水电", "房地产", "地产", "公用事业"]),
    ("科技成长", ["电子", "半导体", "芯片", "集成电路", "计算机", "软件", "通信", "互联网", "传媒", "游戏",
                 "网络", "信息", "数据", "云", "光电", "光学", "元件", "面板", "消费电子", "智能", "AI",
                 "人工智能", "机器人", "安防", "虚拟现实"]),
    ("消费医药", ["医药", "生物", "药", "食品", "饮料", "白酒", "啤酒", "乳", "调味", "休闲食品", "零售",
                 "商贸", "商业", "农业", "养殖", "畜禽", "种植", "渔业", "餐饮", "旅游", "酒店", "免税",
                 "服装", "纺织", "家纺", "家用电器", "家电", "厨电", "家居", "家具", "美容", "护理", "教育",
                 "医疗服务", "疫苗", "中药"]),
    ("制造", ["机械", "设备", "汽车", "整车", "零部件", "汽配", "轻工", "包装", "造纸", "军工", "国防",
              "航空", "航天", "船舶", "电气", "电力设备", "新能源", "光伏", "风电", "储能", "电池", "锂电",
              "充电桩", "特高压", "电网", "电机", "自动化", "仪器", "仪表", "机床", "刀具", "轴承", "液压",
              "金属制品", "重卡", "工程机械", "轨交"]),
]


def _industry_pe_cap(industry):
    """申万行业名 → (估值分桶, PE 上限)。未匹配 → ('通用', 60)。
    行业缺失(空/None, 如申万分类未收录) → ('未知', 周期档25): 无法判断属性时按最严档
    从严拦截, 防止"行业缺失→套通用60上限"漏放高PE伪超跌股 (例: 晶华新材603683 化工却无行业→PE51漏放)。"""
    ind = str(industry or "").strip()
    caps = CONFIG_REBOUND["L2_valuation"]["pe_caps"]
    if not ind:
        return "未知", caps.get("未知", caps["周期"])
    for bucket, kws in _INDUSTRY_PE_CAP_KEYWORDS:
        for kw in kws:
            if kw in ind:
                return bucket, caps.get(bucket, caps["通用"])
    return "通用", caps["通用"]


def _fetch_pe_hist_per_code(codes, latest_date, calendar_days_back=800, max_workers=8):
    """为候选代码抓取 PE-TTM 历史序列 (近3年, 用于 PE 分位 + 估值回撤 校验).
    数据源降级链: 东财 RPT_VALUEANALYSIS_DET(个股估值历史, 快且稳定)
                → Tushare daily_basic(ts_code区间) → AKShare stock_a_indicator_lg(乐咕乐股).
    磁盘缓存 static/pe_hist_cache.json: {ts_code: {"fetched_date", "dates", "pe"}}
    返回 {ts_code: {"dates": [...], "pe": [...]}}; 单只全失败 → 从返回中剔除
    (上层按「数据缺失」跳过 3.2/3.3 硬约束, 不误杀)
    """
    cache = {}
    if PE_HIST_CACHE_FILE.exists():
        try:
            with open(PE_HIST_CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    end = str(latest_date)
    start = (datetime.now() - timedelta(days=calendar_days_back)).strftime("%Y%m%d")
    need = []
    for c in codes:
        ent = cache.get(c)
        if ent and ent.get("fetched_date") == end and len(ent.get("dates", [])) >= 60:
            continue
        need.append(c)
    if need:
        print(f"  [PE历史] 待抓取 {len(need)} 只 (缓存命中 {len(codes) - len(need)})")

        def _one(c):
            hist = None
            # 0) 东财 RPT_VALUEANALYSIS_DET 个股估值历史 (首选: 服务器实测可用, 0.2s/只)
            try:
                import requests as _req
                code6, mkt = c.split(".")[0], c.split(".")[1]
                secu = f"{code6}.{mkt}"
                rows_all = []
                for _pn in range(1, 4):  # 最多3页 (500×3=1500交易日, 覆盖近3年)
                    _url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
                            "?reportName=RPT_VALUEANALYSIS_DET"
                            "&columns=SECUCODE,TRADE_DATE,PE_TTM"
                            f"&filter=(SECUCODE=%22{secu}%22)"
                            f"&pageSize=500&pageNo={_pn}"
                            "&sortColumns=TRADE_DATE&sortTypes=-1")
                    _d = _req.get(_url, timeout=15,
                                  headers={"User-Agent": "Mozilla/5.0"}).json()
                    _data = ((_d.get("result") or {}).get("data") or [])
                    if not _data:
                        break
                    rows_all.extend(_data)
                    if len(_data) < 500:
                        break
                if rows_all:
                    _pairs = []
                    for _row in rows_all:
                        _td = str(_row.get("TRADE_DATE") or "")
                        _d10 = _td[:10].replace("-", "")
                        if start <= _d10 <= end:
                            _pv = _row.get("PE_TTM")
                            if _pv is not None:
                                try:
                                    _fv = float(_pv)
                                    if _fv > 0:
                                        _pairs.append((_d10, _fv))
                                except Exception:
                                    pass
                    if len(_pairs) >= 60:
                        _pairs.sort()  # 日期升序
                        hist = {"dates": [p[0] for p in _pairs], "pe": [p[1] for p in _pairs]}
            except Exception:
                hist = None
            # 1) Tushare daily_basic 区间 (token 无权限则快速失败 → AKShare 兜底)
            if not hist:
                try:
                    df = pool.call_any("daily_basic", ts_code=c, start_date=start, end_date=end)
                    if df is not None and len(df) > 0 and "pe_ttm" in df.columns:
                        rows = df[["trade_date", "pe_ttm"]].dropna(subset=["pe_ttm"])
                        rows = rows[[float(v) > 0 for v in rows["pe_ttm"]]]
                        hist = {"dates": [str(d) for d in rows["trade_date"]],
                                "pe": [float(v) for v in rows["pe_ttm"]]}
                except Exception:
                    hist = None
            # 2) AKShare 乐咕乐股 PE-TTM 历史
            if not hist:
                try:
                    import akshare as _ak
                    code6 = c.split(".")[0]
                    df = _ak.stock_a_indicator_lg(symbol=code6)
                    if df is not None and len(df) > 0 and "pe_ttm" in df.columns:
                        rows = df[["trade_date", "pe_ttm"]].dropna()
                        rows = rows[[float(v) > 0 for v in rows["pe_ttm"]]]
                        dates = [str(d)[:10].replace("-", "") for d in rows["trade_date"]]
                        keep = [i for i, d in enumerate(dates) if start <= d <= end]
                        hist = {"dates": [dates[i] for i in keep],
                                "pe": [float(rows.iloc[i]["pe_ttm"]) for i in keep]}
                except Exception:
                    hist = None
            return c, hist

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_one, c): c for c in need}
            for f in as_completed(futs):
                c = futs[f]
                try:
                    c_, hist = f.result()
                    if hist and len(hist["pe"]) >= 60:
                        hist["fetched_date"] = end
                        cache[c_] = hist
                    else:
                        cache.pop(c_, None)
                except Exception:
                    cache.pop(c, None)
        try:
            with open(PE_HIST_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, allow_nan=False)
        except Exception:
            pass
    codes_set = set(codes)
    return {c: {"dates": v.get("dates", []), "pe": v.get("pe", [])}
            for c, v in cache.items() if c in codes_set and v.get("pe")}


def _screen_L2_valuation(rows, pe_hist_map, fina_map=None, candidate_log=None):
    """估值三层硬约束 (置于 L1 之后、基本面 L2 之前):
      3.1 绝对PE分档: PE > 行业上限 → 一票否决 (PE 缺失/亏损 → 用财务确认: 亏损直接剔除)
      3.2 行业相对PE分位: 近3年分位 ≥70% → 剔除; ≤40% 通过 (≤20% 加分)
      3.3 估值回撤: 1-(当前PE/近1年最高PE) < 股价回撤×0.7 → 剔除 (杀业绩非杀估值)
      + 伪超跌排雷: 题材爆炒腰斩(_pump_ratio≥3) / 周期顶点回落(周期股PE历史低位=业绩顶)
      + 亏损股拦截: PE 无效(≤0/缺失) 且 财务确认最新期净利≤0 → 剔除 (价值陷阱: 盈利无支撑)
      输出每行: valuation_score(0-100) / valuation_items / pe_bucket / pe_cap /
                pe_pct_3y / pe_dd_pct / _pe_high_flag
    """
    cfg = CONFIG_REBOUND["L2_valuation"]
    if not cfg.get("enabled", True):
        for r in rows:
            r["valuation_score"] = 50
            r["valuation_items"] = ["估值层未启用"]
            r["pe_bucket"] = _industry_pe_cap(r.get("industry", ""))[0]
            r["pe_cap"] = _industry_pe_cap(r.get("industry", ""))[1]
        return rows
    passed = []
    for r in rows:
        code = r["ts_code"]
        pe = _num(r.get("pe_ttm"))
        bucket, cap = _industry_pe_cap(r.get("industry", ""))
        r["pe_bucket"] = bucket
        r["pe_cap"] = cap
        items = []
        # —— 3.1 绝对 PE 分档 (一票否决) ——
        if pe is not None and pe > 0:
            if pe > cap:
                if candidate_log is not None:
                    _log_row(r, "L2估值", f"PE {pe:.1f} > {bucket}上限{cap}", candidate_log)
                continue
            r["_pe_high_flag"] = pe > cap * cfg.get("pe_high_downgrade_ratio", 0.8)
            items.append(f"PE {pe:.1f} ≤ {bucket}上限{cap}")
        else:
            # PE 无效(亏损/缺失): 用财务确认是否真亏损 → 亏损直接剔除 (价值陷阱)
            # 财务也缺失 → 放行, 由基本面层统一拦截"财务+估值双缺失"
            _fr = (fina_map or {}).get(code) if fina_map else None
            _np = None
            if _fr is not None:
                _np = _num(_fr.get("kcfjnc"))
                if _np is None:
                    _np = _num(_fr.get("net_profit"))
                if _np is not None and _np <= 0:
                    if candidate_log is not None:
                        _log_row(r, "L2估值", f"亏损股(净利{_np:.2f}亿) PE无效 → 价值陷阱", candidate_log)
                    continue
            items.append("PE无效(基本面层校验)" + ("" if _np is None else "净利为正"))
        # —— 3.2 + 3.3 (需 PE 历史) ——
        hist = pe_hist_map.get(code) or {}
        pe_series = hist.get("pe") or []
        pct3y = None
        pe_dd = None
        if pe is not None and pe > 0 and len(pe_series) >= cfg["min_hist_len"]:
            win3 = pe_series[-cfg["pe_hist_days_3y"]:]
            win1 = pe_series[-cfg["pe_hist_days_1y"]:]
            # 3.2 行业相对 PE 分位 (自身近3年)
            below = sum(1 for v in win3 if v <= pe)
            pct3y = round(below / len(win3) * 100, 1)
            r["pe_pct_3y"] = pct3y
            if pct3y >= cfg["pe_pct_3y_reject"]:
                if candidate_log is not None:
                    _log_row(r, "L2估值", f"PE分位{pct3y:.0f}% ≥ {cfg['pe_pct_3y_reject']}% (行业内偏贵)", candidate_log)
                continue
            if pct3y <= cfg["pe_pct_3y_bonus"]:
                items.append(f"PE近3年分位{pct3y:.0f}% (显著低估)")
            elif pct3y <= cfg["pe_pct_3y_max"]:
                items.append(f"PE近3年分位{pct3y:.0f}% (低估区)")
            # 伪超跌排雷 2: 周期股 PE 分位极低 = 业绩顶点 → 周期下行
            if bucket == "周期" and pct3y <= cfg.get("cycle_pe_low_pct", 20):
                if candidate_log is not None:
                    _log_row(r, "L2估值", f"周期股PE分位{pct3y:.0f}%≤{cfg.get('cycle_pe_low_pct')}% (周期顶点回落)", candidate_log)
                continue
            # 3.3 估值回撤校验
            pe_max1y = float(max(win1))
            if pe_max1y > 0:
                pe_dd = 1.0 - pe / pe_max1y
                price_dd = float(r.get("drawdown_pct") or 0) / 100.0
                r["pe_dd_pct"] = round(pe_dd * 100, 1)
                if price_dd > 0 and pe_dd < price_dd * cfg["val_dd_ratio_min"]:
                    if candidate_log is not None:
                        _log_row(r, "L2估值",
                                 f"估值回撤{pe_dd*100:.0f}% < 股价回撤{price_dd*100:.0f}%×{cfg['val_dd_ratio_min']} (杀业绩非杀估值)",
                                 candidate_log)
                    continue
                items.append(f"估值回撤{pe_dd*100:.0f}%≥股价×{cfg['val_dd_ratio_min']}")
        # 伪超跌排雷 1: 题材爆炒后腰斩 (近1年区间振幅 ≥ theme_pump_ratio 倍)
        pump = _num(r.get("_pump_ratio"))
        if pump is not None and pump >= cfg.get("theme_pump_ratio", 3.0):
            if candidate_log is not None:
                _log_row(r, "L2估值", f"1年区间振幅{pump:.1f}倍 ≥ {cfg['theme_pump_ratio']} (题材爆炒后腰斩)", candidate_log)
            continue
        # —— 估值性价比评分 (0-100) ——
        val_score = 40.0
        if pe is not None and pe > 0:
            if pe <= cap * 0.5:
                val_score += 15
                items.append(f"PE≤上限50%({cap*0.5:.0f})")
            if pct3y is not None:
                if pct3y <= 20:
                    val_score += 25
                elif pct3y <= 40:
                    val_score += 15
                elif pct3y <= 70:
                    val_score += 5
            if pe_dd is not None:
                price_dd = float(r.get("drawdown_pct") or 0) / 100.0
                if pe_dd >= price_dd:
                    val_score += 20
                elif pe_dd >= price_dd * cfg["val_dd_ratio_min"]:
                    val_score += 10
        r["valuation_score"] = int(min(100, val_score))
        r["valuation_items"] = items
        passed.append(r)
    return passed


# ============================================================
# 第二层: 基本面优质验证 (排雷 + 加分项)
# ============================================================
def _screen_L2_fundamental(rows, fina_map, latest_date, daily_map, candidate_log=None, risk_map=None):
    """排雷 (满足任意一条 → 剔除) + 基本面加分项计算 (输出到每行 fundamental_score / 加分项标签列表)
    数据缺失不剔除, 仅相应加分不给
    risk_map: {ts_code: [公告标题,...]} 近3个月风险公告 (立案/调查/减持/解禁) — None/缺失 → 不校验 (规范 3.1, 数据缺失不剔除)
    """
    cfg = CONFIG_REBOUND["L2_fundamental"]
    passed = []
    # —— 流通市值行业分位 (龙头判定用) ——
    latest_df = daily_map.get(latest_date)
    mv_by_industry = {}  # industry -> list[circ_mv 万元]
    if latest_df is not None and "industry" in latest_df.columns and "circ_mv" in latest_df.columns:
        try:
            grouped = latest_df.groupby("industry")["circ_mv"].apply(lambda s: sorted([float(x) for x in s if pd.notna(x) and float(x) > 0], reverse=True))
            mv_by_industry = dict(grouped)
        except Exception:
            mv_by_industry = {}
    for r in rows:
        code = r["ts_code"]
        # 共用: ST 排雷
        if is_st_stock(r.get("name", "")):
            if candidate_log is not None: _log_row(r, "L2基本面", "ST/*ST/退市", candidate_log)
            continue
        # 停牌: 当日 vol=0
        if r.get("vol", 0) is None or pd.isna(r.get("vol", 0)) or float(r.get("vol", 0) or 0) <= 0:
            if candidate_log is not None: _log_row(r, "L2基本面", "当日停牌", candidate_log)
            continue
        # fina_map 基本面排雷
        fr = fina_map.get(code) if fina_map else None
        rej_reason = None
        if fr is not None:
            # 连续亏损扩: 最新两期净利润(扣非)都<0 且 |最新|>|上期| (亏损扩大)
            if cfg.get("reject_consecutive_loss_widen", True):
                np_cur = _num(fr.get("net_profit"))
                np_prev = _num(fr.get("prev_net_profit"))  # fetch_fina_* 统一用 prev_net_profit 存上期
                kp_cur = _num(fr.get("kcfjnc"))  # 扣非净利润
                if kp_cur is None:
                    kp_cur = np_cur
                kp_prev = _num(fr.get("kcfjnc_prev"))
                if kp_prev is None:
                    kp_prev = np_prev
                if kp_cur is not None and kp_prev is not None and kp_cur < 0 and kp_prev < 0 and abs(kp_cur) > abs(kp_prev):
                    rej_reason = f"连续两期扣非亏损且扩大({kp_prev:.2f} → {kp_cur:.2f})"
            if rej_reason is None:
                debt = _num(fr.get("debt_to_assets"))
                if debt is not None and debt > cfg["max_debt_ratio"]:
                    rej_reason = f"资产负债率{debt:.1f}%>{cfg['max_debt_ratio']}%"
            if rej_reason is None:
                gwl = _num(fr.get("goodwill_ratio"))
                if gwl is not None and gwl > cfg["max_goodwill_ratio"]:
                    rej_reason = f"商誉/净资产{gwl:.1f}%>{cfg['max_goodwill_ratio']}%"
            if rej_reason is None:
                plg = _num(fr.get("pledge_ratio"))
                if plg is not None and plg > cfg["max_pledge_ratio"]:
                    rej_reason = f"股权质押{plg:.1f}%>{cfg['max_pledge_ratio']}%"
            # —— 基本面升级硬约束 (2026-08-29 估值陷阱拦截, 数据缺失不剔除) ——
            if rej_reason is None and cfg.get("require_profit_positive", True):
                kp_ok = _num(fr.get("kcfjnc"))
                if kp_ok is None:
                    kp_ok = _num(fr.get("net_profit"))
                if kp_ok is not None and kp_ok <= 0:
                    rej_reason = f"最新期净利≤0({kp_ok:.2f}亿, 盈利无支撑)"
            if rej_reason is None:
                _ocf = _num(fr.get("net_cash_flows_oper_act"))
                if _ocf is not None and _ocf <= 0:
                    rej_reason = f"经营现金流≤0({_ocf:.2f}亿, 纸面盈利)"
            if rej_reason is None:
                _grow = _num(fr.get("dt_netprofit_yoy"))   # 扣非净利同比优先
                if _grow is None:
                    _grow = _num(fr.get("netprofit_yoy"))  # 缺省归母口径
                if _grow is not None and _grow < cfg.get("min_growth_pct", -10.0):
                    rej_reason = f"扣非增速{_grow:.0f}%<{cfg.get('min_growth_pct'):.0f}% (业绩崩盘式下滑)"
            if rej_reason is None:
                _pe_v = _num(r.get("pe_ttm"))
                _grow2 = _num(fr.get("dt_netprofit_yoy"))
                if _grow2 is None:
                    _grow2 = _num(fr.get("netprofit_yoy"))
                if _pe_v is not None and _pe_v > 0 and _grow2 is not None and _grow2 > 0:
                    _peg = _pe_v / _grow2
                    if _peg > cfg.get("max_peg", 2.0):
                        rej_reason = f"PEG {_peg:.1f}=PE{_pe_v:.1f}/增速{_grow2:.0f}%>{cfg.get('max_peg')} (估值透支增长)"
            # —— 近2期扣非净利润均为正 (规范 3.2.1; 有数据校验, 缺失跳过) ——
            if rej_reason is None and cfg.get("require_2y_kcfjnc_positive", True):
                _kp_now = _num(fr.get("kcfjnc"))
                if _kp_now is None:
                    _kp_now = _num(fr.get("n_income_attr_parent"))  # 东财F10 扣非净利字段
                if _kp_now is None:
                    _kp_now = _num(fr.get("net_profit"))
                _kp_prev2 = _num(fr.get("kcfjnc_prev"))
                if _kp_prev2 is None:
                    _kp_prev2 = _num(fr.get("prev_net_profit"))
                if _kp_now is not None and _kp_now <= 0:
                    rej_reason = f"最新扣非净利≤0({_kp_now:.2f}亿)"
                elif _kp_now is not None and _kp_prev2 is not None and _kp_prev2 <= 0:
                    rej_reason = f"上期扣非净利≤0({_kp_prev2:.2f}亿)"
        # —— 公告风险事件排雷 (规范 3.1: 立案/调查/减持/解禁; risk_map 缺失 → 不剔除) ——
        if rej_reason is None and risk_map is not None and code in risk_map:
            _rhits = [t for t in risk_map[code] if t]
            if cfg.get("reject_investigate_recent", True) and any(("立案" in t or "调查" in t) for t in _rhits):
                rej_reason = f"近{cfg.get('investigate_window_days', 90)}天立案/调查公告: {_rhits[0]}"
            elif cfg.get("reject_big_insider_sell", True) and any("减持" in t for t in _rhits):
                rej_reason = f"近{cfg.get('insider_sell_window_days', 90)}天减持公告: {_rhits[0]}"
            elif cfg.get("reject_big_unlock_recent", True) and any("解禁" in t for t in _rhits):
                rej_reason = f"近{cfg.get('big_unlock_window_days', 90)}天解禁公告: {_rhits[0]}"
        if rej_reason:
            if candidate_log is not None: _log_row(r, "L2基本面", rej_reason, candidate_log)
            continue
        # —— 财务+估值双缺失拦截: 无财务数据 且 PE 无效(亏损) → 无法验证基本面, 宁缺毋滥 ——
        # (PE 有效且已通过估值层 = 盈利已被估值验证, 放行, 下方兜底给分)
        if fr is None:
            _pe_v = _num(r.get("pe_ttm"))
            if _pe_v is None or _pe_v <= 0:
                if candidate_log is not None:
                    _log_row(r, "L2基本面", "财务+估值双缺失, 无法验证基本面", candidate_log)
                continue
        # —— 加分项计算 (满分 100, 输出 fundamental_score) ——
        score_items = []
        bonus = 0.0
        # B1. 盈利稳定性: 扣非净利为正 + 经营现金流为正
        if fr is not None:
            kp = _num(fr.get("kcfjnc"))
            if kp is None: kp = _num(fr.get("net_profit"))
            ocf = _num(fr.get("net_cash_flows_oper_act"))
            if kp is not None and kp > 0:
                bonus += 15
                score_items.append("扣非净利为正")
            if ocf is not None and ocf > 0:
                bonus += 10
                score_items.append("经营现金流为正")
        else:
            # 财务缺失: PE 有效(已通过估值层=盈利验证) → 用估值验证兜底给中位分,
            # 避免"数据缺失系统性压分"导致 L4 总分被拖垮 (2026-08-29)
            pe = r.get("pe_ttm")
            if pe is not None and not pd.isna(pe) and float(pe) > 0:
                bonus += 45
                score_items.append("盈利PE>0(估值已验证)")
        # B2. 估值低位: PE(TTM) 行业内历史分位<30% → 简化: PE<行业同市值平均的70%
        pe_val = _num(r.get("pe_ttm"))
        industry = str(r.get("industry") or "")
        if pe_val is not None and pe_val > 0 and industry in mv_by_industry:
            # 粗糙近似: PE<行业平均 PE×0.7
            try:
                peers = []
                for _, row2 in latest_df[latest_df["industry"] == industry].iterrows():
                    p = _num(row2.get("pe_ttm"))
                    if p is not None and 0 < p < 300:
                        peers.append(p)
                if peers:
                    med_pe = sorted(peers)[len(peers) // 2]
                    if pe_val <= med_pe * 0.7:
                        bonus += 15
                        score_items.append(f"PE低于行业中位×0.7({pe_val:.1f} vs 中位{med_pe:.1f})")
                    elif pe_val <= med_pe:
                        bonus += 8
                        score_items.append(f"PE低于行业中位({pe_val:.1f} vs {med_pe:.1f})")
            except Exception:
                pass
        # B3. 细分行业龙头: 该申万三级行业内, 流通市值 Top20%
        cmv = _num(r.get("circ_mv"))  # 万元
        if cmv is not None and industry in mv_by_industry:
            lst = mv_by_industry[industry]
            if len(lst) >= 5:
                top_pos = int(len(lst) * (cfg["leader_top_pct"] / 100.0))
                if top_pos < 1: top_pos = 1
                if cmv >= lst[min(top_pos - 1, len(lst) - 1)]:
                    bonus += 20
                    score_items.append("行业市值龙头(Top20%)")
                elif cmv >= lst[min(4, len(lst) - 1)]:
                    bonus += 10
                    score_items.append("行业头部(Top5)")
        # B4. 业绩拐点: 最新季报净利同比 > 0 且 上一季 < 0 → 拐点
        if fr is not None:
            yoy_cur = _num(fr.get("yoy_sales")) or _num(fr.get("tr_yoy")) or _num(fr.get("dt_netprofit_yoy"))
            if yoy_cur is None:
                # 尝试估算: 从 扣非 - 上期
                yoy_cur = _num(fr.get("netprofit_yoy")) or _num(fr.get("profit_yoy"))
            if yoy_cur is not None and yoy_cur > 0:
                bonus += 15
                score_items.append(f"业绩拐点+{yoy_cur:.0f}%")
            elif yoy_cur is not None and yoy_cur > -10:
                bonus += 5
                score_items.append("业绩接近转正")
        # B5. 机构持仓: 股东总数下降(筹码集中)或北向/公募 (数据缺失跳过)
        if fr is not None:
            sh = _num(fr.get("holders_num"))
            sh_prev = _num(fr.get("holders_num_prev"))
            if sh is not None and sh_prev is not None and sh < sh_prev * 0.98:
                bonus += 10
                score_items.append("股东户数下降(筹码集中)")
            elif sh is not None and sh_prev is not None and sh < sh_prev:
                bonus += 5
                score_items.append("股东户数微降")
        # → 换算为 0-100 (最多可能超过100, 所以 cap 100)
        fund_score = int(min(100, bonus))
        r["fundamental_score"] = fund_score
        r["fundamental_items"] = score_items
        passed.append(r)
    return passed


# ============================================================
# 技术指标数组版 (本地实现, 供 L3 使用)
# 注意: recommend.calc_macd/calc_kdj/calc_ma_series 返回 最近值/None,
#       L3 需要完整数组, 故此处自行实现数组版.
# ============================================================
def _ma_arr(closes, n):
    """MA n 期 → 与 closes 等长数组, 前 n-1 个为 NaN"""
    closes = np.asarray(closes, dtype=float)
    length = len(closes)
    out = np.full(length, np.nan)
    if length < n:
        return out
    for i in range(n - 1, length):
        out[i] = float(np.nanmean(closes[i - n + 1:i + 1]))
    return out


def _macd_arr(closes, fast=12, slow=26, signal=9):
    """MACD → (dif, dea, hist) 三个与 closes 等长的 numpy 数组"""
    closes = np.asarray(closes, dtype=float)
    if len(closes) < 2:
        n = len(closes)
        return (np.zeros(n), np.zeros(n), np.zeros(n))
    s = pd.Series(closes)
    ema_f = s.ewm(span=fast, adjust=False).mean()
    ema_s = s.ewm(span=slow, adjust=False).mean()
    dif = (ema_f - ema_s).to_numpy(dtype=float)
    dea = pd.Series(dif).ewm(span=signal, adjust=False).mean().to_numpy(dtype=float)
    hist = (dif - dea) * 2.0
    return dif, dea, hist


def _kdj_arr(highs, lows, closes, n=9):
    """KDJ(9,3,3) → (k, d, j) 三个与 closes 等长的 numpy 数组"""
    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float)
    closes = np.asarray(closes, dtype=float)
    length = len(closes)
    k = d = 50.0
    k_arr = np.zeros(length)
    d_arr = np.zeros(length)
    j_arr = np.zeros(length)
    for i in range(length):
        if i >= n - 1:
            hh = float(np.nanmax(highs[i - n + 1:i + 1]))
            ll = float(np.nanmin(lows[i - n + 1:i + 1]))
            rsv = 50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100.0
        else:
            rsv = 50.0
        k = k * 2.0 / 3.0 + rsv / 3.0
        d = d * 2.0 / 3.0 + k / 3.0
        j = 3.0 * k - 2.0 * d
        k_arr[i], d_arr[i], j_arr[i] = k, d, j
    return k_arr, d_arr, j_arr


# ============================================================
# 第三层: 爆发前置信号识别
# ============================================================
def _screen_L3_signals(rows, daily_by_code, latest_date, mflow_map, sector_rps_map, candidate_log=None):
    """技术(≤4项) + 资金(≤3项) + 催化(≤2项) 命中总数≥signal_hit_min(3) → 通过 (规范 4.x).
       输出每行: signal_score (百分制), signal_count, tech_hits/fund_hits/catal_hits 命中清单与计数
    """
    cfg = CONFIG_REBOUND["L3_signals"]
    passed = []
    for r in rows:
        code = r["ts_code"]
        info = daily_by_code.get(code)
        if info is None or len(info) < 30:
            if candidate_log is not None: _log_row(r, "L3爆发信号", "数据不足(<30天)", candidate_log)
            continue
        closes = info["close"].astype(float).values
        highs = info["high"].astype(float).values if "high" in info.columns else closes.copy()
        lows = info["low"].astype(float).values if "low" in info.columns else closes.copy()
        vols = info["vol"].astype(float).values if "vol" in info.columns else None
        # —— 技术面 ——
        tech_hits = []
        # 1) MACD 底背离 (最近 60 根内: 价创新低但 dif/macd 未创新低)
        try:
            dif, dea, macd_bar = _macd_arr(closes)
            if len(dif) >= 30:
                # 近似: 近30根价最低 vs 前30~60根 dif 最低
                idx_min_p = int(np.nanargmin(closes[-60:])) + (len(closes) - 60)
                prev_min_dif = float(np.nanmin(dif[max(0, len(closes) - 120):max(0, len(closes) - 30)])) if len(closes) >= 60 else float("inf")
                cur_dif = float(dif[min(idx_min_p, len(dif) - 1)])
                if cur_dif > prev_min_dif:
                    tech_hits.append("MACD底背离")
        except Exception:
            pass
        # 2) KDJ 低位金叉 (K值 < 阈值, K 上穿 D)
        try:
            kdj_k, kdj_d, _ = _kdj_arr(highs, lows, closes)
            if len(kdj_k) >= 3:
                if (kdj_k[-1] < cfg["kdj_oversold_cross"]
                        and kdj_k[-1] > kdj_d[-1] and kdj_k[-2] <= kdj_d[-2]):
                    tech_hits.append("KDJ低位金叉")
        except Exception:
            pass
        # 3) 5日均线拐头向上 + 收盘站在MA5上 (斜率转正 1 根即可, 刚企稳即满足)
        ma5 = _ma_arr(closes, 5)
        if len(ma5) >= 5:
            if ma5[-1] > ma5[-2] and closes[-1] >= ma5[-1]:
                tech_hits.append("MA5拐头向上+站稳")
        # 4) 量价齐升 (当日涨幅≥1.0% 且 量比≥1.2)
        if vols is not None and len(vols) >= 6:
            last_vol = float(vols[-1])
            ma5v = float(np.nanmean(vols[-6:-1])) if len(vols) >= 6 else last_vol
            vr = last_vol / ma5v if ma5v > 0 else 0
            pct_day = (closes[-1] / closes[-2] - 1) * 100 if len(closes) >= 2 else 0
            if vr >= cfg["vol_ratio_amp"] and pct_day >= 1.0:
                tech_hits.append(f"量价齐升(量比{vr:.2f},涨{pct_day:.1f}%)")
            elif vr >= cfg["vol_ratio_amp"]:
                tech_hits.append(f"温和放量(量比{vr:.2f})")
            r["_vr_amp"] = round(vr, 2)
        # 5) 突破短期下降趋势线 (近20日的斜率为负→今日最高价>最近10日下降趋势线≈过去5日最高>近20日前5日最高, 简化用 5日新高 > 近20日前5日最高 -10% ... 用今日收盘>MA10且MA10>MA20)
        ma10 = _ma_arr(closes, 10)
        ma20 = _ma_arr(closes, 20)
        if len(ma10) >= 3 and len(ma20) >= 3:
            if closes[-1] > ma10[-1] > ma20[-1] and ma10[-1] > ma10[-2]:
                tech_hits.append("MA10突破向上+均线金叉")
        # 6) 突破低位平台上沿: 近10日收盘最高 ≈ 近30日收盘最高 (即平台整理后向上突破平台顶)
        if len(closes) >= 30:
            hi10 = float(np.nanmax(closes[-10:]))
            hi30 = float(np.nanmax(closes[-30:]))
            if hi10 >= hi30 * 0.99 and closes[-1] >= hi30 * 0.97:
                tech_hits.append("突破30日低位平台")
        # 7) 筹码集中: 近20日(高低振幅 < 近40日振幅均值一半 + 换手平稳)  [60天数据可算; 原120天窗口导致永不命中]
        if len(closes) >= 40:
            amp20 = (float(np.nanmax(highs[-20:])) - float(np.nanmin(lows[-20:]))) / max(float(np.nanmean(closes[-20:])), 0.01) * 100
            prev20 = (float(np.nanmax(highs[-40:-20])) - float(np.nanmin(lows[-40:-20]))) / max(float(np.nanmean(closes[-40:-20])), 0.01) * 100
            if prev20 > 0 and amp20 <= prev20 * 0.7:
                tech_hits.append("低位筹码集中(振幅收窄)")
        # —— 资金面 ——
        fund_hits = []
        net3d = None
        if mflow_map and code in mflow_map:
            rows_mf = sorted(mflow_map[code], key=lambda x: x["trade_date"])[-cfg.get("main_flow_pos_days", 3):]
            nets = [float(r["buy_sm_amount"] or 0) - float(r["sell_sm_amount"] or 0) for r in rows_mf]
            if nets:
                net3d = sum(nets)
                if net3d > 0 and nets[-1] >= nets[0]:  # 累计净流入 且 最近更大
                    fund_hits.append(f"主力近{len(nets)}日累计净流入")
        # 近5日换手温和提升(从不足1%升到3-5%区间)
        tr_now = _num(r.get("turnover_rate"))
        if tr_now is not None:
            lo, hi = cfg["turnover_target_range"]
            if lo <= tr_now <= hi:
                fund_hits.append(f"换手率进入温和区({tr_now:.1f}%)")
            elif tr_now >= cfg["turnover_improve_lo"]:
                fund_hits.append(f"换手率温和放大({tr_now:.1f}%)")
        # 北向逆势加仓(数据用 fina_map 的北向持有比例 或 用 moneyflow_hsgt 缺失跳过) - 简化: 跳过不否决, 此处占位
        # (当前代码缺失北向明细 → 跳过)
        # 龙虎榜机构席位净买入 (当前 fetch_moneyflow_batch 不含 → 占位跳过)
        # 占位: 如果没有数据则放宽到 1 条命中也算通过 (实际判断: 只按现有 hit 计数, 不足时放宽到 ≥1 通过但打"资金数据缺失"标签)
        # —— 催化 ——
        catal_hits = []
        ind = str(r.get("industry") or "")
        if sector_rps_map and ind in sector_rps_map:
            s_rps20 = sector_rps_map[ind].get("rps20")
            if s_rps20 is not None and s_rps20 >= cfg["sector_rps_recover_min"]:
                catal_hits.append(f"所属板块RPS20回升({s_rps20:.0f})")
        # 简化风格判断: 近5日 高位股(RPS50>90)涨幅 <0 且 低位股(RPS50<40)涨幅 >0 → 轮动
        # (本简化版本跳过)
        # —— 通过判定 + 综合信号分 (规范 4.x: 技术≤4项 + 资金≤3项 + 催化≤2项, 命中总数≥3 判定信号有效) ——
        tech_count = min(len(tech_hits), int(cfg["tech_cap"]))
        fund_count = min(len(fund_hits), int(cfg["fund_cap"]))
        catal_count = min(len(catal_hits), int(cfg["catal_cap"]))
        signal_total = tech_count + fund_count + catal_count
        if signal_total < int(cfg["signal_hit_min"]):
            if candidate_log is not None: _log_row(r, "L3爆发信号", f"信号命中{signal_total}<{cfg['signal_hit_min']} (技术{tech_count}/资金{fund_count}/催化{catal_count}): {tech_hits or '无'} {fund_hits or ''} {catal_hits or ''}", candidate_log)
            continue
        # → 计算信号分: 技术(≤4项,40封顶) + 资金(≤3项,35封顶) + 催化(≤2项,25封顶) = 满分100
        # 达标基准制: 命中门槛即给大头分, 多命中冲满 (原分母按总项数7/3, 导致低命中时信号分过低 → L4总分<60 全部被拦)
        tech_part = min(40, 25 + tech_count * 8)
        fund_part = min(35, 20 + fund_count * 15)
        catal_part = 25 if catal_count >= 1 else 0
        signal_score = int(tech_part + fund_part + catal_part)
        signal_score = max(0, min(100, signal_score))
        r["signal_score"] = signal_score
        r["signal_count"] = signal_total   # 置信度判定用 (规范 5.2)
        r["signal_counts"] = {"tech": tech_count, "fund": fund_count, "catal": catal_count}
        r["tech_hits"] = tech_hits
        r["fund_hits"] = fund_hits
        r["catal_hits"] = catal_hits
        r["net_inflow_3d"] = net3d
        passed.append(r)
    return passed


# ============================================================
# 第四层: 综合评分 & 排序输出 Top N
# ============================================================
def _final_score(r, market_status=None):
    """超跌25% + 基本面25% + 估值性价比25% + 爆发信号25% = 100分 (2026-08-29 估值维度加入)"""
    sc = CONFIG_REBOUND["L4_score"]
    cfg1 = CONFIG_REBOUND["L1_oversold"]
    # 超跌分: 回撤越深分越高 (50%→60分; 60%+→100分)
    dd = float(r.get("drawdown_pct") or 0)
    if dd <= cfg1["drawdown_min_pct"]:
        os_score = 60
    elif dd >= cfg1["drawdown_deep_pct"]:
        os_score = 100
    else:
        ratio = (dd - cfg1["drawdown_min_pct"]) / (cfg1["drawdown_deep_pct"] - cfg1["drawdown_min_pct"])
        os_score = int(60 + ratio * 40)
    os_score = max(0, min(100, os_score))
    fund_s = int(r.get("fundamental_score") or 0)
    val_s = int(r.get("valuation_score") or 0)
    sig_s = int(r.get("signal_score") or 0)
    total = int(round(sc["w_oversold"] * (os_score / 100.0)
                   + sc["w_fundamental"] * (fund_s / 100.0)
                   + sc["w_valuation"] * (val_s / 100.0)
                   + sc["w_signals"] * (sig_s / 100.0)))
    return total, os_score, fund_s, val_s, sig_s


def _predict_rebound(r, latest_date, prev_close, total_score=None, signal_count=None):
    """超跌专属预测: 简单版(无predict模块时构造) —— 上涨概率/预期涨幅/止损位/置信度
    置信度 (规范 5.2): 总分≥75 且 信号≥5 → 高; 总分60-74 且 信号3-4 → 中; 其余 → 低
    """
    sc = CONFIG_REBOUND["L4_score"]
    dd = float(r.get("drawdown_pct") or 0)
    sig = int(r.get("signal_score") or 0)
    fund = int(r.get("fundamental_score") or 0)
    if total_score is None:
        total_score = int(r.get("score_total") or 0)
    if signal_count is None:
        signal_count = int(r.get("signal_count") or 0)
    # 反弹目标: 第一修复位 = 回撤的 1/3
    high_c = float(r.get("drawdown_high_close") or 0)
    price = float(r.get("price") or 0)
    expected_gain_pct = None
    stop_loss_pct = None
    if high_c > 0 and price > 0:
        target = price + (high_c - price) * 0.33  # 修复回撤的 33%
        expected_gain_pct = round((target / price - 1) * 100, 2)
        # 止损: 跌破近期低点(近10日最低)-2%
        stop_loss_pct = -5.0  # 默认 5%
    # 上涨概率: 综合分 (基础胜率 = 45% + 回撤加成+信号加成)
    base = 0.45
    dd_bonus = 0.10 if dd >= 60 else 0.05 if dd >= 55 else 0
    sig_bonus = (sig / 100.0) * 0.25
    fund_bonus = (fund / 100.0) * 0.15
    up_prob = round(min(0.85, max(0.30, base + dd_bonus + sig_bonus + fund_bonus)), 4)
    # 置信度 (规范 5.2)
    if total_score >= int(sc.get("conf_high_total", 75)) and signal_count >= int(sc.get("conf_high_signals", 5)):
        conf = "高"
    elif total_score >= int(sc.get("conf_mid_total", 60)) and signal_count >= int(sc.get("conf_mid_signals", 3)):
        conf = "中"
    else:
        conf = "低"
    # 高 PE 降档: PE 超过行业上限×0.8 但未超上限 → 置信度自动降一档 (未超上限但已偏高)
    if r.get("_pe_high_flag"):
        conf = {"高": "中", "中": "低"}.get(conf, "低")
    return {
        "up_prob": round(up_prob * 100, 2),
        "expected_gain_pct": expected_gain_pct,
        "stop_loss_pct": stop_loss_pct,
        # 兼容字段名: 前端 fmtPredGain/fmtPredStop 与 predict.record_predictions 读 expected_gain/stop_loss
        "expected_gain": expected_gain_pct,
        "stop_loss": stop_loss_pct,
        "confidence": conf,
    }


def _build_tags(r, os_score, fund_score, sig_score):
    """综合标签: 深度超跌 + 基本面亮点 + 爆发信号 → 最终一行标签"""
    tags = []
    dd = float(r.get("drawdown_pct") or 0)
    deep = r.get("deep_drawdown")
    tags.append(("深度超跌" if deep else "超跌") + f"{int(round(dd))}%")
    tags.extend(r.get("fundamental_items", [])[:2])
    tags.extend(r.get("valuation_items", [])[:1])
    tags.extend(r.get("tech_hits", [])[:2])
    tags.extend(r.get("fund_hits", [])[:1])
    tags.extend(r.get("catal_hits", [])[:1])
    # 去重
    uniq = list(dict.fromkeys([str(t) for t in tags if t]))[:6]
    return " + ".join(uniq)


# ============================================================
# 公告风险数据源 (规范 3.1: 立案/调查/减持/解禁 排雷; 失败不剔除)
# ============================================================
def _fetch_announce_titles(ts_code, n=30, timeout=8):
    """东财公告接口 → 近 n 条公告标题列表 (失败返回 []).
    复刻 stock_detail.fetch_news 的数据源, 供主流程构造 risk_map 用.
    """
    try:
        import urllib.request
    except Exception:
        return []
    code, _, ex = str(ts_code or "").partition(".")
    if not (code.isdigit() and ex.upper() in ("SH", "SZ", "BJ")):
        return []
    # 注意: 东财该公告接口 stock_list 仅接受纯代码 (带 1./0. 市场前缀会返回空)
    url = ("https://np-anotice-stock.eastmoney.com/api/security/ann?"
           "sr=-1&page_size=%d&page_index=1&ann_type=A&client_source=web&"
           "stock_list=%s&f_node=0&s_node=0" % (n, code))
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Referer": "https://quote.eastmoney.com/",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        return [str(it.get("title") or "") for it in ((d.get("data") or {}).get("list") or []) if it.get("title")]
    except Exception:
        return []


# ============================================================
# 主流程: pick_rebound_stocks
# ============================================================
def _merge_basic_fields(latest_rows, basic_idx):
    """合并 daily_basic 字段 (PE/流通市值/换手率/量比) 到 latest_rows 并返回.
    basic_idx 的值可能是 pandas Series (df_basic.iterrows) 或 dict:
      不能用 `or {}` 短路 —— 非空 Series 的 bool 判断抛 ValueError (pandas≥1.0 严格模式)
      统一用 .get 安全取值 + pd.notna 过滤 NaN
    """
    for r in latest_rows:
        b = basic_idx.get(r["ts_code"])
        if b is None:
            b = {}
        r["pe_ttm"] = float(b["pe_ttm"]) if b is not None and pd.notna(b.get("pe_ttm")) else None
        r["circ_mv"] = float(b["circ_mv"]) if b is not None and pd.notna(b.get("circ_mv")) else None  # 单位万元
        r["turnover_rate"] = float(b["turnover_rate"]) if b is not None and pd.notna(b.get("turnover_rate")) else None
        r["vol_ratio"] = float(b["volume_ratio"]) if b is not None and pd.notna(b.get("volume_ratio")) else None
    return latest_rows


def pick_rebound_stocks(industry_df, daily_map, latest_date, market_status=None, candidate_log=None, in_list_records=None):
    """超跌反转潜力推荐主流程.
    输入: 与 recommend_stocks 同口径 (industry_df/daily_map/latest_date/market_status)
    输出: list of 推荐 dict (与 recommendations.json stocks 格式对齐, 可直接被前端复用)
    """
    cfg = CONFIG_REBOUND
    if not cfg["run_mode"].get("REBOUND_ENABLED", True):
        print("  [超跌反转] run_mode.REBOUND_ENABLED=False → 跳过")
        return []
    print("\n" + "=" * 55)
    print("  超跌反转潜力精选 (全行业错杀+基本面+反转信号)")
    print("=" * 55)
    # —— 回撤档位自动适配 (规范 2.2): 大盘强弱 → 激进/标准/保守档; 连续3日普跌 → 暂停本策略 ——
    tier_sel = _select_tier(market_status, daily_map, latest_date)
    if tier_sel["pause"]:
        print(f"  ⏸️ 超跌策略暂停: {tier_sel['reason']} → 今日不推荐 (外部保存空文件)")
        return []
    print(f"  📊 大盘环境判定: {tier_sel['market_label']} → 激活「{tier_sel['tier']}」档 (回撤≥{tier_sel['min']}%)")
    if candidate_log is None:
        candidate_log = []
    # ============================================================
    # 日线窗口策略 (OOM 安全, 避免 280天×5500股 DataFrame 字典 被 OOM Killer 杀)
    #   1. 主流程传入 daily_map 通常是 60 交易日 → 原样用于 build_daily_by_code 60天 (满足除回撤外的所有 L1 硬性 + L2/L3 信号)
    #   2. 先 L1_PART_A_LITE 做 60天窗口 可完成的 价/额/停牌/数据充足 筛 → 候选集 (通常压缩到 1500~2500只)
    #   3. 只为候选集 通过 AKShare 单股接口 拉 520自然日 长历史 close/low/amount → 紧凑 float32 数组
    #   4. L1_PART_B_DRAWDOWN 在长历史上做 250天回撤≥50% + 10日不创新低 → 输出通过列表
    #   (Fallback: 若检测到 daily_map 已覆盖 >=280 交易日, 仍保留旧路径 _screen_L1_oversold 一体式)
    # ============================================================
    need_window = int(cfg["L1_oversold"]["drawdown_window"]) + 30  # 280
    _avail_count = len([d for d in daily_map.keys() if str(d) <= str(latest_date)])
    _use_two_phase = _avail_count < need_window
    # —— 构建候选: 全 A 股 (industry_df 中所有 正常上市, 有最新日K 的股票) ——
    if latest_date not in daily_map:
        print(f"  ⚠️ latest_date {latest_date} 不在 daily_map, 跳过超跌推荐")
        return []
    df_latest = daily_map[latest_date]
    # 构建 name/industry 索引 (industry_df: ts_code, name, industry, list_date)
    ind_idx = {}
    if industry_df is not None and len(industry_df) > 0:
        for _, r in industry_df.iterrows():
            ind_idx[r["ts_code"]] = {
                "name": str(r.get("name", "")),
                "industry": str(r.get("industry", "")),
                "list_date": r.get("list_date"),
            }
    # 基础行情数据索引: 最新 price/pct_chg/vol/amount
    latest_rows = []
    codes_seen = set()
    for _, r in df_latest.iterrows():
        code = r["ts_code"]
        if code in codes_seen:
            continue
        codes_seen.add(code)
        info = ind_idx.get(code, {})
        latest_rows.append({
            "ts_code": code,
            "name": info.get("name", ""),
            "industry": info.get("industry", ""),
            "list_date": info.get("list_date"),
            "price": float(r["close"]) if pd.notna(r.get("close")) else None,
            "pct_chg": float(r["pct_chg"]) if pd.notna(r.get("pct_chg")) else None,
            "vol": int(r["vol"]) if pd.notna(r.get("vol")) else 0,
            "amount": float(r["amount"]) if pd.notna(r.get("amount")) else None,
        })
    print(f"  [初始池] 全市场最新 {len(latest_rows)} 只有交易记录 → 开始逐层过滤")
    # 预建日线索引: 两阶段时只需要 60 天 (满足 L1_PART_A + L2/L3 指标); 否则拉 260 天用于一体式
    codes_all = [r["ts_code"] for r in latest_rows]
    _n_idx = 60 if _use_two_phase else 260
    daily_by_code = build_daily_by_code(daily_map, codes_all, latest_date, n_days=_n_idx)
    print(f"  预建个股{_n_idx}日线索引: {len(daily_by_code)} 只  (模式={'OOM安全-两阶段' if _use_two_phase else '全窗口一体式'})")
    # daily_basic (PE/流通市值/换手率/量比) 全市场
    basic_idx = {}
    try:
        df_basic = pool.call_any("daily_basic", trade_date=latest_date)
        if df_basic is not None and len(df_basic) > 0:
            cs = set(codes_all)
            basic_idx = {r["ts_code"]: r for _, r in df_basic.iterrows() if r["ts_code"] in cs}
    except Exception:
        basic_idx = {}
    if not basic_idx:
        try:
            basic_idx = fetch_daily_basic_em(latest_date, codes=codes_all) or {}
        except Exception:
            basic_idx = {}
    # 合并到 latest_rows
    _merge_basic_fields(latest_rows, basic_idx)
    # 财务指标 fina_map (全市场)
    try:
        fina_map = fetch_fina_indicator_batch(latest_date, codes=codes_all) or {}
    except Exception:
        fina_map = {}
    print(f"  财务指标覆盖: {len(fina_map)} 只")
    # 主力资金近3日
    try:
        mflow_map = fetch_moneyflow_batch(latest_date, daily_map, n_days=3, codes=codes_all) or {}
    except Exception:
        mflow_map = {}
    print(f"  主力资金覆盖: {len(mflow_map)} 只")
    # 板块RPS回升映射: 取 industry → 当日 RPS20
    sector_rps_map = {}
    if in_list_records:
        for rec in in_list_records:
            name = rec.get("name") or ""
            rps20 = rec.get("rps")
            if name and rps20 is not None:
                # name 是细分板块, 映射到申万行业: 近似用板块名在 industry 中包含匹配
                # 这里粗略把细分板块记下来, L3 催化里用"是否属强势RPS板块成分"替代
                sector_rps_map[name] = {"rps20": rps20}

    # —— 第一层: 深度超跌硬性门槛 (规范 2.1/7.3: 档位自适应 + 选出为空时自动放宽一档) ——
    relax_chain = ["conservative", "standard", "aggressive"]  # 由严→宽: 45% → 35% → 25%
    _max_l1_attempt = len(relax_chain) if cfg["L1_oversold"].get("fallback_relax", True) else 1
    passed_L1 = []
    _attempt = 0
    while _attempt < _max_l1_attempt and not passed_L1:
        _cur_tier = cfg["L1_oversold"]["active_tier"]
        print(f"\n[L1 超跌门槛-第{_attempt+1}轮] 档位「{_cur_tier}」回撤≥{cfg['L1_oversold']['drawdown_min_pct']}%"
              f" + 近{cfg['L1_oversold']['stable_days']}日不创新低, 共 {len(latest_rows)} 只")
        if _use_two_phase:
            # —— 两阶段 (OOM 安全) ——
            c1 = _screen_L1_partA_lite(latest_rows, daily_by_code, latest_date, candidate_log=candidate_log)
            print(f"  [L1_PART_A 轻筛] 通过: {len(c1)} / {len(latest_rows)} → 进入长历史抓取")
            if c1:
                c1_codes = [r["ts_code"] for r in c1]
                try:
                    long_panel = _fetch_long_panel_per_code(c1_codes, latest_date, calendar_days_back=520, max_workers=12)
                    passed_L1 = _screen_L1_partB_drawdown(c1, long_panel, candidate_log=candidate_log)
                    print(f"  [L1_PART_B 回撤] 250天回撤≥{cfg['L1_oversold']['drawdown_min_pct']}% + 10日不创新低 → 通过: {len(passed_L1)} / {len(c1)}")
                except Exception as _le:
                    print(f"  ⚠️ 两阶段L1失败(长历史抓取异常): {type(_le).__name__}:{str(_le)[:120]}")
                    print("  → 回退到 60天窗口 的 一体式 _screen_L1_oversold (结果偏严, 仅做兜底)")
                    passed_L1 = _screen_L1_oversold(latest_rows, daily_by_code, latest_date, candidate_log=candidate_log)
        else:
            # —— 一体式 (daily_map 已覆盖 ≥280 天, 无 OOM 风险) ——
            passed_L1 = _screen_L1_oversold(latest_rows, daily_by_code, latest_date, candidate_log=candidate_log)
        print(f"  → L1 第{_attempt+1}轮通过: {len(passed_L1)} 只")
        if not passed_L1 and _attempt < _max_l1_attempt - 1:
            _cur_idx = relax_chain.index(_cur_tier) if _cur_tier in relax_chain else 1
            if _cur_idx >= len(relax_chain) - 1:
                break
            _nxt = relax_chain[_cur_idx + 1]
            _activate_tier(_nxt)
            print(f"  ⚠️ 档位「{_cur_tier}」选出为空 → 数量兜底自动放宽至「{_nxt}」档 (回撤≥{cfg['L1_oversold']['drawdown_min_pct']}%)")
        _attempt += 1
    if not passed_L1:
        print("  ⚠️ L1 超跌门槛全部档位无一通过 → 今日无超跌推荐")
        return []
    # —— 估值三层硬约束 (L1 之后、基本面 L2 之前; 拦截高PE价值陷阱) ——
    if cfg["L2_valuation"].get("enabled", True):
        print(f"\n[L2估值] 共 {len(passed_L1)} 只 → 绝对PE分档/行业PE分位/估值回撤 硬约束")
        try:
            val_codes = [r["ts_code"] for r in passed_L1]
            pe_hist_map = _fetch_pe_hist_per_code(val_codes, latest_date) or {}
            passed_L1 = _screen_L2_valuation(passed_L1, pe_hist_map, fina_map=fina_map, candidate_log=candidate_log)
            print(f"  → 通过: {len(passed_L1)} 只")
        except Exception as _ve:
            print(f"  ⚠️ 估值层异常(降级跳过): {type(_ve).__name__}:{str(_ve)[:80]}")
        if not passed_L1:
            print("  ⚠️ 估值硬约束无一通过 → 今日无超跌推荐")
            return []
    # —— 公告风险排雷数据 (规范 3.1: 立案/调查/减持/解禁; 拉取失败或数据缺失 → 不校验, 不剔除) ——
    risk_map = {}
    try:
        if passed_L1 and len(passed_L1) <= 400:
            _codes_r = [r["ts_code"] for r in passed_L1]
            _risk_kw = ("立案", "调查", "减持", "解禁")
            def _fetch_risk_titles(c):
                return [t for t in _fetch_announce_titles(c, n=30) if any(k in t for k in _risk_kw)]
            with ThreadPoolExecutor(max_workers=10) as _ex:
                _futs = {_ex.submit(_fetch_risk_titles, c): c for c in _codes_r}
                for _fu in as_completed(_futs):
                    _c = _futs[_fu]
                    try:
                        _hits = _fu.result()
                        if _hits:
                            risk_map[_c] = _hits
                    except Exception:
                        pass
            print(f"  [公告风险排雷] 覆盖 {len(risk_map)}/{len(_codes_r)} 只 (关键词: 立案/调查/减持/解禁)")
    except Exception as _re:
        print(f"  ⚠️ 公告风险拉取失败(跳过排雷): {str(_re)[:80]}")
        risk_map = {}
    # —— 第二层: 基本面优质 + 加分 ——
    print(f"\n[L2 基本面] 共 {len(passed_L1)} 只 → ST/连续亏损扩/高负债/高商誉/高质押/立案/减持/解禁 剔除 + 加分")
    passed_L2 = _screen_L2_fundamental(passed_L1, fina_map, latest_date, daily_map,
                                       candidate_log=candidate_log, risk_map=risk_map)
    print(f"  → 通过: {len(passed_L2)} 只")
    if not passed_L2:
        print("  ⚠️ L2 基本面无一通过 → 今日无超跌推荐")
        return []
    # —— 第三层: 爆发前置信号 ——
    print(f"\n[L3 爆发信号] 共 {len(passed_L2)} 只 → 技术+资金+催化 命中总数≥{cfg['L3_signals']['signal_hit_min']}项 (技术≤4/资金≤3/催化≤2)")
    passed_L3 = _screen_L3_signals(passed_L2, daily_by_code, latest_date, mflow_map, sector_rps_map, candidate_log=candidate_log)
    print(f"  → 通过: {len(passed_L3)} 只")
    if not passed_L3:
        print("  ⚠️ L3 爆发信号无一通过 → 今日无超跌推荐")
        return []
    # —— 第四层: 综合评分 & 排序 ——
    sc_cfg = cfg["L4_score"]
    top_n = sc_cfg["out_top_n"]
    # 规范 3.2.2: 单边牛市自动降权 (推荐数量减半至1只) —— 由 _select_tier 判定的 market_label 决定
    if tier_sel.get("market_label") == "strong":
        top_n = min(top_n, cfg["market"].get("bull_mode_cap", 1))
        print(f"  (📈 单边强势市 → 超跌推荐数量减半至 {top_n} 只)")
    scored = []
    for r in passed_L3:
        tot, os_s, fund_s, val_s, sig_s = _final_score(r, market_status)
        if tot < sc_cfg["min_total_score"]:
            candidate_log.append({
                "ts_code": r["ts_code"], "name": r.get("name", ""),
                "stage": "L4综合分", "reason": f"总分{tot}<{sc_cfg['min_total_score']} (超跌{os_s}/基本面{fund_s}/估值{val_s}/信号{sig_s})"
            })
            continue
        pred = _predict_rebound(r, latest_date, r.get("drawdown_high_close"),
                                total_score=tot, signal_count=r.get("signal_count", 0))
        tags_txt = _build_tags(r, os_s, fund_s, sig_s)
        rec = {
            "ts_code": r["ts_code"],
            "name": r.get("name", ""),
            "industry": r.get("industry", ""),
            "sectors": [r.get("industry", "")] + (["超跌%d%%修复" % int(round(float(r.get("drawdown_pct") or 0)))] if float(r.get("drawdown_pct") or 0) >= 60 else []),
            "sector_count": 1,
            "price": round(float(r["price"]), 2) if r.get("price") else None,
            "pct_chg": round(float(r["pct_chg"]), 2) if r.get("pct_chg") is not None else None,
            "score_total": tot,
            "score_oversold": os_s,
            "score_fundamental": fund_s,
            "score_valuation": val_s,
            "score_signals": sig_s,
            "score_breakdown": {
                "超跌幅度与质量": os_s,
                "基本面质地": fund_s,
                "估值性价比": val_s,
                "爆发信号强度": sig_s,
            },
            "fundamental": {
                "drawdown_pct": round(float(r.get("drawdown_pct") or 0), 2),
                "drawdown_high_close": r.get("drawdown_high_close"),
                "deep_drawdown": bool(r.get("deep_drawdown")),
                "pe_ttm": r.get("pe_ttm"),
                "pe_bucket": r.get("pe_bucket"),
                "pe_cap": r.get("pe_cap"),
                "pe_pct_3y": r.get("pe_pct_3y"),
                "pe_dd_pct": r.get("pe_dd_pct"),
                "circ_mv_wan": r.get("circ_mv"),
                "items": r.get("fundamental_items"),
            },
            "capital": {
                "net_inflow_3d": r.get("net_inflow_3d"),
                "turnover_rate": r.get("turnover_rate"),
                "vol_ratio": r.get("_vr_amp"),
            },
            "technical": {
                "tech_hits": r.get("tech_hits", []),
                "fund_hits": r.get("fund_hits", []),
                "catal_hits": r.get("catal_hits", []),
            },
            "prediction": pred,
            "tags": tags_txt,
            "strategy": "REBOUND",  # 标记超跌策略
        }
        scored.append((tot, rec))
    def _rank_key(item):
        """同分 tie-break (规范 5.3): 总分 → 回撤深 → 估值分位低"""
        _tot, _rec = item
        _dd = float(_rec.get("fundamental", {}).get("drawdown_pct") or 0)
        _pe_pct = _rec.get("fundamental", {}).get("pe_pct_3y")
        _pe_pct = float(_pe_pct) if _pe_pct is not None else 100.0  # 缺失视为最贵 → 排后
        return (_tot, _dd, -_pe_pct)
    scored.sort(key=_rank_key, reverse=True)
    rec_list = [r for _, r in scored][:top_n]
    print("\n[L4 综合评分] 通过 %d 只 → 最终输出 Top%d/%d (阈值≥%d)" % (len(scored), len(rec_list), top_n, sc_cfg['min_total_score']))
    for i, r in enumerate(rec_list, 1):
        print(f"  {i}. {r['ts_code']} {r['name']}: 总分{r['score_total']} (超跌{r['score_oversold']}/基本面{r['score_fundamental']}/估值{r['score_valuation']}/信号{r['score_signals']}) | 置信度{r['prediction']['confidence']} | {r['tags'][:60]}")
    return rec_list


# ============================================================
# 保存 rebound_recommendations.json
# ============================================================
def _sanitize(obj):
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return round(obj, 4)
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return round(float(obj), 4)
    return obj


def save_rebound_recommendations(rec_list, latest_date, prev_date="", market_status=None):
    """保存到 static/rebound_recommendations.json (schema 对齐 recommendations.json 方便前端复用)"""
    ms = dict(market_status or {})
    ms.setdefault("is_safe", None)
    ms.setdefault("rule", "超跌策略: 大盘基础校验 + 连续3日普跌保护")
    ms.setdefault("reason", "" if ms.get("is_safe") else "大盘环境不满足")
    ms.setdefault("indexes", [])
    # 行业集中度
    try:
        if rec_list:
            cnt = Counter((r.get("industry") or "未知") for r in rec_list)
            top, topn = cnt.most_common(1)[0]
            ic = {"top_industry": top, "top_count": topn, "top_ratio": round(topn / len(rec_list), 2)}
        else:
            ic = {}
    except Exception:
        ic = {}
    output = {
        "schema_version": 1,
        "mode": "oversold_rebound",
        "update_date": latest_date,
        "prev_date": prev_date,
        "market_status": ms,
        "count": len(rec_list),
        "industry_concentration": ic,
        "disclaimer": "⚠️ 本策略为超跌反转筛选(基本面优质+深度回撤档位自适应25%/35%/45%+反转前置信号共振)，本质为左侧交易，存在底部磨底与继续下探风险，胜率低于动量策略，波动较大，必须严格执行止损，仓位建议≤总仓位30%。绝对不构成投资建议。严禁杠杆借贷炒股。",
        "strategy_description": "与强者恒强动量日度推荐形成互补：原有策略做趋势追涨，本策略做估值修复型错杀股的反弹爆发。筛选顺序：深度超跌门槛(回撤三分档自动适配:强势25%/震荡35%/弱势45%+10日不创新低+价≥2元+日均成交额≥3000万+急跌错杀/阴跌剔除)→估值三层硬约束(绝对PE行业分档上限+近3年PE分位≤40%/≥70%剔除+估值回撤≥股价回撤70%+题材爆炒/周期顶点排雷)→基本面升级排雷(净利与现金流为正+扣非增速≥-10%+PEG≤2+立案/减持/解禁公告排雷)→爆发信号(技术≤4+资金≤3+催化≤2,命中总数≥3)→综合评分(超跌25%+基本面25%+估值25%+信号25%)。每日精选2只(单边牛市减半)。",
        "stocks": rec_list,
    }
    output = _sanitize(output)
    with open(REBOUND_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, allow_nan=False)
    # 超跌落选日志
    try:
        with open(CANDIDATE_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "update_date": latest_date,
                "stage_counts": dict(Counter(x["stage"] for x in (_LAST_CANDIDATE_LOG or []))),
                "count": len(_LAST_CANDIDATE_LOG or []),
                "candidates": _sanitize(_LAST_CANDIDATE_LOG or []),
            }, f, ensure_ascii=False, indent=2, allow_nan=False)
    except Exception:
        pass
    # 超跌预测日志 (写入 predictions_log, 便于每日校准复盘)
    if _predict is not None and rec_list:
        try:
            n = _predict.record_predictions(rec_list, latest_date, tag="REBOUND")
            if n:
                print(f"  ✅ 超跌预测日志: 记录 {n} 只")
        except Exception as e:
            print(f"  ⚠️ 超跌预测日志写入失败: {str(e)[:60]}")
    print(f"  ✅ 超跌反转推荐已保存: {REBOUND_FILE} ({len(rec_list)} 只)")
    return output


_LAST_CANDIDATE_LOG = []


# ============================================================
# 独立运行入口
# ============================================================
if __name__ == "__main__":
    logger.info("===== rebound 开始 =====")
    trade_days = rps_calc.get_trade_days(n=300)
    industry_df = rps_calc.get_stock_industry()
    daily_map = rps_calc.fetch_daily_batch(trade_days)
    available = sorted([d for d in trade_days if d in daily_map])
    latest_date = available[-1]
    prev_date = available[-2] if len(available) >= 2 else ""
    if CONFIG_REBOUND.get("run_mode", {}).get("IGNORE_MARKET_ENV", False):
        ok, adj_status = True, {
            "is_safe": True, "level": "pass",
            "rule": "超跌策略: 已启用忽略大盘 (IGNORE_MARKET_ENV=True)",
            "reason": "用户要求: 超跌潜力推荐 不看大盘环境, 任何市场状态下都出推荐",
            "indexes": [],
        }
        fetch_index_daily = lambda *a, **kw: {}  # noqa: E731  跳过 fetch_index_daily (节省 3*多Token池请求)
    else:
        index_map = fetch_index_daily(CONFIG_REBOUND["market"]["index_codes"], available)
        # 大盘基础校验 (复用 check_market_env)
        m_ok, m_status = check_market_env(index_map, daily_map, latest_date)
        # 超跌适配修正
        ok, adj_status = _rebound_market_adjust(index_map, daily_map, latest_date, m_status)
    if not ok:
        print("  大盘环境 REJECT:", adj_status.get("reason"))
        # 仍保存空文件(0只)
        save_rebound_recommendations([], latest_date, prev_date, market_status=adj_status)
    else:
        recs = pick_rebound_stocks(industry_df, daily_map, latest_date,
                                    market_status=adj_status, candidate_log=_LAST_CANDIDATE_LOG)
        save_rebound_recommendations(recs, latest_date, prev_date, market_status=adj_status)
