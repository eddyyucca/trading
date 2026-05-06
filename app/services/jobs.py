from __future__ import annotations
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.entities import MarketCandle, Symbol
from app.services.binance_client import BinanceRestClient, upsert_candles
from app.services.prediction_engine import run_prediction_once
from app.services.evaluator import evaluate_pending

scheduler = BackgroundScheduler(timezone='UTC')


def collect_once(limit: int = 5) -> dict:
    settings = get_settings()
    client = BinanceRestClient()
    db: Session = SessionLocal()
    try:
        inserted_total = 0
        for symbol in settings.symbol_list:
            for timeframe in settings.timeframe_list:
                rows = client.get_klines(symbol, timeframe, limit=limit)
                inserted_total += upsert_candles(db, symbol, timeframe, rows)
        return {'inserted': inserted_total}
    finally:
        db.close()


def backfill(limit: int = 300) -> dict:
    settings = get_settings()
    client = BinanceRestClient()
    db: Session = SessionLocal()
    try:
        inserted_total = 0
        details = []
        for symbol in settings.symbol_list:
            for timeframe in settings.timeframe_list:
                rows = client.get_klines(symbol, timeframe, limit=limit)
                inserted = upsert_candles(db, symbol, timeframe, rows)
                inserted_total += inserted
                details.append({'symbol': symbol, 'timeframe': timeframe, 'inserted': inserted})
        return {'inserted': inserted_total, 'details': details}
    finally:
        db.close()


def candle_count(db: Session, symbol_text: str, timeframe: str) -> int:
    return db.query(func.count(MarketCandle.id)).join(Symbol).filter(
        Symbol.symbol == symbol_text,
        MarketCandle.timeframe == timeframe,
    ).scalar() or 0


def ensure_history() -> dict:
    settings = get_settings()
    db: Session = SessionLocal()
    try:
        missing = []
        for symbol in settings.symbol_list:
            for timeframe in settings.timeframe_list:
                count = candle_count(db, symbol, timeframe)
                if count < settings.startup_min_candles:
                    missing.append({
                        'symbol': symbol,
                        'timeframe': timeframe,
                        'count': count,
                        'required': settings.startup_min_candles,
                    })
    finally:
        db.close()

    if not missing:
        return {
            'status': 'ready',
            'backfilled': False,
            'required': settings.startup_min_candles,
            'missing': [],
        }

    result = backfill(limit=settings.startup_backfill_limit)
    return {
        'status': 'backfilled',
        'backfilled': True,
        'required': settings.startup_min_candles,
        'missing_before': missing,
        **result,
    }


def prediction_job() -> dict:
    db: Session = SessionLocal()
    try:
        return run_prediction_once(db)
    finally:
        db.close()


def evaluator_job() -> dict:
    db: Session = SessionLocal()
    try:
        return evaluate_pending(db)
    finally:
        db.close()


def start_jobs() -> None:
    settings = get_settings()
    if not scheduler.running:
        scheduler.start()
    if not scheduler.get_job('collector'):
        scheduler.add_job(collect_once, 'interval', seconds=settings.collector_interval_seconds, id='collector', kwargs={'limit': 5}, replace_existing=True)
    if not scheduler.get_job('predictor'):
        scheduler.add_job(prediction_job, 'interval', seconds=60, id='predictor', replace_existing=True)
    if not scheduler.get_job('evaluator'):
        scheduler.add_job(evaluator_job, 'interval', seconds=settings.evaluator_interval_seconds, id='evaluator', replace_existing=True)


def stop_jobs() -> None:
    for job_id in ['collector', 'predictor', 'evaluator']:
        job = scheduler.get_job(job_id)
        if job:
            scheduler.remove_job(job_id)


def job_status() -> dict:
    return {
        'running': scheduler.running,
        'jobs': [{'id': job.id, 'next_run_time': str(job.next_run_time)} for job in scheduler.get_jobs()],
    }
