"""
板块 RPS 监测看板 - Web 服务器
启动后手机浏览器访问 http://你的IP:8000
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
import os
from pathlib import Path

app = FastAPI(title="板块 RPS 监测看板")

# 静态文件目录
STATIC_DIR = Path(__file__).parent / "static"
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 入口文件禁止缓存(手机 App WebView 缓存旧 render.js 曾导致 buildReviewHtml is not defined)
NO_CACHE_HEADERS = {"Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache", "Expires": "0"}


@app.get("/")
async def home():
    index_path = Path(__file__).parent / "index.html"
    return FileResponse(str(index_path), headers=NO_CACHE_HEADERS)


@app.get("/render.js")
async def render_js():
    # render.js 与 index.html 同级在项目根目录，单独路由返回
    js_path = Path(__file__).parent / "render.js"
    return FileResponse(str(js_path), media_type="text/javascript", headers=NO_CACHE_HEADERS)


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
