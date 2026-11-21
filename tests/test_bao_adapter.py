# -*- coding: utf-8 -*-
"""
单元测试: BaoStock 备份数据源适配层 (bao_adapter.py) — 2026-08-30 新增
覆盖: 财务指标字段映射(毛利率/净利率/ROE/资产负债率/归母净利/净利同比) +
      日线解析(开高低收/量额/换手) + 交易日历 + 日期/代码转换 + 降级兜底
方式: mock baostock 模块返回伪 ResultSet, 验证解析/降级逻辑 (不依赖真实网络)
运行: python -m unittest tests.test_bao_adapter -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bao_adapter as adapter


class _FakeRs:
    """伪 baostock ResultSet"""
    def __init__(self, fields, rows, error_code="0", error_msg="success"):
        self.fields = fields
        self._rows = list(rows)
        self._i = 0
        self.error_code = error_code
        self.error_msg = error_msg

    def next(self):
        if self._i >= len(self._rows):
            return False
        self._i += 1
        return True

    def get_row_data(self):
        return list(self._rows[self._i - 1])


class _FakeBs:
    """伪 baostock 模块"""
    def __init__(self):
        self.k_rows = []
        self.profit_rows = []
        self.growth_rows = []
        self.balance_rows = []
        self.trade_rows = []
        self.login_ok = True

    def login(self):
        return _FakeRs([], [], error_code="0" if self.login_ok else "10001",
                       error_msg="success" if self.login_ok else "login failed")

    def query_history_k_data_plus(self, code, fields, start_date="", end_date="",
                                  frequency="", adjustflag=""):
        return _FakeRs(fields.split(","), self.k_rows)

    def query_profit_data(self, code="", year=0, quarter=0):
        return _FakeRs(["code", "pubDate", "statDate", "roeAvg", "npMargin", "gpMargin",
                        "netProfit", "epsTTM", "MBRevenue", "totalShare", "liqaShare"],
                       self.profit_rows)

    def query_growth_data(self, code="", year=0, quarter=0):
        return _FakeRs(["code", "pubDate", "statDate", "YOYEquity", "YOYAsset", "YOYNI",
                        "YOYEPSBasic", "YOYPNI"], self.growth_rows)

    def query_balance_data(self, code="", year=0, quarter=0):
        return _FakeRs(["code", "pubDate", "statDate", "currentRatio", "quickRatio",
                        "cashRatio", "YOYLiability", "liabilityToAsset", "assetToEquity"],
                       self.balance_rows)

    def query_trade_dates(self, start_date="", end_date=""):
        return _FakeRs(["calendar_date", "is_trading_day"], self.trade_rows)


class TestBaoFina(unittest.TestCase):
    def setUp(self):
        self.orig_bs = adapter.bs
        self.fake = _FakeBs()
        # 茅台 2026 半年报真实值 (probe 实测): 毛利率89.56%/净利率50.75%/ROE17.95%/负债率15.19%/归母460.3亿
        self.fake.profit_rows = [
            ["sh.600519", "2026-08-15", "2026-06-30", "0.179543", "0.507516",
             "0.895552", "46033330566.78", "65.142936", "92278072083.21",
             "1250081601.00", "1250081601.00"]]
        self.fake.growth_rows = [
            ["sh.600519", "2026-08-15", "2026-06-30", "0.052827", "0.057460",
             "-0.020290", "-0.016860", "-0.019516"]]
        self.fake.balance_rows = [
            ["sh.600519", "2026-08-15", "2026-06-30", "5.589543", "4.274995",
             "1.147362", "0.088869", "0.151931", "1.179150"]]
        adapter.bs = self.fake
        adapter._BSD_LOGIN = False
        adapter._FINA_CACHE.clear()
        adapter._FINA_CACHE_TS = 0

    def tearDown(self):
        adapter.bs = self.orig_bs
        adapter._BSD_LOGIN = False
        adapter._FINA_CACHE.clear()
        adapter._FINA_CACHE_TS = 0

    def test_parse_fina(self):
        r = adapter.fetch_fina_bao("600519.SH")
        v = r["600519.SH"]
        self.assertEqual(v["end_date"], "20260630")
        # 毛利率/净利率/ROE 为百分比数值 (0.8955→89.56), 资产负债率同
        self.assertEqual(v["gross_margin"], 89.56)
        self.assertEqual(v["net_margin"], 50.75)
        self.assertEqual(v["roe"], 17.95)
        self.assertEqual(v["debt_to_assets"], 15.19)
        self.assertEqual(v["net_profit"], 46033330566.78)   # 归母净利绝对数 (同花顺缺失项)
        self.assertEqual(v["total_revenue"], 92278072083.21)
        self.assertEqual(v["netprofit_yoy"], -2.03)
        self.assertIsNone(v["n_income_attr_parent"])        # baostock 无扣非绝对数
        self.assertIsNone(v["roic"])                        # 无 ROIC
        self.assertIsNone(v["ocf_ps"])                      # 无每股现金流

    def test_digit_and_sh_code(self):
        r = adapter.fetch_fina_bao("600519")
        self.assertIn("600519.SH", r)

    def test_fallback_quarter(self):
        # 当前季度(2026Q2 在 8月) → 若 Q2 未披露回退 Q1
        self.fake.profit_rows = [["sh.600519", "2026-05-01", "2026-03-31", "0.12",
                                  "0.50", "0.90", "1e10", "40", "8e10", "1e9", "1e9"]]
        self.fake.growth_rows = [["sh.600519", "2026-05-01", "2026-03-31", "0.02",
                                  "0.03", "0.08", "0.1", "0.05"]]
        self.fake.balance_rows = [["sh.600519", "2026-05-01", "2026-03-31", "5.5",
                                   "4.2", "1.1", "0.08", "0.14", "1.16"]]
        r = adapter.fetch_fina_bao("600519.SH")
        self.assertEqual(r["600519.SH"]["end_date"], "20260331")

    def test_empty_returns_empty(self):
        self.fake.profit_rows = []
        self.fake.growth_rows = []
        self.fake.balance_rows = []
        self.assertEqual(adapter.fetch_fina_bao("600519.SH"), {})

    def test_login_fail_returns_empty(self):
        self.fake.login_ok = False
        self.assertEqual(adapter.fetch_fina_bao("600519.SH"), {})

    def test_bs_not_available(self):
        orig = adapter._BAO_OK
        adapter._BAO_OK = False
        try:
            self.assertEqual(adapter.fetch_fina_bao("600519.SH"), {})
        finally:
            adapter._BAO_OK = orig


class TestBaoKline(unittest.TestCase):
    def setUp(self):
        self.orig_bs = adapter.bs
        self.fake = _FakeBs()
        self.fake.k_rows = [
            ["2026-08-24", "sh.600519", "1271.01", "1313.80", "1270.33", "1304.66",
             "1272.83", "4843957", "6299794441.41", "0.387500", "2.500700"],
            ["2026-08-25", "sh.600519", "1304.66", "1310.00", "1290.00", "1295.00",
             "1304.66", "5000000", "6400000000.00", "0.400000", "-0.736000"],
        ]
        adapter.bs = self.fake
        adapter._BSD_LOGIN = False

    def tearDown(self):
        adapter.bs = self.orig_bs
        adapter._BSD_LOGIN = False

    def test_parse_kline(self):
        df = adapter.fetch_kline_bao("600519.SH", "20260820", "20260828")
        self.assertIsNotNone(df)
        self.assertEqual(len(df), 2)
        row = df.iloc[0]
        self.assertEqual(row["date"], "20260824")   # 日期转 YYYYMMDD
        self.assertEqual(row["close"], 1304.66)
        self.assertEqual(row["turn"], 0.3875)       # 换手率真实值
        self.assertEqual(row["pct_chg"], 2.5007)

    def test_empty_kline(self):
        self.fake.k_rows = []
        self.assertIsNone(adapter.fetch_kline_bao("600519.SH"))


class TestBaoTradeDays(unittest.TestCase):
    def setUp(self):
        self.orig_bs = adapter.bs
        self.fake = _FakeBs()
        self.fake.trade_rows = [
            ["2026-08-24", "1"], ["2026-08-25", "1"], ["2026-08-26", "1"],
            ["2026-08-27", "1"], ["2026-08-28", "1"], ["2026-08-29", "0"]]
        adapter.bs = self.fake
        adapter._BSD_LOGIN = False

    def tearDown(self):
        adapter.bs = self.orig_bs
        adapter._BSD_LOGIN = False

    def test_trade_days(self):
        td = adapter.fetch_trade_days("20260824", "20260829")
        self.assertEqual(td, ["20260824", "20260825", "20260826", "20260827", "20260828"])
        # 8-29(周六) 非交易日被过滤

    def test_no_trade_day(self):
        self.fake.trade_rows = [["2026-08-29", "0"]]
        self.assertIsNone(adapter.fetch_trade_days("20260829", "20260829"))


class TestBaoUtils(unittest.TestCase):
    def test_ts_to_bs(self):
        self.assertEqual(adapter._ts_to_bs_code("600519.SH"), "sh.600519")
        self.assertEqual(adapter._ts_to_bs_code("000001.SZ"), "sz.000001")
        self.assertEqual(adapter._ts_to_bs_code("830799.BJ"), "bj.830799")
        self.assertIsNone(adapter._ts_to_bs_code("bad"))

    def test_ts_back(self):
        self.assertEqual(adapter._ts_back("600519"), "600519.SH")
        self.assertEqual(adapter._ts_back("000001"), "000001.SZ")
        self.assertEqual(adapter._ts_back("830799"), "830799.BJ")

    def test_fmt_bs_date(self):
        self.assertEqual(adapter._fmt_bs_date("20260820"), "2026-08-20")
        self.assertEqual(adapter._fmt_bs_date("2026-08-20"), "2026-08-20")


if __name__ == "__main__":
    unittest.main()