# -*- coding: utf-8 -*-
"""
单元测试：防止"成分股四列(买价/卖价/现量/涨速)为空"等回归问题

覆盖：
1. 实时快照纯函数（em_secid / tx_prefix / em_mkt_from_f13 / bj_date / normalize_rt_fields）
   - 北交所 f13=0 误判深市（历史 bug）
   - 四列归一化语义（现量取绝对值、涨速保留0、无效买卖价→None）
2. 生成的数据文件（static/rps_data.json 存在时）四列填充率回归检查

运行：python -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rps_calc


class TestEmSecid(unittest.TestCase):
    """东方财富 secid 前缀映射"""

    def test_sh(self):
        self.assertEqual(rps_calc.em_secid("600000.SH"), "1.600000")

    def test_sz(self):
        self.assertEqual(rps_calc.em_secid("000001.SZ"), "0.000001")

    def test_bj(self):
        # 北交所与深市同为 0. 前缀
        self.assertEqual(rps_calc.em_secid("920634.BJ"), "0.920634")


class TestTxPrefix(unittest.TestCase):
    """腾讯行情前缀映射"""

    def test_sh(self):
        self.assertEqual(rps_calc.tx_prefix("600000.SH"), "sh")

    def test_sz(self):
        self.assertEqual(rps_calc.tx_prefix("000001.SZ"), "sz")

    def test_bj(self):
        self.assertEqual(rps_calc.tx_prefix("920634.BJ"), "bj")


class TestEmMktFromF13(unittest.TestCase):
    """东财 f13 + 代码前缀 → 市场后缀（回归: 北交所被误判为深市导致四列丢失）"""

    def test_bj_920(self):
        self.assertEqual(rps_calc.em_mkt_from_f13(0, "920634"), "BJ")

    def test_bj_8(self):
        self.assertEqual(rps_calc.em_mkt_from_f13(0, "830799"), "BJ")

    def test_bj_4(self):
        self.assertEqual(rps_calc.em_mkt_from_f13(0, "430047"), "BJ")

    def test_bj_f13_83(self):
        self.assertEqual(rps_calc.em_mkt_from_f13(83, "430047"), "BJ")

    def test_sz(self):
        self.assertEqual(rps_calc.em_mkt_from_f13(0, "000001"), "SZ")

    def test_sz_300(self):
        self.assertEqual(rps_calc.em_mkt_from_f13(0, "300711"), "SZ")

    def test_sh(self):
        self.assertEqual(rps_calc.em_mkt_from_f13(1, "600000"), "SH")

    def test_unknown_f13(self):
        self.assertIsNone(rps_calc.em_mkt_from_f13(99, "600000"))


class TestBjDate(unittest.TestCase):
    """东财 f124 时间戳 → 北京时间"""

    def test_empty(self):
        self.assertEqual(rps_calc.bj_date(None), "")
        self.assertEqual(rps_calc.bj_date(""), "")

    def test_utc_timestamp_to_bj(self):
        # 2026-08-07 00:00 UTC = 北京时间 20260807 08:00
        ts = int(datetime(2026, 8, 7, tzinfo=timezone.utc).timestamp())
        self.assertEqual(rps_calc.bj_date(ts), "20260807")

    def test_bj_cross_midnight(self):
        # 2026-08-06 17:00 UTC = 北京时间 20260807 01:00（跨天）
        ts = int(datetime(2026, 8, 6, 17, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual(rps_calc.bj_date(ts), "20260807")


class TestNormalizeRtFields(unittest.TestCase):
    """四列归一化（回归保护: 四列不得因归一化错误而变空/语义错误）"""

    def test_valid_values_kept(self):
        self.assertEqual(rps_calc.normalize_rt_fields(10.5, 10.6, 100, 0.05), (10.5, 10.6, 100, 0.05))

    def test_vol_now_abs(self):
        # 东财现量带方向符号(负=主动卖), 必须取绝对值
        self.assertEqual(rps_calc.normalize_rt_fields(10.5, 10.6, -10376, 0.1)[2], 10376)

    def test_speed_zero_kept(self):
        # 涨速收盘后为0是正常值, 不得转 None
        self.assertEqual(rps_calc.normalize_rt_fields(10.5, 10.6, 100, 0)[3], 0)

    def test_dash_to_none(self):
        self.assertEqual(rps_calc.normalize_rt_fields("-", "-", "-", "-"), (None, None, None, None))

    def test_none_to_none(self):
        self.assertEqual(rps_calc.normalize_rt_fields(None, None, None, None), (None, None, None, None))

    def test_bid_le_zero_to_none(self):
        # 涨停封板无卖一(ask<=0)/跌停无买一(bid<=0) 属正常, 转 None
        self.assertEqual(rps_calc.normalize_rt_fields(0, -1, 500, 0.1)[0], None)
        self.assertEqual(rps_calc.normalize_rt_fields(0, -1, 500, 0.1)[1], None)

    def test_vol_now_zero_kept(self):
        # 停牌/无成交时现量为0, 保留而非转None(避免误报为空)
        self.assertEqual(rps_calc.normalize_rt_fields(1, 2, 0, 0)[2], 0)


@unittest.skipUnless(os.path.exists("static/rps_data.json"), "无数据文件, 跳过数据级检查")
class TestDataFileCoverage(unittest.TestCase):
    """生成的数据文件四列填充率回归检查（防止线上数据四列再次大面积为空）"""

    @classmethod
    def setUpClass(cls):
        with open("static/rps_data.json", encoding="utf-8") as f:
            data = json.load(f)
        recs = [b for k in ("rps5", "rps10", "rps20")
                for b in data[k]["in_list"] + data[k]["out_list"]]
        cls.all_stocks = [s for b in recs for s in b.get("stocks", [])]

    def _coverage(self, field):
        if not self.all_stocks:
            return 1.0
        n = sum(1 for s in self.all_stocks if s.get(field) is not None)
        return n / len(self.all_stocks)

    def test_bid_coverage(self):
        self.assertGreaterEqual(self._coverage("bid"), 0.9)

    def test_ask_coverage(self):
        self.assertGreaterEqual(self._coverage("ask"), 0.9)

    def test_vol_now_coverage(self):
        self.assertGreaterEqual(self._coverage("vol_now"), 0.9)

    def test_speed_coverage(self):
        self.assertGreaterEqual(self._coverage("speed"), 0.9)

    def test_bj_vol_now_coverage(self):
        # 回归: 北交所现量/涨速曾因 f13 误判全部为空
        bj = [s for s in self.all_stocks if s.get("ts_code", "").endswith(".BJ")]
        if bj:
            n = sum(1 for s in bj if s.get("vol_now") is not None)
            self.assertGreaterEqual(n / len(bj), 0.9)


if __name__ == "__main__":
    unittest.main()
