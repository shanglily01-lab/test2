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
        'explorer_api': 'https://api.etherscan.io/api',
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
        """加载配置文件"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
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
        db_config = self.config.get('database', {})
        
        pool_config = {
            'pool_name': 'gas_collector_pool',
            'pool_size': 5,
            'pool_reset_session': True,
            'host': db_config.get('host', 'localhost'),
            'port': db_config.get('port', 3306),
            'user': db_config.get('user', 'root'),
            'password': db_config.get('password', ''),
            'database': db_config.get('database', 'crypto_analyzer'),
            'charset': 'utf8mb4',
            'autocommit': False
        }
        
        try:
            return pooling.MySQLConnectionPool(**pool_config)
        except Exception as e:
            logger.error(f"初始化数据库连接池失败: {e}")
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
        
        try:
            # 从价格缓存服务获取价格
            from app.services.price_cache_service import get_global_price_cache
            price_cache = get_global_price_cache()
            if price_cache:
                price = price_cache.get_price(symbol)
                if price and price > 0:
                    return float(price)
        except Exception as e:
            logger.debug(f"从价格缓存获取 {symbol} 价格失败: {e}")
        
        # 如果价格缓存不可用，返回None，后续可以从其他API获取
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
    
    async def fetch_gas_stats_from_rpc(
        self,
        chain_name: str,
        target_date: date
    ) -> Optional[Dict]:
        """
        从RPC节点获取Gas统计数据（简化版本，使用估算方法）
        
        注意：完整的RPC实现需要遍历所有区块，计算量很大。
        这里提供一个简化版本，可以后续扩展。
        
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
            native_price = await self.get_native_token_price(chain_name)
            
            # 简化实现：返回基础结构
            # 实际生产环境应该：
            # 1. 使用专业的链上数据API（如The Graph, Dune Analytics等）
            # 2. 或者自己搭建索引节点，定期同步区块数据
            # 3. 或者使用第三方服务（如Blocknative, Alchemy等）
            
            logger.warning(f"{chain_name} Gas数据采集需要实现具体的RPC逻辑或使用第三方API")
            
            # 返回空数据，表示需要手动导入或使用其他数据源
            return {
                'chain_name': chain_name,
                'date': target_date,
                'total_gas_used': 0,
                'total_transactions': 0,
                'avg_gas_per_tx': 0,
                'avg_gas_price': 0,
                'native_token_price_usd': native_price,
                'total_gas_value_usd': 0,
                'total_blocks': 0,
                'data_source': 'placeholder'
            }
            
        except Exception as e:
            logger.error(f"从RPC获取 {chain_name} Gas数据失败: {e}")
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
            logger.warning(f"无法获取 {chain_name} {target_date} 的Gas数据")
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
            avg_gas_value_usd = 0
            if stats.get('total_transactions', 0) > 0:
                avg_gas_value_usd = stats.get('total_gas_value_usd', 0) / stats['total_transactions']
            
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
            
            logger.info(f"✅ 成功保存 {stats['chain_name']} {stats['date']} 的Gas数据")
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
        采集所有链的Gas数据
        
        Args:
            target_date: 目标日期，默认为昨天
        """
        if target_date is None:
            target_date = date.today() - timedelta(days=1)
        
        logger.info(f"开始采集所有链 {target_date} 的Gas数据...")
        
        tasks = []
        for chain_name in CHAIN_CONFIGS.keys():
            tasks.append(self.collect_daily_gas_stats(chain_name, target_date))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        success_count = sum(1 for r in results if r is True)
        logger.info(f"完成采集: {success_count}/{len(CHAIN_CONFIGS)} 条链成功")


async def main():
    """主函数，用于测试"""
    collector = BlockchainGasCollector()
    await collector.collect_all_chains()


if __name__ == "__main__":
    asyncio.run(main())

