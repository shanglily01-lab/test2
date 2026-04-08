#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
企业金库持仓批量写入（与 data_management 手工导入逻辑一致），供网站同步与文件导入共用。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Tuple

import pymysql
from loguru import logger

# (company_name, ticker, cumulative_btc)
HoldingsRow = Tuple[str, str, float]


def upsert_corporate_holdings_batch(
    cursor: pymysql.cursors.Cursor,
    companies: List[HoldingsRow],
    purchase_date: date,
    asset_type: str,
    data_source: str,
) -> Dict[str, Any]:
    """
    写入 corporate_treasury_companies / corporate_treasury_purchases。
    """
    imported = 0
    updated = 0
    skipped = 0
    errors: List[str] = []

    for company_name, ticker, holdings in companies:
        try:
            cursor.execute(
                """
                SELECT id FROM corporate_treasury_companies
                WHERE company_name = %s OR ticker_symbol = %s
                LIMIT 1
                """,
                (company_name, ticker),
            )
            company_result = cursor.fetchone()

            if not company_result:
                cursor.execute(
                    """
                    INSERT INTO corporate_treasury_companies
                    (company_name, ticker_symbol, category, is_active)
                    VALUES (%s, %s, %s, 1)
                    """,
                    (company_name, ticker, "holding"),
                )
                company_id = cursor.lastrowid
                logger.debug("新增公司: {} ({})", company_name, ticker)
            else:
                company_id = (
                    company_result["id"]
                    if isinstance(company_result, dict)
                    else company_result[0]
                )

            cursor.execute(
                """
                SELECT id, cumulative_holdings FROM corporate_treasury_purchases
                WHERE company_id = %s AND purchase_date = %s AND asset_type = %s
                """,
                (company_id, purchase_date, asset_type),
            )
            existing = cursor.fetchone()

            if existing:
                existing_id = (
                    existing["id"] if isinstance(existing, dict) else existing[0]
                )
                existing_holdings = (
                    existing["cumulative_holdings"]
                    if isinstance(existing, dict)
                    else existing[1]
                )
                if existing_holdings is not None and float(existing_holdings) == float(
                    holdings
                ):
                    skipped += 1
                    continue

                cursor.execute(
                    """
                    UPDATE corporate_treasury_purchases
                    SET cumulative_holdings = %s, updated_at = CURRENT_TIMESTAMP,
                        data_source = %s
                    WHERE id = %s
                    """,
                    (holdings, data_source, existing_id),
                )
                updated += 1
                imported += 1
            else:
                cursor.execute(
                    """
                    SELECT cumulative_holdings FROM corporate_treasury_purchases
                    WHERE company_id = %s AND asset_type = %s
                    ORDER BY purchase_date DESC
                    LIMIT 1
                    """,
                    (company_id, asset_type),
                )
                last_record = cursor.fetchone()
                if last_record:
                    if isinstance(last_record, dict):
                        last_holdings = float(last_record.get("cumulative_holdings") or 0)
                    else:
                        last_holdings = float(last_record[0] or 0)
                else:
                    last_holdings = 0.0

                quantity = float(holdings) - last_holdings

                cursor.execute(
                    """
                    INSERT INTO corporate_treasury_purchases
                    (company_id, purchase_date, asset_type, quantity, cumulative_holdings, data_source)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        company_id,
                        purchase_date,
                        asset_type,
                        quantity,
                        holdings,
                        data_source,
                    ),
                )

            imported += 1

        except Exception as e:
            err = f"{company_name} ({ticker}): {e}"
            errors.append(err)
            logger.warning("企业金库写入失败: {}", err)

    return {
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }
