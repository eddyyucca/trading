from __future__ import annotations
import numpy as np
import pandas as pd


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes normalized technical indicators for ML training and inference.
    df must contain columns: ['open_time', 'open_price', 'high_price', 'low_price', 'close_price', 'volume']
    sorted chronologically ascending.
    """
    df = df.copy()
    close = df['close_price']
    high = df['high_price']
    low = df['low_price']
    open_ = df['open_price']
    vol = df['volume']

    # 1. EMAs & Distances (normalized by close price)
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()

    df['dist_ema9'] = (close - ema9) / close
    df['dist_ema21'] = (close - ema21) / close
    df['dist_ema50'] = (close - ema50) / close
    df['dist_ema200'] = (close - ema200) / close
    df['ema_diff_9_21'] = (ema9 - ema21) / close
    df['ema_diff_21_50'] = (ema21 - ema50) / close

    # 2. RSIs
    def _calc_rsi(series: pd.Series, period: int) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-12)
        return 100 - (100 / (1 + rs))

    df['rsi_14'] = _calc_rsi(close, 14)
    df['rsi_7'] = _calc_rsi(close, 7)

    # Stoch RSI
    rsi_14 = df['rsi_14']
    rsi_min = rsi_14.rolling(14).min()
    rsi_max = rsi_14.rolling(14).max()
    df['stoch_rsi_k'] = ((rsi_14 - rsi_min) / (rsi_max - rsi_min).replace(0, 1e-12)).rolling(3).mean() * 100

    # 3. MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = (ema12 - ema26) / close
    macd_signal = macd_line.ewm(span=9, adjust=False).mean()
    df['macd_line'] = macd_line
    df['macd_signal'] = macd_signal
    df['macd_hist'] = macd_line - macd_signal

    # 4. Bollinger Bands (20, 2)
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std().replace(0, 1e-12)
    upper_bb = sma20 + (std20 * 2.0)
    lower_bb = sma20 - (std20 * 2.0)
    df['bb_pct_b'] = (close - lower_bb) / (upper_bb - lower_bb).replace(0, 1e-12)
    df['bb_width'] = (upper_bb - lower_bb) / sma20

    # 5. Volatility (ATR normalized)
    hl = high - low
    hc = (high - close.shift(1)).abs()
    lc = (low - close.shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr14 = tr.ewm(span=14, adjust=False).mean()
    df['atr_ratio'] = atr14 / close

    # 6. Volume & Candle Dynamics
    vol_ma20 = vol.rolling(20).mean().replace(0, 1e-12)
    df['volume_ratio'] = vol / vol_ma20

    total_range = (high - low).replace(0, 1e-12)
    body = (close - open_).abs()
    df['body_ratio'] = body / total_range
    df['upper_wick_ratio'] = (high - np.maximum(open_, close)) / total_range
    df['lower_wick_ratio'] = (np.minimum(open_, close) - low) / total_range

    # 7. Returns (Momenta)
    df['return_1'] = close.pct_change(1)
    df['return_3'] = close.pct_change(3)
    df['return_6'] = close.pct_change(6)

    # 8. Trend strength (ADX-14) and realised volatility
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    atr_w = tr.ewm(alpha=1 / 14, min_periods=14).mean().replace(0, 1e-12)
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, min_periods=14).mean() / atr_w
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, min_periods=14).mean() / atr_w
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-12)
    df['adx_14'] = dx.ewm(alpha=1 / 14, min_periods=14).mean()
    df['di_diff'] = (plus_di - minus_di) / 100.0
    df['ret_std_20'] = df['return_1'].rolling(20).std()

    return df


FEATURE_COLUMNS = [
    'dist_ema9', 'dist_ema21', 'dist_ema50', 'dist_ema200',
    'ema_diff_9_21', 'ema_diff_21_50',
    'rsi_14', 'rsi_7', 'stoch_rsi_k',
    'macd_line', 'macd_signal', 'macd_hist',
    'bb_pct_b', 'bb_width', 'atr_ratio',
    'volume_ratio', 'body_ratio', 'upper_wick_ratio', 'lower_wick_ratio',
    'return_1', 'return_3', 'return_6',
    'adx_14', 'di_diff', 'ret_std_20',
]
