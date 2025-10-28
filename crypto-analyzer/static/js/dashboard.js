// 增强版Dashboard JavaScript

const API_BASE = '';
let chartInstances = {}; // 存储图表实例
let previousPrices = {}; // 存储上一次的价格，用于检测变化

// 更新当前时间
function updateTime() {
    const now = new Date();
    const timeStr = now.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
    document.getElementById('current-time').textContent = timeStr;
}

// 格式化数字
function formatNumber(num, decimals = 2) {
    if (num === null || num === undefined || isNaN(num)) return '-';
    return parseFloat(num).toLocaleString('zh-CN', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals
    });
}

// 智能格式化价格（根据价格大小自动调整小数位数）
function formatPrice(price) {
    if (price === null || price === undefined || isNaN(price)) return '-';

    const num = parseFloat(price);

    // 价格 >= 1000: 2位小数 (如 BTC: 95,234.50)
    if (num >= 1000) {
        return num.toLocaleString('zh-CN', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
    }
    // 价格 >= 1: 4位小数 (如 ETH: 3,512.8000)
    else if (num >= 1) {
        return num.toLocaleString('zh-CN', {
            minimumFractionDigits: 4,
            maximumFractionDigits: 4
        });
    }
    // 价格 < 1: 6位小数 (如 DOGE: 0.123456)
    else {
        return num.toLocaleString('zh-CN', {
            minimumFractionDigits: 6,
            maximumFractionDigits: 6
        });
    }
}

// 格式化大数字 (K, M, B)
function formatLargeNumber(num) {
    if (!num) return '-';
    const absNum = Math.abs(num);
    if (absNum >= 1000000000) return (absNum / 1000000000).toFixed(2) + 'B';
    if (absNum >= 1000000) return (absNum / 1000000).toFixed(2) + 'M';
    if (absNum >= 1000) return (absNum / 1000).toFixed(2) + 'K';
    return absNum.toFixed(2);
}

// 格式化百分比
function formatPercent(num) {
    if (num === null || num === undefined || isNaN(num)) return '-';
    const sign = num >= 0 ? '+' : '';
    return sign + num.toFixed(2) + '%';
}

// 获取仪表盘数据
async function loadDashboard() {
    try {
        const response = await fetch(`${API_BASE}/api/dashboard`);
        const result = await response.json();

        if (result.success) {
            const data = result.data;

            updatePrices(data.prices || []);
            updateFuturesTable(data.futures || []);
            updateRecommendations(data.recommendations || []);
            updateNews(data.news || []);
            updateHyperliquid(data.hyperliquid || {});
            updateStats(data.stats || {}, data.hyperliquid || {});
            document.getElementById('last-update').textContent = data.last_updated || '-';
        }
    } catch (error) {
        console.error('加载仪表盘数据失败:', error);
        showError('加载数据失败，请检查后端服务是否运行');
    }
}

// 更新统计卡片
function updateStats(stats, hyperliquid) {
    document.getElementById('total-symbols').textContent = stats.total_symbols || 0;
    document.getElementById('bullish-count').textContent = stats.bullish_count || 0;
    document.getElementById('bearish-count').textContent = stats.bearish_count || 0;
    document.getElementById('smart-money-count').textContent = hyperliquid.monitored_wallets || 0;
}

// 更新价格表格
function updatePrices(prices) {
    const tbody = document.getElementById('price-table');

    if (!prices || prices.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted p-4">暂无数据</td></tr>';
        return;
    }

    tbody.innerHTML = prices.map(p => {
        const changeClass = p.change_24h >= 0 ? 'price-up' : 'price-down';
        const changeIcon = p.change_24h >= 0 ? '▲' : '▼';

        return `
            <tr>
                <td><strong>${p.symbol || p.full_symbol}</strong></td>
                <td class="text-end">$${formatPrice(p.price)}</td>
                <td class="text-end ${changeClass}">
                    ${changeIcon} ${formatPercent(p.change_24h)}
                </td>
                <td class="text-end text-muted">
                    ${formatLargeNumber(p.volume_24h)}
                </td>
            </tr>
        `;
    }).join('');
}

// 更新合约数据表格
function updateFuturesTable(futuresData) {
    const tbody = document.getElementById('futures-table');

    if (!futuresData || futuresData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted p-4">暂无数据</td></tr>';
        return;
    }

    tbody.innerHTML = futuresData.map(f => {
        // 处理持仓量 - 转换为万或亿单位
        let openInterestStr = '-';
        if (f.open_interest) {
            const oi = f.open_interest;
            if (oi >= 100000000) {
                openInterestStr = (oi / 100000000).toFixed(2) + '亿';
            } else if (oi >= 10000) {
                openInterestStr = (oi / 10000).toFixed(2) + '万';
            } else {
                openInterestStr = oi.toFixed(2);
            }
        }

        // 处理多空比 - 显示比率并着色
        let longShortStr = '-';
        let ratioClass = '';
        if (f.long_short_ratio !== undefined && f.long_short_ratio !== 0) {
            const ratio = f.long_short_ratio;
            longShortStr = ratio.toFixed(2);

            // 根据多空比着色：>1偏多(绿色)，<1偏空(红色)
            if (ratio > 1.2) {
                ratioClass = 'text-success fw-bold';  // 明显偏多
            } else if (ratio > 1.0) {
                ratioClass = 'text-success';  // 轻微偏多
            } else if (ratio < 0.8) {
                ratioClass = 'text-danger fw-bold';  // 明显偏空
            } else if (ratio < 1.0) {
                ratioClass = 'text-danger';  // 轻微偏空
            } else {
                ratioClass = 'text-muted';  // 平衡
            }
        }

        // 处理资金费率 - 显示并着色
        let fundingRateStr = '-';
        let fundingClass = '';
        if (f.funding_rate_pct !== undefined && f.funding_rate_pct !== 0) {
            const rate = f.funding_rate_pct;
            fundingRateStr = `${rate > 0 ? '+' : ''}${rate.toFixed(4)}%`;
            fundingClass = rate > 0 ? 'text-danger' : 'text-success';
        }

        const symbolName = f.symbol || f.full_symbol;

        return `
            <tr>
                <td><strong>${symbolName}</strong></td>
                <td class="text-end">
                    <small class="text-muted">${openInterestStr}</small>
                </td>
                <td class="text-end ${ratioClass}">
                    ${longShortStr}
                </td>
                <td class="text-end ${fundingClass}">
                    <small>${fundingRateStr}</small>
                </td>
            </tr>
        `;
    }).join('');
}

// 更新投资建议
function updateRecommendations(recommendations) {
    const container = document.getElementById('recommendations');

    if (!recommendations || recommendations.length === 0) {
        container.innerHTML = '<div class="text-center p-5 text-muted">暂无投资建议</div>';
        return;
    }

    container.innerHTML = recommendations.map((r, index) => {
        const scores = r.scores || {};
        const canvasId = `radar-${index}`;

        return `
            <div class="recommendation-card">
                <!-- 头部: 币种和信号 -->
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h5 class="mb-0">
                        <i class="bi bi-currency-bitcoin"></i>
                        <strong>${r.symbol || r.full_symbol}</strong>
                    </h5>
                    <span class="signal-badge signal-${r.signal}">
                        ${translateSignal(r.signal)}
                    </span>
                </div>

                <!-- 价格信息 -->
                <div class="row g-3 mb-3">
                    <div class="col-3">
                        <div class="text-muted small">当前价 <span class="badge bg-success" style="font-size: 0.6em;">实时</span></div>
                        <div class="fw-bold price-value" data-symbol="${r.symbol}">${r.current_price ? '$' + formatPrice(r.current_price) : '-'}</div>
                    </div>
                    <div class="col-3">
                        <div class="text-muted small">建仓价</div>
                        <div class="fw-bold text-primary">$${formatPrice(r.entry_price)}</div>
                    </div>
                    <div class="col-3">
                        <div class="text-muted small">止损价</div>
                        <div class="fw-bold text-danger">$${formatPrice(r.stop_loss)}</div>
                    </div>
                    <div class="col-3">
                        <div class="text-muted small">止盈价</div>
                        <div class="fw-bold text-success">$${formatPrice(r.take_profit)}</div>
                    </div>
                </div>

                <!-- 置信度 -->
                <div class="mb-3">
                    <div class="d-flex justify-content-between align-items-center mb-2">
                        <span class="text-muted small">置信度</span>
                        <span class="fw-bold">${r.confidence}%</span>
                    </div>
                    <div class="custom-progress">
                        <div class="custom-progress-bar bg-primary"
                             style="width: ${r.confidence}%; background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);"></div>
                    </div>
                </div>

                <!-- 5维度评分 -->
                <div class="row mb-3">
                    <div class="col-md-6">
                        <div class="mb-2">
                            <div class="d-flex justify-content-between">
                                <span class="small" data-bs-toggle="tooltip" data-bs-placement="top"
                                      title="基于RSI、MACD、布林带、EMA等技术指标的综合分析">
                                    📊 技术指标 <i class="bi bi-info-circle text-muted" style="font-size: 0.8em;"></i>
                                </span>
                                <span class="small fw-bold">${scores.technical}/100</span>
                            </div>
                            <div class="custom-progress">
                                <div class="custom-progress-bar ${getScoreColor(scores.technical)}"
                                     style="width: ${scores.technical}%"></div>
                            </div>
                        </div>
                        <div class="mb-2">
                            <div class="d-flex justify-content-between">
                                <span class="small" data-bs-toggle="tooltip" data-bs-placement="top"
                                      title="基于新闻、社交媒体、监管公告的情绪分析，反映市场舆论">
                                    📰 新闻情绪 <i class="bi bi-info-circle text-muted" style="font-size: 0.8em;"></i>
                                </span>
                                <span class="small fw-bold">${scores.news}/100</span>
                            </div>
                            <div class="custom-progress">
                                <div class="custom-progress-bar ${getScoreColor(scores.news)}"
                                     style="width: ${scores.news}%"></div>
                            </div>
                        </div>
                        <div class="mb-2">
                            <div class="d-flex justify-content-between">
                                <span class="small" data-bs-toggle="tooltip" data-bs-placement="top"
                                      title="永续合约的资金费率，正值表示多头占优，负值表示空头占优">
                                    💰 资金费率 <i class="bi bi-info-circle text-muted" style="font-size: 0.8em;"></i>
                                </span>
                                <span class="small fw-bold">${scores.funding}/100</span>
                            </div>
                            <div class="custom-progress">
                                <div class="custom-progress-bar ${getScoreColor(scores.funding)}"
                                     style="width: ${scores.funding}%"></div>
                            </div>
                        </div>
                    </div>
                    <div class="col-md-6">
                        <div class="mb-2">
                            <div class="d-flex justify-content-between">
                                <span class="small" data-bs-toggle="tooltip" data-bs-placement="top"
                                      title="追踪Hyperliquid上盈利能力强的聪明钱交易者的操作方向">
                                    🧠 Hyperliquid <i class="bi bi-info-circle text-muted" style="font-size: 0.8em;"></i>
                                </span>
                                <span class="small fw-bold">${scores.hyperliquid}/100</span>
                            </div>
                            <div class="custom-progress">
                                <div class="custom-progress-bar ${getScoreColor(scores.hyperliquid)}"
                                     style="width: ${scores.hyperliquid}%"></div>
                            </div>
                        </div>
                        <div class="mb-2">
                            <div class="d-flex justify-content-between">
                                <span class="small" data-bs-toggle="tooltip" data-bs-placement="top"
                                      title="监控链上大额转账、活跃地址数、交易量等区块链数据">
                                    ⛓️ 链上数据 <i class="bi bi-info-circle text-muted" style="font-size: 0.8em;"></i>
                                </span>
                                <span class="small fw-bold">${scores.ethereum}/100</span>
                            </div>
                            <div class="custom-progress">
                                <div class="custom-progress-bar ${getScoreColor(scores.ethereum)}"
                                     style="width: ${scores.ethereum}%"></div>
                            </div>
                        </div>
                        <div class="mb-2">
                            <div class="d-flex justify-content-between">
                                <span class="small fw-bold">📈 综合评分</span>
                                <span class="small fw-bold text-primary">${scores.total}/100</span>
                            </div>
                            <div class="custom-progress">
                                <div class="custom-progress-bar bg-primary"
                                     style="width: ${scores.total}%; background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);"></div>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 资金费率信息 -->
                ${r.funding_rate ? `
                    <div class="alert alert-info py-2 mb-3">
                        <small>
                            <i class="bi bi-graph-up"></i>
                            <strong>资金费率:</strong>
                            <span class="${r.funding_rate.funding_rate >= 0 ? 'text-danger' : 'text-success'}">
                                ${r.funding_rate.funding_rate_pct > 0 ? '+' : ''}${r.funding_rate.funding_rate_pct.toFixed(3)}%
                            </span>
                            ${r.funding_rate.funding_rate > 0.0005 ? '(多头过热 ⚠️)' :
                              r.funding_rate.funding_rate < -0.0005 ? '(空头过度 💪)' : '(中性)'}
                        </small>
                    </div>
                ` : ''}

                <!-- 分析依据 -->
                ${r.reasons && r.reasons.length > 0 ? `
                    <details class="mb-2">
                        <summary class="small">
                            <i class="bi bi-list-ul"></i>
                            分析依据 (${r.reasons.length}条)
                        </summary>
                        <ul class="mt-2 mb-0 small">
                            ${r.reasons.map(reason => `<li>${reason}</li>`).join('')}
                        </ul>
                    </details>
                ` : ''}

                <!-- 风险提示 -->
                <div class="alert alert-warning py-2 mb-0">
                    <small>
                        <strong>⚠️ 风险等级: ${r.risk_level}</strong>
                        ${r.risk_factors && r.risk_factors.length > 0 ?
                            `<br>${r.risk_factors.slice(0, 2).join('<br>')}` : ''}
                    </small>
                </div>
            </div>
        `;
    }).join('');

    // 重新初始化 tooltips
    setTimeout(initTooltips, 100);

    // 检测价格变化并添加动画
    setTimeout(() => {
        recommendations.forEach(r => {
            const symbol = r.symbol || r.full_symbol;
            const currentPrice = r.current_price;

            // 查找该交易对的价格元素
            const priceElements = document.querySelectorAll(`.price-value[data-symbol="${symbol}"]`);

            priceElements.forEach(el => {
                // 如果价格发生变化，添加闪烁动画
                if (previousPrices[symbol] && previousPrices[symbol] !== currentPrice) {
                    el.classList.remove('price-updated');
                    // 强制重绘
                    void el.offsetWidth;
                    el.classList.add('price-updated');
                    console.log(`✓ ${symbol} 价格更新: ${previousPrices[symbol]} → ${currentPrice}`);
                }

                // 更新存储的价格
                previousPrices[symbol] = currentPrice;
            });
        });
    }, 150);
}

// 获取评分颜色
function getScoreColor(score) {
    if (score >= 70) return 'bg-success';
    if (score >= 50) return 'bg-info';
    if (score >= 30) return 'bg-warning';
    return 'bg-danger';
}

// 更新新闻列表
function updateNews(news) {
    const container = document.getElementById('news-list');

    if (!news || news.length === 0) {
        container.innerHTML = '<div class="text-center p-4 text-muted">暂无新闻</div>';
        return;
    }

    container.innerHTML = news.slice(0, 10).map(n => {
        const sentimentClass = `sentiment-${n.sentiment}`;

        return `
            <div class="news-card">
                <a href="${n.url}" target="_blank" class="text-decoration-none text-dark">
                    <div class="d-flex justify-content-between align-items-start mb-2">
                        <h6 class="mb-0 flex-grow-1">${n.title}</h6>
                        <span class="sentiment-badge ${sentimentClass} ms-2">
                            ${translateSentiment(n.sentiment)}
                        </span>
                    </div>
                    <div class="d-flex justify-content-between align-items-center">
                        <small class="text-muted">
                            <i class="bi bi-building"></i> ${n.source} |
                            <i class="bi bi-clock"></i> ${n.published_at}
                        </small>
                        ${n.symbols ? `
                            <div>
                                ${n.symbols.split(',').slice(0, 3).map(s =>
                                    `<span class="badge bg-secondary">${s.trim()}</span>`
                                ).join(' ')}
                            </div>
                        ` : ''}
                    </div>
                </a>
            </div>
        `;
    }).join('');
}

// 更新Hyperliquid聪明钱
function updateHyperliquid(data) {
    const container = document.getElementById('hyperliquid-section');

    // 调试：输出接收到的数据
    console.log('=== Hyperliquid 数据调试 ===');
    console.log('data:', data);
    console.log('monitored_wallets:', data ? data.monitored_wallets : 'undefined');
    console.log('recent_trades length:', data && data.recent_trades ? data.recent_trades.length : 0);
    console.log('top_coins length:', data && data.top_coins ? data.top_coins.length : 0);

    // 检查数据是否为空
    if (!data || (data.monitored_wallets === 0 && (!data.recent_trades || data.recent_trades.length === 0))) {
        container.innerHTML = `
            <div class="text-center p-5">
                <i class="bi bi-wallet2" style="font-size: 3rem; opacity: 0.3;"></i>
                <p class="text-muted mt-3">暂无Hyperliquid聪明钱数据</p>
                <small class="text-muted">
                    ${!data || data.monitored_wallets === 0 ?
                        '系统尚未配置监控钱包，或钱包数据正在采集中...' :
                        '最近24小时没有交易活动'}
                </small>
            </div>
        `;
        return;
    }

    // 调试: 输出PnL原始值
    if (data.recent_trades && data.recent_trades.length > 0) {
        console.log('=== Hyperliquid PnL 数据调试 ===');
        data.recent_trades.slice(0, 5).forEach((trade, idx) => {
            console.log(`交易 ${idx + 1}: ${trade.coin} ${trade.side}`);
            console.log(`  原始 closed_pnl: ${trade.closed_pnl}`);
            console.log(`  格式化后: ${formatLargeNumber(Math.abs(trade.closed_pnl))}`);
        });
    }

    const html = `
        <!-- 统计概览 -->
        <div class="row g-3 mb-4">
            <div class="col-md-4">
                <div class="smart-money-card">
                    <div class="smart-money-stat">
                        <div class="smart-money-value">${data.monitored_wallets}</div>
                        <div class="smart-money-label">监控钱包</div>
                    </div>
                </div>
            </div>
            <div class="col-md-4">
                <div class="smart-money-card">
                    <div class="smart-money-stat">
                        <div class="smart-money-value">$${formatLargeNumber(data.total_volume_24h)}</div>
                        <div class="smart-money-label">24h交易量</div>
                    </div>
                </div>
            </div>
            <div class="col-md-4">
                <div class="smart-money-card">
                    <div class="smart-money-stat">
                        <div class="smart-money-value">${(data.recent_trades || []).length}</div>
                        <div class="smart-money-label">最近交易</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Top活跃币种 -->
        ${data.top_coins && data.top_coins.length > 0 ? `
            <div class="mb-4">
                <h6 class="mb-3"><i class="bi bi-trophy"></i> Top活跃币种</h6>
                <div class="row g-2">
                    ${data.top_coins.map(coin => `
                        <div class="col-md-2 col-4">
                            <div class="text-center p-2 rounded ${coin.direction === 'bullish' ? 'bg-success-subtle' : 'bg-danger-subtle'}">
                                <div class="fw-bold">${coin.coin}</div>
                                <small class="${coin.direction === 'bullish' ? 'text-success' : 'text-danger'}">
                                    ${coin.direction === 'bullish' ? '▲' : '▼'}
                                    $${formatLargeNumber(Math.abs(coin.net_flow))}
                                </small>
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        ` : ''}

        <!-- 最近交易 -->
        ${data.recent_trades && data.recent_trades.length > 0 ? `
            <div>
                <h6 class="mb-3">
                    <i class="bi bi-clock-history"></i>
                    最近大额交易 (Top ${Math.min(50, data.recent_trades.length)})
                </h6>
                <div class="row">
                    ${data.recent_trades.slice(0, 50).map(trade => `
                        <div class="col-md-6">
                            <div class="trade-item trade-${trade.side.toLowerCase()}">
                                <div class="d-flex justify-content-between align-items-center">
                                    <div>
                                        <strong>${trade.coin}</strong>
                                        <span class="badge ${trade.side === 'LONG' ? 'bg-success' : 'bg-danger'} ms-2">
                                            ${trade.side}
                                        </span>
                                    </div>
                                    <div class="text-end">
                                        <div class="fw-bold">$${formatLargeNumber(trade.notional_usd)}</div>
                                        <small class="text-muted">${trade.wallet_label}</small>
                                    </div>
                                </div>
                                <div class="d-flex justify-content-between mt-1">
                                    <small class="text-muted">${trade.trade_time}</small>
                                    ${trade.closed_pnl && trade.closed_pnl !== 0 ? `
                                        <small class="${trade.closed_pnl > 0 ? 'text-success' : 'text-danger'}">
                                            PnL: $${formatLargeNumber(Math.abs(trade.closed_pnl))}${trade.closed_pnl > 0 ? '' : ' (亏损)'}
                                        </small>
                                    ` : ''}
                                </div>
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        ` : ''}
    `;

    container.innerHTML = html;
}

// 翻译信号
function translateSignal(signal) {
    const translations = {
        'STRONG_BUY': '强烈买入 🚀',
        'BUY': '买入 📈',
        'HOLD': '持有 ➡️',
        'SELL': '卖出 📉',
        'STRONG_SELL': '强烈卖出 💥'
    };
    return translations[signal] || signal;
}

// 翻译情绪
function translateSentiment(sentiment) {
    const translations = {
        'positive': '利好 📈',
        'negative': '利空 📉',
        'neutral': '中性 ➖'
    };
    return translations[sentiment] || sentiment;
}

// 显示错误
function showError(message) {
    console.error(message);
    // 可以添加Toast通知
}

// 初始化 Bootstrap tooltips
function initTooltips() {
    const tooltipTriggerList = document.querySelectorAll('[data-bs-toggle="tooltip"]');
    tooltipTriggerList.forEach(el => {
        new bootstrap.Tooltip(el);
    });
}

// 加载企业金库汇总数据
async function loadCorporateTreasury() {
    try {
        const response = await fetch(`${API_BASE}/api/corporate-treasury/summary`);
        const result = await response.json();

        if (result.success) {
            updateCorporateTreasury(result.data);
        }
    } catch (error) {
        console.error('加载企业金库数据失败:', error);
        const section = document.getElementById('corporate-treasury-section');
        if (section) {
            section.innerHTML = '<div class="text-center p-3 text-muted">加载失败</div>';
        }
    }
}

// 更新企业金库显示
function updateCorporateTreasury(data) {
    const section = document.getElementById('corporate-treasury-section');
    if (!section) return;

    const summary = data.summary || {};
    const topHolders = data.top_holders || [];

    let html = `
        <!-- 汇总统计 -->
        <div class="row g-3 mb-3">
            <div class="col-md-2">
                <div class="text-center p-3" style="background: rgba(102, 126, 234, 0.1); border-radius: 10px;">
                    <div class="small text-muted mb-1">监控公司</div>
                    <div class="h4 mb-0 fw-bold text-primary">${summary.total_companies || 0}</div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="text-center p-3" style="background: rgba(255, 215, 0, 0.1); border-radius: 10px;">
                    <div class="small text-muted mb-1">BTC 总持仓</div>
                    <div class="h4 mb-0 fw-bold" style="color: #f5a623;">${formatNumber(summary.total_btc_holdings, 2)} BTC</div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="text-center p-3" style="background: rgba(56, 239, 125, 0.1); border-radius: 10px;">
                    <div class="small text-muted mb-1">总市值 (USD)</div>
                    <div class="h4 mb-0 fw-bold text-success">$${formatLargeNumber(summary.total_value_usd)}</div>
                </div>
            </div>
            <div class="col-md-2">
                <div class="text-center p-3" style="background: rgba(0, 242, 254, 0.1); border-radius: 10px;">
                    <div class="small text-muted mb-1">BTC 价格</div>
                    <div class="h5 mb-0 fw-bold text-info">$${formatNumber(summary.current_btc_price, 0)}</div>
                </div>
            </div>
            <div class="col-md-2">
                <div class="text-center p-3" style="background: rgba(255, 106, 0, 0.1); border-radius: 10px;">
                    <div class="small text-muted mb-1">30天活跃</div>
                    <div class="h4 mb-0 fw-bold" style="color: #ff6a00;">${summary.active_companies_30d || 0}</div>
                </div>
            </div>
        </div>

        <!-- Top 5 持仓公司 -->
        <h6 class="mb-3"><i class="bi bi-trophy-fill text-warning"></i> Top 5 BTC 持仓公司</h6>
        <div class="table-responsive">
            <table class="table table-hover mb-0">
                <thead class="table-light">
                    <tr>
                        <th style="width: 60px;">排名</th>
                        <th>公司名称</th>
                        <th>代码</th>
                        <th class="text-end">BTC 持仓</th>
                        <th class="text-end">市值 (USD)</th>
                        <th class="text-center">最近更新</th>
                    </tr>
                </thead>
                <tbody>
    `;

    if (topHolders.length === 0) {
        html += '<tr><td colspan="6" class="text-center text-muted p-3">暂无数据</td></tr>';
    } else {
        topHolders.slice(0, 5).forEach((holder, index) => {
            const rank = index + 1;
            let rankBadge = '';
            if (rank === 1) rankBadge = '<span class="badge" style="background: linear-gradient(135deg, #ffd700, #ffed4e); color: #000;">🥇 1</span>';
            else if (rank === 2) rankBadge = '<span class="badge" style="background: linear-gradient(135deg, #c0c0c0, #e8e8e8); color: #000;">🥈 2</span>';
            else if (rank === 3) rankBadge = '<span class="badge" style="background: linear-gradient(135deg, #cd7f32, #daa520); color: #fff;">🥉 3</span>';
            else rankBadge = `<span class="badge bg-secondary">${rank}</span>`;

            html += `
                <tr>
                    <td>${rankBadge}</td>
                    <td><strong>${holder.company_name}</strong></td>
                    <td><span class="badge bg-primary">${holder.ticker_symbol || 'N/A'}</span></td>
                    <td class="text-end"><strong>${formatNumber(holder.btc_holdings, 2)}</strong> BTC</td>
                    <td class="text-end text-success fw-bold">$${formatLargeNumber(holder.value_usd)}</td>
                    <td class="text-center"><small class="text-muted">${holder.last_update || '-'}</small></td>
                </tr>
            `;
        });
    }

    html += `
                </tbody>
            </table>
        </div>
    `;

    section.innerHTML = html;
}

// 初始化
document.addEventListener('DOMContentLoaded', function() {
    // 初始化 Bootstrap tooltips
    initTooltips();

    // 更新时间
    updateTime();
    setInterval(updateTime, 1000);

    // 加载数据
    loadDashboard();
    loadCorporateTreasury();

    // 定期刷新 (每5秒主数据，每30秒企业金库数据)
    setInterval(loadDashboard, 5000);
    setInterval(loadCorporateTreasury, 30000);

    console.log('增强版Dashboard已初始化');
});
