# -*- coding: utf-8 -*-
"""
单元测试：每日推荐股票模块 v3.0 (recommend.py)
覆盖: 五层漏斗各层 (大盘/候选池/基本面/资金面/技术共振) + 综合评分 + JSON输出
运行: python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import recommend


def make_daily_map(closes_by_code, latest_date="20260807", vols_by_code=None, amounts_by_code=None,
                   pct_chg_by_code=None):
    """构造 mock daily_map: {date: DataFrame}
    closes_by_code:  {code: [close_day1, ...]}
    vols_by_code:    {code: [vol_day1, ...]} 不传则默认 100000 手/天
    amounts_by_code: {code: [amount_day1, ...]} 不传则按 close*1e5 估算 (千元, 约1亿成交额)
    pct_chg_by_code: {code: [pct_day1, ...]} 不传则默认 1.0
    生成 OHLCV: open=close*0.99, high=close*1.01, low=close*0.98 (保证 close>open)
    """
    import pandas as pd
    n_days = len(next(iter(closes_by_code.values())))
    base = datetime.strptime(latest_date, "%Y%m%d")
    dates = sorted([(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(n_days)])
    dm = {}
    for i, date in enumerate(dates):
        rows = []
        for code, closes in closes_by_code.items():
            c = closes[i]
            vol = vols_by_code[code][i] if vols_by_code else 100000
            amt = amounts_by_code[code][i] if amounts_by_code else c * 1e5  # 千元, 约1亿成交额
            pct = pct_chg_by_code[code][i] if pct_chg_by_code else 1.0
            rows.append({
                "ts_code": code, "open": c * 0.99, "high": c * 1.01,
                "low": c * 0.98, "close": c, "vol": vol, "amount": amt,
                "pct_chg": pct,
            })
        dm[date] = pd.DataFrame(rows)
    return dm


def make_index_map(close_series, latest_date="20260807"):
    """构造 mock index_map: {code: DataFrame(close, trade_date)}"""
    import pandas as pd
    n = len(close_series)
    base = datetime.strptime(latest_date, "%Y%m%d")
    dates = sorted([(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(n)])
    return {"000001.SH": pd.DataFrame({"close": close_series, "trade_date": dates})}


def stock_rows(codes_meta):
    """codes_meta: [(ts_code, name, price, vol, circ_mv, pe_ttm, turnover, vol_ratio)]
    返回 DataFrame, 供 screen_* 层测试
    """
    import pandas as pd
    return pd.DataFrame([
        {
            "ts_code": c[0], "name": c[1], "price": c[2], "vol": c[3],
            "circ_mv": c[4], "pe_ttm": c[5], "turnover_rate": c[6], "vol_ratio": c[7],
        }
        for c in codes_meta
    ])


class TestStFilter(unittest.TestCase):
    def test_normal(self):
        self.assertFalse(recommend.is_st_stock("广哈通信"))
        self.assertFalse(recommend.is_st_stock("中国平安"))

    def test_st(self):
        self.assertTrue(recommend.is_st_stock("ST康美"))
        self.assertTrue(recommend.is_st_stock("*ST康得"))

    def test_delisting(self):
        self.assertTrue(recommend.is_st_stock("退市美都"))

    def test_empty(self):
        self.assertFalse(recommend.is_st_stock(""))
        self.assertFalse(recommend.is_st_stock(None))


class TestNewStock(unittest.TestCase):
    def test_old_stock(self):
        self.assertFalse(recommend.is_new_stock("20200101", "20260807", 60))

    def test_new_stock(self):
        self.assertTrue(recommend.is_new_stock("20260718", "20260807", 60))

    def test_missing_data(self):
        self.assertFalse(recommend.is_new_stock("", "20260807", 60))
        self.assertFalse(recommend.is_new_stock(None, "20260807", 60))


class TestTechnicalIndicators(unittest.TestCase):
    def test_calc_macd_short_data(self):
        self.assertIsNone(recommend.calc_macd([10.0, 10.1, 10.2]))

    def test_calc_macd_normal(self):
        closes = [10 + i * 0.1 for i in range(40)]
        macd = recommend.calc_macd(closes)
        self.assertIsNotNone(macd)
        self.assertIn("dif", macd)
        self.assertGreater(macd["dif"], 0)

    def test_calc_kdj_short(self):
        self.assertIsNone(recommend.calc_kdj([10, 11], [9, 10], [10, 11], n=9))

    def test_calc_kdj_normal(self):
        highs = [11] * 15
        lows = [9] * 15
        closes = [10 + i * 0.05 for i in range(15)]
        kdj = recommend.calc_kdj(highs, lows, closes, n=9)
        self.assertIsNotNone(kdj)
        self.assertIn("j", kdj)

    def test_calc_obv_short(self):
        self.assertIsNone(recommend.calc_obv([10, 11], [100, 200]))

    def test_calc_obv_uptrend(self):
        closes = [10, 11, 12, 13, 14]
        vols = [100, 200, 150, 300, 250]
        obv = recommend.calc_obv(closes, vols)
        self.assertIsNotNone(obv)
        self.assertGreater(obv["today"], obv["prev_3d"])


class TestCalcBoll(unittest.TestCase):
    def test_short_data(self):
        self.assertIsNone(recommend.calc_boll([10, 11, 12]))

    def test_normal_uptrend(self):
        closes = [10 + i * 0.1 for i in range(25)]
        boll = recommend.calc_boll(closes)
        self.assertIsNotNone(boll)
        self.assertGreater(boll["upper"], boll["mid"])
        self.assertGreater(boll["mid"], boll["lower"])
        self.assertGreater(closes[-1], boll["mid"])


class TestCalcCci(unittest.TestCase):
    def test_short_data(self):
        self.assertIsNone(recommend.calc_cci([11, 10, 9], [9, 8, 7], [10, 9, 8]))

    def test_strong_uptrend_high_cci(self):
        highs = [11 + i * 0.3 for i in range(20)]
        lows = [10 + i * 0.3 for i in range(20)]
        closes = [10.5 + i * 0.3 for i in range(20)]
        cci = recommend.calc_cci(highs, lows, closes)
        self.assertIsNotNone(cci)
        self.assertGreater(cci["cci"], 100)


class TestCalcRsi(unittest.TestCase):
    def test_all_up(self):
        closes = [10 + i * 0.5 for i in range(15)]
        rsi = recommend.calc_rsi(closes)
        self.assertIsNotNone(rsi)
        self.assertGreater(rsi["rsi"], 70)

    def test_all_down(self):
        closes = [20 - i * 0.5 for i in range(15)]
        rsi = recommend.calc_rsi(closes)
        self.assertIsNotNone(rsi)
        self.assertLess(rsi["rsi"], 30)


class TestCalcObvSeries(unittest.TestCase):
    def test_short_data(self):
        self.assertIsNone(recommend.calc_obv_series([10, 11], [100, 200]))

    def test_uptrend_no_divergence(self):
        closes = [10 + i * 0.3 for i in range(20)]
        vols = [1000 + i * 100 for i in range(20)]
        obv = recommend.calc_obv_series(closes, vols, n=20)
        self.assertIsNotNone(obv)
        self.assertGreaterEqual(obv["today"], obv["max_n"] - 1)


# ============================================================
# 第一层: 大盘环境全局校验
# ============================================================

class TestCheckMarketEnv(unittest.TestCase):
    def test_missing_index_default_safe(self):
        # 指数数据完全缺失 → 默认安全 (避免接口故障误否决全市场)
        ok, status = recommend.check_market_env({}, {}, "20260807")
        self.assertTrue(ok)
        self.assertTrue(status["is_safe"])
        self.assertIn("默认安全", status["reason"])

    def test_above_ma20_pass(self):
        # 上证指数上行, 收盘站上20日线 → 合格
        idx = make_index_map([3000 + i * 5 for i in range(30)])
        ok, status = recommend.check_market_env(idx, {}, "20260807")
        self.assertTrue(ok)
        self.assertTrue(status["is_safe"])
        self.assertTrue(status["indexes"][0]["above"])

    def test_below_ma20_warn(self):
        # 上证指数下行跌破20日线, 但无普跌信号 → WARN (继续选股, 全量打【大盘偏弱】标签)
        idx = make_index_map([3500 - i * 10 for i in range(30)])
        ok, status = recommend.check_market_env(idx, {}, "20260807")
        self.assertTrue(ok)  # 三态: 未触发 REJECT 则继续选股
        self.assertEqual(status["level"], "warn")
        self.assertIn("跌破20日线", status["reason"])

    def test_reject_extreme_advance(self):
        # 上涨占比 < 25% → REJECT (输出0只)
        import pandas as pd
        idx = make_index_map([3000 + i * 5 for i in range(30)])
        rows = [{"ts_code": f"A{i:03d}", "pct_chg": 1.0, "amount": 1e7} for i in range(24)]
        rows += [{"ts_code": f"B{i:03d}", "pct_chg": -1.0, "amount": 1e7} for i in range(76)]
        dm = {"20260807": pd.DataFrame(rows)}
        ok, status = recommend.check_market_env(idx, dm, "20260807")
        self.assertFalse(ok)
        self.assertEqual(status["level"], "reject")
        self.assertEqual(status["limit_up_count"], 0)

    def test_reject_combination(self):
        # 跌停>40 且 涨停<12 → REJECT (组合判普跌)
        import pandas as pd
        idx = make_index_map([3000 + i * 5 for i in range(30)])
        rows = [{"ts_code": f"L{i:03d}", "pct_chg": -10.0, "amount": 1e7} for i in range(50)]  # 跌停50家
        rows += [{"ts_code": f"U{i:03d}", "pct_chg": 1.0, "amount": 1e7} for i in range(60)]    # 涨停0家
        rows += [{"ts_code": f"D{i:03d}", "pct_chg": -1.0, "amount": 1e7} for i in range(140)]
        dm = {"20260807": pd.DataFrame(rows)}
        ok, status = recommend.check_market_env(idx, dm, "20260807")
        self.assertFalse(ok)
        self.assertEqual(status["level"], "reject")
        self.assertEqual(status["limit_down_count"], 50)
        self.assertLess(status["limit_up_count"], 12)

    def test_need_two_above_warn(self):
        # need_above=2: 仅1个指数站上 → 未达 PASS, 但无普跌信号 → WARN (验证配置生效)
        import pandas as pd
        orig = recommend.CONFIG["market"]["need_above"]
        try:
            recommend.CONFIG["market"]["need_above"] = 2
            base = datetime.strptime("20260807", "%Y%m%d")
            dates = sorted([(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(30)])
            up = pd.DataFrame({"close": [3000 + i * 5 for i in range(30)], "trade_date": dates})
            down = pd.DataFrame({"close": [3500 - i * 10 for i in range(30)], "trade_date": dates})
            ok, status = recommend.check_market_env({"000001.SH": up, "399006.SZ": down}, {}, "20260807")
            self.assertTrue(ok)  # 1 站上 < 2 → WARN, 不终止
            self.assertEqual(status["level"], "warn")
        finally:
            recommend.CONFIG["market"]["need_above"] = orig

    def test_sentiment_weak_warn(self):
        # 指数合格但上涨占比 25% (30%>25%≥25%) → WARN (继续选股, 打【大盘偏弱】标签)
        import pandas as pd
        idx = make_index_map([3000 + i * 5 for i in range(30)])
        # 25涨 75跌 → 上涨占比 25% < 30% PASS 阈值, 但 ≥25% 未触发 REJECT
        rows = [{"ts_code": f"A{i:03d}", "pct_chg": 1.0, "amount": 1e7} for i in range(25)]
        rows += [{"ts_code": f"B{i:03d}", "pct_chg": -1.0, "amount": 1e7} for i in range(75)]
        dm = {"20260807": pd.DataFrame(rows)}
        ok, status = recommend.check_market_env(idx, dm, "20260807")
        self.assertTrue(ok)
        self.assertEqual(status["level"], "warn")
        self.assertIn("上涨家数占比", status["reason"])


# ============================================================
# 第二层: 候选股票池 (板块共振映射)
# ============================================================

class TestBuildMemberSectorMap(unittest.TestCase):
    def test_mapping_tier_resonance(self):
        # 同一板块跨 RPS 档位重复入选 → 保留重复条目 = 共振档位数
        recs = [
            {"name": "黄金", "stocks": [{"ts_code": "600000.SH"}, "600001.SH"]},
            {"name": "黄金", "stocks": [{"ts_code": "600000.SH"}]},  # rps10 档
            {"name": "黄金", "stocks": [{"ts_code": "600000.SH"}]},  # rps20 档
            {"name": "半导体", "stocks": ["600000.SH", {"ts_code": "600002.SH"}]},
        ]
        mapping = recommend.build_member_sector_map(recs)
        # 600000: 黄金3档 + 半导体 = 4 条共振
        self.assertEqual(mapping["600000.SH"], ["黄金", "黄金", "黄金", "半导体"])
        self.assertEqual(mapping["600001.SH"], ["黄金"])
        self.assertEqual(mapping["600002.SH"], ["半导体"])

    def test_empty(self):
        self.assertEqual(recommend.build_member_sector_map([]), {})
        self.assertEqual(recommend.build_member_sector_map(None), {})
        self.assertEqual(recommend.build_member_sector_map([{"name": "X"}]), {})

    def test_extract_member_codes(self):
        recs = [
            {"name": "通信设备", "stocks": [{"ts_code": "600000.SH"}, {"ts_code": "600001.SH"}]},
            {"name": "光模块", "stocks": ["600002.SH", {"ts_code": "600003.SH"}]},
        ]
        self.assertEqual(
            recommend._extract_member_codes(recs),
            {"600000.SH", "600001.SH", "600002.SH", "600003.SH"})


class TestMemberPool(unittest.TestCase):
    """回归: 每日推荐股票必须严格是入选板块成分股"""

    def _industry_df(self):
        import pandas as pd
        return pd.DataFrame([
            {"ts_code": "600000.SH", "name": "甲", "industry": "有色金属"},
            {"ts_code": "600001.SH", "name": "乙", "industry": "有色金属"},
            {"ts_code": "600002.SH", "name": "丙", "industry": "医药"},
        ])

    def test_member_codes_priority_excludes_non_member(self):
        df = self._industry_df()
        members, codes, mode = recommend._select_member_pool(
            df, {"有色金属", "医药"}, member_codes={"600000.SH"})
        self.assertEqual(mode, "成分股名单")
        self.assertEqual(codes, ["600000.SH"])

    def test_fallback_industry_match(self):
        df = self._industry_df()
        members, codes, mode = recommend._select_member_pool(
            df, {"有色金属"}, member_codes=None)
        self.assertEqual(mode, "行业名匹配")
        self.assertEqual(sorted(codes), ["600000.SH", "600001.SH"])


# ============================================================
# 第三层: 基本面排雷 (硬性一票否决)
# ============================================================

class TestScreenFundamental(unittest.TestCase):
    def test_reject_rules(self):
        df = stock_rows([
            #  code       name    price  vol    circ_mv(万)  pe_ttm  turnover  vol_ratio
            ("600000.SH", "正常",   15.0, 100000, 500000,     20.0,   8.0,      1.5),
            ("600001.SH", "ST股",   15.0, 100000, 500000,     20.0,   8.0,      1.5),
            ("600002.SH", "低价股", 1.5,  100000, 500000,     20.0,   8.0,      1.5),
            ("600003.SH", "停牌股", 15.0, 0,      500000,     20.0,   8.0,      1.5),
            ("600004.SH", "小市值", 15.0, 100000, 50000,      20.0,   8.0,      1.5),
            ("600005.SH", "亏损PE", 15.0, 100000, 500000,     -5.0,   8.0,      1.5),
            ("600006.SH", "亏损",   15.0, 100000, 500000,     20.0,   8.0,      1.5),
            ("600007.SH", "高负债", 15.0, 100000, 500000,     20.0,   8.0,      1.5),
        ])
        fina_map = {
            "600006.SH": {"net_profit": -1e8, "debt_to_assets": 40},
            "600007.SH": {"net_profit": 1e8, "debt_to_assets": 85},
        }
        passed, rejects = recommend.screen_fundamental(df, fina_map)
        passed_codes = set(passed["ts_code"])
        self.assertIn("600000.SH", passed_codes)  # 正常保留
        self.assertIn("600005.SH", passed_codes)  # PE-TTM<0 不再一票否决 (评分阶段基本面分上限)
        for c in ("600001.SH", "600002.SH", "600003.SH", "600004.SH",
                  "600006.SH", "600007.SH"):
            self.assertNotIn(c, passed_codes, f"{c} 应被基本面否决")
        reasons = {r["ts_code"]: r["reason"] for r in rejects}
        self.assertIn("ST/退市", reasons["600001.SH"])
        self.assertIn("净利润为负", reasons["600006.SH"])
        self.assertIn("资产负债率", reasons["600007.SH"])

    def test_pe_neg_not_rejected(self):
        # PE-TTM<0 → 保留进入评分, 但基本面得分上限 pe_neg_cap (见 score_fundamental 上限测试)
        df = stock_rows([("600100.SH", "亏损PE", 15.0, 100000, 500000, -5.0, 8.0, 1.5)])
        passed, rejects = recommend.screen_fundamental(df, {"600100.SH": {"net_profit": 1e8}})
        self.assertEqual(set(passed["ts_code"]), {"600100.SH"})
        self.assertEqual(rejects, [])

    def test_pledge_goodwill_hard_veto(self):
        # 质押/商誉: 有数据且超标 → 一票否决; 缺失 → 不剔除
        df = stock_rows([
            ("600200.SH", "高质押", 15.0, 100000, 500000, 20.0, 8.0, 1.5),
            ("600201.SH", "高商誉", 15.0, 100000, 500000, 20.0, 8.0, 1.5),
            ("600202.SH", "无数据", 15.0, 100000, 500000, 20.0, 8.0, 1.5),
        ])
        fina_map = {
            "600200.SH": {"net_profit": 1e8, "pledge_ratio": 60.0},
            "600201.SH": {"net_profit": 1e8, "goodwill_ratio": 40.0},
            "600202.SH": {"net_profit": 1e8},
        }
        passed, rejects = recommend.screen_fundamental(df, fina_map)
        passed_codes = set(passed["ts_code"])
        self.assertNotIn("600200.SH", passed_codes)
        self.assertNotIn("600201.SH", passed_codes)
        self.assertIn("600202.SH", passed_codes)
        reasons = {r["ts_code"]: r["reason"] for r in rejects}
        self.assertIn("股权质押", reasons["600200.SH"])
        self.assertIn("商誉", reasons["600201.SH"])

    def test_no_fina_keeps_all(self):
        df = stock_rows([("600000.SH", "正常", 15.0, 100000, 500000, 20.0, 8.0, 1.5)])
        passed, rejects = recommend.screen_fundamental(df, {})
        self.assertEqual(len(passed), 1)
        self.assertEqual(rejects, [])


# ============================================================
# 第四层: 资金面达标校验 (全部核心项必达)
# ============================================================

class TestScreenCapital(unittest.TestCase):
    def setUp(self):
        # 6日放量上涨, amount=1e6千元=10亿/日 (>5000万)
        closes = [10.0, 10.1, 10.3, 10.5, 10.8, 11.0]
        vols = [100000, 100000, 100000, 100000, 100000, 150000]
        amounts = [1e6] * 6
        self.dm = make_daily_map(
            {"600000.SH": closes},
            vols_by_code={"600000.SH": vols},
            amounts_by_code={"600000.SH": amounts},
        )
        self.latest = "20260807"
        self.mflow_pos = {"600000.SH": [
            {"trade_date": "20260805", "buy_sm_amount": 3e7, "sell_sm_amount": 1e7},
            {"trade_date": "20260806", "buy_sm_amount": 4e7, "sell_sm_amount": 1e7},
            {"trade_date": "20260807", "buy_sm_amount": 5e7, "sell_sm_amount": 1e7},
        ]}  # 3日累计净流入 +1.2亿
        self.mflow_neg = {"600000.SH": [
            {"trade_date": "20260805", "buy_sm_amount": 1e7, "sell_sm_amount": 3e7},
            {"trade_date": "20260806", "buy_sm_amount": 1e7, "sell_sm_amount": 4e7},
            {"trade_date": "20260807", "buy_sm_amount": 1e7, "sell_sm_amount": 5e7},
        ]}  # 3日累计净流入 -1.2亿

    def _df(self, turnover=8.0, vol_ratio=1.5):
        return stock_rows([("600000.SH", "甲", 11.0, 150000, 500000, 20.0, turnover, vol_ratio)])

    def test_all_pass(self):
        passed, details = recommend.screen_capital(self._df(), self.dm, self.latest, self.mflow_pos)
        self.assertEqual(set(passed["ts_code"]), {"600000.SH"})
        self.assertIn("600000.SH", details)
        self.assertGreater(details["600000.SH"]["net_inflow_3d"], 0)

    def test_turnover_out_of_range(self):
        passed, _ = recommend.screen_capital(self._df(turnover=30.0), self.dm, self.latest, self.mflow_pos)
        self.assertTrue(passed.empty)  # 换手超上限 → 否决

    def test_vol_ratio_too_low(self):
        passed, _ = recommend.screen_capital(self._df(vol_ratio=1.0), self.dm, self.latest, self.mflow_pos)
        self.assertTrue(passed.empty)  # 量比 ≤1.1 → 否决

    def test_negative_inflow_reject(self):
        passed, _ = recommend.screen_capital(self._df(), self.dm, self.latest, self.mflow_neg)
        self.assertTrue(passed.empty)  # 主力净流入为负 → 否决

    def test_no_mflow_does_not_kill(self):
        passed, details = recommend.screen_capital(self._df(), self.dm, self.latest, {})
        self.assertEqual(set(passed["ts_code"]), {"600000.SH"})  # 资金数据缺失 → 不误杀
        self.assertIsNone(details["600000.SH"]["net_inflow_3d"])

    def test_vol_ratio_fallback_from_daily(self):
        # daily_basic 缺失(vol_ratio=None) → 用日线成交量兜底 (今日/5日均量=1.5 > 1.1)
        passed, details = recommend.screen_capital(self._df(vol_ratio=None), self.dm, self.latest, self.mflow_pos)
        self.assertEqual(set(passed["ts_code"]), {"600000.SH"})
        self.assertGreaterEqual(details["600000.SH"]["vol_ratio"], 1.1)


# ============================================================
# 第五层: 多技术指标共振 (达标 ≥3 项通过)
# ============================================================

class TestScreenTechnical(unittest.TestCase):
    def test_uptrend_resonance_pass(self):
        # 70日单调上涨: 均线多头 + MACD多头 + 20日新高 + 量价齐升 + RPS50强势 + 均线支撑
        closes = [10 + i * 0.05 for i in range(70)]
        dm = make_daily_map({"600000.SH": closes}, pct_chg_by_code={"600000.SH": [2.0] * 70})
        df = stock_rows([("600000.SH", "甲", 13.4, 100000, 500000, 20.0, 8.0, 1.5)])
        rps50_map = {"600000.SH": 95.0}
        passed, details = recommend.screen_technical(df, dm, "20260807", rps50_map=rps50_map)
        self.assertIn("600000.SH", set(passed["ts_code"]))
        d = details["600000.SH"]
        self.assertGreaterEqual(d["hit_count"], 3)
        self.assertIn("均线多头", d["hits"])
        self.assertIn("MACD多头", d["hits"])
        self.assertIn("20日新高", d["hits"])

    def test_insufficient_data_reject(self):
        dm = make_daily_map({"600000.SH": [10.0, 10.1, 10.2]})
        df = stock_rows([("600000.SH", "甲", 10.2, 100000, 500000, 20.0, 8.0, 1.5)])
        passed, details = recommend.screen_technical(df, dm, "20260807")
        self.assertTrue(passed.empty)  # <20日数据, 保守淘汰
        self.assertEqual(details, {})

    def test_below_threshold_reject(self):
        # 下跌趋势: 均线空头 + MACD空头 + 无新高 → 达标<3 → 否决
        closes = [30 - i * 0.2 for i in range(70)]
        dm = make_daily_map({"600000.SH": closes}, pct_chg_by_code={"600000.SH": [-1.0] * 70})
        df = stock_rows([("600000.SH", "甲", 16.2, 100000, 500000, 20.0, 8.0, 0.8)])
        passed, _ = recommend.screen_technical(df, dm, "20260807")
        self.assertTrue(passed.empty)


class TestBuildRps50Map(unittest.TestCase):
    def test_winner_high_rps(self):
        # 两只股票: 强者大涨 vs 弱者横盘 → 强者 RPS50≈100
        import numpy as np
        strong = [10 * (1.02 ** i) for i in range(55)]
        weak = [10.0 + i * 0.01 for i in range(55)]
        dm = make_daily_map({"600000.SH": strong, "600001.SH": weak})
        rps = recommend.build_rps50_map(dm, "20260807")
        self.assertIn("600000.SH", rps)
        self.assertGreater(rps["600000.SH"], rps["600001.SH"])

    def test_insufficient_days(self):
        dm = make_daily_map({"600000.SH": [10.0]})
        self.assertEqual(recommend.build_rps50_map(dm, "20260807"), {})


# ============================================================
# 综合评分与推荐标签
# ============================================================

class TestScoreFunnel(unittest.TestCase):
    def test_sector_funnel(self):
        self.assertEqual(recommend.score_sector_funnel(0), 0)
        self.assertEqual(recommend.score_sector_funnel(1), 20)
        self.assertEqual(recommend.score_sector_funnel(2), 30)
        self.assertEqual(recommend.score_sector_funnel(3), 40)
        self.assertEqual(recommend.score_sector_funnel(5), 40)

    def test_technical_funnel(self):
        self.assertEqual(recommend.score_technical_funnel(0), 0)
        self.assertEqual(recommend.score_technical_funnel(1), 7)
        self.assertEqual(recommend.score_technical_funnel(2), 14)
        self.assertEqual(recommend.score_technical_funnel(3), 20)  # 21 → 上限20
        self.assertEqual(recommend.score_technical_funnel(7), 20)

    def test_fundamental_funnel(self):
        import pandas as pd
        s = pd.Series({"pe_ttm": 20.0, "circ_mv": 100 * 10000})  # 100亿
        fina = {"net_profit": 1e8, "debt_to_assets": 40}
        self.assertEqual(recommend.score_fundamental_funnel(s, fina), 20)
        # 全缺失 → 0分(不淘汰)
        s0 = pd.Series({"pe_ttm": float("nan"), "circ_mv": float("nan")})
        self.assertEqual(recommend.score_fundamental_funnel(s0, None), 0)

    def test_capital_funnel(self):
        cap = {"turnover": 10.0, "vol_ratio": 2.0, "net_inflow_3d": 2e8, "amount": 5e8}
        self.assertEqual(recommend.score_capital_funnel(cap), 20)  # 5+5+5+5
        cap0 = {"turnover": 3.0, "vol_ratio": 1.0, "net_inflow_3d": -1e7, "amount": 1e7}
        self.assertEqual(recommend.score_capital_funnel(cap0), 0)
        self.assertEqual(recommend.score_capital_funnel({}), 0)

    def test_overbought_deduct_keys_in_technical(self):
        # 回归保护: 超买扣分键必须存在于 technical 配置块 (评分循环从该块读取)
        t = recommend.CONFIG["technical"]
        for k in ("overbought_deduct", "overbought_deduct_both"):
            self.assertIn(k, t, f"CONFIG['technical'] 缺少 {k}")


class TestFilterByMinScore(unittest.TestCase):
    def test_filters_below_threshold(self):
        recs = [{"score_total": 91}, {"score_total": 69}, {"score_total": 70}, {"score_total": 65}]
        out = recommend.filter_by_min_score(recs, 70)
        self.assertEqual([r["score_total"] for r in out], [91, 70])

    def test_default_config_threshold(self):
        recs = [{"score_total": 91}, {"score_total": 69}]
        out = recommend.filter_by_min_score(recs)  # 用 CONFIG 默认 70
        self.assertEqual([r["score_total"] for r in out], [91])

    def test_empty(self):
        self.assertEqual(recommend.filter_by_min_score([]), [])


class TestCapPerSector(unittest.TestCase):
    """输出层同板块控制: 同一细分板块最多输出 N 只"""

    def test_caps_by_first_sector(self):
        recs = [
            {"ts_code": "a", "sectors": ["半导体"]},
            {"ts_code": "b", "sectors": ["半导体"]},
            {"ts_code": "c", "sectors": ["半导体"]},
            {"ts_code": "d", "sectors": ["半导体"]},
            {"ts_code": "e", "sectors": ["白酒"]},
        ]
        kept, removed = recommend._cap_per_sector(recs, 3)
        self.assertEqual([r["ts_code"] for r in kept], ["a", "b", "c", "e"])
        self.assertEqual([r["ts_code"] for r in removed], ["d"])

    def test_no_sectors_use_industry(self):
        recs = [
            {"ts_code": "a", "sectors": [], "industry": "银行"},
            {"ts_code": "b", "sectors": [], "industry": "银行"},
            {"ts_code": "c", "sectors": [], "industry": "银行"},
            {"ts_code": "d", "sectors": [], "industry": "银行"},
        ]
        kept, removed = recommend._cap_per_sector(recs, 3)
        self.assertEqual(len(kept), 3)
        self.assertEqual(len(removed), 1)

    def test_disabled(self):
        recs = [{"ts_code": "a", "sectors": ["半导体"]}]
        kept, removed = recommend._cap_per_sector(recs, 0)
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(removed), 0)
        kept, removed = recommend._cap_per_sector(recs, None)
        self.assertEqual(len(kept), 1)


class TestBuildRecommendTags(unittest.TestCase):
    def test_multi_sector_resonance(self):
        tags = recommend.build_recommend_tags(
            ["通信设备", "光模块", "算力"], ["量价齐升", "MACD多头"])
        self.assertIn("3板块共振", tags)
        self.assertIn("量价齐升", tags)
        self.assertIn("MACD多头", tags)
        # 最多3段, 按优先级排序
        parts = tags.split(" + ")
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0], "3板块共振")

    def test_tier_resonance_single_sector(self):
        # 同一板块跨3档位入选 → 标签用 sector_count=3 (非去重板块数)
        tags = recommend.build_recommend_tags(["黄金"], ["量价齐升"], sector_count=3)
        self.assertIn("3板块共振", tags)

    def test_single_sector(self):
        tags = recommend.build_recommend_tags(["通信设备"], [])
        self.assertEqual(tags, "通信设备热点")

    def test_empty_fallback(self):
        self.assertEqual(recommend.build_recommend_tags([], []), "多指标共振")


# ============================================================
# JSON 输出 (schema v3)
# ============================================================

def _strict_parse_constant(s):
    raise ValueError("invalid JSON constant: " + s)


class TestSanitizeJson(unittest.TestCase):
    """回归: NaN/Inf 不得写入 JSON(浏览器 JSON.parse 抛 'Unexpected token N')"""

    def test_float_nan_to_none(self):
        import json
        out = recommend._sanitize_json({"pe_ttm": float("nan")})
        self.assertIsNone(out["pe_ttm"])
        json.dumps(out, allow_nan=False)

    def test_numpy_types_native(self):
        import json
        import numpy as np
        out = recommend._sanitize_json({"i": np.int64(5), "f": np.float64(3.14), "b": np.bool_(True)})
        self.assertEqual(out["i"], 5)
        self.assertEqual(out["f"], 3.14)
        self.assertIs(out["b"], True)
        json.dumps(out, allow_nan=False)

    def test_nested_dict_list(self):
        import json
        import numpy as np
        out = recommend._sanitize_json({
            "stocks": [{"pe_ttm": float("nan"), "tech": {"dif": np.float64("nan")}}],
        })
        self.assertIsNone(out["stocks"][0]["pe_ttm"])
        json.dumps(out, allow_nan=False)


class TestSaveRecommendationsV3(unittest.TestCase):
    """schema v3: market_status(新结构) / multi_sector_count / 无果断买入"""

    def _save(self, rec_list, market_status=None):
        import json
        import tempfile
        import os as _os
        import recommend as rec_mod
        rec_mod._LAST_MARKET_STATUS = market_status
        orig = rec_mod.REC_FILE
        fd, tmp = tempfile.mkstemp(suffix=".json")
        _os.close(fd)
        rec_mod.REC_FILE = tmp
        try:
            rec_mod.save_recommendations(rec_list, "20260807", "20260806")
            with open(tmp, encoding="utf-8") as f:
                return json.load(f)
        finally:
            rec_mod.REC_FILE = orig
            rec_mod._LAST_MARKET_STATUS = None
            if _os.path.exists(tmp):
                _os.remove(tmp)

    def test_schema_v3_fields(self):
        rec_list = [{
            "ts_code": "600000.SH", "name": "测试A", "industry": "通信设备",
            "sectors": ["通信设备", "光模块"], "sector_count": 2,
            "tags": "2板块共振 + 量价齐升",
            "price": 10.5, "pct_chg": 3.2, "pe_ttm": 15.0, "circ_mv": 200.5,
            "score_total": 88, "score_sector": 30, "score_fundamental": 18,
            "score_capital": 20, "score_technical": 20,
            "fundamental": {}, "capital": {}, "technical": {"hits": [], "hit_count": 0},
        }]
        data = self._save(rec_list, market_status={
            "is_safe": True, "rule": "至少 1 个指数站上 20 日线", "reason": "",
            "indexes": [{"code": "000001.SH", "name": "上证指数", "close": 3200.0, "ma": 3150.0, "above": True}],
            "advance_ratio": 0.6, "limit_down_count": 5, "note": "大盘环境合格, 继续选股",
        })
        self.assertEqual(data["schema_version"], 3)
        self.assertTrue(data["market_status"]["is_safe"])
        self.assertEqual(data["market_status"]["indexes"][0]["name"], "上证指数")
        self.assertEqual(data["multi_sector_count"], 1)
        self.assertEqual(data["count"], 1)
        self.assertNotIn("decisive_count", data)

    def test_veto_market_status_propagated(self):
        data = self._save([], market_status={
            "is_safe": False, "rule": "至少 1 个指数站上 20 日线",
            "reason": "上证指数 跌破20日线", "indexes": [],
            "advance_ratio": 0.2, "limit_down_count": 60, "note": "大盘环境不合格, 终止选股",
        })
        self.assertFalse(data["market_status"]["is_safe"])
        self.assertIn("跌破20日线", data["market_status"]["reason"])

    def test_default_market_status(self):
        data = self._save([])
        self.assertIn("market_status", data)

    def test_no_nan_in_output(self):
        import numpy as np
        nan = float("nan")
        rec_list = [{
            "ts_code": "600000.SH", "name": "测试A", "industry": "银行",
            "sectors": [], "sector_count": 1,
            "tags": "银行热点",
            "price": 10.5, "pct_chg": 3.2, "pe_ttm": nan,
            "circ_mv": 200.5,
            "score_total": 85, "score_sector": 20, "score_fundamental": 15,
            "score_capital": 20, "score_technical": 20,
            "fundamental": {"net_profit": nan}, "capital": {}, "technical": {},
        }]
        data = self._save(rec_list)
        self.assertIsNone(data["stocks"][0]["pe_ttm"])
        raw = __import__("json").dumps(data, allow_nan=False)  # 无 NaN = 不抛错
        self.assertIn("schema_version", data)


# ============================================================
# 候选股感知: 只计算 RPS 入选板块成分股
# ============================================================

class TestCandidateAwareEnrichment(unittest.TestCase):
    def _mock_em_pages(self, all_codes):
        pages = []
        for i in range(0, len(all_codes), 3):
            chunk = all_codes[i:i + 3]
            pages.append({"data": {"diff": [{"f12": c[:6], "f13": 1, "f62": 1.0e8} for c in chunk]}})
        called = {"pages": 0}

        def fake(url, timeout=12):
            called["pages"] += 1
            if called["pages"] <= len(pages):
                return pages[called["pages"] - 1]
            return {"data": {"diff": []}}
        return fake, called

    def test_moneyflow_em_codes_filters_and_breaks_early(self):
        import recommend as rec
        orig = rec._em_get_json
        all_codes = ["600000.SH", "600001.SH", "600002.SH", "600003.SH",
                     "600004.SH", "600005.SH", "600006.SH", "600007.SH", "600008.SH"]
        candidate = {"600000.SH", "600001.SH"}
        fake, called = self._mock_em_pages(all_codes)
        rec._em_get_json = fake
        try:
            result = rec.fetch_moneyflow_em("20260807", page_size=3, max_pages=5, codes=candidate)
        finally:
            rec._em_get_json = orig
        self.assertEqual(set(result.keys()), candidate)
        self.assertEqual(called["pages"], 1, f"应提前退出, 实际拉取 {called['pages']} 页")

    def test_daily_basic_em_codes_filters(self):
        import recommend as rec
        orig = rec._em_get_json
        all_codes = ["600000.SH", "600001.SH", "600002.SH"]
        candidate = {"600000.SH"}
        fake, _ = self._mock_em_pages(all_codes)
        rec._em_get_json = fake
        try:
            result = rec.fetch_daily_basic_em("20260807", page_size=100, max_pages=1, codes=candidate)
        finally:
            rec._em_get_json = orig
        self.assertEqual(set(result.keys()), candidate)


# ============================================================
# 财务 F10 (fetch_fina_f10_em) — 资产负债率真实值补全
# ============================================================

class TestFinaF10Fetch(unittest.TestCase):
    def test_f10_float_helper(self):
        # 数字保留(含0/负值), 真缺失 → None
        self.assertEqual(recommend._f10_float("15.19"), 15.19)
        self.assertEqual(recommend._f10_float(0), 0.0)
        self.assertEqual(recommend._f10_float(-3.2), -3.2)
        self.assertEqual(recommend._f10_float("-"), None)
        self.assertEqual(recommend._f10_float(None), None)
        self.assertEqual(recommend._f10_float(""), None)
        self.assertEqual(recommend._f10_float("NaN"), None)
        self.assertEqual(recommend._f10_float("abc"), None)

    def test_fina_f10_filters_non_stock_and_picks_latest_period(self):
        # 逐报告期拉取: 未来期跳过 → 最新期(2026-06-30, 全页500条) → 次新期(2026-03-31)
        # → 更旧期(2025-12-31)无新增即停; 非个股(指数/基金)始终被过滤
        latest_rows = [
            {"SECUCODE": "600519.SH", "REPORT_DATE": "2026-06-30 00:00:00", "ZCFZL": "15.19",
             "PARENTNETPROFIT": "44500000000", "PARENTNETPROFITTZ": "15.5",
             "KCFJCXSYJLR": "44300000000", "KCFJCXSYJLRTZ": "16.1"},
            {"SECUCODE": "A20007.SH", "REPORT_DATE": "2026-06-30 00:00:00", "ZCFZL": "1.0",
             "PARENTNETPROFIT": None, "PARENTNETPROFITTZ": None,
             "KCFJCXSYJLR": None, "KCFJCXSYJLRTZ": None},
            {"SECUCODE": "000001.SZ", "REPORT_DATE": "2026-06-30 00:00:00", "ZCFZL": "90.1",
             "PARENTNETPROFIT": "1000000", "PARENTNETPROFITTZ": "-5.2",
             "KCFJCXSYJLR": "800000", "KCFJCXSYJLRTZ": "-6.0"},
        ]
        for i in range(497):  # 凑满 500 条 (600100+i).SZ
            c = 600100 + i
            latest_rows.append({"SECUCODE": f"{c}.SZ", "REPORT_DATE": "2026-06-30 00:00:00",
                                "ZCFZL": "40.0", "PARENTNETPROFIT": "100000",
                                "PARENTNETPROFITTZ": "5.0", "KCFJCXSYJLR": "90000",
                                "KCFJCXSYJLRTZ": "4.0"})
        self.assertEqual(len(latest_rows), 500)
        # 600000.SH 最新披露期为 2026-03-31 (未披露半年报) → 应以其期补入
        old_row = {"SECUCODE": "600000.SH", "REPORT_DATE": "2026-03-31 00:00:00", "ZCFZL": "50.0",
                   "PARENTNETPROFIT": "2000000", "PARENTNETPROFITTZ": "3.0",
                   "KCFJCXSYJLR": "1900000", "KCFJCXSYJLRTZ": "2.5"}
        # 600519.SH 在 2025-12-31 也有行 → 已覆盖, 该期无新增即整体停止
        dup_row = {"SECUCODE": "600519.SH", "REPORT_DATE": "2025-12-31 00:00:00", "ZCFZL": "14.0",
                   "PARENTNETPROFIT": "40000000000", "PARENTNETPROFITTZ": "10.0",
                   "KCFJCXSYJLR": "39000000000", "KCFJCXSYJLRTZ": "9.0"}
        orig = recommend._em_get_json
        calls = {"urls": []}

        def fake(url, timeout=20):
            calls["urls"].append(url)
            if "2026-09-30" in url:  # 未来期 → 空
                return {"result": {"data": []}}
            if "2026-06-30" in url:
                # 第1页满500条, 第2页空 → break
                if len([u for u in calls["urls"] if "2026-06-30" in u]) == 1:
                    return {"result": {"data": latest_rows}}
                return {"result": {"data": []}}
            if "2026-03-31" in url:
                return {"result": {"data": [old_row]}}
            if "2025-12-31" in url:
                return {"result": {"data": [dup_row]}}
            return {"result": {"data": []}}
        recommend._em_get_json = fake
        try:
            result = recommend.fetch_fina_f10_em("20260814")
        finally:
            recommend._em_get_json = orig
        # 非个股被过滤; 600519/000001 来自 2026-06-30
        self.assertNotIn("A20007.SH", result)
        self.assertIn("600519.SH", result)
        self.assertIn("000001.SZ", result)
        # 600000.SH 未披露半年报 → 以其最新披露期 2026-03-31 补入 (100% 覆盖的关键)
        self.assertIn("600000.SH", result)
        self.assertEqual(result["600000.SH"]["end_date"], "20260331")
        self.assertEqual(result["600000.SH"]["debt_to_assets"], 50.0)
        self.assertEqual(len(result), 500)
        mt = result["600519.SH"]
        self.assertEqual(mt["debt_to_assets"], 15.19)
        self.assertEqual(mt["net_profit"], 44500000000.0)
        self.assertEqual(mt["netprofit_yoy"], 15.5)
        self.assertEqual(mt["dt_netprofit_yoy"], 16.1)
        self.assertEqual(mt["n_income_attr_parent"], 44300000000.0)
        self.assertEqual(mt["end_date"], "20260630")
        # 首个请求为未来期(2026-09-30)被跳过; 2025-12-31 无新增后整体停止
        self.assertIn("2026-09-30", calls["urls"][0])
        self.assertEqual(len(calls["urls"]), 5)

    def test_fina_f10_codes_finds_all_candidates_then_stops(self):
        # 候选股感知: 600519 在最新期, 600000 需回到次新期; 全部找齐即提前退出
        latest_row = {"SECUCODE": "600519.SH", "REPORT_DATE": "2026-06-30 00:00:00", "ZCFZL": "15.19",
                      "PARENTNETPROFIT": "44500000000", "PARENTNETPROFITTZ": "15.5",
                      "KCFJCXSYJLR": "44300000000", "KCFJCXSYJLRTZ": "16.1"}
        old_row = {"SECUCODE": "600000.SH", "REPORT_DATE": "2026-03-31 00:00:00", "ZCFZL": "50.0",
                   "PARENTNETPROFIT": "2000000", "PARENTNETPROFITTZ": "3.0",
                   "KCFJCXSYJLR": "1900000", "KCFJCXSYJLRTZ": "2.5"}
        orig = recommend._em_get_json
        calls = {"urls": []}

        def fake(url, timeout=20):
            calls["urls"].append(url)
            if "2026-09-30" in url:
                return {"result": {"data": []}}
            if "2026-06-30" in url:
                return {"result": {"data": [latest_row]}}
            if "2026-03-31" in url:
                return {"result": {"data": [old_row]}}
            return {"result": {"data": []}}
        recommend._em_get_json = fake
        try:
            result = recommend.fetch_fina_f10_em("20260814", codes={"600519.SH", "600000.SH"})
        finally:
            recommend._em_get_json = orig
        self.assertEqual(set(result.keys()), {"600519.SH", "600000.SH"})
        self.assertEqual(result["600000.SH"]["end_date"], "20260331")
        # 找齐后未继续请求 2025-12-31
        self.assertFalse(any("2025-12-31" in u for u in calls["urls"]))

    def test_fina_f10_exception_returns_empty(self):
        orig = recommend._em_get_json

        def fake(url, timeout=20):
            raise Exception("boom")
        recommend._em_get_json = fake
        try:
            result = recommend.fetch_fina_f10_em("20260814")
        finally:
            recommend._em_get_json = orig
        self.assertEqual(result, {})


# ============================================================
# 大盘主力资金 (fetch_market_fund_flow) — 替代北向, 真实值
# ============================================================

class TestMarketFundFlow(unittest.TestCase):
    def test_fetch_market_fund_flow_ak_and_cache(self):
        import tempfile
        import types
        from pathlib import Path as _P
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp_path = _P(tmp.name)
        tmp.close()
        orig_file, orig_ak, orig_ok = (recommend.MARKET_FLOW_CACHE_FILE,
                                       recommend._ak, recommend._AK_OK)
        try:
            recommend.MARKET_FLOW_CACHE_FILE = tmp_path
            recommend._ak = types.SimpleNamespace(fetch_market_fund_flow_ak=lambda n_days=5: {
                "20260814": {"main_net": 1.5e10, "main_pct": 0.35}})
            recommend._AK_OK = True
            res = recommend.fetch_market_fund_flow("20260814", n_days=5)
            self.assertEqual(res["20260814"]["main_net"], 1.5e10)
            self.assertTrue(tmp_path.exists())  # 缓存已落盘
            # AKShare 失败时仍可从缓存读出
            recommend._AK_OK = False
            res2 = recommend.fetch_market_fund_flow("20260814")
            self.assertEqual(res2["20260814"]["main_net"], 1.5e10)
        finally:
            recommend.MARKET_FLOW_CACHE_FILE = orig_file
            recommend._ak = orig_ak
            recommend._AK_OK = orig_ok
            tmp_path.unlink(missing_ok=True)

    def test_fetch_market_fund_flow_all_fail_empty(self):
        import tempfile
        from pathlib import Path as _P
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp_path = _P(tmp.name)
        tmp.close()
        orig_file, orig_ok = recommend.MARKET_FLOW_CACHE_FILE, recommend._AK_OK
        try:
            recommend.MARKET_FLOW_CACHE_FILE = tmp_path
            recommend._AK_OK = False
            self.assertEqual(recommend.fetch_market_fund_flow("20260814"), {})
        finally:
            recommend.MARKET_FLOW_CACHE_FILE = orig_file
            recommend._AK_OK = orig_ok
            tmp_path.unlink(missing_ok=True)


# ============================================================
# 大盘主力资金接入 check_market_env (仅提示不否决)
# ============================================================

class TestCheckMarketEnvMainFlow(unittest.TestCase):
    def test_main_flow_weak_hint_not_veto(self):
        idx = make_index_map([3000 + i * 5 for i in range(30)])
        flow = {"20260805": {"main_net": -1e10, "main_pct": -0.5},
                "20260806": {"main_net": -8e9, "main_pct": -0.4},
                "20260807": {"main_net": 3e9, "main_pct": 0.1}}
        ok, status = recommend.check_market_env(idx, {}, "20260807", market_flow=flow)
        self.assertTrue(ok)  # 仅提示, 不否决
        self.assertEqual(status["main_flow"]["verdict"], "资金偏弱")
        self.assertEqual(status["main_flow"]["neg_days"], 2)
        self.assertEqual(status["main_flow"]["total_net_yi"], -150.0)
        self.assertIn("净流出", status["note"])

    def test_main_flow_normal(self):
        idx = make_index_map([3000 + i * 5 for i in range(30)])
        flow = {"20260807": {"main_net": 5e9, "main_pct": 0.2}}
        ok, status = recommend.check_market_env(idx, {}, "20260807", market_flow=flow)
        self.assertTrue(ok)
        self.assertEqual(status["main_flow"]["verdict"], "资金正常")
        self.assertNotIn("净流出", status["note"])

    def test_main_flow_absent(self):
        idx = make_index_map([3000 + i * 5 for i in range(30)])
        ok, status = recommend.check_market_env(idx, {}, "20260807")
        self.assertTrue(ok)
        self.assertIsNone(status["main_flow"])


# ============================================================
# 早期启动挖掘 (EARLY_STAGE_START) — 独立候选池/形态/输出
# ============================================================

class TestEarlySectorPool(unittest.TestCase):
    def test_potential_plus_non_accelerate(self):
        in_list = [{"name": "半导体"}, {"name": "黄金"}, {"name": "白酒"}]
        review = {
            "potential_sectors": [{"name": "蓄势板块"}],
            "sector_life_cycle": {"rps5": {"半导体": "accelerate", "黄金": "growth", "白酒": "base"}},
        }
        names, pot = recommend._early_sector_pool(in_list, review)
        self.assertIn("蓄势板块", names)
        self.assertIn("黄金", names)
        self.assertIn("白酒", names)
        self.assertNotIn("半导体", names)  # accelerate 加速高潮 → 排除
        self.assertEqual(pot, {"蓄势板块"})

    def test_no_review_data(self):
        in_list = [{"name": "半导体"}, {"name": "黄金"}]
        names, pot = recommend._early_sector_pool(in_list, None)
        self.assertEqual(set(names), {"半导体", "黄金"})
        self.assertEqual(pot, set())

    def test_empty_in_list(self):
        review = {"potential_sectors": [{"name": "蓄势板块"}]}
        names, pot = recommend._early_sector_pool([], review)
        self.assertEqual(names, ["蓄势板块"])
        self.assertEqual(pot, {"蓄势板块"})


class TestEarlySectorMap(unittest.TestCase):
    def test_map_by_industry(self):
        import pandas as pd
        industry_df = pd.DataFrame([
            {"ts_code": "600000.SH", "name": "a", "industry": "蓄势板块", "list_date": ""},
            {"ts_code": "600001.SH", "name": "b", "industry": "蓄势板块", "list_date": ""},
            {"ts_code": "600002.SH", "name": "c", "industry": "半导体", "list_date": ""},
        ])
        m = recommend._build_early_sector_map(industry_df, ["蓄势板块", "半导体"])
        self.assertEqual(m["600000.SH"], ["蓄势板块"])
        self.assertEqual(m["600001.SH"], ["蓄势板块"])
        self.assertEqual(m["600002.SH"], ["半导体"])

    def test_empty(self):
        self.assertEqual(recommend._build_early_sector_map(None, ["X"]), {})


class TestEarlyPlatformStart(unittest.TestCase):
    def test_flat_breakout(self):
        # 近20日窄幅横盘(振幅~2%) + 今日收盘突破平台高点 → 平台刚启动
        closes = [10.0] * 19 + [10.15]
        highs = [10.1] * 20
        lows = [9.9] * 20
        self.assertTrue(recommend._is_platform_start(closes, highs, lows))

    def test_big_amplitude_not_platform(self):
        # 近20日大幅拉升(振幅巨大) → 非平台
        closes = [5.0 + i * 0.5 for i in range(20)]
        highs = [c * 1.01 for c in closes]
        lows = [c * 0.98 for c in closes]
        self.assertFalse(recommend._is_platform_start(closes, highs, lows))

    def test_flat_but_not_breakout(self):
        # 横盘但今日收盘未突破平台高点 → 非平台启动
        closes = [10.0] * 20
        highs = [10.5] * 20
        lows = [9.9] * 20
        self.assertFalse(recommend._is_platform_start(closes, highs, lows))

    def test_insufficient_data(self):
        self.assertFalse(recommend._is_platform_start([10.0] * 10, [10.0] * 10, [9.9] * 10))


class TestBuildPotentialStats(unittest.TestCase):
    """rps_calc.build_potential_stats: 临界潜力板块(RPS5∈[88,90))成分股统计"""

    def test_boundary_only(self):
        import rps_calc as rc
        hist = {"20260807": {"黄金": {"RPS5": 95, "rank5": 3},
                             "蓄势": {"RPS5": 89.0, "rank5": 15}},
                "20260810": {"黄金": {"RPS5": 95, "rank5": 3},
                             "蓄势": {"RPS5": 89.5, "rank5": 12}}}
        stock_rps50 = {"蓄势": {"stock_count": 20, "rps50_above_90": 8}}
        out = rc.build_potential_stats(hist, stock_rps50)
        self.assertEqual([x["name"] for x in out], ["蓄势"])
        self.assertEqual(out[0]["stock_count"], 20)
        self.assertEqual(out[0]["rps50_above_90"], 8)
        self.assertEqual(out[0]["rank5"], 12)
        self.assertEqual(out[0]["rank_change"], 3)  # 15-12, 排名上升为正
        self.assertEqual(out[0]["continuous"], 2)   # 连续2天 RPS5≥88

    def test_continuous_breaks(self):
        import rps_calc as rc
        hist = {"20260806": {"蓄势": {"RPS5": 80, "rank5": 20}},
                "20260807": {"蓄势": {"RPS5": 89.0, "rank5": 15}},
                "20260810": {"蓄势": {"RPS5": 89.5, "rank5": 12}}}
        out = rc.build_potential_stats(hist, {"蓄势": {"stock_count": 10, "rps50_above_90": 6}})
        self.assertEqual(out[0]["continuous"], 2)  # 20260806 不达标 → 中断

    def test_empty_history(self):
        import rps_calc as rc
        self.assertEqual(rc.build_potential_stats({}, {}), [])


class TestSaveEarlyRecommendations(unittest.TestCase):
    def _save(self, rec_list, market_status=None):
        import json
        import tempfile
        import os as _os
        rec_mod = recommend
        rec_mod._LAST_EARLY_MARKET_STATUS = market_status
        orig = rec_mod.EARLY_REC_FILE
        fd, tmp = tempfile.mkstemp(suffix=".json")
        _os.close(fd)
        rec_mod.EARLY_REC_FILE = tmp
        try:
            rec_mod.save_early_recommendations(rec_list, "20260810", "20260807")
            with open(tmp, encoding="utf-8") as f:
                return json.load(f)
        finally:
            rec_mod.EARLY_REC_FILE = orig
            rec_mod._LAST_EARLY_MARKET_STATUS = None
            if _os.path.exists(tmp):
                _os.remove(tmp)

    def test_schema_fields(self):
        rec = [{"ts_code": "600000.SH", "name": "测试", "industry": "蓄势板块",
                "sectors": ["蓄势板块"], "sector_count": 1,
                "tags": "平台刚启动 + 短线观察",
                "price": 10.0, "pct_chg": 1.0, "pe_ttm": 15.0, "circ_mv": 50.0,
                "score_total": 75, "score_sector": 20, "score_fundamental": 15,
                "score_capital": 20, "score_technical": 20,
                "early": {"platform_start": True}, "fundamental": {}, "capital": {}, "technical": {}}]
        data = self._save(rec, {"is_safe": True, "level": "good", "reason": "ok", "indexes": []})
        self.assertEqual(data["mode"], "early_stage_start")
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["platform_count"], 1)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["update_date"], "20260810")
        self.assertTrue(data["market_status"]["is_safe"])

    def test_default_market_status(self):
        data = self._save([])
        self.assertIn("market_status", data)
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["platform_count"], 0)

    def test_no_nan(self):
        import json as _json
        import numpy as np
        rec = [{"ts_code": "600000.SH", "name": "测试", "industry": "蓄势板块",
                "sectors": [], "sector_count": 1, "tags": "",
                "price": float("nan"), "pct_chg": 1.0, "pe_ttm": np.float64("nan"),
                "circ_mv": 50.0, "score_total": 71, "score_sector": 20,
                "score_fundamental": 15, "score_capital": 20, "score_technical": 20,
                "early": {"chg10": float("nan")}, "fundamental": {}, "capital": {}, "technical": {}}]
        data = self._save(rec)
        self.assertIsNone(data["stocks"][0]["price"])
        _json.dumps(data, allow_nan=False)  # 无 NaN 不抛错


if __name__ == "__main__":
    unittest.main()
