from __future__ import annotations
import numpy as np
import pandas as pd


LABEL_HOLD = 0
LABEL_BUY = 1
LABEL_SELL = 2

LABEL_MAP = {
    LABEL_HOLD: 'HOLD',
    LABEL_BUY: 'BUY',
    LABEL_SELL: 'SELL',
}


def apply_triple_barrier_labels(
    df: pd.DataFrame,
    tp_pct: float = 0.0050,
    sl_pct: float = 0.0025,
    horizon_bars: int = 3,
) -> pd.DataFrame:
    """
    Labels each candle based on forward price movement:
      1: BUY  (Price hits +TP before -SL within horizon)
      2: SELL (Price hits -TP before +SL within horizon)
      0: HOLD (Neither hit or stopped out)
    """
    df = df.copy()
    n = len(df)
    labels = np.zeros(n, dtype=int)

    closes = df['close_price'].values
    highs = df['high_price'].values
    lows = df['low_price'].values

    for i in range(n - horizon_bars):
        entry_price = closes[i]

        long_tp = entry_price * (1.0 + tp_pct)
        long_sl = entry_price * (1.0 - sl_pct)

        short_tp = entry_price * (1.0 - tp_pct)
        short_sl = entry_price * (1.0 + sl_pct)

        long_win = False
        long_loss = False
        short_win = False
        short_loss = False

        # Scan forward horizon
        for j in range(i + 1, min(i + 1 + horizon_bars, n)):
            f_high = highs[j]
            f_low = lows[j]

            # Long evaluation
            if not long_win and not long_loss:
                if f_high >= long_tp and f_low > long_sl:
                    long_win = True
                elif f_low <= long_sl:
                    long_loss = True

            # Short evaluation
            if not short_win and not short_loss:
                if f_low <= short_tp and f_high < short_sl:
                    short_win = True
                elif f_high >= short_sl:
                    short_loss = True

            if (long_win or long_loss) and (short_win or short_loss):
                break

        if long_win and not short_win:
            labels[i] = LABEL_BUY
        elif short_win and not long_win:
            labels[i] = LABEL_SELL
        else:
            labels[i] = LABEL_HOLD

    # Last horizon_bars cannot be evaluated (no forward data)
    labels[n - horizon_bars:] = LABEL_HOLD
    df['target_label'] = labels
    return df
