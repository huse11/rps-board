#!/bin/bash
# 每日定时计算 RPS + 生成推荐股票
# crontab -e 添加: 30 18 * * 1-5 /opt/rps-board/cron_rps.sh >> /var/log/rps_cron.log 2>&1

set -e
cd /opt/rps-board

echo "=========================================="
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始每日计算"
echo "=========================================="

# 运行 RPS 计算 + 推荐生成
venv/bin/python rps_calc.py 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 计算完成"
echo ""

# 重启 web 服务(确保新数据被加载)
systemctl restart rps-board.service
echo "[$(date '+%Y-%m-%d %H:%M:%S')] web 服务已重启"
