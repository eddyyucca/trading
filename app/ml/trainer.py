from __future__ import annotations
import math
import os
import joblib
import numpy as np
import pandas as pd
from sqlalchemy import text
from sklearn.utils.class_weight import compute_sample_weight
import xgboost as xgb
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.ml.features import FEATURE_COLUMNS, compute_features
from app.ml.labeler import (
    LABEL_BUY, LABEL_HOLD, LABEL_SELL,
    apply_triple_barrier_labels
)

MODEL_DIR = os.path.join(os.path.dirname(__file__), 'models')
os.makedirs(MODEL_DIR, exist_ok=True)


def timeframe_minutes(tf: str) -> int:
    return int(tf[:-1]) * {'m': 1, 'h': 60, 'd': 1440}[tf[-1]]


def default_training_params() -> dict:
    """Training barriers mirror the live trading rules so labels match how trades are judged."""
    st = get_settings()
    tf = st.prediction_timeframe
    horizon_min = max(st.horizon_list) if st.horizon_list else 15
    return {
        'symbols': st.symbol_list,
        'timeframe': tf,
        'tp_pct': st.take_profit_percent,
        'sl_pct': st.stop_loss_percent,
        'horizon_bars': max(1, math.ceil(horizon_min / timeframe_minutes(tf))),
    }


def load_candles_dataframe(symbols: list[str], timeframe: str = '5m') -> dict[str, pd.DataFrame]:
    db = SessionLocal()
    dfs: dict[str, pd.DataFrame] = {}
    try:
        for sym in symbols:
            query = text("""
                SELECT open_time, open_price, high_price, low_price, close_price, volume
                FROM market_candles m
                JOIN symbols s ON m.symbol_id = s.id
                WHERE s.symbol = :symbol AND m.timeframe = :timeframe
                ORDER BY open_time ASC
            """)
            raw = db.execute(query, {'symbol': sym, 'timeframe': timeframe}).fetchall()
            if not raw:
                continue
            df = pd.DataFrame(raw, columns=['open_time', 'open_price', 'high_price', 'low_price', 'close_price', 'volume'])
            for col in ('open_price', 'high_price', 'low_price', 'close_price', 'volume'):
                df[col] = df[col].astype(float)
            # The newest row can be a still-forming candle; never train on it.
            df = df.iloc[:-1]
            dfs[sym] = df
    finally:
        db.close()
    return dfs


def prepare_dataset(
    symbols: list[str] | None = None,
    timeframe: str | None = None,
    warmup_bars: int = 150,
    tp_pct: float | None = None,
    sl_pct: float | None = None,
    horizon_bars: int | None = None,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    d = default_training_params()
    symbols = symbols or d['symbols']
    timeframe = timeframe or d['timeframe']
    tp_pct = d['tp_pct'] if tp_pct is None else tp_pct
    sl_pct = d['sl_pct'] if sl_pct is None else sl_pct
    horizon_bars = d['horizon_bars'] if horizon_bars is None else horizon_bars
    dfs_raw = load_candles_dataframe(symbols, timeframe=timeframe)

    processed_dfs = []
    for sym, df_raw in dfs_raw.items():
        if len(df_raw) <= warmup_bars + horizon_bars:
            continue
        feat_df = compute_features(df_raw)
        labeled_df = apply_triple_barrier_labels(
            feat_df, tp_pct=tp_pct, sl_pct=sl_pct, horizon_bars=horizon_bars
        )
        # Drop warm-up bars and end-of-series unlabelable bars
        valid_df = labeled_df.iloc[warmup_bars: -horizon_bars].copy()
        valid_df['symbol'] = sym
        processed_dfs.append(valid_df)

    if not processed_dfs:
        raise ValueError("Insufficient candle history to build training dataset.")

    master = pd.concat(processed_dfs, ignore_index=True)
    master.sort_values(by='open_time', inplace=True, kind='stable')
    master.reset_index(drop=True, inplace=True)

    master[FEATURE_COLUMNS] = master[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)
    master[FEATURE_COLUMNS] = master.groupby('symbol')[FEATURE_COLUMNS].ffill().fillna(0.0)

    return master[FEATURE_COLUMNS], master['target_label'], master[['long_ret', 'short_ret']]


def _new_model() -> xgb.XGBClassifier:
    return xgb.XGBClassifier(
        n_estimators=180,
        max_depth=4,
        learning_rate=0.03,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=5,
        objective='multi:softprob',
        num_class=3,
        eval_metric='mlogloss',
        random_state=42,
        tree_method='hist',
    )


def _signal_outcomes(probs: np.ndarray, rets: pd.DataFrame, thr: float, cost: float) -> np.ndarray:
    """Net return of every trade the model would take at probability threshold `thr`."""
    p_buy, p_sell = probs[:, LABEL_BUY], probs[:, LABEL_SELL]
    buy = (p_buy >= thr) & (p_buy > p_sell)
    sell = (p_sell >= thr) & (p_sell > p_buy)
    return np.concatenate([
        rets['long_ret'].values[buy] - cost,
        rets['short_ret'].values[sell] - cost,
    ])


def _best_threshold(probs: np.ndarray, rets: pd.DataFrame, cost: float, min_signals: int) -> tuple[float, float, int]:
    best = (0.50, float('-inf'), 0)
    for thr in np.arange(0.38, 0.80, 0.02):
        out = _signal_outcomes(probs, rets, float(thr), cost)
        if len(out) < min_signals:
            continue
        exp = float(out.mean())
        if exp > best[1]:
            best = (float(thr), exp, len(out))
    return best


def train_ai_model(
    symbols: list[str] | None = None,
    timeframe: str | None = None,
    tp_pct: float | None = None,
    sl_pct: float | None = None,
    horizon_bars: int | None = None,
    model_name: str = 'xgboost_model_5m.joblib',
    **_ignored,
) -> dict:
    st = get_settings()
    d = default_training_params()
    symbols = symbols or d['symbols']
    timeframe = timeframe or d['timeframe']
    tp_pct = d['tp_pct'] if tp_pct is None else tp_pct
    sl_pct = d['sl_pct'] if sl_pct is None else sl_pct
    horizon_bars = d['horizon_bars'] if horizon_bars is None else horizon_bars
    cost = st.round_trip_cost

    print(f"[*] Building dataset: {len(symbols)} symbols [{timeframe}] "
          f"TP={tp_pct:.4%} SL={sl_pct:.4%} horizon={horizon_bars} bars...")
    X, y, rets = prepare_dataset(
        symbols=symbols, timeframe=timeframe, tp_pct=tp_pct, sl_pct=sl_pct, horizon_bars=horizon_bars
    )
    n = len(X)
    if n < 600:
        raise ValueError(f"Dataset too small for walk-forward training ({n} rows). Run backfill first.")

    # Walk-forward (expanding window). Folds 0-1 pick the probability threshold;
    # fold 2 is a final untouched holdout that is only reported.
    bounds = [int(n * b) for b in (0.55, 0.70, 0.85, 1.0)]
    oos_probs, oos_rets, fold_sizes = [], [], []
    for k in range(3):
        tr_end, te_end = bounds[k], bounds[k + 1]
        # Purge the last horizon_bars rows of train so labels do not peek into the test fold.
        tr = slice(0, max(1, tr_end - horizon_bars))
        m = _new_model()
        m.fit(X.iloc[tr], y.iloc[tr], sample_weight=compute_sample_weight('balanced', y.iloc[tr]), verbose=False)
        oos_probs.append(m.predict_proba(X.iloc[tr_end:te_end]))
        oos_rets.append(rets.iloc[tr_end:te_end])
        fold_sizes.append(te_end - tr_end)

    sel_probs = np.vstack(oos_probs[:2])
    sel_rets = pd.concat(oos_rets[:2])
    thr, sel_exp, sel_n = _best_threshold(sel_probs, sel_rets, cost, min_signals=max(20, int(len(sel_probs) * 0.005)))
    thr = max(thr, st.ml_min_confidence)

    hold_out = _signal_outcomes(oos_probs[2], oos_rets[2], thr, cost)
    total_signals = int(len(hold_out))
    expectancy = float(hold_out.mean()) if total_signals else 0.0
    win_rate = float((hold_out > 0).mean() * 100.0) if total_signals else 0.0
    print(f"[*] Threshold={thr:.2f} | selection expectancy={sel_exp:.4%} ({sel_n} trades) | "
          f"HOLDOUT expectancy={expectancy:.4%} win={win_rate:.1f}% ({total_signals} trades)")

    # Final production model: all data.
    cut = slice(0, n - horizon_bars)
    model = _new_model()
    model.fit(X.iloc[cut], y.iloc[cut], sample_weight=compute_sample_weight('balanced', y.iloc[cut]), verbose=False)

    fi = pd.Series(model.feature_importances_, index=FEATURE_COLUMNS).sort_values(ascending=False)
    top_features = {k: float(v) for k, v in fi.head(10).items()}

    save_path = os.path.join(MODEL_DIR, model_name)
    artifact = {
        'model': model,
        'feature_columns': FEATURE_COLUMNS,
        'timeframe': timeframe,
        'symbols': symbols,
        'classes': [LABEL_HOLD, LABEL_BUY, LABEL_SELL],
        'tp_pct': tp_pct,
        'sl_pct': sl_pct,
        'horizon_bars': horizon_bars,
        'confidence_threshold': thr,
        'metrics': {
            'total_signals': total_signals,
            'win_rate': win_rate,
            'expectancy': expectancy,
            'round_trip_cost': cost,
            'top_features': top_features,
        },
    }
    joblib.dump(artifact, save_path)
    print(f"[+] Model saved successfully to: {save_path}")

    return {
        'model_path': save_path,
        'test_samples': int(fold_sizes[2]),
        'total_signals': total_signals,
        'win_rate': win_rate,
        'expectancy': expectancy,
        'selection_expectancy': sel_exp,
        'threshold': thr,
        'top_features': top_features,
    }


if __name__ == '__main__':
    results = train_ai_model()
    print("\n=== Model Training Summary (holdout, net of fees) ===")
    print(f"Trades: {results['total_signals']}  Win-rate: {results['win_rate']:.2f}%  "
          f"Expectancy/trade: {results['expectancy']:.4%}  Threshold: {results['threshold']:.2f}")
    print("\nTop 10 Feature Importances:")
    for feat, imp in results['top_features'].items():
        print(f"  - {feat:16s}: {imp:.4f}")
