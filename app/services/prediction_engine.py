"""
Professional Futures Signal Engine — v6
========================================
Philosophy  (think like a top-tier futures trader):
  1. Trade WITH the macro trend (EMA-200).  Counter-trend = no trade.
  2. Trend structure must be established (EMA 9/21/50 stack).
  3. Momentum engine confirms direction (MACD histogram).
  4. Price at a fair position — not overextended (VWAP, BB %B).
  5. Oscillators agree (RSI-14 zone, RSI-7 momentum, StochRSI).
  6. Volume participates — institutions are moving price.
  7. Candle quality confirms intent (pattern + body strength).
  8. Trend strength verified (ADX >= 20).
  9. Higher-timeframe must not oppose the primary signal.
 10. When in doubt, stay out.  Cash is a position.

Score ceiling per timeframe: ±24 points.
Primary threshold to generate a signal: absolute score >= 13 (≈ 54% confluence).
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.models.entities import FeatureSnapshot, ModelVersion, PredictionLog, PredictionResult, Symbol
from app.services.feature_engine import FeatureResult, build_feature_snapshot
from app.ml.predictor import predict_ai_signal

# ── Constants ───────────────────────────────────────────────────────────────
_PRIMARY_SCORE_THRESHOLD    = 15   # minimum primary score — raised for higher precision
_SIDE_SCORE_THRESHOLD       = 8    # minimum score to call BUY/SELL in a timeframe
_CONFIRM_SCORE_THRESHOLD    = 5    # minimum score in a confirmation timeframe
_HIGHER_TF_BLOCK_THRESHOLD  = 6    # opposing higher-TF score that vetoes the signal
_STOCH_OB_BLOCK             = 75   # StochRSI overbought — block BUY above this
_STOCH_OS_BLOCK             = 25   # StochRSI oversold — block SELL below this
_HIGHER_TIMEFRAMES          = {'15m', '30m', '1h', '4h', '1d'}


@dataclass
class PredictionDecision:
    prediction:    str
    confidence:    float
    score_total:   int
    reason_summary: str
    market_regime: str


@dataclass
class TimeframeScore:
    timeframe: str
    score:     int
    side:      str
    blocked:   bool
    reasons:   list[str]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _side(score: int, threshold: int) -> str:
    if score >= threshold:
        return 'BUY'
    if score <= -threshold:
        return 'SELL'
    return 'HOLD'


def _opposite(side: str) -> str:
    return {'BUY': 'SELL', 'SELL': 'BUY'}.get(side, 'HOLD')


def _confidence(total_score: int) -> float:
    """Smooth confidence curve.  Score 13 → ~0.73, score 20 → ~0.88, cap 0.95."""
    return min(0.95, 0.44 + abs(total_score) * 0.022)


def _threshold_for_horizon(minutes: int) -> float:
    s = get_settings()
    return s.threshold_long if minutes >= 10 else s.threshold_short


# ── Core Timeframe Scorer ────────────────────────────────────────────────────

def score_timeframe(
    timeframe:   str,
    snapshot:    FeatureSnapshot,
    close_price: float,
) -> TimeframeScore:
    """
    Score one timeframe.  Returns a TimeframeScore with score in [-24, +24].
    Positive = bullish, negative = bearish.
    A blocked result forces HOLD regardless of score.
    """
    score   = 0
    reasons: list[str] = []
    blocked = False
    settings = get_settings()

    # ── 0. Hard pre-filters (always checked first) ───────────────────────
    if snapshot.market_regime in {'HIGH_VOLATILITY', 'CHAOTIC', 'SIDEWAYS', 'LOW_VOLUME'}:
        reasons.append(f'{timeframe}: regime {snapshot.market_regime} blocked')
        return TimeframeScore(timeframe, 0, 'HOLD', True, reasons)

    if snapshot.volatility:
        if snapshot.volatility > 0.008:
            reasons.append(f'{timeframe}: excessive volatility blocked')
            return TimeframeScore(timeframe, 0, 'HOLD', True, reasons)
        if snapshot.volatility < 0.0003:
            reasons.append(f'{timeframe}: flat/dead market blocked')
            return TimeframeScore(timeframe, 0, 'HOLD', True, reasons)

    if snapshot.candle_range and snapshot.candle_range > 0.010:
        reasons.append(f'{timeframe}: spike candle blocked')
        return TimeframeScore(timeframe, 0, 'HOLD', True, reasons)

    if snapshot.volume_ratio is not None and snapshot.volume_ratio < 0.30:
        reasons.append(f'{timeframe}: near-zero volume blocked')
        return TimeframeScore(timeframe, 0, 'HOLD', True, reasons)

    # ── 1. Macro Trend — EMA-200  (+3 / -3) ─────────────────────────────
    if snapshot.ema_200:
        clearance = 0.0002  # 0.02 % buffer to avoid noise at the line
        if close_price > snapshot.ema_200 * (1 + clearance):
            score += 3
            reasons.append(f'{timeframe}: above EMA200 macro-bull')
        elif close_price < snapshot.ema_200 * (1 - clearance):
            score -= 3
            reasons.append(f'{timeframe}: below EMA200 macro-bear')
        else:
            reasons.append(f'{timeframe}: EMA200 neutral zone')

    # ── 2. Trend Structure — EMA 9/21/50 stack  (+4 / -4) ───────────────
    if snapshot.ema_9 and snapshot.ema_21 and snapshot.ema_50:
        if snapshot.ema_9 > snapshot.ema_21 > snapshot.ema_50:
            score += 4
            reasons.append(f'{timeframe}: EMA stack bull')
        elif snapshot.ema_9 < snapshot.ema_21 < snapshot.ema_50:
            score -= 4
            reasons.append(f'{timeframe}: EMA stack bear')
        elif snapshot.ema_9 > snapshot.ema_21 and close_price > snapshot.ema_50:
            score += 2
            reasons.append(f'{timeframe}: EMA partial bull')
        elif snapshot.ema_9 < snapshot.ema_21 and close_price < snapshot.ema_50:
            score -= 2
            reasons.append(f'{timeframe}: EMA partial bear')
        else:
            reasons.append(f'{timeframe}: EMA mixed')

    # ── 3. Momentum Engine — MACD with slope  (+3 / -3) ─────────────────
    if snapshot.macd_hist is not None and snapshot.macd_line is not None:
        hist  = snapshot.macd_hist
        prev  = snapshot.macd_hist_prev
        if hist > 0:
            if prev is not None and prev < 0:
                score += 3; reasons.append(f'{timeframe}: MACD fresh bull cross')
            elif prev is not None and hist > prev:
                score += 2; reasons.append(f'{timeframe}: MACD hist accelerating')
            else:
                score += 1; reasons.append(f'{timeframe}: MACD hist decelerating')
        elif hist < 0:
            if prev is not None and prev > 0:
                score -= 3; reasons.append(f'{timeframe}: MACD fresh bear cross')
            elif prev is not None and hist < prev:
                score -= 2; reasons.append(f'{timeframe}: MACD hist bear accel')
            else:
                score -= 1; reasons.append(f'{timeframe}: MACD hist bear decel')
        if snapshot.macd_signal is not None:
            if snapshot.macd_line > snapshot.macd_signal:
                score += 1; reasons.append(f'{timeframe}: MACD line>signal')
            else:
                score -= 1; reasons.append(f'{timeframe}: MACD line<signal')

    # ── 4. VWAP Position  (+2 / -2) — block if overextended ─────────────
    if snapshot.vwap:
        dist = abs(close_price - snapshot.vwap) / close_price
        if dist > 0.007 and timeframe == settings.prediction_timeframe:
            reasons.append(f'{timeframe}: overextended VWAP blocked')
            return TimeframeScore(timeframe, 0, 'HOLD', True, reasons)
        if close_price > snapshot.vwap:
            score += 2
            reasons.append(f'{timeframe}: above VWAP')
        else:
            score -= 2
            reasons.append(f'{timeframe}: below VWAP')

    # ── 5. RSI-14 Zone  (+2 / -2) ───────────────────────────────────────
    if snapshot.rsi_14:
        r = snapshot.rsi_14
        if r > 76 or r < 24:
            reasons.append(f'{timeframe}: RSI extreme blocked')
            return TimeframeScore(timeframe, 0, 'HOLD', True, reasons)
        if 48 <= r <= 65:
            score += 2; reasons.append(f'{timeframe}: RSI bull zone')
        elif 35 <= r <= 52:
            score -= 2; reasons.append(f'{timeframe}: RSI bear zone')
        elif 65 < r <= 76:
            reasons.append(f'{timeframe}: RSI overbought caution')  # no bonus — extended
        elif 24 <= r < 35:
            reasons.append(f'{timeframe}: RSI oversold caution')    # no bonus — extended
        else:
            reasons.append(f'{timeframe}: RSI neutral')

    # ── 6. RSI-7 Momentum Lead  (+1 / -1) ───────────────────────────────
    if snapshot.rsi_7 and snapshot.rsi_14:
        diff = snapshot.rsi_7 - snapshot.rsi_14
        if diff > 3:
            score += 1; reasons.append(f'{timeframe}: RSI7 leading bull')
        elif diff < -3:
            score -= 1; reasons.append(f'{timeframe}: RSI7 leading bear')

    # ── 7. Bollinger Bands %B — position quality  (+1 / -1) ─────────────
    if snapshot.bb_percent_b is not None:
        bb = snapshot.bb_percent_b
        if 0.55 <= bb <= 1.0:
            score += 1; reasons.append(f'{timeframe}: BB upper half')
        elif 0.0 <= bb <= 0.45:
            score -= 1; reasons.append(f'{timeframe}: BB lower half')
        elif bb > 1.0:
            reasons.append(f'{timeframe}: BB overextended upper')
        elif bb < 0.0:
            reasons.append(f'{timeframe}: BB overextended lower')

    # ── 8. Stochastic RSI  (+2 / -2) ────────────────────────────────────
    if snapshot.stoch_rsi_k is not None:
        k = snapshot.stoch_rsi_k
        if k > _STOCH_OB_BLOCK:
            score -= 2; reasons.append(f'{timeframe}: StochRSI overbought({k:.0f}) fade')
        elif k < _STOCH_OS_BLOCK:
            score += 2; reasons.append(f'{timeframe}: StochRSI oversold({k:.0f}) fade')
        elif 55 <= k <= _STOCH_OB_BLOCK:
            score += 1; reasons.append(f'{timeframe}: StochRSI bull')
        elif _STOCH_OS_BLOCK <= k <= 45:
            score -= 1; reasons.append(f'{timeframe}: StochRSI bear')
        else:
            reasons.append(f'{timeframe}: StochRSI neutral')

    # ── 9. Candle Pattern  (+2 / -2) ────────────────────────────────────
    pattern = snapshot.candle_pattern or ''
    candle_scores = {
        'BULL_MARUBOZU': +2, 'BEAR_MARUBOZU': -2,
        'BULLISH':       +1, 'BEARISH':       -1,
        'HAMMER':        +1, 'SHOOTING_STAR': -1,
    }
    if pattern in candle_scores:
        delta = candle_scores[pattern]
        score += delta
        reasons.append(f'{timeframe}: candle={pattern}')
    elif pattern == 'DOJI':
        reasons.append(f'{timeframe}: DOJI indecision')

    # ── 10. Volume Confirmation  (+2 / -2) ──────────────────────────────
    if snapshot.volume_ratio is not None:
        vr = snapshot.volume_ratio
        if vr > 4.5:
            reasons.append(f'{timeframe}: abnormal volume blocked')
            return TimeframeScore(timeframe, 0, 'HOLD', True, reasons)
        if 1.2 <= vr <= 4.5:
            score += 2 if score >= 0 else -2
            reasons.append(f'{timeframe}: strong volume')
        elif 0.8 <= vr < 1.2:
            score += 1 if score >= 0 else -1
            reasons.append(f'{timeframe}: normal volume')
        elif 0.50 <= vr < 0.8:
            reasons.append(f'{timeframe}: low volume caution')
        else:
            reasons.append(f'{timeframe}: very low volume caution')

    # ── 11. Regime & ADX Trend Strength  (+2 / +1) ──────────────────────
    if snapshot.market_regime == 'TRENDING_UP':
        score += 2; reasons.append(f'{timeframe}: regime trending up')
    elif snapshot.market_regime == 'TRENDING_DOWN':
        score -= 2; reasons.append(f'{timeframe}: regime trending down')
    elif snapshot.market_regime == 'SIDEWAYS':
        reasons.append(f'{timeframe}: regime sideways (no trend bonus)')
    elif snapshot.market_regime == 'LOW_VOLUME':
        reasons.append(f'{timeframe}: regime low volume (caution)')

    if snapshot.adx_14 is not None:
        if snapshot.adx_14 < 18:
            # Weak trend: attenuate score toward zero slightly
            score = int(score * 0.7)
            reasons.append(f'{timeframe}: ADX={snapshot.adx_14:.1f} weak, score reduced')
        elif snapshot.adx_14 >= 30:
            bonus = 1 if score >= 0 else -1
            score += bonus
            reasons.append(f'{timeframe}: ADX={snapshot.adx_14:.1f} strong trend bonus')

    if blocked:
        return TimeframeScore(timeframe, 0, 'HOLD', True, reasons)

    return TimeframeScore(
        timeframe = timeframe,
        score     = score,
        side      = _side(score, _SIDE_SCORE_THRESHOLD),
        blocked   = False,
        reasons   = reasons,
    )


# ── Multi-Timeframe Orchestrator ─────────────────────────────────────────────

def score_multi_timeframe(
    feature_results: dict[str, FeatureResult],
    primary_tf:      str,
) -> PredictionDecision:
    """
    Combine timeframes using a tiered confirmation model:
      - Primary (e.g. 5m): must score >= 13 and produce a clear side.
      - Higher TFs (e.g. 15m): must NOT clearly oppose; bonus if they confirm.
      - Lower TFs (e.g. 1m): nice to have; bonus if they confirm.
    """
    pri_res = feature_results[primary_tf]
    primary = score_timeframe(primary_tf, pri_res.snapshot, pri_res.latest_close)
    reasons = primary.reasons[:]

    # Primary must be strong enough to proceed
    if primary.blocked or primary.side == 'HOLD' or abs(primary.score) < _PRIMARY_SCORE_THRESHOLD:
        reasons.append(f'{primary_tf}: primary score={primary.score} below threshold={_PRIMARY_SCORE_THRESHOLD}')
        return PredictionDecision('HOLD', 0.50, primary.score, ', '.join(reasons[:16]), pri_res.snapshot.market_regime)

    side      = primary.side
    opposite  = _opposite(side)

    # ── Directional exhaustion guards (checked after side is known) ──────
    snap = pri_res.snapshot
    if snap.stoch_rsi_k is not None:
        k = snap.stoch_rsi_k
        if side == 'BUY' and k > _STOCH_OB_BLOCK:
            reasons.append(f'{primary_tf}: StochRSI {k:.0f} overbought — blocking BUY')
            return PredictionDecision('HOLD', 0.50, primary.score, ', '.join(reasons[:16]), pri_res.snapshot.market_regime)
        if side == 'SELL' and k < _STOCH_OS_BLOCK:
            reasons.append(f'{primary_tf}: StochRSI {k:.0f} oversold — blocking SELL')
            return PredictionDecision('HOLD', 0.50, primary.score, ', '.join(reasons[:16]), pri_res.snapshot.market_regime)

    pattern = snap.candle_pattern or ''
    if side == 'BUY' and pattern in {'BEAR_MARUBOZU', 'SHOOTING_STAR'}:
        reasons.append(f'{primary_tf}: candle {pattern} opposes BUY — blocking')
        return PredictionDecision('HOLD', 0.50, primary.score, ', '.join(reasons[:16]), pri_res.snapshot.market_regime)
    if side == 'SELL' and pattern in {'BULL_MARUBOZU', 'HAMMER'}:
        reasons.append(f'{primary_tf}: candle {pattern} opposes SELL — blocking')
        return PredictionDecision('HOLD', 0.50, primary.score, ', '.join(reasons[:16]), pri_res.snapshot.market_regime)

    conf_count       = 0
    conf_bonus       = 0
    all_soft_blocked = True   # becomes False if any TF scores cleanly

    for tf, result in feature_results.items():
        if tf == primary_tf:
            continue
        tf_score = score_timeframe(tf, result.snapshot, result.latest_close)
        reasons.extend(tf_score.reasons[:3])
        is_higher = tf in _HIGHER_TIMEFRAMES

        if tf_score.blocked:
            hard_reason = any(
                kw in r for r in tf_score.reasons
                for kw in ('volatility blocked', 'RSI extreme blocked', 'spike candle', 'CHAOTIC', 'HIGH_VOLATILITY')
            )
            if is_higher and hard_reason:
                reasons.append(f'{tf}: hard-blocked — aborting')
                return PredictionDecision('HOLD', 0.50, primary.score, ', '.join(reasons[:16]), pri_res.snapshot.market_regime)
            # soft block (volume/partial candle) — skip but don't abort
            reasons.append(f'{tf}: soft-blocked (volume)')
            continue

        all_soft_blocked = False  # at least one TF scored

        if is_higher and tf_score.side == opposite and abs(tf_score.score) >= _HIGHER_TF_BLOCK_THRESHOLD:
            reasons.append(f'{tf}: higher-TF opposing score={tf_score.score} — aborting')
            return PredictionDecision('HOLD', 0.50, primary.score, ', '.join(reasons[:16]), pri_res.snapshot.market_regime)

        if tf_score.side == side and abs(tf_score.score) >= _CONFIRM_SCORE_THRESHOLD:
            conf_count  += 1
            conf_bonus  += min(6, abs(tf_score.score))

    if conf_count < 1:
        # Allow very strong primary signals even when all confirmation TFs are
        # soft-blocked (partial-candle volume issue).  Cap confidence at 0.80.
        _SOLO_THRESHOLD = _PRIMARY_SCORE_THRESHOLD + 4   # 17
        if all_soft_blocked and abs(primary.score) >= _SOLO_THRESHOLD:
            total_score = primary.score
            confidence  = min(0.80, _confidence(total_score))
            reasons.append('solo primary (confirmations soft-blocked)')
            return PredictionDecision(side, confidence, total_score, ', '.join(reasons[:16]), pri_res.snapshot.market_regime)
        reasons.append('no confirmation — HOLD')
        return PredictionDecision('HOLD', 0.50, primary.score, ', '.join(reasons[:16]), pri_res.snapshot.market_regime)

    signed_bonus = conf_bonus if side == 'BUY' else -conf_bonus
    total_score  = primary.score + signed_bonus
    confidence   = min(0.95, _confidence(total_score) + conf_count * 0.04)

    return PredictionDecision(
        prediction    = side,
        confidence    = confidence,
        score_total   = total_score,
        reason_summary= ', '.join(reasons[:16]),
        market_regime = pri_res.snapshot.market_regime,
    )


# ── DB Helpers ───────────────────────────────────────────────────────────────

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

    rows = (
        db.query(PredictionResult)
        .order_by(PredictionResult.evaluation_time.desc())
        .limit(settings.performance_guard_min_results)
        .all()
    )
    if len(rows) < settings.performance_guard_min_results:
        return {'blocked': False, 'reason': 'not_enough_results', 'checked': len(rows)}

    correct = sum(1 for r in rows if r.result == 'CORRECT')
    wrong   = sum(1 for r in rows if r.result == 'WRONG')
    denom   = correct + wrong
    accuracy = (correct / denom) if denom else 0
    pnl_usd  = sum(r.actual_return_percent for r in rows) * settings.paper_position_usd
    blocked  = (
        accuracy < settings.performance_guard_min_accuracy
        or pnl_usd < settings.performance_guard_min_pnl_usd
    )
    return {
        'blocked':  blocked,
        'checked':  len(rows),
        'accuracy': round(accuracy, 4),
        'pnl_usd':  round(pnl_usd, 6),
        'reason':   'recent_performance_below_threshold' if blocked else 'ok',
    }


def get_active_model(db: Session) -> ModelVersion:
    # 1. Prioritize active trained AI model
    ai_model = (
        db.query(ModelVersion)
        .filter(ModelVersion.type == 'ml_xgboost', ModelVersion.is_active == True)
        .order_by(ModelVersion.id.desc())
        .first()
    )
    if ai_model:
        return ai_model

    # 2. Fallback to rule-based model
    model = db.query(ModelVersion).filter(
        ModelVersion.name == 'score_rule',
        ModelVersion.version == 'v6_professional',
    ).first()
    if not model:
        model = ModelVersion(
            name='score_rule',
            version='v6_professional',
            type='rule_based',
            description=(
                'Professional futures engine: EMA200 macro, EMA stack, MACD, '
                'VWAP, RSI, BB %B, StochRSI, ADX, candle patterns, volume.'
            ),
            parameters_json=(
                '{"primary_threshold": 13, "side_threshold": 8, '
                '"confirmation_threshold": 5, "futures_leverage": 10}'
            ),
        )
        db.add(model)
        db.flush()
    return model


# ── Main Prediction Loop ──────────────────────────────────────────────────────

def run_prediction_once(db: Session) -> dict:
    settings = get_settings()
    model    = get_active_model(db)
    created  = 0
    skipped  = 0
    details  = []

    guard = performance_guard_status(db)
    if guard['blocked']:
        return {'created': 0, 'skipped': 0, 'guard': guard, 'details': []}

    open_at_start = open_position_count(db)
    opened_this_run: set[int] = set()

    symbols = db.query(Symbol).filter(Symbol.is_active == True).all()  # noqa: E712
    for symbol in symbols:
        if open_at_start + len(opened_this_run) >= settings.max_open_positions:
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

        # Build required timeframes: primary + available higher/lower TFs
        required_tfs: list[str] = []
        for tf in [settings.prediction_timeframe, '15m', '1m']:
            if tf in settings.timeframe_list and tf not in required_tfs:
                required_tfs.append(tf)

        feature_results = {
            tf: build_feature_snapshot(db, symbol, tf, min_rows=90)
            for tf in required_tfs
        }
        missing = [tf for tf, r in feature_results.items() if r is None]
        if missing:
            skipped += 1
            details.append({'symbol': symbol.symbol, 'status': 'not_enough_data', 'missing': missing})
            continue

        valid = {tf: r for tf, r in feature_results.items() if r is not None}
        primary_result = valid[settings.prediction_timeframe]

        if model.type == 'ml_xgboost':
            ai_res = predict_ai_signal(
                db, symbol, timeframe=settings.prediction_timeframe, confidence_threshold=0.42
            )
            regime = primary_result.snapshot.market_regime if (primary_result and primary_result.snapshot) else 'UNKNOWN'
            decision = PredictionDecision(
                prediction=ai_res['action'],
                confidence=ai_res['confidence'],
                score_total=int(ai_res['confidence'] * 100),
                reason_summary=' | '.join(ai_res['reason']),
                market_regime=regime,
            )
            min_required_conf = 0.40
        else:
            decision = score_multi_timeframe(valid, settings.prediction_timeframe)
            min_required_conf = settings.min_confidence

        if decision.prediction == 'HOLD' or decision.confidence < min_required_conf:
            skipped += 1
            details.append({
                'symbol': symbol.symbol,
                'decision': 'HOLD',
                'confidence': decision.confidence,
                'reason': decision.reason_summary,
            })
            continue

        for horizon in settings.horizon_list:
            due_time = primary_result.snapshot.snapshot_time + timedelta(minutes=horizon)
            exists = db.query(PredictionLog).filter(
                PredictionLog.symbol_id         == symbol.id,
                PredictionLog.timeframe         == settings.prediction_timeframe,
                PredictionLog.prediction_time   == primary_result.snapshot.snapshot_time,
                PredictionLog.horizon_minutes   == horizon,
                PredictionLog.model_version_id  == model.id,
            ).first()
            if exists:
                continue
            db.add(PredictionLog(
                symbol_id          = symbol.id,
                timeframe          = settings.prediction_timeframe,
                prediction_time    = primary_result.snapshot.snapshot_time,
                entry_price        = primary_result.latest_close,
                prediction         = decision.prediction,
                confidence         = decision.confidence,
                score_total        = decision.score_total,
                horizon_minutes    = horizon,
                threshold_percent  = _threshold_for_horizon(horizon),
                evaluation_due_time= due_time,
                model_version_id   = model.id,
                feature_snapshot_id= primary_result.snapshot.id,
                market_regime      = decision.market_regime,
                reason_summary     = decision.reason_summary,
                status             = 'PENDING',
            ))
            created += 1

        if decision.prediction in {'BUY', 'SELL'}:
            opened_this_run.add(symbol.id)
        details.append({
            'symbol':     symbol.symbol,
            'decision':   decision.prediction,
            'confidence': round(decision.confidence, 4),
            'score':      decision.score_total,
            'regime':     decision.market_regime,
        })

    db.commit()
    return {'created': created, 'skipped': skipped, 'details': details}
