from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.models.entities import FeatureSnapshot, ModelVersion, PredictionLog, PredictionResult, Symbol
from app.services.feature_engine import FeatureResult, build_feature_snapshot


@dataclass
class PredictionDecision:
    prediction: str
    confidence: float
    score_total: int
    reason_summary: str
    market_regime: str


@dataclass
class TimeframeScore:
    timeframe: str
    score: int
    side: str
    blocked: bool
    reasons: list[str]


def confidence_from_score(score: int) -> float:
    abs_score = abs(score)
    if abs_score < 8:
        return 0.50
    return min(0.92, 0.58 + (abs_score * 0.025))


def side_from_score(score: int, threshold: int) -> str:
    if score >= threshold:
        return 'BUY'
    if score <= -threshold:
        return 'SELL'
    return 'HOLD'


def opposite_side(side: str) -> str:
    if side == 'BUY':
        return 'SELL'
    if side == 'SELL':
        return 'BUY'
    return 'HOLD'


def score_timeframe(timeframe: str, snapshot: FeatureSnapshot, close_price: float, open_price: float) -> TimeframeScore:
    score = 0
    reasons: list[str] = []
    blocked = False

    if snapshot.ema_9 and snapshot.ema_21 and snapshot.ema_50:
        if snapshot.ema_9 > snapshot.ema_21 > snapshot.ema_50:
            score += 4
            reasons.append(f'{timeframe}: EMA stack bullish')
        elif snapshot.ema_9 < snapshot.ema_21 < snapshot.ema_50:
            score -= 4
            reasons.append(f'{timeframe}: EMA stack bearish')
        elif snapshot.ema_9 > snapshot.ema_21 and close_price > snapshot.ema_50:
            score += 2
            reasons.append(f'{timeframe}: trend bullish')
        elif snapshot.ema_9 < snapshot.ema_21 and close_price < snapshot.ema_50:
            score -= 2
            reasons.append(f'{timeframe}: trend bearish')

    if snapshot.vwap:
        vwap_distance = abs(close_price - snapshot.vwap) / close_price
        if vwap_distance > 0.006 and timeframe == get_settings().prediction_timeframe:
            blocked = True
            reasons.append(f'{timeframe}: overextended from VWAP blocked')
        if close_price > snapshot.vwap:
            score += 2
            reasons.append(f'{timeframe}: close>VWAP')
        elif close_price < snapshot.vwap:
            score -= 2
            reasons.append(f'{timeframe}: close<VWAP')

    if snapshot.rsi_14:
        if 52 <= snapshot.rsi_14 <= 68:
            score += 2
            reasons.append(f'{timeframe}: RSI bullish')
        elif 32 <= snapshot.rsi_14 <= 48:
            score -= 2
            reasons.append(f'{timeframe}: RSI bearish')
        elif snapshot.rsi_14 > 76 or snapshot.rsi_14 < 24:
            blocked = True
            reasons.append(f'{timeframe}: RSI extreme blocked')
        elif 48 < snapshot.rsi_14 < 52:
            reasons.append(f'{timeframe}: RSI neutral')

    if close_price > open_price:
        score += 1
        reasons.append(f'{timeframe}: candle bullish')
    elif close_price < open_price:
        score -= 1
        reasons.append(f'{timeframe}: candle bearish')

    if snapshot.volume_ratio:
        if 0.80 <= snapshot.volume_ratio <= 3.50:
            score += 1 if score >= 0 else -1
            reasons.append(f'{timeframe}: volume confirmed')
        elif snapshot.volume_ratio < 0.80:
            reasons.append(f'{timeframe}: low volume caution')
        elif snapshot.volume_ratio > 4.50:
            blocked = True
            reasons.append(f'{timeframe}: abnormal volume blocked')

    if snapshot.volatility:
        if snapshot.volatility > 0.008:
            blocked = True
            reasons.append(f'{timeframe}: high volatility blocked')
        elif snapshot.volatility < 0.00012:
            blocked = True
            reasons.append(f'{timeframe}: flat volatility blocked')
    if snapshot.candle_range and snapshot.candle_range > 0.010:
        blocked = True
        reasons.append(f'{timeframe}: wide candle blocked')

    if snapshot.market_regime == 'TRENDING_UP':
        score += 2
        reasons.append(f'{timeframe}: regime up')
    elif snapshot.market_regime == 'TRENDING_DOWN':
        score -= 2
        reasons.append(f'{timeframe}: regime down')
    elif snapshot.market_regime == 'LOW_VOLUME':
        reasons.append(f'{timeframe}: regime LOW_VOLUME caution')
    elif snapshot.market_regime in {'HIGH_VOLATILITY', 'CHAOTIC'}:
        blocked = True
        reasons.append(f'{timeframe}: regime {snapshot.market_regime} blocked')
    elif snapshot.market_regime == 'SIDEWAYS':
        reasons.append(f'{timeframe}: sideways caution')

    if blocked:
        return TimeframeScore(timeframe=timeframe, score=0, side='HOLD', blocked=True, reasons=reasons)
    return TimeframeScore(timeframe=timeframe, score=score, side=side_from_score(score, 5), blocked=False, reasons=reasons)


def open_position_count(db: Session) -> int:
    return db.query(func.count(func.distinct(PredictionLog.symbol_id))).filter(
        PredictionLog.status == 'PENDING',
        PredictionLog.prediction.in_(['BUY', 'SELL', 'UP', 'DOWN']),
    ).scalar() or 0


def has_open_position(db: Session, symbol: Symbol) -> bool:
    return db.query(PredictionLog.id).filter(
        PredictionLog.symbol_id == symbol.id,
        PredictionLog.status == 'PENDING',
        PredictionLog.prediction.in_(['BUY', 'SELL', 'UP', 'DOWN']),
    ).first() is not None


def in_cooldown(db: Session, symbol: Symbol) -> bool:
    settings = get_settings()
    since = datetime.utcnow() - timedelta(minutes=settings.signal_cooldown_minutes)
    return db.query(PredictionResult.id).join(PredictionLog).filter(
        PredictionLog.symbol_id == symbol.id,
        PredictionResult.result == 'WRONG',
        PredictionResult.evaluation_time >= since,
    ).first() is not None


def performance_guard_status(db: Session) -> dict:
    settings = get_settings()
    if not settings.performance_guard_enabled:
        return {'blocked': False, 'reason': 'disabled'}

    rows = db.query(PredictionResult).order_by(PredictionResult.evaluation_time.desc()).limit(settings.performance_guard_min_results).all()
    if len(rows) < settings.performance_guard_min_results:
        return {'blocked': False, 'reason': 'not_enough_results', 'checked': len(rows)}

    correct = sum(1 for row in rows if row.result == 'CORRECT')
    wrong = sum(1 for row in rows if row.result == 'WRONG')
    denom = correct + wrong
    accuracy = (correct / denom) if denom else 0
    pnl_usd = sum(row.actual_return_percent for row in rows) * settings.paper_position_usd
    blocked = accuracy < settings.performance_guard_min_accuracy or pnl_usd < settings.performance_guard_min_pnl_usd
    return {
        'blocked': blocked,
        'checked': len(rows),
        'accuracy': round(accuracy, 4),
        'pnl_usd': round(pnl_usd, 6),
        'reason': 'recent_performance_below_threshold' if blocked else 'ok',
    }


def score_multi_timeframe(feature_results: dict[str, FeatureResult], primary_timeframe: str) -> PredictionDecision:
    primary_result = feature_results[primary_timeframe]
    primary = score_timeframe(
        primary_timeframe,
        primary_result.snapshot,
        primary_result.latest_close,
        primary_result.latest_open,
    )
    scores = {
        timeframe: score_timeframe(timeframe, result.snapshot, result.latest_close, result.latest_open)
        for timeframe, result in feature_results.items()
        if timeframe != primary_timeframe
    }

    reasons = primary.reasons[:]
    if primary.blocked or primary.side == 'HOLD' or abs(primary.score) < 7:
        reasons.append(f'{primary_timeframe}: primary setup not strong enough')
        return PredictionDecision('HOLD', 0.50, primary.score, ', '.join(reasons[:14]), primary_result.snapshot.market_regime)

    side = primary.side
    opposite = opposite_side(side)
    confirmation_count = 0
    confirmation_bonus = 0
    for timeframe, timeframe_score in scores.items():
        reasons.extend(timeframe_score.reasons[:5])
        if timeframe_score.blocked:
            reasons.append(f'{timeframe}: confirmation blocked')
            return PredictionDecision('HOLD', 0.50, primary.score, ', '.join(reasons[:14]), primary_result.snapshot.market_regime)
        if timeframe_score.side == opposite and abs(timeframe_score.score) >= 4:
            reasons.append(f'{timeframe}: opposite trend blocked')
            return PredictionDecision('HOLD', 0.50, primary.score, ', '.join(reasons[:14]), primary_result.snapshot.market_regime)
        if timeframe_score.side == side and abs(timeframe_score.score) >= 4:
            confirmation_count += 1
            confirmation_bonus += min(4, abs(timeframe_score.score))

    if confirmation_count < 1:
        reasons.append('multi-timeframe confirmation missing')
        return PredictionDecision('HOLD', 0.50, primary.score, ', '.join(reasons[:14]), primary_result.snapshot.market_regime)

    signed_bonus = confirmation_bonus if side == 'BUY' else -confirmation_bonus
    total_score = primary.score + signed_bonus
    confidence = min(0.94, confidence_from_score(total_score) + (confirmation_count * 0.04))

    return PredictionDecision(
        prediction=side,
        confidence=confidence,
        score_total=total_score,
        reason_summary=', '.join(reasons[:14]),
        market_regime=primary_result.snapshot.market_regime,
    )


def get_active_model(db: Session) -> ModelVersion:
    model = db.query(ModelVersion).filter(ModelVersion.name == 'score_rule', ModelVersion.version == 'v5_futures_aggressive').first()
    if not model:
        model = ModelVersion(
            name='score_rule',
            version='v5_futures_aggressive',
            type='rule_based',
            description='Aggressive futures paper model with leverage, wider TP, capped positions and performance guard.',
            parameters_json='{"primary_threshold": 7, "confirmation_threshold": 4, "single_horizon": true, "cooldown_minutes": 5, "max_open_positions": 5, "futures_leverage": 10}',
        )
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
    open_positions_at_start = open_position_count(db)
    opened_symbols_this_run: set[int] = set()
    guard = performance_guard_status(db)
    if guard['blocked']:
        return {'created': 0, 'skipped': 0, 'guard': guard, 'details': []}

    symbols = db.query(Symbol).filter(Symbol.is_active == True).all()  # noqa: E712
    for symbol in symbols:
        if open_positions_at_start + len(opened_symbols_this_run) >= settings.max_open_positions:
            skipped += 1
            details.append({'symbol': symbol.symbol, 'status': 'max_open_positions_reached'})
            continue
        if has_open_position(db, symbol):
            skipped += 1
            details.append({'symbol': symbol.symbol, 'status': 'open_position_exists'})
            continue
        if in_cooldown(db, symbol):
            skipped += 1
            details.append({'symbol': symbol.symbol, 'status': 'cooldown_after_loss'})
            continue

        required_timeframes = []
        for timeframe in [settings.prediction_timeframe, '5m', '15m']:
            if timeframe in settings.timeframe_list and timeframe not in required_timeframes:
                required_timeframes.append(timeframe)
        feature_results = {
            timeframe: build_feature_snapshot(db, symbol, timeframe, min_rows=90)
            for timeframe in required_timeframes
        }
        missing_timeframes = [timeframe for timeframe, result in feature_results.items() if result is None]
        if missing_timeframes:
            skipped += 1
            details.append({'symbol': symbol.symbol, 'status': 'not_enough_data', 'missing_timeframes': missing_timeframes})
            continue

        valid_feature_results = {timeframe: result for timeframe, result in feature_results.items() if result is not None}
        feature_result = valid_feature_results[settings.prediction_timeframe]
        decision = score_multi_timeframe(valid_feature_results, settings.prediction_timeframe)
        if decision.prediction == 'HOLD' or decision.confidence < settings.min_confidence:
            skipped += 1
            details.append({'symbol': symbol.symbol, 'decision': 'HOLD', 'confidence': decision.confidence, 'reason': decision.reason_summary})
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
        if decision.prediction in {'BUY', 'SELL'}:
            opened_symbols_this_run.add(symbol.id)
        details.append({'symbol': symbol.symbol, 'decision': decision.prediction, 'confidence': decision.confidence, 'score': decision.score_total})

    db.commit()
    return {'created': created, 'skipped': skipped, 'details': details}
