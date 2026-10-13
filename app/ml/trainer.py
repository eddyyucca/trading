from __future__ import annotations
import os
import joblib
import numpy as np
import pandas as pd
from sqlalchemy import text
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.utils.class_weight import compute_sample_weight
import xgboost as xgb
from app.db.session import SessionLocal
from app.ml.features import FEATURE_COLUMNS, compute_features
from app.ml.labeler import (
    LABEL_BUY, LABEL_HOLD, LABEL_MAP, LABEL_SELL,
    apply_triple_barrier_labels
)

MODEL_DIR = os.path.join(os.path.dirname(__file__), 'models')
os.makedirs(MODEL_DIR, exist_ok=True)


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
            df['open_price'] = df['open_price'].astype(float)
            df['high_price'] = df['high_price'].astype(float)
            df['low_price'] = df['low_price'].astype(float)
            df['close_price'] = df['close_price'].astype(float)
            df['volume'] = df['volume'].astype(float)
            dfs[sym] = df
    finally:
        db.close()
    return dfs


def prepare_dataset(
    symbols: list[str] | None = None,
    timeframe: str = '5m',
    warmup_bars: int = 150,
    tp_pct: float = 0.0035,
    sl_pct: float = 0.0020,
    horizon_bars: int = 4,
) -> tuple[pd.DataFrame, pd.Series]:
    symbols = symbols or ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT']
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
        valid_df = labeled_df.iloc[warmup_bars : -horizon_bars].copy()
        valid_df['symbol'] = sym
        processed_dfs.append(valid_df)

    if not processed_dfs:
        raise ValueError("Insufficient candle history to build training dataset.")

    master = pd.concat(processed_dfs, ignore_index=True)
    master.sort_values(by='open_time', inplace=True)
    master.reset_index(drop=True, inplace=True)

    master[FEATURE_COLUMNS] = master[FEATURE_COLUMNS].ffill().bfill().fillna(0.0)

    X = master[FEATURE_COLUMNS]
    y = master['target_label']
    return X, y


def train_ai_model(
    symbols: list[str] | None = None,
    timeframe: str = '5m',
    test_ratio: float = 0.20,
    tp_pct: float = 0.0035,
    sl_pct: float = 0.0020,
    horizon_bars: int = 4,
    model_name: str = 'xgboost_model_5m.joblib',
) -> dict:
    symbols = symbols or ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT']
    print(f"[*] Extracting candle features & labels for {symbols} [{timeframe}]...")
    X, y = prepare_dataset(
        symbols=symbols, timeframe=timeframe, tp_pct=tp_pct, sl_pct=sl_pct, horizon_bars=horizon_bars
    )

    n_samples = len(X)
    split_idx = int(n_samples * (1.0 - test_ratio))

    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

    # Compute balanced sample weights to address class imbalance
    sample_weights_train = compute_sample_weight('balanced', y_train)

    print(f"[*] Dataset size: {n_samples} samples.")
    print(f"    - Training set: {len(X_train)} samples")
    print(f"    - Test set (Out-of-sample): {len(X_test)} samples")
    print(f"    - Class distribution in train: {dict(y_train.value_counts())}")
    print(f"    - Class distribution in test:  {dict(y_test.value_counts())}")

    # Build and configure XGBoost classifier
    model = xgb.XGBClassifier(
        n_estimators=180,
        max_depth=4,
        learning_rate=0.03,
        subsample=0.8,
        colsample_bytree=0.8,
        objective='multi:softprob',
        num_class=3,
        eval_metric='mlogloss',
        random_state=42,
        tree_method='hist',
    )

    print("[*] Training XGBoost model with class balancing...")
    model.fit(
        X_train,
        y_train,
        sample_weight=sample_weights_train,
        eval_set=[(X_train, y_train), (X_test, y_test)],
        verbose=False,
    )

    # Evaluate on unseen Out-of-Sample test set
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)

    report = classification_report(
        y_test, y_pred, target_names=['HOLD', 'BUY', 'SELL'], output_dict=True, zero_division=0
    )

    # High-confidence trade filter: probability >= 0.40 (given balanced 3-class baseline is 0.33)
    p_buy = y_prob[:, LABEL_BUY]
    p_sell = y_prob[:, LABEL_SELL]
    confidence_thresh = 0.42

    buy_signals = p_buy >= confidence_thresh
    sell_signals = p_sell >= confidence_thresh
    action_signals = buy_signals | sell_signals

    total_signals = int(action_signals.sum())
    correct_signals = 0

    if total_signals > 0:
        actual_test = y_test.values
        correct_buy = ((p_buy >= confidence_thresh) & (actual_test == LABEL_BUY)).sum()
        correct_sell = ((p_sell >= confidence_thresh) & (actual_test == LABEL_SELL)).sum()
        correct_signals = int(correct_buy + correct_sell)
        win_rate = (correct_signals / total_signals) * 100.0
    else:
        win_rate = 0.0

    # Feature importances
    fi = pd.Series(model.feature_importances_, index=FEATURE_COLUMNS).sort_values(ascending=False)
    top_features = fi.head(10).to_dict()

    # Save model artifact
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
        'confidence_threshold': confidence_thresh,
        'metrics': {
            'out_of_sample_report': report,
            'total_signals': total_signals,
            'win_rate': float(win_rate),
            'top_features': top_features,
        },
    }
    joblib.dump(artifact, save_path)
    print(f"[+] Model saved successfully to: {save_path}")

    return {
        'model_path': save_path,
        'test_samples': len(X_test),
        'total_signals': total_signals,
        'win_rate': float(win_rate),
        'top_features': top_features,
        'report': report,
    }


if __name__ == '__main__':
    results = train_ai_model()
    print("\n=== Model Training Summary ===")
    print(f"Total Out-of-Sample Trade Signals: {results['total_signals']}")
    print(f"Signal Win-Rate (Positive Expectancy): {results['win_rate']:.2f}%")
    print("\nTop 10 Feature Importances:")
    for feat, imp in results['top_features'].items():
        print(f"  - {feat:16s}: {imp:.4f}")
