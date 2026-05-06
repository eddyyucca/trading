from __future__ import annotations
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import MarketCandle, PredictionLog, PredictionResult, Symbol
from app.services.jobs import backfill, collect_once, evaluator_job, job_status, prediction_job, start_jobs, stop_jobs
from app.services.stats import summary

router = APIRouter(prefix='/api')


@router.get('/health')
def health():
    return {'status': 'ok', 'service': 'binance_prediction_tracker'}


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


@router.get('/predictions/recent')
def recent_predictions(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    rows = db.query(PredictionLog).join(Symbol).outerjoin(PredictionResult).order_by(PredictionLog.id.desc()).limit(limit).all()
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
