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
        var code = s.ts_code || '';
        var nameHtml = code
            ? '<a class="stock-link" onclick="openStockDetail(\'' + code + '\')">' + dash(s.name) + '</a>'
            : dash(s.name);
        return '<tr>' +
            '<td>' + dash(s.ts_code) + '</td>' +
            '<td style="text-align:left;">' + nameHtml + '</td>' +
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

    // 精简板块条目 → "黄金(RPS=100 排名1 达标80%)" 文本 (v2 精简字段用 RPS, 兼容旧 RPS5)
    function fmtReviewSector(s) {
        if (!s) return '';
        var parts = [];
        var rps = (s.RPS !== undefined && s.RPS !== null && s.RPS !== '') ? s.RPS : s.RPS5;
        if (rps !== undefined && rps !== null && rps !== '') parts.push('RPS=' + Number(rps).toFixed(0));
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

    // 市场强弱定调 → 带色 span (v2: 全面强/结构性强/中性偏强/中性/中性偏弱/弱)
    function fmtReviewTone(tone) {
        if (!tone) return '--';
        var map = { '全面强': '#059669', '强': '#059669', '结构性强': '#10b981',
                    '中性偏强': '#3b82f6', '中性': '#2563eb', '中性偏弱': '#d97706', '弱': '#dc2626' };
        var color = map[tone] || '#475569';
        return '<span style="font-weight:700;color:' + color + ';">' + tone + '</span>';
    }

    // 风险预警列表 → HTML(按级别着色: danger红/warning橙/hint灰; 无风险绿色)
    function fmtReviewWarnings(arr) {
        if (!arr || !arr.length) return '<div style="color:#059669;font-weight:600;">无风险信号: 未检测到批量调出、高位顶背离、动量衰减, 市场结构健康</div>';
        return arr.map(function (w) {
            var text = (typeof w === 'string') ? w : (w && w.text);
            var lv = (typeof w === 'string') ? 'warning' : (w && w.level) || 'warning';
            var color = lv === 'danger' ? '#dc2626' : lv === 'warning' ? '#d97706' : '#64748b';
            return '<div style="color:' + color + ';font-weight:600;margin:2px 0;">' + text + '</div>';
        }).join('');
    }

    // 情绪周期主标签 + 附加信号 → 带色文本
    function fmtReviewPhase(p) {
        if (!p || !p.main) return '--';
        var map = { '启动期': '#059669', '发酵期': '#f59e0b', '高潮期': '#dc2626', '冰点期': '#64748b', '震荡期': '#475569' };
        var color = map[p.main] || '#475569';
        var sig = (p.signal && p.signal.length) ? ' + <span style="color:#d97706;">' + p.signal.join('、') + '</span>' : '';
        return '<span style="font-weight:700;color:' + color + ';">' + p.main + '</span>' + sig +
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

    // ===== 复盘图表 (纯 SVG 手绘, 无外部依赖, 可单测) =====
    var REVIEW_PHASES = ['冰点期', '启动期', '发酵期', '高潮期', '震荡期'];
    var PHASE_COLORS = ['#64748b', '#3b82f6', '#f59e0b', '#dc2626', '#94a3b8'];

    function _escTxt(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    // 通用横向条形图 items: [{label, value}], opts: {width, row, max, labelWidth, color(item,v), fmt(v), title}
    function reviewChartBars(items, opts) {
        opts = opts || {};
        var W = opts.width || 340, ROW = opts.row || 22, max = opts.max || 100;
        if (!items || !items.length) return '';
        var labelW = opts.labelWidth || 96;
        var cw = W - labelW - 40;
        var H = items.length * ROW + 10;
        var rows = '';
        items.forEach(function (it, i) {
            var v = Number(it.value) || 0;
            var bw = Math.max(2, (v / max) * cw);
            var color = opts.color ? opts.color(it, v) : '#dc2626';
            rows += '<rect x="' + labelW + '" y="' + (5 + i * ROW) + '" width="' + bw + '" height="13" rx="3" fill="' + color + '" opacity="0.85"/>' +
                '<text x="' + (labelW - 6) + '" y="' + (16 + i * ROW) + '" text-anchor="end" font-size="11" fill="#334155">' + _escTxt(it.label) + '</text>' +
                '<text x="' + (labelW + bw + 6) + '" y="' + (16 + i * ROW) + '" font-size="11" fill="#475569">' + (opts.fmt ? opts.fmt(v, it) : v.toFixed(1)) + '</text>';
        });
        return '<div style="margin:6px 0;"><div style="font-size:11px;font-weight:700;color:#1e293b;margin-bottom:2px;">' + _escTxt(opts.title || '') + '</div>' +
            '<svg width="100%" viewBox="0 0 ' + W + ' ' + H + '" style="display:block;max-width:360px;">' + rows + '</svg></div>';
    }

    // 情绪周期五档指示图 (主标签 + 附加信号)
    function reviewChartPhase(sent) {
        var phase = sent && (sent.main || sent.phase);
        if (!phase) return '';
        var idx = REVIEW_PHASES.indexOf(phase);
        var cells = REVIEW_PHASES.map(function (p, i) {
            var active = (i === idx);
            var bg = active ? (PHASE_COLORS[i] || '#dc2626') : '#f1f5f9';
            var fg = active ? '#fff' : '#64748b';
            var border = active ? '' : '1px solid #e2e8f0';
            return '<div style="flex:1;text-align:center;font-size:11px;padding:6px 2px;border-radius:6px;' +
                'background:' + bg + ';color:' + fg + ';border:' + border + ';' +
                (active ? 'font-weight:700;box-shadow:0 1px 3px rgba(0,0,0,0.2);' : '') + '">' + p + '</div>';
        }).join('');
        var sig = (sent.signal && sent.signal.length) ? ' <span style="color:#d97706;">+ ' + sent.signal.join('、') + '</span>' : '';
        var info = '种子 <b>' + (sent.seed_n || 0) + '</b> · 调出 <b>' + (sent.out_n || 0) + '</b> · 核心主线 <b>' + (sent.core_n || 0) + '</b> · 高分板块 <b>' + (sent.high_n || 0) + '</b>';
        return '<div style="margin:6px 0;"><div style="font-size:11px;font-weight:700;color:#1e293b;margin-bottom:4px;">情绪周期：<span style="color:#dc2626;">' + _escTxt(phase) + '</span>' + sig + '</div>' +
            '<div style="display:flex;gap:4px;">' + cells + '</div>' +
            '<div style="font-size:11px;color:#475569;margin-top:4px;">' + info + '</div></div>';
    }

    // 风格归因条形图 style: {rps5: {风格: {count, pct}}} (兼容旧 {风格: 数量})
    function reviewChartStyle(style) {
        var m = style && style.rps5;
        if (!m) return '';
        var items = Object.keys(m).map(function (k) {
            var v = m[k];
            return { label: k, value: (v && typeof v === 'object') ? (v.count || 0) : v };
        })
            .sort(function (a, b) { return b.value - a.value; }).slice(0, 6);
        if (!items.length) return '';
        return reviewChartBars(items, {
            title: '风格归因（RPS5 上榜板块）', max: items[0].value || 1, row: 24,
            color: function () { return '#3b82f6'; },
            fmt: function (v, it) {
                var st = m[it.label];
                var pct = (st && typeof st === 'object') ? st.pct : undefined;
                return v + '个' + (pct === undefined ? '' : ' (' + pct + '%)');
            }
        });
    }

    // 三档入选数量条形图
    function reviewChartMkt(m, total) {
        if (!m) return '';
        var items = [
            { label: 'RPS5', value: m.high5 },
            { label: 'RPS10', value: m.high10 },
            { label: 'RPS20', value: m.high20 }
        ];
        return reviewChartBars(items, {
            title: '三档入选数量（共' + (total || '--') + '板块）',
            max: Math.max(m.high5 || 0, m.high10 || 0, m.high20 || 0, 1),
            row: 26, labelWidth: 70,
            color: function (it) { return it.label === 'RPS5' ? '#dc2626' : (it.label === 'RPS10' ? '#f59e0b' : '#3b82f6'); },
            fmt: function (v) { return v + '个'; }
        });
    }

    // 复盘统计 → 完整 HTML (六大方向 + 进阶 + 一致性校验)
    function buildReviewHtml(data) {
        if (!data || !data.update_date) return '<div class="empty-msg">暂无复盘数据, 请先运行 rps_calc.py</div>';
        var m = data.market || {}, res = data.resonance || {}, tier = data.tier || {};
        var rot = data.rotation || {}, heal = data.health || {}, risk = data.risk || {};
        var watch = data.watch || {}, sent = data.sentiment || {}, style = data.style || {};
        var cons = data.consistency || {};
        var card = function (title, body) {
            return '<div style="background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px;margin-bottom:10px;">' +
                '<div style="font-weight:700;color:#1e293b;margin-bottom:6px;border-left:3px solid #dc2626;padding-left:6px;">' + title + '</div>' +
                '<div style="font-size:12px;line-height:1.9;color:#334155;word-break:break-all;">' + body + '</div></div>';
        };
        var fmtRatio = function (x) { return x.name + '(' + Number(x.ratio).toFixed(0) + '%)'; };
        var diffTxt = (m.diff5 === undefined || m.diff5 === null) ? '--' : (m.diff5 > 0 ? '+' + m.diff5 : m.diff5);
        var pctTxt = (m.high_score_count_pct === undefined || m.high_score_count_pct === null) ? '--' : m.high_score_count_pct.toFixed(0) + '%';
        var html = '<div style="font-size:12px;">';
        // 一、市场整体定调 (含历史分位/指数状态/一致性校验)
        var mBody = '今日市场动量<b>' + fmtReviewTone(m.tone) + '</b>：RPS5≥90 板块 ' +
            (m.high5 === undefined ? '--' : m.high5) + ' 个（较前日 ' + diffTxt + '）、RPS10≥90 ' +
            (m.high10 === undefined ? '--' : m.high10) + ' 个、RPS20≥90 ' +
            (m.high20 === undefined ? '--' : m.high20) + ' 个。高分板块历史分位 ' + pctTxt + '。' + (m.note || '');
        if (cons.conflict) mBody += '<div style="color:#dc2626;font-weight:600;margin-top:4px;">' + cons.warning + '</div>';
        html += card('一、市场整体定调', mBody + reviewChartMkt(m, data.total_industries));
        // 二、主线识别 (硬核/韧性主线 + 梯队过滤)
        var sec2 = [];
        if (res.core && res.core.length) sec2.push('<b>硬核主线</b>（三周期共振' + res.core.length + '条）：' + fmtReviewSectorList(res.core));
        if (res.tough && res.tough.length) sec2.push('<b>韧性主线</b>（RPS10/20在榜、RPS5掉1天' + res.tough.length + '条）：' + fmtReviewSectorList(res.tough));
        if (res.pulse && res.pulse.length) sec2.push('<b>短期脉冲</b>（仅RPS5上榜' + res.pulse.length + '个）：' + fmtReviewSectorList(res.pulse));
        if (res.diverge && res.diverge.length) sec2.push('<b>主线分歧</b>（RPS10/20在榜、RPS5回落）：' + fmtReviewNames(res.diverge));
        if (tier.tier1 && tier.tier1.length) sec2.push('<b>第一梯队</b>（Top10, 达标率≥15%）：' + fmtReviewSectorList(tier.tier1, 10));
        if (tier.tier2 && tier.tier2.length) sec2.push('<b>第二梯队</b>（11~30, 达标率≥10%）：' + fmtReviewSectorList(tier.tier2, 10));
        html += card('二、板块强弱梯队与主线', sec2.length ? sec2.join('<br/>') : '暂无数据');
        // 三、轮动动向 (跃升/调出分类/反复轮动)
        var sec3 = [];
        if (rot.new_in && rot.new_in.length) sec3.push('<b>今日新调入</b>（' + rot.new_in.length + '个）：' + fmtReviewSectorList(rot.new_in));
        if (rot.rank_jump && rot.rank_jump.length) {
            sec3.push('<b>排名大幅跃升Top' + rot.rank_jump.length + '</b>（≥8名）：' + rot.rank_jump.map(function (s) {
                return s.name + '(名次+' + s.rank_change + '→第' + s.rank + '名)';
            }).join('、'));
        }
        if (rot.exits && rot.exits.length) {
            var eMap = { mainline_exit: '主线调出(上榜≥5天)', pulse_exit: '脉冲调出(上榜≤2天)', normal_exit: '普通调出' };
            sec3.push('<b>调出分类</b>：' + rot.exits.map(function (e) {
                return e.name + '[' + (eMap[e.exit_type] || e.exit_type) + ' 连续' + e.continuous + '天]';
            }).join('、'));
        }
        if (rot.repeat && rot.repeat.length) sec3.push('<b>反复轮动</b>（近10日多次调入调出, 持续性差）：' + fmtReviewNames(rot.repeat));
        html += card('三、板块轮动动向', sec3.length ? sec3.join('<br/>') : '暂无数据');
        // 四、健康度 (种子分级/扩散健康/成熟疲劳)
        var sec4 = [];
        if (heal.seed_high && heal.seed_high.length) sec4.push('<b>高质量种子</b>（首日入选+扩散达标）：' + heal.seed_high.map(fmtRatio).join('、'));
        if (heal.seed_pulse && heal.seed_pulse.length) sec4.push('<b>脉冲种子</b>（首日入选但扩散不足）：' + fmtReviewNames(heal.seed_pulse));
        if (heal.expand && heal.expand.length) sec4.push('<b>扩散健康</b>（达标率≥规模阈值）：' + heal.expand.map(fmtRatio).join('、'));
        if (heal.mature && heal.mature.length) sec4.push('<b>成熟主线</b>（连续上榜≥5天）：' + fmtReviewNames(heal.mature));
        if (heal.mature_fatigue && heal.mature_fatigue.length) sec4.push('<b>成熟疲劳</b>（≥8天且达标率下滑）：' + heal.mature_fatigue.map(fmtRatio).join('、'));
        html += card('四、题材/板块健康度', sec4.length ? sec4.join('<br/>') : '暂无数据');
        // 五、风险预警 (风险等级 + 分级预警)
        var lvColor = { safe: '#059669', hint: '#64748b', warning: '#d97706', danger: '#dc2626' };
        var lvTxt = { safe: '安全', hint: '提示', warning: '警告', danger: '高危' };
        var rl = risk.level || 'safe';
        html += card('五、风险与退潮预警（风险等级: <span style="color:' + (lvColor[rl] || '#475569') + ';">' + (lvTxt[rl] || rl) + '</span>）',
            fmtReviewWarnings(risk.warnings));
        // 六、次日跟踪清单 (分池 + 掉榜天数)
        var sec6 = [];
        if (watch.seed_high && watch.seed_high.length) sec6.push('<b>高质量种子池</b>（次日连榜则确认强度）：' + watch.seed_high.map(fmtRatio).join('、'));
        if (watch.seed_pulse && watch.seed_pulse.length) sec6.push('<b>脉冲种子池</b>（需扩散确认）：' + fmtReviewNames(watch.seed_pulse));
        if (watch.critical && watch.critical.length) sec6.push('<b>临界晋级池</b>（RPS≈阈值且排名连续上升）：' + watch.critical.slice(0, 8).map(function (c) { return c.name + '(' + Number(c.rps5).toFixed(0) + ')'; }).join('、'));
        if (watch.diverge_watch && watch.diverge_watch.length) sec6.push('<b>主线分歧池</b>（RPS20在榜、RPS5掉榜, 良性分歧低吸观察）：' + watch.diverge_watch.map(function (x) { return x.name + '(掉' + x.drop_days + '天)'; }).join('、'));
        if (watch.diverge_dropped && watch.diverge_dropped.length) sec6.push('<span style="color:#dc2626;"><b>主线衰竭</b>（RPS5掉榜≥3天, 移出跟踪）：' + watch.diverge_dropped.map(function (x) { return x.name + '(掉' + x.drop_days + '天)'; }).join('、') + '</span>');
        html += card('六、次日跟踪清单', sec6.length ? sec6.join('<br/>') : '暂无跟踪标的');
        // 进阶: 情绪周期图 + 风格图 + 一致性校验
        var adv = reviewChartPhase(sent) + reviewChartStyle(style) +
            '情绪周期：' + fmtReviewPhase(sent) +
            (cons.conflict ? '<div style="color:#dc2626;font-weight:600;margin-top:4px;">' + cons.warning + '</div>' : '') +
            '<br/>免责声明：本复盘为 RPS 动量量化复盘, 仅供研究参考, 不构成投资建议。';
        html += card('进阶观察', adv);
        html += '</div>';
        return html;
    }

    /* ===== 早期启动候选渲染辅助 (index.html 早期启动 Tab 复用, 可单测) ===== */

    // 数字格式化 → '--' 或保留 digits 位
    function fmtEarlyNum(v, digits) {
        if (v === null || v === undefined || v === '' || isNaN(Number(v))) return '--';
        return Number(v).toFixed(digits === undefined ? 2 : digits);
    }

    // 早期推荐标签字符串 "平台刚启动 + 短线优选 + 半导体 + ..." → 标签块 HTML
    // 平台刚启动(green) / 板块名(sector红色) / 属性标签(tech蓝色)
    function fmtEarlyTags(s) {
        var parts = [];
        var sectors = s.sectors || [];
        String(s.tags || '').split(' + ').filter(Boolean).forEach(function (t) {
            var cls = 'tech';
            if (t === '平台刚启动') cls = 'platform';
            else if (sectors.indexOf(t) >= 0) cls = 'sector';
            parts.push('<span class="tag-rec ' + cls + '">' + t + '</span>');
        });
        return parts.join('') || '--';
    }

    // 早期大盘两档 → {cls, title, icon}
    // safe: 环境合格正常输出 / danger: 大盘不满足暂无早期候选
    function fmtEarlyMktLevel(m) {
        if (!m || m.is_safe !== false) {
            return { cls: 'safe', title: '大盘环境合格, 正常输出早期启动候选', icon: '✅' };
        }
        return { cls: 'danger', title: '大盘环境不满足, 暂无早期启动候选', icon: '⛔' };
    }

    // 早期日度详情块: 潜力板块/启动形态/RPS50/近10日涨幅/MA60偏离/早期加减分/基本面/资金面/技术/分项
    function fmtEarlyDetail(s) {
        if (!s) return '';
        var e = s.early || {};
        var cap = s.capital || {};
        var tech = s.technical || {};
        var fund = s.fundamental || {};
        var yi = function (v) {
            if (v === null || v === undefined || v === '' || isNaN(Number(v))) return '--';
            return (Number(v) / 1e8).toFixed(1) + '亿';
        };
        var np = (fund.net_profit === null || fund.net_profit === undefined || fund.net_profit === '' || isNaN(Number(fund.net_profit)))
            ? '--'
            : (Number(fund.net_profit) / 1e8).toFixed(2) + '亿';
        var form = e.platform_start
            ? '<span style="color:#059669;font-weight:700;">平台刚启动</span>'
            : '<span style="color:#64748b;">普通</span>';
        // 早期加减分明细
        var adj = [];
        if (e.gain10_deduct) adj.push('涨幅惩罚 -' + e.gain10_deduct);
        if (e.ma60_dev_deduct) adj.push('MA60偏离 -' + e.ma60_dev_deduct);
        if (e.rps50_over_deduct) adj.push('RPS50过高 -' + e.rps50_over_deduct);
        if (e.platform_bonus) adj.push('平台启动 +' + e.platform_bonus);
        var adjTxt = adj.length ? adj.join(' | ') : '无';
        var pct = function (v) {
            var s = fmtEarlyNum(v, 1);
            return s === '--' ? s : s + '%';
        };
        return '<div class="rec-detail-box">' +
            '<div class="dl"><span class="k">潜力板块:</span> <span class="v">' + (s.sector_count || 0) + ' 档 (' + ((s.sectors || []).join('/') || '--') + ')</span></div>' +
            '<div class="dl"><span class="k">启动形态:</span> <span class="v">' + form + '</span></div>' +
            '<div class="dl"><span class="k">早期指标:</span> <span class="v">RPS50 ' + fmtEarlyNum(e.rps50, 0) + ' | 近10日涨幅 ' + pct(e.chg10) + ' | MA60偏离 ' + pct(e.ma60_dev) + '</span></div>' +
            '<div class="dl"><span class="k">早期加减分:</span> <span class="v">' + adjTxt + '</span></div>' +
            '<div class="dl"><span class="k">基本面:</span> <span class="v">净利润 ' + np + ' | 负债率 ' + pct(fund.debt_to_assets) + '</span></div>' +
            '<div class="dl"><span class="k">资金面:</span> <span class="v">换手 ' + pct(cap.turnover) + ' | 量比 ' + fmtEarlyNum(cap.vol_ratio, 2) + '</span></div>' +
            '<div class="dl"><span class="k">技术达标:</span> <span class="v">' + (tech.hit_count || 0) + '/7 项 (' + ((tech.hits || []).join('、') || '--') + ')</span></div>' +
            '<div class="dl"><span class="k">分项得分:</span> <span class="v">板块 ' + fmtEarlyNum(s.score_sector, 0) + ' | 基本面 ' + fmtEarlyNum(s.score_fundamental, 0) + ' | 资金 ' + fmtEarlyNum(s.score_capital, 0) + ' | 技术 ' + fmtEarlyNum(s.score_technical, 0) + '</span></div>' +
        '</div>';
    }

    // 周度早期详情块: 中期板块/基本面/资金面/周线达标/近8周涨幅/分项
    function fmtWeeklyEarlyDetail(s) {
        if (!s) return '';
        var cap = s.capital || {};
        var tech = s.technical || {};
        var fund = s.fundamental || {};
        var pct = function (v) {
            var t = fmtEarlyNum(v, 1);
            return t === '--' ? t : t + '%';
        };
        return '<div class="rec-detail-box">' +
            '<div class="dl"><span class="k">潜力中期板块:</span> <span class="v">' + (s.sector_count || 0) + ' 个 (' + ((s.sectors || []).join('/') || '--') + ')</span></div>' +
            '<div class="dl"><span class="k">近8周涨幅:</span> <span class="v">' + pct(s.chg8_week) + '</span></div>' +
            '<div class="dl"><span class="k">基本面:</span> <span class="v">' + fmtWeeklyFund(fund) + '</span></div>' +
            '<div class="dl"><span class="k">资金面:</span> <span class="v">' + fmtWeeklyCap(cap) + '</span></div>' +
            '<div class="dl"><span class="k">周线达标:</span> <span class="v">' + (tech.hit_count || 0) + '/7 项 (' + ((tech.hits || []).join('、') || '--') + ')</span></div>' +
            '<div class="dl"><span class="k">分项得分:</span> <span class="v">板块 ' + fmtEarlyNum(s.score_sector, 0) + ' | 基本面 ' + fmtEarlyNum(s.score_fundamental, 0) + ' | 资金 ' + fmtEarlyNum(s.score_capital, 0) + ' | 技术 ' + fmtEarlyNum(s.score_technical, 0) + '</span></div>' +
        '</div>';
    }

    /* ===== 个股详情弹窗渲染辅助 (index.html 详情弹窗复用, 可单测) ===== */

    // 详情数字格式化: '--' 或保留 nd 位
    function fmtDetailNum(v, nd) {
        if (v === null || v === undefined || v === '' || isNaN(Number(v))) return '--';
        return Number(v).toFixed(nd === undefined ? 2 : nd);
    }

    // 涨跌色: A股红涨绿跌
    function clsColor(v) {
        if (v === null || v === undefined || v === '' || isNaN(Number(v))) return '';
        return Number(v) > 0 ? 'pos' : (Number(v) < 0 ? 'neg' : '');
    }

    // 带符号百分比: +1.23% / -0.45% / 0.00%
    function fmtDetailPct(v, nd) {
        var s = fmtDetailNum(v, nd === undefined ? 2 : nd);
        if (s === '--') return '--';
        var n = Number(v);
        return (n > 0 ? '+' : '') + s + '%';
    }

    // A. 顶部基础行情头部: 名称/代码/现价/涨跌/今开/最高/最低/量比/换手/总额/总市值/PE/盘后价
    function fmtDetailHeader(rt) {
        if (!rt) return '';
        var name = rt.name || '--';
        var code = rt.code || '--';
        var price = fmtDetailNum(rt.price, 2);
        var cls = clsColor(rt.change);
        var chg = (rt.change === null || rt.change === undefined) ? '--' : ((Number(rt.change) > 0 ? '+' : '') + fmtDetailNum(rt.change, 2));
        var pct = fmtDetailPct(rt.changePercent, 2);
        var cell = function (k, v, extra) {
            return '<div class="dh-cell"><span class="k">' + k + '</span><span class="v ' + (extra || '') + '">' + v + '</span></div>';
        };
        var grid = [
            cell('今开', fmtDetailNum(rt.open, 2)),
            cell('最高', fmtDetailNum(rt.high, 2)),
            cell('最低', fmtDetailNum(rt.low, 2)),
            cell('昨收', fmtDetailNum(rt.preClose, 2)),
            cell('量比', fmtDetailNum(rt.volumeRatio, 2)),
            cell('换手', (rt.turnover === null || rt.turnover === undefined) ? '--' : fmtDetailNum(rt.turnover, 2) + '%'),
            cell('总额', fmtWan(rt.totalAmount)),
            cell('总市值', fmtWan(rt.totalMarket)),
            cell('市盈', fmtDetailNum(rt.pe_ttm, 2)),
            cell('盘后价', fmtDetailNum(rt.afterPrice, 2)),
        ];
        return '<div class="dh">' +
            '<div class="dh-head">' +
                '<span class="dh-name">' + _escTxt(name) + '</span>' +
                '<span class="dh-code">' + _escTxt(code) + '</span>' +
                '<span class="dh-price ' + cls + '">' + price + '</span>' +
                '<span class="dh-chg ' + cls + '">' + chg + '  ' + pct + '</span>' +
            '</div>' +
            '<div class="dh-grid">' + grid.join('') + '</div>' +
        '</div>';
    }

    // 金额 → 中文单位
    function fmtWan(v) {
        if (v === null || v === undefined || v === '' || isNaN(Number(v))) return '--';
        var n = Math.abs(Number(v));
        if (n >= 1e8) return (Number(v) / 1e8).toFixed(2) + '亿';
        if (n >= 1e4) return (Number(v) / 1e4).toFixed(1) + '万';
        return fmtDetailNum(v, 0);
    }

    // E. 综合评分环形 + 打败比例 + AI解盘
    function fmtDetailScore(sc) {
        if (!sc) return '';
        var total = Number(sc.totalScore) || 0;
        var r = 30, c = 2 * Math.PI * r;
        var pct = Math.max(0, Math.min(100, total));
        var dash = (pct / 100 * c).toFixed(1);
        var ai = sc.aiText || '暂无 AI 解盘文本';
        var rank = sc.industryRank || {};
        var mrank = sc.marketRank || {};
        return '<div class="dc score-card">' +
            '<div class="dc-title">综合评分 &amp; AI 智能解盘</div>' +
            '<div class="score-body">' +
                '<div class="ring-wrap">' +
                    '<svg width="84" height="84" viewBox="0 0 84 84">' +
                        '<circle cx="42" cy="42" r="' + r + '" fill="none" stroke="#e5e7eb" stroke-width="7"/>' +
                        '<circle cx="42" cy="42" r="' + r + '" fill="none" stroke="' + (pct >= 60 ? '#dc2626' : pct >= 40 ? '#f59e0b' : '#16a34a') + '" stroke-width="7" stroke-linecap="round" stroke-dasharray="' + dash + ' ' + c.toFixed(1) + '" transform="rotate(-90 42 42)"/>' +
                        '<text x="42" y="45" text-anchor="middle" font-size="16" font-weight="700" fill="#1e293b">' + fmtDetailNum(total, 0) + '</text>' +
                        '<text x="42" y="60" text-anchor="middle" font-size="9" fill="#94a3b8">综合评分</text>' +
                    '</svg>' +
                    '<div class="beat">打败 <b>' + (sc.beatStock || '--') + '</b> 股票</div>' +
                '</div>' +
                '<div class="score-ranks">' +
                    '<div class="sr-row"><span class="k">行业排名</span><span class="v">' + (rank.cur || '--') + ' / ' + (rank.total || '--') + '</span></div>' +
                    '<div class="sr-row"><span class="k">市场排名</span><span class="v">' + (mrank.cur || '--') + ' / ' + (mrank.total || '--') + '</span></div>' +
                    '<div class="sr-row"><span class="k">评分</span><span class="v">' + fmtDetailNum(total, 2) + ' / 100</span></div>' +
                '</div>' +
            '</div>' +
            '<div class="ai-text">💡 ' + _escTxt(ai) + '</div>' +
        '</div>';
    }

    // F. 风险 & 亮点标签
    function fmtDetailRisk(rh) {
        if (!rh) return '';
        var risk = (rh.riskTags || []).map(function (t) {
            return '<span class="tag-pill risk">' + _escTxt(t) + '</span>';
        }).join('');
        var hls = (rh.highlightTags || []).map(function (t) {
            return '<span class="tag-pill good">' + _escTxt(t) + '</span>';
        }).join('');
        return '<div class="dc"><div class="dc-title">风险 &amp; 亮点</div>' +
            '<div class="tags-row">' + (risk || '<span class="muted">暂无风险标签</span>') + '</div>' +
            '<div class="tags-row">' + (hls || '<span class="muted">暂无亮点标签</span>') + '</div>' +
        '</div>';
    }

    // G. 历史回测
    function fmtDetailBacktest(bt) {
        if (!bt) return '';
        return '<div class="dc"><div class="dc-title">历史回测（近 ' + (bt.days || '--') + ' 个交易日）</div>' +
            '<div class="bt-grid">' +
                '<div class="bt-cell"><div class="v">' + (bt.riseRate || '--') + '</div><div class="k">单日上涨比率</div></div>' +
                '<div class="bt-cell"><div class="v">' + (bt.avgIncrease || '--') + '</div><div class="k">平均涨幅</div></div>' +
            '</div>' +
            '<div class="note">回测统计基于历史单日涨跌, 仅供编程学习参考。</div>' +
        '</div>';
    }

    // H. 个股印象: 阶段涨幅对比
    function fmtDetailImpression(imp) {
        if (!imp) return '';
        var sr = imp.stageRise || {};
        var pct = function (v) {
            if (v === '--' || v === null || v === undefined) return '--';
            return v;
        };
        return '<div class="dc"><div class="dc-title">个股印象 · 阶段涨幅对比</div>' +
            '<div class="sr-bar">' +
                '<div class="srb-row"><span class="k">个股</span><span class="v">' + pct(sr.stock) + '</span></div>' +
                '<div class="srb-row"><span class="k">行业</span><span class="v">' + pct(sr.industry) + '</span></div>' +
                '<div class="srb-row"><span class="k">沪深300</span><span class="v">' + pct(sr.hs300) + '</span></div>' +
            '</div>' +
        '</div>';
    }

    // I. 技术探宝
    function fmtDetailTechbao(tb) {
        if (!tb) return '';
        var chip = tb.chip || {};
        var yj = (tb.yujia || []).map(function (t) {
            return '<span class="tag-pill good">' + _escTxt(t) + '</span>';
        }).join('');
        return '<div class="dc"><div class="dc-title">技术探宝</div>' +
            '<div class="dl"><span class="k">K线形态:</span> <span class="v">' + _escTxt(tb.klineForm || '--') + '</span></div>' +
            '<div class="dl"><span class="k">筹码成本区:</span> <span class="v">' + _escTxt(chip.avgCost || '--') + '</span></div>' +
            '<div class="dl"><span class="k">获利比例(估算):</span> <span class="v">' + _escTxt(chip.profitRate || '--') + '</span></div>' +
            '<div class="tags-row">' + (yj || '<span class="muted">暂无业绩预增信号</span>') + '</div>' +
            (tb.chipNote ? '<div class="note">' + _escTxt(tb.chipNote) + '</div>' : '') +
        '</div>';
    }

    // J. 资金跟踪
    function fmtDetailFundtrack(ft) {
        if (!ft) return '';
        var m = ft.market || {};
        var n = ft.north;
        var mg = ft.margin;
        var dr = ft.dragon;
        var card = function (title, items) {
            return '<div class="ft-card"><div class="ft-title">' + title + '</div>' + items + '</div>';
        };
        var row = function (k, v) {
            return '<div class="ft-row"><span class="k">' + k + '</span><span class="v">' + v + '</span></div>';
        };
        var northTxt = '--';
        if (n) northTxt = (n.netBuy === null || n.netBuy === undefined) ? '--' : n.netBuy;
        var marginTxt = '--';
        if (mg) marginTxt = (mg.marginNet === null || mg.marginNet === undefined) ? '--' : mg.marginNet;
        var dragonTxt = '--';
        if (dr) dragonTxt = (dr.lastDate === null || dr.lastDate === undefined) ? '--' : dr.lastDate;
        return '<div class="dc"><div class="dc-title">资金跟踪</div>' +
            '<div class="ft-grid">' +
                card('市场资金', row('散户净流入', m.netInTxt || '--') + row('主力净流入', m.mainNetTxt || '--')) +
                card('北上资金', row('净买入', northTxt)) +
                card('两融余额', row('融资净买入', marginTxt)) +
                card('龙虎榜单', row('上榜日期', dragonTxt)) +
            '</div>' +
            '<div class="note">资金数据来自东财/AKShare, 仅供编程学习参考。</div>' +
        '</div>';
    }

    // 主力捉妖 → 当日资金数据卡片 (简化版: 历史资金流受限, 以当日主力/游资/散户净流入 + 信号替代曲线)
    function fmtDetailZhuoyao(row) {
        if (!row) return '';
        var cell = function (k, x) {
            var txt = '--', cls = '';
            if (x !== null && x !== undefined && !isNaN(Number(x))) {
                var n = Number(x);
                txt = fmtWan(n);
                cls = n > 0 ? 'pos' : (n < 0 ? 'neg' : '');
            }
            return '<div class="zy-row"><span class="k">' + k + '</span><span class="' + cls + '">' + txt + '</span></div>';
        };
        var sign = '无信号', signCls = '';
        if (row.addPosition) { sign = '▲ 加仓信号'; signCls = 'pos'; }
        else if (row.sellSignal) { sign = '▼ 卖出信号'; signCls = 'neg'; }
        return '<div class="zy-card"><div class="zy-title">主力捉妖 · 当日资金' + (row.date || '') + '</div>' +
            '<div class="zy-grid">' +
                cell('主力', row.zhuil) + cell('游资', row.youzi) + cell('散户', row.sanhu) +
            '</div>' +
            '<div class="zy-sign ' + signCls + '">' + sign + '</div>' +
        '</div>';
    }

    // K. 价值分析: PE 百分位 + 估值历史迷你柱状图
    function fmtDetailValue(va) {
        if (!va) return '';
        var hist = va.peHistory || [];
        var bars = '';
        if (hist.length) {
            var vals = hist.map(function (h) { return Number(h.value); }).filter(function (v) { return !isNaN(v); });
            var maxV = Math.max.apply(null, vals.concat([1]));
            var seg = hist.slice(-30);
            bars = '<div class="pe-bars">' + seg.map(function (h) {
                var v = Number(h.value);
                var hgt = isNaN(v) ? 2 : Math.max(2, Math.round(v / maxV * 40));
                return '<div class="pe-bar" style="height:' + hgt + 'px;" title="' + h.time + ' PE ' + fmtDetailNum(v, 1) + '"></div>';
            }).join('') + '</div>';
        }
        return '<div class="dc"><div class="dc-title">价值分析</div>' +
            '<div class="dl"><span class="k">当前PE(TTM):</span> <span class="v">' + fmtDetailNum(va.pePercent, 2) + '</span></div>' +
            (bars || '<div class="muted">暂无 PE 历史曲线数据</div>') +
            ((va.growth || []).length ? '<div class="tags-row">' + va.growth.map(function (t) { return '<span class="tag-pill good">' + _escTxt(t) + '</span>'; }).join('') + '</div>' : '') +
        '</div>';
    }

    // 资讯列表
    function fmtDetailNews(nw) {
        var list = (nw && nw.news) || [];
        if (!list.length) return '<div class="dc"><div class="dc-title">个股资讯</div><div class="muted">暂无最新公告</div></div>';
        var items = list.map(function (it) {
            return '<div class="news-item"><span class="date">' + _escTxt(it.date || '') + '</span><span class="t">' + _escTxt(it.title || '') + '</span></div>';
        }).join('');
        return '<div class="dc"><div class="dc-title">个股资讯 / 公告</div>' + items + '</div>';
    }

    // F10 简况
    function fmtDetailF10(f10) {
        if (!f10) return '';
        return '<div class="dc"><div class="dc-title">F10 简况</div>' +
            '<div class="dl"><span class="k">名称:</span> <span class="v">' + _escTxt(f10.name || '--') + '</span></div>' +
            '<div class="dl"><span class="k">代码:</span> <span class="v">' + _escTxt(f10.code || '--') + '</span></div>' +
            '<div class="dl"><span class="k">所属行业:</span> <span class="v">' + _escTxt(f10.industry || '--') + '</span></div>' +
            '<div class="dl"><span class="k">上市日期:</span> <span class="v">' + _escTxt(f10.listDate || '--') + '</span></div>' +
            '<div class="dl"><span class="k">主营业务:</span> <span class="v">' + _escTxt(f10.mainBusiness || '--') + '</span></div>' +
            (f10.desc ? '<div class="note">' + _escTxt(f10.desc) + '</div>' : '') +
        '</div>';
    }

    // 技术面 Tab: MACD/KDJ/RSI/BOLL 当前值
    function fmtDetailTechnical(ind) {
        if (!ind) return '';
        var macd = ind.macd, kdj = ind.kdj, rsi = ind.rsi, boll = ind.boll;
        var last = function (arr) { return arr && arr.length ? arr[arr.length - 1] : null; };
        var m = last(macd), k = last(kdj), r = last(rsi), b = last(boll);
        var row = function (k, v) { return '<div class="ft-row"><span class="k">' + k + '</span><span class="v">' + v + '</span></div>'; };
        return '<div class="dc"><div class="dc-title">技术面指标（当前值）</div>' +
            (m ? '<div class="dl"><span class="k">MACD:</span> <span class="v">DIF ' + fmtDetailNum(m.dif, 3) + ' | DEA ' + fmtDetailNum(m.dea, 3) + ' | 柱 ' + fmtDetailNum(m.hist, 3) + '</span></div>' : '') +
            (k ? '<div class="dl"><span class="k">KDJ:</span> <span class="v">K ' + fmtDetailNum(k.k, 1) + ' | D ' + fmtDetailNum(k.d, 1) + ' | J ' + fmtDetailNum(k.j, 1) + '</span></div>' : '') +
            (r !== null && r !== undefined ? '<div class="dl"><span class="k">RSI(6):</span> <span class="v">' + fmtDetailNum(r, 1) + '</span></div>' : '') +
            (b && b.mid !== null && b.mid !== undefined ? '<div class="dl"><span class="k">BOLL:</span> <span class="v">上 ' + fmtDetailNum(b.upper, 2) + ' | 中 ' + fmtDetailNum(b.mid, 2) + ' | 下 ' + fmtDetailNum(b.lower, 2) + '</span></div>' : '') +
        '</div>';
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
    root._escTxt = _escTxt;
    root.reviewChartBars = reviewChartBars;
    root.reviewChartPhase = reviewChartPhase;
    root.reviewChartStyle = reviewChartStyle;
    root.reviewChartMkt = reviewChartMkt;
    root.fmtEarlyNum = fmtEarlyNum;
    root.fmtEarlyTags = fmtEarlyTags;
    root.fmtEarlyMktLevel = fmtEarlyMktLevel;
    root.fmtEarlyDetail = fmtEarlyDetail;
    root.fmtWeeklyEarlyDetail = fmtWeeklyEarlyDetail;
    root.fmtDetailNum = fmtDetailNum;
    root.clsColor = clsColor;
    root.fmtDetailPct = fmtDetailPct;
    root.fmtWan = fmtWan;
    root.fmtDetailHeader = fmtDetailHeader;
    root.fmtDetailScore = fmtDetailScore;
    root.fmtDetailRisk = fmtDetailRisk;
    root.fmtDetailBacktest = fmtDetailBacktest;
    root.fmtDetailImpression = fmtDetailImpression;
    root.fmtDetailTechbao = fmtDetailTechbao;
    root.fmtDetailFundtrack = fmtDetailFundtrack;
    root.fmtDetailZhuoyao = fmtDetailZhuoyao;
    root.fmtDetailValue = fmtDetailValue;
    root.fmtDetailNews = fmtDetailNews;
    root.fmtDetailF10 = fmtDetailF10;
    root.fmtDetailTechnical = fmtDetailTechnical;

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
            buildPredictionBox: buildPredictionBox,
            _escTxt: _escTxt,
            reviewChartBars: reviewChartBars,
            reviewChartPhase: reviewChartPhase,
            reviewChartStyle: reviewChartStyle,
            reviewChartMkt: reviewChartMkt,
            fmtEarlyNum: fmtEarlyNum,
            fmtEarlyTags: fmtEarlyTags,
            fmtEarlyMktLevel: fmtEarlyMktLevel,
            fmtEarlyDetail: fmtEarlyDetail,
            fmtWeeklyEarlyDetail: fmtWeeklyEarlyDetail,
            fmtDetailNum: fmtDetailNum,
            clsColor: clsColor,
            fmtDetailPct: fmtDetailPct,
            fmtWan: fmtWan,
            fmtDetailHeader: fmtDetailHeader,
            fmtDetailScore: fmtDetailScore,
            fmtDetailRisk: fmtDetailRisk,
            fmtDetailBacktest: fmtDetailBacktest,
            fmtDetailImpression: fmtDetailImpression,
            fmtDetailTechbao: fmtDetailTechbao,
            fmtDetailFundtrack: fmtDetailFundtrack,
            fmtDetailZhuoyao: fmtDetailZhuoyao,
            fmtDetailValue: fmtDetailValue,
            fmtDetailNews: fmtDetailNews,
            fmtDetailF10: fmtDetailF10,
            fmtDetailTechnical: fmtDetailTechnical
        };
    }
})(typeof window !== 'undefined' ? window : globalThis);
