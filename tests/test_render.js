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

test('stockRowHtml: 输出18列(名称列即个股超链接, 无独立按钮列)', () => {
    const s = {
        ts_code: '300711.SZ', name: '广哈通信', pct_chg: 3.5, price: 19.92,
        consec_up: 2, change: 0.67, bid: 19.92, ask: 19.93,
        vol: 158623, vol_now: 10376, speed: 0.05, turnover: 8.2,
        open: 19.5, high: 20.1, low: 19.3, pre_close: 19.25,
        vol_ratio: 1.61, industry: '通信设备'
    };
    const html = R.stockRowHtml(s);
    assert.equal(countCells(html), 18);
    assert.ok(html.includes('stock-link'), '名称列为超链接');
    assert.ok(html.includes("onclick=\"openStockDetail('300711.SZ')\""), '名称超链接打开个股详情');
    assert.ok(html.includes('广哈通信'), '名称保留');
    assert.ok(!html.includes('>个股<'), '不再有独立个股按钮');
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
    assert.equal(countCells(html), 18);
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

test('fmtReviewTone: v2六档带色(浅色主题白底深字), 空值--', () => {
    assert.ok(R.fmtReviewTone('全面强').includes('#059669'));
    assert.ok(R.fmtReviewTone('结构性强').includes('#059669'));
    assert.ok(R.fmtReviewTone('中性偏强').includes('#0284c7'));
    assert.ok(R.fmtReviewTone('中性').includes('#0284c7'));
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

// ---------- 每日复盘图表化 (v2.1) ----------
test('reviewChartResonance: 主线构成四档数量, 空数据空串', () => {
    const html = R.reviewChartResonance({
        core: [{ name: '黄金' }, { name: '半导体' }],
        tough: [{ name: '中药' }],
        pulse: [{ name: '脉冲X' }],
        diverge: []
    });
    assert.ok(html.includes('主线构成'), '标题');
    assert.ok(html.includes('硬核主线') && html.includes('韧性主线'), '档位标签');
    assert.ok(html.includes('2条'), '核心数量');
    assert.equal(R.reviewChartResonance(null), '');
    assert.equal(R.reviewChartResonance({ core: [], tough: [], pulse: [], diverge: [] }), '');
});

test('reviewChartTier: 一档/二档按达标率降序, 空数据空串', () => {
    const html = R.reviewChartTier({
        tier1: [{ name: '医疗外包', ratio: 93.1 }, { name: '黄金', ratio: 80 }],
        tier2: [{ name: '模拟芯片', ratio: 26.5 }]
    });
    assert.ok(html.includes('板块梯队达标率'), '标题');
    assert.ok(html.includes('93.1%') && html.includes('26.5%'), '达标率百分比');
    assert.ok(html.indexOf('医疗外包') < html.indexOf('模拟芯片'), '按达标率降序');
    assert.equal(R.reviewChartTier(null), '');
    assert.equal(R.reviewChartTier({ tier1: [], tier2: [] }), '');
});

test('reviewChartRotation: 轮动数量对比, 空数据空串', () => {
    const html = R.reviewChartRotation({
        new_in: [1, 2], rank_jump: [1], out_count: 5, repeat: []
    });
    assert.ok(html.includes('轮动动向'), '标题');
    assert.ok(html.includes('新调入') && html.includes('排名跃升') && html.includes('调出'), '类别');
    assert.ok(html.includes('2个') && html.includes('5个'), '数量');
    assert.equal(R.reviewChartRotation(null), '');
    assert.equal(R.reviewChartRotation({}), '');
});

test('reviewChartHealth: 种子/扩散/疲劳达标率, 空数据空串', () => {
    const html = R.reviewChartHealth({
        seed_high: [{ name: '印制电路板', ratio: 24.4 }],
        expand: [{ name: '医疗外包', ratio: 93.1 }],
        mature_fatigue: [{ name: '培训教育', ratio: 55 }]
    });
    assert.ok(html.includes('扩散/种子达标率'), '标题');
    assert.ok(html.includes('24.4%') && html.includes('93.1%'), '达标率');
    assert.equal(R.reviewChartHealth(null), '');
    assert.equal(R.reviewChartHealth({}), '');
});

test('reviewChartRisk: 批量调出构成按数量降序, 空数据空串', () => {
    const html = R.reviewChartRisk({ batch_out: { 医药: 3, 未归类: 8, 科技: 1 } });
    assert.ok(html.includes('批量调出板块构成'), '标题');
    assert.ok(html.includes('未归类') && html.includes('8个'), '最大风格');
    assert.equal(R.reviewChartRisk(null), '');
    assert.equal(R.reviewChartRisk({}), '');
});

test('reviewChartWatch: 跟踪池构成数量, 空数据空串', () => {
    const html = R.reviewChartWatch({
        seed_high: [{ name: 'A' }], seed_pulse: [{ name: 'B' }, { name: 'C' }],
        critical: [], diverge_watch: [], diverge_dropped: []
    });
    assert.ok(html.includes('次日跟踪池构成'), '标题');
    assert.ok(html.includes('高质量种子') && html.includes('脉冲种子'), '池标签');
    assert.ok(html.includes('2只'), '数量');
    assert.equal(R.reviewChartWatch(null), '');
    assert.equal(R.reviewChartWatch({}), '');
});

test('buildReviewHtml: 复盘图表化(六大方向各含图表)', () => {
    const data = {
        update_date: '20260814', total_industries: 208,
        market: { tone: '中性', high5: 20, high10: 20, high20: 20, diff5: 0, note: '上证指数站上20日线' },
        resonance: { core: [{ name: '黄金', RPS: 100, rank: 1, ratio: 80 }], tough: [],
                     pulse: [{ name: '脉冲X', RPS: 92, rank: 12, ratio: 10 }], diverge: [] },
        tier: { tier1: [{ name: '黄金', ratio: 80 }], tier2: [{ name: '模拟芯片', ratio: 26.5 }] },
        rotation: { new_in: [{ name: '分立器件' }], rank_jump: [{ name: '中成药', rank_change: 43, rank: 10 }],
                    out_count: 3, exits: [{ name: '半导体', continuous: 5, exit_type: 'mainline_exit' }], repeat: [] },
        health: { expand: [{ name: '医疗外包', ratio: 93.1 }], mature: [], mature_fatigue: [],
                  seed_high: [{ name: '印制电路板', ratio: 24.4 }], seed_pulse: [] },
        risk: { batch_out: { 医药: 2 }, warnings: [{ text: '批量调出预警', level: 'warning' }], level: 'warning' },
        watch: { seed_high: [{ name: '印制电路板', ratio: 24.4 }], seed_pulse: [{ name: '分立器件' }], critical: [],
                 diverge_watch: [{ name: '摩托车', drop_days: 1 }], diverge_dropped: [] },
        sentiment: { main: '发酵期', signal: [], seed_n: 0, out_n: 1, core_n: 1, high_n: 20 },
        style: { rps5: { 科技: { count: 3, pct: 27.3 } } },
        consistency: { conflict: false, warning: '' }
    };
    const html = R.buildReviewHtml(data);
    for (const chart of ['三档入选数量', '主线构成', '板块梯队达标率', '轮动动向',
        '扩散/种子达标率', '批量调出板块构成', '次日跟踪池构成']) {
        assert.ok(html.includes(chart), '缺少图表: ' + chart);
    }
    assert.ok(html.includes('摩托车'), '跟踪池文本保留');
    assert.ok(html.includes('调出分类'), '调出分类文本保留');
    assert.ok(!html.includes('undefined'), '不得渲染undefined');
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

test('复盘深色主题: 面板/卡片深色 + 图表浅色文字', () => {
    const data = {
        update_date: '20260814', total_industries: 208,
        market: { tone: '中性', high5: 20, high10: 20, high20: 20, diff5: 0, note: '' },
        resonance: { core: [{ name: '黄金', RPS: 100, rank: 1, ratio: 80 }], tough: [],
                     pulse: [{ name: '脉冲X', RPS: 92, rank: 12, ratio: 10 }], diverge: [] },
        tier: { tier1: [{ name: '黄金', ratio: 80 }], tier2: [] },
        rotation: { new_in: [{ name: 'A' }], rank_jump: [], out_count: 1, repeat: [] },
        health: { expand: [{ name: '医疗外包', ratio: 93.1 }], mature: [], mature_fatigue: [],
                  seed_high: [], seed_pulse: [] },
        risk: { batch_out: {}, warnings: [], level: 'safe' },
        watch: { seed_high: [], seed_pulse: [], critical: [], diverge_watch: [], diverge_dropped: [] },
        sentiment: { main: '发酵期', signal: [], seed_n: 0, out_n: 0, core_n: 1, high_n: 20 },
        style: { rps5: { 科技: { count: 3, pct: 27.3 } } },
        consistency: { conflict: false, warning: '' }
    };
    const html = R.buildReviewHtml(data);
    // 白色面板容器 + 浅灰蓝卡片 (白底深字)
    assert.ok(html.includes('background:#ffffff'), '面板白底');
    assert.ok(html.includes('#f8fafc'), '卡片浅灰蓝背景');
    // 深色文字 (白底可读)
    assert.ok(html.includes('#64748b'), '图表标签中灰');
    assert.ok(html.includes('#334155'), '图表数值/章节文字深色');
    // 金融配色: 红涨绿健康
    assert.ok(html.includes('#dc2626'), '金融红');
    assert.ok(html.includes('#059669'), '金融绿');
    assert.ok(!html.includes('background:#0b1120'), '不再深色面板');
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

// ---------- _tagCls: 标签样式归类 (精准度增强) ----------
test('_tagCls: 风险标签 warn/danger, 平台/板块/常规各归其位', () => {
    const sectors = ['半导体'];
    assert.equal(R._tagCls('反弹未反转', sectors), 'warn');
    assert.equal(R._tagCls('超跌反弹', sectors), 'warn');
    assert.equal(R._tagCls('高估值', sectors), 'warn');
    assert.equal(R._tagCls('涨停追高风险', sectors), 'danger');
    assert.equal(R._tagCls('龙虎榜出货', sectors), 'danger'); // 上榜出货警示 (2026-08-23)
    assert.equal(R._tagCls('业绩预减', sectors), 'danger');   // 业绩预告负面 (2026-08-23)
    assert.equal(R._tagCls('首亏预警', sectors), 'danger');   // 首亏预警 (2026-08-23)
    assert.equal(R._tagCls('现金流为负', sectors), 'danger'); // 盈利质量防御 (2026-08-23)
    assert.equal(R._tagCls('利润质量存疑', sectors), 'danger'); // 利润质量存疑 (2026-08-23)
    assert.equal(R._tagCls('龙虎榜买入', sectors), 'tech');   // 净买入保持常规
    assert.equal(R._tagCls('业绩预增', sectors), 'tech');     // 业绩预告正面保持常规 (2026-08-23)
    assert.equal(R._tagCls('扭亏为盈', sectors), 'tech');     // 扭亏为盈保持常规 (2026-08-23)
    assert.equal(R._tagCls('平台刚启动', sectors), 'platform');
    assert.equal(R._tagCls('半导体', sectors), 'sector');
    assert.equal(R._tagCls('贴近新高', sectors), 'tech'); // 正面信号保持蓝色
    assert.equal(R._tagCls('短线优选', sectors), 'tech');
});

test('fmtEarlyTags: 高估值/涨停追高风险带 warn/danger 样式', () => {
    const s = { tags: '高估值 + 涨停追高风险 + 半导体', sectors: ['半导体'] };
    const html = R.fmtEarlyTags(s);
    assert.ok(html.includes('tag-rec warn'), '高估值用 warn 样式');
    assert.ok(html.includes('tag-rec danger'), '涨停追高风险用 danger 样式');
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

test('fmtDetailHeader: 名称/代码/现价/涨跌/状态标签/行情格齐全', () => {
    const rt = {
        name: '华钰矿业', code: '601020.SH',
        price: 21.94, change: 0.03, changePercent: 0.14,
        open: 21.82, high: 22.23, low: 21.5, preClose: 21.91,
        volumeRatio: 0.52, turnover: 2.63, amplitude: 3.33,
        totalAmount: 4.72e8, totalMarket: 1.799e10, circ_mv: 8.5e9,
        pe_ttm: 147.18, afterPrice: 21.94, marketStatus: '收盘',
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
    assert.ok(html.includes('振幅'), '振幅');
    assert.ok(html.includes('换手'), '换手');
    assert.ok(html.includes('总额'), '总额');
    assert.ok(html.includes('总市值'), '总市值');
    assert.ok(html.includes('流通市值'), '流通市值');
    assert.ok(html.includes('市盈TTM'), '市盈TTM');
    assert.ok(html.includes('179.90亿'), '总市值格式化');
    assert.ok(html.includes('85.00亿'), '流通市值格式化(8.5e9=85亿)');
    assert.ok(html.includes('dh-status'), '状态标签');
    assert.ok(html.includes('收盘'), '状态=收盘');
    assert.ok(html.includes('onclick="copyStockInfo()"'), '名称代码可复制');
    assert.ok(!html.includes('盘后价'), '不再显示盘后价');
    assert.equal(R.fmtDetailHeader(null), '');
});

test('fmtDetailHeader: 停牌状态给提示文案', () => {
    const html = R.fmtDetailHeader({ name: 'X', code: '600000.SH', marketStatus: '停牌', price: null, volume: 0 });
    assert.ok(html.includes('停牌'), '停牌标签');
    assert.ok(html.includes('该股当前停牌'), '停牌提示文案');
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
    assert.ok(html.includes('ft-grid'), '2x2指标卡片布局');
    assert.ok(html.includes('ft-card'), '指标卡片');
    assert.equal(R.fmtDetailTechnical(null), '');
});

test('explainTag: 已知标签返回说明, 未知返回通用文案', () => {
    assert.ok(R.explainTag('流动性风险').includes('成交活跃度'), '已知标签说明');
    assert.ok(R.explainTag('任意自定义标签').includes('不构成投资建议'), '未知标签通用文案');
});

test('fmtDetailBacktest: 胜率占比条形图', () => {
    const html = R.fmtDetailBacktest({ days: 120, riseRate: '57.14%', avgIncrease: '1.29%' });
    assert.ok(html.includes('bt-bar'), '胜率条形图');
    assert.ok(html.includes('57.1%'), '上涨占比');
    assert.ok(html.includes('42.9%'), '下跌占比');
    assert.equal(R.fmtDetailBacktest(null), '');
});

test('fmtDetailValue: 支持 1/3/5 年区间切换按钮', () => {
    const hist = [];
    for (let i = 0; i < 300; i++) hist.push({ time: '2026-01-01', value: 10 + (i % 50) });
    const va = { pePercent: 25, peHistory: hist, growth: [] };
    const html = R.fmtDetailValue(va, 1);
    assert.ok(html.includes('pe-range'), '区间切换按钮');
    assert.ok(html.includes('近1年'), '近1年按钮');
    assert.ok(html.includes('近3年'), '近3年按钮');
    assert.ok(html.includes('近5年'), '近5年按钮');
    assert.ok(html.includes('近1年'), '默认激活近1年');
    const html3 = R.fmtDetailValue(va, 3);
    assert.ok(html3.includes('近3年'), '3年激活');
    assert.equal(R.fmtDetailValue(null), '');
});

test('fmtDetailImpression: 阶段涨幅小折线 + 三栏对比', () => {
    const imp = {
        stageRise: { stock: '16.58%', industry: '8.37%', hs300: '-2.53%' },
        stageDetail: { stock5: 2.1, stock20: 8.4, stock60: 16.58 },
    };
    const html = R.fmtDetailImpression(imp);
    assert.ok(html.includes('stage-spark'), '小折线图');
    assert.ok(html.includes('<svg'), 'SVG折线');
    assert.ok(html.includes('16.58%'), '个股涨幅');
    assert.ok(html.includes('8.37%'), '行业涨幅');
    assert.ok(html.includes('-2.53%'), '沪深300');
    assert.equal(R.fmtDetailImpression(null), '');
});

test('fmtDetailRisk: 标签可点击弹出说明', () => {
    const rh = { riskTags: ['流动性风险'], highlightTags: ['资金面回暖'] };
    const html = R.fmtDetailRisk(rh);
    assert.ok(html.includes('showTagTip(this)'), '标签点击弹说明');
    assert.ok(html.includes('data-tag='), '标签原文透传');
});

// ---------- XSS 防护: 外部可控文本必须转义后进入 innerHTML ----------
test('XSS: stockRowHtml 个股名含标签被转义', () => {
    const html = R.stockRowHtml({ ts_code: '600519.SH', name: '<img src=x onerror=alert(1)>', pct_chg: 1.2, price: 10, change: 1.0 });
    assert.ok(!html.includes('<img'), '不应出现原始 <img 标签');
    assert.ok(html.includes('&lt;img'), '应输出转义后的 &lt;img');
});

test('XSS: stockRowHtml onclick 中 ts_code 无法逃逸 JS 字符串', () => {
    const evil = "000001';alert(1);//";
    const html = R.stockRowHtml({ ts_code: evil, name: '测试', pct_chg: 1, price: 1, change: 1 });
    assert.ok(html.includes("\\';alert"), '单引号前有反斜杠转义, alert 无法逃出字符串字面量');
    assert.ok(!html.includes("');"), '不存在未转义引号直接闭合逃逸');
});

test('XSS: fmtWeeklyTags/fmtEarlyTags 标签文本被转义', () => {
    const wk = R.fmtWeeklyTags({ tags: '<script>alert(1)</script>', sectors: [] });
    assert.ok(!wk.includes('<script>'));
    assert.ok(wk.includes('&lt;script&gt;'));
    const early = R.fmtEarlyTags({ tags: '<b>x</b>', sectors: [] });
    assert.ok(!early.includes('<b>'));
    assert.ok(early.includes('&lt;b&gt;'));
});

test('XSS: fmtReviewSector/fmtReviewNames 板块名被转义', () => {
    const s = R.fmtReviewSector({ name: '<svg onload=alert(1)>', RPS: 95, rank: 1, ratio: 80 });
    assert.ok(!s.includes('<svg'));
    assert.ok(s.includes('&lt;svg'));
    const names = R.fmtReviewNames(['<img src=x onerror=1>', '正常']);
    assert.ok(!names.includes('<img'));
    assert.ok(names.includes('&lt;img'));
});

test('XSS: buildReviewHtml 中 m.note / cons.warning / 跟踪池名称被转义', () => {
    const data = {
        update_date: '20260818',
        market: { tone: '强', note: '<script>alert(2)</script>', high5: 10, diff5: 1 },
        resonance: {}, tier: {}, rotation: { new_in: [{ name: '<b>evil</b>', RPS: 90 }] },
        health: {}, risk: {}, watch: { critical: [{ name: '<i>c</i>', rps5: 88 }] },
        sentiment: {}, style: {}, consistency: { conflict: true, warning: '<img onerror=3>' },
        total_industries: 100,
    };
    const html = R.buildReviewHtml(data);
    assert.ok(!html.includes('<script>'), 'note 中的 script 应转义');
    assert.ok(!html.includes('<img'), 'warning 中的 img 应转义');
    assert.ok(!html.includes('<b>evil</b>'), '板块名不应原样注入');
    assert.ok(html.includes('&lt;script&gt;') && html.includes('&lt;img'), '应包含转义后的文本');
});

test('XSS: buildPredictionBox 中 logic/regime/basis 被转义', () => {
    const html = R.buildPredictionBox({
        prediction: {
            horizon: 3, up_prob: 70, expected_gain: 6, gain_range: [3, 9], stop_loss: -4, confidence: '高',
            regime: '<b>偏强</b>',
            logic: ['<script>x</script>'],
            basis: '<iframe src=x>',
            factors: { momentum: 60, technical: 55, capital: 50, sector: 60, sentiment: 50, fundamental: 50 },
        },
    });
    assert.ok(!html.includes('<script>'), 'logic 应转义');
    assert.ok(!html.includes('<iframe'), 'basis 应转义');
    assert.ok(!html.includes('<b>偏强</b>'), 'regime 应转义');
});

test('XSS: fmtReviewWarnings 文本与 fmtReviewPhase 信号被转义', () => {
    const w = R.fmtReviewWarnings([{ text: '<div>x</div>', level: 'warning' }]);
    assert.ok(!w.includes('<div>'));
    assert.ok(w.includes('&lt;div&gt;'));
    const p = R.fmtReviewPhase({ main: '高潮期', signal: ['<a>s</a>'], seed_n: 3 });
    assert.ok(!p.includes('<a>'));
    assert.ok(p.includes('&lt;a&gt;'));
});

// ---------- 情绪周期历史趋势图 ----------
test('reviewChartSentimentHistory: 空输入返回空串', () => {
    assert.equal(R.reviewChartSentimentHistory(), '');
    assert.equal(R.reviewChartSentimentHistory([]), '');
    assert.equal(R.reviewChartSentimentHistory(null), '');
});

test('reviewChartSentimentHistory: 生成 SVG 并含标题', () => {
    const html = R.reviewChartSentimentHistory([
        { date: '20260810', phase: '冰点期', high_n: 6 },
        { date: '20260811', phase: '震荡期', high_n: 20 },
    ]);
    assert.ok(html.includes('情绪周期历史'));
    assert.ok(html.includes('<svg'));
    assert.ok(html.includes('<polyline'));
});

test('reviewChartSentimentHistory: 最多渲染最近 24 条', () => {
    const rows = [];
    for (let i = 0; i < 30; i++) rows.push({ date: '2026' + String(i).padStart(4, '0'), phase: '震荡期', high_n: i });
    const html = R.reviewChartSentimentHistory(rows);
    assert.ok(html.includes('近24个交易日'));
    assert.ok(!html.includes('近30个交易日'));
});

test('reviewChartSentimentHistory: 未知阶段降级不崩溃', () => {
    const html = R.reviewChartSentimentHistory([{ date: '20260810', phase: '神秘期', high_n: 5 }]);
    assert.ok(html.includes('<svg'));
});

// ---------- 板块热度排名图 ----------
test('reviewChartSectorHeat: 空 tier 返回空串', () => {
    assert.equal(R.reviewChartSectorHeat(), '');
    assert.equal(R.reviewChartSectorHeat({}), '');
    assert.equal(R.reviewChartSectorHeat({ tier1: [], tier2: [] }), '');
});

test('reviewChartSectorHeat: 合并一/二梯队按 RPS 降序 Top10', () => {
    const tier = {
        tier1: [{ name: '甲板块', RPS: 99.2, ratio: 30 }, { name: '乙板块', RPS: 92.0, ratio: 18 }],
        tier2: [{ name: '丙板块', RPS: 95.5, ratio: 22 }, { name: '丁板块', RPS: 88.1, ratio: 12 }],
    };
    const html = R.reviewChartSectorHeat(tier);
    assert.ok(html.includes('板块热度排名'));
    // 甲(RPS99.2) 应排第一 → 条形最宽 → 其 x 坐标文本标签在 SVG 序列最前
    const iA = html.indexOf('甲板块');
    const iC = html.indexOf('丙板块');
    const iB = html.indexOf('乙板块');
    assert.ok(iA !== -1 && iA < iC && iC < iB, '按 RPS 降序排列');
    assert.ok(html.includes('达标30%') || html.includes('达标 30%'), '行末附达标率');
});

// ---------- 消息面复盘渲染 (2026-08-23) ----------

test('fmtSignedNum: 带符号数字', () => {
    assert.equal(R.fmtSignedNum(3), '+3');
    assert.equal(R.fmtSignedNum(-2), '-2');
    assert.equal(R.fmtSignedNum(0), '0');
    assert.equal(R.fmtSignedNum(null), '0');
    assert.equal(R.fmtSignedNum(undefined), '0');
});

test('fmtReviewNews: 权威利好偏暖 + 净影响', () => {
    const html = R.fmtReviewNews({
        has_news: true, stock_count: 16, hit_count: 3, score_sum: 4, env: '偏暖',
        official_pos_list: [{ name: '兴业银锡', count: 1, headline: '业绩预增公告' }],
        official_neg_list: [], rumor_list: [],
        tags_count: { 利好消息: 3 }, note: '消息面偏暖, 推荐股票中权威利好1只(1条)；净影响+4分'
    });
    assert.ok(html.includes('偏暖'));
    assert.ok(html.includes('权威利好'));
    assert.ok(html.includes('兴业银锡'));
    assert.ok(html.includes('业绩预增公告'));
    assert.ok(html.includes('+4'));
});

test('fmtReviewNews: 权威利空偏冷 + 传闻 + 主力散户动向', () => {
    const html = R.fmtReviewNews({
        has_news: true, stock_count: 10, hit_count: 2, score_sum: -3, env: '偏冷',
        official_pos_list: [], official_neg_list: [{ name: '某股', count: 1, headline: '' }],
        rumor_list: [{ name: '传闻股', pos: 1, neg: 1 }],
        tags_count: { 利空消息: 1, 人气榜TOP: 2, 机构大宗买入: 1, 大宗折价: 1 },
        note: ''
    });
    assert.ok(html.includes('偏冷'));
    assert.ok(html.includes('权威利空'));
    assert.ok(html.includes('传闻股'));
    assert.ok(html.includes('主力/散户动向'));
    assert.ok(html.includes('人气榜TOP2只'));
    assert.ok(html.includes('机构大宗买入1只'));
});

test('fmtReviewNews: 无消息面提示', () => {
    const html = R.fmtReviewNews(null);
    assert.ok(html.includes('暂无消息面信号'));
    assert.ok(R.fmtReviewNews({}).includes('暂无消息面信号'));
    assert.ok(R.fmtReviewNews({ has_news: false }).includes('暂无消息面信号'));
});

test('fmtReviewNews: headline 转义防 XSS', () => {
    const html = R.fmtReviewNews({
        has_news: true, env: '偏暖', stock_count: 1, hit_count: 1, score_sum: 2,
        official_pos_list: [{ name: 'A', count: 1, headline: '<script>x</script>' }],
        official_neg_list: [], rumor_list: [], tags_count: {}, note: ''
    });
    assert.ok(!html.includes('<script>'), 'headline 应转义');
    assert.ok(html.includes('&lt;script&gt;'));
});

test('buildReviewHtml: 消息面复盘卡片 (偏暖环境)', () => {
    const data = {
        update_date: '20260821',
        market: { tone: '中性', high5: 20, high10: 20, high20: 20, diff5: 1, high_score_count_pct: 50 },
        resonance: {}, tier: {}, rotation: {}, health: {}, risk: {}, watch: {},
        sentiment: {}, style: {}, consistency: {},
        news: {
            has_news: true, stock_count: 16, hit_count: 3, score_sum: 4, env: '偏暖',
            official_pos_list: [{ name: '兴业银锡', count: 1, headline: '业绩预增' }],
            official_neg_list: [], rumor_list: [], tags_count: { 利好消息: 3 }, note: ''
        }
    };
    const html = R.buildReviewHtml(data);
    assert.ok(html.includes('消息面复盘'), '消息面卡片标题');
    assert.ok(html.includes('兴业银锡'), '权威利好个股');
});

test('buildReviewHtml: 消息面卡片无数据降级', () => {
    const data = {
        update_date: '20260821',
        market: { tone: '中性', high5: 20, diff5: 1 }, resonance: {}, tier: {},
        rotation: {}, health: {}, risk: {}, watch: {}, sentiment: {}, style: {},
        consistency: {}
    };
    const html = R.buildReviewHtml(data);
    assert.ok(html.includes('消息面复盘'));
    assert.ok(html.includes('暂无消息面信号'));
});

// ---------- 每周复盘渲染 (2026-08-23) ----------

test('buildWeeklyReviewHtml: 六大板块齐全', () => {
    const data = {
        week_id: '2026W34', update_date: '20260821', days: 5,
        tone: { series: [{ date: '20260817', high_n: 20 }, { date: '20260818', high_n: 22 },
                          { date: '20260819', high_n: 25 }, { date: '20260820', high_n: 24 },
                          { date: '20260821', high_n: 28 }],
                first: 20, last: 28, avg: 23.8, diff: 8, tone: '周度偏强', note: '周均高分板块24个(首日20→末日28, +8), 周内热度走高' },
        mainlines: [{ name: '有色金属', days_on: 5, rps5: 95.0, rank: 1 }],
        rotation: { sustained: [{ name: '有色金属', days_on: 5, rps5: 95 }],
                    new_in: [{ name: '半导体', days_on: 1, rps5: 92 }],
                    dropped: [{ name: '乳制品', days_on: 2 }] },
        sentiment: { series: [{ date: '20260818', phase: '冰点期' }, { date: '20260821', phase: '启动期' }],
                     start_phase: '冰点期', end_phase: '启动期', trend: '情绪从冰点期到启动期(升温)', phase_change: '升温' },
        news: { has_news: true, stock_count: 16, hit_count: 3, score_sum: 4, env: '偏暖',
                official_pos_list: [{ name: '兴业银锡', count: 1, headline: '业绩预增' }],
                official_neg_list: [], rumor_list: [], tags_count: {}, note: '' },
        watch: { critical: [{ name: 'X', rps5: 89.5 }], seed_high: [{ name: 'Y', ratio: 80 }],
                 potential: [{ name: 'P', rps5: 88.5 }], diverge_watch: [{ name: 'Z', drop_days: 2 }] }
    };
    const html = R.buildWeeklyReviewHtml(data);
    for (const sec of ['一、本周定调', '二、本周主线', '三、本周轮动',
        '四、本周情绪演变', '五、消息面周度快照', '六、下周关注']) {
        assert.ok(html.includes(sec), '缺少章节: ' + sec);
    }
    assert.ok(html.includes('周度偏强'), '周度强度');
    assert.ok(html.includes('有色金属'), '本周主线');
    assert.ok(html.includes('本周新晋'), '轮动新晋');
    assert.ok(html.includes('升温'), '情绪演变');
    assert.ok(html.includes('兴业银锡'), '消息面快照');
    assert.ok(html.includes('临界晋级池'), '下周关注');
    assert.ok(!html.includes('undefined'), '不得渲染undefined');
});

test('buildWeeklyReviewHtml: 空数据兜底', () => {
    assert.ok(R.buildWeeklyReviewHtml(null).includes('暂无周度复盘数据'));
    assert.ok(R.buildWeeklyReviewHtml({}).includes('暂无周度复盘数据'));
    assert.ok(R.buildWeeklyReviewHtml({ week_id: '2026W34' }).includes('本周定调'));
});

test('浏览器全局导出: IIFE root 必须暴露全部渲染函数(回归: 每周复盘加载失败修复)', () => {
    // 模拟浏览器直接 <script src="render.js"> 加载(无 module), 若忘记挂 root 此处立即失败
    const fs = require('node:fs');
    const path = require('node:path');
    const vm = require('node:vm');
    const code = fs.readFileSync(path.join(__dirname, '../render.js'), 'utf-8');
    const sandbox = { window: {} };
    sandbox.window.window = sandbox.window;
    vm.createContext(sandbox);
    vm.runInContext(code, sandbox);
    const root = sandbox.window;
    for (const f of ['buildWeeklyReviewHtml', 'fmtReviewNews', 'fmtSignedNum',
        'fmtDrillRps', 'fmtDrillRankChange', 'fmtDrillStatus', 'fmtDrillPct', 'fmtDrillNum',
        'buildDrillBreadcrumb', 'buildDrillLevel1Html', 'buildDrillLevel2Html', 'buildDrillLevel3Html']) {
        assert.strictEqual(typeof root[f], 'function', 'root.' + f + ' 未暴露, 浏览器将报 is not defined');
    }
    const html = root.buildWeeklyReviewHtml({
        week_id: '2026W34', tone: { tone: '周度中性', note: 'x', series: [] },
        mainlines: [{ name: 'A', days_on: 3, rps5: 95, rank: 2 }],
        rotation: {}, sentiment: {}, news: null, watch: {}
    });
    assert.ok(html.includes('本周定调') && html.includes('A'), '浏览器作用域渲染正常');
});

// ---------- 四级下钻渲染函数 (fmtDrill* / buildDrill*) ----------

test('fmtDrillRps: ≥90红 / ≥70蓝 / 其余纯文本 / 空值--', () => {
    assert.strictEqual(R.fmtDrillRps(95.4), '<span class="tag-red">95.4</span>');
    assert.strictEqual(R.fmtDrillRps('90'), '<span class="tag-red">90.0</span>');
    assert.strictEqual(R.fmtDrillRps(75), '<span class="tag-blue">75.0</span>');
    assert.strictEqual(R.fmtDrillRps(69.9), '69.9');
    assert.strictEqual(R.fmtDrillRps(0), '0.0');
    assert.strictEqual(R.fmtDrillRps(null), '--');
    assert.strictEqual(R.fmtDrillRps(undefined), '--');
    assert.strictEqual(R.fmtDrillRps(''), '--');
    assert.strictEqual(R.fmtDrillRps('abc'), '--');
});

test('fmtDrillRankChange: 连续1天=新, 正↑/负↓/0→, 空值--', () => {
    assert.strictEqual(R.fmtDrillRankChange(5, 1), '<span class="tag-green">新</span>');
    assert.strictEqual(R.fmtDrillRankChange(0, 1), '<span class="tag-green">新</span>', '入榜优先于变化');
    assert.strictEqual(R.fmtDrillRankChange(3, 2), '<span class="rank-up">↑3</span>');
    assert.strictEqual(R.fmtDrillRankChange('-2', 2), '<span class="rank-down">↓2</span>');
    assert.strictEqual(R.fmtDrillRankChange(0, 2), '<span class="rank-flat">→</span>');
    assert.strictEqual(R.fmtDrillRankChange(0, 0), '<span class="rank-flat">→</span>');
    assert.strictEqual(R.fmtDrillRankChange(null, 2), '--');
    assert.strictEqual(R.fmtDrillRankChange(undefined, 2), '--');
    assert.strictEqual(R.fmtDrillRankChange('', 2), '--');
    assert.strictEqual(R.fmtDrillRankChange('x', 2), '--');
});

test('fmtDrillStatus: 调入/调出/在榜三状态 + 空值--', () => {
    assert.strictEqual(R.fmtDrillStatus('调入'), '<span class="tag-green">调入</span>');
    assert.strictEqual(R.fmtDrillStatus('调出'), '<span class="tag-red">调出</span>');
    assert.strictEqual(R.fmtDrillStatus('在榜'), '<span class="tag-blue">在榜</span>');
    assert.strictEqual(R.fmtDrillStatus(''), '--');
    assert.strictEqual(R.fmtDrillStatus(null), '--');
    assert.strictEqual(R.fmtDrillStatus(undefined), '--');
    assert.strictEqual(R.fmtDrillStatus('未知'), '--');
});

test('fmtDrillPct: 红涨绿跌(A股口径), 0正常显示, 空值--', () => {
    assert.strictEqual(R.fmtDrillPct(3.2), '<span class="pos">+3.20</span>');
    assert.strictEqual(R.fmtDrillPct('-1.5'), '<span class="neg">-1.50</span>');
    assert.strictEqual(R.fmtDrillPct(0), '0.00');
    assert.strictEqual(R.fmtDrillPct(0.001), '<span class="pos">+0.00</span>', '正数按绝对值显示');
    assert.strictEqual(R.fmtDrillPct(null), '--');
    assert.strictEqual(R.fmtDrillPct(undefined), '--');
    assert.strictEqual(R.fmtDrillPct(''), '--');
    assert.strictEqual(R.fmtDrillPct('abc'), '--');
});

test('fmtDrillNum: 保留2位小数去尾零, 空值--', () => {
    assert.strictEqual(R.fmtDrillNum(10), '10');
    assert.strictEqual(R.fmtDrillNum(10.5), '10.5');
    assert.strictEqual(R.fmtDrillNum(10.56), '10.56');
    assert.strictEqual(R.fmtDrillNum('3.1400'), '3.14');
    assert.strictEqual(R.fmtDrillNum(0), '0');
    assert.strictEqual(R.fmtDrillNum(1.00), '1');
    assert.strictEqual(R.fmtDrillNum(null), '--');
    assert.strictEqual(R.fmtDrillNum(undefined), '--');
    assert.strictEqual(R.fmtDrillNum(''), '--');
    assert.strictEqual(R.fmtDrillNum('x'), '--');
});

test('buildDrillBreadcrumb: 四级状态各层级可点击 + 转义', () => {
    // 首页
    assert.strictEqual(R.buildDrillBreadcrumb(),
        '<a class="crumb-link" href="javascript:;" onclick="drillGoHome()">首页</a>');
    assert.strictEqual(R.buildDrillBreadcrumb({}),
        '<a class="crumb-link" href="javascript:;" onclick="drillGoHome()">首页</a>');
    // 一级 (当前级不可点)
    const l1 = R.buildDrillBreadcrumb({ level1Name: '电子' });
    assert.ok(l1.includes('>首页</a>') && l1.includes('drillGoHome()'));
    assert.ok(l1.includes('<span class="crumb-cur">电子</span>'), '当前一级为高亮非链接');
    assert.ok(!l1.includes('drillGoLevel2'), '一级无下钻按钮');
    // 一级+二级 (一级可回, 二级当前)
    const l12 = R.buildDrillBreadcrumb({ level1Name: '电子', level2Name: '半导体' });
    assert.ok(l12.includes('drillGoLevel2(\'电子\')'), '面包屑一级可点击返回');
    assert.ok(l12.includes('<span class="crumb-cur">半导体</span>'), '当前二级高亮');
    assert.ok(!l12.includes('drillGoLevel3'), '二级无下钻按钮');
    // 三级 (个股, 全部可回)
    const l123 = R.buildDrillBreadcrumb({ level1Name: '电子', level2Name: '半导体', stockName: '中芯国际' });
    assert.ok(l123.includes('drillGoLevel2(\'电子\')') && l123.includes('drillGoLevel3(\'半导体\')'));
    assert.ok(l123.includes('<span class="crumb-cur">中芯国际</span>'), '个股高亮');
    // JS 字符串转义: 板块名含单引号时 onclick 不破
    const esc = R.buildDrillBreadcrumb({ level1Name: "建'筑", level2Name: '装饰' });
    assert.ok(esc.includes('drillGoLevel2(\'建\\\'筑\')'), 'onclick 单引号转义: ' + esc);
    // HTML 转义: 名含 < > &
    const escH = R.buildDrillBreadcrumb({ level1Name: '<电子>&' });
    assert.ok(escH.includes('<span class="crumb-cur">&lt;电子&gt;&amp;</span>'), '显示文本转义');
});

test('buildDrillLevel1Html: 一级板块表 + 下钻链接 + badge + RPS 高亮', () => {
    const d = {
        level1: [
            { display_rank: 1, name: '电子', level2_count: 12, RPS5: 95, RPS10: 88, RPS20: 60,
              rank5_change: 2, continuous5: 5, status: '在榜' },
            { display_rank: 2, name: "建'筑", level2_count: 8, RPS5: 75, RPS10: 50, RPS20: 40,
              rank5_change: -3, continuous5: 1, status: '调入' }
        ]
    };
    const html = R.buildDrillLevel1Html(d);
    assert.ok(html.includes('<th>排名</th>') && html.includes('<th>一级板块</th>') && html.includes('<th>RPS20</th>'));
    assert.ok(html.includes('drillGoLevel2(\'电子\')'), '一级名点击下钻');
    assert.ok(html.includes('12个二级'), '二级数量 badge');
    assert.ok(html.includes('rank-up') && html.includes('↑2'), '排名上升');
    assert.ok(html.includes('tag-green') && html.includes('新'), '调入+入榜标记');
    assert.ok(html.includes('<span class="tag-red">95.0</span>'), 'RPS5≥90 红色');
    assert.ok(html.includes('<span class="tag-blue">75.0</span>'), 'RPS5≥70 蓝色');
    assert.ok(html.includes('drillGoLevel2(\'建\\\'筑\')'), '板块名单引号转义');
    assert.ok(html.includes('排名</th><th>一级板块</th>'), '表头顺序');
});

test('buildDrillLevel1Html: 空数据兜底', () => {
    assert.ok(R.buildDrillLevel1Html(null).includes('暂无一级板块数据'));
    assert.ok(R.buildDrillLevel1Html({}).includes('暂无一级板块数据'));
    assert.ok(R.buildDrillLevel1Html({ level1: [] }).includes('暂无一级板块数据'));
});

test('buildDrillLevel2Html: 二级板块表 + 成分股数量 badge', () => {
    const d = {
        level2: [
            { display_rank: 1, name: '半导体', stock_count: 45, RPS5: 92, RPS10: 70, RPS20: 55,
              rank5_change: 0, continuous5: 3, status: '在榜' },
            { display_rank: 2, name: '消费电子', stock_count: 0, RPS5: 30, RPS10: 20, RPS20: 10,
              rank5_change: -1, continuous5: 2, status: '调出' }
        ]
    };
    const html = R.buildDrillLevel2Html(d);
    assert.ok(html.includes('<th>二级板块</th>') && html.includes('<th>排名变化</th>'));
    assert.ok(html.includes('drillGoLevel3(\'半导体\')'), '二级名点击下钻');
    assert.ok(html.includes('45只'), '成分股数量 badge');
    assert.ok(!html.includes('0只'), '无成分股不显示 0 只 badge');
    assert.ok(html.includes('rank-flat') && html.includes('→'), '排名持平');
    assert.ok(html.includes('<span class="tag-red">调出</span>'), '调出状态');
    assert.ok(html.includes('<span class="tag-red">92.0</span>'), 'RPS5≥90 红色');
});

test('buildDrillLevel2Html: 空数据兜底', () => {
    assert.ok(R.buildDrillLevel2Html(null).includes('暂无二级板块数据'));
    assert.ok(R.buildDrillLevel2Html({ level2: [] }).includes('暂无二级板块数据'));
});

test('buildDrillLevel3Html: 按RPS50降序(空值排后)再按涨跌幅降序', () => {
    const d = {
        stocks: [
            { ts_code: '600001', name: '甲', price: 10, pct_chg: 3.2, turnover: 5.1, vol_ratio: 1.8, rps50: 80, industry: '半导体' },
            { ts_code: '600002', name: '乙', price: null, pct_chg: 6.0, turnover: 2.0, vol_ratio: 0.9, rps50: null, industry: '半导体' },
            { ts_code: '600003', name: '丙', price: 20, pct_chg: 1.0, turnover: 1.1, vol_ratio: 1.2, rps50: 95, industry: '半导体' },
            { ts_code: '600004', name: '丁', price: null, pct_chg: -2.0, turnover: 3.0, vol_ratio: 1.0, rps50: 95, industry: '半导体' }
        ]
    };
    const html = R.buildDrillLevel3Html(d);
    // RPS50 降序: 丙(95)/丁(95) 先, 甲(80) 后, 乙(null) 最后; 丙/丁 再按 pct_chg 降序
    const iC = html.indexOf('丙'), iD = html.indexOf('丁'), iA = html.indexOf('甲'), iB = html.indexOf('乙');
    assert.ok(iC !== -1 && iD !== -1 && iA !== -1 && iB !== -1);
    assert.ok(iC < iD && iD < iA && iA < iB, '排序: 丙 < 丁 < 甲 < 乙, 实际顺序 ' + [iC, iD, iA, iB]);
    assert.ok(html.includes('<th>个股名称</th>') && html.includes('<th>RPS50</th>') && html.includes('<th>细分行业</th>'));
    assert.ok(html.includes("openDrillStock('600001','甲')"), '个股名点击下钻');
    assert.ok(html.includes('<span class="pos">+3.20</span>'), '涨跌幅红涨');
    assert.ok(html.includes('<span class="neg">-2.00</span>'), '涨跌幅绿跌');
    assert.ok(html.includes('<span class="tag-red">95.0</span>'), 'RPS50 高亮');
    assert.ok(html.includes('--'), '空值显示 --');
    assert.ok(html.includes('600002'), '空 rps50 股票仍在表中');
});

test('buildDrillLevel3Html: 兼容大写 Price 字段与空数据兜底', () => {
    const d = { stocks: [{ ts_code: '300001', name: '宁', Price: 55.5, pct_chg: 1.1, rps50: 60, industry: '电池' }] };
    const html = R.buildDrillLevel3Html(d);
    assert.ok(html.includes('55.5'), '大写 Price 字段兜底渲染');
    assert.ok(R.buildDrillLevel3Html(null).includes('暂无成分股数据'));
    assert.ok(R.buildDrillLevel3Html({ stocks: [] }).includes('暂无成分股数据'));
});



