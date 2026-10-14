from __future__ import annotations
from datetime import datetime
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.models.entities import MarketCandle, PredictionLog, PredictionResult
from app.services.risk import build_risk_plan, signal_side


def find_actual_price(db: Session, pred: PredictionLog) -> float | None:
    candle = db.query(MarketCandle).filter(
        MarketCandle.symbol_id == pred.symbol_id,
        MarketCandle.timeframe == pred.timeframe,
        MarketCandle.open_time >= pred.evaluation_due_time,
    ).order_by(MarketCandle.open_time.asc()).first()
    return candle.close_price if candle else None


def find_risk_exit(db: Session, pred: PredictionLog, now: datetime) -> tuple[datetime, float, str, str] | None:
    settings = get_settings()
    side = signal_side(pred.prediction)
    if side not in {'BUY', 'SELL'}:
        return None
    risk_plan = build_risk_plan(side, pred.entry_price)
    until_time = min(now, pred.evaluation_due_time)
    candles = db.query(MarketCandle).filter(
        MarketCandle.symbol_id == pred.symbol_id,
        MarketCandle.timeframe == pred.timeframe,
        MarketCandle.open_time > pred.prediction_time,
        MarketCandle.open_time <= until_time,
    ).order_by(MarketCandle.open_time.asc()).all()

    breakeven_triggered = False
    # If favorable move reaches 50% of TP distance, activate Breakeven Stop
    be_profit_threshold = settings.take_profit_percent * 0.50

    for candle in candles:
        # Breakeven state is the one known BEFORE this candle: intrabar order is
        # unknown, so a candle that both arms breakeven and touches entry is not
        # assumed to have armed it first.
        effective_sl = pred.entry_price if breakeven_triggered else risk_plan.stop_loss_price
        if side == 'BUY':
            stop_hit = candle.low_price <= effective_sl
            profit_hit = candle.high_price >= risk_plan.take_profit_price
            favorable_move = (candle.high_price / pred.entry_price) - 1
        else:
            stop_hit = candle.high_price >= effective_sl
            profit_hit = candle.low_price <= risk_plan.take_profit_price
            favorable_move = 1 - (candle.low_price / pred.entry_price)

        # Conservative: if TP and SL are both touched in the same candle, the stop wins.
        if stop_hit:
            if breakeven_triggered:
                return candle.open_time, pred.entry_price, f'BE_{side}', 'NEUTRAL'
            return candle.open_time, risk_plan.stop_loss_price, f'SL_{side}', 'WRONG'
        if profit_hit:
            return candle.open_time, risk_plan.take_profit_price, f'TP_{side}', 'CORRECT'

        if favorable_move >= be_profit_threshold and settings.breakeven_stop_enabled:
            breakeven_triggered = True

        if not breakeven_triggered:
            if side == 'BUY':
                near_stop_hit = ((candle.close_price / pred.entry_price) - 1) <= -(settings.stop_loss_percent * settings.near_stop_percent)
            else:
                near_stop_hit = ((pred.entry_price / candle.close_price) - 1) <= -(settings.stop_loss_percent * settings.near_stop_percent)
            if near_stop_hit:
                return candle.open_time, candle.close_price, f'NS_{side}', 'WRONG'
    return None


def actual_direction(entry_price: float, actual_price: float, threshold: float) -> tuple[str, float]:
    actual_return = (actual_price / entry_price) - 1
    if actual_return > threshold:
        return 'BUY', actual_return
    if actual_return < -threshold:
        return 'SELL', actual_return
    return 'NEUTRAL', actual_return


def prediction_side(prediction: str) -> str:
    return signal_side(prediction)


def evaluate_pending(db: Session, limit: int = 200) -> dict:
    settings = get_settings()
    now = datetime.utcnow()
    pending = db.query(PredictionLog).filter(
        PredictionLog.status == 'PENDING',
    ).order_by(PredictionLog.evaluation_due_time.asc()).limit(limit).all()

    evaluated = 0
    waiting_data = 0
    stopped = 0
    near_stopped = 0
    take_profit = 0
    for pred in pending:
        risk_exit = find_risk_exit(db, pred, now)
        if risk_exit:
            evaluation_time, actual_price, actual_direction_value, result = risk_exit
            return_value = (actual_price / pred.entry_price) - 1
            if prediction_side(pred.prediction) == 'SELL':
                return_value = -return_value
            return_value -= settings.round_trip_cost
            if actual_direction_value.startswith('SL_'):
                stopped += 1
            elif actual_direction_value.startswith('NS_'):
                near_stopped += 1
            elif actual_direction_value.startswith('TP_'):
                take_profit += 1
        elif pred.evaluation_due_time > now:
            waiting_data += 1
            continue
        else:
            actual_price = find_actual_price(db, pred)
            if actual_price is None:
                waiting_data += 1
                continue
            direction, raw_return_value = actual_direction(pred.entry_price, actual_price, pred.threshold_percent)
            paper_side = prediction_side(pred.prediction)
            return_value = raw_return_value if paper_side == 'BUY' else -raw_return_value
            actual_direction_value = direction
            evaluation_time = pred.evaluation_due_time
            if direction == 'NEUTRAL':
                if return_value > 0:
                    result = 'CORRECT'
                    actual_direction_value = f'EXIT_{paper_side}'
                elif return_value < 0:
                    result = 'WRONG'
                    actual_direction_value = f'EXIT_{paper_side}'
                else:
                    result = 'NEUTRAL'
            elif direction == paper_side:
                result = 'CORRECT'
            else:
                result = 'WRONG'

        if not risk_exit:
            return_value -= settings.round_trip_cost
            if result == 'CORRECT' and return_value <= 0:
                result = 'WRONG'

        pred.status = 'EVALUATED'
        db.add(PredictionResult(
            prediction_id=pred.id,
            evaluation_time=evaluation_time,
            actual_price=actual_price,
            actual_return_percent=return_value,
            actual_direction=actual_direction_value,
            result=result,
        ))
        evaluated += 1
    db.commit()
    return {
        'evaluated': evaluated,
        'waiting_data': waiting_data,
        'checked': len(pending),
        'take_profit': take_profit,
        'stopped': stopped,
        'near_stopped': near_stopped,
        'paper_position_usd': settings.paper_position_usd,
    }
