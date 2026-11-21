# -*- coding: utf-8 -*-
"""
统一日志工具 (P1-6 优化):
- 所有核心计算模块(rps_calc/recommend/...)用 get_logger(__name__)拿到同配置 logger
- 标准输出 handler: 带时间戳/级别/模块名, 与 cron 日志兼容性好
- 行为兼容: 默认写 stdout, 不影响 systemd/cron 抓取; 不关闭用户直接用的 print.

用法:
    from logutil import get_logger
    logger = get_logger(__name__)
    logger.info("=== 模块启动 ===")  # 代替顶层 print(...)
"""
import logging
import sys


_FMT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def _configure_once():
    global _configured
    if _configured:
        return
    _configured = True
    # 根 logger 默认 INFO; 已经挂过 handler 就不重复加(避免 pytest 等宿主预配置的情况)
    root = logging.getLogger()
    if not root.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter(_FMT, _DATEFMT))
        root.addHandler(h)
        root.setLevel(logging.INFO)
    else:
        # 宿主已有 handler: 确保 root 级别至少 INFO
        if root.level == logging.NOTSET or root.level > logging.INFO:
            root.setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    _configure_once()
    return logging.getLogger(name)
