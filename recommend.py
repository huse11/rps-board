# -*- coding: utf-8 -*-
"""
每日推荐股票打分模块 v3.3 —— 五层漏斗筛选 + 综合评分
整体架构(漏斗式逐层过滤, 候选股严格限定在 RPS 入选板块成分股范围内):
  第一层 大盘环境: 上证指数/创业板指 vs 20日均线 + 上涨占比≥40% + 跌停≤40家
                   (三条件全满足才选股, 任一不满足 → 输出0只; 指数缺失默认安全)
  第二层 候选池:   100% 来自入选板块成分股, sector_count=归属 RPS5/10/20 三档板块数(跨档重复=共振强度1~3)
  第三层 基本面:   硬性一票否决 (ST/停牌/低价/小市值/PE≤0或>200/亏损/高负债/质押商誉超标)
  第四层 资金面:   换手率[2%,25%]/量比>1.1/成交额≥5000万 全部必达
  第五层 技术共振: 7项独立指标(均线多头/MACD金叉/KDJ低金叉/BOLL突破中轨/RPS50≥80/CCI/RSI) 达标≥3项
  综合评分: 板块热度30 + 基本面15 + 资金面40(v2.0梯度制: 换手20+量比20) + 技术面15 = 100分
  标签: ≥80 → 短线优选; 70-79 → 短线观察
  按综合得分降序输出, 全部通过五层筛选的股票进入推荐列表
- 所有阈值集中在 CONFIG 配置字典, 无需改代码即可调整选股严格度
- 数据策略(真实+降级):
  * index_daily(指数日线): 真实拉取, 失败默认大盘安全(避免误否决)
  * fina_indicator(净利润/资产负债率): 按 period 批量, 失败降级 PE>0 / 东财替代
  * moneyflow(主力净流入): 按 trade_date 批量, 失败降级近似估算 / AKShare / 东财替代
  * 大盘主力资金(stock_market_fund_flow): AKShare 真实值, 失败跳过(仅提示, 不影响否决)
  * 北向资金(moneyflow_hsgt): 港交所2024年8月起停披露, 已移除(替换为大盘主力资金)
- 输出: static/recommendations.json (schema_version=3)

重要免责声明: 量化条件仅为技术逻辑参考,不构成任何投资建议;
股市波动极大,中短线亏损概率高,严禁杠杆借贷炒股;
本工具仅做个人研究使用,严禁对外收费荐股牟利。
"""
import json
import math
import os
import re
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
EARLY_REC_FILE = STATIC_DIR / "recommendations_early.json"  # 早期启动挖掘独立输出(不污染原版)
# 五层漏斗筛选 + 综合评分 — 全部阈值可配置(无需改代码即可调整选股严格度)
CONFIG = {
    # 第一层: 大盘环境全局校验 (严格二态: 三条件全满足才选股, 任一不满足 → 输出0只)
    "market": {
        "index_codes": ["000001.SH", "399006.SZ"],  # 上证指数 + 创业板指
        "index_names": {"000001.SH": "上证指数", "399006.SZ": "创业板指"},
        "ma_period": 20,           # 指数 20 日均线
        "need_above": 1,           # 至少 1 个指数站稳 20 日线 (可配置为 2)
        "default_safe": True,      # 指数数据完全缺失时默认安全(避免接口故障误否决全市场)
        "check_sentiment": True,   # 是否做市场情绪校验(涨跌比/涨跌停数)
        "advance_ratio_min": 0.40, # 情绪校验: 上涨家数占比下限 (低于 → 否决, 输出0只)
        "limit_down_max": 40,      # 情绪校验: 跌停家数上限 (高于 → 否决, 输出0只)
        "limit_up_pct": 9.8,       # 涨停近似判定: 涨幅 ≥ 该值 (10%/20% 涨停板通用近似)
        "main_flow_days": 3,       # 大盘主力资金参考近 N 个交易日 (真实值, 仅提示标签不否决)
        "main_flow_warn_neg": 2,   # 连续 N 日主力净流出 → 提示"资金偏弱"
        # 板块动量替代路径: 指数站上20日线 + 跌停可控 时, 若 RPS5 入选(强势)板块数 ≥ sector_strong_min,
        # 视为"板块动量强" → 豁免"上涨家数占比"条件 (结构性行情: 板块强但全市场广度不足时不误否决)
        "sector_alt_allow": True,
        "sector_strong_min": 8,    # 替代路径: RPS5 入选板块数 ≥ 该值 → 板块动量强 (RPS95下恒~10, 8=强度充足)
        # 北向广度提示 (2026-08-23): 北向成分股上涨占比 < 该值 → note 附加"北向广度偏弱"(仅提示不否决)
        "hsgt_ratio_warn": 0.40,
    },
    # 第二层: 候选股票池 (核心约束 = RPS 入选板块成分股)
    "pool": {
        "min_sector_count": 1,     # 至少属于 1 个强势板块
        "out_sector_remove": True, # 个股所属板块全部在当日调出列表 → 剔除候选池
        "rps20_soft": 70,          # 个股 RPS20 < 该值 → 板块热度分减半(软过滤, 不否决)
        "sector_groups": [         # 同源板块关键词组: 同组板块按真实共振计, 防题材重复虚高板块分
            ["算力", "AI服务器", "服务器", "数据中心", "液冷", "光模块", "CPO", "云计算"],
            ["半导体", "芯片", "集成电路", "存储芯片", "模拟芯片", "功率半导体", "半导体设备"],
            ["白酒", "啤酒", "食品", "饮料", "乳制品", "调味品"],
            ["银行", "证券", "券商", "保险", "多元金融"],
            ["锂电池", "电池", "储能", "光伏", "风电", "新能源"],
            ["军工", "国防", "航空装备", "航天", "船舶制造"],
            ["CXO", "创新药", "疫苗", "生物医药", "中药"],
            ["房地产", "物业", "建材", "装修装饰"],
        ],
        "blacklist_file": "blacklist.json",  # 手工黑名单(大额解禁/减持/利空), 路径相对 STATIC_DIR
    },
    # 第三层: 基本面排雷 (硬性一票否决)
    "fundamental": {
        "min_price": 2.0,          # 最低收盘价(低价仙股剔除)
        "min_circ_mv": 10e8,       # 最低流通市值 10亿 (daily_basic.circ_mv 单位万元 → 10亿=10万万元)
        "max_pe": 200,             # PE-TTM 上限 (PE≤0 或 >200 均一票否决)
        "max_debt_ratio": 80,      # 资产负债率上限 % (远超合理区间剔除)
        "max_pledge_ratio": 50,    # 股权质押比例上限 % (有数据则强制校验)
        "max_goodwill_ratio": 30,  # 商誉占净资产上限 % (有数据则强制校验)
        "max_report_age_days": 180,  # 财报距今 > 该天数 → 基本面得分×0.6 + 标签【财报陈旧】
        "stale_factor": 0.6,       # 财报陈旧基本面得分系数
    },
    # 第四层: 资金面达标校验 (全部核心项必达)
    "capital": {
        "turnover_min": 2.0,       # 换手率下限 % (过低=无人关注死水)
        "turnover_max": 25.0,      # 换手率上限 % (过高=高位出货风险)
        "vol_ratio_min": 1.1,      # 量比下限 (近期量能较前期放大)
        "amount_min": 5e7,         # 单日成交额下限 5000万 (保证流动性)
        "limit_up_pct": 9.8,       # 当日涨停(涨幅≥该值) → 豁免量比校验, 打【涨停无量】标签
    },
    # 第五层: 多技术指标共振
    "technical": {
        "hit_min": 3,              # 达标指标数 ≥3 项判为共振通过
        "rps50_threshold": 80,     # 个股 RPS50 中期强势阈值
        "cci_strong_min": 100,     # CCI 达标阈值 (>100 进入强势区)
        "rsi_low": 50,             # RSI 达标下限 (多头区)
        "rsi_high": 75,            # RSI 达标上限 (未超买)
        "rsi_overbought": 75,      # RSI > 该值 → 技术超买
        "cci_overbought": 150,     # CCI > 该值 → 技术超买
        "overbought_deduct": 3,    # 单一超买信号: 技术分扣 3
        "overbought_deduct_both": 5,  # RSI+CCI 双超买: 技术分扣 5
    },
    # 综合评分权重 (总分 100)
    "score": {
        "sector": 30,              # 板块热度分(归属强势板块数: 1档=15, 2档=22, 3档=30)
        "fundamental": 15,         # 基本面分
        "capital": 40,             # 资金面分(恢复 v2.0 梯度制: 换手20 + 量比20)
        "technical": 15,           # 技术面分
        "min_score": 70,           # 综合得分门槛: <70 的股票不进入推荐列表(前端不显示)
        "min_fundamental": 8,      # 分项最低门槛: 基本面 < 8 直接剔除(防板块分拉满掩盖缺陷)
        "min_capital": 8,          # 分项最低门槛: 资金面 < 8 直接剔除
        "gain5_threshold": 12.0,   # 近5日累计涨幅 > 该值(%) → 总分扣分(短线追高风险, 原25收紧)
        "gain5_deduct": 12,        # 近5日累计涨幅过高扣分
        "high_pos_pct": 7.0,       # 当天涨幅 > 该值 且 未涨停 → 高位追涨扣分(次日高开回落风险)
        "high_pos_deduct": 5,      # 高位追涨扣分
        "ma20_dev_threshold": 25.0,# 收盘距 MA20 偏离 > 该值(%) → 高位乖离扣分(远离成本区)
        "ma20_dev_deduct": 4,      # 乖离扣分
        "lhb_net_deduct": 3,       # 龙虎榜净买额<0(上榜出货) → 总分扣分(防御性, 不否决)
        # 盈利质量增强(2026-08-23, financial-modeling/earnings-interpretation 方法论):
        #   加分制(上限 bonus_cap, 缺失记0不否决) + 防御性扣分(现金流为负/利润质量存疑)
        "quality": {
            "bonus_cap": 5,            # 质量加分上限
            "revenue_yoy_pos": 2,      # 营收同比>0 加分
            "revenue_yoy_high": 3,     # 营收同比≥30% 加分(高成长)
            "revenue_yoy_high_min": 30.0,
            "gross_margin_high": 2,    # 毛利率≥40% 加分
            "gross_margin_mid": 1,     # 毛利率20-40% 加分
            "gross_margin_high_min": 40.0,
            "gross_margin_mid_min": 20.0,
            "roe_high": 2,             # ROE≥15% 加分
            "roe_mid": 1,              # ROE 8-15% 加分
            "roe_high_min": 15.0,
            "roe_mid_min": 8.0,
            "ocf_pos": 2,              # 每股经营现金流>0 加分(盈利现金含量)
            "roic_pos": 1,             # ROIC≥8% 加分
            "roic_min": 8.0,
            "ocf_neg_deduct": 2,       # 经营现金流<0 且 净利>0 → 扣分(现金流为负)
            "margin_gap_deduct": 1,    # 净利增速-营收增速>40pct → 扣分(利润质量存疑)
            "margin_gap_max": 40.0,
        },
        # 业绩预告信号(2026-08-23, earnings-interpretation 预期差 + industry-researcher 催化):
        #   预增/扭亏/略增/续盈 → 加分+利好标签; 预减/略减/首亏/续亏 → 扣分+风险标签; 缺失跳过
        "yjyg": {
            "bonus": 2,                # 正向预告加分
            "deduct": 2,               # 负向预告扣分
        },
        # 消息面信号(2026-08-23, 金融新闻+主力/散户真实消息):
        #   个股新闻(真实性甄别: 权威来源/公告→official 加分扣分; 股吧等散户传闻→rumor 仅标签不采信)
        #   + 人气榜(散户情绪) + 大宗交易(主力/产业资本行为); 全部"缺失跳过, 不误杀"
        "news": {
            "bonus": 2,                # 权威利好新闻 ≥1 且无权威利空 → 加分 (真实消息采信)
            "deduct": 3,               # 权威利空新闻 ≥1 → 扣分 (防御性, 不否决)
            "window_days": 7,          # 新闻时间窗: 仅统计最新交易日往前 N 天 (旧闻不影响评分)
            "max_fetch": 50,           # 单流程最多拉取新闻的股票数 (性能保护, 超限跳过不误杀)
            "hot_rank_top": 50,        # 人气榜前 N 名 → 标签【人气榜TOP】(散户关注度, 仅提示)
            "dzjy_discount_max": 0.03, # 大宗折溢率 < -3% → 标签【大宗折价】(主力/产业资本让利出货提示)
            "dzjy_inst_buy": True,     # 机构专用席位买入 → 标签【机构大宗买入】(主力真实买入信号)
        },
        "sentiment_cap": {         # 情绪联动: 大盘上涨占比不足且指数未全面站稳 → 缩减推荐数量
            "advance_ratio_min": 0.5,  # 上涨占比低于该值触发
            "keep_ratio": 0.5,         # 保留比例
            "keep_min": 3,             # 至少保留只数
        },
        "gain8_week_threshold": 80.0,  # 周度: 近8周累计涨幅 > 该值(%) → 扣分
        "gain8_week_deduct": 10,   # 周度: 近8周累计涨幅过高扣分
        "max_per_sector": 3,       # 输出层同板块控制: 同一细分板块最多输出 N 只
        "fatigue_deduct": 7,       # 复盘联动: 成熟疲劳板块内个股总分扣 7 (6~8 取中)
        "danger_deduct": 10,       # 复盘联动: danger 风险板块内个股总分扣 10
        # 精准度增强(2026-08-18): 趋势/位置形态 + 高估值降权
        # 目标: 区分"反弹 vs 反转"、压制高估值情绪股、避免追高涨停板
        "trend": {
            "chg60_days": 60,      # 60日区间涨跌窗口 (首根收→末根收, 排除最新日)
            "rebound_deduct": 4,   # 60日区间涨幅 <0 (下跌趋势反弹) → 总分扣分
            "retreat_pct_max": 30.0,  # 距60日最高点回撤 > 该值(%) → 【超跌反弹】(非强势新高)
            "retreat_near": 15.0,  # 距60日最高点回撤 ≤ 该值(%) 且 60日涨幅>0 → 【贴近新高】
            "pe_high_threshold": 100,  # PE-TTM > 该值 → 高估值降权
            "pe_high_deduct": 3,   # 高估值扣分
            "chase_deduct": 3,     # 涨停且(高估值或短期涨幅过高) → 追加扣分
        },
    },
    # 运行模式开关 (新增早期启动挖掘, 原有逻辑/输出完全保留):
    #   ORIGINAL_MOMENTUM=True → 原有整套日推荐逻辑, 输出 recommendations.json (老输出不变)
    #   EARLY_STAGE_START=True → 独立候选池/过滤/打分, 输出 recommendations_early.json
    #   两开关都关 → 不生成任何日度推荐; 关掉 EARLY_STAGE_START → 系统退回原版
    "run_mode": {
        "ORIGINAL_MOMENTUM": True,  # 原有模式: 传统RPS, 成熟强势板块, 选已经走强个股
        "EARLY_STAGE_START": True,  # 新增模式: 刚启动, 潜力板块+涨幅约束, 抓低位初期标的
    },
    # 早期启动挖掘专属配置 (只对 EARLY_STAGE_START 生效, 完全不影响原有模式)
    "early": {
        "rps50_preferred_min": 78,      # 个股 RPS50 优先区间下限
        "rps50_preferred_max": 88,      # 个股 RPS50 优先区间上限
        "rps50_over": 92,               # 个股 RPS50 > 该值 → 硬扣分(已走出, 非刚启动)
        "rps50_over_deduct": 8,         # RPS50 过高扣分
        "gain10_max": 28.0,             # 近10日涨幅 > 该值(%) → 硬扣分(涨幅惩罚)
        "gain10_deduct": 12,            # 近10日涨幅过高扣分
        "ma60_dev_max": 45.0,           # 距 MA60 偏离 > 该值(%) → 硬扣分(已远离成本区)
        "ma60_dev_deduct": 10,          # 距 MA60 偏离过高扣分
        "platform_bonus": 4,            # 平台刚启动形态加分
        "platform_amplitude_max": 30.0, # 平台整理振幅上限(%) (近20日高低点振幅 ≤ 该值=横盘)
        "break_above_pct": 1.5,         # 突破确认: 收盘 ≥ 近20日高点×(1-该%) (允许盘中刷新)
        "sort_tag_first": "平台刚启动",  # 内部排序: 带该标签的优先展示
        "fundamental_overrides": {},    # 早期模式基本面阈值微调(覆盖 CONFIG["fundamental"])
        "capital_overrides": {},        # 早期模式资金面阈值微调(覆盖 CONFIG["capital"])
        "technical_overrides": {},      # 早期模式技术阈值微调(覆盖 CONFIG["technical"])
    },
}
INDEX_CODES = CONFIG["market"]["index_codes"]
INDEX_CACHE_DIR = STATIC_DIR / "index_cache"
FINA_CACHE_FILE = STATIC_DIR / "fina_indicator_cache.json"
MFLOW_CACHE_FILE = STATIC_DIR / "moneyflow_cache.json"
MARKET_FLOW_CACHE_FILE = STATIC_DIR / "market_fund_flow_cache.json"  # 大盘主力资金(替代北向, 真实值)


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


def _f10_float(val):
    """东财F10财务字段: '-'/None/空/NaN → None(真缺失), 数字 → float(保留0/负值)
    区别于 _em_safe_float: 财务缺失不能伪造为 0 (负债率=0 是假数据)"""
    if val is None or val == "-" or val == "":
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


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


def fetch_fina_f10_em(latest_date, codes=None):
    """东财 F10 主要财务指标 (替代 fina_indicator, 字段最全: 含资产负债率真实值)
    RPT_F10_FINANCE_MAINFINADATA 按最近 6 个报告期逐期过滤分页拉取:
    披露季各公司最新披露期不同 (如8月多数公司最新仍是Q1), 单一"最新期"会漏股;
    逐期过滤 (filter=REPORT_DATE) 可 100% 覆盖全市场各公司的最新披露期, 均为真实值
    字段映射:
      ZCFZL→debt_to_assets(资产负债率%), PARENTNETPROFIT→net_profit(归母净利),
      PARENTNETPROFITTZ→netprofit_yoy(归母同比%), KCFJCXSYJLR→n_income_attr_parent(扣非净利),
      KCFJCXSYJLRTZ→dt_netprofit_yoy(扣非同比%),
      TOTALOPERATEREVE→total_revenue(营业总收入), TOTALOPERATEREVETZ→revenue_yoy(营收同比%),
      XSMLL→gross_margin(销售毛利率%), XSJLL→net_margin(销售净利率%),
      ROEJQ→roe(净资产收益率加权%), ROIC→roic(投入资本回报率%),
      MGJYXJJE→ocf_ps(每股经营现金流), BPS→bps(每股净资产)
      (2026-08-23 扩展: 盈利质量多维字段, financial-modeling/earnings-interpretation 方法论)
    返回 {ts_code: {end_date, net_profit, netprofit_yoy, dt_netprofit_yoy,
                    n_income_attr_parent, debt_to_assets, total_revenue, revenue_yoy,
                    gross_margin, net_margin, roe, roic, ocf_ps, bps}} (各自最新披露期)
    codes: 可选候选股 ts_code 集合; 传入时仅保留候选股, 全部找齐即提前退出
    失败/无数据返回 {}
    """
    codes_set = set(codes) if codes else None
    result = {}
    # 从最新已结束季度往前生成 8 个报告期 (如 2026-08 → 20260630, 20260331, 20251231, ...)
    q_end = {1: "0331", 2: "0630", 3: "0930", 4: "1231"}
    cy, cq = int(latest_date[:4]), (int(latest_date[4:6]) - 1) // 3 + 1
    periods = []
    for _ in range(8):
        periods.append(f"{cy}{q_end[cq]}")
        cq -= 1
        if cq == 0:
            cq, cy = 4, cy - 1
    for period in periods:
        end_date = f"{period[:4]}-{period[4:6]}-{period[6:]}"
        page_has_new = False  # 本报告期是否新增个股
        period_seen_rows = False  # 本报告期是否存在个股行(未来期=0行, 跳过继续往旧期找)
        for pn in range(1, 30):
            url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
                   "?sortColumns=REPORT_DATE&sortTypes=-1"
                   f"&pageSize=500&pageNumber={pn}"
                   "&reportName=RPT_F10_FINANCE_MAINFINADATA"
                   "&columns=SECUCODE,REPORT_DATE,ZCFZL,PARENTNETPROFIT,PARENTNETPROFITTZ,"
                   "KCFJCXSYJLR,KCFJCXSYJLRTZ,TOTALOPERATEREVE,TOTALOPERATEREVETZ,"
                   "XSMLL,XSJLL,ROEJQ,ROIC,MGJYXJJE,BPS"
                   f"&filter=(REPORT_DATE=%27{end_date}%27)")
            try:
                d = _em_get_json(url, timeout=20)
            except Exception:
                break
            data = ((d or {}).get("result") or {}).get("data") or []
            if not data:
                break
            for row in data:
                sc = str(row.get("SECUCODE") or "")
                if not re.match(r"^\d{6}\.(SH|SZ|BJ)$", sc):
                    continue  # 过滤指数/基金(A/B开头)等非个股
                code = sc.split(".")[0] + "." + sc.split(".")[1]
                period_seen_rows = True
                if code in result:
                    continue  # 已有更新的披露期, 保留新期
                if codes_set is not None and code not in codes_set:
                    continue
                result[code] = {
                    "end_date": period,
                    "net_profit": _f10_float(row.get("PARENTNETPROFIT")),
                    "netprofit_yoy": _f10_float(row.get("PARENTNETPROFITTZ")),
                    "dt_netprofit_yoy": _f10_float(row.get("KCFJCXSYJLRTZ")),
                    "n_income_attr_parent": _f10_float(row.get("KCFJCXSYJLR")),
                    "debt_to_assets": _f10_float(row.get("ZCFZL")),
                    "total_revenue": _f10_float(row.get("TOTALOPERATEREVE")),
                    "revenue_yoy": _f10_float(row.get("TOTALOPERATEREVETZ")),
                    "gross_margin": _f10_float(row.get("XSMLL")),
                    "net_margin": _f10_float(row.get("XSJLL")),
                    "roe": _f10_float(row.get("ROEJQ")),
                    "roic": _f10_float(row.get("ROIC")),
                    "ocf_ps": _f10_float(row.get("MGJYXJJE")),
                    "bps": _f10_float(row.get("BPS")),
                }
                page_has_new = True
            # 候选股全部找齐 → 提前退出
            if codes_set is not None and len(result) >= len(codes_set):
                return result
            if len(data) < 500:
                break
        if codes_set is not None and len(result) >= len(codes_set):
            break
        if not period_seen_rows:
            continue  # 未来期/无披露期 → 继续往更旧期找
        if not page_has_new:
            break  # 本披露期有数据但无新增 → 全市场已全部覆盖
    return result


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

def _sort_index_df(df):
    """指数日线统一按日期升序(消费者假设升序: closes[-1]为最新; Tushare返回降序需重排)"""
    col = "trade_date" if "trade_date" in df.columns else ("date" if "date" in df.columns else None)
    if col is not None:
        df = df.sort_values(col).reset_index(drop=True)
    return df


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
                        result[code] = _sort_index_df(df)
                        continue
            except Exception:
                pass
        try:
            df = pool.call_any("index_daily", ts_code=code, start_date=start, end_date=end)
            if df is not None and len(df) > 0:
                df = _sort_index_df(df)
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
                    df = _sort_index_df(df)
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
                df = _sort_index_df(df)
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
            data_items = {k: v for k, v in cache.items() if not k.startswith("_")}
            if data_items:
                # 财务为季度披露数据: 仅当"已按最新交易日拉取过"且字段齐全时复用;
                # 最新交易日变化(跨季度/跨披露期)则清空重建, 确保财务数据是最新披露期
                # (2026-08-23) 质量字段 (gross_margin/roe/ocf_ps 等) 加入后, 旧版本缓存无这些键
                #   → 必须一并校验, 否则旧缓存被复用会导致 score_quality 恒为 0
                fresh = cache.get("_fetched_date", "") == latest_date and all(
                    "netprofit_yoy" in v and "gross_margin" in v for v in data_items.values())
                if fresh:
                    if codes_set:
                        return {c: v for c, v in data_items.items() if c in codes_set}
                    return data_items
            cache = {}
        except Exception:
            cache = {}
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
                        "pledge_ratio": float(r["pledge_ratio"]) if pd.notna(r.get("pledge_ratio")) else None,
                        "goodwill_ratio": float(r["goodwill_ratio"]) if pd.notna(r.get("goodwill_ratio")) else None,
                        # 盈利质量字段 (2026-08-23, 与东财F10路径同构; Tushare无营收同比→None)
                        "gross_margin": float(r["grossprofit_margin"]) if pd.notna(r.get("grossprofit_margin")) else None,
                        "net_margin": float(r["netprofit_margin"]) if pd.notna(r.get("netprofit_margin")) else None,
                        "roe": float(r["roe"]) if pd.notna(r.get("roe")) else None,
                        "roic": float(r["roic"]) if pd.notna(r.get("roic")) else None,
                        "ocf_ps": float(r["ocfps"]) if pd.notna(r.get("ocfps")) else None,
                        "bps": float(r["bps"]) if pd.notna(r.get("bps")) else None,
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
    # 东财替代: Tushare 全失败时启用 (F10 逐期全市场最新披露期, 含资产负债率; 旧报表兜底)
    if not cache:
        try:
            f10_cache = fetch_fina_f10_em(latest_date, codes=codes)
            if f10_cache:
                cache = f10_cache
                print(f"  ✅ fina_indicator (东财F10逐期): {len(cache)} 条, 含资产负债率真实值")
        except Exception as e:
            print(f"  ⚠️ fina_indicator 东财F10失败: {str(e)[:50]}")
    if not cache:
        try:
            em_cache = fetch_fina_em(latest_date, codes=codes)
            if em_cache:
                cache = em_cache
                print(f"  ✅ fina_indicator (东财旧报表兜底): {len(cache)} 条")
        except Exception as e:
            print(f"  ⚠️ fina_indicator 东财兜底失败: {str(e)[:50]}")
    if cache:
        try:
            cache["_fetched_date"] = latest_date
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


def fetch_market_fund_flow(latest_date, n_days=5):
    """大盘主力资金近 n 日 (替代已停披露的北向资金 moneyflow_hsgt, 真实值)
    主源 AKShare stock_market_fund_flow (东财datacenter)
    缓存 static/market_fund_flow_cache.json: {trade_date: {"main_net": 元, "main_pct": %}}
    失败返回 {} (仅提示, 不影响否决)
    """
    if MARKET_FLOW_CACHE_FILE.exists():
        try:
            with open(MARKET_FLOW_CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    else:
        cache = {}
    if _AK_OK:
        try:
            ak_flow = _ak.fetch_market_fund_flow_ak(n_days=n_days)
            if ak_flow:
                cache.update(ak_flow)
                print(f"  ✅ 大盘主力资金 (AKShare): {len(ak_flow)} 日真实值")
        except Exception as e:
            print(f"  ⚠️ 大盘主力资金 AKShare失败: {str(e)[:50]}")
    if cache:
        try:
            with open(MARKET_FLOW_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception:
            pass
    return cache


def fetch_lhb(latest_date):
    """龙虎榜净买额 (第四层资金面防御性信号, 2026-08-23)
    主源 AKShare stock_lhb_detail_em; 返回 {ts_code: {"lhb_net": 元, "reason": str}}
    lhb_net<0 → 上榜出货(标签+扣分); lhb_net>0 → 净买入(仅标签); 缺失/失败返回 {} (不误杀)
    """
    if not _AK_OK:
        return {}
    try:
        lhb = _ak.fetch_lhb_ak(latest_date)
        if lhb:
            print(f"  ✅ 龙虎榜: {len(lhb)} 只上榜 (净买入{sum(1 for v in lhb.values() if v['lhb_net']>0)} / 出货{sum(1 for v in lhb.values() if v['lhb_net']<0)})")
        return lhb or {}
    except Exception as e:
        print(f"  ⚠️ 龙虎榜 AKShare失败: {str(e)[:50]}")
        return {}


def fetch_hsgt_breadth():
    """北向资金广度 (大盘环境提示, 2026-08-23)
    主源 AKShare stock_hsgt_fund_flow_summary_em; 北向实时净买额已停披露(恒0),
    仅用"上涨数/下跌数"计算北向成分股上涨占比 → {north_up, north_down, north_ratio}
    失败返回 {} (仅提示, 不影响否决)
    """
    if not _AK_OK:
        return {}
    try:
        hsgt = _ak.fetch_hsgt_ak()
        if hsgt:
            print(f"  ✅ 北向广度: 上涨{hsgt['north_up']} / 下跌{hsgt['north_down']} "
                  f"(占比{hsgt['north_ratio']:.0%})")
        return hsgt or {}
    except Exception as e:
        print(f"  ⚠️ 北向广度 AKShare失败: {str(e)[:50]}")
        return {}


# ============================================================
# 8. 消息面信号 (金融新闻 + 主力/散户真实消息, 2026-08-23)
#    数据源: 东财个股新闻(真实性甄别) + 人气榜(散户情绪) + 大宗交易(主力行为)
#    甄别真实性: 权威媒体/公告 → official(采信, 影响分数); 股吧等散户来源 → rumor(仅标签不采信)
# ============================================================
# 权威来源白名单 (官方媒体/专业财经媒体/主流门户 → 真实性较高)
NEWS_OFFICIAL_SOURCES = (
    "新华社", "人民日报", "央视", "中国证券报", "上海证券报", "证券时报", "证券日报",
    "财联社", "第一财经", "21世纪经济报道", "每日经济新闻", "经济参考报", "界面新闻",
    "中国基金报", "经济观察报", "澎湃新闻", "华夏时报", "金融时报", "证券之星",
    "中新经纬", "中国网财经", "北京商报", "新京报", "时代周报", "南方财经",
    "新浪财经", "腾讯财经", "网易财经", "搜狐财经", "和讯", "东方财富研究中心",
    "中国证券网", "上海证券", "国际金融报", "红周刊",
)
# 散户/传闻来源 (股吧/社交平台 → 小道消息, 真实性低, 不采信)
NEWS_RUMOR_SOURCES = ("股吧", "淘股吧", "雪球", "微博", "贴吧", "知乎", "论坛", "散户", "龙虎榜吧")
# 利好关键词 (新闻标题)
NEWS_BULL_KW = (
    "中标", "签约", "签署", "合同", "订单", "增持", "回购", "并购", "重组", "注入",
    "战略合作", "获批", "过会", "投产", "涨价", "提价", "扩产", "新产品", "新品",
    "突破", "创历史新高", "创新高", "扭亏", "预增", "盈利", "分红", "送转", "高送转",
    "涨停", "超预期", "供货", "专利", "获准", "斩获", "大单", "上调", "供应商",
    "量价齐升", "满产", "满负荷", "提质增效", "龙头",
)
# 利空关键词 (新闻标题)
NEWS_BEAR_KW = (
    "减持", "质押", "冻结", "诉讼", "仲裁", "违规", "处罚", "立案", "调查", "问询",
    "警示", "退市", "预减", "预亏", "首亏", "续亏", "亏损", "商誉减值", "减值",
    "解禁", "延期", "终止", "取消", "下修", "跌停", "破发", "ST", "戴帽", "非标",
    "风险提示", "计提", "爆雷", "爆仓", "违约", "退市风险", "业绩下滑", "利空",
    "被监管", "停牌核查", "异常波动", "业绩变脸",
)
# 反向表述排除 (先检查: 含这些词 → 中性, 避免把"解除质押/取消减持"误判为利空)
NEWS_NEUTRAL_KW = (
    "解除质押", "解除冻结", "撤销", "摘帽", "取消减持", "终止减持", "承诺不减持",
    "不受影响", "已澄清", "回应", "辟谣",
)
_NEWS_SIGNALS_CACHE = {}       # 进程内缓存: {ts_code: signals} (日度/早期两循环共用, 不重复拉取)
_HOT_RANK_CACHE = None         # 人气榜缓存 (全市场一次性)
_DZJY_CACHE_DATE = None        # 大宗交易按日期缓存
_DZJY_CACHE = None


def _news_source_level(source, title):
    """新闻真实性甄别 → 'official'(权威/官方披露) / 'rumor'(散户传闻) / 'neutral'(其他)
    甄别逻辑: ①标题含公告/业绩说明会/年报等披露特征 → official (内容即权威)
              ②来源为股吧/雪球等散户平台 → rumor (小道消息)
              ③来源在权威财经媒体/主流门户白名单 → official
              ④其余 → neutral (不采信, 不参与评分)
    """
    src = str(source or "").strip()
    t = str(title or "")
    if not src and not t:
        return "neutral"
    # ① 公司披露特征 (公告/财报/业绩说明会/投资者关系) → 官方
    if any(k in t for k in ("公告", "业绩说明会", "年报", "中报", "季报", "半年报",
                            "回复问询", "澄清公告", "投资者关系", "调研纪要")):
        return "official"
    # ② 散户/传闻来源 → rumor
    for k in NEWS_RUMOR_SOURCES:
        if k in src:
            return "rumor"
    # ③ 权威财经媒体/主流门户 → official
    for k in NEWS_OFFICIAL_SOURCES:
        if k in src:
            return "official"
    return "neutral"


def _news_sentiment(title):
    """新闻标题 → 'pos'(利好) / 'neg'(利空) / None(中性或反向表述)
    反向表述先排除 (解除质押/取消减持/辟谣等不按利空处理); 无明确关键词 → None
    """
    t = str(title or "")
    if not t:
        return None
    for k in NEWS_NEUTRAL_KW:
        if k in t:
            return None
    for kw in NEWS_BEAR_KW:
        if kw in t:
            return "neg"
    for kw in NEWS_BULL_KW:
        if kw in t:
            return "pos"
    return None


def fetch_stock_news(symbol):
    """包装 _ak.fetch_stock_news_ak, 失败返回 []"""
    if not _AK_OK:
        return []
    try:
        return _ak.fetch_stock_news_ak(symbol) or []
    except Exception:
        return []


def fetch_hot_rank():
    """人气榜 {ts_code: rank}, 进程内缓存"""
    global _HOT_RANK_CACHE
    if _HOT_RANK_CACHE is not None:
        return _HOT_RANK_CACHE
    _HOT_RANK_CACHE = {}
    if not _AK_OK:
        return _HOT_RANK_CACHE
    try:
        _HOT_RANK_CACHE = _ak.fetch_hot_rank_ak() or {}
    except Exception:
        _HOT_RANK_CACHE = {}
    return _HOT_RANK_CACHE


def fetch_dzjy(latest_date):
    """当日大宗交易 {ts_code: {...}}, 按日期缓存"""
    global _DZJY_CACHE_DATE, _DZJY_CACHE
    td = str(latest_date).replace("-", "")
    if _DZJY_CACHE_DATE == td and _DZJY_CACHE is not None:
        return _DZJY_CACHE
    _DZJY_CACHE_DATE = td
    _DZJY_CACHE = {}
    if not _AK_OK:
        return _DZJY_CACHE
    try:
        _DZJY_CACHE = _ak.fetch_dzjy_ak(latest_date) or {}
    except Exception:
        _DZJY_CACHE = {}
    return _DZJY_CACHE


def _news_in_window(pub_time, latest_date, days=7):
    """新闻发布时间(如 '2026-08-21 17:36:00') 是否在 latest_date 往前 days 天内"""
    d = str(pub_time or "")[:10].replace("-", "")
    if not re.match(r"^\d{8}$", d):
        return False
    latest = str(latest_date).replace("-", "")
    try:
        d0 = datetime.strptime(d, "%Y%m%d")
        d1 = datetime.strptime(latest, "%Y%m%d")
    except ValueError:
        return False
    return d0 <= d1 and (d1 - d0).days <= days


def fetch_news_signals(codes, latest_date, cfg=None):
    """批量个股新闻信号 (消息面, 2026-08-23)
    返回 {ts_code: {"official_pos","official_neg","rumor_pos","rumor_neg","headlines"}}
      official_*: 权威/公告类新闻 利好/利空条数 (真实消息, 采信 → 影响分数)
      rumor_*:    散户/传闻类新闻 利好/利空条数 (小道消息, 仅标签不采信)
      headlines:  权威新闻标题前 4 条 (供前端展示)
    时间窗: 仅统计 latest_date 往前 window_days 天内新闻 (旧闻不影响评分)
    进程内缓存共享, 日度/早期两循环不重复拉取; 缺失/失败 → 该股无信号 (不误杀)
    """
    cfg = cfg or CONFIG["score"].get("news") or {}
    days = int(cfg.get("window_days", 7))
    max_fetch = int(cfg.get("max_fetch", 50))
    todo = [c for c in codes if c not in _NEWS_SIGNALS_CACHE][:max_fetch]
    # 缓存中已有的信号一并返回, 避免被 apply 侧忽略
    signals = {c: _NEWS_SIGNALS_CACHE[c] for c in codes if c in _NEWS_SIGNALS_CACHE}
    for code in todo:
        symbol = str(code).split(".")[0]
        news = fetch_stock_news(symbol)
        sig = {"official_pos": 0, "official_neg": 0, "rumor_pos": 0, "rumor_neg": 0, "headlines": []}
        for a in news:
            if not _news_in_window(a.get("time"), latest_date, days):
                continue  # 旧闻不入窗
            sent = _news_sentiment(a.get("title"))
            if sent is None:
                continue
            lvl = _news_source_level(a.get("source"), a.get("title"))
            if lvl == "official":
                sig["official_pos" if sent == "pos" else "official_neg"] += 1
                if len(sig["headlines"]) < 4:
                    sig["headlines"].append({"title": str(a.get("title", ""))[:60],
                                             "source": str(a.get("source", "")),
                                             "sentiment": sent, "time": str(a.get("time", ""))})
            elif lvl == "rumor":
                sig["rumor_pos" if sent == "pos" else "rumor_neg"] += 1
            # neutral: 不采信, 不参与评分也不展示
        _NEWS_SIGNALS_CACHE[code] = sig
        signals[code] = sig
    return signals


def score_news_funnel(ns, hot_rank=None, dzjy=None, code="", cfg=None):
    """消息面评分 (加分制 + 防御性扣分, 2026-08-23)
    返回 (bonus, deduct, tags); 缺失 → (0, 0, []) 不误杀
    - 权威利好≥1 且无权威利空 → +bonus, 标签【利好消息】(真实消息采信)
    - 权威利空≥1 → -deduct, 标签【利空消息】(防御性, 不否决; 利好利空并存时利空优先)
    - 传闻利好/利空 → 仅标签【传闻利好】/【消息待证】, 不加减分 (甄别真实性: 小道消息不采信)
    - 人气榜前 hot_rank_top → 标签【人气榜TOP】(散户情绪提示)
    - 大宗机构专用买入 → 标签【机构大宗买入】; 折溢率<-阈值 → 标签【大宗折价】
    """
    cfg = cfg or CONFIG["score"].get("news") or {}
    bonus = int(cfg.get("bonus", 2))
    deduct = int(cfg.get("deduct", 3))
    hot_top = int(cfg.get("hot_rank_top", 50))
    dzjy_disc = abs(float(cfg.get("dzjy_discount_max", 0.03)))
    ns = ns or {}
    op = int(ns.get("official_pos", 0) or 0)
    on = int(ns.get("official_neg", 0) or 0)
    rp = int(ns.get("rumor_pos", 0) or 0)
    rn = int(ns.get("rumor_neg", 0) or 0)
    nb = nd = 0
    tags = []
    if op > 0 and on == 0:
        nb += bonus
        tags.append("利好消息")
    elif on > 0:
        nd += deduct
        tags.append("利空消息")
    elif rp > 0 and rn == 0:
        tags.append("传闻利好")
    elif rn > 0:
        tags.append("消息待证")
    if hot_rank and code and code in hot_rank and int(hot_rank[code]) <= hot_top:
        tags.append("人气榜TOP")
    if dzjy and code and code in dzjy:
        dz = dzjy[code]
        if cfg.get("dzjy_inst_buy", True) and dz.get("inst_buy"):
            tags.append("机构大宗买入")
        if dz.get("avg_discount", 0) < -dzjy_disc:
            tags.append("大宗折价")
    return nb, nd, tags


def apply_news_signals(results, latest_date, cfg=None):
    """消息面统一增强 (2026-08-23): 对已通过五层漏斗的结果批量拉取新闻/人气/大宗,
    按真实性甄别加减分 + 追加消息标签。日度/早期两循环共用 (新闻进程内缓存, 不重复拉取)
    在 results 排序/门槛之前调用, 消息面可影响最终排序与入选
    """
    if not results:
        return results
    ncfg = (cfg or CONFIG).get("score", {}).get("news") or {}
    codes = [r["ts_code"] for r in results]
    ns_map = fetch_news_signals(codes, latest_date, cfg=ncfg) if codes else {}
    hot_rank = fetch_hot_rank()
    dzjy_map = fetch_dzjy(latest_date)
    hit = 0
    for r in results:
        ns = ns_map.get(r["ts_code"])
        nb, nd, ntags = score_news_funnel(ns, hot_rank, dzjy_map, r["ts_code"], cfg=ncfg)
        if nb or nd:
            r["score_total"] = r["score_total"] + nb - nd
        r["score_news"] = nb - nd
        r["news"] = {
            "official_pos": (ns or {}).get("official_pos", 0),
            "official_neg": (ns or {}).get("official_neg", 0),
            "rumor_pos": (ns or {}).get("rumor_pos", 0),
            "rumor_neg": (ns or {}).get("rumor_neg", 0),
            "headlines": (ns or {}).get("headlines", []),
        }
        if ntags:
            hit += 1
            r["tags"] = (r["tags"] + " + " + " + ".join(ntags)) if r.get("tags") else " + ".join(ntags)
    if ns_map:
        print(f"  [消息面] {len(ns_map)} 只有新闻信号, {hit} 只打消息标签 (真实消息加减分已计入总分)")
    return results


# ============================================================
# 9. 业绩预告信号 (earnings-interpretation 预期差 + industry-researcher 催化, 2026-08-23)
# ============================================================
# 预告类型分组 (东财 PREDICT_TYPE 值, 子串匹配兼容表述差异)
YJYG_POS = ("预增", "略增", "续盈", "扭亏", "减亏")
YJYG_NEG = ("预减", "略减", "首亏", "续亏", "增亏")


def _yjyg_tag(predict_type):
    """业绩预告类型 → (positive, tag); 中性类型(不确定等)返回 (None, "")"""
    t = str(predict_type or "")
    for kw in YJYG_POS:
        if kw in t:
            return True, ("扭亏为盈" if "扭亏" in t else "业绩预增")
    for kw in YJYG_NEG:
        if kw in t:
            return False, ("首亏预警" if "首亏" in t else "业绩预减")
    return None, ""


def fetch_yjyg(latest_date):
    """当日业绩预告信号 (2026-08-23, 预期差维度: 披露了什么 → 相对预期如何)
    主源: 东财 RPT_PUBLIC_OP_NEWPREDICT (当日披露, IS_LATEST=1 取最新修正)
    返回 {ts_code: {"predict_type": str, "positive": bool, "tag": str, "ratio": float}}
      positive=True → 预增/扭亏/略增/续盈/减亏 (加分+利好标签)
      positive=False → 预减/略减/首亏/续亏/增亏 (扣分+风险标签)
    非交易日/当日无预告/接口失败 → 返回 {} (缺失跳过, 不误杀)
    """
    td = str(latest_date).replace("-", "")
    d = None
    try:
        end_date = f"{td[:4]}-{td[4:6]}-{td[6:]}"
        url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
               "?sortColumns=NOTICE_DATE&sortTypes=-1&pageSize=500&pageNumber=1"
               "&reportName=RPT_PUBLIC_OP_NEWPREDICT"
               "&columns=SECUCODE,PREDICT_TYPE,PREDICT_RATIO_LOWER,PREDICT_RATIO_UPPER,REPORT_DATE"
               f"&filter=(NOTICE_DATE=%27{end_date}%27)(IS_LATEST=%221%22)")
        d = _em_get_json(url, timeout=15)
    except Exception as e:
        print(f"  ⚠️ 业绩预告拉取失败: {str(e)[:50]}")
        return {}
    data = ((d or {}).get("result") or {}).get("data") or []
    if not data:
        return {}
    result = {}
    for row in data:
        sc = str(row.get("SECUCODE") or "")
        if not re.match(r"^\d{6}\.(SH|SZ|BJ)$", sc):
            continue
        code = sc.split(".")[0] + "." + sc.split(".")[1]
        pos, tag = _yjyg_tag(row.get("PREDICT_TYPE"))
        if pos is None:
            continue  # 中性类型(不确定等)无信号意义
        lo = _f10_float(row.get("PREDICT_RATIO_LOWER"))
        hi = _f10_float(row.get("PREDICT_RATIO_UPPER"))
        ratio = None
        if lo is not None and hi is not None:
            ratio = round((lo + hi) / 2, 2)
        elif lo is not None:
            ratio = lo
        elif hi is not None:
            ratio = hi
        result[code] = {
            "predict_type": str(row.get("PREDICT_TYPE") or ""),
            "positive": pos,
            "tag": tag,
            "ratio": ratio,
        }
    if result:
        pos_n = sum(1 for v in result.values() if v["positive"])
        print(f"  ✅ 业绩预告: {len(result)} 只披露 (利好{pos_n} / 利空{len(result)-pos_n})")
    return result


# ============================================================
# 7. 第一层: 大盘环境全局校验 (20日均线, 不合格终止选股)
# ============================================================

def count_strong_sectors(in_list_records, threshold=95):
    """统计 RPS5 入选(强势)板块数: RPS5 ≥ threshold 的板块数量
    供"板块动量强"替代路径判定 (结构性行情: 板块强但全市场广度不足时豁免上涨占比)
    """
    n = 0
    for rec in in_list_records or []:
        if not isinstance(rec, dict):
            continue
        r5 = rec.get("RPS5")
        try:
            if r5 is not None and float(r5) >= threshold:
                n += 1
        except (TypeError, ValueError):
            continue
    return n


def check_market_env(index_map, daily_map, latest_date, market_flow=None, strong_sector_count=None,
                     hsgt_breadth=None):
    """第一层: 大盘环境全局校验 (严格二态: pass 选股 / reject 输出0只)
    规则(可配置 CONFIG["market"]):
      硬底线 (无替代, 任一不满足 → reject 输出0只):
        - 至少 need_above 个指数站上 20 日线
        - 跌停家数 ≤ limit_down_max
      广度条件 (二选一通过):
        - 上涨家数占比 ≥ advance_ratio_min (40%)
        - 板块动量替代: sector_alt_allow 开启 且 strong_sector_count ≥ sector_strong_min (8)
          → 视为板块动量强, 豁免上涨占比 (结构性行情: 少数主线强但全市场普跌时不误否决)
      - 大盘主力资金: market_flow 仅做提示标签(近N日连续净流出→note), 完全不参与否决
      - 北向广度: hsgt_breadth 仅做提示标签(北向成分股上涨占比低→note), 完全不参与否决
      - 指数数据完全缺失 → 默认安全(避免接口故障误否决全市场)
    返回 (ok, market_status):
      ok: level=="pass" 才 True; level=="reject" → False (前端展示"大盘环境不合格, 暂无推荐")
      market_status: {is_safe, level, rule, reason, indexes[{code,name,close,ma,above}],
                      advance_ratio, limit_down_count, limit_up_count, main_flow,
                      sector_strong_count, alt_pass, hsgt_breadth, note}
    """
    cfg = CONFIG["market"]
    index_names = cfg.get("index_names", {})
    rows = []
    above_cnt = 0
    for code in cfg["index_codes"]:
        idx_df = index_map.get(code)
        name = index_names.get(code, code)
        if idx_df is None or len(idx_df) == 0:
            rows.append({"code": code, "name": name, "close": None, "ma": None, "above": None})
            continue
        df = idx_df.sort_values("trade_date") if "trade_date" in idx_df.columns else idx_df
        closes = [float(c) for c in df["close"].tolist() if pd.notna(c)]
        if len(closes) < cfg["ma_period"]:
            rows.append({"code": code, "name": name, "close": None, "ma": None, "above": None})
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

    # 市场情绪统计: 上涨/下跌家数 + 涨跌停家数 (涨停近似 pct>=limit_up_pct, 跌停近似 pct<=-9.8)
    adv, dec, limit_down, limit_up = 0, 0, 0, 0
    if latest_date in daily_map:
        for _, r in daily_map[latest_date].iterrows():
            chg = r.get("pct_chg")
            if pd.notna(chg):
                f = float(chg)
                if f > 0:
                    adv += 1
                elif f < 0:
                    dec += 1
                if f <= -9.8:
                    limit_down += 1
                if f >= cfg["limit_up_pct"]:
                    limit_up += 1
    advance_ratio = (adv / (adv + dec)) if (adv + dec) > 0 else None

    # 严格二态判定 (rows 全为 above=None 视为指数数据完全缺失)
    alt_note = ""
    if all(r.get("above") is None for r in rows):
        # 指数数据完全缺失 → 默认安全, 避免接口故障误否决全市场
        if cfg.get("default_safe", True):
            level, reason = "pass", "指数数据缺失, 默认安全"
        else:
            level, reason = "reject", "指数数据不足, 无法确认站上20日线"
    else:
        pass_ma = above_cnt >= cfg["need_above"]
        pass_sent = advance_ratio is None or advance_ratio >= cfg["advance_ratio_min"]
        pass_ld = limit_down <= cfg.get("limit_down_max", 10)
        # 板块动量替代: 指数/跌停为硬底线不豁免; 仅豁免"上涨占比"广度条件
        alt_allow = bool(cfg.get("sector_alt_allow", True))
        sector_strong = alt_allow and strong_sector_count is not None \
            and strong_sector_count >= cfg.get("sector_strong_min", 8)
        if pass_ma and pass_ld and (pass_sent or sector_strong):
            level, reason = "pass", ""
            if not pass_sent and sector_strong:
                alt_note = (f"；上涨占比{advance_ratio:.0%}<{cfg['advance_ratio_min']:.0%} 但强势板块"
                            f"{strong_sector_count}个≥{cfg.get('sector_strong_min', 8)}, 板块动量强, 豁免通过")
        else:
            level = "reject"
            if not pass_ma:
                below = [r["name"] for r in rows if r.get("above") is False]
                if below:
                    reason = " / ".join(below) + f" 未站上{cfg['ma_period']}日线"
                else:
                    reason = f"仅 {above_cnt} 个指数站上{cfg['ma_period']}日线 (需 {cfg['need_above']} 个)"
            elif not pass_ld:
                reason = f"跌停 {limit_down} 家 > {cfg.get('limit_down_max', 10)} 家"
            else:
                reason = f"上涨家数占比 {advance_ratio:.0%} < {cfg['advance_ratio_min']:.0%}"

    ok = (level != "reject")

    # 大盘主力资金统计 (真实值, 仅提示不否决: 连续多日净流出 → note 附加资金偏弱提示)
    main_flow = None
    note = {"pass": "大盘环境合格, 继续选股",
            "reject": "大盘环境不合格, 终止选股"}[level]
    if alt_note:
        note = note + alt_note
    if market_flow:
        flow_days = cfg.get("main_flow_days", 3)
        dates = sorted([d for d in market_flow.keys() if d <= latest_date])[-flow_days:]
        if dates:
            neg_days = sum(1 for d in dates if (market_flow[d].get("main_net") or 0) < 0)
            total_net = float(sum((market_flow[d].get("main_net") or 0) for d in dates))
            weak = neg_days >= cfg.get("main_flow_warn_neg", 2)
            main_flow = {
                "days": len(dates),
                "neg_days": neg_days,
                "total_net_yi": round(total_net / 1e8, 1),  # 合计主力净流入(亿元, 负=净流出)
                "verdict": "资金偏弱" if weak else "资金正常",
            }
            if weak:
                note = note + f"；大盘主力资金近{len(dates)}日{neg_days}日净流出"

    # 北向广度提示 (仅提示不否决, 与 main_flow 同级): 北向成分股上涨占比低于阈值 → note 附加
    hsgt_stat = None
    if hsgt_breadth and hsgt_breadth.get("north_ratio") is not None:
        ratio = float(hsgt_breadth["north_ratio"])
        hsgt_stat = {
            "up": int(hsgt_breadth.get("north_up", 0)),
            "down": int(hsgt_breadth.get("north_down", 0)),
            "ratio": round(ratio, 2),
        }
        if ratio < cfg.get("hsgt_ratio_warn", 0.40):
            note = note + (f"；北向广度偏弱(上涨占比{ratio:.0%}<{cfg.get('hsgt_ratio_warn', 0.40):.0%})")

    market_status = {
        "is_safe": ok,
        "level": level,
        "rule": f"至少 {cfg['need_above']} 个指数站上 {cfg['ma_period']} 日线 且 上涨占比≥{cfg['advance_ratio_min']:.0%} 且 跌停≤{cfg.get('limit_down_max', 10)}家",
        "reason": reason,
        "indexes": rows,
        "advance_ratio": round(advance_ratio, 2) if advance_ratio is not None else None,
        "limit_down_count": limit_down,
        "limit_up_count": limit_up,
        "main_flow": main_flow,
        "sector_strong_count": strong_sector_count,
        "alt_pass": bool(alt_note),
        "hsgt_breadth": hsgt_stat,
        "note": note,
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
      - PE-TTM > max_pe (远超行业上限); PE-TTM<0 不否决, 评分阶段强制上限 pe_neg_cap 分
      - 净利润 < 0 (fina_map; fina 缺失降级 PE>0 判定, 不误杀)
      - 资产负债率 > max_debt_ratio
      - 股权质押 > max_pledge_ratio / 商誉 > max_goodwill_ratio (有数据则强制校验, 缺失不剔除)
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
    # PE-TTM 远超上限一票否决; PE-TTM<0 不否决(评分阶段基本面分上限 pe_neg_cap)
    pe_bad = df["pe_ttm"].notna() & (df["pe_ttm"] > cfg["max_pe"])
    _reject_rows(df[pe_bad], rejects, f"PE-TTM>{cfg['max_pe']}")
    df = df[~pe_bad]
    # 财务指标 (fina_map: 净利润/资产负债率/质押/商誉; 缺失不剔除)
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
            # 质押/商誉: 有数据则强制校验, 缺失不剔除 (一票否决)
            plg = fr.get("pledge_ratio")
            if plg is not None and float(plg) > cfg["max_pledge_ratio"]:
                rejects.append({"ts_code": code, "name": df[df["ts_code"] == code]["name"].iloc[0],
                                "reason": f"股权质押>{cfg['max_pledge_ratio']}%"})
                continue
            gwl = fr.get("goodwill_ratio")
            if gwl is not None and float(gwl) > cfg["max_goodwill_ratio"]:
                rejects.append({"ts_code": code, "name": df[df["ts_code"] == code]["name"].iloc[0],
                                "reason": f"商誉>{cfg['max_goodwill_ratio']}%"})
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


def _turnover_range(mv_wan, cfg=None):
    """换手率区间按流通市值(万元)自适应 (大盘股天然换手低, 小盘股活跃):
      <50亿:    [3%, 25%]
      50~200亿: [2%, 20%]
      >200亿:   [1%, 15%]
    流通市值缺失 → 兜底区间 [turnover_min, turnover_max]
    cfg: 完整 CONFIG 或已提取的 capital 子配置均可
    """
    if cfg is None:
        cap = CONFIG["capital"]
    elif "capital" in cfg:
        cap = cfg["capital"]
    else:
        cap = cfg
    bands = cap.get("turnover_bands", [])
    if mv_wan is None or pd.isna(mv_wan):
        return cap["turnover_min"], cap["turnover_max"]
    for b in bands:
        if b.get("max_mv") is None or float(mv_wan) < b["max_mv"]:
            return b["min"], b["max"]
    return cap["turnover_min"], cap["turnover_max"]


def screen_capital(stocks_df, daily_map, latest_date, mflow_map, cfg=None, lhb_map=None):
    """第四层: 资金面达标校验 (全部核心项必达, 任一不满足即淘汰)
    规则(可配置 CONFIG["capital"]):
      - 换手率 ∈ 市值自适应区间 (turnover_bands; 流通市值缺失用 turnover_min/max 兜底)
      - 量比 > vol_ratio_min (量能较前期放大; 当日涨停豁免量比校验, 打【涨停无量】标签)
      - 单日成交额 > amount_min (daily_map 最新日 amount, 千元→元)
      - 主力资金: 当日净额>0 或 近3日累计净流入>0 (mflow_map; 缺失跳过, 不误杀)
      - 龙虎榜: lhb_map 仅标注不否决 (净买额<0 → lhb_sell=True, 评分阶段扣分+【龙虎榜出货】标签)
    返回 (passed_df, cap_details): cap_details={code:{turnover, vol_ratio, amount, net_inflow_3d, limit_up, lhb_net, lhb_reason, lhb_sell}}
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
    limit_up_pct = cfg.get("limit_up_pct", 9.8)
    for _, s in df.iterrows():
        code = s["ts_code"]
        # 换手率区间 (按流通市值自适应)
        tr = s.get("turnover_rate")
        if tr is None or pd.isna(tr):
            continue
        tr_f = float(tr)
        lo, hi = _turnover_range(s.get("circ_mv"), cfg)
        if not (lo <= tr_f <= hi):
            continue
        # 量比: 当日涨停(涨幅≥limit_up_pct)豁免量比校验 (一字涨停无量不误杀)
        pct = s.get("pct_chg")
        is_limit_up = pct is not None and not pd.isna(pct) and float(pct) >= limit_up_pct
        vr = s.get("vol_ratio")
        if vr is None or pd.isna(vr):
            vr = _calc_vol_ratio_fallback(daily_map, code, latest_date)
        if not is_limit_up and (vr is None or float(vr) <= cfg["vol_ratio_min"]):
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
            "turnover": round(tr_f, 2),
            "vol_ratio": round(float(vr), 2) if vr is not None else None,
            "amount": round(amt, 0),
            "net_inflow_3d": net3,
            "limit_up": is_limit_up,
        }
        # 龙虎榜信号标注 (缺失跳过不误杀; 仅标注, 否决/加分在评分阶段)
        if lhb_map and code in lhb_map:
            cap_details[code]["lhb_net"] = lhb_map[code].get("lhb_net")
            cap_details[code]["lhb_reason"] = lhb_map[code].get("reason", "")
            cap_details[code]["lhb_sell"] = bool((lhb_map[code].get("lhb_net") or 0) < 0)
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


def _merge_same_source_sectors(sector_names, cfg=None):
    """同源板块合并 → 真实共振板块数 (防题材重复计数虚高板块热度分)
    例: 个股同属 ["算力", "AI服务器", "半导体"] → 算力/AI服务器同属一个关键词组
        (CONFIG["pool"]["sector_groups"]) → 真实共振 = 2 (算力主线 + 半导体)
    打分/标签使用去重后的真实共振数, 避免"算力/AI服务器/数据中心"多板块虚假拉高板块热度 40 分
    """
    groups = (cfg or CONFIG)["pool"].get("sector_groups", [])
    used_groups = set()
    count = 0
    for name in sector_names or []:
        gid = None
        for gi, g in enumerate(groups):
            if any(kw in name for kw in g):
                gid = gi
                break
        if gid is not None:
            if gid not in used_groups:
                used_groups.add(gid)
                count += 1
        else:
            count += 1  # 不在同源组内的板块独立计数
    return count


def _load_blacklist(cfg=None):
    """加载手工黑名单 (大额解禁/减持预告/利空个股), L2 候选池直接剔除
    文件: static/blacklist.json, 结构: {"codes": ["600000.SH", ...]}
    文件缺失/解析失败 → 返回空集合 (不阻断选股)
    """
    fname = (cfg or CONFIG)["pool"].get("blacklist_file", "blacklist.json")
    fpath = STATIC_DIR / fname
    if not fpath.exists():
        return set()
    try:
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
        return {str(c) for c in (data.get("codes") or []) if c}
    except Exception:
        return set()


def _report_age_days(fina_row, latest_date):
    """财报距今天数 (fina_row['end_date'] 格式 YYYYMMDD), 缺失/异常返回 None"""
    ed = (fina_row or {}).get("end_date") or ""
    if not ed or not latest_date or latest_date < str(ed):
        return None
    try:
        return (datetime.strptime(latest_date, "%Y%m%d") - datetime.strptime(str(ed), "%Y%m%d")).days
    except Exception:
        return None


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
    超买检测(不否决, 评分扣分+打【技术超买】标签): RSI > rsi_overbought / CCI > cci_overbought
    返回 (passed_df, tech_details): tech_details={code:{hits:[指标名], hit_count, overbought, rsi, cci}}
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
            detail = {"hits": hits, "hit_count": len(hits)}
            # 超买检测: RSI>rsi_overbought / CCI>cci_overbought → 技术超买 (不否决, 评分扣分)
            rsi_obj = calc_rsi(closes)
            cci_obj = calc_cci(highs, lows, closes)
            rsi_v = rsi_obj["rsi"] if rsi_obj else None
            cci_v = cci_obj["cci"] if cci_obj else None
            if rsi_v is not None:
                detail["rsi"] = round(rsi_v, 1)
            if cci_v is not None:
                detail["cci"] = round(cci_v, 1)
            ob = []
            if rsi_v is not None and rsi_v > cfg["rsi_overbought"]:
                ob.append("rsi")
            if cci_v is not None and cci_v > cfg["cci_overbought"]:
                ob.append("cci")
            if ob:
                detail["overbought"] = "both" if len(ob) == 2 else ob[0]
            tech_details[code] = detail
            keep.append(code)
    return df[df["ts_code"].isin(keep)], tech_details


# ============================================================
# 6. 主流程 v2.0 —— "果断买入"五维共振
# ============================================================

# 模块级缓存: 最近一次大盘状态(供 save_recommendations 写入 banner)
_LAST_MARKET_STATUS = None
# 模块级缓存: 最近一次候选股过滤日志(供 save_recommendations 写 candidate_log.json)
_LAST_CANDIDATE_LOG = []
# 早期启动挖掘独立缓存(不污染原版模块级状态)
_LAST_EARLY_MARKET_STATUS = None
_LAST_EARLY_CANDIDATE_LOG = []


def _review_deduct_map(review_data):
    """从 daily_review.json 提取评分扣分映射 (复盘驱动选股, 不否决只降权):
      fatigue_sectors: 成熟疲劳板块 (health.mature_fatigue) → 个股总分扣 fatigue_deduct
      danger_sectors:  风险等级 danger 时的顶背离板块 (risk.diverge) → 个股总分扣 danger_deduct
    复盘无数据 / 无对应信号 → 返回空集合 (不影响选股)
    """
    fatigue, danger = set(), set()
    if not review_data:
        return fatigue, danger
    health = review_data.get("health") or {}
    for f in health.get("mature_fatigue") or []:
        nm = f.get("name") if isinstance(f, dict) else f
        if nm:
            fatigue.add(str(nm))
    risk = review_data.get("risk") or {}
    if risk.get("level") == "danger":
        for dv in risk.get("diverge") or []:
            nm = dv.get("name") if isinstance(dv, dict) else dv
            if nm:
                danger.add(str(nm))
    return fatigue, danger


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
    """基本面 15 分 (缺失子项记0分, 不淘汰):
      - 净利润>0: 4分 (fina缺失降级 PE>0: 2分)
      - PE-TTM 0-50: 4分; 50-200: 2分
      - 流通市值 50-600亿: 4分; >600亿: 1分
      - 资产负债率 ≤55%: 3分; 55-70%: 1分
    """
    score = 0
    pe = s.get("pe_ttm")
    mv = s.get("circ_mv")
    np_val = fina_row.get("net_profit") if fina_row else None
    debt = fina_row.get("debt_to_assets") if fina_row else None
    if np_val is not None and float(np_val) > 0:
        score += 4
    elif pe is not None and not pd.isna(pe) and float(pe) > 0:
        score += 2
    if pe is not None and not pd.isna(pe):
        p = float(pe)
        if 0 < p <= 50:
            score += 4
        elif p <= 200:
            score += 2
    if mv is not None and not pd.isna(mv):
        mv_yi = float(mv) / 10000
        if 50 <= mv_yi <= 600:
            score += 4
        elif mv_yi > 600:
            score += 1
    if debt is not None and not pd.isna(debt):
        d = float(debt)
        if d <= 55:
            score += 3
        elif d <= 70:
            score += 1
    return min(score, 15)


def score_quality_funnel(fina_row, cfg=None):
    """盈利质量加分/扣分 (2026-08-23, financial-modeling/earnings-interpretation 方法论):
    加分制(上限 CONFIG["score"]["quality"]["bonus_cap"]=5, 缺失子项记0不否决):
      - 营收同比 >0: +2; ≥30%: +3 (成长性)
      - 毛利率 ≥40%: +2; 20-40%: +1 (盈利能力)
      - ROE ≥15%: +2; 8-15%: +1 (资本效率)
      - 每股经营现金流 >0: +2 (盈利现金含量)
      - ROIC ≥8%: +1 (投入资本回报)
    防御性扣分(不否决, 仅总分扣减+标签):
      - 经营现金流<0 且 净利>0: 扣2 + 标签【现金流为负】
      - 净利增速-营收增速 > margin_gap_max(40pct): 扣1 + 标签【利润质量存疑】
    返回 (bonus, deduct, tags); 无财务数据 → (0, 0, [])
    """
    bonus = 0
    deduct = 0
    tags = []
    qcfg = (cfg or CONFIG)["score"].get("quality") or {}
    if not fina_row:
        return 0, 0, []
    try:
        ry = fina_row.get("revenue_yoy")
        gm = fina_row.get("gross_margin")
        roe = fina_row.get("roe")
        ocf = fina_row.get("ocf_ps")
        roic = fina_row.get("roic")
        np_val = fina_row.get("net_profit")
        ny = fina_row.get("netprofit_yoy")
        # ---- 加分 (缺失记0, 不否决) ----
        if ry is not None and not pd.isna(ry):
            ry = float(ry)
            if ry >= qcfg.get("revenue_yoy_high_min", 30.0):
                bonus += qcfg.get("revenue_yoy_high", 3)
            elif ry > 0:
                bonus += qcfg.get("revenue_yoy_pos", 2)
        if gm is not None and not pd.isna(gm):
            gm = float(gm)
            if gm >= qcfg.get("gross_margin_high_min", 40.0):
                bonus += qcfg.get("gross_margin_high", 2)
            elif gm >= qcfg.get("gross_margin_mid_min", 20.0):
                bonus += qcfg.get("gross_margin_mid", 1)
        if roe is not None and not pd.isna(roe):
            roe = float(roe)
            if roe >= qcfg.get("roe_high_min", 15.0):
                bonus += qcfg.get("roe_high", 2)
            elif roe >= qcfg.get("roe_mid_min", 8.0):
                bonus += qcfg.get("roe_mid", 1)
        if ocf is not None and not pd.isna(ocf) and float(ocf) > 0:
            bonus += qcfg.get("ocf_pos", 2)
        if roic is not None and not pd.isna(roic) and float(roic) >= qcfg.get("roic_min", 8.0):
            bonus += qcfg.get("roic_pos", 1)
        bonus = min(bonus, qcfg.get("bonus_cap", 5))
        # ---- 防御性扣分 (不否决) ----
        if ocf is not None and not pd.isna(ocf) and float(ocf) < 0 and np_val is not None and not pd.isna(np_val) and float(np_val) > 0:
            deduct += qcfg.get("ocf_neg_deduct", 2)
            tags.append("现金流为负")
        if ny is not None and ry is not None and not pd.isna(ny) and not pd.isna(ry):
            gap = float(ny) - float(ry)
            if gap > qcfg.get("margin_gap_max", 40.0):
                deduct += qcfg.get("margin_gap_deduct", 1)
                tags.append("利润质量存疑")
    except Exception:
        return 0, 0, []
    return bonus, deduct, tags


def score_capital_funnel(cap, cfg=None):
    """资金面 40 分 (恢复 v2.0 梯度制, 满分=换手20+量比20):
      - 换手率 6-8%: 20分; 4-6%/8-10%: 15分; 10-12%: 10分; 其他: 0分
      - 量比 1.5-2.5: 20分; 1.3-1.5/2.5-3.5: 15分; ≥3.5: 5分; <1.3: 0分
    """
    score = 0
    tr = cap.get("turnover")
    if tr is not None:
        if 6 <= tr <= 8:
            score += 20
        elif (4 <= tr < 6) or (8 < tr <= 10):
            score += 15
        elif 10 < tr <= 12:
            score += 10
    vr = cap.get("vol_ratio")
    if vr is not None:
        if 1.5 <= vr <= 2.5:
            score += 20
        elif (1.3 <= vr < 1.5) or (2.5 < vr <= 3.5):
            score += 15
        elif vr > 3.5:
            score += 5
    return min(score, 40)


def score_technical_funnel(hit_count, cfg=None):
    """技术面 15 分: 达标指标数 × 5, 上限 15 (≥3 项即满分)"""
    return min(15, int(hit_count) * 5)


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


def _chase_deducts(code, s, df_by_code, sc_cfg, limit_up_pct=None):
    """短期追高抑制扣分 (日度/早期共用, 保证两套评分口径一致):
      1) 近5日涨幅 > gain5_threshold → 扣 gain5_deduct 【短期涨幅过高】
      2) 当天涨幅 ∈ [high_pos_pct, limit_up_pct) 未涨停 → 扣 high_pos_deduct 【高位追涨】
      3) 乖离 MA20 > ma20_dev_threshold → 扣 ma20_dev_deduct 【乖离过大】
    返回 {"deduct","chg5","ma20_dev","flags":{gain5_high,high_pos,dev_high},"tags":[]}
    """
    if limit_up_pct is None:
        limit_up_pct = CONFIG["capital"]["limit_up_pct"]
    deduct = 0
    flags = {"gain5_high": False, "high_pos": False, "dev_high": False}
    tags = []
    chg5 = None
    if df_by_code is not None and len(df_by_code) >= 6:
        closes5 = [float(c) for c in df_by_code["close"].tolist()]
        if closes5[-6] > 0:
            chg5 = (closes5[-1] / closes5[-6] - 1) * 100
    if chg5 is not None and chg5 > sc_cfg["gain5_threshold"]:
        deduct += sc_cfg["gain5_deduct"]
        flags["gain5_high"] = True
        tags.append("短期涨幅过高")
    pct_today = s.get("pct_chg")
    high_pos = (pct_today is not None and not pd.isna(pct_today)
                and sc_cfg["high_pos_pct"] <= float(pct_today) < limit_up_pct)
    if high_pos:
        deduct += sc_cfg["high_pos_deduct"]
        flags["high_pos"] = True
        tags.append("高位追涨")
    ma20_dev = None
    if df_by_code is not None and len(df_by_code) >= 20:
        closes20 = [float(c) for c in df_by_code["close"].tolist()]
        ma20 = sum(closes20[-20:]) / 20
        if ma20 > 0:
            ma20_dev = (closes20[-1] / ma20 - 1) * 100
    if ma20_dev is not None and ma20_dev > sc_cfg["ma20_dev_threshold"]:
        deduct += sc_cfg["ma20_dev_deduct"]
        flags["dev_high"] = True
        tags.append("乖离过大")
    return {"deduct": deduct, "chg5": chg5, "ma20_dev": ma20_dev,
            "flags": flags, "tags": tags}


def trend_regime_flags(code, s, df_by_code, cfg=None):
    """趋势/位置形态研判 (精准度增强, 纯函数可单测):
    基于个股日线收盘价判断个股处于何种趋势形态, 用于区分"反弹 vs 反转"并压制高位追涨:
      1) 60日区间涨跌: 取近 chg60_days 根收盘 (首根收→末根收), <0 → 下跌趋势反弹 【反弹未反转】
      2) 距 60 日最高点回撤: > retreat_pct_max(30%) → 【超跌反弹】(非强势新高, 情绪盘);
         回撤 ≤ retreat_near(15%) 且 60日涨幅>0 → 【贴近新高】(强势跟踪本义)
    返回 {chg60, retreat_pct, regime, flags{rebound,retreat,near_high}, tags}
    """
    tcfg = (cfg or CONFIG)["score"].get("trend") or {}
    days = int(tcfg.get("chg60_days", 60))
    out = {"chg60": None, "retreat_pct": None, "regime": None,
           "flags": {"rebound": False, "retreat": False, "near_high": False}, "tags": []}
    if df_by_code is None:
        return out
    if isinstance(df_by_code, dict):  # 兼容 {code: df} 映射 (调用处传单 df 时直接跳过)
        df_by_code = df_by_code.get(code)
    if df_by_code is None or len(df_by_code) < max(days, 6):
        return out
    closes = [float(c) for c in df_by_code["close"].tolist()]
    highs = [float(h) for h in df_by_code["high"].tolist()]
    win = closes[-(days + 1):] if len(closes) >= days + 1 else closes  # 首根作为起点
    if len(win) >= 6 and win[0] > 0:
        out["chg60"] = round((win[-1] / win[0] - 1) * 100, 2)
    if highs:
        hi60 = max(highs[-days:])
        last = closes[-1]
        if hi60 > 0:
            out["retreat_pct"] = round((last / hi60 - 1) * 100, 2)
    # 形态判定
    if out["chg60"] is not None and out["chg60"] < 0:
        out["regime"] = "rebound"        # 60日下跌趋势中的反弹
        out["flags"]["rebound"] = True
        out["tags"].append("反弹未反转")
    if out["retreat_pct"] is not None and out["retreat_pct"] <= -abs(float(tcfg.get("retreat_pct_max", 30.0))):
        out["regime"] = "retreat"        # 距60日高点回撤过深 (超跌反抽)
        out["flags"]["retreat"] = True
        out["tags"].append("超跌反弹")
    if (out["retreat_pct"] is not None and out["retreat_pct"] >= -abs(float(tcfg.get("retreat_near", 15.0)))
            and out["chg60"] is not None and out["chg60"] > 0):
        out["flags"]["near_high"] = True  # 贴近新高且中期向上 → 强势跟踪首选
        out["tags"].append("贴近新高")
    return out


def pe_high_flag(s, cfg=None):
    """高估值降权判定: PE-TTM > pe_high_threshold(100) → True (情绪股预警, 不回补基础分)"""
    tcfg = (cfg or CONFIG)["score"].get("trend") or {}
    pe = s.get("pe_ttm")
    if pe is None or pd.isna(pe):
        return False
    try:
        return float(pe) > float(tcfg.get("pe_high_threshold", 100))
    except Exception:
        return False


def _sentiment_cap_shown(shown, market_status, cfg=None):
    """情绪联动: 大盘上涨占比 < 阈值 且 指数未全面站稳 → 缩减推荐数量(只保留最强)
    触发时保留 top keep_ratio (至少 keep_min 只); 不触发原样返回。
    返回 (缩减后列表, 是否触发)
    """
    sc_cfg = (cfg or CONFIG)["score"]
    cap = sc_cfg.get("sentiment_cap") or {}
    if not cap:
        return shown, False
    adv = (market_status or {}).get("advance_ratio")
    if adv is None or adv >= cap.get("advance_ratio_min", 0.5):
        return shown, False
    indexes = (market_status or {}).get("indexes") or []
    above_n = sum(1 for r in indexes if r.get("above") is True)
    if above_n >= max(1, len(indexes)):
        return shown, False  # 指数全面站稳, 不缩减
    keep_n = max(int(cap.get("keep_min", 3)), int(len(shown) * cap.get("keep_ratio", 0.5)))
    if len(shown) <= keep_n:
        return shown, False
    return shown[:keep_n], True


def _cap_per_sector(rec_list, max_per):
    """输出层同板块控制: 以个股第一个强势板块为主板块, 每细分板块最多输出 max_per 只
    防止市场主线集中时推荐列表全部同一板块 (高度同质化)
    返回 (保留列表, 剔除列表)
    """
    if not max_per or max_per <= 0:
        return rec_list, []
    counts = {}
    kept, removed = [], []
    for r in rec_list:
        sectors = r.get("sectors") or []
        key = str(sectors[0]) if sectors else str(r.get("industry", ""))
        if counts.get(key, 0) >= max_per:
            removed.append(r)
        else:
            counts[key] = counts.get(key, 0) + 1
            kept.append(r)
    return kept, removed


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
    chg3 = chg5 = consec_up = ma20_dev = None
    if len(closes) >= 4:
        chg3 = (closes[-1] / closes[-4] - 1) * 100
    if len(closes) >= 6:
        chg5 = (closes[-1] / closes[-6] - 1) * 100
    if len(closes) >= 20 and sum(closes[-20:]) > 0:
        ma20 = sum(closes[-20:]) / 20
        if ma20 > 0:
            ma20_dev = (closes[-1] / ma20 - 1) * 100
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
        "chg3": chg3, "chg5": chg5, "ma20_dev": ma20_dev, "consec_up": consec_up,
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


def _build_rps_map(daily_map, latest_date, n_days):
    """全市场个股 RPS_n (n 日相对强弱) 映射 {ts_code: RPS值}
    欧奈尔体系: 按近 n 个交易日累计涨幅在全市场排名, 涨幅越高 RPS 越大
    RPS = (1 - (rank-1)/N) * 100, 第1名=100
    数据缺失/交易日不足 → 返回 {} (对应层指标自动跳过)
    """
    dates = sorted([d for d in daily_map.keys() if d <= latest_date])
    if len(dates) < 2:
        return {}
    n = min(n_days, len(dates) - 1)
    d_old, d_new = dates[-1 - n], dates[-1]
    old = daily_map[d_old][["ts_code", "close"]].rename(columns={"close": "close_old"})
    new = daily_map[d_new][["ts_code", "close"]].rename(columns={"close": "close_new"})
    merged = old.merge(new, on="ts_code")
    merged = merged[(merged["close_old"] > 0) & merged["close_new"].notna()]
    if merged.empty:
        return {}
    total = len(merged)
    merged["chg_n"] = merged["close_new"] / merged["close_old"] - 1
    merged["rank"] = merged["chg_n"].rank(ascending=False, method="min")
    merged["RPS"] = (1 - (merged["rank"] - 1) / total) * 100
    return {r["ts_code"]: round(float(r["RPS"]), 1) for _, r in merged.iterrows()}


def build_rps50_map(daily_map, latest_date):
    """全市场个股 RPS50 (50日相对强弱) 映射 {ts_code: RPS值}"""
    return _build_rps_map(daily_map, latest_date, 50)


def build_rps20_map(daily_map, latest_date):
    """全市场个股 RPS20 (20日相对强弱) 映射 {ts_code: RPS值}
    用途: L2 候选池软过滤 — 个股 RPS20 < CONFIG["pool"]["rps20_soft"] (默认70)
    时板块热度分减半 (不直接一票否决, 板块强而个股弱的"跟班"降权)
    """
    return _build_rps_map(daily_map, latest_date, 20)


# ============================================================
# 12. 早期启动挖掘 (EARLY_STAGE_START) — 独立候选池/过滤/打分, 完全不影响原有模式
# ============================================================

def _early_sector_pool(in_list_records, review_data):
    """早期启动候选池板块名:
    ① daily_review.potential_sectors 临界潜力板块 (RPS5∈[88,90) 达标率合格)
    ② 原 RPS≥90 板块, 剔除 sector_life_cycle 标记为 accelerate 加速高潮的板块
    返回 (板块名列表, 潜力板块名集合)
    """
    review = review_data or {}
    pot = [str(s.get("name") or "").strip() for s in review.get("potential_sectors", []) or []
           if s and s.get("name")]
    pot = [n for n in pot if n]
    lc = review.get("sector_life_cycle", {}) or {}
    lc5 = lc.get("rps5", {}) if isinstance(lc, dict) else {}
    in_names = {str(rec.get("name") or "").strip() for rec in in_list_records or [] if rec.get("name")}
    keep = [n for n in sorted(in_names) if lc5.get(n) != "accelerate"]
    names = list(dict.fromkeys(pot + keep))  # 保持顺序, 去重
    return names, set(pot)


def _build_early_sector_map(industry_df, sector_names):
    """构建 成分股→早期候选板块 映射 {ts_code: [板块名,...]} (候选池共振计数)
    潜力板块无 stocks 成分股名单, 统一按行业名从 industry_df 匹配 (与 _select_member_pool 行业名回退同源)
    """
    mapping = {}
    if industry_df is None or len(industry_df) == 0:
        return mapping
    members = industry_df[industry_df["industry"].isin(sector_names)]
    for _, r in members.iterrows():
        mapping.setdefault(str(r["ts_code"]), [])
        mapping[str(r["ts_code"])].append(str(r["industry"]))
    return mapping


def _is_platform_start(closes, highs, lows, cfg=None):
    """平台刚启动形态(早期模式加分项): 近20日窄幅横盘整理 + 今日收盘突破平台高点
    判定(全部满足):
      1) 近20日高低点振幅 ≤ platform_amplitude_max (横盘整理, 未大幅拉升)
      2) 今日收盘 ≥ 近20日最高 × (1 - break_above_pct) (突破平台, 允许盘中刷新高点)
    """
    e = (cfg or CONFIG)["early"]
    if len(closes) < 20:
        return False
    c20 = closes[-20:]
    h20 = max(highs[-20:])
    l20 = min(lows[-20:])
    close = closes[-1]
    if h20 <= 0 or l20 <= 0:
        return False
    amplitude = (h20 - l20) / l20 * 100
    if amplitude > e["platform_amplitude_max"]:
        return False
    if close < h20 * (1 - e["break_above_pct"] / 100.0):
        return False
    return True


def recommend_early_stocks(industry_df, daily_map, latest_date, in_list_records, member_codes=None,
                           out_list_names=None, review_data=None, cfg=None):
    """早期启动挖掘(刚启动候选) —— 独立候选池/独立过滤/独立打分
    与原有五层漏斗物理隔离: 原有模式不执行涨幅惩罚/形态加分, 本分支也不受原版逻辑干扰。
    候选池 = ① daily_review.potential_sectors 潜力板块 (RPS5∈[88,90) 达标率合格)
             ② 原 RPS≥90 板块剔除 accelerate 加速高潮板块 (避免拿已经高潮的老主线)
    早期专属约束(只对 early 生效):
      - 个股 RPS50 优先区间 [78,88]; RPS50>92 硬扣分
      - 硬惩罚: 近10日涨幅>28% 扣12分; 距 MA60 偏离>45% 扣10分
      - 平台刚启动形态加分 +4 (标签【平台刚启动】)
    门槛依然 70 分; 内部排序: 优先【平台刚启动】标签, 同等分数按近10日涨幅从小到大。
    输出: save_early_recommendations → recommendations_early.json
    """
    global _LAST_EARLY_MARKET_STATUS, _LAST_EARLY_CANDIDATE_LOG, _DAILY_BY_CODE
    cfg = cfg or CONFIG
    early_cfg = cfg.get("early", {})
    print("\n" + "=" * 55)
    print("  早期启动挖掘 (EARLY_STAGE_START) — 独立候选池/过滤/打分")
    print("=" * 55)
    candidate_log = []

    def _log_codes(codes, stage, reason, name_map=None):
        for c in codes:
            candidate_log.append({"ts_code": c, "name": (name_map or {}).get(c, ""),
                                  "stage": stage, "reason": reason})

    def _log_rows(sub_df, stage, reason):
        for _, r in sub_df.iterrows():
            candidate_log.append({"ts_code": r["ts_code"], "name": r.get("name", ""),
                                  "stage": stage, "reason": reason})

    if not in_list_records and not review_data:
        print("  ⚠️ 无入选板块且无复盘数据, 跳过早期启动挖掘")
        _LAST_EARLY_MARKET_STATUS = None
        return []

    # ---- 早期候选池板块: 潜力板块 + 非加速入选板块 ----
    pool_names, potential_set = _early_sector_pool(in_list_records, review_data)
    if not pool_names:
        print("  ⚠️ 早期候选池为空(无潜力板块且无入选板块), 跳过")
        _LAST_EARLY_MARKET_STATUS = None
        return []
    print(f"  早期候选板块: {len(pool_names)} 个 (潜力{len(potential_set)} + 非加速入选{len(pool_names)-len(potential_set)})")
    if member_codes is None:
        member_codes = _extract_member_codes(in_list_records)
    members, codes, pool_mode = _select_member_pool(industry_df, pool_names, member_codes)
    if members.empty:
        print("  ⚠️ 早期候选池无成分股, 跳过")
        _LAST_EARLY_MARKET_STATUS = None
        return []
    codes = list(members["ts_code"].unique())
    # 黑名单剔除 (与原有模式共用)
    bl_codes = _load_blacklist()
    if bl_codes:
        codes = [c for c in codes if c not in bl_codes]
    print(f"  早期选股池: {len(codes)} 只 ({pool_mode})")
    _DAILY_BY_CODE = build_daily_by_code(daily_map, codes, latest_date, n_days=80)

    if latest_date not in daily_map:
        print("  ⚠️ latest_date 不在 daily_map, 跳过")
        _LAST_EARLY_MARKET_STATUS = None
        return []
    df_latest = daily_map[latest_date]
    latest_idx = {r["ts_code"]: r for _, r in df_latest.iterrows() if r["ts_code"] in codes}
    codes_set = set(codes)

    # ---- 第一层: 大盘环境 (复用原版校验, REJECT 同样终止) ----
    print("\n[第一层] 大盘环境全局校验...")
    trade_days = sorted(daily_map.keys())
    index_map = fetch_index_daily(INDEX_CODES, trade_days)
    market_flow = fetch_market_fund_flow(latest_date)
    hsgt_breadth = fetch_hsgt_breadth()
    market_ok, market_status = check_market_env(index_map, daily_map, latest_date, market_flow,
                                                strong_sector_count=count_strong_sectors(in_list_records),
                                                hsgt_breadth=hsgt_breadth)
    _LAST_EARLY_MARKET_STATUS = market_status
    if not market_ok:
        print(f"  ⛔ 大盘环境不满足 (REJECT): {market_status['reason']} → 早期候选为空")
        _LAST_EARLY_CANDIDATE_LOG = candidate_log
        return []
    warn_flag = market_status.get("level") == "warn"
    if warn_flag:
        print("  ⚠️ 大盘环境偏弱 (WARN) → 继续选股, 全量打【大盘偏弱】标签")

    # ---- daily_basic: PE/流通市值/换手率/量比 (复用原版降级链) ----
    basic_idx = {}
    try:
        df_basic = pool.call_any("daily_basic", trade_date=latest_date)
        if df_basic is not None and len(df_basic) > 0:
            basic_idx = {r["ts_code"]: r for _, r in df_basic.iterrows() if r["ts_code"] in codes_set}
    except Exception as e:
        print(f"  ⚠️ daily_basic Tushare失败: {str(e)[:50]}, 尝试 AKShare/东财替代...")
    if not basic_idx and _AK_OK:
        try:
            ak_basic = _ak.fetch_daily_basic_ak(latest_date)
            if ak_basic:
                basic_idx = {c: v for c, v in ak_basic.items() if c in codes_set}
        except Exception as e:
            print(f"  ⚠️ daily_basic AKShare失败: {str(e)[:50]}")
    if not basic_idx:
        try:
            em_basic = fetch_daily_basic_em(latest_date, codes=codes)
            if em_basic:
                basic_idx = em_basic
        except Exception as e:
            print(f"  ⚠️ daily_basic 东财替代失败: {str(e)[:50]}")
    print(f"  ✅ daily_basic: 早期候选股 {len(basic_idx)} 条")

    # ---- 第二层: 候选池 DataFrame + 早期共振计数 ----
    print("\n[第二层] 早期候选池 (潜力板块 + 非加速入选板块成分股)...")
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
        print("  ⚠️ 早期候选池为空")
        _LAST_EARLY_MARKET_STATUS = None
        return []
    early_sector_map = _build_early_sector_map(industry_df, pool_names)
    stocks_df["sector_names"] = stocks_df["ts_code"].apply(
        lambda c: list(dict.fromkeys(early_sector_map.get(c, []))))
    stocks_df["sector_count"] = stocks_df["sector_names"].apply(_merge_same_source_sectors)
    # 个股 RPS20 软过滤: RPS20 < rps20_soft 不否决, 但板块热度分减半 (与日度模式口径一致)
    rps20_map_early = build_rps20_map(daily_map, latest_date)
    stocks_df["rps20"] = stocks_df["ts_code"].apply(lambda c: rps20_map_early.get(c))
    rps20_soft = cfg["pool"].get("rps20_soft")
    if rps20_map_early and rps20_soft:
        weak = stocks_df["rps20"].notna() & (stocks_df["rps20"] < rps20_soft)
        if weak.any():
            print(f"  ⚠️ RPS20 软过滤: {int(weak.sum())} 只个股 RPS20<{rps20_soft} → 板块热度分减半")
    # 全调出剔除: 所属板块全部在当日调出列表 → 剔除 (与原有模式一致)
    if out_list_names:
        ol = set(out_list_names)
        before = len(stocks_df)
        out_mask = stocks_df["sector_names"].apply(lambda sn: len(sn) > 0 and all(s in ol for s in sn))
        if out_mask.any():
            _log_rows(stocks_df[out_mask], "L2全调出", "所属板块全部在当日调出列表")
        stocks_df = stocks_df[~out_mask]
        removed = before - len(stocks_df)
        if removed:
            print(f"  全调出板块剔除: {before} -> {len(stocks_df)} 只")
    min_sc = cfg["pool"].get("min_sector_count", 1)
    before = len(stocks_df)
    low_res = stocks_df[stocks_df["sector_count"] < min_sc]
    if len(low_res):
        _log_rows(low_res, "L2共振不足", f"真实共振板块数<{min_sc}")
    stocks_df = stocks_df[stocks_df["sector_count"] >= min_sc]
    print(f"  板块共振过滤: {before} -> {len(stocks_df)} 只 (每股须属 ≥{min_sc} 个候选板块)")
    if stocks_df.empty:
        print("  ⚠️ 早期候选池无有效股票, 终止")
        _LAST_EARLY_CANDIDATE_LOG = candidate_log
        return []

    # ---- 第三层: 基本面排雷 (复用原版, 允许 early 微调阈值) ----
    print("\n[第三层] 基本面排雷 (硬性一票否决)...")
    fund_cfg = dict(cfg["fundamental"])
    fund_cfg.update(early_cfg.get("fundamental_overrides", {}) or {})
    fina_map = fetch_fina_indicator_batch(latest_date, codes=codes)
    eff_fund_cfg = dict(cfg); eff_fund_cfg["fundamental"] = fund_cfg
    stocks_df, rejects = screen_fundamental(stocks_df, fina_map, cfg=eff_fund_cfg)
    for r in rejects:
        candidate_log.append({"ts_code": r["ts_code"], "name": r.get("name", ""),
                              "stage": "L3基本面", "reason": r["reason"]})
    print(f"  ⛔ 基本面剔除 {len(rejects)} 只, 通过: {len(stocks_df)} 只")
    if stocks_df.empty:
        print("  ⚠️ 基本面排雷后无候选, 终止")
        _LAST_EARLY_CANDIDATE_LOG = candidate_log
        return []

    # ---- 第四层: 资金面达标校验 (复用原版, 允许 early 微调阈值) ----
    print("\n[第四层] 资金面达标校验...")
    mflow_map = fetch_moneyflow_batch(latest_date, daily_map, n_days=3, codes=codes)
    lhb_map = fetch_lhb(latest_date)
    cap_cfg = dict(cfg["capital"])
    cap_cfg.update(early_cfg.get("capital_overrides", {}) or {})
    eff_cap_cfg = dict(cfg); eff_cap_cfg["capital"] = cap_cfg
    before_df = stocks_df
    stocks_df, cap_details = screen_capital(stocks_df, daily_map, latest_date, mflow_map, cfg=eff_cap_cfg,
                                            lhb_map=lhb_map)
    if len(stocks_df) < len(before_df):
        cap_removed = before_df[~before_df["ts_code"].isin(set(stocks_df["ts_code"]))]
        _log_rows(cap_removed, "L4资金面", "换手率区间/量比/成交额/主力资金 未全部达标")
    print(f"  资金面达标: {len(before_df)} -> {len(stocks_df)} 只")
    if stocks_df.empty:
        print("  ⚠️ 资金面无一达标, 终止")
        _LAST_EARLY_CANDIDATE_LOG = candidate_log
        return []

    # ---- 第五层: 技术共振 (复用原版, 允许 early 微调阈值) ----
    print("\n[第五层] 多技术指标共振 (达标 ≥hit_min 通过)...")
    tech_cfg = dict(cfg["technical"])
    tech_cfg.update(early_cfg.get("technical_overrides", {}) or {})
    eff_tech_cfg = dict(cfg); eff_tech_cfg["technical"] = tech_cfg
    rps50_map = build_rps50_map(daily_map, latest_date)
    print(f"  全市场 RPS50 映射: {len(rps50_map)} 只")
    before_df = stocks_df
    stocks_df, tech_details = screen_technical(stocks_df, daily_map, latest_date,
                                               cfg=eff_tech_cfg, rps50_map=rps50_map)
    if len(stocks_df) < len(before_df):
        tech_removed = before_df[~before_df["ts_code"].isin(set(stocks_df["ts_code"]))]
        _log_rows(tech_removed, "L5技术共振", f"技术指标达标数<{tech_cfg['hit_min']}")
    print(f"  技术共振通过: {len(before_df)} -> {len(stocks_df)} 只")
    if stocks_df.empty:
        print("  ⚠️ 技术共振无一通过, 终止")
        _LAST_EARLY_CANDIDATE_LOG = candidate_log
        return []

    # ---- 综合评分: 复用四维分项 + 早期专属惩罚/加分 ----
    print(f"\n[综合评分] 通过漏斗 {len(stocks_df)} 只 → 四维评分 + 早期约束(涨幅/MA60偏离/平台形态)")
    sc_cfg = cfg["score"]
    fatigue_sectors, danger_sectors = _review_deduct_map(review_data)
    yjyg_map = fetch_yjyg(latest_date)  # 业绩预告信号(2026-08-23): 当日披露, 命中候选则加减分
    yj_cfg = sc_cfg.get("yjyg") or {}
    results = []
    for _, s in stocks_df.iterrows():
        code = s["ts_code"]
        sectors = s["sector_names"] or []
        sector_count = int(s["sector_count"])
        fina_row = fina_map.get(code)
        cap = cap_details.get(code, {})
        tech = tech_details.get(code, {})
        score_sector = score_sector_funnel(sector_count)
        # RPS20 软过滤: 个股 RPS20 < rps20_soft → 板块热度分减半 (与日度模式口径一致)
        rps20_val = s.get("rps20")
        if rps20_val is not None and rps20_soft and float(rps20_val) < rps20_soft:
            score_sector = int(score_sector * 0.5)
        score_fund = score_fundamental_funnel(s, fina_row, cfg=eff_fund_cfg)
        pe = s.get("pe_ttm")
        pe_neg = pe is not None and not pd.isna(pe) and float(pe) < 0
        if pe_neg:
            score_fund = min(score_fund, fund_cfg["pe_neg_cap"])
        report_age = _report_age_days(fina_row, latest_date)
        stale_flag = report_age is not None and report_age > fund_cfg["max_report_age_days"]
        if stale_flag:
            score_fund = int(score_fund * fund_cfg["stale_factor"])
        score_cap = score_capital_funnel(cap)
        score_tech = score_technical_funnel(tech.get("hit_count", 0))
        if tech.get("overbought"):
            score_tech = max(0, score_tech - tech_cfg["overbought_deduct_both"] if tech["overbought"] == "both"
                             else score_tech - tech_cfg["overbought_deduct"])
        if score_fund < sc_cfg["min_fundamental"] or score_cap < sc_cfg["min_capital"]:
            candidate_log.append({"ts_code": code, "name": s["name"], "stage": "评分分项门槛",
                                  "reason": f"基本面{score_fund}<{sc_cfg['min_fundamental']} "
                                            f"或资金面{score_cap}<{sc_cfg['min_capital']}"})
            continue
        total = score_sector + score_fund + score_cap + score_tech

        # ---- 盈利质量增强(2026-08-23, financial-modeling/earnings-interpretation):
        #      质量加分(营收/毛利率/ROE/现金流/ROIC) + 防御性扣分(现金流为负/利润质量存疑) ----
        qb, qd, qtags = score_quality_funnel(fina_row, cfg=eff_fund_cfg)
        total += qb - qd

        # ---- 业绩预告信号(2026-08-23, earnings-interpretation 预期差):
        #      预增/扭亏加分, 预减/首亏扣分 (仅当日披露命中, 缺失跳过不误杀) ----
        yj = yjyg_map.get(code)
        yj_adj = 0
        if yj:
            yj_adj = yj_cfg.get("bonus", 2) if yj["positive"] else -yj_cfg.get("deduct", 2)
        total += yj_adj

        # ---- 短期追高抑制 (与日度模式共用同一口径, 保证两套评分可比) ----
        df_by_code = _DAILY_BY_CODE.get(code)
        chase = _chase_deducts(code, s, df_by_code, sc_cfg,
                               limit_up_pct=cfg["capital"]["limit_up_pct"])
        total -= chase["deduct"]

        # ---- 精准度增强(2026-08-18): 趋势/位置 + 高估值降权 + 涨停追高 (与日度口径一致) ----
        trend = trend_regime_flags(code, s, df_by_code)
        tcfg = sc_cfg.get("trend") or {}
        trend_deduct = 0
        if trend["flags"]["rebound"]:
            trend_deduct += tcfg.get("rebound_deduct", 4)
        pe_high = pe_high_flag(s)
        if pe_high:
            trend_deduct += tcfg.get("pe_high_deduct", 3)
        total -= trend_deduct
        chase_risk = bool(cap.get("limit_up")) and (pe_high or chase["flags"]["gain5_high"])
        if chase_risk:
            total -= tcfg.get("chase_deduct", 3)
            chase["tags"].append("涨停追高风险")

        # ---- 龙虎榜资金信号 (仅提示+防御性扣分, 不否决): 净买额<0=上榜出货扣分打标签 ----
        lhb_sell = bool(cap.get("lhb_sell"))
        lhb_deduct = sc_cfg.get("lhb_net_deduct", 3) if lhb_sell else 0
        if lhb_deduct:
            total -= lhb_deduct

        # ---- 早期专属: 形态/涨幅/偏离 (只对 early 生效) ----
        closes = [float(c) for c in df_by_code["close"].tolist()] if df_by_code is not None else []
        highs = [float(h) for h in df_by_code["high"].tolist()] if df_by_code is not None else []
        lows = [float(l) for l in df_by_code["low"].tolist()] if df_by_code is not None else []
        chg10 = None
        if len(closes) >= 11 and closes[-11] > 0:
            chg10 = (closes[-1] / closes[-11] - 1) * 100
        ma60 = calc_ma_series(closes, 60)
        dev60 = None
        if ma60 and ma60 > 0 and closes:
            dev60 = (closes[-1] - ma60) / ma60 * 100
        rps50 = rps50_map.get(code)
        early_tags = list(chase["tags"])  # 短期追高抑制标签 (与日度一致)
        # 1) 涨幅惩罚: 近10日涨幅 > 阈值 → 硬扣分
        gain10_high = chg10 is not None and chg10 > early_cfg["gain10_max"]
        if gain10_high:
            total -= early_cfg["gain10_deduct"]
            early_tags.append("涨幅偏大")
        # 2) MA60 偏离惩罚: 距离 MA60 > 阈值 → 硬扣分 (已远离成本区, 非低位启动)
        dev60_high = dev60 is not None and dev60 > early_cfg["ma60_dev_max"]
        if dev60_high:
            total -= early_cfg["ma60_dev_deduct"]
            early_tags.append("偏离MA60过大")
        # 3) RPS50 过高扣分: 已走强个股非"刚启动"
        rps50_over = rps50 is not None and float(rps50) > early_cfg["rps50_over"]
        if rps50_over:
            total -= early_cfg["rps50_over_deduct"]
            early_tags.append("RPS50偏高")
        # 4) 平台刚启动形态加分
        platform = _is_platform_start(closes, highs, lows, cfg=cfg)
        if platform:
            total += early_cfg["platform_bonus"]
        # 复盘联动: 疲劳/danger 板块降权 (与原有模式一致, 不否决只降权)
        review_deduct = 0
        if fatigue_sectors and any(sec in fatigue_sectors for sec in sectors):
            review_deduct = sc_cfg["fatigue_deduct"]
        elif danger_sectors and any(sec in danger_sectors for sec in sectors):
            review_deduct = sc_cfg["danger_deduct"]
        if review_deduct:
            total -= review_deduct
            early_tags.append("复盘风险降权")

        tags = build_recommend_tags(sectors, tech.get("hits", []), sector_count)
        if platform:
            tags = ("平台刚启动" + (" + " + tags) if tags else "平台刚启动")
        if total >= 80:
            tags = ("短线优选" + (" + " + tags) if tags else "短线优选")
        elif total >= 70:
            tags = ("短线观察" + (" + " + tags) if tags else "短线观察")
        extra_tags = list(early_tags)
        for tg in trend["tags"]:
            extra_tags.append(tg)
        # 龙虎榜信号标签 (防御性提示; 出货>买入, 均不否决)
        if lhb_sell:
            extra_tags.append("龙虎榜出货")
        elif cap.get("lhb_net"):
            extra_tags.append("龙虎榜买入")
        if pe_high:
            extra_tags.append("高估值")
        if stale_flag:
            extra_tags.append("财报陈旧")
        if tech.get("overbought"):
            extra_tags.append("技术超买")
        if warn_flag:
            extra_tags.append("大盘偏弱")
        # 盈利质量标签 (2026-08-23): 现金流为负/利润质量存疑 (防御性提示)
        for qtg in qtags:
            extra_tags.append(qtg)
        # 业绩预告标签 (2026-08-23): 预增/扭亏/预减/首亏 (当日披露命中才打)
        if yj:
            extra_tags.append(yj["tag"])
        if extra_tags:
            tags = tags + " + " + " + ".join(extra_tags)
        results.append({
            "ts_code": code,
            "name": s["name"],
            "industry": s["industry"],
            "sectors": sectors,
            "sector_count": sector_count,
            "tags": tags,
            "price": round(s["price"], 2) if s.get("price") is not None else None,
            "pct_chg": round(s["pct_chg"], 2) if s.get("pct_chg") is not None else None,
            "pe_ttm": s.get("pe_ttm"),
            "circ_mv": round(s["circ_mv"] / 10000, 2) if s.get("circ_mv") else None,  # 亿元
            "score_total": total,
            "score_sector": score_sector,
            "score_fundamental": score_fund,
            "score_capital": score_cap,
            "score_technical": score_tech,
            "score_quality": qb - qd,
            "early": {
                "chg10": round(chg10, 2) if chg10 is not None else None,
                "ma60_dev": round(dev60, 2) if dev60 is not None else None,
                "rps50": float(rps50) if rps50 is not None else None,
                "platform_start": bool(platform),
                "gain10_deduct": early_cfg["gain10_deduct"] if gain10_high else 0,
                "ma60_dev_deduct": early_cfg["ma60_dev_deduct"] if dev60_high else 0,
                "rps50_over_deduct": early_cfg["rps50_over_deduct"] if rps50_over else 0,
                "platform_bonus": early_cfg["platform_bonus"] if platform else 0,
            },
            "fundamental": {
                "net_profit": fina_row.get("net_profit") if fina_row else None,
                "debt_to_assets": fina_row.get("debt_to_assets") if fina_row else None,
                "report_age_days": report_age,
            },
            "capital": cap,
            "technical": tech,
            "trend": trend,
        })

    # 消息面增强(2026-08-23): 金融新闻/主力与散户真实信号, 在门槛/排序前应用 → 可影响最终入选
    results = apply_news_signals(results, latest_date, cfg=cfg)
    # 门槛 ≥70 + 排序: 平台刚启动优先, 同等分数按近10日涨幅从小到大
    shown = [r for r in results if r["score_total"] >= float(sc_cfg["min_score"])]
    tag_first = early_cfg.get("sort_tag_first", "平台刚启动")
    shown.sort(key=lambda x: (0 if tag_first in x.get("tags", "") else 1,
                              -x["score_total"],
                              (x.get("early") or {}).get("chg10") if (x.get("early") or {}).get("chg10") is not None else 1e9))
    # 情绪联动: 大盘上涨占比不足且指数未全面站稳 → 缩减推荐数量 (与原版模式一致)
    shown, sent_capped = _sentiment_cap_shown(shown, market_status, cfg=cfg)
    if sent_capped:
        print(f"  ⚠️ 情绪联动: 上涨占比{market_status['advance_ratio']:.0%}<"
              f"{cfg['score']['sentiment_cap']['advance_ratio_min']:.0%} 且指数未全面站稳 → "
              f"推荐缩减为 {len(shown)} 只")
    max_per = sc_cfg.get("max_per_sector")
    if max_per and max_per > 0:
        shown, capped = _cap_per_sector(shown, max_per)
        for c in capped:
            candidate_log.append({"ts_code": c["ts_code"], "name": c["name"],
                                  "stage": "输出同板块控制",
                                  "reason": f"同细分板块最多输出{max_per}只"})
        if capped:
            print(f"  同板块输出控制: 剔除 {len(capped)} 只")
    _LAST_EARLY_CANDIDATE_LOG = candidate_log
    dropped = len(results) - len(shown)
    print(f"\n[结果] 早期漏斗通过 {len(results)} 只, 门槛≥{sc_cfg['min_score']}分后显示 {len(shown)} 只 (剔除 {dropped} 只)")
    if shown:
        print(f"  平台刚启动: {sum(1 for r in shown if '平台刚启动' in r.get('tags', ''))} 只")
    return shown


def recommend_stocks(industry_df, daily_map, latest_date, in_list_records, member_codes=None,
                     out_list_names=None, review_data=None):
    """主推荐流程 v3.2 —— 五层漏斗筛选 + 综合评分
    输入:
      industry_df: 含 ts_code/name/industry/list_date
      daily_map: {date: DataFrame of daily}
      latest_date: 最新交易日 YYYYMMDD
      in_list_records: 全部 RPS 入选板块记录 (每条含 name + stocks 成分股), 跨 RPS5/10/20 三档汇总
      member_codes: 可选, 入选板块成分股 ts_code 集合 (默认从 in_list_records 提取)
      out_list_names: 可选, 当日 RPS 调出板块名集合 (个股所属板块全部调出 → 剔除候选池)
      review_data: 可选, daily_review.json 复盘数据 (mature_fatigue/danger 板块 → 评分扣分降级)
    流程:
      第一层 大盘环境: 三态 PASS/WARN/REJECT, REJECT 输出0只, WARN 全量打【大盘偏弱】标签
      第二层 候选池:   入选板块成分股 + 同源去重真实共振数 + 全调出剔除 + RPS20软过滤 + 黑名单
      第三层 基本面:   一票否决 (ST/停牌/低价/小市值/高PE/亏损/高负债/质押商誉超标) + PE<0上限/财报时效
      第四层 资金面:   换手率市值自适应/量比(涨停豁免)/成交额 全部必达
      第五层 技术共振: 7项独立指标达标 ≥3 项判为通过 + 超买扣分
      综合评分: 板块30 + 基本面15 + 资金40 + 技术15 = 100, 分项最低门槛 + 软扣分, 按综合得分降序
    """
    global _LAST_MARKET_STATUS, _DAILY_BY_CODE, _LAST_CANDIDATE_LOG
    print("\n" + "=" * 55)
    print("  每日推荐股票 v3.3 (五层漏斗筛选 + 综合评分)")
    print("=" * 55)
    candidate_log = []  # 落选原因日志: 记录每只候选股被哪一层过滤、为什么 (供调参回测)

    def _log_codes(codes, stage, reason, name_map=None):
        for c in codes:
            candidate_log.append({"ts_code": c, "name": (name_map or {}).get(c, ""),
                                  "stage": stage, "reason": reason})

    def _log_rows(sub_df, stage, reason):
        for _, r in sub_df.iterrows():
            candidate_log.append({"ts_code": r["ts_code"], "name": r.get("name", ""),
                                  "stage": stage, "reason": reason})

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
    if out_list_names:
        print(f"  当日调出板块: {len(out_list_names)} 个 (个股全部所属调出 → 剔除)")
    bl_codes = _load_blacklist()
    if bl_codes:
        print(f"  黑名单: {len(bl_codes)} 只 (L2 直接剔除)")

    # ============ 第一层: 大盘环境全局校验 ============
    print("\n[第一层] 大盘环境全局校验 (上证/创业板指 vs 20日均线 + 大盘主力资金)...")
    trade_days = sorted(daily_map.keys())
    index_map = fetch_index_daily(INDEX_CODES, trade_days)
    market_flow = fetch_market_fund_flow(latest_date)
    hsgt_breadth = fetch_hsgt_breadth()
    market_ok, market_status = check_market_env(index_map, daily_map, latest_date, market_flow,
                                                strong_sector_count=count_strong_sectors(in_list_records),
                                                hsgt_breadth=hsgt_breadth)
    _LAST_MARKET_STATUS = market_status
    for r in market_status["indexes"]:
        c, m = r.get("close"), r.get("ma")
        state = "站上" if r.get("above") else "跌破" if r.get("above") is False else "无数据"
        print(f"    {r['name']}: 收盘{c if c is not None else '—'} / MA{m if m is not None else '—'} ({state})")
    print(f"    上涨占比: {market_status['advance_ratio']} | 跌停: {market_status['limit_down_count']}家 | 涨停: {market_status.get('limit_up_count')}家")
    mf = market_status.get("main_flow")
    if mf:
        print(f"    大盘主力资金: 近{mf['days']}日净流出{mf['neg_days']}日, 合计{mf['total_net_yi']}亿 ({mf['verdict']})")
    if not market_ok:
        print(f"  ⛔ 大盘环境不满足 (REJECT): {market_status['reason']} → 终止选股, 前端展示「大盘环境不满足, 暂无推荐」")
        return []
    if market_status.get("level") == "warn":
        print(f"  ⚠️ 大盘环境偏弱 (WARN): {market_status['reason']} → 继续选股, 全量打【大盘偏弱】标签")
    else:
        print(f"  ✅ 大盘环境合格, 继续选股")

    # ============ 第二层: 候选股票池构建 (核心约束) ============
    print("\n[第二层] 候选股票池构建 (100% 来自入选板块成分股 + 同源去重共振计数)...")
    sector_map = build_member_sector_map(in_list_records)
    members, codes, pool_mode = _select_member_pool(industry_df, in_list_names, member_codes)
    # 黑名单剔除 (手工维护利空个股, L2 直接剔除, 避免后续拉取浪费)
    if bl_codes:
        before_bl = len(codes)
        codes = [c for c in codes if c not in bl_codes]
        if before_bl > len(codes):
            removed_bl = [c for c in _extract_member_codes(in_list_records) if c in bl_codes]
            _log_codes(removed_bl, "L2黑名单", "手工黑名单(解禁/减持/利空)")
            print(f"  黑名单剔除: {before_bl} -> {len(codes)} 只")
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

    # 板块共振计数 (第二层核心)
    # sector_names  = 去重后的板块名(前端展示所属热点板块)
    # sector_count  = 同源合并后的"真实共振板块数" (算力/AI服务器同组只计1, 防题材重复虚高)
    stocks_df["sector_names"] = stocks_df["ts_code"].apply(
        lambda c: list(dict.fromkeys(sector_map.get(c, []))))
    stocks_df["sector_count"] = stocks_df["sector_names"].apply(_merge_same_source_sectors)
    # 全调出剔除: 个股所属板块全部在当日 RPS 调出列表 → 剔除候选池 (板块已结束强势, 个股无逻辑支撑)
    if out_list_names:
        ol = set(out_list_names)
        before = len(stocks_df)
        out_mask = stocks_df["sector_names"].apply(lambda sn: len(sn) > 0 and all(s in ol for s in sn))
        if out_mask.any():
            _log_rows(stocks_df[out_mask], "L2全调出", "所属板块全部在当日调出列表")
        stocks_df = stocks_df[~out_mask]
        removed = before - len(stocks_df)
        if removed:
            print(f"  全调出板块剔除: {before} -> {len(stocks_df)} 只 (所属板块全部调出 {removed} 只)")
    min_sc = CONFIG["pool"]["min_sector_count"]
    before = len(stocks_df)
    low_res = stocks_df[stocks_df["sector_count"] < min_sc]
    if len(low_res):
        _log_rows(low_res, "L2共振不足", f"真实共振板块数<{min_sc}")
    stocks_df = stocks_df[stocks_df["sector_count"] >= min_sc]
    print(f"  板块共振过滤: {before} -> {len(stocks_df)} 只 (每股须属 ≥{min_sc} 个真实共振板块)")
    if stocks_df.empty:
        print("  ⚠️ 无股票属于入选板块成分股, 终止")
        return []
    print(f"  真实共振分布(板块数→只数): {dict(stocks_df['sector_count'].value_counts().sort_index())}")
    # 个股 RPS20 软过滤: RPS20 < rps20_soft 不否决, 但板块热度分减半 (评分循环使用)
    rps20_map = build_rps20_map(daily_map, latest_date)
    stocks_df["rps20"] = stocks_df["ts_code"].apply(lambda c: rps20_map.get(c))
    rps20_soft = CONFIG["pool"].get("rps20_soft")
    if rps20_map and rps20_soft:
        weak = stocks_df["rps20"].notna() & (stocks_df["rps20"] < rps20_soft)
        if weak.any():
            print(f"  ⚠️ RPS20 软过滤: {int(weak.sum())} 只个股 RPS20<{rps20_soft} → 板块热度分减半")

    # ============ 第三层: 基本面排雷 (硬性一票否决) ============
    print("\n[第三层] 基本面排雷 (硬性一票否决)...")
    _t1 = datetime.now()
    fina_map = fetch_fina_indicator_batch(latest_date, codes=codes)
    print(f"  财务指标: 候选股 {len(fina_map)} 只 ({(datetime.now()-_t1).total_seconds():.1f}s)")
    stocks_df, rejects = screen_fundamental(stocks_df, fina_map)
    for r in rejects:
        candidate_log.append({"ts_code": r["ts_code"], "name": r.get("name", ""),
                              "stage": "L3基本面", "reason": r["reason"]})
    print(f"  ⛔ 基本面剔除 {len(rejects)} 只:")
    for r in rejects[:10]:
        print(f"    - {r['ts_code']} {r['name']}: {r['reason']}")
    if len(rejects) > 10:
        print(f"    ... 其余 {len(rejects) - 10} 只")
    print(f"  ✅ 通过基本面: {len(stocks_df)} 只")
    if stocks_df.empty:
        print("  ⚠️ 基本面排雷后无候选, 终止")
        _LAST_CANDIDATE_LOG = candidate_log
        return []

    # ============ 第四层: 资金面达标校验 (全部核心项必达) ============
    print("\n[第四层] 资金面达标校验 (换手区间/量比/成交额/主力资金 全必达)...")
    _t2 = datetime.now()
    mflow_map = fetch_moneyflow_batch(latest_date, daily_map, n_days=3, codes=codes)
    lhb_map = fetch_lhb(latest_date)
    print(f"  主力资金: 候选股 {len(mflow_map)} 只 ({(datetime.now()-_t2).total_seconds():.1f}s)")
    before_df = stocks_df
    stocks_df, cap_details = screen_capital(stocks_df, daily_map, latest_date, mflow_map, lhb_map=lhb_map)
    if len(stocks_df) < len(before_df):
        cap_removed = before_df[~before_df["ts_code"].isin(set(stocks_df["ts_code"]))]
        _log_rows(cap_removed, "L4资金面", "换手率区间/量比/成交额/主力资金 未全部达标")
    print(f"  资金面达标: {len(before_df)} -> {len(stocks_df)} 只")
    if stocks_df.empty:
        print("  ⚠️ 资金面无一达标, 终止")
        _LAST_CANDIDATE_LOG = candidate_log
        return []

    # ============ 第五层: 多技术指标共振 (达标 ≥3 项通过) ============
    print("\n[第五层] 多技术指标共振 (7项独立指标, 达标 ≥3 通过)...")
    rps50_map = build_rps50_map(daily_map, latest_date)
    print(f"  全市场 RPS50 映射: {len(rps50_map)} 只")
    before_df = stocks_df
    stocks_df, tech_details = screen_technical(stocks_df, daily_map, latest_date, rps50_map=rps50_map)
    if len(stocks_df) < len(before_df):
        tech_removed = before_df[~before_df["ts_code"].isin(set(stocks_df["ts_code"]))]
        _log_rows(tech_removed, "L5技术共振", f"技术指标达标数<{CONFIG['technical']['hit_min']}")
    print(f"  技术共振通过: {len(before_df)} -> {len(stocks_df)} 只")
    if stocks_df.empty:
        print("  ⚠️ 技术共振无一通过, 终止")
        _LAST_CANDIDATE_LOG = candidate_log
        return []

    # ============ 综合评分与最终推荐排序 ============
    print(f"\n[综合评分] 通过五层漏斗 {len(stocks_df)} 只 → 四维评分 (板块40/基本面20/资金20/技术20) + 预测化")
    sector_cont_map = _build_sector_cont_map(in_list_records)
    sc_cfg = CONFIG["score"]
    fund_cfg = CONFIG["fundamental"]
    warn_flag = market_status.get("level") == "warn"
    fatigue_sectors, danger_sectors = _review_deduct_map(review_data)
    if fatigue_sectors or danger_sectors:
        print(f"  ⚠️ 复盘联动: 成熟疲劳板块 {len(fatigue_sectors)} 个 (总分扣{sc_cfg['fatigue_deduct']}) "
              f"| danger风险板块 {len(danger_sectors)} 个 (总分扣{sc_cfg['danger_deduct']})")
    yjyg_map = fetch_yjyg(latest_date)  # 业绩预告信号(2026-08-23): 当日披露, 命中候选则加减分
    yj_cfg = sc_cfg.get("yjyg") or {}
    results = []
    for _, s in stocks_df.iterrows():
        code = s["ts_code"]
        sectors = s["sector_names"] or []  # 去重板块名(前端展示)
        sector_count = int(s["sector_count"])  # 同源合并后真实共振板块数(评分用)
        fina_row = fina_map.get(code)
        cap = cap_details.get(code, {})
        tech = tech_details.get(code, {})
        score_sector = score_sector_funnel(sector_count)
        # L2 软过滤: 个股 RPS20 < rps20_soft → 板块热度分减半 (板块强但个股弱的"跟班"降权)
        rps20 = s.get("rps20")
        rps20_soft = CONFIG["pool"].get("rps20_soft")
        if rps20 is not None and rps20_soft and float(rps20) < rps20_soft:
            score_sector = int(score_sector * 0.5)
        score_fund = score_fundamental_funnel(s, fina_row)
        # L3 软约束: PE-TTM<0 → 基本面分强制上限 pe_neg_cap (不否决但大幅压低)
        pe = s.get("pe_ttm")
        pe_neg = pe is not None and not pd.isna(pe) and float(pe) < 0
        if pe_neg:
            score_fund = min(score_fund, fund_cfg["pe_neg_cap"])
        # L3 财报时效: 财报距今 > max_report_age_days → 基本面分 × stale_factor + 标签【财报陈旧】
        report_age = _report_age_days(fina_row, latest_date)
        stale_flag = report_age is not None and report_age > fund_cfg["max_report_age_days"]
        if stale_flag:
            score_fund = int(score_fund * fund_cfg["stale_factor"])
        score_cap = score_capital_funnel(cap)
        # L5 技术超买: RSI>rsi_overbought / CCI>cci_overbought → 技术分扣分 + 标签【技术超买】
        score_tech = score_technical_funnel(tech.get("hit_count", 0))
        if tech.get("overbought"):
            score_tech = max(0, score_tech - CONFIG["technical"]["overbought_deduct_both"] if tech["overbought"] == "both"
                             else score_tech - CONFIG["technical"]["overbought_deduct"])
        # 分项最低门槛: 基本面/资金面 不达下限直接剔除 (防板块热度分拉满掩盖其他维度缺陷)
        if score_fund < sc_cfg["min_fundamental"] or score_cap < sc_cfg["min_capital"]:
            candidate_log.append({"ts_code": code, "name": s["name"], "stage": "评分分项门槛",
                                  "reason": f"基本面{score_fund}<{sc_cfg['min_fundamental']} "
                                            f"或资金面{score_cap}<{sc_cfg['min_capital']}"})
            continue
        total = score_sector + score_fund + score_cap + score_tech
        # ---- 盈利质量增强(2026-08-23, financial-modeling/earnings-interpretation):
        #      质量加分(营收/毛利率/ROE/现金流/ROIC) + 防御性扣分(现金流为负/利润质量存疑) ----
        qb, qd, qtags = score_quality_funnel(fina_row)
        total += qb - qd
        # ---- 业绩预告信号(2026-08-23, earnings-interpretation 预期差):
        #      预增/扭亏加分, 预减/首亏扣分 (仅当日披露命中, 缺失跳过不误杀) ----
        yj = yjyg_map.get(code)
        yj_adj = 0
        if yj:
            yj_adj = yj_cfg.get("bonus", 2) if yj["positive"] else -yj_cfg.get("deduct", 2)
        total += yj_adj
        # 短期追高抑制扣分 (近5日涨幅/高位追涨/MA20乖离, 与早期模式共用同一口径)
        df_by_code = _DAILY_BY_CODE.get(code)
        chase = _chase_deducts(code, s, df_by_code, sc_cfg)
        total -= chase["deduct"]
        gain5_high = chase["flags"]["gain5_high"]
        high_pos = chase["flags"]["high_pos"]
        dev_high = chase["flags"]["dev_high"]
        # 精准度增强(2026-08-18): 趋势/位置形态 + 高估值降权 + 涨停追高风险
        #  反弹未反转(60日区间跌)扣 rebound_deduct / 高估值(PE>100)扣 pe_high_deduct
        trend = trend_regime_flags(code, s, df_by_code)
        tcfg = sc_cfg.get("trend") or {}
        trend_deduct = 0
        if trend["flags"]["rebound"]:
            trend_deduct += tcfg.get("rebound_deduct", 4)
        pe_high = pe_high_flag(s)
        if pe_high:
            trend_deduct += tcfg.get("pe_high_deduct", 3)
        total -= trend_deduct
        # 涨停追高风险: 当日涨停 且 (高估值 或 近5日涨幅过高) → 追加扣分+提示
        chase_risk = bool(cap.get("limit_up")) and (pe_high or gain5_high)
        if chase_risk:
            total -= tcfg.get("chase_deduct", 3)
        # 龙虎榜资金信号 (仅提示+防御性扣分, 不否决): 净买额<0=上榜出货扣分打标签
        lhb_sell = bool(cap.get("lhb_sell"))
        lhb_deduct = sc_cfg.get("lhb_net_deduct", 3) if lhb_sell else 0
        if lhb_deduct:
            total -= lhb_deduct
        # 复盘联动: 成熟疲劳板块扣 fatigue_deduct / danger 风险板块扣 danger_deduct (不否决只降权)
        review_deduct = 0
        if fatigue_sectors and any(sec in fatigue_sectors for sec in sectors):
            review_deduct = sc_cfg["fatigue_deduct"]
        elif danger_sectors and any(sec in danger_sectors for sec in sectors):
            review_deduct = sc_cfg["danger_deduct"]
        if review_deduct:
            total -= review_deduct
        # 预测化: 6 维度概率 + 置信度 + 止损位 + 预测逻辑 (不依赖额外接口)
        pred_features = _pred_features(code, s, cap, tech, rps50_map, _DAILY_BY_CODE,
                                       sector_cont_map, market_status)
        prediction = predict.build_prediction(pred_features, market_status)
        tags = build_recommend_tags(sectors, tech.get("hits", []), sector_count)
        # 级别标签 (日线筛选不固化持有周期, 仅文字提示):
        #   ≥80 → 【短线优选】; 70-79 → 【短线观察】 (替代旧"优选 2-8周"误导性标签)
        if total >= 80:
            tags = ("短线优选" + (" + " + tags) if tags else "短线优选")
        elif total >= 70:
            tags = ("短线观察" + (" + " + tags) if tags else "短线观察")
        extra_tags = []
        if stale_flag:
            extra_tags.append("财报陈旧")
        if tech.get("overbought"):
            extra_tags.append("技术超买")
        if cap.get("limit_up"):
            extra_tags.append("涨停无量")
        if gain5_high:
            extra_tags.append("短期涨幅过高")
        if high_pos:
            extra_tags.append("高位追涨")
        if dev_high:
            extra_tags.append("乖离过大")
        for tg in trend["tags"]:
            extra_tags.append(tg)
        # 龙虎榜信号标签 (防御性提示; 出货>买入, 均不否决)
        if lhb_sell:
            extra_tags.append("龙虎榜出货")
        elif cap.get("lhb_net"):
            extra_tags.append("龙虎榜买入")
        if pe_high:
            extra_tags.append("高估值")
        if chase_risk:
            extra_tags.append("涨停追高风险")
        if review_deduct:
            extra_tags.append("复盘风险降权")
        if warn_flag:
            extra_tags.append("大盘偏弱")
        # 盈利质量标签 (2026-08-23): 现金流为负/利润质量存疑 (防御性提示)
        for qtg in qtags:
            extra_tags.append(qtg)
        # 业绩预告标签 (2026-08-23): 预增/扭亏/预减/首亏 (当日披露命中才打)
        if yj:
            extra_tags.append(yj["tag"])
        if extra_tags:
            tags = tags + " + " + " + ".join(extra_tags)
        results.append({
            "ts_code": code,
            "name": s["name"],
            "industry": s["industry"],
            "sectors": sectors,
            "sector_count": sector_count,
            "tags": tags,
            "price": round(s["price"], 2) if s.get("price") is not None else None,
            "pct_chg": round(s["pct_chg"], 2) if s.get("pct_chg") is not None else None,
            "pe_ttm": s.get("pe_ttm"),
            "circ_mv": round(s["circ_mv"] / 10000, 2) if s.get("circ_mv") else None,  # 亿元
            "score_total": total,
            "score_sector": score_sector,
            "score_fundamental": score_fund,
            "score_capital": score_cap,
            "score_technical": score_tech,
            "score_quality": qb - qd,
            "fundamental": {
                "net_profit": fina_row.get("net_profit") if fina_row else None,
                "debt_to_assets": fina_row.get("debt_to_assets") if fina_row else None,
                "report_age_days": report_age,
            },
            "capital": cap,
            "technical": tech,
            "trend": trend,
            "prediction": prediction,
        })
    # 消息面增强(2026-08-23): 金融新闻/主力与散户真实信号, 在排序/门槛前应用 → 可影响最终入选
    results = apply_news_signals(results, latest_date)
    results.sort(key=lambda x: -x["score_total"])

    # 综合得分门槛: < min_score 的股票不进入推荐列表 (前端不显示)
    shown = filter_by_min_score(results)
    dropped = len(results) - len(shown)
    # 情绪联动: 大盘上涨占比不足且指数未全面站稳 → 缩减推荐数量 (只保留最强)
    shown, sent_capped = _sentiment_cap_shown(shown, market_status)
    if sent_capped:
        print(f"  ⚠️ 情绪联动: 上涨占比{market_status['advance_ratio']:.0%}<"
              f"{CONFIG['score']['sentiment_cap']['advance_ratio_min']:.0%} 且指数未全面站稳 → "
              f"推荐缩减为 {len(shown)} 只")
    # 输出层同板块控制: 同一细分板块最多输出 N 只 (防主线集中时列表高度同质)
    max_per = sc_cfg.get("max_per_sector")
    if max_per and max_per > 0:
        shown, capped = _cap_per_sector(shown, max_per)
        for c in capped:
            candidate_log.append({"ts_code": c["ts_code"], "name": c["name"],
                                  "stage": "输出同板块控制",
                                  "reason": f"同细分板块最多输出{max_per}只"})
        if capped:
            print(f"  同板块输出控制: 剔除 {len(capped)} 只 (每细分板块最多 {max_per} 只)")
    _LAST_CANDIDATE_LOG = candidate_log

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
    # 行业集中度: top1 行业占比 (前端提示单一题材集中风险, 精准度增强 2026-08-18)
    try:
        from collections import Counter
        _ind_counts = Counter((r.get("industry") or "未知") for r in rec_list)
        _top_ind, _top_n = _ind_counts.most_common(1)[0] if _ind_counts else ("", 0)
        industry_concentration = {
            "top_industry": _top_ind,
            "top_count": _top_n,
            "top_ratio": round(_top_n / len(rec_list), 2),
        } if rec_list else {}
    except Exception:
        industry_concentration = {}
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
        "industry_concentration": industry_concentration,
        "prediction_status": prediction_status,
        "disclaimer": "⚠️ 全部为量化规则/软件算法逻辑参考,绝对不构成任何个股投资建议;中短线交易天然高风险,任何公式都无法100%胜率,系统必须绑定强制止损;本工具仅个人研究使用,严禁对外收费荐股、批量推送盈利承诺,违反监管法规。严禁杠杆借贷炒股。",
        "count": len(rec_list),
        "stocks": rec_list,
    }
    output = _sanitize_json(output)  # NaN/Inf/numpy → None/原生, 保证浏览器可解析
    with open(REC_FILE, "w", encoding="utf-8") as f:
        # allow_nan=False: 兜底, 若 sanitizer 漏网 NaN 直接抛错而非静默写出非法 JSON
        json.dump(output, f, ensure_ascii=False, indent=2, allow_nan=False)
    # 落选原因日志: 记录全部候选股各层过滤原因 (供调参回测, 失败不阻断)
    try:
        cl = _LAST_CANDIDATE_LOG or []
        if cl:
            log_file = STATIC_DIR / "candidate_log.json"
            with open(log_file, "w", encoding="utf-8") as f:
                json.dump({"update_date": latest_date, "count": len(cl),
                           "candidates": _sanitize_json(cl)}, f,
                          ensure_ascii=False, indent=2, allow_nan=False)
            print(f"  ✅ 落选日志: {len(cl)} 条 (保存至 {log_file.name})")
    except Exception as e:
        print(f"  ⚠️ 落选日志写入失败(不影响推荐): {str(e)[:60]}")
    # 预测化: 记录当日预测 (供每日校准核对实际涨跌)
    try:
        n_pred = predict.record_predictions(rec_list, latest_date)
        if n_pred:
            print(f"  ✅ 预测日志: 记录 {n_pred} 只预测 (保存至 {predict.LOG_FILE.name})")
    except Exception as e:
        print(f"  ⚠️ 预测日志记录失败(不影响推荐): {str(e)[:60]}")
    print(f"  ✅ 已保存: {REC_FILE} ({len(rec_list)} 只, 多板块共振 {multi_sector_count})")


def save_early_recommendations(rec_list, latest_date, prev_date=""):
    """保存早期启动挖掘结果到 static/recommendations_early.json (独立输出, 不污染原版)
    结构对齐原版 recommendations.json, 前端可复用渲染逻辑分开展示
    """
    market_status = _LAST_EARLY_MARKET_STATUS or {
        "is_safe": None, "rule": "至少 1 个指数站上 20 日线",
        "reason": "指数数据缺失", "indexes": [],
        "advance_ratio": None, "limit_down_count": None,
        "note": "指数数据缺失, 无法校验大盘环境",
    }
    multi_sector_count = sum(1 for r in rec_list if r.get("sector_count", 0) >= 2)
    platform_count = sum(1 for r in rec_list if "平台刚启动" in r.get("tags", ""))
    # 行业集中度: top1 行业占比 (与日度模式同口径, 精准度增强 2026-08-18)
    try:
        from collections import Counter
        _ind_counts = Counter((r.get("industry") or "未知") for r in rec_list)
        _top_ind, _top_n = _ind_counts.most_common(1)[0] if _ind_counts else ("", 0)
        industry_concentration = {
            "top_industry": _top_ind,
            "top_count": _top_n,
            "top_ratio": round(_top_n / len(rec_list), 2),
        } if rec_list else {}
    except Exception:
        industry_concentration = {}
    output = {
        "schema_version": 1,
        "mode": "early_stage_start",
        "update_date": latest_date,
        "prev_date": prev_date,
        "market_status": market_status,
        "multi_sector_count": multi_sector_count,
        "platform_count": platform_count,
        "industry_concentration": industry_concentration,
        "disclaimer": "⚠️ 早期启动挖掘为独立候选池量化结果(潜力板块RPS∈[88,90) + 涨幅/MA60偏离惩罚 + 平台形态加分), 刚启动标的胜率低于原版动量模式, 但规避高位接盘; 两个列表互为参考, 绝对不构成任何个股投资建议; 严禁杠杆借贷炒股。",
        "count": len(rec_list),
        "stocks": rec_list,
    }
    output = _sanitize_json(output)  # NaN/Inf/numpy → None/原生, 保证浏览器可解析
    with open(EARLY_REC_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, allow_nan=False)
    # 早期落选原因日志 (独立文件, 不覆盖原版 candidate_log)
    try:
        cl = _LAST_EARLY_CANDIDATE_LOG or []
        if cl:
            log_file = STATIC_DIR / "early_candidate_log.json"
            with open(log_file, "w", encoding="utf-8") as f:
                json.dump({"update_date": latest_date, "count": len(cl),
                           "candidates": _sanitize_json(cl)}, f,
                          ensure_ascii=False, indent=2, allow_nan=False)
            print(f"  ✅ 早期落选日志: {len(cl)} 条 (保存至 {log_file.name})")
    except Exception as e:
        print(f"  ⚠️ 早期落选日志写入失败(不影响推荐): {str(e)[:60]}")
    print(f"  ✅ 早期启动挖掘已保存: {EARLY_REC_FILE} ({len(rec_list)} 只, 平台刚启动 {platform_count})")


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
    out_list_names = set()
    if data_file.exists():
        with open(data_file, encoding="utf-8") as f:
            data = json.load(f)
        for k in ("rps5", "rps10", "rps20"):
            in_list_records.extend(data.get(k, {}).get("in_list", []))
            for rec in data.get(k, {}).get("out_list", []) or []:
                if rec and rec.get("name"):
                    out_list_names.add(str(rec["name"]).strip())
    # 复盘联动: 读取 daily_review.json (成熟疲劳/danger 板块 → 评分扣分降级 + 早期启动潜力板块)
    review_data = None
    review_file = STATIC_DIR / "daily_review.json"
    if review_file.exists():
        try:
            with open(review_file, encoding="utf-8") as f:
                review_data = json.load(f)
        except Exception as e:
            print(f"  ⚠️ daily_review.json 读取失败(跳过复盘联动): {str(e)[:50]}")
    run_mode = CONFIG["run_mode"]
    if run_mode.get("ORIGINAL_MOMENTUM", True):
        rec_list = recommend_stocks(industry_df, daily_map, latest_date, in_list_records,
                                    out_list_names=out_list_names, review_data=review_data)
        save_recommendations(rec_list, latest_date, prev_date)
    if run_mode.get("EARLY_STAGE_START", True):
        rec_early = recommend_early_stocks(industry_df, daily_map, latest_date, in_list_records,
                                           out_list_names=out_list_names, review_data=review_data)
        save_early_recommendations(rec_early, latest_date, prev_date)
