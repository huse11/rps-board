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
