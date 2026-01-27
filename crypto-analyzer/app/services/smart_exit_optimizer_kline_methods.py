"""
智能平仓优化器 - K线强度监控方法
这些方法应该添加到SmartExitOptimizer类中
"""

async def _should_check_kline_strength(self, position_id: int) -> bool:
    """
    判断是否需要检查K线强度（每15分钟检查一次）

    Args:
        position_id: 持仓ID

    Returns:
        是否需要检查
    """
    now = datetime.now()

    if position_id not in self.last_kline_check:
        # 首次检查
        self.last_kline_check[position_id] = now
        return True

    last_check = self.last_kline_check[position_id]
    elapsed = (now - last_check).total_seconds()

    if elapsed >= self.kline_check_interval:
        self.last_kline_check[position_id] = now
        return True

    return False


async def _check_kline_strength_decay(
    self,
    position: Dict,
    current_price: float,
    profit_info: Dict
) -> Optional[Tuple[str, float]]:
    """
    检查K线强度是否衰减，决定是否平仓

    Args:
        position: 持仓信息
        current_price: 当前价格
        profit_info: 盈亏信息

    Returns:
        (平仓原因, 平仓比例) 或 None
    """
    try:
        symbol = position['symbol']
        direction = position['direction']
        entry_time = position.get('entry_signal_time', datetime.now())

        # 获取持仓时长（分钟）
        hold_minutes = (datetime.now() - entry_time).total_seconds() / 60

        # 获取当前K线强度
        strength_1h = self.signal_analyzer.analyze_kline_strength(symbol, '1h', 24)
        strength_15m = self.signal_analyzer.analyze_kline_strength(symbol, '15m', 24)
        strength_5m = self.signal_analyzer.analyze_kline_strength(symbol, '5m', 24)

        if not all([strength_1h, strength_15m, strength_5m]):
            return None

        # 计算当前K线强度评分
        current_kline = self.kline_scorer.calculate_strength_score(
            strength_1h, strength_15m, strength_5m
        )

        # === 检测1: 1H K线反转 ===
        if direction == 'LONG' and strength_1h['net_power'] <= -3:
            # 多头持仓，但1H出现空头信号
            if profit_info['profit_pct'] >= 2.0:
                return ('1H K线反转+盈利>=2%', 0.7)  # 平仓70%
            else:
                return ('1H K线反转', 0.5)  # 平仓50%

        elif direction == 'SHORT' and strength_1h['net_power'] >= 3:
            # 空头持仓，但1H出现多头信号
            if profit_info['profit_pct'] >= 2.0:
                return ('1H K线反转+盈利>=2%', 0.7)
            else:
                return ('1H K线反转', 0.5)

        # === 检测2: 15M连续强力反转 ===
        if direction == 'LONG':
            # 检查15M是否连续3根强空K线
            is_strong_reversal = (
                strength_15m['net_power'] <= -5 and
                strength_5m['net_power'] <= -5
            )
            if is_strong_reversal:
                return ('15M连续强力反转', 1.0)  # 全部平仓

        elif direction == 'SHORT':
            # 检查15M是否连续3根强多K线
            is_strong_reversal = (
                strength_15m['net_power'] >= 5 and
                strength_5m['net_power'] >= 5
            )
            if is_strong_reversal:
                return ('15M连续强力反转', 1.0)  # 全部平仓

        # === 检测3: 持仓时长到期 + 强度衰减 ===
        # 获取最大持仓时长
        max_hold_minutes = position.get('max_hold_minutes') or 360

        if hold_minutes >= max_hold_minutes:
            # 检查K线强度是否明显衰减
            if current_kline['total_score'] < 15:
                # 强度不足15分
                if profit_info['profit_pct'] >= 4.0:
                    return ('持仓时长到期+强度衰减+盈利>=4%', 1.0)  # 全部平仓
                elif profit_info['profit_pct'] >= 2.0:
                    return ('持仓时长到期+强度衰减+盈利>=2%', 0.7)  # 平仓70%
                else:
                    return ('持仓时长到期+强度衰减', 0.5)  # 平仓50%

        # === 检测4: 盈利+强度衰减 ===
        if profit_info['profit_pct'] >= 4.0:
            # 盈利>=4%，检查强度是否减弱
            if current_kline['total_score'] < 20:
                return ('盈利>=4%+强度减弱', 1.0)  # 全部平仓

        elif profit_info['profit_pct'] >= 2.0:
            # 盈利>=2%，检查强度是否大幅减弱
            if current_kline['total_score'] < 15:
                return ('盈利>=2%+强度大幅减弱', 0.7)  # 平仓70%

        # === 检测5: 亏损 + 强度反转 ===
        if profit_info['profit_pct'] < -1.0:
            # 亏损>1%，检查K线方向是否反转
            if current_kline['direction'] != 'NEUTRAL' and current_kline['direction'] != direction:
                return ('亏损>1%+方向反转', 1.0)  # 止损

        return None

    except Exception as e:
        logger.error(f"检查K线强度衰减失败: {e}")
        return None


async def _execute_partial_close(
    self,
    position_id: int,
    current_price: float,
    close_ratio: float,
    reason: str
):
    """
    执行部分平仓

    Args:
        position_id: 持仓ID
        current_price: 当前价格
        close_ratio: 平仓比例 (0.0-1.0)
        reason: 平仓原因
    """
    try:
        # 获取持仓
        position = await self._get_position(position_id)
        if not position:
            return

        # 计算平仓数量
        total_size = Decimal(str(position['position_size']))
        close_size = total_size * Decimal(str(close_ratio))

        logger.info(
            f"📉 执行部分平仓: 持仓{position_id} {position['symbol']} | "
            f"比例{close_ratio*100:.0f}% | 数量{float(close_size):.4f}/{float(total_size):.4f}"
        )

        # 调用实盘引擎执行平仓
        # 注意：close_position_partial会负责更新数据库（quantity, margin, notes等）
        # 因此这里不再重复更新数据库，避免竞态条件导致数量被重复扣除
        if self.live_engine:
            result = await self.live_engine.close_position_partial(
                position_id=position_id,
                close_ratio=close_ratio,
                reason=reason
            )

            if result and result.get('success'):
                remaining_quantity = result.get('remaining_quantity', 0)
                logger.info(f"✅ 部分平仓完成: 持仓{position_id} | 剩余数量{remaining_quantity:.4f}")
            else:
                logger.error(f"❌ 部分平仓失败: 持仓{position_id}")

    except Exception as e:
        logger.error(f"执行部分平仓失败: {e}")
