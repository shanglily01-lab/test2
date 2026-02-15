#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
共振检查器
检查代币自评分与Big4评分是否共振
"""
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class ResonanceChecker:
    """共振检查器"""

    def __init__(self, config: Dict = None):
        """
        初始化共振检查器

        Args:
            config: 共振配置
                {
                    'enabled': True,
                    'min_symbol_score': 15,  # 代币最低分数（绝对值）
                    'min_big4_score': 10,    # Big4最低分数（绝对值）
                    'require_same_direction': True,  # 是否要求方向一致
                    'resonance_threshold': 25  # 共振总分阈值（两者之和绝对值）
                }
        """
        self.config = config or self._default_config()

    def _default_config(self) -> Dict:
        """默认配置"""
        return {
            'enabled': True,
            'min_symbol_score': 15,
            'min_big4_score': 10,
            'require_same_direction': True,
            'resonance_threshold': 25
        }

    def check_resonance(
        self,
        symbol: str,
        symbol_score_data: Dict,
        big4_score_data: Dict
    ) -> Dict:
        """
        检查代币评分与Big4评分是否共振

        Args:
            symbol: 交易对名称
            symbol_score_data: 代币评分数据（来自KlineScoreCalculator）
            big4_score_data: Big4评分数据

        Returns:
            {
                'resonance': True/False,  # 是否共振
                'passed': True/False,     # 是否通过过滤
                'symbol_score': -25,      # 代币评分
                'big4_score': -20,        # Big4评分
                'symbol_direction': 'SHORT',
                'big4_direction': 'SHORT',
                'resonance_strength': 'strong',  # 共振强度: strong/medium/weak
                'total_abs_score': 45,    # 总分绝对值
                'reason': '代币强空(-25) + Big4强空(-20) = 强共振通过',
                'details': []  # 详细检查信息
            }
        """
        if not self.config.get('enabled', True):
            return {
                'resonance': True,
                'passed': True,
                'reason': '共振检查已禁用',
                'details': []
            }

        details = []

        # 1. 提取评分
        symbol_score = symbol_score_data.get('total_score', 0)
        symbol_direction = symbol_score_data.get('direction', 'NEUTRAL')
        symbol_strength = symbol_score_data.get('strength_level', 'weak')

        big4_score = big4_score_data.get('strength', 0)
        big4_direction = self._get_direction(big4_score)

        # 2. 检查代币分数是否达标
        min_symbol_score = self.config.get('min_symbol_score', 15)
        symbol_abs = abs(symbol_score)
        symbol_meets_threshold = symbol_abs >= min_symbol_score

        details.append({
            'check': '代币评分',
            'value': symbol_score,
            'threshold': min_symbol_score,
            'passed': symbol_meets_threshold,
            'desc': f"{symbol} 评分={symbol_score:+d} (阈值≥{min_symbol_score})"
        })

        # 3. 检查Big4分数是否达标
        min_big4_score = self.config.get('min_big4_score', 10)
        big4_abs = abs(big4_score)
        big4_meets_threshold = big4_abs >= min_big4_score

        details.append({
            'check': 'Big4评分',
            'value': big4_score,
            'threshold': min_big4_score,
            'passed': big4_meets_threshold,
            'desc': f"Big4 评分={big4_score:+d} (阈值≥{min_big4_score})"
        })

        # 4. 检查方向是否一致
        require_same_direction = self.config.get('require_same_direction', True)
        direction_match = symbol_direction == big4_direction

        if require_same_direction:
            details.append({
                'check': '方向一致性',
                'value': f"{symbol_direction} vs {big4_direction}",
                'threshold': '一致',
                'passed': direction_match,
                'desc': f"代币{symbol_direction} vs Big4{big4_direction}"
            })

        # 5. 检查共振总分
        # 只有同向时才累加，反向时不计算共振
        if direction_match:
            total_abs_score = symbol_abs + big4_abs
        else:
            total_abs_score = 0

        resonance_threshold = self.config.get('resonance_threshold', 25)
        resonance_meets_threshold = total_abs_score >= resonance_threshold

        details.append({
            'check': '共振总分',
            'value': total_abs_score,
            'threshold': resonance_threshold,
            'passed': resonance_meets_threshold,
            'desc': f"共振总分={total_abs_score} (阈值≥{resonance_threshold})"
        })

        # 6. 判断共振强度
        if total_abs_score >= 50:
            resonance_strength = 'strong'
        elif total_abs_score >= 30:
            resonance_strength = 'medium'
        else:
            resonance_strength = 'weak'

        # 7. 综合判断
        passed = True
        failed_checks = []

        if not symbol_meets_threshold:
            passed = False
            failed_checks.append(f"代币评分不足({symbol_abs}<{min_symbol_score})")

        if not big4_meets_threshold:
            passed = False
            failed_checks.append(f"Big4评分不足({big4_abs}<{min_big4_score})")

        if require_same_direction and not direction_match:
            passed = False
            failed_checks.append(f"方向不一致({symbol_direction}≠{big4_direction})")

        if not resonance_meets_threshold:
            passed = False
            failed_checks.append(f"共振总分不足({total_abs_score}<{resonance_threshold})")

        # 8. 生成原因说明
        if passed:
            reason = self._generate_passed_reason(
                symbol, symbol_score, big4_score, total_abs_score, resonance_strength
            )
        else:
            reason = f"❌ 共振检查失败: {', '.join(failed_checks)}"

        return {
            'resonance': passed,
            'passed': passed,
            'symbol_score': symbol_score,
            'big4_score': big4_score,
            'symbol_direction': symbol_direction,
            'big4_direction': big4_direction,
            'symbol_strength': symbol_strength,
            'resonance_strength': resonance_strength,
            'total_abs_score': total_abs_score,
            'reason': reason,
            'details': details
        }

    def _get_direction(self, score: int) -> str:
        """根据分数判断方向"""
        if score > 5:
            return 'LONG'
        elif score < -5:
            return 'SHORT'
        else:
            return 'NEUTRAL'

    def _generate_passed_reason(
        self,
        symbol: str,
        symbol_score: int,
        big4_score: int,
        total_abs_score: int,
        resonance_strength: str
    ) -> str:
        """生成通过原因"""
        # 判断趋势方向
        if symbol_score > 0:
            trend_desc = "多头"
        else:
            trend_desc = "空头"

        # 判断强度
        strength_map = {
            'strong': '强',
            'medium': '中',
            'weak': '弱'
        }
        strength_cn = strength_map.get(resonance_strength, '弱')

        return (
            f"✅ {trend_desc}{strength_cn}共振通过: "
            f"{symbol}({symbol_score:+d}) + Big4({big4_score:+d}) = {total_abs_score}分"
        )
