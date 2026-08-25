# RPS 板块自动化每日复盘逻辑 daily_review.py

> 所有数据源全部复用已有 JSON，不新增外部数据源。
> 版本：v2（2026-08-15，双约束定调 / 硬核+韧性主线 / 调出分类 / 规模自适应健康度 / 风险分级 / 复合情绪标签 / 风格多标签 / 一致性校验）

## 数据源

| 文件 | 用途 |
|---|---|
| `static/rps_data.json` | 三档入选/调出板块、连续上榜天数、60 日 RPS50 达标数 |
| `static/rps_history.json` | 全行业 RPS 历史数据，用于昨日对比、历史分位、动量衰减、反复轮动、掉榜天数 |
| `static/index_cache/000001.SH.json` | 上证指数 20 日线状态 |
| `static/recommendations.json` | 推荐系统大盘环境结论，用于一致性校验 |

执行：由 `rps_calc.py` 自动调用，幂等执行（校验输出文件内日期字段）；`--force` 参数强制重跑。
输出：`static/daily_review.json`（结构化数据，前端渲染）、`static/daily_review.md`（复盘初稿文本）。

## 六大分析模块

### ① market_tone 市场整体定调（变化量 + 绝对水位双约束）

统计今日 RPS5/RPS10/RPS20≥90 板块数量，`diff = 今日高分板块数 − 昨日高分板块数`。

| 结果 | 条件 |
|---|---|
| **全面强** | diff≥+3 且 今日高分≥20 且 上证指数站上 20 日线 |
| **结构性强** | 高分板块≥25（沪指破 20 日线也允许，解决指数失真） |
| **弱** | (diff≤-5 且 高分≤14) 或 三档合计≤10 |
| **中性偏强** | diff>0 且 高分≥15 |
| **中性偏弱** | diff<0 或 高分≤14 |
| **中性** | 其余 |

附加输出：`high_score_count_pct`（高分板块数在近 120 个交易日历史分位，取自 rps_history.json）、指数 20 日线状态、`note` 说明。

### ② resonance_analysis + tier_analysis 主线识别

- **硬核主线**：RPS5 ∩ RPS10 ∩ RPS20 三档同时上榜
- **韧性主线**：RPS10 ∩ RPS20 在榜，RPS5 仅掉 1 个交易日（昨日 RPS5 在榜），允许短期分歧不踢出主线
- **短期脉冲**：仅 RPS5 上榜
- **主线分歧**：(n10 ∪ n20) − n5

梯队过滤（剔除小体量虚假强势板块，按板块内部达标率 RPS50≥90 占比）：

| 梯队 | 排名 | 达标率门槛 |
|---|---|---|
| 第一梯队 | 1~10 | ≥15% |
| 第二梯队 | 11~30 | ≥10% |

输出：核心主线数量（`mainline_count`）、硬核/韧性主线列表（名称、连续上榜天数、达标率）。

### ③ rotation_analysis 轮动解读

1. **新调入板块**：连续上榜天数=1
2. **排名大幅跃升 Top5**：rank_change≥8，按跃升幅度降序，输出名次变化 + 最终排名
3. **调出分类**（调出前连续在榜天数从 history 计算，out_list 的 continuous 恒为 0）：
   - `pulse_exit`：上榜≤2 天调出（脉冲行情）
   - `mainline_exit`：上榜≥5 天调出（老主线终结）
   - `normal_exit`：其余
4. **反复轮动**：近 10 个交易日 RPS5 在榜状态翻转≥3 次的板块（高轮动低持续性）

### ④ health_analysis 健康度验证（规模自适应）

板块内部达标率 = `rps50_above_90 / stock_count`，阈值随成分股数量自适应：

| 成分股数量 | 扩散健康阈值 |
|---|---|
| <15 只 | ≥40% |
| 15~40 只 | ≥30% |
| >40 只 | ≥20% |

标签：
1. **高质量种子**：首日入选 + 满足扩散健康阈值
2. **脉冲种子**：首日入选但扩散不足
3. **成熟主线**：连续上榜≥5 天且排名≤20
4. **成熟疲劳**：连续上榜≥8 天且今日达标率跌破规模阈值

### ⑤ risk_analysis 风险预警（分级 hint / warning / danger）

| 预警 | 条件 | 级别 |
|---|---|---|
| 批量调出 | 连续两日同风格累计调出≥3（今日+昨日都有） | warning |
| 批量调出异动 | 同风格单日调出≥3 | hint |
| 高位顶背离 | 成分股≥12 且 排名 Top10 且 内部达标率<15% | warning |
| 动量衰减 | 高分板块数量连续 3 日递减 且 当前高分>15（低位回落不重复告警） | warning |

汇总级别：2+ warning/danger → `danger`；1 个 warning → `warning`；仅 hint → `hint`；无 → `safe`。
输出 `warnings: [{text, level}]` 供前端按级别着色。

### ⑥ watchlist 次日跟踪清单

- **高质量种子池**：首日入选 + 扩散达标
- **脉冲种子池**：首日入选但扩散不足
- **临界晋级池**：RPS5∈[88, 90)，排名连续 2 日上升，且内部达标率达标（过滤只有 2 只股票拉上去的虚假临界板块）
- **主线分歧池**：RPS20 在榜、RPS5 掉榜，标记 `rps5_drop_days`；掉榜≥3 天 → 移出跟踪池进入 `diverge_dropped`（主线衰竭）

## 进阶分析

### 情绪周期（主标签 + 附加信号，允许复合状态）

主标签：冰点期 / 高潮期 / 发酵期 / 启动期 / 震荡期
- 冰点期：高分板块 ≤8
- 高潮期：高分板块 ≥25
- 发酵期：硬核主线≥3 且 高质量种子≥4
- 启动期：高质量种子≥4
- 震荡期：其余

附加信号（可叠加，`signal: [...]`）：
- **退潮信号**：(连续两日同风格调出≥3 且 硬核主线≤3) 或 动量衰减
- 输出复合场景示例：`{"main": "高潮期", "signal": ["退潮信号"]}`（高潮后资金出逃）

### 风格归因

STYLE_KEYWORDS 8 类：科技 / 消费 / 医药 / 金融 / 周期 / 制造 / 新能源 / 公用。
- 板块支持**多标签**匹配（如储能命中新能源+科技）
- 匹配失败归入**未归类**
- 输出各风格命中板块数 + 占比（`{style: {count, pct}}`）

### 一致性校验

对比 recommendations.json 大盘环境结论：
- RPS 推导偏强（全面强/结构性强/中性偏强）但推荐系统判定大盘不合格 → 冲突告警
- RPS 推导偏弱（弱/中性偏弱）但推荐系统判定大盘合格 → 冲突告警
- 输出 `consistency: {conflict, warning}`

## 输出格式

- **daily_review.json**：结构化数据（market / resonance / tier / rotation / health / risk / watch / sentiment / style / consistency），风险等级、情绪主标签+附加信号、种子分级、掉榜天数等字段供前端颜色渲染。
- **daily_review.md**：复盘初稿，结构：今日市场总览 | 情绪周期 | 大盘强度 | 主线梯队 | 轮动观察 | 板块健康扫描 | 风险预警 | 次日跟踪清单 | 风格分布。

## 运行约束

- 幂等执行：校验输出文件内日期字段（`update_date`），避免旧文件导致跳过；`--force` 强制重跑
- 不新增任何外部数据源，全部由已有 JSON 派生
- md 为初稿，保留人工编辑空间

## 关键配置（CONFIG_REVIEW）

| 配置 | 值 | 含义 |
|---|---|---|
| strong_min_diff / strong_min_high | 3 / 20 | 全面强：diff≥+3 且 高分≥20 |
| structural_high | 25 | 结构性强：高分≥25 |
| weak_min_diff / weak_max_high / weak_total_max | -5 / 14 / 10 | 弱判定 |
| tier1_min_ratio / tier2_min_ratio | 0.15 / 0.10 | 梯队达标率门槛 |
| expand_ratio_small/mid/large | 0.40/0.30/0.20 | 规模自适应扩散阈值 |
| mature_fatigue_days | 8 | 成熟疲劳天数 |
| batch_style_n | 3 | 同风格批量调出阈值 |
| decay_days / decay_high_floor | 3 / 15 | 动量衰减 |
| high_ice / high_burst / seed_phase | 8 / 25 / 4 | 情绪周期水位 |
| jump_min_gain / pulse_continuous / mainline_continuous | 8 / 2 / 5 | 跃升与调出分类 |
| repeat_window / repeat_flips | 10 / 3 | 反复轮动 |
| hist_percentile_days | 120 | 历史分位窗口 |
