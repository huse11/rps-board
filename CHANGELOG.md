# RPS 看板系统 — 更新日志与维护手册

> 本文档记录系统的**选股逻辑**、**缓存回退机制**与**历次更新**，供后续维护参考。
> 更新时间：2026-08-13（数据绑定交易日 20260813）

---

## 一、本次更新概要（2026-08-13）

| 模块 | 变更 | 原因 |
|---|---|---|
| 选股逻辑 | 大盘环境第一层校验确认：指数站上 20 日线但上涨占比 < 30% 时终止选股，输出 0 只 | 当日 8/13 实测验证（上涨占比 21%，普跌） |
| 缓存机制 | `stock_basic` 缓存回退策略 | 服务器 Tushare 免费 token 无 `stock_basic` 权限，重拉必失败；旧缓存含 110 个真实细分行业，优于代码前缀推断的 6 个粗分组 |
| 缓存机制 | `fina_indicator` 缓存跨交易日重建 | 财务为季度披露数据，需保证最新披露期 |
| 缓存机制 | `index_cache` 指数缓存必须含最新交易日才复用 | 修复"缓存永不刷新"导致的 8/13 误判 |
| 数据链路 | 服务器补齐 `akshare_adapter.py`（AKShare 腾讯数据源适配器） | 服务器缺文件导致 AKShare 数据源不可用 |
| 部署 | 彻底断开 GitHub（删除 3 个 Actions workflow、移除 remote、App 去掉 gh-pages 备用源） | 仅用阿里云服务器 |

---

## 二、选股逻辑（recommend.py v3.0 五层漏斗 + 综合评分）

整体架构：**漏斗式逐层过滤，候选股 100% 严格限定在 RPS 入选板块成分股范围内**。

### 第一层：大盘环境全局校验（不满足直接终止选股，输出 0 只）
- 指数：上证指数（000001.SH）+ 创业板指（399006.SZ），20 日均线
- 条件：**至少 1 个指数站稳 20 日线**（`need_above=1`）+ **上涨家数占比 ≥ 30%**（`advance_ratio_min=0.30`）+ **跌停 ≤ 50 家**（`limit_down_max=50`）
- 指数数据完全缺失时默认安全（`default_safe=True`，避免接口故障误否决全市场）
- **8/13 实测**：上证收盘 3926.97 / MA 3862.24（站上）、创业板收盘 3586.04 / MA 3487.29（站上），但上涨占比仅 **21%**（< 30%）→ 终止选股，正确输出 0 只

### 第二层：候选池（核心约束）
- 候选股 100% 来自 RPS 入选板块（RPS5/10/20 ≥ 90）成分股
- 每股统计归属强势板块数，多题材共振计数（`min_sector_count=1`）

### 第三层：基本面排雷（硬性一票否决）
| 项 | 阈值 |
|---|---|
| ST/*ST/退 | 名称含关键字强制剔除 |
| 最低收盘价 | < 2.0 元剔除（低价仙股） |
| 最低流通市值 | < 10 亿剔除（circ_mv 单位万元） |
| PE-TTM 上限 | > 200 剔除 |
| 资产负债率 | > 80% 剔除 |
| 股权质押比例 | > 50% 剔除（数据缺失不剔除） |
| 商誉占净资产 | > 30% 剔除（数据缺失不剔除） |
| 次新股 | 上市不足 60 个交易日剔除（list_date 换算） |

### 第四层：资金面达标校验（核心项必达）
| 项 | 阈值 |
|---|---|
| 换手率 | 2.0% ~ 25.0% |
| 量比 | ≥ 1.1 |
| 单日成交额 | ≥ 5000 万 |

### 第五层：多技术指标共振（7 项独立指标达标 ≥ 3 项）
- 均线多头排列、MACD 金叉、KDJ 金叉、BOLL 突破、个股 RPS50 ≥ 80 等
- `hit_min=3` 项达标判为通过

### 综合评分（总分 100）
| 维度 | 权重 | 说明 |
|---|---|---|
| 板块热度 | 40 | 归属强势板块数，权重最高 |
| 基本面 | 20 | 财务质量 |
| 资金面 | 20 | 40 分梯度制：换手率 6-8%→20 分、4-6%/8-10%→15 分、10-12%→10 分；量比 1.5-2.5→20 分、1.3-1.5/2.5-3.5→15 分、≥3.5→5 分、<1.3→0 分 |
| 技术面 | 20 | 累计制：均线多头 10 分、MACD 零上金叉 8 分、KDJ 金叉（J<80）4 分、BOLL 中轨突破 3 分 |

- **输出门槛**：综合得分 ≥ 70（`min_score=70`），低于不显示
- **标签**：优选 ≥ 80（2-8 周持有）、短线 70-79（1-7 天持有）
- **大盘判定终止时**：第一层直接返回，前端展示"大盘环境不满足，暂无推荐"

### 预测化增强（predict.py）
- 每只推荐股输出 6 维度概率：动量延续 / 技术形态 / 资金行为 / 板块轮动 / 情绪周期 / 基本面预期差
- 附带：3 日上涨概率、预期涨幅、止损位、置信度、预测逻辑说明
- 每日校准：`predictions_log.json` 记录预测，次日核对实际涨跌输出分维度胜率

---

## 三、缓存回退机制（数据源可靠性）

> 所有数据源采用 **多级降级** 策略，任一接口失败不影响主流程。

### 3.1 缓存文件刷新规则明细

| 缓存文件 | 写入方 | 刷新规则 | 降级路径 |
|---|---|---|---|
| `trade_cal_cache.json` 交易日历 | rps_calc | 缓存末日 ≥ 今天才复用 | Tushare trade_cal 失败 → pandas bdate_range 生成 |
| `stock_basic_cache.json` 股票基础 | rps_calc | `fetch_date` ≤ 5 天才复用；**缺 fetch_date 或过期 → 先尝试重拉，失败回退复用旧缓存（补 fetch_date）** | Tushare 失败 → 旧缓存（110 真实行业）→ 代码前缀推断（6 粗分组，仅无缓存时） |
| `daily_cache/*.json` 日 K | rps_calc | 当日文件存在即复用 | Tushare daily 多 token 轮询 |
| `index_cache/*.json` 指数日线 | recommend | **≥ 60 条且已含最新交易日才复用**，否则重拉 | Tushare index_daily → AKShare → 东财（失败默认安全） |
| `fina_indicator_cache.json` 财务 | recommend | **`_fetched_date` == 最新交易日才复用**，跨季度/披露期变化清空重建 | Tushare → 东财（失败降级 PE>0） |
| `moneyflow_cache.json` 主力资金 | recommend | 按交易日增量补齐缺失日期 | Tushare → 近似估算 → AKShare → 东财 |
| `moneyflow_hsgt_cache.json` 北向 | recommend | 按日增量 | 失败跳过（加分项，不影响否决；北向 2024 年起停止每日披露） |
| `daily_basic` 行情 | recommend | 当日拉取 | Tushare → AKShare 腾讯 → 东财 |

### 3.2 stock_basic 缓存回退策略（2026-08-13 新增）
- **背景**：服务器 5 个 Tushare token 均为免费版，`stock_basic` 接口无权限（报"您没有接口"），且 Token1/2 被限频 3600s。若每次重拉必失败，将降级为**代码前缀推断**（仅 6 个粗分组：沪市主板/深市主板/创业板/科创板/北交所等），板块映射质量严重退化。
- **修复**：Tushare 拉取失败时，若存在含 `list_date` + `industry` 的旧缓存，**回退复用旧缓存**（实测 5539 只、110 个真实细分行业），并补写 `fetch_date=今天` 写回，避免每次重复尝试。
- **优先级**：新缓存（fetch_date ≤ 5 天）> Tushare 重拉成功 > 旧缓存回退 > 代码前缀推断。

### 3.3 数据源三级降级总览
```
Tushare Pro (免费token, 部分接口无权限/限频)
  └─ 失败 → AKShare (腾讯/东财, 已装 1.18.83, 服务器需有 akshare_adapter.py)
       └─ 失败 → 东财手动 push2 接口 / 近似估算
```

---

## 四、数据更新链路总览（每日收盘后）

```
cron: 工作日 18:30 → /opt/rps-board/cron_rps.sh
  → rps_calc.py（RPS 计算, SCHEMA_VERSION 幂等）
    → recommend.py（每日推荐, 五层漏斗 + 预测, 解耦独立执行）
    → recommend_weekly.py（每周五, 周线五层漏斗）
    → daily_review.py（每日复盘, 六方向 + 情绪周期, 幂等）
  → systemctl restart rps-board.service
```

| 数据文件 | 更新频率 | 最新状态 |
|---|---|---|
| rps_data.json / rps_history.json / last_result.json | 每日 | 8/13 三档各 11 板块 |
| recommendations.json + predictions_log.json | 每日 | 8/13（0 只，大盘不合格） |
| weekly_recommendations.json | 每周五 | 8/10 |
| daily_review.json / daily_review.md | 每日 | 8/13 |
| 各缓存（见第三节） | 按规则 | 均已含 8/13 |

---

## 五、历史更新日志（近期）

### 2026-08-13 — 数据全链路梳理与修复
- 修复 `index_cache` 永不刷新 bug（缓存须含最新交易日才复用），8/13 大盘判定由误判"跌破 60 日线"修正为正确判定
- 修复 `stock_basic` 缓存无 `fetch_date` 导致每次重拉降级的问题（回退机制见 3.2）
- 修复 `fina_indicator` 跨季度不刷新（`_fetched_date` 机制）
- 补传 `akshare_adapter.py` 到服务器，AKShare 数据源验证可用（指数可拉到 8/13、daily_basic 正常）
- 确认所有数据文件 `update_date` 绑定最新交易日（非计算日期），服务器/GitHub Pages 均显示 8/13
- 运行完整选股流程验证：今日大盘环境下确实输出 0 只推荐（上涨占比 21%）

### 2026-08-13 — 欧奈尔标准 RPS 公式（SCHEMA_VERSION 2 → 3）
- RPS = (1 - rank / total) × 100（rank 从 1 起，涨幅最高 = 排名 1）
- 阈值 90 下，110 个板块前 11 名入选（rank11 = 90.0、rank12 = 89.09）
- 修改公式/格式需 SCHEMA_VERSION + 1 强制全量重算（幂等保护）

### 2026-08-13 — 彻底断开 GitHub
- 删除 `.github/workflows/` 下全部 3 个 Actions workflow（rps_deploy / daily_check / build_apk）
- `git remote remove origin`，仅保留本地版本历史
- Android App 去掉 gh-pages 备用源，仅连阿里云主源（http://47.99.178.225:8000/）
- 服务器数据链路完全自给自足（本地 cron + systemd，无任何外部依赖）

### 2026-08-12 — 每日复盘模块（daily_review.py）
- 六方向：市场定调 / 梯队主线 / 轮动 / 健康度 / 风险预警 / 跟踪清单
- 进阶：情绪周期五档（冰点/退潮/高潮/发酵/启动）+ 8 风格关键词归因
- 复用 rps_data / rps_history / index_cache / recommendations，不重复拉数

### 2026-08-10 — 资金面评分梯度化
- 换手率/量比从二元阈值改为 40 分梯度制（见第二节评分表）

---

## 六、维护注意事项

1. **SCHEMA_VERSION 幂等**：`rps_calc.py` 顶部常量（当前 v3）。同日已生成且版本达标则跳过计算，保护 last_result 调入/调出对比基准。**改公式/输出格式必须 +1**，否则不重算。
2. **推荐与 RPS 解耦**：即使 RPS 幂等跳过，推荐流程仍独立加载 daily_map 执行，保证推荐每天生成。
3. **服务器部署**：默认只推阿里云服务器。改代码后执行 `python _upload_file.py <本地> /opt/rps-board/<远程>`，然后 `python _ssh_run.py "systemctl restart rps-board.service"`。切勿恢复 GitHub 推送。
4. **服务器必装文件**：`akshare_adapter.py` 必须与 `recommend.py` 同在 /opt/rps-board/（缺失则 AKShare 腾讯数据源不可用，仅剩东财降级）。
5. **缓存数据不入库**：`rps_data.json` / `last_result.json` / `rps_history.json` / `index_cache/` 等在 .gitignore 中，GitHub 分支已停用，数据只在阿里云服务器生成。
6. **测试**：改逻辑后跑 `python -m unittest discover -s tests`（184 项，Windows 下需 `PYTHONIOENCODING=utf-8` 避免 GBK emoji 报错）+ `node --test tests/test_render.js`（33 项）。
7. **大盘终止选股是设计行为**：上涨占比 < 30% 或跌停 > 50 家时输出 0 只推荐，前端显示"大盘环境不满足"，非数据故障。
8. **Tushare 免费 token 无 premium 接口权限**（fina_indicator / moneyflow / index_daily / stock_basic），全部依赖降级策略兜底，属正常预期。
