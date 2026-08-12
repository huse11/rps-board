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
        self.assertEqual(p["horizon"], 3)

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
        """预期涨幅区间 = 中值 ± (中值*0.45)"""
        p = predict.build_prediction(base_features(), market_ok())
        g = p["expected_gain"]
        self.assertEqual(p["gain_range"], [round(g * 0.55, 1), round(g * 1.45, 1)])

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


if __name__ == "__main__":
    unittest.main()
