# -*- coding: utf-8 -*-
"""rps_calc.py 核心计算回归测试 (2026-08-18 新增, 为性能重构锁行为)

覆盖: calc_full_rps_history / calc_consecutive / count_above_threshold /
      calc_industry_rps50_60day_count 的输出结构与排序正确性
"""
import unittest
from datetime import datetime, timedelta
from unittest import mock

import pandas as pd

import rps_calc as rc


def make_daily_map(pct_by_code, latest_date="20260817", n_days=25):
    """构造 mock daily_map: {date: DataFrame(ts_code, pct_chg)}
    pct_by_code: {code: 固定每日涨跌幅} 简化构造: 每只股票每日涨幅恒定
    """
    base = datetime.strptime(latest_date, "%Y%m%d")
    dates = sorted((base - timedelta(days=i)).strftime("%Y%m%d") for i in range(n_days))
    dm = {}
    for date in dates:
        rows = [{"ts_code": code, "pct_chg": pct, "close": 10.0}
                for code, pct in pct_by_code.items()]
        dm[date] = pd.DataFrame(rows)
    return dm


def make_industry_df(code_industry):
    return pd.DataFrame([{"ts_code": c, "industry": ind} for c, ind in code_industry.items()])


class TestCalcFullRpsHistory(unittest.TestCase):
    """全量RPS历史: 涨幅排序 → RPS=(1-排名/总数)*100, 排名变化/连续/60日计数"""

    def setUp(self):
        # 24 个行业, 每日涨幅恒定且互不相同, 保证排名稳定可预测
        # 阈值95: 第1名 RPS=(1-1/24)*100≈95.83 ≥95 达标 (2026-08-25 阈值90→95 后由12扩到24)
        pcts = [2.0 - 0.2 * i for i in range(24)]
        self.inds = ["IND%02d" % i for i in range(24)]
        self.pct_by_code = {}
        self.code_industry = {}
        for ind, pct in zip(self.inds, pcts):
            for k in ("A", "B"):
                code = "%s%s" % (ind, k)
                self.pct_by_code[code] = pct
                self.code_industry[code] = ind
        self.trade_days = sorted((datetime(2026, 8, 17) - timedelta(days=i)).strftime("%Y%m%d")
                                 for i in range(25))
        self.dm = make_daily_map(self.pct_by_code, latest_date=self.trade_days[-1], n_days=25)
        self.ind_df = make_industry_df(self.code_industry)

    def test_output_structure(self):
        df, history = rc.calc_full_rps_history(self.dm, self.ind_df, self.trade_days)
        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 24)
        for col in ("RPS5", "RPS10", "RPS20", "rank5", "consecutive5", "days90_5"):
            self.assertIn(col, df.columns)
        # 历史快照覆盖 第20天起 的每个交易日
        avail = [d for d in self.trade_days if d in self.dm]
        self.assertEqual(sorted(history.keys()), avail[19:])
        # 所有 RPS 数值有限 (无 NaN)
        for col in ("RPS5", "RPS10", "RPS20"):
            self.assertTrue(df[col].notna().all(), col)

    def test_rps_ranking_order(self):
        df, _ = rc.calc_full_rps_history(self.dm, self.ind_df, self.trade_days)
        # 涨幅最高行业 RPS5 最大, 涨幅最低行业 RPS5 最小
        top = df.loc[df["RPS5"].idxmax(), "industry"]
        bot = df.loc[df["RPS5"].idxmin(), "industry"]
        self.assertEqual(top, "IND00")
        self.assertEqual(bot, "IND23")
        # 欧奈尔标准公式: 第1名 = (1-1/24)*100 ≈ 95.83 (≥95 阈值)
        self.assertAlmostEqual(df.loc[df["industry"] == "IND00", "RPS5"].iloc[0], 100 * (1 - 1 / 24), places=1)

    def test_consecutive_and_days90(self):
        df, history = rc.calc_full_rps_history(self.dm, self.ind_df, self.trade_days)
        row = df[df["industry"] == "IND00"].iloc[0]
        # IND00 每日涨幅最高 → 每个历史日 RPS5 均 ≥90 → 连续天数 = 历史天数
        self.assertEqual(row["consecutive5"], len(history))
        self.assertEqual(row["days90_5"], len(history))
        # 涨幅最低行业从未达标
        row_bot = df[df["industry"] == "IND11"].iloc[0]
        self.assertEqual(row_bot["consecutive5"], 0)

    def test_rank_change(self):
        df, history = rc.calc_full_rps_history(self.dm, self.ind_df, self.trade_days)
        # 稳定排序 → 排名无变化
        row = df[df["industry"] == "IND00"].iloc[0]
        self.assertEqual(row["rank_chg5"], 0)

    def test_calc_consecutive_helpers(self):
        hist = {"20260801": {"A": {"RPS5": 95.0}}, "20260802": {"A": {"RPS5": 96.0}},
                "20260803": {"A": {"RPS5": 88.0}}}
        # 从今天向前连续达标 (今天本身计入); 阈值95: 95/96达标, 88不达标
        self.assertEqual(rc.calc_consecutive(hist, self.trade_days, "A", "RPS5", "20260803"), 0)
        self.assertEqual(rc.calc_consecutive(hist, self.trade_days, "A", "RPS5", "20260802"), 2)
        self.assertEqual(rc.calc_consecutive(hist, self.trade_days, "A", "RPS5", "20260801"), 1)
        self.assertEqual(rc.count_above_threshold(hist, self.trade_days, "A", "RPS5", "20260803", 60), 2)


class TestMaybeRunWeeklyReview(unittest.TestCase):
    """每周复盘触发逻辑 (V2.0 需求文档: 每周五收盘后自动生成, 周日兜底): 周五(weekday=4)/周日(weekday=6)运行, 其余跳过, 失败不阻断"""

    @staticmethod
    def _fake_weekly(raise_err=False):
        import sys  # noqa: F401 (在 mock.patch.dict 中使用 sys.modules)
        class FakeWeekly:
            calls = 0

            @staticmethod
            def main(force=False):
                FakeWeekly.calls += 1
                if raise_err:
                    raise RuntimeError("weekly boom")
        return FakeWeekly

    def test_friday_runs(self):
        """周五收盘后自动生成 (V2.0 需求文档更新时机)"""
        import sys
        fake = self._fake_weekly()
        with mock.patch.object(rc, "datetime") as mdt, \
             mock.patch.dict(sys.modules, {"weekly_review": fake}):
            mdt.now.return_value.weekday.return_value = 4  # 周五
            rc._maybe_run_weekly_review()
        self.assertEqual(fake.calls, 1)

    def test_sunday_runs(self):
        import sys
        fake = self._fake_weekly()
        with mock.patch.object(rc, "datetime") as mdt, \
             mock.patch.dict(sys.modules, {"weekly_review": fake}):
            mdt.now.return_value.weekday.return_value = 6  # 周日
            rc._maybe_run_weekly_review()
        self.assertEqual(fake.calls, 1)

    def test_non_trigger_day_skip(self):
        import sys
        fake = self._fake_weekly()
        for wd in (0, 1, 2, 3, 5):  # 周一~周四 / 周六
            with mock.patch.object(rc, "datetime") as mdt, \
                 mock.patch.dict(sys.modules, {"weekly_review": fake}):
                mdt.now.return_value.weekday.return_value = wd
                rc._maybe_run_weekly_review()
        self.assertEqual(fake.calls, 0)

    def test_exception_non_blocking(self):
        import sys
        fake = self._fake_weekly(raise_err=True)
        with mock.patch.object(rc, "datetime") as mdt, \
             mock.patch.dict(sys.modules, {"weekly_review": fake}):
            mdt.now.return_value.weekday.return_value = 6
            rc._maybe_run_weekly_review()  # 异常应被捕获, 不抛出
        self.assertEqual(fake.calls, 1)


class TestAtomicWriteJson(unittest.TestCase):
    """atomic_write_json 原子落盘 (2026-08-30 新增): 写 tmp + os.replace, 半写文件防护"""

    def test_write_and_readback(self):
        """正常写入: 内容正确且不残留 .tmp"""
        import json as _json
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = rc.Path(td) / "a" / "b.json"  # 嵌套目录自动创建
            self.assertTrue(rc.atomic_write_json(p, {"x": 1}, indent=2))
            self.assertEqual(_json.loads(p.read_text(encoding="utf-8")), {"x": 1})
            self.assertFalse((rc.Path(str(p) + ".tmp")).exists())

    def test_overwrite(self):
        """覆盖写: 更新内容生效"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = rc.Path(td) / "d.json"
            rc.atomic_write_json(p, {"v": 1})
            rc.atomic_write_json(p, {"v": 2, "w": [1, 2]})
            import json as _json
            self.assertEqual(_json.loads(p.read_text(encoding="utf-8")), {"v": 2, "w": [1, 2]})

    def test_allow_nan_kwarg(self):
        """kwargs 透传: allow_nan=False 时 NaN 写入应抛 ValueError 且不留 .tmp"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = rc.Path(td) / "n.json"
            with self.assertRaises(ValueError):
                rc.atomic_write_json(p, {"v": float("nan")}, allow_nan=False)
            self.assertFalse(p.exists(), "失败时不落半写文件")
            self.assertFalse((rc.Path(str(p) + ".tmp")).exists(), "失败时清理 .tmp")

    def test_failure_cleans_tmp(self):
        """写入异常(模拟)时清理 .tmp 并抛错"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = rc.Path(td) / "f.json"
            with mock.patch("builtins.open", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    rc.atomic_write_json(p, {"x": 1})
            self.assertFalse(p.exists())
            self.assertFalse((rc.Path(str(p) + ".tmp")).exists())


if __name__ == "__main__":
    unittest.main()
