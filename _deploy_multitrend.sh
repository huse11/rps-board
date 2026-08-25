#!/bin/bash
set -e
cd /opt/rps-board
echo "=== 1. 语法检查 ==="
/opt/rps-board/venv/bin/python -m py_compile stock_detail.py web_server.py
echo "OK: py_compile passed"

echo "=== 2. 服务器 pytest (stock_detail) ==="
cd /opt/rps-board
/opt/rps-board/venv/bin/python -m pytest tests/test_stock_detail.py -q 2>&1 | tail -5

echo "=== 3. 重启 rps-board ==="
systemctl restart rps-board
sleep 2
systemctl is-active rps-board

echo "=== 4. HTTP 验证 ==="
curl -s -o /dev/null -w "index: %{http_code}\n" http://127.0.0.1:8000/
curl -s -o /dev/null -w "kline multi: %{http_code}\n" "http://127.0.0.1:8000/api/stock/kline?code=601020&period=multi&fqt=qfq&days=5"
curl -s -o /dev/null -w "render.js: %{http_code}\n" http://127.0.0.1:8000/render.js
