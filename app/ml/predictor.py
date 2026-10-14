from __future__ import annotations
import os
from datetime import datetime, timedelta
import joblib
import pandas as pd
from sqlalchemy.orm import Session
from app.models.entities import MarketCandle, Symbol
from app.ml.features import FEATURE_COLUMNS, compute_features
from app.ml.labeler import LABEL_BUY, LABEL_HOLD, LABEL_MAP, LABEL_SELL

MODEL_PATH = os.path.join(os.path.dirname(__file__), 'models', 'xgboost_model_5m.joblib')
_CACHED_ARTIFACT = None


def get_model_artifact() -> dict | None:
    global _CACHED_ARTIFACT
    if _CACHED_ARTIFACT is None:
        if os.path.exists(MODEL_PATH):
            _CACHED_ARTIFACT = joblib.load(MODEL_PATH)
    return _CACHED_ARTIFACT


def predict_ai_signal(
    db: Session,
    symbol: Symbol,
    timeframe: str = '5m',
    min_candles: int = 60,
    confidence_threshold: float | None = None,
) -> dict:
    """
    Inference endpoint: evaluates the latest market data for a given symbol
    using the trained XGBoost model.
    """
    artifact = get_model_artifact()
    if not artifact:
        return {
            'action': 'HOLD',
            'confidence': 0.0,
            'probabilities': {'HOLD': 1.0, 'BUY': 0.0, 'SELL': 0.0},
            'reason': ['AI Model not loaded or not yet trained.'],
            'model_ready': False,
        }

    model = artifact['model']
    feature_cols = artifact['feature_columns']

    if artifact.get('timeframe') and artifact['timeframe'] != timeframe:
        return {
            'action': 'HOLD',
            'confidence': 0.0,
            'probabilities': {'HOLD': 1.0, 'BUY': 0.0, 'SELL': 0.0},
            'reason': [f"Model trained on {artifact['timeframe']} but asked for {timeframe}; retrain required."],
            'model_ready': False,
        }
    if confidence_threshold is None:
        confidence_threshold = float(artifact.get('confidence_threshold', 0.48))

    # Fetch last 120 candles for proper indicator warmup
    candles = (
        db.query(MarketCandle)
        .filter(MarketCandle.symbol_id == symbol.id, MarketCandle.timeframe == timeframe)
        .order_by(MarketCandle.open_time.desc())
        .limit(300)
        .all()
    )

    if len(candles) < min_candles:
        return {
            'action': 'HOLD',
            'confidence': 0.0,
            'probabilities': {'HOLD': 1.0, 'BUY': 0.0, 'SELL': 0.0},
            'reason': [f'Candle count ({len(candles)}) below minimum ({min_candles}).'],
            'model_ready': True,
        }

    candles = list(reversed(candles))
    # Training only saw closed candles; drop the still-forming one to avoid train/serve skew.
    tf_min = int(timeframe[:-1]) * {'m': 1, 'h': 60, 'd': 1440}[timeframe[-1]]
    if candles and candles[-1].open_time + timedelta(minutes=tf_min) > datetime.utcnow():
        candles = candles[:-1]
    if len(candles) < min_candles:
        return {
            'action': 'HOLD',
            'confidence': 0.0,
            'probabilities': {'HOLD': 1.0, 'BUY': 0.0, 'SELL': 0.0},
            'reason': [f'Closed candle count ({len(candles)}) below minimum ({min_candles}).'],
            'model_ready': True,
        }
    df_raw = pd.DataFrame([{
        'open_time': c.open_time,
        'open_price': c.open_price,
        'high_price': c.high_price,
        'low_price': c.low_price,
        'close_price': c.close_price,
        'volume': c.volume,
    } for c in candles])

    feat_df = compute_features(df_raw)
    feat_df[feature_cols] = feat_df[feature_cols].replace([float('inf'), float('-inf')], float('nan')).ffill().fillna(0.0)

    # Take the latest candle features
    latest_row = feat_df.iloc[[-1]][feature_cols]

    # Predict probabilities: [P(HOLD), P(BUY), P(SELL)]
    probs = model.predict_proba(latest_row)[0]
    p_hold = float(probs[LABEL_HOLD])
    p_buy = float(probs[LABEL_BUY])
    p_sell = float(probs[LABEL_SELL])

    reasons = []
    reasons.append(f"AI Probs -> BUY: {p_buy:.1%}, SELL: {p_sell:.1%}, HOLD: {p_hold:.1%}")

    # Decision logic
    action = 'HOLD'
    confidence = p_hold

    if p_buy >= confidence_threshold and p_buy > p_sell:
        action = 'BUY'
        confidence = p_buy
        reasons.append(f"XGBoost BUY confidence ({p_buy:.1%}) meets threshold ({confidence_threshold:.1%})")
    elif p_sell >= confidence_threshold and p_sell > p_buy:
        action = 'SELL'
        confidence = p_sell
        reasons.append(f"XGBoost SELL confidence ({p_sell:.1%}) meets threshold ({confidence_threshold:.1%})")
    else:
        reasons.append("Confidence below decision threshold -> HOLD")

    return {
        'action': action,
        'confidence': round(confidence, 4),
        'probabilities': {
            'HOLD': round(p_hold, 4),
            'BUY': round(p_buy, 4),
            'SELL': round(p_sell, 4),
        },
        'latest_close': float(df_raw['close_price'].iloc[-1]),
        'reason': reasons,
        'model_ready': True,
    }
