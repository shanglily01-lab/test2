"""
手动触发一次市场预测
用法: python scripts/run_predictor.py
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

from app.utils.config_loader import load_config
from app.services.market_predictor import MarketPredictor

config = load_config(project_root / 'config.yaml')
db_config = config.get('database', {}).get('mysql', {})
db_config['port'] = int(db_config.get('port', 3306))
symbols = config.get('symbols', [])

print(f'交易对数量: {len(symbols)}')

predictor = MarketPredictor(db_config)
count = predictor.run_all(symbols)

print(f'完成，分析了 {count} 个交易对')
