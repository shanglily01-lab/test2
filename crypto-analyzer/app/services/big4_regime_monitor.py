"""
Big4市场状态监控器（操纵子原理 / lac Operon）

类比大肠杆菌lac操纵子：根据环境信号（Big4趋势分布）自动切换策略模式，
无需人工手动切换 allow_long / allow_short 开关。

状态判定逻辑（基于big4_trend_history过去48小时的信号分布）：
- BULL状态  （多头信号占比 >=70%）→ allow_long=1, allow_short=0
- BEAR状态  （空头信号占比 >=70%）→ allow_long=0, allow_short=1
- SIDEWAYS状态（多空各占一席）   → allow_long=1, allow_short=1

设计哲学：
  生物体中，lac操纵子在乳糖存在时自动开启乳糖代谢基因，
  葡萄糖充足时自动关闭，环境信号直接驱动基因开关，不需要外部干预。
  本模块让超级大脑同样具备"感知环境→自动切换代谢模式"的能力。
"""

import pymysql
from datetime import datetime
from loguru import logger
from typing import Dict, Optional


class Big4RegimeMonitor:
    """
    Big4市场状态监控器

    基于Big4过去48小时趋势信号的分布，判断当前整体市场状态，
    并自动更新system_settings中的allow_long/allow_short开关。
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
        根据检测到的市场状态更新system_settings（仅在状态切换时写库）

        BULL     → allow_long=1, allow_short=0
        BEAR     → allow_long=0, allow_short=1
        SIDEWAYS → allow_long=1, allow_short=1

        Returns:
            bool: 状态是否发生切换
        """
        regime = regime_result['regime']

        if regime == self.current_regime:
            return False  # 状态无变化，不写库

        regime_settings = {
            self.REGIME_BULL:     (1, 0),
            self.REGIME_BEAR:     (0, 1),
            self.REGIME_SIDEWAYS: (1, 1),
        }
        allow_long, allow_short = regime_settings[regime]

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO system_settings (setting_key, setting_value) VALUES ('allow_long', %s)"
                " ON DUPLICATE KEY UPDATE setting_value = %s",
                (str(allow_long), str(allow_long))
            )
            cursor.execute(
                "INSERT INTO system_settings (setting_key, setting_value) VALUES ('allow_short', %s)"
                " ON DUPLICATE KEY UPDATE setting_value = %s",
                (str(allow_short), str(allow_short))
            )
            conn.commit()
            cursor.close()

            old_regime = self.current_regime or '初始化'
            self.current_regime = regime
            self.last_check_time = datetime.now()

            logger.warning(
                f"🧬 [BIG4-REGIME] 市场状态切换: {old_regime} → {regime} | "
                f"{regime_result['reason']} | "
                f"allow_long={allow_long} allow_short={allow_short}"
            )
            return True
        except Exception as e:
            logger.error(f"❌ [BIG4-REGIME] 更新system_settings失败: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            conn.close()

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
