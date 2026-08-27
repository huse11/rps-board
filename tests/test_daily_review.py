# -*- coding: utf-8 -*-
"""
单元测试：每日 RPS 复盘生成模块 v2 (daily_review.py)
覆盖: 市场定调(双约束/分位) / 硬核+韧性主线 / 梯队达标率过滤 / 轮动分类+反复轮动 /
      健康度规模自适应+疲劳+种子分级 / 风险分级(hint/warning/danger) / 次日清单分池+掉榜天数 /
      情绪周期主标签+附加信号 / 风格多标签+占比 / 一致性校验 / markdown渲染 / 幂等
运行: python -m unittest tests.test_daily_review -v
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

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
    return spec


def patch_index(above=True):
    return mock.patch.object(dr, "index_ma20_status",
                             return_value={"ok": True, "close": 100, "ma20": 99,
                                           "above": above, "days": 30})


# ========================== 工具 ==========================

class TestUtils(unittest.TestCase):
    def test_strength_ratio(self):
        s = make_sector("黄金", stock_count=10, rps50_above_90=8)
        self.assertAlmostEqual(dr.strength_ratio(s), 0.8)
        self.assertAlmostEqual(dr.strength_ratio(make_sector("空", stock_count=0)), 0.0)

    def test_expand_threshold_adaptive(self):
        self.assertEqual(dr.expand_threshold(5), 0.40)   # <15 小板块
        self.assertEqual(dr.expand_threshold(20), 0.30)  # 15~40 中板块
        self.assertEqual(dr.expand_threshold(60), 0.20)  # >40 大板块

    def test_style_of_multilabel(self):
        self.assertEqual(dr._style_of("半导体设备"), {"科技"})
        self.assertEqual(dr._style_of("医药商业"), {"医药"})
        self.assertEqual(dr._style_of("黄金"), {"周期"})
        self.assertEqual(dr._style_of("未知名称"), {"未归类"})
        # 多标签: 储能命中新能源(电池), 中成药命中医药
        self.assertEqual(dr._style_of("储能"), {"新能源"})
        self.assertIn("医药", dr._style_of("中成药"))

    def test_percentile(self):
        counts = {"d1": 5, "d2": 10, "d3": 15, "d4": 20}
        self.assertEqual(dr._percentile(counts, 10), 25.0)   # 1/4 小于 10
        self.assertEqual(dr._percentile(counts, 20), 75.0)   # 3/4 小于 20
        self.assertIsNone(dr._percentile({"d1": 5}, 5))


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
        dr.STATIC_DIR = Path(self._tmp.name)
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
        closes = list(range(100, 120)) + [50]
        self._write_index(closes)
        r = dr.index_ma20_status()
        self.assertFalse(r["above"])

    def test_insufficient_data(self):
        self._write_index(list(range(10)))
        self.assertIsNone(dr.index_ma20_status())

    def test_missing_file(self):
        self.assertIsNone(dr.index_ma20_status())


# ========================== 方向1: 市场定调(双约束) ==========================

class TestMarketTone(unittest.TestCase):
    def _hist(self, dates_high, chg5=0.0):
        """dates_high: [(date, n)] → 三档高分数量均为 n; chg5: 高分板块统一5日涨幅(强度维度)"""
        hist = {}
        for date, n in dates_high:
            sectors = {}
            for i in range(n):
                sectors["板块%d" % i] = {"RPS5": 95, "RPS10": 95, "RPS20": 95, "chg5": chg5}
            hist[date] = sectors
        return hist

    def _rps(self):
        lv = make_level([make_sector("黄金", rank=1)])
        return make_rps_data(rps5=lv, rps10=lv, rps20=lv)

    def test_full_strong(self):
        # RPS95下: 数量≥10 + 高分平均涨幅≥8% + 指数站上20日线 → 全面强
        with patch_index(above=True):
            t = dr.market_tone(self._rps(), self._hist([("20260807", 8), ("20260810", 10)], chg5=12))
        self.assertEqual(t["tone"], "全面强")
        self.assertEqual(t["high5"], 10)
        self.assertEqual(t["avg_gain"], 12.0)

    def test_structural_strong_even_index_below(self):
        # 指数跌破20日线但高分平均涨幅≥8% 且 数量≥10 → 结构性强
        with patch_index(above=False):
            t = dr.market_tone(self._rps(), self._hist([("20260807", 10), ("20260810", 10)], chg5=10))
        self.assertEqual(t["tone"], "结构性强")
        self.assertIn("结构性", t["note"])

    def test_weak_when_avg_low(self):
        # 高分平均涨幅≤3% → 弱 (数量恒定下强度主导)
        with patch_index(above=True):
            t = dr.market_tone(self._rps(), self._hist([("20260807", 10), ("20260810", 9)], chg5=2))
        self.assertEqual(t["tone"], "弱")

    def test_weak_when_total_low(self):
        with patch_index(above=True):
            # 三档合计=6 ≤15
            t = dr.market_tone(self._rps(), self._hist([("20260807", 1), ("20260810", 2)]))
        self.assertEqual(t["tone"], "弱")

    def test_neutral_strong_floor(self):
        # diff=+3 增长 + 平均涨幅≥6% + 数量≥10 → 中性偏强
        with patch_index(above=True):
            t = dr.market_tone(self._rps(), self._hist([("20260807", 8), ("20260810", 10)], chg5=6))
        self.assertEqual(t["tone"], "中性偏强")
        # 平均涨幅≤4% → 中性偏弱
        t2 = dr.market_tone(self._rps(), self._hist([("20260807", 10), ("20260810", 10)], chg5=3.5))
        self.assertEqual(t2["tone"], "中性偏弱")

    def test_neutral(self):
        # 平均涨幅居中(4~6%), 数量≥10, diff=0 → 中性
        with patch_index(above=True):
            t = dr.market_tone(self._rps(), self._hist([("20260807", 10), ("20260810", 10)], chg5=5))
        self.assertEqual(t["tone"], "中性")

    def test_hist_percentile_field(self):
        with patch_index(above=True):
            t = dr.market_tone(self._rps(), self._hist([("20260807", 10), ("20260810", 10)], chg5=5))
        self.assertIn("high_score_count_pct", t)
        self.assertIn("avg_gain", t)


# ========================== 方向2: 主线识别(硬核/韧性) ==========================

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
        self.assertEqual([d["name"] for d in res["diverge"]], ["分歧B"])
        self.assertEqual(res["mainline_count"], 2)

    def test_tough_core_rps5_dropped_1day(self):
        # 韧性主线: RPS10/20 在榜, RPS5 不在榜, 但昨日 RPS5 在榜(掉1天)
        hist = {"20260807": {"韧性X": {"RPS5": 95}},
                "20260810": {"韧性X": {"RPS5": 60}}}
        r5 = make_level([])
        r10 = make_level([make_sector("韧性X", rank=3)])
        r20 = make_level([make_sector("韧性X", rank=3)])
        res = dr.resonance_analysis(r5, r10, r20, hist)
        self.assertEqual([s["name"] for s in res["tough"]], ["韧性X"])
        self.assertEqual(res["mainline_count"], 1)
        # 昨日也不在榜 → 非韧性(掉≥2天)
        hist2 = {"20260807": {"韧性X": {"RPS5": 60}},
                 "20260810": {"韧性X": {"RPS5": 60}}}
        res2 = dr.resonance_analysis(r5, r10, r20, hist2)
        self.assertEqual(res2["tough"], [])


class TestTier(unittest.TestCase):
    def test_three_tiers_with_ratio_filter(self):
        in_list = [make_sector("A", rank=1, stock_count=10, rps50_above_90=8),   # 80%
                   make_sector("B", rank=15, stock_count=10, rps50_above_90=5),  # 50%
                   make_sector("C", rank=35)]
        t = dr.tier_analysis(in_list)
        self.assertEqual([s["name"] for s in t["tier1"]], ["A"])
        self.assertEqual([s["name"] for s in t["tier2"]], ["B"])
        self.assertEqual([s["name"] for s in t["tier3"]], ["C"])

    def test_ratio_filter_removes_small_pulse(self):
        # 排名 Top3 但内部达标率 5% < 15% → 降级不进第一梯队
        in_list = [make_sector("小脉冲", rank=1, stock_count=20, rps50_above_90=1)]
        t = dr.tier_analysis(in_list)
        self.assertEqual(t["tier1"], [])


# ========================== 方向3: 轮动解读 ==========================

class TestRotation(unittest.TestCase):
    def _rps(self, out=None):
        r5 = make_level([make_sector("新进X", rank=5, continuous=1, rank_change=9),
                         make_sector("稳A", rank=1, continuous=4, rank_change=0)],
                        out_list=out or [])
        r10 = make_level([make_sector("稳A", rank=1)])
        r20 = make_level([make_sector("稳A", rank=1)])
        return r5, r10, r20

    def test_new_in_jump_min_gain(self):
        r5, r10, r20 = self._rps()
        rot = dr.rotation_analysis(r5, r10, r20)
        self.assertEqual([s["name"] for s in rot["new_in"]], ["新进X"])
        # rank_change=9 ≥8 → 大幅跃升
        self.assertEqual([s["name"] for s in rot["rank_jump"]], ["新进X"])

    def test_small_jump_filtered(self):
        r5 = make_level([make_sector("微升A", rank=6, continuous=1, rank_change=3)])
        r10 = make_level([])
        r20 = make_level([])
        rot = dr.rotation_analysis(r5, r10, r20)
        self.assertEqual(rot["rank_jump"], [])

    def test_out_dedupe(self):
        r5 = make_level([make_sector("稳A", rank=1)],
                        out_list=[make_sector("半导体", status="调出")])
        r10 = make_level([], out_list=[make_sector("半导体", status="调出"),
                                       make_sector("纺织", status="调出")])
        r20 = make_level([])
        rot = dr.rotation_analysis(r5, r10, r20)
        self.assertEqual(sorted(rot["out_all"]), ["半导体", "纺织"])
        self.assertEqual(rot["out_count"], 2)

    def test_exit_type_from_history(self):
        hist = {"20260803": {"老主线": {"RPS5": 95}},
                "20260804": {"老主线": {"RPS5": 95}},
                "20260805": {"老主线": {"RPS5": 95}},
                "20260806": {"老主线": {"RPS5": 95}},
                "20260807": {"老主线": {"RPS5": 95}},
                "20260810": {"老主线": {"RPS5": 60}}}   # 今日调出
        r5 = make_level([make_sector("稳A", rank=1)],
                        out_list=[make_sector("老主线", status="调出")])
        r10 = make_level([])
        r20 = make_level([])
        rot = dr.rotation_analysis(r5, r10, r20, hist)
        e = rot["exits"][0]
        self.assertEqual(e["name"], "老主线")
        self.assertEqual(e["continuous"], 5)          # 连续在榜5天
        self.assertEqual(e["exit_type"], "mainline_exit")

    def test_repeat_rotation(self):
        # 近10日翻转≥3次 → 反复轮动
        names = ["轮动A"]
        hist = {}
        vals = [95, 60, 95, 60, 95, 60, 95, 60, 95, 60]   # 翻转9次
        for i, v in enumerate(vals):
            hist["202608%02d" % (i + 1)] = {names[0]: {"RPS5": v}}
        rep = dr._repeat_rotation(hist)
        self.assertEqual(rep, ["轮动A"])


# ========================== 方向4: 健康度(规模自适应) ==========================

class TestHealth(unittest.TestCase):
    def test_expand_adaptive_and_seed_split(self):
        in_list = [make_sector("小扩散", rank=3, continuous=6, stock_count=10, rps50_above_90=5),   # 50%≥40% 小板块
                   make_sector("大弱", rank=5, continuous=2, stock_count=60, rps50_above_90=8),      # 13%<20% 大板块
                   make_sector("高质量种子", rank=9, continuous=1, stock_count=10, rps50_above_90=5),  # 50%≥40%
                   make_sector("脉冲种子", rank=8, continuous=1, stock_count=10, rps50_above_90=2)]   # 20%<40%
        h = dr.health_analysis(in_list)
        self.assertEqual([x["name"] for x in h["expand"]], ["小扩散", "高质量种子"])
        self.assertEqual(h["mature"], ["小扩散"])
        self.assertEqual([x["name"] for x in h["seed_high"]], ["高质量种子"])
        self.assertEqual([x["name"] for x in h["seed_pulse"]], ["脉冲种子"])

    def test_mature_fatigue(self):
        # 连续上榜8天 且 达标率跌破规模阈值 → 成熟疲劳
        in_list = [make_sector("老主线A", rank=5, continuous=8, stock_count=20, rps50_above_90=4)]  # 20%<30%
        h = dr.health_analysis(in_list)
        self.assertEqual([x["name"] for x in h["mature_fatigue"]], ["老主线A"])
        # 连续5天但达标率健康 → 不疲劳
        in_list2 = [make_sector("老主线B", rank=5, continuous=5, stock_count=20, rps50_above_90=8)]
        h2 = dr.health_analysis(in_list2)
        self.assertEqual(h2["mature_fatigue"], [])


# ========================== 方向5: 风险预警(分级) ==========================

class TestRisk(unittest.TestCase):
    def _hist2(self, today=95):
        return {"20260807": {"A": {"RPS5": 95}}, "20260810": {"A": {"RPS5": today}}}

    def test_batch_out_single_day_hint(self):
        outs = [make_sector("半导体%d" % i, status="调出") for i in range(3)]
        r5 = make_level([make_sector("黄金", rank=1)], out_list=outs)
        r10 = make_level([])
        r20 = make_level([])
        risk = dr.risk_analysis(r5, r10, r20, self._hist2())
        self.assertTrue(any("批量调出异动" in w["text"] for w in risk["warnings"]))
        self.assertIn("科技", risk["batch_out"])
        self.assertEqual(risk["level"], "hint")

    def test_batch_out_two_day_warning(self):
        # 昨日在榜今日掉榜 + 今日调出, 两日累计同风格≥3 → warning
        outs = [make_sector("半导体%d" % i, status="调出") for i in range(2)]
        r5 = make_level([], out_list=outs)
        r10 = make_level([])
        r20 = make_level([])
        hist = {"20260807": {"半导体X": {"RPS5": 95}, "半导体Y": {"RPS5": 95}},
                "20260810": {"半导体X": {"RPS5": 60}, "半导体Y": {"RPS5": 60}}}
        risk = dr.risk_analysis(r5, r10, r20, hist)
        self.assertTrue(any("批量调出预警" in w["text"] for w in risk["warnings"]))
        self.assertEqual(risk["level"], "warning")

    def test_top_diverge_skips_small_board(self):
        # 成分股<12 → 不告警
        in_list = [make_sector("小板块", rank=2, stock_count=4, rps50_above_90=0)]
        r5 = make_level(in_list)
        risk = dr.risk_analysis(r5, make_level([]), make_level([]), self._hist2())
        self.assertEqual(risk["diverge"], [])
        # 大板块达标率10% → 告警
        in_list2 = [make_sector("虚高板块", rank=2, stock_count=50, rps50_above_90=5)]
        r5b = make_level(in_list2)
        risk2 = dr.risk_analysis(r5b, make_level([]), make_level([]), self._hist2())
        self.assertTrue(any("顶背离" in w["text"] for w in risk2["warnings"]))
        self.assertEqual(risk2["level"], "warning")

    def test_momentum_decay_requires_high_floor(self):
        hist = {"20260805": {str(i): {"RPS5": 95} for i in range(16)},
                "20260806": {str(i): {"RPS5": 95} for i in range(13)},
                "20260807": {str(i): {"RPS5": 95} for i in range(10)}}
        r5 = make_level([make_sector("黄金", rank=1)])
        risk = dr.risk_analysis(r5, make_level([]), make_level([]), hist)
        self.assertTrue(risk["momentum_decay"])
        self.assertTrue(any("动量衰减" in w["text"] for w in risk["warnings"]))
        self.assertEqual(risk["level"], "warning")
        # 低位回落(高分≤8)不触发 (RPS95下 decay_high_floor=8)
        hist_low = {"20260805": {str(i): {"RPS5": 95} for i in range(8)},
                    "20260806": {str(i): {"RPS5": 95} for i in range(6)},
                    "20260807": {str(i): {"RPS5": 95} for i in range(5)}}
        risk_low = dr.risk_analysis(r5, make_level([]), make_level([]), hist_low)
        self.assertFalse(risk_low["momentum_decay"])

    def test_danger_when_multiple_warnings(self):
        # 动量衰减(warning) + 顶背离(warning) → danger
        hist = {"20260805": {str(i): {"RPS5": 95} for i in range(16)},
                "20260806": {str(i): {"RPS5": 95} for i in range(13)},
                "20260807": {str(i): {"RPS5": 95} for i in range(10)}}
        in_list = [make_sector("虚高板块", rank=2, stock_count=50, rps50_above_90=5)]
        risk = dr.risk_analysis(make_level(in_list), make_level([]), make_level([]), hist)
        self.assertEqual(risk["level"], "danger")

    def test_no_warning(self):
        r5 = make_level([make_sector("黄金", rank=1, stock_count=10, rps50_above_90=9)])
        risk = dr.risk_analysis(r5, make_level([]), make_level([]), self._hist2())
        self.assertEqual(risk["warnings"], [])
        self.assertEqual(risk["level"], "safe")


# ========================== 方向6: 次日清单(分池+掉榜天数) ==========================

class TestWatchlist(unittest.TestCase):
    def test_seed_split_critical_diverge_dropdays(self):
        r5 = make_level([make_sector("高质量种A", rank=4, continuous=1, stock_count=10, rps50_above_90=6),
                         make_sector("脉冲种B", rank=5, continuous=1, stock_count=10, rps50_above_90=2),
                         make_sector("化学制药", rank=3, continuous=3, stock_count=40, rps50_above_90=15)])
        r10 = make_level([make_sector("分歧C", rank=8)])
        r20 = make_level([make_sector("分歧C", rank=8), make_sector("衰竭D", rank=9)])
        hist = {"20260807": {"化学制药": {"RPS5": 88, "rank5": 20},
                             "高质量种A": {"RPS5": 95, "rank5": 4},
                             "脉冲种B": {"RPS5": 95, "rank5": 5},
                             "分歧C": {"RPS5": 60, "rank5": 8},
                             "衰竭D": {"RPS5": 60, "rank5": 9}},
                "20260810": {"化学制药": {"RPS5": 88, "rank5": 15},
                             "高质量种A": {"RPS5": 95, "rank5": 4},
                             "脉冲种B": {"RPS5": 95, "rank5": 5},
                             "分歧C": {"RPS5": 60, "rank5": 8},
                             "衰竭D": {"RPS5": 60, "rank5": 9}}}
        w = dr.watchlist(r5, r10, r20, hist)
        self.assertEqual([x["name"] for x in w["seed_high"]], ["高质量种A"])
        self.assertEqual([x["name"] for x in w["seed_pulse"]], ["脉冲种B"])
        # 化学制药 达标率 15/40=37.5% ≥30% → 入临界池
        self.assertEqual([c["name"] for c in w["critical"]], ["化学制药"])
        # 分歧C 掉榜2天 → 入分歧池; 衰竭D 掉榜2天(两天都在history里) → 也入分歧池
        dw = {x["name"]: x["drop_days"] for x in w["diverge_watch"]}
        self.assertEqual(dw.get("分歧C"), 2)
        self.assertEqual(w["diverge_dropped"], [])  # 无 ≥3 天掉榜

    def test_diverge_dropped_after_3_days(self):
        r5 = make_level([])
        r20 = make_level([make_sector("衰竭E", rank=9)])
        hist = {"20260803": {"衰竭E": {"RPS5": 60}},
                "20260804": {"衰竭E": {"RPS5": 60}},
                "20260805": {"衰竭E": {"RPS5": 60}},
                "20260806": {"衰竭E": {"RPS5": 60}},
                "20260807": {"衰竭E": {"RPS5": 60}}}
        w = dr.watchlist(r5, make_level([]), r20, hist)
        self.assertEqual(w["diverge_watch"], [])
        self.assertEqual([x["name"] for x in w["diverge_dropped"]], ["衰竭E"])


# ========================== 进阶: 情绪周期 + 风格 + 一致性 ==========================

class TestSentiment(unittest.TestCase):
    def _rps5(self, n=12, continuous=1):
        sectors = [make_sector("板块%d" % i, rank=i + 1, continuous=continuous)
                   for i in range(n)]
        return make_level(sectors)

    def test_ice_phase(self):
        hist = {"20260810": {str(i): {"RPS5": 95} for i in range(3)}}
        sent = dr.sentiment_phase(self._rps5(5), make_level([]), make_level([]), hist)
        self.assertEqual(sent["main"], "冰点期")
        self.assertEqual(sent["signal"], [])

    def test_burst_phase(self):
        hist = {"20260810": {str(i): {"RPS5": 95} for i in range(25)}}
        sent = dr.sentiment_phase(self._rps5(), make_level([]), make_level([]), hist)
        self.assertEqual(sent["main"], "高潮期")

    def test_compound_signal(self):
        # 高潮后批量调出 → 主标签高潮期 + 附加退潮信号
        hist = {"20260810": {str(i): {"RPS5": 95} for i in range(25)}}
        outs = [make_sector("半导体%d" % i, status="调出") for i in range(3)]
        r5 = make_level(self._rps5()["in_list"], out_list=outs)
        risk = {"momentum_decay": False}
        sent = dr.sentiment_phase(r5, make_level([]), make_level([]), hist, risk)
        self.assertEqual(sent["main"], "高潮期")
        self.assertIn("退潮信号", sent["signal"])

    def test_startup_with_seed_high(self):
        # high_n=10 < high_burst(12) → 不触发高潮; seed_high_n≥4 → 启动期
        hist = {"20260810": {str(i): {"RPS5": 95} for i in range(10)}}
        sent = dr.sentiment_phase(self._rps5(10, continuous=1), make_level([]),
                                  make_level([]), hist, seed_high_n=4)
        self.assertEqual(sent["main"], "启动期")


class TestStyleAnalysis(unittest.TestCase):
    def test_multilabel_with_pct(self):
        r5 = make_level([make_sector("半导体"), make_sector("医药"), make_sector("黄金"),
                         make_sector("储能"), make_sector("未知X")])
        st = dr.style_analysis(r5, make_level([]), make_level([]))
        s5 = st["rps5"]
        self.assertEqual(s5["科技"]["count"], 1)
        self.assertEqual(s5["医药"]["count"], 1)
        self.assertEqual(s5["周期"]["count"], 1)
        self.assertEqual(s5["新能源"]["count"], 1)
        self.assertEqual(s5["未归类"]["count"], 1)
        self.assertAlmostEqual(s5["医药"]["pct"], 20.0)


class TestConsistency(unittest.TestCase):
    def test_conflict_when_rps_strong_rec_fail(self):
        c = dr.consistency_check("全面强", {"is_safe": False})
        self.assertTrue(c["conflict"])
        self.assertIn("冲突", c["warning"])

    def test_no_conflict(self):
        self.assertFalse(dr.consistency_check("全面强", {"is_safe": True})["conflict"])
        self.assertFalse(dr.consistency_check("中性", {"is_safe": False})["conflict"])
        self.assertFalse(dr.consistency_check("弱", None)["conflict"])


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
        with patch_index(above=True), \
             mock.patch.object(dr, "load_recommendations",
                               return_value={"market_status": {"is_safe": True, "advance_ratio": 0.75, "note": "合格"}}):
            stats = dr.build_review_stats(self._full_rps_data(), self._history())
        for key in ("market", "resonance", "tier", "rotation", "health", "risk",
                    "watch", "sentiment", "style", "consistency"):
            self.assertIn(key, stats)
        self.assertEqual(stats["update_date"], "20260810")
        self.assertNotIn("stocks", stats["tier"]["tier1"][0])
        self.assertNotIn("stocks", stats["resonance"]["core"][0])
        self.assertEqual([s["name"] for s in stats["resonance"]["core"]], ["黄金"])
        self.assertIn("high_score_count_pct", stats["market"])

    def test_render_markdown_sections(self):
        with patch_index(above=True), \
             mock.patch.object(dr, "load_recommendations", return_value={}):
            stats = dr.build_review_stats(self._full_rps_data(), self._history())
        md = dr.render_markdown(stats)
        for sec in ("今日市场总览", "情绪周期", "大盘强度", "主线梯队", "轮动观察",
                    "板块健康扫描", "风险预警", "次日跟踪清单", "风格分布"):
            self.assertIn(sec, md)
        self.assertIn("黄金", md)
        self.assertIn("硬核主线", md)
        self.assertIn("风险等级", md)

    def test_save_review_json_serializable(self):
        with patch_index(above=True), \
             mock.patch.object(dr, "load_recommendations", return_value={}):
            stats = dr.build_review_stats(self._full_rps_data(), self._history())
        tmp = tempfile.TemporaryDirectory()
        try:
            with mock.patch.object(dr, "STATIC_DIR", Path(tmp.name)):
                p, _ = dr.save_review(stats, dr.render_markdown(stats))
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)  # 无 NaN 才能正常解析
                self.assertEqual(data["update_date"], "20260810")
        finally:
            tmp.cleanup()

    def test_main_idempotent(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            with mock.patch.object(dr, "STATIC_DIR", Path(tmp.name)), \
                 mock.patch.object(dr, "load_rps_data", return_value=self._full_rps_data()), \
                 mock.patch.object(dr, "load_history", return_value=self._history()), \
                 mock.patch.object(dr, "load_recommendations", return_value={}), \
                 patch_index(above=True):
                dr.main(force=True)
                dr.main(force=False)  # 同日已生成 → 跳过
                with open(os.path.join(tmp.name, "daily_review.json"), encoding="utf-8") as f:
                    data = json.load(f)
                self.assertEqual(data["update_date"], "20260810")
        finally:
            tmp.cleanup()


class TestSentimentHistory(unittest.TestCase):
    """情绪周期历史文件: 幂等追加 + 保留上限 + 空日期跳过"""

    def _stats(self, date="20260810", phase="震荡期"):
        return {
            "update_date": date,
            "sentiment": {"main": phase, "phase": phase,
                          "high_n": 20, "seed_n": 9, "core_n": 3, "out_n": 0},
        }

    def _read(self, tmp):
        return dr._load_json(Path(tmp.name) / dr.SENTIMENT_HISTORY_NAME)

    def test_append_creates_file(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            with mock.patch.object(dr, "STATIC_DIR", Path(tmp.name)):
                dr._append_sentiment_history(self._stats())
                data = self._read(tmp)
                self.assertEqual(len(data), 1)
                self.assertEqual(data[0]["date"], "20260810")
                self.assertEqual(data[0]["phase"], "震荡期")
                self.assertEqual(data[0]["high_n"], 20)
        finally:
            tmp.cleanup()

    def test_append_idempotent_same_day(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            with mock.patch.object(dr, "STATIC_DIR", Path(tmp.name)):
                dr._append_sentiment_history(self._stats("20260810", "震荡期"))
                dr._append_sentiment_history(self._stats("20260810", "高潮期"))  # 同日 → 跳过
                data = self._read(tmp)
                self.assertEqual(len(data), 1)
                self.assertEqual(data[0]["phase"], "震荡期")  # 不覆盖
        finally:
            tmp.cleanup()

    def test_append_keeps_max(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            with mock.patch.object(dr, "STATIC_DIR", Path(tmp.name)):
                for i in range(dr.SENTIMENT_HISTORY_MAX + 5):
                    dr._append_sentiment_history(self._stats("2026" + str(i).zfill(4)))
                data = self._read(tmp)
                self.assertLessEqual(len(data), dr.SENTIMENT_HISTORY_MAX)
                self.assertEqual(data[-1]["date"], "2026" + str(dr.SENTIMENT_HISTORY_MAX + 4).zfill(4))
        finally:
            tmp.cleanup()

    def test_append_skip_empty_date(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            with mock.patch.object(dr, "STATIC_DIR", Path(tmp.name)):
                dr._append_sentiment_history(self._stats(""))
                self.assertIsNone(self._read(tmp))
        finally:
            tmp.cleanup()


class TestPotentialSectors(unittest.TestCase):
    """早期启动挖掘: 临界潜力板块 (RPS5∈[88,90) + 板块内部达标率合格)"""

    def test_potential_with_stats(self):
        history = {"20260807": {"种子": {"RPS5": 80, "rank5": 20},
                                "蓄势": {"RPS5": 89.0, "rank5": 15}},
                   "20260810": {"种子": {"RPS5": 80, "rank5": 20},
                                "蓄势": {"RPS5": 89.5, "rank5": 12}}}
        stats = [{"name": "蓄势", "stock_count": 20, "rps50_above_90": 8,
                  "rank5": 12, "rank_change": 3, "continuous": 3}]
        out = dr.potential_sectors(history, stats)
        self.assertEqual([s["name"] for s in out], ["蓄势"])
        self.assertAlmostEqual(out[0]["ratio"], 40.0, places=1)  # 8/20=40%
        self.assertEqual(out[0]["stock_count"], 20)
        self.assertEqual(out[0]["continuous"], 3)

    def test_ratio_filter(self):
        history = {"20260810": {"蓄势": {"RPS5": 89.0, "rank5": 12}}}
        # 2/20=10% < 中板块阈值30% → 不合格
        stats = [{"name": "蓄势", "stock_count": 20, "rps50_above_90": 2}]
        self.assertEqual(dr.potential_sectors(history, stats), [])

    def test_no_stats_returns_empty(self):
        history = {"20260810": {"蓄势": {"RPS5": 89.0, "rank5": 12}}}
        self.assertEqual(dr.potential_sectors(history, None), [])

    def test_out_of_range_excluded(self):
        history = {"20260810": {"未达标": {"RPS5": 80.0, "rank5": 30},
                                "已入选": {"RPS5": 95.0, "rank5": 3}}}
        stats = [{"name": "未达标", "stock_count": 10, "rps50_above_90": 5},
                 {"name": "已入选", "stock_count": 10, "rps50_above_90": 9}]
        self.assertEqual(dr.potential_sectors(history, stats), [])

    def test_noise_sector_excluded(self):
        history = {"20260810": {"其他": {"RPS5": 89.0, "rank5": 5}}}
        stats = [{"name": "其他", "stock_count": 10, "rps50_above_90": 6}]
        self.assertEqual(dr.potential_sectors(history, stats), [])


class TestSectorLifeCycle(unittest.TestCase):
    """板块生命周期: base/growth/accelerate/fatigue"""

    def _level(self, sectors):
        return {"in_list": sectors, "out_list": [],
                "current_names": [s["name"] for s in sectors]}

    def test_four_phases(self):
        base = make_sector("起步板块", rank=30, continuous=1, stock_count=20, rps50_above_90=10)
        growth = make_sector("成长板块", rank=20, continuous=3, stock_count=20, rps50_above_90=10)
        accel = make_sector("加速板块", rps=96, rank=5, continuous=5, stock_count=20, rps50_above_90=10)
        fatigue = make_sector("疲劳板块", rank=15, continuous=8, stock_count=20, rps50_above_90=2)  # 10%<30%
        lc = dr.sector_life_cycle(self._level([base, growth, accel, fatigue]))
        self.assertEqual(lc["起步板块"], "base")
        self.assertEqual(lc["成长板块"], "growth")
        self.assertEqual(lc["加速板块"], "accelerate")
        self.assertEqual(lc["疲劳板块"], "fatigue")

    def test_accelerate_rank_condition(self):
        # 连续≥5天 + 排名≤10 → accelerate (即使 RPS 不高)
        s = make_sector("头部板块", rps=90, rank=8, continuous=5, stock_count=20, rps50_above_90=10)
        lc = dr.sector_life_cycle(self._level([s]))
        self.assertEqual(lc["头部板块"], "accelerate")

    def test_fatigue_takes_priority_over_accelerate(self):
        # 连续≥8天 + 达标率跌破 → fatigue (先于 accelerate 判定)
        s = make_sector("疲劳加速", rps=98, rank=3, continuous=9, stock_count=20, rps50_above_90=1)
        lc = dr.sector_life_cycle(self._level([s]))
        self.assertEqual(lc["疲劳加速"], "fatigue")


class TestFmtMarketStatus(unittest.TestCase):
    def test_dict_to_text(self):
        self.assertEqual(dr._fmt_market_status(
            {"is_safe": True, "advance_ratio": 0.75, "note": "合格"}), "大盘环境合格；上涨占比75%；合格")
        self.assertEqual(dr._fmt_market_status({"is_safe": False}), "大盘环境不合格")
        self.assertEqual(dr._fmt_market_status(None), "")


class TestNewsReview(unittest.TestCase):
    """消息面复盘: 聚合日度+早期推荐中的新闻/人气/大宗信号 (2026-08-23)"""

    @staticmethod
    def _stock(name="测试", news=None, tags="", score_news=0):
        return {"name": name, "ts_code": "000001", "news": news,
                "tags": tags, "score_news": score_news}

    @staticmethod
    def _news(op=0, on=0, rp=0, rn=0, title=None):
        return {"official_pos": op, "official_neg": on,
                "rumor_pos": rp, "rumor_neg": rn,
                "headlines": [{"title": title or "新闻标题"}] if title else []}

    def _call(self, recs=None, early=None):
        with mock.patch.object(dr, "load_recommendations", return_value={}), \
             mock.patch.object(dr, "load_early_recommendations", return_value={}):
            return dr.news_review(recs, early)

    def test_pos_news_env_warm(self):
        recs = {"stocks": [self._stock("兴业银锡", self._news(op=1, title="业绩预增"),
                                        tags="日度入选 + 利好消息", score_news=2)]}
        out = self._call(recs, None)
        self.assertIsNotNone(out)
        self.assertTrue(out["has_news"])
        self.assertEqual(out["env"], "偏暖")
        self.assertEqual(out["hit_count"], 1)
        self.assertEqual(out["score_sum"], 2)
        self.assertEqual(out["official_pos_list"][0]["name"], "兴业银锡")
        self.assertEqual(out["official_pos_list"][0]["count"], 1)
        self.assertIn("业绩预增", out["official_pos_list"][0]["headline"])
        self.assertEqual(out["tags_count"]["利好消息"], 1)

    def test_neg_news_env_cold(self):
        recs = {"stocks": [self._stock("某股", self._news(on=1, title="业绩预减"),
                                        tags="日度入选 + 利空消息", score_news=-3)]}
        out = self._call(recs, None)
        self.assertEqual(out["env"], "偏冷")
        self.assertEqual(out["score_sum"], -3)
        self.assertEqual(out["official_neg_list"][0]["name"], "某股")
        self.assertEqual(out["tags_count"]["利空消息"], 1)
        self.assertIn("偏冷", out["note"])

    def test_mixed_env_neg_priority(self):
        # 利空优先: 同股同时有权威利好+利空 → env 偏冷 (防御性)
        recs = {"stocks": [self._stock("双信号", self._news(op=1, on=1),
                                        tags="利好消息 + 利空消息", score_news=-1)]}
        out = self._call(recs, None)
        self.assertEqual(out["env"], "偏冷")
        self.assertEqual(len(out["official_pos_list"]), 1)
        self.assertEqual(len(out["official_neg_list"]), 1)

    def test_rumor_only_tags(self):
        recs = {"stocks": [self._stock("传闻股", self._news(rp=1, rn=1),
                                        tags="日度入选 + 传闻利好")]}
        out = self._call(recs, None)
        self.assertEqual(len(out["rumor_list"]), 1)
        self.assertEqual(out["rumor_list"][0]["pos"], 1)
        self.assertEqual(out["rumor_list"][0]["neg"], 1)
        self.assertEqual(out["env"], "中性偏杂")  # 无权威列表 → 按标签计数

    def test_tags_extra_dzjy_hotrank(self):
        recs = {"stocks": [self._stock("大宗A", self._news(), tags="机构大宗买入"),
                           self._stock("人气B", self._news(), tags="人气榜TOP")]}
        out = self._call(recs, None)
        self.assertEqual(out["tags_count"]["机构大宗买入"], 1)
        self.assertEqual(out["tags_count"]["人气榜TOP"], 1)
        self.assertNotIn("利好消息", out["tags_count"])

    def test_score_sum_aggregation(self):
        recs = {"stocks": [self._stock("A", self._news(op=1), score_news=2),
                           self._stock("B", self._news(on=1), score_news=-3),
                           self._stock("C", self._news(), score_news=0)]}
        out = self._call(recs, None)
        self.assertEqual(out["score_sum"], -1)
        self.assertEqual(out["stock_count"], 3)

    def test_early_plus_daily_merge(self):
        daily = {"stocks": [self._stock("日度股", self._news(op=1), score_news=2)]}
        early = {"stocks": [self._stock("早期股", self._news(on=1), score_news=-3)]}
        out = self._call(daily, early)
        self.assertEqual(out["stock_count"], 2)
        self.assertEqual(out["score_sum"], -1)
        self.assertEqual(len(out["official_pos_list"]), 1)
        self.assertEqual(len(out["official_neg_list"]), 1)

    def test_same_stock_dedup_merge(self):
        # 日度+早期同股都有权威利好 → 合并计数, 不重复列出 (2026-08-23)
        daily = {"stocks": [self._stock("白银有色", self._news(op=4, title="银价大涨"),
                                        tags="利好消息", score_news=2)]}
        early = {"stocks": [self._stock("白银有色", self._news(op=4, title="白银新高"),
                                        tags="利好消息", score_news=2)]}
        out = self._call(daily, early)
        self.assertEqual(out["stock_count"], 2)
        self.assertEqual(len(out["official_pos_list"]), 1)
        self.assertEqual(out["official_pos_list"][0]["name"], "白银有色")
        self.assertEqual(out["official_pos_list"][0]["count"], 8)  # 4+4 合并
        self.assertEqual(out["score_sum"], 4)

    def test_old_no_news_field(self):
        # 旧版推荐无 news 字段 → None, 前端不展示消息面卡片
        recs = {"stocks": [self._stock("旧版", None, tags="日度入选")]}
        self.assertIsNone(self._call(recs, None))

    def test_no_stocks(self):
        self.assertIsNone(self._call({"stocks": []}, None))
        self.assertIsNone(self._call(None, {"stocks": None}))

    def test_render_markdown_news_section(self):
        r5 = make_level([make_sector("黄金", rank=1)])
        rps = make_rps_data(rps5=r5, rps10=r5, rps20=r5)
        recs = {"stocks": [self._stock("兴业银锡", self._news(op=1, title="业绩预增"),
                                        tags="利好消息", score_news=2)]}
        with patch_index(above=True), \
             mock.patch.object(dr, "load_recommendations", return_value={"market_status": {}}), \
             mock.patch.object(dr, "load_early_recommendations", return_value={}):
            stats = dr.build_review_stats(
                rps, {"20260807": {"黄金": {"RPS5": 90, "rank5": 5}},
                      "20260810": {"黄金": {"RPS5": 95, "rank5": 1}}})
        stats["news"] = dr.news_review(recs, None)
        md = dr.render_markdown(stats)
        self.assertIn("消息面复盘", md)
        self.assertIn("偏暖", md)
        self.assertIn("兴业银锡", md)


class TestRecAccuracy(unittest.TestCase):
    """推荐准确率复盘: 读取 prediction_review.json + recommendations.json"""

    def _review_data(self):
        return {
            "update_date": "20260821", "horizon": 1,
            "summary": {"samples": 44, "hit_rate": 0.205,
                        "avg_gain": -3.68, "avg_up_prob": 69.5,
                        "prev_dates": ["20260812", "20260818"]},
            "by_confidence": {
                "高": {"samples": 10, "hit_rate": 0.4, "avg_gain": 3.86},
                "中": {"samples": 32, "hit_rate": 0.156, "avg_gain": -5.73},
            },
            "recent": [
                {"pred_date": "20260818", "code": "600460.SH", "name": "士兰微",
                 "up_prob": 59, "confidence": "中", "sector_count": 1,
                 "gain": 2.9, "hit": True},
                {"pred_date": "20260818", "code": "300980.SZ", "name": "祥源新材",
                 "up_prob": 62, "confidence": "中", "sector_count": 1,
                 "gain": -7.74, "hit": False},
            ],
            "note": "测试说明",
        }

    def _recs_data(self, count=2):
        stocks = []
        for i in range(count):
            stocks.append({"ts_code": "00000%d.SZ" % i, "name": "测试%d" % i,
                           "prediction": {"up_prob": 65 + i}})
        return {"stocks": stocks}

    def test_no_data(self):
        with mock.patch.object(dr, "_load_json", return_value=None):
            out = dr.rec_accuracy()
        self.assertFalse(out["has_data"])

    def test_with_data(self):
        rev = self._review_data()
        recs = self._recs_data(2)
        with mock.patch.object(dr, "_load_json", side_effect=[rev, recs]):
            out = dr.rec_accuracy()
        self.assertTrue(out["has_data"])
        self.assertEqual(out["samples"], 44)
        self.assertEqual(out["hit_rate"], 0.205)
        self.assertEqual(out["avg_gain"], -3.68)
        self.assertEqual(out["today_count"], 2)
        self.assertEqual(out["today_avg_prob"], 65.5)
        self.assertIn("高", out["by_confidence"])
        self.assertEqual(len(out["best"]), 2)
        self.assertEqual(out["best"][0]["name"], "士兰微")
        self.assertEqual(out["worst"][0]["name"], "祥源新材")

    def test_zero_today_recs(self):
        rev = self._review_data()
        recs = {"stocks": []}
        with mock.patch.object(dr, "_load_json", side_effect=[rev, recs]):
            out = dr.rec_accuracy()
        self.assertTrue(out["has_data"])
        self.assertEqual(out["today_count"], 0)
        self.assertIsNone(out["today_avg_prob"])

    def test_markdown_render(self):
        # 使用完整 rps 数据(含 rps5/rps10/rps20 level) 避免 _in_sectors(None) 崩溃
        r5 = make_level([make_sector("黄金", rank=1, rank_change=4, continuous=4,
                                     stock_count=10, rps50_above_90=8)])
        r10 = make_level([make_sector("黄金", rank=1)])
        r20 = make_level([make_sector("黄金", rank=1)])
        rps = make_rps_data(rps5=r5, rps10=r10, rps20=r20)
        rev = self._review_data()
        recs = self._recs_data(1)
        with mock.patch.object(dr, "load_recommendations", return_value=recs), \
             mock.patch.object(dr, "load_early_recommendations", return_value={}), \
             mock.patch.object(dr, "_load_json", return_value=rev), \
             mock.patch.object(dr, "index_ma20_status",
                               return_value={"ok": True, "close": 100, "ma20": 99,
                                              "above": True, "days": 30}):
            stats = dr.build_review_stats(
                rps, {"20260807": {"黄金": {"RPS5": 90, "rank5": 5}},
                      "20260810": {"黄金": {"RPS5": 95, "rank5": 1}}})
        self.assertIn("rec_accuracy", stats)
        md = dr.render_markdown(stats)
        self.assertIn("推荐准确率复盘", md)
        self.assertIn("21%", md)  # 0.205 → 21%


if __name__ == "__main__":
    unittest.main()
