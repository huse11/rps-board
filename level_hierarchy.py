# -*- coding: utf-8 -*-
"""
RPS 看板四级下钻层级数据派生
输入(静态文件, 不依赖网络):
  - rps_history.json    : {date: {industry: {chg5,chg10,chg20,RPS5,..,rank5,..}}}
  - sw_industry_cache.json: board_parent(三级细分→申万一级), board_stocks(板块→成分股), stock_name
  - rps_data.json       : update_date + 各板块 stocks(成分股+当日行情, 含 rps50 若有)
输出: static/level_hierarchy.json
  {
    schema_version: 1, update_date,
    level1: [{name, RPS5/10/20, rank5/10/20, rank*_change, continuous*, stock_count, level2_count}],
    level2: [{name, parent, RPS5/10/20(组内), rank5/10/20, rank*_change, continuous*, status, stock_count}],
    stocks: {level2_name: [{ts_code,name,price,pct_chg,turnover,vol_ratio,rps50,industry}]}
  }
计算口径(与 rps_calc 引擎一致, 欧奈尔标准):
  - 一级 RPS: 31 个申万一级中, 按各自聚合 N 日涨跌幅(其下二级板块有效值等权平均)做百分位排名
  - 二级 RPS: 在其所属一级板块的全部二级板块中, 按 N 日涨跌幅做百分位排名(组内, 非全市场)
  - rank_change = 前日rank - 今日rank (正=排名上升)
  - continuous = RPS>=95 连续交易日数
"""
import json
import os
from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"
LEVEL_FILE = STATIC_DIR / "level_hierarchy.json"
RPS_HISTORY_FILE = STATIC_DIR / "rps_history.json"
RPS_DATA_FILE = STATIC_DIR / "rps_data.json"
SW_CACHE_FILE = STATIC_DIR / "sw_industry_cache.json"
SCHEMA_VERSION = 2
RPS_THRESHOLD = 95

# (chg列, RPS列, rank列, continuous列)
PERIODS = [
    ("chg5", "RPS5", "rank5", "continuous5"),
    ("chg10", "RPS10", "rank10", "continuous10"),
    ("chg20", "RPS20", "rank20", "continuous20"),
]


def _avg(values):
    """等权平均(忽略 None/NaN)"""
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _round(v, nd=1):
    return round(float(v), nd) if v is not None else None


def _to_int(v):
    return int(v) if v is not None else None


def build_level1_daily(rps_history, board_parent):
    """逐日派生一级板块聚合 chg + 一级 rank/RPS (纯函数)
    返回 {date: {level1_name: {chg5,chg10,chg20, rank5,rank10,rank20, RPS5,RPS10,RPS20}}}
    一级 chg = 其下二级板块(有 parent 归属) chg 有效值等权平均
    """
    l1_names = sorted({p for p in board_parent.values() if p})
    result = {}
    for date, day in rps_history.items():
        groups = {l1: {"chg5": [], "chg10": [], "chg20": []} for l1 in l1_names}
        for ind, rec in day.items():
            p = board_parent.get(ind, "")
            if not p or p not in groups:
                continue
            for chg_col in ("chg5", "chg10", "chg20"):
                v = rec.get(chg_col)
                if v is not None:
                    groups[p][chg_col].append(v)
        l1_day = {}
        for l1, g in groups.items():
            l1_day[l1] = {c: _avg(vals) for c, vals in g.items()}
        # 同层级百分位排名(欧奈尔: RPS=(1-rank/total)*100, 涨幅最高→rank1)
        total = len(l1_names)
        for chg_col, rps_col, rank_col, _cont in PERIODS:
            valid = [(l1, l1_day[l1].get(chg_col)) for l1 in l1_names]
            valid = [x for x in valid if x[1] is not None]
            valid.sort(key=lambda x: x[1], reverse=True)
            for i, (l1, _v) in enumerate(valid, start=1):
                l1_day[l1][rank_col] = i
                l1_day[l1][rps_col] = (1 - i / total) * 100
            for l1 in l1_names:
                if rank_col not in l1_day[l1]:
                    l1_day[l1][rank_col] = None
                    l1_day[l1][rps_col] = None
        result[date] = l1_day
    return result


def build_level2_daily(rps_history, board_parent):
    """逐日派生二级板块组内 rank/RPS (纯函数)
    返回 {date: {level2_name: {rank5,rank10,rank20, RPS5,RPS10,RPS20}}}
    组内: 该二级所属一级板块下的全部二级板块中排名 (非全市场)
    """
    result = {}
    for date, day in rps_history.items():
        groups = {}  # l1 -> {ind: rec}
        for ind, rec in day.items():
            p = board_parent.get(ind, "")
            if not p:
                continue
            groups.setdefault(p, {})[ind] = rec
        day_res = {}
        for l1, members in groups.items():
            names = list(members.keys())
            total = len(names)
            for chg_col, rps_col, rank_col, _cont in PERIODS:
                valid = [(n, members[n].get(chg_col)) for n in names]
                valid = [x for x in valid if x[1] is not None]
                valid.sort(key=lambda x: x[1], reverse=True)
                for i, (n, _v) in enumerate(valid, start=1):
                    day_res.setdefault(n, {})[rank_col] = i
                    day_res[n][rps_col] = (1 - i / total) * 100
                for n in names:
                    if rank_col not in day_res.get(n, {}):
                        day_res.setdefault(n, {})[rank_col] = None
                        day_res[n][rps_col] = None
        result[date] = day_res
    return result


def _consecutive(daily, dates, name, rps_col, today, threshold=RPS_THRESHOLD):
    """从 today 往前数连续 RPS>=threshold 的交易日数 (与 rps_calc.calc_consecutive 同口径)"""
    if today not in daily:
        return 0
    cur = daily[today].get(name, {}).get(rps_col)
    if cur is None or cur < threshold:
        return 0
    n = 0
    for d in reversed(dates):
        if d > today:
            continue
        rec = daily[d].get(name, {}).get(rps_col)
        if rec is not None and rec >= threshold:
            n += 1
        else:
            break
    return n


def build_level1_board(l1_daily, dates, latest_date, prev_date, level1_meta):
    """一级板块三档入选/调出列表 (纯函数, 供三档板块榜)
    l1_daily    : build_level1_daily 输出 {date: {l1: {chg*, rank*, RPS*}}}
    level1_meta : {l1: {"days_60": int, "stock_count": int, "rps50_above_90": int, "level2_count": int}}
    口径与 rps_calc.split_in_out 一致 (阈值 RPS_THRESHOLD):
      入选 = 当日 RPS>=95; 调出 = 前日入选且当日掉出; 调入 = 前日未入选
    返回 {"threshold": 95, "rps5": {"in_list","out_list","current_names"}, rps10..., rps20...}
    """
    today = l1_daily.get(latest_date, {})
    prev = l1_daily.get(prev_date, {}) if prev_date else {}
    board = {"threshold": RPS_THRESHOLD}
    for _chg_col, rps_col, rank_col, _cont_col in PERIODS:
        now_in = {n for n, r in today.items() if r.get(rps_col) is not None and r[rps_col] >= RPS_THRESHOLD}
        last_in = {n for n, r in prev.items() if r.get(rps_col) is not None and r[rps_col] >= RPS_THRESHOLD}
        out_names = last_in - now_in

        def make_rec(name, status_val):
            t = today.get(name, {})
            p = prev.get(name, {})
            pc = p.get(rank_col)
            tc = t.get(rank_col)
            meta = level1_meta.get(name, {})
            return {
                "name": name,
                "status": status_val,
                rps_col: _round(t.get(rps_col)),
                "rank": _to_int(tc),
                "rank_change": (pc - tc) if (pc is not None and tc is not None) else 0,
                "continuous": _consecutive(l1_daily, dates, name, rps_col, latest_date),
                "days_60": int(meta.get("days_60") or 0),
                "stock_count": int(meta.get("stock_count") or 0),
                "rps50_above_90": int(meta.get("rps50_above_90") or 0),
                "level2_count": int(meta.get("level2_count") or 0),
            }

        in_records = [make_rec(n, "调入" if n not in last_in else "在榜") for n in now_in]
        in_records.sort(key=lambda r: r[rps_col] if r[rps_col] is not None else -1, reverse=True)
        out_records = [make_rec(n, "调出") for n in out_names]
        out_records.sort(key=lambda r: r[rps_col] if r[rps_col] is not None else -1, reverse=True)
        board[rps_col.lower()] = {
            "in_list": in_records,
            "out_list": out_records,
            "current_names": sorted(now_in),
        }
    return board


def collect_stocks(rps_data, board_stocks, stock_name):
    """{level2_name: [成分股记录]}
    优先 rps_data.json 各板块 stocks(含当日行情+rps50); 不在榜板块用 board_stocks 全成分补齐(行情字段 None, 前端显示 '--')
    """
    result = {}
    if rps_data:
        for key in ("rps5", "rps10", "rps20"):
            for lst in (rps_data.get(key, {}).get("in_list", []), rps_data.get(key, {}).get("out_list", [])):
                for rec in lst:
                    stocks = rec.get("stocks")
                    if stocks:
                        result.setdefault(rec.get("name", ""), stocks)
    for bname, codes in (board_stocks or {}).items():
        if bname in result:
            continue
        result[bname] = [
            {
                "ts_code": c,
                "name": (stock_name or {}).get(c, c),
                "industry": bname,
                "price": None, "pct_chg": None, "turnover": None,
                "vol_ratio": None, "rps50": None,
            }
            for c in codes
        ]
    return result


def build_hierarchy(rps_history, board_parent, board_stocks, stock_name, rps_data, latest_date=None):
    """组装 level_hierarchy dict (纯函数, 供单测)"""
    l1_daily = build_level1_daily(rps_history, board_parent)
    l2_daily = build_level2_daily(rps_history, board_parent)
    dates = sorted(rps_history.keys())
    if not dates:
        return None
    latest_date = latest_date or dates[-1]
    idx = dates.index(latest_date) if latest_date in dates else len(dates) - 1
    prev_date = dates[idx - 1] if idx > 0 else None

    today_l1 = l1_daily.get(latest_date, {})
    prev_l1 = l1_daily.get(prev_date, {}) if prev_date else {}
    today_l2 = l2_daily.get(latest_date, {})
    prev_l2 = l2_daily.get(prev_date, {}) if prev_date else {}

    l1_names = sorted({p for p in board_parent.values() if p})
    # 一级 → 其下二级板块名列表
    l1_members = {}
    for ind, p in board_parent.items():
        if p:
            l1_members.setdefault(p, []).append(ind)

    stocks_map = collect_stocks(rps_data, board_stocks, stock_name)

    # ===== 一级板块列表 (按 RPS5 降序) =====
    level1 = []
    for l1 in l1_names:
        t = today_l1.get(l1, {})
        p = prev_l1.get(l1, {})
        members = l1_members.get(l1, [])
        stock_count = 0
        for m in members:
            stock_count += len(stocks_map.get(m, []))
        rec = {"name": l1, "stock_count": stock_count, "level2_count": len(members), "status": ""}
        for chg_col, rps_col, rank_col, cont_col in PERIODS:
            rec[rps_col] = _round(t.get(rps_col))
            rec[rank_col] = _to_int(t.get(rank_col))
            pc = p.get(rank_col)
            tc = t.get(rank_col)
            rec[rank_col + "_change"] = (pc - tc) if (pc is not None and tc is not None) else None
            rec[cont_col] = _consecutive(l1_daily, dates, l1, rps_col, latest_date)
        level1.append(rec)
    level1.sort(key=lambda r: r.get("RPS5") if r.get("RPS5") is not None else -1, reverse=True)
    for i, rec in enumerate(level1, start=1):
        rec["display_rank"] = i

    # ===== 二级板块列表 (组内 RPS; 按一级分组后组内排序) =====
    level2 = []
    for l1 in l1_names:
        members = l1_members.get(l1, [])
        tmp = []
        for ind in members:
            t = today_l2.get(ind, {})
            p = prev_l2.get(ind, {})
            rec = {"name": ind, "parent": l1, "stock_count": len(stocks_map.get(ind, []))}
            for chg_col, rps_col, rank_col, cont_col in PERIODS:
                rec[rps_col] = _round(t.get(rps_col))
                rec[rank_col] = _to_int(t.get(rank_col))
                pc = p.get(rank_col)
                tc = t.get(rank_col)
                rec[rank_col + "_change"] = (pc - tc) if (pc is not None and tc is not None) else None
                rec[cont_col] = _consecutive(l2_daily, dates, ind, rps_col, latest_date)
            tmp.append(rec)
        tmp.sort(key=lambda r: r.get("RPS5") if r.get("RPS5") is not None else -1, reverse=True)
        for i, rec in enumerate(tmp, start=1):
            rec["display_rank"] = i
        level2.extend(tmp)

    # 二级 status: 结合 rps_data 在榜/调出标记 (组内 RPS>=阈值但无记录时按连续天数推断)
    status_map = {}
    if rps_data:
        for key in ("rps5", "rps10", "rps20"):
            for rec in rps_data.get(key, {}).get("in_list", []):
                status_map.setdefault(rec.get("name", ""), "在榜")
            for rec in rps_data.get(key, {}).get("out_list", []):
                status_map.setdefault(rec.get("name", ""), "调出")
    for rec in level2:
        st = status_map.get(rec["name"], "")
        if not st:
            st = "调入" if rec["continuous5"] == 1 else ("在榜" if rec["continuous5"] >= 2 else "")
        rec["status"] = st

    # 一级 status: 聚合其下二级状态 (调入 > 在榜 > 调出)
    pri = {"调入": 3, "在榜": 2, "调出": 1}
    l1_status = {}
    for rec in level2:
        st = rec["status"]
        if not st or st not in pri:
            continue
        l1 = rec["parent"]
        if pri.get(l1_status.get(l1, ""), 0) < pri[st]:
            l1_status[l1] = st
    for rec in level1:
        rec["status"] = l1_status.get(rec["name"], "")

    # ===== 一级 meta: days_60/stock_count/rps50_above_90 = 聚合其下二级板块(rps_data 在榜/调出记录, 按板块去重) =====
    l1_meta = {l1: {"days_60": 0, "stock_count": 0, "rps50_above_90": 0,
                    "level2_count": len(l1_members.get(l1, []))} for l1 in l1_names}
    if rps_data:
        seen = set()
        for key in ("rps5", "rps10", "rps20"):
            for lst in (rps_data.get(key, {}).get("in_list", []), rps_data.get(key, {}).get("out_list", [])):
                for rec in lst:
                    nm = rec.get("name", "")
                    if not nm or nm in seen:
                        continue
                    seen.add(nm)
                    p = board_parent.get(nm, "")
                    if p not in l1_meta:
                        continue
                    m = l1_meta[p]
                    m["stock_count"] += int(rec.get("stock_count") or 0)
                    m["rps50_above_90"] += int(rec.get("rps50_above_90") or 0)
                    m["days_60"] = max(m["days_60"], int(rec.get("days_60") or 0))

    # ===== 三档板块榜(一级板块): RPS5/10/20 入选/调出 =====
    board = build_level1_board(l1_daily, dates, latest_date, prev_date, l1_meta)

    return {
        "schema_version": SCHEMA_VERSION,
        "update_date": latest_date,
        "level1_count": len(level1),
        "level2_count": len(level2),
        "level1": level1,
        "level2": level2,
        "stocks": stocks_map,
        "board": board,
    }


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def build_and_save(rps_history=None, board_parent=None, board_stocks=None, stock_name=None,
                   rps_data=None, latest_date=None):
    """生成 level_hierarchy.json (幂等: 已是最新交易日则跳过)
    无参调用时读取静态文件 (rps_calc 成功路径 + skip 路径统一入口)
    """
    if rps_history is None:
        rps_history = _load_json(RPS_HISTORY_FILE)
    if board_parent is None:
        sw = _load_json(SW_CACHE_FILE) or {}
        board_parent = sw.get("board_parent") or {}
        board_stocks = sw.get("board_stocks") or {}
        stock_name = sw.get("stock_name") or {}
    if rps_data is None:
        rps_data = _load_json(RPS_DATA_FILE)
    if not rps_history or not board_parent:
        return False

    latest_date = latest_date or (rps_data or {}).get("update_date") or sorted(rps_history.keys())[-1]

    # 幂等: 当日已生成则跳过 (与 daily_review 同日跳过口径一致)
    exist = _load_json(LEVEL_FILE)
    if exist and str(exist.get("update_date", "")) == str(latest_date):
        return True

    data = build_hierarchy(rps_history, board_parent, board_stocks, stock_name, rps_data, latest_date)
    if not data:
        return False
    os.makedirs(STATIC_DIR, exist_ok=True)
    with open(LEVEL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"  ✅ 层级下钻数据: {data['level1_count']}个一级板块, {data['level2_count']}个二级板块 -> {LEVEL_FILE.name}")
    return True


if __name__ == "__main__":
    ok = build_and_save()
    print("生成成功" if ok else "生成失败/跳过")
