# 多用户实盘交易架构改造方案

## 🚨 当前问题

### 1. 实盘交易引擎问题
**文件**: `app/trading/binance_futures_engine.py`

**当前实现**:
```python
def __init__(self, db_config: dict, api_key: str = None, api_secret: str = None):
    if api_key and api_secret:
        self.api_key = api_key
        self.api_secret = api_secret
    else:
        self._load_api_config()  # 从config.yaml读取固定密钥
```

**问题**:
- ❌ 使用配置文件中的固定API密钥
- ❌ 所有用户共用一个币安账户
- ❌ 无法实现多用户隔离
- ❌ JWT过期不影响（因为根本没验证）

---

### 2. Telegram通知器问题
**文件**: `app/services/trade_notifier.py`

**当前实现**:
```python
def __init__(self, config: Dict):
    telegram_config = config.get('notifications', {}).get('telegram', {})
    self.bot_token = telegram_config.get('bot_token', '')
    self.chat_id = str(telegram_config.get('chat_id', ''))  # 固定chat_id
```

**问题**:
- ❌ 使用配置文件中的固定chat_id
- ❌ 所有用户的通知发到同一个TG账户
- ❌ 无法为每个用户发送独立通知

---

## ✅ 解决方案

### 方案1：用户API密钥表（推荐）

#### 1.1 数据库表结构

```sql
-- ============================================================
-- 用户币安API密钥表
-- ============================================================
CREATE TABLE IF NOT EXISTS user_binance_keys (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL COMMENT '用户ID（关联users表）',

    -- API密钥（加密存储）
    api_key VARCHAR(255) NOT NULL COMMENT '币安API Key',
    api_secret_encrypted TEXT NOT NULL COMMENT '加密后的API Secret',

    -- API权限
    can_trade BOOLEAN DEFAULT TRUE COMMENT '是否有交易权限',
    can_withdraw BOOLEAN DEFAULT FALSE COMMENT '是否有提现权限（建议禁用）',

    -- IP白名单
    ip_whitelist TEXT COMMENT 'IP白名单（JSON数组）',

    -- 状态
    is_active BOOLEAN DEFAULT TRUE COMMENT '是否启用',
    is_verified BOOLEAN DEFAULT FALSE COMMENT '是否已验证（测试连接成功）',
    last_verified_at DATETIME COMMENT '最后验证时间',

    -- Telegram配置
    telegram_chat_id VARCHAR(100) COMMENT 'Telegram Chat ID',
    telegram_enabled BOOLEAN DEFAULT FALSE COMMENT '是否启用TG通知',

    -- 审计字段
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_user_id (user_id),
    INDEX idx_is_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户币安API密钥';

-- ============================================================
-- 用户表（如果不存在）
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    email VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,

    -- JWT相关
    last_login_at DATETIME,
    token_version INT DEFAULT 0 COMMENT 'Token版本，用于强制过期',

    -- 状态
    is_active BOOLEAN DEFAULT TRUE,
    is_verified BOOLEAN DEFAULT FALSE,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 1.2 API密钥加密存储

**文件**: `app/utils/encryption.py` (新建)

```python
"""
API密钥加密工具
使用 Fernet (AES-128) 对称加密
"""
from cryptography.fernet import Fernet
import os
import base64

class KeyEncryption:
    """API密钥加密/解密"""

    def __init__(self):
        # 从环境变量或配置文件读取加密密钥
        # ⚠️ 这个密钥必须保密，不能提交到Git
        encryption_key = os.getenv('API_ENCRYPTION_KEY')

        if not encryption_key:
            raise ValueError("环境变量 API_ENCRYPTION_KEY 未设置")

        self.cipher = Fernet(encryption_key.encode())

    def encrypt(self, plaintext: str) -> str:
        """加密API Secret"""
        encrypted = self.cipher.encrypt(plaintext.encode())
        return base64.b64encode(encrypted).decode()

    def decrypt(self, ciphertext: str) -> str:
        """解密API Secret"""
        encrypted = base64.b64decode(ciphertext.encode())
        decrypted = self.cipher.decrypt(encrypted)
        return decrypted.decode()

    @staticmethod
    def generate_encryption_key() -> str:
        """生成新的加密密钥"""
        return Fernet.generate_key().decode()

# 全局实例
_encryptor = None

def get_encryptor():
    global _encryptor
    if _encryptor is None:
        _encryptor = KeyEncryption()
    return _encryptor
```

**首次部署时生成加密密钥**:
```python
# scripts/generate_encryption_key.py
from app.utils.encryption import KeyEncryption

key = KeyEncryption.generate_encryption_key()
print(f"将此密钥添加到环境变量 API_ENCRYPTION_KEY:")
print(f"export API_ENCRYPTION_KEY='{key}'")
print(f"\n或添加到 .env 文件:")
print(f"API_ENCRYPTION_KEY={key}")
```

#### 1.3 改造 BinanceFuturesEngine

**文件**: `app/trading/binance_futures_engine.py`

```python
class BinanceFuturesEngine:
    """币安实盘合约交易引擎（支持多用户）"""

    def __init__(self, db_config: dict, user_id: int = None):
        """
        初始化币安实盘合约交易引擎

        Args:
            db_config: 数据库配置
            user_id: 用户ID（如果提供，则从数据库加载该用户的API密钥）
        """
        self.db_config = db_config
        self.user_id = user_id
        self.connection = None
        self._is_first_connection = True

        # 如果指定了user_id，从数据库加载API密钥
        if user_id:
            self._load_user_api_keys(user_id)
        else:
            # 向后兼容：从配置文件加载（用于系统级操作）
            self._load_api_config()

        # 验证API配置
        if not self.api_key or not self.api_secret:
            raise ValueError(f"用户 {user_id} 的币安API密钥未配置")

        # 连接数据库
        self._connect_db()

        # 加载交易对信息
        self._load_exchange_info()

        logger.info(f"币安实盘合约交易引擎初始化完成 (user_id={user_id})")

    def _load_user_api_keys(self, user_id: int):
        """从数据库加载用户API密钥"""
        try:
            connection = pymysql.connect(**self.db_config)
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT api_key, api_secret_encrypted,
                           telegram_chat_id, telegram_enabled
                    FROM user_binance_keys
                    WHERE user_id = %s AND is_active = 1
                """, (user_id,))

                result = cursor.fetchone()

                if not result:
                    raise ValueError(f"用户 {user_id} 未配置币安API密钥")

                # 解密API Secret
                from app.utils.encryption import get_encryptor
                encryptor = get_encryptor()

                self.api_key = result['api_key']
                self.api_secret = encryptor.decrypt(result['api_secret_encrypted'])
                self.telegram_chat_id = result['telegram_chat_id']
                self.telegram_enabled = result['telegram_enabled']

                logger.info(f"已加载用户 {user_id} 的API密钥")

            connection.close()

        except Exception as e:
            logger.error(f"加载用户API密钥失败: {e}")
            raise

    def open_position(
        self,
        symbol: str,
        position_side: str,
        quantity: Decimal,
        leverage: int = 1,
        limit_price: Optional[Decimal] = None,
        stop_loss_pct: Optional[Decimal] = None,
        take_profit_pct: Optional[Decimal] = None,
        source: str = 'api',
        signal_id: Optional[int] = None,
        strategy_id: Optional[int] = None
    ) -> Dict:
        """
        开仓（使用当前用户的API密钥）

        注意：不再需要 account_id 参数，因为 user_id 已在初始化时指定
        """
        # ... 实现逻辑保持不变，使用 self.user_id ...

        # 发送Telegram通知（使用用户自己的chat_id）
        if self.telegram_enabled and self.telegram_chat_id:
            self._send_user_telegram_notification(
                chat_id=self.telegram_chat_id,
                message=f"🟢 开仓成功\n交易对: {symbol}\n..."
            )
```

#### 1.4 改造 TradeNotifier

**文件**: `app/services/trade_notifier.py`

```python
class TradeNotifier:
    """实盘交易通知器（支持多用户）"""

    def __init__(self, config: Dict, user_telegram_config: Dict = None):
        """
        初始化通知器

        Args:
            config: 系统配置（包含bot_token）
            user_telegram_config: 用户TG配置 {'chat_id': '123', 'enabled': True}
        """
        self.config = config
        telegram_config = config.get('notifications', {}).get('telegram', {})

        # Bot Token从系统配置读取（所有用户共用一个bot）
        self.bot_token = telegram_config.get('bot_token', '')

        # 用户的chat_id从参数读取
        if user_telegram_config:
            self.chat_id = str(user_telegram_config.get('chat_id', ''))
            self.enabled = user_telegram_config.get('enabled', False)
        else:
            # 向后兼容：从配置文件读取
            self.chat_id = str(telegram_config.get('chat_id', ''))
            self.enabled = telegram_config.get('enabled', False)

        # ... 其他初始化代码 ...
```

#### 1.5 改造限价单执行器

**文件**: `app/services/futures_limit_order_executor.py`

```python
class FuturesLimitOrderExecutor:
    """合约限价单自动执行器（支持多用户）"""

    def __init__(self, db_config: Dict, trading_engine, price_cache_service=None):
        """
        注意：不再传入 live_engine，而是在执行时根据 user_id 动态创建
        """
        self.db_config = db_config
        self.trading_engine = trading_engine
        self.price_cache_service = price_cache_service
        # 移除: self.live_engine = live_engine

    async def execute_pending_orders(self):
        """执行待处理订单"""
        # ... 查询订单 ...

        for order in pending_orders:
            # 获取订单关联的用户ID
            user_id = order.get('user_id')
            if not user_id:
                logger.error(f"订单 {order['id']} 没有关联用户ID")
                continue

            # 根据用户ID创建实盘引擎实例
            try:
                live_engine = BinanceFuturesEngine(
                    db_config=self.db_config,
                    user_id=user_id
                )

                # 执行实盘同步
                live_result = live_engine.open_position(...)

                # 发送通知（会自动使用该用户的TG配置）

            except Exception as e:
                logger.error(f"用户 {user_id} 实盘同步失败: {e}")
```

---

### 方案2：JWT Token验证（与方案1配合）

#### 2.1 API路由添加JWT验证

**文件**: `app/api/futures_routes.py`

```python
from flask import request, jsonify
from functools import wraps
import jwt

def token_required(f):
    """JWT Token验证装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')

        if not token:
            return jsonify({'error': '缺少认证Token'}), 401

        try:
            # 解析Token
            payload = jwt.decode(
                token,
                app.config['JWT_SECRET_KEY'],
                algorithms=['HS256']
            )

            user_id = payload['user_id']
            token_version = payload.get('token_version', 0)

            # 验证Token版本（用于强制过期）
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT token_version FROM users WHERE id = %s",
                    (user_id,)
                )
                user = cursor.fetchone()

                if not user or user['token_version'] != token_version:
                    return jsonify({'error': 'Token已过期，请重新登录'}), 401

            # 将user_id注入到请求上下文
            request.user_id = user_id

        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token已过期'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': '无效的Token'}), 401

        return f(*args, **kwargs)

    return decorated

# 在所有实盘交易API上添加装饰器
@app.route('/api/futures/live/open', methods=['POST'])
@token_required
def live_open_position():
    """实盘开仓（需要JWT认证）"""
    user_id = request.user_id  # 从JWT获取

    # 使用用户的API密钥创建引擎
    engine = BinanceFuturesEngine(db_config=db_config, user_id=user_id)

    # ... 执行开仓 ...
```

#### 2.2 后台任务用户隔离

**问题**: 限价单执行器、策略引擎等后台任务如何知道是哪个用户？

**解决方案**: 在订单/持仓表中添加 `user_id` 字段

```sql
-- 添加user_id到相关表
ALTER TABLE futures_orders ADD COLUMN user_id BIGINT COMMENT '用户ID';
ALTER TABLE futures_positions ADD COLUMN user_id BIGINT COMMENT '用户ID';
ALTER TABLE trading_strategies ADD COLUMN user_id BIGINT COMMENT '用户ID';

-- 添加索引
CREATE INDEX idx_user_id ON futures_orders(user_id);
CREATE INDEX idx_user_id ON futures_positions(user_id);
CREATE INDEX idx_user_id ON trading_strategies(user_id);
```

---

## 🔄 迁移步骤

### 步骤1: 数据库准备
```bash
# 1. 创建用户表和API密钥表
mysql -u admin -p binance-data < scripts/migrations/026_create_user_tables.sql

# 2. 为现有表添加user_id
mysql -u admin -p binance-data < scripts/migrations/027_add_user_id_to_tables.sql

# 3. 为现有数据设置默认user_id=1（管理员）
mysql -u admin -p binance-data -e "
UPDATE futures_orders SET user_id = 1 WHERE user_id IS NULL;
UPDATE futures_positions SET user_id = 1 WHERE user_id IS NULL;
UPDATE trading_strategies SET user_id = 1 WHERE user_id IS NULL;
"
```

### 步骤2: 生成加密密钥
```bash
python3 scripts/generate_encryption_key.py
# 将输出的密钥添加到环境变量
export API_ENCRYPTION_KEY='your-generated-key-here'
```

### 步骤3: 迁移现有配置
```bash
# 将config.yaml中的API密钥迁移到数据库
python3 scripts/migrate_api_keys_to_db.py
```

### 步骤4: 改造代码
```bash
# 1. 添加加密工具
# 2. 改造 BinanceFuturesEngine
# 3. 改造 TradeNotifier
# 4. 改造限价单执行器
# 5. 添加JWT验证装饰器
```

### 步骤5: 测试
```bash
# 1. 测试加密/解密
# 2. 测试多用户API密钥加载
# 3. 测试JWT验证
# 4. 测试实盘交易隔离
# 5. 测试TG通知隔离
```

---

## 📋 检查清单

- [ ] 创建 `user_binance_keys` 表
- [ ] 创建 `users` 表
- [ ] 实现API密钥加密/解密
- [ ] 改造 `BinanceFuturesEngine` 支持多用户
- [ ] 改造 `TradeNotifier` 支持多用户
- [ ] 改造限价单执行器
- [ ] 为所有表添加 `user_id` 字段
- [ ] 实现JWT验证装饰器
- [ ] 实现Token强制过期机制
- [ ] 迁移现有数据
- [ ] 编写单元测试
- [ ] 编写集成测试
- [ ] 更新API文档

---

## ⚠️ 安全注意事项

1. **加密密钥管理**
   - `API_ENCRYPTION_KEY` 绝对不能提交到Git
   - 部署时通过环境变量注入
   - 定期轮换加密密钥

2. **API密钥权限**
   - 建议用户只开启交易权限
   - 禁用提现权限
   - 设置IP白名单

3. **JWT Token**
   - 设置合理的过期时间（如1小时）
   - 支持刷新Token机制
   - 实现Token版本控制（强制过期）

4. **审计日志**
   - 记录所有API密钥的增删改
   - 记录所有实盘交易操作
   - 记录用户登录/登出

---

## 🎯 最终架构

```
用户1 登录
  ↓
生成 JWT Token (user_id=1, exp=1h)
  ↓
前端携带 Token 调用 /api/futures/live/open
  ↓
后端验证 Token → 提取 user_id=1
  ↓
从 user_binance_keys 表加载 user_id=1 的API密钥
  ↓
创建 BinanceFuturesEngine(user_id=1)
  ↓
使用该用户的API密钥调用币安API
  ↓
使用该用户的 telegram_chat_id 发送通知

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

后台任务（限价单执行器）
  ↓
扫描 futures_orders 表 WHERE status='PENDING'
  ↓
对于每个订单，获取 order.user_id
  ↓
创建 BinanceFuturesEngine(user_id=order.user_id)
  ↓
使用该用户的API密钥执行交易
  ↓
使用该用户的 telegram_chat_id 发送通知
```

---

**总结**: 当前系统不支持多用户，所有实盘交易都使用同一个账户。JWT Token过期不会影响交易，因为根本没验证。需要完整的架构改造才能实现真正的多用户隔离。
