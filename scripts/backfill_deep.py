from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import Any
import httpx
from sqlalchemy import text
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.binance_client import get_or_create_symbol, ms_to_dt


def fetch_klines_batch(
    client: httpx.Client,
    symbol: str,
    interval: str,
    limit: int = 1000,
    end_time: int | None = None,
) -> list[list[Any]]:
    params: dict[str, Any] = {'symbol': symbol, 'interval': interval, 'limit': limit}
    if end_time is not None:
        params['endTime'] = end_time
    res = client.get('/api/v3/klines', params=params)
    res.raise_for_status()
    return res.json()


def deep_backfill(
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
    target_candles: int = 2000,
) -> dict[str, int]:
    settings = get_settings()
    symbols = symbols or ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT']
    timeframes = timeframes or ['5m', '15m']

    client = httpx.Client(
        base_url=settings.binance_rest_base_url,
        timeout=15,
        verify=settings.binance_verify_ssl,
    )

    db = SessionLocal()
    inserted_summary: dict[str, int] = {}

    try:
        for symbol_text in symbols:
            symbol_obj = get_or_create_symbol(db, symbol_text)
            db.commit()

            for tf in timeframes:
                key = f"{symbol_text}_{tf}"
                total_inserted = 0
                end_time_ms: int | None = None

                print(f"[*] Backfilling {symbol_text} [{tf}] target: {target_candles} candles...")

                while total_inserted < target_candles:
                    batch_limit = min(1000, target_candles - total_inserted)
                    try:
                        raw_rows = fetch_klines_batch(client, symbol_text, tf, limit=batch_limit, end_time=end_time_ms)
                    except Exception as e:
                        print(f"    [!] Error fetching {symbol_text} {tf}: {e}")
                        break

                    if not raw_rows:
                        break

                    # Format values for bulk INSERT IGNORE
                    records = []
                    for r in raw_rows:
                        open_dt = ms_to_dt(int(r[0]))
                        close_dt = ms_to_dt(int(r[6]))
                        records.append({
                            'symbol_id': symbol_obj.id,
                            'timeframe': tf,
                            'open_time': open_dt,
                            'close_time': close_dt,
                            'open_price': float(r[1]),
                            'high_price': float(r[2]),
                            'low_price': float(r[3]),
                            'close_price': float(r[4]),
                            'volume': float(r[5]),
                            'quote_volume': float(r[7]) if r[7] is not None else None,
                            'trade_count': int(r[8]) if r[8] is not None else None,
                        })

                    if records:
                        # Fast bulk insert ignore
                        stmt = text("""
                            INSERT IGNORE INTO market_candles 
                            (symbol_id, timeframe, open_time, close_time, open_price, high_price, low_price, close_price, volume, quote_volume, trade_count)
                            VALUES 
                            (:symbol_id, :timeframe, :open_time, :close_time, :open_price, :high_price, :low_price, :close_price, :volume, :quote_volume, :trade_count)
                        """)
                        db.execute(stmt, records)
                        db.commit()

                        total_inserted += len(records)
                        # Set next end_time to oldest open_time - 1ms to walk backwards in history
                        oldest_open_ms = int(raw_rows[0][0])
                        end_time_ms = oldest_open_ms - 1

                    if len(raw_rows) < batch_limit:
                        # No more earlier history available
                        break

                    time.sleep(0.1)  # Respect Binance rate limit

                print(f"    [+] {symbol_text} [{tf}] total fetched/processed: {total_inserted}")
                inserted_summary[key] = total_inserted

    finally:
        client.close()
        db.close()

    return inserted_summary


if __name__ == '__main__':
    print("[*] Starting Deep Historical Backfill...")
    results = deep_backfill(
        symbols=['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT'],
        timeframes=['5m', '15m'],
        target_candles=2000,
    )
    print("[*] Backfill complete:", results)
