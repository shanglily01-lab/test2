"""
Big4市场状态监控器（仅记录告警，不修改系统设置）

检测Big4过去48小时趋势信号分布，仅输出日志和Telegram通知，
不再自动覆写 allow_long/allow_short 开关。

状态判定逻辑（仅用于日志/通知参考）：
- BULL状态    （多头信号占比 >=70%）-> 建议 allow_short=0
- BEAR状态    （空头信号占比 >=70%）-> 建议 allow_long=0
- SIDEWAYS状态（多空各占一席）       -> 建议 allow_long=1, allow_short=1
"""

import pymysql
from datetime import datetime
from loguru import logger
from typing import Dict, Optional


class Big4RegimeMonitor:
    """
    Big4市场状态监控器（仅记录告警，不修改系统设置）

    基于Big4过去48小时趋势信号的分布，检测市场状态并输出日志。
    不再自动覆写 allow_long/allow_short 开关。
    建议每小时运行一次（由调用方决定频率）。
    """

    REGIME_BULL = 'BULL'
    REGIME_BEAR = 'BEAR'
    REGIME_SIDEWAYS = 'SIDEWAYS'

    # 判定趋势市的单方向占比阈值
    TREND_THRESHOLD_PCT = 70.0

    def __init__(self, db_config: dict):
        self.db_config = db_config
        self.current_regime: Optional[str] = None
        self.last_check_time: Optional[datetime] = None

    def _get_connection(self):
        return pymysql.connect(
            host=self.db_config.get('host', 'localhost'),
            port=self.db_config.get('port', 3306),
            user=self.db_config.get('user', 'root'),
            password=self.db_config.get('password', ''),
            database=self.db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10,
        )

    def detect_regime(self, hours: int = 48) -> Dict:
        """
        检测当前市场状态（基于big4_trend_history过去N小时的信号分布）

        Returns:
            {
                'regime': 'BULL' | 'BEAR' | 'SIDEWAYS',
                'bullish_pct': float,
                'bearish_pct': float,
                'total_records': int,
                'reason': str
            }
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT overall_signal, COUNT(*) AS cnt
                FROM big4_trend_history
                WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s HOUR)
                  AND overall_signal IN ('BULLISH', 'BEARISH', 'STRONG_BULLISH', 'STRONG_BEARISH')
                GROUP BY overall_signal
            """, (hours,))
            rows = cursor.fetchall()
            cursor.close()

            bullish_count = sum(r['cnt'] for r in rows if r['overall_signal'] in ('BULLISH', 'STRONG_BULLISH'))
            bearish_count = sum(r['cnt'] for r in rows if r['overall_signal'] in ('BEARISH', 'STRONG_BEARISH'))
            total = bullish_count + bearish_count

            if total < 20:
                return {
                    'regime': self.REGIME_SIDEWAYS,
                    'bullish_pct': 0.0,
                    'bearish_pct': 0.0,
                    'total_records': total,
                    'reason': f'数据不足({total}条)，默认震荡市'
                }

            bullish_pct = bullish_count / total * 100
            bearish_pct = bearish_count / total * 100

            if bullish_pct >= self.TREND_THRESHOLD_PCT:
                regime = self.REGIME_BULL
                reason = f'多头主导 {bullish_pct:.0f}%({bullish_count}/{total})'
            elif bearish_pct >= self.TREND_THRESHOLD_PCT:
                regime = self.REGIME_BEAR
                reason = f'空头主导 {bearish_pct:.0f}%({bearish_count}/{total})'
            else:
                regime = self.REGIME_SIDEWAYS
                reason = f'多空拉锯 多{bullish_pct:.0f}% 空{bearish_pct:.0f}%'

            return {
                'regime': regime,
                'bullish_pct': bullish_pct,
                'bearish_pct': bearish_pct,
                'total_records': total,
                'reason': reason
            }
        finally:
            conn.close()

    def apply_regime(self, regime_result: Dict) -> bool:
        """
        记录市场状态变化（仅供日志/通知参考，不修改系统设置）

        Args:
            regime_result: detect_regime 的返回结果

        Returns:
            bool: 状态是否发生变化
        """
        regime = regime_result['regime']

        if regime == self.current_regime:
            return False  # 状态无变化

        old_regime = self.current_regime or '初始化'
        self.current_regime = regime
        self.last_check_time = datetime.now()

        logger.warning(
            f"🧬 [BIG4-REGIME] 市场状态切换: {old_regime} → {regime} | "
            f"{regime_result['reason']} | "
            f"(仅记录，未修改 allow_long/allow_short)"
        )
        return True

    def run_detection(self) -> Dict:
        """
        运行一次完整的检测+应用（供定时任务调用，建议每小时一次）
        """
        try:
            result = self.detect_regime(hours=48)
            changed = self.apply_regime(result)

            if changed:
                logger.warning(
                    f"🧬 [BIG4-REGIME] ⚡ 已切换至 {result['regime']} | {result['reason']}"
                )
            else:
                logger.info(
                    f"🧬 [BIG4-REGIME] 维持 {self.current_regime or result['regime']} | {result['reason']}"
                )
            return result
        except Exception as e:
            logger.error(f"❌ [BIG4-REGIME] 检测运行失败: {e}")
            return {'regime': None, 'error': str(e)}
