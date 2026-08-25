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
            ? '<a class="stock-link" onclick="openStockDetail(\'' + _escJs(code) + '\')">' + _escTxt(dash(s.name)) + '</a>'
            : _escTxt(dash(s.name));
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

    // 标签样式归类 (精准度增强 2026-08-18): 风险提示(琥珀/红) / 板块(红) / 平台(绿) / 常规(蓝)
    function _tagCls(t, sectors) {
        if (t === '平台刚启动') return 'platform';
        if ((sectors || []).indexOf(t) >= 0) return 'sector';
        if (t === '反弹未反转' || t === '超跌反弹' || t === '高估值') return 'warn';
        // 风险提示 (2026-08-23 新增: 业绩预减/首亏预警/现金流为负/利润质量存疑 → danger 红色)
        if (t === '涨停追高风险' || t === '龙虎榜出货' || t === '业绩预减' || t === '首亏预警'
            || t === '现金流为负' || t === '利润质量存疑') return 'danger';
        // 消息面风险 (2026-08-23: 权威利空/待证传闻/大宗折价 → danger; 利好类 → tech 常规)
        if (t === '利空消息' || t === '消息待证' || t === '大宗折价') return 'danger';
        return 'tech';
    }

    // 周度推荐标签字符串 "业绩增长 + 周线突破 + 半导体" → 标签块 HTML
    // 板块名(sector红色) / 中线属性标签(tech蓝色) / 风险类(warn/danger)
    function fmtWeeklyTags(s) {
        var parts = [];
        var sectors = s.sectors || [];
        String(s.tags || '').split(' + ').filter(Boolean).forEach(function (t) {
            parts.push('<span class="tag-rec ' + _tagCls(t, sectors) + '">' + _escTxt(t) + '</span>');
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
        }).filter(Boolean).map(_escTxt);
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
        return _escTxt(s.name) + (parts.length ? '(' + parts.join(' ') + ')' : '');
    }

    // 板块条目列表 → 文本(max 截断)
    function fmtReviewSectorList(arr, max) {
        if (!arr || !arr.length) return '无';
        var n = max || 8;
        var shown = arr.slice(0, n).map(fmtReviewSector).join('、');
        if (arr.length > n) shown += ' 等' + arr.length + '个';
        return shown;
    }

    // 市场强弱定调 → 带色 span (v2: 全面强/结构性强/中性偏强/中性/中性偏弱/弱; 深色主题提亮)
    function fmtReviewTone(tone) {
        if (!tone) return '--';
        var map = { '全面强': DARK.green, '强': DARK.green, '结构性强': '#059669',
                    '中性偏强': DARK.blue, '中性': '#0284c7', '中性偏弱': DARK.amber, '弱': DARK.red };
        var color = map[tone] || DARK.label;
        return '<span style="font-weight:700;color:' + color + ';">' + _escTxt(tone) + '</span>';
    }

    // 风险预警列表 → HTML(按级别着色: danger红/warning橙/hint灰; 无风险绿色; 深色主题提亮)
    function fmtReviewWarnings(arr) {
        if (!arr || !arr.length) return '<div style="color:' + DARK.green + ';font-weight:600;">无风险信号: 未检测到批量调出、高位顶背离、动量衰减, 市场结构健康</div>';
        return arr.map(function (w) {
            var text = (typeof w === 'string') ? w : (w && w.text);
            var lv = (typeof w === 'string') ? 'warning' : (w && w.level) || 'warning';
            var color = lv === 'danger' ? DARK.red : lv === 'warning' ? DARK.amber : DARK.label;
            return '<div style="color:' + color + ';font-weight:600;margin:2px 0;">' + _escTxt(text) + '</div>';
        }).join('');
    }

    // 情绪周期主标签 + 附加信号 → 带色文本 (深色主题提亮)
    function fmtReviewPhase(p) {
        if (!p || !p.main) return '--';
        var map = { '启动期': DARK.green, '发酵期': DARK.amber, '高潮期': DARK.red, '冰点期': DARK.slate, '震荡期': DARK.label };
        var color = map[p.main] || DARK.label;
        var sig = (p.signal && p.signal.length) ? ' + <span style="color:' + DARK.amber + ';">' + _escTxt(p.signal.join('、')) + '</span>' : '';
        var fmtN = function (v) { return (v === undefined || v === null || v === '') ? '--' : _escTxt(v); };
        return '<span style="font-weight:700;color:' + color + ';">' + _escTxt(p.main) + '</span>' + sig +
            '（种子' + fmtN(p.seed_n) + ' 调出' + fmtN(p.out_n) + ' 核心' + fmtN(p.core_n) + ' 高分' + fmtN(p.high_n) + '）';
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
            return '<span class="tag-rec tech">' + _escTxt(x) + '</span>';
        }).join('') || '--';
        return '<div style="margin-top:6px;padding-top:6px;border-top:1px dashed #e2e8f0;">' +
            '<div style="font-weight:700;color:#1e293b;">预测（' + (p.horizon || 3) + '日内）</div>' +
            '<div class="dl"><span class="k">上涨概率:</span> <span class="v">' + fmtPredUpProb(p.up_prob) +
            ' | 预期涨幅 ' + fmtPredGain(p) + ' | 止损位 ' + fmtPredStop(p) + '</span></div>' +
            '<div class="dl"><span class="k">置信度:</span> <span class="v">' + fmtPredConf(p.confidence) +
            (p.regime ? '（市场' + _escTxt(p.regime) + '）' : '') + '</span></div>' +
            '<div class="dl"><span class="k">预测逻辑:</span> <span class="v">' + logic + '</span></div>' +
            (fHtml ? '<div class="dl"><span class="k">六维概率:</span> <span class="v">' + fHtml + '</span></div>' : '') +
            '<div class="dl"><span class="k">依据:</span> <span class="v" style="font-size:10px;opacity:0.7;">' +
            _escTxt(p.basis || '') + '</span></div>' +
        '</div>';
    }

    // ===== 复盘图表 (纯 SVG 手绘, 无外部依赖, 可单测) =====
    var REVIEW_PHASES = ['冰点期', '启动期', '发酵期', '高潮期', '震荡期'];
    var PHASE_COLORS = ['#64748b', '#3b82f6', '#d97706', '#dc2626', '#94a3b8'];
    // ===== 复盘浅色主题 (白底深字, 适配浅色背景) =====
    var DARK = {
        panelBg: '#ffffff',      // 复盘整体面板背景 (白)
        cardBg: '#f8fafc',       // 单张卡片背景 (浅灰蓝, 与白底区分)
        cardBorder: '#e2e8f0',   // 卡片边框 (浅灰)
        title: '#0f172a',        // 章节标题 (深灰蓝)
        chartTitle: '#334155',   // 图表标题
        text: '#334155',         // 正文
        label: '#64748b',        // 图表标签 (中灰)
        dim: '#94a3b8',          // 次要文字
        grid: '#e2e8f0',         // 分隔线/非激活边框 (浅灰)
        red: '#dc2626',          // 金融红 (涨/强势, 白底加深)
        green: '#059669',        // 金融绿 (健康, 白底加深)
        amber: '#d97706',
        blue: '#0284c7',
        purple: '#7c3aed',
        slate: '#64748b',
        inactive: 'rgba(100,116,139,0.12)'  // 非激活单元背景
    };

    function _escTxt(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    // HTML 属性转义 (含双引号)
    function _escAttr(s) {
        return _escTxt(s).replace(/"/g, '&quot;');
    }

    // JS 单引号字符串字面量转义 (用于 onclick="fn('...')" 事件属性, 先转义反斜杠再转义单引号防逃逸)
    function _escJs(s) {
        return String(s == null ? '' : s).replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/[\r\n]/g, ' ');
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
                '<text x="' + (labelW - 6) + '" y="' + (16 + i * ROW) + '" text-anchor="end" font-size="11" fill="' + DARK.label + '">' + _escTxt(it.label) + '</text>' +
                '<text x="' + (labelW + bw + 6) + '" y="' + (16 + i * ROW) + '" font-size="11" fill="' + DARK.text + '">' + (opts.fmt ? opts.fmt(v, it) : v.toFixed(1)) + '</text>';
        });
        return '<div style="margin:6px 0;"><div style="font-size:11px;font-weight:700;color:' + DARK.chartTitle + ';margin-bottom:2px;">' + _escTxt(opts.title || '') + '</div>' +
            '<svg width="100%" viewBox="0 0 ' + W + ' ' + H + '" style="display:block;max-width:360px;">' + rows + '</svg></div>';
    }

    // 情绪周期五档指示图 (主标签 + 附加信号)
    function reviewChartPhase(sent) {
        var phase = sent && (sent.main || sent.phase);
        if (!phase) return '';
        var idx = REVIEW_PHASES.indexOf(phase);
        var cells = REVIEW_PHASES.map(function (p, i) {
            var active = (i === idx);
            var bg = active ? (PHASE_COLORS[i] || DARK.red) : DARK.inactive;
            var fg = active ? '#fff' : DARK.label;
            var border = active ? '' : '1px solid ' + DARK.grid;
            return '<div style="flex:1;text-align:center;font-size:11px;padding:6px 2px;border-radius:6px;' +
                'background:' + bg + ';color:' + fg + ';border:' + border + ';' +
                (active ? 'font-weight:700;box-shadow:0 0 8px rgba(244,63,94,0.35);' : '') + '">' + p + '</div>';
        }).join('');
        var sig = (sent.signal && sent.signal.length) ? ' <span style="color:' + DARK.amber + ';">+ ' + _escTxt(sent.signal.join('、')) + '</span>' : '';
        var info = '种子 <b>' + (sent.seed_n || 0) + '</b> · 调出 <b>' + (sent.out_n || 0) + '</b> · 核心主线 <b>' + (sent.core_n || 0) + '</b> · 高分板块 <b>' + (sent.high_n || 0) + '</b>';
        return '<div style="margin:6px 0;"><div style="font-size:11px;font-weight:700;color:' + DARK.chartTitle + ';margin-bottom:4px;">情绪周期：<span style="color:' + DARK.red + ';">' + _escTxt(phase) + '</span>' + sig + '</div>' +
            '<div style="display:flex;gap:4px;">' + cells + '</div>' +
            '<div style="font-size:11px;color:' + DARK.label + ';margin-top:4px;">' + info + '</div></div>';
    }

    // 情绪周期历史趋势图 history: [{date, phase, high_n, seed_n, core_n, out_n}] 升序
    function reviewChartSentimentHistory(history) {
        if (!history || !history.length) return '';
        var rows = history.slice(-24);
        var W = 520, H = 170, padL = 34, padR = 10, padT = 14, padB = 18;
        var cw = W - padL - padR, ch = H - padT - padB;
        var maxHigh = 1;
        rows.forEach(function (r) { if (Number(r.high_n) > maxHigh) maxHigh = Number(r.high_n); });
        // 情绪热度值: 冰点0 → 启动1 → 发酵2 → 高潮3; 震荡中性1.5
        var PHASE_VAL = { '冰点期': 0, '启动期': 1, '发酵期': 2, '高潮期': 3, '震荡期': 1.5 };
        var valOf = function (p) {
            var v = PHASE_VAL[p];
            return (v === undefined) ? 1.5 : v;
        };
        var colorOf = function (p) {
            var idx = REVIEW_PHASES.indexOf(p);
            return PHASE_COLORS[idx < 0 ? 4 : idx] || DARK.slate;
        };
        var xAt = function (i) { return padL + (rows.length === 1 ? cw / 2 : (i / (rows.length - 1)) * cw); };
        var yAt = function (p) { return padT + ch - (valOf(p) / 3) * ch; };
        var step = Math.max(1, Math.ceil(rows.length / 8));
        var grid = '', bars = '', dots = '', labels = '', pts = '';
        REVIEW_PHASES.forEach(function (p, i) {
            var y = padT + ch - (valOf(p) / 3) * ch;
            grid += '<line x1="' + padL + '" y1="' + y + '" x2="' + (W - padR) + '" y2="' + y + '" stroke="' + DARK.grid + '" stroke-width="0.5" stroke-dasharray="3 3"/>' +
                '<text x="' + (padL - 4) + '" y="' + (y + 3) + '" text-anchor="end" font-size="9" fill="' + PHASE_COLORS[i] + '">' + p.charAt(0) + '</text>';
        });
        rows.forEach(function (r, i) {
            var x = xAt(i), y = yAt(r.phase);
            var bh = Math.max(2, (Number(r.high_n) || 0) / maxHigh * ch);
            bars += '<rect x="' + (x - 4) + '" y="' + (padT + ch - bh) + '" width="8" height="' + bh + '" fill="' + DARK.inactive + '" opacity="0.35" rx="2"/>';
            pts += (i === 0 ? '' : 'L') + x + ',' + y;
            var last = (i === rows.length - 1);
            var color = colorOf(r.phase);
            dots += '<circle cx="' + x + '" cy="' + y + '" r="' + (last ? 4 : 2.5) + '" fill="' + color + '"' +
                (last ? ' stroke="#0f172a" stroke-width="1.5"' : ' opacity="0.85"') + '/>';
            if (i % step === 0 || last) {
                labels += '<text x="' + x + '" y="' + (H - 4) + '" text-anchor="middle" font-size="9" fill="' + DARK.label + '">' + _escTxt(String(r.date || '').slice(5)) + '</text>';
            }
        });
        return '<div style="margin:6px 0;"><div style="font-size:11px;font-weight:700;color:' + DARK.chartTitle + ';margin-bottom:2px;">情绪周期历史（近' + rows.length + '个交易日）</div>' +
            '<svg width="100%" viewBox="0 0 ' + W + ' ' + H + '" style="display:block;max-width:560px;">' + grid + bars +
            '<polyline points="' + pts + '" fill="none" stroke="' + DARK.red + '" stroke-width="1.5" opacity="0.8"/>' + dots + labels + '</svg>' +
            '<div style="font-size:9px;color:' + DARK.label + ';">曲线=情绪阶段（左轴首字 冰/启/发/高/震, 越高越热），灰柱=高分板块数，最右为最新</div></div>';
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
            color: function () { return DARK.blue; },
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
            color: function (it) { return it.label === 'RPS5' ? DARK.red : (it.label === 'RPS10' ? DARK.amber : DARK.blue); },
            fmt: function (v) { return v + '个'; }
        });
    }

    // 主线构成对比图: 硬核/韧性/脉冲/分歧 四档数量 (二、板块强弱梯队与主线)
    function reviewChartResonance(res) {
        if (!res) return '';
        var defs = [
            { key: 'core', label: '硬核主线', color: DARK.red },
            { key: 'tough', label: '韧性主线', color: DARK.amber },
            { key: 'pulse', label: '短期脉冲', color: DARK.blue },
            { key: 'diverge', label: '主线分歧', color: DARK.purple }
        ];
        var items = defs.map(function (d) {
            var arr = res[d.key] || [];
            return { label: d.label, value: arr.length, color: d.color };
        }).filter(function (it) { return it.value > 0; });
        if (!items.length) return '';
        return reviewChartBars(items, {
            title: '主线构成（按档位数量）',
            max: Math.max.apply(null, items.map(function (i) { return i.value; })) || 1,
            row: 24, labelWidth: 76,
            color: function (it) { return it.color; },
            fmt: function (v) { return v + '条'; }
        });
    }

    // 板块梯队达标率图: 一档/二档按达标率降序 (二、板块强弱梯队与主线)
    function reviewChartTier(tier) {
        if (!tier) return '';
        var items = [];
        (tier.tier1 || []).forEach(function (x) {
            if (x && x.name) items.push({ label: x.name, value: x.ratio, sub: '一档' });
        });
        (tier.tier2 || []).forEach(function (x) {
            if (x && x.name) items.push({ label: x.name, value: x.ratio, sub: '二档' });
        });
        items = items.filter(function (x) { return x.value !== undefined && x.value !== null && !isNaN(Number(x.value)); })
            .sort(function (a, b) { return b.value - a.value; }).slice(0, 10);
        if (!items.length) return '';
        return reviewChartBars(items, {
            title: '板块梯队达标率（%）', max: 100, row: 24, labelWidth: 96,
            color: function (it) { return it.sub === '一档' ? DARK.red : DARK.amber; },
            fmt: function (v) { return Number(v).toFixed(1) + '%'; }
        });
    }

    // 板块热度排名图: 合并一/二梯队, 按 RPS 强度降序 Top10 (横条=RPS, 行末附达标率)
    function reviewChartSectorHeat(tier) {
        if (!tier) return '';
        var items = [];
        (tier.tier1 || []).forEach(function (x) {
            if (x && x.name) items.push({ label: x.name, value: x.RPS, ratio: x.ratio, sub: '一档' });
        });
        (tier.tier2 || []).forEach(function (x) {
            if (x && x.name) items.push({ label: x.name, value: x.RPS, ratio: x.ratio, sub: '二档' });
        });
        items = items.filter(function (x) { return x.value !== undefined && x.value !== null && !isNaN(Number(x.value)); })
            .sort(function (a, b) { return Number(b.value) - Number(a.value); }).slice(0, 10);
        if (!items.length) return '';
        return reviewChartBars(items, {
            title: '板块热度排名（按RPS强度 Top10）', max: 100, row: 24, labelWidth: 96,
            color: function (it) { return it.sub === '一档' ? DARK.red : DARK.blue; },
            fmt: function (v, it) { return Number(v).toFixed(1) + ' 达标' + Number(it.ratio || 0).toFixed(0) + '%'; }
        });
    }

    // 轮动动向图: 新调入/排名跃升/调出/反复 数量对比 (三、板块轮动动向)
    function reviewChartRotation(rot) {
        if (!rot) return '';
        var items = [];
        if (rot.new_in && rot.new_in.length) items.push({ label: '新调入', value: rot.new_in.length, color: DARK.red });
        if (rot.rank_jump && rot.rank_jump.length) items.push({ label: '排名跃升', value: rot.rank_jump.length, color: DARK.amber });
        if (rot.out_count !== undefined && rot.out_count !== null) items.push({ label: '调出', value: rot.out_count, color: DARK.slate });
        if (rot.repeat && rot.repeat.length) items.push({ label: '反复轮动', value: rot.repeat.length, color: DARK.purple });
        if (!items.length) return '';
        return reviewChartBars(items, {
            title: '轮动动向（数量）',
            max: Math.max.apply(null, items.map(function (i) { return i.value; })) || 1,
            row: 24, labelWidth: 76,
            color: function (it) { return it.color; },
            fmt: function (v) { return v + '个'; }
        });
    }

    // 健康度达标率图: 高质量种子/扩散健康/成熟疲劳 (四、题材/板块健康度)
    function reviewChartHealth(heal) {
        if (!heal) return '';
        var items = [];
        (heal.seed_high || []).forEach(function (x) {
            if (x && x.name) items.push({ label: x.name, value: x.ratio, sub: '高质量种子' });
        });
        (heal.expand || []).forEach(function (x) {
            if (x && x.name) items.push({ label: x.name, value: x.ratio, sub: '扩散健康' });
        });
        (heal.mature_fatigue || []).forEach(function (x) {
            if (x && x.name) items.push({ label: x.name, value: x.ratio, sub: '成熟疲劳' });
        });
        items = items.filter(function (x) { return x.value !== undefined && x.value !== null && !isNaN(Number(x.value)); })
            .sort(function (a, b) { return b.value - a.value; }).slice(0, 8);
        if (!items.length) return '';
        return reviewChartBars(items, {
            title: '扩散/种子达标率（%）', max: 100, row: 24, labelWidth: 96,
            color: function (it) {
                return it.sub === '成熟疲劳' ? DARK.red : (it.sub === '高质量种子' ? DARK.green : DARK.blue);
            },
            fmt: function (v) { return Number(v).toFixed(1) + '%'; }
        });
    }

    // 批量调出构成图: batch_out {风格: 数量} (五、风险与退潮预警)
    function reviewChartRisk(risk) {
        if (!risk) return '';
        var bo = risk.batch_out || {};
        var items = Object.keys(bo).map(function (k) {
            return { label: k, value: bo[k] };
        }).sort(function (a, b) { return b.value - a.value; });
        if (!items.length) return '';
        return reviewChartBars(items, {
            title: '批量调出板块构成（按风格）',
            max: Math.max.apply(null, items.map(function (i) { return i.value; })) || 1,
            row: 24, labelWidth: 76,
            color: function () { return DARK.red; },
            fmt: function (v) { return v + '个'; }
        });
    }

    // 次日跟踪池构成图: 各池数量 (六、次日跟踪清单)
    function reviewChartWatch(watch) {
        if (!watch) return '';
        var defs = [
            { key: 'seed_high', label: '高质量种子', color: DARK.green },
            { key: 'seed_pulse', label: '脉冲种子', color: DARK.amber },
            { key: 'critical', label: '临界晋级', color: DARK.blue },
            { key: 'diverge_watch', label: '主线分歧', color: DARK.purple },
            { key: 'diverge_dropped', label: '主线衰竭', color: DARK.red }
        ];
        var items = defs.map(function (d) {
            return { label: d.label, value: (watch[d.key] || []).length, color: d.color };
        }).filter(function (it) { return it.value > 0; });
        if (!items.length) return '';
        return reviewChartBars(items, {
            title: '次日跟踪池构成',
            max: Math.max.apply(null, items.map(function (i) { return i.value; })) || 1,
            row: 24, labelWidth: 96,
            color: function (it) { return it.color; },
            fmt: function (v) { return v + '只'; }
        });
    }

    // 复盘统计 → 完整 HTML (六大方向图表化 + 精简文本 + 进阶 + 一致性校验)
    function buildReviewHtml(data) {
        if (!data || !data.update_date) return '<div class="empty-msg">暂无复盘数据, 请先运行 rps_calc.py</div>';
        var m = data.market || {}, res = data.resonance || {}, tier = data.tier || {};
        var rot = data.rotation || {}, heal = data.health || {}, risk = data.risk || {};
        var watch = data.watch || {}, sent = data.sentiment || {}, style = data.style || {};
        var cons = data.consistency || {};
        var card = function (title, body) {
            return '<div style="background:' + DARK.cardBg + ';border:1px solid ' + DARK.cardBorder + ';border-radius:8px;padding:10px 12px;margin-bottom:10px;">' +
                '<div style="font-weight:700;color:' + DARK.title + ';margin-bottom:6px;border-left:3px solid ' + DARK.red + ';padding-left:6px;">' + title + '</div>' +
                '<div style="font-size:12px;line-height:1.9;color:' + DARK.text + ';word-break:break-all;">' + body + '</div></div>';
        };
        var fmtRatio = function (x) { return _escTxt(x.name) + '(' + Number(x.ratio).toFixed(0) + '%)'; };
        var diffTxt = (m.diff5 === undefined || m.diff5 === null) ? '--' : (m.diff5 > 0 ? '+' + m.diff5 : m.diff5);
        var pctTxt = (typeof m.high_score_count_pct !== 'number' || isNaN(m.high_score_count_pct)) ? '--' : m.high_score_count_pct.toFixed(0) + '%';
        // 深色面板容器: 复盘整体作为一块金融终端风格深色区域
        var html = '<div style="font-size:12px;background:' + DARK.panelBg + ';border-radius:10px;padding:10px;border:1px solid ' + DARK.cardBorder + ';">';
        // 一、市场整体定调: 图表(三档条形图) + 一句摘要
        var mBody = '<b>市场动量：</b>' + fmtReviewTone(m.tone) + '；RPS5≥90 <b>' +
            (m.high5 === undefined ? '--' : m.high5) + '</b> 个（较前日 ' + diffTxt + '）、RPS10≥90 <b>' +
            (m.high10 === undefined ? '--' : m.high10) + '</b> 个、RPS20≥90 <b>' +
            (m.high20 === undefined ? '--' : m.high20) + '</b> 个；高分板块历史分位 <b>' + pctTxt + '</b>。' + _escTxt(m.note || '');
        if (cons.conflict) mBody += '<div style="color:' + DARK.red + ';font-weight:600;margin-top:4px;">' + _escTxt(cons.warning) + '</div>';
        html += card('一、市场整体定调', reviewChartMkt(m, data.total_industries) + mBody);
        // 二、主线识别: 主线构成图 + 梯队达标率图 + 精简名单
        var sec2 = [];
        if (res.core && res.core.length) sec2.push('<b>硬核主线</b>（三周期' + res.core.length + '）：' + fmtReviewSectorList(res.core));
        if (res.tough && res.tough.length) sec2.push('<b>韧性主线</b>（' + res.tough.length + '）：' + fmtReviewSectorList(res.tough));
        if (res.pulse && res.pulse.length) sec2.push('<b>短期脉冲</b>（' + res.pulse.length + '）：' + fmtReviewSectorList(res.pulse));
        if (res.diverge && res.diverge.length) sec2.push('<b>主线分歧</b>（在榜,RPS5回落）：' + fmtReviewNames(res.diverge));
        if (tier.tier1 && tier.tier1.length) sec2.push('<b>第一梯队</b>（Top10）：' + fmtReviewSectorList(tier.tier1, 10));
        if (tier.tier2 && tier.tier2.length) sec2.push('<b>第二梯队</b>（11~30）：' + fmtReviewSectorList(tier.tier2, 10));
        html += card('二、板块强弱梯队与主线',
            reviewChartResonance(res) + reviewChartTier(tier) + reviewChartSectorHeat(tier) + (sec2.length ? sec2.join('<br/>') : '暂无数据'));
        // 三、轮动动向: 轮动数量图 + 精简名单
        var sec3 = [];
        if (rot.new_in && rot.new_in.length) sec3.push('<b>今日新调入</b>（' + rot.new_in.length + '）：' + fmtReviewSectorList(rot.new_in));
        if (rot.rank_jump && rot.rank_jump.length) {
            sec3.push('<b>排名跃升Top' + rot.rank_jump.length + '</b>：' + rot.rank_jump.map(function (s) {
                return _escTxt(s.name) + '(名次+' + s.rank_change + '→第' + s.rank + '名)';
            }).join('、'));
        }
        if (rot.exits && rot.exits.length) {
            var eMap = { mainline_exit: '主线调出', pulse_exit: '脉冲调出', normal_exit: '普通调出' };
            sec3.push('<b>调出分类</b>：' + rot.exits.map(function (e) {
                return _escTxt(e.name) + '[' + _escTxt(eMap[e.exit_type] || e.exit_type) + ' ' + e.continuous + '天]';
            }).join('、'));
        }
        if (rot.repeat && rot.repeat.length) sec3.push('<b>反复轮动</b>：' + fmtReviewNames(rot.repeat));
        html += card('三、板块轮动动向', reviewChartRotation(rot) + (sec3.length ? sec3.join('<br/>') : '暂无数据'));
        // 四、健康度: 达标率图 + 精简名单
        var sec4 = [];
        if (heal.seed_high && heal.seed_high.length) sec4.push('<b>高质量种子</b>：' + heal.seed_high.map(fmtRatio).join('、'));
        if (heal.seed_pulse && heal.seed_pulse.length) sec4.push('<b>脉冲种子</b>：' + fmtReviewNames(heal.seed_pulse));
        if (heal.expand && heal.expand.length) sec4.push('<b>扩散健康</b>：' + heal.expand.map(fmtRatio).join('、'));
        if (heal.mature && heal.mature.length) sec4.push('<b>成熟主线</b>（≥5天）：' + fmtReviewNames(heal.mature));
        if (heal.mature_fatigue && heal.mature_fatigue.length) sec4.push('<b>成熟疲劳</b>（≥8天,回落）：' + heal.mature_fatigue.map(fmtRatio).join('、'));
        html += card('四、题材/板块健康度', reviewChartHealth(heal) + (sec4.length ? sec4.join('<br/>') : '暂无数据'));
        // 五、风险预警: 批量调出构成图 + 风险等级 + 分级预警
        var lvColor = { safe: DARK.green, hint: DARK.label, warning: DARK.amber, danger: DARK.red };
        var lvTxt = { safe: '安全', hint: '提示', warning: '警告', danger: '高危' };
        var rl = risk.level || 'safe';
        html += card('五、风险与退潮预警（风险等级: <span style="color:' + (lvColor[rl] || DARK.label) + ';">' + _escTxt(lvTxt[rl] || rl) + '</span>）',
            reviewChartRisk(risk) + fmtReviewWarnings(risk.warnings));
        // 六、次日跟踪清单: 跟踪池构成图 + 精简名单
        var sec6 = [];
        if (watch.seed_high && watch.seed_high.length) sec6.push('<b>高质量种子池</b>：' + watch.seed_high.map(fmtRatio).join('、'));
        if (watch.seed_pulse && watch.seed_pulse.length) sec6.push('<b>脉冲种子池</b>：' + fmtReviewNames(watch.seed_pulse));
        if (watch.critical && watch.critical.length) sec6.push('<b>临界晋级池</b>（RPS≈阈值）：' + watch.critical.slice(0, 8).map(function (c) { return _escTxt(c.name) + '(' + Number(c.rps5).toFixed(0) + ')'; }).join('、'));
        if (watch.diverge_watch && watch.diverge_watch.length) sec6.push('<b>主线分歧池</b>（掉榜分歧）：' + watch.diverge_watch.map(function (x) { return _escTxt(x.name) + '(掉' + x.drop_days + '天)'; }).join('、'));
        if (watch.diverge_dropped && watch.diverge_dropped.length) sec6.push('<span style="color:' + DARK.red + ';"><b>主线衰竭</b>（掉榜≥3天）：' + watch.diverge_dropped.map(function (x) { return _escTxt(x.name) + '(掉' + x.drop_days + '天)'; }).join('、') + '</span>');
        html += card('六、次日跟踪清单', reviewChartWatch(watch) + (sec6.length ? sec6.join('<br/>') : '暂无跟踪标的'));
        // 消息面复盘 (2026-08-23): 推荐股票中的新闻/人气/大宗信号聚合
        html += card('消息面复盘', fmtReviewNews(data.news));
        // 推荐准确率复盘
        html += card('推荐准确率复盘', fmtReviewAccuracy(data.rec_accuracy));
        // 进阶: 情绪周期图 + 风格图 + 一致性校验
        var adv = reviewChartPhase(sent) + reviewChartStyle(style) +
            '情绪周期：' + fmtReviewPhase(sent) +
            (cons.conflict ? '<div style="color:' + DARK.red + ';font-weight:600;margin-top:4px;">' + _escTxt(cons.warning) + '</div>' : '') +
            '<br/>免责声明：本复盘为 RPS 动量量化复盘, 仅供研究参考, 不构成投资建议。';
        html += card('进阶观察', adv);
        html += '</div>';
        return html;
    }

    /* ===== 消息面复盘渲染 (每日复盘 + 每周复盘共用, 2026-08-23) ===== */

    // 消息面统计 → HTML 摘要 (环境/权威利好利空/传闻/主力散户动向)
    function fmtReviewNews(nws) {
        if (!nws || !nws.has_news) {
            return '<span style="color:' + DARK.label + ';">暂无消息面信号</span>';
        }
        var envColor = { '偏暖': DARK.green, '偏冷': DARK.blue, '中性偏杂': DARK.amber };
        var parts = [];
        var envTxt = _escTxt(nws.env || '中性');
        parts.push('消息面环境: <b style="color:' + (envColor[nws.env] || DARK.label) + ';">' + envTxt + '</b>; ' +
            '推荐<b>' + (nws.stock_count || 0) + '</b>只中<b>' + (nws.hit_count || 0) + '</b>只命中, ' +
            '净影响<b>' + fmtSignedNum(nws.score_sum) + '</b>分');
        if (nws.official_pos_list && nws.official_pos_list.length) {
            parts.push('<span style="color:' + DARK.green + ';">✔ 权威利好(+2分/条):</span> ' +
                nws.official_pos_list.map(function (x) {
                    var h = x.headline ? (' — ' + _escTxt(x.headline)) : '';
                    return _escTxt(x.name) + '(' + x.count + '条)' + h;
                }).join('、'));
        }
        if (nws.official_neg_list && nws.official_neg_list.length) {
            parts.push('<span style="color:' + DARK.red + ';">✘ 权威利空(-3分/条):</span> ' +
                nws.official_neg_list.map(function (x) {
                    var h = x.headline ? (' — ' + _escTxt(x.headline)) : '';
                    return _escTxt(x.name) + '(' + x.count + '条)' + h;
                }).join('、'));
        }
        if (nws.rumor_list && nws.rumor_list.length) {
            parts.push('<span style="color:' + DARK.amber + ';">◯ 传闻消息(仅提示):</span> ' +
                nws.rumor_list.map(function (x) {
                    return _escTxt(x.name) + '(好' + x.pos + '/空' + x.neg + ')';
                }).join('、'));
        }
        if (nws.tags_count) {
            var extra = [];
            for (var t in nws.tags_count) {
                if (nws.tags_count.hasOwnProperty(t) && t !== '利好消息' && t !== '利空消息' &&
                    t !== '传闻利好' && t !== '消息待证') {
                    extra.push(_escTxt(t) + nws.tags_count[t] + '只');
                }
            }
            if (extra.length) parts.push('<b>主力/散户动向:</b> ' + extra.join('; '));
        }
        if (nws.note) parts.push('<span style="color:' + DARK.label + ';">' + _escTxt(nws.note) + '</span>');
        return parts.join('<br/>');
    }

    // 带符号数字 → '+3' / '-2' / '0'
    function fmtSignedNum(v) {
        var n = Number(v) || 0;
        if (n > 0) return '+' + n;
        if (n < 0) return String(n);
        return '0';
    }

    /* ===== 推荐准确率复盘渲染 (每日复盘 + 每周复盘共用) ===== */

    // 日度推荐准确率 → HTML 摘要
    function fmtReviewAccuracy(acc) {
        if (!acc || !acc.has_data) {
            return '<span style="color:' + DARK.label + ';">暂无预测准确率数据</span>';
        }
        var hr = (Number(acc.hit_rate) || 0) * 100;
        var ag = Number(acc.avg_gain) || 0;
        var hrColor = hr >= 50 ? DARK.green : (hr >= 30 ? DARK.amber : DARK.red);
        var parts = [];
        parts.push('整体命中率: <b style="color:' + hrColor + ';">' + hr.toFixed(0) + '%</b>' +
            '(' + (acc.samples || 0) + '样本), 平均涨幅 <b style="color:' + (ag >= 0 ? DARK.green : DARK.red) + ';">' +
            (ag >= 0 ? '+' : '') + ag.toFixed(1) + '%</b>; 预测平均概率' + (Number(acc.avg_up_prob) || 0).toFixed(1) + '%');
        if (acc.today_count) {
            var tp = acc.today_avg_prob;
            parts.push('今日推荐<b>' + acc.today_count + '</b>只, 平均上涨概率' +
                (tp !== null && tp !== undefined ? '<b>' + Number(tp).toFixed(1) + '%</b>' : '无'));
        }
        var byConf = acc.by_confidence || {};
        var confParts = [];
        for (var i = 0; i < 3; i++) {
            var lv = ['高', '中', '低'][i];
            var v = byConf[lv];
            if (v && v.samples) {
                var cHr = (Number(v.hit_rate) || 0) * 100;
                var cAg = Number(v.avg_gain) || 0;
                confParts.push(_escTxt(lv) + v.samples + '样本命中率<b style="color:' +
                    (cHr >= 50 ? DARK.green : (cHr >= 30 ? DARK.amber : DARK.red)) + ';">' +
                    cHr.toFixed(0) + '%</b>(平均' + (cAg >= 0 ? '+' : '') + cAg.toFixed(1) + '%)');
            }
        }
        if (confParts.length) parts.push('分置信度: ' + confParts.join('; '));
        if (acc.best && acc.best.length) {
            parts.push('<span style="color:' + DARK.green + ';"><b>最佳预测</b>:</span> ' +
                acc.best.map(function (x) { return _escTxt(x.name) + '(+' + Number(x.gain).toFixed(1) + '%)'; }).join('、'));
        }
        if (acc.worst && acc.worst.length) {
            parts.push('<span style="color:' + DARK.red + ';"><b>最差预测</b>:</span> ' +
                acc.worst.map(function (x) { return _escTxt(x.name) + '(' + Number(x.gain).toFixed(1) + '%)'; }).join('、'));
        }
        if (acc.note) parts.push('<span style="color:' + DARK.label + ';">' + _escTxt(acc.note) + '</span>');
        return parts.join('<br/>');
    }

    // 周度推荐准确率 → HTML 摘要
    function fmtReviewWeekAccuracy(acc) {
        if (!acc || !acc.has_data) {
            return '<span style="color:' + DARK.label + ';">暂无本周推荐准确率数据</span>';
        }
        var hr = (Number(acc.week_hit_rate) || 0) * 100;
        var ag = Number(acc.week_avg_gain) || 0;
        var hrColor = hr >= 50 ? DARK.green : (hr >= 30 ? DARK.amber : DARK.red);
        var parts = [];
        parts.push('本周已验证<b>' + (acc.week_samples || 0) + '</b>样本, 命中率 <b style="color:' + hrColor + ';">' +
            hr.toFixed(0) + '%</b>, 平均涨幅 <b style="color:' + (ag >= 0 ? DARK.green : DARK.red) + ';">' +
            (ag >= 0 ? '+' : '') + ag.toFixed(1) + '%</b>');
        var byConf = acc.by_confidence || {};
        var confParts = [];
        for (var i = 0; i < 3; i++) {
            var lv = ['高', '中', '低'][i];
            var v = byConf[lv];
            if (v) {
                var cHr = (Number(v.hit_rate) || 0) * 100;
                var cAg = Number(v.avg_gain) || 0;
                confParts.push(_escTxt(lv) + v.samples + '样本命中率<b style="color:' +
                    (cHr >= 50 ? DARK.green : (cHr >= 30 ? DARK.amber : DARK.red)) + ';">' +
                    cHr.toFixed(0) + '%</b>(平均' + (cAg >= 0 ? '+' : '') + cAg.toFixed(1) + '%)');
            }
        }
        if (confParts.length) parts.push('分置信度: ' + confParts.join('; '));
        if (acc.best && acc.best.length) {
            parts.push('<span style="color:' + DARK.green + ';"><b>最佳预测</b>:</span> ' +
                acc.best.map(function (x) { return _escTxt(x.name) + '(+' + Number(x.gain).toFixed(1) + '%)'; }).join('、'));
        }
        if (acc.worst && acc.worst.length) {
            parts.push('<span style="color:' + DARK.red + ';"><b>最差预测</b>:</span> ' +
                acc.worst.map(function (x) { return _escTxt(x.name) + '(' + Number(x.gain).toFixed(1) + '%)'; }).join('、'));
        }
        if (acc.note) parts.push('<span style="color:' + DARK.label + ';">' + _escTxt(acc.note) + '</span>');
        return parts.join('<br/>');
    }

    /* ===== 每周复盘渲染 (2026-08-23, 可单测) ===== */

    // 每周复盘统计 → 完整 HTML (本周定调/本周主线/本周轮动/情绪演变/消息面/下周关注)
    function buildWeeklyReviewHtml(data) {
        if (!data || !data.week_id) return '<div class="empty-msg">暂无周度复盘数据, 请先运行 rps_calc.py（每周日生成一次）</div>';
        var tone = data.tone || {}, rot = data.rotation || {}, sent = data.sentiment || {};
        var watch = data.watch || {};
        var card = function (title, body) {
            return '<div style="background:' + DARK.cardBg + ';border:1px solid ' + DARK.cardBorder + ';border-radius:8px;padding:10px 12px;margin-bottom:10px;">' +
                '<div style="font-weight:700;color:' + DARK.title + ';margin-bottom:6px;border-left:3px solid ' + DARK.red + ';padding-left:6px;">' + title + '</div>' +
                '<div style="font-size:12px;line-height:1.9;color:' + DARK.text + ';word-break:break-all;">' + body + '</div></div>';
        };
        var html = '<div style="font-size:12px;background:' + DARK.panelBg + ';border-radius:10px;padding:10px;border:1px solid ' + DARK.cardBorder + ';">';
        // 一、本周定调: 每日高分板块数条形图 + 摘要
        var tItems = (tone.series || []).map(function (x) {
            return { label: x.date, value: x.high_n, color: DARK.red };
        });
        var tChart = tItems.length ? reviewChartBars(tItems, {
            title: '本周每日高分板块数(RPS5≥90)',
            max: Math.max.apply(null, tItems.map(function (i) { return i.value; })) || 1,
            row: 22, labelWidth: 64,
            color: function () { return DARK.red; },
            fmt: function (v) { return v + '个'; }
        }) : '';
        html += card('一、本周定调', tChart + '周度强度: <b>' + _escTxt(tone.tone || '周度中性') + '</b>; ' +
            _escTxt(tone.note || ''));
        // 二、本周主线: 在榜天数排序名单
        var sec2 = (data.mainlines || []).map(function (x) {
            return _escTxt(x.name) + '(在榜<b>' + x.days_on + '</b>天, RPS5=' + Number(x.rps5).toFixed(0) +
                ', 第' + x.rank + '名)';
        });
        html += card('二、本周主线(在榜≥3天)', sec2.length ? sec2.join('<br/>') : '无持续性主线(缺乏连续上榜板块)');
        // 三、本周轮动: 持续主线 / 本周新晋 / 本周衰退
        var sec3 = [];
        if (rot.sustained && rot.sustained.length) sec3.push('<b>持续主线</b>(全周在榜≥4天): ' +
            rot.sustained.slice(0, 8).map(function (x) { return _escTxt(x.name) + '(' + x.days_on + '天,RPS' + Number(x.rps5).toFixed(0) + ')'; }).join('、'));
        if (rot.new_in && rot.new_in.length) sec3.push('<b>本周新晋</b>(周初不在榜→周末在榜): ' +
            rot.new_in.slice(0, 8).map(function (x) { return _escTxt(x.name) + '(' + x.days_on + '天,RPS' + Number(x.rps5).toFixed(0) + ')'; }).join('、'));
        if (rot.dropped && rot.dropped.length) sec3.push('<span style="color:' + DARK.red + ';"><b>本周衰退</b>(周初在榜→周末不在榜): ' +
            rot.dropped.slice(0, 8).map(function (x) { return _escTxt(x.name) + '(在榜' + x.days_on + '天)'; }).join('、') + '</span>');
        html += card('三、本周轮动', sec3.length ? sec3.join('<br/>') : '无明显轮动');
        // 四、本周情绪演变: 逐日相位序列
        var sText = '';
        if (sent.series && sent.series.length) {
            sText = sent.series.map(function (x) { return _escTxt(x.date) + '(' + _escTxt(x.phase || '') + ')'; }).join('、');
        }
        html += card('四、本周情绪演变', (sent.trend ? ('<b>' + _escTxt(sent.trend) + '</b><br/>') : '') +
            (sText ? ('逐日: ' + sText) : '无情绪历史数据'));
        // 五、消息面周度快照: 复用每日消息面渲染
        html += card('五、消息面周度快照(周五推荐)', fmtReviewNews(data.news));
        // 推荐准确率周度复盘
        html += card('本周推荐准确率', fmtReviewWeekAccuracy(data.rec_accuracy));
        // 六、下周关注: 临界晋级/种子/潜力板块/主线分歧
        var sec6 = [];
        if (watch.critical && watch.critical.length) sec6.push('<b>临界晋级池</b>(RPS≈阈值, 连升): ' +
            watch.critical.slice(0, 8).map(function (c) { return _escTxt(c.name) + '(' + Number(c.rps5).toFixed(0) + ')'; }).join('、'));
        if (watch.seed_high && watch.seed_high.length) sec6.push('<b>高质量种子</b>(首日入选+扩散达标): ' +
            watch.seed_high.slice(0, 8).map(function (s) { return _escTxt(s.name) + '(' + Number(s.ratio).toFixed(0) + '%)'; }).join('、'));
        if (watch.potential && watch.potential.length) sec6.push('<b>潜力板块</b>(RPS5∈[88,90) 内部达标): ' +
            watch.potential.slice(0, 8).map(function (p) { return _escTxt(p.name) + '(' + Number(p.rps5).toFixed(0) + ')'; }).join('、'));
        if (watch.diverge_watch && watch.diverge_watch.length) sec6.push('<b>主线分歧</b>(RPS20在榜RPS5掉, 良性低吸观察): ' +
            watch.diverge_watch.slice(0, 8).map(function (x) { return _escTxt(x.name) + '(掉' + x.drop_days + '天)'; }).join('、'));
        html += card('六、下周关注', sec6.length ? sec6.join('<br/>') : '暂无关注标的');
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
            parts.push('<span class="tag-rec ' + _tagCls(t, sectors) + '">' + _escTxt(t) + '</span>');
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

    // A. 顶部基础行情头部: 名称/代码(可复制)/现价/涨跌 + 状态标签 + 行情格(颜色统一: 涨红/跌绿/平灰)
    //    字段: 今开/最高/最低/昨收/量比/振幅/换手/总额/总市值/流通市值/市盈TTM
    function fmtDetailHeader(rt) {
        if (!rt) return '';
        var name = rt.name || '--';
        var code = rt.code || '--';
        var price = fmtDetailNum(rt.price, 2);
        var cls = clsColor(rt.change);
        var chg = (rt.change === null || rt.change === undefined) ? '--' : ((Number(rt.change) > 0 ? '+' : '') + fmtDetailNum(rt.change, 2));
        var pct = fmtDetailPct(rt.changePercent, 2);
        var pre = Number(rt.preClose);
        // 状态标签: 盘中/收盘/盘后/停牌 (纯展示); 停牌给提示文案
        var status = rt.marketStatus || '';
        var statusCls = status === '盘中' ? 'pos' : (status === '停牌' ? 'susp' : '');
        var statusHtml = status ? '<span class="dh-status ' + statusCls + '">' + _escTxt(status) + '</span>' : '';
        var suspHtml = status === '停牌' ? '<div class="dh-susp">该股当前停牌，以下为最近交易日行情，仅供查看。</div>' : '';
        var cell = function (k, v, extra) {
            return '<div class="dh-cell"><span class="k">' + k + '</span><span class="v ' + (extra || '') + '">' + v + '</span></div>';
        };
        // 与昨收对比着色: 上涨红 / 下跌绿 / 平盘灰(无类)
        var relCls = function (v) {
            if (v === null || v === undefined || isNaN(Number(v)) || !isFinite(pre)) return '';
            var n = Number(v);
            return n > pre ? 'pos' : (n < pre ? 'neg' : '');
        };
        var grid = [
            cell('今开', fmtDetailNum(rt.open, 2), relCls(rt.open)),
            cell('最高', fmtDetailNum(rt.high, 2), relCls(rt.high)),
            cell('最低', fmtDetailNum(rt.low, 2), relCls(rt.low)),
            cell('昨收', fmtDetailNum(rt.preClose, 2)),
            cell('量比', fmtDetailNum(rt.volumeRatio, 2)),
            cell('振幅', (rt.amplitude === null || rt.amplitude === undefined) ? '--' : fmtDetailNum(rt.amplitude, 2) + '%'),
            cell('换手', (rt.turnover === null || rt.turnover === undefined) ? '--' : fmtDetailNum(rt.turnover, 2) + '%'),
            cell('总额', fmtWan(rt.totalAmount)),
            cell('总市值', fmtWan(rt.totalMarket)),
            cell('流通市值', fmtWan(rt.circ_mv)),
            cell('市盈TTM', fmtDetailNum(rt.pe_ttm, 2)),
        ];
        return '<div class="dh">' +
            '<div class="dh-head">' +
                '<span class="dh-name" title="点击复制名称/代码" onclick="copyStockInfo()">' + _escTxt(name) + '</span>' +
                '<span class="dh-code" title="点击复制" onclick="copyStockInfo()">' + _escTxt(code) + '</span>' +
                statusHtml +
                '<span class="dh-price ' + cls + '">' + price + '</span>' +
                '<span class="dh-chg ' + cls + '">' + chg + '  ' + pct + '</span>' +
            '</div>' +
            suspHtml +
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
        var r = 26, c = 2 * Math.PI * r;
        var pct = Math.max(0, Math.min(100, total));
        var dash = (pct / 100 * c).toFixed(1);
        var ai = sc.aiText || '暂无 AI 解盘文本';
        var rank = sc.industryRank || {};
        var mrank = sc.marketRank || {};
        // AI 解盘: 长文本折叠展开 + 复制 (仅查看)
        var aiLong = ai.length > 90;
        var aiBody = aiLong
            ? '<span class="ai-body">' + _escTxt(ai.slice(0, 90)) + '…</span><a class="ai-link" data-full="' + _escAttr(ai) + '" onclick="toggleAiText(this)">展开</a>'
            : '<span class="ai-body">' + _escTxt(ai) + '</span>';
        aiBody += ' <a class="ai-link" onclick="copyAiText()">复制</a>';
        return '<div class="dc score-card">' +
            '<div class="dc-title">综合评分 &amp; AI 智能解盘</div>' +
            '<div class="score-body">' +
                '<div class="ring-wrap">' +
                    '<svg width="72" height="72" viewBox="0 0 72 72">' +
                        '<circle cx="36" cy="36" r="' + r + '" fill="none" stroke="#e5e7eb" stroke-width="6"/>' +
                        '<circle cx="36" cy="36" r="' + r + '" fill="none" stroke="' + (pct >= 70 ? '#16a34a' : pct >= 50 ? '#f59e0b' : '#9ca3af') + '" stroke-width="6" stroke-linecap="round" stroke-dasharray="' + dash + ' ' + c.toFixed(1) + '" transform="rotate(-90 36 36)"/>' +
                        '<text x="36" y="39" text-anchor="middle" font-size="14" font-weight="700" fill="#1e293b">' + fmtDetailNum(total, 0) + '</text>' +
                        '<text x="36" y="52" text-anchor="middle" font-size="8" fill="#94a3b8">综合评分</text>' +
                    '</svg>' +
                    '<div class="beat">打败 <b>' + (sc.beatStock || '--') + '</b> 股票</div>' +
                '</div>' +
                '<div class="score-ranks">' +
                    '<div class="sr-row"><span class="k">行业排名</span><span class="v">' + (rank.cur || '--') + ' / ' + (rank.total || '--') + '</span></div>' +
                    '<div class="sr-row"><span class="k">市场排名</span><span class="v">' + (mrank.cur || '--') + ' / ' + (mrank.total || '--') + '</span></div>' +
                    '<div class="sr-row"><span class="k">评分</span><span class="v">' + fmtDetailNum(total, 2) + ' / 100</span></div>' +
                '</div>' +
            '</div>' +
            '<div class="ai-text">💡 ' + aiBody + '</div>' +
        '</div>';
    }

    // 风险/亮点标签说明词典 (点击标签弹出, 纯查看, 无任何操作)
    function explainTag(tag) {
        var m = {
            '流动性风险': '指该股近期成交活跃度下降或盘口挂单稀薄, 买卖盘深度不足, 大额进出可能引起价格明显波动。',
            '市盈率偏高(PE>150)': '当前滚动市盈率高于150倍, 估值处于历史高位, 一旦业绩不及预期, 股价回调压力较大。',
            '当日涨停, 短期波动风险': '当日股价封涨停, 短期情绪过热, 次日高开低走或继续逼空的概率均存在, 波动加大。',
            '量比异常放大, 谨防追高': '量比明显高于常态(>5), 说明当日成交异常活跃, 可能有资金分歧, 追高需谨慎。',
            '股价低于20日均线10%以上': '股价已跌破20日均线10%以上, 中期趋势走弱, 处于弱势区间。',
            '资金面回暖': '近期主力资金净流入转正或流出收窄, 资金面边际改善。',
            '均线系统完好': '短期均线多头排列, 股价运行在均线上方, 技术形态健康。',
            '量价配合良好': '股价上涨伴随成交量放大, 涨跌与量能方向一致, 走势较扎实。',
        };
        return m[tag] || ('「' + tag + '」由行情/技术指标自动推导, 仅作历史信号参考, 不构成投资建议。');
    }

    // F. 风险 & 亮点标签 (点击弹出原始说明, 仅查看)
    function fmtDetailRisk(rh) {
        if (!rh) return '';
        var pill = function (t, cls) {
            return '<span class="tag-pill ' + cls + '" onclick="showTagTip(this)" data-tag="' + _escAttr(t) + '">' + _escTxt(t) + '</span>';
        };
        var risk = (rh.riskTags || []).map(function (t) { return pill(t, 'risk'); }).join('');
        var hls = (rh.highlightTags || []).map(function (t) { return pill(t, 'good'); }).join('');
        return '<div class="dc"><div class="dc-title">风险 &amp; 亮点</div>' +
            '<div class="tags-row">' + (risk || '<span class="muted">暂无风险标签</span>') + '</div>' +
            '<div class="tags-row">' + (hls || '<span class="muted">暂无亮点标签</span>') + '</div>' +
            '<div class="note">点击标签可查看说明，仅供查看，无任何操作含义。</div>' +
        '</div>';
    }

    // G. 历史回测: 单日上涨比率/平均涨幅 + 胜率占比条形图 (仅统计展示, 不执行模拟交易)
    function fmtDetailBacktest(bt) {
        if (!bt) return '';
        var win = parseFloat(String(bt.riseRate).replace('%', ''));
        var bar = '';
        if (!isNaN(win)) {
            bar = '<div class="bt-bar">' +
                '<div class="bt-bar-in" style="width:' + win.toFixed(1) + '%"></div>' +
                '<div class="bt-bar-label"><span class="pos">上涨 ' + win.toFixed(1) + '%</span><span class="neg">下跌 ' + (100 - win).toFixed(1) + '%</span></div>' +
            '</div>';
        }
        return '<div class="dc"><div class="dc-title">历史回测（近 ' + (bt.days || '--') + ' 个交易日）</div>' +
            '<div class="bt-grid">' +
                '<div class="bt-cell"><div class="v">' + (bt.riseRate || '--') + '</div><div class="k">单日上涨比率</div></div>' +
                '<div class="bt-cell"><div class="v">' + (bt.avgIncrease || '--') + '</div><div class="k">平均涨幅</div></div>' +
            '</div>' +
            bar +
            '<div class="note">回测统计基于历史单日涨跌，仅供编程学习参考，不构成买卖建议。</div>' +
        '</div>';
    }

    // H. 个股印象: 阶段涨幅对比 (个股5/20/60日小折线 + 个股/行业/沪深300 对比条, 仅查看)
    function fmtDetailImpression(imp) {
        if (!imp) return '';
        var sr = imp.stageRise || {};
        var sd = imp.stageDetail || {};
        var pct = function (v) {
            if (v === '--' || v === null || v === undefined) return '--';
            return v;
        };
        // 个股阶段涨幅小折线 (5日/20日/60日)
        var spark = '';
        var s5 = Number(sd.stock5), s20 = Number(sd.stock20), s60 = Number(sd.stock60);
        if (!isNaN(s5) && !isNaN(s20) && !isNaN(s60)) {
            var vals = [s5, s20, s60];
            var maxV = Math.max.apply(null, vals.concat([0]));
            var minV = Math.min.apply(null, vals.concat([0]));
            var rng = (maxV - minV) || 1;
            var W = 130, H = 36, px = 10;
            var pts = vals.map(function (v, i) {
                var x = px + i * ((W - 20) / 2);
                var y = 6 + (H - 12) * (1 - (v - minV) / rng);
                return x.toFixed(1) + ',' + y.toFixed(1);
            }).join(' ');
            var color = s60 >= 0 ? '#dc2626' : '#16a34a';
            spark = '<div class="stage-spark">' +
                '<svg width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '">' +
                    '<line x1="10" y1="' + (6 + (H - 12) * (1 - (0 - minV) / rng)).toFixed(1) + '" x2="' + (W - 10) + '" y2="' + (6 + (H - 12) * (1 - (0 - minV) / rng)).toFixed(1) + '" stroke="#e2e8f0" stroke-dasharray="3 3"/>' +
                    '<polyline points="' + pts + '" fill="none" stroke="' + color + '" stroke-width="1.6"/>' +
                    '<text x="8" y="' + (H - 2) + '" font-size="8" fill="#94a3b8">5日</text>' +
                    '<text x="' + (W / 2 - 8) + '" y="' + (H - 2) + '" font-size="8" fill="#94a3b8">20日</text>' +
                    '<text x="' + (W - 24) + '" y="' + (H - 2) + '" font-size="8" fill="#94a3b8">60日</text>' +
                '</svg>' +
            '</div>';
        }
        return '<div class="dc"><div class="dc-title">个股印象 · 阶段涨幅对比</div>' +
            spark +
            '<div class="sr-bar">' +
                '<div class="srb-row"><span class="k">个股</span><span class="v">' + pct(sr.stock) + '</span></div>' +
                '<div class="srb-row"><span class="k">行业</span><span class="v">' + pct(sr.industry) + '</span></div>' +
                '<div class="srb-row"><span class="k">沪深300</span><span class="v">' + pct(sr.hs300) + '</span></div>' +
            '</div>' +
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
        return '<div class="zy-card"><div class="zy-title">主力捉妖 · 当日资金' + _escTxt(row.date || '') + '</div>' +
            '<div class="zy-grid">' +
                cell('主力', row.zhuil) + cell('游资', row.youzi) + cell('散户', row.sanhu) +
            '</div>' +
            '<div class="zy-sign ' + signCls + '">' + sign + '</div>' +
        '</div>';
    }

    // K. 价值分析: 当前PE + 估值历史柱状图 (支持 1/3/5 年区间切换, 仅查看)
    function fmtDetailValue(va, rangeYears) {
        if (!va) return '';
        var ry = [1, 3, 5].indexOf(Number(rangeYears)) >= 0 ? Number(rangeYears) : 1;
        var hist = va.peHistory || [];
        var bars = '';
        if (hist.length) {
            var vals = hist.map(function (h) { return Number(h.value); }).filter(function (v) { return !isNaN(v); });
            var maxV = Math.max.apply(null, vals.concat([1]));
            // 区间换算: 约250根/年; 数据不足时取全部
            var nMax = 250 * ry;
            var seg = hist.length > nMax ? hist.slice(-nMax) : hist;
            bars = '<div class="pe-bars">' + seg.map(function (h) {
                var v = Number(h.value);
                var hgt = isNaN(v) ? 2 : Math.max(2, Math.round(v / maxV * 40));
                return '<div class="pe-bar" style="height:' + hgt + 'px;" title="' + _escAttr(h.time) + ' PE ' + fmtDetailNum(v, 1) + '"></div>';
            }).join('') + '</div>';
        }
        var rangeBtns = '<div class="pe-range">' +
            [1, 3, 5].map(function (r) {
                return '<button data-r="' + r + '" class="' + (r === ry ? 'active' : '') + '" onclick="setValueRange(' + r + ')">近' + r + '年</button>';
            }).join('') +
        '</div>';
        return '<div class="dc"><div class="dc-title">价值分析</div>' +
            '<div class="dl"><span class="k">当前PE(TTM):</span> <span class="v">' + fmtDetailNum(va.pePercent, 2) + '</span></div>' +
            rangeBtns +
            (bars || '<div class="muted">暂无 PE 历史曲线数据</div>') +
            (hist.length < 250 * ry ? '<div class="note">当前仅提供约 ' + Math.max(1, Math.round(hist.length / 250)) + ' 年历史数据。</div>' : '') +
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

    // 技术面 Tab: MACD/KDJ/RSI/BOLL 当前值 (2x2 指标小卡片, 观感更整齐)
    function fmtDetailTechnical(ind) {
        if (!ind) return '';
        var macd = ind.macd, kdj = ind.kdj, rsi = ind.rsi, boll = ind.boll;
        var last = function (arr) { return arr && arr.length ? arr[arr.length - 1] : null; };
        var m = last(macd), k = last(kdj), r = last(rsi), b = last(boll);
        var histCls = function (v) {
            return v > 0 ? 'pos' : (v < 0 ? 'neg' : '');
        };
        var card = function (title, body) {
            return '<div class="ft-card"><div class="ft-card-k">' + title + '</div><div class="ft-card-v">' + body + '</div></div>';
        };
        var macdBody = m
            ? ('DIF ' + fmtDetailNum(m.dif, 3) + '<br>DEA ' + fmtDetailNum(m.dea, 3) + '<br><span class="' + histCls(Number(m.hist)) + '">柱 ' + fmtDetailNum(m.hist, 3) + '</span>')
            : '<span class="muted">暂无</span>';
        var kdjBody = k
            ? ('K ' + fmtDetailNum(k.k, 1) + '<br>D ' + fmtDetailNum(k.d, 1) + '<br>J ' + fmtDetailNum(k.j, 1))
            : '<span class="muted">暂无</span>';
        var rsiBody = (r !== null && r !== undefined)
            ? fmtDetailNum(r, 1)
            : '<span class="muted">暂无</span>';
        var bollBody = (b && b.mid !== null && b.mid !== undefined)
            ? ('上 ' + fmtDetailNum(b.upper, 2) + '<br>中 ' + fmtDetailNum(b.mid, 2) + '<br>下 ' + fmtDetailNum(b.lower, 2))
            : '<span class="muted">暂无</span>';
        return '<div class="dc"><div class="dc-title">技术面指标（当前值）</div>' +
            '<div class="ft-grid">' +
                card('MACD', macdBody) +
                card('KDJ', kdjBody) +
                card('RSI(6)', rsiBody) +
                card('BOLL', bollBody) +
            '</div>' +
        '</div>';
    }


    // 浏览器: 挂到全局（index.html 主脚本直接按函数名调用）
    root.fmtStockNum = fmtStockNum;
    root.fmtStockPct = fmtStockPct;
    root.fmtStockVol = fmtStockVol;
    root.stockRowHtml = stockRowHtml;
    root.dash = dash;
    root.fmtWeeklyTags = fmtWeeklyTags;
    root._tagCls = _tagCls;
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
    root._escAttr = _escAttr;
    root._escJs = _escJs;
    root.explainTag = explainTag;
    root.reviewChartBars = reviewChartBars;
    root.reviewChartPhase = reviewChartPhase;
    root.reviewChartStyle = reviewChartStyle;
    root.reviewChartMkt = reviewChartMkt;
    root.reviewChartResonance = reviewChartResonance;
    root.reviewChartTier = reviewChartTier;
    root.reviewChartSectorHeat = reviewChartSectorHeat;
    root.reviewChartSentimentHistory = reviewChartSentimentHistory;
    root.reviewChartRotation = reviewChartRotation;
    root.reviewChartHealth = reviewChartHealth;
    root.reviewChartRisk = reviewChartRisk;
    root.reviewChartWatch = reviewChartWatch;
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
    root.fmtDetailZhuoyao = fmtDetailZhuoyao;
    root.fmtDetailValue = fmtDetailValue;
    root.fmtDetailNews = fmtDetailNews;
    root.fmtDetailF10 = fmtDetailF10;
    root.fmtDetailTechnical = fmtDetailTechnical;
    // 每日复盘消息面 + 每周复盘 (2026-08-23): 必须挂 root 供浏览器直接调用
    root.fmtReviewNews = fmtReviewNews;
    root.fmtSignedNum = fmtSignedNum;
    root.fmtReviewAccuracy = fmtReviewAccuracy;
    root.fmtReviewWeekAccuracy = fmtReviewWeekAccuracy;
    root.buildWeeklyReviewHtml = buildWeeklyReviewHtml;

    // Node: 导出供单元测试
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            fmtStockNum: fmtStockNum,
            fmtStockPct: fmtStockPct,
            fmtStockVol: fmtStockVol,
            stockRowHtml: stockRowHtml,
            dash: dash,
            fmtWeeklyTags: fmtWeeklyTags,
            _tagCls: _tagCls,
            fmtWeeklyMktLevel: fmtWeeklyMktLevel,
            fmtWeeklyFund: fmtWeeklyFund,
            fmtWeeklyCap: fmtWeeklyCap,
            fmtReviewNames: fmtReviewNames,
            fmtReviewSector: fmtReviewSector,
            fmtReviewSectorList: fmtReviewSectorList,
            fmtReviewTone: fmtReviewTone,
            fmtReviewWarnings: fmtReviewWarnings,
            fmtReviewPhase: fmtReviewPhase,
            fmtReviewNews: fmtReviewNews,
            fmtSignedNum: fmtSignedNum,
            fmtReviewAccuracy: fmtReviewAccuracy,
            fmtReviewWeekAccuracy: fmtReviewWeekAccuracy,
            buildReviewHtml: buildReviewHtml,
            buildWeeklyReviewHtml: buildWeeklyReviewHtml,
            fmtPredUpProb: fmtPredUpProb,
            fmtPredGain: fmtPredGain,
            fmtPredStop: fmtPredStop,
            fmtPredConf: fmtPredConf,
            buildPredictionBox: buildPredictionBox,
            _escTxt: _escTxt,
            _escAttr: _escAttr,
            explainTag: explainTag,
            reviewChartBars: reviewChartBars,
            reviewChartPhase: reviewChartPhase,
            reviewChartStyle: reviewChartStyle,
            reviewChartMkt: reviewChartMkt,
            reviewChartResonance: reviewChartResonance,
            reviewChartTier: reviewChartTier,
            reviewChartSectorHeat: reviewChartSectorHeat,
            reviewChartSentimentHistory: reviewChartSentimentHistory,
            reviewChartRotation: reviewChartRotation,
            reviewChartHealth: reviewChartHealth,
            reviewChartRisk: reviewChartRisk,
            reviewChartWatch: reviewChartWatch,
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
            fmtDetailZhuoyao: fmtDetailZhuoyao,
            fmtDetailValue: fmtDetailValue,
            fmtDetailNews: fmtDetailNews,
            fmtDetailF10: fmtDetailF10,
            fmtDetailTechnical: fmtDetailTechnical
        };
    }
})(typeof window !== 'undefined' ? window : globalThis);
