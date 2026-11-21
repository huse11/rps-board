# -*- coding: utf-8 -*-
"""
单元测试：日度推荐「预测化」模块 v1.0 (predict.py)
覆盖: 六维度概率 (动量/技术/资金/板块/情绪/基本面) + 综合预测值 + 置信度 + 止损位
     + 市场环境档位 + 每日校准 (预测日志/准确率复盘)
运行: python -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import predict

import pandas as pd

# 单元测试隔离: 禁用概率校准(防止本地 prediction_review.json 影响既有断言;
# 校准逻辑在 TestCalibration 中通过桩单独验证)
predict._load_calib = lambda: None


def base_features(**kw):
    """构造默认特征 dict (单板块共振, 中性数据)"""
    f = {
        "rps50": 85, "sector_count": 1, "continuous": 2,
        "chg3": 5.0, "chg5": 8.0, "consec_up": 3,
        "vol_ratio": 1.5, "turnover": 8.0, "net_inflow_3d": 1e7,
        "pct_chg": 2.0, "price": 10.0, "pe_ttm": 25.0,
        "tech_hits": ["20日新高", "均线多头", "MACD多头", "量价齐升"],
        "hit_count": 4, "lows": [9.2, 9.0, 9.4], "advance_ratio": 0.55,
    }
    f.update(kw)
    return f


def market_ok(adv=0.55, above=2, total=2):
    return {
        "is_safe": True,
        "advance_ratio": adv,
        "indexes": [{"code": c, "name": n, "above": True}
                    for c, n in [("000001.SH", "上证指数"), ("399006.SZ", "创业板指")][:above]],
    }


class TestClamp(unittest.TestCase):
    def test_bounds(self):
        self.assertEqual(predict._clamp(10), 30)
        self.assertEqual(predict._clamp(100), 88)
        self.assertEqual(predict._clamp(60), 60)


class TestMarketRegime(unittest.TestCase):
    def test_strong(self):
        ms = {"advance_ratio": 0.68, "indexes": [{"above": True}, {"above": True}]}
        r = predict.market_regime(ms)
        self.assertEqual(r["regime"], "偏强")
        self.assertEqual(r["prob_adjust"], predict.DELTA["regime_strong"])

    def test_weak(self):
        ms = {"advance_ratio": 0.30, "indexes": [{"above": False}]}
        r = predict.market_regime(ms)
        self.assertEqual(r["regime"], "偏弱")
        self.assertEqual(r["prob_adjust"], predict.DELTA["regime_weak"])

    def test_oscillating(self):
        ms = {"advance_ratio": 0.50, "indexes": [{"above": True}]}
        self.assertEqual(predict.market_regime(ms)["regime"], "震荡市")

    def test_none(self):
        self.assertEqual(predict.market_regime(None)["regime"], "震荡市")


class TestDimensionProb(unittest.TestCase):
    def test_momentum_rps90(self):
        p, notes = predict._momentum_prob(base_features(rps50=95))
        self.assertGreaterEqual(p, 60)
        self.assertTrue(any("RPS50≥90" in x for x in notes))

    def test_momentum_overheat(self):
        """近3日涨幅>12% 过热 → 概率低于温和上行"""
        hot = predict._momentum_prob(base_features(chg3=15.0))[0]
        mild = predict._momentum_prob(base_features(chg3=5.0))[0]
        self.assertLess(hot, mild)

    def test_technical_combo_boost(self):
        """高置信组合(20日新高+量比>1.2+均线多头) 应有额外加分"""
        f = base_features(hit_count=5, vol_ratio=1.5,
                          tech_hits=["20日新高", "均线多头", "MACD多头", "量价齐升", "RPS50强势"])
        p, _ = predict._technical_prob(f)
        self.assertGreaterEqual(p, predict.PROB_BASE + predict.DELTA["tech_5"] + predict.DELTA["tech_combo"])

    def test_capital_ambush(self):
        """资金埋伏: 净流入>0 且 近3日涨幅<8% → 加分"""
        p, notes = predict._capital_prob(base_features(chg3=3.0, net_inflow_3d=2e7))
        self.assertTrue(any("埋伏" in x for x in notes))
        self.assertGreaterEqual(p, predict.PROB_BASE + predict.DELTA["cap_inflow"] + predict.DELTA["cap_ambush"])

    def test_capital_dump(self):
        """单日暴涨>9% 且主力未流入 → 出货预警, 概率低于基准值"""
        p, notes = predict._capital_prob(base_features(pct_chg=10.0, net_inflow_3d=None))
        self.assertTrue(any("出货" in x for x in notes))
        self.assertLess(p, predict.PROB_BASE)

    def test_sector_three(self):
        """三档共振 → 板块前三≈70% 基准"""
        p, _ = predict._sector_prob(base_features(sector_count=3))
        self.assertGreaterEqual(p, predict.PROB_BASE + predict.DELTA["sector_count_3"])

    def test_sentiment_strong(self):
        p, _ = predict._sentiment_prob(base_features(advance_ratio=0.65), {})
        self.assertGreaterEqual(p, predict.PROB_BASE + predict.DELTA["sent_strong"])

    def test_fundamental_pe(self):
        low = predict._fundamental_prob(base_features(pe_ttm=15.0))[0]
        high = predict._fundamental_prob(base_features(pe_ttm=300.0))[0]
        self.assertGreater(low, high)


class TestBuildPrediction(unittest.TestCase):
    def test_composite_range(self):
        """综合概率必须在 [30,88] 区间内"""
        p = predict.build_prediction(base_features(), market_ok())
        self.assertTrue(30 <= p["up_prob"] <= 88)
        self.assertEqual(p["horizon"], 1)  # 次日口径 (2026-08-26 由 3 改 1)

    def test_confidence_high(self):
        """高置信: 概率≥68 + 双档共振 + 技术≥4 + 主力流入"""
        f = base_features(sector_count=3, rps50=95, hit_count=5, net_inflow_3d=3e7,
                          tech_hits=["20日新高", "均线多头", "MACD多头", "量价齐升", "RPS50强势"],
                          consec_up=6, continuous=6)
        p = predict.build_prediction(f, market_ok(adv=0.62))
        self.assertEqual(p["confidence"], "高")
        self.assertGreaterEqual(p["up_prob"], 68)

    def test_confidence_weak_market_downgrade(self):
        """偏弱市场: 置信度高 → 降为中"""
        f = base_features(sector_count=3, rps50=95, hit_count=5, net_inflow_3d=3e7,
                          tech_hits=["20日新高", "均线多头", "MACD多头", "量价齐升", "RPS50强势"],
                          consec_up=6, continuous=6)
        p = predict.build_prediction(f, market_ok(adv=0.30))
        self.assertEqual(p["regime"], "偏弱")
        self.assertNotEqual(p["confidence"], "高")

    def test_stop_loss_from_lows(self):
        """止损位: 由 20 日低点支撑计算, 限制在 [-9,-3]"""
        p = predict.build_prediction(base_features(price=10.0, lows=[9.0, 9.1, 9.2]), market_ok())
        # 低点 -10% 超出止损上限, 应被夹取到 -9.0
        self.assertEqual(p["stop_loss"], -9.0)
        self.assertTrue(-9.0 <= p["stop_loss"] <= -3.0)

    def test_gain_range(self):
        """预期涨幅区间: 按中值分档缩放, 高预期给更宽区间
        gain≥3→[g-3,g+3.5]; 1.5-3→[max(0,g-2),g+3.5]; 0-1.5→[0,g+3.5]; 负→[g-2,g+1.5]
        """
        p = predict.build_prediction(base_features(), market_ok())
        g = p["expected_gain"]
        if g >= 3.0:
            expected = [round(g - 3.0, 1), round(g + 3.5, 1)]
        elif g >= 1.5:
            expected = [round(max(0.0, g - 2.0), 1), round(g + 3.5, 1)]
        elif g > 0:
            expected = [round(0.0, 1), round(g + 3.5, 1)]
        else:
            expected = [round(g - 2.0, 1), round(g + 1.5, 1)]
        self.assertEqual(p["gain_range"], expected)

    def test_gain_range_positive_floor(self):
        """正预期下限: gain≥3→[g-3,g+3.5]; 1.5-3→[max(0,g-2),g+3.5]; 0-1.5→[0,g+3.5]"""
        self.assertEqual(predict._gain_range(5.0), [2.0, 8.5])
        self.assertEqual(predict._gain_range(2.0), [0.0, 5.5])
        self.assertEqual(predict._gain_range(0.5), [0.0, 4.0])

    def test_gain_range_negative(self):
        """负预期区间偏下: gain=-1.5 → [-3.5, 0.0]"""
        self.assertEqual(predict._gain_range(-1.5), [-3.5, 0.0])

    def test_expected_gain_thresholds(self):
        """7 档分档: 极高6.0/高5.0/中高4.0/中3.0/中低2.0/低1.0/极低-1.0 (无加成)"""
        self.assertEqual(predict._expected_gain(80), 6.0)
        self.assertEqual(predict._expected_gain(75), 5.0)
        self.assertEqual(predict._expected_gain(70), 4.0)
        self.assertEqual(predict._expected_gain(66), 3.0)
        self.assertEqual(predict._expected_gain(63), 2.0)
        self.assertEqual(predict._expected_gain(60), 1.0)
        self.assertEqual(predict._expected_gain(40), -1.0)

    def test_expected_gain_confidence_boost(self):
        """置信度加成: 高+1.0 / 中+0 / 低-0.5"""
        # 高置信 + 70%概率 → 4.0 + 1.0 = 5.0
        self.assertEqual(predict._expected_gain(70, confidence="高"), 5.0)
        # 低置信 + 68%概率 → 4.0 + (-0.5) = 3.5
        self.assertEqual(predict._expected_gain(68, confidence="低"), 3.5)
        # 中置信 + 66%概率 → 3.0 + 0 = 3.0
        self.assertEqual(predict._expected_gain(66), 3.0)

    def test_expected_gain_sector_boost(self):
        """板块共振加成: sector_count=2→+0.3, 3→+0.6, 4→上限+0.9"""
        # 高置信 + sector_count=2 + 72% → 5.0 + 1.0 + 0.3 = 6.3
        self.assertEqual(predict._expected_gain(72, confidence="高", sector_count=2), 6.3)
        # 高置信 + sector_count=4 + 72% → 5.0 + 1.0 + min(0.9, 0.9) = 6.9
        self.assertEqual(predict._expected_gain(72, confidence="高", sector_count=4), 6.9)

    def test_logic_present(self):
        """预测逻辑: 2~3 条核心支撑理由, 且含板块共振依据"""
        p = predict.build_prediction(base_features(sector_count=2), market_ok())
        self.assertTrue(1 <= len(p["logic"]) <= 3)
        self.assertTrue(any("共振" in x or "强势" in x for x in p["logic"]))

    def test_factors_six_dimensions(self):
        p = predict.build_prediction(base_features(), market_ok())
        self.assertEqual(set(p["factors"].keys()),
                         {"momentum", "technical", "capital", "sector", "sentiment", "fundamental"})
        for v in p["factors"].values():
            self.assertTrue(0 <= v <= 100)


class TestRecordPredictions(unittest.TestCase):
    def test_record_and_trim(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "log.json"
            recs = [
                {"ts_code": "000001.SZ", "name": "平安银行", "price": 10.5,
                 "prediction": {"up_prob": 66, "expected_gain": 6.0, "horizon": 3, "confidence": "中"},
                 "sector_count": 2, "technical": {"hit_count": 4}, "capital": {"net_inflow_3d": 1e7}},
                {"ts_code": "000002.SZ", "name": "万科A", "price": 8.0, "prediction": None},
            ]
            n = predict.record_predictions(recs, "20260807", log)
            self.assertEqual(n, 1)
            data = json.loads(log.read_text(encoding="utf-8"))
            self.assertIn("20260807", data)
            self.assertEqual(len(data["20260807"]), 1)
            self.assertEqual(data["20260807"][0]["code"], "000001.SZ")
            # H5: 未传 tag → 记录 tag 为 None (默认值兼容)
            self.assertIsNone(data["20260807"][0]["tag"])

    def test_tag_field(self):
        """H5: record_predictions 支持 tag 参数, 写入每条预测记录 (超跌/动量分维度复盘)"""
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "log.json"
            recs = [
                {"ts_code": "300123.SZ", "name": "安培龙", "price": 15.0,
                 "prediction": {"up_prob": 60, "expected_gain": 25.0, "confidence": "中"},
                 "sector_count": 1, "technical": {"hit_count": 3}, "capital": {}},
            ]
            n = predict.record_predictions(recs, "20260828", log, tag="REBOUND")
            self.assertEqual(n, 1)
            data = json.loads(log.read_text(encoding="utf-8"))
            self.assertEqual(data["20260828"][0]["tag"], "REBOUND")

    def test_trim_to_30_dates(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "log.json"
            # 先写入 40 个历史日期, 再记录最新 → 应裁剪为 30
            old = {("2026%02d01" % (i % 12 + 1)): [] for i in range(40)}
            log.write_text(json.dumps(old), encoding="utf-8")
            predict.record_predictions([], "20260807", log)
            data = json.loads(log.read_text(encoding="utf-8"))
            self.assertLessEqual(len(data), 30)
            self.assertIn("20260807", data)


class TestMomentumChasePenalty(unittest.TestCase):
    """预测修正: 追高抑制 (近5日暴涨 / 乖离MA20过大 / 当天大涨未涨停)"""

    def test_chg5_hot_penalty(self):
        """近5日涨幅>12% → 动量维度概率降低 (追高惩罚)"""
        hot = predict._momentum_prob(base_features(chg5=15.0, chg3=5.0))[0]
        ok = predict._momentum_prob(base_features(chg5=8.0, chg3=5.0))[0]
        self.assertLess(hot, ok)

    def test_ma20_dev_penalty(self):
        """乖离MA20>25% → 动量维度扣分 + 风险标签"""
        dev = predict._momentum_prob(base_features(ma20_dev=30.0))[0]
        mild = predict._momentum_prob(base_features(ma20_dev=10.0))[0]
        self.assertLess(dev, mild)
        _, notes = predict._momentum_prob(base_features(ma20_dev=30.0))
        self.assertTrue(any("偏离MA20过大" in x for x in notes))

    def test_high_pos_penalty(self):
        """当天涨7%~9.8%未涨停 → 高位追涨扣分 + 标签"""
        high = predict._momentum_prob(base_features(pct_chg=8.0))[0]
        mild = predict._momentum_prob(base_features(pct_chg=3.0))[0]
        self.assertLess(high, mild)
        _, notes = predict._momentum_prob(base_features(pct_chg=8.0))
        self.assertTrue(any("高位追涨" in x for x in notes))

    def test_high_position_no_high_confidence(self):
        """chg5>12 或 ma20_dev>25 → 置信度不为"高" (位置约束, 即使概率达档)"""
        base = dict(sector_count=3, rps50=95, hit_count=5, net_inflow_3d=3e7,
                    tech_hits=["20日新高", "均线多头", "MACD多头", "量价齐升", "RPS50强势"],
                    consec_up=6, continuous=6)
        p1 = predict.build_prediction(base_features(chg5=15.0, **base), market_ok(adv=0.62))
        self.assertNotEqual(p1["confidence"], "高")
        p2 = predict.build_prediction(base_features(ma20_dev=30.0, **base), market_ok(adv=0.62))
        self.assertNotEqual(p2["confidence"], "高")
        # 对照: 无位置风险 → 高
        p3 = predict.build_prediction(base_features(chg5=8.0, ma20_dev=10.0, **base),
                                      market_ok(adv=0.62))
        self.assertEqual(p3["confidence"], "高")


class TestCalibration(unittest.TestCase):
    """概率校准: 用实测命中率压缩虚高概率 (防过度乐观, 校准逻辑单独桩验证)"""

    def _calib(self, hit, avg, samples=10):
        return {"hit": hit, "avg_prob": avg, "samples": samples}

    def _patch(self, calib):
        orig = predict._load_calib
        predict._load_calib = lambda: calib
        return orig

    def test_no_calib_without_data(self):
        orig = self._patch(None)
        try:
            self.assertEqual(predict.calibrate_prob(80), 80)
        finally:
            predict._load_calib = orig

    def test_small_gap_no_calibration(self):
        """误差 ≤3pp → 不校准 (避免过度修正)
        hit=0.60 (60%), avg=62 (62%), gap=2pp ≤3 → 不校准"""
        orig = self._patch(self._calib(hit=0.60, avg=62))
        try:
            self.assertEqual(predict.calibrate_prob(80), 80)
        finally:
            predict._load_calib = orig

    def test_big_gap_compresses(self):
        """hit=0.50 (50%), avg=82 (82%), gap=32pp; anchor=max(45,min(65,60))=60, k=max(0.7,1-32/80)=0.7; 80→60+20*0.7=74"""
        orig = self._patch(self._calib(hit=0.50, avg=82))
        try:
            self.assertEqual(predict.calibrate_prob(80), 74)
        finally:
            predict._load_calib = orig

    def test_compression_ratio(self):
        """保留排序: 高概率压缩后仍高于低概率, 且保留大部分原始差异"""
        orig = self._patch(self._calib(hit=0.50, avg=82))
        try:
            hi = predict.calibrate_prob(88)
            lo = predict.calibrate_prob(60)
            self.assertGreater(hi, lo)
            self.assertLessEqual(hi, 88)
            # 保留至少 70% 原始差异: 88-60=28 → 校准后应≥20
            self.assertGreaterEqual(hi - lo, 20)
        finally:
            predict._load_calib = orig

    def test_never_below_floor(self):
        """校准后概率不跌破 PROB_MIN=30"""
        orig = self._patch(self._calib(hit=0.30, avg=90))
        try:
            self.assertGreaterEqual(predict.calibrate_prob(30), predict.PROB_MIN)
        finally:
            predict._load_calib = orig

    def test_preserves_differentiation(self):
        """关键: 校准后仍保留股票间差异化 (保留≥70%原始差异)
        生产数据: hit=0.40 (40%), avg=66 (66%), gap=26pp
        anchor=max(45,min(65,50))=50, k=max(0.7,1-26/80)=0.7
        raw=80 → 50+30*0.7=71
        raw=60 → 50+10*0.7=57
        raw=55 → 50+5*0.7=53.5 → 54"""
        orig = self._patch(self._calib(hit=0.40, avg=66))
        try:
            hi = predict.calibrate_prob(80)
            mid = predict.calibrate_prob(60)
            lo = predict.calibrate_prob(55)
            self.assertGreater(hi, mid, "高概率应保持高于中概率")
            self.assertGreater(mid, lo, "中概率应保持高于低概率")
            # 保留至少 70% 原始差异: 80-55=25 → 校准后应≥17
            self.assertGreaterEqual(hi - lo, 17)
        finally:
            predict._load_calib = orig


class TestCalcAccuracy(unittest.TestCase):
    def _make_daily_map(self, latest_date="20260807"):
        """mock daily_map: 5 个交易日 × 2 只股票, 价格逐步上行"""
        base = datetime.strptime(latest_date, "%Y%m%d")
        dates = sorted([(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(4)])
        dm = {}
        for j, date in enumerate(dates):
            dm[date] = pd.DataFrame([
                {"ts_code": "000001.SZ", "close": 10.0 + j, "pct_chg": 1.0},
                {"ts_code": "000002.SZ", "close": 9.0 + j * 0.5, "pct_chg": 1.0},
            ])
        return dm, dates

    def test_accuracy_hit(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "log.json"
            review = Path(td) / "review.json"
            dm, dates = self._make_daily_map()
            # 预测日在 dates[0], price=10 → dates[3] 收盘 13 → 命中
            log.write_text(json.dumps({
                dates[0]: [{"code": "000001.SZ", "price": 10.0, "up_prob": 70,
                            "confidence": "高", "sector_count": 2}]
            }), encoding="utf-8")
            rev = predict.calc_prediction_accuracy(dm, dates[-1], log, review)
            self.assertIsNotNone(rev)
            self.assertEqual(rev["summary"]["samples"], 1)
            self.assertEqual(rev["summary"]["hit_rate"], 1.0)
            self.assertGreater(rev["summary"]["avg_gain"], 0)
            # by_confidence 分组正确
            self.assertEqual(rev["by_confidence"]["高"]["samples"], 1)
            # all = 完整已验证列表, recent = 截断最近15条 (周度复盘依赖 all, 避免截断)
            self.assertEqual(len(rev["all"]), 1)
            self.assertEqual(rev["all"][0]["code"], "000001.SZ")
            self.assertEqual(rev["all"][0]["hit"], True)
            self.assertEqual(rev["recent"], rev["all"])

    def test_accuracy_miss_and_skip_insufficient(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "log.json"
            review = Path(td) / "review.json"
            dm, dates = self._make_daily_map()
            # 一只下跌(未命中), 一只数据不足(跳过)
            log.write_text(json.dumps({
                dates[0]: [
                    {"code": "000002.SZ", "price": 12.0, "up_prob": 55, "confidence": "低", "sector_count": 1},
                ],
                dates[3]: [{"code": "000001.SZ", "price": 10.0, "up_prob": 60, "confidence": "中", "sector_count": 1}],
            }), encoding="utf-8")
            rev = predict.calc_prediction_accuracy(dm, dates[-1], log, review)
            self.assertIsNotNone(rev)
            self.assertEqual(rev["summary"]["samples"], 1)  # dates[3] 的预测数据不足被跳过
            self.assertEqual(rev["summary"]["hit_rate"], 0.0)
            self.assertLess(rev["summary"]["avg_gain"], 0)

    def test_empty_log(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "log.json"
            review = Path(td) / "review.json"
            dm, dates = self._make_daily_map()
            log.write_text("{}", encoding="utf-8")
            self.assertIsNone(predict.calc_prediction_accuracy(dm, dates[-1], log, review))

    def test_horizon_gain_3d_5d(self):
        # V2.0 (需求 3.9): 持有3日/5日周期收益, 数据充足时全部计算
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "log.json"
            review = Path(td) / "review.json"
            # 6 个交易日, 价格逐步上行
            base = datetime.strptime("20260813", "%Y%m%d")
            dates = sorted([(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(6)])
            dm = {}
            for j, date in enumerate(dates):
                dm[date] = pd.DataFrame([
                    {"ts_code": "000001.SZ", "close": 10.0 + j, "pct_chg": 1.0},
                ])
            log.write_text(json.dumps({
                dates[0]: [{"code": "000001.SZ", "price": 10.0, "up_prob": 70,
                            "confidence": "高", "sector_count": 2}]
            }), encoding="utf-8")
            rev = predict.calc_prediction_accuracy(dm, dates[-1], log, review)
            self.assertIsNotNone(rev)
            item = rev["all"][0]
            # 次日 = dates[1] 收盘 11 → +10%; 3日 = dates[3] 收盘 13 → +30%; 5日 = dates[5] 收盘 15 → +50%
            self.assertAlmostEqual(item["gain"], 10.0)
            self.assertAlmostEqual(item["gain3"], 30.0)
            self.assertAlmostEqual(item["gain5"], 50.0)
            self.assertAlmostEqual(rev["summary"]["avg_gain3"], 30.0)
            self.assertAlmostEqual(rev["summary"]["avg_gain5"], 50.0)
            self.assertEqual(rev["summary"]["horizon3_n"], 1)
            self.assertEqual(rev["summary"]["horizon5_n"], 1)

    def test_horizon_gain_insufficient_none(self):
        # V2.0 (需求 3.9): 数据不足时 gain3/gain5 为 None, 且不污染均值
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "log.json"
            review = Path(td) / "review.json"
            dm, dates = self._make_daily_map()  # 仅 4 个交易日
            # dates[2] 的预测: 次日(idx+1=3)可验证, 3日/5日(idx+5)数据不足
            log.write_text(json.dumps({
                dates[2]: [{"code": "000001.SZ", "price": 10.0, "up_prob": 60,
                            "confidence": "中", "sector_count": 1}],
            }), encoding="utf-8")
            rev = predict.calc_prediction_accuracy(dm, dates[-1], log, review)
            self.assertIsNotNone(rev)
            self.assertEqual(rev["summary"]["samples"], 1)
            item = rev["all"][0]
            self.assertIsNotNone(item["gain"])
            self.assertIsNone(item["gain3"])
            self.assertIsNone(item["gain5"])
            self.assertIsNone(rev["summary"]["avg_gain3"])
            self.assertIsNone(rev["summary"]["avg_gain5"])
            self.assertEqual(rev["summary"]["horizon3_n"], 0)
            self.assertEqual(rev["summary"]["horizon5_n"], 0)


class TestCloseIndex(unittest.TestCase):
    """N+1 修复回归: 按日期预构建 {code: close} 索引, O(1) 查询替代全表布尔过滤"""

    def test_build_close_index_skips_nan(self):
        df = pd.DataFrame([
            {"ts_code": "000001.SZ", "close": 10.5},
            {"ts_code": "000002.SZ", "close": None},
            {"ts_code": "000003.SZ", "close": 8.0},
        ])
        idx = predict._build_close_index(df)
        self.assertEqual(idx, {"000001.SZ": 10.5, "000003.SZ": 8.0})
        self.assertNotIn("000002.SZ", idx)

    def test_lookup_with_cache_reuses_index(self):
        dm = {"20260801": pd.DataFrame([{"ts_code": "000001.SZ", "close": 10.0}])}
        cache = {}
        v1 = predict._lookup_close(dm, "20260801", "000001.SZ", cache)
        v2 = predict._lookup_close(dm, "20260801", "000001.SZ", cache)
        self.assertEqual((v1, v2), (10.0, 10.0))
        self.assertIn("20260801", cache)
        self.assertIsNone(predict._lookup_close(dm, "20260802", "000001.SZ", cache))
        self.assertIsNone(predict._lookup_close(dm, "20260801", "000099.SZ", cache))


if __name__ == "__main__":
    unittest.main()
