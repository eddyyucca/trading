from __future__ import annotations
from dataclasses import dataclass
from datetime import timedelta
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.models.entities import FeatureSnapshot, ModelVersion, PredictionLog, Symbol
from app.services.feature_engine import build_feature_snapshot


@dataclass
class PredictionDecision:
    prediction: str
    confidence: float
    score_total: int
    reason_summary: str
    market_regime: str


def confidence_from_score(score: int) -> float:
    abs_score = abs(score)
    if abs_score <= 4:
        return 0.50
    return min(0.80, 0.55 + (abs_score * 0.05))


def score_snapshot(snapshot: FeatureSnapshot, close_price: float, open_price: float) -> PredictionDecision:
    score = 0
    reasons: list[str] = []

    # Trend score
    if snapshot.ema_9 and snapshot.ema_21:
        if snapshot.ema_9 > snapshot.ema_21:
            score += 2
            reasons.append('EMA9>EMA21')
        elif snapshot.ema_9 < snapshot.ema_21:
            score -= 2
            reasons.append('EMA9<EMA21')

    if snapshot.vwap:
        if close_price > snapshot.vwap:
            score += 1
            reasons.append('close>VWAP')
        elif close_price < snapshot.vwap:
            score -= 1
            reasons.append('close<VWAP')

    # Momentum score
    if snapshot.rsi_7:
        if 45 <= snapshot.rsi_7 <= 70:
            score += 1
            reasons.append('RSI bullish healthy')
        elif 30 <= snapshot.rsi_7 <= 55:
            score -= 1
            reasons.append('RSI bearish healthy')
        elif snapshot.rsi_7 > 78 or snapshot.rsi_7 < 22:
            score -= 1 if snapshot.rsi_7 > 78 else -1
            reasons.append('RSI extreme caution')

    if close_price > open_price:
        score += 1
        reasons.append('last candle bullish')
    elif close_price < open_price:
        score -= 1
        reasons.append('last candle bearish')

    # Volume score
    if snapshot.volume_ratio:
        if snapshot.volume_ratio >= 1.20:
            score += 1 if score >= 0 else -1
            reasons.append('volume spike')
        elif snapshot.volume_ratio < 0.65:
            reasons.append('low volume penalty')
            score = 0

    # Volatility/noise penalty
    if snapshot.volatility and snapshot.volatility > 0.01:
        reasons.append('high volatility penalty')
        score = 0
    if snapshot.candle_range and snapshot.candle_range > 0.012:
        reasons.append('wide candle range penalty')
        score = 0

    # Regime score/filter
    if snapshot.market_regime == 'TRENDING_UP':
        score += 1
        reasons.append('regime trending up')
    elif snapshot.market_regime == 'TRENDING_DOWN':
        score -= 1
        reasons.append('regime trending down')
    elif snapshot.market_regime in {'SIDEWAYS', 'LOW_VOLUME', 'HIGH_VOLATILITY', 'CHAOTIC'}:
        reasons.append(f'regime {snapshot.market_regime} caution')
        if abs(score) < 6:
            score = 0

    confidence = confidence_from_score(score)
    if score >= 5:
        prediction = 'UP'
    elif score <= -5:
        prediction = 'DOWN'
    else:
        prediction = 'SKIP'
        confidence = 0.50

    return PredictionDecision(
        prediction=prediction,
        confidence=confidence,
        score_total=score,
        reason_summary=', '.join(reasons[:12]),
        market_regime=snapshot.market_regime,
    )


def get_active_model(db: Session) -> ModelVersion:
    model = db.query(ModelVersion).filter(ModelVersion.name == 'score_rule', ModelVersion.version == 'v1').first()
    if not model:
        model = ModelVersion(name='score_rule', version='v1', type='rule_based', description='Default rule model')
        db.add(model)
        db.flush()
    return model


def threshold_for_horizon(minutes: int) -> float:
    settings = get_settings()
    return settings.threshold_long if minutes >= 10 else settings.threshold_short


def run_prediction_once(db: Session) -> dict:
    settings = get_settings()
    model = get_active_model(db)
    created = 0
    skipped = 0
    details = []

    symbols = db.query(Symbol).filter(Symbol.is_active == True).all()  # noqa: E712
    for symbol in symbols:
        feature_result = build_feature_snapshot(db, symbol, settings.prediction_timeframe)
        if not feature_result:
            skipped += 1
            details.append({'symbol': symbol.symbol, 'status': 'not_enough_data'})
            continue

        decision = score_snapshot(feature_result.snapshot, feature_result.latest_close, feature_result.latest_open)
        if decision.prediction == 'SKIP' or decision.confidence < settings.min_confidence:
            skipped += 1
            details.append({'symbol': symbol.symbol, 'prediction': 'SKIP', 'confidence': decision.confidence, 'reason': decision.reason_summary})
            continue

        for horizon in settings.horizon_list:
            due_time = feature_result.snapshot.snapshot_time + timedelta(minutes=horizon)
            exists = db.query(PredictionLog).filter(
                PredictionLog.symbol_id == symbol.id,
                PredictionLog.timeframe == settings.prediction_timeframe,
                PredictionLog.prediction_time == feature_result.snapshot.snapshot_time,
                PredictionLog.horizon_minutes == horizon,
                PredictionLog.model_version_id == model.id,
            ).first()
            if exists:
                continue
            db.add(PredictionLog(
                symbol_id=symbol.id,
                timeframe=settings.prediction_timeframe,
                prediction_time=feature_result.snapshot.snapshot_time,
                entry_price=feature_result.latest_close,
                prediction=decision.prediction,
                confidence=decision.confidence,
                score_total=decision.score_total,
                horizon_minutes=horizon,
                threshold_percent=threshold_for_horizon(horizon),
                evaluation_due_time=due_time,
                model_version_id=model.id,
                feature_snapshot_id=feature_result.snapshot.id,
                market_regime=decision.market_regime,
                reason_summary=decision.reason_summary,
                status='PENDING',
            ))
            created += 1
        details.append({'symbol': symbol.symbol, 'prediction': decision.prediction, 'confidence': decision.confidence, 'score': decision.score_total})

    db.commit()
    return {'created': created, 'skipped': skipped, 'details': details}
