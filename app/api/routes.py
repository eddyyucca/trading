from __future__ import annotations
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import MarketCandle, PredictionLog, PredictionResult, Symbol
from app.services.jobs import backfill, candle_count, collect_once, ensure_history, evaluator_job, job_status, prediction_job, start_jobs, stop_jobs
from app.services.risk import build_risk_plan, paper_pnl_usd, paper_return, risk_status, signal_side
from app.services.stats import summary

router = APIRouter(prefix='/api')


@router.get('/health')
def health():
    return {'status': 'ok', 'service': 'binance_prediction_tracker'}


@router.post('/reset/predictions')
def reset_predictions(db: Session = Depends(get_db)):
    """Delete all prediction logs and results. Candle history is kept."""
    results_deleted = db.query(PredictionResult).delete()
    logs_deleted    = db.query(PredictionLog).delete()
    db.commit()
    return {
        'status':           'ok',
        'results_deleted':  results_deleted,
        'logs_deleted':     logs_deleted,
    }


@router.post('/control/start')
def start_background_jobs():
    start_jobs()
    return {'status': 'started', **job_status()}


@router.post('/control/stop')
def stop_background_jobs():
    stop_jobs()
    return {'status': 'stopped', **job_status()}


@router.get('/control/status')
def background_job_status():
    return job_status()


@router.get('/symbols')
def active_symbols(db: Session = Depends(get_db)):
    rows = db.query(Symbol).filter(Symbol.is_active == True).order_by(Symbol.symbol.asc()).all()  # noqa: E712
    return [{'symbol': row.symbol, 'base_asset': row.base_asset, 'quote_asset': row.quote_asset} for row in rows]


@router.get('/history/status')
def history_status(db: Session = Depends(get_db)):
    from app.core.config import get_settings

    settings = get_settings()
    rows = []
    ready = True
    for symbol in settings.symbol_list:
        for timeframe in settings.timeframe_list:
            count = candle_count(db, symbol, timeframe)
            is_ready = count >= settings.startup_min_candles
            ready = ready and is_ready
            rows.append({
                'symbol': symbol,
                'timeframe': timeframe,
                'candles': count,
                'required': settings.startup_min_candles,
                'ready': is_ready,
            })
    return {'ready': ready, 'items': rows}


@router.post('/history/ensure')
def ensure_history_data():
    return ensure_history()


@router.post('/collector/backfill')
def backfill_candles(limit: int = Query(300, ge=1, le=1000)):
    return backfill(limit=limit)


@router.post('/collector/run-once')
def collect_one_cycle():
    return collect_once(limit=5)


@router.post('/predict/run-once')
def predict_once():
    return prediction_job()


@router.post('/evaluator/run-once')
def evaluate_once():
    return evaluator_job()


@router.get('/stats/summary')
def stats_summary(db: Session = Depends(get_db)):
    return summary(db)


@router.get('/positions/active')
def active_positions(db: Session = Depends(get_db)):
    rows = db.query(PredictionLog).join(Symbol).filter(
        PredictionLog.status == 'PENDING',
        PredictionLog.prediction.in_(['BUY', 'SELL', 'UP', 'DOWN']),
    ).order_by(PredictionLog.prediction_time.desc(), PredictionLog.horizon_minutes.asc()).limit(300).all()

    positions = {}
    for p in rows:
        side = signal_side(p.prediction)
        risk_plan = build_risk_plan(side, p.entry_price)
        latest_candle = db.query(MarketCandle).filter(
            MarketCandle.symbol_id == p.symbol_id,
            MarketCandle.timeframe == '1m',
        ).order_by(MarketCandle.open_time.desc()).first()
        if not latest_candle:
            latest_candle = db.query(MarketCandle).filter(
                MarketCandle.symbol_id == p.symbol_id,
                MarketCandle.timeframe == p.timeframe,
            ).order_by(MarketCandle.open_time.desc()).first()
        current_price = latest_candle.close_price if latest_candle else p.entry_price
        current_return = paper_return(side, p.entry_price, current_price)
        current_pnl = paper_pnl_usd(side, p.entry_price, current_price)
        item = positions.get(p.symbol.symbol)
        if not item:
            item = {
                'symbol': p.symbol.symbol,
                'side': side,
                'entry_price': p.entry_price,
                'current_price': current_price,
                'margin_usd': risk_plan.margin_usd,
                'leverage': risk_plan.leverage,
                'notional_usd': risk_plan.notional_usd,
                'quantity': risk_plan.quantity,
                'take_profit_price': risk_plan.take_profit_price,
                'stop_loss_price': risk_plan.stop_loss_price,
                'liquidation_price': risk_plan.liquidation_price,
                'take_profit_pnl_usd': risk_plan.take_profit_pnl_usd,
                'stop_loss_pnl_usd': risk_plan.stop_loss_pnl_usd,
                'unrealized_return_percent': round(current_return * 100, 4),
                'unrealized_pnl_usd': round(current_pnl, 6),
                'risk_status': risk_status(side, p.entry_price, current_price),
                'confidence': round(p.confidence * 100, 2),
                'score_total': p.score_total,
                'prediction_time': p.prediction_time.isoformat(),
                'market_regime': p.market_regime,
                'reason_summary': p.reason_summary,
                'horizons': [],
                'next_due_time': p.evaluation_due_time.isoformat(),
            }
            positions[p.symbol.symbol] = item
        if p.prediction_time.isoformat() == item['prediction_time']:
            item['horizons'].append(p.horizon_minutes)
            if p.evaluation_due_time.isoformat() < item['next_due_time']:
                item['next_due_time'] = p.evaluation_due_time.isoformat()
    return list(positions.values())


@router.get('/predictions/recent')
def recent_predictions(
    limit: int = Query(50, ge=1, le=200),
    symbol: str | None = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(PredictionLog).join(Symbol).outerjoin(PredictionResult)
    if symbol:
        query = query.filter(Symbol.symbol == symbol.upper())
    rows = query.order_by(PredictionLog.id.desc()).limit(limit).all()
    data = []
    for p in rows:
        data.append({
            'id': p.id,
            'symbol': p.symbol.symbol,
            'timeframe': p.timeframe,
            'prediction_time': p.prediction_time.isoformat(),
            'entry_price': p.entry_price,
            'prediction': p.prediction,
            'confidence': round(p.confidence * 100, 2),
            'score_total': p.score_total,
            'horizon_minutes': p.horizon_minutes,
            'threshold_percent': p.threshold_percent,
            'evaluation_due_time': p.evaluation_due_time.isoformat(),
            'market_regime': p.market_regime,
            'status': p.status,
            'reason_summary': p.reason_summary,
            'result': p.result.result if p.result else None,
            'actual_price': p.result.actual_price if p.result else None,
            'actual_return_percent': round(p.result.actual_return_percent * 100, 4) if p.result else None,
            'actual_direction': p.result.actual_direction if p.result else None,
        })
    return data


@router.get('/candles/{symbol}/{timeframe}')
def candles(symbol: str, timeframe: str, limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    sym = db.query(Symbol).filter(Symbol.symbol == symbol.upper()).first()
    if not sym:
        return []
    rows = db.query(MarketCandle).filter(
        MarketCandle.symbol_id == sym.id,
        MarketCandle.timeframe == timeframe,
    ).order_by(MarketCandle.open_time.desc()).limit(limit).all()
    rows = list(reversed(rows))
    return [{
        'time': c.open_time.isoformat(),
        'open': c.open_price,
        'high': c.high_price,
        'low': c.low_price,
        'close': c.close_price,
        'volume': c.volume,
    } for c in rows]


@router.get('/ai/status')
def ai_status(db: Session = Depends(get_db)):
    import json
    from app.ml.auto_learner import get_latest_ai_model_version
    model = get_latest_ai_model_version(db)
    if not model:
        return {'active': False, 'message': 'No AI model found'}
    params = json.loads(model.parameters_json) if model.parameters_json else {}
    return {
        'active': model.is_active,
        'model_name': model.name,
        'version': model.version,
        'type': model.type,
        'description': model.description,
        'created_at': model.created_at.isoformat() if model.created_at else None,
        'metrics': params,
    }


@router.post('/ai/retrain')
def trigger_ai_retrain():
    from app.ml.auto_learner import run_auto_retrain_cycle
    result = run_auto_retrain_cycle(force=True)
    return result

