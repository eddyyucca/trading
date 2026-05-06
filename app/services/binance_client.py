from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Iterable
import httpx
from sqlalchemy.orm import Session
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy import select
from app.core.config import get_settings
from app.models.entities import MarketCandle, Symbol


def ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(tzinfo=None)


class BinanceRestClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = httpx.Client(base_url=self.settings.binance_rest_base_url, timeout=15)

    def get_klines(self, symbol: str, interval: str, limit: int = 300) -> list[list[Any]]:
        res = self.client.get('/api/v3/klines', params={'symbol': symbol, 'interval': interval, 'limit': limit})
        res.raise_for_status()
        return res.json()

    def get_book_ticker(self, symbol: str) -> dict[str, Any]:
        res = self.client.get('/api/v3/ticker/bookTicker', params={'symbol': symbol})
        res.raise_for_status()
        return res.json()


def get_or_create_symbol(db: Session, symbol_text: str) -> Symbol:
    symbol = db.execute(select(Symbol).where(Symbol.symbol == symbol_text)).scalar_one_or_none()
    if symbol:
        return symbol
    symbol = Symbol(symbol=symbol_text, base_asset=symbol_text.replace('USDT', ''), quote_asset='USDT')
    db.add(symbol)
    db.flush()
    return symbol


def upsert_candles(db: Session, symbol_text: str, timeframe: str, rows: Iterable[list[Any]]) -> int:
    symbol = get_or_create_symbol(db, symbol_text)
    count = 0
    for row in rows:
        payload = {
            'symbol_id': symbol.id,
            'timeframe': timeframe,
            'open_time': ms_to_dt(int(row[0])),
            'open_price': float(row[1]),
            'high_price': float(row[2]),
            'low_price': float(row[3]),
            'close_price': float(row[4]),
            'volume': float(row[5]),
            'close_time': ms_to_dt(int(row[6])),
            'quote_volume': float(row[7]) if row[7] is not None else None,
            'trade_count': int(row[8]) if row[8] is not None else None,
        }
        # Generic merge: works for SQLite/MySQL via ORM lookup, simpler for MVP.
        existing = db.query(MarketCandle).filter(
            MarketCandle.symbol_id == symbol.id,
            MarketCandle.timeframe == timeframe,
            MarketCandle.open_time == payload['open_time'],
        ).first()
        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
        else:
            db.add(MarketCandle(**payload))
            count += 1
    db.commit()
    return count
