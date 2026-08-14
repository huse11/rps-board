# -*- coding: utf-8 -*-
"""sw_industry 申万三级板块池模块单元测试"""
import json
import os
import tempfile
import unittest
from pathlib import Path

import sw_industry


class TestToTsCode(unittest.TestCase):
    def test_sh(self):
        self.assertEqual(sw_industry.to_ts_code("600000"), "600000.SH")
        self.assertEqual(sw_industry.to_ts_code("688001"), "688001.SH")

    def test_sz(self):
        self.assertEqual(sw_industry.to_ts_code("000001"), "000001.SZ")
        self.assertEqual(sw_industry.to_ts_code("300750"), "300750.SZ")
        self.assertEqual(sw_industry.to_ts_code("002594"), "002594.SZ")

    def test_north(self):
        # 北交所(4/8开头) 申万无归属
        self.assertIsNone(sw_industry.to_ts_code("430047"))
        self.assertIsNone(sw_industry.to_ts_code("832000"))
        self.assertIsNone(sw_industry.to_ts_code("920000"))

    def test_pad(self):
        self.assertEqual(sw_industry.to_ts_code("1"), "000001.SZ")


class TestBuildIndustryDf(unittest.TestCase):
    def test_basic(self):
        sw_map = {
            "stock_industry": {"600000.SH": "银行Ⅲ", "300750.SZ": "电池"},
            "stock_name": {"600000.SH": "浦发银行", "300750.SZ": "宁德时代"},
            "board_stocks": {},
        }
        old_basic = [
            {"ts_code": "600000.SH", "name": "浦发银行", "list_date": "19991110"},
            {"ts_code": "300750.SZ", "name": "宁德时代", "list_date": "20180611"},
        ]
        import pandas as pd
        old_df = pd.DataFrame(old_basic)
        records = sw_industry.build_industry_df(sw_map, old_df)
        self.assertEqual(len(records), 2)
        r0 = records[0]
        self.assertEqual(r0["ts_code"], "600000.SH")
        self.assertEqual(r0["industry"], "银行Ⅲ")
        self.assertEqual(r0["list_date"], "19991110")

    def test_name_from_sw_when_old_missing(self):
        sw_map = {
            "stock_industry": {"600999.SH": "证券Ⅲ"},
            "stock_name": {"600999.SH": "招商证券"},
            "board_stocks": {},
        }
        records = sw_industry.build_industry_df(sw_map, None)
        self.assertEqual(records[0]["name"], "招商证券")
        self.assertEqual(records[0]["list_date"], "")  # 无 old_basic → 空

    def test_list_date_missing_ok(self):
        sw_map = {
            "stock_industry": {"600000.SH": "银行Ⅲ"},
            "stock_name": {"600000.SH": "浦发银行"},
            "board_stocks": {},
        }
        records = sw_industry.build_industry_df(sw_map, None)
        self.assertEqual(records[0]["list_date"], "")


class TestBuildCache(unittest.TestCase):
    def setUp(self):
        self._orig_cache = sw_industry.SW_CACHE_FILE
        self.tmpdir = tempfile.mkdtemp()
        sw_industry.SW_CACHE_FILE = Path(self.tmpdir) / "sw_industry_cache.json"

    def tearDown(self):
        sw_industry.SW_CACHE_FILE = self._orig_cache

    def test_cache_reuse(self):
        # 写入一个新鲜缓存(今天), build 应直接复用而不调网络
        data = {"fetch_date": "20260813", "stock_industry": {"600000.SH": "银行Ⅲ"},
                "stock_name": {"600000.SH": "浦发银行"}, "board_stocks": {"银行Ⅲ": ["600000.SH"]}}
        with open(sw_industry.SW_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        # 若触发网络会因 mock 缺失而报错; 命中缓存则安全返回
        result = sw_industry.build_sw_industry_map(force=False)
        self.assertEqual(result["board_stocks"]["银行Ⅲ"], ["600000.SH"])

    def test_expired_cache_refetch(self):
        # 过期缓存(20天前) → 触发重拉; mock akshare
        data = {"fetch_date": "20260701", "stock_industry": {}, "stock_name": {}, "board_stocks": {}}
        with open(sw_industry.SW_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

        class FakeAK:
            def sw_index_third_info(self):
                import pandas as pd
                return pd.DataFrame([
                    {"行业代码": "850111.SI", "行业名称": "种子", "成份个数": 8},
                    {"行业代码": "850999.SI", "行业名称": "僵尸板块", "成份个数": 2},  # 应被剔除
                ])

            def index_component_sw(self, symbol):
                import pandas as pd
                return pd.DataFrame([
                    {"证券代码": "000998", "证券名称": "隆平高科"},
                    {"证券代码": "600000", "证券名称": "浦发银行"},
                ])

        sw_industry._ak = FakeAK()
        try:
            result = sw_industry.build_sw_industry_map(force=True)
            self.assertEqual(len(result["board_stocks"]), 1)  # 僵尸板块被剔除
            self.assertIn("种子", result["board_stocks"])
            self.assertIn("600000.SH", result["stock_industry"])
            self.assertEqual(result["stock_industry"]["600000.SH"], "种子")
        finally:
            sw_industry._ak = None

    def test_fetch_failure_fallback_old_cache(self):
        # 拉取失败 → 回退旧缓存(即使过期)
        data = {"fetch_date": "20260101", "stock_industry": {"600000.SH": "银行Ⅲ"},
                "stock_name": {"600000.SH": "浦发银行"}, "board_stocks": {"银行Ⅲ": ["600000.SH"]}}
        with open(sw_industry.SW_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

        class FakeAKFail:
            def sw_index_third_info(self):
                raise RuntimeError("network down")

        sw_industry._ak = FakeAKFail()
        try:
            result = sw_industry.build_sw_industry_map(force=True)
            self.assertIsNotNone(result)
            self.assertEqual(result["stock_industry"]["600000.SH"], "银行Ⅲ")
        finally:
            sw_industry._ak = None


if __name__ == "__main__":
    unittest.main()
