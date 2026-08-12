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

test('stockRowHtml: 输出18列', () => {
    const s = {
        ts_code: '300711.SZ', name: '广哈通信', pct_chg: 3.5, price: 19.92,
        consec_up: 2, change: 0.67, bid: 19.92, ask: 19.93,
        vol: 158623, vol_now: 10376, speed: 0.05, turnover: 8.2,
        open: 19.5, high: 20.1, low: 19.3, pre_close: 19.25,
        vol_ratio: 1.61, industry: '通信设备'
    };
    const html = R.stockRowHtml(s);
    assert.equal(countCells(html), 18);
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

test('fmtReviewSector: 精简板块条目带RPS5/排名/达标率', () => {
    assert.equal(R.fmtReviewSector({ name: '黄金', RPS5: 100, rank: 1, ratio: 80 }),
        '黄金(RPS5=100 排名1 达标80%)');
    assert.equal(R.fmtReviewSector({ name: '铅锌' }), '铅锌');
});

test('fmtReviewTone: 强/中性/弱 带色, 空值--', () => {
    assert.ok(R.fmtReviewTone('强').includes('强'));
    assert.ok(R.fmtReviewTone('强').includes('#059669'));
    assert.ok(R.fmtReviewTone('弱').includes('#dc2626'));
    assert.equal(R.fmtReviewTone(''), '--');
});

test('fmtReviewWarnings: 有预警逐条红字, 无预警绿色', () => {
    const warn = R.fmtReviewWarnings(['批量调出预警: x', '动量衰减预警: y']);
    assert.ok(warn.includes('批量调出预警'));
    assert.ok(warn.includes('动量衰减预警'));
    const none = R.fmtReviewWarnings([]);
    assert.ok(none.includes('无风险信号'));
    assert.ok(none.includes('#059669'));
});

test('fmtReviewPhase: 五档带色 + 统计数字', () => {
    assert.ok(R.fmtReviewPhase({ phase: '高潮期', seed_n: 1, out_n: 2, core_n: 5, high_n: 25 })
        .includes('高潮期'));
    assert.equal(R.fmtReviewPhase(null), '--');
});

test('buildReviewHtml: 六大方向齐全, 空数据兜底', () => {
    const data = {
        update_date: '20260810',
        market: { tone: '中性', high5: 12, high10: 12, high20: 12, diff5: 0, note: '上证指数站上20日线' },
        resonance: { core: [{ name: '黄金', RPS5: 100, rank: 1, ratio: 80 }], pulse: [], diverge: [] },
        tier: { tier1: [{ name: '黄金', rank: 1 }], tier2: [] },
        rotation: { new_in: [], rank_jump: [], out_all: ['专用机械'], out_count: 1 },
        health: { expand: [], mature: [], seed: [] },
        risk: { warnings: [] },
        watch: { seed: [], critical: [], diverge_watch: ['乳制品'] },
        sentiment: { phase: '震荡期', seed_n: 0, out_n: 1, core_n: 1, high_n: 12 },
        style: { rps5: { 科技: 3, 周期: 2 } }
    };
    const html = R.buildReviewHtml(data);
    for (const sec of ['一、市场整体定调', '二、板块强弱梯队与主线', '三、板块轮动动向',
        '四、题材/板块健康度', '五、风险与退潮预警', '六、次日跟踪清单', '进阶观察']) {
        assert.ok(html.includes(sec), '缺少章节: ' + sec);
    }
    assert.ok(html.includes('黄金'), '核心主线板块渲染');
    assert.ok(html.includes('专用机械'), '调出板块渲染');
    assert.ok(html.includes('乳制品'), '主线分歧池渲染');
    assert.ok(!html.includes('undefined'), '不得渲染undefined');
});

test('buildReviewHtml: 无数据兜底提示', () => {
    assert.ok(R.buildReviewHtml(null).includes('暂无复盘数据'));
    assert.ok(R.buildReviewHtml({}).includes('暂无复盘数据'));
});
