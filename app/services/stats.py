from __future__ import annotations
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.models.entities import MarketCandle, PredictionLog, PredictionResult
from app.services.risk import paper_pnl_usd, signal_side


def summary(db: Session) -> dict:
    settings = get_settings()
    total_predictions = db.query(func.count(PredictionLog.id)).filter(PredictionLog.prediction.in_(['BUY', 'SELL', 'UP', 'DOWN'])).scalar() or 0
    pending = db.query(func.count(PredictionLog.id)).filter(PredictionLog.status == 'PENDING').scalar() or 0
    correct = db.query(func.count(PredictionResult.id)).filter(PredictionResult.result == 'CORRECT').scalar() or 0
    wrong = db.query(func.count(PredictionResult.id)).filter(PredictionResult.result == 'WRONG').scalar() or 0
    neutral = db.query(func.count(PredictionResult.id)).filter(PredictionResult.result == 'NEUTRAL').scalar() or 0
    take_profit = db.query(func.count(PredictionResult.id)).filter(PredictionResult.actual_direction.like('TP_%')).scalar() or 0
    stop_loss = db.query(func.count(PredictionResult.id)).filter(PredictionResult.actual_direction.like('SL_%')).scalar() or 0
    near_stop = db.query(func.count(PredictionResult.id)).filter(PredictionResult.actual_direction.like('NS_%')).scalar() or 0
    denom = correct + wrong
    directional_accuracy = (correct / denom) if denom else 0
    total_accuracy = (correct / total_predictions) if total_predictions else 0
    realized_return = db.query(func.coalesce(func.sum(PredictionResult.actual_return_percent), 0)).scalar() or 0
    leverage = settings.futures_leverage if settings.futures_mode else 1.0
    notional_usd = settings.paper_position_usd * leverage
    realized_pnl_usd = float(realized_return) * notional_usd

    pending_rows = db.query(PredictionLog).filter(
        PredictionLog.status == 'PENDING',
        PredictionLog.prediction.in_(['BUY', 'SELL', 'UP', 'DOWN']),
    ).all()
    unrealized_pnl_usd = 0.0
    for pred in pending_rows:
        latest_candle = db.query(MarketCandle).filter(
            MarketCandle.symbol_id == pred.symbol_id,
            MarketCandle.timeframe == pred.timeframe,
        ).order_by(MarketCandle.open_time.desc()).first()
        if latest_candle:
            unrealized_pnl_usd += paper_pnl_usd(signal_side(pred.prediction), pred.entry_price, latest_candle.close_price)

    total_pnl_usd = realized_pnl_usd + unrealized_pnl_usd
    return {
        'total_predictions': total_predictions,
        'pending': pending,
        'correct': correct,
        'wrong': wrong,
        'neutral': neutral,
        'take_profit': take_profit,
        'stop_loss': stop_loss,
        'near_stop': near_stop,
        'directional_accuracy': round(directional_accuracy * 100, 2),
        'total_accuracy': round(total_accuracy * 100, 2),
        'paper_position_usd': settings.paper_position_usd,
        'futures_mode': settings.futures_mode,
        'futures_leverage': leverage,
        'position_notional_usd': notional_usd,
        'realized_pnl_usd': round(realized_pnl_usd, 6),
        'unrealized_pnl_usd': round(unrealized_pnl_usd, 6),
        'total_pnl_usd': round(total_pnl_usd, 6),
        'sample_warning': total_predictions < 1000,
    }
