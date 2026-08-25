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
        from datetime import datetime as _dt
        data = {"fetch_date": _dt.now().strftime("%Y%m%d"), "stock_industry": {"600000.SH": "银行Ⅲ"},
                "stock_name": {"600000.SH": "浦发银行"}, "board_stocks": {"银行Ⅲ": ["600000.SH"]},
                "board_parent": {"银行Ⅲ": "银行"}}
        with open(sw_industry.SW_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        # 若触发网络会因 mock 缺失而报错; 命中缓存则安全返回
        result = sw_industry.build_sw_industry_map(force=False)
        self.assertEqual(result["board_stocks"]["银行Ⅲ"], ["600000.SH"])
        self.assertEqual(result["board_parent"]["银行Ⅲ"], "银行")

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

            def sw_index_first_info(self):
                import pandas as pd
                return pd.DataFrame([
                    {"行业代码": "801010.SI", "行业名称": "农林牧渔", "成份个数": 100},
                ])

            def index_component_sw(self, symbol):
                import pandas as pd
                if symbol == "850111":
                    return pd.DataFrame([
                        {"证券代码": "000998", "证券名称": "隆平高科"},
                        {"证券代码": "600000", "证券名称": "浦发银行"},
                    ])
                # 一级行业成分: 与三级"种子"交集最大(000998) → 一级板块应归类为 农林牧渔
                return pd.DataFrame([
                    {"证券代码": "000998", "证券名称": "隆平高科"},
                    {"证券代码": "600036", "证券名称": "招商银行"},
                    {"证券代码": "601318", "证券名称": "中国平安"},
                ])

        sw_industry._ak = FakeAK()
        try:
            result = sw_industry.build_sw_industry_map(force=True)
            self.assertEqual(len(result["board_stocks"]), 1)  # 僵尸板块被剔除
            self.assertIn("种子", result["board_stocks"])
            self.assertIn("600000.SH", result["stock_industry"])
            self.assertEqual(result["stock_industry"]["600000.SH"], "种子")
            # 一级板块归类: 种子 → 农林牧渔 (交集最大)
            self.assertEqual(result["board_parent"]["种子"], "农林牧渔")
        finally:
            sw_industry._ak = None

    def test_fetch_failure_fallback_old_cache(self):
        # 拉取失败 → 回退旧缓存(即使过期)
        data = {"fetch_date": "20260101", "stock_industry": {"600000.SH": "银行Ⅲ"},
                "stock_name": {"600000.SH": "浦发银行"}, "board_stocks": {"银行Ⅲ": ["600000.SH"]},
                "board_parent": {"银行Ⅲ": "银行"}}
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
            self.assertEqual(result["board_parent"]["银行Ⅲ"], "银行")
        finally:
            sw_industry._ak = None

    def test_old_cache_without_parent_backfilled(self):
        """旧缓存(无 board_parent) → 仅补拉一级成分股补全, 不重拉三级"""
        data = {"fetch_date": "20260813", "stock_industry": {"000998.SZ": "种子"},
                "stock_name": {"000998.SZ": "隆平高科"}, "board_stocks": {"种子": ["000998.SZ"]}}
        with open(sw_industry.SW_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

        class FakeAK:
            def sw_index_first_info(self):
                import pandas as pd
                return pd.DataFrame([
                    {"行业代码": "801010.SI", "行业名称": "农林牧渔", "成份个数": 100},
                    {"行业代码": "801030.SI", "行业名称": "基础化工", "成份个数": 100},
                ])

            def index_component_sw(self, symbol):
                import pandas as pd
                return pd.DataFrame([
                    {"证券代码": "000998", "证券名称": "隆平高科"},  # 与种子交集
                    {"证券代码": "600036", "证券名称": "招商银行"},
                ])

        sw_industry._ak = FakeAK()
        try:
            result = sw_industry.build_sw_industry_map(force=False)
            self.assertEqual(result["board_parent"]["种子"], "农林牧渔")
        finally:
            sw_industry._ak = None


class TestBoardParent(unittest.TestCase):
    """三级→一级板块归类(交集法)"""

    def test_basic(self):
        class FakeAK:
            def sw_index_first_info(self):
                import pandas as pd
                return pd.DataFrame([
                    {"行业代码": "801010.SI", "行业名称": "农林牧渔", "成份个数": 100},
                    {"行业代码": "801150.SI", "行业名称": "医药生物", "成份个数": 100},
                ])

            def index_component_sw(self, symbol):
                import pandas as pd
                if symbol == "801010":
                    return pd.DataFrame([
                        {"证券代码": "000998", "证券名称": "隆平高科"},
                        {"证券代码": "600598", "证券名称": "北大荒"},
                    ])
                return pd.DataFrame([
                    {"证券代码": "300009", "证券名称": "安科生物"},
                    {"证券代码": "600276", "证券名称": "恒瑞医药"},
                ])

        boards = {
            "种植业": ["000998.SZ", "600598.SH"],            # 全在农林牧渔
            "医疗研发外包": ["300009.SZ", "600276.SH"],      # 全在医药生物
            "混合板块": ["000998.SZ", "600276.SH"],          # 各一个 → 归到农林牧渔(先遍历到)
        }
        parent = sw_industry._build_board_parent(FakeAK(), boards)
        self.assertEqual(parent["种植业"], "农林牧渔")
        self.assertEqual(parent["医疗研发外包"], "医药生物")
        self.assertEqual(parent["混合板块"], "农林牧渔")

    def test_first_info_failure_returns_empty(self):
        class FakeAKFail:
            def sw_index_first_info(self):
                raise RuntimeError("down")

        self.assertEqual(sw_industry._build_board_parent(FakeAKFail(), {"x": ["600000.SH"]}), {})

    def test_empty_board(self):
        class FakeAK:
            def sw_index_first_info(self):
                import pandas as pd
                return pd.DataFrame([{"行业代码": "801010.SI", "行业名称": "农林牧渔", "成份个数": 100}])

            def index_component_sw(self, symbol):
                import pandas as pd
                return pd.DataFrame([])

        parent = sw_industry._build_board_parent(FakeAK(), {"空板块": []})
        self.assertEqual(parent["空板块"], "")


class TestSplitInOutParent(unittest.TestCase):
    """split_in_out 板块记录新增 parent(一级板块归类) 字段"""

    def test_parent_field_present(self):
        import pandas as pd
        import rps_calc
        df_rps = pd.DataFrame([
            {"industry": "医疗研发外包", "RPS5": 95.0, "RPS10": 88.0, "RPS20": 80.0,
             "chg5": 1.2, "chg10": 0.8, "chg20": 0.5,
             "rank5": 1, "rank10": 5, "rank20": 10,
             "rank_chg5": 1, "rank_chg10": 0, "rank_chg20": -1,
             "consecutive5": 3, "consecutive10": 0, "consecutive20": 0,
             "days90_5": 5, "days90_10": 0, "days90_20": 0},
        ])
        bp = {"医疗研发外包": "医药生物", "银行Ⅲ": "银行"}
        res = rps_calc.split_in_out(df_rps, 90, "RPS5", "rank5", "rank_chg5", "consecutive5",
                                    None, {}, set(), bp)
        self.assertEqual(len(res["in_list"]), 1)
        self.assertEqual(res["in_list"][0]["parent"], "医药生物")

    def test_parent_empty_when_no_map(self):
        import pandas as pd
        import rps_calc
        df_rps = pd.DataFrame([
            {"industry": "银行Ⅲ", "RPS5": 93.0, "RPS10": 0.0, "RPS20": 0.0,
             "chg5": 0.5, "chg10": 0.0, "chg20": 0.0,
             "rank5": 2, "rank10": None, "rank20": None,
             "rank_chg5": 0, "rank_chg10": 0, "rank_chg20": 0,
             "consecutive5": 1, "consecutive10": 0, "consecutive20": 0,
             "days90_5": 1, "days90_10": 0, "days90_20": 0},
        ])
        res = rps_calc.split_in_out(df_rps, 90, "RPS5", "rank5", "rank_chg5", "consecutive5",
                                    None, {}, set())
        self.assertEqual(res["in_list"][0]["parent"], "")


class TestStFilter(unittest.TestCase):
    """v5: 股票池统一剔除 ST/*ST/退市 (rps_calc.get_stock_industry 出口过滤)"""

    def test_filter_st_names(self):
        import pandas as pd
        import rps_calc
        df = pd.DataFrame({
            "ts_code": ["600001.SH", "600002.SH", "600003.SH", "600004.SH", "600005.SH"],
            "name": ["浦发银行", "ST康美", "*ST康得", "退市美都", "中国平安"],
            "industry": ["银行Ⅲ", "中药", "医药", "化工", "保险"],
        })
        out = rps_calc._filter_st(df)
        self.assertEqual(set(out["name"]), {"浦发银行", "中国平安"})

    def test_filter_st_null_and_empty(self):
        import pandas as pd
        import rps_calc
        self.assertIsNone(rps_calc._filter_st(None))
        self.assertEqual(len(rps_calc._filter_st(pd.DataFrame())), 0)

    def test_get_stock_industry_drops_st(self):
        """申万出口过滤: 含 ST/*ST/退 的股票不进入行业映射"""
        import pandas as pd
        import rps_calc
        from unittest import mock
        tmpdir = tempfile.mkdtemp()
        sw_map = {
            "stock_industry": {
                "600001.SH": "银行Ⅲ", "600002.SH": "中药", "600003.SH": "医药",
                "600004.SH": "化工", "600005.SH": "保险",
            },
            "stock_name": {
                "600001.SH": "浦发银行", "600002.SH": "ST康美", "600003.SH": "*ST康得",
                "600004.SH": "退市美都", "600005.SH": "中国平安",
            },
            "board_stocks": {},
        }
        with mock.patch.object(sw_industry, "build_sw_industry_map", return_value=sw_map), \
             mock.patch.object(rps_calc, "STATIC_DIR", Path(tmpdir)):
            df = rps_calc.get_stock_industry()
        names = set(df["name"])
        self.assertIn("浦发银行", names)
        self.assertIn("中国平安", names)
        for bad in ("ST康美", "*ST康得", "退市美都"):
            self.assertNotIn(bad, names)
        self.assertTrue((~df["name"].str.contains(r"ST|退", regex=True, na=False)).all())


if __name__ == "__main__":
    unittest.main()
