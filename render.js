/* 成分股弹窗渲染函数（浏览器全局 + Node 模块导出，供单元测试）
   覆盖: 买价/卖价/现量/涨速 等 18 列渲染; 空值显示 '--' */
(function (root) {
    'use strict';

    function dash(v) {
        return (v === null || v === undefined || v === '' ? '--' : v);
    }

    function fmtStockNum(v) {
        if (v === null || v === undefined || v === '' || isNaN(Number(v))) return '--';
        var n = Number(v);
        return n > 0 ? '<span class="pos">' + n + '</span>' : n < 0 ? '<span class="neg">' + n + '</span>' : n;
    }

    function fmtStockPct(v) {
        if (v === null || v === undefined || v === '' || isNaN(Number(v))) return '--';
        var n = Number(v);
        return n > 0 ? '<span class="pos">+' + n.toFixed(2) + '</span>' : n < 0 ? '<span class="neg">' + n.toFixed(2) + '</span>' : n.toFixed(2);
    }

    function fmtStockVol(v) {
        if (v === null || v === undefined || v === '' || isNaN(Number(v))) return '--';
        var n = Number(v);
        if (n >= 10000) return (n / 10000).toFixed(1) + '万';
        return n;
    }

    function stockRowHtml(s) {
        return '<tr>' +
            '<td>' + dash(s.ts_code) + '</td>' +
            '<td style="text-align:left;">' + dash(s.name) + '</td>' +
            '<td>' + fmtStockPct(s.pct_chg) + '</td>' +
            '<td>' + fmtStockNum(s.price) + '</td>' +
            '<td>' + (s.consec_up || 0) + '</td>' +
            '<td>' + fmtStockPct(s.change) + '</td>' +
            '<td>' + dash(s.bid) + '</td>' +
            '<td>' + dash(s.ask) + '</td>' +
            '<td>' + fmtStockVol(s.vol) + '</td>' +
            '<td>' + fmtStockVol(s.vol_now) + '</td>' +
            '<td>' + fmtStockPct(s.speed) + '</td>' +
            '<td>' + fmtStockPct(s.turnover) + '</td>' +
            '<td>' + fmtStockNum(s.open) + '</td>' +
            '<td>' + fmtStockNum(s.high) + '</td>' +
            '<td>' + fmtStockNum(s.low) + '</td>' +
            '<td>' + fmtStockNum(s.pre_close) + '</td>' +
            '<td>' + fmtStockNum(s.vol_ratio) + '</td>' +
            '<td style="text-align:left;">' + dash(s.industry) + '</td>' +
            '</tr>';
    }

    /* ===== 周度推荐渲染辅助 (index.html 周度 Tab 复用, 可单测) ===== */

    // 周度推荐标签字符串 "业绩增长 + 周线突破 + 半导体" → 标签块 HTML
    // 板块名(sector红色) / 中线属性标签(tech蓝色)
    function fmtWeeklyTags(s) {
        var parts = [];
        var sectors = s.sectors || [];
        String(s.tags || '').split(' + ').filter(Boolean).forEach(function (t) {
            var isSector = sectors.indexOf(t) >= 0;
            parts.push('<span class="tag-rec ' + (isSector ? 'sector' : 'tech') + '">' + t + '</span>');
        });
        return parts.join('') || '--';
    }

    // 周度大盘三档 → {cls, title, icon}
    // good: 环境合格正常输出 / weak: 偏弱谨慎参与 / bad: 恶劣本周无推荐
    function fmtWeeklyMktLevel(m) {
        if (!m || m.level === 'good') {
            return { cls: 'safe', title: '大盘环境合格, 正常输出周度推荐', icon: '✅' };
        }
        if (m.level === 'weak') {
            return { cls: 'warn', title: '大盘环境偏弱, 谨慎参与控制仓位', icon: '⚠️' };
        }
        return { cls: 'danger', title: '中期趋势偏弱, 本周无推荐', icon: '⛔' };
    }

    // 周度基本面摘要 {net_profit, netprofit_yoy, dt_netprofit_yoy, debt_to_assets} → 文本
    function fmtWeeklyFund(fund) {
        var f = fund || {};
        var pct = function (v) {
            if (v === null || v === undefined || v === '' || isNaN(Number(v))) return '--';
            return Number(v).toFixed(1) + '%';
        };
        var np = (f.net_profit === null || f.net_profit === undefined || f.net_profit === '' || isNaN(Number(f.net_profit)))
            ? '--'
            : (Number(f.net_profit) / 1e8).toFixed(2) + '亿';
        return '净利润 ' + np + ' | 同比 ' + pct(f.netprofit_yoy) +
            ' | 扣非同比 ' + pct(f.dt_netprofit_yoy) +
            ' | 负债率 ' + pct(f.debt_to_assets);
    }

    // 周度资金面 {turnover, vol_expand, amount, net_inflow_5d} → 文本
    function fmtWeeklyCap(cap) {
        var c = cap || {};
        var yi = function (v) {
            if (v === null || v === undefined || v === '' || isNaN(Number(v))) return '--';
            return (Number(v) / 1e8).toFixed(1) + '亿';
        };
        var net = c.net_inflow_5d;
        var netTxt = (net === null || net === undefined || net === '' || isNaN(Number(net)))
            ? '--'
            : ((Number(net) >= 0 ? '+' : '') + (Number(net) / 1e8).toFixed(2) + '亿');
        return '周换手 ' + (c.turnover === null || c.turnover === undefined || c.turnover === '' ? '--' : Number(c.turnover).toFixed(1) + '%') +
            ' | 量能 ' + (c.vol_expand === null || c.vol_expand === undefined ? '--' : Number(c.vol_expand).toFixed(2) + 'x') +
            ' | 周成交额 ' + yi(c.amount) +
            ' | 5日主力净流入 ' + netTxt;
    }

    // 浏览器: 挂到全局（index.html 主脚本直接按函数名调用）
    root.fmtStockNum = fmtStockNum;
    root.fmtStockPct = fmtStockPct;
    root.fmtStockVol = fmtStockVol;
    root.stockRowHtml = stockRowHtml;
    root.dash = dash;
    root.fmtWeeklyTags = fmtWeeklyTags;
    root.fmtWeeklyMktLevel = fmtWeeklyMktLevel;
    root.fmtWeeklyFund = fmtWeeklyFund;
    root.fmtWeeklyCap = fmtWeeklyCap;

    // Node: 导出供单元测试
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            fmtStockNum: fmtStockNum,
            fmtStockPct: fmtStockPct,
            fmtStockVol: fmtStockVol,
            stockRowHtml: stockRowHtml,
            dash: dash,
            fmtWeeklyTags: fmtWeeklyTags,
            fmtWeeklyMktLevel: fmtWeeklyMktLevel,
            fmtWeeklyFund: fmtWeeklyFund,
            fmtWeeklyCap: fmtWeeklyCap
        };
    }
})(typeof window !== 'undefined' ? window : globalThis);
