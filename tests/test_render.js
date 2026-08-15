'use strict';
/*
 * 单元测试：成分股弹窗渲染函数（render.js）
 * 回归保护: 四列(买价/卖价/现量/涨速)渲染不得因空值/格式错误而显示异常
 * 运行: node --test tests/test_render.js
 */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const R = require('../render.js');

// ---------- dash: 空值占位 ----------
test('dash: 空值显示 --', () => {
    assert.equal(R.dash(null), '--');
    assert.equal(R.dash(undefined), '--');
    assert.equal(R.dash(''), '--');
});

test('dash: 有效值原样返回(含0)', () => {
    assert.equal(R.dash(0), 0);
    assert.equal(R.dash(19.92), 19.92);
    assert.equal(R.dash('600000.SH'), '600000.SH');
});

// ---------- fmtStockPct: 涨速/涨幅/换手 ----------
test('fmtStockPct: 空值显示 --', () => {
    assert.equal(R.fmtStockPct(null), '--');
    assert.equal(R.fmtStockPct(undefined), '--');
    assert.equal(R.fmtStockPct(''), '--');
    assert.equal(R.fmtStockPct('abc'), '--');
});

test('fmtStockPct: 涨速为0显示 0.00(非--, 回归保护)', () => {
    assert.equal(R.fmtStockPct(0), '0.00');
});

test('fmtStockPct: 正负号与颜色', () => {
    assert.equal(R.fmtStockPct(0.05), '<span class="pos">+0.05</span>');
    assert.equal(R.fmtStockPct(-0.07), '<span class="neg">-0.07</span>');
});

// ---------- fmtStockVol: 现量/总量 ----------
test('fmtStockVol: 空值显示 --', () => {
    assert.equal(R.fmtStockVol(null), '--');
    assert.equal(R.fmtStockVol(undefined), '--');
    assert.equal(R.fmtStockVol(''), '--');
});

test('fmtStockVol: 停牌现量为0显示0(非--)', () => {
    assert.equal(R.fmtStockVol(0), 0);
});

test('fmtStockVol: 万单位换算', () => {
    assert.equal(R.fmtStockVol(10376), '1.0万');
    assert.equal(R.fmtStockVol(5961), 5961);
    assert.equal(R.fmtStockVol(15000), '1.5万');
});

// ---------- fmtStockNum: 现价/今开/最高等 ----------
test('fmtStockNum: 空值显示 --', () => {
    assert.equal(R.fmtStockNum(null), '--');
    assert.equal(R.fmtStockNum(''), '--');
});

test('fmtStockNum: 涨跌颜色', () => {
    assert.equal(R.fmtStockNum(19.92), '<span class="pos">19.92</span>');
    assert.equal(R.fmtStockNum(-0.5), '<span class="neg">-0.5</span>');
    assert.equal(R.fmtStockNum(0), 0);
});

// ---------- stockRowHtml: 18列完整行 ----------
function countCells(html) {
    return (html.match(/<td/g) || []).length;
}

test('stockRowHtml: 输出19列(含个股详情按钮列)', () => {
    const s = {
        ts_code: '300711.SZ', name: '广哈通信', pct_chg: 3.5, price: 19.92,
        consec_up: 2, change: 0.67, bid: 19.92, ask: 19.93,
        vol: 158623, vol_now: 10376, speed: 0.05, turnover: 8.2,
        open: 19.5, high: 20.1, low: 19.3, pre_close: 19.25,
        vol_ratio: 1.61, industry: '通信设备'
    };
    const html = R.stockRowHtml(s);
    assert.equal(countCells(html), 19);
    assert.ok(html.includes('openStockDetail'), '含个股详情按钮');
});

test('stockRowHtml: 四列有值时正确渲染', () => {
    const html = R.stockRowHtml({
        ts_code: '300711.SZ', name: '广哈通信', bid: 19.92, ask: 19.93,
        vol_now: 10376, speed: 0.05
    });
    assert.ok(html.includes('<td>19.92</td>'), '买价原值');
    assert.ok(html.includes('<td>19.93</td>'), '卖价原值');
    assert.ok(html.includes('<td>1.0万</td>'), '现量万单位');
    assert.ok(html.includes('<span class="pos">+0.05</span>'), '涨速带符号');
});

test('stockRowHtml: 四列为空时显示 --(回归保护: 不再渲染空白)', () => {
    const html = R.stockRowHtml({
        ts_code: '920634.BJ', name: '新威凌', bid: null, ask: null,
        vol_now: null, speed: null
    });
    assert.ok(html.includes('<td>--</td>'), '空值显示--而非空白');
    assert.ok(!html.includes('undefined'), '不得渲染undefined');
    assert.ok(!html.includes('<td></td>'), '不得渲染空单元格');
});

test('stockRowHtml: 北交所/涨停卖价为--时行仍完整', () => {
    // 涨停封板卖一为空(ask=null) 属正常, 行输出不得报错/缺列
    const s = {
        ts_code: '600111.SH', name: '盛达资源', pct_chg: 10.0, price: 34.43,
        bid: 34.43, ask: null, vol_now: 5961, speed: 0,
        change: 3.13, vol: 100000, turnover: 5.0, open: 31.3,
        high: 34.43, low: 31.2, pre_close: 31.3, vol_ratio: 2.1, industry: '铅锌'
    };
    const html = R.stockRowHtml(s);
    assert.equal(countCells(html), 19);
    assert.ok(html.includes('<td>--</td>'), '卖价空显示--');
    assert.ok(html.includes('0.00'), '涨速0显示0.00');
});

// ---------- 周度推荐辅助 (recommend_weekly 前端) ----------

test('fmtWeeklyTags: 板块名红色/中线属性蓝色/空则--', () => {
    assert.equal(
        R.fmtWeeklyTags({ tags: '业绩增长 + 周线突破 + 半导体', sectors: ['半导体'] }),
        '<span class="tag-rec tech">业绩增长</span><span class="tag-rec tech">周线突破</span><span class="tag-rec sector">半导体</span>'
    );
    assert.equal(R.fmtWeeklyTags({ tags: '', sectors: [] }), '--');
    assert.equal(R.fmtWeeklyTags({ sectors: undefined }), '--');
});

test('fmtWeeklyMktLevel: 三档判定 good/weak/bad', () => {
    assert.equal(R.fmtWeeklyMktLevel({ level: 'good' }).cls, 'safe');
    assert.equal(R.fmtWeeklyMktLevel({ level: 'weak' }).cls, 'warn');
    assert.equal(R.fmtWeeklyMktLevel({ level: 'bad' }).cls, 'danger');
    assert.equal(R.fmtWeeklyMktLevel(undefined).cls, 'safe');  // 无数据默认安全
    assert.ok(R.fmtWeeklyMktLevel({ level: 'bad' }).title.includes('无推荐'));
});

test('fmtWeeklyFund: 净利润/同比/负债率格式化', () => {
    const txt = R.fmtWeeklyFund({ net_profit: 1.5e8, netprofit_yoy: 25.0, dt_netprofit_yoy: 18.0, debt_to_assets: 42.5 });
    assert.ok(txt.includes('净利润 1.50亿'));
    assert.ok(txt.includes('同比 25.0%'));
    assert.ok(txt.includes('负债率 42.5%'));
    // 空值不渲染 undefined
    const empty = R.fmtWeeklyFund({});
    assert.ok(!empty.includes('undefined'));
    assert.ok(empty.includes('--'));
});

test('fmtWeeklyCap: 周换手/量能/成交额/净流入格式化', () => {
    const txt = R.fmtWeeklyCap({ turnover: 12.34, vol_expand: 1.8, amount: 2.5e8, net_inflow_5d: 3e7 });
    assert.ok(txt.includes('周换手 12.3%'));
    assert.ok(txt.includes('量能 1.80x'));
    assert.ok(txt.includes('周成交额 2.5亿'));
    assert.ok(txt.includes('+0.30亿'));
    // 空值
    const empty = R.fmtWeeklyCap({});
    assert.ok(!empty.includes('undefined'));
    assert.ok(empty.includes('--'));
});

// ---------- 每日复盘渲染辅助 (daily_review 前端) ----------

test('fmtReviewNames: 字符串/对象数组混合, 超长截断', () => {
    assert.equal(R.fmtReviewNames([]), '无');
    assert.equal(R.fmtReviewNames(['黄金', '半导体']), '黄金、半导体');
    assert.equal(R.fmtReviewNames([{ name: '乳制品' }, { name: '白酒' }]), '乳制品、白酒');
    const many = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J'];
    assert.ok(R.fmtReviewNames(many).includes('等10个'));
    assert.equal(R.fmtReviewNames(many, 3), 'A、B、C 等10个');
});

test('fmtReviewSector: 精简板块条目带RPS/排名/达标率, 兼容旧RPS5', () => {
    assert.equal(R.fmtReviewSector({ name: '黄金', RPS: 100, rank: 1, ratio: 80 }),
        '黄金(RPS=100 排名1 达标80%)');
    assert.equal(R.fmtReviewSector({ name: '旧格式', RPS5: 95 }), '旧格式(RPS=95)');
    assert.equal(R.fmtReviewSector({ name: '铅锌' }), '铅锌');
});

test('fmtReviewTone: v2六档带色(全面强/结构性强/中性偏强/中性/中性偏弱/弱), 空值--', () => {
    assert.ok(R.fmtReviewTone('全面强').includes('#059669'));
    assert.ok(R.fmtReviewTone('结构性强').includes('#10b981'));
    assert.ok(R.fmtReviewTone('中性偏强').includes('#3b82f6'));
    assert.ok(R.fmtReviewTone('中性').includes('#2563eb'));
    assert.ok(R.fmtReviewTone('中性偏弱').includes('#d97706'));
    assert.ok(R.fmtReviewTone('弱').includes('#dc2626'));
    assert.ok(R.fmtReviewTone('强').includes('#059669'));
    assert.equal(R.fmtReviewTone(''), '--');
});

test('fmtReviewWarnings: [{text,level}]分级着色, 无预警绿色', () => {
    const warn = R.fmtReviewWarnings([
        { text: '批量调出预警: x', level: 'warning' },
        { text: '动量衰减预警: y', level: 'danger' },
        { text: '单日异动提示: z', level: 'hint' }
    ]);
    assert.ok(warn.includes('批量调出预警'));
    assert.ok(warn.includes('动量衰减预警'));
    assert.ok(warn.includes('#d97706'), 'warning橙色');
    assert.ok(warn.includes('#dc2626'), 'danger红色');
    assert.ok(warn.includes('#64748b'), 'hint灰色');
    const none = R.fmtReviewWarnings([]);
    assert.ok(none.includes('无风险信号'));
    assert.ok(none.includes('#059669'));
});

test('fmtReviewPhase: 主标签+附加信号带色 + 统计数字', () => {
    const html = R.fmtReviewPhase({ main: '高潮期', signal: ['退潮信号'], seed_n: 1, out_n: 2, core_n: 5, high_n: 25 });
    assert.ok(html.includes('高潮期'));
    assert.ok(html.includes('退潮信号'));
    assert.equal(R.fmtReviewPhase(null), '--');
    assert.ok(R.fmtReviewPhase({ main: '震荡期', seed_n: 0 }).includes('震荡期'));
});

test('buildReviewHtml: 六大方向齐全(v2结构), 空数据兜底', () => {
    const data = {
        update_date: '20260810',
        market: { tone: '中性偏弱', high5: 11, high10: 11, high20: 11, diff5: 0,
                  high_score_count_pct: 25, note: '上证指数站上20日线' },
        resonance: { core: [{ name: '黄金', RPS: 100, rank: 1, ratio: 80 }], tough: [],
                     pulse: [{ name: '脉冲X', RPS: 92, rank: 12, ratio: 10 }], diverge: [{ name: '乳制品' }] },
        tier: { tier1: [{ name: '黄金', rank: 1 }], tier2: [] },
        rotation: { new_in: [], rank_jump: [{ name: '中成药', rank_change: 43, rank: 10 }],
                    out_all: ['专用机械'], out_count: 1,
                    exits: [{ name: '半导体', continuous: 5, exit_type: 'mainline_exit' }], repeat: [] },
        health: { expand: [], mature: [], mature_fatigue: [], seed_high: [{ name: '医疗保健', ratio: 25 }], seed_pulse: [] },
        risk: { warnings: [{ text: '批量调出预警: x', level: 'warning' }], level: 'warning' },
        watch: { seed_high: [], seed_pulse: [], critical: [], diverge_watch: [{ name: '摩托车', drop_days: 1 }],
                 diverge_dropped: [{ name: '公共交通', drop_days: 40 }] },
        sentiment: { main: '震荡期', signal: [], seed_n: 0, out_n: 1, core_n: 1, high_n: 12 },
        style: { rps5: { 科技: { count: 3, pct: 27.3 }, 周期: { count: 2, pct: 18.2 } } },
        consistency: { conflict: false, warning: '' }
    };
    const html = R.buildReviewHtml(data);
    for (const sec of ['一、市场整体定调', '二、板块强弱梯队与主线', '三、板块轮动动向',
        '四、题材/板块健康度', '五、风险与退潮预警', '六、次日跟踪清单', '进阶观察']) {
        assert.ok(html.includes(sec), '缺少章节: ' + sec);
    }
    assert.ok(html.includes('黄金'), '硬核主线板块渲染');
    assert.ok(html.includes('硬核主线'), '韧性主线区分');
    assert.ok(html.includes('调出分类'), '调出分类渲染');
    assert.ok(html.includes('摩托车'), '主线分歧池渲染');
    assert.ok(html.includes('公共交通'), '主线衰竭池渲染');
    assert.ok(html.includes('高危') || html.includes('警告'), '风险等级渲染');
    assert.ok(!html.includes('undefined'), '不得渲染undefined');
});

test('buildReviewHtml: 一致性冲突告警展示', () => {
    const data = {
        update_date: '20260810',
        market: { tone: '结构性强', high5: 25, high10: 25, high20: 25, diff5: 6 },
        resonance: { core: [], tough: [], pulse: [], diverge: [] },
        tier: { tier1: [], tier2: [] },
        rotation: { new_in: [], rank_jump: [], out_all: [], out_count: 0, exits: [], repeat: [] },
        health: { expand: [], mature: [], mature_fatigue: [], seed_high: [], seed_pulse: [] },
        risk: { warnings: [], level: 'safe' },
        watch: { seed_high: [], seed_pulse: [], critical: [], diverge_watch: [], diverge_dropped: [] },
        sentiment: { main: '震荡期', signal: [], seed_n: 0, out_n: 0, core_n: 1, high_n: 25 },
        style: { rps5: {} },
        consistency: { conflict: true, warning: '一致性校验: RPS 推导环境偏强, 结论冲突' }
    };
    const html = R.buildReviewHtml(data);
    assert.ok(html.includes('一致性校验'), '一致性冲突告警');
});

test('buildReviewHtml: 无数据兜底提示', () => {
    assert.ok(R.buildReviewHtml(null).includes('暂无复盘数据'));
    assert.ok(R.buildReviewHtml({}).includes('暂无复盘数据'));
});

// ===== 日度推荐「预测化」格式化 =====
test('fmtPredUpProb: 空值显示 --', () => {
    assert.equal(R.fmtPredUpProb(null), '--');
    assert.equal(R.fmtPredUpProb(undefined), '--');
    assert.equal(R.fmtPredUpProb(''), '--');
});

test('fmtPredUpProb: 三档配色(≥68强红/58-67中橙/<58弱灰)', () => {
    assert.ok(R.fmtPredUpProb(72).includes('class="pos"'));
    assert.ok(R.fmtPredUpProb(65).includes('class="pred-mid"'));
    assert.ok(R.fmtPredUpProb(50).includes('class="pred-low"'));
    assert.ok(R.fmtPredUpProb(72).includes('72%'));
});

test('fmtPredGain: 中值+区间, 空值兜底', () => {
    const html = R.fmtPredGain({ expected_gain: 7.0, gain_range: [3.9, 10.2] });
    assert.ok(html.includes('7.0%'));
    assert.ok(html.includes('3.9~10.2'));
    assert.equal(R.fmtPredGain(null), '--');
    assert.equal(R.fmtPredGain({}), '--');
});

test('fmtPredGain: 无区间时按中值±45%兜底', () => {
    const html = R.fmtPredGain({ expected_gain: 6.0 });
    assert.ok(html.includes('3.3~8.7'));
});

test('fmtPredStop: 负值红色, 空值兜底', () => {
    const html = R.fmtPredStop({ stop_loss: -4.5 });
    assert.ok(html.includes('-4.5%'));
    assert.ok(html.includes('class="neg"'));
    assert.equal(R.fmtPredStop(null), '--');
    assert.equal(R.fmtPredStop({}), '--');
});

test('fmtPredConf: 高/中/低三档徽标, 未知值--', () => {
    assert.ok(R.fmtPredConf('高').includes('pred-conf high'));
    assert.ok(R.fmtPredConf('中').includes('pred-conf mid'));
    assert.ok(R.fmtPredConf('低').includes('pred-conf low'));
    assert.equal(R.fmtPredConf('未知'), '--');
});

test('buildPredictionBox: 无预测字段返回空串', () => {
    assert.equal(R.buildPredictionBox(null), '');
    assert.equal(R.buildPredictionBox({}), '');
    assert.equal(R.buildPredictionBox({ name: 'X' }), '');
});

test('buildPredictionBox: 完整预测内容渲染', () => {
    const s = {
        prediction: {
            horizon: 3, up_prob: 72, expected_gain: 7.0, gain_range: [3.9, 10.2],
            stop_loss: -4.5, confidence: '高', regime: '震荡市',
            logic: ['三档RPS共振', '主力资金连续流入', '突破20日新高+放量+均线多头'],
            basis: '基于历史动量统计',
            factors: { momentum: 66, technical: 62, capital: 60, sector: 70, sentiment: 56, fundamental: 57 }
        }
    };
    const html = R.buildPredictionBox(s);
    assert.ok(html.includes('预测（3日内）'), '预测周期');
    assert.ok(html.includes('72%'), '上涨概率');
    assert.ok(html.includes('止损位'), '止损位');
    assert.ok(html.includes('置信度'), '置信度');
    assert.ok(html.includes('pred-conf high'), '高置信');
    assert.ok(html.includes('三档RPS共振'), '预测逻辑');
    assert.ok(html.includes('动量'), '六维概率');
    assert.ok(html.includes('基于历史动量统计'), '依据');
    assert.ok(!html.includes('undefined'), '不得渲染undefined');
});

// ===== 复盘图表 (纯SVG手绘) =====
test('_escTxt: HTML 转义 & < > 字符', () => {
    assert.equal(R._escTxt('<b>&</b>'), '&lt;b&gt;&amp;&lt;/b&gt;');
    assert.equal(R._escTxt(null), '');
    assert.equal(R._escTxt('黄金'), '黄金');
});

test('reviewChartBars: 输出含 <svg>/<rect>/<text> 条形图', () => {
    const html = R.reviewChartBars([{ label: 'RPS5', value: 20 }, { label: 'RPS10', value: 15 }], { title: '三档' });
    assert.ok(html.includes('<svg'), 'SVG容器');
    assert.ok(html.includes('<rect'), '条形');
    assert.ok(html.includes('RPS5'), '标签');
    assert.ok(html.includes('20.0'), '数值格式化');
    assert.ok(html.includes('三档'), '标题');
    assert.ok(!html.includes('undefined'), '不得渲染undefined');
});

test('reviewChartBars: 空数据返回空串', () => {
    assert.equal(R.reviewChartBars(null), '');
    assert.equal(R.reviewChartBars([]), '');
});

test('reviewChartBars: 自定义fmt与color回调生效', () => {
    const html = R.reviewChartBars([{ label: '科技', value: 3 }], {
        title: '风格', max: 3, color: () => '#3b82f6', fmt: v => v + '个'
    });
    assert.ok(html.includes('3个'), '自定义格式化');
    assert.ok(html.includes('#3b82f6'), '自定义颜色');
});

test('reviewChartPhase: 激活档高亮 + 统计信息, 未知档无高亮', () => {
    const html = R.reviewChartPhase({ main: '高潮期', signal: ['退潮信号'], seed_n: 2, out_n: 3, core_n: 5, high_n: 25 });
    assert.ok(html.includes('情绪周期'), '标题');
    assert.ok(html.includes('高潮期'), '阶段名');
    assert.ok(html.includes('退潮信号'), '附加信号');
    assert.ok(html.includes('box-shadow'), '激活档高亮(仅激活格带阴影)');
    assert.ok(html.includes('种子 <b>2</b>'), '种子统计');
    assert.ok(html.includes('调出 <b>3</b>'), '调出统计');
    // 兼容旧 phase 字段 + 未知档(退潮期已改为附加信号, 非主标签)
    const legacy = R.reviewChartPhase({ phase: '发酵期', seed_n: 0 });
    assert.ok(legacy.includes('box-shadow'), '旧phase字段兼容');
    const unknown = R.reviewChartPhase({ phase: '退潮期', seed_n: 0 });
    assert.ok(!unknown.includes('box-shadow'), '未知档不高亮');
});

test('reviewChartPhase: 无数据返回空串', () => {
    assert.equal(R.reviewChartPhase(null), '');
    assert.equal(R.reviewChartPhase({}), '');
});

test('reviewChartStyle: 新结构{count,pct}按数量降序取前6, 空数据空串', () => {
    const html = R.reviewChartStyle({ rps5: { 科技: { count: 5, pct: 45.5 }, 周期: { count: 3, pct: 27.3 },
                                              消费: { count: 2, pct: 18.2 }, 金融: { count: 1, pct: 9.1 } } });
    assert.ok(html.includes('风格归因'), '标题');
    assert.ok(html.includes('科技'), '最大风格');
    assert.ok(html.includes('5个'), '数量格式化');
    assert.ok(html.includes('45.5%'), '占比格式化');
    assert.equal(R.reviewChartStyle(null), '');
    assert.equal(R.reviewChartStyle({}), '');
    assert.equal(R.reviewChartStyle({ rps5: {} }), '');
});

test('reviewChartMkt: 三档数量条形图 + 总计, 空数据空串', () => {
    const html = R.reviewChartMkt({ high5: 20, high10: 15, high20: 12 }, 208);
    assert.ok(html.includes('三档入选数量'), '标题');
    assert.ok(html.includes('共208板块'), '总计');
    assert.ok(html.includes('RPS5'), '档位标签');
    assert.equal(R.reviewChartMkt(null), '');
});

test('buildReviewHtml: 图文集成(市场定调含图表, 进阶含情绪周期图)', () => {
    const data = {
        update_date: '20260814', total_industries: 208,
        market: { tone: '中性', high5: 20, high10: 20, high20: 20, diff5: 0, note: '上证指数站上20日线' },
        resonance: { core: [], tough: [], pulse: [], diverge: [] },
        tier: { tier1: [], tier2: [] },
        rotation: { new_in: [], rank_jump: [], out_all: [], out_count: 0, exits: [], repeat: [] },
        health: { expand: [], mature: [], mature_fatigue: [], seed_high: [], seed_pulse: [] },
        risk: { warnings: [], level: 'safe' },
        watch: { seed_high: [], seed_pulse: [], critical: [], diverge_watch: [], diverge_dropped: [] },
        sentiment: { main: '发酵期', signal: [], seed_n: 0, out_n: 0, core_n: 1, high_n: 20 },
        style: { rps5: { 科技: { count: 3, pct: 27.3 } } },
        consistency: { conflict: false, warning: '' }
    };
    const html = R.buildReviewHtml(data);
    assert.ok(html.includes('<svg'), '市场定调卡含三档条形图');
    assert.ok(html.includes('共208板块'), '板块总数');
    assert.ok(html.includes('情绪周期'), '进阶观察含情绪周期');
    assert.ok(html.includes('发酵期'), '情绪周期激活档');
    assert.ok(html.includes('风格归因'), '进阶观察含风格图');
    assert.ok(!html.includes('undefined'), '不得渲染undefined');
});

// ---------- fmtEarlyNum: 早期数字格式化 ----------
test('fmtEarlyNum: 空值显示 --', () => {
    assert.equal(R.fmtEarlyNum(null), '--');
    assert.equal(R.fmtEarlyNum(undefined), '--');
    assert.equal(R.fmtEarlyNum(''), '--');
    assert.equal(R.fmtEarlyNum('abc'), '--');
});

test('fmtEarlyNum: 保留指定位数', () => {
    assert.equal(R.fmtEarlyNum(88.456, 0), '88');
    assert.equal(R.fmtEarlyNum(12.3456, 1), '12.3');
    assert.equal(R.fmtEarlyNum(5, 2), '5.00');
});

// ---------- fmtEarlyTags: 早期推荐标签 ----------
test('fmtEarlyTags: 平台刚启动绿色/板块红色/属性蓝色', () => {
    const s = { tags: '平台刚启动 + 短线优选 + 半导体 + 均线多头', sectors: ['半导体'] };
    const html = R.fmtEarlyTags(s);
    assert.ok(html.includes('tag-rec platform'), '平台刚启动用 platform 样式');
    assert.ok(html.includes('tag-rec sector'), '板块名用 sector 样式');
    assert.ok(html.includes('tag-rec tech'), '属性标签用 tech 样式');
});

test('fmtEarlyTags: 空标签显示 --', () => {
    assert.equal(R.fmtEarlyTags({ tags: '', sectors: [] }), '--');
    assert.equal(R.fmtEarlyTags({ sectors: undefined }), '--');
});

// ---------- fmtEarlyMktLevel: 早期大盘两档 ----------
test('fmtEarlyMktLevel: 合格/不满足两档判定', () => {
    assert.equal(R.fmtEarlyMktLevel({ is_safe: true }).cls, 'safe');
    assert.equal(R.fmtEarlyMktLevel({ is_safe: false }).cls, 'danger');
    assert.equal(R.fmtEarlyMktLevel(undefined).cls, 'safe'); // 无数据默认安全
    assert.ok(R.fmtEarlyMktLevel({ is_safe: false }).title.includes('暂无早期'));
});

// ---------- fmtEarlyDetail: 早期日度详情 ----------
test('fmtEarlyDetail: 含潜力板块/启动形态/早期指标/加减分', () => {
    const s = {
        sector_count: 2, sectors: ['半导体', '光刻机'],
        early: { platform_start: true, rps50: 82.4, chg10: 12.3, ma60_dev: 5.2,
                 gain10_deduct: 0, ma60_dev_deduct: 0, rps50_over_deduct: 0, platform_bonus: 4 },
        fundamental: { net_profit: 1.2e8, debt_to_assets: 35.5 },
        capital: { turnover: 6.5, vol_ratio: 1.8 },
        technical: { hit_count: 3, hits: ['均线多头', 'MACD金叉'] },
        score_sector: 30, score_fundamental: 15, score_capital: 15, score_technical: 10
    };
    const html = R.fmtEarlyDetail(s);
    assert.ok(html.includes('平台刚启动'), '启动形态标记');
    assert.ok(html.includes('RPS50'), '早期指标');
    assert.ok(html.includes('82'), 'RPS50 数值');
    assert.ok(html.includes('平台启动 +4'), '平台加分显示');
    assert.ok(html.includes('分项得分'), '分项得分');
});

test('fmtEarlyDetail: 空记录返回空串 / 惩罚项显示', () => {
    assert.equal(R.fmtEarlyDetail(null), '');
    const html = R.fmtEarlyDetail({
        sectors: [], early: { platform_start: false, chg10: 30.5, ma60_dev: 50.2, rps50: 95,
                               gain10_deduct: 12, ma60_dev_deduct: 10, rps50_over_deduct: 8, platform_bonus: 0 },
        fundamental: {}, capital: {}, technical: {}
    });
    assert.ok(html.includes('涨幅惩罚 -12'), '近10日涨幅惩罚');
    assert.ok(html.includes('MA60偏离 -10'), 'MA60偏离惩罚');
    assert.ok(html.includes('RPS50过高 -8'), 'RPS50过高惩罚');
    assert.ok(html.includes('普通'), '非平台形态显示普通');
});

// ---------- fmtWeeklyEarlyDetail: 早期周度详情 ----------
test('fmtWeeklyEarlyDetail: 含近8周涨幅与周线指标', () => {
    const s = {
        sector_count: 1, sectors: ['半导体'],
        chg8_week: 30.2,
        fundamental: { net_profit: 2e8, netprofit_yoy: 25.0, dt_netprofit_yoy: 20.0, debt_to_assets: 30.0 },
        capital: { turnover: 5.5, vol_expand: 1.5, amount: 8e8, net_inflow_5d: 1e8 },
        technical: { hit_count: 2, hits: ['周线突破'] },
        score_sector: 25, score_fundamental: 15, score_capital: 15, score_technical: 10
    };
    const html = R.fmtWeeklyEarlyDetail(s);
    assert.ok(html.includes('近8周涨幅'), '近8周涨幅');
    assert.ok(html.includes('30.2'), '近8周涨幅数值');
    assert.ok(html.includes('周线达标'), '周线达标');
    assert.equal(R.fmtWeeklyEarlyDetail(null), '');
});

// ---------- 个股详情弹窗渲染函数 (fmtDetail*) ----------

test('fmtDetailNum: 空值显示 --, 有效值保留指定位数', () => {
    assert.equal(R.fmtDetailNum(null), '--');
    assert.equal(R.fmtDetailNum(''), '--');
    assert.equal(R.fmtDetailNum(undefined, 1), '--');
    assert.equal(R.fmtDetailNum('abc'), '--');
    assert.equal(R.fmtDetailNum(21.937), '21.94');
    assert.equal(R.fmtDetailNum(21.937, 1), '21.9');
    assert.equal(R.fmtDetailNum(0), '0.00');
});

test('clsColor: A股红涨绿跌, 0/空返回空类', () => {
    assert.equal(R.clsColor(0.14), 'pos');
    assert.equal(R.clsColor(-1.5), 'neg');
    assert.equal(R.clsColor(0), '');
    assert.equal(R.clsColor(null), '');
    assert.equal(R.clsColor(''), '');
});

test('fmtDetailPct: 带符号百分比, 空值--', () => {
    assert.equal(R.fmtDetailPct(0.14), '+0.14%');
    assert.equal(R.fmtDetailPct(-2.5), '-2.50%');
    assert.equal(R.fmtDetailPct(0), '0.00%');
    assert.equal(R.fmtDetailPct(null), '--');
});

test('fmtWan: 亿/万/原值 格式化', () => {
    assert.equal(R.fmtWan(1.799e10), '179.90亿');
    assert.equal(R.fmtWan(4.72e8), '4.72亿');
    assert.equal(R.fmtWan(4.72e7), '4720.0万');
    assert.equal(R.fmtWan(12345), '1.2万');
    assert.equal(R.fmtWan(999), '999');
    assert.equal(R.fmtWan(null), '--');
});

test('fmtDetailHeader: 名称/代码/现价/涨跌/十格数据齐全', () => {
    const rt = {
        name: '华钰矿业', code: '601020.SH',
        price: 21.94, change: 0.03, changePercent: 0.14,
        open: 21.82, high: 22.23, low: 21.5, preClose: 21.91,
        volumeRatio: 0.52, turnover: 2.63,
        totalAmount: 4.72e8, totalMarket: 1.799e10,
        pe_ttm: 147.18, afterPrice: 21.94,
    };
    const html = R.fmtDetailHeader(rt);
    assert.ok(html.includes('华钰矿业'), '名称');
    assert.ok(html.includes('601020.SH'), '代码');
    assert.ok(html.includes('21.94'), '现价');
    assert.ok(html.includes('pos'), '涨=红色类');
    assert.ok(html.includes('今开'), '今开');
    assert.ok(html.includes('最高'), '最高');
    assert.ok(html.includes('最低'), '最低');
    assert.ok(html.includes('量比'), '量比');
    assert.ok(html.includes('换手'), '换手');
    assert.ok(html.includes('总额'), '总额');
    assert.ok(html.includes('总市值'), '总市值');
    assert.ok(html.includes('市盈'), '市盈');
    assert.ok(html.includes('盘后价'), '盘后价');
    assert.ok(html.includes('179.90亿'), '总市值格式化');
    assert.equal(R.fmtDetailHeader(null), '');
});

test('fmtDetailScore: 环形SVG评分 + 打败比例 + 排名 + AI解盘', () => {
    const sc = {
        totalScore: 2.83,
        beatStock: '32.04%',
        industryRank: { cur: 115, total: 139 },
        marketRank: { cur: 3768, total: 5543 },
        aiText: '财报显示该股利润水平一般',
    };
    const html = R.fmtDetailScore(sc);
    assert.ok(html.includes('<svg'), '环形SVG');
    assert.ok(html.includes('32.04%'), '打败比例');
    assert.ok(html.includes('115'), '行业排名');
    assert.ok(html.includes('139'), '行业总数');
    assert.ok(html.includes('3768'), '市场排名');
    assert.ok(html.includes('财报显示该股利润水平一般'), 'AI解盘');
    assert.equal(R.fmtDetailScore(null), '');
});

test('fmtDetailRisk: 风险标签与亮点标签分别渲染', () => {
    const rh = { riskTags: ['流动性风险', '高管人员偏少'], highlightTags: ['多位牛散持股'] };
    const html = R.fmtDetailRisk(rh);
    assert.ok(html.includes('流动性风险'), '风险标签');
    assert.ok(html.includes('多位牛散持股'), '亮点标签');
    assert.ok(html.includes('tag-pill risk'), '风险样式');
    assert.ok(html.includes('tag-pill good'), '亮点样式');
    assert.equal(R.fmtDetailRisk(null), '');
});

test('fmtDetailBacktest: 单日上涨比率 + 平均涨幅', () => {
    const bt = { days: 120, riseRate: '57.14%', avgIncrease: '1.29%' };
    const html = R.fmtDetailBacktest(bt);
    assert.ok(html.includes('120'), '天数');
    assert.ok(html.includes('57.14%'), '上涨比率');
    assert.ok(html.includes('1.29%'), '平均涨幅');
    assert.equal(R.fmtDetailBacktest(null), '');
});

test('fmtDetailImpression: 个股/行业/沪深300 阶段涨幅对比', () => {
    const imp = { stageRise: { stock: '16.58%', industry: '8.37%', hs300: '-2.53%' } };
    const html = R.fmtDetailImpression(imp);
    assert.ok(html.includes('16.58%'), '个股涨幅');
    assert.ok(html.includes('8.37%'), '行业涨幅');
    assert.ok(html.includes('-2.53%'), '沪深300');
    assert.equal(R.fmtDetailImpression(null), '');
});

test('fmtDetailTechbao: K线形态/筹码成本区/获利比例', () => {
    const tb = { klineForm: '平台突破', chip: { avgCost: '18.94-25.34', profitRate: '43.89%' }, yujia: ['业绩预增50%'] };
    const html = R.fmtDetailTechbao(tb);
    assert.ok(html.includes('平台突破'), 'K线形态');
    assert.ok(html.includes('18.94-25.34'), '筹码成本区');
    assert.ok(html.includes('43.89%'), '获利比例');
    assert.ok(html.includes('业绩预增50%'), '业绩预增标签');
    assert.equal(R.fmtDetailTechbao(null), '');
});

test('fmtDetailFundtrack: 市场/北上/两融/龙虎 四卡片', () => {
    const ft = {
        market: { netInTxt: '1136.0万', mainNetTxt: '-1860.7万' },
        north: { netBuy: '-350.21万' },
        margin: { marginNet: '-1141.4万', total: '16.16亿' },
        dragon: { lastDate: '2026-06-16' },
    };
    const html = R.fmtDetailFundtrack(ft);
    assert.ok(html.includes('市场资金'), '市场资金卡片');
    assert.ok(html.includes('北上资金'), '北上卡片');
    assert.ok(html.includes('两融余额'), '两融卡片');
    assert.ok(html.includes('龙虎榜单'), '龙虎卡片');
    assert.ok(html.includes('-1860.7万'), '主力净流入');
    assert.equal(R.fmtDetailFundtrack(null), '');
});

test('fmtDetailZhuoyao: 当日主力/游资/散户净流入卡片 + 无信号', () => {
    const row = { date: '2026-08-14', sanhu: -1136e4, youzi: 1189e4, zhuil: 3005e4, addPosition: false, sellSignal: false };
    const html = R.fmtDetailZhuoyao(row);
    assert.ok(html.includes('主力捉妖 · 当日资金2026-08-14'), '标题含日期');
    assert.ok(html.includes('主力'), '主力行');
    assert.ok(html.includes('游资'), '游资行');
    assert.ok(html.includes('散户'), '散户行');
    assert.ok(html.includes('3005.0万'), '主力净流入正数');
    assert.ok(html.includes('class="pos"'), '正流入红色');
    assert.ok(html.includes('class="neg"'), '负流入绿色');
    assert.ok(html.includes('无信号'), '默认无信号');
    assert.equal(R.fmtDetailZhuoyao(null), '');
});

test('fmtDetailZhuoyao: 加仓/卖出信号标记', () => {
    const add = { date: '2026-08-14', sanhu: 100, youzi: 200, zhuil: 300, addPosition: true, sellSignal: false };
    const addHtml = R.fmtDetailZhuoyao(add);
    assert.ok(addHtml.includes('▲ 加仓信号'), '加仓信号');
    assert.ok(addHtml.includes('class="zy-sign pos"'), '加仓信号红色');
    const sell = { date: '2026-08-14', sanhu: 100, youzi: 200, zhuil: -300, addPosition: false, sellSignal: true };
    const sellHtml = R.fmtDetailZhuoyao(sell);
    assert.ok(sellHtml.includes('▼ 卖出信号'), '卖出信号');
    assert.ok(sellHtml.includes('class="zy-sign neg"'), '卖出信号绿色');
});

test('fmtDetailZhuoyao: 空值字段显示 -- 不加色', () => {
    const html = R.fmtDetailZhuoyao({ date: '2026-08-14', sanhu: null, youzi: undefined, zhuil: 0 });
    assert.ok(html.includes('--'), '空值显示--');
    assert.ok(html.includes('0'), '0值正常显示');
});

test('fmtDetailValue: 当前PE + PE历史迷你柱状图', () => {
    const va = {
        pePercent: 2.4,
        peHistory: [
            { time: '2025-08-15', value: 80 },
            { time: '2025-09-15', value: 45 },
            { time: '2025-10-15', value: 12 },
        ],
        growth: ['净利润增长25%'],
    };
    const html = R.fmtDetailValue(va);
    assert.ok(html.includes('2.40'), '当前PE');
    assert.ok(html.includes('pe-bar'), 'PE柱状图');
    assert.ok(html.includes('净利润增长25%'), '成长分析');
    assert.equal(R.fmtDetailValue(null), '');
});

test('fmtDetailNews: 资讯公告列表', () => {
    const nw = { news: [{ date: '2026-08-14', title: '公司发布半年报' }] };
    const html = R.fmtDetailNews(nw);
    assert.ok(html.includes('公司发布半年报'), '资讯标题');
    assert.ok(R.fmtDetailNews({ news: [] }).includes('暂无最新公告'), '空列表');
    assert.ok(R.fmtDetailNews(null).includes('暂无最新公告'), 'null');
});

test('fmtDetailF10: 名称/代码/行业/主营业务', () => {
    const f10 = { name: '华钰矿业', code: '601020.SH', industry: '有色', listDate: '2017-08-30', mainBusiness: '金属冶炼' };
    const html = R.fmtDetailF10(f10);
    assert.ok(html.includes('华钰矿业'), '名称');
    assert.ok(html.includes('有色'), '行业');
    assert.ok(html.includes('金属冶炼'), '主营业务');
    assert.equal(R.fmtDetailF10(null), '');
});

test('fmtDetailTechnical: MACD/KDJ/RSI/BOLL 当前值', () => {
    const ind = {
        macd: [{ dif: 0.12, dea: 0.08, hist: 0.04 }],
        kdj: [{ k: 50.1, d: 45.2, j: 60.0 }],
        rsi: [55.5],
        boll: [{ upper: 23.1, mid: 21.5, lower: 19.9 }],
    };
    const html = R.fmtDetailTechnical(ind);
    assert.ok(html.includes('MACD'), 'MACD');
    assert.ok(html.includes('KDJ'), 'KDJ');
    assert.ok(html.includes('RSI'), 'RSI');
    assert.ok(html.includes('BOLL'), 'BOLL');
    assert.ok(html.includes('0.120'), 'DIF值');
    assert.equal(R.fmtDetailTechnical(null), '');
});

