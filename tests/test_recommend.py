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

    def test_below_ma20_reject(self):
        # 严格二态: 上证指数下行跌破20日线 → REJECT (输出0只, 任一条件不满足即否决)
        idx = make_index_map([3500 - i * 10 for i in range(30)])
        ok, status = recommend.check_market_env(idx, {}, "20260807")
        self.assertFalse(ok)
        self.assertEqual(status["level"], "reject")
        self.assertIn("未站上20日线", status["reason"])

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

    def test_need_two_above_reject(self):
        # 严格二态: need_above=2, 仅1个指数站上 → 未达 PASS → REJECT (验证配置生效)
        import pandas as pd
        orig = recommend.CONFIG["market"]["need_above"]
        try:
            recommend.CONFIG["market"]["need_above"] = 2
            base = datetime.strptime("20260807", "%Y%m%d")
            dates = sorted([(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(30)])
            up = pd.DataFrame({"close": [3000 + i * 5 for i in range(30)], "trade_date": dates})
            down = pd.DataFrame({"close": [3500 - i * 10 for i in range(30)], "trade_date": dates})
            ok, status = recommend.check_market_env({"000001.SH": up, "399006.SZ": down}, {}, "20260807")
            self.assertFalse(ok)  # 1 站上 < 2 → REJECT, 终止选股
            self.assertEqual(status["level"], "reject")
            self.assertIn("未站上20日线", status["reason"])
        finally:
            recommend.CONFIG["market"]["need_above"] = orig

    def test_sentiment_weak_reject(self):
        # 严格二态: 指数合格但上涨占比 25% < 40% → REJECT (输出0只)
        import pandas as pd
        idx = make_index_map([3000 + i * 5 for i in range(30)])
        # 25涨 75跌 → 上涨占比 25% < advance_ratio_min(40%)
        rows = [{"ts_code": f"A{i:03d}", "pct_chg": 1.0, "amount": 1e7} for i in range(25)]
        rows += [{"ts_code": f"B{i:03d}", "pct_chg": -1.0, "amount": 1e7} for i in range(75)]
        dm = {"20260807": pd.DataFrame(rows)}
        ok, status = recommend.check_market_env(idx, dm, "20260807")
        self.assertFalse(ok)
        self.assertEqual(status["level"], "reject")
        self.assertIn("上涨家数占比", status["reason"])

    def test_sector_momentum_alt_pass(self):
        # 板块动量替代路径: 指数站上 + 跌停可控, 上涨占比 39%<40%
        # 但强势板块 18 个 ≥15 → 板块动量强, 豁免通过
        import pandas as pd
        idx = make_index_map([3000 + i * 5 for i in range(30)])
        rows = [{"ts_code": f"A{i:03d}", "pct_chg": 1.0, "amount": 1e7} for i in range(39)]
        rows += [{"ts_code": f"B{i:03d}", "pct_chg": -1.0, "amount": 1e7} for i in range(61)]
        dm = {"20260807": pd.DataFrame(rows)}  # 上涨占比 39%
        ok, status = recommend.check_market_env(idx, dm, "20260807", strong_sector_count=18)
        self.assertTrue(ok)
        self.assertTrue(status["alt_pass"])
        self.assertEqual(status["sector_strong_count"], 18)
        self.assertIn("板块动量强", status["note"])

    def test_sector_momentum_not_enough_reject(self):
        # 强势板块仅 5 个 < 15 → 无替代, 上涨占比不足 → 仍 REJECT
        import pandas as pd
        idx = make_index_map([3000 + i * 5 for i in range(30)])
        rows = [{"ts_code": f"A{i:03d}", "pct_chg": 1.0, "amount": 1e7} for i in range(39)]
        rows += [{"ts_code": f"B{i:03d}", "pct_chg": -1.0, "amount": 1e7} for i in range(61)]
        dm = {"20260807": pd.DataFrame(rows)}
        ok, status = recommend.check_market_env(idx, dm, "20260807", strong_sector_count=5)
        self.assertFalse(ok)
        self.assertEqual(status["level"], "reject")
        self.assertIn("上涨家数占比", status["reason"])

    def test_sector_alt_not_override_limit_down(self):
        # 替代路径不豁免跌停硬底线: 强势板块 18 个但跌停 50 家 → 仍 REJECT
        import pandas as pd
        idx = make_index_map([3000 + i * 5 for i in range(30)])
        rows = [{"ts_code": f"L{i:03d}", "pct_chg": -10.0, "amount": 1e7} for i in range(50)]
        rows += [{"ts_code": f"U{i:03d}", "pct_chg": 1.0, "amount": 1e7} for i in range(150)]
        dm = {"20260807": pd.DataFrame(rows)}
        ok, status = recommend.check_market_env(idx, dm, "20260807", strong_sector_count=18)
        self.assertFalse(ok)
        self.assertEqual(status["limit_down_count"], 50)
        self.assertIn("跌停", status["reason"])

    def test_count_strong_sectors(self):
        # 仅统计 RPS5 ≥ 95 的板块, 异常/缺失值跳过 (RPS95 校准后阈值)
        self.assertEqual(recommend.count_strong_sectors([
            {"name": "A", "RPS5": 95.0}, {"name": "B", "RPS5": 88},
            {"name": "C", "RPS5": 96.5}, {"name": "D", "RPS5": None},
            {"name": "E", "RPS5": "x"}, {"name": "F"}, "bad",
        ]), 2)
        self.assertEqual(recommend.count_strong_sectors([]), 0)
        self.assertEqual(recommend.count_strong_sectors(None), 0)


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

    def test_pe_neg_cap_config(self):
        # H1 回归: CONFIG["fundamental"] 必须含 pe_neg_cap, 且为不超过满分(15)的正数
        cap = recommend.CONFIG["fundamental"]["pe_neg_cap"]
        self.assertIsInstance(cap, (int, float))
        self.assertTrue(0 < cap <= 15, "pe_neg_cap 应在 (0,15] 区间: %r" % cap)

    def test_pe_neg_cap_caps_score(self):
        # H1 逻辑: PE<0 时基本面分被强制压到 pe_neg_cap 以下
        cap = recommend.CONFIG["fundamental"]["pe_neg_cap"]
        s = {"ts_code": "600100.SH", "name": "亏损PE", "pe_ttm": -5.0,
             "circ_mv": 500000.0}  # 市值(4分)+负PE区间(2分)+低负债(3分)=9分, 仍被压到 cap
        high = recommend.score_fundamental_funnel(s, {"net_profit": -1e8, "debt_to_assets": 40.0})
        self.assertGreater(high, cap, "前提: 负PE仍可能有高于上限的原始分")
        self.assertLessEqual(min(high, cap), cap)

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
        self.assertEqual(details["600000.SH"]["fail_gates"], [])  # 4 门全过

    # ---- 2026-08-29 放宽: 4 门中 ≥3 门通过 (单门失败不再否决) ----
    def test_turnover_out_of_range_single_gate_ok(self):
        # 仅换手率超上限, 量比/成交额/主力资金 3 门达标 → 3/4 通过
        passed, details = recommend.screen_capital(self._df(turnover=30.0), self.dm, self.latest, self.mflow_pos)
        self.assertEqual(set(passed["ts_code"]), {"600000.SH"})
        self.assertTrue(any("换手率" in g for g in details["600000.SH"]["fail_gates"]))

    def test_vol_ratio_too_low_single_gate_ok(self):
        # 仅量比≤1.1, 其余 3 门达标 → 3/4 通过
        passed, details = recommend.screen_capital(self._df(vol_ratio=1.0), self.dm, self.latest, self.mflow_pos)
        self.assertEqual(set(passed["ts_code"]), {"600000.SH"})
        self.assertTrue(any("量比" in g for g in details["600000.SH"]["fail_gates"]))

    def test_negative_inflow_single_gate_ok(self):
        # 仅主力资金3日净流入为负, 其余 3 门达标 → 3/4 通过 (不再一票否决)
        passed, details = recommend.screen_capital(self._df(), self.dm, self.latest, self.mflow_neg)
        self.assertEqual(set(passed["ts_code"]), {"600000.SH"})
        self.assertTrue(any("主力资金" in g for g in details["600000.SH"]["fail_gates"]))

    def test_two_gates_fail_reject(self):
        # 换手率超上限 + 量比不足 → 2/4 门 → 剔除
        passed, _ = recommend.screen_capital(self._df(turnover=30.0, vol_ratio=1.0),
                                             self.dm, self.latest, self.mflow_pos)
        self.assertTrue(passed.empty)

    def test_turnover_missing_single_gate_ok(self):
        # 换手率缺失 → 计失败但其余 3 门达标 → 3/4 通过 (不再硬杀)
        passed, details = recommend.screen_capital(self._df(turnover=None), self.dm, self.latest, self.mflow_pos)
        self.assertEqual(set(passed["ts_code"]), {"600000.SH"})
        self.assertIsNone(details["600000.SH"]["turnover"])
        self.assertTrue(any("换手率" in g for g in details["600000.SH"]["fail_gates"]))

    def test_removed_log_records_specific_gates(self):
        # 剔除记录含具体失败门 (而非统一文案)
        log = []
        recommend.screen_capital(self._df(turnover=30.0, vol_ratio=1.0), self.dm, self.latest,
                                 self.mflow_pos, removed_log=log)
        self.assertEqual(len(log), 1)
        self.assertIn("2/4门达标", log[0]["reason"])
        self.assertIn("换手率", log[0]["reason"])
        self.assertIn("量比", log[0]["reason"])

    def test_no_mflow_does_not_kill(self):
        passed, details = recommend.screen_capital(self._df(), self.dm, self.latest, {})
        self.assertEqual(set(passed["ts_code"]), {"600000.SH"})  # 资金数据缺失 → 3/4 通过
        self.assertIsNone(details["600000.SH"]["net_inflow_3d"])
        self.assertTrue(any("主力资金" in g for g in details["600000.SH"]["fail_gates"]))

    def test_vol_ratio_fallback_from_daily(self):
        # daily_basic 缺失(vol_ratio=None) → 用日线成交量兜底 (今日/5日均量=1.5 > 1.1)
        passed, details = recommend.screen_capital(self._df(vol_ratio=None), self.dm, self.latest, self.mflow_pos)
        self.assertEqual(set(passed["ts_code"]), {"600000.SH"})
        self.assertGreaterEqual(details["600000.SH"]["vol_ratio"], 1.1)

    # ---- 龙虎榜信号 (2026-08-23 数据源增强): 仅标注不否决 ----
    def test_lhb_sell_annotated_not_vetoed(self):
        # 龙虎榜净买额<0 (上榜出货) → 通过但标注 lhb_sell=True + 净买额/原因
        lhb = {"600000.SH": {"lhb_net": -7155484.55, "reason": "日跌幅偏离值达到7%的前5只证券"}}
        passed, details = recommend.screen_capital(self._df(), self.dm, self.latest, self.mflow_pos, lhb_map=lhb)
        self.assertEqual(set(passed["ts_code"]), {"600000.SH"})  # 不否决
        self.assertTrue(details["600000.SH"]["lhb_sell"])
        self.assertEqual(details["600000.SH"]["lhb_net"], -7155484.55)
        self.assertIn("日跌幅", details["600000.SH"]["lhb_reason"])

    def test_lhb_buy_annotated(self):
        # 龙虎榜净买额>0 (净买入) → lhb_sell=False, 保留净买额
        lhb = {"600000.SH": {"lhb_net": 402753375.14, "reason": "日涨幅偏离值达到7%的前5只证券"}}
        passed, details = recommend.screen_capital(self._df(), self.dm, self.latest, self.mflow_pos, lhb_map=lhb)
        self.assertEqual(set(passed["ts_code"]), {"600000.SH"})
        self.assertFalse(details["600000.SH"]["lhb_sell"])
        self.assertGreater(details["600000.SH"]["lhb_net"], 0)

    def test_lhb_missing_does_not_kill(self):
        # lhb_map 缺失/无该股 → 无 lhb 字段, 正常通过 (不误杀)
        passed, details = recommend.screen_capital(self._df(), self.dm, self.latest, self.mflow_pos)
        self.assertEqual(set(passed["ts_code"]), {"600000.SH"})
        self.assertNotIn("lhb_sell", details["600000.SH"])


class TestCalcTurnoverFallback(unittest.TestCase):
    """换手率兜底 (2026-08-29): 官方值缺失时用 成交量/流通股本 估算, 口径与 Tushare turnover_rate 一致"""

    def test_circ_mv_based(self):
        # vol=100000手(1千万股), price=10元, 流通市值=50亿(500000万) → 流通股本5亿股 → 换手2%
        self.assertEqual(recommend._calc_turnover_fallback(100000, 10.0, 500000, None), 2.0)

    def test_total_share_based(self):
        # vol=100000手, 总股本5亿(50000万股) → 换手2%
        self.assertEqual(recommend._calc_turnover_fallback(100000, None, None, 50000), 2.0)

    def test_circ_mv_preferred_over_total_share(self):
        # 两口径都有 → 优先流通市值
        self.assertEqual(recommend._calc_turnover_fallback(100000, 10.0, 500000, 25000), 2.0)

    def test_returns_rounded_float(self):
        self.assertEqual(recommend._calc_turnover_fallback(50000, 20.0, 250000, None), 4.0)

    def test_missing_all_returns_none(self):
        self.assertIsNone(recommend._calc_turnover_fallback(100000, None, None, None))

    def test_zero_or_missing_vol_returns_none(self):
        self.assertIsNone(recommend._calc_turnover_fallback(None, 10.0, 500000, None))
        self.assertIsNone(recommend._calc_turnover_fallback(0, 10.0, 500000, None))

    def test_zero_circ_mv_falls_to_total_share(self):
        self.assertEqual(recommend._calc_turnover_fallback(100000, 10.0, 0, 50000), 2.0)


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
        # v3.3: 板块满分 30 (1档=15, 2档=22, ≥3档=30)
        self.assertEqual(recommend.score_sector_funnel(0), 0)
        self.assertEqual(recommend.score_sector_funnel(1), 15)
        self.assertEqual(recommend.score_sector_funnel(2), 22)
        self.assertEqual(recommend.score_sector_funnel(3), 30)
        self.assertEqual(recommend.score_sector_funnel(5), 30)

    def test_technical_funnel(self):
        # v3.3: 技术满分 15 (达标数×5, ≥3项即满分)
        self.assertEqual(recommend.score_technical_funnel(0), 0)
        self.assertEqual(recommend.score_technical_funnel(1), 5)
        self.assertEqual(recommend.score_technical_funnel(2), 10)
        self.assertEqual(recommend.score_technical_funnel(3), 15)  # 15 → 上限15
        self.assertEqual(recommend.score_technical_funnel(7), 15)

    def test_fundamental_funnel(self):
        import pandas as pd
        s = pd.Series({"pe_ttm": 20.0, "circ_mv": 100 * 10000})  # 100亿
        fina = {"net_profit": 1e8, "debt_to_assets": 40}
        # v3.3: 净利4 + PE4 + 市值4 + 负债3 = 15
        self.assertEqual(recommend.score_fundamental_funnel(s, fina), 15)
        # 全缺失 → 0分(不淘汰)
        s0 = pd.Series({"pe_ttm": float("nan"), "circ_mv": float("nan")})
        self.assertEqual(recommend.score_fundamental_funnel(s0, None), 0)

    def test_capital_funnel(self):
        # v3.3: 资金满分 40 (v2.0 梯度制: 换手20 + 量比20)
        cap_best = {"turnover": 7.0, "vol_ratio": 2.0}  # 6-8% + 1.5-2.5 → 20+20
        self.assertEqual(recommend.score_capital_funnel(cap_best), 40)
        cap_mid = {"turnover": 5.0, "vol_ratio": 1.4}  # 4-6% + 1.3-1.5 → 15+15
        self.assertEqual(recommend.score_capital_funnel(cap_mid), 30)
        cap_high = {"turnover": 11.0, "vol_ratio": 4.0}  # 10-12% + ≥3.5 → 10+5
        self.assertEqual(recommend.score_capital_funnel(cap_high), 15)
        cap0 = {"turnover": 3.0, "vol_ratio": 1.0}  # 区间外 → 0
        self.assertEqual(recommend.score_capital_funnel(cap0), 0)
        self.assertEqual(recommend.score_capital_funnel({}), 0)

    def test_overbought_deduct_keys_in_technical(self):
        # 回归保护: 超买扣分键必须存在于 technical 配置块 (评分循环从该块读取)
        t = recommend.CONFIG["technical"]
        for k in ("overbought_deduct", "overbought_deduct_both"):
            self.assertIn(k, t, f"CONFIG['technical'] 缺少 {k}")


# ============================================================
# 盈利质量评分 score_quality_funnel (2026-08-23, financial-modeling/earnings-interpretation)
# ============================================================

class TestScoreQualityFunnel(unittest.TestCase):
    def test_quality_config_keys_present(self):
        # 回归保护: 评分循环从 CONFIG["score"]["quality"] 读取阈值
        q = recommend.CONFIG["score"]["quality"]
        for k in ("bonus_cap", "revenue_yoy_pos", "gross_margin_high", "roe_high",
                  "ocf_pos", "roic_pos", "ocf_neg_deduct", "margin_gap_deduct", "margin_gap_max"):
            self.assertIn(k, q, f"CONFIG['score']['quality'] 缺少 {k}")

    def test_quality_bonus_capped(self):
        # 满分项 7 → 上限 5
        fina = {"revenue_yoy": 35.0, "gross_margin": 45.0, "roe": 18.0,
                "ocf_ps": 1.2, "roic": 10.0, "net_profit": 1e8}
        bonus, deduct, tags = recommend.score_quality_funnel(fina)
        self.assertEqual(bonus, 5)
        self.assertEqual(deduct, 0)
        self.assertEqual(tags, [])

    def test_quality_bonus_gradient(self):
        # 营收正增2 + 中毛利1 + 中ROE1 + 现金流2 + ROIC1 = 7 → 上限5
        fina = {"revenue_yoy": 10.0, "gross_margin": 25.0, "roe": 10.0,
                "ocf_ps": 0.5, "roic": 9.0, "net_profit": 1e8}
        bonus, _, _ = recommend.score_quality_funnel(fina)
        self.assertEqual(bonus, 5)

    def test_quality_bonus_minimal(self):
        # 仅营收正增长 → 2
        fina = {"revenue_yoy": 5.0}
        bonus, _, _ = recommend.score_quality_funnel(fina)
        self.assertEqual(bonus, 2)

    def test_quality_missing_no_punish(self):
        # 全缺失 → (0,0,[]) (缺失跳过, 不误杀)
        self.assertEqual(recommend.score_quality_funnel({}), (0, 0, []))
        self.assertEqual(recommend.score_quality_funnel(None), (0, 0, []))

    def test_quality_ocf_negative_deduct(self):
        # 经营现金流为负 且 净利>0 → 扣2 + 标签【现金流为负】
        fina = {"revenue_yoy": 10.0, "ocf_ps": -0.3, "net_profit": 1e8}
        bonus, deduct, tags = recommend.score_quality_funnel(fina)
        self.assertEqual(deduct, 2)
        self.assertIn("现金流为负", tags)
        self.assertGreaterEqual(bonus, 2)  # 营收正增仍加分 (加减独立)

    def test_quality_margin_gap_deduct(self):
        # 净利增速-营收增速 > 40pct → 扣1 + 标签【利润质量存疑】
        fina = {"revenue_yoy": 5.0, "netprofit_yoy": 60.0, "net_profit": 1e8}
        bonus, deduct, tags = recommend.score_quality_funnel(fina)
        self.assertEqual(deduct, 1)
        self.assertIn("利润质量存疑", tags)

    def test_quality_gap_normal_no_deduct(self):
        # 增速匹配 (差值≤40pct) → 不扣分无标签
        fina = {"revenue_yoy": 30.0, "netprofit_yoy": 40.0, "net_profit": 1e8}
        _, deduct, tags = recommend.score_quality_funnel(fina)
        self.assertEqual(deduct, 0)
        self.assertEqual(tags, [])


# ============================================================
# 业绩预告信号 fetch_yjyg (2026-08-23, earnings-interpretation 预期差)
# ============================================================

class TestFetchYjyg(unittest.TestCase):
    def test_yjyg_tag_positive(self):
        self.assertEqual(recommend._yjyg_tag("预增"), (True, "业绩预增"))
        self.assertEqual(recommend._yjyg_tag("略增"), (True, "业绩预增"))
        self.assertEqual(recommend._yjyg_tag("续盈"), (True, "业绩预增"))
        self.assertEqual(recommend._yjyg_tag("扭亏"), (True, "扭亏为盈"))
        self.assertEqual(recommend._yjyg_tag("减亏"), (True, "业绩预增"))

    def test_yjyg_tag_negative(self):
        self.assertEqual(recommend._yjyg_tag("预减"), (False, "业绩预减"))
        self.assertEqual(recommend._yjyg_tag("略减"), (False, "业绩预减"))
        self.assertEqual(recommend._yjyg_tag("续亏"), (False, "业绩预减"))
        self.assertEqual(recommend._yjyg_tag("首亏"), (False, "首亏预警"))
        self.assertEqual(recommend._yjyg_tag("增亏"), (False, "业绩预减"))

    def test_yjyg_tag_neutral(self):
        self.assertEqual(recommend._yjyg_tag("不确定"), (None, ""))

    def test_fetch_yjyg_parses(self):
        rows = [
            {"SECUCODE": "000001.SZ", "PREDICT_TYPE": "预增", "PREDICT_RATIO_LOWER": 20.0,
             "PREDICT_RATIO_UPPER": 40.0, "REPORT_DATE": "2026-06-30"},
            {"SECUCODE": "600519.SH", "PREDICT_TYPE": "首亏", "PREDICT_RATIO_LOWER": -50.0,
             "PREDICT_RATIO_UPPER": -20.0, "REPORT_DATE": "2026-06-30"},
            {"SECUCODE": "000002.SZ", "PREDICT_TYPE": "不确定", "PREDICT_RATIO_LOWER": None,
             "PREDICT_RATIO_UPPER": None, "REPORT_DATE": "2026-06-30"},
        ]
        orig = recommend._em_get_json
        calls = {"urls": []}

        def fake(url, timeout=15):
            calls["urls"].append(url)
            return {"result": {"data": rows}}
        recommend._em_get_json = fake
        try:
            result = recommend.fetch_yjyg("20260821")
        finally:
            recommend._em_get_json = orig
        self.assertIn("2026-08-21", calls["urls"][0])  # NOTICE_DATE 过滤当日
        self.assertIn("IS_LATEST", calls["urls"][0])
        self.assertEqual(set(result.keys()), {"000001.SZ", "600519.SH"})  # 不确定被忽略
        self.assertTrue(result["000001.SZ"]["positive"])
        self.assertEqual(result["000001.SZ"]["ratio"], 30.0)  # (20+40)/2
        self.assertEqual(result["000001.SZ"]["tag"], "业绩预增")
        self.assertFalse(result["600519.SH"]["positive"])
        self.assertEqual(result["600519.SH"]["tag"], "首亏预警")

    def test_fetch_yjyg_exception_returns_empty(self):
        orig = recommend._em_get_json

        def fake(url, timeout=15):
            raise Exception("boom")
        recommend._em_get_json = fake
        try:
            result = recommend.fetch_yjyg("20260821")
        finally:
            recommend._em_get_json = orig
        self.assertEqual(result, {})

    def test_fetch_yjyg_empty_returns_empty(self):
        orig = recommend._em_get_json

        def fake(url, timeout=15):
            return {"result": {"data": []}}
        recommend._em_get_json = fake
        try:
            result = recommend.fetch_yjyg("20260821")
        finally:
            recommend._em_get_json = orig
        self.assertEqual(result, {})


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

    def test_fina_f10_quality_fields(self):
        # 盈利质量扩展字段 (2026-08-23): 营收/毛利率/ROE/ROIC/现金流/BPS 解析
        row = {"SECUCODE": "600519.SH", "REPORT_DATE": "2026-06-30 00:00:00", "ZCFZL": "15.19",
               "PARENTNETPROFIT": "44500000000", "PARENTNETPROFITTZ": "15.5",
               "KCFJCXSYJLR": "44300000000", "KCFJCXSYJLRTZ": "16.1",
               "TOTALOPERATEREVE": "80000000000", "TOTALOPERATEREVETZ": "12.3",
               "XSMLL": "91.5", "XSJLL": "55.0", "ROEJQ": "28.0", "ROIC": "25.0",
               "MGJYXJJE": "18.5", "BPS": "120.0"}
        orig = recommend._em_get_json
        calls = {"urls": []}

        def fake(url, timeout=20):
            calls["urls"].append(url)
            if "2026-06-30" in url:
                return {"result": {"data": [row]}}
            return {"result": {"data": []}}
        recommend._em_get_json = fake
        try:
            result = recommend.fetch_fina_f10_em("20260814", codes={"600519.SH"})
        finally:
            recommend._em_get_json = orig
        self.assertEqual(set(result.keys()), {"600519.SH"})
        mt = result["600519.SH"]
        self.assertEqual(mt["total_revenue"], 80000000000.0)
        self.assertEqual(mt["revenue_yoy"], 12.3)
        self.assertEqual(mt["gross_margin"], 91.5)
        self.assertEqual(mt["net_margin"], 55.0)
        self.assertEqual(mt["roe"], 28.0)
        self.assertEqual(mt["roic"], 25.0)
        self.assertEqual(mt["ocf_ps"], 18.5)
        self.assertEqual(mt["bps"], 120.0)
        # columns 请求必须携带新增质量字段
        self.assertIn("XSMLL", calls["urls"][0])
        self.assertIn("TOTALOPERATEREVE", calls["urls"][0])
        self.assertIn("MGJYXJJE", calls["urls"][0])

    def test_fina_f10_missing_quality_fields_none(self):
        # 旧 mock 行(无质量字段) → 新字段为 None, 不报错 (缺失跳过, 不误杀)
        row = {"SECUCODE": "600519.SH", "REPORT_DATE": "2026-06-30 00:00:00", "ZCFZL": "15.19",
               "PARENTNETPROFIT": "44500000000", "PARENTNETPROFITTZ": "15.5",
               "KCFJCXSYJLR": "44300000000", "KCFJCXSYJLRTZ": "16.1"}
        orig = recommend._em_get_json

        def fake(url, timeout=20):
            if "2026-06-30" in url:
                return {"result": {"data": [row]}}
            return {"result": {"data": []}}
        recommend._em_get_json = fake
        try:
            result = recommend.fetch_fina_f10_em("20260814", codes={"600519.SH"})
        finally:
            recommend._em_get_json = orig
        mt = result["600519.SH"]
        self.assertIsNone(mt["gross_margin"])
        self.assertIsNone(mt["roe"])
        self.assertIsNone(mt["ocf_ps"])

    def test_fina_cache_old_without_quality_rebuilds(self):
        # 回归保护 (2026-08-23): 旧缓存缺 gross_margin 等质量字段 → 判定不 fresh → 强制重建
        # 场景: 服务器 8/21 现场缓存 (旧字段), 新代码加入质量分后若不校验该键,
        #        同交易日重跑会命中旧缓存 → score_quality 恒为 0
        import json as _json
        import tempfile
        import os as _os
        from pathlib import Path as _P
        fd, tmp = tempfile.mkstemp(suffix=".json")
        _os.close(fd)
        tmp_path = _P(tmp)
        old = {"_fetched_date": "20260821", "600519.SH": {"end_date": "20260630",
               "net_profit": 1.0, "netprofit_yoy": 2.0, "debt_to_assets": 15.0}}
        with open(tmp_path, "w", encoding="utf-8") as f:
            _json.dump(old, f)
        orig_cache = recommend.FINA_CACHE_FILE
        recommend.FINA_CACHE_FILE = tmp_path
        orig_pool = recommend.pool.call_any
        orig_f10 = recommend.fetch_fina_f10_em
        # Tushare fina_indicator 不可用(服务器现场), 东财F10 提供含质量字段的新数据
        recommend.pool.call_any = lambda *a, **k: (_ for _ in ()).throw(Exception("您没有接口"))
        recommend.fetch_fina_f10_em = lambda *a, **k: {"600519.SH": {
            "end_date": "20260630", "net_profit": 1.0, "netprofit_yoy": 2.0,
            "gross_margin": 91.5, "roe": 28.0, "ocf_ps": 18.5}}
        try:
            result = recommend.fetch_fina_indicator_batch("20260821", codes={"600519.SH"})
        finally:
            recommend.FINA_CACHE_FILE = orig_cache
            recommend.pool.call_any = orig_pool
            recommend.fetch_fina_f10_em = orig_f10
            _os.remove(tmp_path)
        # 旧缓存被弃用, 新数据含质量字段
        self.assertEqual(result["600519.SH"]["gross_margin"], 91.5)
        self.assertEqual(result["600519.SH"]["roe"], 28.0)

    def test_fina_cache_fresh_with_quality_reused(self):
        # 新格式缓存(含 gross_margin)且 _fetched_date=最新交易日 → fresh → 直接复用
        import json as _json
        import tempfile
        import os as _os
        from pathlib import Path as _P
        fd, tmp = tempfile.mkstemp(suffix=".json")
        _os.close(fd)
        tmp_path = _P(tmp)
        new = {"_fetched_date": "20260821",
               "600519.SH": {"end_date": "20260630", "net_profit": 1.0, "netprofit_yoy": 2.0,
                             "gross_margin": 91.5, "roe": 28.0, "ocf_ps": 18.5}}
        with open(tmp_path, "w", encoding="utf-8") as f:
            _json.dump(new, f)
        orig_cache = recommend.FINA_CACHE_FILE
        recommend.FINA_CACHE_FILE = tmp_path
        orig_pool = recommend.pool.call_any
        recommend.pool.call_any = lambda *a, **k: (_ for _ in ()).throw(Exception("不应调用接口"))
        try:
            result = recommend.fetch_fina_indicator_batch("20260821", codes={"600519.SH"})
        finally:
            recommend.FINA_CACHE_FILE = orig_cache
            recommend.pool.call_any = orig_pool
            _os.remove(tmp_path)
        # 直接复用, 未触发任何网络调用
        self.assertEqual(result["600519.SH"]["gross_margin"], 91.5)
        self.assertEqual(result["600519.SH"]["roe"], 28.0)


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
# 北向广度接入 check_market_env (仅提示不否决, 2026-08-23)
# ============================================================

class TestCheckMarketEnvHsgtBreadth(unittest.TestCase):
    def test_hsgt_weak_hint_not_veto(self):
        # 北向成分股上涨占比 30% < 40% → note 附加"北向广度偏弱", 但不否决
        idx = make_index_map([3000 + i * 5 for i in range(30)])
        hsgt = {"north_up": 300, "north_down": 700, "north_ratio": 0.30}
        ok, status = recommend.check_market_env(idx, {}, "20260807", hsgt_breadth=hsgt)
        self.assertTrue(ok)
        self.assertEqual(status["hsgt_breadth"]["ratio"], 0.30)
        self.assertEqual(status["hsgt_breadth"]["up"], 300)
        self.assertEqual(status["hsgt_breadth"]["down"], 700)
        self.assertIn("北向广度偏弱", status["note"])

    def test_hsgt_normal_no_hint(self):
        # 北向成分股上涨占比 55% ≥ 40% → 记录字段但不加提示
        idx = make_index_map([3000 + i * 5 for i in range(30)])
        hsgt = {"north_up": 550, "north_down": 450, "north_ratio": 0.55}
        ok, status = recommend.check_market_env(idx, {}, "20260807", hsgt_breadth=hsgt)
        self.assertTrue(ok)
        self.assertEqual(status["hsgt_breadth"]["ratio"], 0.55)
        self.assertNotIn("北向广度偏弱", status["note"])

    def test_hsgt_absent(self):
        # hsgt_breadth 缺失 → 字段为 None, 无提示, 不否决
        idx = make_index_map([3000 + i * 5 for i in range(30)])
        ok, status = recommend.check_market_env(idx, {}, "20260807")
        self.assertTrue(ok)
        self.assertIsNone(status["hsgt_breadth"])
        self.assertNotIn("北向广度", status["note"])


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


class TestChaseDeducts(unittest.TestCase):
    """短期追高抑制扣分 (日度/早期共用, 保证两套评分口径一致)"""

    def _df(self, closes):
        import pandas as pd
        return pd.DataFrame({"date": [str(i) for i in range(len(closes))],
                             "close": [float(c) for c in closes]})

    def _cfg(self):
        return recommend.CONFIG["score"]

    def test_gain5_high(self):
        # 近5日涨幅 30% > 12% → 扣 gain5_deduct
        df = self._df([10.0] * 5 + [13.0])
        r = recommend._chase_deducts("x", {"pct_chg": 1.0}, df, self._cfg())
        self.assertEqual(r["deduct"], recommend.CONFIG["score"]["gain5_deduct"])
        self.assertTrue(r["flags"]["gain5_high"])
        self.assertIn("短期涨幅过高", r["tags"])

    def test_high_pos(self):
        # 当天涨8% (7≤8<9.8) 未涨停 → 扣 high_pos_deduct
        r = recommend._chase_deducts("x", {"pct_chg": 8.0}, self._df([10.0] * 25), self._cfg())
        self.assertEqual(r["deduct"], recommend.CONFIG["score"]["high_pos_deduct"])
        self.assertTrue(r["flags"]["high_pos"])
        self.assertIn("高位追涨", r["tags"])
        # 涨停(≥9.8)不触发 (涨停无量豁免)
        r2 = recommend._chase_deducts("x", {"pct_chg": 9.9}, self._df([10.0] * 25), self._cfg())
        self.assertFalse(r2["flags"]["high_pos"])

    def test_ma20_dev_high(self):
        # 前期横盘10元, 近6日缓涨至14.5 → 乖离MA20≈29%>25% 但近5日涨幅仅6.6%<12%
        closes = [10.0] * 14 + [13.6, 13.8, 14.0, 14.2, 14.4, 14.5]
        df = self._df(closes)
        r = recommend._chase_deducts("x", {"pct_chg": 1.0}, df, self._cfg())
        self.assertEqual(r["deduct"], recommend.CONFIG["score"]["ma20_dev_deduct"])
        self.assertFalse(r["flags"]["gain5_high"])  # 仅乖离触发
        self.assertTrue(r["flags"]["dev_high"])
        self.assertIn("乖离过大", r["tags"])
        self.assertGreater(r["ma20_dev"], 25)

    def test_no_deduct_normal(self):
        # 温和上行 + 小幅当日涨幅 → 无扣分
        closes = [10.0 + i * 0.05 for i in range(30)]
        r = recommend._chase_deducts("x", {"pct_chg": 1.0}, self._df(closes), self._cfg())
        self.assertEqual(r["deduct"], 0)
        self.assertEqual(r["tags"], [])
        self.assertFalse(any(r["flags"].values()))

    def test_combined_deducts(self):
        # 三项同时触发 → 扣分叠加
        df = self._df([10.0] * 19 + [14.0])  # ma20_dev≈34% + 近5日涨幅40%
        r = recommend._chase_deducts("x", {"pct_chg": 8.0}, df, self._cfg())
        sc = recommend.CONFIG["score"]
        self.assertEqual(r["deduct"], sc["gain5_deduct"] + sc["high_pos_deduct"] + sc["ma20_dev_deduct"])
        self.assertEqual(len(r["tags"]), 3)

    def test_insufficient_data(self):
        # 历史数据不足(<6天) → chg5/ma20_dev 均无法计算; 当日涨幅温和 → 无扣分
        r = recommend._chase_deducts("x", {"pct_chg": 1.0}, self._df([10.0, 10.1]), self._cfg())
        self.assertEqual(r["deduct"], 0)
        self.assertIsNone(r["chg5"])
        self.assertIsNone(r["ma20_dev"])
        # 但当日大涨未涨停的扣分不依赖历史数据, 数据不足时仍触发
        r2 = recommend._chase_deducts("x", {"pct_chg": 8.0}, self._df([10.0, 10.1]), self._cfg())
        self.assertEqual(r2["deduct"], recommend.CONFIG["score"]["high_pos_deduct"])


class TestPredictionCorrectionConfig(unittest.TestCase):
    """回归保护: 预测修正 CONFIG 键必须存在 (评分循环/特征提取依赖)"""

    def test_score_keys_present(self):
        sc = recommend.CONFIG["score"]
        for k in ("gain5_threshold", "gain5_deduct", "high_pos_pct",
                  "high_pos_deduct", "ma20_dev_threshold", "ma20_dev_deduct",
                  "sentiment_cap"):
            self.assertIn(k, sc, f"CONFIG['score'] 缺少 {k}")
        # 追高阈值收紧: 近5日涨幅 >12% 即触发 (原25)
        self.assertLessEqual(sc["gain5_threshold"], 12.0)
        self.assertGreaterEqual(sc["gain5_deduct"], 12)

    def test_sentiment_cap_defaults(self):
        cap = recommend.CONFIG["score"]["sentiment_cap"]
        self.assertEqual(cap["advance_ratio_min"], 0.5)
        self.assertEqual(cap["keep_ratio"], 0.5)
        self.assertGreaterEqual(cap["keep_min"], 3)


class TestSentimentCap(unittest.TestCase):
    """情绪联动: 大盘上涨占比不足且指数未全面站稳 → 缩减推荐数量"""

    def _recs(self, n=6):
        return [{"ts_code": "%06d.SH" % (600000 + i), "score_total": 90 - i}
                for i in range(n)]

    def test_weak_market_capped(self):
        shown, capped = recommend._sentiment_cap_shown(
            self._recs(), {"advance_ratio": 0.45,
                           "indexes": [{"above": True}, {"above": False}]})
        self.assertTrue(capped)
        self.assertEqual(len(shown), 3)  # 6*0.5=3, 保留最强的

    def test_strong_advance_no_cap(self):
        shown, capped = recommend._sentiment_cap_shown(
            self._recs(), {"advance_ratio": 0.55, "indexes": [{"above": False}]})
        self.assertFalse(capped)
        self.assertEqual(len(shown), 6)

    def test_indexes_all_above_no_cap(self):
        """上涨占比不足但指数全面站稳 → 不缩减"""
        shown, capped = recommend._sentiment_cap_shown(
            self._recs(), {"advance_ratio": 0.45,
                           "indexes": [{"above": True}, {"above": True}]})
        self.assertFalse(capped)

    def test_keep_min_floor(self):
        """触发时至少保留 keep_min 只; 数量不足不触发"""
        shown, capped = recommend._sentiment_cap_shown(
            self._recs(3), {"advance_ratio": 0.30, "indexes": [{"above": False}]})
        self.assertFalse(capped)  # len(3) <= keep_min(3)
        shown, capped = recommend._sentiment_cap_shown(
            self._recs(8), {"advance_ratio": 0.30, "indexes": [{"above": False}]})
        self.assertTrue(capped)
        self.assertEqual(len(shown), 4)  # 8*0.5=4

    def test_no_market_status_no_cap(self):
        shown, capped = recommend._sentiment_cap_shown(self._recs(), None)
        self.assertFalse(capped)


class TestPredFeaturesMa20Dev(unittest.TestCase):
    """回归: _pred_features 必须输出 ma20_dev 特征 (位置风险约束依赖)"""

    def test_ma20_dev_in_features(self):
        import pandas as pd
        closes = [10.0 + i * 0.1 for i in range(25)]  # 末值 12.4
        df = pd.DataFrame({"date": [str(i) for i in range(25)],
                           "close": closes, "low": [9.0] * 25})
        s = {"ts_code": "600000.SH", "sector_count": 1, "sector_names": ["半导体"],
             "pct_chg": 2.0, "price": 12.4, "pe_ttm": 20.0,
             "vol_ratio": 1.5, "turnover_rate": 8.0}
        cap = {"vol_ratio": 1.5, "turnover": 8.0, "net_inflow_3d": 1e7}
        tech = {"hits": ["均线多头"], "hit_count": 1}
        f = recommend._pred_features("600000.SH", s, cap, tech,
                                     {"600000.SH": 85}, {"600000.SH": df},
                                     {"半导体": 3}, {"advance_ratio": 0.55})
        self.assertIn("ma20_dev", f)
        self.assertIsNotNone(f["ma20_dev"])
        ma20 = sum(closes[-20:]) / 20
        self.assertAlmostEqual(f["ma20_dev"], (12.4 / ma20 - 1) * 100, places=4)

    def test_ma20_dev_none_with_short_data(self):
        import pandas as pd
        df = pd.DataFrame({"date": ["0", "1", "2"],
                           "close": [10.0, 10.1, 10.2], "low": [9.9, 9.8, 9.9]})
        s = {"ts_code": "600000.SH", "sector_count": 1, "sector_names": [],
             "pct_chg": 1.0, "price": 10.2, "pe_ttm": 20.0}
        f = recommend._pred_features("600000.SH", s, {}, {"hits": [], "hit_count": 0},
                                     {}, {"600000.SH": df}, {}, {"advance_ratio": 0.55})
        self.assertIn("ma20_dev", f)
        self.assertIsNone(f["ma20_dev"])


class TestTrendRegimeFlags(unittest.TestCase):
    """精准度增强(2026-08-18): 趋势/位置形态研判
    三态: 反弹未反转(60日区间跌) / 超跌反弹(距60日高点回撤>30%) / 贴近新高(回撤≤15%且60日涨)
    """

    def _df(self, closes, high_mult=1.02):
        import pandas as pd
        closes = [float(c) for c in closes]
        return {"TEST.SH": pd.DataFrame({
            "date": [str(i) for i in range(len(closes))],
            "close": closes,
            "high": [c * high_mult for c in closes],
        })}

    def test_rebound_not_reversed(self):
        # 61根: 10 → 9.4 温和阴跌 (60日区间 -6%), 高点回撤仅 -10.5% → 只标反弹未反转
        closes = [10.0] * 30 + [9.8 - i * 0.02 for i in range(31)]
        out = recommend.trend_regime_flags("TEST.SH", {}, self._df(closes, high_mult=1.05))
        self.assertTrue(out["flags"]["rebound"])
        self.assertFalse(out["flags"]["retreat"])
        self.assertFalse(out["flags"]["near_high"])
        self.assertIn("反弹未反转", out["tags"])
        self.assertLess(out["chg60"], 0)
        self.assertEqual(out["regime"], "rebound")

    def test_retreat_oversold(self):
        # 拉升到15后回落到10.5: 60日区间 +5% 但距高点回撤 -31.4% → 只标超跌反弹
        closes = [10.0] * 40 + [11, 12, 13, 14, 15] + [10.5] * 16
        out = recommend.trend_regime_flags("TEST.SH", {}, self._df(closes))
        self.assertTrue(out["flags"]["retreat"])
        self.assertFalse(out["flags"]["rebound"])
        self.assertFalse(out["flags"]["near_high"])
        self.assertIn("超跌反弹", out["tags"])
        self.assertLessEqual(out["retreat_pct"], -30)
        self.assertEqual(out["regime"], "retreat")

    def test_near_high(self):
        # 10 → 19 单边慢牛 (60日 +90%), 距最高点仅 -2% → 贴近新高
        closes = [10.0 + i * 0.15 for i in range(61)]
        out = recommend.trend_regime_flags("TEST.SH", {}, self._df(closes))
        self.assertTrue(out["flags"]["near_high"])
        self.assertFalse(out["flags"]["rebound"])
        self.assertFalse(out["flags"]["retreat"])
        self.assertIn("贴近新高", out["tags"])
        self.assertGreater(out["chg60"], 0)

    def test_near_high_requires_positive_chg60(self):
        # 回撤≤15% 但60日区间为负 (下跌途中小反弹) → 不标贴近新高, 只标反弹未反转
        closes = [12.0] * 58 + [11.5, 11.2, 11.0]
        out = recommend.trend_regime_flags("TEST.SH", {}, self._df(closes))
        self.assertTrue(out["flags"]["rebound"])
        self.assertFalse(out["flags"]["near_high"])
        self.assertGreaterEqual(out["retreat_pct"], -15)

    def test_insufficient_data(self):
        out = recommend.trend_regime_flags("TEST.SH", {}, self._df([10.0] * 10))
        self.assertIsNone(out["chg60"])
        self.assertIsNone(out["retreat_pct"])
        self.assertFalse(any(out["flags"].values()))
        self.assertEqual(out["tags"], [])

    def test_none_df(self):
        out = recommend.trend_regime_flags("TEST.SH", {}, None)
        self.assertIsNone(out["regime"])
        self.assertEqual(out["tags"], [])


class TestPeHighFlag(unittest.TestCase):
    """高估值降权: PE-TTM > 100 才判定为高估值"""

    def test_pe_high(self):
        self.assertTrue(recommend.pe_high_flag({"pe_ttm": 101.0}))

    def test_pe_below_threshold(self):
        self.assertFalse(recommend.pe_high_flag({"pe_ttm": 99.0}))

    def test_pe_none(self):
        self.assertFalse(recommend.pe_high_flag({"pe_ttm": None}))
        self.assertFalse(recommend.pe_high_flag({}))

    def test_pe_na(self):
        import pandas as pd
        self.assertFalse(recommend.pe_high_flag({"pe_ttm": pd.NA}))

    def test_pe_negative(self):
        # 亏损/负PE 不归为"高估值" (基本面条已处理)
        self.assertFalse(recommend.pe_high_flag({"pe_ttm": -5.0}))


class TestIndustryConcentration(unittest.TestCase):
    """精准度增强: 输出 top1 行业集中度 (日度+早期双列表同口径)"""

    def _rec(self, industry, name):
        return {"ts_code": "600000.SH", "name": name, "industry": industry,
                "sectors": [], "sector_count": 1, "tags": "", "price": 10.0,
                "pct_chg": 1.0, "pe_ttm": 15.0, "circ_mv": 50.0,
                "score_total": 80, "score_sector": 20, "score_fundamental": 15,
                "score_capital": 20, "score_technical": 20,
                "fundamental": {}, "capital": {}, "technical": {}}

    def _save_daily(self, rec_list):
        import json, tempfile, os as _os
        rec_mod = recommend
        rec_mod._LAST_MARKET_STATUS = None
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
            if _os.path.exists(tmp):
                _os.remove(tmp)

    def _save_early(self, rec_list):
        import json, tempfile, os as _os
        rec_mod = recommend
        rec_mod._LAST_EARLY_MARKET_STATUS = None
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
            if _os.path.exists(tmp):
                _os.remove(tmp)

    def test_daily_top_industry(self):
        rec_list = [self._rec("半导体", "股1"), self._rec("半导体", "股2"), self._rec("银行", "股3")]
        ic = self._save_daily(rec_list)["industry_concentration"]
        self.assertEqual(ic["top_industry"], "半导体")
        self.assertEqual(ic["top_count"], 2)
        self.assertEqual(ic["top_ratio"], round(2 / 3, 2))

    def test_early_top_industry(self):
        rec_list = [self._rec("半导体", "股1"), self._rec("银行", "股2")]
        ic = self._save_early(rec_list)["industry_concentration"]
        self.assertEqual(ic["top_industry"], "半导体")
        self.assertEqual(ic["top_ratio"], 0.5)

    def test_empty_list_empty_dict(self):
        self.assertEqual(self._save_daily([])["industry_concentration"], {})
        self.assertEqual(self._save_early([])["industry_concentration"], {})

    def test_missing_industry_unknown(self):
        rec = self._rec(None, "股1")
        ic = self._save_early([rec, rec])["industry_concentration"]
        self.assertEqual(ic["top_industry"], "未知")
        self.assertEqual(ic["top_ratio"], 1.0)


# ============================================================
# 消息面信号 (2026-08-23, 金融新闻 + 主力/散户真实消息)
# ============================================================

class TestNewsSourceLevel(unittest.TestCase):
    def test_official_source_media(self):
        self.assertEqual(recommend._news_source_level("中国证券报", "某公司中标大单"), "official")
        self.assertEqual(recommend._news_source_level("财联社", "某公司业绩预增"), "official")
        self.assertEqual(recommend._news_source_level("新浪财经", "某公司签订合同"), "official")

    def test_official_announcement_feature(self):
        # 标题含公告/披露特征 → 即便来源普通媒体也算官方 (内容即公司披露)
        self.assertEqual(recommend._news_source_level("综合", "某公司发布年度业绩公告"), "official")
        self.assertEqual(recommend._news_source_level("", "某公司半年度业绩说明会"), "official")

    def test_rumor_source(self):
        # 股吧/雪球等散户平台 → rumor (小道消息, 不采信)
        self.assertEqual(recommend._news_source_level("东方财富股吧", "某公司重大利好传闻"), "rumor")
        self.assertEqual(recommend._news_source_level("雪球", "某公司要重组了"), "rumor")

    def test_neutral_other(self):
        self.assertEqual(recommend._news_source_level("某某自媒体", "随便聊聊市场"), "neutral")
        self.assertEqual(recommend._news_source_level("", ""), "neutral")


class TestNewsSentiment(unittest.TestCase):
    def test_bull_kw(self):
        self.assertEqual(recommend._news_sentiment("公司中标1.2亿订单"), "pos")
        self.assertEqual(recommend._news_sentiment("控股股东增持500万股"), "pos")
        self.assertEqual(recommend._news_sentiment("前三季度业绩预增50%"), "pos")

    def test_bear_kw(self):
        self.assertEqual(recommend._news_sentiment("股东拟减持不超过2%"), "neg")
        self.assertEqual(recommend._news_sentiment("收到证监会立案调查通知"), "neg")
        self.assertEqual(recommend._news_sentiment("公司预计首亏"), "neg")

    def test_neutral_reverse(self):
        # 反向表述: 解除质押/取消减持 → 中性 (不误判利空)
        self.assertEqual(recommend._news_sentiment("控股股东解除质押公告"), None)
        self.assertEqual(recommend._news_sentiment("控股股东取消减持计划"), None)

    def test_no_kw(self):
        self.assertEqual(recommend._news_sentiment("公司参加行业展会"), None)
        self.assertEqual(recommend._news_sentiment(""), None)


class TestNewsWindow(unittest.TestCase):
    def test_within_window(self):
        self.assertTrue(recommend._news_in_window("2026-08-20 10:00:00", "20260821", 7))

    def test_today(self):
        self.assertTrue(recommend._news_in_window("2026-08-21 09:00:00", "20260821", 7))

    def test_too_old(self):
        self.assertFalse(recommend._news_in_window("2026-08-01 10:00:00", "20260821", 7))

    def test_future(self):
        self.assertFalse(recommend._news_in_window("2026-08-22 10:00:00", "20260821", 7))

    def test_invalid(self):
        self.assertFalse(recommend._news_in_window("", "20260821", 7))


class TestScoreNewsFunnel(unittest.TestCase):
    def test_news_config_keys_present(self):
        # 回归保护: 评分循环从 CONFIG["score"]["news"] 读取阈值
        n = recommend.CONFIG["score"]["news"]
        for k in ("bonus", "deduct", "window_days", "max_fetch", "hot_rank_top", "dzjy_discount_max"):
            self.assertIn(k, n, f"CONFIG['score']['news'] 缺少 {k}")

    def test_official_pos_bonus(self):
        nb, nd, tags = recommend.score_news_funnel({"official_pos": 2, "official_neg": 0})
        self.assertEqual(nb, 2)
        self.assertEqual(nd, 0)
        self.assertIn("利好消息", tags)

    def test_official_neg_deduct(self):
        nb, nd, tags = recommend.score_news_funnel({"official_neg": 1})
        self.assertEqual(nb, 0)
        self.assertEqual(nd, 3)
        self.assertIn("利空消息", tags)

    def test_neg_priority_over_pos(self):
        # 利好利空并存 → 利空优先扣分 (防御优先)
        nb, nd, tags = recommend.score_news_funnel({"official_pos": 1, "official_neg": 1})
        self.assertEqual(nb, 0)
        self.assertEqual(nd, 3)
        self.assertIn("利空消息", tags)

    def test_rumor_tag_only_no_score(self):
        # 传闻利好 → 仅标签, 不加减分 (真实性甄别: 小道消息不采信)
        nb, nd, tags = recommend.score_news_funnel({"rumor_pos": 2})
        self.assertEqual((nb, nd), (0, 0))
        self.assertIn("传闻利好", tags)
        nb, nd, tags = recommend.score_news_funnel({"rumor_neg": 1})
        self.assertEqual((nb, nd), (0, 0))
        self.assertIn("消息待证", tags)

    def test_missing_no_punish(self):
        self.assertEqual(recommend.score_news_funnel(None), (0, 0, []))
        self.assertEqual(recommend.score_news_funnel({}), (0, 0, []))

    def test_hot_rank_tag(self):
        nb, nd, tags = recommend.score_news_funnel({}, hot_rank={"600519.SH": 3}, code="600519.SH")
        self.assertEqual((nb, nd), (0, 0))
        self.assertIn("人气榜TOP", tags)
        _, _, tags = recommend.score_news_funnel({}, hot_rank={"600519.SH": 80}, code="600519.SH")
        self.assertNotIn("人气榜TOP", tags)

    def test_dzjy_tags(self):
        dzjy = {"600519.SH": {"count": 2, "total_amt": 1e8, "avg_discount": -0.05, "inst_buy": False}}
        _, _, tags = recommend.score_news_funnel({}, dzjy=dzjy, code="600519.SH")
        self.assertIn("大宗折价", tags)
        dzjy2 = {"600519.SH": {"count": 1, "total_amt": 1e8, "avg_discount": 0.0, "inst_buy": True}}
        _, _, tags = recommend.score_news_funnel({}, dzjy=dzjy2, code="600519.SH")
        self.assertIn("机构大宗买入", tags)


class TestApplyNewsSignals(unittest.TestCase):
    def setUp(self):
        self._reset_cache()

    def _reset_cache(self):
        recommend._NEWS_SIGNALS_CACHE.clear()
        recommend._HOT_RANK_CACHE = None
        recommend._DZJY_CACHE_DATE = None
        recommend._DZJY_CACHE = None

    def test_applies_score_and_tags(self):
        # 预填缓存, 避免真实网络请求
        recommend._NEWS_SIGNALS_CACHE["000001.SZ"] = {"official_pos": 1, "official_neg": 0,
                                                       "rumor_pos": 0, "rumor_neg": 0, "headlines": []}
        recommend._NEWS_SIGNALS_CACHE["600519.SH"] = {"official_pos": 0, "official_neg": 1,
                                                       "rumor_pos": 0, "rumor_neg": 0, "headlines": []}
        recommend._HOT_RANK_CACHE = {"000001.SZ": 5}
        recommend._DZJY_CACHE_DATE = "20260821"
        recommend._DZJY_CACHE = {}
        # 无消息面股票预填空信号, 避免触发真实网络请求
        recommend._NEWS_SIGNALS_CACHE["000002.SZ"] = {"official_pos": 0, "official_neg": 0,
                                                       "rumor_pos": 0, "rumor_neg": 0, "headlines": []}
        results = [
            {"ts_code": "000001.SZ", "score_total": 80, "tags": "短线优选"},
            {"ts_code": "600519.SH", "score_total": 75, "tags": "短线观察"},
            {"ts_code": "000002.SZ", "score_total": 70, "tags": ""},  # 无消息面
        ]
        out = recommend.apply_news_signals(results, "20260821")
        r1 = next(r for r in out if r["ts_code"] == "000001.SZ")
        r2 = next(r for r in out if r["ts_code"] == "600519.SH")
        r3 = next(r for r in out if r["ts_code"] == "000002.SZ")
        self.assertEqual(r1["score_total"], 82)      # 权威利好 +2
        self.assertEqual(r1["score_news"], 2)
        self.assertIn("利好消息", r1["tags"])
        self.assertIn("人气榜TOP", r1["tags"])
        self.assertEqual(r2["score_total"], 72)      # 权威利空 -3
        self.assertEqual(r2["score_news"], -3)
        self.assertIn("利空消息", r2["tags"])
        self.assertEqual(r3["score_total"], 70)      # 无消息面不误杀
        self.assertEqual(r3["score_news"], 0)
        self.assertIn("news", r3)

    def test_empty_results(self):
        self.assertEqual(recommend.apply_news_signals([], "20260821"), [])


if __name__ == "__main__":
    unittest.main()
