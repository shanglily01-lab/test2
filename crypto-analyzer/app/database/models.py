"""
数据库模型定义
用于存储价格数据、K线数据、交易数据等
"""

from sqlalchemy import Column, Integer, BigInteger, Float, String, DateTime, Boolean, Index, DECIMAL
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()


class PriceData(Base):
    """实时价格数据表"""
    __tablename__ = 'price_data'

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)  # 交易对 如 BTC/USDT
    exchange = Column(String(20), nullable=False, default='binance')  # 交易所
    timestamp = Column(DateTime, nullable=False, index=True)  # 时间戳

    # 价格数据
    price = Column(DECIMAL(18, 8), nullable=False)  # 当前价格
    open_price = Column(DECIMAL(18, 8))  # 开盘价
    high_price = Column(DECIMAL(18, 8))  # 最高价
    low_price = Column(DECIMAL(18, 8))  # 最低价
    close_price = Column(DECIMAL(18, 8))  # 收盘价

    # 成交量数据
    volume = Column(DECIMAL(20, 8))  # 成交量(基础货币)
    quote_volume = Column(DECIMAL(24, 2))  # 成交量(计价货币) - 扩大以支持大额合约数据

    # 买卖盘数据
    bid_price = Column(DECIMAL(18, 8))  # 买一价
    ask_price = Column(DECIMAL(18, 8))  # 卖一价

    # 变化数据
    change_24h = Column(Float)  # 24小时涨跌幅

    created_at = Column(DateTime, default=datetime.now)  # 记录创建时间

    # 创建复合索引以提高查询性能
    __table_args__ = (
        Index('idx_symbol_timestamp', 'symbol', 'timestamp'),
        Index('idx_exchange_symbol', 'exchange', 'symbol'),
    )

    def __repr__(self):
        return f"<PriceData(symbol={self.symbol}, price={self.price}, timestamp={self.timestamp})>"


class KlineData(Base):
    """K线数据表"""
    __tablename__ = 'kline_data'

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)  # 交易对
    exchange = Column(String(20), nullable=False, default='binance')  # 交易所
    timeframe = Column(String(10), nullable=False)  # 时间周期 如 1m, 5m, 1h, 1d

    # 时间
    open_time = Column(BigInteger, nullable=False, index=True)  # 开盘时间(毫秒时间戳)
    close_time = Column(BigInteger)  # 收盘时间
    timestamp = Column(DateTime, nullable=False)  # 可读时间

    # OHLCV数据
    open_price = Column(DECIMAL(18, 8), nullable=False)  # 开盘价
    high_price = Column(DECIMAL(18, 8), nullable=False)  # 最高价
    low_price = Column(DECIMAL(18, 8), nullable=False)  # 最低价
    close_price = Column(DECIMAL(18, 8), nullable=False)  # 收盘价
    volume = Column(DECIMAL(20, 8), nullable=False)  # 成交量

    # 额外数据
    quote_volume = Column(DECIMAL(24, 2))  # 成交额 - 扩大以支持大额合约数据
    number_of_trades = Column(Integer)  # 成交笔数
    taker_buy_base_volume = Column(DECIMAL(20, 8))  # 主动买入成交量
    taker_buy_quote_volume = Column(DECIMAL(24, 2))  # 主动买入成交额 - 扩大以支持大额合约数据

    created_at = Column(DateTime, default=datetime.now)

    # 复合索引和唯一约束
    __table_args__ = (
        Index('idx_symbol_timeframe_time', 'symbol', 'timeframe', 'open_time'),
        Index('idx_timestamp', 'timestamp'),
    )

    # 属性别名,方便访问
    @property
    def open(self):
        return self.open_price

    @property
    def high(self):
        return self.high_price

    @property
    def low(self):
        return self.low_price

    @property
    def close(self):
        return self.close_price

    def __repr__(self):
        return f"<KlineData(symbol={self.symbol}, timeframe={self.timeframe}, time={self.timestamp})>"


class TradeData(Base):
    """交易数据表"""
    __tablename__ = 'trade_data'

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(BigInteger, unique=True, index=True)  # 交易ID
    symbol = Column(String(20), nullable=False, index=True)  # 交易对
    exchange = Column(String(20), nullable=False, default='binance')  # 交易所

    # 交易数据
    price = Column(DECIMAL(18, 8), nullable=False)  # 成交价格
    quantity = Column(DECIMAL(18, 8), nullable=False)  # 成交数量
    quote_quantity = Column(DECIMAL(18, 8))  # 成交额

    # 时间
    trade_time = Column(BigInteger, nullable=False, index=True)  # 交易时间(毫秒)
    timestamp = Column(DateTime, nullable=False)  # 可读时间

    # 方向
    is_buyer_maker = Column(Boolean)  # 是否是买方挂单
    is_best_match = Column(Boolean)  # 是否是最优匹配
    side = Column(String(10))  # buy 或 sell

    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index('idx_symbol_time', 'symbol', 'trade_time'),
    )

    def __repr__(self):
        return f"<TradeData(symbol={self.symbol}, price={self.price}, qty={self.quantity})>"


class OrderBookData(Base):
    """订单簿数据表(快照)"""
    __tablename__ = 'orderbook_data'

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    exchange = Column(String(20), nullable=False, default='binance')
    timestamp = Column(DateTime, nullable=False, index=True)

    # 最优买卖价
    best_bid = Column(DECIMAL(18, 8))  # 买一价
    best_ask = Column(DECIMAL(18, 8))  # 卖一价
    spread = Column(DECIMAL(18, 8))  # 价差

    # 深度数据
    bid_volume = Column(DECIMAL(18, 8))  # 买盘总量
    ask_volume = Column(DECIMAL(18, 8))  # 卖盘总量

    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index('idx_orderbook_symbol_timestamp', 'symbol', 'timestamp'),
    )

    def __repr__(self):
        return f"<OrderBookData(symbol={self.symbol}, timestamp={self.timestamp})>"


class NewsData(Base):
    """新闻数据表"""
    __tablename__ = 'news_data'

    id = Column(Integer, primary_key=True, autoincrement=True)
    news_id = Column(String(100), unique=True, index=True)  # 新闻唯一标识

    # 新闻内容
    title = Column(String(500), nullable=False)  # 标题
    url = Column(String(500), nullable=False, unique=True)  # URL
    source = Column(String(100))  # 来源(如 coindesk, cointelegraph)
    description = Column(String(2000))  # 描述/摘要

    # 时间
    published_at = Column(String(100))  # 发布时间(字符串格式)
    published_datetime = Column(DateTime, index=True)  # 发布时间(日期时间)

    # 关联币种
    symbols = Column(String(200))  # 关联的币种,逗号分隔(如 'BTC,ETH')

    # 情绪分析
    sentiment = Column(String(20))  # positive, negative, neutral
    sentiment_score = Column(Float)  # 情绪分数

    # 投票数据(来自CryptoPanic)
    votes_positive = Column(Integer, default=0)
    votes_negative = Column(Integer, default=0)
    votes_important = Column(Integer, default=0)

    # 元数据
    data_source = Column(String(50))  # 数据来源(cryptopanic, rss, reddit)
    created_at = Column(DateTime, default=datetime.now)  # 记录创建时间

    __table_args__ = (
        Index('idx_published_datetime', 'published_datetime'),
        Index('idx_symbols', 'symbols'),
        Index('idx_sentiment', 'sentiment'),
        Index('idx_source', 'source'),
    )

    def __repr__(self):
        return f"<NewsData(title={self.title[:30]}..., source={self.source})>"


class FundingRateData(Base):
    """资金费率数据表(永续合约)"""
    __tablename__ = 'funding_rate_data'

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)  # 交易对 如 BTCUSDT
    exchange = Column(String(20), nullable=False, default='binance')  # 交易所

    # 资金费率数据
    funding_rate = Column(Float, nullable=False)  # 当前资金费率
    funding_time = Column(BigInteger, nullable=False, index=True)  # 资金费用时间(毫秒时间戳)
    timestamp = Column(DateTime, nullable=False, index=True)  # 可读时间

    # 标记价格(用于计算资金费率)
    mark_price = Column(DECIMAL(18, 8))  # 标记价格
    index_price = Column(DECIMAL(18, 8))  # 指数价格

    # 下一次资金费率时间
    next_funding_time = Column(BigInteger)  # 下次结算时间(毫秒)

    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index('idx_symbol_funding_time', 'symbol', 'funding_time'),
        Index('idx_timestamp', 'timestamp'),
    )

    def __repr__(self):
        return f"<FundingRateData(symbol={self.symbol}, rate={self.funding_rate}, time={self.timestamp})>"


class FuturesOpenInterest(Base):
    """合约持仓量数据表"""
    __tablename__ = 'futures_open_interest'

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)  # 交易对
    exchange = Column(String(20), nullable=False, default='binance_futures')  # 交易所

    # 持仓量数据
    open_interest = Column(DECIMAL(20, 8), nullable=False)  # 持仓量（合约张数）
    open_interest_value = Column(DECIMAL(20, 2))  # 持仓价值(USD)

    # 时间
    timestamp = Column(DateTime, nullable=False, index=True)  # 时间戳

    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index('idx_symbol_timestamp', 'symbol', 'timestamp'),
        Index('idx_exchange_symbol', 'exchange', 'symbol'),
    )

    def __repr__(self):
        return f"<FuturesOpenInterest(symbol={self.symbol}, oi={self.open_interest}, time={self.timestamp})>"


class FuturesLongShortRatio(Base):
    """合约多空比数据表"""
    __tablename__ = 'futures_long_short_ratio'

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)  # 交易对
    exchange = Column(String(20), nullable=False, default='binance_futures')  # 交易所
    period = Column(String(10), nullable=False, default='5m')  # 统计周期

    # 多空比数据 - 账户数比
    long_account = Column(Float, nullable=False)  # 做多账户数比例
    short_account = Column(Float, nullable=False)  # 做空账户数比例
    long_short_ratio = Column(Float, nullable=False)  # 账户数多空比率

    # 多空比数据 - 持仓量比（新增）
    long_position = Column(Float, nullable=True)  # 做多持仓量比例
    short_position = Column(Float, nullable=True)  # 做空持仓量比例
    long_short_position_ratio = Column(Float, nullable=True)  # 持仓量多空比率

    # 时间
    timestamp = Column(DateTime, nullable=False, index=True)  # 时间戳

    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index('idx_symbol_timestamp', 'symbol', 'timestamp'),
        Index('idx_exchange_symbol_period', 'exchange', 'symbol', 'period'),
    )

    def __repr__(self):
        return f"<FuturesLongShortRatio(symbol={self.symbol}, ratio={self.long_short_ratio}, time={self.timestamp})>"


class SmartMoneyAddress(Base):
    """聪明钱地址表 - 存储被监控的大户/机构地址"""
    __tablename__ = 'smart_money_addresses'

    id = Column(Integer, primary_key=True, autoincrement=True)
    address = Column(String(100), nullable=False, unique=True, index=True)  # 区块链地址
    blockchain = Column(String(20), nullable=False, index=True)  # 区块链网络(ethereum, bsc, etc)

    # 地址信息
    label = Column(String(200))  # 地址标签(如: Binance Hot Wallet, Jump Trading)
    address_type = Column(String(50))  # 地址类型: whale(巨鲸), institution(机构), smartTrader(聪明交易者)

    # 统计数据
    total_value_usd = Column(DECIMAL(18, 2))  # 总资产价值(USD)
    win_rate = Column(Float)  # 胜率(%)
    total_trades = Column(Integer, default=0)  # 总交易次数
    profitable_trades = Column(Integer, default=0)  # 盈利交易次数

    # 元数据
    is_active = Column(Boolean, default=True)  # 是否监控中
    first_seen = Column(DateTime)  # 首次发现时间
    last_active = Column(DateTime, index=True)  # 最后活跃时间
    data_source = Column(String(50))  # 数据来源(etherscan, bscscan, nansen, arkham)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        Index('idx_blockchain_type', 'blockchain', 'address_type'),
        Index('idx_active_last_active', 'is_active', 'last_active'),
    )

    def __repr__(self):
        return f"<SmartMoneyAddress(address={self.address[:10]}..., type={self.address_type}, label={self.label})>"


class SmartMoneyTransaction(Base):
    """聪明钱交易记录表 - 存储监控地址的交易活动"""
    __tablename__ = 'smart_money_transactions'

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 交易基本信息
    tx_hash = Column(String(100), nullable=False, unique=True, index=True)  # 交易哈希
    address = Column(String(100), nullable=False, index=True)  # 聪明钱地址
    blockchain = Column(String(20), nullable=False, index=True)  # 区块链网络

    # 代币信息
    token_address = Column(String(100), nullable=False, index=True)  # 代币合约地址
    token_symbol = Column(String(20), nullable=False, index=True)  # 代币符号(BTC, ETH, etc)
    token_name = Column(String(100))  # 代币名称

    # 交易详情
    action = Column(String(20), nullable=False, index=True)  # buy(买入), sell(卖出), transfer(转账)
    amount = Column(DECIMAL(30, 8), nullable=False)  # 交易数量
    amount_usd = Column(DECIMAL(18, 2))  # 交易金额(USD)
    price_usd = Column(DECIMAL(18, 8))  # 交易时代币价格(USD)

    # 交易对手方
    from_address = Column(String(100), index=True)  # 发送方地址
    to_address = Column(String(100), index=True)  # 接收方地址

    # 交易平台
    dex_name = Column(String(50))  # DEX名称(Uniswap, PancakeSwap, etc)
    contract_address = Column(String(100))  # 交易合约地址

    # 时间信息
    block_number = Column(BigInteger, nullable=False, index=True)  # 区块号
    block_timestamp = Column(BigInteger, nullable=False, index=True)  # 区块时间戳
    timestamp = Column(DateTime, nullable=False, index=True)  # 交易时间

    # Gas信息
    gas_used = Column(BigInteger)  # 消耗的Gas
    gas_price = Column(BigInteger)  # Gas价格(Wei)
    transaction_fee = Column(DECIMAL(18, 8))  # 交易手续费(ETH/BNB)

    # 分析标签
    is_large_transaction = Column(Boolean, default=False)  # 是否大额交易(>$100k)
    is_first_buy = Column(Boolean, default=False)  # 是否首次买入此代币
    signal_strength = Column(String(20))  # 信号强度: strong(强), medium(中), weak(弱)

    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index('idx_address_token', 'address', 'token_symbol'),
        Index('idx_token_action_time', 'token_symbol', 'action', 'timestamp'),
        Index('idx_blockchain_block', 'blockchain', 'block_number'),
        Index('idx_large_recent', 'is_large_transaction', 'timestamp'),
    )

    def __repr__(self):
        return f"<SmartMoneyTransaction(token={self.token_symbol}, action={self.action}, amount=${self.amount_usd}, time={self.timestamp})>"


class SmartMoneySignal(Base):
    """聪明钱信号表 - 聚合分析后的投资信号"""
    __tablename__ = 'smart_money_signals'

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 代币信息
    token_symbol = Column(String(20), nullable=False, index=True)  # 代币符号
    token_address = Column(String(100), index=True)  # 代币合约地址
    blockchain = Column(String(20), nullable=False)  # 区块链网络

    # 信号类型
    signal_type = Column(String(20), nullable=False, index=True)  # BUY(买入), SELL(卖出), ACCUMULATION(积累), DISTRIBUTION(分发)
    signal_strength = Column(String(20), nullable=False)  # STRONG(强), MEDIUM(中), WEAK(弱)
    confidence_score = Column(Float, nullable=False)  # 置信度分数(0-100)

    # 聚合统计(最近24小时)
    smart_money_count = Column(Integer, default=0)  # 参与的聪明钱地址数量
    total_buy_amount_usd = Column(DECIMAL(18, 2), default=0)  # 总买入金额
    total_sell_amount_usd = Column(DECIMAL(18, 2), default=0)  # 总卖出金额
    net_flow_usd = Column(DECIMAL(18, 2))  # 净流入(买入-卖出)
    transaction_count = Column(Integer, default=0)  # 交易笔数

    # 价格影响
    price_before = Column(DECIMAL(18, 8))  # 信号前价格
    price_current = Column(DECIMAL(18, 8))  # 当前价格
    price_change_pct = Column(Float)  # 价格变化百分比

    # 时间窗口
    signal_start_time = Column(DateTime, nullable=False)  # 信号开始时间
    signal_end_time = Column(DateTime, nullable=False)  # 信号结束时间
    timestamp = Column(DateTime, nullable=False, index=True)  # 信号生成时间

    # 关联交易
    related_tx_hashes = Column(String(2000))  # 关联的交易哈希,逗号分隔
    top_addresses = Column(String(500))  # 主要参与地址,逗号分隔

    # 状态
    is_active = Column(Boolean, default=True, index=True)  # 信号是否有效
    is_verified = Column(Boolean, default=False)  # 是否已验证(价格是否按预期移动)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        Index('idx_token_signal_time', 'token_symbol', 'signal_type', 'timestamp'),
        Index('idx_active_confidence', 'is_active', 'confidence_score'),
        Index('idx_blockchain_token', 'blockchain', 'token_symbol'),
    )

    def __repr__(self):
        return f"<SmartMoneySignal(token={self.token_symbol}, type={self.signal_type}, strength={self.signal_strength}, confidence={self.confidence_score}%)>"


class StrategyTradeRecord(Base):
    """策略交易记录表 - 存储策略执行的买入、平仓等交易信息"""
    __tablename__ = 'strategy_trade_records'

    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # 策略信息
    strategy_id = Column(BigInteger, nullable=False, index=True)  # 策略ID
    strategy_name = Column(String(100))  # 策略名称
    account_id = Column(Integer, nullable=False, index=True)  # 账户ID
    
    # 交易信息
    symbol = Column(String(20), nullable=False, index=True)  # 交易对
    action = Column(String(20), nullable=False, index=True)  # 交易动作: BUY(买入/开仓), SELL(平仓), CLOSE(平仓)
    direction = Column(String(10))  # 方向: long(做多), short(做空)
    position_side = Column(String(10))  # 持仓方向: LONG, SHORT
    
    # 价格和数量
    entry_price = Column(DECIMAL(18, 8))  # 开仓价格
    exit_price = Column(DECIMAL(18, 8))  # 平仓价格
    quantity = Column(DECIMAL(18, 8), nullable=False)  # 数量
    leverage = Column(Integer, default=1)  # 杠杆倍数
    
    # 金额信息
    margin = Column(DECIMAL(18, 8))  # 保证金
    total_value = Column(DECIMAL(18, 8))  # 总价值
    fee = Column(DECIMAL(18, 8))  # 手续费
    realized_pnl = Column(DECIMAL(18, 8))  # 已实现盈亏
    
    # 关联信息
    position_id = Column(Integer, index=True)  # 持仓ID
    order_id = Column(String(50), index=True)  # 订单ID
    signal_id = Column(Integer)  # 信号ID（关联strategy_hits表）
    
    # 交易原因
    reason = Column(String(200))  # 交易原因，如：策略信号、止损、止盈、趋势反转等
    
    # 时间信息
    trade_time = Column(DateTime, nullable=False, index=True)  # 交易时间
    
    created_at = Column(DateTime, default=datetime.now)  # 记录创建时间
    
    __table_args__ = (
        Index('idx_strategy_symbol_time', 'strategy_id', 'symbol', 'trade_time'),
        Index('idx_account_action_time', 'account_id', 'action', 'trade_time'),
        Index('idx_position_id', 'position_id'),
    )
    
    def __repr__(self):
        return f"<StrategyTradeRecord(strategy={self.strategy_name}, symbol={self.symbol}, action={self.action}, time={self.trade_time})>"


class StrategyTestRecord(Base):
    """策略测试交易记录表 - 存储策略测试过程中的买入、平仓等交易信息"""
    __tablename__ = 'strategy_test_records'

    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # 策略信息
    strategy_id = Column(BigInteger, nullable=False, index=True)  # 策略ID
    strategy_name = Column(String(100))  # 策略名称
    account_id = Column(Integer, nullable=False, index=True)  # 账户ID
    
    # 交易信息
    symbol = Column(String(20), nullable=False, index=True)  # 交易对
    action = Column(String(20), nullable=False, index=True)  # 交易动作: BUY(买入/开仓), SELL(平仓), CLOSE(平仓)
    direction = Column(String(10))  # 方向: long(做多), short(做空)
    position_side = Column(String(10))  # 持仓方向: LONG, SHORT
    
    # 价格和数量
    entry_price = Column(DECIMAL(18, 8))  # 开仓价格
    exit_price = Column(DECIMAL(18, 8))  # 平仓价格
    quantity = Column(DECIMAL(18, 8), nullable=False)  # 数量
    leverage = Column(Integer, default=1)  # 杠杆倍数
    
    # 金额信息
    margin = Column(DECIMAL(18, 8))  # 保证金
    total_value = Column(DECIMAL(18, 8))  # 总价值
    fee = Column(DECIMAL(18, 8))  # 手续费
    realized_pnl = Column(DECIMAL(18, 8))  # 已实现盈亏
    
    # 关联信息
    position_id = Column(Integer, index=True)  # 持仓ID
    order_id = Column(String(50), index=True)  # 订单ID
    signal_id = Column(Integer)  # 信号ID（关联strategy_hits表）
    
    # 交易原因
    reason = Column(String(200))  # 交易原因，如：策略信号、止损、止盈、趋势反转等
    
    # 时间信息
    trade_time = Column(DateTime, nullable=False, index=True)  # 交易时间
    
    created_at = Column(DateTime, default=datetime.now)  # 记录创建时间
    
    __table_args__ = (
        Index('idx_strategy_symbol_time', 'strategy_id', 'symbol', 'trade_time'),
        Index('idx_account_action_time', 'account_id', 'action', 'trade_time'),
        Index('idx_position_id', 'position_id'),
    )
    
    def __repr__(self):
        return f"<StrategyTestRecord(strategy={self.strategy_name}, symbol={self.symbol}, action={self.action}, time={self.trade_time})>"