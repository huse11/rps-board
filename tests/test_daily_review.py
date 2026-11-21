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

    def test_pct30_fields_and_level(self):
        # V2.0 需求 3.2: 近30日分位 + 水位形容词; 今日高分=窗口最高 → 偏高水位(29/30)
        hist = self._hist([("202608%02d" % i, i) for i in range(1, 30)] + [("20260830", 30)])
        with patch_index(above=True):
            t = dr.market_tone(self._rps(), hist)
        self.assertAlmostEqual(t["pct30"], 96.7)
        self.assertEqual(t["pct_level"], "偏高水位")
        self.assertIn("近30日", t["note"])

    def test_pct_level_watermark(self):
        # 0%=极低; 100%=极高; 50%=中等
        self.assertEqual(dr.percentile_level(0), "极低水位")
        self.assertEqual(dr.percentile_level(20), "偏低水位")
        self.assertEqual(dr.percentile_level(50), "中等水位")
        self.assertEqual(dr.percentile_level(90), "偏高水位")
        self.assertEqual(dr.percentile_level(100), "极高水位")
        self.assertEqual(dr.percentile_level(None), "")


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

    def test_repeat_detail_flips(self):
        # V2.0 需求 3.4: 反复轮动带次数标注 (翻转9次)
        hist = {}
        vals = [95, 60, 95, 60, 95, 60, 95, 60, 95, 60]
        for i, v in enumerate(vals):
            hist["202608%02d" % (i + 1)] = {"轮动A": {"RPS5": v}}
        rep = dr._repeat_rotation(hist, detail=True)
        self.assertEqual(rep[0]["name"], "轮动A")
        self.assertEqual(rep[0]["flips"], 9)

    def test_mainline_branch_out_split(self):
        # V2.0 需求 3.4: 调出板块昨日属主线分歧(R10/R20在榜,R5不在) → 主线调出(高危);
        # 仅脉冲板块调出 → 支线调出(正常)
        r5 = make_level([make_sector("稳A", rank=1)],
                        out_list=[make_sector("半导体A", status="调出"),
                                  make_sector("脉冲B", status="调出")])
        r10 = make_level([make_sector("稳A", rank=1), make_sector("半导体A", rank=2)])
        r20 = make_level([make_sector("稳A", rank=1), make_sector("半导体A", rank=2)])
        rot = dr.rotation_analysis(r5, r10, r20)
        self.assertEqual([e["name"] for e in rot["main_out"]], ["半导体A"])
        self.assertEqual([e["name"] for e in rot["branch_out"]], ["脉冲B"])
        self.assertEqual(rot["main_out_count"], 1)
        self.assertEqual(rot["branch_out_count"], 1)
        self.assertIn("流入", rot["summary"])

    def test_rotation_summary_switch(self):
        # 流入科技(半导体) / 流出周期(黄金) → 风格切换
        new_in = [make_sector("半导体", rank=5, continuous=1)]
        out_all = ["黄金"]
        s = dr._rotation_summary(new_in, out_all)
        self.assertIn("流入", s)
        self.assertIn("流出", s)
        self.assertIn("切换", s)

    def test_rotation_summary_quiet(self):
        self.assertEqual(dr._rotation_summary([], []), "轮动平稳, 无显著资金流向")


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


class TestHealthV2(unittest.TestCase):
    """V2.0 扩散/种子达标率 (需求 3.5): 合并重复板块 + 判定规则 + 达标率环比"""

    def test_unified_merges_duplicate_sector(self):
        # 同一板块同时满足 扩散健康 + 高质量种子 → 只出现一次, 带完整标签
        in_list = [make_sector("种子", rank=9, continuous=1, stock_count=10, rps50_above_90=5)]  # 50%≥40%
        h = dr.health_analysis(in_list)
        self.assertEqual(len(h["unified"]), 1)
        self.assertEqual(h["unified"][0]["name"], "种子")
        self.assertIn("高质量种子", h["unified"][0]["tags"])
        self.assertIn("扩散健康", h["unified"][0]["tags"])

    def test_unified_sorts_by_category_priority(self):
        # 高质量种子(优先0) 排在 脉冲种子(优先4) 前
        in_list = [make_sector("脉冲", rank=8, continuous=1, stock_count=10, rps50_above_90=2),
                   make_sector("优种", rank=9, continuous=1, stock_count=10, rps50_above_90=5)]
        h = dr.health_analysis(in_list)
        self.assertEqual([x["name"] for x in h["unified"]], ["优种", "脉冲"])

    def test_ratio_chg_with_prev(self):
        in_list = [make_sector("种子", rank=9, continuous=1, stock_count=10, rps50_above_90=5)]  # 50%
        h = dr.health_analysis(in_list, prev_ratios={"种子": 40.0})
        self.assertEqual(h["unified"][0]["ratio_chg"], 10.0)

    def test_ratio_chg_missing_prev_is_none(self):
        in_list = [make_sector("种子", rank=9, continuous=1, stock_count=10, rps50_above_90=5)]
        h = dr.health_analysis(in_list)
        self.assertIsNone(h["unified"][0]["ratio_chg"])

    def test_load_prev_health_ratios(self):
        prev = {"update_date": "20260827",
                "health": {"expand": [{"name": "黄金", "ratio": 71.4}],
                           "seed_pulse": [{"name": "种子", "ratio": 22.2}],
                           "mature": ["钟表珠宝"]}}
        with mock.patch.object(dr, "_load_json", return_value=prev):
            ratios = dr._load_prev_health_ratios("20260828")
        self.assertEqual(ratios, {"黄金": 71.4, "种子": 22.2})  # mature 无 ratio 不采信

    def test_load_prev_health_ratios_same_day_skipped(self):
        prev = {"update_date": "20260828",
                "health": {"expand": [{"name": "黄金", "ratio": 71.4}]}}
        with mock.patch.object(dr, "_load_json", return_value=prev):
            ratios = dr._load_prev_health_ratios("20260828")
        self.assertEqual(ratios, {})

    def test_load_prev_health_ratios_archive_priority(self):
        """2026-08-30 修复: 优先读 health_ratios_history.json 上一交易日, 解决单文件覆盖导致环比恒空"""
        hist_file = str(dr.STATIC_DIR / dr.HEALTH_RATIOS_NAME)
        archive = {"20260827": {"黄金": 71.4}, "20260828": {"黄金": 75.0}}

        def fake_load(path):
            if str(path) == hist_file:
                return archive
            return {"update_date": "20260828", "health": {}}  # 当前文件与今日同日 → 归档优先
        with mock.patch.object(dr, "_load_json", side_effect=fake_load):
            ratios = dr._load_prev_health_ratios("20260829")
        self.assertEqual(ratios, {"黄金": 75.0})

    def test_archive_health_ratios(self):
        """归档当日 unified 达标率 → health_ratios_history.json (供次日环比)"""
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(dr, "STATIC_DIR", Path(td)):
            stats = {"update_date": "20260828",
                     "health": {"unified": [{"name": "黄金", "ratio": 75.0},
                                            {"name": "种子", "ratio": 44.4}]}}
            dr._archive_health_ratios(stats)
            p = Path(td) / dr.HEALTH_RATIOS_NAME
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")),
                             {"20260828": {"黄金": 75.0, "种子": 44.4}})

    def test_build_stats_includes_unified_and_render(self):
        r5 = make_level([make_sector("种子", rank=9, continuous=1, stock_count=10, rps50_above_90=5),
                         make_sector("黄金", rank=1, continuous=4, stock_count=10, rps50_above_90=8)])
        rps = make_rps_data(rps5=r5, rps10=r5, rps20=r5)
        with patch_index(above=True), \
             mock.patch.object(dr, "fetch_market_breadth",
                               return_value={"up_count": 3000, "down_count": 1500,
                                             "limit_up": 40, "limit_down": 5,
                                             "max_board": 4, "total_amount": 9000.0}), \
             mock.patch.object(dr, "load_recommendations", return_value={}), \
             mock.patch.object(dr, "load_early_recommendations", return_value={}), \
             mock.patch.object(dr, "_load_prev_health_ratios",
                               return_value={"种子": 40.0}):
            stats = dr.build_review_stats(
                rps, {"20260807": {"黄金": {"RPS5": 90, "rank5": 5}},
                      "20260810": {"黄金": {"RPS5": 95, "rank5": 1}}})
        unified = stats["health"]["unified"]
        self.assertTrue(any(x["name"] == "种子" and x["ratio_chg"] == 10.0 for x in unified))
        md = dr.render_markdown(stats)
        self.assertIn("判定规则", md)
        self.assertIn("高质量种子", md)


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


class TestRiskV2(unittest.TestCase):
    """V2.0 四级风险等级 (需求 3.6): 低/中/高/极高 + 赛道归类 + 操作建议"""
    def _hist2(self):
        return {"20260807": {"A": {"RPS5": 95}}, "20260810": {"A": {"RPS5": 95}}}

    def _out_level(self, n):
        outs = [make_sector("半导体%d" % i, status="调出") for i in range(n)]
        return make_level([make_sector("黄金", rank=1)], out_list=outs)

    def test_low_risk_normal_rotation(self):
        r5 = make_level([make_sector("黄金", rank=1)])
        risk = dr.risk_analysis(r5, make_level([]), make_level([]), self._hist2())
        self.assertEqual(risk["level_v2"], "low")
        self.assertEqual(risk["level_v2_text"], "低风险")
        self.assertEqual(risk["level_color"], "green")
        self.assertEqual(risk["out_total"], 0)
        self.assertEqual(risk["main_out_total"], 0)
        self.assertTrue(any("正常轮动" in a for a in risk["advice"]))

    def test_mid_risk_batch_out(self):
        # 今日调出5个(≥5) 且集中在单一赛道 → 中风险
        risk = dr.risk_analysis(self._out_level(5), make_level([]), make_level([]),
                                self._hist2())
        self.assertEqual(risk["level_v2"], "mid")
        self.assertEqual(risk["level_v2_text"], "中风险")
        self.assertEqual(risk["level_color"], "yellow")
        self.assertEqual(risk["out_total"], 5)

    def test_high_risk_out_ge8(self):
        # 今日调出8个(≥8) → 高风险
        risk = dr.risk_analysis(self._out_level(8), make_level([]), make_level([]),
                                self._hist2())
        self.assertEqual(risk["level_v2"], "high")
        self.assertEqual(risk["level_color"], "orange")

    def test_high_risk_mainline_batch_out(self):
        # 主线批量调出≥3: 昨日主线(RPS10/20仍在榜) 今日从RPS5掉榜
        outs = [make_sector("半导体A", status="调出"), make_sector("半导体B", status="调出"),
                make_sector("半导体C", status="调出")]
        r5 = make_level([make_sector("黄金", rank=1)], out_list=outs)
        r10 = make_level([make_sector("黄金", rank=1), make_sector("半导体A", rank=2),
                          make_sector("半导体B", rank=3), make_sector("半导体C", rank=4)])
        r20 = make_level([make_sector("黄金", rank=1), make_sector("半导体A", rank=2),
                          make_sector("半导体B", rank=3), make_sector("半导体C", rank=4)])
        risk = dr.risk_analysis(r5, r10, r20, self._hist2())
        self.assertEqual(risk["main_out_total"], 3)
        self.assertEqual(risk["level_v2"], "high")
        self.assertTrue(any("主线批量调出" in a for a in risk["advice"]))

    def test_critical_high_score_3day_drop(self):
        # 高分板块连续3天减少 → 极高风险
        hist = {"20260805": {str(i): {"RPS5": 95} for i in range(20)},
                "20260806": {str(i): {"RPS5": 95} for i in range(15)},
                "20260807": {str(i): {"RPS5": 95} for i in range(10)},
                "20260810": {str(i): {"RPS5": 95} for i in range(5)}}
        risk = dr.risk_analysis(make_level([]), make_level([]), make_level([]), hist)
        self.assertTrue(risk["drop3"])
        self.assertEqual(risk["level_v2"], "critical")
        self.assertEqual(risk["level_color"], "red")
        self.assertTrue(any("连续3天减少" in a for a in risk["advice"]))

    def test_critical_market_plunge(self):
        # 上涨家数 < 下跌家数×40% → 全市场普跌 → 极高风险
        r5 = make_level([make_sector("黄金", rank=1)])
        breadth = {"up_count": 100, "down_count": 400}
        risk = dr.risk_analysis(r5, make_level([]), make_level([]), self._hist2(),
                                breadth=breadth)
        self.assertEqual(risk["level_v2"], "critical")
        self.assertTrue(any("全市场普跌" in a for a in risk["advice"]))

    def test_track_groups_structure(self):
        # 批量调出按赛道归类: [{track, count}]
        risk = dr.risk_analysis(self._out_level(3), make_level([]), make_level([]),
                                self._hist2())
        self.assertEqual(risk["track_groups"], [{"track": "科技", "count": 3}])

    def test_build_stats_includes_risk_v2(self):
        # build_review_stats 接入链路: 复用 fetch_market_breadth 结果
        r5 = make_level([make_sector("黄金", rank=1, stock_count=10, rps50_above_90=9)])
        rps = make_rps_data(rps5=r5, rps10=r5, rps20=r5)
        mb = {"up_count": 3000, "down_count": 1500, "limit_up": 40, "limit_down": 5,
              "max_board": 4, "total_amount": 9000.0, "source": "akshare"}
        with patch_index(above=True), \
             mock.patch.object(dr, "fetch_market_breadth", return_value=mb), \
             mock.patch.object(dr, "load_recommendations", return_value={}), \
             mock.patch.object(dr, "load_early_recommendations", return_value={}):
            stats = dr.build_review_stats(
                rps, {"20260807": {"黄金": {"RPS5": 90, "rank5": 5}},
                      "20260810": {"黄金": {"RPS5": 95, "rank5": 1}}})
        self.assertIn("level_v2", stats["risk"])
        self.assertEqual(stats["risk"]["level_v2"], "low")
        self.assertEqual(stats["market_breadth"]["up_count"], 3000)


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


class TestSentimentV2Quant(unittest.TestCase):
    """V2.0 情绪阶段量化字段 (需求 3.1: 数据可量化/逻辑可追溯)"""

    def _rps5(self, n=12, continuous=1):
        sectors = [make_sector("板块%d" % i, rank=i + 1, continuous=continuous)
                   for i in range(n)]
        return make_level(sectors)

    def test_quant_fields_present(self):
        hist = {"20260810": {str(i): {"RPS5": 95} for i in range(10)},
                "20260811": {str(i): {"RPS5": 95} for i in range(12)}}
        sent = dr.sentiment_phase(self._rps5(12), make_level([]), make_level([]), hist)
        for k in ("prev_high_n", "trend", "trend_days", "rising", "basis", "phase_rules"):
            self.assertIn(k, sent)
        self.assertEqual(sent["prev_high_n"], 10)
        self.assertEqual(sent["trend"], 2)
        self.assertTrue(sent["rising"])
        self.assertIn("高分板块", sent["basis"])

    def test_phase_rules_five_stages(self):
        hist = {"20260810": {str(i): {"RPS5": 95} for i in range(10)}}
        sent = dr.sentiment_phase(self._rps5(10), make_level([]), make_level([]), hist)
        rules = {r["phase"]: r["rule"] for r in sent["phase_rules"]}
        for p in ("冰点期", "启动期", "发酵期", "高潮期", "退潮期"):
            self.assertIn(p, rules)
        self.assertIn("连续3天减少", rules["冰点期"])

    def test_trend_days_negative(self):
        # 高分板块数 12→9→6: 连续2天减少
        hist = {"20260808": {str(i): {"RPS5": 95} for i in range(12)},
                "20260809": {str(i): {"RPS5": 95} for i in range(9)},
                "20260810": {str(i): {"RPS5": 95} for i in range(6)}}
        sent = dr.sentiment_phase(self._rps5(6), make_level([]), make_level([]), hist)
        self.assertEqual(sent["trend_days"], -2)
        self.assertLess(sent["trend"], 0)
        self.assertFalse(sent["rising"])


class TestSentimentMilestones(unittest.TestCase):
    """V2.0 情绪关键拐点标注 (需求 3.1: 主线确认日/首次退潮日/情绪冰点日)"""

    def test_empty_history(self):
        ms = dr.sentiment_milestones({})
        self.assertIsNone(ms["mainline_confirm_date"])
        self.assertIsNone(ms["first_tideout_date"])
        self.assertIsNone(ms["emotion_ice_date"])
        self.assertIn("缺失", ms["note"])

    def test_mainline_confirm_date(self):
        # 板块A 连续5天 RPS5≥95 → 第5天为主线确认日
        hist = {}
        for i in range(1, 7):
            d = "2026080%d" % i
            hist[d] = {"板块A": {"RPS5": 95}}
            if i >= 3:
                hist[d]["板块B"] = {"RPS5": 60}
        ms = dr.sentiment_milestones(hist)
        self.assertEqual(ms["mainline_confirm_date"], "20260805")

    def test_first_tideout_date(self):
        # 高分 12→9→6: 连续2天减少, 首个满足日 = 第二次减少日
        hist = {"20260808": {str(i): {"RPS5": 95} for i in range(12)},
                "20260809": {str(i): {"RPS5": 95} for i in range(9)},
                "20260810": {str(i): {"RPS5": 95} for i in range(6)}}
        ms = dr.sentiment_milestones(hist)
        self.assertEqual(ms["first_tideout_date"], "20260810")

    def test_emotion_ice_date(self):
        # 高分 10→7→4→2: 连续3天减少且≤5 → 冰点日 = 最后一天
        hist = {"20260807": {str(i): {"RPS5": 95} for i in range(10)},
                "20260808": {str(i): {"RPS5": 95} for i in range(7)},
                "20260809": {str(i): {"RPS5": 95} for i in range(4)},
                "20260810": {str(i): {"RPS5": 95} for i in range(2)}}
        ms = dr.sentiment_milestones(hist)
        self.assertEqual(ms["emotion_ice_date"], "20260810")

    def test_emotion_ice_fallback_min(self):
        # 无连续3天减少 → 取高分数量最小值日期
        hist = {"20260809": {str(i): {"RPS5": 95} for i in range(4)},
                "20260810": {str(i): {"RPS5": 95} for i in range(8)}}
        ms = dr.sentiment_milestones(hist)
        self.assertEqual(ms["emotion_ice_date"], "20260809")

    def test_build_stats_includes_milestones(self):
        with patch_index(above=True), \
             mock.patch.object(dr, "load_recommendations", return_value={}):
            stats = dr.build_review_stats(
                make_rps_data(rps5=make_level([make_sector("黄金", rank=1)]),
                              rps10=make_level([make_sector("黄金", rank=1)]),
                              rps20=make_level([make_sector("黄金", rank=1)])),
                {"20260806": {"黄金": {"RPS5": 90}},
                 "20260807": {"黄金": {"RPS5": 95}},
                 "20260810": {"黄金": {"RPS5": 100}}})
        ms = stats["milestones"]
        for k in ("mainline_confirm_date", "first_tideout_date", "emotion_ice_date"):
            self.assertIn(k, ms)
        # 黄金仅连续2天上榜(<5天) → 主线确认日尚未触发; 拐点字段结构完整
        self.assertIsNone(ms["mainline_confirm_date"])


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

    def test_append_trend_field(self):
        # V2.0: 高分板块数较前日变化写入历史(供前端双轴图/拐点标注), 首条为 None
        tmp = tempfile.TemporaryDirectory()
        try:
            with mock.patch.object(dr, "STATIC_DIR", Path(tmp.name)):
                dr._append_sentiment_history(self._stats("20260809"))
                dr._append_sentiment_history(self._stats("20260810"))
                data = self._read(tmp)
                self.assertIsNone(data[0].get("trend"))
                self.assertEqual(data[1]["trend"], 0)  # 两日 high_n 相同 → 持平
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
        # news_review 的 early 参数传 None 会回退读真实文件, 需显式 mock 为空
        stats["news"] = dr.news_review(recs, {})
        md = dr.render_markdown(stats)
        self.assertIn("消息面复盘", md)
        self.assertIn("偏暖", md)
        self.assertIn("兴业银锡", md)


class TestNewsReviewV2(unittest.TestCase):
    """消息面复盘 V2.0 (需求 3.8): 板块标注 + 评分基准 + 资金面 (2026-08-29)"""

    @staticmethod
    def _stock(name="测试", news=None, tags="", score_news=0, industry="有色金属"):
        return {"name": name, "ts_code": "000001", "news": news,
                "tags": tags, "score_news": score_news, "industry": industry}

    def _call(self, recs=None, early=None, breadth=None, fund_flow=None, north=None):
        with mock.patch.object(dr, "load_recommendations", return_value={}), \
             mock.patch.object(dr, "load_early_recommendations", return_value={}):
            return dr.news_review(recs, early, breadth=breadth,
                                  fund_flow=fund_flow, north=north)

    def test_sector_tag_on_official_lists(self):
        # 每条权威利好/利空消息标注对应板块 (需求 3.8: [PCB板块])
        recs = {"stocks": [self._stock("沪电股份", TestNewsReview._news(op=1, title="业绩预增"),
                                        tags="利好消息", score_news=2, industry="电子"),
                           self._stock("某医药股", TestNewsReview._news(on=1, title="集采利空"),
                                        tags="利空消息", score_news=-3, industry="医药")]}
        out = self._call(recs, None)
        self.assertEqual(out["official_pos_list"][0]["sector"], "电子")
        self.assertEqual(out["official_neg_list"][0]["sector"], "医药")
        self.assertEqual(dr._sector_tag(out["official_pos_list"][0]), " [电子板块]")
        self.assertEqual(dr._sector_tag(out["official_neg_list"][0]), " [医药板块]")

    def test_sector_tag_rumor(self):
        recs = {"stocks": [self._stock("传闻股", TestNewsReview._news(rp=1), industry="汽车")]}
        out = self._call(recs, None)
        self.assertEqual(out["rumor_list"][0]["sector"], "汽车")
        self.assertEqual(dr._sector_tag(out["rumor_list"][0]), " [汽车板块]")

    def test_sector_tag_empty(self):
        self.assertEqual(dr._sector_tag({"sector": ""}), "")
        self.assertEqual(dr._sector_tag({}), "")

    def test_score_basis_present(self):
        # 评分基准说明, 让用户理解净影响分含义 (需求 3.8)
        recs = {"stocks": [self._stock("A", TestNewsReview._news(op=1), score_news=2)]}
        out = self._call(recs, None)
        self.assertIn("+2分", out["score_basis"])
        self.assertIn("-3分", out["score_basis"])
        self.assertIn("正分=偏暖", out["score_basis"])

    def test_fund_aggregation(self):
        # 资金面: 两市成交额(亿) / 主力净额(亿) / 北向成分股上涨占比% (需求 3.8)
        breadth = {"total_amount": 12345.6}           # 亿元
        fund_flow = {"main_net": 12.3e8, "main_pct": 2.5}  # 元 → 亿
        north = {"north_ratio": 0.58}                 # 小数 → %
        recs = {"stocks": [self._stock("A", TestNewsReview._news(op=1), score_news=2)]}
        out = self._call(recs, None, breadth=breadth, fund_flow=fund_flow, north=north)
        fund = out["fund"]
        self.assertAlmostEqual(fund["total_amount"], 12345.6)
        self.assertAlmostEqual(fund["main_net"], 12.3)
        self.assertAlmostEqual(fund["main_pct"], 2.5)
        self.assertAlmostEqual(fund["north_ratio"], 58.0)
        self.assertIn("north_note", fund)

    def test_fund_partial_missing(self):
        # 部分数据缺失时 fund 只含可用字段, 不阻断
        recs = {"stocks": [self._stock("A", TestNewsReview._news(op=1), score_news=2)]}
        out = self._call(recs, None, breadth={"total_amount": 9000.0})
        self.assertEqual(list(out["fund"].keys()), ["total_amount"])
        # main_net=0 (无主力数据) → 不写入
        out2 = self._call(recs, None, fund_flow={"main_net": 0, "main_pct": 0})
        self.assertNotIn("main_net", out2["fund"])

    def test_build_stats_news_fund_link(self):
        # build_review_stats 接入链路: 资金面随消息面一并输出 (需求 3.8)
        r5 = make_level([make_sector("黄金", rank=1)])
        rps = make_rps_data(rps5=r5, rps10=r5, rps20=r5)
        recs = {"stocks": [self._stock("兴业银锡", TestNewsReview._news(op=1, title="业绩预增"),
                                        tags="利好消息", score_news=2, industry="有色金属")],
                "market_status": {}}
        mb = {"up_count": 3000, "down_count": 1500, "limit_up": 60, "limit_down": 8,
              "max_board": 5, "total_amount": 9876.5}
        with patch_index(above=True), \
             mock.patch.object(dr, "fetch_market_breadth", return_value=mb), \
             mock.patch.object(dr, "_fetch_market_fund_flow",
                               return_value={"main_net": 5.0e8, "main_pct": 1.2}), \
             mock.patch.object(dr, "_fetch_north_breadth",
                               return_value={"north_ratio": 0.62}), \
             mock.patch.object(dr, "load_recommendations", return_value=recs), \
             mock.patch.object(dr, "load_early_recommendations", return_value={}):
            stats = dr.build_review_stats(
                rps, {"20260807": {"黄金": {"RPS5": 90, "rank5": 5}},
                      "20260810": {"黄金": {"RPS5": 95, "rank5": 1}}})
        nws = stats["news"]
        self.assertIsNotNone(nws)
        self.assertAlmostEqual(nws["fund"]["total_amount"], 9876.5)
        self.assertAlmostEqual(nws["fund"]["main_net"], 5.0)
        self.assertAlmostEqual(nws["fund"]["north_ratio"], 62.0)
        self.assertEqual(nws["official_pos_list"][0]["sector"], "有色金属")

    def test_render_markdown_news_v2(self):
        # md 渲染: 评分基准 + 资金面 + 板块标注
        recs = {"stocks": [self._stock("沪电股份", TestNewsReview._news(op=1, title="业绩预增"),
                                        tags="利好消息", score_news=2, industry="电子")],
                "market_status": {}}
        r5 = make_level([make_sector("黄金", rank=1)])
        rps = make_rps_data(rps5=r5, rps10=r5, rps20=r5)
        with patch_index(above=True), \
             mock.patch.object(dr, "fetch_market_breadth",
                               return_value={"total_amount": 12000.0}), \
             mock.patch.object(dr, "_fetch_market_fund_flow",
                               return_value={"main_net": -30.0e8, "main_pct": -1.5}), \
             mock.patch.object(dr, "_fetch_north_breadth",
                               return_value={"north_ratio": 0.55}), \
             mock.patch.object(dr, "load_recommendations", return_value=recs), \
             mock.patch.object(dr, "load_early_recommendations", return_value={}):
            stats = dr.build_review_stats(
                rps, {"20260807": {"黄金": {"RPS5": 90, "rank5": 5}},
                      "20260810": {"黄金": {"RPS5": 95, "rank5": 1}}})
        self.assertIsNotNone(stats["news"])
        md = dr.render_markdown(stats)
        self.assertIn("评分基准", md)
        self.assertIn("资金面", md)
        self.assertIn("两市成交额12000亿", md)
        self.assertIn("主力资金净流出30亿", md)
        self.assertIn("北向成分股上涨占比55.0%", md)
        self.assertIn("[电子板块]", md)


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

    def test_horizon_fields_passthrough(self):
        # V2.0 (需求 3.9): 3/5日周期收益透传 + 策略名称标识
        rev = self._review_data()
        rev["summary"].update({"avg_gain3": 1.85, "avg_gain5": 2.4,
                               "horizon3_n": 40, "horizon5_n": 30})
        recs = self._recs_data(1)
        with mock.patch.object(dr, "_load_json", side_effect=[rev, recs]):
            out = dr.rec_accuracy()
        self.assertTrue(out["has_data"])
        self.assertEqual(out["strategy"], "日度动量策略")
        self.assertAlmostEqual(out["avg_gain3"], 1.85)
        self.assertAlmostEqual(out["avg_gain5"], 2.4)
        self.assertEqual(out["horizon3_n"], 40)
        self.assertEqual(out["horizon5_n"], 30)
        # 旧版 review 无 horizon 字段 → 缺省 None, 不崩溃
        rev2 = self._review_data()
        with mock.patch.object(dr, "_load_json", side_effect=[rev2, recs]):
            out2 = dr.rec_accuracy()
        self.assertIsNone(out2["avg_gain3"])
        self.assertIsNone(out2["avg_gain5"])

    def test_markdown_horizon_render(self):
        # V2.0 (需求 3.9): md 标题含策略名 + 三个持有周期收益行
        r5 = make_level([make_sector("黄金", rank=1, rank_change=4, continuous=4,
                                     stock_count=10, rps50_above_90=8)])
        r10 = make_level([make_sector("黄金", rank=1)])
        r20 = make_level([make_sector("黄金", rank=1)])
        rps = make_rps_data(rps5=r5, rps10=r10, rps20=r20)
        rev = self._review_data()
        rev["summary"].update({"avg_gain3": -1.2, "avg_gain5": 3.4,
                               "horizon3_n": 40, "horizon5_n": 30})
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
        md = dr.render_markdown(stats)
        self.assertIn("日度动量策略 · 推荐准确率复盘", md)
        self.assertIn("周期收益", md)
        self.assertIn("次日平均", md)
        self.assertIn("持有3日平均-1.2%(40样本)", md)
        self.assertIn("持有5日平均+3.4%(30样本)", md)


# ========================== 市场广度 fetch_market_breadth (V2.0) ==========================

class TestMarketBreadth(unittest.TestCase):
    def test_from_akshare(self):
        ak_data = {"up_count": 3123, "down_count": 1789, "limit_up": 52,
                   "limit_down": 8, "max_board": 5, "total_amount": 9876.5}
        with mock.patch.object(dr, "_fetch_market_breadth_from_ak",
                               return_value=ak_data):
            out = dr.fetch_market_breadth()
        self.assertEqual(out["source"], "akshare")
        self.assertEqual(out["up_count"], 3123)
        self.assertEqual(out["max_board"], 5)
        self.assertEqual(out["total_amount"], 9876.5)

    def test_fallback_to_recommendations(self):
        recs = {"market_status": {"limit_up_count": 30, "limit_down_count": 15,
                                  "advance_ratio": 0.6}}
        with mock.patch.object(dr, "_fetch_market_breadth_from_ak", return_value=None), \
             mock.patch.object(dr, "load_recommendations", return_value=recs):
            out = dr.fetch_market_breadth()
        self.assertEqual(out["source"], "recommendations")
        self.assertEqual(out["limit_up"], 30)
        self.assertEqual(out["limit_down"], 15)
        self.assertIsNone(out["up_count"])      # 绝对涨跌家数降级缺失
        self.assertIsNone(out["total_amount"])

    def test_partial_ak_then_fallback(self):
        # ak 返回不含涨跌停 → 用 recommendations 补齐
        ak_data = {"up_count": 100, "down_count": 50, "limit_up": None,
                   "limit_down": None, "max_board": None, "total_amount": 5000.0}
        recs = {"market_status": {"limit_up_count": 20, "limit_down_count": 5}}
        with mock.patch.object(dr, "_fetch_market_breadth_from_ak",
                               return_value=ak_data), \
             mock.patch.object(dr, "load_recommendations", return_value=recs):
            out = dr.fetch_market_breadth()
        self.assertEqual(out["source"], "akshare")
        self.assertEqual(out["limit_up"], 20)
        self.assertEqual(out["limit_down"], 5)
        self.assertEqual(out["total_amount"], 5000.0)

    def test_both_missing(self):
        with mock.patch.object(dr, "_fetch_market_breadth_from_ak", return_value=None), \
             mock.patch.object(dr, "load_recommendations", return_value={}):
            out = dr.fetch_market_breadth()
        self.assertIsNone(out["source"])
        self.assertIsNone(out["up_count"])
        self.assertIn("缺失", out["note"])

    def test_wired_into_build_stats(self):
        r5 = make_level([make_sector("黄金", rank=1)])
        rps = make_rps_data(rps5=r5, rps10=r5, rps20=r5)
        ak_data = {"up_count": 3123, "down_count": 1789, "limit_up": 52,
                   "limit_down": 8, "max_board": 5, "total_amount": 9876.5}
        with patch_index(above=True), \
             mock.patch.object(dr, "_fetch_market_breadth_from_ak",
                               return_value=ak_data), \
             mock.patch.object(dr, "load_recommendations",
                               return_value={"market_status": {}}), \
             mock.patch.object(dr, "load_early_recommendations", return_value={}):
            stats = dr.build_review_stats(
                rps, {"20260807": {"黄金": {"RPS5": 90, "rank5": 5}},
                      "20260810": {"黄金": {"RPS5": 95, "rank5": 1}}})
        self.assertEqual(stats["market_breadth"]["up_count"], 3123)
        self.assertEqual(stats["market_breadth"]["source"], "akshare")


# ========================== V2.0 新增模块 (需求 4.1-4.5) ==========================

class TestV2NewModules(unittest.TestCase):
    """V2.0 新增模块: 核心结论/仓位指引/昨日验证/标的映射/异常信号 (2026-08-29)"""

    # ---- position_guide (需求 4.2) ----

    def test_position_guide_phases(self):
        self.assertEqual(dr.position_guide({"phase": "高潮期"})["total"], 8)
        self.assertEqual(dr.position_guide({"phase": "发酵期"})["total"], 6)
        self.assertEqual(dr.position_guide({"phase": "启动期"})["total"], 5)
        self.assertEqual(dr.position_guide({"phase": "冰点期"})["total"], 2)
        pg = dr.position_guide({"phase": "震荡期"})
        self.assertEqual(pg["total"], 4)
        self.assertEqual(pg["direction"], "观望, 轻仓试错")
        self.assertIn("总仓位建议4成", pg["reason"])

    def test_position_guide_retreat_signal(self):
        pg = dr.position_guide({"phase": "高潮期", "signals": ["退潮信号"]})
        self.assertEqual(pg["phase_txt"], "高潮期(退潮)")
        self.assertEqual(pg["total"], 3)
        self.assertEqual(pg["direction"], "减仓防守")

    def test_position_guide_default(self):
        pg = dr.position_guide({})
        self.assertEqual(pg["phase"], "震荡期")
        self.assertEqual(pg["total"], 4)

    # ---- core_conclusion (需求 4.1) ----

    def _core_concl_inputs(self, sentiment=None, rotation=None, watch=None):
        return (sentiment or {"phase": "发酵期", "core_n": 3, "trend": 1},
                {}, {"mainline_count": 3}, {},
                rotation or {"repeat_detail": []},
                watch or {"seed_high": [{"name": "黄金"}, {"name": "证券"}]})

    def test_core_conclusion_fields(self):
        cc = dr.core_conclusion(*self._core_concl_inputs())
        self.assertIn("发酵期", cc["text"])
        self.assertIn("主线确认(3个核心)", cc["text"])
        self.assertIn("建议仓位6成", cc["text"])
        self.assertIn("黄金", cc["text"])
        self.assertEqual(cc["phase"], "发酵期(数量回升)")
        self.assertEqual(cc["mainline"], "主线确认(3个核心)")
        self.assertEqual(cc["position"], 6)
        self.assertEqual(cc["focus"], "黄金、证券")

    def test_core_conclusion_fast_rotation(self):
        rot = {"repeat_detail": [{"name": "A", "flips": 2}, {"name": "B", "flips": 3}]}
        cc = dr.core_conclusion(*self._core_concl_inputs(rotation=rot))
        self.assertIn("板块轮动快", cc["text"])
        self.assertEqual(cc["rotation"], "板块轮动快")

    def test_core_conclusion_no_focus(self):
        # 无 core_n 且 resonance 无 mainline_count → 主线缺失; 无种子 → 暂无明确方向
        cc = dr.core_conclusion({"phase": "发酵期", "trend": 1}, {},
                                {"mainline_count": 0}, {}, {"repeat_detail": []},
                                {"seed_high": []})
        self.assertIn("暂无明确方向", cc["text"])
        self.assertIn("主线缺失", cc["text"])

    # ---- yesterday_check (需求 4.3) ----

    def _prev_review(self, phase="震荡期", seeds=("黄金", "证券")):
        return {"update_date": "20260807",
                "sentiment": {"phase": phase},
                "watch": {"seed_high": [{"name": s, "ratio": 30.0} for s in seeds]}}

    def _today_rps5(self, in_names=("黄金",), out_names=("证券",)):
        return make_level([make_sector(n) for n in in_names],
                          out_list=[make_sector(n) for n in out_names])

    def test_yesterday_check_no_prev(self):
        with mock.patch.object(dr, "_load_json", return_value=None):
            out = dr.yesterday_check(self._today_rps5(), {"phase": "高潮期"})
        self.assertFalse(out["has_data"])
        self.assertIn("缺失", out["note"])

    def test_yesterday_check_same_day_skip(self):
        with mock.patch.object(dr, "_load_json",
                               return_value={"update_date": "20260810"}):
            out = dr.yesterday_check(self._today_rps5(), {"phase": "高潮期"}, "20260810")
        self.assertFalse(out["has_data"])
        self.assertIn("同日", out["note"])

    def test_yesterday_check_phase_upgrade_and_verdict(self):
        # 昨日震荡 → 今日高潮 = 转好; 黄金晋级, 证券调出 → 准确率50%
        with mock.patch.object(dr, "_load_json", return_value=self._prev_review("震荡期")):
            out = dr.yesterday_check(self._today_rps5(), {"phase": "高潮期"})
        self.assertTrue(out["has_data"])
        self.assertEqual(out["yesterday_date"], "20260807")
        self.assertEqual(out["phase_check"], "转好(震荡期→高潮期)")
        self.assertEqual(out["track_checked"][0]["verdict"], "晋级")
        self.assertEqual(out["track_checked"][1]["verdict"], "调出")
        self.assertEqual(out["hit_rate"], 50.0)
        self.assertIn("50.0%", out["summary"])

    def test_yesterday_check_phase_downgrade(self):
        with mock.patch.object(dr, "_load_json",
                               return_value=self._prev_review("发酵期")):
            out = dr.yesterday_check(self._today_rps5(), {"phase": "震荡期"})
        self.assertEqual(out["phase_check"], "转差(发酵期→震荡期)")

    def test_yesterday_check_phase_same(self):
        with mock.patch.object(dr, "_load_json",
                               return_value=self._prev_review("震荡期")):
            out = dr.yesterday_check(self._today_rps5(), {"phase": "震荡期"})
        self.assertEqual(out["phase_check"], "符合(震荡期持续)")

    # ---- core_targets (需求 4.4) ----

    def test_core_targets_match(self):
        resonance = {"core": [{"name": "黄金"}, {"name": "证券"}]}
        recs = {"stocks": [
            {"name": "山东黄金", "ts_code": "600547.SH", "sector": "黄金"},
            {"name": "中金黄金", "ts_code": "600489.SH", "sector": "黄金"},
            {"name": "某银行", "ts_code": "600000.SH", "sector": "银行"},
        ]}
        out = dr.core_targets(resonance, recs)
        self.assertEqual(len(out["targets"]), 1)
        self.assertEqual(out["targets"][0]["sector"], "黄金")
        self.assertEqual(len(out["targets"][0]["stocks"]), 2)  # max_per=2
        self.assertEqual(out["targets"][0]["stocks"][0]["code"], "600547.SH")
        self.assertEqual(out["note"], "")

    def test_core_targets_fuzzy_match(self):
        # 主线板块名与推荐 sector 宽容匹配: "黄金" in "黄金概念"
        resonance = {"core": [{"name": "黄金"}]}
        recs = {"stocks": [{"name": "A", "ts_code": "1", "sector": "黄金概念"}]}
        out = dr.core_targets(resonance, recs)
        self.assertEqual(out["targets"][0]["stocks"][0]["name"], "A")

    def test_core_targets_no_match(self):
        resonance = {"core": [{"name": "半导体"}]}
        recs = {"stocks": [{"name": "A", "ts_code": "1", "sector": "银行"}]}
        out = dr.core_targets(resonance, recs)
        self.assertEqual(out["targets"], [])
        self.assertIn("未覆盖", out["note"])

    def test_core_targets_empty_resonance(self):
        out = dr.core_targets({"core": []}, {"stocks": []})
        self.assertEqual(out["targets"], [])
        self.assertIn("未覆盖", out["note"])

    # ---- abnormal_signals (需求 4.5) ----

    @staticmethod
    def _idx(above=True):
        return {"ok": True, "close": 100, "ma20": 99, "above": above, "days": 30}

    def _noise_market(self, avg_gain=2.0):
        return {"avg_gain": avg_gain}

    def test_abnormal_index_distortion(self):
        mb = {"up_count": 40, "down_count": 80, "total_amount": 8000.0}
        out = dr.abnormal_signals(mb, self._noise_market(), {}, {}, {}, {}, self._idx(True))
        types = [s["type"] for s in out["signals"]]
        self.assertIn("index_distortion", types)
        s = next(x for x in out["signals"] if x["type"] == "index_distortion")
        self.assertEqual(s["level"], "high")
        self.assertEqual(s["level_text"], "高风险")
        self.assertIn("67%", s["desc"])

    def test_abnormal_divergence(self):
        health = {"unified": [{"name": "黄金", "ratio": 30.0, "ratio_chg": -5.0}]}
        history = {"20260806": {"黄金": {"RPS5": 90}},
                   "20260807": {"黄金": {"RPS5": 92}},
                   "20260810": {"黄金": {"RPS5": 95}}}  # 95 为近5日新高
        out = dr.abnormal_signals({}, self._noise_market(), {}, health, history, {}, self._idx())
        types = [s["type"] for s in out["signals"]]
        self.assertIn("divergence", types)
        s = next(x for x in out["signals"] if x["type"] == "divergence")
        self.assertIn("黄金", s["desc"])

    def test_abnormal_divergence_no_new_high(self):
        # RPS 未创新高(前日更高) → 顶背离不触发
        health = {"unified": [{"name": "黄金", "ratio": 30.0, "ratio_chg": -5.0}]}
        history = {"20260806": {"黄金": {"RPS5": 96}},
                   "20260807": {"黄金": {"RPS5": 95}},
                   "20260810": {"黄金": {"RPS5": 95}}}
        out = dr.abnormal_signals({}, self._noise_market(), {}, health, history, {}, self._idx())
        self.assertNotIn("divergence", [s["type"] for s in out["signals"]])

    def test_abnormal_volume_stagnation(self):
        mb = {"up_count": 60, "down_count": 70, "total_amount": 13000.0}  # 跌多涨少 + 放量
        out = dr.abnormal_signals(mb, self._noise_market(avg_gain=0.5), {}, {}, {}, {}, self._idx())
        types = [s["type"] for s in out["signals"]]
        self.assertIn("volume_stagnation", types)
        s = next(x for x in out["signals"] if x["type"] == "volume_stagnation")
        self.assertEqual(s["level"], "mid")
        self.assertEqual(s["level_text"], "中风险")

    def test_abnormal_capital_exit(self):
        risk = {"main_out_total": 3}
        out = dr.abnormal_signals({}, self._noise_market(), risk, {}, {}, {}, self._idx(True))
        types = [s["type"] for s in out["signals"]]
        self.assertIn("capital_exit", types)
        s = next(x for x in out["signals"] if x["type"] == "capital_exit")
        self.assertEqual(s["level"], "critical")
        self.assertEqual(s["level_text"], "极高风险")
        self.assertIn("3个", s["desc"])
        # 指数站上20日线为触发前提; 跌破则不触发
        out2 = dr.abnormal_signals({}, self._noise_market(), risk, {}, {}, {}, self._idx(False))
        self.assertNotIn("capital_exit", [s["type"] for s in out2["signals"]])

    def test_abnormal_none_when_insufficient(self):
        out = dr.abnormal_signals({}, {}, {}, {}, {}, {}, None)
        self.assertEqual(out["signals"], [])
        self.assertIn("数据不足", out["note"])

    # ---- build_review_stats 接入链路 (需求 4.1-4.5) ----

    def test_build_stats_v2_modules(self):
        r5 = make_level([make_sector("黄金", rank=1)])
        rps = make_rps_data(rps5=r5, rps10=r5, rps20=r5)
        recs = {"stocks": [{"name": "山东黄金", "ts_code": "600547.SH", "sector": "黄金"}],
                "market_status": {}}
        mb = {"up_count": 40, "down_count": 80, "total_amount": 8000.0}
        history = {"20260807": {"黄金": {"RPS5": 90, "rank5": 5}},
                   "20260810": {"黄金": {"RPS5": 95, "rank5": 1}}}
        with patch_index(above=True), \
             mock.patch.object(dr, "fetch_market_breadth", return_value=mb), \
             mock.patch.object(dr, "_fetch_market_fund_flow",
                               return_value={"main_net": 5.0e8, "main_pct": 1.2}), \
             mock.patch.object(dr, "_fetch_north_breadth",
                               return_value={"north_ratio": 0.62}), \
             mock.patch.object(dr, "load_recommendations", return_value=recs), \
             mock.patch.object(dr, "load_early_recommendations", return_value={}), \
             mock.patch.object(dr, "_load_json", return_value=None):
            stats = dr.build_review_stats(rps, history)
        self.assertIn("core_conclusion", stats)
        self.assertIn("position_guide", stats)
        self.assertIn("yesterday_check", stats)
        self.assertIn("core_targets", stats)
        self.assertIn("abnormal_signals", stats)
        self.assertTrue(stats["core_conclusion"]["text"])
        # 仓位指引与 build 实际情绪阶段一致 (小样本数据 → 冰点期, 总仓位2成)
        self.assertEqual(stats["position_guide"]["total"],
                         dr.position_guide(stats["sentiment"])["total"])
        self.assertEqual(stats["position_guide"]["phase"],
                         stats["sentiment"].get("phase") or stats["sentiment"].get("main"))
        self.assertFalse(stats["yesterday_check"]["has_data"])
        md = dr.render_markdown(stats)
        # 置顶核心结论 + 仓位指引 + 核心标的 + 昨日验证 + 异常信号 各渲染段
        self.assertIn("> **今日情绪处于", md)
        self.assertIn("仓位指引", md)
        self.assertIn("核心标的", md)
        self.assertIn("【昨日复盘验证】", md)
        self.assertIn("【异常信号高亮】", md)
        self.assertIn("指数失真", md)


# ========================== V2.0 进阶观察 (需求 3.10) ==========================

class TestStyleOutlook(unittest.TestCase):
    """进阶观察: 风格标签完善 + 风格切换判断 + 明日核心策略要点"""

    # ---- 风格关键词扩充 (未归类 ≤20%) ----

    def test_style_keywords_new_coverage(self):
        # 之前未归类的常见板块名 → 现在全部命中
        self.assertEqual(dr._style_of("焦煤"), {"周期"})
        self.assertEqual(dr._style_of("动力煤"), {"周期"})
        self.assertEqual(dr._style_of("铜"), {"周期"})
        self.assertEqual(dr._style_of("无机盐"), {"周期"})
        self.assertEqual(dr._style_of("其他塑料制品"), {"周期"})
        self.assertEqual(dr._style_of("非金属材料Ⅲ"), {"周期"})
        self.assertEqual(dr._style_of("种子"), {"消费"})
        self.assertEqual(dr._style_of("钟表珠宝"), {"消费"})
        self.assertEqual(dr._style_of("照明设备"), {"科技"})
        self.assertIn("制造", dr._style_of("制冷空调设备"))
        self.assertIn("金融", dr._style_of("金融控股"))
        # V2.0 服务器实盘数据覆盖 (2026-08-29 rps5 在榜): 复合肥/其他农产品加工/其他化学原料
        self.assertEqual(dr._style_of("复合肥"), {"周期"})
        self.assertEqual(dr._style_of("其他农产品加工"), {"消费"})
        self.assertEqual(dr._style_of("其他化学原料"), {"周期"})

    def test_real_data_unclassified_below_20(self):
        # 用当前实际 rps5 板块名验证 未归类 ≤20% (需求 3.10 达标)
        import json
        with open(os.path.join(os.path.dirname(__file__), "..", "static", "rps_data.json"),
                  encoding="utf-8") as f:
            d = json.load(f)
        names = [s["name"] for s in (d.get("rps5") or {}).get("in_list", []) if s.get("name")]
        unc = [n for n in names if dr._style_of(n) == {"未归类"}]
        pct = len(unc) / len(names) * 100 if names else 0
        self.assertLessEqual(pct, 20.0)

    # ---- style_outlook ----

    def _style(self, r5_top="科技", r10_top="科技"):
        def _mk(items):
            return {k: {"count": c, "pct": c} for k, c in items.items()}
        return {"rps5": _mk({r5_top: 3, "周期": 1}),
                "rps10": _mk({r10_top: 3}),
                "rps20": _mk({r10_top: 2})}

    def test_outlook_unclassified_all_covered(self):
        r5 = make_level([make_sector("半导体"), make_sector("黄金"), make_sector("煤炭")])
        out = dr.style_outlook(r5, make_level([]), make_level([]), {"phase": "震荡期"},
                               {}, {"mainline_count": 0})
        self.assertEqual(out["unclassified"]["pct"], 0.0)
        self.assertTrue(out["ratio_ok"])

    def test_outlook_unclassified_over_limit(self):
        r5 = make_level([make_sector("神秘A"), make_sector("神秘B"),
                         make_sector("半导体"), make_sector("黄金")])
        out = dr.style_outlook(r5, make_level([]), make_level([]), {}, {}, {})
        self.assertEqual(out["unclassified"]["count"], 2)
        self.assertEqual(out["unclassified"]["pct"], 50.0)
        self.assertFalse(out["ratio_ok"])
        self.assertIn("扩充关键词", out["note"])

    def test_outlook_switch_true(self):
        out = dr.style_outlook(make_level([]), make_level([]), make_level([]),
                               {}, {}, {}, style=self._style("科技", "周期"))
        self.assertTrue(out["switch"])
        self.assertIn("切换", out["switch_desc"])
        self.assertIn("科技", out["switch_desc"])
        self.assertIn("周期", out["switch_desc"])

    def test_outlook_switch_false(self):
        out = dr.style_outlook(make_level([]), make_level([]), make_level([]),
                               {}, {}, {}, style=self._style("科技", "科技"))
        self.assertFalse(out["switch"])
        self.assertIn("延续", out["switch_desc"])

    def test_outlook_tomorrow_points(self):
        r5 = make_level([make_sector("半导体"), make_sector("黄金")])
        sent = {"phase": "高潮期", "signals": ["退潮信号"]}
        risk = {"main_out_total": 3}
        res = {"mainline_count": 2}
        out = dr.style_outlook(r5, make_level([]), make_level([]), sent, risk, res)
        txt = "；".join(out["tomorrow_points"])
        self.assertIn("退潮信号", txt)
        self.assertIn("3个", txt)
        self.assertIn("2个", txt)

    def test_outlook_fallback_point(self):
        out = dr.style_outlook(make_level([]), make_level([]), make_level([]), {}, {}, {})
        self.assertIn("轻仓观望", out["tomorrow_points"][0])

    def test_build_stats_style_outlook_render(self):
        r5 = make_level([make_sector("半导体", rank=1), make_sector("黄金", rank=2),
                         make_sector("煤炭", rank=3)])
        rps = make_rps_data(rps5=r5, rps10=r5, rps20=r5)
        history = {"20260807": {"半导体": {"RPS5": 90, "rank5": 5},
                                "黄金": {"RPS5": 90, "rank5": 6}},
                   "20260810": {"半导体": {"RPS5": 95, "rank5": 1},
                                "黄金": {"RPS5": 95, "rank5": 2},
                                "煤炭": {"RPS5": 95, "rank5": 3}}}
        with patch_index(above=True), \
             mock.patch.object(dr, "fetch_market_breadth",
                               return_value={"up_count": 100, "down_count": 50,
                                             "total_amount": 8000.0}), \
             mock.patch.object(dr, "_fetch_market_fund_flow", return_value=None), \
             mock.patch.object(dr, "_fetch_north_breadth", return_value={}), \
             mock.patch.object(dr, "load_recommendations",
                               return_value={"stocks": [], "market_status": {}}), \
             mock.patch.object(dr, "load_early_recommendations", return_value={}), \
             mock.patch.object(dr, "_load_json", return_value=None):
            stats = dr.build_review_stats(rps, history)
        self.assertIn("style_outlook", stats)
        self.assertTrue(stats["style_outlook"]["ratio_ok"])
        md = dr.render_markdown(stats)
        self.assertIn("【进阶观察】", md)
        self.assertIn("风格标签完善", md)
        self.assertIn("风格切换判断", md)
        self.assertIn("明日核心策略要点", md)


if __name__ == "__main__":
    unittest.main()
