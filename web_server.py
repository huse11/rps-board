"""
板块 RPS 监测看板 - Web 服务器
启动后手机浏览器访问 http://你的IP:8000
"""
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


@app.get("/api/stock/realtime")
async def api_stock_realtime(code: str):
    return _ok(stock_detail.fetch_realtime(code))


@app.get("/api/stock/kline")
async def api_stock_kline(code: str, period: str = "day"):
    d = stock_detail.fetch_kline(code, period)
    if d is None:
        return JSONResponse({"error": "K线获取失败"}, status_code=502)
    # 附加指标: BOLL / 主力捉妖 / 擒龙启爆 / B/S 标记 (基于日线或对应周期)
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
        "bsMarks": stock_detail.calc_bs_marks(bars),
    }
    return JSONResponse(d, headers=NO_CACHE_HEADERS)


@app.get("/api/stock/fundflow")
async def api_stock_fundflow(code: str):
    return _ok(stock_detail.fetch_fund_flow(code))


@app.get("/api/stock/score")
async def api_stock_score(code: str):
    rt = stock_detail.fetch_realtime(code)
    kl = stock_detail.fetch_kline(code, "day")
    ff = stock_detail.fetch_fund_flow(code)
    return _ok(stock_detail.fetch_score(code, rt, kl, ff))


@app.get("/api/stock/risk")
async def api_stock_risk(code: str):
    rt = stock_detail.fetch_realtime(code)
    kl = stock_detail.fetch_kline(code, "day")
    return _ok(stock_detail.fetch_risk_highlight(code, rt, kl))


@app.get("/api/stock/backtest")
async def api_stock_backtest(code: str):
    kl = stock_detail.fetch_kline(code, "day")
    return _ok(stock_detail.fetch_backtest(code, kl))


@app.get("/api/stock/impression")
async def api_stock_impression(code: str):
    rt = stock_detail.fetch_realtime(code)
    kl = stock_detail.fetch_kline(code, "day")
    return _ok(stock_detail.fetch_impression(code, rt, kl))


@app.get("/api/stock/techbao")
async def api_stock_techbao(code: str):
    rt = stock_detail.fetch_realtime(code)
    kl = stock_detail.fetch_kline(code, "day")
    ff = stock_detail.fetch_fund_flow(code)
    return _ok(stock_detail.fetch_techbao(code, rt, kl, ff))


@app.get("/api/stock/fundtrack")
async def api_stock_fundtrack(code: str):
    rt = stock_detail.fetch_realtime(code)
    ff = stock_detail.fetch_fund_flow(code)
    return _ok(stock_detail.fetch_fundtrack(code, rt, ff))


@app.get("/api/stock/value")
async def api_stock_value(code: str):
    rt = stock_detail.fetch_realtime(code)
    kl = stock_detail.fetch_kline(code, "day")
    return _ok(stock_detail.fetch_value(code, rt, kl))


@app.get("/api/stock/news")
async def api_stock_news(code: str):
    return _ok({"news": stock_detail.fetch_news(code)})


@app.get("/api/stock/f10")
async def api_stock_f10(code: str):
    rt = stock_detail.fetch_realtime(code)
    kl = stock_detail.fetch_kline(code, "day")
    return _ok(stock_detail.fetch_f10(code, rt, kl))


@app.get("/api/stock/detail")
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
