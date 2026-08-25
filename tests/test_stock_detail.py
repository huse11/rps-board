# -*- coding: utf-8 -*-
"""
单元测试：个股详情数据模块 v1.0 (stock_detail.py)
覆盖: 基础工具(代码规范/单位格式化) / BOLL / RSI / MACD / KDJ /
      主力捉妖(加仓/卖信号) / 擒龙启爆(色块/启爆器/火焰/起爆点) /
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
        # 去噪: 连续满足条件只标记首次, 末位应为 False(已被抑制)
        adds = [r for r in out if r["addPosition"]]
        self.assertEqual(len(adds), 1)
        self.assertFalse(out[-1]["addPosition"])
        self.assertTrue(out[1]["addPosition"])  # 连续净流入满2日即首次标记
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
        em_fake = {"data": {"trends": []}}
        tx_fake = {"data": {"sh601020": {"data": {
            "date": "20260814", "data": ["0930 21.82 3356 7322792.00"],
        }}}}
        with mock.patch.object(sd, "_em_get", return_value=em_fake), \
                mock.patch.object(sd, "_http_get_json", return_value=tx_fake):
            out = sd.fetch_kline("601020.SH", "time", force=True)
        # time 周期返回分时结构 days:[{date, points}], 不再走 K线 bars (2026-08-24 多日分时改造)
        self.assertEqual(out["period"], "time")
        self.assertEqual(len(out["days"]), 1)
        self.assertTrue(out["days"][0]["points"])

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

    def test_fetch_trends_em_field_mapping(self):
        """东财 trends2 列序: f51时间 f52开盘 f53收盘(最新) f54最高 f55最低 f56量 f57额 f58均价
        回归: price 取收盘(最新价) 而非开盘; avg 取均价 而非最低价 (2026-08-18 修复)
        """
        em_fake = {"data": {"trends": [
            "2026-08-18 09:30,22.14,22.14,22.14,22.14,1835,4062247.00,22.140",
            "2026-08-18 09:31,22.13,22.14,22.14,22.00,5374,11871254.00,22.102",
        ]}}
        with mock.patch.object(sd, "_em_get", return_value=em_fake):
            rows = sd.fetch_trends("601020.SH", force=True)
        self.assertEqual(len(rows), 2)
        # 09:31: 开盘22.13 ≠ 最新价22.14 → price 必须是收盘价
        self.assertAlmostEqual(rows[1]["price"], 22.14)
        # 均价列 = f58 (22.102), 而非最低价 22.00
        self.assertAlmostEqual(rows[1]["avg"], 22.102)
        self.assertEqual(rows[1]["volume"], 5374)
        self.assertEqual(rows[0]["time"], "2026-08-18 09:30")


class TestMultiTrends(unittest.TestCase):
    """多日连续分时 fetch_multiday_trends (2026-08-24): trends2 按交易日分组正序, 单次调用封顶 5 日"""

    def _em_fake(self):
        return {"data": {"trends": [
            # 第1天 08-18
            "2026-08-18 09:30,22.14,22.14,22.14,22.14,1835,4062247.00,22.140",
            "2026-08-18 09:31,22.13,22.14,22.14,22.00,5374,11871254.00,22.102",
            # 第2天 08-19
            "2026-08-19 09:30,22.10,22.20,22.20,22.10,2000,4400000.00,22.200",
            "2026-08-19 09:31,22.20,22.25,22.26,22.15,3100,6878000.00,22.233",
        ]}}

    def test_multiday_groups_by_day_ordered(self):
        """跨日数据按日期分组且按日期正序, 组内点序保持原始顺序"""
        with mock.patch.object(sd, "_em_get", return_value=self._em_fake()):
            out = sd.fetch_multiday_trends("601020.SH", days=2, force=True)
        self.assertEqual([d["date"] for d in out], ["2026-08-18", "2026-08-19"])
        self.assertEqual(len(out[0]["points"]), 2)
        self.assertEqual(len(out[1]["points"]), 2)
        # 第2天首点 time 为当日 09:30 (未被拼到第1天)
        self.assertEqual(out[1]["points"][0]["time"], "2026-08-19 09:30")
        # 字段: time/price/avg/volume
        p = out[0]["points"][1]
        self.assertAlmostEqual(p["price"], 22.14)
        self.assertAlmostEqual(p["avg"], 22.102)
        self.assertEqual(p["volume"], 5374)

    def test_multiday_caps_at_5_single_call(self):
        """days>5 退化: 单次调用 ndays=5 (trends2 无日期偏移, 循环翻页会产生脏数据)"""
        seen = {}
        def fake_em(url, domains, args=(), tries=3):
            seen["url"] = url
            seen["args"] = args
            return self._em_fake()
        with mock.patch.object(sd, "_em_get", side_effect=fake_em):
            sd.fetch_multiday_trends("601020.SH", days=10, force=True)
        self.assertIn("ndays=", seen["url"], "trends2 按 ndays 拉取")
        self.assertEqual(seen["args"], (sd.em_secid(sd.to_ts_code("601020.SH")), 5), "单次封顶 5 日")

    def test_multiday_empty_returns_empty(self):
        with mock.patch.object(sd, "_em_get", return_value={"data": {"trends": []}}):
            self.assertEqual(sd.fetch_multiday_trends("601020.SH", days=5, force=True), [])
        with mock.patch.object(sd, "_em_get", return_value=None):
            self.assertEqual(sd.fetch_multiday_trends("601020.SH", days=5, force=True), [])

    def test_fetch_kline_multi_structure(self):
        """fetch_kline(multi) → {period, fqt, days:[{date,points}], last_close}, 不走 K线 bars"""
        with mock.patch.object(sd, "_em_get", return_value=self._em_fake()):
            out = sd.fetch_kline("601020.SH", "multi", fqt="qfq", days=2, force=True)
        self.assertEqual(out["period"], "multi")
        self.assertEqual(out["fqt"], "qfq")
        self.assertEqual(len(out["days"]), 2)
        self.assertAlmostEqual(out["last_close"], 22.25)  # 最后一天最后一点 price
        self.assertNotIn("bars", out)

    def test_fetch_kline_multi_degrade_to_single_day(self):
        """多日分时源(push2his)不可达 → 降级退回当日分时 (days 单元素 + degraded 标记), 不白屏"""
        # fetch_multiday_trends 内部 _em_get 返回空 → 再走 fetch_trends 也空 → 腾讯兜底
        em_empty = {"data": {"trends": []}}
        tx_fake = {"data": {"sh601020": {"data": {
            "date": "20260814", "data": ["0930 21.82 3356 7322792.00"],
        }}}}
        with mock.patch.object(sd, "_em_get", return_value=em_empty), \
                mock.patch.object(sd, "_http_get_json", return_value=tx_fake):
            out = sd.fetch_kline("601020.SH", "multi", fqt="qfq", days=5, force=True)
        self.assertEqual(out["period"], "multi")
        self.assertEqual(len(out["days"]), 1, "降级为单日分时")
        self.assertTrue(out["days"][0].get("degraded"), "降级数据需带 degraded 标记")
        self.assertTrue(out["days"][0]["points"])


class TestBsMarks(unittest.TestCase):
    """B/S 历史信号标记: 均线金叉(B)/死叉(S), 纯静态仅供查看; 连续重复信号只标首次"""

    def _bars(self, closes):
        return [{"time": "202601%02d" % i, "open": c, "high": c + 0.1, "low": c - 0.1,
                 "close": c, "volume": 1000} for i, c in enumerate(closes)]

    def test_uptrend_only_B(self):
        # 低位横盘后单边上涨: MA5 上穿 MA10 一次 → 只有一个 B, 无 S
        closes = [10] * 12 + [10.5 + i * 0.5 for i in range(18)]
        marks = sd.calc_bs_marks(self._bars(closes))
        self.assertTrue(any(m["type"] == "B" for m in marks))
        self.assertFalse(any(m["type"] == "S" for m in marks))

    def test_alternating_cross(self):
        # 先涨后跌再涨 → 出现 B / S / B 交替信号
        closes = [10] * 6 + [12] * 6 + [8] * 6 + [13] * 6
        marks = sd.calc_bs_marks(self._bars(closes))
        types = [m["type"] for m in marks]
        self.assertIn("B", types)
        self.assertIn("S", types)

    def test_short_data_empty(self):
        self.assertEqual(sd.calc_bs_marks([]), [])
        self.assertEqual(sd.calc_bs_marks(self._bars([10, 11, 12])), [])

    def test_consecutive_dedupe(self):
        # 连续处于金叉状态多日 → 只标记首次(去噪)
        closes = [10] * 12 + [10.2, 10.5, 11, 12, 13, 14]
        marks = sd.calc_bs_marks(self._bars(closes))
        b_count = sum(1 for m in marks if m["type"] == "B")
        self.assertEqual(b_count, 1)


class TestMarketStatus(unittest.TestCase):
    def test_suspend(self):
        self.assertEqual(sd.market_status({}), "停牌")
        self.assertEqual(sd.market_status({"price": 10.0, "volume": 0}), "停牌")

    def test_weekend_close(self):
        # 周末(weekday>=5) → 非交易时段 → 收盘
        rt = {"price": 10.0, "volume": 100}
        with mock.patch.object(sd, "datetime") as fake_dt:
            class _Now:
                def weekday(self):
                    return 6
                hour = 10
                minute = 30
            fake_dt.now.return_value = _Now()
            self.assertEqual(sd.market_status(rt), "收盘")

    def test_trading_hours(self):
        with mock.patch.object(sd, "datetime") as fake_dt:
            class _Now:
                def weekday(self):
                    return 2  # 周三
                hour = 10
                minute = 30
            fake_dt.now.return_value = _Now()
            self.assertEqual(sd.market_status({"price": 10.0, "volume": 100}), "盘中")


class TestSignalDedupe(unittest.TestCase):
    """擒龙启爆/主力捉妖 连续重复信号过滤 (同拐点只标记一次)"""

    def test_qibao_break_dedupe(self):
        closes = [10 + i * 0.1 for i in range(40)]
        highs = [c + 0.2 for c in closes]
        lows = [c - 0.2 for c in closes]
        vols = [1000 + (i * 13) % 500 for i in range(40)]
        qb = sd.calc_qinlong_qibao(closes, highs, lows, vols)
        # 连续两天 break=1 的情况不应出现相邻重复
        for i in range(1, len(qb)):
            self.assertFalse(qb[i]["break"] == 1 and qb[i - 1]["break"] == 1)

    def test_zhuoyao_add_dedupe(self):
        # 连续5天满足加仓条件 → 只首日标记
        rows = [
            {"date": f"2026080{i}", "main": 1000 + i * 100, "small": -100 - i,
             "mid": 300, "big": 500 + i * 10, "super": 200}
            for i in range(1, 6)
        ]
        out = sd.calc_zhuli_zhuoyao(rows)
        add_days = [r["date"] for r in out if r["addPosition"]]
        self.assertEqual(len(add_days), 1, "连续加仓只标首次: %s" % add_days)


class TestRealtimeExtra(unittest.TestCase):
    def test_fetch_realtime_new_fields(self):
        fake = {"data": {
            "f43": 21.94, "f60": 21.91, "f169": 0.03, "f170": 0.14,
            "f46": 21.82, "f44": 22.23, "f45": 21.5, "f50": 0.52, "f168": 2.63,
            "f48": 4.72e8, "f116": 1.799e10, "f117": 8.5e9, "f162": 147.18,
            "f167": 3.2, "f47": 123456, "f58": "华钰矿业", "f57": "601020",
        }}
        with mock.patch.object(sd, "_em_get", return_value=fake):
            rt = sd.fetch_realtime("601020.SH", force=True)
        self.assertEqual(rt["circ_mv"], 8.5e9, "流通市值")
        self.assertAlmostEqual(rt["amplitude"], round((22.23 - 21.5) / 21.91 * 100, 2), places=2)
        self.assertIn("marketStatus", rt)


if __name__ == "__main__":
    unittest.main()
