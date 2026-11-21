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
        // 双阈值分层 v8: 弹性备选池标签 → 橙色 reserve 标识 (板块贝塔弱/靠个股阿尔法, 与核心池红色板块区分)
        if (t === '弹性备选') return 'reserve';
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

    // 市场强弱定调 → 带色 span (v2: 全面强/结构性强/中性偏强/中性/中性偏弱/弱; 白底深字)
    function fmtReviewTone(tone) {
        if (!tone) return '--';
        var map = { '全面强': DARK.green, '强': DARK.green, '结构性强': DARK.green,
                    '中性偏强': DARK.blue, '中性': DARK.blue, '中性偏弱': DARK.amber, '弱': DARK.red };
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

    /* ===== V2.0 新增模块渲染 (需求 4.1-4.5 / 3.10, 可单测) ===== */

    // 板块名 chips (data-sector 由 index.html 委托点击 → 二级板块详情页)
    function fmtReviewSectorChips(arr, max) {
        if (!arr || !arr.length) return '';
        var n = max || 8;
        var chips = arr.slice(0, n).map(function (s) {
            var name = (typeof s === 'string') ? s : (s && s.name) || '';
            if (!name) return '';
            return '<span class="rv-chip" data-sector="' + _escAttr(name) +
                '" style="cursor:pointer;display:inline-block;padding:2px 8px;border:1px solid ' + DARK.cardBorder +
                ';border-radius:10px;background:' + DARK.cardBg + ';color:' + DARK.blue + ';font-size:11px;">' +
                _escTxt(name) + '</span>';
        }).filter(Boolean).join('');
        if (arr.length > n) chips += '<span style="color:' + DARK.label + ';font-size:11px;">等' + arr.length + '个</span>';
        return '<div style="display:flex;flex-wrap:wrap;gap:6px;margin:4px 0;">' + chips + '</div>';
    }

    // 风险四级等级 (V2.0 需求 3.6): low绿/mid灰/high橙/critical深红
    function fmtRiskLevelV2(risk) {
        if (!risk || !risk.level_v2) return '';
        var map = { low: ['低风险', DARK.green], mid: ['中风险', DARK.label],
                    high: ['高风险', DARK.amber], critical: ['极高风险', DARK.critical] };
        var m = map[risk.level_v2];
        if (!m) return '';
        return '<span style="font-weight:700;color:' + m[1] + ';">' + _escTxt(risk.level_v2_text || m[0]) + '</span>';
    }

    // 异常信号高亮 (V2.0 需求 4.5): 4 级着色逐条渲染
    function fmtAbnormalSignals(abn) {
        if (!abn || !abn.signals || !abn.signals.length) return '';
        var lvColor = { low: DARK.green, mid: DARK.label, high: DARK.amber, critical: DARK.critical };
        return abn.signals.map(function (s) {
            var c = lvColor[s.level] || DARK.label;
            return '<div style="color:' + c + ';font-weight:600;margin:3px 0;padding-left:8px;border-left:3px solid ' + c + ';">' +
                '【' + _escTxt(s.title || '') + ' · ' + _escTxt(s.level_text || '') + '】' + _escTxt(s.desc || '') + '</div>';
        }).join('');
    }

    // 昨日复盘验证闭环 (V2.0 需求 4.3)
    function fmtYesterdayCheck(yc) {
        if (!yc) return '';
        if (!yc.has_data) return '<span style="color:' + DARK.label + ';">' + _escTxt(yc.note || '暂无昨日复盘数据') + '</span>';
        var parts = [];
        parts.push('昨日观点（' + _escTxt(yc.yesterday_date || '—') + '）: <b>' + _escTxt(yc.phase_check || '—') + '</b>');
        if (yc.track_checked && yc.track_checked.length) {
            var vColor = { '晋级': DARK.green, '调出': DARK.red, '未在榜': DARK.label };
            parts.push('跟踪板块: ' + yc.track_checked.map(function (x) {
                return '<span style="color:' + (vColor[x.verdict] || DARK.label) + ';">' + _escTxt(x.name) + '(' + _escTxt(x.verdict) + ')</span>';
            }).join('、'));
        }
        if (yc.hit_rate !== null && yc.hit_rate !== undefined) {
            parts.push('观点准确率 <b style="color:' + (yc.hit_rate >= 50 ? DARK.green : (yc.hit_rate >= 30 ? DARK.amber : DARK.red)) + ';">' + _escTxt(yc.hit_rate) + '%</b>');
        }
        if (yc.summary) parts.push('<span style="color:' + DARK.label + ';">' + _escTxt(yc.summary) + '</span>');
        return parts.join('<br/>');
    }

    // 核心标的映射 (V2.0 需求 4.4): 板块 chip → 二级板块详情, 个股链接 → 个股详情
    function fmtCoreTargets(ct) {
        if (!ct) return '';
        if (!ct.targets || !ct.targets.length) {
            return '<span style="color:' + DARK.label + ';">' + _escTxt(ct.note || '暂无核心标的映射') + '</span>';
        }
        return ct.targets.map(function (t) {
            var stocks = (t.stocks || []).map(function (s) {
                var nm = s.name || s.code || '';
                var code = s.code || '';
                return '<a data-stock="' + _escAttr(code) + '" style="color:' + DARK.blue + ';cursor:pointer;text-decoration:underline;font-weight:600;">' +
                    _escTxt(nm) + '</a>';
            }).join('、');
            return '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:4px 0;">' +
                '<span class="rv-chip" data-sector="' + _escAttr(t.sector || '') +
                '" style="cursor:pointer;padding:2px 8px;border:1px solid ' + DARK.cardBorder +
                ';border-radius:10px;background:' + DARK.cardBg + ';color:' + DARK.red + ';font-size:11px;font-weight:700;">' +
                _escTxt(t.sector || '') + '</span>' +
                '<span style="color:' + DARK.label + ';">→</span>' + stocks + '</div>';
        }).join('');
    }

    // 仓位指引卡片 (V2.0 需求 4.2): 总仓位/主线配置/试错仓位/操作方向
    function fmtPositionGuide(pg) {
        if (!pg) return '';
        var bar = function (label, n, color) {
            return '<div style="flex:1;min-width:64px;text-align:center;background:' + DARK.cardBg +
                ';border:1px solid ' + DARK.cardBorder + ';border-radius:6px;padding:6px 2px;">' +
                '<div style="font-size:10px;color:' + DARK.label + ';">' + label + '</div>' +
                '<div style="font-size:16px;font-weight:700;color:' + color + ';">' + (n || 0) + '成</div></div>';
        };
        return '<div style="display:flex;flex-wrap:wrap;gap:6px;">' +
            bar('总仓位', pg.total, DARK.red) +
            bar('主线配置', pg.mainline, DARK.blue) +
            bar('试错仓位', pg.trial, DARK.amber) +
            '<div style="flex:1;min-width:96px;text-align:center;background:' + DARK.cardBg +
            ';border:1px solid ' + DARK.cardBorder + ';border-radius:6px;padding:6px 2px;">' +
            '<div style="font-size:10px;color:' + DARK.label + ';">操作方向</div>' +
            '<div style="font-size:12px;font-weight:700;color:' + DARK.title + ';">' + _escTxt(pg.direction || '—') + '</div></div>' +
            '</div><div style="font-size:11px;color:' + DARK.label + ';margin-top:4px;">' + _escTxt(pg.reason || '') + '</div>';
    }

    // 市场广度 (V2.0 需求 3.2): 涨跌家数/涨停/连板/两市成交额
    function fmtMarketBreadth(mb) {
        if (!mb) return '';
        var parts = [];
        if (mb.up_count !== undefined || mb.down_count !== undefined) {
            parts.push('涨<b style="color:' + DARK.red + ';">' + (Number(mb.up_count) || 0) + '</b>/跌<b style="color:' + DARK.green + ';">' + (Number(mb.down_count) || 0) + '</b>家');
        }
        if (mb.limit_up !== undefined || mb.limit_down !== undefined) {
            parts.push('涨停<b style="color:' + DARK.red + ';">' + (Number(mb.limit_up) || 0) + '</b>/跌停' + (Number(mb.limit_down) || 0));
        }
        if (mb.zt_count !== undefined) parts.push('涨停' + (Number(mb.zt_count) || 0) + '家');
        if (mb.max_lianban) parts.push('最高连板<b>' + _escTxt(mb.max_lianban) + '</b>板');
        if (mb.total_amount) parts.push('两市成交<b>' + Number(mb.total_amount).toFixed(0) + '</b>亿');
        if (mb.source) parts.push('<span style="color:' + DARK.label + ';">来源' + _escTxt(mb.source) + '</span>');
        if (!parts.length) return '';
        return '<span style="font-size:11px;">' + parts.join(' · ') + '</span>';
    }

    // 进阶观察 (V2.0 需求 3.10): 风格标签完善 + 风格切换判断 + 明日核心策略要点
    function fmtStyleOutlook(so) {
        if (!so) return '';
        var parts = [];
        var unc = so.unclassified || {};
        if (unc.total) {
            var color = (so.ratio_ok === false) ? DARK.amber : DARK.green;
            var txt = '未归类 ' + (unc.count || 0) + '/' + unc.total + ' 个（' + (unc.pct || 0) + '%）';
            parts.push('风格标签: <b style="color:' + color + ';">' + txt + '</b>' +
                (unc.count ? '：' + _escTxt((unc.names || []).join('、')) : '，全部已归类'));
        }
        if (so.switch_desc) {
            parts.push('风格切换: <span style="color:' + (so.switch ? DARK.amber : DARK.green) + ';font-weight:600;">' +
                _escTxt(so.switch_desc) + '</span>');
        }
        if (so.tomorrow_points && so.tomorrow_points.length) {
            parts.push('明日核心策略要点: ' + so.tomorrow_points.map(function (p) {
                return '<div style="margin:2px 0;">• ' + _escTxt(p) + '</div>';
            }).join(''));
        }
        if (!parts.length) return '';
        return parts.join('<br/>');
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

    // 预期涨幅 → 中值 + 区间 (正预期绿色带+, 负预期红色反映下跌风险)
    function fmtPredGain(p) {
        if (!p || p.expected_gain === null || p.expected_gain === undefined || isNaN(Number(p.expected_gain))) return '--';
        var g = Number(p.expected_gain);
        var range;
        if (p.gain_range && p.gain_range.length === 2) {
            range = p.gain_range;
        } else if (g >= 3.0) {
            range = [g - 3.0, g + 3.5];
        } else if (g >= 1.5) {
            range = [Math.max(0, g - 2.0), g + 3.5];
        } else if (g > 0) {
            range = [0, g + 3.5];
        } else {
            range = [g - 2.0, g + 1.5];
        }
        var cls = g >= 0 ? 'pos' : 'neg';
        var sign = g > 0 ? '+' : '';
        var r0 = Number(range[0]).toFixed(1);
        var r1 = Number(range[1]).toFixed(1);
        if (Number(range[0]) > 0) r0 = '+' + r0;
        return '<span class="' + cls + '">' + sign + g.toFixed(1) + '%</span>' +
            '<span style="color:#64748b;font-size:10px;"> (' + r0 + '~' + r1 + ')</span>';
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

    // horizon 文本: 1=次日, N>1=N个交易日
    function _horizonText(h) {
        if (h === 1 || h === '1') return '次日';
        if (h === null || h === undefined || h === '' || isNaN(Number(h))) return '3个交易日';
        return h + '个交易日';
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
            '<div style="font-weight:700;color:#1e293b;">预测（' + _horizonText(p.horizon) + '）</div>' +
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
    // V2.0 全局色彩规范 (需求 2.2): 红#e53e3e涨 / 绿#0ea960跌 / 灰#86909c平 /
    // 橙#ff7d00高风险 / 深红#d90000极高风险 / 蓝#165dff高亮
    var DARK = {
        panelBg: '#ffffff',      // 复盘整体面板背景 (白)
        cardBg: '#f8fafc',       // 单张卡片背景 (浅灰蓝, 与白底区分)
        cardBorder: '#e2e8f0',   // 卡片边框 (浅灰)
        title: '#0f172a',        // 章节标题 (深灰蓝)
        chartTitle: '#334155',   // 图表标题
        text: '#334155',         // 正文
        label: '#86909c',        // 图表标签 (灰/平)
        dim: '#a0aab6',          // 次要文字
        grid: '#e2e8f0',         // 分隔线/非激活边框 (浅灰)
        red: '#e53e3e',          // 金融红 (涨/强势)
        green: '#0ea960',        // 金融绿 (跌/健康)
        amber: '#ff7d00',        // 橙 (高风险)
        critical: '#d90000',     // 深红 (极高风险)
        blue: '#165dff',         // 蓝 (高亮/跳转)
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
    // V2.0 (需求 3.1): 完整纵坐标(五档全名) + 双轴(左轴情绪热度/右轴高分板块数) + 里程碑拐点(milestones)
    function reviewChartSentimentHistory(history, milestones) {
        if (!history || !history.length) return '';
        var rows = history.slice(-24);
        var W = 540, H = 190, padL = 52, padR = 42, padT = 18, padB = 20;
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
        var grid = '', bars = '', dots = '', labels = '', pts = '', rightAxis = '', mile = '';
        // 左轴: 五档完整名称 (V2.0 完整纵坐标)
        REVIEW_PHASES.forEach(function (p, i) {
            var y = padT + ch - (valOf(p) / 3) * ch;
            grid += '<line x1="' + padL + '" y1="' + y + '" x2="' + (W - padR) + '" y2="' + y + '" stroke="' + DARK.grid + '" stroke-width="0.5" stroke-dasharray="3 3"/>' +
                '<text x="' + (padL - 5) + '" y="' + (y + 3) + '" text-anchor="end" font-size="8" fill="' + PHASE_COLORS[i] + '">' + p + '</text>';
        });
        // 右轴: 高分板块数刻度 (V2.0 双轴)
        var ticks = [0, Math.round(maxHigh / 2), maxHigh];
        for (var t = 0; t < ticks.length; t++) {
            var ty = padT + ch - (ticks[t] / maxHigh) * ch;
            rightAxis += '<text x="' + (W - padR + 5) + '" y="' + (ty + 3) + '" font-size="8" fill="' + DARK.label + '">' + ticks[t] + '</text>';
        }
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
                labels += '<text x="' + x + '" y="' + (H - 5) + '" text-anchor="middle" font-size="8" fill="' + DARK.label + '">' + _escTxt(String(r.date || '').slice(5)) + '</text>';
            }
        });
        // 里程碑拐点 (V2.0 需求 3.1): 主线确认日/首次退潮日/情绪冰点日 (日期 YYYYMMDD, 与 history.date 匹配)
        var mileDefs = [
            { key: 'mainline_confirm_date', label: '主线确认', color: DARK.red },
            { key: 'first_tideout_date', label: '首次退潮', color: DARK.amber },
            { key: 'emotion_ice_date', label: '情绪冰点', color: DARK.blue }
        ];
        var mileLegend = '';
        var mileIdx = 0;
        mileDefs.forEach(function (md) {
            var d = milestones && milestones[md.key];
            if (!d) return;
            var found = -1;
            for (var k = 0; k < rows.length; k++) {
                if (String(rows[k].date) === String(d)) { found = k; break; }
            }
            var clamped = false;
            if (found < 0) {
                // 里程碑日期超出绘图窗口: 早于窗口起点→左边缘, 晚于终点→右边缘 (拐点标注始终可见)
                if (String(d) < String(rows[0].date)) { found = 0; clamped = true; }
                else if (String(d) > String(rows[rows.length - 1].date)) { found = rows.length - 1; clamped = true; }
                else return;
            }
            var x = xAt(found);
            var labelY = padT + 6 + mileIdx * 10;
            var edgeMark = clamped ? (found === 0 ? '«' : '»') : '';
            mile += '<line x1="' + x + '" y1="' + (padT - 2) + '" x2="' + x + '" y2="' + (H - padB + 6) + '" stroke="' + md.color + '" stroke-width="1" stroke-dasharray="4 3" opacity="0.7"/>' +
                '<path d="M ' + x + ' ' + (padT - 6) + ' l 6 -7 l -12 0 z" fill="' + md.color + '"/>' +
                '<text x="' + x + '" y="' + labelY + '" text-anchor="middle" font-size="8" font-weight="700" fill="' + md.color + '" stroke="#fff" stroke-width="2.5" paint-order="stroke">' + edgeMark + md.label + '</text>';
            mileLegend += '<span style="color:' + md.color + ';">▲ ' + md.label + (clamped ? '(窗口外)' : '') + '</span>';
            mileIdx++;
        });
        var legend = '<div style="font-size:9px;color:' + DARK.label + ';">曲线=情绪阶段（左轴五档，越高越热）· 灰柱=高分板块数（右轴）· 最右为最新' +
            (mileLegend ? ' · ' + mileLegend : '') + '</div>';
        return '<div style="margin:6px 0;"><div style="font-size:11px;font-weight:700;color:' + DARK.chartTitle + ';margin-bottom:2px;">情绪周期历史（近' + rows.length + '个交易日）</div>' +
            '<svg width="100%" viewBox="0 0 ' + W + ' ' + H + '" style="display:block;max-width:580px;">' + grid + bars +
            '<polyline points="' + pts + '" fill="none" stroke="' + DARK.red + '" stroke-width="1.5" opacity="0.8"/>' + dots + labels +
            mile + rightAxis + '</svg>' + legend + '</div>';
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
    // V2.0: 卡片式布局 + 置顶核心结论全宽条 + 新增模块卡片 (需求 4.1-4.5 / 3.2/3.5/3.10)
    function buildReviewHtml(data) {
        if (!data || !data.update_date) return '<div class="empty-msg">暂无复盘数据, 请先运行 rps_calc.py</div>';
        var m = data.market || {}, res = data.resonance || {}, tier = data.tier || {};
        var rot = data.rotation || {}, heal = data.health || {}, risk = data.risk || {};
        var watch = data.watch || {}, sent = data.sentiment || {}, style = data.style || {};
        var cons = data.consistency || {};
        var cc = data.core_conclusion || {}, pg = data.position_guide || {};
        var yc = data.yesterday_check || {}, ct = data.core_targets || {};
        var abn = data.abnormal_signals || {}, mb = data.market_breadth || {};
        var so = data.style_outlook || {};
        var card = function (title, body) {
            return '<div style="background:' + DARK.cardBg + ';border:1px solid ' + DARK.cardBorder + ';border-radius:8px;padding:10px 12px;margin-bottom:10px;">' +
                '<div style="font-weight:700;color:' + DARK.title + ';margin-bottom:6px;border-left:3px solid ' + DARK.red + ';padding-left:6px;">' + title + '</div>' +
                '<div style="font-size:12px;line-height:1.9;color:' + DARK.text + ';word-break:break-all;">' + body + '</div></div>';
        };
        // V2.0 折叠块 (需求 3.3): details/summary 原生折叠, 无需 JS, 可单测
        var fold = function (summaryHtml, bodyHtml) {
            return '<details class="rv-fold" style="margin-top:4px;"><summary style="cursor:pointer;outline:none;font-size:11px;font-weight:600;color:' + DARK.blue + ';">' + summaryHtml +
                ' <span style="color:' + DARK.label + ';">▾ 展开/收起</span></summary><div style="margin-top:4px;">' + bodyHtml + '</div></details>';
        };
        var fmtRatio = function (x) { return _escTxt(x.name) + '(' + Number(x.ratio).toFixed(0) + '%)'; };
        var diffTxt = (m.diff5 === undefined || m.diff5 === null) ? '--' : (m.diff5 > 0 ? '+' + m.diff5 : m.diff5);
        var pctTxt = (typeof m.high_score_count_pct !== 'number' || isNaN(m.high_score_count_pct)) ? '--' : m.high_score_count_pct.toFixed(0) + '%';
        // 深色面板容器: 复盘整体作为一块金融终端风格深色区域
        var html = '<div style="font-size:12px;background:' + DARK.panelBg + ';border-radius:10px;padding:10px;border:1px solid ' + DARK.cardBorder + ';">';
        // 置顶全宽背景条: 一句话核心结论 (V2.0 需求 4.1)
        if (cc && cc.text) {
            var ccSub = [];
            if (cc.phase) ccSub.push('阶段: <b>' + _escTxt(cc.phase) + '</b>');
            if (cc.mainline) ccSub.push('主线: <b>' + _escTxt(cc.mainline) + '</b>');
            if (cc.position !== undefined && cc.position !== null) ccSub.push('建议仓位: <b>' + _escTxt(cc.position) + '成</b>');
            if (cc.focus) ccSub.push('重点跟踪: ' + _escTxt(cc.focus));
            if (cc.rotation) ccSub.push('轮动: ' + _escTxt(cc.rotation));
            html += '<div style="background:linear-gradient(90deg,#0f172a,#334155);border-radius:8px;padding:10px 12px;margin-bottom:10px;color:#fff;">' +
                '<div style="font-size:10px;color:#94a3b8;font-weight:700;letter-spacing:2px;margin-bottom:4px;">一句话核心结论</div>' +
                '<div style="font-size:13px;font-weight:700;line-height:1.7;">' + _escTxt(cc.text) + '</div>' +
                (ccSub.length ? '<div style="font-size:11px;color:#cbd5e1;margin-top:6px;display:flex;flex-wrap:wrap;gap:4px 12px;">' + ccSub.join('') + '</div>' : '') +
                '</div>';
        }
        // 一、市场整体定调: 图表(三档条形图) + 一句摘要 + 市场广度 (V2.0 需求 3.2)
        var mBody = '<b>市场动量：</b>' + fmtReviewTone(m.tone) + '；RPS5≥90 <b>' +
            (m.high5 === undefined ? '--' : m.high5) + '</b> 个（较前日 ' + diffTxt + '）、RPS10≥90 <b>' +
            (m.high10 === undefined ? '--' : m.high10) + '</b> 个、RPS20≥90 <b>' +
            (m.high20 === undefined ? '--' : m.high20) + '</b> 个；高分板块历史分位 <b>' + pctTxt + '</b>。' + _escTxt(m.note || '');
        if (mb && mb.up_count !== undefined) mBody += '<div style="margin-top:4px;padding-top:4px;border-top:1px dashed ' + DARK.cardBorder + ';">' + fmtMarketBreadth(mb) + '</div>';
        if (cons.conflict) mBody += '<div style="color:' + DARK.red + ';font-weight:600;margin-top:4px;">' + _escTxt(cons.warning) + '</div>';
        html += card('一、市场整体定调', reviewChartMkt(m, data.total_industries) + mBody);
        // 仓位指引 (V2.0 需求 4.2)
        html += card('仓位指引', fmtPositionGuide(pg) || '<span style="color:' + DARK.label + ';">暂无仓位指引数据</span>');
        // 二、主线识别: 主线构成图 + 梯队达标率图 + 精简名单 (梯队折叠 需求 3.3)
        var sec2 = [];
        if (res.core && res.core.length) sec2.push('<b>硬核主线</b>（三周期共振' + res.core.length + '条）：' + fmtReviewSectorList(res.core));
        if (res.tough && res.tough.length) sec2.push('<b>韧性主线</b>（RPS10/20在榜、RPS5掉1天' + res.tough.length + '条）：' + fmtReviewSectorList(res.tough));
        if (res.pulse && res.pulse.length) sec2.push('<b>短期脉冲</b>（仅RPS5上榜' + res.pulse.length + '个）：' + fmtReviewSectorList(res.pulse));
        if (res.diverge && res.diverge.length) sec2.push('<b>主线分歧</b>（RPS10/20在榜、RPS5回落）：' + fmtReviewNames(res.diverge));
        if (tier.tier1 && tier.tier1.length) sec2.push('<b>第一梯队</b>（Top10, 达标率≥15%）：' + fmtReviewSectorList(tier.tier1, 10));
        if (tier.tier2 && tier.tier2.length) {
            sec2.push(fold('<b>第二梯队</b>（11~30, 达标率≥10%）' + fmtReviewSectorList(tier.tier2.slice(0, 3), 3),
                fmtReviewSectorList(tier.tier2, 10)));
        }
        html += card('二、板块强弱梯队与主线',
            reviewChartResonance(res) + reviewChartTier(tier) + reviewChartSectorHeat(tier) + (sec2.length ? sec2.join('<br/>') : '暂无数据'));
        // 核心标的映射 (V2.0 需求 4.4): 板块 chip → 二级板块详情, 个股 → 个股详情
        var ctHtml = fmtCoreTargets(ct) || '<span style="color:' + DARK.label + ';">暂无核心标的映射数据</span>';
        html += card('核心标的映射', ctHtml);
        // 三、轮动动向: 轮动数量图 + 精简名单
        var sec3 = [];
        if (rot.new_in && rot.new_in.length) sec3.push('<b>今日新调入</b>（' + rot.new_in.length + '个）：' + fmtReviewSectorList(rot.new_in));
        if (rot.rank_jump && rot.rank_jump.length) {
            sec3.push('<b>排名大幅跃升Top' + rot.rank_jump.length + '</b>（≥8名）：' + rot.rank_jump.map(function (s) {
                return _escTxt(s.name) + '(名次+' + s.rank_change + '→第' + s.rank + '名)';
            }).join('、'));
        }
        if (rot.exits && rot.exits.length) {
            var eMap = { mainline_exit: '主线调出(上榜≥5天)', pulse_exit: '脉冲调出(上榜≤2天)', normal_exit: '普通调出' };
            sec3.push('<b>调出分类</b>：' + rot.exits.map(function (e) {
                return _escTxt(e.name) + '[' + _escTxt(eMap[e.exit_type] || e.exit_type) + ' 连续' + e.continuous + '天]';
            }).join('、'));
        }
        if (rot.repeat && rot.repeat.length) sec3.push('<b>反复轮动</b>（近10日多次调入调出, 持续性差）：' + fmtReviewNames(rot.repeat));
        html += card('三、板块轮动动向', reviewChartRotation(rot) + (sec3.length ? sec3.join('<br/>') : '暂无数据'));
        // 四、健康度: 达标率图 + 精简名单 + 合并板块分类 (V2.0 需求 3.5: unified 带 tags/ratio_chg)
        var sec4 = [];
        if (heal.seed_high && heal.seed_high.length) sec4.push('<b>高质量种子</b>（首日入选+扩散达标）：' + heal.seed_high.map(fmtRatio).join('、'));
        if (heal.seed_pulse && heal.seed_pulse.length) sec4.push('<b>脉冲种子</b>（首日入选但扩散不足）：' + fmtReviewNames(heal.seed_pulse));
        if (heal.expand && heal.expand.length) sec4.push('<b>扩散健康</b>（达标率≥规模阈值）：' + heal.expand.map(fmtRatio).join('、'));
        if (heal.mature && heal.mature.length) sec4.push('<b>成熟主线</b>（连续上榜≥5天）：' + fmtReviewNames(heal.mature));
        if (heal.mature_fatigue && heal.mature_fatigue.length) sec4.push('<b>成熟疲劳</b>（≥8天且达标率下滑）：' + heal.mature_fatigue.map(fmtRatio).join('、'));
        if (heal.unified && heal.unified.length) {
            var unifHtml = heal.unified.map(function (u) {
                var tagsHtml = (u.tags || []).map(function (t) { return '<span style="color:' + DARK.blue + ';">' + _escTxt(t) + '</span>'; }).join('/');
                var chg = (u.ratio_chg === null || u.ratio_chg === undefined) ? '' :
                    (u.ratio_chg > 0 ? ' <span style="color:' + DARK.red + ';">+' + u.ratio_chg + 'pp</span>' :
                        ' <span style="color:' + DARK.green + ';">' + u.ratio_chg + 'pp</span>');
                return _escTxt(u.name) + '(' + u.ratio + '%)' + chg + '[' + tagsHtml + ']';
            }).join('、');
            sec4.push(fold('<b>合并板块分类</b>（' + heal.unified.length + '个, 去重）', unifHtml));
        }
        html += card('四、题材/板块健康度', reviewChartHealth(heal) + (sec4.length ? sec4.join('<br/>') : '暂无数据'));
        // 五、风险预警: 批量调出构成图 + 风险等级(V2 四级) + 赛道归类 + 操作建议
        var riskBody = reviewChartRisk(risk) + fmtReviewWarnings(risk.warnings);
        if (risk.track_groups && risk.track_groups.length) {
            riskBody += '<div style="margin-top:4px;"><b>批量调出赛道归类</b>：' + risk.track_groups.map(function (g) {
                return _escTxt(g.track) + ' <b>' + g.count + '</b>';
            }).join('、') + '（主线调出 <b>' + (risk.main_out_total || 0) + '</b> / 总调出 <b>' + (risk.out_total || 0) + '</b>）</div>';
        }
        if (risk.advice && risk.advice.length) {
            riskBody += '<div style="margin-top:4px;"><b>操作建议</b>：' + risk.advice.map(function (a) {
                return '• ' + _escTxt(a);
            }).join('<br/>') + '</div>';
        }
        var riskLvHtml = fmtRiskLevelV2(risk);
        var lvColor = { safe: DARK.green, hint: DARK.label, warning: DARK.amber, danger: DARK.red };
        var lvTxt = { safe: '安全', hint: '提示', warning: '警告', danger: '高危' };
        var rl = risk.level || 'safe';
        var rTitle = '五、风险与退潮预警';
        rTitle += riskLvHtml ? '（' + riskLvHtml + '）' : '（风险等级: <span style="color:' + (lvColor[rl] || DARK.label) + ';">' + _escTxt(lvTxt[rl] || rl) + '</span>）';
        html += card(rTitle, riskBody);
        // 异常信号高亮 (V2.0 需求 4.5): 4 级着色逐条渲染
        var abnHtml = fmtAbnormalSignals(abn);
        html += card('异常信号高亮', abnHtml || '<span style="color:' + DARK.green + ';">暂无异常信号，市场运行平稳</span>');
        // 六、次日跟踪清单: 跟踪池构成图 + 精简名单
        var sec6 = [];
        if (watch.seed_high && watch.seed_high.length) sec6.push('<b>高质量种子池</b>（次日连榜则确认强度）：' + watch.seed_high.map(fmtRatio).join('、'));
        if (watch.seed_pulse && watch.seed_pulse.length) sec6.push('<b>脉冲种子池</b>（需扩散确认）：' + fmtReviewNames(watch.seed_pulse));
        if (watch.critical && watch.critical.length) sec6.push('<b>临界晋级池</b>（RPS≈阈值且排名连续上升）：' + watch.critical.slice(0, 8).map(function (c) { return _escTxt(c.name) + '(' + Number(c.rps5).toFixed(0) + ')'; }).join('、'));
        if (watch.diverge_watch && watch.diverge_watch.length) sec6.push('<b>主线分歧池</b>（RPS20在榜、RPS5掉榜, 良性分歧低吸观察）：' + watch.diverge_watch.map(function (x) { return _escTxt(x.name) + '(掉' + x.drop_days + '天)'; }).join('、'));
        if (watch.diverge_dropped && watch.diverge_dropped.length) sec6.push('<span style="color:' + DARK.red + ';"><b>主线衰竭</b>（RPS5掉榜≥3天, 移出跟踪）：' + watch.diverge_dropped.map(function (x) { return _escTxt(x.name) + '(掉' + x.drop_days + '天)'; }).join('、') + '</span>');
        html += card('六、次日跟踪清单', reviewChartWatch(watch) + (sec6.length ? sec6.join('<br/>') : '暂无跟踪标的'));
        // 昨日复盘验证闭环 (V2.0 需求 4.3)
        var ycHtml = fmtYesterdayCheck(yc) || '<span style="color:' + DARK.label + ';">暂无昨日复盘数据</span>';
        html += card('昨日复盘验证', ycHtml);
        // 消息面复盘 (2026-08-23): 推荐股票中的新闻/人气/大宗信号聚合
        html += card('消息面复盘', fmtReviewNews(data.news));
        // 推荐准确率复盘
        html += card('推荐准确率复盘', fmtReviewAccuracy(data.rec_accuracy));
        // 进阶: 情绪周期图 + 风格图 + 进阶观察(V2.0 需求 3.10) + 一致性校验
        var adv = reviewChartPhase(sent) + reviewChartStyle(style) +
            '情绪周期：' + fmtReviewPhase(sent) +
            (fmtStyleOutlook(so) ? '<div style="margin-top:4px;">' + fmtStyleOutlook(so) + '</div>' : '') +
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
            return '<span style="color:' + DARK.label + ';">暂无消息面信号(推荐不含新闻/人气/大宗数据)</span>';
        }
        var envColor = { '偏暖': DARK.green, '偏冷': DARK.blue, '中性偏杂': DARK.amber };
        var parts = [];
        var envTxt = _escTxt(nws.env || '中性');
        parts.push('消息面环境: <b style="color:' + (envColor[nws.env] || DARK.label) + ';">' + envTxt + '</b>; ' +
            '推荐<b>' + (nws.stock_count || 0) + '</b>只中<b>' + (nws.hit_count || 0) + '</b>只命中, ' +
            '净影响<b>' + fmtSignedNum(nws.score_sum) + '</b>分');
        if (nws.official_pos_list && nws.official_pos_list.length) {
            parts.push('<span style="color:' + DARK.green + ';">✔ 权威利好(真实性采信, +2分/条):</span> ' +
                nws.official_pos_list.map(function (x) {
                    var h = x.headline ? (' — ' + _escTxt(x.headline)) : '';
                    return _escTxt(x.name) + '(' + x.count + '条)' + h;
                }).join('、'));
        }
        if (nws.official_neg_list && nws.official_neg_list.length) {
            parts.push('<span style="color:' + DARK.red + ';">✘ 权威利空(真实性采信, -3分/条):</span> ' +
                nws.official_neg_list.map(function (x) {
                    var h = x.headline ? (' — ' + _escTxt(x.headline)) : '';
                    return _escTxt(x.name) + '(' + x.count + '条)' + h;
                }).join('、'));
        }
        if (nws.rumor_list && nws.rumor_list.length) {
            parts.push('<span style="color:' + DARK.amber + ';">◯ 传闻消息(散户平台, 仅提示不采信):</span> ' +
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
        // V2.0 周度扩展 (需求 3.5): 数据范围 + 评分基准 + 周度资金子项 (每日复盘无这些键, 条件渲染)
        if (nws.range_note) parts.push('<span style="color:' + DARK.label + ';font-size:10px;">' + _escTxt(nws.range_note) + '</span>');
        if (nws.score_basis) parts.push('<span style="color:' + DARK.label + ';font-size:10px;">评分基准: ' + _escTxt(nws.score_basis) + '</span>');
        if (nws.weekly_fund) {
            var wf = nws.weekly_fund;
            var fundParts = [];
            if (wf.main_week_net_yi !== null && wf.main_week_net_yi !== undefined && !isNaN(Number(wf.main_week_net_yi))) {
                var mn = Number(wf.main_week_net_yi);
                var mv = wf.main_verdict || (mn >= 0 ? '净流入' : '净流出');
                fundParts.push('主力资金周累计<b style="color:' + (mn >= 0 ? DARK.green : DARK.red) + ';">' + _escTxt(mv) +
                    Math.abs(mn).toFixed(0) + '亿</b>');
            }
            if (wf.north_ratio !== null && wf.north_ratio !== undefined) {
                fundParts.push('北向成分股上涨占比<b>' + (Number(wf.north_ratio) * 100).toFixed(0) + '%</b>' +
                    (wf.north_note ? '<span style="font-size:10px;color:' + DARK.label + ';">(' + _escTxt(wf.north_note) + ')</span>' : ''));
            }
            if (fundParts.length) {
                parts.push('<b>周度资金:</b> ' + fundParts.join('; ') +
                    (wf.note ? ' <span style="font-size:10px;color:' + DARK.label + ';">' + _escTxt(wf.note) + '</span>' : ''));
            }
        }
        return parts.join('<br/>');
    }

    // 带符号数字 → '+3' / '-2' / '0'
    function fmtSignedNum(v) {
        var n = Number(v) || 0;
        if (n > 0) return '+' + n;
        if (n < 0) return String(n);
        return '0';
    }

    /* ===== 每周复盘渲染 (2026-08-23, 可单测) ===== */

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

    // 周度推荐准确率 → HTML 摘要 (V2.0 需求 3.6: 多周期 + 分置信度 + 基准对比 + 案例板块)
    function fmtReviewWeekAccuracy(acc) {
        if (!acc || !acc.has_data) {
            return '<span style="color:' + DARK.label + ';">暂无本周推荐准确率数据</span>';
        }
        var hr = (Number(acc.week_hit_rate) || 0) * 100;
        var ag = Number(acc.week_avg_gain) || 0;
        var hrColor = hr >= 50 ? DARK.green : (hr >= 30 ? DARK.amber : DARK.red);
        var parts = [];
        parts.push('本周已验证<b>' + (acc.week_samples || 0) + '</b>样本, 命中率 <b style="color:' + hrColor + ';">' +
            hr.toFixed(0) + '%</b>, 次日平均涨幅 <b style="color:' + (ag >= 0 ? DARK.green : DARK.red) + ';">' +
            (ag >= 0 ? '+' : '') + ag.toFixed(1) + '%</b>');
        // V2.0: 持有3日/5日平均涨幅
        var hz = [];
        if (acc.avg_gain3 !== null && acc.avg_gain3 !== undefined && !isNaN(Number(acc.avg_gain3))) {
            var g3 = Number(acc.avg_gain3);
            hz.push('持有3日 <b style="color:' + (g3 >= 0 ? DARK.green : DARK.red) + ';">' + (g3 >= 0 ? '+' : '') +
                g3.toFixed(2) + '%</b>(' + (acc.horizon3_n || 0) + '样本)');
        }
        if (acc.avg_gain5 !== null && acc.avg_gain5 !== undefined && !isNaN(Number(acc.avg_gain5))) {
            var g5 = Number(acc.avg_gain5);
            hz.push('持有5日 <b style="color:' + (g5 >= 0 ? DARK.green : DARK.red) + ';">' + (g5 >= 0 ? '+' : '') +
                g5.toFixed(2) + '%</b>(' + (acc.horizon5_n || 0) + '样本)');
        }
        if (hz.length) parts.push('多周期收益: ' + hz.join('; '));
        // V2.0: 基准对比 (超额 = 推荐持有收益 - 基准指数周涨幅)
        var bench = acc.benchmark || {};
        if (bench.bench_gain !== null && bench.bench_gain !== undefined) {
            var baseGain = (acc.avg_gain5 !== null && acc.avg_gain5 !== undefined && !isNaN(Number(acc.avg_gain5)))
                ? Number(acc.avg_gain5) : ag;
            var bG = Number(bench.bench_gain);
            var exc = Number(bench.excess) || 0;
            parts.push('基准对比(' + _escTxt(bench.basis || '') + '): 本周推荐平均涨幅 <b style="color:' +
                (baseGain >= 0 ? DARK.green : DARK.red) + ';">' + (baseGain >= 0 ? '+' : '') + baseGain.toFixed(1) + '%</b> | ' +
                _escTxt(bench.bench_name || '基准') + '周涨幅 ' + bG.toFixed(1) + '% | 超额收益 <b style="color:' +
                (exc >= 0 ? DARK.green : DARK.red) + ';">' + (exc >= 0 ? '+' : '') + exc.toFixed(1) + '%</b>');
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
        // V2.0: 最佳/最差案例 + 所属板块
        if (acc.best && acc.best.length) {
            parts.push('<span style="color:' + DARK.green + ';"><b>最佳预测</b>:</span> ' +
                acc.best.map(function (x) {
                    return _escTxt(x.name) + (x.sector ? '(' + _escTxt(x.sector) + ')' : '') +
                        '(+' + Number(x.gain).toFixed(1) + '%)';
                }).join('、'));
        }
        if (acc.worst && acc.worst.length) {
            parts.push('<span style="color:' + DARK.red + ';"><b>最差预测</b>:</span> ' +
                acc.worst.map(function (x) {
                    return _escTxt(x.name) + (x.sector ? '(' + _escTxt(x.sector) + ')' : '') +
                        '(' + Number(x.gain).toFixed(1) + '%)';
                }).join('、'));
        }
        if (acc.note) parts.push('<span style="color:' + DARK.label + ';">' + _escTxt(acc.note) + '</span>');
        return parts.join('<br/>');
    }

    /* ===== 每周复盘渲染 V2.0 (2026-08-29, 需求 3.1-3.7 + 4.1-4.5, 可单测) ===== */

    // 板块名 chip (data-sector 由 index.html 委托点击 → 二级板块详情页)
    function _sectorChip(name) {
        return '<span class="rv-chip" data-sector="' + _escAttr(name) +
            '" style="cursor:pointer;display:inline-block;padding:1px 7px;border:1px solid ' + DARK.cardBorder +
            ';border-radius:10px;background:' + DARK.cardBg + ';color:' + DARK.blue +
            ';font-size:11px;font-weight:600;">' + _escTxt(name) + '</span>';
    }

    // 通用折叠块 (需求 2.3): 长列表默认折叠展示前3条, 点击「展开/收起」查看完整列表
    function _foldBlock(summaryHtml, bodyHtml) {
        return '<details class="rv-fold" style="margin-top:4px;"><summary style="cursor:pointer;outline:none;font-size:11px;font-weight:600;color:' + DARK.blue + ';">' +
            summaryHtml + ' <span style="color:' + DARK.label + ';">▾ 展开/收起</span></summary>' +
            '<div style="margin-top:4px;">' + bodyHtml + '</div></details>';
    }

    // 情绪阶段 → 颜色
    function _phaseColor(p) {
        var map = { '退潮期': DARK.slate, '冰点期': DARK.slate, '启动期': DARK.blue,
                    '震荡期': DARK.label, '发酵期': DARK.amber, '高潮期': DARK.red };
        var s = String(p || '');
        for (var k in map) { if (s.indexOf(k) >= 0) return map[k]; }
        return DARK.label;
    }

    // 本周定调 (V2.0 需求 3.1): 三周期并列条形图 + 历史水位 + 判定阈值
    function fmtWeekTone(tone) {
        if (!tone) return '';
        var mk = function (series, label, color) {
            if (!series || !series.length) return '';
            var items = series.map(function (x) { return { label: String(x.date || '').slice(4), value: x.high_n }; });
            var mx = Math.max.apply(null, items.map(function (i) { return Number(i.value) || 0; })) || 1;
            return reviewChartBars(items, {
                title: label, max: mx, row: 18, labelWidth: 46,
                color: function () { return color; },
                fmt: function (v) { return v + '个'; }
            });
        };
        var parts = [];
        parts.push(mk(tone.rps5_series, '每日RPS5≥90板块数', DARK.red));
        parts.push(mk(tone.rps10_series, '每日RPS10≥90板块数', DARK.amber));
        parts.push(mk(tone.rps20_series, '每日RPS20≥90板块数', DARK.blue));
        var toneColor = { '周度偏强': DARK.red, '周度强': DARK.red, '周度中性': DARK.label, '周度偏弱': DARK.green };
        parts.push('周度强度: <b style="color:' + (toneColor[tone.tone] || DARK.label) + ';font-size:13px;">' +
            _escTxt(tone.tone || '周度中性') + '</b>');
        if (tone.note) parts.push(_escTxt(tone.note));
        if (tone.threshold_note) parts.push('<span style="font-size:10px;color:' + DARK.label + ';">判定阈值: ' +
            _escTxt(tone.threshold_note) + '</span>');
        return parts.join('<br/>');
    }

    // 周市场广度 (V2.0 需求 3.1 子卡片): 周涨跌占比/成交额+环比/涨跌停/最高连板
    function fmtWeekMarketBreadth(mb) {
        if (!mb) return '';
        var parts = [];
        if (mb.up_ratio !== null && mb.up_ratio !== undefined && !isNaN(Number(mb.up_ratio))) {
            parts.push('全市场周上涨占比 <b style="color:' + DARK.red + ';">' + (Number(mb.up_ratio) * 100).toFixed(0) +
                '%</b> / 下跌占比 <b style="color:' + DARK.green + ';">' + (Number(mb.down_ratio) * 100).toFixed(0) + '%</b>');
        }
        if (mb.week_amount_yi !== null && mb.week_amount_yi !== undefined && Number(mb.week_amount_yi) > 0) {
            var chgTxt = '';
            if (mb.amount_chg !== null && mb.amount_chg !== undefined && !isNaN(Number(mb.amount_chg))) {
                var c = Number(mb.amount_chg);
                chgTxt = ' <span style="color:' + (c >= 0 ? DARK.green : DARK.red) + ';">环比' +
                    (c >= 0 ? '+' : '') + c.toFixed(1) + '%</span>';
            }
            parts.push('周两市累计成交 <b>' + Number(mb.week_amount_yi).toFixed(0) + '</b>亿' + chgTxt);
        }
        if (mb.avg_limit_up !== null && mb.avg_limit_up !== undefined) {
            parts.push('周均涨停 <b style="color:' + DARK.red + ';">' + mb.avg_limit_up + '</b>家 / 跌停 ' + (mb.avg_limit_down || 0) + '家');
        }
        if (mb.max_board) parts.push('周最高连板 <b>' + mb.max_board + '</b>板');
        if (mb.note) parts.push('<span style="font-size:10px;color:' + DARK.label + ';">' + _escTxt(mb.note) + '</span>');
        if (!parts.length) return '';
        return parts.join('<br/>');
    }

    // 本周主线 (V2.0 需求 3.2): 完整字段表 + 强度分级(硬核红/短期蓝/动量衰减灰) + 长列表折叠 + hover指标提示
    function fmtWeekMainlines(mains) {
        if (!mains || !mains.length) return '无持续性主线(缺乏连续上榜板块)';
        var stColor = { '硬核主线': DARK.red, '短期主线': DARK.blue, '动量衰减': DARK.label };
        var rowHtml = function (x) {
            var sc = stColor[x.strength] || DARK.label;
            var resoTxt = x.level === 'hard'
                ? '<b style="color:' + DARK.red + ';">【三周期共振】</b>'
                : (x.level === 'short' ? '<span style="color:' + DARK.blue + ';">【短期】</span>'
                    : '<span style="color:' + DARK.label + ';">【动量衰减】</span>');
            var rps10 = (x.rps10 === null || x.rps10 === undefined) ? '—' : Number(x.rps10).toFixed(0);
            var rps20 = (x.rps20 === null || x.rps20 === undefined) ? '—' : Number(x.rps20).toFixed(0);
            var ratio = (x.ratio === null || x.ratio === undefined) ? '—' : Number(x.ratio).toFixed(1) + '%';
            var chgTxt = (x.chg === null || x.chg === undefined || isNaN(Number(x.chg)))
                ? '—' : ((Number(x.chg) >= 0 ? '+' : '') + Number(x.chg).toFixed(1) + '%');
            return '<tr>' +
                '<td>' + _sectorChip(x.name) + '</td>' +
                '<td style="text-align:center;">' + x.days_on + '天</td>' +
                '<td style="text-align:center;">' + Number(x.rps5).toFixed(0) + '</td>' +
                '<td style="text-align:center;">' + rps10 + '</td>' +
                '<td style="text-align:center;">' + rps20 + '</td>' +
                '<td style="text-align:center;">' + ratio + '</td>' +
                '<td style="text-align:center;color:' + (Number(x.chg) >= 0 ? DARK.green : DARK.red) + ';">' + chgTxt + '</td>' +
                '<td style="text-align:center;">' + _escTxt(x.style || '—') + '</td>' +
                '<td style="text-align:center;color:' + sc + ';font-weight:700;">' + _escTxt(x.strength || '') + resoTxt + '</td></tr>';
        };
        var header = '<tr style="color:' + DARK.label + ';font-size:10px;">' +
            '<th style="padding:3px 6px;text-align:left;">板块</th>' +
            '<th style="padding:3px 6px;" title="当周RPS5≥90的交易日数量">在榜</th>' +
            '<th style="padding:3px 6px;">RPS5</th><th style="padding:3px 6px;">RPS10</th><th style="padding:3px 6px;">RPS20</th>' +
            '<th style="padding:3px 6px;" title="板块内RPS50≥90个股数/板块总成分股数, 衡量主线内部强度">周扩散</th>' +
            '<th style="padding:3px 6px;">周涨幅</th>' +
            '<th style="padding:3px 6px;">风格</th>' +
            '<th style="padding:3px 6px;" title="硬核=三周期共振(RPS5/10/20同时在榜); 短期=仅RPS5在榜; 动量衰减=RPS5<60">强度</th></tr>';
        var all = mains.map(rowHtml);
        var show = all.length >= 5 ? 3 : all.length;
        var table = function (rows) {
            return '<div style="overflow-x:auto;"><table style="border-collapse:collapse;width:100%;min-width:560px;font-size:11px;">' +
                header + rows + '</table></div>';
        };
        var html = table(all.slice(0, show).join(''));
        if (all.length > show) {
            html += _foldBlock('<b>展开全部</b>（共' + all.length + '条, 已显示' + show + '条）',
                table(all.slice(show).join('')));
        }
        html += '<div style="font-size:10px;color:' + DARK.label + ';margin-top:4px;">扩散达标率 = 板块内RPS50≥90个股数/总成分股; ' +
            '【三周期共振】= RPS5/10/20 同时在榜; 排序: 硬核 &gt; 短期 &gt; 动量衰减</div>';
        return html;
    }

    // 本周轮动 (V2.0 需求 3.3): 一句话总结 + 持续/新晋/衰退三类 + 高频轮动标记
    function fmtWeekRotation(rot) {
        if (!rot) return '无明显轮动';
        var parts = [];
        if (rot.summary) parts.push('<div style="background:' + DARK.cardBg + ';border-left:3px solid ' + DARK.blue +
            ';padding:6px 8px;margin:2px 0;color:' + DARK.title + ';">' + _escTxt(rot.summary) + '</div>');
        // 标签徽章: 结构化渲染, 不入 detail 纯文本 (避免被 _escTxt 转义成源码)
        var RISK_COLOR = { '高风险': DARK.critical, '中风险': DARK.amber, '低风险': DARK.label };
        var badge = function (text, color) {
            return '<span style="display:inline-block;padding:0 5px;margin:0 4px 0 0;border-radius:8px;' +
                'font-size:10px;font-weight:700;border:1px solid ' + color + ';color:' + color + ';">' +
                _escTxt(text) + '</span>';
        };
        var line = function (items) {
            return items.map(function (x) {
                var tags = '';
                if (x.resonance) tags += badge('【三周期共振】', DARK.red);
                if (x.risk_level) tags += badge(x.risk_level, RISK_COLOR[x.risk_level] || DARK.label);
                return '<div style="margin:2px 0;">• ' + _sectorChip(x.name) + ' ' + tags +
                    '<span style="color:' + DARK.text + ';">' + _escTxt(x.detail || '') + '</span></div>';
            }).join('');
        };
        var sustained = (rot.sustained || []).map(function (x) {
            return { name: x.name, resonance: x.resonance,
                     detail: ('全周在榜' + x.days_on + '天 · ' + (x.style || '—')) };
        });
        var new_in = (rot.new_in || []).map(function (x) {
            return { name: x.name, detail: ('首次上榜' + (x.first_date || '—') + ' · 在榜' + x.days_on + '天' +
                    (x.repeat_n > 0 ? ' · 反复轮动' + x.repeat_n + '次' : '') + ' · ' + (x.style || '—')) };
        });
        var dropped = (rot.dropped || []).map(function (x) {
            return { name: x.name, risk_level: x.risk_level,
                     detail: ('退出' + (x.exit_date || '—') + ' · 曾在榜' + x.days_on + '天' +
                             (x.risk ? ' · ' + x.risk : '') + ' · ' + (x.style || '—')) };
        });
        if (sustained.length) parts.push('<b>持续主线</b>' + line(sustained));
        if (new_in.length) parts.push('<b>本周新晋</b>' + line(new_in));
        if (dropped.length) parts.push('<b>本周衰退</b>' + line(dropped));
        if (rot.high_freq && rot.high_freq.length) {
            parts.push('<span style="color:' + DARK.amber + ';font-weight:700;">⚠ 高频轮动·谨慎追高:</span> ' +
                rot.high_freq.map(function (x) { return _sectorChip(x.name) + '(' + x.flips + '次)'; }).join('、'));
        }
        return parts.join('<br/>') || '无明显轮动';
    }

    // 周情绪组合图 (V2.0 需求 3.4): X轴=当周交易日, 左Y轴=情绪阶段, 灰柱=高分板块数(右轴), 彩点=阶段, 拐点标注
    function reviewChartWeekSentiment(sent) {
        var rows = (sent && sent.series) || [];
        if (!rows.length) return '';
        var W = 560, H = 230, padL = 52, padR = 40, padT = 26, padB = 22;
        var cw = W - padL - padR, ch = H - padT - padB;
        // 情绪热度刻度: 退潮最低 → 高潮最高 (与后端 week_sentiment 一致)
        var PVAL = { '退潮期': 0.5, '冰点期': 1.1, '启动期': 1.8, '震荡期': 2.4, '发酵期': 3.0, '高潮期': 3.6 };
        var vMax = 3.6;
        var maxHigh = 1;
        rows.forEach(function (r) { if (Number(r.high_n) > maxHigh) maxHigh = Number(r.high_n); });
        var valOf = function (p) {
            var s = String(p || '');
            for (var k in PVAL) { if (s.indexOf(k) >= 0) return PVAL[k]; }
            return 1.8;
        };
        var xAt = function (i) { return padL + (rows.length === 1 ? cw / 2 : (i / (rows.length - 1)) * cw); };
        var yAt = function (p) { return padT + ch - (valOf(p) / vMax) * ch; };
        var grid = '', bars = '', dots = '', labels = '', axis = '', mile = '';
        // 左轴: 六档情绪阶段
        var axisOrder = ['退潮期', '冰点期', '启动期', '震荡期', '发酵期', '高潮期'];
        axisOrder.forEach(function (p) {
            var y = padT + ch - (PVAL[p] / vMax) * ch;
            grid += '<line x1="' + padL + '" y1="' + y + '" x2="' + (W - padR) + '" y2="' + y +
                '" stroke="' + DARK.grid + '" stroke-width="0.5" stroke-dasharray="3 3"/>';
            axis += '<text x="' + (padL - 5) + '" y="' + (y + 3) + '" text-anchor="end" font-size="8.5" fill="' + _phaseColor(p) + '">' + p + '</text>';
        });
        // 右轴: 高分板块数刻度
        var ticks = [0, Math.round(maxHigh / 2), maxHigh];
        for (var t = 0; t < ticks.length; t++) {
            var ty = padT + ch - (ticks[t] / maxHigh) * ch;
            axis += '<text x="' + (W - padR + 5) + '" y="' + (ty + 3) + '" font-size="8" fill="' + DARK.label + '">' + ticks[t] + '</text>';
        }
        rows.forEach(function (r, i) {
            var x = xAt(i), y = yAt(r.phase);
            var bh = Math.max(2, (Number(r.high_n) || 0) / maxHigh * ch);
            bars += '<rect x="' + (x - 4) + '" y="' + (padT + ch - bh) + '" width="8" height="' + bh +
                '" fill="' + DARK.inactive + '" opacity="0.45" rx="2"/>';
            var color = _phaseColor(r.phase);
            dots += '<circle cx="' + x + '" cy="' + y + '" r="4" fill="' + color + '" stroke="#fff" stroke-width="1.2"/>';
            labels += '<text x="' + x + '" y="' + (H - 6) + '" text-anchor="middle" font-size="9" fill="' + DARK.label + '">' +
                _escTxt(String(r.date || '').slice(4)) + '</text>';
        });
        // 拐点标注: 启动日/高点日/退潮日 (milestones 含 *_label)
        // 图上用短标签(启动日/高点日/退潮日)+动态锚点+垂直错开, 避免与纵轴/相邻拐点文字重叠(乱码观感);
        // 完整说明保留在图例
        var _mileShort = function (s) {
            var t = String(s || '');
            if (t.indexOf('启动') >= 0) return '启动日';
            if (t.indexOf('高点') >= 0) return '高点日';
            if (t.indexOf('退潮') >= 0) return '退潮日';
            if (t.indexOf('冰点') >= 0) return '冰点日';
            if (t.indexOf('高峰') >= 0) return '高峰日';
            return t.length > 4 ? t.slice(0, 4) : t;
        };
        var ml = (sent && sent.milestones) || {};
        var mileDefs = [
            { date: ml.start_date, label: ml.start_label, color: DARK.blue },
            { date: ml.peak_date, label: ml.peak_label, color: DARK.red },
            { date: ml.tideout_date, label: ml.tideout_label, color: DARK.amber }
        ];
        var mileLegend = [], mileIdx = 0;
        mileDefs.forEach(function (md) {
            if (!md.date) return;
            var found = -1;
            for (var k = 0; k < rows.length; k++) { if (String(rows[k].date) === String(md.date)) { found = k; break; } }
            if (found < 0) return;
            var x = xAt(found);
            var short = _mileShort(md.label);
            var sw = short.length * 9;
            var anchor = 'middle', tx = x;
            if (x - sw / 2 < padL) { anchor = 'start'; tx = x + 5; }
            else if (x + sw / 2 > W - padR) { anchor = 'end'; tx = x - 5; }
            mile += '<line x1="' + x + '" y1="' + (padT - 6) + '" x2="' + x + '" y2="' + (H - padB + 4) +
                '" stroke="' + md.color + '" stroke-width="1" stroke-dasharray="4 3" opacity="0.65"/>' +
                '<path d="M ' + x + ' ' + (padT - 8) + ' l 6 -7 l -12 0 z" fill="' + md.color + '"/>' +
                '<text x="' + tx + '" y="' + (padT - 10 + mileIdx * 13) + '" text-anchor="' + anchor + '" font-size="8" font-weight="700" fill="' +
                md.color + '" stroke="#fff" stroke-width="2.5" paint-order="stroke">' + _escTxt(short) + '</text>';
            mileLegend.push('<span style="color:' + md.color + ';">▲ ' + _escTxt(md.label || '') + '</span>');
            mileIdx++;
        });
        var legend = '<div style="font-size:9px;color:' + DARK.label + ';">彩点=当日情绪阶段(左轴) · 灰柱=高分板块数(右轴)' +
            (mileLegend.length ? ' · ' + mileLegend.join(' ') : '') + '</div>';
        return '<div style="margin:6px 0;"><div style="font-size:11px;font-weight:700;color:' + DARK.chartTitle +
            ';margin-bottom:2px;">本周情绪组合（当周' + rows.length + '个交易日）</div>' +
            '<svg width="100%" viewBox="0 0 ' + W + ' ' + H + '" style="display:block;max-width:600px;">' +
            grid + bars + dots + labels + mile + axis + '</svg>' + legend + '</div>';
    }

    // 本周情绪演变 (V2.0 需求 3.4): 组合图 + 逐日阶段+判定规则小字 + 周情绪总结
    function fmtWeekSentiment(sent) {
        if (!sent || !(sent.series || []).length) return '无情绪历史数据(周五复盘缺失)';
        var parts = [];
        parts.push(reviewChartWeekSentiment(sent));
        var dayParts = sent.series.map(function (x) {
            return '<span style="color:' + DARK.text + ';font-weight:600;">' + _escTxt(String(x.date || '').slice(4)) + '</span> ' +
                '<b style="color:' + _phaseColor(x.phase) + ';">' + _escTxt(x.phase || '—') + '</b>' +
                (x.phase_rule ? '<span style="font-size:10px;color:' + DARK.label + ';">(' + _escTxt(x.phase_rule) + ')</span>' : '');
        }).join('<span style="color:' + DARK.dim + ';"> · </span>');
        parts.push('逐日: ' + dayParts);
        if (sent.trend) parts.push('阶段演变: <b>' + _escTxt(sent.trend) + '</b>');
        if (sent.summary) parts.push('<div style="background:' + DARK.cardBg + ';border-left:3px solid ' + DARK.blue +
            ';padding:6px 8px;margin:4px 0;color:' + DARK.title + ';">周情绪总结: ' + _escTxt(sent.summary) + '</div>');
        return parts.join('<br/>');
    }

    // 上周复盘·结果验证闭环 (V2.0 需求 4.3): 上周「下周关注」对照本周实际表现
    function fmtLastWeekCheck(lwc) {
        if (!lwc) return '';
        if (!lwc.has_data) return '<span style="color:' + DARK.label + ';">' + _escTxt(lwc.note || '暂无上周复盘数据') + '</span>';
        var vColor = { '晋级': DARK.green, '衰退': DARK.red, '震荡': DARK.label };
        var parts = [];
        parts.push('上周复盘(' + _escTxt(lwc.prev_week_id || '—') + ', 更新 ' + _escTxt(lwc.prev_date || '—') + ')「下周关注」验证:');
        (lwc.items || []).forEach(function (it) {
            parts.push('<div style="margin:2px 0;">• ' + _sectorChip(it.name) +
                ' <span style="color:' + DARK.label + ';">' + _escTxt(it.expected || '重点跟踪') + '</span> → ' +
                '<b style="color:' + (vColor[it.verdict] || DARK.label) + ';">' + _escTxt(it.verdict || '—') + '</b></div>');
        });
        if (lwc.summary) {
            var hrC = (lwc.hit_rate !== null && lwc.hit_rate !== undefined && lwc.hit_rate >= 50) ? DARK.green
                : (lwc.hit_rate !== null && lwc.hit_rate !== undefined && lwc.hit_rate >= 30) ? DARK.amber : DARK.label;
            parts.push('验证总结: <b style="color:' + hrC + ';">' + _escTxt(lwc.summary) + '</b>');
        }
        return parts.join('<br/>');
    }

    // 下周关注 (V2.0 需求 3.7): ★重点跟踪 / 普通观察 / ⚠风险回避 + 跟踪规则
    function fmtWeekWatch(watch) {
        if (!watch) return '暂无关注标的';
        var parts = [];
        var item = function (x, mark, color) {
            return '<div style="margin:3px 0;">' + mark + ' ' + _sectorChip(x.name) +
                ' <span style="color:' + DARK.label + ';">' + _escTxt(x.reason || '') + '</span>' +
                (x.rule ? '<div style="font-size:10px;color:' + color + ';padding-left:20px;">跟踪规则: ' + _escTxt(x.rule) + '</div>' : '') +
                '</div>';
        };
        if (watch.focus && watch.focus.length) parts.push('<div style="font-weight:700;color:' + DARK.red + ';">★ 重点跟踪</div>' +
            watch.focus.map(function (x) { return item(x, '★', DARK.red); }).join(''));
        if (watch.observe && watch.observe.length) parts.push('<div style="font-weight:700;color:' + DARK.blue + ';">普通观察</div>' +
            watch.observe.map(function (x) { return item(x, '•', DARK.blue); }).join(''));
        if (watch.avoid && watch.avoid.length) parts.push('<div style="font-weight:700;color:' + DARK.amber + ';">⚠ 风险回避</div>' +
            watch.avoid.map(function (x) { return item(x, '⚠', DARK.amber); }).join(''));
        return parts.join('<div style="height:5px;"></div>') || '暂无关注标的';
    }

    // 每周复盘统计 → 完整 HTML V2.0 (4.1置顶结论 / 3.1-3.7 七个模块 / 4.2-4.5 四个新模块)
    function buildWeeklyReviewHtml(data) {
        if (!data || !data.week_id) return '<div class="empty-msg">暂无周度复盘数据, 请先运行 rps_calc.py（每周五收盘后生成）</div>';
        var card = function (title, body, borderColor) {
            return '<div style="background:' + DARK.cardBg + ';border:1px solid ' + DARK.cardBorder + ';border-radius:8px;padding:10px 12px;margin-bottom:10px;">' +
                '<div style="font-weight:700;color:' + DARK.title + ';margin-bottom:6px;border-left:3px solid ' + (borderColor || DARK.red) +
                ';padding-left:6px;font-size:13px;">' + title + '</div>' +
                '<div style="font-size:12px;line-height:1.9;color:' + DARK.text + ';word-break:break-all;">' + body + '</div></div>';
        };
        var html = '<div style="font-size:12px;background:' + DARK.panelBg + ';border-radius:10px;padding:10px;border:1px solid ' + DARK.cardBorder + ';">';
        // 4.1 置顶: 本周一句话核心结论 (全宽高亮卡片)
        var cc = data.core_conclusion || {};
        if (cc.text) {
            html += '<div style="background:linear-gradient(90deg,#fff5f5,#ffffff);border:1px solid ' + DARK.red +
                ';border-left:4px solid ' + DARK.red + ';border-radius:8px;padding:10px 12px;margin-bottom:10px;">' +
                '<div style="font-size:10px;color:' + DARK.red + ';font-weight:700;margin-bottom:4px;">本周一句话核心结论</div>' +
                '<div style="font-size:13px;font-weight:700;color:' + DARK.title + ';line-height:1.8;">' + _escTxt(cc.text) + '</div></div>';
        }
        // 3.1 本周定调 + 周市场广度子卡片 + 4.2 仓位指引
        var mbHtml = fmtWeekMarketBreadth(data.market_breadth);
        var toneBody = fmtWeekTone(data.tone);
        if (mbHtml) {
            toneBody += '<div style="border-top:1px dashed ' + DARK.cardBorder + ';margin-top:6px;padding-top:6px;">' +
                '<b>周市场广度</b><br/>' + mbHtml + '</div>';
        }
        html += card('一、本周定调', toneBody);
        html += card('二、周度仓位指引', fmtPositionGuide(data.position_guide), DARK.blue);
        // 3.2 本周主线
        html += card('三、本周主线(在榜≥3天)', fmtWeekMainlines(data.mainlines));
        // 3.3 本周轮动
        html += card('四、本周轮动', fmtWeekRotation(data.rotation));
        // 4.4 周度异常风险信号 (无风险默认隐藏)
        var abn = data.abnormal_risks || {};
        if (abn.signals && abn.signals.length) {
            html += card('五、周度异常风险信号', fmtAbnormalSignals(abn) +
                (abn.note ? '<div style="font-size:10px;color:' + DARK.label + ';margin-top:4px;">' + _escTxt(abn.note) + '</div>' : ''), DARK.amber);
        }
        // 3.4 本周情绪演变
        html += card('六、本周情绪演变', fmtWeekSentiment(data.sentiment));
        // 3.5 消息面周度快照
        html += card('七、消息面周度快照', fmtReviewNews(data.news));
        // 3.6 本周推荐准确率
        html += card('八、本周推荐准确率', fmtReviewWeekAccuracy(data.rec_accuracy));
        // 4.3 上周复盘·结果验证闭环
        html += card('九、上周复盘·结果验证闭环', fmtLastWeekCheck(data.last_week_check), DARK.blue);
        // 4.5 主线核心龙头映射
        html += card('十、主线核心龙头映射', fmtCoreTargets(data.core_targets), DARK.blue);
        // 3.7 下周关注
        html += card('十一、下周关注', fmtWeekWatch(data.watch));
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
            buildPredictionBox(s) +
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
            buildPredictionBox(s) +
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

    /* ===== 个股 AI 综合总分析 (合并综评 Tab 5 个零散卡片) ===== */
    function fmtDetailAiAnalysis(ai) {
        if (!ai) return '';
        var O = ai.overview || {};
        var F = ai.fundamentals || {};
        var H = ai.highlights || [];
        var R = ai.risks || [];
        var T = ai.technical || {};
        var C = ai.capital || {};
        var A = ai.action || {};
        var K = ai.keyLevels || {};
        var S = ai.sectorHeat || {};

        var chgCls = (O.changePercent && Number(O.changePercent) >= 0) ? 'pos' : 'neg';
        var chgSign = (O.changePercent && Number(O.changePercent) >= 0) ? '+' : '';
        var rCol = A.color || 'warn';

        // 1. Hero 顶部速览 (渐变卡片)
        var hero = '<div class="ai-hero">' +
            '<div class="ai-hero-row1">' +
                '<span class="ai-hero-name">' + _escTxt(O.name || '--') + '</span>' +
                '<span class="ai-hero-code">' + _escTxt(O.code || '--') + '</span>' +
                '<span class="ai-hero-status">' + _escTxt(O.marketStatus || '--') + '</span>' +
                '<span class="ai-hero-price ' + chgCls + '">' + (O.price != null ? fmtDetailNum(O.price, 2) : '--') + '</span>' +
                '<span class="ai-hero-chg ' + chgCls + '">' +
                    (O.changePercent != null ? chgSign + fmtDetailNum(O.changePercent, 2) + '%' : '--') +
                '</span>' +
            '</div>' +
            '<div class="ai-hero-row2">' +
                '<div class="ai-hero-badge"><div class="num">' + (O.totalScore != null ? fmtDetailNum(O.totalScore, 0) : '--') + '</div><div class="lab">综合评分</div></div>' +
                '<div class="ai-hero-badge"><div class="num" style="font-size:16px;">' + (O.beatStock || '--') + '</div><div class="lab">打败个股</div></div>' +
                '<div class="ai-hero-rec">' +
                    '<span class="tag ' + rCol + '">' + _escTxt(A.recommend || '--') + '</span>' +
                    '<span class="rsn">' + _escTxt(A.reason || '') + '</span>' +
                '</div>' +
            '</div>' +
            (O.todaySummary ? '<div class="ai-hero-summary">💡 ' + _escTxt(O.todaySummary) + '</div>' : '') +
        '</div>';

        // 2. 基本面速览 (3列网格)
        var peTagCls = 'gray';
        var peScore = F.pe && F.pe.score != null ? Number(F.pe.score) : 0;
        if (peScore >= 4) peTagCls = 'green';
        else if (peScore <= 1 && peScore > 0) peTagCls = 'red';
        else if (peScore === 3) peTagCls = 'blue';

        var mvTxt = F.marketCap && F.marketCap.label || '--';
        var mvTagCls = (mvTxt.indexOf('大盘') >= 0 || mvTxt.indexOf('超大盘') >= 0) ? 'blue' : 'amber';
        if (mvTxt.indexOf('微盘') >= 0 || mvTxt.indexOf('小盘') >= 0) mvTagCls = 'amber';

        var circTxt = F.circCap && F.circCap.ratioTxt || '--';
        var circTagCls = circTxt === '全流通' ? 'green' : (circTxt === '限售较多' ? 'red' : 'gray');

        var fund = '<div class="ai-sec info">' +
            '<div class="ai-sec-title">📊 基本面速览</div>' +
            '<div class="fund-grid">' +
                '<div class="fund-cell"><div class="k">总市值</div><div class="v">' + _escTxt(F.marketCap ? (F.marketCap.txt || '--') : '--') + '</div>' +
                    '<span class="tag ' + mvTagCls + '">' + _escTxt(mvTxt) + '</span></div>' +
                '<div class="fund-cell"><div class="k">流通市值</div><div class="v">' + _escTxt(F.circCap ? (F.circCap.txt || '--') : '--') + '</div>' +
                    '<span class="tag ' + circTagCls + '">' + _escTxt(circTxt) + '</span></div>' +
                '<div class="fund-cell"><div class="k">PE(TTM)</div><div class="v">' + _escTxt(F.pe ? (F.pe.txt || '--') : '--') + '</div>' +
                    '<span class="tag ' + peTagCls + '">' + _escTxt(F.pe ? (F.pe.label || '--') : '--') + '</span></div>' +
                '<div class="fund-cell"><div class="k">PE 历史分位</div><div class="v">' + _escTxt(F.pe ? (F.pe.percentileTxt || '--') : '--') + '</div>' +
                    '<span class="tag gray">' + _escTxt(F.pb ? (F.pb.label || '--') : '--') + '</span></div>' +
                '<div class="fund-cell"><div class="k">PB</div><div class="v">' + _escTxt(F.pb ? (F.pb.txt || '--') : '--') + '</div>' +
                    '<span class="tag blue">' + _escTxt(F.pb ? (F.pb.label || '--') : '--') + '</span></div>' +
                '<div class="fund-cell"><div class="k">换手率 / 量比</div><div class="v">' + _escTxt(F.turnover ? (F.turnover.txt || '--') : '--') + ' / ' + _escTxt(F.volumeRatio ? (F.volumeRatio.txt || '--') : '--') + '</div>' +
                    '<span class="tag amber">' + _escTxt(F.volumeRatio ? (F.volumeRatio.label || '--') : '--') + '</span></div>' +
            '</div>' +
        '</div>';

        // 3. 核心亮点 (good) & 风险提示 (bad)
        var hlItems = H.map(function (it) {
            return '<div class="hr-item"><div class="icon">' + (it.icon || '✨') + '</div>' +
                '<div class="body"><div class="t">' + _escTxt(it.title || '') + '</div>' +
                '<div class="d">' + _escTxt(it.body || '') + '</div></div></div>';
        }).join('');
        var highlights = '<div class="ai-sec good">' +
            '<div class="ai-sec-title">✅ 核心亮点 <span style="font-size:10px;font-weight:400;color:#64748b;">共 ' + H.length + ' 项</span></div>' +
            '<div class="hr-list">' + (hlItems || '<div class="muted">暂无显著亮点信号</div>') + '</div>' +
        '</div>';

        var rkItems = R.map(function (it) {
            return '<div class="hr-item"><div class="icon">' + (it.icon || '⚠️') + '</div>' +
                '<div class="body"><div class="t">' + _escTxt(it.title || '') + '</div>' +
                '<div class="d">' + _escTxt(it.body || '') + '</div></div></div>';
        }).join('');
        var risks = '<div class="ai-sec bad">' +
            '<div class="ai-sec-title">⚠️ 风险提示 <span style="font-size:10px;font-weight:400;color:#64748b;">共 ' + R.length + ' 项</span></div>' +
            '<div class="hr-list">' + rkItems + '</div>' +
        '</div>';

        // 4. 买卖建议卡片 (渐变)
        var actColor = A.color || 'warn';
        var action = '<div class="ai-sec ' + actColor + '">' +
            '<div class="ai-sec-title">🎯 买卖操作建议</div>' +
            '<div class="act-card ' + actColor + '">' +
                '<div class="act-head">' +
                    '<span class="act-tag">' + _escTxt(A.recommend || '--') + '</span>' +
                    '<span class="act-reason">📌 ' + _escTxt(A.reason || '') + '</span>' +
                '</div>' +
                '<div class="act-grid">' +
                    '<div class="act-cell"><div class="k">' + (A.recommend && (A.recommend.indexOf('买') >= 0) ? '建议买入区间' : (A.recommend == '观望' ? '关注价格' : '建议减仓区间')) + '</div><div class="v">' + _escTxt(A.buyZone || '--') + '</div></div>' +
                    '<div class="act-cell"><div class="k">' + (A.stopLoss ? '止损价' : '持仓参考') + '</div><div class="v">' + _escTxt(A.stopLoss || (A.recommend == '观望' ? (A.holdPeriod || '--') : '反弹处理')) + '</div></div>' +
                    '<div class="act-cell"><div class="k">建议持有期</div><div class="v">' + _escTxt(A.holdPeriod || '--') + '</div></div>' +
                '</div>' +
                (A.disclaimer ? '<div class="act-disclaimer">' + _escTxt(A.disclaimer) + '</div>' : '') +
            '</div>' +
        '</div>';

        // 5. 技术面 + 资金面 (双栏)
        var macdV = T.macd || {};
        var kdjV = T.kdj || {};
        var rsiV = T.rsi || {};
        var bollV = T.boll || {};
        var maMap = T.ma || {};
        var sr = T.stageRise || {};
        function srPct(v) {
            if (v == null || v === '' || isNaN(Number(v))) return '--';
            var n = Number(v);
            return (n >= 0 ? '<span class="pos">+' + n.toFixed(2) + '%</span>' : '<span class="neg">' + n.toFixed(2) + '%</span>');
        }
        function mcv(txt, cls) {
            return '<div class="mini-card"><div class="mc-k">' + _escTxt(txt) + '</div><div class="mc-v ' + (cls || '') + '">' + Array.prototype.slice.call(arguments, 2).join('<br>') + '</div></div>';
        }
        var maTxt = 'MA5 ' + (maMap.MA5 || '--') + ' / MA10 ' + (maMap.MA10 || '--') + '<br>MA20 ' + (maMap.MA20 || '--') + ' / MA60 ' + (maMap.MA60 || '--');
        var maCls = T.maAlign == '多头排列' ? 'good' : (T.maAlign == '空头排列' ? 'bad' : '');

        var macdCls = (macdV.signal || '').indexOf('多头') >= 0 || (macdV.signal || '').indexOf('红柱') >= 0 ? 'good'
            : ((macdV.signal || '').indexOf('空头') >= 0 || (macdV.signal || '').indexOf('绿柱') >= 0 ? 'bad' : '');

        var kdjCls = (kdjV.signal || '').indexOf('金叉') >= 0 || (kdjV.signal || '').indexOf('偏多') >= 0 ? 'good'
            : ((kdjV.signal || '').indexOf('死叉') >= 0 ? 'bad' : ((kdjV.signal || '').indexOf('超买') >= 0 ? 'bad' : ((kdjV.signal || '').indexOf('超卖') >= 0 ? 'good' : '')));

        var rsiCls = (rsiV.signal || '').indexOf('超卖') >= 0 ? 'good' : ((rsiV.signal || '').indexOf('超买') >= 0 ? 'bad' : '');

        var bollCls = (bollV.signal || '').indexOf('偏强') >= 0 || (bollV.signal || '').indexOf('突破') >= 0 ? 'good'
            : ((bollV.signal || '').indexOf('偏弱') >= 0 || (bollV.signal || '').indexOf('跌破') >= 0 ? 'bad' : '');

        var dual = '<div class="ai-sec purple">' +
            '<div class="ai-sec-title">📈 技术面 &amp; 资金面</div>' +
            '<div class="dual-col">' +
                mcv('均线形态 · ' + _escTxt(T.maAlign || '--'), maCls, maTxt) +
                mcv('MACD · ' + _escTxt(macdV.signal || '--'), macdCls,
                    'DIF ' + (macdV.dif != null ? fmtDetailNum(macdV.dif, 3) : '--') + ' / DEA ' + (macdV.dea != null ? fmtDetailNum(macdV.dea, 3) : '--') + '<br>柱 ' + (macdV.hist != null ? fmtDetailNum(macdV.hist, 3) : '--') + ' · ' + _escTxt(macdV.zone || '')) +
                mcv('KDJ · ' + _escTxt(kdjV.signal || '--'), kdjCls,
                    'K ' + (kdjV.k != null ? fmtDetailNum(kdjV.k, 1) : '--') + ' / D ' + (kdjV.d != null ? fmtDetailNum(kdjV.d, 1) : '--') + ' / J ' + (kdjV.j != null ? fmtDetailNum(kdjV.j, 1) : '--')) +
                mcv('RSI(6) · ' + _escTxt(rsiV.signal || '--'), rsiCls,
                    (rsiV.value != null ? fmtDetailNum(rsiV.value, 1) : '--')) +
                mcv('BOLL · ' + _escTxt(bollV.signal || '--'), bollCls,
                    '上 ' + (bollV.upper || '--') + ' / 中 ' + (bollV.mid || '--') + '<br>下 ' + (bollV.lower || '--')) +
                mcv('资金 · 主力连续' + (C.streak || 0) + '日流入', (C.streak || 0) >= 3 ? 'good' : (((C.mainToday && C.mainToday.value) || 0) < 0 ? 'bad' : ''),
                    '今日 ' + _escTxt(C.mainToday ? (C.mainToday.txt || '--') : '--') + '<br>近3日 ' + _escTxt(C.main3d ? (C.main3d.txt || '--') : '--') + ' / 近5日 ' + _escTxt(C.main5d ? (C.main5d.txt || '--') : '--')) +
            '</div>' +
            (C.interpret ? '<div class="note" style="margin-top:8px;">💬 ' + _escTxt(C.interpret) + '</div>' : '') +
            '<div class="stage-rise">' +
                '<div class="sr-chip"><div class="k">近5日涨跌</div><div class="v">' + srPct(sr['5D']) + '</div></div>' +
                '<div class="sr-chip"><div class="k">近20日涨跌</div><div class="v">' + srPct(sr['20D']) + '</div></div>' +
                '<div class="sr-chip"><div class="k">近60日涨跌</div><div class="v">' + srPct(sr['60D']) + '</div></div>' +
            '</div>' +
        '</div>';

        // 6. 关键价位 (支撑+压力可视化)
        var cur = K.current;
        function buildLevelList(items, kind) {
            var fillCls = kind == 'res' ? 'res' : 'sup';
            var valCls = kind == 'res' ? 'bad' : 'good';
            // 找范围用于百分比条
            var nums = items.map(function (i) { return Number(i.value); }).filter(function (v) { return !isNaN(v) && v > 0; });
            if (nums.length === 0 || cur == null) {
                return items.map(function (it) {
                    return '<div class="kl-line"><span class="n">' + _escTxt(it.name || '') + '</span>' +
                        '<span class="v">' + _escTxt(it.value != null ? String(it.value) : '--') + '</span>' +
                        '<span class="bar"></span></div>';
                }).join('');
            }
            var mn = Math.min.apply(null, nums.concat([cur]));
            var mx = Math.max.apply(null, nums.concat([cur]));
            var rng = (mx - mn) || 1;
            return items.map(function (it) {
                var v = Number(it.value);
                var pct = isNaN(v) ? 0 : Math.max(3, Math.min(100, (v - mn) / rng * 100));
                return '<div class="kl-line"><span class="n">' + _escTxt(it.name || '') + '</span>' +
                    '<span class="v ' + (isNaN(v) ? '' : (kind == 'res' ? (v >= cur ? valCls : '') : (v <= cur ? valCls : ''))) + '">' + _escTxt(it.value != null ? String(it.value) : '--') + '</span>' +
                    '<span class="bar"><span class="fill ' + fillCls + '" style="width:' + pct + '%;"></span></span>' +
                '</div>';
            }).join('');
        }
        var resItems = (K.resistance || []).filter(function (i) { return i.value != null; });
        var supItems = (K.support || []).filter(function (i) { return i.value != null; });
        var keyLv = '<div class="ai-sec warn">' +
            '<div class="ai-sec-title">💰 关键价位 (压力/支撑)</div>' +
            '<div class="dual-col">' +
                '<div>' +
                    '<div class="kl-block"><div class="blk-title">🔴 压力位 (上方阻力)</div>' +
                    buildLevelList(resItems, 'res') +
                    '</div>' +
                '</div>' +
                '<div>' +
                    '<div class="kl-block"><div class="blk-title">🟢 支撑位 (下方兜底)</div>' +
                    buildLevelList(supItems, 'sup') +
                    '</div>' +
                '</div>' +
            '</div>' +
            (cur != null ? '<div class="note">当前价 <b>' + fmtDetailNum(cur, 2) + '</b>，压力位在其上方、支撑位在其下方。条形长度代表价位相对远近。</div>' : '') +
        '</div>';

        // 7. 板块热度联动
        var shRows = (S.membership || []).map(function (s) {
            return '<div class="sh-row">' +
                '<span class="nm">' + _escTxt(s.name || '--') + '</span>' +
                (s.tiers ? '<span class="tr">' + _escTxt(s.tiers) + '</span>' : '') +
                '<span class="st ' + (s.status == '在榜' ? 'in' : 'out') + '">' + _escTxt(s.status || '--') + '</span>' +
            '</div>';
        }).join('');
        var sectorHeat = '<div class="ai-sec dark">' +
            '<div class="ai-sec-title">🏭 所属板块热度联动</div>' +
            '<div class="sh-level">' + _escTxt(S.level || '--') + '</div>' +
            (shRows
                ? '<div class="sh-list">' + shRows + '</div>'
                : '<div class="muted">该股暂不属于 RPS 强势榜板块（可能属于非热点行业或处于弱势周期）。</div>') +
            (S.tierCount != null ? '<div class="note">所属强势板块档位数量: <b>' + S.tierCount + '</b> (最多 3 档=RPS5/RPS10/RPS20 三档共振)</div>' : '') +
        '</div>';

        var footer = ai.generatedAt ? '<div class="ai-generated">🕐 分析于 ' + _escTxt(ai.generatedAt) + '，数据由量化指标自动生成，仅供编程学习参考。</div>' : '';

        return '<div class="ai-wrap">' + hero + fund + highlights + risks + action + dual + keyLv + sectorHeat + footer + '</div>';
    }


    /* ===== 四级板块下钻渲染 (fmtDrill* / buildDrill*, 2026-08-25) ===== */

    // RPS 值: ≥90红 / ≥70蓝 / 其余纯文本 / 空值--
    function fmtDrillRps(v) {
        if (v === null || v === undefined || v === '' || isNaN(Number(v))) return '--';
        var n = Number(v);
        var txt = n.toFixed(1);
        if (n >= 90) return '<span class="tag-red">' + txt + '</span>';
        if (n >= 70) return '<span class="tag-blue">' + txt + '</span>';
        return txt;
    }

    // 排名变化: 刚入榜(连续1天) → "新", 正↑ / 负↓ / 0→
    function fmtDrillRankChange(change, continuous) {
        if (continuous === 1) return '<span class="tag-green">新</span>';
        if (change === null || change === undefined || change === '' || isNaN(Number(change))) return '--';
        var n = Number(change);
        if (n > 0) return '<span class="rank-up">↑' + n + '</span>';
        if (n < 0) return '<span class="rank-down">↓' + Math.abs(n) + '</span>';
        return '<span class="rank-flat">→</span>';
    }

    // 状态标签: 调入/调出/在榜/空
    function fmtDrillStatus(status) {
        if (status === '调入') return '<span class="tag-green">调入</span>';
        if (status === '调出') return '<span class="tag-red">调出</span>';
        if (status === '在榜') return '<span class="tag-blue">在榜</span>';
        return '--';
    }

    // 涨跌幅/换手率: 红涨绿跌 (A股口径, .pos=红 .neg=绿)
    function fmtDrillPct(v) {
        if (v === null || v === undefined || v === '' || isNaN(Number(v))) return '--';
        var n = Number(v);
        return n > 0 ? '<span class="pos">+' + n.toFixed(2) + '</span>'
            : n < 0 ? '<span class="neg">' + n.toFixed(2) + '</span>'
            : n.toFixed(2);
    }

    // 普通数值 (价/量比等): 保留2位小数, 去尾零
    function fmtDrillNum(v) {
        if (v === null || v === undefined || v === '' || isNaN(Number(v))) return '--';
        return Number(v).toFixed(2).replace(/\.?0+$/, '');
    }

    // 面包屑: state={level1Name, level2Name, stockName}; 首页>一级>二级>个股, 每级可点击返回
    function buildDrillBreadcrumb(state) {
        var s = state || {};
        var parts = ['<a class="crumb-link" href="javascript:;" onclick="drillGoHome()">首页</a>'];
        if (s.level1Name) {
            parts.push('<span class="crumb-sep">›</span>');
            parts.push(s.level2Name
                ? '<a class="crumb-link" href="javascript:;" onclick="drillGoLevel2(\'' + _escJs(s.level1Name) + '\')">' + _escTxt(s.level1Name) + '</a>'
                : '<span class="crumb-cur">' + _escTxt(s.level1Name) + '</span>');
        }
        if (s.level2Name) {
            parts.push('<span class="crumb-sep">›</span>');
            parts.push(s.stockName
                ? '<a class="crumb-link" href="javascript:;" onclick="drillGoLevel3(\'' + _escJs(s.level2Name) + '\')">' + _escTxt(s.level2Name) + '</a>'
                : '<span class="crumb-cur">' + _escTxt(s.level2Name) + '</span>');
        }
        if (s.stockName) {
            parts.push('<span class="crumb-sep">›</span>');
            parts.push('<span class="crumb-cur">' + _escTxt(s.stockName) + '</span>');
        }
        return parts.join('');
    }

    // 第1级: 一级板块 RPS 排行 (level1_rps API 响应 {level1:[...]})
    function buildDrillLevel1Html(d) {
        var list = (d && d.level1) || [];
        if (!list.length) return '<div class="empty-msg">暂无一级板块数据</div>';
        var html = '<div style="overflow-x:auto;"><table class="drill-table"><thead><tr>' +
            '<th>排名</th><th>一级板块</th><th>RPS5</th><th>RPS10</th><th>RPS20</th>' +
            '<th>排名变化</th><th>连续上榜</th><th>状态</th></tr></thead><tbody>';
        list.forEach(function (r, i) {
            html += '<tr>' +
                '<td>' + (r.display_rank || i + 1) + '</td>' +
                '<td style="text-align:left;padding-left:6px;"><a class="drill-link" href="javascript:;" onclick="drillGoLevel2(\'' + _escJs(r.name) + '\')">' + _escTxt(r.name) + '</a>' +
                (r.level2_count ? '<span class="badge-mini">' + r.level2_count + '个二级</span>' : '') + '</td>' +
                '<td class="rps-val">' + fmtDrillRps(r.RPS5) + '</td>' +
                '<td>' + fmtDrillRps(r.RPS10) + '</td>' +
                '<td>' + fmtDrillRps(r.RPS20) + '</td>' +
                '<td>' + fmtDrillRankChange(r.rank5_change, r.continuous5) + '</td>' +
                '<td>' + (r.continuous5 === null || r.continuous5 === undefined ? '--' : r.continuous5) + '</td>' +
                '<td>' + fmtDrillStatus(r.status) + '</td>' +
                '</tr>';
        });
        return html + '</tbody></table></div>';
    }

    // 第2级: 二级板块 RPS 排行 (level2_rps API 响应 {level2:[...]}, 组内RPS)
    function buildDrillLevel2Html(d) {
        var list = (d && d.level2) || [];
        if (!list.length) return '<div class="empty-msg">该一级板块暂无二级板块数据</div>';
        var html = '<div style="overflow-x:auto;"><table class="drill-table"><thead><tr>' +
            '<th>排名</th><th>二级板块</th><th>RPS5</th><th>RPS10</th><th>RPS20</th>' +
            '<th>排名变化</th><th>连续上榜</th><th>状态</th></tr></thead><tbody>';
        list.forEach(function (r, i) {
            html += '<tr>' +
                '<td>' + (r.display_rank || i + 1) + '</td>' +
                '<td style="text-align:left;padding-left:6px;"><a class="drill-link" href="javascript:;" onclick="drillGoLevel3(\'' + _escJs(r.name) + '\')">' + _escTxt(r.name) + '</a>' +
                (r.stock_count ? '<span class="badge-mini">' + r.stock_count + '只</span>' : '') + '</td>' +
                '<td class="rps-val">' + fmtDrillRps(r.RPS5) + '</td>' +
                '<td>' + fmtDrillRps(r.RPS10) + '</td>' +
                '<td>' + fmtDrillRps(r.RPS20) + '</td>' +
                '<td>' + fmtDrillRankChange(r.rank5_change, r.continuous5) + '</td>' +
                '<td>' + (r.continuous5 === null || r.continuous5 === undefined ? '--' : r.continuous5) + '</td>' +
                '<td>' + fmtDrillStatus(r.status) + '</td>' +
                '</tr>';
        });
        return html + '</tbody></table></div>';
    }

    // 第3级: 成分股列表 (level2_stocks API 响应 {stocks:[...]})
    // 默认按 RPS50 降序(空值排后), 再按当日涨跌幅降序
    function buildDrillLevel3Html(d) {
        var list = (d && d.stocks) || [];
        if (!list.length) return '<div class="empty-msg">该二级板块暂无成分股数据</div>';
        var sorted = list.slice().sort(function (a, b) {
            var ra = (a.rps50 === null || a.rps50 === undefined || isNaN(Number(a.rps50))) ? -1 : Number(a.rps50);
            var rb = (b.rps50 === null || b.rps50 === undefined || isNaN(Number(b.rps50))) ? -1 : Number(b.rps50);
            if (rb !== ra) return rb - ra;
            var pa = (a.pct_chg === null || a.pct_chg === undefined || isNaN(Number(a.pct_chg))) ? -Infinity : Number(a.pct_chg);
            var pb = (b.pct_chg === null || b.pct_chg === undefined || isNaN(Number(b.pct_chg))) ? -Infinity : Number(b.pct_chg);
            return pb - pa;
        });
        var html = '<div style="overflow-x:auto;"><table class="drill-table"><thead><tr>' +
            '<th>代码</th><th>个股名称</th><th>最新价</th><th>涨跌幅%</th><th>换手率%</th><th>量比</th><th>RPS50</th><th>细分行业</th></tr></thead><tbody>';
        sorted.forEach(function (s) {
            var price = (s.price === null || s.price === undefined) ? (s.Price === null || s.Price === undefined ? '--' : s.Price) : s.price;
            html += '<tr>' +
                '<td>' + _escTxt(s.ts_code || '--') + '</td>' +
                '<td style="text-align:left;padding-left:6px;"><a class="drill-link" href="javascript:;" onclick="openDrillStock(\'' + _escJs(s.ts_code) + '\',\'' + _escJs(s.name || '') + '\')">' + _escTxt(s.name || '--') + '</a></td>' +
                '<td>' + fmtDrillNum(price) + '</td>' +
                '<td>' + fmtDrillPct(s.pct_chg) + '</td>' +
                '<td>' + fmtDrillPct(s.turnover) + '</td>' +
                '<td>' + fmtDrillNum(s.vol_ratio) + '</td>' +
                '<td>' + fmtDrillRps(s.rps50) + '</td>' +
                '<td style="text-align:left;">' + _escTxt(s.industry || '--') + '</td>' +
                '</tr>';
        });
        return html + '</tbody></table></div>';
    }

    root.fmtDrillRps = fmtDrillRps;
    root.fmtDrillRankChange = fmtDrillRankChange;
    root.fmtDrillStatus = fmtDrillStatus;
    root.fmtDrillPct = fmtDrillPct;
    root.fmtDrillNum = fmtDrillNum;
    root.buildDrillBreadcrumb = buildDrillBreadcrumb;
    root.buildDrillLevel1Html = buildDrillLevel1Html;
    root.buildDrillLevel2Html = buildDrillLevel2Html;
    root.buildDrillLevel3Html = buildDrillLevel3Html;

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
    root.fmtDetailAiAnalysis = fmtDetailAiAnalysis;
    // 每日复盘消息面 + 每周复盘 (2026-08-23): 必须挂 root 供浏览器直接调用
    root.fmtReviewNews = fmtReviewNews;
    root.fmtReviewAccuracy = fmtReviewAccuracy;
    root.fmtReviewWeekAccuracy = fmtReviewWeekAccuracy;
    root.fmtSignedNum = fmtSignedNum;
    root.buildWeeklyReviewHtml = buildWeeklyReviewHtml;
    root.reviewChartWeekSentiment = reviewChartWeekSentiment;
    root.fmtWeekTone = fmtWeekTone;
    root.fmtWeekMainlines = fmtWeekMainlines;
    root.fmtWeekRotation = fmtWeekRotation;
    root.fmtWeekSentiment = fmtWeekSentiment;
    root.fmtWeekMarketBreadth = fmtWeekMarketBreadth;
    root.fmtLastWeekCheck = fmtLastWeekCheck;
    root.fmtWeekWatch = fmtWeekWatch;

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
            fmtReviewAccuracy: fmtReviewAccuracy,
            fmtReviewWeekAccuracy: fmtReviewWeekAccuracy,
            fmtSignedNum: fmtSignedNum,
            fmtDrillRps: fmtDrillRps,
            fmtDrillRankChange: fmtDrillRankChange,
            fmtDrillStatus: fmtDrillStatus,
            fmtDrillPct: fmtDrillPct,
            fmtDrillNum: fmtDrillNum,
            buildDrillBreadcrumb: buildDrillBreadcrumb,
            buildDrillLevel1Html: buildDrillLevel1Html,
            buildDrillLevel2Html: buildDrillLevel2Html,
            buildDrillLevel3Html: buildDrillLevel3Html,
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
            fmtDetailTechnical: fmtDetailTechnical,
            fmtDetailAiAnalysis: fmtDetailAiAnalysis,
            // V2.0 复盘新增模块 (需求 4.1-4.5 / 3.10)
            fmtReviewSectorChips: fmtReviewSectorChips,
            fmtRiskLevelV2: fmtRiskLevelV2,
            fmtAbnormalSignals: fmtAbnormalSignals,
            fmtYesterdayCheck: fmtYesterdayCheck,
            fmtCoreTargets: fmtCoreTargets,
            fmtPositionGuide: fmtPositionGuide,
            fmtMarketBreadth: fmtMarketBreadth,
            fmtStyleOutlook: fmtStyleOutlook,
            reviewChartWeekSentiment: reviewChartWeekSentiment,
            fmtWeekTone: fmtWeekTone,
            fmtWeekMainlines: fmtWeekMainlines,
            fmtWeekRotation: fmtWeekRotation,
            fmtWeekSentiment: fmtWeekSentiment,
            fmtWeekMarketBreadth: fmtWeekMarketBreadth,
            fmtLastWeekCheck: fmtLastWeekCheck,
            fmtWeekWatch: fmtWeekWatch
        };
    }
})(typeof window !== 'undefined' ? window : globalThis);
