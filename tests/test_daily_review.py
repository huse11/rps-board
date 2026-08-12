# -*- coding: utf-8 -*-
"""
单元测试：每日 RPS 复盘生成模块 (daily_review.py)
覆盖: 市场定调 / 三周期共振 / 梯队分层 / 轮动解读 / 健康度 / 风险预警 /
      次日清单 / 情绪周期 / 风格归因 / markdown渲染 / 幂等
运行: python -m unittest tests.test_daily_review -v
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import daily_review as dr


def make_sector(name, rps=95.0, rank=1, rank_change=0, continuous=1,
                stock_count=10, rps50_above_90=5, status="在榜"):
    return {"name": name, "status": status, "RPS5": rps, "RPS10": rps,
            "RPS20": rps, "rank": rank, "rank_change": rank_change,
            "continuous": continuous, "days_60": 1, "stock_count": stock_count,
            "rps50_above_90": rps50_above_90}


def make_level(sectors, out_list=None):
    return {"in_list": sectors, "out_list": out_list or [],
            "current_names": [s["name"] for s in sectors]}


def make_rps_data(update_date="20260810", prev_date="20260807", rps5=None,
                  rps10=None, rps20=None):
    return {"schema_version": 2, "update_date": update_date,
            "prev_date": prev_date, "total_industries": 110, "threshold": 90,
            "rps5": rps5, "rps10": rps10, "rps20": rps20}


def make_history(spec):
    """spec: {date: {industry: {RPS5, rank5}}} → history dict"""
    return spec


# ========================== 方向1: 市场定调 ==========================

class TestHighRpsCounts(unittest.TestCase):
    def test_counts_per_date(self):
        hist = {
            "20260807": {"A": {"RPS5": 95}, "B": {"RPS5": 80}},
            "20260810": {"A": {"RPS5": 95}, "B": {"RPS5": 92}, "C": {"RPS5": 91}},
        }
        c = dr.high_rps_counts(hist, "RPS5", 90)
        self.assertEqual(list(c.keys()), ["20260807", "20260810"])
        self.assertEqual(c["20260807"], 1)
        self.assertEqual(c["20260810"], 3)

    def test_empty_history(self):
        self.assertEqual(dr.high_rps_counts(None, "RPS5"), {})
        self.assertEqual(dr.high_rps_counts({}, "RPS5"), {})


class TestIndexMa20(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = dr.STATIC_DIR
        dr.STATIC_DIR = __import__("pathlib").Path(self._tmp.name)
        os.makedirs(os.path.join(self._tmp.name, "index_cache"), exist_ok=True)

    def tearDown(self):
        dr.STATIC_DIR = self._orig
        self._tmp.cleanup()

    def _write_index(self, closes):
        bars = [{"date": "2026%02d" % i, "close": c} for i, c in enumerate(closes)]
        with open(os.path.join(self._tmp.name, "index_cache", "000001.SH.json"),
                  "w", encoding="utf-8") as f:
            json.dump(bars, f)

    def test_ma20_above(self):
        closes = list(range(100, 130))  # 30 条, 末值129 > ma20
        self._write_index(closes)
        r = dr.index_ma20_status()
        self.assertTrue(r["ok"])
        self.assertTrue(r["above"])
        self.assertAlmostEqual(r["ma20"], sum(closes[-20:]) / 20, places=6)
        self.assertEqual(r["close"], 129)

    def test_ma20_below(self):
        closes = list(range(100, 120)) + [50]  # 末值跳水
        self._write_index(closes)
        r = dr.index_ma20_status()
        self.assertFalse(r["above"])

    def test_insufficient_data(self):
        self._write_index(list(range(10)))
        self.assertIsNone(dr.index_ma20_status())

    def test_missing_file(self):
        self.assertIsNone(dr.index_ma20_status())


class TestMarketTone(unittest.TestCase):
    def _hist(self, dates_high):
        """dates_high: [(date, high_count)]"""
        hist = {}
        for date, n in dates_high:
            sectors = {}
            for i in range(n):
                sectors["板块%d" % i] = {"RPS5": 95}
            hist[date] = sectors
        return hist

    def _rps(self):
        lv = make_level([make_sector("黄金", rank=1)])
        return make_rps_data(rps5=lv, rps10=lv, rps20=lv)

    def test_strong_when_increase_and_index_above(self):
        with mock.patch.object(dr, "index_ma20_status",
                               return_value={"ok": True, "close": 100, "ma20": 99, "above": True, "days": 30}):
            t = dr.market_tone(self._rps(), self._hist([("20260807", 10), ("20260810", 15)]))
        self.assertEqual(t["tone"], "强")
        self.assertEqual(t["high5"], 15)
        self.assertEqual(t["diff5"], 5)

    def test_weak_when_big_decrease(self):
        with mock.patch.object(dr, "index_ma20_status",
                               return_value={"ok": True, "close": 100, "ma20": 99, "above": True, "days": 30}):
            t = dr.market_tone(self._rps(), self._hist([("20260807", 15), ("20260810", 9)]))
        self.assertEqual(t["tone"], "弱")

    def test_weak_when_total_low(self):
        with mock.patch.object(dr, "index_ma20_status",
                               return_value={"ok": True, "close": 100, "ma20": 99, "above": True, "days": 30}):
            t = dr.market_tone(self._rps(), self._hist([("20260807", 2), ("20260810", 5)]))
        self.assertEqual(t["tone"], "弱")
        self.assertIn("增加", t["note"])  # 数量增加但总量极低, 仍定调弱

    def test_neutral(self):
        with mock.patch.object(dr, "index_ma20_status",
                               return_value={"ok": True, "close": 100, "ma20": 99, "above": True, "days": 30}):
            # 16 个高分板块 → 三档合计 >15, 非弱; diff=0 → 中性
            t = dr.market_tone(self._rps(), self._hist([("20260807", 16), ("20260810", 16)]))
        self.assertEqual(t["tone"], "中性")


# ========================== 方向2: 主线识别 ==========================

class TestResonance(unittest.TestCase):
    def test_core_pulse_diverge(self):
        r5 = make_level([make_sector("黄金", rank=1), make_sector("半导体", rank=2),
                         make_sector("脉冲A", rank=3)])
        r10 = make_level([make_sector("黄金", rank=1), make_sector("半导体", rank=2),
                          make_sector("分歧B", rank=5)])
        r20 = make_level([make_sector("黄金", rank=1), make_sector("半导体", rank=2),
                          make_sector("分歧B", rank=5)])
        res = dr.resonance_analysis(r5, r10, r20)
        self.assertEqual([s["name"] for s in res["core"]], ["黄金", "半导体"])
        self.assertEqual([s["name"] for s in res["pulse"]], ["脉冲A"])
        # 分歧: RPS10/20 在榜但 RPS5 不在
        self.assertEqual([d["name"] for d in res["diverge"]], ["分歧B"])

    def test_strength_ratio(self):
        s = make_sector("黄金", stock_count=10, rps50_above_90=8)
        self.assertAlmostEqual(dr.strength_ratio(s), 0.8)
        self.assertAlmostEqual(dr.strength_ratio(make_sector("空", stock_count=0)), 0.0)


class TestTier(unittest.TestCase):
    def test_three_tiers(self):
        in_list = [make_sector("A", rank=1), make_sector("B", rank=15),
                   make_sector("C", rank=35)]
        t = dr.tier_analysis(in_list)
        self.assertEqual([s["name"] for s in t["tier1"]], ["A"])
        self.assertEqual([s["name"] for s in t["tier2"]], ["B"])
        self.assertEqual([s["name"] for s in t["tier3"]], ["C"])


# ========================== 方向3: 轮动解读 ==========================

class TestRotation(unittest.TestCase):
    def test_new_in_jump_out(self):
        r5 = make_level([make_sector("新进X", rank=5, continuous=1, rank_change=6),
                         make_sector("稳A", rank=1, continuous=4, rank_change=0)],
                        out_list=[make_sector("专用机械", status="调出", rank=13)])
        r10 = make_level([make_sector("稳A", rank=1)], out_list=[make_sector("纺织", status="调出")])
        r20 = make_level([make_sector("稳A", rank=1)], out_list=[make_sector("专用机械", status="调出")])
        rot = dr.rotation_analysis(r5, r10, r20)
        self.assertEqual([s["name"] for s in rot["new_in"]], ["新进X"])
        self.assertEqual([s["name"] for s in rot["rank_jump"]], ["新进X"])
        # out_list 三档去重
        self.assertEqual(sorted(rot["out_all"]), ["专用机械", "纺织"])
        self.assertEqual(rot["out_count"], 2)


# ========================== 方向4: 健康度 ==========================

class TestHealth(unittest.TestCase):
    def test_expand_mature_seed(self):
        in_list = [make_sector("扩散A", rank=3, continuous=6, stock_count=10, rps50_above_90=5),
                   make_sector("成熟B", rank=10, continuous=5, stock_count=20, rps50_above_90=2),
                   make_sector("种子C", rank=9, continuous=1, stock_count=10, rps50_above_90=1)]
        h = dr.health_analysis(in_list)
        self.assertEqual([x["name"] for x in h["expand"]], ["扩散A"])  # 50% ≥30%
        self.assertEqual(h["mature"], ["扩散A", "成熟B"])  # 连续≥5 且 rank≤20
        self.assertEqual(h["seed"], ["种子C"])


# ========================== 方向5: 风险预警 ==========================

class TestRisk(unittest.TestCase):
    def test_batch_out_warning(self):
        outs = []
        for i in range(5):
            outs.append(make_sector("半导体%d" % i, status="调出"))
        r5 = make_level([make_sector("黄金", rank=1)], out_list=outs)
        r10 = make_level([])
        r20 = make_level([])
        hist = {"20260807": {"A": {"RPS5": 95}}, "20260810": {"A": {"RPS5": 95}}}
        risk = dr.risk_analysis(r5, r10, r20, hist)
        self.assertTrue(any("批量调出" in w for w in risk["warnings"]))
        self.assertIn("科技", risk["batch_out"])

    def test_top_diverge_warning(self):
        in_list = [make_sector("虚高板块", rank=2, stock_count=50, rps50_above_90=5)]  # 10% < 15%
        r5 = make_level(in_list)
        r10 = make_level([])
        r20 = make_level([])
        hist = {"20260807": {"A": {"RPS5": 95}}, "20260810": {"A": {"RPS5": 95}}}
        risk = dr.risk_analysis(r5, r10, r20, hist)
        self.assertTrue(any("顶背离" in w for w in risk["warnings"]))
        self.assertEqual(risk["diverge"][0]["name"], "虚高板块")

    def test_momentum_decay(self):
        hist = {"20260805": {str(i): {"RPS5": 95} for i in range(15)},
                "20260806": {str(i): {"RPS5": 95} for i in range(12)},
                "20260807": {str(i): {"RPS5": 95} for i in range(10)}}
        r5 = make_level([make_sector("黄金", rank=1)])
        r10 = make_level([])
        r20 = make_level([])
        risk = dr.risk_analysis(r5, r10, r20, hist)
        self.assertTrue(risk["momentum_decay"])
        self.assertTrue(any("动量衰减" in w for w in risk["warnings"]))

    def test_no_warning(self):
        r5 = make_level([make_sector("黄金", rank=1, stock_count=10, rps50_above_90=9)])
        r10 = make_level([])
        r20 = make_level([])
        hist = {"20260807": {"A": {"RPS5": 95}}, "20260810": {"A": {"RPS5": 95}}}
        risk = dr.risk_analysis(r5, r10, r20, hist)
        self.assertEqual(risk["warnings"], [])

    def test_style_of(self):
        self.assertEqual(dr._style_of("半导体设备"), "科技")
        self.assertEqual(dr._style_of("医药商业"), "医药")
        self.assertEqual(dr._style_of("黄金"), "周期")
        self.assertEqual(dr._style_of("未知名称"), "其他")


# ========================== 方向6: 次日清单 ==========================

class TestWatchlist(unittest.TestCase):
    def test_seed_critical_diverge(self):
        r5 = make_level([make_sector("种子A", rank=4, continuous=1, rps=95)])
        r10 = make_level([make_sector("种子A", rank=4), make_sector("分歧C", rank=8)])
        r20 = make_level([make_sector("分歧C", rank=8)])
        # 历史: 化学制药 RPS5=88(临界) 且 rank 上升 20→15
        hist = {"20260807": {"化学制药": {"RPS5": 88, "rank5": 20},
                             "种子A": {"RPS5": 95, "rank5": 4},
                             "分歧C": {"RPS5": 60, "rank5": 8}},
                "20260810": {"化学制药": {"RPS5": 88, "rank5": 15},
                             "种子A": {"RPS5": 95, "rank5": 4},
                             "分歧C": {"RPS5": 60, "rank5": 8}}}
        w = dr.watchlist(r5, r10, r20, hist)
        self.assertEqual(w["seed"], ["种子A"])
        self.assertEqual([c["name"] for c in w["critical"]], ["化学制药"])
        self.assertEqual(w["diverge_watch"], ["分歧C"])


# ========================== 进阶 ==========================

class TestSentimentStyle(unittest.TestCase):
    def _rps20(self, n=12, continuous=1, rank=None):
        sectors = [make_sector("板块%d" % i, rank=rank or (i + 1), continuous=continuous)
                   for i in range(n)]
        return make_level(sectors)

    def test_ice_phase(self):
        hist = {"20260810": {str(i): {"RPS5": 95} for i in range(8)}}
        sent = dr.sentiment_phase(self._rps20(5), make_level([]), make_level([]), hist)
        self.assertEqual(sent["phase"], "冰点期")

    def test_burst_phase(self):
        hist = {"20260810": {str(i): {"RPS5": 95} for i in range(25)}}
        sent = dr.sentiment_phase(self._rps20(), make_level([]), make_level([]), hist)
        self.assertEqual(sent["phase"], "高潮期")

    def test_style_analysis(self):
        r5 = make_level([make_sector("半导体"), make_sector("医药"), make_sector("黄金")])
        st = dr.style_analysis(r5, make_level([]), make_level([]))
        self.assertEqual(st["rps5"].get("科技"), 1)
        self.assertEqual(st["rps5"].get("医药"), 1)
        self.assertEqual(st["rps5"].get("周期"), 1)


# ========================== 汇总/渲染/幂等 ==========================

class TestBuildAndRender(unittest.TestCase):
    def _full_rps_data(self):
        r5 = make_level(
            [make_sector("黄金", rank=1, rank_change=4, continuous=4, stock_count=10, rps50_above_90=8),
             make_sector("半导体", rank=2, rank_change=1, continuous=2, stock_count=30, rps50_above_90=5),
             make_sector("脉冲X", rank=12, rank_change=3, continuous=1, stock_count=10, rps50_above_90=1)],
            out_list=[make_sector("专用机械", status="调出", rank=13)])
        r10 = make_level([make_sector("黄金", rank=1), make_sector("半导体", rank=2)])
        r20 = make_level([make_sector("黄金", rank=1)])
        return make_rps_data(rps5=r5, rps10=r10, rps20=r20)

    def _history(self):
        return {"20260807": {"黄金": {"RPS5": 90, "rank5": 5}, "半导体": {"RPS5": 80, "rank5": 6}},
                "20260810": {"黄金": {"RPS5": 100, "rank5": 1}, "半导体": {"RPS5": 95, "rank5": 2}}}

    def test_build_stats_all_sections(self):
        with mock.patch.object(dr, "index_ma20_status",
                               return_value={"ok": True, "close": 100, "ma20": 99, "above": True, "days": 30}), \
             mock.patch.object(dr, "load_recommendations",
                               return_value={"market_status": {"is_safe": True, "advance_ratio": 0.75, "note": "合格"}}):
            stats = dr.build_review_stats(self._full_rps_data(), self._history())
        for key in ("market", "resonance", "tier", "rotation", "health", "risk",
                    "watch", "sentiment", "style"):
            self.assertIn(key, stats)
        self.assertEqual(stats["update_date"], "20260810")
        # 精简: 无 stocks 大字段
        self.assertNotIn("stocks", stats["tier"]["tier1"][0])
        self.assertNotIn("stocks", stats["resonance"]["core"][0])
        # 核心主线 = 三档共振
        self.assertEqual([s["name"] for s in stats["resonance"]["core"]], ["黄金"])

    def test_render_markdown_sections(self):
        with mock.patch.object(dr, "index_ma20_status",
                               return_value={"ok": True, "close": 100, "ma20": 99, "above": True, "days": 30}), \
             mock.patch.object(dr, "load_recommendations", return_value={}):
            stats = dr.build_review_stats(self._full_rps_data(), self._history())
        md = dr.render_markdown(stats)
        for sec in ("一、市场整体定调", "二、板块强弱梯队与主线", "三、板块轮动动向",
                    "四、题材/板块健康度", "五、风险与退潮预警", "六、次日跟踪清单", "进阶观察"):
            self.assertIn(sec, md)
        self.assertIn("黄金", md)
        self.assertIn("核心主线", md)

    def test_save_review_json_serializable(self):
        with mock.patch.object(dr, "index_ma20_status",
                               return_value={"ok": True, "close": 100, "ma20": 99, "above": True, "days": 30}), \
             mock.patch.object(dr, "load_recommendations", return_value={}):
            stats = dr.build_review_stats(self._full_rps_data(), self._history())
        tmp = tempfile.TemporaryDirectory()
        try:
            with mock.patch.object(dr, "STATIC_DIR", __import__("pathlib").Path(tmp.name)):
                p, _ = dr.save_review(stats, dr.render_markdown(stats))
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)  # 无 NaN 才能正常解析
                self.assertEqual(data["update_date"], "20260810")
        finally:
            tmp.cleanup()

    def test_main_idempotent(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            with mock.patch.object(dr, "STATIC_DIR", __import__("pathlib").Path(tmp.name)), \
                 mock.patch.object(dr, "load_rps_data", return_value=self._full_rps_data()), \
                 mock.patch.object(dr, "load_history", return_value=self._history()), \
                 mock.patch.object(dr, "load_recommendations", return_value={}), \
                 mock.patch.object(dr, "index_ma20_status",
                                   return_value={"ok": True, "close": 100, "ma20": 99, "above": True, "days": 30}):
                dr.main(force=True)
                dr.main(force=False)  # 同日已生成 → 跳过
                with open(os.path.join(tmp.name, "daily_review.json"), encoding="utf-8") as f:
                    data = json.load(f)
                self.assertEqual(data["update_date"], "20260810")
        finally:
            tmp.cleanup()


class TestFmtMarketStatus(unittest.TestCase):
    def test_dict_to_text(self):
        self.assertEqual(dr._fmt_market_status(
            {"is_safe": True, "advance_ratio": 0.75, "note": "合格"}), "大盘环境合格；上涨占比75%；合格")
        self.assertEqual(dr._fmt_market_status({"is_safe": False}), "大盘环境不合格")
        self.assertEqual(dr._fmt_market_status(None), "")


if __name__ == "__main__":
    unittest.main()
