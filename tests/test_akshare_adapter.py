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
        self.spot_df = None
        self.zt_df = None
        self.ths_fina_df = None

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

    def stock_zh_a_spot_tx(self):
        return self.spot_df

    def stock_zt_pool_em(self, date=""):
        return self.zt_df

    def stock_financial_analysis_indicator(self, symbol="", start_year="2025"):
        return self.ths_fina_df


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


# ============================================================
# 市场广度 fetch_market_breadth_ak (2026-08-29 每日复盘 V2.0)
# ============================================================
class TestFetchMarketBreadthAk(unittest.TestCase):
    def setUp(self):
        import pandas as pd
        self.orig_ak = adapter.ak
        # _fetch_spot_tx 有全局内存缓存, 必须清空避免串用旧数据
        self.orig_cache = adapter._SPOT_TX_CACHE
        self.orig_cache_ts = adapter._SPOT_TX_CACHE_TS
        adapter._SPOT_TX_CACHE = None
        adapter._SPOT_TX_CACHE_TS = 0
        self.fake = _FakeAk()
        # 与腾讯 stock_zh_a_spot_tx 列结构一致 (code/zdf涨跌幅/turnover成交额万元)
        self.fake.spot_df = pd.DataFrame([
            {"code": "sh600000", "name": "A", "zdf": "5.20", "turnover": "10000000"},
            {"code": "sz000001", "name": "B", "zdf": "-3.10", "turnover": "20000000"},
            {"code": "sh600001", "name": "C", "zdf": "10.00", "turnover": "10000000"},
            {"code": "sz000002", "name": "D", "zdf": "-10.00", "turnover": "5000000"},
            {"code": "sh600002", "name": "E", "zdf": "0.00", "turnover": "5000000"},
        ])
        # 与东财 stock_zt_pool_em 列结构一致 (连板数)
        self.fake.zt_df = pd.DataFrame([
            {"代码": "000001", "名称": "A", "连板数": 1},
            {"代码": "000002", "名称": "B", "连板数": 2},
            {"代码": "000003", "名称": "C", "连板数": 5},
        ])
        adapter.ak = self.fake

    def tearDown(self):
        adapter.ak = self.orig_ak
        adapter._SPOT_TX_CACHE = self.orig_cache
        adapter._SPOT_TX_CACHE_TS = self.orig_cache_ts

    def test_full_breadth(self):
        result = adapter.fetch_market_breadth_ak()
        # zdf: 5.2 / -3.1 / 10.0 / -10.0 / 0.0
        self.assertEqual(result["up_count"], 2)
        self.assertEqual(result["down_count"], 2)
        self.assertEqual(result["limit_up"], 1)     # 10.0 ≥ 9.8
        self.assertEqual(result["limit_down"], 1)   # -10.0 ≤ -9.8
        # turnover 万元求和 5e7 → /1e4 = 5000.0 亿元
        self.assertEqual(result["total_amount"], 5000.0)
        self.assertEqual(result["max_board"], 5)

    def test_zt_pool_fail_keeps_spot(self):
        def boom(**kw):
            raise RuntimeError("network down")
        self.fake.stock_zt_pool_em = boom
        result = adapter.fetch_market_breadth_ak()
        self.assertEqual(result["up_count"], 2)
        self.assertEqual(result["limit_up"], 1)
        self.assertIsNone(result["max_board"])      # 独立降级

    def test_spot_empty_keeps_max_board(self):
        import pandas as pd
        self.fake.spot_df = pd.DataFrame()
        result = adapter.fetch_market_breadth_ak()
        self.assertIsNone(result["up_count"])
        self.assertIsNone(result["total_amount"])
        self.assertEqual(result["max_board"], 5)    # 涨停池不受影响

    def test_spot_raise_returns_others(self):
        def boom():
            raise RuntimeError("network down")
        self.fake.stock_zh_a_spot_tx = boom
        result = adapter.fetch_market_breadth_ak()
        self.assertIsNone(result["up_count"])
        self.assertEqual(result["max_board"], 5)

    def test_amount_yuan_unit(self):
        import pandas as pd
        # 元口径: 两市~1e12 → /1e8 → 亿元
        self.fake.spot_df = pd.DataFrame([
            {"code": "sh600000", "zdf": "1.0", "turnover": "1e12"},
            {"code": "sz000001", "zdf": "-1.0", "turnover": "1e12"},
        ])
        result = adapter.fetch_market_breadth_ak()
        self.assertEqual(result["total_amount"], 20000.0)  # 2e12/1e8

    def test_ak_not_available(self):
        orig = adapter._AK_OK
        adapter._AK_OK = False
        try:
            self.assertEqual(adapter.fetch_market_breadth_ak(), {})
        finally:
            adapter._AK_OK = orig
 

# ============================================================
# 同花顺财务指标 fetch_fina_ths_ak (2026-08-30 接入: 替代 fina_indicator)
# ============================================================
class TestFetchFinaThsAk(unittest.TestCase):
    def setUp(self):
        import pandas as pd
        self.orig_ak = adapter.ak
        self.fake = _FakeAk()
        # 同花顺 stock_financial_analysis_indicator 列结构 (升序: 首行=最早, 末行=最新)
        self.fake.ths_fina_df = pd.DataFrame([
            {"日期": "2025-03-31", "资产负债率(%)": 20.0, "销售净利率(%)": 55.0,
             "销售毛利率(%)": None, "加权净资产收益率(%)": 12.0, "每股经营性现金流(元)": 5.0,
             "每股净资产_调整后(元)": 190.0, "净利润增长率(%)": 8.0,
             "主营业务收入增长率(%)": 6.0, "扣除非经常性损益后的净利润(元)": 2.0e10},
            {"日期": "2025-06-30", "资产负债率(%)": 21.0, "销售净利率(%)": 56.0,
             "销售毛利率(%)": None, "加权净资产收益率(%)": 13.0, "每股经营性现金流(元)": 6.0,
             "每股净资产_调整后(元)": 195.0, "净利润增长率(%)": 9.0,
             "主营业务收入增长率(%)": 7.0, "扣除非经常性损益后的净利润(元)": 2.1e10},
            {"日期": "2025-09-30", "资产负债率(%)": 22.0, "销售净利率(%)": 57.0,
             "销售毛利率(%)": None, "加权净资产收益率(%)": 14.0, "每股经营性现金流(元)": 7.0,
             "每股净资产_调整后(元)": 200.0, "净利润增长率(%)": 10.0,
             "主营业务收入增长率(%)": 8.0, "扣除非经常性损益后的净利润(元)": 2.2e10},
            {"日期": "2025-12-31", "资产负债率(%)": 23.0, "销售净利率(%)": 58.0,
             "销售毛利率(%)": None, "加权净资产收益率(%)": 15.0, "每股经营性现金流(元)": 8.0,
             "每股净资产_调整后(元)": 205.0, "净利润增长率(%)": 11.0,
             "主营业务收入增长率(%)": 9.0, "扣除非经常性损益后的净利润(元)": 2.3e10},
            {"日期": "2026-03-31", "资产负债率(%)": 24.0, "销售净利率(%)": 59.0,
             "销售毛利率(%)": None, "加权净资产收益率(%)": 16.0, "每股经营性现金流(元)": 9.0,
             "每股净资产_调整后(元)": 210.0, "净利润增长率(%)": 12.0,
             "主营业务收入增长率(%)": 10.0, "扣除非经常性损益后的净利润(元)": 2.4e10},
            {"日期": "2026-06-30", "资产负债率(%)": 25.0, "销售净利率(%)": 60.0,
             "销售毛利率(%)": None, "加权净资产收益率(%)": 17.0, "每股经营性现金流(元)": 10.0,
             "每股净资产_调整后(元)": 215.0, "净利润增长率(%)": 13.0,
             "主营业务收入增长率(%)": 11.0, "扣除非经常性损益后的净利润(元)": 2.5e10},
        ])
        adapter.ak = self.fake
        # 清缓存避免跨测试干扰
        adapter._THS_FINA_CACHE.clear()
        adapter._THS_FINA_CACHE_TS = 0

    def tearDown(self):
        adapter.ak = self.orig_ak
        adapter._THS_FINA_CACHE.clear()
        adapter._THS_FINA_CACHE_TS = 0

    def test_parse_latest_period(self):
        r = adapter.fetch_fina_ths_ak("600519.SH")
        v = r["600519.SH"]
        # 末行=最新期 (升序接口, 2026-08-30 修复 iloc[-1])
        self.assertEqual(v["end_date"], "20260630")
        self.assertEqual(v["debt_to_assets"], 25.0)
        self.assertEqual(v["net_margin"], 60.0)
        self.assertEqual(v["roe"], 17.0)
        self.assertEqual(v["ocf_ps"], 10.0)
        self.assertEqual(v["bps"], 215.0)
        self.assertEqual(v["netprofit_yoy"], 13.0)
        self.assertEqual(v["revenue_yoy"], 11.0)
        self.assertEqual(v["n_income_attr_parent"], 2.5e10)
        self.assertEqual(v["net_profit"], 2.5e10)  # 同花顺无总净利, 用扣非(真实值)
        self.assertIsNone(v["gross_margin"])  # 该接口销售毛利率恒 NaN
        self.assertIsNone(v["dt_netprofit_yoy"])
        self.assertIsNone(v["roic"])

    def test_raise_returns_empty(self):
        def boom(**kw):
            raise RuntimeError("network down")
        self.fake.stock_financial_analysis_indicator = boom
        self.assertEqual(adapter.fetch_fina_ths_ak("600519.SH"), {})

    def test_ak_not_available(self):
        orig = adapter._AK_OK
        adapter._AK_OK = False
        try:
            self.assertEqual(adapter.fetch_fina_ths_ak("600519.SH"), {})
        finally:
            adapter._AK_OK = orig

    def test_digit_symbol(self):
        # 传 600519/600519.SH 均归一为 600519 → 600519.SH
        r = adapter.fetch_fina_ths_ak("600519")
        self.assertIn("600519.SH", r)


if __name__ == "__main__":
    unittest.main()
