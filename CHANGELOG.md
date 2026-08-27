# RPS 看板系统 — 更新日志与维护手册

> 本文档记录系统的**选股逻辑**、**缓存回退机制**与**历次更新**，供后续维护参考。
> 更新时间：2026-08-25（数据绑定交易日 20260825）

---

## 〇、更新概要（2026-08-25）

| 模块 | 变更 | 原因 |
|---|---|---|
| 复盘 | 每日/每周复盘新增**推荐准确率分析**（命中率/平均涨幅/分置信度胜率/最佳最差个股），数据来自 prediction_review.json | 复盘要能评估日度推荐质量 |
| 复盘 | 每日复盘文字全面精简（去冗余分位说明/条件括号解释/超长 note），并修复 daily_review.md 底部 AUTO_GEN_END 标记重复累积 | 复盘太长、可读性差 |
| 看板 | RPS5/10/20 三档表格**删除 RPS 列**，改显示各档实际周期涨幅（5日/10日/20日涨幅，红涨绿跌） | RPS 为排名百分位（各档 Top20 序列必然相同），真实涨幅才能体现各档强弱差异 |
| 部署 | **彻底移除代码中全部 GitHub 相关引用**（index.html 提示文案/注释），仅保留阿里云服务器部署（47.99.178.225:8000） | 不再需要 GitHub 服务器 |
| 复盘 | **每日预测校准接入每日流程**（`_maybe_run_prediction_accuracy`），收盘后自动验证历史预测并更新 prediction_review.json，使"推荐准确率复盘"按最新验证结果更新 | 原校准函数一直未接入每日流程，prediction_review.json 停留在 20260821，复盘多天显示同一数据 |
| 预测 | **命中验证周期由 3 日改为次日**（`PRED_HORIZON 3→1`）：命中=昨日推荐今日上涨（次收盘≥预测日现价）；幂等跳过路径也强制执行校准 | 原为预测后 3 日验证，用户要求次日口径 |
| 复盘 | **周度推荐准确率改用完整验证列表**（prediction_review 新增 `all` 字段），按次日口径精确筛取本周预测验证结果，替代原 recent(仅15条)截断 | 原周度从 recent 截断漏样本 + 3日口径失真（本周样本 15→31、命中率 7%→58%） |
| 定调 | **市场定调引入强度维度**：新增高分板块平均5日涨幅（`high_rps_avg_gain`），market_tone 由数量+强度双维度判定；同步校准全阈值（全面强/结构性强/弱/情绪/动量衰减/周度/板块替代路径） | RPS95 下高分板块数恒定~10，原数量阈值(20/25/15/8)全部失效，大盘恒"中性偏弱" |
| 预测 | **次日口径全面对齐**：PRED_HORIZON=1 后 EXPECTED_GAIN 由3日量级调小（75→3.0%等）、PROB_BASE 52、注释更新 | 预测框显示"次日"但预期涨幅仍按3日量级偏高 |
| 清理 | 删除不被引用的 000300.SH.json 缓存、冗余的 _android_src.tar.gz 与工作区一次性调试脚本（_diag_*/_probe*/_ssh_probe*/_sync_*/tmp_* 等）；删除 GitHub 服务器残留密钥（.deploy_keys/github_rps）；tk.csv 凭据文件加入 .gitignore；recommend.py 板块替代路径兜底默认值 15→8 与校准值对齐 | 冗余数据与脚本、残留 GitHub 凭据 |

---

## 一、本次更新概要（2026-08-14）

| 模块 | 变更 | 原因 |
|---|---|---|
| 股票池 | `rps_calc.get_stock_industry` 所有出口统一剔除 **ST/\*ST/退市**（SCHEMA_VERSION 4 → 5 强制重算） | 板块 RPS / 成分股弹窗 / 候选池不再混入 ST 股票 |
| 周度推荐 | `_maybe_run_weekly` 幂等修复：周一~周四本周已生成跳过，**周五强制重新生成** | 旧逻辑按 ISO 周判幂等，周一生成后周五被误跳过，周度推荐停留周一数据 |
| 前端 | 复盘 Tab 切换时补设弹窗标题"每日复盘" | 共用一个标题，切到复盘仍显示"每日推荐股票" |
| 部署 | `web_server.py` 静态 JSON 统一加 no-cache 头；`render.js` 复盘页新增 3 个纯 SVG 图表（三档数量条形图 / 情绪周期五档指示 / 风格归因） | 手机 App 缓存旧数据、复盘纯文字不直观 |

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
| `sw_industry_cache.json` 申万板块池 | sw_industry | `fetch_date` ≤ 7 天才复用（成分名单季度调整） | 拉取失败 → 旧缓存兜底 → 无缓存回退 Tushare 行业 |
| `stock_basic_cache.json` 股票基础 | rps_calc | **industry 来自申万映射(208细分)**；name/list_date 复用旧缓存 | 申万映射失败 → Tushare stock_basic → 旧缓存回退 → 代码前缀推断 |
| `daily_cache/*.json` 日 K | rps_calc | 当日文件存在即复用 | Tushare daily 多 token 轮询 |
| `index_cache/*.json` 指数日线 | recommend | **≥ 60 条且已含最新交易日才复用**，否则重拉；**统一按日期升序存储**（Tushare 返回降序需重排） | Tushare index_daily → AKShare → 东财（失败默认安全） |
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
| rps_data.json / rps_history.json / last_result.json | 每日 | 8/14 三档各 20 板块（208 细分） |
| recommendations.json + predictions_log.json | 每日 | 8/14（21 只） |
| weekly_recommendations.json | 每周五 | 8/8 |
| daily_review.json / daily_review.md | 每日 | 8/14 |
| 各缓存（见第三节） | 按规则 | 均已含 8/14 |

---

## 五、历史更新日志（近期）

### 2026-08-15 — 每日复盘 v2 大升级（daily_review.py + render.js 双端）
- **市场定调双约束**：强度 = 变化量 + 绝对水位（diff≥+3 且高分≥20 且站上 20 日线 → 全面强；高分≥25 且允许沪指破线 → 结构性强；弱需 diff≤-5 AND 高分≤14），消除只看 diff 误判；新增高分板块数近 120 日历史分位
- **主线分级**：硬核主线（三档共振）+ 韧性主线（RPS10/20 在榜、RPS5 仅掉 1 天，不直接踢出）；梯队按内部达标率过滤（Tier1≥15% / Tier2≥10%），剔除小体量脉冲板块
- **轮动细化**：跃升仅保留 rank_change≥8 并输出最终排名；调出分类 pulse_exit(≤2天)/mainline_exit(≥5天)/normal_exit（连续天数从 history 计算，修复 out_list continuous 恒 0 的误判）；新增近 10 日反复轮动板块
- **健康度规模自适应**：扩散阈值随成分股数分档（<15 只:40% / 15-40:30% / >40:20%），大小板块公平；新增成熟疲劳（≥8 天且达标率跌破阈值）；种子分高质量/脉冲两级
- **风险分级 hint/warning/danger**：批量调出区分单日异动（hint）与连续两日累计（warning）；顶背离排除成分股<12 的小板块；动量衰减要求当前高分>15
- **清单分池 + 掉榜天数**：种子池分高质量/脉冲；主线分歧池标记 rps5_drop_days，掉榜≥3 天移入主线衰竭池；临界池增加达标率过滤
- **情绪周期复合标签**：主标签（冰点/高潮/发酵/启动/震荡）+ 附加信号（退潮信号），允许"高潮期+退潮信号"复合状态，修复优先级冲突
- **风格多标签归因**：板块可命中多风格，输出 count+pct 占比 + 未归类兜底
- **一致性校验**：RPS 推导环境 vs recommendations.json 大盘结论冲突告警
- **文档**：新增 `docs/daily_review_logic.md` 完整逻辑说明
- **验证**：本地 221 Python + 43 Node 测试全部通过；服务器 force 重跑 20260814 数据正常（中性/震荡期+退潮信号/风险 warning/韧性主线印制电路板识别）

### 2026-08-14 — 全链路剔除 ST 股票 + 周度推荐幂等修复
- **ST 剔除**：`rps_calc.get_stock_industry` 新增 `_filter_st()`，所有出口（申万板块池 / stock_basic / 缓存回退 / 前缀推断）统一剔除名称含 `ST`/`*ST`/`退` 的股票；下游板块 RPS 计算、成分股弹窗、日度/周度候选池自动不含 ST（推荐层原有 `is_st_stock` 硬否决保留，双保险）
- **SCHEMA_VERSION 4 → 5** 强制全量重算（股票池变化）
- **周度推荐幂等修复**：`_maybe_run_weekly` 旧逻辑按 ISO 周判幂等（周一生成后周五被误跳过）；改为周一~周四本周已生成则跳过、**周五强制重新生成**，并补 5 个回归测试（周一跳过/周四跳过/周五重生成/上周补跑/无板块跳过）
- **前端**：复盘 Tab 补设弹窗标题；复盘页新增 3 个 SVG 图表；web_server 静态 JSON 加 no-cache 头
- **验证**：本地 119 Python + 42 Node 测试通过；服务器重算后 rps_data.json 成分股 0 个 ST 残留

### 2026-08-14 — 板块池切换：110 大行业 → 208 申万三级细分板块
- **背景**：原看板用 Tushare `stock_basic.industry`（约 110 个一级大行业），颗粒度太粗，大行业内细分涨跌被平均，RPS 信号失真
- **数据源**：新增 `sw_industry.py` 模块，用 AKShare 免费接口 `sw_index_third_info()`（335 个申万三级）+ `index_component_sw()`（各板块成分股）构建股票→细分行业映射
- **板块池**：剔除成分股 < 8 只的僵尸板块（74 个）→ **208 个主流细分板块**，覆盖 4662 只股票；中位数成分 10 只
- **缓存**：`static/sw_industry_cache.json`（含 fetch_date，7 天刷新；拉取失败回退旧缓存）
- **SCHEMA_VERSION 3 → 4** 强制全量重算（板块池变化）
- **验证**：8/14 数据——208 行业、RPS5/RPS10/RPS20 各 20 入选（阈值 90 下前 20 名，原 110 板块为 11 个）、推荐 21 只、大盘合格（上涨占比 45%）
- **连带修复**：指数缓存 `index_cache/*.json` 被 Tushare 降序写入导致 `closes[-1]` 取到最旧日期——`recommend.fetch_index_daily` 所有写入点统一升序排序 + `daily_review.index_ma20_status` 读缓存时排序防御（双保险）
- **注意**：北交所股票（4/8/920 开头）无申万归属，不参与板块 RPS 与推荐；"种子调入"增多属细分板块正常现象，需过滤一日游噪音

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

1. **SCHEMA_VERSION 幂等**：`rps_calc.py` 顶部常量（当前 v5=股票池剔除ST/退市）。同日已生成且版本达标则跳过计算，保护 last_result 调入/调出对比基准。**改公式/输出格式/板块池/股票池必须 +1**，否则不重算。
2. **推荐与 RPS 解耦**：即使 RPS 幂等跳过，推荐流程仍独立加载 daily_map 执行，保证推荐每天生成。
3. **服务器部署**：默认只推阿里云服务器。改代码后执行 `python _upload_file.py <本地> /opt/rps-board/<远程>`，然后 `python _ssh_run.py "systemctl restart rps-board.service"`。切勿恢复 GitHub 推送。
4. **服务器必装文件**：`akshare_adapter.py` 与 `sw_industry.py` 必须与 `rps_calc.py`/`recommend.py` 同在 /opt/rps-board/（缺失则 AKShare 数据源/申万板块池不可用）。服务器需安装 akshare（≥1.18）。
5. **缓存数据不入库**：`rps_data.json` / `last_result.json` / `rps_history.json` / `index_cache/` / `sw_industry_cache.json` 等在 .gitignore 中，GitHub 分支已停用，数据只在阿里云服务器生成。
6. **测试**：改逻辑后跑 `python -m unittest discover -s tests`（194 项，Windows 下需 `PYTHONIOENCODING=utf-8` 避免 GBK emoji 报错）+ `node --test tests/test_render.js`（33 项）。
7. **大盘终止选股是设计行为**：上涨占比 < 30% 或跌停 > 50 家时输出 0 只推荐，前端显示"大盘环境不满足"，非数据故障。
8. **Tushare 免费 token 无 premium 接口权限**（fina_indicator / moneyflow / index_daily / stock_basic），全部依赖降级策略兜底，属正常预期。
9. **细分板块特性**：208 个申万三级板块中部分成分股少（8-15 只），少数大盘股可撬动板块 RPS 虚高；"调入/种子"增多且多为一两日行情，需结合板块内多数个股是否同步走强判断。
10. **ST 已全链路剔除**：`get_stock_industry` 出口统一过滤（名称含 ST/退），推荐层 `is_st_stock` 硬否决保留。若需恢复 ST 股票参与板块 RPS，删除 `_filter_st` 调用并将 SCHEMA_VERSION +1。
