from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from sqlalchemy.orm import Session
from app.models.entities import FeatureSnapshot, MarketCandle, Symbol

# Fetch window: 250 candles gives EMA-200 a proper warm-up period.
_FETCH_LIMIT = 250


@dataclass
class FeatureResult:
    snapshot: FeatureSnapshot
    latest_close: float
    latest_open: float
    reason_base: list[str]


# ── Indicator Calculations ──────────────────────────────────────────────────

def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-12)
    return 100 - (100 / (1 + rs))


def _true_range(df: pd.DataFrame) -> pd.Series:
    hl = df['high_price'] - df['low_price']
    hc = (df['high_price'] - df['close_price'].shift(1)).abs()
    lc = (df['low_price'] - df['close_price'].shift(1)).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1)


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _true_range(df).ewm(span=period, min_periods=period, adjust=False).mean()


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ADX — trend strength, 0-100. >25 = trending, <20 = choppy."""
    up   = df['high_price'].diff()
    down = -df['low_price'].diff()
    dm_p = up.where((up > down) & (up > 0), 0.0)
    dm_m = down.where((down > up) & (down > 0), 0.0)
    atr  = _atr(df, period)
    di_p = 100 * dm_p.ewm(span=period, adjust=False).mean() / atr.replace(0, 1e-12)
    di_m = 100 * dm_m.ewm(span=period, adjust=False).mean() / atr.replace(0, 1e-12)
    dx   = 100 * (di_p - di_m).abs() / (di_p + di_m).replace(0, 1e-12)
    return dx.ewm(span=period, adjust=False).mean()


def _stoch_rsi_k(rsi_series: pd.Series, rsi_period: int = 14, smooth: int = 3) -> pd.Series:
    rsi_min = rsi_series.rolling(rsi_period).min()
    rsi_max = rsi_series.rolling(rsi_period).max()
    raw = (rsi_series - rsi_min) / (rsi_max - rsi_min).replace(0, 1e-12)
    return raw.rolling(smooth).mean() * 100


# ── Candle Pattern ──────────────────────────────────────────────────────────

def _candle_pattern(row: pd.Series) -> str:
    total = row['high_price'] - row['low_price']
    if total < 1e-10:
        return 'DOJI'
    body = abs(row['close_price'] - row['open_price'])
    body_pct = body / total
    upper_wick = row['high_price'] - max(row['close_price'], row['open_price'])
    lower_wick = min(row['close_price'], row['open_price']) - row['low_price']
    bullish = row['close_price'] >= row['open_price']

    if body_pct < 0.12:
        return 'DOJI'
    if lower_wick >= body * 2.0 and upper_wick <= body * 0.5:
        return 'HAMMER'           # bullish reversal signal
    if upper_wick >= body * 2.0 and lower_wick <= body * 0.5:
        return 'SHOOTING_STAR'    # bearish reversal signal
    if body_pct >= 0.65:
        return 'BULL_MARUBOZU' if bullish else 'BEAR_MARUBOZU'
    if body_pct >= 0.40:
        return 'BULLISH' if bullish else 'BEARISH'
    return 'NEUTRAL'


# ── Regime Detection ────────────────────────────────────────────────────────

def _regime(row: pd.Series) -> str:
    volatility   = row.get('volatility') or 0
    volume_ratio = row.get('volume_ratio') or 0
    adx          = row.get('adx_14') or 0
    ema9         = row.get('ema_9')
    ema21        = row.get('ema_21')
    ema50        = row.get('ema_50')
    close        = row['close_price']

    if volatility > 0.012:
        return 'CHAOTIC'
    if volatility > 0.010:
        return 'HIGH_VOLATILITY'
    if volume_ratio < 0.60:
        return 'LOW_VOLUME'
    if adx < 18:
        return 'SIDEWAYS'

    # EMA alignment determines direction; require EMA21 > EMA50 for full confirmation
    if ema9 and ema21 and ema50:
        diff = (ema9 - ema21) / close
        if diff > 0.0004 and ema21 > ema50:
            return 'TRENDING_UP'
        if diff < -0.0004 and ema21 < ema50:
            return 'TRENDING_DOWN'
    elif ema9 and ema21:
        diff = (ema9 - ema21) / close
        if diff > 0.0004:
            return 'TRENDING_UP'
        if diff < -0.0004:
            return 'TRENDING_DOWN'
    return 'SIDEWAYS'


# ── Main Builder ────────────────────────────────────────────────────────────

def build_feature_snapshot(
    db: Session,
    symbol: Symbol,
    timeframe: str,
    min_rows: int = 60,
) -> FeatureResult | None:

    candles = (
        db.query(MarketCandle)
        .filter(MarketCandle.symbol_id == symbol.id, MarketCandle.timeframe == timeframe)
        .order_by(MarketCandle.open_time.desc())
        .limit(_FETCH_LIMIT)
        .all()
    )
    if len(candles) < min_rows:
        return None

    candles = list(reversed(candles))
    df = pd.DataFrame([{
        'open_time':   c.open_time,
        'open_price':  c.open_price,
        'high_price':  c.high_price,
        'low_price':   c.low_price,
        'close_price': c.close_price,
        'volume':      c.volume,
    } for c in candles])

    close = df['close_price']

    # ── Trend EMAs ──────────────────────────────────────────────────────
    df['ema_9']   = close.ewm(span=9,   adjust=False).mean()
    df['ema_21']  = close.ewm(span=21,  adjust=False).mean()
    df['ema_50']  = close.ewm(span=50,  adjust=False).mean()
    df['ema_200'] = close.ewm(span=200, adjust=False).mean()

    # ── Oscillators ─────────────────────────────────────────────────────
    df['rsi_7']      = _rsi(close, 7)
    df['rsi_14']     = _rsi(close, 14)
    df['stoch_rsi_k'] = _stoch_rsi_k(df['rsi_14'], 14, 3)

    # ── MACD (12, 26, 9) ────────────────────────────────────────────────
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df['macd_line']   = ema12 - ema26
    df['macd_signal'] = df['macd_line'].ewm(span=9, adjust=False).mean()
    df['macd_hist']      = df['macd_line'] - df['macd_signal']
    df['macd_hist_prev'] = df['macd_hist'].shift(1)

    # ── Volatility & Range ───────────────────────────────────────────────
    df['atr_14']      = _atr(df, 14)
    df['return_1']    = close.pct_change()
    df['volatility']  = df['return_1'].rolling(20).std()
    df['candle_range'] = (df['high_price'] - df['low_price']) / close

    # ── Bollinger Bands (20, 2σ) ────────────────────────────────────────
    bb_mid   = close.rolling(20).mean()
    bb_std   = close.rolling(20).std()
    df['bb_upper']     = bb_mid + 2 * bb_std
    df['bb_lower']     = bb_mid - 2 * bb_std
    band_w             = (df['bb_upper'] - df['bb_lower']).replace(0, 1e-12)
    df['bb_percent_b'] = (close - df['bb_lower']) / band_w

    # ── VWAP (rolling 20-bar) ────────────────────────────────────────────
    tp = (df['high_price'] + df['low_price'] + df['close_price']) / 3
    df['vwap'] = (tp * df['volume']).rolling(20).sum() / df['volume'].rolling(20).sum()

    # ── Volume ───────────────────────────────────────────────────────────
    df['volume_ratio'] = df['volume'] / df['volume'].rolling(20).mean()

    # ── Candle Body ──────────────────────────────────────────────────────
    body = (close - df['open_price']).abs()
    wick = (df['high_price'] - df['low_price']) - body
    df['body_to_wick_ratio'] = body / wick.replace(0, 1e-12)

    # ── ADX ──────────────────────────────────────────────────────────────
    df['adx_14'] = _adx(df, 14)

    # ── Build snapshot from latest row ──────────────────────────────────
    row   = df.iloc[-1]
    regime        = _regime(row)
    pattern       = _candle_pattern(row)
    snapshot_time = row['open_time'].to_pydatetime() if hasattr(row['open_time'], 'to_pydatetime') else row['open_time']

    def sf(val) -> float | None:
        return float(val) if pd.notna(val) else None

    payload = dict(
        symbol_id         = symbol.id,
        timeframe         = timeframe,
        snapshot_time     = snapshot_time,
        ema_9             = sf(row['ema_9']),
        ema_21            = sf(row['ema_21']),
        ema_50            = sf(row['ema_50']),
        ema_200           = sf(row['ema_200']),
        rsi_7             = sf(row['rsi_7']),
        rsi_14            = sf(row['rsi_14']),
        stoch_rsi_k       = sf(row['stoch_rsi_k']),
        macd_line         = sf(row['macd_line']),
        macd_signal       = sf(row['macd_signal']),
        macd_hist         = sf(row['macd_hist']),
        macd_hist_prev    = sf(row['macd_hist_prev']),
        atr_14            = sf(row['atr_14']),
        bb_upper          = sf(row['bb_upper']),
        bb_lower          = sf(row['bb_lower']),
        bb_percent_b      = sf(row['bb_percent_b']),
        vwap              = sf(row['vwap']),
        volume_ratio      = sf(row['volume_ratio']),
        volatility        = sf(row['volatility']),
        candle_range      = sf(row['candle_range']),
        body_to_wick_ratio= sf(row['body_to_wick_ratio']),
        adx_14            = sf(row['adx_14']),
        candle_pattern    = pattern,
        spread            = None,
        orderbook_imbalance_5 = None,
        market_regime     = regime,
    )

    existing = (
        db.query(FeatureSnapshot)
        .filter(
            FeatureSnapshot.symbol_id    == symbol.id,
            FeatureSnapshot.timeframe    == timeframe,
            FeatureSnapshot.snapshot_time == snapshot_time,
        )
        .first()
    )
    if existing:
        for k, v in payload.items():
            setattr(existing, k, v)
        snapshot = existing
    else:
        snapshot = FeatureSnapshot(**payload)
        db.add(snapshot)
        db.flush()

    adx_str = f'{payload["adx_14"]:.1f}' if payload['adx_14'] else 'n/a'
    reasons = [f'regime={regime}', f'adx={adx_str}', f'pattern={pattern}']
    return FeatureResult(
        snapshot     = snapshot,
        latest_close = float(row['close_price']),
        latest_open  = float(row['open_price']),
        reason_base  = reasons,
    )
