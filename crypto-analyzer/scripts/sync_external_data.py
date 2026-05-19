"""
手动同步外部数据（Farside ETF + BitcoinTreasuries 企业金库）

用法:
    python scripts/sync_external_data.py            # 全部同步
    python scripts/sync_external_data.py --etf      # 仅同步 ETF (BTC + ETH)
    python scripts/sync_external_data.py --treasury # 仅同步企业金库
    python scripts/sync_external_data.py --btc      # 仅同步 BTC ETF
    python scripts/sync_external_data.py --eth      # 仅同步 ETH ETF
"""
import argparse
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 用 dotenv_values 直接读 .env 字典，不污染系统环境变量
# (防止多版本部署时 DB_NAME 被系统环境变量污染，参见 commit bb97892)
from dotenv import dotenv_values

from app.services.farside_etf_sync import (
    sync_farside_btc_flows,
    sync_farside_eth_flows,
)
from app.services.bitcointreasuries_sync import sync_bitcointreasuries_holdings


def build_mysql_config() -> dict:
    env = dotenv_values(project_root / ".env")
    return {
        "host": env.get("DB_HOST", "localhost"),
        "port": int(env.get("DB_PORT", 3306)),
        "user": env.get("DB_USER", "root"),
        "password": env.get("DB_PASSWORD", ""),
        "database": env.get("DB_NAME", "binance-data"),
    }


def run_btc_etf(mysql_config: dict) -> None:
    print("[BTC ETF] 开始同步 farside.co.uk/btc/ ...")
    r = sync_farside_btc_flows(mysql_config)
    print(
        f"[BTC ETF] 完成: rows={r.get('imported_rows')}, "
        f"errors={r.get('error_count')}, "
        f"tickers={len(r.get('tickers', []))}"
    )
    if r.get("errors"):
        for err in r["errors"]:
            print(f"  ! {err}")


def run_eth_etf(mysql_config: dict) -> None:
    print("[ETH ETF] 开始同步 farside.co.uk/eth/ ...")
    r = sync_farside_eth_flows(mysql_config)
    print(
        f"[ETH ETF] 完成: rows={r.get('imported_rows')}, "
        f"errors={r.get('error_count')}, "
        f"tickers={len(r.get('tickers', []))}"
    )
    if r.get("errors"):
        for err in r["errors"]:
            print(f"  ! {err}")


def run_treasury(mysql_config: dict) -> None:
    print("[企业金库] 开始同步 bitcointreasuries.net ...")
    r = sync_bitcointreasuries_holdings(mysql_config)
    print(
        f"[企业金库] 完成: imported={r.get('imported')}, "
        f"updated={r.get('updated')}, "
        f"skipped={r.get('skipped')}, "
        f"total={r.get('company_count')}, "
        f"date={r.get('purchase_date')}"
    )
    if r.get("errors"):
        for err in r["errors"]:
            print(f"  ! {err}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="同步 Farside ETF 资金流和 BitcoinTreasuries 企业金库数据"
    )
    parser.add_argument("--etf", action="store_true", help="仅同步 ETF (BTC+ETH)")
    parser.add_argument("--btc", action="store_true", help="仅同步 BTC ETF")
    parser.add_argument("--eth", action="store_true", help="仅同步 ETH ETF")
    parser.add_argument(
        "--treasury", action="store_true", help="仅同步 BitcoinTreasuries 企业金库"
    )
    args = parser.parse_args()

    # 默认全部跑；指定任一参数则只跑对应项
    any_flag = any([args.etf, args.btc, args.eth, args.treasury])
    do_btc = (not any_flag) or args.etf or args.btc
    do_eth = (not any_flag) or args.etf or args.eth
    do_treasury = (not any_flag) or args.treasury

    mysql_config = build_mysql_config()
    print(
        f"DB: {mysql_config['user']}@{mysql_config['host']}:"
        f"{mysql_config['port']}/{mysql_config['database']}"
    )

    failed = []
    if do_btc:
        try:
            run_btc_etf(mysql_config)
        except Exception as e:
            print(f"[BTC ETF] 失败: {type(e).__name__}: {e}")
            failed.append("BTC ETF")

    if do_eth:
        try:
            run_eth_etf(mysql_config)
        except Exception as e:
            print(f"[ETH ETF] 失败: {type(e).__name__}: {e}")
            failed.append("ETH ETF")

    if do_treasury:
        try:
            run_treasury(mysql_config)
        except Exception as e:
            print(f"[企业金库] 失败: {type(e).__name__}: {e}")
            failed.append("企业金库")

    if failed:
        print(f"\n失败任务: {', '.join(failed)}")
        return 1

    print("\n全部完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
