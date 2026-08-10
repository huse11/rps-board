# -*- coding: utf-8 -*-
"""
单元测试：每周推荐股票模块 v1.0 (recommend_weekly.py)
覆盖: 周K聚合 / 指数周线 / 周涨跌幅 / 周度大盘三档判定 / 板块共振映射 /
      基本面深度排雷 / 周度资金面 / 周线技术共振 / 综合评分 / 标签 / 高位过滤 / JSON输出
运行: python -m unittest tests.test_recommend_weekly -v
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import recommend_weekly as rw
import recommend as rd


def make_daily_map(closes_by_code, latest_date="20260807", vols_by_code=None, amounts_by_code=None):
    """构造 mock daily_map: {date: DataFrame(ts_code,open,high,low,close,vol,amount,pct_chg)}
    仅交易日(bdate_range, 无周末行, 与真实 daily_cache 一致; 保证 W-FRI 周聚合 bar=周一到周五)
    """
    n_days = len(next(iter(closes_by_code.values())))
    dates = pd.bdate_range(end=pd.Timestamp(latest_date), periods=n_days).strftime("%Y%m%d").tolist()
    dm = {}
    for i, date in enumerate(dates):
        rows = []
        for code, closes in closes_by_code.items():
            c = closes[i]
            vol = vols_by_code[code][i] if vols_by_code else 100000
            amt = amounts_by_code[code][i] if amounts_by_code else c * 1e5
            rows.append({
                "ts_code": code, "open": c * 0.99, "high": c * 1.01,
                "low": c * 0.98, "close": c, "vol": vol, "amount": amt,
                "pct_chg": 1.0,
            })
        dm[date] = pd.DataFrame(rows)
    return dm


def make_weekly_kline(closes, vols=None):
    """构造 mock 周K线 DataFrame: 列 close/high/low/vol/pct_chg (按行顺序=时间序)"""
    import pandas as pd
    n = len(closes)
    if vols is None:
        vols = [1e6 + i * 1e4 for i in range(n)]
    rows = []
    for i in range(n):
        c = closes[i]
        pct = (closes[i] / closes[i - 1] - 1) * 100 if i > 0 else 0.0
        rows.append({
            "date": f"w{i:02d}", "open": c * 0.99, "high": c * 1.02,
            "low": c * 0.98, "close": c, "vol": vols[i], "amount": c * 1e5,
            "pct_chg": pct,
        })
    return pd.DataFrame(rows)


def make_index_weekly(closes):
    """构造 mock 周线指数: {code: Series(date→close)} (40周以上, 满足20周均线)"""
    import pandas as pd
    idx = pd.date_range("2025-11-07", periods=len(closes), freq="W-FRI")
    return {"000001.SH": pd.Series(closes, index=idx), "399006.SZ": pd.Series(closes, index=idx)}


class TestAggregateWeeklyKline(unittest.TestCase):
    def test_aggregation_basic(self):
        """上涨趋势 65 自然日 → 聚合 ~9 周, 列齐全, 最后一周 pct_chg 为正"""
        closes = [10 + i * 0.3 for i in range(65)]
        dm = make_daily_map({"000001.SZ": closes})
        wk = rw.aggregate_weekly_kline(dm, "20260807", codes=["000001.SZ"], n_weeks=70)
        self.assertIn("000001.SZ", wk)
        df = wk["000001.SZ"]
        self.assertGreaterEqual(len(df), 8)   # 65自然日≈9.3周
        for col in ("date", "open", "high", "low", "close", "vol", "amount", "pct_chg"):
            self.assertIn(col, df.columns)
        self.assertGreater(df["pct_chg"].iloc[-1], 0)  # 上涨趋势周涨跌幅为正

    def test_aggregation_close_is_week_last(self):
        """每周 close = 该周最后一个交易日收盘"""
        closes = [10 + i * 0.2 for i in range(40)]
        dm = make_daily_map({"000001.SZ": closes})
        wk = rw.aggregate_weekly_kline(dm, "20260807", codes=["000001.SZ"])
        last = wk["000001.SZ"].iloc[-1]
        self.assertAlmostEqual(last["close"], closes[-1], places=1)

    def test_aggregation_code_filter(self):
        """只聚合指定 codes, 未指定股票不出现"""
        closes_a = [10 + i for i in range(40)]
        closes_b = [5 + i for i in range(40)]
        dm = make_daily_map({"000001.SZ": closes_a, "000002.SZ": closes_b})
        wk = rw.aggregate_weekly_kline(dm, "20260807", codes=["000001.SZ"])
        self.assertIn("000001.SZ", wk)
        self.assertNotIn("000002.SZ", wk)


class TestAggregateWeeklyIndex(unittest.TestCase):
    def test_trade_date_column(self):
        """Tushare 格式(trade_date 列) → 周线序列"""
        import pandas as pd
        n = 45
        base = datetime.strptime("20260807", "%Y%m%d")
        dates = sorted([(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(n)])
        df = pd.DataFrame({"trade_date": dates, "close": [100 + i for i in range(n)]})
        res = rw.aggregate_weekly_index({"000001.SH": df})
        self.assertIn("000001.SH", res)
        self.assertGreaterEqual(len(res["000001.SH"]), 4)

    def test_date_column(self):
        """AKShare 格式(date 列) → 周线序列"""
        import pandas as pd
        n = 45
        base = datetime.strptime("20260807", "%Y%m%d")
        dates = sorted([(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(n)])
        df = pd.DataFrame({"date": dates, "close": [100 + i for i in range(n)]})
        res = rw.aggregate_weekly_index({"000001.SH": df})
        self.assertIn("000001.SH", res)


class TestRecentWeekPctMap(unittest.TestCase):
    def test_uptrend_positive(self):
        """近1周上涨趋势 → 涨跌幅为正"""
        closes = [10 + i * 0.2 for i in range(30)]
        dm = make_daily_map({"000001.SZ": closes})
        m = rw.recent_week_pct_map(dm, "20260807", n_weeks=1)
        self.assertGreater(m["000001.SZ"], 0)

    def test_downtrend_negative(self):
        """近1周下跌趋势 → 涨跌幅为负"""
        closes = [20 - i * 0.3 for i in range(30)]
        dm = make_daily_map({"000001.SZ": closes})
        m = rw.recent_week_pct_map(dm, "20260807", n_weeks=1)
        self.assertLess(m["000001.SZ"], 0)


class TestCheckMarketEnvWeekly(unittest.TestCase):
    def _good_week_pct(self):
        return {f"{i:06d}.SZ": (2.0 if i < 60 else -1.0) for i in range(100)}  # 60% 上涨

    def test_good(self):
        """指数站稳 20 周均线 + 上涨占比 60% → good"""
        closes = [100 + i * 2 for i in range(40)]  # 单调上涨
        level, status = rw.check_market_env_weekly(make_index_weekly(closes), self._good_week_pct())
        self.assertEqual(level, "good")
        self.assertTrue(status["is_safe"])
        self.assertEqual(status["level"], "good")
        self.assertTrue(status["position_advice"])

    def test_bad_index_below_ma20(self):
        """指数跌破 20 周均线 → bad (终止选股)"""
        closes = [100 + i * 2 for i in range(35)] + [160, 150, 140, 130, 120]
        level, status = rw.check_market_env_weekly(make_index_weekly(closes), self._good_week_pct())
        self.assertEqual(level, "bad")
        self.assertFalse(status["is_safe"])
        self.assertIn("20周均线", status["reason"])

    def test_weak_advance_ratio(self):
        """指数站稳均线但上涨占比 35% (<40%) → weak"""
        closes = [100 + i * 2 for i in range(40)]
        week_pct = {f"{i:06d}.SZ": (2.0 if i < 35 else -2.0) for i in range(100)}  # 35% 上涨
        level, status = rw.check_market_env_weekly(make_index_weekly(closes), week_pct)
        self.assertEqual(level, "weak")
        self.assertTrue(status["is_safe"])
        self.assertIn("谨慎", status["position_advice"])

    def test_bad_extreme_pride(self):
        """上涨占比 20% (<30%) → bad"""
        closes = [100 + i * 2 for i in range(40)]
        week_pct = {f"{i:06d}.SZ": (2.0 if i < 20 else -2.0) for i in range(100)}  # 20% 上涨
        level, _ = rw.check_market_env_weekly(make_index_weekly(closes), week_pct)
        self.assertEqual(level, "bad")


class TestBuildWeeklySectorMap(unittest.TestCase):
    def test_mapping_and_streak(self):
        """成分股 → 板块映射: sector_count 去重, week_streak=continuous/5 累计"""
        recs = [
            {"name": "半导体", "continuous": 10,
             "stocks": [{"ts_code": "000001.SZ"}, {"ts_code": "000002.SZ"}]},
            {"name": "芯片", "continuous": 5,
             "stocks": [{"ts_code": "000001.SZ"}]},
            {"name": "半导体", "continuous": 10,   # 同名板块重复 → sector_count 去重
             "stocks": [{"ts_code": "000003.SZ"}]},
        ]
        m = rw.build_weekly_sector_map(recs)
        self.assertEqual(m["000001.SZ"]["sector_count"], 2)   # 半导体+芯片
        self.assertAlmostEqual(m["000001.SZ"]["week_streak"], 3.0)  # 10/5 + 5/5
        self.assertEqual(m["000002.SZ"]["sector_count"], 1)
        self.assertEqual(m["000003.SZ"]["sector_count"], 1)   # 同名板块去重为1
        self.assertNotIn("999999.SZ", m)


class TestScreenFundamentalWeekly(unittest.TestCase):
    def _df(self, metas):
        import pandas as pd
        return pd.DataFrame([
            {"ts_code": c[0], "name": c[1], "price": 10.0, "vol": 100000,
             "circ_mv": 5e5, "pe_ttm": 20.0, "turnover_rate": 5.0, "vol_ratio": 1.5}
            for c in metas])

    def test_pass_and_reject_profit(self):
        """净利润>0 且同比正 → 通过; 净利润≤0 → 剔除"""
        df = self._df([("000001.SZ", "正常"), ("000002.SZ", "亏损")])
        fina = {
            "000001.SZ": {"net_profit": 1e8, "netprofit_yoy": 15.0,
                          "prev_netprofit_yoy": 10.0, "dt_netprofit_yoy": 8.0,
                          "debt_to_assets": 40.0},
            "000002.SZ": {"net_profit": -1e7, "netprofit_yoy": -50.0,
                          "prev_netprofit_yoy": -30.0, "dt_netprofit_yoy": -40.0,
                          "debt_to_assets": 40.0},
        }
        passed, rejects = rw.screen_fundamental_weekly(df, fina)
        self.assertIn("000001.SZ", passed["ts_code"].tolist())
        self.assertNotIn("000002.SZ", passed["ts_code"].tolist())
        self.assertTrue(any("净利润" in r["reason"] for r in rejects))

    def test_reject_continuous_decline(self):
        """近2期同比连续下滑 → 剔除"""
        df = self._df([("000001.SZ", "下滑")])
        fina = {"000001.SZ": {"net_profit": 1e8, "netprofit_yoy": -10.0,
                              "prev_netprofit_yoy": -5.0, "dt_netprofit_yoy": 5.0,
                              "debt_to_assets": 40.0}}
        passed, rejects = rw.screen_fundamental_weekly(df, fina)
        self.assertNotIn("000001.SZ", passed["ts_code"].tolist())
        self.assertTrue(any("净利润同比" in r["reason"] for r in rejects))

    def test_keep_non_continuous(self):
        """上期正增长 + 本期未转负(0) → 非连续下滑, 保留"""
        df = self._df([("000001.SZ", "企稳")])
        fina = {"000001.SZ": {"net_profit": 1e8, "netprofit_yoy": 0.0,
                              "prev_netprofit_yoy": 12.0, "dt_netprofit_yoy": 3.0,
                              "debt_to_assets": 40.0}}
        passed, _ = rw.screen_fundamental_weekly(df, fina)
        self.assertIn("000001.SZ", passed["ts_code"].tolist())

    def test_reject_deduct_abnormal(self):
        """扣非同比大幅异常(< -50) → 剔除 (疑一次性收益虚增)"""
        df = self._df([("000001.SZ", "扣非异常")])
        fina = {"000001.SZ": {"net_profit": 1e8, "netprofit_yoy": 20.0,
                              "prev_netprofit_yoy": 10.0, "dt_netprofit_yoy": -80.0,
                              "debt_to_assets": 40.0}}
        passed, rejects = rw.screen_fundamental_weekly(df, fina)
        self.assertNotIn("000001.SZ", passed["ts_code"].tolist())
        self.assertTrue(any("扣非净利润同比" in r["reason"] for r in rejects))

    def test_no_fina_map_fallback(self):
        """财务数据缺失 → 仅日度硬门槛, 不误杀"""
        df = self._df([("000001.SZ", "无财务数据")])
        passed, _ = rw.screen_fundamental_weekly(df, {})
        self.assertIn("000001.SZ", passed["ts_code"].tolist())


class TestScreenCapitalWeekly(unittest.TestCase):
    def _stocks(self):
        return pd.DataFrame([{
            "ts_code": "000001.SZ", "name": "达标", "price": 10.0, "vol": 100000,
            "circ_mv": 5e5, "pe_ttm": 20.0, "turnover_rate": 5.0, "vol_ratio": 1.5,
        }])

    def _setup(self, this_week_vol, last_week_vol, net_daily=1e7):
        """构造 25 天 daily_map (5天/周×5周) + 周K(本周/上周/上上周量) + moneyflow
        circ_mv=5e5万元(50亿), price=10 → 周换手=Σvol*10/(5e5*100)
        """
        # 最近5个交易日=本周(vol=this_week_vol/5), 再前5天=上周, 更早=上上周(较小量)
        dm = {}
        dates = pd.bdate_range(end=pd.Timestamp("2026-08-07"), periods=25).strftime("%Y%m%d").tolist()
        for i, d in enumerate(dates):
            if i >= 20:
                v = this_week_vol // 5
            elif i >= 15:
                v = last_week_vol // 5
            else:
                v = this_week_vol // 10
            dm[d] = pd.DataFrame([{
                "ts_code": "000001.SZ", "open": 9.9, "high": 10.1, "low": 9.8,
                "close": 10.0, "vol": v, "amount": 3e5, "pct_chg": 1.0,
            }])
        wk = rw.aggregate_weekly_kline(dm, "20260807", codes=["000001.SZ"], n_weeks=10)
        mflow = {"000001.SZ": [
            {"trade_date": d, "buy_sm_amount": net_daily, "sell_sm_amount": 0.0}
            for d in dates[-5:]
        ]}
        return dm, wk, mflow

    def test_pass(self):
        """量能放大 + 换手[5,40] + 成交额达标 + 净流入 → 通过"""
        # 周换手: Σvol=1e8手(周), tr=1e8*10/(5e5*100)=20% 达标
        dm, wk, mflow = self._setup(this_week_vol=1e8, last_week_vol=5e7)
        passed, details = rw.screen_capital_weekly(self._stocks(), wk, dm, "20260807", mflow)
        self.assertIn("000001.SZ", passed["ts_code"].tolist())
        self.assertIn("000001.SZ", details)
        self.assertAlmostEqual(details["000001.SZ"]["turnover"], 20.0, places=1)

    def test_reject_volume_shrink(self):
        """本周量能 < 上周 → 剔除 (量能趋势不向上)"""
        dm, wk, mflow = self._setup(this_week_vol=5e7, last_week_vol=1e8)
        passed, _ = rw.screen_capital_weekly(self._stocks(), wk, dm, "20260807", mflow)
        self.assertNotIn("000001.SZ", passed["ts_code"].tolist())

    def test_reject_high_turnover(self):
        """周换手超 40% → 剔除 (高位出货风险)"""
        # 周换手: Σvol=5e8手, tr=5e8*10/(5e5*100)=100% > 40
        dm, wk, mflow = self._setup(this_week_vol=5e8, last_week_vol=2e8)
        passed, _ = rw.screen_capital_weekly(self._stocks(), wk, dm, "20260807", mflow)
        self.assertNotIn("000001.SZ", passed["ts_code"].tolist())

    def test_reject_net_outflow(self):
        """主力资金累计净流出 → 剔除"""
        dm, wk, mflow = self._setup(this_week_vol=1e8, last_week_vol=5e7, net_daily=-1e7)
        passed, _ = rw.screen_capital_weekly(self._stocks(), wk, dm, "20260807", mflow)
        self.assertNotIn("000001.SZ", passed["ts_code"].tolist())

    def test_no_mflow_fallback(self):
        """mflow_map 缺失 → 资金流向跳过, 不误杀"""
        dm, wk, _ = self._setup(this_week_vol=1e8, last_week_vol=5e7)
        passed, _ = rw.screen_capital_weekly(self._stocks(), wk, dm, "20260807", {})
        self.assertIn("000001.SZ", passed["ts_code"].tolist())


class TestScreenTechnicalWeekly(unittest.TestCase):
    def _stocks(self):
        return pd.DataFrame([{
            "ts_code": "000001.SZ", "name": "趋势股", "price": 40.0, "vol": 100000,
            "circ_mv": 5e5, "pe_ttm": 20.0, "turnover_rate": 5.0, "vol_ratio": 1.5,
        }])

    def test_uptrend_pass(self):
        """65周单调上涨 → 均线多头/MACD/突破/量价/支撑 达标 ≥3 → 通过"""
        closes = [10 + i * 0.5 for i in range(65)]
        wk = {"000001.SZ": make_weekly_kline(closes)}
        rps = {"000001.SZ": 90.0}
        passed, details = rw.screen_technical_weekly(self._stocks(), wk, rps50_map=rps)
        self.assertIn("000001.SZ", passed["ts_code"].tolist())
        d = details["000001.SZ"]
        self.assertGreaterEqual(d["hit_count"], 3)
        self.assertIn("周线均线多头", d["hits"])
        self.assertIn("周线突破", d["hits"])

    def test_downtrend_reject(self):
        """65周持续下跌 → 达标 <3 → 剔除"""
        closes = [40 - i * 0.4 for i in range(65)]
        wk = {"000001.SZ": make_weekly_kline(closes)}
        passed, _ = rw.screen_technical_weekly(self._stocks(), wk, rps50_map={})
        self.assertNotIn("000001.SZ", passed["ts_code"].tolist())

    def test_insufficient_weeks(self):
        """周K不足 21 周 → 剔除 (数据不足)"""
        closes = [10 + i for i in range(15)]
        wk = {"000001.SZ": make_weekly_kline(closes)}
        passed, _ = rw.screen_technical_weekly(self._stocks(), wk)
        self.assertNotIn("000001.SZ", passed["ts_code"].tolist())


class TestScoringWeekly(unittest.TestCase):
    def test_score_sector(self):
        """板块热度: 1板块=20, 2板块=30, ≥3板块=40; 连续上榜加权"""
        self.assertEqual(rw.score_sector_weekly(1, 0), 20)
        self.assertEqual(rw.score_sector_weekly(2, 0), 30)
        self.assertEqual(rw.score_sector_weekly(3, 0), 40)
        self.assertEqual(rw.score_sector_weekly(2, 3), 33)   # 30+3
        self.assertEqual(rw.score_sector_weekly(1, 5), 25)   # 20+5
        self.assertEqual(rw.score_sector_weekly(3, 10), 40)  # 40+5 → 封顶40

    def test_score_fundamental(self):
        """基本面 30 分制: 高增长 > 正增长 > 缺失"""
        high = {"net_profit": 1e8, "netprofit_yoy": 25.0, "prev_netprofit_yoy": 10.0,
                "dt_netprofit_yoy": 8.0, "debt_to_assets": 30.0}
        low = {"net_profit": 1e8, "netprofit_yoy": 5.0, "prev_netprofit_yoy": 3.0,
               "dt_netprofit_yoy": -10.0, "debt_to_assets": 60.0}
        self.assertGreater(rw.score_fundamental_weekly(high), rw.score_fundamental_weekly(low))
        self.assertLessEqual(rw.score_fundamental_weekly(high), 30)
        self.assertEqual(rw.score_fundamental_weekly(None), 0)

    def test_score_technical(self):
        """技术 20 分制: 6项=20, 5项=18, 4项=15, 3项=12, <3项=0"""
        self.assertEqual(rw.score_technical_weekly(6), 20)
        self.assertEqual(rw.score_technical_weekly(5), 18)
        self.assertEqual(rw.score_technical_weekly(4), 15)
        self.assertEqual(rw.score_technical_weekly(3), 12)
        self.assertEqual(rw.score_technical_weekly(2), 0)

    def test_score_capital(self):
        """资金 10 分制: 量放大1.3+/换手健康/净流入 → 满分"""
        cap = {"vol_expand": 1.5, "turnover": 12.0, "net_inflow_5d": 1e8}
        self.assertEqual(rw.score_capital_weekly(cap), 10)
        self.assertEqual(rw.score_capital_weekly(None), 0)
        weak = {"vol_expand": 1.1, "turnover": 30.0, "net_inflow_5d": None}
        self.assertLess(rw.score_capital_weekly(weak), 10)

    def test_weights_sum(self):
        """权重总和 = 100"""
        cfg = rw.CONFIG_WEEKLY["score"]
        total = cfg["sector"] + cfg["fundamental"] + cfg["technical"] + cfg["capital"]
        self.assertEqual(total, 100)


class TestBuildWeeklyTags(unittest.TestCase):
    def test_multi_sector_tags(self):
        tags = rw.build_weekly_tags(["半导体", "芯片"], ["周线突破", "周线MACD多头"], sector_count=2)
        self.assertIn("周线突破", tags)
        self.assertIn("2板块共振", tags)
        self.assertIn("半导体", tags)

    def test_empty_fallback(self):
        tags = rw.build_weekly_tags([], [])
        self.assertIn("周线多指标共振", tags)


class TestFilterHighGain(unittest.TestCase):
    def test_filter(self):
        recs = [{"ts_code": "000001.SZ"}, {"ts_code": "000002.SZ"}, {"ts_code": "000003.SZ"}]
        gains = {"000001.SZ": 10.0, "000002.SZ": 35.0, "000003.SZ": 30.0}  # 30不剔除(严格>30)
        kept, dropped = rw.filter_high_gain(recs, gains)
        self.assertEqual(dropped, 1)
        self.assertEqual([r["ts_code"] for r in kept], ["000001.SZ", "000003.SZ"])


class TestSaveWeeklyRecommendations(unittest.TestCase):
    def test_schema_v1(self):
        import json
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        rw.WEEKLY_REC_FILE = tmp.name
        try:
            recs = [{
                "ts_code": "000001.SZ", "name": "测试", "sectors": ["半导体"],
                "sector_count": 1, "tags": "周线突破", "price": 10.0,
                "pct_chg_week": 3.5, "score_total": 82.0,
                "score_sector": 20.0, "score_fundamental": 30.0,
                "score_capital": 10.0, "score_technical": 20.0,
                "fundamental": {"net_profit": 1e8}, "capital": {}, "technical": {"hits": []},
            }]
            rw.save_weekly_recommendations(recs, "20260807", "20260731")
            with open(tmp.name, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["schema_version"], 1)
            self.assertEqual(data["update_date"], "20260807")
            self.assertIn("week", data)
            self.assertIn("market_status", data)
            self.assertIn("position_advice", data)
            self.assertEqual(data["count"], 1)
            self.assertEqual(data["stocks"][0]["score_total"], 82.0)
        finally:
            os.unlink(tmp.name)
            rw.WEEKLY_REC_FILE = rw.STATIC_DIR / "weekly_recommendations.json"


class TestWeeklyMainFlow(unittest.TestCase):
    """端到端: recommend_weekly_stocks 主流程 (mock 全部网络函数)
    验证五层漏斗完整链路: 大盘→候选池→基本面→资金面→技术共振→综合评分→排序
    """

    N = 20  # 候选股数量

    @classmethod
    def setUpClass(cls):
        cls.mock_ctx = mock.patch.multiple(
            rd,
            fetch_index_daily=mock.DEFAULT, fetch_fina_indicator_batch=mock.DEFAULT,
            fetch_moneyflow_batch=mock.DEFAULT, build_rps50_map=mock.DEFAULT,
            _AK_OK=False, fetch_daily_basic_em=mock.DEFAULT,
        )
        cls.mocks = cls.mock_ctx.start()

    @classmethod
    def tearDownClass(cls):
        cls.mock_ctx.stop()

    def _industry(self):
        codes = [f"{i:06d}.SZ" for i in range(1, self.N + 1)]
        return pd.DataFrame({
            "ts_code": codes, "name": [f"测试{i}" for i in range(1, self.N + 1)],
            "industry": ["半导体"] * self.N, "list_date": ["20200101"] * self.N,
        })

    def _daily(self):
        """365 交易日: 每股缓慢上涨 + 成交量递增(周量能放大)
        流通市值10亿(circ_mv=1e5万元), 周换手≈Σvol×price/(1e5×100)∈[5,40]
        """
        n = 365
        closes, vols = {}, {}
        for i in range(self.N):
            code = f"{i + 1:06d}.SZ"
            base = 10.0 + i * 0.5
            closes[code] = [base + d * 0.05 for d in range(n)]
            vols[code] = [int(5e5 * (1 + d / 400.0)) for d in range(n)]
        return make_daily_map(closes, latest_date="20260807", vols_by_code=vols)

    def _rps20_records(self):
        codes = [f"{i:06d}.SZ" for i in range(1, self.N + 1)]
        return [
            {"name": "半导体", "continuous": 10, "stocks": [{"ts_code": c} for c in codes]},
            {"name": "芯片", "continuous": 5, "stocks": [{"ts_code": c} for c in codes[:10]]},
        ]

    def test_full_flow(self):
        ind = self._industry()
        dm = self._daily()
        codes = [f"{i:06d}.SZ" for i in range(1, self.N + 1)]

        # mock 返回数据
        self.mocks["fetch_index_daily"].return_value = {
            c: pd.DataFrame({"trade_date": pd.bdate_range(end="2026-08-07", periods=120).strftime("%Y%m%d").tolist(),
                             "close": [100 + i * 0.5 for i in range(120)]})
            for c in ("000001.SH", "399006.SZ")
        }
        fina = {c: {"net_profit": 1e8, "netprofit_yoy": 15.0, "prev_netprofit_yoy": 10.0,
                    "dt_netprofit_yoy": 8.0, "debt_to_assets": 40.0, "end_date": "20260630"}
                for c in codes}
        self.mocks["fetch_fina_indicator_batch"].return_value = fina
        self.mocks["fetch_moneyflow_batch"].return_value = {
            c: [{"trade_date": d, "buy_sm_amount": 1e7, "sell_sm_amount": 0.0}
                for d in pd.bdate_range(end="2026-08-07", periods=5).strftime("%Y%m%d").tolist()]
            for c in codes
        }
        self.mocks["build_rps50_map"].return_value = {c: 90.0 for c in codes}
        self.mocks["fetch_daily_basic_em"].return_value = {
            c: {"pe_ttm": 20.0, "circ_mv": 1e5, "turnover_rate": 5.0, "volume_ratio": 1.5}
            for c in codes
        }
        # daily_basic: pool.call_any 返回候选股 DataFrame
        basic_df = pd.DataFrame([
            {"ts_code": c, "pe_ttm": 20.0, "circ_mv": 1e5, "turnover_rate": 5.0, "volume_ratio": 1.5}
            for c in codes
        ])
        with mock.patch.object(rd.pool, "call_any", return_value=basic_df):
            recs = rw.recommend_weekly_stocks(ind, dm, "20260807", self._rps20_records())

        self.assertGreaterEqual(len(recs), 1, "五层漏斗应至少输出1只")
        r = recs[0]
        for k in ("ts_code", "name", "sectors", "sector_count", "week_streak", "tags",
                  "price", "pct_chg_week", "score_total", "score_sector", "score_fundamental",
                  "score_capital", "score_technical", "fundamental", "capital", "technical"):
            self.assertIn(k, r)
        self.assertGreaterEqual(r["score_total"], 70, "综合得分须过门槛")
        # 按综合得分降序
        scores = [x["score_total"] for x in recs]
        self.assertEqual(scores, sorted(scores, reverse=True))
        # 近2周涨幅不应触发高位过滤(缓慢上涨)
        self.assertGreater(r["pct_chg_week"], 0)

    def test_bad_market_stops(self):
        """大盘恶劣(bad) → 直接终止, 返回空列表"""
        ind = self._industry()
        dm = self._daily()
        # 指数最后5周大跌: 跌破20周均线 → bad
        closes = [100 + i * 0.5 for i in range(115)] + [170, 160, 150, 140, 130]
        self.mocks["fetch_index_daily"].return_value = {
            c: pd.DataFrame({"trade_date": pd.bdate_range(end="2026-08-07", periods=len(closes)).strftime("%Y%m%d").tolist(),
                             "close": closes})
            for c in ("000001.SH", "399006.SZ")
        }
        with mock.patch.object(rd.pool, "call_any", return_value=pd.DataFrame()):
            recs = rw.recommend_weekly_stocks(ind, dm, "20260807", self._rps20_records())
        self.assertEqual(recs, [])

    def test_no_rps20_stops(self):
        """无 RPS20 入选板块 → 跳过周度推荐"""
        ind = self._industry()
        dm = self._daily()
        recs = rw.recommend_weekly_stocks(ind, dm, "20260807", [])
        self.assertEqual(recs, [])


if __name__ == "__main__":
    unittest.main()