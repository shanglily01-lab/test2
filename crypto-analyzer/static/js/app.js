// 主应用JavaScript

// API基础URL
const API_BASE = '';

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
    if (num === null || num === undefined) return '-';
    return parseFloat(num).toLocaleString('zh-CN', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals
    });
}

// 格式化百分比
function formatPercent(num) {
    if (num === null || num === undefined) return '-';
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
            updatePrices(data.prices);
            updateRecommendations(data.recommendations);
            updateNews(data.news);
            updateStats(data);

            // 更新合约数据（如果有的话）
            if (data.futures && data.futures.length > 0) {
                updateFuturesTable(data.futures);
            }

            document.getElementById('last-update').textContent = data.last_updated;
        }
    } catch (error) {
        console.error('加载仪表盘数据失败:', error);
    }
}

// 更新价格表格
function updatePrices(prices) {
    const tbody = document.getElementById('price-table');
    if (!prices || prices.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" class="text-center text-muted">暂无数据</td></tr>';
        return;
    }

    tbody.innerHTML = prices.map(p => {
        const changeClass = p.change_24h >= 0 ? 'price-up' : 'price-down';
        const changeIcon = p.change_24h >= 0 ? '▲' : '▼';

        return `
            <tr onclick="showDetail('${p.symbol}')">
                <td>
                    <strong>${p.symbol}</strong>
                </td>
                <td class="text-end">
                    $${formatNumber(p.price)}
                </td>
                <td class="text-end ${changeClass}">
                    ${changeIcon} ${formatPercent(p.change_24h)}
                </td>
            </tr>
        `;
    }).join('');

    document.getElementById('total-symbols').textContent = prices.length;
}

// 更新合约数据表格
function updateFuturesTable(futuresData) {
    const tbody = document.getElementById('futures-table');
    if (!futuresData || futuresData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" class="text-center text-muted">暂无数据</td></tr>';
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
        if (f.long_short_ratio) {
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
        let fundingRateStr = '';
        let fundingClass = '';
        if (f.funding_rate_pct !== undefined && f.funding_rate_pct !== 0) {
            const rate = f.funding_rate_pct;
            fundingRateStr = `<br><small class="${rate > 0 ? 'text-danger' : 'text-success'}" style="font-size: 0.75rem;">${rate > 0 ? '+' : ''}${rate.toFixed(4)}%</small>`;
        }

        // 获取币种简称（去掉/USDT）
        const symbolName = f.symbol.replace('/USDT', '');

        return `
            <tr>
                <td>
                    <strong>${symbolName}</strong>${fundingRateStr}
                </td>
                <td class="text-end">
                    <small class="text-muted">${openInterestStr}</small>
                </td>
                <td class="text-end ${ratioClass}">
                    ${longShortStr}
                </td>
            </tr>
        `;
    }).join('');
}

// 更新投资建议
function updateRecommendations(recommendations) {
    const container = document.getElementById('recommendations');

    if (!recommendations || recommendations.length === 0) {
        container.innerHTML = '<div class="text-center p-4 text-muted">暂无建议</div>';
        return;
    }

    // 统计信号
    let bullishCount = 0;
    let bearishCount = 0;

    recommendations.forEach(r => {
        if (r.signal === 'STRONG_BUY' || r.signal === 'BUY') {
            bullishCount++;
        } else if (r.signal === 'STRONG_SELL' || r.signal === 'SELL') {
            bearishCount++;
        }
    });

    document.getElementById('bullish-count').textContent = bullishCount;
    document.getElementById('bearish-count').textContent = bearishCount;

    container.innerHTML = recommendations.map(r => {
        const signalClass = `signal-${r.signal}`;

        return `
            <div class="recommendation-item fade-in">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <h6 class="mb-0"><strong>${r.symbol}</strong></h6>
                    <span class="signal-badge ${signalClass}">${translateSignal(r.signal)}</span>
                </div>

                <div class="mb-2">
                    <small class="text-muted">当前价格:</small>
                    <strong class="ms-2">$${formatNumber(r.current_price)}</strong>
                </div>

                <div class="mb-2">
                    <small class="text-muted">置信度:</small>
                    <div class="confidence-bar mt-1">
                        <div class="confidence-fill" style="width: ${r.confidence}%"></div>
                    </div>
                    <small class="text-muted">${r.confidence}%</small>
                </div>

                <div class="mb-2">
                    <small class="text-muted"><i class="bi bi-lightbulb-fill"></i> ${r.advice}</small>
                </div>

                ${r.funding_rate ? `
                    <div class="mb-2 p-2" style="background-color: #f8f9fa; border-radius: 5px;">
                        <small class="text-muted">
                            <i class="bi bi-graph-up"></i> 资金费率:
                            <strong class="${r.funding_rate.funding_rate >= 0 ? 'text-danger' : 'text-success'}">
                                ${r.funding_rate.funding_rate_pct > 0 ? '+' : ''}${r.funding_rate.funding_rate_pct}%
                            </strong>
                            ${r.funding_rate.funding_rate > 0.0005 ? '(多头过热)' :
                              r.funding_rate.funding_rate < -0.0005 ? '(空头过度)' : '(中性)'}
                        </small>
                    </div>
                ` : ''}

                ${r.entry_price > 0 ? `
                    <div class="row g-2 small">
                        <div class="col-4">
                            <div class="indicator-item text-center">
                                <div class="text-muted" style="font-size: 0.75rem;">建仓价</div>
                                <div class="indicator-value">${formatNumber(r.entry_price)}</div>
                            </div>
                        </div>
                        <div class="col-4">
                            <div class="indicator-item text-center">
                                <div class="text-muted" style="font-size: 0.75rem;">止损价</div>
                                <div class="indicator-value text-danger">${formatNumber(r.stop_loss)}</div>
                            </div>
                        </div>
                        <div class="col-4">
                            <div class="indicator-item text-center">
                                <div class="text-muted" style="font-size: 0.75rem;">止盈价</div>
                                <div class="indicator-value text-success">${formatNumber(r.take_profit)}</div>
                            </div>
                        </div>
                    </div>
                ` : ''}

                ${r.reasons && r.reasons.length > 0 ? `
                    <details class="mt-2">
                        <summary class="text-muted small" style="cursor: pointer;">
                            <i class="bi bi-list-ul"></i> 分析依据 (${r.reasons.length}条)
                        </summary>
                        <ul class="reasons-list small mt-2">
                            ${r.reasons.map(reason => `<li>${reason}</li>`).join('')}
                        </ul>
                    </details>
                ` : ''}
            </div>
        `;
    }).join('');
}

// 更新新闻列表
function updateNews(news) {
    const container = document.getElementById('news-list');

    if (!news || news.length === 0) {
        container.innerHTML = '<div class="text-center p-4 text-muted">暂无新闻</div>';
        return;
    }

    document.getElementById('news-count').textContent = news.length;

    container.innerHTML = news.map(n => {
        const sentimentIcon = getSentimentIcon(n.sentiment);
        const sentimentClass = `sentiment-${n.sentiment}`;

        return `
            <div class="news-item fade-in">
                <a href="${n.url}" target="_blank">
                    <div class="d-flex justify-content-between align-items-start mb-1">
                        <h6 class="mb-0">${n.title}</h6>
                    </div>
                    <div class="d-flex justify-content-between align-items-center">
                        <small class="text-muted">
                            <i class="bi bi-building"></i> ${n.source} |
                            <i class="bi bi-clock"></i> ${n.published_at}
                        </small>
                        <span class="${sentimentClass}">
                            ${sentimentIcon} ${translateSentiment(n.sentiment)}
                        </span>
                    </div>
                    ${n.symbols ? `
                        <div class="mt-1">
                            <small class="text-muted">
                                <i class="bi bi-tags"></i>
                                ${n.symbols.split(',').map(s => `<span class="badge bg-secondary me-1">${s}</span>`).join('')}
                            </small>
                        </div>
                    ` : ''}
                </a>
            </div>
        `;
    }).join('');
}

// 更新统计数据
function updateStats(data) {
    // 已在其他函数中更新
}

// 翻译信号
function translateSignal(signal) {
    const translations = {
        'STRONG_BUY': '强烈买入',
        'BUY': '买入',
        'HOLD': '持有',
        'SELL': '卖出',
        'STRONG_SELL': '强烈卖出'
    };
    return translations[signal] || signal;
}

// 翻译情绪
function translateSentiment(sentiment) {
    const translations = {
        'positive': '利好',
        'negative': '利空',
        'neutral': '中性'
    };
    return translations[sentiment] || sentiment;
}

// 获取情绪图标
function getSentimentIcon(sentiment) {
    const icons = {
        'positive': '📈',
        'negative': '📉',
        'neutral': '➖'
    };
    return icons[sentiment] || '';
}

// 显示详情
function showDetail(symbol) {
    alert(`点击查看 ${symbol} 详细分析 (功能开发中...)`);
}

// 初始化
document.addEventListener('DOMContentLoaded', function() {
    // 更新时间
    updateTime();
    setInterval(updateTime, 1000);

    // 加载数据（dashboard已包含合约数据）
    loadDashboard();

    // 定期刷新 (每30秒)
    setInterval(loadDashboard, 30000);
});
