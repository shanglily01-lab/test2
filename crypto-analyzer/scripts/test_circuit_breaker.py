#!/usr/bin/env python3
"""测试熔断器检测"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.config_loader import load_config
from app.services.circuit_breaker import CircuitBreaker


def main():
    config = load_config()
    db_config = config['database']['mysql']

    breaker = CircuitBreaker(db_config)

    print('=' * 80)
    print('检查熔断条件')
    print('=' * 80)

    should_trigger, reason = breaker.check_should_trigger(account_id=2)

    if should_trigger:
        print('\n[DANGER] 检测到熔断条件!')
        print(reason)
    else:
        print('\n[OK] 无熔断风险')

    print('=' * 80)


if __name__ == '__main__':
    main()
