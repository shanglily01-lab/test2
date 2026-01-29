# Coin-Margined Futures Trading Engine

## Overview

Created a new coin-margined futures trading engine (`CoinFuturesTradingEngine`) based on the existing USDT futures engine. This engine is specialized for coin-margined perpetual futures contracts.

## File Location

- **Source File**: `d:\test2\crypto-analyzer\app\trading\futures_trading_engine.py`
- **New File**: `d:\test2\crypto-analyzer\app\trading\coin_futures_trading_engine.py`

## Key Modifications

### 1. Class Name Change
- **Old**: `FuturesTradingEngine`
- **New**: `CoinFuturesTradingEngine`

### 2. Account ID Configuration
- **Fixed Account ID**: `self.account_id = 3`
- **User ID**: 1000
- **Account Type**: Coin-margined futures account
- Set in `__init__` method (line 102)

### 3. Coin-Margin Field
- Added `coin_margin` field to position insertion (line 719)
- Value: `1` (indicating coin-margined position) (line 739)
- Helps identify positions as coin-margined in the database

### 4. Supported Trading Pairs

The engine supports the following coin-margined symbols:
- ADA/USD
- DOT/USD
- BNB/USD
- SOL/USD
- XRP/USD
- LINK/USD
- BTC/USD
- ETH/USD

### 5. Documentation Updates

#### Module Docstring (Lines 1-6)
```python
"""
币本位合约交易引擎
支持币本位合约交易（ADA/USD, DOT/USD, BNB/USD, SOL/USD, XRP/USD, LINK/USD, BTC/USD, ETH/USD等）
支持多空双向交易、杠杆、止盈止损
使用 account_id=3 (user_id=1000, 币本位合约账户)
"""
```

#### Class Docstring (Lines 52-58)
```python
class CoinFuturesTradingEngine:
    """币本位合约交易引擎

    专用于币本位合约交易，支持的交易对包括：
    - ADA/USD, DOT/USD, BNB/USD, SOL/USD, XRP/USD, LINK/USD, BTC/USD, ETH/USD

    固定使用 account_id=3 (user_id=1000, 币本位合约账户)
    """
```

#### __init__ Docstring (Lines 84-94)
Added notes documenting:
- Fixed account_id=3 for coin-margined account
- Supported trading pairs

#### open_position Docstring (Lines 361-396)
Updated to specify:
- account_id must be 3
- symbol must be coin-margined contract pair
- New parameter: entry_score

#### close_position Docstring (Lines 877-892)
Updated to specify:
- Coin-margined contract closing
- close_price is optional parameter

### 6. Logging Updates

Updated log messages to reflect coin-margined operations:
- Connection: "币本位合约交易引擎数据库连接成功 (account_id=3)"
- Position closing: "📤 [币本位合约平仓]"

## Unchanged Features

All core functionality remains identical to the USDT futures engine:

### Entry Logic
- **Batch entry support**: Positions can be built incrementally
- **Price fetching**: Real-time API fallback to database cache
- **Limit orders**: Support for pending limit orders
- **Margin validation**: Single position margin limited to 10% of total equity

### Position Management
- **Stop loss**: Percentage or price-based
- **Take profit**: Percentage or price-based
- **Liquidation price calculation**: Based on leverage and maintenance margin rate
- **EMA tracking**: Entry EMA diff for trend reversal detection

### Exit Logic
- **Smart exit optimization**: Multiple exit strategies supported
- **Stop loss monitor**: Automatic stop loss execution
- **Take profit monitor**: Automatic take profit execution
- **Trend reversal detection**: EMA-based position closing

### Account Management
- **Balance tracking**: Current, frozen, and available balances
- **Equity calculation**: Total equity = balance + frozen + unrealized PnL
- **Trade statistics**: Win rate, winning/losing trades tracking
- **Fee calculation**: 0.04% transaction fee

### Additional Features
- **Telegram notifications**: Trade opening/closing notifications
- **Live trading sync**: Optional synchronization with live trading engine
- **Position tracking**: Full order and trade history
- **Multi-connection support**: Connection pooling with 5-minute refresh

## Database Schema Expectations

The engine expects the following tables and fields:

### `futures_trading_accounts`
- id
- user_id
- current_balance
- frozen_balance
- total_equity
- realized_pnl
- total_trades
- winning_trades
- losing_trades
- win_rate
- updated_at

### `futures_positions`
- id
- account_id
- symbol
- position_side (LONG/SHORT)
- leverage
- quantity
- notional_value
- margin
- entry_price
- mark_price
- liquidation_price
- stop_loss_price
- take_profit_price
- stop_loss_pct
- take_profit_pct
- entry_ema_diff
- entry_signal_type
- entry_score
- entry_reason
- **coin_margin** (NEW FIELD)
- unrealized_pnl
- unrealized_pnl_pct
- open_time
- close_time
- source
- signal_id
- strategy_id
- status
- notes
- realized_pnl
- last_update_time

### `futures_orders`
- id
- account_id
- order_id
- position_id
- symbol
- side
- order_type
- leverage
- price
- quantity
- executed_quantity
- margin
- total_value
- executed_value
- fee
- fee_rate
- status
- avg_fill_price
- fill_time
- realized_pnl
- pnl_pct
- order_source
- entry_signal_type
- signal_id
- strategy_id
- notes
- created_at
- stop_loss_price
- take_profit_price

### `futures_trades`
- id
- account_id
- order_id
- position_id
- trade_id
- symbol
- side
- price
- quantity
- notional_value
- leverage
- margin
- fee
- fee_rate
- realized_pnl
- pnl_pct
- roi
- entry_price
- close_price
- trade_time

## Usage Example

```python
from app.trading.coin_futures_trading_engine import CoinFuturesTradingEngine

# Initialize the engine
engine = CoinFuturesTradingEngine(
    db_config={
        'host': 'localhost',
        'port': 3306,
        'user': 'root',
        'password': 'password',
        'database': 'binance-data'
    },
    trade_notifier=notifier,
    live_engine=live_engine
)

# Open a coin-margined position
result = engine.open_position(
    account_id=3,  # Always 3 for coin-margined accounts
    symbol='BTC/USD',  # Coin-margined symbol
    position_side='LONG',
    quantity=Decimal('0.1'),
    leverage=5,
    stop_loss_pct=Decimal('5'),
    take_profit_pct=Decimal('10')
)

# Close position
close_result = engine.close_position(
    position_id=result['position_id'],
    reason='manual'
)
```

## Key Differences from USDT Futures Engine

1. **Account ID**: Fixed to 3 (coin-margined account)
2. **Symbols**: Uses coin-margined pairs (e.g., BTC/USD instead of BTC/USDT)
3. **Exchange Data**: Sources from binance_coin_futures for price data
4. **Position Marking**: coin_margin field set to 1 for all positions
5. **Documentation**: Specific to coin-margined contract trading

## Performance Characteristics

- **Connection Pool**: 5-minute connection refresh for database efficiency
- **Price Fetching**: Real-time API with fallback to database cache
- **Batch Operations**: Support for incremental position building
- **Equity Updates**: Automatic equity recalculation after each trade

## Notes

- The engine maintains backward compatibility with all existing interfaces
- All mathematical calculations (margin, liquidation price, PnL) are identical to USDT futures
- The only operational difference is the account_id (fixed to 3) and symbol format (USD instead of USDT)
- The coin_margin flag helps distinguish coin-margined positions in the database for reporting and analysis purposes

## Migration Path

If upgrading from the USDT futures engine:
1. Keep both engines running independently
2. Route coin-margined trades to CoinFuturesTradingEngine
3. Route USDT futures trades to FuturesTradingEngine
4. Use the coin_margin field in database queries to filter positions by type
