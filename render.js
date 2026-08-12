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

    /* ===== 每日复盘渲染辅助 (index.html 每日复盘 Tab 复用, 可单测) ===== */

    // 板块名列表(字符串数组 或 [{name}] 数组) → 顿号连接文本(超长截断)
    function fmtReviewNames(arr, max) {
        if (!arr || !arr.length) return '无';
        var n = max || 8;
        var names = arr.map(function (x) {
            return (typeof x === 'string') ? x : (x && x.name) || '';
        }).filter(Boolean);
        if (!names.length) return '无';
        var shown = names.slice(0, n).join('、');
        if (names.length > n) shown += ' 等' + names.length + '个';
        return shown;
    }

    // 精简板块条目 → "黄金(RPS5=100 排名1 达标80%)" 文本
    function fmtReviewSector(s) {
        if (!s) return '';
        var parts = [];
        if (s.RPS5 !== undefined && s.RPS5 !== null && s.RPS5 !== '') parts.push('RPS5=' + Number(s.RPS5).toFixed(0));
        if (s.rank !== undefined && s.rank !== null && s.rank !== '') parts.push('排名' + s.rank);
        if (s.ratio !== undefined && s.ratio !== null && s.ratio !== '') parts.push('达标' + Number(s.ratio).toFixed(0) + '%');
        return s.name + (parts.length ? '(' + parts.join(' ') + ')' : '');
    }

    // 板块条目列表 → 文本(max 截断)
    function fmtReviewSectorList(arr, max) {
        if (!arr || !arr.length) return '无';
        var n = max || 8;
        var shown = arr.slice(0, n).map(fmtReviewSector).join('、');
        if (arr.length > n) shown += ' 等' + arr.length + '个';
        return shown;
    }

    // 市场强弱定调 → 带色 span (强/中性/弱)
    function fmtReviewTone(tone) {
        if (!tone) return '--';
        var map = { '强': '#059669', '中性': '#2563eb', '弱': '#dc2626' };
        var color = map[tone] || '#475569';
        return '<span style="font-weight:700;color:' + color + ';">' + tone + '</span>';
    }

    // 风险预警列表 → HTML(无风险显示绿色)
    function fmtReviewWarnings(arr) {
        if (!arr || !arr.length) return '<div style="color:#059669;font-weight:600;">无风险信号: 未检测到批量调出、高位顶背离、动量衰减, 市场结构健康</div>';
        return arr.map(function (w) {
            return '<div style="color:#dc2626;font-weight:600;margin:2px 0;">' + w + '</div>';
        }).join('');
    }

    // 情绪周期五档 → 带色文本
    function fmtReviewPhase(p) {
        if (!p || !p.phase) return '--';
        var map = { '启动期': '#059669', '发酵期': '#2563eb', '高潮期': '#dc2626', '退潮期': '#d97706', '冰点期': '#64748b', '震荡期': '#475569' };
        var color = map[p.phase] || '#475569';
        return '<span style="font-weight:700;color:' + color + ';">' + p.phase + '</span>' +
            '（种子' + p.seed_n + ' 调出' + p.out_n + ' 核心' + p.core_n + ' 高分' + p.high_n + '）';
    }

    // ===== 日度推荐「预测化」格式化 =====
    // 上涨概率 → 带色文本 (≥68 强, 58-67 中, <58 弱)
    function fmtPredUpProb(v) {
        if (v === null || v === undefined || v === '' || isNaN(Number(v))) return '--';
        var n = Number(v);
        var cls = n >= 68 ? 'pos' : (n >= 58 ? 'pred-mid' : 'pred-low');
        return '<span class="' + cls + '" style="font-weight:700;">' + n + '%</span>';
    }

    // 预期涨幅 → 中值 + 区间
    function fmtPredGain(p) {
        if (!p || p.expected_gain === null || p.expected_gain === undefined || isNaN(Number(p.expected_gain))) return '--';
        var g = Number(p.expected_gain);
        var range = (p.gain_range && p.gain_range.length === 2) ? p.gain_range : [g * 0.55, g * 1.45];
        return '<span class="pos">' + g.toFixed(1) + '%</span><span style="color:#64748b;font-size:10px;"> (' +
            Number(range[0]).toFixed(1) + '~' + Number(range[1]).toFixed(1) + ')</span>';
    }

    // 止损位 → 负值红色
    function fmtPredStop(p) {
        if (!p || p.stop_loss === null || p.stop_loss === undefined || isNaN(Number(p.stop_loss))) return '--';
        return '<span class="neg" style="font-weight:700;">' + Number(p.stop_loss).toFixed(1) + '%</span>';
    }

    // 置信度 → 三档徽标 (高红/中橙/低灰)
    function fmtPredConf(v) {
        if (v === '高') return '<span class="pred-conf high">高</span>';
        if (v === '中') return '<span class="pred-conf mid">中</span>';
        if (v === '低') return '<span class="pred-conf low">低</span>';
        return '--';
    }

    // 预测详情块: 核心逻辑 + 六维度概率 + 风控位 (详情展开内使用)
    function buildPredictionBox(s) {
        var p = s && s.prediction;
        if (!p) return '';
        var dims = { momentum: '动量', technical: '技术', capital: '资金',
                     sector: '板块', sentiment: '情绪', fundamental: '基本面' };
        var fHtml = '';
        if (p.factors) {
            fHtml = Object.keys(dims).map(function (k) {
                var v = p.factors[k];
                return '<span style="display:inline-block;margin:2px 6px 2px 0;font-size:11px;">' + dims[k] +
                    ' <b>' + (v === undefined || v === null ? '--' : v + '%') + '</b></span>';
            }).join('');
        }
        var logic = (p.logic || []).map(function (x) {
            return '<span class="tag-rec tech">' + x + '</span>';
        }).join('') || '--';
        return '<div style="margin-top:6px;padding-top:6px;border-top:1px dashed #e2e8f0;">' +
            '<div style="font-weight:700;color:#1e293b;">预测（' + (p.horizon || 3) + '日内）</div>' +
            '<div class="dl"><span class="k">上涨概率:</span> <span class="v">' + fmtPredUpProb(p.up_prob) +
            ' | 预期涨幅 ' + fmtPredGain(p) + ' | 止损位 ' + fmtPredStop(p) + '</span></div>' +
            '<div class="dl"><span class="k">置信度:</span> <span class="v">' + fmtPredConf(p.confidence) +
            (p.regime ? '（市场' + p.regime + '）' : '') + '</span></div>' +
            '<div class="dl"><span class="k">预测逻辑:</span> <span class="v">' + logic + '</span></div>' +
            (fHtml ? '<div class="dl"><span class="k">六维概率:</span> <span class="v">' + fHtml + '</span></div>' : '') +
            '<div class="dl"><span class="k">依据:</span> <span class="v" style="font-size:10px;opacity:0.7;">' +
            (p.basis || '') + '</span></div>' +
        '</div>';
    }

    // 复盘统计 → 完整 HTML (六大方向 + 进阶)
    function buildReviewHtml(data) {
        if (!data || !data.update_date) return '<div class="empty-msg">暂无复盘数据, 请先运行 rps_calc.py</div>';
        var m = data.market || {}, res = data.resonance || {}, tier = data.tier || {};
        var rot = data.rotation || {}, heal = data.health || {}, risk = data.risk || {};
        var watch = data.watch || {}, sent = data.sentiment || {}, style = data.style || {};
        var card = function (title, body) {
            return '<div style="background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px;margin-bottom:10px;">' +
                '<div style="font-weight:700;color:#1e293b;margin-bottom:6px;border-left:3px solid #dc2626;padding-left:6px;">' + title + '</div>' +
                '<div style="font-size:12px;line-height:1.9;color:#334155;word-break:break-all;">' + body + '</div></div>';
        };
        var diffTxt = (m.diff5 === undefined || m.diff5 === null) ? '--' : (m.diff5 > 0 ? '+' + m.diff5 : m.diff5);
        var html = '<div style="font-size:12px;">';
        // 一、市场整体定调
        var mBody = '今日市场动量<b>' + fmtReviewTone(m.tone) + '</b>：RPS5≥90 板块 ' +
            (m.high5 === undefined ? '--' : m.high5) + ' 个（较前日 ' + diffTxt + '）、RPS10≥90 ' +
            (m.high10 === undefined ? '--' : m.high10) + ' 个、RPS20≥90 ' +
            (m.high20 === undefined ? '--' : m.high20) + ' 个。' + (m.note || '');
        html += card('一、市场整体定调', mBody);
        // 二、主线识别
        var sec2 = [];
        if (res.core && res.core.length) sec2.push('<b>核心主线</b>（三周期共振' + res.core.length + '个）：' + fmtReviewSectorList(res.core));
        if (res.pulse && res.pulse.length) sec2.push('<b>短期脉冲</b>（仅RPS5上榜' + res.pulse.length + '个）：' + fmtReviewSectorList(res.pulse));
        if (res.diverge && res.diverge.length) sec2.push('<b>主线分歧</b>（RPS10/20在榜、RPS5回落）：' + fmtReviewNames(res.diverge));
        if (tier.tier1 && tier.tier1.length) sec2.push('<b>第一梯队</b>（Top10）：' + fmtReviewSectorList(tier.tier1, 10));
        if (tier.tier2 && tier.tier2.length) sec2.push('<b>第二梯队</b>（11~30支线/补涨）：' + fmtReviewSectorList(tier.tier2, 10));
        html += card('二、板块强弱梯队与主线', sec2.length ? sec2.join('<br/>') : '暂无数据');
        // 三、轮动动向
        var sec3 = [];
        if (rot.new_in && rot.new_in.length) sec3.push('<b>今日新调入</b>（' + rot.new_in.length + '个）：' + fmtReviewSectorList(rot.new_in));
        if (rot.rank_jump && rot.rank_jump.length) {
            sec3.push('<b>排名跃升前5</b>：' + rot.rank_jump.slice(0, 5).map(function (s) {
                return s.name + '(' + (s.rank_change > 0 ? '+' : '') + s.rank_change + ')';
            }).join('、'));
        }
        if (rot.out_all && rot.out_all.length) sec3.push('<b>今日调出</b>（' + rot.out_count + '个）：' + fmtReviewNames(rot.out_all, 12));
        var stTop = Object.keys(style.rps5 || {}).sort(function (a, b) { return style.rps5[b] - style.rps5[a]; }).slice(0, 3);
        if (stTop.length) sec3.push('<b>风格分布</b>：' + stTop.map(function (k) { return k + style.rps5[k] + '个'; }).join('、'));
        html += card('三、板块轮动动向', sec3.length ? sec3.join('<br/>') : '暂无数据');
        // 四、健康度
        var sec4 = [];
        if (heal.expand && heal.expand.length) sec4.push('<b>扩散健康</b>（内部达标率≥30%）：' + heal.expand.map(function (x) { return x.name + '(' + Number(x.ratio).toFixed(0) + '%)'; }).join('、'));
        if (heal.mature && heal.mature.length) sec4.push('<b>成熟主线</b>（连续上榜≥5天）：' + fmtReviewNames(heal.mature));
        if (heal.seed && heal.seed.length) sec4.push('<b>种子板块</b>（首日入选）：' + fmtReviewNames(heal.seed));
        html += card('四、题材/板块健康度', sec4.length ? sec4.join('<br/>') : '暂无数据');
        // 五、风险预警
        html += card('五、风险与退潮预警', fmtReviewWarnings(risk.warnings));
        // 六、次日清单
        var sec6 = [];
        if (watch.seed && watch.seed.length) sec6.push('<b>种子观察池</b>（次日连榜则确认强度）：' + fmtReviewNames(watch.seed));
        if (watch.critical && watch.critical.length) sec6.push('<b>临界晋级池</b>（RPS≈阈值且排名连续上升）：' + watch.critical.slice(0, 8).map(function (c) { return c.name + '(' + Number(c.rps5).toFixed(0) + ')'; }).join('、'));
        if (watch.diverge_watch && watch.diverge_watch.length) sec6.push('<b>主线分歧池</b>（RPS20在榜、RPS5回落, 分歧低吸观察）：' + fmtReviewNames(watch.diverge_watch));
        html += card('六、次日跟踪清单', sec6.length ? sec6.join('<br/>') : '暂无跟踪标的');
        // 进阶
        html += card('进阶观察', '情绪周期：' + fmtReviewPhase(sent) +
            '<br/>免责声明：本复盘为 RPS 动量量化复盘, 仅供研究参考, 不构成投资建议。');
        html += '</div>';
        return html;
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
    root.fmtReviewNames = fmtReviewNames;
    root.fmtReviewSector = fmtReviewSector;
    root.fmtReviewSectorList = fmtReviewSectorList;
    root.fmtReviewTone = fmtReviewTone;
    root.fmtReviewWarnings = fmtReviewWarnings;
    root.fmtReviewPhase = fmtReviewPhase;
    root.buildReviewHtml = buildReviewHtml;
    root.fmtPredUpProb = fmtPredUpProb;
    root.fmtPredGain = fmtPredGain;
    root.fmtPredStop = fmtPredStop;
    root.fmtPredConf = fmtPredConf;
    root.buildPredictionBox = buildPredictionBox;

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
            fmtWeeklyCap: fmtWeeklyCap,
            fmtReviewNames: fmtReviewNames,
            fmtReviewSector: fmtReviewSector,
            fmtReviewSectorList: fmtReviewSectorList,
            fmtReviewTone: fmtReviewTone,
            fmtReviewWarnings: fmtReviewWarnings,
            fmtReviewPhase: fmtReviewPhase,
            buildReviewHtml: buildReviewHtml,
            fmtPredUpProb: fmtPredUpProb,
            fmtPredGain: fmtPredGain,
            fmtPredStop: fmtPredStop,
            fmtPredConf: fmtPredConf,
            buildPredictionBox: buildPredictionBox
        };
    }
})(typeof window !== 'undefined' ? window : globalThis);
