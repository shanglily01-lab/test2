/**
 * 统一的时间格式化工具
 *
 * 规则：
 * - 后端和数据库：UTC+0（标准时间）
 * - 前端显示：自动转换为 UTC+8（北京时间/中国时区）
 * - 显示格式：明确标注 UTC+8
 */

/**
 * 将UTC时间转换为UTC+8并格式化
 * @param {string|Date} utcTime - UTC时间字符串或Date对象
 * @param {string} format - 格式类型: 'full'（完整）, 'datetime'（日期+时间）, 'date'（仅日期）, 'time'（仅时间）, 'relative'（相对时间）
 * @param {boolean} showTimezone - 是否显示时区标识（默认true）
 * @returns {string} 格式化后的时间字符串
 */
function formatTimeUTC8(utcTime, format = 'datetime', showTimezone = true) {
    if (!utcTime) return '-';

    try {
        // 统一转换为Date对象
        let date;
        if (utcTime instanceof Date) {
            date = utcTime;
        } else if (typeof utcTime === 'string') {
            // 检查是否有时区标识（Z或±HH:MM）
            // 注意：不能用 includes('-') 判断，因为日期格式本身包含 '-'
            const hasTimezone = utcTime.endsWith('Z') ||
                               /[+-]\d{2}:\d{2}$/.test(utcTime) ||
                               /[+-]\d{4}$/.test(utcTime);

            if (!hasTimezone) {
                // 没有时区信息，假设是UTC时间，添加Z后缀
                date = new Date(utcTime + 'Z');
            } else {
                date = new Date(utcTime);
            }
        } else {
            return '-';
        }

        // 检查日期是否有效
        if (isNaN(date.getTime())) {
            return '-';
        }

        // 转换为UTC+8（北京时间）
        const utc8Date = new Date(date.getTime() + 8 * 60 * 60 * 1000);

        const year = utc8Date.getUTCFullYear();
        const month = String(utc8Date.getUTCMonth() + 1).padStart(2, '0');
        const day = String(utc8Date.getUTCDate()).padStart(2, '0');
        const hours = String(utc8Date.getUTCHours()).padStart(2, '0');
        const minutes = String(utc8Date.getUTCMinutes()).padStart(2, '0');
        const seconds = String(utc8Date.getUTCSeconds()).padStart(2, '0');

        const tz = showTimezone ? ' (UTC+8)' : '';

        switch (format) {
            case 'full':
                // 完整格式: 2026-01-23 15:30:45 (UTC+8)
                return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}${tz}`;

            case 'datetime':
                // 日期+时间: 01-23 15:30 (UTC+8)
                return `${month}-${day} ${hours}:${minutes}${tz}`;

            case 'date':
                // 仅日期: 2026-01-23
                return `${year}-${month}-${day}`;

            case 'time':
                // 仅时间: 15:30:45
                return `${hours}:${minutes}:${seconds}`;

            case 'relative':
                // 相对时间: 5分钟前, 2小时前
                return formatRelativeTime(date, showTimezone);

            default:
                return `${month}-${day} ${hours}:${minutes}${tz}`;
        }
    } catch (e) {
        console.error('时间格式化失败:', e, utcTime);
        return '-';
    }
}

/**
 * 格式化相对时间（例如：5分钟前）
 * @param {Date} date - 时间对象
 * @param {boolean} showTimezone - 是否显示时区标识
 * @returns {string} 相对时间字符串
 */
function formatRelativeTime(date, showTimezone = true) {
    const now = new Date();
    const diffMs = now - date;
    const diffMinutes = Math.floor(diffMs / 1000 / 60);
    const diffHours = Math.floor(diffMinutes / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffMinutes < 1) {
        return '刚刚';
    } else if (diffMinutes < 60) {
        return `${diffMinutes}分钟前`;
    } else if (diffHours < 24) {
        return `${diffHours}小时前`;
    } else if (diffDays < 7) {
        return `${diffDays}天前`;
    } else {
        // 超过7天，显示具体日期
        return formatTimeUTC8(date, 'datetime', showTimezone);
    }
}

/**
 * 格式化时间范围
 * @param {string|Date} startTime - 开始时间
 * @param {string|Date} endTime - 结束时间
 * @returns {string} 格式化后的时间范围
 */
function formatTimeRange(startTime, endTime) {
    const start = formatTimeUTC8(startTime, 'datetime', false);
    const end = formatTimeUTC8(endTime, 'datetime', false);
    return `${start} ~ ${end} (UTC+8)`;
}

/**
 * 格式化持仓时长
 * @param {string|Date} openTime - 开仓时间
 * @param {string|Date} closeTime - 平仓时间（可选，默认为当前时间）
 * @returns {string} 持仓时长字符串（例如：2小时15分钟）
 */
function formatDuration(openTime, closeTime = null) {
    if (!openTime) return '-';

    try {
        const start = new Date(openTime);
        const end = closeTime ? new Date(closeTime) : new Date();

        const diffMs = end - start;
        const diffMinutes = Math.floor(diffMs / 1000 / 60);
        const diffHours = Math.floor(diffMinutes / 60);
        const diffDays = Math.floor(diffHours / 24);

        const remainingMinutes = diffMinutes % 60;
        const remainingHours = diffHours % 24;

        if (diffDays > 0) {
            return `${diffDays}天${remainingHours}小时`;
        } else if (diffHours > 0) {
            return `${diffHours}小时${remainingMinutes}分钟`;
        } else {
            return `${diffMinutes}分钟`;
        }
    } catch (e) {
        console.error('时长格式化失败:', e, openTime, closeTime);
        return '-';
    }
}

/**
 * 将UTC+8时间转换为UTC时间（用于提交到后端）
 * @param {string|Date} utc8Time - UTC+8时间
 * @returns {string} ISO格式的UTC时间字符串
 */
function convertToUTC(utc8Time) {
    if (!utc8Time) return null;

    try {
        const date = new Date(utc8Time);
        // 减去8小时
        const utcDate = new Date(date.getTime() - 8 * 60 * 60 * 1000);
        return utcDate.toISOString();
    } catch (e) {
        console.error('时间转换失败:', e, utc8Time);
        return null;
    }
}

// 兼容旧代码的函数名（逐步迁移）
function formatTime(time) {
    return formatTimeUTC8(time, 'datetime', true);
}

function formatDate(time) {
    return formatTimeUTC8(time, 'date', false);
}

// 导出函数（用于ES6模块）
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        formatTimeUTC8,
        formatRelativeTime,
        formatTimeRange,
        formatDuration,
        convertToUTC,
        formatTime,
        formatDate
    };
}
