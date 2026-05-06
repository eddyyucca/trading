from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
import pandas as pd
from sqlalchemy.orm import Session
from app.models.entities import FeatureSnapshot, MarketCandle, Symbol


@dataclass
class FeatureResult:
    snapshot: FeatureSnapshot
    latest_close: float
    latest_open: float
    reason_base: list[str]


def calculate_rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-12)
    return 100 - (100 / (1 + rs))


def detect_regime(row: pd.Series) -> str:
    ema9 = row.get('ema_9')
    ema21 = row.get('ema_21')
    volatility = row.get('volatility') or 0
    volume_ratio = row.get('volume_ratio') or 0
    if volume_ratio < 0.65:
        return 'LOW_VOLUME'
    if volatility > 0.01:
        return 'HIGH_VOLATILITY'
    if ema9 and ema21:
        diff = (ema9 - ema21) / row['close_price']
        if diff > 0.0005:
            return 'TRENDING_UP'
        if diff < -0.0005:
            return 'TRENDING_DOWN'
    return 'SIDEWAYS'


def build_feature_snapshot(db: Session, symbol: Symbol, timeframe: str, min_rows: int = 60) -> FeatureResult | None:
    candles = db.query(MarketCandle).filter(
        MarketCandle.symbol_id == symbol.id,
        MarketCandle.timeframe == timeframe,
    ).order_by(MarketCandle.open_time.desc()).limit(120).all()
    if len(candles) < min_rows:
        return None

    candles = list(reversed(candles))
    df = pd.DataFrame([{
        'open_time': c.open_time,
        'open_price': c.open_price,
        'high_price': c.high_price,
        'low_price': c.low_price,
        'close_price': c.close_price,
        'volume': c.volume,
    } for c in candles])

    close = df['close_price']
    df['ema_9'] = close.ewm(span=9, adjust=False).mean()
    df['ema_21'] = close.ewm(span=21, adjust=False).mean()
    df['ema_50'] = close.ewm(span=50, adjust=False).mean()
    df['rsi_7'] = calculate_rsi(close, 7)
    df['rsi_14'] = calculate_rsi(close, 14)
    typical_price = (df['high_price'] + df['low_price'] + df['close_price']) / 3
    df['vwap'] = (typical_price * df['volume']).rolling(20).sum() / df['volume'].rolling(20).sum()
    df['volume_ratio'] = df['volume'] / df['volume'].rolling(20).mean()
    df['return_1'] = close.pct_change()
    df['volatility'] = df['return_1'].rolling(20).std()
    df['candle_range'] = (df['high_price'] - df['low_price']) / df['close_price']
    body = (df['close_price'] - df['open_price']).abs()
    wick = (df['high_price'] - df['low_price']) - body
    df['body_to_wick_ratio'] = body / wick.replace(0, 1e-12)

    row = df.iloc[-1]
    regime = detect_regime(row)
    snapshot_time: datetime = row['open_time'].to_pydatetime() if hasattr(row['open_time'], 'to_pydatetime') else row['open_time']

    existing = db.query(FeatureSnapshot).filter(
        FeatureSnapshot.symbol_id == symbol.id,
        FeatureSnapshot.timeframe == timeframe,
        FeatureSnapshot.snapshot_time == snapshot_time,
    ).first()
    payload = dict(
        symbol_id=symbol.id,
        timeframe=timeframe,
        snapshot_time=snapshot_time,
        ema_9=float(row['ema_9']) if pd.notna(row['ema_9']) else None,
        ema_21=float(row['ema_21']) if pd.notna(row['ema_21']) else None,
        ema_50=float(row['ema_50']) if pd.notna(row['ema_50']) else None,
        rsi_7=float(row['rsi_7']) if pd.notna(row['rsi_7']) else None,
        rsi_14=float(row['rsi_14']) if pd.notna(row['rsi_14']) else None,
        vwap=float(row['vwap']) if pd.notna(row['vwap']) else None,
        volume_ratio=float(row['volume_ratio']) if pd.notna(row['volume_ratio']) else None,
        volatility=float(row['volatility']) if pd.notna(row['volatility']) else None,
        candle_range=float(row['candle_range']) if pd.notna(row['candle_range']) else None,
        body_to_wick_ratio=float(row['body_to_wick_ratio']) if pd.notna(row['body_to_wick_ratio']) else None,
        spread=None,
        orderbook_imbalance_5=None,
        market_regime=regime,
    )
    if existing:
        for k, v in payload.items():
            setattr(existing, k, v)
        snapshot = existing
    else:
        snapshot = FeatureSnapshot(**payload)
        db.add(snapshot)
        db.flush()

    reasons = [f'regime={regime}', f'volume_ratio={payload["volume_ratio"]:.2f}' if payload['volume_ratio'] else 'volume_ratio=n/a']
    return FeatureResult(snapshot=snapshot, latest_close=float(row['close_price']), latest_open=float(row['open_price']), reason_base=reasons)
