# -*- coding: utf-8 -*-
"""
单元测试：AKShare 数据源适配层 (akshare_adapter.py) — 数据源增强 (2026-08-23)
覆盖: 龙虎榜 fetch_lhb_ak (净买额/上榜原因/代码转换) + 北向广度 fetch_hsgt_ak (上涨占比)
方式: mock akshare 模块返回伪 DataFrame, 验证解析/降级逻辑 (不依赖真实网络)
运行: python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import akshare_adapter as adapter


class _FakeAk:
    """伪 akshare: 挂载被测接口, 其余访问返回 None"""
    def __init__(self):
        self.lhb_df = None
        self.hsgt_df = None
        self.news_df = None
        self.hot_df = None
        self.dzjy_df = None

    def stock_lhb_detail_em(self, start_date="", end_date=""):
        return self.lhb_df

    def stock_hsgt_fund_flow_summary_em(self):
        return self.hsgt_df

    def stock_news_em(self, symbol=""):
        return self.news_df

    def stock_hot_rank_em(self):
        return self.hot_df

    def stock_dzjy_mrmx(self, symbol="", start_date="", end_date=""):
        return self.dzjy_df


class TestFetchLhbAk(unittest.TestCase):
    def setUp(self):
        import pandas as pd
        self.orig_ak = adapter.ak
        self.fake = _FakeAk()
        # 与东财 stock_lhb_detail_em 列结构一致 (代码/名称/龙虎榜净买额/上榜原因)
        self.fake.lhb_df = pd.DataFrame([
            {"代码": "000017", "名称": "深中华A", "龙虎榜净买额": 4599091.67,
             "上榜原因": "连续三个交易日内，涨幅偏离值累计达到20%的证券"},
            {"代码": "000505", "名称": "京粮控股", "龙虎榜净买额": -7155484.55,
             "上榜原因": "日跌幅偏离值达到7%的前5只证券"},
            {"代码": "600519", "名称": "贵州茅台", "龙虎榜净买额": 1000.0,
             "上榜原因": "无"},
        ])
        adapter.ak = self.fake

    def tearDown(self):
        adapter.ak = self.orig_ak

    def test_parse_net_buy_sell(self):
        result = adapter.fetch_lhb_ak("20260821")
        self.assertEqual(len(result), 3)
        self.assertIn("000017.SZ", result)
        self.assertGreater(result["000017.SZ"]["lhb_net"], 0)
        self.assertIn("涨幅偏离", result["000017.SZ"]["reason"])
        self.assertIn("000505.SZ", result)
        self.assertLess(result["000505.SZ"]["lhb_net"], 0)

    def test_sh_code_conversion(self):
        result = adapter.fetch_lhb_ak("20260821")
        self.assertIn("600519.SH", result)  # 沪市 600 → SH

    def test_empty_df_returns_empty(self):
        self.fake.lhb_df = self.fake.lhb_df.iloc[0:0]
        self.assertEqual(adapter.fetch_lhb_ak("20260821"), {})

    def test_raise_returns_empty(self):
        def boom(**kw):
            raise RuntimeError("network down")
        self.fake.stock_lhb_detail_em = boom
        self.assertEqual(adapter.fetch_lhb_ak("20260821"), {})

    def test_ak_not_available(self):
        orig = adapter._AK_OK
        adapter._AK_OK = False
        try:
            self.assertEqual(adapter.fetch_lhb_ak("20260821"), {})
        finally:
            adapter._AK_OK = orig


class TestFetchHsgtAk(unittest.TestCase):
    def setUp(self):
        import pandas as pd
        self.orig_ak = adapter.ak
        self.fake = _FakeAk()
        # 与东财 stock_hsgt_fund_flow_summary_em 列结构一致 (资金方向/上涨数/下跌数)
        self.fake.hsgt_df = pd.DataFrame([
            {"资金方向": "北向", "板块": "沪股通", "上涨数": 684, "持平数": 53, "下跌数": 902},
            {"资金方向": "南向", "板块": "港股通(沪)", "上涨数": 437, "持平数": 15, "下跌数": 168},
            {"资金方向": "北向", "板块": "深股通", "上涨数": 883, "持平数": 53, "下跌数": 939},
            {"资金方向": "南向", "板块": "港股通(深)", "上涨数": 437, "持平数": 15, "下跌数": 168},
        ])
        adapter.ak = self.fake

    def tearDown(self):
        adapter.ak = self.orig_ak

    def test_north_breadth_sum(self):
        result = adapter.fetch_hsgt_ak()
        self.assertEqual(result["north_up"], 684 + 883)       # 仅北向两行求和
        self.assertEqual(result["north_down"], 902 + 939)
        self.assertAlmostEqual(result["north_ratio"],
                               (684 + 883) / (684 + 883 + 902 + 939), places=4)

    def test_ratio_range(self):
        result = adapter.fetch_hsgt_ak()
        self.assertGreaterEqual(result["north_ratio"], 0.0)
        self.assertLessEqual(result["north_ratio"], 1.0)

    def test_no_north_rows_returns_empty(self):
        import pandas as pd
        self.fake.hsgt_df = pd.DataFrame([
            {"资金方向": "南向", "板块": "港股通(沪)", "上涨数": 437, "下跌数": 168},
        ])
        self.assertEqual(adapter.fetch_hsgt_ak(), {})

    def test_raise_returns_empty(self):
        def boom():
            raise RuntimeError("network down")
        self.fake.stock_hsgt_fund_flow_summary_em = boom
        self.assertEqual(adapter.fetch_hsgt_ak(), {})

    def test_ak_not_available(self):
        orig = adapter._AK_OK
        adapter._AK_OK = False
        try:
            self.assertEqual(adapter.fetch_hsgt_ak(), {})
        finally:
            adapter._AK_OK = orig


# ============================================================
# 个股新闻 fetch_stock_news_ak (2026-08-23 消息面: 金融新闻真实性甄别)
# ============================================================
class TestFetchStockNewsAk(unittest.TestCase):
    def setUp(self):
        import pandas as pd
        self.orig_ak = adapter.ak
        self.fake = _FakeAk()
        # 与东财 stock_news_em 列结构一致 (新闻标题/新闻内容/发布时间/文章来源)
        self.fake.news_df = pd.DataFrame([
            {"关键词": "600519", "新闻标题": "贵州茅台发布2026年半年度报告",
             "新闻内容": "营业总收入922.78亿元", "发布时间": "2026-08-15 10:11:51",
             "文章来源": "界面新闻", "新闻链接": "http://a"},
            {"关键词": "600519", "新闻标题": "贵州茅台股东减持计划公告",
             "新闻内容": "拟减持不超过2%", "发布时间": "2026-08-21 09:00:00",
             "文章来源": "上海证券报", "新闻链接": "http://b"},
        ])
        adapter.ak = self.fake

    def tearDown(self):
        adapter.ak = self.orig_ak

    def test_parse_news(self):
        news = adapter.fetch_stock_news_ak("600519")
        self.assertEqual(len(news), 2)
        self.assertEqual(news[0]["source"], "界面新闻")
        self.assertIn("半年度报告", news[0]["title"])
        self.assertEqual(news[1]["source"], "上海证券报")

    def test_ak_not_available(self):
        orig = adapter._AK_OK
        adapter._AK_OK = False
        try:
            self.assertEqual(adapter.fetch_stock_news_ak("600519"), [])
        finally:
            adapter._AK_OK = orig

    def test_raise_returns_empty(self):
        def boom(**kw):
            raise RuntimeError("network down")
        self.fake.stock_news_em = boom
        self.assertEqual(adapter.fetch_stock_news_ak("600519"), [])

    def test_empty_df_returns_empty(self):
        self.fake.news_df = self.fake.news_df.iloc[0:0]
        self.assertEqual(adapter.fetch_stock_news_ak("600519"), [])


# ============================================================
# 人气榜 fetch_hot_rank_ak (2026-08-23 消息面: 散户情绪/关注度)
# ============================================================
class TestFetchHotRankAk(unittest.TestCase):
    def setUp(self):
        import pandas as pd
        self.orig_ak = adapter.ak
        self.fake = _FakeAk()
        # 与东财 stock_hot_rank_em 列结构一致 (当前排名/代码/股票名称)
        self.fake.hot_df = pd.DataFrame([
            {"当前排名": 1, "代码": "SZ002015", "股票名称": "协鑫能科", "最新价": 14.15, "涨跌幅": 0.86},
            {"当前排名": 2, "代码": "SZ002491", "股票名称": "通鼎互联", "最新价": 21.20, "涨跌幅": 10.02},
            {"当前排名": 50, "代码": "SH600460", "股票名称": "士兰微", "最新价": 35.89, "涨跌幅": 9.99},
        ])
        adapter.ak = self.fake

    def tearDown(self):
        adapter.ak = self.orig_ak

    def test_parse_rank(self):
        result = adapter.fetch_hot_rank_ak()
        self.assertEqual(result["002015.SZ"], 1)
        self.assertEqual(result["002491.SZ"], 2)
        self.assertEqual(result["600460.SH"], 50)

    def test_empty_df_returns_empty(self):
        self.fake.hot_df = self.fake.hot_df.iloc[0:0]
        self.assertEqual(adapter.fetch_hot_rank_ak(), {})

    def test_raise_returns_empty(self):
        def boom():
            raise RuntimeError("network down")
        self.fake.stock_hot_rank_em = boom
        self.assertEqual(adapter.fetch_hot_rank_ak(), {})


# ============================================================
# 大宗交易 fetch_dzjy_ak (2026-08-23 消息面: 主力/产业资本真实行为)
# ============================================================
class TestFetchDzjyAk(unittest.TestCase):
    def setUp(self):
        import pandas as pd
        self.orig_ak = adapter.ak
        self.fake = _FakeAk()
        # 与东财 stock_dzjy_mrmx 列结构一致 (证券代码/折溢率/成交额/买方营业部)
        self.fake.dzjy_df = pd.DataFrame([
            {"交易日期": "2026-08-21", "证券代码": "001309", "证券简称": "德明利", "折溢率": 0.0,
             "成交额": 109965300, "买方营业部": "机构专用"},
            {"交易日期": "2026-08-21", "证券代码": "001388", "证券简称": "信通电子", "折溢率": -0.0938,
             "成交额": 3091200, "买方营业部": "中银国际证券股份有限公司南京洪武北路证券营业部"},
        ])
        adapter.ak = self.fake

    def tearDown(self):
        adapter.ak = self.orig_ak

    def test_parse_dzjy(self):
        result = adapter.fetch_dzjy_ak("20260821")
        self.assertIn("001309.SZ", result)
        self.assertTrue(result["001309.SZ"]["inst_buy"])          # 机构专用买入
        self.assertAlmostEqual(result["001309.SZ"]["avg_discount"], 0.0, places=3)
        self.assertIn("001388.SZ", result)
        self.assertLess(result["001388.SZ"]["avg_discount"], 0)   # 折价
        self.assertFalse(result["001388.SZ"]["inst_buy"])

    def test_avg_discount_aggregate(self):
        import pandas as pd
        self.fake.dzjy_df = pd.DataFrame([
            {"交易日期": "2026-08-21", "证券代码": "000001", "证券简称": "A", "折溢率": -0.10,
             "成交额": 100, "买方营业部": "X"},
            {"交易日期": "2026-08-21", "证券代码": "000001", "证券简称": "A", "折溢率": 0.0,
             "成交额": 100, "买方营业部": "Y"},
        ])
        result = adapter.fetch_dzjy_ak("20260821")
        self.assertEqual(result["000001.SZ"]["count"], 2)
        self.assertAlmostEqual(result["000001.SZ"]["avg_discount"], -0.05, places=4)
        self.assertEqual(result["000001.SZ"]["total_amt"], 200)

    def test_empty_df_returns_empty(self):
        self.fake.dzjy_df = self.fake.dzjy_df.iloc[0:0]
        self.assertEqual(adapter.fetch_dzjy_ak("20260821"), {})

    def test_raise_returns_empty(self):
        def boom(**kw):
            raise RuntimeError("network down")
        self.fake.stock_dzjy_mrmx = boom
        self.assertEqual(adapter.fetch_dzjy_ak("20260821"), {})


if __name__ == "__main__":
    unittest.main()
