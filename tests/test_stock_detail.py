# -*- coding: utf-8 -*-
"""
单元测试：个股详情数据模块 v1.0 (stock_detail.py)
覆盖: 基础工具(代码规范/单位格式化) / BOLL / RSI / MACD / KDJ /
      主力捉妖(加仓/卖信号) / 擒龙启爆(色块/启爆器/火焰/起爆点) / B/S标记 /
      评分 / 风险亮点 / 回测 / 印象 / 探宝 / 资金跟踪降级 / 估值
运行: python -m pytest tests/test_stock_detail.py -q
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stock_detail as sd


class TestTools(unittest.TestCase):
    def test_to_ts_code(self):
        self.assertEqual(sd.to_ts_code("601020"), "601020.SH")
        self.assertEqual(sd.to_ts_code("000001"), "000001.SZ")
        self.assertEqual(sd.to_ts_code("601020.SH"), "601020.SH")
        self.assertEqual(sd.to_ts_code("1.601020"), "601020.SH")

    def test_fmt_wan(self):
        self.assertEqual(sd._fmt_wan(4.72e8), "4.72亿")
        self.assertEqual(sd._fmt_wan(-1136e4), "-1136.0万")
        self.assertIn("万", sd._fmt_wan(18607e3))

    def test_num(self):
        self.assertEqual(sd._num("1,234.5"), 1234.5)
        self.assertIsNone(sd._num("-"))
        self.assertIsNone(sd._num(None))
        self.assertAlmostEqual(sd._num("3.33"), 3.33)


class TestBoll(unittest.TestCase):
    def test_boll_short_data(self):
        self.assertEqual(sd.calc_boll([1, 2, 3]), [])

    def test_boll_symmetry(self):
        closes = [10 + (i % 7) * 0.5 for i in range(40)]
        b = sd.calc_boll(closes, 20)
        last = b[-1]
        self.assertIsNotNone(last["mid"])
        self.assertAlmostEqual((last["upper"] - last["mid"]), (last["mid"] - last["lower"]), places=6)
        self.assertGreater(last["upper"], last["mid"])


class TestRsi(unittest.TestCase):
    def test_rsi_short_data(self):
        self.assertEqual(sd.calc_rsi([1, 2, 3]), [])

    def test_rsi_all_up_is_100(self):
        closes = [float(i) for i in range(1, 30)]
        r = sd.calc_rsi(closes, 6)
        self.assertAlmostEqual(r[-1], 100.0, places=4)


class TestMacd(unittest.TestCase):
    def test_macd_length(self):
        closes = [10 + (i % 5) * 0.3 for i in range(60)]
        m = sd.calc_macd(closes)
        self.assertEqual(len(m), 60)
        last = m[-1]
        self.assertIn("dif", last)
        self.assertIn("dea", last)
        self.assertIn("hist", last)


class TestKdj(unittest.TestCase):
    def test_kdj_bounds(self):
        highs = [10 + (i % 9) for i in range(40)]
        lows = [9 + (i % 9) * 0.5 for i in range(40)]
        closes = [(h + l) / 2 for h, l in zip(highs, lows)]
        kdj = sd.calc_kdj(highs, lows, closes)
        self.assertEqual(len(kdj), 40)
        for row in kdj[-5:]:
            self.assertGreaterEqual(row["k"], 0)
            self.assertLessEqual(row["k"], 100)


class TestZhuliZhuoyao(unittest.TestCase):
    def test_continuous_inflow_add_signal(self):
        rows = [
            {"date": f"2026080{i}", "main": 1000 + i * 100, "small": -100 - i,
             "mid": 300, "big": 500 + i * 10, "super": 200}
            for i in range(1, 8)
        ]
        out = sd.calc_zhuli_zhuoyao(rows)
        # 游资(大单+中单)连续净流入 + 主力净流入 → 触发加仓
        self.assertTrue(out[-1]["addPosition"])
        self.assertFalse(out[-1]["sellSignal"])
        self.assertGreater(out[-1]["youzi"], 0)

    def test_continuous_outflow_sell_signal(self):
        rows = [
            {"date": f"2026080{i}", "main": -1000 - i * 50, "small": 200 + i,
             "mid": -300, "big": -500 - i * 10, "super": -200}
            for i in range(1, 8)
        ]
        out = sd.calc_zhuli_zhuoyao(rows)
        self.assertFalse(out[-1]["addPosition"])
        self.assertIn("sanhu", out[-1])
        self.assertLess(out[-1]["youzi"], 0)

    def test_empty(self):
        self.assertEqual(sd.calc_zhuli_zhuoyao([]), [])


class TestQinlongQibao(unittest.TestCase):
    @staticmethod
    def _bars(n=40):
        closes = [10 + (i * 0.1) for i in range(n)]  # 单边上涨
        highs = [c + 0.2 for c in closes]
        lows = [c - 0.2 for c in closes]
        vols = [1000 + (i * 13) % 500 for i in range(n)]
        return closes, highs, lows, vols

    def test_uptrend_red_color(self):
        closes, highs, lows, vols = self._bars()
        qb = sd.calc_qinlong_qibao(closes, highs, lows, vols)
        self.assertEqual(len(qb), len(closes))
        # 单边上涨后期: 价格站上MA20且MA20上行 → 红色色块(1)
        self.assertEqual(qb[-1]["color"], 1)

    def test_booster_and_break_keys(self):
        closes, highs, lows, vols = self._bars()
        qb = sd.calc_qinlong_qibao(closes, highs, lows, vols)
        for row in qb:
            self.assertIn("booster", row)
            self.assertIn("flame", row)
            self.assertIn("break", row)
            self.assertIn("color", row)

    def test_empty(self):
        self.assertEqual(sd.calc_qinlong_qibao([], [], [], []), [])


class TestBsMarks(unittest.TestCase):
    def test_cross_generates_marks(self):
        # 构造先跌后涨序列, 产生均线金叉/死叉
        closes = [10 - i * 0.1 for i in range(15)] + [9 - (i - 15) * -0.15 for i in range(15, 30)]
        bars = [{"time": f"t{i}", "close": closes[i], "volume": 1000} for i in range(len(closes))]
        marks = sd.calc_bs_marks(bars)
        types = {m["type"] for m in marks}
        self.assertTrue(types.issubset({"B", "S"}))
        for m in marks:
            self.assertIn("index", m)
            self.assertIn("time", m)


class TestScore(unittest.TestCase):
    def test_score_basic(self):
        closes = [10 + i * 0.2 for i in range(30)]
        kline = {"bars": [{"time": f"t{i}", "open": 10, "high": c + 0.5, "low": c - 0.5,
                           "close": c, "volume": 1000} for i, c in enumerate(closes)]}
        rt = {"name": "X", "price": 15.6, "changePercent": 2.5, "volumeRatio": 1.5,
              "turnover": 5.0, "totalAmount": 5e8, "pe_ttm": 25.0}
        ff = [{"date": "20260801", "main": 2e7, "small": -1e6, "mid": 1e6, "big": 1e7, "super": 1e7}]
        sc = sd.fetch_score("601020", rt, kline, ff)
        self.assertIn("totalScore", sc)
        self.assertIn("aiText", sc)
        self.assertIn("beatStock", sc)
        self.assertGreaterEqual(sc["totalScore"], 0)
        self.assertLessEqual(sc["totalScore"], 100)
        self.assertTrue(sc["factors"])


class TestRiskHighlight(unittest.TestCase):
    def test_high_pe_risk(self):
        rt = {"name": "X", "pe_ttm": 500.0, "changePercent": 1.0}
        kline = {"bars": [{"time": "t", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100}]}
        rh = sd.fetch_risk_highlight("601020", rt, kline)
        self.assertTrue(any("市盈率偏高" in t for t in rh["riskTags"]))

    def test_normal_no_risk(self):
        rt = {"name": "X", "pe_ttm": 20.0, "changePercent": 1.0}
        kline = {"bars": [{"time": "t", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100}]}
        rh = sd.fetch_risk_highlight("601020", rt, kline)
        self.assertIsInstance(rh["riskTags"], list)


class TestBacktest(unittest.TestCase):
    def test_backtest_short_data(self):
        self.assertEqual(sd.fetch_backtest("601020", {"bars": [{"close": 1}]})["days"], 0)

    def test_backtest_all_up(self):
        bars = [{"time": f"t{i}", "open": i, "high": i + 1, "low": i - 1,
                 "close": float(i + 1), "volume": 1000} for i in range(60)]
        bt = sd.fetch_backtest("601020", {"bars": bars})
        self.assertEqual(bt["riseRate"], "100.00%")
        self.assertGreater(bt["days"], 0)


class TestImpression(unittest.TestCase):
    def test_impression_stage(self):
        bars = [{"time": f"t{i}", "open": i, "high": i + 1, "low": i - 1,
                 "close": float(i + 1), "volume": 1000} for i in range(70)]
        imp = sd.fetch_impression("601020", {"name": "X"}, {"bars": bars})
        self.assertNotEqual(imp["stageRise"]["stock"], "--")


class TestTechbao(unittest.TestCase):
    def test_techbao_chip(self):
        bars = [{"time": f"t{i}", "open": 10, "high": 12, "low": 8,
                 "close": 10 + i * 0.1, "volume": 1000} for i in range(30)]
        tb = sd.fetch_techbao("601020", {"name": "X", "pe_ttm": 30.0}, {"bars": bars})
        self.assertIsNotNone(tb["chip"]["avgCost"])
        self.assertIn("klineForm", tb)


class TestFundtrackDegrade(unittest.TestCase):
    def test_market_from_fflow(self):
        rt = {"name": "X", "totalAmount": 1e9}
        ff = [{"date": "20260814", "main": -2e7, "small": -1.8e6, "mid": -5e6, "big": -1.5e7, "super": -5e6}]
        ft = sd.fetch_fundtrack("601020", rt, ff)
        self.assertEqual(ft["market"]["mainNet"], -2e7)
        self.assertIn("万", ft["market"]["mainNetTxt"])


class TestValue(unittest.TestCase):
    def test_value_history(self):
        bars = [{"time": f"t{i}", "open": 10, "high": 11, "low": 9,
                 "close": 10 + i * 0.05, "volume": 1000, "amount": 5e7} for i in range(70)]
        va = sd.fetch_value("601020", {"name": "X", "pe_ttm": 30.0}, {"bars": bars})
        self.assertGreater(len(va["peHistory"]), 0)
        self.assertIn("pePercent", va)


class TestTxFallback(unittest.TestCase):
    """腾讯兜底: 东财 push2his 不可达时, K线/分时仍能拿到数据"""

    def test_tx_fetch_kline_parse(self):
        fake = {"data": {"sh601020": {"qfqday": [
            ["2026-08-12", "22.800", "22.800", "23.340", "22.470", "345703"],
            ["2026-08-14", "21.820", "21.940", "22.230", "21.500", "215766"],
        ]}}}
        with mock.patch.object(sd, "_http_get_json", return_value=fake):
            bars = sd._tx_fetch_kline("601020.SH", "day", 250)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[-1]["time"], "2026-08-14")
        self.assertAlmostEqual(bars[-1]["close"], 21.94)
        self.assertAlmostEqual(bars[-1]["open"], 21.82)
        # 腾讯列序为 开/收/高/低; volume 手 → 股
        self.assertEqual(bars[-1]["volume"], 215766 * 100)

    def test_tx_fetch_kline_bj_unfq_key(self):
        # 北交所 qfq 参数被忽略 → 返回 "day" 键 (非 "qfqday")
        fake = {"data": {"bj920634": {"day": [
            ["2026-08-14", "17.09", "16.92", "17.37", "16.73", "7552"],
        ]}}}
        with mock.patch.object(sd, "_http_get_json", return_value=fake):
            bars = sd._tx_fetch_kline("920634.BJ", "day", 250)
        self.assertEqual(len(bars), 1)
        self.assertAlmostEqual(bars[0]["close"], 16.92)

    def test_tx_fetch_kline_week_period(self):
        fake = {"data": {"sh601020": {"qfqweek": [
            ["2026-08-14", "21.500", "21.940", "24.020", "21.300", "1997456"],
        ]}}}
        with mock.patch.object(sd, "_http_get_json", return_value=fake):
            bars = sd._tx_fetch_kline("601020.SH", "week", 120)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["time"], "2026-08-14")

    def test_tx_fetch_kline_fail_none(self):
        with mock.patch.object(sd, "_http_get_json", side_effect=Exception("net")):
            self.assertIsNone(sd._tx_fetch_kline("601020.SH", "day", 250))

    def test_tx_fetch_minute_parse(self):
        fake = {"data": {"sh601020": {"data": {
            "date": "20260814",
            "data": ["0930 21.82 3356 7322792.00", "0931 21.51 8842 19188720.00"],
        }}}}
        with mock.patch.object(sd, "_http_get_json", return_value=fake):
            bars = sd._tx_fetch_minute("601020.SH")
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0]["time"], "2026-08-14 09:30")
        self.assertAlmostEqual(bars[0]["close"], 21.82)
        self.assertEqual(bars[0]["open"], bars[0]["high"])
        self.assertEqual(bars[0]["volume"], 3356 * 100)

    def test_tx_fetch_trends_rows_avg(self):
        fake = {"data": {"sh601020": {"data": {
            "data": ["0930 21.82 3356 7322792.00", "0931 21.51 8842 19188720.00"],
        }}}}
        with mock.patch.object(sd, "_http_get_json", return_value=fake):
            rows = sd._tx_fetch_trends_rows("601020.SH")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["time"], "09:30")
        # 均价 = 累计成交额 / 累计成交量(股)
        expected = 19188720.0 / ((3356 + 8842) * 100)
        self.assertAlmostEqual(rows[1]["avg"], round(expected, 3))

    def test_fetch_kline_falls_back_to_tx(self):
        # 东财 klines 为空 → 自动走腾讯兜底
        em_fake = {"data": {"klines": []}}
        tx_fake = {"data": {"sh601020": {"qfqday": [
            ["2026-08-14", "21.820", "21.940", "22.230", "21.500", "215766"],
        ]}}}
        with mock.patch.object(sd, "_em_get", return_value=em_fake), \
                mock.patch.object(sd, "_http_get_json", return_value=tx_fake):
            out = sd.fetch_kline("601020.SH", "day", force=True)
        self.assertEqual(len(out["bars"]), 1)
        self.assertAlmostEqual(out["last_close"], 21.94)

    def test_fetch_kline_time_falls_back_to_minute(self):
        em_fake = {"data": {"klines": []}}
        tx_fake = {"data": {"sh601020": {"data": {
            "date": "20260814", "data": ["0930 21.82 3356 7322792.00"],
        }}}}
        with mock.patch.object(sd, "_em_get", return_value=em_fake), \
                mock.patch.object(sd, "_http_get_json", return_value=tx_fake):
            out = sd.fetch_kline("601020.SH", "time", force=True)
        self.assertEqual(len(out["bars"]), 1)
        self.assertIn(" ", out["bars"][0]["time"])  # 含时分

    def test_fetch_trends_falls_back_to_tx(self):
        em_fake = {"data": {"trends": []}}
        tx_fake = {"data": {"sh601020": {"data": {
            "data": ["0930 21.82 3356 7322792.00", "0931 21.51 8842 19188720.00"],
        }}}}
        with mock.patch.object(sd, "_em_get", return_value=em_fake), \
                mock.patch.object(sd, "_http_get_json", return_value=tx_fake):
            rows = sd.fetch_trends("601020.SH", force=True)
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[0]["price"], 21.82)


if __name__ == "__main__":
    unittest.main()
