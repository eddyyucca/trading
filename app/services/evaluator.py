from __future__ import annotations
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.entities import MarketCandle, PredictionLog, PredictionResult


def find_actual_price(db: Session, pred: PredictionLog) -> float | None:
    candle = db.query(MarketCandle).filter(
        MarketCandle.symbol_id == pred.symbol_id,
        MarketCandle.timeframe == pred.timeframe,
        MarketCandle.open_time >= pred.evaluation_due_time,
    ).order_by(MarketCandle.open_time.asc()).first()
    return candle.close_price if candle else None


def actual_direction(entry_price: float, actual_price: float, threshold: float) -> tuple[str, float]:
    actual_return = (actual_price / entry_price) - 1
    if actual_return > threshold:
        return 'UP', actual_return
    if actual_return < -threshold:
        return 'DOWN', actual_return
    return 'NEUTRAL', actual_return


def evaluate_pending(db: Session, limit: int = 200) -> dict:
    now = datetime.utcnow()
    pending = db.query(PredictionLog).filter(
        PredictionLog.status == 'PENDING',
        PredictionLog.evaluation_due_time <= now,
    ).order_by(PredictionLog.evaluation_due_time.asc()).limit(limit).all()

    evaluated = 0
    waiting_data = 0
    for pred in pending:
        actual_price = find_actual_price(db, pred)
        if actual_price is None:
            waiting_data += 1
            continue
        direction, return_value = actual_direction(pred.entry_price, actual_price, pred.threshold_percent)
        if direction == 'NEUTRAL':
            result = 'NEUTRAL'
        elif direction == pred.prediction:
            result = 'CORRECT'
        else:
            result = 'WRONG'

        pred.status = 'EVALUATED'
        db.add(PredictionResult(
            prediction_id=pred.id,
            evaluation_time=pred.evaluation_due_time,
            actual_price=actual_price,
            actual_return_percent=return_value,
            actual_direction=direction,
            result=result,
        ))
        evaluated += 1
    db.commit()
    return {'evaluated': evaluated, 'waiting_data': waiting_data, 'checked': len(pending)}
