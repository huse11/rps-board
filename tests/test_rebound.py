# -*- coding: utf-8 -*-
"""单元测试: 超跌反转策略 rebound.py — 各层边界与兜底正确性"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pandas as pd
from datetime import datetime

import rebound as rb

def _mk_closes(base, high_idx, length=260, now_drop_to=None, stable_after=10,
               dip_days=25, dip_ratio=0.985):
    """合成收盘价序列: 前半段涨至high_idx位置达峰值, 之后一路回撤, 再做一次短暂下探(最低价出现在此), 最后N根横盘企稳。
    stable_after: 最后 N 根横盘(=模拟近N日不创新低, 默认10日)
    dip_days: 在企稳平台前 dip_days 根, 价格短暂下探到 dip_ratio × drop_to (形成最低价)
    dip_ratio: 下探相对目标价的比例 (<1.0)
    """
    arr = np.linspace(base, base * 2.2, high_idx)  # 先涨上去 (最高 2.2x)
    peak = float(arr[-1]) if len(arr) > 0 else (base * 2.2)
    if now_drop_to is None:
        drop_to = peak * 0.45
    else:
        drop_to = now_drop_to
    n_drop_total = length - high_idx
    # 下跌段需拆成: 主跌段 + dip段 + 企稳段
    n_stable = stable_after
    n_dip = min(dip_days, max(0, n_drop_total - n_stable - 10))
    n_fall = n_drop_total - n_dip - n_stable  # 主跌段长度
    assert n_fall > 0, "length=%d too short: high_idx=%d drop=%d" % (length, high_idx, n_drop_total)
    # 1) 主跌段: peak → drop_to
    fall_tail = np.linspace(peak, drop_to, n_fall) if n_fall > 1 else np.array([peak, drop_to][:n_fall])
    # 2) dip 段: 从 drop_to 下探到 drop_to*dip_ratio (最低), 然后回到 drop_to 附近
    if n_dip >= 2:
        dip_low = drop_to * dip_ratio
        dip_half = n_dip // 2
        dip_part1 = np.linspace(drop_to, dip_low, dip_half)
        dip_part2 = np.linspace(dip_low, drop_to, n_dip - dip_half)
        dip_tail = np.concatenate([dip_part1, dip_part2])
    else:
        dip_tail = np.array([drop_to])
    # 3) 企稳段: 全部 = drop_to
    stable_tail = np.full(n_stable, drop_to, dtype=float)
    # 组合
    all_ = np.concatenate([arr, fall_tail, dip_tail, stable_tail])
    # 截断/补齐到 length
    if len(all_) > length:
        all_ = all_[:length]
    return all_


def _mk_df(closes, latest_date="20260828", code="000001.SZ", name="平安银行", vol_mult=1e5):
    dates = []
    start = pd.Timestamp(str(latest_date)) - pd.Timedelta(days=len(closes) * 2)
    for i in range(len(closes)):
        t = start + pd.Timedelta(days=i * 2)  # 跳过周末 (粗略)
        dates.append(int(t.strftime("%Y%m%d")))
    # 保证最新是 latest_date
    dates[-1] = int(latest_date)
    daily_map = {}
    for i, ts in enumerate(dates):
        c = float(closes[i])
        daily_map.setdefault(ts, []).append({
            "ts_code": code,
            "open": c, "high": c * 1.02, "low": c * 0.98, "close": c,
            "pct_chg": (c / (closes[i - 1] if i > 0 else c) - 1) * 100,
            "vol": int(vol_mult),
            "amount": float(vol_mult) * c,  # 千元
        })
    for ts in list(daily_map.keys()):
        daily_map[ts] = pd.DataFrame(daily_map[ts])
    # 个股基础信息 (industry_df)
    industry_df = pd.DataFrame([{
        "ts_code": code, "name": name, "industry": "银行", "list_date": "20100101",
    }])
    # latest_idx 基础行情 row
    rows = [{
        "ts_code": code, "name": name, "industry": "银行",
        "price": float(closes[-1]),
        "pct_chg": (float(closes[-1]) / float(closes[-2]) - 1) * 100,
        "vol": int(vol_mult), "amount": float(vol_mult) * float(closes[-1]),
        "list_date": "20100101",
        "pe_ttm": 10.0, "circ_mv": 2000000.0,  # 200亿 = 200万万元
        "turnover_rate": 3.5, "vol_ratio": 1.5,
    }]
    return daily_map, industry_df, rows


def test_L1_drawdown_tier_gate():
    """L1: 回撤门槛随档位动态切换 (规范 2.1/2.2): 标准档35% / 保守档45% / 激进档25%
    注: 合成序列必须为「急跌」形态 (峰值距当前≤40交易日), 否则会被下跌性质校验(规范2.3)识别为阴跌而剔除
    """
    # 案例1: 标准档(35%)下 回撤仅30% → 剔除
    rb._activate_tier("standard")
    log = []
    closes = _mk_closes(base=10, high_idx=230, length=260, now_drop_to=22 * 0.70)  # 峰值22, 现价15.4 → 回撤30%
    dm, ind_df, rows = _mk_df(closes)
    from recommend import build_daily_by_code
    dby = build_daily_by_code(dm, [r["ts_code"] for r in rows], 20260828, n_days=260)
    passed = rb._screen_L1_oversold(rows, dby, 20260828, candidate_log=log)
    assert len(passed) == 0, "标准档下回撤仅30%%应被剔除, 实际通过 %d 只: %s" % (len(passed), log)
    print("  ✓ L1-标准档(35%%)下回撤30%%被剔除: OK (%s)" % ((log[0].get("reason") if log else "无日志"),))

    # 案例2: 标准档(35%)下 回撤40% + 10日横盘企稳 → 通过
    closes = _mk_closes(base=10, high_idx=230, length=260, now_drop_to=22 * 0.60)  # 现价13.2 → 回撤40%
    dm, ind_df, rows = _mk_df(closes, code="000002.SZ", name="测试超跌")
    dby = build_daily_by_code(dm, [r["ts_code"] for r in rows], 20260828, n_days=260)
    log2 = []
    passed = rb._screen_L1_oversold(rows, dby, 20260828, candidate_log=log2)
    assert len(passed) >= 1, "标准档下回撤40%%+企稳应通过, 实际0只, 原因:%s" % ([r["reason"] for r in log2])
    assert 35 <= float(passed[0]["drawdown_pct"]) <= 60, "回撤率不对: %s" % passed[0]["drawdown_pct"]
    print("  ✓ L1-标准档(35%%)下回撤40%%+企稳通过:  OK 回撤%.1f%% 深跌档=%s" % (passed[0]["drawdown_pct"], passed[0]["deep_drawdown"]))

    # 案例3: 保守档(45%)下 回撤40% → 剔除 (档位收紧后门槛变高)
    rb._activate_tier("conservative")
    log3 = []
    passed = rb._screen_L1_oversold(rows, dby, 20260828, candidate_log=log3)
    assert len(passed) == 0, "保守档(45%%)下回撤40%%应剔除, 实际通过 %d 只: %s" % (len(passed), log3)
    print("  ✓ L1-保守档(45%%)下回撤40%%被剔除: OK")

    # 案例4: 激进档(25%)下 回撤30% → 通过 (档位放宽后门槛变低)
    rb._activate_tier("aggressive")
    closes = _mk_closes(base=10, high_idx=230, length=260, now_drop_to=22 * 0.70)
    dm, ind_df, rows = _mk_df(closes, code="000003.SZ", name="激进超跌")
    dby = build_daily_by_code(dm, [r["ts_code"] for r in rows], 20260828, n_days=260)
    log4 = []
    passed = rb._screen_L1_oversold(rows, dby, 20260828, candidate_log=log4)
    assert len(passed) >= 1, "激进档(25%%)下回撤30%%应通过, 实际0只, 原因:%s" % ([r["reason"] for r in log4])
    print("  ✓ L1-激进档(25%%)下回撤30%%通过: OK")

    rb._activate_tier("standard")  # 还原默认档位, 避免污染后续测试


def test_L2_ST_filter():
    """L2: ST/退市股 必须剔除"""
    rows_ok = [{"ts_code": "000001.SZ", "name": "平安银行", "price": 12, "vol": 10000, "industry": "银行",
               "pe_ttm": 8, "circ_mv": 2e6, "turnover_rate": 2}]
    rows_st = [{"ts_code": "000002.ST", "name": "*ST万科", "price": 3, "vol": 5000, "industry": "房地产",
                "pe_ttm": None, "circ_mv": 1e5, "turnover_rate": 5}]
    fina_empty = {}
    latest = 20260828
    dm_empty = {latest: pd.DataFrame([{"ts_code": "000001.SZ", "close": 12, "vol": 10000, "industry": "银行"}])}
    log = []
    out_ok = rb._screen_L2_fundamental(rows_ok, fina_empty, latest, dm_empty, candidate_log=log)
    assert len(out_ok) >= 1, "非ST不应被剔除"
    log2 = []
    out_st = rb._screen_L2_fundamental(rows_st, fina_empty, latest, dm_empty, candidate_log=log2)
    assert len(out_st) == 0, "*ST必须剔除, 实际通过 %d 只 log=%s" % (len(out_st), log2)
    print("  ✓ L2-ST/*ST 剔除: OK")


def test_L2_consecutive_loss_widen_reject():
    """L2: 连续两期扣非为负+亏损扩大 → 剔除"""
    rows = [{"ts_code": "000001.SZ", "name": "亏损扩大股", "price": 5, "vol": 1000, "industry": "综合",
             "pe_ttm": None, "circ_mv": 5e5, "turnover_rate": 3}]
    fina = {"000001.SZ": {"kcfjnc": -100.0, "kcfjnc_prev": -50.0}}  # -50 → -100 (亏损扩大)
    latest = 20260828
    dm = {latest: pd.DataFrame([{"ts_code": "000001.SZ", "close": 5, "vol": 1000, "industry": "综合"}])}
    log = []
    out = rb._screen_L2_fundamental(rows, fina, latest, dm, candidate_log=log)
    assert len(out) == 0, "连续亏损扩大应剔除, 实际过 %d 只 log=%s" % (len(out), log)
    rej_reason = log[0]["reason"] if log else ""
    assert "连续" in rej_reason or "亏损" in rej_reason, "剔除原因不对: %s" % rej_reason
    print("  ✓ L2-连续两期扣非亏损+扩大 剔除: OK 原因:", rej_reason)


def test_score_weights_sum():
    """综合评分 权重 25/25/25/25=100, 满分应≤100 最低≥0 (2026-08-29 估值维度加入)"""
    rb._activate_tier("standard")  # 标准档: min=35 / deep=55
    r_ok = {"drawdown_pct": 65, "price": 20, "drawdown_high_close": 50, "deep_drawdown": True}
    r_ok["fundamental_score"] = 90
    r_ok["valuation_score"] = 80
    r_ok["signal_score"] = 80
    tot, os_s, fu_s, va_s, si_s = rb._final_score(r_ok)
    # 65%回撤≥深跌档55% → 超跌分 100 (cap)
    assert 0 <= tot <= 100, "总分应在0~100, 实际: %d (超跌%d/基本面%d/估值%d/信号%d)" % (tot, os_s, fu_s, va_s, si_s)
    assert fu_s == 90 and si_s == 80 and va_s == 80 and os_s == 100, "分项不对: 超跌%d/基本面%d/估值%d/信号%d" % (os_s, fu_s, va_s, si_s)
    # 期望: 25*100/100 + 25*90/100 + 25*80/100 + 25*80/100 = 25+22.5+20+20 = 87.5 → 88
    assert tot == 88, "四维 25/25/25/25 总分应为 88, 实际 %d" % tot
    print("  ✓ L4-评分权重(25/25/25/25):  超跌%d×0.25 + 基本面%d×0.25 + 估值%d×0.25 + 信号%d×0.25 = 总分%d  (0≤≤100: OK)" % (os_s, fu_s, va_s, si_s, tot))


def test_final_score_oversold_threshold():
    """超跌分: 标准档(35/55)下 刚好35% → 60分; 45% → 80分; ≥55% → 100分"""
    rb._activate_tier("standard")  # min=35 / deep=55
    cases = [(35, 60), (45, 80), (55, 100), (65, 100)]
    for dd, expect in cases:
        r = {"drawdown_pct": dd, "price": 10, "fundamental_score": 0, "valuation_score": 0, "signal_score": 0, "deep_drawdown": dd >= 55}
        tot, os_s, _, _, _ = rb._final_score(r)
        assert os_s == expect, "回撤%.1f%% 超跌分应为%d, 实际%d" % (dd, expect, os_s)
    print("  ✓ L4-超跌分(标准档35/55) 35%→60 / 45%→80 / 55%→100 / 65%→100: OK")


def _mk_val_row(code="000001.SZ", industry="通用", pe=20.0, dd=55.0, name="测试"):
    """构造估值层测试用行 (含 L1 已写入的 drawdown_pct/_pump_ratio)"""
    return {"ts_code": code, "name": name, "industry": industry,
            "price": 10.0, "pe_ttm": pe, "drawdown_pct": dd,
            "drawdown_high_close": 22.0, "deep_drawdown": dd >= 60,
            "_pump_ratio": None}


def test_L2_valuation_industry_cap_buckets():
    """行业PE分桶: 煤炭→周期25 / 半导体→科技成长50 / 银行→通用60 / 行业缺失→未知25(从严)"""
    assert rb._industry_pe_cap("煤炭") == ("周期", 25)
    assert rb._industry_pe_cap("半导体") == ("科技成长", 50)
    assert rb._industry_pe_cap("银行") == ("通用", 60)
    assert rb._industry_pe_cap(None) == ("未知", 25)
    assert rb._industry_pe_cap("") == ("未知", 25)
    print("  ✓ 行业PE分桶: 周期25 / 科技成长50 / 消费医药40 / 制造35 / 通用60 / 未知25: OK")


def test_L2_valuation_unknown_industry_high_pe_reject():
    """回归: 行业缺失(申万未收录) 不得套用通用60上限 — 高PE(如晶华新材603683 化工PE51)必须被拦截"""
    rows = [_mk_val_row("603683.SH", "", pe=51.19)]      # 行业缺失 + PE51 → 未知档25 → 剔除
    log = []
    out = rb._screen_L2_valuation(rows, {}, candidate_log=log)
    assert len(out) == 0, "行业缺失+PE51应剔除(未知档25), 实际通过 %d 只 log=%s" % (len(out), log)
    assert "PE 51.2 > 未知上限25" in str(log), "日志应记录 未知上限25 拦截: %s" % log
    # 行业缺失但PE足够低(≤25) → 可进入下游层继续校验
    rows2 = [_mk_val_row("603683.SH", "", pe=18.0)]
    out2 = rb._screen_L2_valuation(rows2, {}, candidate_log=[])
    assert len(out2) == 1 and out2[0]["pe_bucket"] == "未知" and out2[0]["pe_cap"] == 25
    print("  ✓ 行业缺失从严: PE51>未知25剔除 / PE18≤未知25放行: OK")


def test_L2_valuation_absolute_pe_reject():
    """3.1 绝对PE分档: PE>行业上限 → 一票否决; PE≤上限 且无历史 → 通过"""
    rows = [_mk_val_row("000001.SZ", "煤炭", pe=30.0),    # 周期上限25 → 剔除
            _mk_val_row("000002.SZ", "煤炭", pe=20.0)]    # 20≤25 → 通过
    log = []
    out = rb._screen_L2_valuation(rows, {}, candidate_log=log)
    assert len(out) == 1, "PE>行业上限应剔除, 实际通过 %d 只 log=%s" % (len(out), log)
    assert out[0]["ts_code"] == "000002.SZ" and out[0]["pe_bucket"] == "周期" and out[0]["pe_cap"] == 25
    assert out[0]["valuation_score"] > 0
    print("  ✓ 3.1 绝对PE分档否决: PE30>周期上限25剔除 / PE20通过: OK")


def test_L2_valuation_pe_pct_and_val_dd():
    """3.2 PE分位≥70%剔除; 3.3 估值回撤<股价回撤×0.7剔除 (杀业绩非杀估值)"""
    # 案例1: 分位过高 → 剔除
    pe_hist_high = {"dates": ["20260%03d" % i for i in range(300)], "pe": [10.0] * 100 + [24.0] * 200}
    rows = [_mk_val_row("000001.SZ", "银行", pe=25.0, dd=55.0)]
    log = []
    out = rb._screen_L2_valuation(rows, {"000001.SZ": pe_hist_high}, candidate_log=log)
    assert len(out) == 0, "PE近3年分位≥70%%应剔除, 实际通过 %d 只 log=%s" % (len(out), log)
    # 案例2: 分位低但估值回撤不足 → 剔除 (杀业绩)
    pe_hist_dd = {"dates": ["20260%03d" % i for i in range(300)], "pe": [10.0] * 50 + [30.0] * 250}
    rows2 = [_mk_val_row("000002.SZ", "银行", pe=25.0, dd=60.0)]  # 估值回撤1-25/30=16.7% < 60%×0.7=42%
    log2 = []
    out2 = rb._screen_L2_valuation(rows2, {"000002.SZ": pe_hist_dd}, candidate_log=log2)
    assert len(out2) == 0, "估值回撤不足应剔除, 实际通过 %d 只 log=%s" % (len(out2), log2)
    # 案例3: 分位低 + 估值回撤足 → 通过 (真错杀)
    rows3 = [_mk_val_row("000003.SZ", "银行", pe=12.0, dd=55.0)]  # 分位17%/回撤1-12/30=60% ≥ 38.5%
    log3 = []
    out3 = rb._screen_L2_valuation(rows3, {"000003.SZ": pe_hist_dd}, candidate_log=log3)
    assert len(out3) == 1, "估值低估+杀估值到位应通过, 实际 %d 只 log=%s" % (len(out3), log3)
    assert out3[0]["valuation_score"] >= 70, "低估+杀估值到位 估值分应≥70, 实际 %s" % out3[0]["valuation_score"]
    assert out3[0]["pe_pct_3y"] is not None and out3[0]["pe_dd_pct"] is not None
    print("  ✓ 3.2 PE分位≥70%%剔除 / 3.3 估值回撤不足剔除 / 真错杀通过: OK")


def test_L2_valuation_theme_pump_reject():
    """伪超跌排雷: 近1年区间振幅≥3倍 (题材爆炒后腰斩) → 剔除"""
    rows = [_mk_val_row("000001.SZ", "银行", pe=12.0, dd=55.0)]
    rows[0]["_pump_ratio"] = 3.5   # 1年振幅3.5倍 → 爆炒腰斩
    log = []
    out = rb._screen_L2_valuation(rows, {}, candidate_log=log)
    assert len(out) == 0, "题材爆炒后腰斩应剔除, 实际通过 %d 只 log=%s" % (len(out), log)
    print("  ✓ 伪超跌排雷-题材爆炒腰斩(振幅≥3倍): 剔除: OK")


def test_L2_fundamental_upgrade_constraints():
    """基本面升级硬约束: 净利≤0 / 现金流≤0 / 扣非增速<-10% / PEG>2 → 剔除 (有数据时)"""
    base = {"ts_code": "000001.SZ", "name": "测试", "price": 10, "vol": 1000,
            "industry": "综合", "pe_ttm": 30.0, "circ_mv": 5e5, "turnover_rate": 3}
    latest = 20260828
    dm = {latest: pd.DataFrame([{"ts_code": "000001.SZ", "close": 10, "vol": 1000, "industry": "综合"}])}
    # 1) 净利≤0 → 剔除
    log = []
    out = rb._screen_L2_fundamental([dict(base)], {"000001.SZ": {"net_profit": -5.0}}, latest, dm, candidate_log=log)
    assert len(out) == 0, "净利≤0 应剔除, log=%s" % log
    # 2) 经营现金流≤0 → 剔除
    log = []
    out = rb._screen_L2_fundamental([dict(base)], {"000001.SZ": {"net_profit": 8.0, "net_cash_flows_oper_act": -2.0}}, latest, dm, candidate_log=log)
    assert len(out) == 0, "经营现金流≤0 应剔除, log=%s" % log
    # 3) 扣非增速<-10% → 剔除
    log = []
    out = rb._screen_L2_fundamental([dict(base)], {"000001.SZ": {"net_profit": 8.0, "dt_netprofit_yoy": -25.0}}, latest, dm, candidate_log=log)
    assert len(out) == 0, "扣非增速-25%%<-10%% 应剔除, log=%s" % log
    # 4) PEG>2 → 剔除 (PE30 / 增速10% → PEG3)
    log = []
    out = rb._screen_L2_fundamental([dict(base)], {"000001.SZ": {"net_profit": 8.0, "dt_netprofit_yoy": 10.0}}, latest, dm, candidate_log=log)
    assert len(out) == 0, "PEG3>2 应剔除, log=%s" % log
    # 5) 全部健康 → 通过 (PE15/增速20% → PEG0.75)
    base2 = dict(base); base2["pe_ttm"] = 15.0
    log = []
    out = rb._screen_L2_fundamental([base2], {"000001.SZ": {"net_profit": 8.0, "net_cash_flows_oper_act": 3.0, "dt_netprofit_yoy": 20.0}}, latest, dm, candidate_log=log)
    assert len(out) == 1, "健康基本面应通过, log=%s" % log
    print("  ✓ 基本面升级硬约束: 净利≤0/现金流≤0/增速<-10%%/PEG>2 剔除, 健康通过: OK")


def test_L2_valuation_loss_maker_reject():
    """估值层亏损拦截: PE无效(亏损) + 财务确认净利≤0 → 剔除; 财务缺失/净利为正 → 放行"""
    # 1) PE 无效(负值) + 财务亏损 → 剔除 (价值陷阱)
    rows = [_mk_val_row("000001.SZ", "银行", pe=-5.0, dd=55.0)]
    log = []
    out = rb._screen_L2_valuation(rows, {}, fina_map={"000001.SZ": {"net_profit": -3.0}}, candidate_log=log)
    assert len(out) == 0, "亏损股(净利-3亿, PE无效)应剔除, log=%s" % log
    # 2) PE 无效 + 财务也缺失 → 放行 (交给基本面层拦截"双缺失")
    rows2 = [_mk_val_row("000002.SZ", "银行", pe=None, dd=55.0)]
    log2 = []
    out2 = rb._screen_L2_valuation(rows2, {}, fina_map={}, candidate_log=log2)
    assert len(out2) == 1, "财务缺失时应放行给基本面层, 实际 %d 只 log=%s" % (len(out2), log2)
    # 3) PE 无效 + 财务净利为正 → 放行 (净利为正, 盈利有支撑)
    rows3 = [_mk_val_row("000003.SZ", "银行", pe=None, dd=55.0)]
    log3 = []
    out3 = rb._screen_L2_valuation(rows3, {}, fina_map={"000003.SZ": {"net_profit": 5.0}}, candidate_log=log3)
    assert len(out3) == 1, "净利为正(PE缺失)应放行, log=%s" % log3
    print("  ✓ 估值层亏损拦截: 亏损剔除 / 财务缺失放行 / 净利为正放行: OK")


def test_L2_fundamental_data_gap_gate():
    """基本面层数据缺口拦截: 财务缺失+PE无效 → 剔除; 财务缺失但PE有效 → 通过(估值验证兜底45分)"""
    base = {"ts_code": "000001.SZ", "name": "测试", "price": 10, "vol": 1000,
            "industry": "综合", "pe_ttm": None, "circ_mv": 5e5, "turnover_rate": 3}
    latest = 20260828
    dm = {latest: pd.DataFrame([{"ts_code": "000001.SZ", "close": 10, "vol": 1000, "industry": "综合"}])}
    # 1) 财务缺失 + PE无效 → 剔除 (无法验证基本面, 宁缺毋滥)
    log = []
    out = rb._screen_L2_fundamental([dict(base)], {}, latest, dm, candidate_log=log)
    assert len(out) == 0, "财务+估值双缺失应剔除, log=%s" % log
    # 2) 财务缺失但 PE 有效(已过估值层=盈利验证) → 通过, 兜底分 45
    base2 = dict(base); base2["pe_ttm"] = 20.0
    log2 = []
    out2 = rb._screen_L2_fundamental([base2], {}, latest, dm, candidate_log=log2)
    assert len(out2) == 1, "PE有效(估值已验证)应通过, log=%s" % log2
    assert out2[0]["fundamental_score"] >= 45, "兜底分应≥45, 实际 %s" % out2[0]["fundamental_score"]
    print("  ✓ 基本面数据缺口: 双缺失剔除 / PE有效兜底45分: OK")


def test_predict_rebound_pe_high_downgrade():
    """高PE降档: _pe_high_flag=True → 置信度自动降一档 (总分80+信号5 → 高; PE偏高 → 中)"""
    r = {"drawdown_pct": 65, "price": 20.0, "drawdown_high_close": 50.0, "signal_score": 85, "fundamental_score": 85}
    p_base = rb._predict_rebound(r, 20260828, 50.0, total_score=80, signal_count=5)
    assert p_base["confidence"] == "高"
    r2 = dict(r); r2["_pe_high_flag"] = True
    p_dn = rb._predict_rebound(r2, 20260828, 50.0, total_score=80, signal_count=5)
    assert p_dn["confidence"] == "中", "PE偏高应降档 高→中, 实际 %s" % p_dn["confidence"]
    print("  ✓ 高PE置信度降档: 高→中: OK")


def test_predict_rebound_confidence_tiers():
    """置信度判定 (规范 5.2): 总分≥75且信号≥5→高; 60-74且信号3-4→中; 其余→低"""
    r = {"drawdown_pct": 65, "price": 20.0, "drawdown_high_close": 50.0, "signal_score": 85, "fundamental_score": 85}
    def _conf(total, cnt):
        return rb._predict_rebound(r, 20260828, 50.0, total_score=total, signal_count=cnt)["confidence"]
    assert _conf(80, 5) == "高", "总分80+信号5 应为高"
    assert _conf(80, 6) == "高", "总分80+信号6 应为高"
    assert _conf(70, 3) == "中", "总分70+信号3 应为中"
    assert _conf(74, 4) == "中", "总分74+信号4 应为中"
    assert _conf(80, 2) == "低", "总分高但信号<3 应为低 (信号不足)"
    assert _conf(50, 6) == "低", "总分<60 应为低"
    print("  ✓ 置信度: (80,5)→高 / (70,3)→中 / (80,2)→低 / (50,6)→低: OK")


def test_predict_rebound():
    """预测化: 上涨概率/预期涨幅/止损/置信度合理区间"""
    r = {"drawdown_pct": 65, "price": 20.0, "drawdown_high_close": 50.0, "signal_score": 85, "fundamental_score": 85}
    p = rb._predict_rebound(r, 20260828, 50.0, total_score=80, signal_count=5)
    up = p["up_prob"]
    assert 30 <= up <= 85, "上涨概率应在30~85, 实际: %s" % up
    gain = p["expected_gain_pct"]
    # 第一修复位: (20 + (50-20)*0.33) = 29.9, 预期涨幅=(29.9/20-1)*100≈49.5%
    assert 15 <= gain <= 60, "预期涨幅应在15~60%%, 实际: %s" % gain
    assert p["stop_loss_pct"] == -5.0
    assert p["confidence"] in ("高", "中", "低")
    # H4 兼容字段: 前端 fmtPredGain/fmtPredStop 与 predict.record_predictions 读 expected_gain/stop_loss
    assert p["expected_gain"] == gain, "expected_gain 别名应与 expected_gain_pct 一致"
    assert p["stop_loss"] == p["stop_loss_pct"], "stop_loss 别名应与 stop_loss_pct 一致"
    print("  ✓ 预测化输出合理: 上涨概率%.1f%% 预期涨幅%.1f%% 止损%s 置信度%s" % (up, gain, p["stop_loss_pct"], p["confidence"]))


def test_market_crash_protection():
    """大盘暴跌市: 连续3日上涨占比<0.4 → REJECT is_safe=False"""
    from recommend import fetch_index_daily, check_market_env
    # 临时关闭 IGNORE_MARKET_ENV (用户默认开启忽略大盘, 但暴跌市保护单元测试仍需验证开关关闭时逻辑正确)
    _orig = rb.CONFIG_REBOUND["run_mode"].get("IGNORE_MARKET_ENV", False)
    try:
        rb.CONFIG_REBOUND["run_mode"]["IGNORE_MARKET_ENV"] = False
        # 构造假 daily_map: 最近3天上涨占比都很低 (模拟普跌)
        dm = {}
        latest = 20260828
        for i, d in enumerate([20260826, 20260827, 20260828]):
            rows_fake = []
            for j in range(100):
                # 只有 20 家上涨 (20%占比)
                pct = 1.0 if j < 20 else -1.0
                rows_fake.append({"ts_code": "00%04d.SZ" % j, "pct_chg": pct, "vol": 100, "close": 5 + j * 0.1})
            dm[d] = pd.DataFrame(rows_fake)
        base_stat = {"is_safe": True, "advance_ratio": 0.2, "limit_down_count": 20,
                     "reason": "基础通过", "indexes": []}
        ok, adj = rb._rebound_market_adjust({}, dm, latest, base_stat)
        assert ok is False, "连续3日普跌应该进入暴跌保护 → REJECT, 实际 ok=%s" % ok
        assert adj.get("is_safe") is False
        print("  ✓ 暴跌市保护(连续3日普跌→拒绝):  OK → %s" % adj.get("reason", "")[:60])
    finally:
        rb.CONFIG_REBOUND["run_mode"]["IGNORE_MARKET_ENV"] = _orig


def _mk_advance_daily_map(up_ratio, days=3, latest=20260828):
    """构造 daily_map: 每个交易日的上涨占比 = up_ratio (0~1)"""
    dm = {}
    base_dates = [latest - i for i in range(days - 1, -1, -1)]
    for d in base_dates:
        rows_fake = []
        for j in range(100):
            pct = 1.0 if j < int(up_ratio * 100) else -1.0
            rows_fake.append({"ts_code": "00%04d.SZ" % j, "pct_chg": pct, "vol": 100, "close": 5 + j * 0.1})
        dm[d] = pd.DataFrame(rows_fake)
    return dm


def test_select_tier_auto_and_pause():
    """档位自动适配 (规范 2.2): 连续3日普跌→暂停; 强势(≥60%)→激进25%; 偏弱(<42%)→保守45%; 震荡→标准35%"""
    # 1) 连续3日上涨占比 25% (<30%) → 暂停
    dm_crash = _mk_advance_daily_map(0.25, days=3, latest=20260828)
    sel = rb._select_tier(None, dm_crash, 20260828)
    assert sel["pause"] is True, "连续3日普跌(25%%)应暂停, 实际 %s" % sel
    print("  ✓ _select_tier-连续3日普跌→暂停: OK (%s)" % sel["reason"])

    # 2) 上涨占比 70% (≥60%) → 强势 → 激进档 25%
    dm_strong = _mk_advance_daily_map(0.70, days=3, latest=20260828)
    sel = rb._select_tier(None, dm_strong, 20260828)
    assert sel["pause"] is False and sel["tier"] == "aggressive" and sel["min"] == 25, \
        "强势市应切激进档(25%%), 实际 %s" % sel
    print("  ✓ _select_tier-强势(70%%)→激进档25%%: OK")

    # 3) 上涨占比 35% (<42%) → 偏弱 → 保守档 45%
    dm_weak = _mk_advance_daily_map(0.35, days=3, latest=20260828)
    sel = rb._select_tier(None, dm_weak, 20260828)
    assert sel["pause"] is False and sel["tier"] == "conservative" and sel["min"] == 45, \
        "偏弱市应切保守档(45%%), 实际 %s" % sel
    print("  ✓ _select_tier-偏弱(35%%)→保守档45%%: OK")

    # 4) 上涨占比 50% (42%~60%) → 震荡 → 标准档 35%
    dm_steady = _mk_advance_daily_map(0.50, days=3, latest=20260828)
    sel = rb._select_tier(None, dm_steady, 20260828)
    assert sel["pause"] is False and sel["tier"] == "standard" and sel["min"] == 35, \
        "震荡市应切标准档(35%%), 实际 %s" % sel
    print("  ✓ _select_tier-震荡(50%%)→标准档35%%: OK")

    rb._activate_tier("standard")  # 还原


def test_is_slow_drift_fast_vs_slow():
    """下跌性质校验 (规范 2.3): 2个月内急跌→保留(False); 3个月以上阴跌+创新低+无反弹→剔除(True)"""
    # 1) 急跌: 峰值距当前 ≤40 交易日 → 保留
    closes_fast = _mk_closes(base=10, high_idx=230, length=260, now_drop_to=22 * 0.45)
    assert rb._is_slow_drift(closes_fast) is False, "2个月内急跌应保留(非阴跌), 实际被判为阴跌"
    print("  ✓ _is_slow_drift-急跌(峰值距当前30日)保留: OK")

    # 2) 阴跌: 3个月以上 + 持续创新低 + 无反弹 → 剔除
    arr = np.linspace(10, 22, 51)      # 0..50 上涨至峰值22
    slow = np.linspace(22, 5, 210)[1:]  # 51..259 一路阴跌至5, 持续创新低且无反弹
    closes_slow = np.concatenate([arr, slow])[:260]
    assert len(closes_slow) == 260
    assert rb._is_slow_drift(closes_slow) is True, "3个月以上阴跌+创新低+无反弹应剔除, 实际保留"
    print("  ✓ _is_slow_drift-阴跌(3个月+创新低+无反弹)剔除: OK")

    # 3) 数据不足 (<120天) → 不误杀 (False)
    assert rb._is_slow_drift(closes_fast[:100]) is False, "数据不足不应判定阴跌"
    print("  ✓ _is_slow_drift-数据不足不误杀: OK")


def test_L2_risk_map_reject():
    """公告风险排雷 (规范 3.1): risk_map 命中 立案/减持/解禁 → 剔除; 无 risk_map → 不校验"""
    base = {"ts_code": "000001.SZ", "name": "测试", "price": 10, "vol": 1000,
            "industry": "综合", "pe_ttm": 15.0, "circ_mv": 5e5, "turnover_rate": 3}
    latest = 20260828
    dm = {latest: pd.DataFrame([{"ts_code": "000001.SZ", "close": 10, "vol": 1000, "industry": "综合"}])}
    fina_ok = {"000001.SZ": {"net_profit": 8.0, "net_cash_flows_oper_act": 3.0, "dt_netprofit_yoy": 20.0,
                             "kcfjnc": 6.0, "kcfjnc_prev": 5.0, "prev_net_profit": 5.0}}
    # 1) 无 risk_map → 通过
    log = []
    out = rb._screen_L2_fundamental([dict(base)], fina_ok, latest, dm, candidate_log=log)
    assert len(out) == 1, "无 risk_map 不应剔除, log=%s" % log
    # 2) 立案/调查公告 → 剔除
    for kw, expect_reason in [("立案调查", "立案"), ("股东减持计划", "减持"), ("限售股解禁", "解禁")]:
        log = []
        out = rb._screen_L2_fundamental([dict(base)], fina_ok, latest, dm,
                                        candidate_log=log, risk_map={"000001.SZ": [f"关于{kw}的公告"]})
        assert len(out) == 0, "命中「%s」公告应剔除, 实际通过, log=%s" % (kw, log)
        assert expect_reason in log[0]["reason"], "剔除原因应含%s, 实际: %s" % (expect_reason, log[0]["reason"])
    # 3) 正常公告(不含关键词) → 不剔除
    log = []
    out = rb._screen_L2_fundamental([dict(base)], fina_ok, latest, dm,
                                    candidate_log=log, risk_map={"000001.SZ": ["2026年半年度报告"]})
    assert len(out) == 1, "无关公告不应剔除, log=%s" % log
    # 4) 个股不在 risk_map 中 → 通过
    log = []
    out = rb._screen_L2_fundamental([dict(base)], fina_ok, latest, dm,
                                    candidate_log=log, risk_map={"999999.SZ": ["立案调查"]})
    assert len(out) == 1, "risk_map 中无此股不应剔除, log=%s" % log
    print("  ✓ 公告风险排雷: 立案/减持/解禁剔除, 无关公告与缺数据放行: OK")


def test_L3_indicators_array_usage():
    """L3 爆发信号层: 技术指标必须按数组使用, 不得因返回标量而抛 TypeError (回归保护).
    recommend.calc_ma_series 返回 float → 旧代码 len(ma5) 直接崩; 修复后走本地数组版.
    """
    closes = _mk_closes(base=10, high_idx=130, length=260, now_drop_to=22 * 0.45)
    dm, ind_df, rows = _mk_df(closes)
    from recommend import build_daily_by_code
    dby = build_daily_by_code(dm, [r["ts_code"] for r in rows], 20260828, n_days=260)
    # 三个本地数组版指标: 长度必须与 closes 一致, 末尾必须是有限数值
    c = np.asarray(closes, dtype=float)
    ma5 = rb._ma_arr(c, 5)
    assert len(ma5) == len(c), "ma5 长度不对: %d != %d" % (len(ma5), len(c))
    assert np.isfinite(ma5[-1]), "ma5 末尾应为有限值"
    dif, dea, hist = rb._macd_arr(c)
    assert len(dif) == len(c) == len(dea) == len(hist), "macd 长度不一致"
    k_arr, d_arr, j_arr = rb._kdj_arr(c * 1.02, c * 0.98, c)
    assert len(k_arr) == len(c) == len(d_arr) == len(j_arr), "kdj 长度不一致"
    # L3 整体调用: 不得抛异常, 返回 list (可能为空, 但绝不能 TypeError)
    log = []
    out = rb._screen_L3_signals(rows, dby, 20260828, mflow_map={}, sector_rps_map={}, candidate_log=log)
    assert isinstance(out, list), "L3 应返回 list"
    print("  ✓ L3-指标数组版: ma5/macd/kdj 长度一致, L3 不抛 TypeError, 通过 %d 只" % len(out))


def test_merge_basic_fields_series():
    """主流程 basic_idx 合并: 值为 pandas Series (daily_basic.iterrows) 时不得抛 ValueError (回归保护).
    旧代码 `basic_idx.get(code, {}) or {}` → 非空 Series 的 bool 判断抛 ValueError (pandas 严格模式), 服务器崩溃.
    """
    rows = [
        {"ts_code": "000001.SZ", "name": "平安银行"},
        {"ts_code": "300123.SZ", "name": "安培龙"},
        {"ts_code": "999999.SZ", "name": "无数据股"},  # basic_idx 无此 code → 各字段 None
    ]
    # 模拟 df_basic.iterrows(): 值是非空 pandas Series (含 NaN 字段)
    basic_idx = {
        "000001.SZ": pd.Series({"pe_ttm": 8.5, "circ_mv": 3000000.0, "turnover_rate": 3.2, "volume_ratio": 1.4}),
        "300123.SZ": pd.Series({"pe_ttm": float("nan"), "circ_mv": 1500000.0, "turnover_rate": 2.1, "volume_ratio": float("nan")}),
    }
    out = rb._merge_basic_fields(rows, basic_idx)
    assert out[0]["pe_ttm"] == 8.5, "Series 值应正确取 pe_ttm, 实际: %r" % out[0]["pe_ttm"]
    assert out[0]["circ_mv"] == 3000000.0
    assert out[0]["turnover_rate"] == 3.2
    assert out[0]["vol_ratio"] == 1.4
    # NaN → None
    assert out[1]["pe_ttm"] is None, "NaN pe_ttm 应为 None, 实际: %r" % out[1]["pe_ttm"]
    assert out[1]["vol_ratio"] is None, "NaN volume_ratio 应为 None, 实际: %r" % out[1]["vol_ratio"]
    assert out[1]["turnover_rate"] == 2.1, "非 NaN 字段仍应正常取值"
    # 无 key → 全 None (dict 与 Series 均安全, 无 Series bool 崩溃)
    assert out[2]["pe_ttm"] is None and out[2]["vol_ratio"] is None
    print("  ✓ basic_idx Series 合并: 不抛 ValueError, PE/市值/换手/量比取值与 NaN→None 均正确")


def test_fetch_announce_url_pure_code():
    """公告接口 stock_list 必须用纯代码 (带 1./0. 市场前缀会返回空) — 回归保护"""
    from unittest import mock

    captured = {}

    def _fake_urlopen(req, timeout=10):
        captured["url"] = req.full_url
        payload = json.dumps({"data": {"list": [{"title": "恒盛能源:关于股东减持计划的公告"}]}})
        class _Resp:
            def read(self):
                return payload.encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        return _Resp()

    with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        titles = rb._fetch_announce_titles("605580.SH", n=30, timeout=10)
    assert "stock_list=605580" in captured["url"], "URL 应使用纯代码 stock_list=605580, 实际: %s" % captured["url"]
    assert "stock_list=1.605580" not in captured["url"], "URL 不应带市场前缀, 实际: %s" % captured["url"]
    assert titles == ["恒盛能源:关于股东减持计划的公告"], "标题解析错误: %s" % titles
    # 非法代码 → 空
    assert rb._fetch_announce_titles("abc") == []
    assert rb._fetch_announce_titles("") == []
    print("  ✓ 公告接口 URL 纯代码格式: 沪/深前缀不注入, 解析与非法入参: OK")


if __name__ == "__main__":
    tests = [
        test_L1_drawdown_tier_gate,
        test_L2_ST_filter,
        test_L2_consecutive_loss_widen_reject,
        test_score_weights_sum,
        test_final_score_oversold_threshold,
        test_L2_valuation_industry_cap_buckets,
        test_L2_valuation_absolute_pe_reject,
        test_L2_valuation_pe_pct_and_val_dd,
        test_L2_valuation_theme_pump_reject,
        test_L2_fundamental_upgrade_constraints,
        test_predict_rebound_pe_high_downgrade,
        test_predict_rebound_confidence_tiers,
        test_predict_rebound,
        test_market_crash_protection,
        test_select_tier_auto_and_pause,
        test_is_slow_drift_fast_vs_slow,
        test_L2_risk_map_reject,
        test_fetch_announce_url_pure_code,
        test_L3_indicators_array_usage,
        test_merge_basic_fields_series,
    ]
    passed = 0
    failed = 0
    for fn in tests:
        print("\n[ RUN ]", fn.__name__)
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print("  ✗ FAIL:", e)
        except Exception as e:
            failed += 1
            import traceback
            print("  ✗ ERROR:", type(e).__name__, str(e))
            traceback.print_exc()
    print("\n" + "=" * 50)
    print("Passed: %d / %d" % (passed, passed + failed))
    if failed:
        sys.exit(1)
    print("✅ 全部通过")
