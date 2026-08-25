# -*- coding: utf-8 -*-
"""
单元测试：RPS 看板四级下钻层级数据派生 (level_hierarchy.py)
覆盖: 一级聚合chg/百分位RPS / 二级组内RPS / 连续天数 / rank_change /
      成分股合并(rps_data优先+board_stocks补齐) / 一级status聚合 / 组装完整性 / 幂等
运行: python -m unittest tests.test_level_hierarchy -v
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import level_hierarchy as lh


def make_history(dates):
    """dates: [(date, {industry: {chg5,chg10,chg20}})] → rps_history 结构
    RPS/rank 字段由派生逻辑自行计算, 测试只需给 chg"""
    out = {}
    for date, day in dates:
        out[date] = {ind: {"chg5": v[0], "chg10": v[1], "chg20": v[2]} for ind, v in day.items()}
    return out


def make_cache(board_parent, board_stocks=None, stock_name=None):
    return {"board_parent": board_parent, "board_stocks": board_stocks or {},
            "stock_name": stock_name or {}}


# ========================== 工具 ==========================

class TestUtils(unittest.TestCase):
    def test_avg(self):
        self.assertEqual(lh._avg([1, 2, 3]), 2.0)
        self.assertEqual(lh._avg([1, None, 3]), 2.0)
        self.assertIsNone(lh._avg([]))
        self.assertIsNone(lh._avg([None]))

    def test_round_to_int(self):
        self.assertEqual(lh._round(96.77), 96.8)
        self.assertIsNone(lh._round(None))
        self.assertEqual(lh._to_int(5), 5)
        self.assertIsNone(lh._to_int(None))


# ========================== 一级聚合 ==========================

class TestBuildLevel1Daily(unittest.TestCase):
    def test_aggregation_and_percentile(self):
        hist = make_history([
            ("20260810", {"半导体设备": (8, 6, 4), "数字芯片设计": (6, 5, 3),
                          "股份制银行Ⅲ": (2, 1, 0), "城商行Ⅲ": (0, -1, -2)}),
        ])
        bp = {"半导体设备": "电子", "数字芯片设计": "电子",
              "股份制银行Ⅲ": "银行", "城商行Ⅲ": "银行"}
        res = lh.build_level1_daily(hist, bp)
        day = res["20260810"]
        # 一级聚合 = 其下二级 chg 等权平均
        self.assertAlmostEqual(day["电子"]["chg5"], 7.0)     # (8+6)/2
        self.assertAlmostEqual(day["电子"]["chg10"], 5.5)
        self.assertAlmostEqual(day["银行"]["chg5"], 1.0)     # (2+0)/2
        # 一级百分位 RPS: 2个一级, 电子 rank1 → (1-1/2)*100=50
        self.assertAlmostEqual(day["电子"]["RPS5"], 50.0)
        self.assertEqual(day["电子"]["rank5"], 1)
        self.assertAlmostEqual(day["银行"]["RPS5"], 0.0)
        self.assertEqual(day["银行"]["rank5"], 2)

    def test_ignore_industry_without_parent(self):
        hist = make_history([("20260810", {"半导体设备": (8, 6, 4), "孤立板块": (99, 99, 99)})])
        bp = {"半导体设备": "电子"}
        res = lh.build_level1_daily(hist, bp)
        self.assertEqual(list(res["20260810"].keys()), ["电子"])


# ========================== 二级组内 RPS ==========================

class TestBuildLevel2Daily(unittest.TestCase):
    def test_group_relative_rps(self):
        hist = make_history([
            ("20260810", {"半导体设备": (10, 8, 6), "数字芯片设计": (5, 4, 3), "分立器件": (0, -1, -2),
                          "股份制银行Ⅲ": (3, 2, 1)}),
        ])
        bp = {"半导体设备": "电子", "数字芯片设计": "电子", "分立器件": "电子",
              "股份制银行Ⅲ": "银行"}
        res = lh.build_level2_daily(hist, bp)
        day = res["20260810"]
        # 电子组内 3 个成员: rank1→66.67, rank2→33.33, rank3→0 (组内而非全市场)
        self.assertAlmostEqual(day["半导体设备"]["RPS5"], 66.6667, places=2)
        self.assertEqual(day["半导体设备"]["rank5"], 1)
        self.assertAlmostEqual(day["数字芯片设计"]["RPS5"], 33.3333, places=2)
        self.assertAlmostEqual(day["分立器件"]["RPS5"], 0.0)
        # 银行组独立: 仅1个成员 → rank1 → RPS5 = 0.0 (1/1 覆盖, 组内排名无区分度)
        self.assertAlmostEqual(day["股份制银行Ⅲ"]["RPS5"], 0.0)
        self.assertEqual(day["股份制银行Ⅲ"]["rank5"], 1)


# ========================== 连续天数 ==========================

class TestConsecutive(unittest.TestCase):
    def test_consecutive_backward(self):
        # 3 个一级, 电子在强日 rank1 (RPS5=66.7), 弱日 rank3 (RPS5=0); 阈值取 50 可区分
        hist = make_history([
            ("20260806", {"半导体设备": (10, 8, 6), "银行A": (1, 1, 1), "消费A": (0, 0, 0)}),
            ("20260807", {"半导体设备": (10, 8, 6), "银行A": (1, 1, 1), "消费A": (0, 0, 0)}),
            ("20260810", {"半导体设备": (10, 8, 6), "银行A": (1, 1, 1), "消费A": (0, 0, 0)}),
            ("20260811", {"半导体设备": (10, 8, 6), "银行A": (1, 1, 1), "消费A": (0, 0, 0)}),
        ])
        bp = {"半导体设备": "电子", "银行A": "银行", "消费A": "消费"}
        daily = lh.build_level1_daily(hist, bp)
        dates = sorted(hist.keys())
        self.assertEqual(lh._consecutive(daily, dates, "电子", "RPS5", "20260811", threshold=50), 4)
        # 隔断: 中间某日电子跌到 rank3 (RPS5=0<50) → 中断
        hist2 = make_history([
            ("20260807", {"半导体设备": (10, 8, 6), "银行A": (1, 1, 1), "消费A": (0, 0, 0)}),
            ("20260810", {"半导体设备": (0, 0, 0), "银行A": (1, 1, 1), "消费A": (2, 2, 2)}),
            ("20260811", {"半导体设备": (10, 8, 6), "银行A": (1, 1, 1), "消费A": (0, 0, 0)}),
        ])
        daily2 = lh.build_level1_daily(hist2, bp)
        dates2 = sorted(hist2.keys())
        self.assertEqual(lh._consecutive(daily2, dates2, "电子", "RPS5", "20260811", threshold=50), 1)
        self.assertEqual(lh._consecutive(daily2, dates2, "电子", "RPS5", "20260807", threshold=50), 1)


# ========================== 成分股合并 ==========================

class TestCollectStocks(unittest.TestCase):
    def test_rps_data_priority(self):
        rps_data = {"rps5": {"in_list": [{"name": "半导体设备", "stocks": [
            {"ts_code": "A", "name": "甲", "price": 10.0, "pct_chg": 1.0}]}]}}
        bs = {"半导体设备": ["A", "B", "C"], "其他板块": ["D"]}
        sn = {"D": "丁"}
        res = lh.collect_stocks(rps_data, bs, sn)
        # 在榜板块优先用 rps_data 成分股 (即使 board_stocks 有更多成员)
        self.assertEqual([s["ts_code"] for s in res["半导体设备"]], ["A"])
        # 不在榜板块用 board_stocks 补齐 + stock_name 映射
        self.assertEqual(res["其他板块"][0]["name"], "丁")
        self.assertIsNone(res["其他板块"][0]["price"])


# ========================== 组装完整性 ==========================

class TestBuildHierarchy(unittest.TestCase):
    def setUp(self):
        self.hist = make_history([
            ("20260807", {"半导体设备": (10, 8, 6), "数字芯片设计": (5, 4, 3),
                          "股份制银行Ⅲ": (3, 2, 1), "城商行Ⅲ": (1, 0, -1)}),
            ("20260810", {"半导体设备": (9, 7, 5), "数字芯片设计": (8, 6, 4),
                          "股份制银行Ⅲ": (2, 1, 0), "城商行Ⅲ": (3, 2, 1)}),
        ])
        self.bp = {"半导体设备": "电子", "数字芯片设计": "电子",
                   "股份制银行Ⅲ": "银行", "城商行Ⅲ": "银行"}
        self.bs = {"半导体设备": ["A", "B"], "数字芯片设计": ["C"],
                   "股份制银行Ⅲ": ["D"], "城商行Ⅲ": ["E", "F"]}
        self.sn = {c: c for c in "ABCDEF"}

    def test_level1_and_level2_counts(self):
        data = lh.build_hierarchy(self.hist, self.bp, self.bs, self.sn, None)
        self.assertEqual(data["level1_count"], 2)
        self.assertEqual(data["level2_count"], 4)
        self.assertEqual(data["update_date"], "20260810")

    def test_level1_sorted_and_status_aggregation(self):
        # 电子: 20260810 chg5=(9+8)/2=8.5; 银行: (2+3)/2=2.5 → 电子 rank1
        rps_data = {"rps5": {"in_list": [{"name": "半导体设备", "stocks": [
            {"ts_code": "A"}, {"ts_code": "B"}]}]}}
        data = lh.build_hierarchy(self.hist, self.bp, self.bs, self.sn, rps_data)
        self.assertEqual(data["level1"][0]["name"], "电子")
        self.assertEqual(data["level1"][0]["display_rank"], 1)
        self.assertEqual(data["level1"][0]["level2_count"], 2)
        self.assertEqual(data["level1"][0]["stock_count"], 3)  # 半导体(A,B) + 数字(C)
        # 半导体设备在榜(连续≥2) → 电子 status=在榜
        self.assertEqual(data["level1"][0]["status"], "在榜")
        # rank_change: 电子 20260810 rank1, 前日 rank1 → 0
        self.assertEqual(data["level1"][0]["rank5_change"], 0)

    def test_level2_group_sorted_and_rank_change(self):
        data = lh.build_hierarchy(self.hist, self.bp, self.bs, self.sn, None)
        l2 = data["level2"]
        # 银行组内: 城商行Ⅲ chg5=3 > 股份制银行Ⅲ chg5=2 → 城商行Ⅲ rank1
        bank = [r for r in l2 if r["parent"] == "银行"]
        self.assertEqual(bank[0]["name"], "城商行Ⅲ")
        self.assertEqual(bank[0]["display_rank"], 1)
        self.assertEqual(bank[1]["name"], "股份制银行Ⅲ")
        # rank_change: 城商行Ⅲ 前日 rank2 → 今日 rank1 → +1
        self.assertEqual(bank[0]["rank5_change"], 1)
        self.assertEqual(bank[0]["stock_count"], 2)

    def test_stocks_map(self):
        data = lh.build_hierarchy(self.hist, self.bp, self.bs, self.sn, None)
        self.assertEqual(len(data["stocks"]), 4)
        self.assertEqual(len(data["stocks"]["城商行Ⅲ"]), 2)

    def test_prior_date_none(self):
        data = lh.build_hierarchy(self.hist, self.bp, self.bs, self.sn, None, latest_date="20260807")
        self.assertEqual(data["update_date"], "20260807")
        self.assertIsNone(data["level1"][0]["rank5_change"])


# ========================== 幂等保存 ==========================

class TestBuildAndSave(unittest.TestCase):
    def test_generate_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            hist = make_history([
                ("20260810", {"半导体设备": (10, 8, 6), "股份制银行Ⅲ": (3, 2, 1)}),
            ])
            bp = {"半导体设备": "电子", "股份制银行Ⅲ": "银行"}
            sw = make_cache(bp)
            rps_data = {"update_date": "20260810"}
            with mock.patch.object(lh, "STATIC_DIR", tmp), \
                 mock.patch.object(lh, "LEVEL_FILE", tmp / "level_hierarchy.json"):
                self.assertTrue(lh.build_and_save(hist, bp, sw["board_stocks"], sw["stock_name"], rps_data))
                data = json.loads((tmp / "level_hierarchy.json").read_text(encoding="utf-8"))
                self.assertEqual(data["level1_count"], 2)
                # 幂等: 同日再次调用 → 跳过不再写
                self.assertTrue(lh.build_and_save(hist, bp, sw["board_stocks"], sw["stock_name"], rps_data))
                self.assertEqual(json.loads((tmp / "level_hierarchy.json").read_text(encoding="utf-8"))["level1_count"], 2)

    def test_missing_input_false(self):
        # 静态文件全部缺失 → 返回 False (mock _load_json 防止读到本地真实缓存)
        with mock.patch.object(lh, "_load_json", return_value=None):
            self.assertFalse(lh.build_and_save(None, None, None, None, None))


if __name__ == "__main__":
    unittest.main()
