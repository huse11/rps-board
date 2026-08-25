"""
板块 RPS 监测看板 - Web 服务器
启动后手机浏览器访问 http://你的IP:8000
"""
import time
from functools import wraps

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
# ============================================================

def _ok(data):
    if data is None:
        return JSONResponse({"error": "数据获取失败"}, status_code=502)
    return JSONResponse(data, headers=NO_CACHE_HEADERS)


# ============================================================
# 详情接口结果级缓存 (解决 5-8 秒慢响应: 重复查看/切Tab 直接命中)
# 仅缓存成功响应(2xx); 最多保留 N 条防止内存无界增长
# ============================================================
_DETAIL_RESULT_MAX = 2000
_detail_result_cache = {}


def _detail_cache(ttl=300, key_fn=None):
    """装饰详情接口: 按 (函数名:code[:params]) 缓存成功 JSONResponse"""
    def deco(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            key = key_fn(*args, **kwargs) if key_fn else (
                fn.__name__ + ":" + str(kwargs.get("code") or args[0]).upper())
            now = time.time()
            hit = _detail_result_cache.get(key)
            if hit is not None and now - hit[0] < ttl:
                return hit[1]
            resp = await fn(*args, **kwargs)
            if 200 <= resp.status_code < 300:
                if len(_detail_result_cache) >= _DETAIL_RESULT_MAX:
                    # 简单清理: 丢弃最早写入的一半
                    items = sorted(_detail_result_cache.items(), key=lambda kv: kv[1][0])
                    for k, _ in items[:len(items) // 2]:
                        del _detail_result_cache[k]
                _detail_result_cache[key] = (now, resp)
            return resp
        return wrapper
    return deco


# 行情(实时)变化快: 短 TTL
@app.get("/api/stock/realtime")
@_detail_cache(ttl=30)
async def api_stock_realtime(code: str):
    return _ok(stock_detail.fetch_realtime(code))


@app.get("/api/stock/kline")
@_detail_cache(ttl=600, key_fn=lambda code, period="day", fqt="qfq": "kline:%s:%s:%s" % (code, period, fqt))
async def api_stock_kline(code: str, period: str = "day", fqt: str = "qfq"):
    d = stock_detail.fetch_kline(code, period, fqt)
    if d is None:
        return JSONResponse({"error": "K线获取失败"}, status_code=502)
    # 附加指标: BOLL / 主力捉妖 / 擒龙启爆 / MACD / KDJ / RSI (基于日线或对应周期)
    bars = d.get("bars") or []
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    vols = [b["volume"] or 0 for b in bars]
    fflow = stock_detail.fetch_fund_flow(code)
    d["indicators"] = {
        "boll": stock_detail.calc_boll(closes, 20),
        "zhuoyao": stock_detail.calc_zhuli_zhuoyao(fflow),
        "qibao": stock_detail.calc_qinlong_qibao(closes, highs, lows, vols),
        "macd": stock_detail.calc_macd(closes),
        "kdj": stock_detail.calc_kdj(highs, lows, closes),
        "rsi": stock_detail.calc_rsi(closes),
        # B/S 历史信号标记: 均线金叉(B)/死叉(S), 纯静态仅供查看, 不可点击交易
        "bsMarks": stock_detail.calc_bs_marks(bars),
    }
    return JSONResponse(d, headers=NO_CACHE_HEADERS)


@app.get("/api/stock/fundflow")
@_detail_cache(ttl=600)
async def api_stock_fundflow(code: str):
    return _ok(stock_detail.fetch_fund_flow(code))


@app.get("/api/stock/score")
@_detail_cache(ttl=600)
async def api_stock_score(code: str):
    rt = stock_detail.fetch_realtime(code)
    kl = stock_detail.fetch_kline(code, "day")
    ff = stock_detail.fetch_fund_flow(code)
    return _ok(stock_detail.fetch_score(code, rt, kl, ff))


@app.get("/api/stock/risk")
@_detail_cache(ttl=600)
async def api_stock_risk(code: str):
    rt = stock_detail.fetch_realtime(code)
    kl = stock_detail.fetch_kline(code, "day")
    return _ok(stock_detail.fetch_risk_highlight(code, rt, kl))


@app.get("/api/stock/backtest")
@_detail_cache(ttl=600)
async def api_stock_backtest(code: str):
    kl = stock_detail.fetch_kline(code, "day")
    return _ok(stock_detail.fetch_backtest(code, kl))


@app.get("/api/stock/impression")
@_detail_cache(ttl=600)
async def api_stock_impression(code: str):
    rt = stock_detail.fetch_realtime(code)
    kl = stock_detail.fetch_kline(code, "day")
    return _ok(stock_detail.fetch_impression(code, rt, kl))


@app.get("/api/stock/techbao")
@_detail_cache(ttl=600)
async def api_stock_techbao(code: str):
    rt = stock_detail.fetch_realtime(code)
    kl = stock_detail.fetch_kline(code, "day")
    ff = stock_detail.fetch_fund_flow(code)
    return _ok(stock_detail.fetch_techbao(code, rt, kl, ff))


@app.get("/api/stock/fundtrack")
@_detail_cache(ttl=600)
async def api_stock_fundtrack(code: str):
    rt = stock_detail.fetch_realtime(code)
    ff = stock_detail.fetch_fund_flow(code)
    return _ok(stock_detail.fetch_fundtrack(code, rt, ff))


@app.get("/api/stock/value")
@_detail_cache(ttl=600)
async def api_stock_value(code: str):
    rt = stock_detail.fetch_realtime(code)
    kl = stock_detail.fetch_kline(code, "day")
    return _ok(stock_detail.fetch_value(code, rt, kl))


@app.get("/api/stock/news")
@_detail_cache(ttl=600)
async def api_stock_news(code: str):
    return _ok({"news": stock_detail.fetch_news(code)})


@app.get("/api/stock/f10")
@_detail_cache(ttl=600)
async def api_stock_f10(code: str):
    rt = stock_detail.fetch_realtime(code)
    kl = stock_detail.fetch_kline(code, "day")
    return _ok(stock_detail.fetch_f10(code, rt, kl))


@app.get("/api/stock/detail")
@_detail_cache(ttl=600)
async def api_stock_detail(code: str):
    """详情弹窗一次性聚合 (各模块失败降级为空, 不阻断)"""
    return _ok(stock_detail.build_detail_payload(code))


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
    uvicorn.run("web_server:app", host="0.0.0.0", port=8000, reload=True)
