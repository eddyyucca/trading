from __future__ import annotations
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models.entities import PredictionLog, PredictionResult


def summary(db: Session) -> dict:
    total_predictions = db.query(func.count(PredictionLog.id)).filter(PredictionLog.prediction.in_(['UP', 'DOWN'])).scalar() or 0
    pending = db.query(func.count(PredictionLog.id)).filter(PredictionLog.status == 'PENDING').scalar() or 0
    correct = db.query(func.count(PredictionResult.id)).filter(PredictionResult.result == 'CORRECT').scalar() or 0
    wrong = db.query(func.count(PredictionResult.id)).filter(PredictionResult.result == 'WRONG').scalar() or 0
    neutral = db.query(func.count(PredictionResult.id)).filter(PredictionResult.result == 'NEUTRAL').scalar() or 0
    denom = correct + wrong
    directional_accuracy = (correct / denom) if denom else 0
    total_accuracy = (correct / total_predictions) if total_predictions else 0
    return {
        'total_predictions': total_predictions,
        'pending': pending,
        'correct': correct,
        'wrong': wrong,
        'neutral': neutral,
        'directional_accuracy': round(directional_accuracy * 100, 2),
        'total_accuracy': round(total_accuracy * 100, 2),
        'sample_warning': total_predictions < 1000,
    }
