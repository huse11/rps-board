#!/bin/bash
# 服务器端: 写入新的 version.json (v1.3, 记住忽略版本功能)
/opt/rps-board/venv/bin/python << 'PYEOF'
import json
data = {
    "versionCode": 4,
    "versionName": "1.3",
    "url": "http://47.99.178.225:8000/static/app/rps-board.apk",
    "note": "修复: 点「以后再说」后记住版本, 同一版本不再重复弹更新提示",
}
with open("/opt/rps-board/static/app/version.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(open("/opt/rps-board/static/app/version.json", encoding="utf-8").read())
PYEOF
