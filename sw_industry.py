# -*- coding: utf-8 -*-
"""
申万三级细分行业映射模块 —— RPS 看板板块池: 110 大行业 → 208 细分板块
背景: 原看板用 Tushare stock_basic.industry(约110个大行业), 颗粒度太粗,
      大行业内细分涨跌被平均, RPS 信号失真。切换为申万三级细分板块。

数据源 (AKShare, 免费):
  - sw_index_third_info():    申万三级行业列表(约335个, 含成份个数)
  - index_component_sw(code): 指定三级行业成分股(证券代码/证券名称/权重)
  - 剔除成份股<MIN_BOARD_COUNT(8)只的僵尸板块 → 约208个主流细分板块

缓存: static/sw_industry_cache.json
  {fetch_date, stock_industry{ts_code:行业名}, stock_name{ts_code:名称},
   board_stocks{行业名:[ts_code]}, board_count{行业名:成分数}}
  - 成分股名单为季度调整, 7天内复用, 超过7天重新拉取(force=True 强制)

降级: AKShare 拉取失败且无有效缓存 → 返回 None, 上层回退原 Tushare 逻辑
"""
import json
import os
from datetime import datetime
from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"
SW_CACHE_FILE = STATIC_DIR / "sw_industry_cache.json"
MIN_BOARD_COUNT = 8          # 成分股下限(剔除僵尸板块)
CACHE_MAX_DAYS = 7           # 成分名单刷新周期(天)

# 运行时按需 import akshare (服务器/本地均已安装)
_ak = None


def _get_ak():
    global _ak
    if _ak is None:
        import akshare
        _ak = akshare
    return _ak


def to_ts_code(code6):
    """6位证券代码 → ts_code; 北交所(4/8开头)申万无归属返回 None"""
    code6 = str(code6).strip().zfill(6)
    if code6.startswith(("4", "8", "920")):
        return None
    if code6.startswith("6"):
        return f"{code6}.SH"
    return f"{code6}.SZ"


def _fetch_sw_map():
    """拉取申万三级信息 + 各板块成分股, 构建映射(不落盘)"""
    ak = _get_ak()
    info = ak.sw_index_third_info()
    boards = info[info["成份个数"] >= MIN_BOARD_COUNT].reset_index(drop=True)
    if len(boards) == 0:
        return None
    stock_industry = {}   # ts_code -> 三级行业名
    stock_name = {}       # ts_code -> 证券名称
    board_stocks = {}     # 行业名 -> [ts_code]
    for _, row in boards.iterrows():
        code6 = str(row["行业代码"]).split(".")[0]
        name = str(row["行业名称"])
        cons = ak.index_component_sw(symbol=code6)
        members = []
        for _, c in cons.iterrows():
            ts = to_ts_code(c["证券代码"])
            if ts is None:
                continue
            members.append(ts)
            stock_industry[ts] = name
            stock_name[ts] = str(c["证券名称"])
        board_stocks[name] = members
    # 行业名必须唯一, 否则 merge 分组会错误合并
    names = list(board_stocks.keys())
    if len(names) != len(set(names)):
        dup = [n for n in set(names) if names.count(n) > 1]
        raise ValueError(f"申万三级行业名重复: {dup}")
    return {
        "fetch_date": datetime.now().strftime("%Y%m%d"),
        "stock_industry": stock_industry,
        "stock_name": stock_name,
        "board_stocks": board_stocks,
        "board_count": {n: len(v) for n, v in board_stocks.items()},
        "board_parent": _build_board_parent(ak, board_stocks),  # 一级板块归类
    }


def _build_board_parent(ak, boards):
    """三级行业名 → 一级行业名 映射（一级板块归类）
    方法: 拉取申万一级行业成分股(index_component_sw), 与三级板块成分股求交集,
          交集股票数最多的父级一级行业即为归类结果(如 医疗研发外包 → 医药生物)。
    任一一级行业拉取失败仅跳过该行业; 全部失败返回 {} (前端显示 '--')。
    """
    first_members = {}  # 一级行业名 -> set(ts_code)
    try:
        info = ak.sw_index_first_info()
    except Exception as e:
        print(f"  ⚠️ 申万一级行业列表拉取失败: {str(e)[:80]}")
        return {}
    for _, row in info.iterrows():
        code6 = str(row["行业代码"]).split(".")[0]
        name = str(row["行业名称"])
        try:
            cons = ak.index_component_sw(symbol=code6)
        except Exception:
            continue
        members = set()
        for _, c in cons.iterrows():
            ts = to_ts_code(c["证券代码"])
            if ts is not None:
                members.add(ts)
        first_members[name] = members
    if not first_members:
        return {}
    board_parent = {}
    for bname, bmembers in boards.items():
        bset = set(bmembers)
        best, best_cnt = "", 0
        for fname, fset in first_members.items():
            cnt = len(bset & fset)
            if cnt > best_cnt:
                best, best_cnt = fname, cnt
        board_parent[bname] = best
    return board_parent


def _ensure_board_parent(cache):
    """旧缓存缺 board_parent 字段时补全(仅需额外拉一级成分股, 不重拉三级);
    补全失败不影响原缓存返回"""
    if cache.get("board_parent"):
        return cache
    try:
        board_parent = _build_board_parent(_get_ak(), cache.get("board_stocks", {}))
        if board_parent:
            cache["board_parent"] = board_parent
            try:
                os.makedirs(STATIC_DIR, exist_ok=True)
                with open(SW_CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(cache, f, ensure_ascii=False)
                print(f"  ✅ 一级板块归类补全: {len(board_parent)}个三级板块映射父级")
            except Exception:
                pass
    except Exception as e:
        print(f"  ⚠️ 一级板块归类补全失败(前端显示'--'): {str(e)[:80]}")
    return cache


def build_sw_industry_map(force=False):
    """构建申万三级映射(带缓存)。成功返回 dict, 失败返回 None"""
    if not force and os.path.exists(SW_CACHE_FILE):
        try:
            with open(SW_CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
            fd = str(cache.get("fetch_date", ""))
            if len(fd) == 8 and (datetime.now() - datetime.strptime(fd, "%Y%m%d")).days <= CACHE_MAX_DAYS:
                return _ensure_board_parent(cache)
        except Exception:
            pass
    try:
        data = _fetch_sw_map()
        if data is None:
            return None
        os.makedirs(STATIC_DIR, exist_ok=True)
        with open(SW_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        print(f"  ✅ 申万三级板块池: {len(data['board_stocks'])}个细分板块, 覆盖{len(data['stock_industry'])}只股票"
              f", 一级板块归类{len(data.get('board_parent', {}))}个")
        return data
    except Exception as e:
        print(f"  ⚠️ 申万三级映射拉取失败: {str(e)[:80]}")
        # 旧缓存兜底(即使超7天, 也比无数据强)
        if os.path.exists(SW_CACHE_FILE):
            try:
                with open(SW_CACHE_FILE, encoding="utf-8") as f:
                    return _ensure_board_parent(json.load(f))
            except Exception:
                return None
        return None


def load_sw_industry_map():
    """仅读缓存(不拉网络), 无缓存返回 None"""
    if os.path.exists(SW_CACHE_FILE):
        try:
            with open(SW_CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def build_industry_df(sw_map, old_basic_df=None):
    """由申万映射构造 rps_calc 所需 industry_df:
    [ts_code, name, industry(三级行业名), list_date]
    - name/list_date 优先从旧 stock_basic 缓存取, 缺失用申万名称/置空
    """
    records = []
    old_map = {}
    if old_basic_df is not None and len(old_basic_df) > 0:
        old_map = {r["ts_code"]: r for _, r in old_basic_df.iterrows()}
    for ts, ind in sw_map["stock_industry"].items():
        old = old_map.get(ts)
        name = sw_map["stock_name"].get(ts, old["name"] if old is not None else ts.split(".")[0])
        list_date = old["list_date"] if old is not None and old.get("list_date") else ""
        records.append({"ts_code": ts, "name": name, "industry": ind, "list_date": str(list_date)})
    return records
