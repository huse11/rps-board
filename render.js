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

    // 浏览器: 挂到全局（index.html 主脚本直接按函数名调用）
    root.fmtStockNum = fmtStockNum;
    root.fmtStockPct = fmtStockPct;
    root.fmtStockVol = fmtStockVol;
    root.stockRowHtml = stockRowHtml;
    root.dash = dash;

    // Node: 导出供单元测试
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            fmtStockNum: fmtStockNum,
            fmtStockPct: fmtStockPct,
            fmtStockVol: fmtStockVol,
            stockRowHtml: stockRowHtml,
            dash: dash
        };
    }
})(typeof window !== 'undefined' ? window : globalThis);
