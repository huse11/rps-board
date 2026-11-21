# -*- coding: utf-8 -*-
"""
单元测试：每周 A 股收盘复盘生成器 v1 (weekly_review.py)
覆盖: 周号(跨年) / 周度定调 / 本周主线 / 本周轮动 / 情绪演变 / 消息面周度 / 下周关注 /
      汇总 build / markdown 渲染 / main 幂等
运行: python -m unittest tests.test_weekly_review -v
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
import weekly_review as wr


DATES = ["20260817", "20260818", "20260819", "20260820", "20260821"]


def make_history(on_map):
    """on_map: {date: [板块名,...]} → history dict (在榜板块 RPS5=95, rank5=1)"""
    h = {}
    for d in DATES:
        h[d] = {}
        for name in on_map.get(d, []):
            h[d][name] = {"RPS5": 95.0, "rank5": 1}
    return h


def make_many(prefix, n):
    return ["%s%d" % (prefix, i) for i in range(n)]


class TestWeekId(unittest.TestCase):
    def test_normal_week(self):
        self.assertEqual(wr.week_id_of("20260821"), "2026W34")

    def test_cross_year(self):
        # 2025-12-29 属于 2026 年第 1 周 (ISO 跨年)
        self.assertEqual(wr.week_id_of("20251229"), "2026W01")

    def test_week_dates_window(self):
        h = make_history({"20260817": ["A"]})
        out = wr._week_dates(h, days=3)
        self.assertEqual(out, ["20260819", "20260820", "20260821"])
        out2 = wr._week_dates(h)  # 缺省 days=5
        self.assertEqual(out2, DATES)


class TestWeekTone(unittest.TestCase):
    def test_bullish(self):
        h = make_history({d: make_many("S", 25) for d in DATES[:-1]})
        h["20260821"] = {s: {"RPS5": 95.0, "rank5": i} for i, s in enumerate(make_many("S", 30))}
        out = wr.week_tone(h)
        self.assertEqual(out["tone"], "周度偏强")  # avg≥10 且 diff≥1
        self.assertEqual(out["first"], 25)
        self.assertEqual(out["last"], 30)
        self.assertEqual(out["diff"], 5)
        self.assertGreaterEqual(out["avg"], 10)

    def test_strong(self):
        h = make_history({d: make_many("S", 25) for d in DATES})
        out = wr.week_tone(h)
        self.assertEqual(out["tone"], "周度强")  # avg≥10 但 diff=0<1

    def test_weak(self):
        # 周内从 8 收缩到 3 (末日≤4 且 diff≤-1) → 周度偏弱
        h = make_history({d: make_many("S", 8) for d in DATES[:-1]})
        h["20260821"] = {s: {"RPS5": 95.0, "rank5": i} for i, s in enumerate(make_many("S", 3))}
        out = wr.week_tone(h)
        self.assertEqual(out["tone"], "周度偏弱")  # 末日≤4 / 周内跌幅≤-1

    def test_neutral(self):
        h = make_history({d: make_many("S", 8) for d in DATES})
        out = wr.week_tone(h)
        self.assertEqual(out["tone"], "周度中性")

    def test_empty(self):
        out = wr.week_tone({})
        self.assertEqual(out["tone"], "周度中性")
        self.assertEqual(out["series"], [])


class TestWeekMainlines(unittest.TestCase):
    def test_sort_by_days_then_rps(self):
        h = make_history({
            "20260817": ["A", "B", "ST噪声"],
            "20260818": ["A", "B"],
            "20260819": ["A", "B"],
            "20260820": ["A"],
            "20260821": ["A"],
        })
        out = wr.week_mainlines(h)
        names = [x["name"] for x in out]
        self.assertEqual(names, ["A", "B"])  # A=5天 B=4天, ST噪声被过滤
        self.assertEqual(out[0]["days_on"], 5)
        self.assertNotIn("ST噪声", names)

    def test_no_mainline(self):
        h = make_history({"20260817": ["A"]})
        self.assertEqual(wr.week_mainlines(h), [])


class TestWeekRotation(unittest.TestCase):
    def test_sustained_newin_dropped(self):
        h = make_history({
            "20260817": ["A", "C"],
            "20260818": ["A"],
            "20260819": ["A"],
            "20260820": ["A"],
            "20260821": ["A", "B"],
        })
        out = wr.week_rotation(h)
        # A 全周在榜 → sustained; B 末日在榜且仅1天 → new_in; C 首日在榜末日不在榜 → dropped
        self.assertEqual([x["name"] for x in out["sustained"]], ["A"])
        self.assertEqual([x["name"] for x in out["new_in"]], ["B"])
        self.assertEqual([x["name"] for x in out["dropped"]], ["C"])

    def test_insufficient_days(self):
        h = make_history({"20260817": ["A"]})
        out = wr.week_rotation(h, days=1)
        self.assertEqual(out["new_in"], [])
        self.assertEqual(out["sustained"], [])
        self.assertEqual(out["dropped"], [])


class TestWeekSentiment(unittest.TestCase):
    def test_warming(self):
        sent = [{"date": "20260818", "phase": "冰点期"},
                {"date": "20260819", "phase": "启动期"},
                {"date": "20260820", "phase": "发酵期"},
                {"date": "20260821", "phase": "高潮期"}]
        out = wr.week_sentiment(sent, DATES)
        self.assertEqual(out["start_phase"], "冰点期")
        self.assertEqual(out["end_phase"], "高潮期")
        self.assertEqual(out["phase_change"], "升温")
        self.assertIn("升温", out["trend"])
        self.assertEqual(len(out["series"]), 4)

    def test_cooling(self):
        sent = [{"date": "20260819", "phase": "高潮期"},
                {"date": "20260821", "phase": "退潮期"}]
        out = wr.week_sentiment(sent, DATES)
        self.assertEqual(out["phase_change"], "降温")

    def test_empty(self):
        out = wr.week_sentiment([], DATES)
        self.assertEqual(out["series"], [])
        self.assertEqual(out["trend"], "")


class TestWeekNews(unittest.TestCase):
    def test_news_aggregate(self):
        recs = {"stocks": [{"name": "兴业银锡", "ts_code": "000426",
                            "news": {"official_pos": 1, "official_neg": 0,
                                     "rumor_pos": 0, "rumor_neg": 0,
                                     "headlines": [{"title": "业绩预增"}]},
                            "tags": "利好消息", "score_news": 2}]}
        with mock.patch.object(dr, "load_recommendations", return_value={}), \
             mock.patch.object(dr, "load_early_recommendations", return_value={}):
            out = wr.week_news(recs, None)
        self.assertIsNotNone(out)
        self.assertTrue(out["has_news"])
        self.assertEqual(out["env"], "偏暖")
        self.assertEqual(out["score_sum"], 2)

    def test_news_none(self):
        with mock.patch.object(dr, "load_recommendations", return_value={}), \
             mock.patch.object(dr, "load_early_recommendations", return_value={}):
            out = wr.week_news({"stocks": []}, None)
        self.assertIsNone(out)


class TestWeekWatch(unittest.TestCase):
    def test_watch_merge(self):
        drv = {"watch": {"critical": [{"name": "X", "rps5": 89.5}],
                         "seed_high": [{"name": "Y", "ratio": 80.0}],
                         "diverge_watch": [{"name": "Z", "drop_days": 2}]},
               "potential_sectors": [{"name": "P", "rps5": 88.5}]}
        out = wr.week_watch(drv)
        self.assertEqual(out["critical"][0]["name"], "X")
        self.assertEqual(out["seed_high"][0]["name"], "Y")
        self.assertEqual(out["diverge_watch"][0]["name"], "Z")
        self.assertEqual(out["potential"][0]["name"], "P")

    def test_watch_empty(self):
        self.assertEqual(wr.week_watch(None), {"critical": [], "diverge_watch": [],
                                               "seed_high": [], "potential": [],
                                               "focus": [], "observe": [], "avoid": []})
        self.assertEqual(wr.week_watch({}), {"critical": [], "diverge_watch": [],
                                             "seed_high": [], "potential": [],
                                             "focus": [], "observe": [], "avoid": []})

    def test_watch_v2_categories(self):
        """V2.0 (需求 3.7): focus/observe/avoid 三类 + 优先级 + 跟踪规则"""
        drv = {"watch": {"critical": [{"name": "X", "rps5": 89.5}],
                         "seed_high": [{"name": "Y", "ratio": 80.0}],
                         "diverge_watch": [{"name": "Z", "drop_days": 2}]},
               "potential_sectors": [{"name": "P", "rps5": 88.5}]}
        rot = {"dropped": [{"name": "D", "risk_level": "高风险", "risk": "原持续主线衰退",
                            "days_on": 4}]}
        out = wr.week_watch(drv, rotation=rot)
        self.assertEqual([x["name"] for x in out["focus"]], ["Y", "Z"])  # 种子+分歧 → 重点跟踪★
        self.assertEqual([x["name"] for x in out["observe"]], ["X", "P"])  # 临界+潜力 → 普通观察
        self.assertEqual([x["name"] for x in out["avoid"]], ["D"])  # 中/高风险衰退 → 风险回避⚠
        self.assertTrue(all(x.get("priority") for x in out["focus"]))
        self.assertTrue(all(x.get("rule") for x in out["observe"]))
        self.assertEqual(out["avoid"][0]["priority"], "⚠")

    def test_watch_rps5_backfilled(self):
        """2026-08-30 修复: focus/avoid 的 rps5 从 rps_history 补齐, 不再恒空"""
        drv = {"watch": {"seed_high": [{"name": "Y", "ratio": 80.0}],
                         "diverge_watch": [{"name": "Z", "drop_days": 2}]},
               "potential_sectors": []}
        rot = {"dropped": [{"name": "D", "risk_level": "高风险", "risk": "衰退", "days_on": 4}]}
        hist = {"20260828": {"Y": {"RPS5": 92.0}, "Z": {"RPS5": 88.5}, "D": {"RPS5": 95.0}}}
        with mock.patch.object(wr, "_load_json", return_value=hist):
            out = wr.week_watch(drv, rotation=rot)
        self.assertEqual(out["focus"][0]["rps5"], 92.0)   # 高质量种子 Y
        self.assertEqual(out["focus"][1]["rps5"], 88.5)   # 分歧 Z
        self.assertEqual(out["avoid"][0]["rps5"], 95.0)   # 衰退 D


class TestBuildWeeklyReview(unittest.TestCase):
    def test_build_all_sections(self):
        history = make_history({
            "20260817": ["A", "C"],
            "20260818": ["A"],
            "20260819": ["A"],
            "20260820": ["A"],
            "20260821": ["A", "B"],
        })
        rps_data = {"update_date": "20260821"}
        drv = {"watch": {"critical": [{"name": "X", "rps5": 89.5}]},
               "potential_sectors": []}
        sent = [{"date": "20260818", "phase": "冰点期"},
                {"date": "20260821", "phase": "启动期"}]
        recs = {"stocks": [{"name": "A", "ts_code": "000001",
                            "news": {"official_pos": 1, "official_neg": 0,
                                     "rumor_pos": 0, "rumor_neg": 0, "headlines": []},
                            "tags": "利好消息", "score_news": 2}]}
        with mock.patch.object(dr, "load_recommendations", return_value={}), \
             mock.patch.object(dr, "load_early_recommendations", return_value={}):
            out = wr.build_weekly_review(history=history, rps_data=rps_data,
                                         daily_review=drv, recs=recs,
                                         early_recs=None, sent_history=sent)
        self.assertEqual(out["week_id"], "2026W34")
        self.assertEqual(out["update_date"], "20260821")
        self.assertEqual(out["days"], 5)
        self.assertIn("tone", out)
        self.assertIn("mainlines", out)
        self.assertIn("rotation", out)
        self.assertIn("sentiment", out)
        self.assertIn("news", out)
        self.assertIn("watch", out)
        self.assertEqual(out["mainlines"][0]["name"], "A")
        self.assertEqual(out["rotation"]["new_in"][0]["name"], "B")
        self.assertTrue(out["news"]["has_news"])

    def test_render_markdown_sections(self):
        history = make_history({"20260817": ["A"], "20260821": ["A"]})
        with mock.patch.object(dr, "load_recommendations", return_value={}), \
             mock.patch.object(dr, "load_early_recommendations", return_value={}):
            st = wr.build_weekly_review(history=history,
                                        rps_data={"update_date": "20260821"},
                                        daily_review={}, recs={"stocks": []},
                                        early_recs=None, sent_history=[])
        md = wr.render_markdown(st)
        for sec in ("本周定调", "本周主线", "本周轮动", "本周情绪演变",
                    "消息面周度快照", "下周关注", "免责声明"):
            self.assertIn(sec, md)

    def test_render_markdown_no_news(self):
        st = {"week_id": "2026W34", "update_date": "20260821",
              "tone": {"series": [], "tone": "周度中性", "note": ""},
              "mainlines": [], "rotation": {}, "sentiment": {}, "news": None, "watch": {}}
        md = wr.render_markdown(st)
        self.assertIn("暂无消息面信号", md)


class TestMain(unittest.TestCase):
    def test_main_idempotent(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            history = make_history({"20260817": ["A"], "20260821": ["A"]})
            with mock.patch.object(wr, "STATIC_DIR", Path(tmp.name)), \
                 mock.patch.object(dr, "load_recommendations", return_value={}), \
                 mock.patch.object(dr, "load_early_recommendations", return_value={}):
                for name, obj in (("rps_data.json", {"update_date": "20260821"}),
                                  ("rps_history.json", history),
                                  ("daily_review.json", {}),
                                  ("review_sentiment_history.json", [])):
                    with open(os.path.join(tmp.name, name), "w", encoding="utf-8") as f:
                        json.dump(obj, f)
                wr.main(force=True)
                wr.main(force=False)  # 本周已生成 → 跳过
                with open(os.path.join(tmp.name, "weekly_review.json"), encoding="utf-8") as f:
                    data = json.load(f)
                self.assertEqual(data["week_id"], "2026W34")
                self.assertEqual(data["update_date"], "20260821")
        finally:
            tmp.cleanup()

    def test_main_no_data(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            with mock.patch.object(wr, "STATIC_DIR", Path(tmp.name)):
                wr.main(force=True)  # 无数据 → 跳过不报错
        finally:
            tmp.cleanup()


class TestWeekRecAccuracy(unittest.TestCase):
    """本周推荐准确率: 从 prediction_review.json 筛选本周预测日验证结果"""

    def _review_data(self):
        return {
            "update_date": "20260821", "horizon": 1,
            "summary": {"samples": 10, "hit_rate": 0.3,
                        "avg_gain": -1.0, "avg_up_prob": 65.0,
                        "prev_dates": ["20260817", "20260818"]},
            "by_confidence": {},
            "recent": [  # 最近15条(旧字段, all 缺失时回退用)
                {"pred_date": "20260818", "code": "600460.SH", "name": "士兰微",
                 "up_prob": 59, "confidence": "高", "sector_count": 2,
                 "gain": 2.9, "hit": True},
                {"pred_date": "20260819", "code": "300980.SZ", "name": "祥源新材",
                 "up_prob": 62, "confidence": "中", "sector_count": 1,
                 "gain": -7.74, "hit": False},
                {"pred_date": "20260812", "code": "002552.SZ", "name": "宝鼎科技",
                 "up_prob": 77, "confidence": "高", "sector_count": 2,
                 "gain": 5.0, "hit": True},
            ],
            "all": [  # 完整验证列表(周度筛选取用, 与每日口径一致=次日收盘≥预测日现价)
                {"pred_date": "20260817", "code": "600001.SH", "name": "A股",
                 "up_prob": 60, "confidence": "高", "sector_count": 1,
                 "gain": 1.0, "hit": True},
                {"pred_date": "20260817", "code": "600002.SH", "name": "B股",
                 "up_prob": 55, "confidence": "中", "sector_count": 1,
                 "gain": -2.0, "hit": False},
                {"pred_date": "20260818", "code": "600460.SH", "name": "士兰微",
                 "up_prob": 59, "confidence": "高", "sector_count": 2,
                 "gain": 2.9, "hit": True},
                {"pred_date": "20260819", "code": "300980.SZ", "name": "祥源新材",
                 "up_prob": 62, "confidence": "中", "sector_count": 1,
                 "gain": -7.74, "hit": False},
                {"pred_date": "20260812", "code": "002552.SZ", "name": "宝鼎科技",
                 "up_prob": 77, "confidence": "高", "sector_count": 2,
                 "gain": 5.0, "hit": True},
            ],
            "note": "测试",
        }

    def test_no_data(self):
        with mock.patch.object(wr, "_load_json", return_value=None):
            out = wr.week_rec_accuracy({})
        self.assertFalse(out["has_data"])

    def test_week_filter(self):
        """本周日期内的预测被筛选 (用 all 完整列表, 避免 recent 截断)"""
        rev = self._review_data()
        # 本周 = 20260817~20260821, all 中 0817(A,B)+0818+0819 在内, 0812 不在
        history = {d: {"A": {"RPS5": 90}} for d in DATES}
        with mock.patch.object(wr, "_load_json", return_value=rev):
            out = wr.week_rec_accuracy(history)
        self.assertTrue(out["has_data"])
        self.assertEqual(out["week_samples"], 4)  # 0817×2 + 0818 + 0819
        # hit: A(True) B(False) 士兰微(True) 祥源(False) → 2/4 = 50%
        self.assertEqual(out["week_hit_rate"], 0.5)
        self.assertEqual(out["best"][0]["name"], "士兰微")

    def test_fallback_all_recent(self):
        """本周无匹配时退用全部已验证样本(all)"""
        rev = self._review_data()
        # 本周日期不含任何 pred_date
        history = {"20260101": {"A": {"RPS5": 90}}}
        with mock.patch.object(wr, "_load_json", return_value=rev):
            out = wr.week_rec_accuracy(history)
        self.assertTrue(out["has_data"])
        self.assertEqual(out["week_samples"], 5)  # 全部 all

    def test_confidence_breakdown(self):
        rev = self._review_data()
        history = {d: {"A": {"RPS5": 90}} for d in DATES}
        with mock.patch.object(wr, "_load_json", return_value=rev):
            out = wr.week_rec_accuracy(history)
        bc = out["by_confidence"]
        self.assertIn("高", bc)
        self.assertIn("中", bc)
        # 本周4样本: 高=A股+士兰微, 中=B股+祥源
        self.assertEqual(bc["高"]["samples"], 2)
        self.assertEqual(bc["中"]["samples"], 2)


# ========================== V2.0 测试 (每周复盘升级) ==========================

WEEK1 = ["20260803", "20260804", "20260805", "20260806", "20260807"]
WEEK2 = ["20260810", "20260811", "20260812", "20260813", "20260814"]


def make_rps_history(days_on, sectors, extra=None):
    """多周历史: days_on: {date: 在榜板块数}, sectors 生成在榜板块名
    每个板块 RPS5=95, RPS10=95, RPS20=95 (供三周期共振测试)"""
    h = {}
    for d in days_on:
        h[d] = {}
        for i in range(days_on[d]):
            nm = sectors[i]
            v = {"RPS5": 95.0, "RPS10": 95.0, "RPS20": 95.0, "rank5": i + 1}
            if extra and nm in extra:
                v.update(extra[nm])
            h[d][nm] = v
    return h


class TestWeekToneV2(unittest.TestCase):
    def test_three_period_series(self):
        h = make_history({d: make_many("S", 10) for d in DATES})
        out = wr.week_tone(h)
        self.assertEqual(len(out["rps5_series"]), 5)
        self.assertEqual(len(out["rps10_series"]), 5)
        self.assertEqual(len(out["rps20_series"]), 5)
        self.assertEqual(out["rps5_series"][0]["high_n"], 10)
        # make_history 无 RPS10/20 → 两序列全 0
        self.assertEqual(out["rps10_series"][0]["high_n"], 0)

    def test_hist_water_level(self):
        """近3周周均高分分位: 8 / 12 / 10 → 当前10 → 67% 中高水位"""
        secs = make_many("S", 12)
        h = {}
        for d in WEEK1:
            h[d] = {s: {"RPS5": 95.0} for s in secs[:8]}
        for d in WEEK2:
            h[d] = {s: {"RPS5": 95.0} for s in secs[:12]}
        for d in DATES:
            h[d] = {s: {"RPS5": 95.0} for s in secs[:10]}
        out = wr.week_tone(h)
        wl = out["hist_water_level"]
        self.assertIsNotNone(wl)
        self.assertEqual(wl["pct"], 67)
        self.assertEqual(wl["level"], "中高水位")

    def test_threshold_note(self):
        """判定阈值: 纯判定文本(无重复前缀), 由前端统一加"判定阈值:"标签"""
        h = make_history({d: make_many("S", 3) for d in DATES})
        out = wr.week_tone(h)
        self.assertIn("日均高分", out["threshold_note"])
        # 回归: threshold_note 不含"阈值"字样 (前缀由前端统一加, 旧版会重复3次)
        self.assertEqual(out["threshold_note"].count("阈值"), 0)
        self.assertNotIn("判定阈值", out["threshold_note"])


class TestWeekMainlinesV2(unittest.TestCase):
    def _base(self):
        """A/B/C 三板块全周在榜, 默认仅 RPS5=95 (无 RPS10/20 → 非共振)"""
        h = make_history({d: ["A", "B", "C"] for d in DATES})
        for nm in ("A", "B", "C"):
            for d in DATES:
                h[d][nm] = {"RPS5": 95.0, "rank5": 1}
        return h

    def test_strength_hard_vs_short(self):
        """A: 三周期共振→硬核; B: 仅RPS5→短期; 硬核排序在前"""
        h = self._base()
        for d in DATES:
            h[d]["A"] = {"RPS5": 95.0, "RPS10": 92.0, "RPS20": 90.0, "rank5": 1}
        out = wr.week_mainlines(h)
        self.assertEqual(out[0]["name"], "A")
        self.assertEqual(out[0]["level"], "hard")
        self.assertEqual(out[0]["strength"], "硬核主线")
        self.assertEqual(out[1]["level"], "short")
        self.assertIn("ratio", out[0])

    def test_decay_level(self):
        """C: 前3天在榜, 后2天 RPS5 跌破60 → 动量衰减, 排最后"""
        h = self._base()
        for d in DATES:
            h[d]["A"] = {"RPS5": 95.0, "RPS10": 92.0, "RPS20": 90.0, "rank5": 1}
        for d in DATES[:3]:
            h[d]["C"] = {"RPS5": 95.0, "rank5": 3}
        for d in DATES[3:]:
            h[d]["C"] = {"RPS5": 45.0, "rank5": 3}
        out = wr.week_mainlines(h)
        self.assertEqual(out[-1]["name"], "C")
        self.assertEqual(out[-1]["level"], "decay")
        self.assertEqual(out[-1]["strength"], "动量衰减")


class TestWeekRotationV2(unittest.TestCase):
    def test_risk_levels(self):
        h = make_history({
            "20260817": ["A", "B", "C", "D"],
            "20260818": ["A", "B", "C", "D"],
            "20260819": ["A", "B", "C"],
            "20260820": ["A", "B"],
            "20260821": ["A"],
        })
        # A 三周期共振(补 RPS10/20)
        for d in DATES:
            if "A" in h[d]:
                h[d]["A"].update({"RPS10": 95.0, "RPS20": 95.0})
        out = wr.week_rotation(h)
        # A 全周5天在榜 → 持续; B=4天/C=3天/D=2天 依次衰退: 高/中/低风险
        self.assertEqual([x["name"] for x in out["sustained"]], ["A"])
        self.assertTrue(out["sustained"][0]["resonance"])
        by_name = {x["name"]: x for x in out["dropped"]}
        self.assertEqual(by_name["B"]["risk_level"], "高风险")
        self.assertEqual(by_name["C"]["risk_level"], "中风险")
        self.assertEqual(by_name["D"]["risk_level"], "低风险")
        self.assertTrue(out["summary"])

    def test_new_in_repeat(self):
        h = make_history({
            "20260817": ["B"],
            "20260818": [],
            "20260819": ["B"],
            "20260820": [],
            "20260821": ["B"],
        })
        out = wr.week_rotation(h)
        self.assertEqual([x["name"] for x in out["new_in"]], ["B"])


class TestWeekSentimentV2(unittest.TestCase):
    def test_milestones_and_summary(self):
        sent = [{"date": "20260817", "phase": "冰点期", "high_n": 3},
                {"date": "20260818", "phase": "启动期", "high_n": 6},
                {"date": "20260819", "phase": "发酵期", "high_n": 9},
                {"date": "20260820", "phase": "高潮期", "high_n": 12},
                {"date": "20260821", "phase": "退潮期", "high_n": 8}]
        out = wr.week_sentiment(sent, DATES)
        ms = out["milestones"]
        self.assertEqual(ms["start_date"], "20260818")
        self.assertEqual(ms["peak_date"], "20260820")
        self.assertEqual(ms["tideout_date"], "20260821")
        self.assertTrue(out["summary"])
        # 逐日判定规则注释
        self.assertEqual(out["series"][0]["phase_rule"], "高分≤3且连续回落")
        self.assertIn("拐点", str(out["milestones"]))


class TestWeekNewsV2(unittest.TestCase):
    def test_v2_keys(self):
        recs = {"stocks": [{"name": "兴业银锡", "ts_code": "000426",
                            "news": {"official_pos": 1, "official_neg": 0,
                                     "rumor_pos": 0, "rumor_neg": 0,
                                     "headlines": [{"title": "业绩预增"}]},
                            "tags": "利好消息", "score_news": 2}]}
        with mock.patch.object(dr, "load_recommendations", return_value={}), \
             mock.patch.object(dr, "load_early_recommendations", return_value={}):
            out = wr.week_news(recs, None)
        self.assertIn("score_basis", out)
        self.assertIn("range_note", out)
        wf = out["weekly_fund"]
        self.assertIn("main_week_net_yi", wf)
        self.assertIn("north_ratio", wf)


class TestWeekMarketBreadth(unittest.TestCase):
    def test_aggregate(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            dc = os.path.join(tmp.name, "daily_cache")
            os.makedirs(dc)
            for d in DATES:
                rows = [{"ts_code": "c%d" % i, "pct_chg": 1.5 if i < 4 else -1.0,
                         "amount": 1e10} for i in range(6)]
                with open(os.path.join(dc, d + ".json"), "w", encoding="utf-8") as f:
                    json.dump(rows, f)
            with mock.patch.object(wr, "STATIC_DIR", Path(tmp.name)), \
                 mock.patch.object(dr, "load_recommendations",
                                   return_value={"market_status": {"limit_up_count": 60,
                                                                   "limit_down_count": 8}}), \
                 mock.patch.object(dr, "fetch_market_breadth",
                                   return_value={"max_board": 6}):
                out = wr.week_market_breadth(dates=DATES)
            self.assertEqual(out["up_ratio"], round(4 / 6, 3))  # 每天4涨2跌
            self.assertIsNotNone(out["week_amount_yi"])
            self.assertEqual(out["avg_limit_up"], 60)
            self.assertEqual(out["max_board"], 6)
        finally:
            tmp.cleanup()


class TestWeeklyCoreConclusion(unittest.TestCase):
    def test_text(self):
        tone = {"note": "周均高分板块10个(首日8→末日12, +4), 周内热度走高"}
        mainlines = [{"name": "黄金", "level": "hard", "style": "周期"},
                     {"name": "疫苗", "level": "hard", "style": "成长"}]
        sentiment = {"end_phase": "震荡期", "summary": "本周情绪整体处于震荡期"}
        watch = {"focus": [{"name": "贵金属"}], "avoid": [{"name": "航运"}]}
        out = wr.weekly_core_conclusion(tone, mainlines, {}, sentiment, watch)
        self.assertIn("震荡期", out["text"])
        self.assertIn("黄金", out["text"])
        self.assertIn("疫苗", out["text"])
        self.assertIn("市场热度小幅抬升", out["text"])
        self.assertIn("下周优先观察贵金属", out["text"])
        self.assertIn("谨慎追高", out["text"])


class TestWeekPositionGuide(unittest.TestCase):
    def test_phase_map(self):
        for ph, total in (("高潮期", 8), ("发酵期", 6), ("震荡期", 5),
                          ("启动期", 4), ("退潮期", 3), ("冰点期", 2)):
            out = wr.week_position_guide({"end_phase": ph})
            self.assertEqual(out["total"], total, ph)
            self.assertTrue(out["direction"])
        # 缺省 → 震荡期
        self.assertEqual(wr.week_position_guide({})["total"], 5)


class TestLastWeekCheck(unittest.TestCase):
    def test_closure(self):
        prev = {"week_id": "2026W33", "update_date": "20260814",
                "watch": {"focus": [{"name": "A", "reason": "高质量种子"},
                                    {"name": "D", "reason": "分歧观察"}],
                          "observe": [{"name": "B", "reason": "临界晋级"}]}}
        history = {d: {"A": {"RPS5": 95}} for d in DATES}
        with mock.patch.object(wr, "_load_json", return_value=prev):
            out = wr.last_week_check(
                mainlines=[{"name": "A", "level": "hard"}],
                rotation={"dropped": [{"name": "D", "risk_level": "高风险"}]},
                history=history)
        self.assertTrue(out["has_data"])
        self.assertEqual(out["prev_week_id"], "2026W33")
        by_name = {it["name"]: it["verdict"] for it in out["items"]}
        self.assertEqual(by_name["A"], "晋级")
        self.assertEqual(by_name["D"], "衰退")
        self.assertEqual(by_name["B"], "震荡")
        self.assertEqual(out["hit_rate"], round(1 / 3 * 100, 1))
        self.assertTrue(out["summary"])


class TestWeeklyAbnormalRisks(unittest.TestCase):
    def test_group_tideout(self):
        rot = {"dropped": [{"name": "A", "risk_level": "高风险"},
                           {"name": "B", "risk_level": "高风险"},
                           {"name": "C", "risk_level": "高风险"}]}
        out = wr.weekly_abnormal_risks(rotation=rot)
        titles = [s["title"] for s in out["signals"]]
        self.assertIn("集体退潮", titles)
        self.assertEqual(out["signals"][0]["level"], "high")

    def test_volume_price_divergence(self):
        tone = {"diff": -1}
        breadth = {"amount_chg": 25.0}
        out = wr.weekly_abnormal_risks(tone=tone, breadth=breadth)
        self.assertEqual(out["signals"][0]["title"], "量价背离")

    def test_stagnation(self):
        mainlines = [{"name": "黄金", "level": "hard", "chg": 0.5}]
        out = wr.weekly_abnormal_risks(mainlines=mainlines)
        self.assertEqual(out["signals"][0]["title"], "高位滞涨")

    def test_no_risk_hidden(self):
        out = wr.weekly_abnormal_risks(rotation={"dropped": []}, mainlines=[])
        self.assertEqual(out["signals"], [])


class TestWeekCoreTargets(unittest.TestCase):
    def test_match(self):
        mainlines = [{"name": "贵金属", "level": "hard"}]
        recs = {"stocks": [{"name": "山东黄金", "ts_code": "600547.SH", "sectors": ["贵金属"]},
                           {"name": "赤峰黄金", "ts_code": "600988.SH", "sectors": ["贵金属"]},
                           {"name": "白酒", "ts_code": "000001.SZ", "sectors": ["白酒"]}]}
        out = wr.week_core_targets(mainlines, recs=recs)
        self.assertEqual(len(out["targets"]), 1)
        self.assertEqual(out["targets"][0]["sector"], "贵金属")
        self.assertEqual([s["name"] for s in out["targets"][0]["stocks"]],
                         ["山东黄金", "赤峰黄金"])  # 每板块最多2只

    def test_no_match(self):
        out = wr.week_core_targets([{"name": "X", "level": "hard"}], recs={"stocks": []})
        self.assertEqual(out["targets"], [])
        self.assertTrue(out["note"])


class TestWeekRecAccuracyV2(unittest.TestCase):
    def _review_data(self):
        return {
            "update_date": "20260821", "horizon": 1,
            "summary": {"samples": 10, "hit_rate": 0.3, "avg_gain": -1.0,
                        "avg_up_prob": 65.0, "prev_dates": ["20260817", "20260818"]},
            "by_confidence": {},
            "all": [
                {"pred_date": "20260817", "code": "600001.SH", "name": "A股",
                 "up_prob": 60, "confidence": "高", "sector_count": 1,
                 "gain": 1.0, "hit": True},
                {"pred_date": "20260817", "code": "600002.SH", "name": "B股",
                 "up_prob": 55, "confidence": "中", "sector_count": 1,
                 "gain": -2.0, "hit": False},
                {"pred_date": "20260818", "code": "600460.SH", "name": "士兰微",
                 "up_prob": 59, "confidence": "高", "sector_count": 2,
                 "gain": 2.9, "hit": True},
                {"pred_date": "20260819", "code": "300980.SZ", "name": "祥源新材",
                 "up_prob": 62, "confidence": "中", "sector_count": 1,
                 "gain": -7.74, "hit": False},
            ],
            "note": "测试",
        }

    def test_v2_horizon_and_benchmark(self):
        rev = self._review_data()
        history = {d: {"A": {"RPS5": 90}} for d in DATES}
        recs = {"stocks": [{"name": "士兰微", "ts_code": "600460.SH", "sector": "半导体"}]}
        with mock.patch.object(wr, "_load_json", return_value=rev), \
             mock.patch.object(dr, "load_recommendations", return_value=recs):
            out = wr.week_rec_accuracy(history)
        self.assertTrue(out["has_data"])
        # 无 daily_cache / index_cache 数据 → 多周期与基准为空但不阻断
        self.assertIn("avg_gain3", out)
        self.assertIn("avg_gain5", out)
        bench = out["benchmark"]
        self.assertIn("bench_name", bench)
        self.assertIn("excess", bench)
        # 最佳/最差案例补板块
        best_sectors = {x["name"]: x.get("sector") for x in out["best"]}
        self.assertEqual(best_sectors.get("士兰微"), "半导体")


if __name__ == "__main__":
    unittest.main()
