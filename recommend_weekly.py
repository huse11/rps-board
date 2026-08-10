# -*- coding: utf-8 -*-
"""
每周推荐股票模块 v1.0 —— 周线五层漏斗筛选 + 综合评分 (中线波段方向)
周度定位为中线波段选股, 与日度短线推荐周期互补:
  数据周期: 周K线 | 板块依据: RPS20 中期强势板块 (RPS60 未计算, 预留扩展)
  更新频率: 每周五收盘后运行 1 次, 结果缓存为独立 JSON, 有效期为下一个交易周
架构(漏斗式逐层过滤, 候选股严格限定在 RPS20 入选板块成分股范围内):
  第一层 周度大盘:   上证/创业板指 vs 20周均线, 三档判定(合格good/偏弱weak/恶劣bad)
  第二层 周度候选池: 100% 来自中期强势板块成分股, 板块连续上榜周数加权(多题材共振)
  第三层 基本面深度排雷: 日度硬门槛 + 业绩持续性(最新期净利润>0 且近2期同比增速为正/非连续下滑)
  第四层 周度资金面: 周量能趋势/周换手区间/周主力净流入/周成交额 全部核心项必达
  第五层 周线技术共振: 7项周线指标达标 ≥3 项判为通过
  综合评分: 板块40(权重最高, 含连续上榜周数) + 基本面30 + 技术20 + 资金10 = 100
  额外过滤: 剔除近2周累计涨幅 > 30% 的高位追涨标的, 优先趋势中继/刚启动
输出: static/weekly_recommendations.json (schema_version=1)

重要免责声明: 周度推荐为中线波段参考, 不构成任何投资建议; 股市波动极大,
严禁杠杆借贷炒股; 本工具仅个人研究使用, 严禁对外收费荐股牟利。
"""
import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

import rps_calc
from rps_calc import pool, STATIC_DIR
import recommend as rd  # 复用日度基础工具: 技术指标/数据源降级链/基础排雷/_sanitize_json 等

WEEKLY_REC_FILE = STATIC_DIR / "weekly_recommendations.json"

# 周度五层漏斗 — 全部阈值可配置(无需改代码即可调整选股严格度)
CONFIG_WEEKLY = {
    # 第一层: 周度大盘环境全局校验
    "market": {
        "index_codes": ["000001.SH", "399006.SZ"],  # 上证指数 + 创业板指
        "index_names": {"000001.SH": "上证指数", "399006.SZ": "创业板指"},
        "ma_period": 20,           # 20 周均线(中期趋势)
        "need_above": 1,           # 至少 1 个核心指数站稳 20 周均线
        "default_safe": True,      # 指数数据缺失默认"偏弱"(不否决, 提示谨慎)
        "advance_ratio_good": 0.40,  # 周上涨占比 ≥40% 为健康周
        "advance_ratio_bad": 0.30,   # 周上涨占比 <30% 为恶劣周(终止)
        "big_drop_max": 100,       # 周跌幅>10% 家数上限(超过=恶劣周)
        "check_macd": True,        # 周线 MACD 死叉 → 降级为偏弱
    },
    # 第二层: 周度候选股票池
    "pool": {
        "min_sector_count": 1,     # 至少属于 1 个中期强势板块
    },
    # 第三层: 周度基本面深度排雷 (硬性一票否决)
    "fundamental": {
        "min_price": 2.0,          # 最低收盘价(低价仙股剔除)
        "min_circ_mv": 10e8,       # 最低流通市值 10亿 (circ_mv 单位万元)
        "max_pe": 200,             # PE-TTM 上限
        "max_debt_ratio": 80,      # 资产负债率上限 % (远超合理区间剔除)
        "max_pledge_ratio": 50,    # 股权质押比例上限 % (数据缺失不剔除)
        "max_goodwill_ratio": 30,  # 商誉占净资产上限 % (数据缺失不剔除)
        "require_positive_profit": True,  # 最新期净利润>0 (业绩持续性)
        "yoy_min": 0.0,            # 净利润同比增速下限(近2期为正或非连续下滑)
        "deduct_yoy_min": -50.0,   # 扣非净利润同比下限(排除大幅异常/一次性收益虚增)
    },
    # 第四层: 周度资金面达标校验 (全部核心项必达)
    "capital": {
        "turnover_min": 5.0,       # 周换手率下限 % (资金关注度)
        "turnover_max": 40.0,      # 周换手率上限 % (规避高位出货)
        "vol_expand": 1.0,         # 本周量能 ≥ 上周倍率 (量能趋势向上)
        "amount_min": 2e8,         # 单周成交额下限 2亿 (中线流动性)
    },
    # 第五层: 周线多技术指标共振
    "technical": {
        "hit_min": 3,              # 达标指标数 ≥3 项判为共振通过
        "rps50_threshold": 80,     # 中期相对强势阈值 (周线RPS≈日度RPS50, 近50日涨幅排名)
        "break_weeks": 20,         # 周线突破: 收盘创近20周新高
    },
    # 综合评分权重 (总分 100) + 额外过滤
    "score": {
        "sector": 40,              # 板块热度分(权重最高: 归属板块数 + 连续上榜周数)
        "fundamental": 30,         # 基本面分(权重次高: 业绩增速/健康度/估值)
        "technical": 20,           # 技术面分(周线共振达标数量/信号强度)
        "capital": 10,             # 资金面分(周度资金流入强度/量能放大程度)
        "min_score": 70,           # 综合得分门槛: <70 不进入周度推荐列表
        "max_2w_gain": 30.0,       # 近2周累计涨幅 >30% 剔除(规避高位追涨)
    },
}
INDEX_CODES = CONFIG_WEEKLY["market"]["index_codes"]

# 模块级缓存: 最近一次周度大盘状态(供 save 写入 banner)
_LAST_WEEKLY_MARKET_STATUS = None


# ============================================================
# 0. 周K线聚合 (日线 → 周线)
# ============================================================

def aggregate_weekly_kline(daily_map, latest_date, codes=None, n_weeks=70):
    """从 daily_map 聚合周K线: {ts_code: DataFrame[date,open,high,low,close,vol,amount,pct_chg]}
    按 W-FRI(周五收盘) 对齐: open=首日, high=周内最高, low=周内最低, close=周五收盘,
    vol/amount=周累计, pct_chg=周收盘较前周涨跌幅(%)
    codes: 限定聚合哪些股票(周度只处理 RPS20 成分股, 避免全市场 5000 只聚合浪费)
    n_weeks: 保留最近多少周 (60周均线需要 ≥60 周, 默认 70)
    """
    codes_set = set(codes) if codes else None
    n_days = n_weeks * 5 + 15
    if codes_set:
        all_codes = sorted(codes_set)
    else:
        all_codes = sorted({tc for d in daily_map for tc in daily_map[d]["ts_code"]})
    daily_by_code = rd.build_daily_by_code(daily_map, all_codes, latest_date, n_days=n_days)
    weekly = {}
    for code, g in daily_by_code.items():
        if len(g) < 5:
            continue
        df = g.copy()
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
        df = df.dropna(subset=["date"]).set_index("date")
        w = df.resample("W-FRI").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "vol": "sum", "amount": "sum",
        })
        w = w.dropna(subset=["close"])
        if len(w) < 2:
            continue
        w["pct_chg"] = w["close"].pct_change() * 100
        w = w.reset_index()
        w["date"] = w["date"].dt.strftime("%Y%m%d")
        weekly[code] = w
    return weekly


def aggregate_weekly_index(index_map):
    """指数日线 → 周线收盘序列: {code: Series(date→close, 周频)}
    兼容 Tushare index_daily(trade_date列) 与 AKShare/东财(date列) 两种格式
    """
    result = {}
    for code, df in (index_map or {}).items():
        if df is None or len(df) == 0:
            continue
        g = df.copy()
        date_col = "trade_date" if "trade_date" in g.columns else "date"
        g["date"] = pd.to_datetime(g[date_col].astype(str), format="%Y%m%d", errors="coerce")
        g = g.dropna(subset=["date"]).set_index("date")
        s = g["close"].resample("W-FRI").last().dropna()
        if len(s) >= 2:
            result[code] = s
    return result


def recent_week_pct_map(daily_map, latest_date, n_weeks=1):
    """全市场近 n 周(每周5交易日)累计涨跌幅映射 {ts_code: 周涨跌幅%}
    用于周度市场结构统计(上涨占比/周跌幅>10%家数), 向量化近似周K聚合
    """
    dates = sorted(d for d in daily_map if d <= latest_date)
    if len(dates) < 2:
        return {}
    n = min(n_weeks * 5, len(dates) - 1)
    old = daily_map[dates[-1 - n]][["ts_code", "close"]].rename(columns={"close": "o"})
    new = daily_map[dates[-1]][["ts_code", "close"]].rename(columns={"close": "c"})
    m = old.merge(new, on="ts_code")
    m = m[(m["o"] > 0) & m["c"].notna()]
    m["w"] = (m["c"] / m["o"] - 1) * 100
    return dict(zip(m["ts_code"], m["w"]))


# ============================================================
# 1. 第一层: 周度大盘环境全局校验 (三档: good/weak/bad)
# ============================================================

def check_market_env_weekly(index_weekly, week_pct_map, cfg=None):
    """周度大盘环境: 指数周线 vs 20周均线 + 周度市场结构 + 周线MACD
    返回 (level, status):
      level: good(合格, 正常输出) / weak(偏弱, 输出但控制仓位) / bad(恶劣, 终止选股)
      status: {is_safe, level, reason, position_advice, indexes[], advance_ratio,
               big_drop_count, macd_ok, note}
    """
    cfg = (cfg or CONFIG_WEEKLY)["market"]
    index_names = cfg.get("index_names", {})
    rows = []
    above_cnt = 0
    data_ok = True
    for code in cfg["index_codes"]:
        s = index_weekly.get(code)
        name = index_names.get(code, code)
        if s is None or len(s) < cfg["ma_period"]:
            rows.append({"code": code, "name": name, "close": None, "ma": None, "above": None})
            data_ok = False
            continue
        closes = [float(x) for x in s.tolist() if pd.notna(x)]
        if len(closes) < cfg["ma_period"]:
            rows.append({"code": code, "name": name, "close": None, "ma": None, "above": None})
            data_ok = False
            continue
        today = closes[-1]
        ma = float(np.mean(closes[-cfg["ma_period"]:]))
        above = today > ma
        if above:
            above_cnt += 1
        rows.append({"code": code, "name": name, "close": round(today, 2), "ma": round(ma, 2), "above": above})

    # 周线 MACD (取第一个有足够数据的指数)
    macd_ok = None
    for code in cfg["index_codes"]:
        s = index_weekly.get(code)
        if s is None:
            continue
        closes = [float(x) for x in s.tolist() if pd.notna(x)]
        if len(closes) >= 30:
            macd = rd.calc_macd(closes)
            if macd:
                macd_ok = macd["dif"] > macd["dea"]
                break

    # 周度市场结构: 上涨占比 / 周跌幅>10%家数
    adv = dec = big = 0
    for c, w in (week_pct_map or {}).items():
        if w > 0:
            adv += 1
        elif w < 0:
            dec += 1
        if w <= -10:
            big += 1
    advance_ratio = (adv / (adv + dec)) if (adv + dec) > 0 else None

    # 三档判定
    level = "good"
    if above_cnt < cfg["need_above"]:
        if data_ok:
            level = "bad"            # 有指数数据但均跌破20周线 → 中期趋势恶劣
        else:
            level = "weak"           # 指数数据缺失 → 默认偏弱(不否决, 提示谨慎)
    elif advance_ratio is not None and advance_ratio < cfg["advance_ratio_bad"]:
        level = "bad"                # 周度普跌
    elif big > cfg["big_drop_max"]:
        level = "bad"                # 批量个股周线大跌
    elif advance_ratio is not None and advance_ratio < cfg["advance_ratio_good"]:
        level = "weak"               # 上涨占比不足 → 谨慎
    elif cfg.get("check_macd", True) and macd_ok is False:
        level = "weak"               # 周线MACD死叉 → 中期动能转弱

    position_advice = {
        "good": "正常仓位, 可积极参与中线波段",
        "weak": "谨慎参与, 控制仓位(建议半仓以内)",
        "bad": "建议空仓或轻仓观望",
    }.get(level, "")

    # 原因描述
    if level == "bad":
        if above_cnt < cfg["need_above"]:
            below = [r["name"] for r in rows if r.get("above") is False]
            reason = "/".join(below) + f" 跌破{cfg['ma_period']}周均线, 中期趋势偏弱"
        elif advance_ratio is not None and advance_ratio < cfg["advance_ratio_bad"]:
            reason = f"本周上涨家数占比 {advance_ratio:.0%} < {cfg['advance_ratio_bad']:.0%}, 极端普跌"
        else:
            reason = f"本周周跌幅超10%个股 {big} 家 > {cfg['big_drop_max']} 家, 批量大跌"
    elif level == "weak":
        if not data_ok:
            reason = "指数周线数据缺失, 默认谨慎(控制仓位)"
        elif advance_ratio is not None and advance_ratio < cfg["advance_ratio_good"]:
            reason = f"本周上涨家数占比 {advance_ratio:.0%} < {cfg['advance_ratio_good']:.0%}, 市场偏弱"
        else:
            reason = "指数周线MACD死叉, 中期动能转弱"
    else:
        reason = "核心指数站稳20周均线, 中期趋势健康"

    status = {
        "is_safe": level != "bad",
        "level": level,
        "reason": reason,
        "position_advice": position_advice,
        "indexes": rows,
        "advance_ratio": round(advance_ratio, 2) if advance_ratio is not None else None,
        "big_drop_count": big,
        "macd_ok": macd_ok,
        "note": {
            "good": "周度大盘环境合格, 正常输出推荐",
            "weak": "周度大盘环境偏弱, 输出推荐但建议控制仓位",
            "bad": "周度大盘环境恶劣, 本周无推荐",
        }.get(level, ""),
    }
    return level, status


# ============================================================
# 2. 第二层: 周度候选股票池 (核心约束 = RPS20 入选板块成分股)
# ============================================================

def build_weekly_sector_map(rps20_records):
    """构建 成分股→中期强势板块 映射 {ts_code: {sectors:[{name, continuous_days}],
    sector_count(去重板块数), week_streak(板块连续上榜周数之和)}}
    板块持续上榜加权: 连续上榜天数/5 = 周数, 上榜越久权重越高
    rps20_records: RPS20 档位入选板块记录(每条含 name + continuous + stocks 成分股)
    """
    mapping = {}
    for rec in rps20_records or []:
        name = str(rec.get("name") or "").strip()
        continuous = float(rec.get("continuous") or 0)
        for m in rec.get("stocks", []) or []:
            tc = str(m.get("ts_code")) if isinstance(m, dict) else str(m)
            if not tc or tc == "None":
                continue
            entry = mapping.setdefault(tc, {"sectors": [], "sector_count": 0, "week_streak": 0.0})
            entry["sectors"].append({"name": name, "continuous_days": continuous})
            entry["week_streak"] += continuous / 5.0
    for e in mapping.values():
        e["sector_count"] = len({s["name"] for s in e["sectors"]})
    return mapping


# ============================================================
# 3. 第三层: 周度基本面深度排雷 (硬性一票否决 + 业绩持续性)
# ============================================================

def screen_fundamental_weekly(stocks_df, fina_map, cfg=None):
    """周度基本面深度排雷:
      基础硬门槛(复用日度): ST/退市/停牌/低价/小市值/PE异常/净利润<0/高负债/高质押/高商誉
      周度特有业绩持续性: 最新期净利润>0, 近2期净利润同比增速为正(或非连续下滑),
                          扣非同比无大幅异常(排除一次性收益虚增)
      fina_map 缺失 → 跳过业绩持续性(不误杀), 仅硬门槛把关
    返回 (passed_df, rejects)
    """
    cfg = (cfg or CONFIG_WEEKLY)["fundamental"]
    df, rejects = rd.screen_fundamental(stocks_df, fina_map)
    if df.empty or not fina_map:
        return df, rejects  # 财务数据缺失: 深度排雷无法执行, 仅硬门槛
    keep = []
    for code in df["ts_code"]:
        fr = fina_map.get(code)
        if fr is None:
            keep.append(code)  # 该股无财务数据: 不误杀
            continue
        np_val = fr.get("net_profit")
        if np_val is not None and float(np_val) <= 0:
            rejects.append({"ts_code": code, "name": df[df["ts_code"] == code]["name"].iloc[0],
                            "reason": "最新期净利润≤0(业绩持续性不达标)"})
            continue
        yoy = fr.get("netprofit_yoy")
        prev_yoy = fr.get("prev_netprofit_yoy")
        if yoy is not None:
            ok = float(yoy) > cfg["yoy_min"]
            if not ok and prev_yoy is not None:
                # 或"非连续下滑": 上期增速为正 + 本期未转负
                ok = (float(prev_yoy) > cfg["yoy_min"] and float(yoy) >= 0)
            if not ok:
                rejects.append({"ts_code": code, "name": df[df["ts_code"] == code]["name"].iloc[0],
                                "reason": f"净利润同比 {yoy:.1f}% 为负或连续下滑"})
                continue
        dyoy = fr.get("dt_netprofit_yoy")
        if dyoy is not None and float(dyoy) < cfg["deduct_yoy_min"]:
            rejects.append({"ts_code": code, "name": df[df["ts_code"] == code]["name"].iloc[0],
                            "reason": f"扣非净利润同比 {dyoy:.1f}% 大幅异常(疑一次性收益虚增)"})
            continue
        keep.append(code)
    return df[df["ts_code"].isin(keep)], rejects


# ============================================================
# 4. 第四层: 周度资金面达标校验 (全部核心项必达)
# ============================================================

def screen_capital_weekly(stocks_df, weekly_kline, daily_map, latest_date, mflow_map, cfg=None):
    """周度资金面达标校验:
      - 周量能趋势: 本周量≥上周 或 近3周量能逐步抬升
      - 周换手率 ∈ [turnover_min, turnover_max] (≈Σ5日vol×price/流通市值, %)
      - 单周成交额 > amount_min (中线流动性保证)
      - 周主力资金: 近5日主力资金累计净额>0 (缺失跳过, 不误杀)
    返回 (passed_df, cap_details): cap_details={code:{turnover, vol_expand, amount, net_inflow_5d}}
    """
    cfg = (cfg or CONFIG_WEEKLY)["capital"]
    df = stocks_df.copy()
    cap_details = {}
    keep = []
    # 近5个交易日(≈1周) vol(手)/amount(千元→元) 汇总
    dates = sorted(d for d in daily_map if d <= latest_date)[-5:]
    week_vol, week_amt = {}, {}
    for d in dates:
        sub = daily_map[d]
        for _, r in sub.iterrows():
            tc = r["ts_code"]
            if pd.notna(r.get("vol")):
                week_vol[tc] = week_vol.get(tc, 0) + float(r["vol"])
            if pd.notna(r.get("amount")):
                week_amt[tc] = week_amt.get(tc, 0) + float(r["amount"]) * 1000  # 千元→元
    for _, s in df.iterrows():
        code = s["ts_code"]
        wk = weekly_kline.get(code)
        if wk is None or len(wk) < 3:
            continue
        vols = [float(v) for v in wk["vol"].tolist()]
        # 周量能趋势
        if not (vols[-1] > vols[-2] * cfg["vol_expand"]
                or (vols[-1] > vols[-2] and vols[-2] > vols[-3])):
            continue
        # 周换手率%: Σ5日vol(手→股)×price / 流通股本 = week_vol*100*price/(cmv万元×1e4) ≈ week_vol*price/(cmv×100)
        price = s.get("price")
        cmv = s.get("circ_mv")
        tr = None
        if price is not None and pd.notna(price) and float(price) > 0 \
                and cmv is not None and pd.notna(cmv) and float(cmv) > 0:
            tr = week_vol.get(code, 0) * float(price) / (float(cmv) * 100)
        if tr is None or not (cfg["turnover_min"] <= tr <= cfg["turnover_max"]):
            continue
        # 单周成交额
        amt = week_amt.get(code, 0)
        if amt < cfg["amount_min"]:
            continue
        # 周主力资金净流入 (近5日, 缺失跳过不误杀)
        net5 = None
        if mflow_map and code in mflow_map:
            rows = sorted(mflow_map[code], key=lambda x: x["trade_date"])[-5:]
            nets = [r["buy_sm_amount"] - r["sell_sm_amount"] for r in rows]
            if nets:
                net5 = sum(nets)
        if net5 is not None and net5 <= 0:
            continue
        cap_details[code] = {
            "turnover": round(tr, 2),
            "vol_expand": round(vols[-1] / vols[-2], 2) if vols[-2] else None,
            "amount": round(amt, 0),
            "net_inflow_5d": net5,
        }
        keep.append(code)
    return df[df["ts_code"].isin(keep)], cap_details


# ============================================================
# 5. 第五层: 周线多技术指标共振 (达标 ≥ hit_min 项判为通过)
# ============================================================

def screen_technical_weekly(stocks_df, weekly_kline, cfg=None, rps50_map=None):
    """周线技术共振 (全部基于周K线, 中线信号可靠性高于日线)
    指标(7项, 可独立判定):
      1 周线均线多头: MA5w > MA10w > MA20w > MA60w (中期趋势完整向上)
      2 周线MACD多头: DIF > DEA
      3 周线KDJ健康: J∈[50,80] 多头未超买, 或低位金叉向上发散
      4 周线突破: 收盘创近20周新高 (突破前期平台压力位)
      5 周线量价齐升: 本周上涨 + 本周量>上周量
      6 中期相对强势: 周线RPS≥80 (复用日度RPS50, 近50日涨幅排名≈近10周)
      7 周线均线支撑: 收盘价站稳所有短中期周均线
    返回 (passed_df, tech_details): {code:{hits:[指标名], hit_count}}
    """
    cfg = (cfg or CONFIG_WEEKLY)["technical"]
    df = stocks_df.copy()
    tech_details = {}
    keep = []
    for _, s in df.iterrows():
        code = s["ts_code"]
        wk = weekly_kline.get(code)
        if wk is None or len(wk) < cfg["break_weeks"] + 1:
            continue
        closes = [float(c) for c in wk["close"].tolist() if pd.notna(c)]
        highs = [float(h) for h in wk["high"].tolist() if pd.notna(h)]
        lows = [float(l) for l in wk["low"].tolist() if pd.notna(l)]
        vols = [float(v) for v in wk["vol"].tolist() if pd.notna(v)]
        if len(closes) != len(highs) or len(closes) != len(lows) or len(closes) != len(vols):
            continue
        hits = []
        today_close = closes[-1]
        ma5 = rd.calc_ma_series(closes, 5)
        ma10 = rd.calc_ma_series(closes, 10)
        ma20 = rd.calc_ma_series(closes, 20)
        ma60 = rd.calc_ma_series(closes, 60)
        # 1 周线均线多头
        if ma5 and ma10 and ma20 and ma60 and ma5 > ma10 > ma20 > ma60:
            hits.append("周线均线多头")
        # 2 周线MACD多头
        macd = rd.calc_macd(closes)
        if macd and macd["dif"] > macd["dea"]:
            hits.append("周线MACD多头")
        # 3 周线KDJ健康
        kdj = rd.calc_kdj(highs, lows, closes)
        if kdj:
            j = kdj["j"]
            if 50 <= j <= 80:
                hits.append("周线KDJ健康")
            elif j > kdj["k"] and kdj["prev_j"] <= kdj["k"] and j < 50:
                hits.append("周线KDJ金叉")
        # 4 周线突破: 收盘创近20周新高(不含本周)
        if len(closes) >= cfg["break_weeks"] + 1 and today_close > max(closes[-cfg["break_weeks"]:-1]):
            hits.append("周线突破")
        # 5 周线量价齐升
        pct = float(wk["pct_chg"].iloc[-1]) if pd.notna(wk["pct_chg"].iloc[-1]) else None
        if pct is not None and pct > 0 and vols[-1] > vols[-2]:
            hits.append("周线量价齐升")
        # 6 中期相对强势 RPS (周线RPS≈日度RPS50)
        if rps50_map and code in rps50_map and rps50_map[code] >= cfg["rps50_threshold"]:
            hits.append("周线RPS强势")
        # 7 周线均线支撑
        mas = [m for m in (ma5, ma10, ma20, ma60) if m is not None]
        if mas and today_close > max(mas):
            hits.append("周线均线支撑")
        if len(hits) >= cfg["hit_min"]:
            tech_details[code] = {"hits": hits, "hit_count": len(hits)}
            keep.append(code)
    return df[df["ts_code"].isin(keep)], tech_details


# ============================================================
# 6. 综合评分与最终推荐排序 (板块40 + 基本面30 + 技术20 + 资金10)
# ============================================================

def score_sector_weekly(sector_count, week_streak, cfg=None):
    """板块热度分(权重最高): 归属中期强势板块数 + 板块连续上榜周数加权
    1板块=20, 2板块=30, ≥3板块=40; 连续上榜≥2周+3, ≥4周+5 (封顶40)
    """
    cfg = (cfg or CONFIG_WEEKLY)["score"]
    base = cfg["sector"]
    if sector_count >= 3:
        v = base
    elif sector_count == 2:
        v = base * 0.75
    elif sector_count == 1:
        v = base * 0.5
    else:
        v = 0
    if week_streak >= 4:
        v += 5
    elif week_streak >= 2:
        v += 3
    return min(base, round(v, 1))


def score_fundamental_weekly(fina_row, cfg=None):
    """基本面分(权重次高): 盈利/增速持续性/估值/负债健康度"""
    cfg = (cfg or CONFIG_WEEKLY)["score"]["fundamental"]
    if not fina_row:
        return 0
    v = 0.0
    np_val = fina_row.get("net_profit")
    yoy = fina_row.get("netprofit_yoy")
    prev_yoy = fina_row.get("prev_netprofit_yoy")
    dyoy = fina_row.get("dt_netprofit_yoy")
    if np_val is not None and float(np_val) > 0:
        v += cfg * 0.3      # 9 盈利为正
    if yoy is not None:
        if float(yoy) > 20:
            v += cfg * 0.3  # 9 高增长
        elif float(yoy) > 0:
            v += cfg * 0.2  # 6 正增长
    if prev_yoy is not None and float(prev_yoy) > 0 and (yoy is None or float(yoy) > 0):
        v += cfg * 0.13     # 4 业绩持续增长(近2期)
    if dyoy is not None and float(dyoy) > 0:
        v += cfg * 0.13     # 4 扣非增长(业绩质量)
    debt = fina_row.get("debt_to_assets")
    if debt is not None and float(debt) < 50:
        v += cfg * 0.13     # 4 低负债财务健康
    return min(cfg, round(v, 1))


def score_technical_weekly(hit_count, cfg=None):
    """技术面分: 周线共振达标数量越多信号越强"""
    cfg = (cfg or CONFIG_WEEKLY)["score"]["technical"]
    if hit_count >= 6:
        return cfg
    if hit_count == 5:
        return round(cfg * 0.9, 1)
    if hit_count == 4:
        return round(cfg * 0.75, 1)
    if hit_count == 3:
        return round(cfg * 0.6, 1)
    return 0


def score_capital_weekly(cap, cfg=None):
    """资金面分: 周量能放大/周换手健康/周主力净流入"""
    cfg = (cfg or CONFIG_WEEKLY)["score"]["capital"]
    if not cap:
        return 0
    v = 0.0
    if cap.get("vol_expand") is not None and cap["vol_expand"] >= 1.3:
        v += 4
    elif cap.get("vol_expand") is not None:
        v += 2
    tr = cap.get("turnover")
    if tr is not None and 8 <= tr <= 20:
        v += 3
    elif tr is not None:
        v += 2
    if cap.get("net_inflow_5d") is not None and cap["net_inflow_5d"] > 0:
        v += 3
    return min(cfg, round(v, 1))


def build_weekly_tags(sector_names, tech_hits, sector_count=None):
    """周度推荐理由标签: 突出「周线突破」「多板块共振」「业绩增长」等中线属性"""
    tags = []
    if "周线突破" in (tech_hits or []):
        tags.append("周线突破")
    if sector_count and sector_count >= 2:
        tags.append(f"{sector_count}板块共振")
    for nm in (sector_names or [])[:2]:
        tags.append(nm)
    for h in (tech_hits or []):
        if h not in ("周线突破", "周线KDJ健康", "周线KDJ金叉") and len(tags) < 4:
            tags.append(h)
    if not tags:
        tags.append("周线多指标共振")
    return " + ".join(tags)


def filter_high_gain(recs, gain_map, cfg=None):
    """额外过滤: 剔除近2周累计涨幅 > max_2w_gain 的高位追涨标的"""
    max_g = (cfg or CONFIG_WEEKLY)["score"]["max_2w_gain"]
    kept, dropped = [], 0
    for r in recs:
        g = gain_map.get(r["ts_code"])
        if g is not None and g > max_g:
            dropped += 1
            continue
        kept.append(r)
    return kept, dropped


# ============================================================
# 7. 主推荐流程 (周度五层漏斗)
# ============================================================

def recommend_weekly_stocks(industry_df, daily_map, latest_date, rps20_records, member_codes=None):
    """周度主推荐流程 v1.0 —— 周线五层漏斗 + 综合评分
    输入:
      industry_df: 含 ts_code/name/industry/list_date
      daily_map: {date: DataFrame of daily}
      latest_date: 最新交易日 YYYYMMDD
      rps20_records: RPS20 档位入选板块记录(每条含 name + continuous + stocks 成分股)
      member_codes: 可选, 入选板块成分股 ts_code 集合 (默认从 rps20_records 提取)
    流程:
      第一层 周度大盘: 指数周线 vs 20周均线, 恶劣(bad)直接终止
      第二层 候选池:   100% 来自 RPS20 入选板块成分股, 板块连续上榜周数加权
      第三层 基本面:   日度硬门槛 + 业绩持续性深度排雷
      第四层 资金面:   周量能/周换手/周成交额/周主力净流入 全部必达
      第五层 技术共振: 7项周线指标达标 ≥3 项判为通过
      综合评分: 板块40 + 基本面30 + 技术20 + 资金10 = 100, 剔除近2周涨幅>30%高位标的
    """
    global _LAST_WEEKLY_MARKET_STATUS
    print("\n" + "=" * 55)
    print("  每周推荐股票 v1.0 (周线五层漏斗 + 综合评分)")
    print("=" * 55)

    if not rps20_records:
        print("  ⚠️ 无 RPS20 入选板块, 跳过周度推荐")
        _LAST_WEEKLY_MARKET_STATUS = None
        return []

    cfg = CONFIG_WEEKLY
    # 汇总 RPS20 入选板块: 名称集合 + 成分股名单
    in_list_names = {str(rec.get("name") or "").strip() for rec in rps20_records if rec.get("name")}
    if member_codes is None:
        member_codes = rd._extract_member_codes(rps20_records)
    if not member_codes:
        print("  ⚠️ RPS20 入选板块无成分股名单, 回退按行业名匹配")
    print(f"  RPS20 入选板块: {len(in_list_names)} 个 | 成分股: {len(member_codes)} 只")

    # ============ 第一层: 周度大盘环境全局校验 ============
    print("\n[第一层] 周度大盘环境校验 (指数周线 vs 20周均线)...")
    trade_days = sorted(daily_map.keys())
    index_map = rd.fetch_index_daily(cfg["market"]["index_codes"], trade_days)
    index_weekly = aggregate_weekly_index(index_map)
    week_pct = recent_week_pct_map(daily_map, latest_date)
    level, market_status = check_market_env_weekly(index_weekly, week_pct)
    _LAST_WEEKLY_MARKET_STATUS = market_status
    for r in market_status["indexes"]:
        c, m = r.get("close"), r.get("ma")
        state = "站上" if r.get("above") else "跌破" if r.get("above") is False else "无数据"
        print(f"    {r['name']}: 周收盘{c if c is not None else '—'} / 20周均线{m if m is not None else '—'} ({state})")
    print(f"    周上涨占比: {market_status['advance_ratio']} | 周跌幅>10%: {market_status['big_drop_count']}家 | 周线MACD: {market_status['macd_ok']}")
    print(f"    判定: {level} | {market_status['reason']} | 仓位建议: {market_status['position_advice']}")
    if level == "bad":
        print("  ⛔ 周度大盘环境恶劣 → 终止选股, 前端提示「中期趋势偏弱, 本周无推荐」")
        return []

    # ============ 第二层: 周度候选股票池构建 (核心约束) ============
    print("\n[第二层] 周度候选池 (100% 来自 RPS20 入选板块成分股, 板块持续上榜加权)...")
    sector_map = build_weekly_sector_map(rps20_records)
    print(f"  板块共振映射: {len(sector_map)} 只成分股")
    members, _codes, _mode = rd._select_member_pool(industry_df, in_list_names, member_codes)
    if members.empty:
        print("  ⚠️ 无匹配成分股, 终止")
        _LAST_WEEKLY_MARKET_STATUS = None
        return []
    codes = members["ts_code"].unique().tolist()
    print(f"  选股池: {len(codes)} 只")

    if latest_date not in daily_map:
        print("  ⚠️ latest_date 不在 daily_map, 跳过")
        return []
    df_latest = daily_map[latest_date]
    latest_idx = {r["ts_code"]: r for _, r in df_latest.iterrows() if r["ts_code"] in codes}
    codes_set = set(codes)

    # daily_basic: PE/流通市值/换手率/量比 (复用日度降级链)
    basic_idx = {}
    try:
        df_basic = pool.call_any("daily_basic", trade_date=latest_date)
        if df_basic is not None and len(df_basic) > 0:
            basic_idx = {r["ts_code"]: r for _, r in df_basic.iterrows() if r["ts_code"] in codes_set}
        print(f"  ✅ daily_basic: 全市场 → 候选股 {len(basic_idx)} 条")
    except Exception as e:
        print(f"  ⚠️ daily_basic Tushare失败: {str(e)[:50]}, 尝试 AKShare/东财替代...")
    if not basic_idx and rd._AK_OK:
        try:
            ak_basic = rd._ak.fetch_daily_basic_ak(latest_date)
            if ak_basic:
                basic_idx = {c: v for c, v in ak_basic.items() if c in codes_set}
            print(f"  ✅ daily_basic (AKShare腾讯): 候选股 {len(basic_idx)} 条")
        except Exception as e:
            print(f"  ⚠️ daily_basic AKShare失败: {str(e)[:50]}")
    if not basic_idx:
        try:
            em_basic = rd.fetch_daily_basic_em(latest_date, codes=codes)
            if em_basic:
                basic_idx = em_basic
            print(f"  ✅ daily_basic (东财替代): 候选股 {len(basic_idx)} 条")
        except Exception as e:
            print(f"  ⚠️ daily_basic 东财替代失败: {str(e)[:50]}")

    # 构建候选池 DataFrame
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
        return []

    # 板块共振计数 (第二层核心: 归属中期强势板块数 + 连续上榜周数)
    stocks_df["sector_count"] = stocks_df["ts_code"].apply(lambda c: sector_map.get(c, {}).get("sector_count", 0))
    stocks_df["sector_names"] = stocks_df["ts_code"].apply(
        lambda c: list({s["name"] for s in sector_map.get(c, {}).get("sectors", [])}))
    stocks_df["week_streak"] = stocks_df["ts_code"].apply(lambda c: sector_map.get(c, {}).get("week_streak", 0.0))
    min_sc = cfg["pool"]["min_sector_count"]
    before = len(stocks_df)
    stocks_df = stocks_df[stocks_df["sector_count"] >= min_sc]
    print(f"  板块共振过滤: {before} -> {len(stocks_df)} 只 (每股须属 ≥{min_sc} 个入选板块)")
    if stocks_df.empty:
        print("  ⚠️ 无股票属于入选板块成分股, 终止")
        return []
    print(f"  共振分布(板块数→只数): {dict(stocks_df['sector_count'].value_counts().sort_index())}")

    # 周K线聚合 (仅候选池, 提速)
    _t0 = datetime.now()
    weekly_kline = aggregate_weekly_kline(daily_map, latest_date, codes=codes, n_weeks=70)
    print(f"  周K聚合: {len(weekly_kline)} 只 × ~70周 ({(datetime.now() - _t0).total_seconds():.1f}s)")

    # ============ 第三层: 周度基本面深度排雷 (硬性一票否决 + 业绩持续性) ============
    print("\n[第三层] 周度基本面深度排雷 (硬门槛 + 业绩持续性)...")
    fina_map = rd.fetch_fina_indicator_batch(latest_date, codes=codes)
    print(f"  财务指标: 候选股 {len(fina_map)} 只 (含近2期净利润同比)")
    before = len(stocks_df)
    stocks_df, rejects = screen_fundamental_weekly(stocks_df, fina_map)
    print(f"  基本面剔除 {len(rejects)} 只:")
    for r in rejects[:10]:
        print(f"    - {r['ts_code']} {r['name']}: {r['reason']}")
    if len(rejects) > 10:
        print(f"    ... 其余 {len(rejects) - 10} 只")
    print(f"  ✅ 通过基本面深度排雷: {before} -> {len(stocks_df)} 只")
    if stocks_df.empty:
        print("  ⚠️ 基本面排雷后无候选, 终止")
        return []

    # ============ 第四层: 周度资金面达标校验 (全部核心项必达) ============
    print("\n[第四层] 周度资金面达标校验 (周量能/周换手/周成交额/周主力净流入 全必达)...")
    mflow_map = rd.fetch_moneyflow_batch(latest_date, daily_map, n_days=5, codes=codes)
    print(f"  主力资金: 候选股 {len(mflow_map)} 只 (近5日)")
    before = len(stocks_df)
    stocks_df, cap_details = screen_capital_weekly(stocks_df, weekly_kline, daily_map, latest_date, mflow_map)
    print(f"  周度资金面达标: {before} -> {len(stocks_df)} 只")
    if stocks_df.empty:
        print("  ⚠️ 周度资金面无一达标, 终止")
        return []

    # ============ 第五层: 周线多技术指标共振 (达标 ≥3 项通过) ============
    print("\n[第五层] 周线多技术指标共振 (7项周线指标, 达标 ≥3 通过)...")
    rps50_map = rd.build_rps50_map(daily_map, latest_date)
    print(f"  全市场 RPS50 映射: {len(rps50_map)} 只 (周线RPS替代)")
    before = len(stocks_df)
    stocks_df, tech_details = screen_technical_weekly(stocks_df, weekly_kline, rps50_map=rps50_map)
    print(f"  周线技术共振通过: {before} -> {len(stocks_df)} 只")
    if stocks_df.empty:
        print("  ⚠️ 周线技术共振无一通过, 终止")
        return []

    # ============ 综合评分与最终推荐排序 ============
    print(f"\n[综合评分] 通过周度五层漏斗 {len(stocks_df)} 只 → 四维评分 (板块40/基本面30/技术20/资金10)")
    results = []
    for _, s in stocks_df.iterrows():
        code = s["ts_code"]
        sectors = s["sector_names"] or []
        sector_count = int(s["sector_count"])
        streak = float(s["week_streak"])
        fina_row = fina_map.get(code)
        cap = cap_details.get(code, {})
        tech = tech_details.get(code, {})
        ss = score_sector_weekly(sector_count, streak)
        fs = score_fundamental_weekly(fina_row)
        cs = score_capital_weekly(cap)
        ts = score_technical_weekly(tech.get("hit_count", 0))
        total = round(ss + fs + cs + ts, 1)
        tags = build_weekly_tags(sectors, tech.get("hits", []), sector_count)
        if fina_row and fina_row.get("netprofit_yoy") is not None and float(fina_row["netprofit_yoy"]) > 0:
            tags = "业绩增长 + " + tags
        results.append({
            "ts_code": code,
            "name": s["name"],
            "industry": s["industry"],
            "sectors": sectors,
            "sector_count": sector_count,
            "week_streak": round(streak, 1),
            "tags": tags,
            "price": round(float(s["price"]), 2) if s["price"] is not None else None,
            "pct_chg_week": round(float(tech.get("pct_chg_week", 0) or 0), 2),
            "score_total": total,
            "score_sector": ss,
            "score_fundamental": fs,
            "score_capital": cs,
            "score_technical": ts,
            "fundamental": {
                "net_profit": fina_row.get("net_profit") if fina_row else None,
                "netprofit_yoy": fina_row.get("netprofit_yoy") if fina_row else None,
                "dt_netprofit_yoy": fina_row.get("dt_netprofit_yoy") if fina_row else None,
                "debt_to_assets": fina_row.get("debt_to_assets") if fina_row else None,
            },
            "capital": cap,
            "technical": {"hits": tech.get("hits", []), "hit_count": tech.get("hit_count", 0)},
        })
        # 周涨跌幅 (从周K最新周)
        wk = weekly_kline.get(code)
        if wk is not None and len(wk) > 0 and pd.notna(wk["pct_chg"].iloc[-1]):
            results[-1]["pct_chg_week"] = round(float(wk["pct_chg"].iloc[-1]), 2)

    # 高位追涨过滤: 剔除近2周累计涨幅 > max_2w_gain
    gain2 = recent_week_pct_map(daily_map, latest_date, n_weeks=2)
    results, dropped_high = filter_high_gain(results, gain2)
    print(f"  高位追涨过滤(近2周涨幅>{cfg['score']['max_2w_gain']}%): 剔除 {dropped_high} 只")

    # 排序 + 综合得分门槛
    results.sort(key=lambda x: -x["score_total"])
    min_score = float(cfg["score"]["min_score"])
    shown = [r for r in results if r["score_total"] >= min_score]
    print(f"\n[结果] 周度漏斗通过 {len(results)} 只, 门槛≥{min_score:.0f}分后显示 {len(shown)} 只, 按综合得分降序")
    return shown


def save_weekly_recommendations(rec_list, latest_date, prev_date=""):
    """保存周度推荐结果到 static/weekly_recommendations.json (schema v1)
    market_status: {is_safe, level(good/weak/bad), reason, position_advice, indexes[],
                    advance_ratio, big_drop_count, macd_ok, note}
    """
    ms = _LAST_WEEKLY_MARKET_STATUS or {
        "is_safe": None, "level": "weak", "reason": "指数数据缺失",
        "position_advice": "谨慎参与, 控制仓位", "indexes": [],
        "advance_ratio": None, "big_drop_count": None, "macd_ok": None,
        "note": "指数数据缺失, 无法校验周度大盘环境",
    }
    # ISO 周标识 (如 2026W32)
    try:
        d = datetime.strptime(latest_date, "%Y%m%d")
        iso = d.isocalendar()
        week = f"{iso[0]}W{iso[1]}"
    except Exception:
        week = ""
    output = {
        "schema_version": 1,
        "update_date": latest_date,
        "week": week,
        "prev_date": prev_date,
        "market_status": ms,
        "position_advice": ms.get("position_advice", ""),
        "disclaimer": "⚠️ 周度推荐为中线波段量化筛选结果(周线五层漏斗), 仅作技术逻辑参考, 绝对不构成任何个股投资建议; 中线波段同样高风险, 严禁杠杆借贷炒股; 本工具仅个人研究使用, 严禁对外收费荐股牟利。",
        "count": len(rec_list),
        "stocks": rec_list,
    }
    output = rd._sanitize_json(output)  # NaN/Inf/numpy → None/原生, 保证浏览器可解析
    with open(WEEKLY_REC_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(f"  ✅ 周度推荐已保存: {WEEKLY_REC_FILE} ({len(rec_list)} 只, {week})")


if __name__ == "__main__":
    # 独立运行: 复用 rps_calc 的 daily_map / industry_df, 从 rps_data.json 读 RPS20 入选板块
    trade_days = rps_calc.get_trade_days(n=80)
    industry_df = rps_calc.get_stock_industry()
    daily_map = rps_calc.fetch_daily_batch(trade_days)
    available = [d for d in trade_days if d in daily_map]
    latest_date = available[-1]
    prev_date = available[-2] if len(available) >= 2 else ""
    data_file = STATIC_DIR / "rps_data.json"
    rps20_records = []
    if data_file.exists():
        with open(data_file, encoding="utf-8") as f:
            data = json.load(f)
        rps20_records = data.get("rps20", {}).get("in_list", [])
    rec_list = recommend_weekly_stocks(industry_df, daily_map, latest_date, rps20_records)
    save_weekly_recommendations(rec_list, latest_date, prev_date)
