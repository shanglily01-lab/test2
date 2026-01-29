# Coin-Margined Futures Trading Engine - Quick Start Guide

## Import

```python
from app.trading.coin_futures_trading_engine import CoinFuturesTradingEngine
from decimal import Decimal
```

## Initialization

```python
db_config = {
    'host': 'localhost',
    'port': 3306,
    'user': 'root',
    'password': 'your_password',
    'database': 'binance-data'
}

engine = CoinFuturesTradingEngine(
    db_config=db_config,
    trade_notifier=notifier,  # Optional
    live_engine=live_engine    # Optional
)
```

## Open Position (Coin-Margined)

### Simple Market Order
```python
result = engine.open_position(
    account_id=3,                          # Always 3 for coin-margined
    symbol='BTC/USD',                      # Coin-margined symbol
    position_side='LONG',                  # or 'SHORT'
    quantity=Decimal('0.1'),               # 0.1 BTC
    leverage=5                             # 5x leverage
)

if result['success']:
    position_id = result['position_id']
    print(f"Position opened: {result['symbol']} at {result['entry_price']}")
```

### With Stop Loss & Take Profit (Percentage)
```python
result = engine.open_position(
    account_id=3,
    symbol='ETH/USD',
    position_side='LONG',
    quantity=Decimal('1'),
    leverage=3,
    stop_loss_pct=Decimal('5'),            # 5% stop loss
    take_profit_pct=Decimal('10')          # 10% take profit
)
```

### With Specific Price Levels
```python
result = engine.open_position(
    account_id=3,
    symbol='BTC/USD',
    position_side='SHORT',
    quantity=Decimal('0.05'),
    leverage=2,
    stop_loss_price=Decimal('32000'),      # Specific price
    take_profit_price=Decimal('28000')     # Specific price
)
```

### With Limit Order
```python
result = engine.open_position(
    account_id=3,
    symbol='SOL/USD',
    position_side='LONG',
    quantity=Decimal('10'),
    leverage=4,
    limit_price=Decimal('120'),            # Limit order at 120
    stop_loss_pct=Decimal('3'),
    take_profit_pct=Decimal('8')
)

if result['status'] == 'PENDING':
    print(f"Limit order created, waiting for price {result['limit_price']}")
```

## Close Position

### Full Position Close
```python
close_result = engine.close_position(
    position_id=position_id,
    reason='manual'  # or other reasons like 'take_profit', 'stop_loss'
)

if close_result['success']:
    print(f"Realized P&L: {close_result['realized_pnl']}")
    print(f"ROI: {close_result['roi']}%")
```

### Partial Position Close
```python
close_result = engine.close_position(
    position_id=position_id,
    close_quantity=Decimal('0.05'),        # Close only 0.05 BTC
    reason='manual'
)
```

### Close with Specific Price
```python
close_result = engine.close_position(
    position_id=position_id,
    close_price=Decimal('29500'),          # Close at specific price
    reason='manual'
)
```

## Get Open Positions

```python
positions = engine.get_open_positions(account_id=3)

for pos in positions:
    print(f"{pos['symbol']} {pos['position_side']}: {pos['quantity']} @ {pos['entry_price']}")
    print(f"Current P&L: {pos['unrealized_pnl']} ({pos['unrealized_pnl_pct']}%)")
```

## Update Account Equity

```python
updated_count = engine.update_all_accounts_equity()
print(f"Updated {updated_count} account(s)")
```

## Supported Coin-Margined Symbols

```
BTC/USD    Bitcoin
ETH/USD    Ethereum
BNB/USD    Binance Coin
SOL/USD    Solana
ADA/USD    Cardano
DOT/USD    Polkadot
XRP/USD    Ripple
LINK/USD   Chainlink
```

## Key Features

### Batch Entry
Positions can be built incrementally with multiple open calls:
```python
# First entry
result1 = engine.open_position(..., quantity=Decimal('0.1'))

# Add to position
result2 = engine.open_position(..., quantity=Decimal('0.05'))
# Same symbol and direction will combine into one position
```

### EMA-Based Signals
Enter positions with EMA signal information:
```python
result = engine.open_position(
    account_id=3,
    symbol='BTC/USD',
    position_side='LONG',
    quantity=Decimal('0.1'),
    leverage=2,
    signal_id=123,
    strategy_id=456,
    entry_signal_type='golden_cross',
    entry_reason='EMA9 crossed above EMA26',
    entry_score=8.5
)
```

### Smart Exit
Multiple exit strategies are automatically applied:
- Stop loss at specific price or percentage
- Take profit at specific price or percentage
- EMA trend reversal detection
- Moving stop loss
- Maximum hold time protection

## Error Handling

```python
result = engine.open_position(
    account_id=3,
    symbol='BTC/USD',
    position_side='LONG',
    quantity=Decimal('0.1'),
    leverage=5
)

if not result['success']:
    error_msg = result.get('message', 'Unknown error')
    print(f"Error: {error_msg}")
    # Common errors:
    # - "余额不足" (Insufficient balance)
    # - "保证金超过限制" (Margin exceeds limit)
    # - "无法获取{symbol}的价格" (Cannot get price)
```

## Important Notes

1. **Fixed Account ID**: Always use `account_id=3` for coin-margined trading
2. **Symbol Format**: Use `/USD` suffix (e.g., `BTC/USD`, not `BTCUSD`)
3. **Quantity Precision**: Quantities are automatically rounded to 8 decimal places
4. **Margin Limit**: Single position margin cannot exceed 10% of account equity
5. **Fee Rate**: 0.04% transaction fee on all trades
6. **Liquidation**: Calculated with 0.5% maintenance margin rate

## Database Requirements

Make sure your database has:
- `futures_trading_accounts` table with account_id=3
- `futures_positions` table with `coin_margin` column
- K-line data for price calculations in `kline_data` table
- Price data in `price_data` table

## Performance Tips

1. **Connection Pooling**: Engine maintains persistent connection (5-min refresh)
2. **Price Caching**: Uses real-time API with database fallback
3. **Batch Updates**: Equity updates are done at account level, not per trade
4. **Efficient Queries**: Uses indexed fields for position lookups

## Notifications

If trade_notifier is provided, automatic notifications are sent for:
- Position opening (with details)
- Position closing (with P&L)
- Close reasons (stop loss, take profit, etc.)

## Live Engine Sync

If live_engine is provided, can optionally sync with live trading:
```python
# Set syncLive flag in strategy config
strategy_config = {
    'syncLive': True,
    ...
}
```

## Debugging

Enable logging to see detailed operations:
```python
from loguru import logger
logger.enable("app.trading.coin_futures_trading_engine")
logger.add(sys.stderr, level="DEBUG")
```

Log messages will show:
- Database connection status
- Position creation/closure
- Price fetching (real-time vs. cached)
- Limit order creation
- Equity updates
- Error details with full stack traces

## Common Workflows

### Swing Trade
```python
# Open position
result = engine.open_position(
    account_id=3, symbol='BTC/USD', position_side='LONG',
    quantity=Decimal('0.1'), leverage=3,
    stop_loss_pct=Decimal('5'), take_profit_pct=Decimal('15')
)

# Hold and monitor positions
positions = engine.get_open_positions(3)

# Close when target reached or stop triggered
engine.close_position(result['position_id'], reason='take_profit')
```

### Scalping
```python
# Open micro position
result = engine.open_position(
    account_id=3, symbol='ETH/USD', position_side='SHORT',
    quantity=Decimal('0.01'), leverage=5,
    stop_loss_price=Decimal('1950'), take_profit_price=Decimal('1930')
)

# Close quickly
engine.close_position(result['position_id'], reason='manual')
```

### Grid Trading
```python
# Multiple positions at different levels
for price_level, qty in [(30000, 0.05), (29500, 0.05), (29000, 0.05)]:
    engine.open_position(
        account_id=3, symbol='BTC/USD', position_side='LONG',
        quantity=Decimal(str(qty)), leverage=2,
        limit_price=Decimal(str(price_level))
    )

# Manage exits programmatically
positions = engine.get_open_positions(3)
for pos in positions:
    if pos['unrealized_pnl'] > 100:
        engine.close_position(pos['position_id'])
```

---

For detailed documentation, see: `COIN_FUTURES_ENGINE_SUMMARY.md`
For verification checklist, see: `COIN_FUTURES_VERIFICATION.md`
