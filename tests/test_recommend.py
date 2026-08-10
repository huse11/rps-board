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

    def test_below_ma20_fail(self):
        # 上证指数下行, 收盘跌破20日线 → 不合格, 终止选股
        idx = make_index_map([3500 - i * 10 for i in range(30)])
        ok, status = recommend.check_market_env(idx, {}, "20260807")
        self.assertFalse(ok)
        self.assertFalse(status["is_safe"])
        self.assertIn("跌破20日线", status["reason"])

    def test_need_two_above_config(self):
        # need_above=2: 仅1个指数站上 → 不合格 (验证配置生效)
        import pandas as pd
        orig = recommend.CONFIG["market"]["need_above"]
        try:
            recommend.CONFIG["market"]["need_above"] = 2
            base = datetime.strptime("20260807", "%Y%m%d")
            dates = sorted([(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(30)])
            up = pd.DataFrame({"close": [3000 + i * 5 for i in range(30)], "trade_date": dates})
            down = pd.DataFrame({"close": [3500 - i * 10 for i in range(30)], "trade_date": dates})
            ok, _ = recommend.check_market_env({"000001.SH": up, "399006.SZ": down}, {}, "20260807")
            self.assertFalse(ok)  # 1 站上 < 2
        finally:
            recommend.CONFIG["market"]["need_above"] = orig

    def test_sentiment_veto(self):
        # 指数合格但涨跌比过低 → 情绪校验否决
        import pandas as pd
        idx = make_index_map([3000 + i * 5 for i in range(30)])
        # 25涨 75跌 → 上涨占比 25% < 30% 阈值
        rows = [{"ts_code": f"A{i:03d}", "pct_chg": 1.0, "amount": 1e7} for i in range(25)]
        rows += [{"ts_code": f"B{i:03d}", "pct_chg": -1.0, "amount": 1e7} for i in range(75)]
        dm = {"20260807": pd.DataFrame(rows)}
        ok, status = recommend.check_market_env(idx, dm, "20260807")
        self.assertFalse(ok)
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
        for c in ("600001.SH", "600002.SH", "600003.SH", "600004.SH", "600005.SH",
                  "600006.SH", "600007.SH"):
            self.assertNotIn(c, passed_codes, f"{c} 应被基本面否决")
        reasons = {r["ts_code"]: r["reason"] for r in rejects}
        self.assertIn("ST/退市", reasons["600001.SH"])
        self.assertIn("净利润为负", reasons["600006.SH"])
        self.assertIn("资产负债率", reasons["600007.SH"])

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


if __name__ == "__main__":
    unittest.main()
