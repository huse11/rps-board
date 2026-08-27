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
        self.assertEqual(wr.week_watch(None), {})
        self.assertEqual(wr.week_watch({}), {"critical": [], "diverge_watch": [],
                                             "seed_high": [], "potential": []})


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


if __name__ == "__main__":
    unittest.main()
