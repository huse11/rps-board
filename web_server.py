"""
板块 RPS 监测看板 - Web 服务器
启动后手机浏览器访问 http://你的IP:8000
"""
import time
from functools import wraps
import asyncio

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import uvicorn
import os
from pathlib import Path

import stock_detail

app = FastAPI(title="板块 RPS 监测看板")

# 静态文件目录
STATIC_DIR = Path(__file__).parent / "static"
os.makedirs(STATIC_DIR, exist_ok=True)

# 入口文件禁止缓存(手机 App WebView 缓存旧 render.js 曾导致 buildReviewHtml is not defined)
NO_CACHE_HEADERS = {"Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache", "Expires": "0"}


class NoCacheStaticFiles(StaticFiles):
    """静态 JSON 数据统一禁止缓存, 确保 App/浏览器每次拉到最新收盘数据"""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.endswith(".json"):
            response.headers.update(NO_CACHE_HEADERS)
        return response


app.mount("/static", NoCacheStaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def home():
    index_path = Path(__file__).parent / "index.html"
    return FileResponse(str(index_path), headers=NO_CACHE_HEADERS)


@app.get("/render.js")
async def render_js():
    # render.js 与 index.html 同级在项目根目录，单独路由返回
    js_path = Path(__file__).parent / "render.js"
    return FileResponse(str(js_path), media_type="text/javascript", headers=NO_CACHE_HEADERS)


# ============================================================
# 个股详情动态接口 (数据源: 东方财富/腾讯/AKShare, 见 stock_detail.py)
#
# 路由去重辅助:
#   _fetch_and_call(code, handler_name_or_fn, *, deps=(), wrap_fn=None, period="day", fqt="qfq")
#   并行 gather 指定的 deps 依赖(rt/kl/kl_full/ff), 再把结果关键字传给 stock_detail.<handler_name>
#     deps 简写:
#       rt     = fetch_realtime(code)
#       kl     = fetch_kline(code, "day")
#       kl_full= fetch_kline(code, period, fqt)   [仅用于 kline 带可选 period/fqt 的路由]
#       ff     = fetch_fund_flow(code)
#   wrap_fn(code, deps_map) -> Response: 用于 kline 那种要拼 indicators 的自定义包装
# ============================================================

def _ok(data):
    if data is None:
        return JSONResponse({"error": "数据获取失败"}, status_code=502)
    return JSONResponse(data, headers=NO_CACHE_HEADERS)


async def _fetch_and_call(code, handler, *, deps=(), wrap_fn=None, period="day", fqt="qfq"):
    """根据 deps 并行预取依赖, 调用 handler, 返回 _ok 结果或 wrap_fn 自定义结果.

    deps 简写 → 预取函数 → 注入 handler 的 keyword 参数名:
        "rt"  → fetch_realtime(code)       → realtime=
        "kl"  → fetch_kline(code, "day")   → kline=
        "kl_full" → fetch_kline(code, period, fqt) → kline_full_  (仅供 wrap_fn 使用, wrap_fn 负责解包映射)
        "ff"  → fetch_fund_flow(code)     → fflow=
    注意: wrap_fn 收到的 deps_map 仍然使用 deps 简写键(rt/kl/kl_full/ff), 这样 kline 自定义包装可区分 kl_full vs kl.
    """
    dep_set = set(deps)
    # 预取 task 的 key 用简写, 但注入 handler 时要转成 stock_detail 约定的参数名
    short_to_param = {"rt": "realtime", "kl": "kline", "ff": "fflow"}
    tasks = {}
    if "rt" in dep_set:
        tasks["rt"] = asyncio.to_thread(stock_detail.fetch_realtime, code)
    if "kl" in dep_set:
        tasks["kl"] = asyncio.to_thread(stock_detail.fetch_kline, code, "day")
    if "kl_full" in dep_set:
        tasks["kl_full"] = asyncio.to_thread(stock_detail.fetch_kline, code, period, fqt)
    if "ff" in dep_set:
        tasks["ff"] = asyncio.to_thread(stock_detail.fetch_fund_flow, code)
    deps_map_short = {}
    if tasks:
        keys = list(tasks.keys())
        vals = await asyncio.gather(*(tasks[k] for k in keys))
        deps_map_short = dict(zip(keys, vals))
    if wrap_fn is not None:
        return wrap_fn(code, deps_map_short)
    if handler is None:
        return _ok(None)
    fn = getattr(stock_detail, handler) if isinstance(handler, str) else handler
    # 简写 → 正式参数名
    handler_kwargs = {}
    for short, formal in short_to_param.items():
        if short in deps_map_short:
            handler_kwargs[formal] = deps_map_short[short]
    payload = fn(code, **handler_kwargs)
    return _ok(payload)


# ============================================================
# 详情接口结果级缓存 (解决 5-8 秒慢响应: 重复查看/切Tab 直接命中)
# 仅缓存成功响应(2xx); 最多保留 N 条防止内存无界增长
# ============================================================
_DETAIL_RESULT_MAX = 2000
_detail_result_cache = {}
_in_flight = {}  # 并发单飞: key -> asyncio.Task, 相同请求在途时复用, 避免重复穿透外部行情源


def _detail_cache(ttl=300, key_fn=None):
    """装饰详情接口: 按 (函数名:code[:params]) 缓存成功 JSONResponse + 并发单飞去重"""
    def deco(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            key = key_fn(*args, **kwargs) if key_fn else (
                fn.__name__ + ":" + str(kwargs.get("code") or (args[0] if args else "")).upper())
            now = time.time()
            hit = _detail_result_cache.get(key)
            if hit is not None and now - hit[0] < ttl:
                return hit[1]
            # 并发单飞: 相同 key 已在途 → 等待同一结果, 不重复发起外部请求
            task = _in_flight.get(key)
            if task is not None:
                return await asyncio.shield(task)
            task = asyncio.ensure_future(fn(*args, **kwargs))
            _in_flight[key] = task
            try:
                resp = await task
                if 200 <= resp.status_code < 300:
                    if len(_detail_result_cache) >= _DETAIL_RESULT_MAX:
                        # 简单清理: 丢弃最早写入的一半
                        items = sorted(_detail_result_cache.items(), key=lambda kv: kv[1][0])
                        for k, _ in items[:len(items) // 2]:
                            del _detail_result_cache[k]
                    _detail_result_cache[key] = (time.time(), resp)
                return resp
            finally:
                _in_flight.pop(key, None)
        return wrapper
    return deco


# ---------------------------------------------------------------------------
# /api/stock/* 13 条详情路由: 统一用 _fetch_and_call + deps 声明去重
# 自定义逻辑只有 kline(拼 indicators 返回 JSONResponse 而非 _ok) 和 news(wrap 成 {news})
# ---------------------------------------------------------------------------

# 行情(实时)变化快: 短 TTL
@app.get("/api/stock/realtime")
@_detail_cache(ttl=30)
async def api_stock_realtime(code: str):
    return await _fetch_and_call(code, "fetch_realtime")


def _kline_wrap(code, deps):
    """kline 自定义包装: 拼 indicators + 错 None 返回 502."""
    d = deps.get("kl_full")
    fflow = deps.get("ff")
    if d is None:
        return JSONResponse({"error": "K线获取失败"}, status_code=502)
    bars = d.get("bars") or []
    closes = [b.get("close") or 0 for b in bars]
    highs = [b.get("high") or 0 for b in bars]
    lows = [b.get("low") or 0 for b in bars]
    vols = [b.get("volume") or 0 for b in bars]
    d["indicators"] = {
        "boll": stock_detail.calc_boll(closes, 20),
        "zhuoyao": stock_detail.calc_zhuli_zhuoyao(fflow),
        "qibao": stock_detail.calc_qinlong_qibao(closes, highs, lows, vols),
        "macd": stock_detail.calc_macd(closes),
        "kdj": stock_detail.calc_kdj(highs, lows, closes),
        "rsi": stock_detail.calc_rsi(closes),
        "bsMarks": stock_detail.calc_bs_marks(bars),
    }
    return JSONResponse(d, headers=NO_CACHE_HEADERS)


@app.get("/api/stock/kline")
@_detail_cache(ttl=600, key_fn=lambda code, period="day", fqt="qfq": "kline:%s:%s:%s" % (code, period, fqt))
async def api_stock_kline(code: str, period: str = "day", fqt: str = "qfq"):
    return await _fetch_and_call(
        code, None, deps=("kl_full", "ff"), wrap_fn=_kline_wrap, period=period, fqt=fqt
    )


@app.get("/api/stock/fundflow")
@_detail_cache(ttl=600)
async def api_stock_fundflow(code: str):
    return await _fetch_and_call(code, "fetch_fund_flow")


# score/risk/impression/techbao/value/f10 的 deps 组合不同, 但逻辑均是 rt+kl+ff/rt+kl/kl/rt+ff 的子集
@app.get("/api/stock/score")
@_detail_cache(ttl=600)
async def api_stock_score(code: str):
    return await _fetch_and_call(code, "fetch_score", deps=("rt", "kl", "ff"))


@app.get("/api/stock/risk")
@_detail_cache(ttl=600)
async def api_stock_risk(code: str):
    return await _fetch_and_call(code, "fetch_risk_highlight", deps=("rt", "kl"))


@app.get("/api/stock/backtest")
@_detail_cache(ttl=600)
async def api_stock_backtest(code: str):
    return await _fetch_and_call(code, "fetch_backtest", deps=("kl",))


@app.get("/api/stock/impression")
@_detail_cache(ttl=600)
async def api_stock_impression(code: str):
    return await _fetch_and_call(code, "fetch_impression", deps=("rt", "kl"))


@app.get("/api/stock/techbao")
@_detail_cache(ttl=600)
async def api_stock_techbao(code: str):
    return await _fetch_and_call(code, "fetch_techbao", deps=("rt", "kl", "ff"))


@app.get("/api/stock/fundtrack")
@_detail_cache(ttl=600)
async def api_stock_fundtrack(code: str):
    return await _fetch_and_call(code, "fetch_fundtrack", deps=("rt", "ff"))


@app.get("/api/stock/value")
@_detail_cache(ttl=600)
async def api_stock_value(code: str):
    return await _fetch_and_call(code, "fetch_value", deps=("rt", "kl"))


@app.get("/api/stock/news")
@_detail_cache(ttl=600)
async def api_stock_news(code: str):
    # news 返回 list, 前端期望 {news: [...]}, 所以 wrap 一层
    raw = await asyncio.to_thread(stock_detail.fetch_news, code)
    return _ok({"news": raw})


@app.get("/api/stock/f10")
@_detail_cache(ttl=600)
async def api_stock_f10(code: str):
    return await _fetch_and_call(code, "fetch_f10", deps=("rt", "kl"))


@app.get("/api/stock/ai")
@_detail_cache(ttl=300)
async def api_stock_ai(code: str):
    """个股 AI 综合总分析: 合并综评 Tab 零散卡片为单页结构化报告"""
    return await _fetch_and_call(code, "fetch_ai_analysis")


@app.get("/api/stock/detail")
@_detail_cache(ttl=600)
async def api_stock_detail(code: str):
    """详情弹窗一次性聚合 (各模块失败降级为空, 不阻断)"""
    return await _fetch_and_call(code, "build_detail_payload")


# ============================================================
# 股票代码/名称 搜索建议
# ============================================================
_SW_CACHE = None
_SW_CACHE_TS = 0
_SW_CACHE_TTL = 3600  # 1 小时重载一次 (申万映射基本不变)

# 股票搜索 结果级 TTL 缓存 (q 变化频繁但命中率高的查询可复用)
_SUGGEST_CACHE = {}
_SUGGEST_CACHE_TTL = 60

# rps_data.json 解析结果 TTL 缓存 (板块详情接口避免每次请求全量 json.load)
_RPS_DATA_CACHE = None
_RPS_DATA_TS = 0
_RPS_DATA_TTL = 300


def _load_rps_data():
    """读 rps_data.json 并缓存解析结果 (板块详情接口); 返回 (data, err)"""
    global _RPS_DATA_CACHE, _RPS_DATA_TS
    now = time.time()
    if _RPS_DATA_CACHE is not None and now - _RPS_DATA_TS < _RPS_DATA_TTL:
        return _RPS_DATA_CACHE, None
    path = STATIC_DIR / "rps_data.json"
    if not path.exists():
        return None, "rps_data.json 缺失"
    try:
        import json
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _RPS_DATA_CACHE = data
        _RPS_DATA_TS = now
        return data, None
    except Exception:
        return None, "rps_data.json 解析失败"


def _load_sw_stock_map():
    """从 sw_industry_cache.json 读 stock_name: {ts_code: 名称}"""
    global _SW_CACHE, _SW_CACHE_TS
    now = time.time()
    if _SW_CACHE is not None and now - _SW_CACHE_TS < _SW_CACHE_TTL:
        return _SW_CACHE
    cache_file = STATIC_DIR / "sw_industry_cache.json"
    if not cache_file.exists():
        # 回退: 根目录 sw_industry_cache.json (老路径)
        cache_file = Path(__file__).parent / "sw_industry_cache.json"
    if not cache_file.exists():
        _SW_CACHE = {}
        _SW_CACHE_TS = now
        return _SW_CACHE
    try:
        import json
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        sn = data.get("stock_name") or {}
        # 同时构造"纯数字代码"→ts_code 的反向索引, 搜 600519 也能命中 600519.SH
        digit_to_ts = {}
        for ts in sn.keys():
            try:
                d = ts.split(".")[0]
                if d:
                    digit_to_ts.setdefault(d, ts)  # 同数字不同市场极少, 先到先得
            except Exception:
                pass
        _SW_CACHE = {"stock_name": sn, "digit_to_ts": digit_to_ts}
    except Exception:
        _SW_CACHE = {"stock_name": {}, "digit_to_ts": {}}
    _SW_CACHE_TS = now
    return _SW_CACHE


@app.get("/api/stocks/suggest")
async def api_stocks_suggest(q: str = "", limit: int = 10):
    """搜索股票: 支持 中文名称(精确/子串)/拼音首字母(若后续加)/ts_code(000998.SZ)/纯数字(600519)。
    返回: [{code:'600519.SH', name:'贵州茅台', digit:'600519'}] 最多 limit 条"""
    raw = (q or "").strip().lower()
    if not raw:
        return JSONResponse([], headers=NO_CACHE_HEADERS)
    # 结果级 TTL 缓存: 避免每次输入全量遍历 ~5000 只股票
    ckey = raw + ":" + str(limit)
    now = time.time()
    cached = _SUGGEST_CACHE.get(ckey)
    if cached is not None and now - cached[0] < _SUGGEST_CACHE_TTL:
        return cached[1]
    mp = _load_sw_stock_map()
    sn = mp.get("stock_name") or {}
    d2ts = mp.get("digit_to_ts") or {}
    results = []
    seen = set()
    # 1) 精确匹配: 纯数字代码(用户最常搜)
    if raw.isdigit():
        ts = d2ts.get(raw)
        if ts and ts not in seen:
            name = sn.get(ts, ts)
            results.append({"code": ts, "name": name, "digit": raw})
            seen.add(ts)
    # 2) 精确 ts_code: 600519.SH / 000001.SZ
    raw_up = raw.upper().replace("sh", ".SH").replace("sz", ".SZ")
    for cand in (raw, raw + ".SH", raw + ".SZ", raw.replace(".", "").replace("SH", ".SH").replace("SZ", ".SZ")):
        if cand in sn and cand not in seen:
            name = sn[cand]
            try:
                d = cand.split(".")[0]
            except Exception:
                d = cand
            results.append({"code": cand, "name": name, "digit": d})
            seen.add(cand)
    # 3) 名称/代码 子串匹配 (限 limit 控制性能)
    if len(results) < limit:
        q_nospace = raw
        # 预处理后名称包含 & 数字代码包含
        for ts, name in sn.items():
            if len(results) >= limit:
                break
            if ts in seen:
                continue
            try:
                digit = ts.split(".")[0]
            except Exception:
                digit = ts
            name_lc = (name or "").lower()
            # 包含判定: 名称包含 或 代码包含
            if (q_nospace in name_lc) or (raw in digit) or (raw in ts.lower()):
                results.append({"code": ts, "name": name or ts, "digit": digit})
                seen.add(ts)
    # 4) 截断
    results = results[:limit]
    resp = JSONResponse(results, headers=NO_CACHE_HEADERS)
    _SUGGEST_CACHE[ckey] = (time.time(), resp)
    return resp


# ============================================================
# 板块详情接口 (V2.0 复盘需求 4.4: 板块 chip → 轻量二级板块详情)
# 从 rps_data.json 三档入选/调出中定位板块, 返回概览 + 三档维度 + 成分股
# ============================================================
@app.get("/api/sector/detail")
async def api_sector_detail(name: str = ""):
    """板块详情: 精确名优先, 宽容包含匹配兜底。
    返回 {name, parent, status, stock_count, RPS, rank, rank_change, chg5, continuous,
          dims: [{level, status, rps, rank, rank_change, continuous}],
          stocks: [成分股], update_date}; 未找到 {name:'', stocks:[]}"""
    raw = (name or "").strip()
    if not raw:
        return JSONResponse({"name": "", "stocks": []}, headers=NO_CACHE_HEADERS)
    data, err = _load_rps_data()
    if data is None:
        return JSONResponse({"name": raw, "stocks": [], "error": err}, headers=NO_CACHE_HEADERS)

    def _all_items(lv):
        block = data.get(lv) or {}
        return (block.get("in_list") or []) + (block.get("out_list") or [])

    def _find(nm):
        for lv in ("rps5", "rps10", "rps20"):
            for it in _all_items(lv):
                if it.get("name") == nm:
                    return it
        return None

    found = _find(raw)
    if not found:  # 宽容包含匹配 (chip 截断名等)
        for lv in ("rps5", "rps10", "rps20"):
            for it in _all_items(lv):
                nm = it.get("name") or ""
                if nm and (nm in raw or raw in nm):
                    found = it
                    break
            if found:
                break
    if not found:
        return JSONResponse({"name": raw, "stocks": []}, headers=NO_CACHE_HEADERS)
    dims = []
    for lv, label, rps_key in (("rps5", "RPS5", "RPS5"), ("rps10", "RPS10", "RPS10"),
                               ("rps20", "RPS20", "RPS20")):
        it = _find(found["name"])
        dims.append({"level": label, "status": (it.get("status") if it else None),
                     "rps": (it.get(rps_key) if it else None),
                     "rank": (it.get("rank") if it else None),
                     "rank_change": (it.get("rank_change") if it else None),
                     "continuous": (it.get("continuous") if it else None)})
    return JSONResponse({
        "name": found.get("name"), "parent": found.get("parent"),
        "status": found.get("status"), "stock_count": found.get("stock_count"),
        "RPS": found.get("RPS5", found.get("RPS10", found.get("RPS20"))),
        "rank": found.get("rank"), "rank_change": found.get("rank_change"),
        "chg5": found.get("chg5"), "continuous": found.get("continuous"),
        "dims": dims,
        "stocks": found.get("stocks") or [],
        "update_date": data.get("update_date", ""),
    }, headers=NO_CACHE_HEADERS)


# ============================================================
# 超跌反转潜力推荐接口 (读取静态 rebound_recommendations.json)
# 失败降级返回空结构 (前端展示"暂无超跌反转推荐"提示)
# ============================================================
@app.get("/api/recommend/rebound")
async def api_recommend_rebound():
    """返回 static/rebound_recommendations.json 内容 (超跌反转策略)"""
    path = STATIC_DIR / "rebound_recommendations.json"
    if not path.exists():
        return JSONResponse({
            "schema_version": 1, "mode": "oversold_rebound",
            "update_date": "", "prev_date": "", "count": 0, "stocks": [],
            "market_status": {"is_safe": None, "reason": "尚未生成超跌反转推荐，请先运行 rps_calc.py"},
            "disclaimer": "暂无超跌反转推荐数据",
        }, headers=NO_CACHE_HEADERS)
    try:
        import json as _json
        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        return JSONResponse(data, headers=NO_CACHE_HEADERS)
    except Exception as e:
        return JSONResponse({"error": f"读取超跌反转推荐失败: {str(e)}", "count": 0, "stocks": []},
                            status_code=500, headers=NO_CACHE_HEADERS)


if __name__ == "__main__":
    print("=" * 50)
    print("板块 RPS 监测看板 - Web 服务器")
    print("=" * 50)
    print("手机访问地址:")
    print("  http://localhost:8000  (本机)")
    print("  http://<你的局域网IP>:8000  (手机)")
    print()
    print("使用前请先运行: python rps_calc.py")
    print("=" * 50)
    uvicorn.run("web_server:app", host="0.0.0.0", port=8000, reload=False)
