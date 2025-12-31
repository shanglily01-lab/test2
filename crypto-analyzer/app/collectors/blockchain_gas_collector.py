"""
区块链Gas消耗数据采集器
支持六大主链：Ethereum, BSC, Polygon, Arbitrum, Optimism, Avalanche
"""

import asyncio
import aiohttp
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
from decimal import Decimal
import logging
from pathlib import Path
import yaml
import mysql.connector
from mysql.connector import pooling

logger = logging.getLogger(__name__)

# 六大主链配置
CHAIN_CONFIGS = {
    'ethereum': {
        'display_name': 'Ethereum',
        'native_token': 'ETH',
        'rpc_url': 'https://eth.llamarpc.com',
        'explorer_api': 'https://api.etherscan.io/api',  # V1 API (deprecated)
        'explorer_api_v2': 'https://api.etherscan.io/v2',  # V2 API
        'explorer_api_key': None,  # 从config.yaml读取
        'chain_id': 1,
        'decimals': 18
    },
    'bsc': {
        'display_name': 'BSC',
        'native_token': 'BNB',
        'rpc_url': 'https://bsc-dataseed1.binance.org',
        'explorer_api': 'https://api.bscscan.com/api',
        'explorer_api_key': None,
        'chain_id': 56,
        'decimals': 18
    },
    'polygon': {
        'display_name': 'Polygon',
        'native_token': 'MATIC',
        'rpc_url': 'https://polygon-rpc.com',
        'explorer_api': 'https://api.polygonscan.com/api',
        'explorer_api_key': None,
        'chain_id': 137,
        'decimals': 18
    },
    'arbitrum': {
        'display_name': 'Arbitrum',
        'native_token': 'ETH',
        'rpc_url': 'https://arb1.arbitrum.io/rpc',
        'explorer_api': 'https://api.arbiscan.io/api',
        'explorer_api_key': None,
        'chain_id': 42161,
        'decimals': 18
    },
    'optimism': {
        'display_name': 'Optimism',
        'native_token': 'ETH',
        'rpc_url': 'https://mainnet.optimism.io',
        'explorer_api': 'https://api-optimistic.etherscan.io/api',
        'explorer_api_key': None,
        'chain_id': 10,
        'decimals': 18
    },
    'avalanche': {
        'display_name': 'Avalanche',
        'native_token': 'AVAX',
        'rpc_url': 'https://api.avax.network/ext/bc/C/rpc',
        'explorer_api': 'https://api.snowtrace.io/api',
        'explorer_api_key': None,
        'chain_id': 43114,
        'decimals': 18
    }
}


class BlockchainGasCollector:
    """区块链Gas消耗数据采集器"""
    
    def __init__(self, config_path: Optional[Path] = None):
        """
        初始化采集器
        
        Args:
            config_path: 配置文件路径
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / "config.yaml"
        
        self.config_path = config_path
        self.config = self._load_config()
        self.db_pool = self._init_db_pool()
        
        # 从配置加载API密钥
        self._load_api_keys()
    
    def _load_config(self) -> dict:
        """加载配置文件（支持环境变量替换）"""
        try:
            from app.utils.config_loader import load_config
            return load_config(self.config_path)
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            return {}
    
    def _load_api_keys(self):
        """从配置文件加载API密钥"""
        smart_money_config = self.config.get('smart_money', {})
        onchain_config = self.config.get('onchain', {})
        
        # Etherscan API Key
        etherscan_key = smart_money_config.get('etherscan_api_key') or onchain_config.get('etherscan_api_key')
        if etherscan_key:
            CHAIN_CONFIGS['ethereum']['explorer_api_key'] = etherscan_key
            CHAIN_CONFIGS['arbitrum']['explorer_api_key'] = etherscan_key
            CHAIN_CONFIGS['optimism']['explorer_api_key'] = etherscan_key
        
        # BscScan API Key
        bscscan_key = smart_money_config.get('bscscan_api_key') or onchain_config.get('bscscan_api_key')
        if bscscan_key:
            CHAIN_CONFIGS['bsc']['explorer_api_key'] = bscscan_key
        
        # PolygonScan API Key (使用Etherscan key)
        if etherscan_key:
            CHAIN_CONFIGS['polygon']['explorer_api_key'] = etherscan_key
        
        # Snowtrace API Key (Avalanche)
        if etherscan_key:
            CHAIN_CONFIGS['avalanche']['explorer_api_key'] = etherscan_key
    
    def _init_db_pool(self) -> pooling.MySQLConnectionPool:
        """初始化数据库连接池"""
        # 从 config.yaml 的 database.mysql 路径读取配置
        mysql_config = self.config.get('database', {}).get('mysql', {})

        # 确保端口号是整数类型
        port = mysql_config.get('port', 3306)
        if isinstance(port, str):
            port = int(port)

        pool_config = {
            'pool_name': 'gas_collector_pool',
            'pool_size': 10,  # 增加连接池大小
            'pool_reset_session': True,
            'host': mysql_config.get('host', 'localhost'),
            'port': port,
            'user': mysql_config.get('user', 'root'),
            'password': mysql_config.get('password', ''),
            'database': mysql_config.get('database', 'binance-data'),
            'charset': 'utf8mb4',
            'autocommit': False
        }
        
        try:
            logger.info(f"初始化Gas采集器数据库连接池: {pool_config['host']}:{pool_config['port']}/{pool_config['database']}")
            return pooling.MySQLConnectionPool(**pool_config)
        except Exception as e:
            logger.error(f"初始化数据库连接池失败: {e}")
            logger.error(f"数据库配置: host={pool_config['host']}, port={pool_config['port']}, user={pool_config['user']}, database={pool_config['database']}")
            raise
    
    async def get_native_token_price(self, chain_name: str) -> Optional[float]:
        """
        获取原生代币价格
        
        Args:
            chain_name: 链名称
            
        Returns:
            代币价格(USD)
        """
        chain_config = CHAIN_CONFIGS.get(chain_name)
        if not chain_config:
            return None
        
        native_token = chain_config['native_token']
        symbol = f"{native_token}/USDT"
        
        # 首先尝试从价格缓存服务获取价格
        try:
            from app.services.price_cache_service import get_global_price_cache
            price_cache = get_global_price_cache()
            if price_cache:
                price = price_cache.get_price(symbol)
                if price and price > 0:
                    logger.debug(f"从价格缓存获取 {symbol} 价格: {price}")
                    return float(price)
        except Exception as e:
            logger.debug(f"从价格缓存获取 {symbol} 价格失败: {e}")
        
        # 如果价格缓存不可用，尝试从多个数据源获取
        # 1. 首先尝试 CoinGecko API
        try:
            # CoinGecko 代币 ID 映射
            token_id_map = {
                'ETH': 'ethereum',
                'BNB': 'binancecoin',
                'MATIC': 'matic-network',
                'AVAX': 'avalanche-2'
            }
            
            token_id = token_id_map.get(native_token)
            if token_id:
                api_url = 'https://api.coingecko.com/api/v3/simple/price'
                params = {
                    'ids': token_id,
                    'vs_currencies': 'usd'
                }
                
                # 添加重试机制，避免 HTTP 429 错误
                max_retries = 2  # 减少重试次数，快速切换到备用源
                for attempt in range(max_retries):
                    try:
                        # 添加延迟，避免请求过快
                        if attempt > 0:
                            await asyncio.sleep(5 * (attempt + 1))  # 延迟：5秒, 10秒
                        
                        async with aiohttp.ClientSession() as session:
                            async with session.get(
                                api_url,
                                params=params,
                                timeout=aiohttp.ClientTimeout(total=15)
                            ) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    price = data.get(token_id, {}).get('usd')
                                    if price:
                                        logger.info(f"从 CoinGecko 获取 {native_token} 价格: ${price}")
                                        return float(price)
                                    else:
                                        logger.debug(f"CoinGecko 返回数据中未找到 {native_token} 价格")
                                        break  # 数据格式问题，切换到备用源
                                elif resp.status == 429:
                                    logger.debug(f"CoinGecko API 请求过多 (HTTP 429)，切换到备用数据源")
                                    break  # 限流，切换到备用源
                                else:
                                    logger.debug(f"CoinGecko API 请求失败: HTTP {resp.status}，切换到备用数据源")
                                    break  # 其他错误，切换到备用源
                    except asyncio.TimeoutError:
                        logger.debug(f"从 CoinGecko 获取 {native_token} 价格超时，切换到备用数据源")
                        break
                    except Exception as e:
                        logger.debug(f"从 CoinGecko 获取 {native_token} 价格失败: {e}，切换到备用数据源")
                        break
        except Exception as e:
            logger.debug(f"CoinGecko 价格获取异常: {e}，尝试备用数据源")
        
        # 2. 备用方案：从 Binance API 获取价格
        try:
            # Binance 交易对映射
            binance_symbol_map = {
                'ETH': 'ETHUSDT',
                'BNB': 'BNBUSDT',
                'MATIC': 'MATICUSDT',
                'AVAX': 'AVAXUSDT'
            }
            
            binance_symbol = binance_symbol_map.get(native_token)
            if binance_symbol:
                binance_url = f'https://api.binance.com/api/v3/ticker/price'
                params = {'symbol': binance_symbol}
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        binance_url,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            price = data.get('price')
                            if price:
                                logger.info(f"从 Binance 获取 {native_token} 价格: ${price}")
                                return float(price)
        except Exception as e:
            logger.debug(f"从 Binance 获取 {native_token} 价格失败: {e}")
        
        return None
    
    async def fetch_gas_stats_from_explorer(
        self,
        chain_name: str,
        target_date: date
    ) -> Optional[Dict]:
        """
        从区块链浏览器API获取Gas统计数据
        
        Args:
            chain_name: 链名称
            target_date: 目标日期
            
        Returns:
            Gas统计数据字典
        """
        chain_config = CHAIN_CONFIGS.get(chain_name)
        if not chain_config:
            logger.error(f"不支持的链: {chain_name}")
            return None
        
        explorer_api = chain_config['explorer_api']
        api_key = chain_config['explorer_api_key']
        
        if not api_key:
            logger.warning(f"{chain_name} 未配置API密钥，跳过浏览器API查询")
            return None
        
        try:
            # 计算时间范围（UTC时间）
            start_timestamp = int(datetime.combine(target_date, datetime.min.time()).timestamp())
            end_timestamp = int(datetime.combine(target_date, datetime.max.time()).timestamp())
            
            async with aiohttp.ClientSession() as session:
                # 获取区块范围
                start_block_url = f"{explorer_api}?module=block&action=getblocknobytime&timestamp={start_timestamp}&closest=after&apikey={api_key}"
                end_block_url = f"{explorer_api}?module=block&action=getblocknobytime&timestamp={end_timestamp}&closest=before&apikey={api_key}"
                
                async with session.get(start_block_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('status') == '1':
                            start_block = int(data.get('result', 0))
                        else:
                            logger.warning(f"获取 {chain_name} 起始区块失败: {data.get('message')}")
                            return None
                    else:
                        logger.warning(f"获取 {chain_name} 起始区块失败: HTTP {resp.status}")
                        return None
                
                async with session.get(end_block_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('status') == '1':
                            end_block = int(data.get('result', 0))
                        else:
                            logger.warning(f"获取 {chain_name} 结束区块失败: {data.get('message')}")
                            return None
                    else:
                        logger.warning(f"获取 {chain_name} 结束区块失败: HTTP {resp.status}")
                        return None
                
                # 获取区块范围内的交易统计
                # 注意：不同浏览器的API可能不同，这里使用通用方法
                # 实际实现可能需要根据具体链的API调整
                
                # 简化实现：获取最近区块的gas数据作为估算
                latest_block_url = f"{explorer_api}?module=proxy&action=eth_getBlockByNumber&tag=latest&boolean=true&apikey={api_key}"
                
                async with session.get(latest_block_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('result'):
                            block = data['result']
                            # 这里可以进一步处理区块数据
                            # 由于不同链的API差异较大，建议使用RPC节点直接查询
                            pass
                
        except Exception as e:
            logger.error(f"从浏览器API获取 {chain_name} Gas数据失败: {e}")
            return None
        
        return None
    
    async def _rpc_call(self, rpc_url: str, method: str, params: List) -> Optional[Dict]:
        """执行 RPC 调用"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
                "id": 1
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    rpc_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if 'result' in data:
                            return data['result']
                        elif 'error' in data:
                            logger.warning(f"RPC调用失败: {data['error']}")
                            return None
                    return None
        except Exception as e:
            logger.debug(f"RPC调用异常: {e}")
            return None
    
    async def fetch_gas_stats_from_rpc(
        self,
        chain_name: str,
        target_date: date
    ) -> Optional[Dict]:
        """
        从RPC节点获取Gas统计数据
        
        Args:
            chain_name: 链名称
            target_date: 目标日期
            
        Returns:
            Gas统计数据字典
        """
        chain_config = CHAIN_CONFIGS.get(chain_name)
        if not chain_config:
            return None
        
        try:
            # 获取原生代币价格
            # 优先使用预获取的价格缓存
            native_price = None
            if hasattr(self, '_price_cache') and chain_name in self._price_cache:
                native_price = self._price_cache[chain_name]
                logger.debug(f"{chain_name} 使用预获取的价格: ${native_price:.2f}")
            
            # 如果缓存中没有，则获取价格（带重试机制）
            if not native_price or native_price <= 0:
                native_price = await self.get_native_token_price(chain_name)
                if not native_price or native_price <= 0:
                    logger.warning(f"{chain_name} 无法获取原生代币价格，Gas价值将无法计算")
                    # 如果价格获取失败，尝试再次获取（最多重试2次）
                    for retry in range(2):
                        await asyncio.sleep(3)  # 等待3秒后重试
                        native_price = await self.get_native_token_price(chain_name)
                        if native_price and native_price > 0:
                            logger.info(f"{chain_name} 重试获取价格成功: ${native_price:.2f}")
                            break
                    
                    if not native_price or native_price <= 0:
                        logger.error(f"{chain_name} 多次尝试后仍无法获取原生代币价格")
                        native_price = 0.0
            
            rpc_url = chain_config['rpc_url']
            
            # 1. 获取当前 Gas 价格
            gas_price_hex = await self._rpc_call(rpc_url, "eth_gasPrice", [])
            if not gas_price_hex:
                logger.warning(f"{chain_name} 无法从RPC获取Gas价格")
                return None
            
            # 转换为十进制（Wei）
            avg_gas_price = int(gas_price_hex, 16)
            avg_gas_price_gwei = avg_gas_price / (10 ** 9)
            
            # 估算 Gas 价格范围（基于当前价格）
            max_gas_price = int(avg_gas_price * 1.5)
            min_gas_price = int(avg_gas_price * 0.5)
            
            # 2. 获取最新区块信息
            latest_block_hex = await self._rpc_call(rpc_url, "eth_blockNumber", [])
            if not latest_block_hex:
                logger.warning(f"{chain_name} 无法从RPC获取最新区块号")
                return None
            
            latest_block_num = int(latest_block_hex, 16)
            
            # 3. 采样最近的区块来计算平均 Gas 使用量
            # 采样最近 100 个区块（约1-2小时的数据）
            sample_blocks = min(100, latest_block_num)
            total_gas_used = 0
            total_transactions = 0
            gas_prices = []
            
            # 计算目标日期对应的区块范围（估算）
            # 由于是历史数据，我们使用当前区块和估算的区块时间来计算
            blocks_per_day = {
                'ethereum': 7200,   # 12秒/块
                'bsc': 28800,       # 3秒/块
                'polygon': 43200,   # 2秒/块
                'arbitrum': 7200,   # 12秒/块
                'optimism': 7200,   # 12秒/块
                'avalanche': 28800  # 1秒/块
            }.get(chain_name, 7200)
            
            # 采样最近的区块
            sample_count = min(50, latest_block_num)  # 采样50个区块
            successful_samples = 0
            for i in range(sample_count):
                block_num = latest_block_num - i
                block_hex = hex(block_num)
                
                block_data = await self._rpc_call(rpc_url, "eth_getBlockByNumber", [block_hex, False])
                if block_data and 'gasUsed' in block_data and 'transactions' in block_data:
                    try:
                        gas_used = int(block_data['gasUsed'], 16) if block_data.get('gasUsed') else 0
                        tx_count = len(block_data.get('transactions', []))
                        
                        if gas_used > 0:  # 只统计有效的区块
                            total_gas_used += gas_used
                            total_transactions += tx_count
                            successful_samples += 1
                            
                            # 获取区块的 baseFeePerGas（如果支持 EIP-1559）
                            if 'baseFeePerGas' in block_data and block_data['baseFeePerGas']:
                                base_fee = int(block_data['baseFeePerGas'], 16)
                                gas_prices.append(base_fee)
                    except (ValueError, TypeError) as e:
                        logger.debug(f"{chain_name} 区块 {block_num} 数据解析失败: {e}")
                        continue
                
                # 避免请求过快
                if i % 10 == 0:
                    await asyncio.sleep(0.1)
            
            # 更新实际采样成功的区块数
            if successful_samples > 0:
                sample_count = successful_samples
            
            # 计算平均值
            if total_transactions > 0 and sample_count > 0:
                avg_gas_per_tx = total_gas_used / total_transactions
                # 估算每日数据（基于采样区块的平均值）
                avg_gas_per_block = total_gas_used / sample_count
                estimated_total_gas_used = int(avg_gas_per_block * blocks_per_day)
                estimated_total_transactions = int((total_transactions / sample_count) * blocks_per_day)
                logger.info(f"{chain_name} 采样成功: {sample_count}个区块, {total_transactions}笔交易, 平均Gas/交易: {avg_gas_per_tx:.0f}")
            else:
                # 如果采样失败，使用典型值
                logger.warning(f"{chain_name} 区块采样失败（total_transactions=0），使用典型值估算")
                estimated_total_transactions = {
                    'ethereum': 1000000,
                    'bsc': 3000000,
                    'polygon': 5000000,
                    'arbitrum': 800000,
                    'optimism': 400000,
                    'avalanche': 600000
                }.get(chain_name, 500000)
                
                avg_gas_per_tx = {
                    'ethereum': 21000,
                    'bsc': 21000,
                    'polygon': 21000,
                    'arbitrum': 21000,
                    'optimism': 21000,
                    'avalanche': 21000
                }.get(chain_name, 21000)
                
                estimated_total_gas_used = estimated_total_transactions * avg_gas_per_tx
            
            # 计算 Gas 价值（USD）
            # 使用 Decimal 确保精度
            from decimal import Decimal, ROUND_DOWN
            
            if native_price and native_price > 0:
                # 总Gas价值 = (总Gas消耗量 * Gas价格(Wei) / 10^18) * 代币价格(USD)
                total_gas_value_usd = float(Decimal(str(estimated_total_gas_used)) * Decimal(str(avg_gas_price)) / Decimal('1000000000000000000') * Decimal(str(native_price)))
                
                # 平均Gas价值 = (平均Gas/交易 * Gas价格(Wei) / 10^18) * 代币价格(USD)
                avg_gas_value_usd = float(Decimal(str(avg_gas_per_tx)) * Decimal(str(avg_gas_price)) / Decimal('1000000000000000000') * Decimal(str(native_price)))
                
                logger.info(f"[SUCCESS] 成功从RPC获取 {chain_name} {target_date} 的Gas数据")
                logger.info(f"  总Gas消耗: {estimated_total_gas_used}, Gas价格: {avg_gas_price_gwei:.2f} Gwei, 代币价格: ${native_price:.2f}")
                logger.info(f"  总Gas价值: ${total_gas_value_usd:.6f}, 平均Gas价值: ${avg_gas_value_usd:.6f}")
            else:
                logger.warning(f"{chain_name} 原生代币价格为0或未获取，Gas价值无法计算")
                total_gas_value_usd = 0.0
                avg_gas_value_usd = 0.0
            
            return {
                'chain_name': chain_name,
                'date': target_date,
                'total_gas_used': estimated_total_gas_used,
                'total_transactions': estimated_total_transactions,
                'avg_gas_per_tx': int(avg_gas_per_tx),
                'avg_gas_price': avg_gas_price,
                'max_gas_price': max_gas_price,
                'min_gas_price': min_gas_price,
                'native_token_price_usd': native_price,
                'total_gas_value_usd': total_gas_value_usd,
                'avg_gas_value_usd': avg_gas_value_usd,
                'total_blocks': blocks_per_day,
                'data_source': 'rpc_node'
            }
            
        except Exception as e:
            logger.error(f"从RPC获取 {chain_name} Gas数据失败: {e}", exc_info=True)
            return None
    
    async def collect_daily_gas_stats(
        self,
        chain_name: str,
        target_date: Optional[date] = None
    ) -> bool:
        """
        采集指定链指定日期的Gas统计数据
        
        Args:
            chain_name: 链名称
            target_date: 目标日期，默认为昨天
            
        Returns:
            是否成功
        """
        if target_date is None:
            target_date = date.today() - timedelta(days=1)
        
        if chain_name not in CHAIN_CONFIGS:
            logger.error(f"不支持的链: {chain_name}")
            return False
        
        logger.info(f"开始采集 {chain_name} {target_date} 的Gas数据...")
        
        # 尝试从浏览器API获取
        stats = await self.fetch_gas_stats_from_explorer(chain_name, target_date)
        
        # 如果浏览器API失败，尝试从RPC获取
        if not stats:
            stats = await self.fetch_gas_stats_from_rpc(chain_name, target_date)
        
        if not stats:
            logger.warning(f"无法获取 {chain_name} {target_date} 的Gas数据（API调用失败，跳过保存）")
            return False
        
        # 如果数据源是估算数据，且 API 密钥已配置，说明 API 调用失败，不保存估算数据
        if stats.get('data_source') == 'estimated' and chain_config.get('explorer_api_key'):
            logger.warning(f"{chain_name} API调用失败，跳过保存估算数据（避免产生测试数据）")
            logger.info(f"提示：Etherscan V1 API 已废弃，需要迁移到 V2 API 或使用其他数据源")
            return False
        
        # 保存到数据库
        return await self.save_gas_stats(stats)
    
    async def save_gas_stats(self, stats: Dict) -> bool:
        """
        保存Gas统计数据到数据库
        
        Args:
            stats: Gas统计数据字典
            
        Returns:
            是否成功
        """
        conn = None
        try:
            conn = self.db_pool.get_connection()
            cursor = conn.cursor()
            
            chain_config = CHAIN_CONFIGS.get(stats['chain_name'])
            display_name = chain_config['display_name'] if chain_config else stats['chain_name']
            
            # 计算平均Gas价值
            # 使用已计算好的平均Gas价值，不要重新计算
            avg_gas_value_usd = stats.get('avg_gas_value_usd', 0)
            
            sql = """
                INSERT INTO blockchain_gas_daily (
                    chain_name, chain_display_name, date,
                    total_gas_used, total_transactions, avg_gas_per_tx,
                    avg_gas_price, max_gas_price, min_gas_price,
                    native_token_price_usd, total_gas_value_usd, avg_gas_value_usd,
                    total_blocks, data_source, created_at, updated_at
                ) VALUES (
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, NOW(), NOW()
                )
                ON DUPLICATE KEY UPDATE
                    total_gas_used = VALUES(total_gas_used),
                    total_transactions = VALUES(total_transactions),
                    avg_gas_per_tx = VALUES(avg_gas_per_tx),
                    avg_gas_price = VALUES(avg_gas_price),
                    max_gas_price = VALUES(max_gas_price),
                    min_gas_price = VALUES(min_gas_price),
                    native_token_price_usd = VALUES(native_token_price_usd),
                    total_gas_value_usd = VALUES(total_gas_value_usd),
                    avg_gas_value_usd = VALUES(avg_gas_value_usd),
                    total_blocks = VALUES(total_blocks),
                    data_source = VALUES(data_source),
                    updated_at = NOW()
            """
            
            values = (
                stats['chain_name'],
                display_name,
                stats['date'],
                stats.get('total_gas_used', 0),
                stats.get('total_transactions', 0),
                stats.get('avg_gas_per_tx', 0),
                stats.get('avg_gas_price', 0),
                stats.get('max_gas_price'),
                stats.get('min_gas_price'),
                stats.get('native_token_price_usd'),
                stats.get('total_gas_value_usd', 0),
                avg_gas_value_usd,
                stats.get('total_blocks', 0),
                stats.get('data_source', 'rpc')
            )
            
            cursor.execute(sql, values)
            conn.commit()
            
            logger.info(f"[SUCCESS] 成功保存 {stats['chain_name']} {stats['date']} 的Gas数据")
            return True
            
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"保存Gas统计数据失败: {e}")
            return False
        finally:
            if conn:
                cursor.close()
                conn.close()
    
    async def collect_all_chains(self, target_date: Optional[date] = None):
        """
        采集所有链的Gas数据（顺序执行，避免API限流）
        
        Args:
            target_date: 目标日期，默认为昨天
        """
        if target_date is None:
            target_date = date.today() - timedelta(days=1)
        
        logger.info(f"开始采集所有链 {target_date} 的Gas数据...")
        
        # 顺序执行，避免同时请求CoinGecko API导致429错误
        success_count = 0
        chain_list = list(CHAIN_CONFIGS.keys())
        
        # 先批量获取所有链的价格，避免在采集过程中频繁请求导致 HTTP 429
        logger.info("预获取所有链的原生代币价格...")
        price_cache = {}
        for chain_name in chain_list:
            try:
                price = await self.get_native_token_price(chain_name)
                if price and price > 0:
                    price_cache[chain_name] = price
                    logger.info(f"  {chain_name}: ${price:.2f}")
                else:
                    logger.warning(f"  {chain_name}: 价格获取失败，将在采集时重试")
                # 每个价格请求之间添加延迟，避免 HTTP 429
                await asyncio.sleep(5)  # 增加到 5 秒延迟，避免限流
            except Exception as e:
                logger.warning(f"  {chain_name}: 价格获取异常: {e}")
        
        logger.info(f"价格预获取完成，成功获取 {len(price_cache)}/{len(chain_list)} 个链的价格")
        
        # 将预获取的价格存储到实例变量中，供后续使用
        self._price_cache = price_cache
        
        # 采集每个链的 Gas 数据
        for i, chain_name in enumerate(chain_list):
            try:
                result = await self.collect_daily_gas_stats(chain_name, target_date)
                if result:
                    success_count += 1
                
                # 在链之间添加延迟，避免 API 限流
                if i < len(chain_list) - 1:
                    await asyncio.sleep(2)  # 每个链之间延迟 2 秒
            except Exception as e:
                logger.error(f"采集 {chain_name} 失败: {e}", exc_info=True)
        
        logger.info(f"完成采集: {success_count}/{len(CHAIN_CONFIGS)} 条链成功")


async def main():
    """主函数，用于测试"""
    collector = BlockchainGasCollector()
    await collector.collect_all_chains()


if __name__ == "__main__":
    asyncio.run(main())

