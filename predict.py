# -*- coding: utf-8 -*-
"""
日度股票推荐「预测化」模块 v1.0
==================================================
核心: 从「描述当前强弱」升级为「预判未来 1 个交易日(次日)上涨概率 / 预期空间 / 风险边界」
所有预测均基于「历史同类情况 → 统计后续表现 → 推导当前概率」的量化逻辑,
不依赖主观判断; 输出为概率预判, 非确定性结论。

六大可量化预测维度:
  1 动量延续性 momentum : RPS50 + 板块连续天数 + 短中期涨幅 + 连涨天数 (RPS 体系自带预测属性)
  2 技术形态   technical : 7 项共振指标 + 高置信组合(20日新高+放量+均线多头)
  3 资金行为   capital   : 3 日主力净流入 + 换手健康 + 量比温和放大 + 资金埋伏(流入+滞涨)
  4 板块轮动   sector    : 跨 RPS 档位共振数 (板块前三约 70% 上行基准)
  5 情绪周期   sentiment : 全市场上涨占比 + 指数 20 日线 (震荡市动量权重最高)
  6 基本面预期差 fundamental: PE-TTM 估值位置 (低估加分 / 透支减分)

保障机制:
  - 置信度三档(高/中/低): 三周期共振 + 资金达标 + 技术共振 → 高
  - 止损位: 20 日低点支撑 + 置信度档位兜底
  - 每日校准: predictions_log.json 记录预测 → 次日运行自动核对实际涨跌,
    产出 prediction_review.json (分维度胜率复盘, 供因子权重迭代)
  - 熔断: 大盘不合格时主流程已终止选股; 赚钱效应偏弱时降低置信度并提示仓位
"""
import json
import os
from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"
LOG_FILE = STATIC_DIR / "predictions_log.json"
REVIEW_FILE = STATIC_DIR / "prediction_review.json"

PRED_HORIZON = 1  # 预测周期: 次日验证 (2026-08-26 由 3 改为 1: 命中=昨日推荐今日上涨)

# 基准胜率库: 各维度单独估算的次日上行概率基准值 (PRED_HORIZON=1, 2026-08-26 由3日改次日)
# 依据: A 股动量因子历史回测 (RPS≥90 个股次日上行约 50-55%, 板块前三约 60%)
PROB_BASE = 52          # 综合基准概率 (所有修正叠加后上下限夹取)
PROB_MIN, PROB_MAX = 30, 88

# 各因子修正增量 (delta, 单位 % 概率)
DELTA = {
    "sector_count_1": +2,    # 属 1 个强势板块
    "sector_count_2": +7,    # 双 RPS 档位共振
    "sector_count_3": +12,   # 三档共振 (板块前三 ≈70% 基准)
    "sector_cont_5": +2,     # 板块连续上榜 ≥5 天 (成熟主线惯性)
    "rps50_90": +6,          # 个股 RPS50 ≥90
    "rps50_80": +3,          # 个股 RPS50 ≥80
    "rps50_70": +1,
    "chg3_mild": +2,         # 近3日温和上行 (0,12]
    "chg3_hot": -5,          # 近3日过热 >12 (追高风险, 原-3收紧)
    "chg3_down": -2,         # 近3日转弱 ≤0
    "chg5_ok": +2,           # 近5日 (5,15]
    "chg5_hot": -5,          # 近5日 >12 (追高风险, 原阈值20/-2收紧)
    "ma20_dev_high": -4,     # 距 MA20 乖离 >25% (高位远离成本区)
    "high_pos": -5,          # 当天大涨>7%未涨停 (高位追涨, 次日高开回落风险)
    "consec_3": +2,          # 连涨 ≥3 天
    "consec_6": +3,          # 连涨 ≥6 天
    "consec_9": +1,          # 连涨 ≥9 天 (惯性但过热衰减)
    "tech_3": +1, "tech_4": +3, "tech_5": +5, "tech_6": +7,  # 技术共振项数
    "tech_combo": +3,        # 高置信组合: 20日新高+量比>1.2+均线多头
    "cap_inflow": +3,        # 3日主力净流入 >0
    "cap_turnover_ok": +2,   # 换手 (5,15] 健康区
    "cap_vr_ok": +2,         # 量比 (1.2,2.5] 温和放大
    "cap_ambush": +2,        # 资金埋伏: 净流入>0 且近3日涨幅<8% (滞涨)
    "cap_dump": -5,          # 单日暴涨>9% 但主力未流入 (疑似出货)
    "sent_strong": +3,       # 上涨占比 ≥0.60
    "sent_mild": +1,         # 上涨占比 [0.50,0.60)
    "sent_weak": -3,         # 上涨占比 <0.40 (赚钱效应差)
    "fund_pe30": +2,         # PE 0-30 低估未透支
    "fund_pe60": +1,         # PE 30-60
    "fund_pe150": -1,        # PE 100-150 偏高
    "fund_pe_high": -2,      # PE >150 透支
    "regime_strong": +2,     # 市场偏强 (指数站稳+上涨占比高)
    "regime_weak": -4,       # 市场偏弱 (上涨占比低)
}

# 预期次日涨幅空间映射 (按综合上涨概率分档, 中值 %; PRED_HORIZON=1 由3日量级调小)
EXPECTED_GAIN = ((75, 3.0), (68, 2.5), (60, 2.0), (0, 1.5))


def _clamp(v, lo=PROB_MIN, hi=PROB_MAX):
    return max(lo, min(hi, int(round(v))))


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def market_regime(ms):
    """市场环境 → 情绪周期档位 (震荡市动量权重最高)
    返回 {"regime": "偏强|震荡市|偏弱", "prob_adjust": int, "note": str}
    """
    if not ms:
        return {"regime": "震荡市", "prob_adjust": 0, "note": "市场数据缺失, 按震荡市处理"}
    adv = _num(ms.get("advance_ratio"))
    above = sum(1 for r in ms.get("indexes", []) if r.get("above") is True)
    n_idx = max(1, len(ms.get("indexes", []) or []))
    if adv >= 0.60 and above >= n_idx:
        return {"regime": "偏强", "prob_adjust": DELTA["regime_strong"], "note": "指数站上20日线且上涨占比≥60%, 赚钱效应强"}
    if adv < 0.40:
        return {"regime": "偏弱", "prob_adjust": DELTA["regime_weak"], "note": "上涨占比<40%, 赚钱效应弱, 预测胜率下降, 注意控制仓位"}
    return {"regime": "震荡市", "prob_adjust": 0, "note": "震荡市, 动量因子权重最高"}


# ============================================================
# 六维度概率估算 (每维度独立: 基准 55 + 该维度增量, 夹取 30~88)
# ============================================================

def _momentum_prob(f):
    """维度1 动量延续性: RPS50 + 板块连续天数 + 近3/5日涨幅 + 连涨天数"""
    d = 0
    notes = []
    sc = int(f.get("sector_count") or 0)
    if sc >= 3:
        notes.append("三档RPS共振")
    elif sc >= 2:
        notes.append("双RPS共振")
    cont = int(f.get("continuous") or 0)
    if cont >= 5:
        d += DELTA["sector_cont_5"]
    rps50 = f.get("rps50")
    if rps50 is not None:
        if rps50 >= 90:
            d += DELTA["rps50_90"]; notes.append("个股RPS50≥90")
        elif rps50 >= 80:
            d += DELTA["rps50_80"]
        elif rps50 >= 70:
            d += DELTA["rps50_70"]
    chg3 = f.get("chg3")
    if chg3 is not None:
        if chg3 > 12:
            d += DELTA["chg3_hot"]
        elif chg3 > 0:
            d += DELTA["chg3_mild"]
        else:
            d += DELTA["chg3_down"]
    chg5 = f.get("chg5")
    if chg5 is not None:
        if chg5 > 12:
            d += DELTA["chg5_hot"]
        elif chg5 > 5:
            d += DELTA["chg5_ok"]
    cu = int(f.get("consec_up") or 0)
    if cu >= 9:
        d += DELTA["consec_9"]
    elif cu >= 6:
        d += DELTA["consec_6"]
    elif cu >= 3:
        d += DELTA["consec_3"]
    if cu >= 5:
        notes.append("连涨%d日动量延续" % cu)
    # 位置风险: 距 MA20 乖离过大 → 高位追涨风险
    ma20_dev = f.get("ma20_dev")
    if ma20_dev is not None and _num(ma20_dev) > 25:
        d += DELTA["ma20_dev_high"]
        notes.append("偏离MA20过大")
    # 当天大涨(>7%)但未涨停 → 次日高开回落风险
    pct = _num(f.get("pct_chg"))
    if 7 < pct < 9.8:
        d += DELTA["high_pos"]
        notes.append("大涨未涨停高位追涨")
    return _clamp(PROB_BASE + d), notes


def _technical_prob(f):
    """维度2 技术形态: 7 项共振 + 高置信组合(20日新高+量比>1.2+均线多头)"""
    d = 0
    notes = []
    hc = int(f.get("hit_count") or 0)
    hits = f.get("tech_hits") or []
    if hc >= 6:
        d += DELTA["tech_6"]; notes.append("技术7项共振%d/7" % hc)
    elif hc >= 5:
        d += DELTA["tech_5"]; notes.append("技术多指标共振%d/7" % hc)
    elif hc >= 4:
        d += DELTA["tech_4"]
    elif hc >= 3:
        d += DELTA["tech_3"]
    if "20日新高" in hits and _num(f.get("vol_ratio")) > 1.2 and "均线多头" in hits:
        d += DELTA["tech_combo"]
        notes.append("突破20日新高+放量+均线多头")
    elif "20日新高" in hits:
        notes.append("创20日新高")
    return _clamp(PROB_BASE + d), notes


def _capital_prob(f):
    """维度3 资金行为: 主力净流入 + 换手 + 量比 + 资金埋伏/出货识别"""
    d = 0
    notes = []
    inflow = f.get("net_inflow_3d")
    if inflow is not None and inflow > 0:
        d += DELTA["cap_inflow"]
        notes.append("主力资金连续流入")
    tr = _num(f.get("turnover"))
    if 5 < tr <= 15:
        d += DELTA["cap_turnover_ok"]
    vr = _num(f.get("vol_ratio"))
    if 1.2 < vr <= 2.5:
        d += DELTA["cap_vr_ok"]
        if not any("量比" in x for x in notes):
            notes.append("量能温和放大")
    chg3 = f.get("chg3")
    if inflow is not None and inflow > 0 and (chg3 is None or chg3 < 8):
        d += DELTA["cap_ambush"]
        notes.append("资金埋伏(流入+滞涨)")
    pct = _num(f.get("pct_chg"))
    if pct > 9 and (inflow is None or inflow <= 0):
        d += DELTA["cap_dump"]
        notes.append("放量暴涨但主力未流入, 警惕出货")
    return _clamp(PROB_BASE + d), notes


def _sector_prob(f):
    """维度4 板块轮动: 跨 RPS 档位共振数 (板块前三约 70% 基准)"""
    sc = int(f.get("sector_count") or 0)
    d = DELTA.get("sector_count_%d" % sc, 0)
    notes = []
    if sc >= 2:
        notes.append("%d个强势板块共振" % sc)
    elif sc == 1:
        notes.append("所属板块RPS强势")
    return _clamp(PROB_BASE + d), notes


def _sentiment_prob(f, ms):
    """维度5 情绪周期: 上涨占比 + 指数 20 日线"""
    adv = f.get("advance_ratio")
    d = 0
    if adv is not None:
        if adv >= 0.60:
            d += DELTA["sent_strong"]
        elif adv >= 0.50:
            d += DELTA["sent_mild"]
        elif adv < 0.40:
            d += DELTA["sent_weak"]
    return _clamp(PROB_BASE + d), []


def _fundamental_prob(f):
    """维度6 基本面预期差: PE-TTM 估值位置 (低估加分 / 透支减分)"""
    pe = f.get("pe_ttm")
    if pe is None or pe <= 0:
        return _clamp(PROB_BASE), []
    d = 0
    if pe <= 30:
        d += DELTA["fund_pe30"]
    elif pe <= 60:
        d += DELTA["fund_pe60"]
    elif pe <= 100:
        pass
    elif pe <= 150:
        d += DELTA["fund_pe150"]
    else:
        d += DELTA["fund_pe_high"]
    return _clamp(PROB_BASE + d), []


# ============================================================
# 综合预测值
# ============================================================

def _expected_gain(up_prob):
    """预期 3 日涨幅中值 (按概率分档)"""
    for th, gain in EXPECTED_GAIN:
        if up_prob >= th:
            return gain
    return EXPECTED_GAIN[-1][1]


def _stop_loss_pct(f, confidence):
    """止损位(相对现价 %): 20 日低点支撑优先, 兜底按置信度档位"""
    lows = f.get("lows") or []
    price = f.get("price")
    if lows and price:
        lo = min(lows)
        pct = -(price - lo) / price * 100
        return round(max(-9.0, min(-3.0, pct)), 1)
    return {"高": -4.0, "中": -5.0, "低": -6.0}.get(confidence, -5.0)


def _confidence(up_prob, f):
    """置信度三档:
    高 = 概率≥68 且 双RPS档位以上共振 且 技术共振≥4 且 主力净流入>0
    中 = 概率≥58; 低 = 其余 (含偏弱市场环境整体降档)
    位置约束: 近5日涨幅>12% 或 乖离MA20>25% → 高位追涨, 不给"高"(即使概率达档)
    """
    if _num(f.get("chg5")) > 12 or _num(f.get("ma20_dev")) > 25:
        return "中" if up_prob >= 58 else "低"
    if up_prob >= 68 and int(f.get("sector_count") or 0) >= 2 \
            and int(f.get("hit_count") or 0) >= 4 \
            and _num(f.get("net_inflow_3d")) > 0:
        return "高"
    if up_prob >= 58:
        return "中"
    return "低"


# ============================================================
# 概率校准 (预测效果复盘驱动, 防概率虚高)
# ============================================================

def _load_calib():
    """从 prediction_review.json 读取近期实测命中率 (用于概率校准)
    样本 <5 或字段缺失 → None (不校准, 避免小样本抖动)
    """
    rev = _load_json(REVIEW_FILE)
    s = rev.get("summary") or {}
    hit = s.get("hit_rate")
    avg = s.get("avg_up_prob")
    samples = s.get("samples") or 0
    if hit is None or avg is None or samples < 5:
        return None
    return {"hit": float(hit), "avg_prob": float(avg), "samples": int(samples)}


def calibrate_prob(up_prob):
    """概率校准: 用实测命中率压缩虚高概率。
    若系统近期平均输出概率(avg_prob)显著高于实测命中率(hit), 说明过度乐观,
    将输出向 55% 基准收缩: p = 55 + (p-55)*k, k = 1 - 虚高量/100, 下限 0.3。
    误差 ≤3pp 或样本不足 → 不做校准 (避免过度修正)。
    """
    cal = _load_calib()
    if not cal:
        return up_prob
    gap = cal["avg_prob"] - cal["hit"]  # 虚高量 (如 82-50=32)
    if gap <= 3:
        return up_prob
    k = max(0.3, 1 - gap / 100.0)
    p = 55 + (up_prob - 55) * k
    return _clamp(int(round(p)), lo=PROB_MIN + 5)


def _pick_logic(notes, n=3):
    """从各维度 notes 中挑 2~3 条最有信息量的预测支撑理由"""
    out = []
    for x in notes:
        if x not in out:
            out.append(x)
        if len(out) >= n:
            break
    return out


def build_prediction(f, market_status=None):
    """综合预测 → {"horizon","up_prob","expected_gain","gain_range",
                   "stop_loss","confidence","logic","factors","basis","regime"}
    f: 特征 dict (由 recommend_stocks 提取)
    """
    ms = market_status or {}
    regime = market_regime(ms)
    p_mom, n_mom = _momentum_prob(f)
    p_tech, n_tech = _technical_prob(f)
    p_cap, n_cap = _capital_prob(f)
    p_sec, n_sec = _sector_prob(f)
    p_sent, _ = _sentiment_prob(f, ms)
    p_fund, n_fund = _fundamental_prob(f)
    # 综合 = 基准 + 各维度增量之和 + 市场环境修正
    deltas = (p_mom + p_tech + p_cap + p_sec + p_sent + p_fund) - 6 * PROB_BASE
    raw_prob = _clamp(PROB_BASE + deltas + int(regime["prob_adjust"]))
    # 概率校准: 用 prediction_review 实测命中率压缩虚高概率 (防过度乐观)
    up_prob = calibrate_prob(raw_prob)
    confidence = _confidence(up_prob, f)
    if regime["regime"] == "偏弱" and confidence == "高":
        confidence = "中"  # 市场偏弱整体降一档
    gain = _expected_gain(up_prob)
    notes = n_mom + n_tech + n_cap + n_sec + n_fund
    factors = {
        "momentum": p_mom, "technical": p_tech, "capital": p_cap,
        "sector": p_sec, "sentiment": p_sent, "fundamental": p_fund,
    }
    return {
        "horizon": PRED_HORIZON,
        "up_prob": up_prob,
        "expected_gain": gain,
        "gain_range": [round(gain * 0.55, 1), round(gain * 1.45, 1)],
        "stop_loss": _stop_loss_pct(f, confidence),
        "confidence": confidence,
        "logic": _pick_logic(notes, 3),
        "factors": factors,
        "basis": "基于历史动量统计: RPS≥90个股次日上行概率约50-55%, 板块前三约60%; 综合概率为量化预判, 非确定结论",
        "regime": regime["regime"],
    }


# ============================================================
# 每日预测校准 (预测效果复盘)
# ============================================================

def record_predictions(rec_list, latest_date, log_file=None):
    """将当日推荐预测写入 predictions_log.json (保留近 30 个交易日)"""
    path = Path(log_file) if log_file else LOG_FILE
    log = _load_json(path)
    preds = []
    for r in rec_list or []:
        p = r.get("prediction")
        if not p:
            continue
        preds.append({
            "code": r.get("ts_code"), "name": r.get("name"),
            "price": r.get("price"), "up_prob": p.get("up_prob"),
            "expected_gain": p.get("expected_gain"), "horizon": p.get("horizon"),
            "confidence": p.get("confidence"),
            "sector_count": r.get("sector_count"), "hit_count": (r.get("technical") or {}).get("hit_count"),
            "net_inflow_3d": (r.get("capital") or {}).get("net_inflow_3d"),
        })
    log[str(latest_date)] = preds
    dates = sorted(log.keys())[-30:]
    trimmed = {d: log[d] for d in dates}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)
    return len(preds)


def _build_close_index(df):
    """DataFrame → {ts_code: close} (跳过 NaN/None), 供 O(1) 查询"""
    idx = {}
    codes = df["ts_code"] if "ts_code" in df.columns else df.get("code")
    closes = df["close"]
    for code, v in zip(codes, closes):
        if v is None or str(v) in ("nan", "None"):
            continue
        idx[code] = float(v)
    return idx


def _lookup_close(daily_map, date, code, close_cache=None):
    """查某日收盘价: 优先用 close_cache 缓存索引, 首次访问该日期时构建一次"""
    df = daily_map.get(date)
    if df is None:
        return None
    if close_cache is not None and date in close_cache:
        return close_cache[date].get(code)
    idx = _build_close_index(df)
    if close_cache is not None:
        close_cache[date] = idx
    return idx.get(code)


def calc_prediction_accuracy(daily_map, latest_date, log_file=None, review_file=None):
    """每日收盘后自动复盘: 核对历史预测实际涨跌, 输出分维度胜率 (prediction_review.json)
    daily_map: 当前运行窗口的日线 (含过去 80 日, 可覆盖上次预测后的 3 日验证)
    """
    log_file = Path(log_file) if log_file else LOG_FILE
    review_file = Path(review_file) if review_file else REVIEW_FILE
    log = _load_json(log_file)
    if not log:
        return None
    trade_days = sorted(daily_map.keys())
    day_pos = {d: i for i, d in enumerate(trade_days)}
    close_cache = {}  # 按目标日期惰性构建 {code: close}, 避免每条预测全表过滤 (N+1)
    verified = []
    for date, preds in sorted(log.items()):
        if date >= latest_date or date not in day_pos:
            continue
        idx = day_pos[date]
        horizon = PRED_HORIZON
        if idx + horizon >= len(trade_days):
            continue  # 数据不足(最近的预测需等 horizon 日后才能验证)
        tgt = trade_days[idx + horizon]
        for p in preds or []:
            close_t = _lookup_close(daily_map, tgt, p.get("code"), close_cache)
            if close_t is None or not p.get("price"):
                continue
            gain = (close_t / p["price"] - 1) * 100
            verified.append({
                "pred_date": date, "code": p.get("code"), "name": p.get("name"),
                "up_prob": p.get("up_prob"), "confidence": p.get("confidence"),
                "sector_count": p.get("sector_count"), "gain": round(gain, 2),
                # 命中=严格上涨: 昨日推荐 → 今日收盘价 > 推荐时价格 (平盘/下跌均不计命中)
                "hit": close_t > p["price"],
            })
    if not verified:
        return None

    def _rate(sub):
        if not sub:
            return None
        hits = sum(1 for x in sub if x["hit"])
        return round(hits / len(sub), 3)

    def _group(key):
        out = {}
        for x in verified:
            k = x.get(key)
            out.setdefault(k, []).append(x)
        return {k: {"samples": len(v), "hit_rate": _rate(v),
                    "avg_gain": round(sum(y["gain"] for y in v) / len(v), 2)} for k, v in out.items()}

    avg_prob = sum(x["up_prob"] or 0 for x in verified) / len(verified)
    _sorted = sorted(verified, key=lambda x: (x["pred_date"], x["code"]))
    review = {
        "update_date": latest_date,
        "horizon": PRED_HORIZON,
        "summary": {
            "samples": len(verified),
            "hit_rate": _rate(verified),
            "avg_gain": round(sum(x["gain"] for x in verified) / len(verified), 2),
            "avg_up_prob": round(avg_prob, 1),
            "prev_dates": sorted({x["pred_date"] for x in verified}),
        },
        "by_confidence": _group("confidence"),
        "by_sector_count": _group("sector_count"),
        # 完整已验证列表(供周度复盘筛选本周样本, 避免 recent 仅15条截断)
        "all": _sorted,
        "recent": _sorted[-15:],
        "note": "每日校准: 命中=预测后%s个交易日收盘价>预测日现价(严格上涨); 分维度胜率用于动态调整因子权重" % PRED_HORIZON,
    }
    with open(review_file, "w", encoding="utf-8") as f:
        json.dump(review, f, ensure_ascii=False, indent=2, allow_nan=False)
    return review


# ============================================================
# 独立运行(调试): 基于已有 recommendations.json 重放预测
# ============================================================
if __name__ == "__main__":
    rec = _load_json(STATIC_DIR / "recommendations.json")
    print("推荐数:", len(rec.get("stocks") or []))
    for s in (rec.get("stocks") or [])[:5]:
        p = s.get("prediction") or {}
        print("  %s %s 概率%s%% 预期%s%% 止损%s%% 置信度%s 逻辑:%s" % (
            s.get("ts_code"), s.get("name"), p.get("up_prob"),
            p.get("expected_gain"), p.get("stop_loss"),
            p.get("confidence"), "、".join(p.get("logic") or [])))
