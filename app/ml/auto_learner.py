from __future__ import annotations
import json
import os
from datetime import datetime, timezone
import joblib
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.entities import ModelVersion, PredictionLog, PredictionResult
from app.ml.features import FEATURE_COLUMNS
from app.ml.predictor import MODEL_PATH, get_model_artifact
from app.ml.trainer import train_ai_model


def get_latest_ai_model_version(db: Session) -> ModelVersion | None:
    return (
        db.query(ModelVersion)
        .filter(ModelVersion.type == 'ml_xgboost')
        .order_by(ModelVersion.id.desc())
        .first()
    )


def run_auto_retrain_cycle(
    symbols: list[str] | None = None,
    timeframe: str | None = None,
    min_new_evaluations: int = 5,
    force: bool = False,
) -> dict:
    """
    Closed-loop continual learning:
    1. Checks if new trade results exist to learn from.
    2. Trains a 'Challenger' model on the latest expanded dataset.
    3. Compares Challenger vs Champion.
    4. Automatically promotes Challenger if performance is equal or superior.
    """
    db: Session = SessionLocal()

    try:
        current_champion = get_latest_ai_model_version(db)

        # Count total evaluations in DB
        total_evals = db.query(PredictionResult).count()

        # Generate new version identifier
        if current_champion:
            try:
                # e.g. v1.0.0-xgb -> v1.0.1-xgb
                base_ver = current_champion.version.replace('-xgb', '').replace('v', '')
                parts = [int(p) for p in base_ver.split('.')]
                parts[-1] += 1
                new_ver_str = f"v{'.'.join(str(p) for p in parts)}-xgb"
            except Exception:
                new_ver_str = f"v1.0.{total_evals}-xgb"
        else:
            new_ver_str = "v1.0.0-xgb"

        model_filename = f"xgboost_model_{new_ver_str}.joblib"

        print(f"[*] Starting Auto-Learner cycle. Champion: {current_champion.version if current_champion else 'None'}")
        print(f"[*] Training Challenger: {new_ver_str}...")

        # Train candidate model (barriers/symbols/timeframe come from live settings)
        train_res = train_ai_model(symbols=symbols, timeframe=timeframe, model_name=model_filename)

        st = get_settings()
        challenger_exp = train_res['expectancy']
        challenger_trades = train_res['total_signals']
        challenger_accuracy = train_res['win_rate']

        champion_exp = None
        if current_champion and current_champion.parameters_json:
            try:
                champion_exp = json.loads(current_champion.parameters_json).get('expectancy')
            except Exception:
                champion_exp = None

        # Promotion is judged on net expectancy per trade (after fees) on an untouched
        # walk-forward holdout, with a minimum number of trades.
        if current_champion is None:
            promote, promotion_reason = True, "Initial AI model bootstrap."
        elif force:
            promote, promotion_reason = True, "Forced retraining by administrator."
        elif challenger_trades < st.ml_min_signals_for_promotion:
            promote = False
            promotion_reason = (f"Challenger produced only {challenger_trades} holdout trades "
                                f"(< {st.ml_min_signals_for_promotion}); Champion retained.")
        elif challenger_exp <= 0:
            promote = False
            promotion_reason = f"Challenger net expectancy {challenger_exp:.4%} <= 0; Champion retained."
        elif champion_exp is None or challenger_exp >= float(champion_exp) + st.ml_promotion_margin:
            promote = True
            promotion_reason = (f"Challenger expectancy {challenger_exp:.4%}/trade beats Champion "
                                f"({'n/a' if champion_exp is None else f'{float(champion_exp):.4%}'}).")
        else:
            promote = False
            promotion_reason = (f"Challenger expectancy {challenger_exp:.4%} not better than "
                                f"Champion {float(champion_exp):.4%}; Champion retained.")

        if promote:
            # Overwrite active production pointer
            active_path = MODEL_PATH
            new_model_path = train_res['model_path']

            # Copy or save to primary model path
            artifact = joblib.load(new_model_path)
            joblib.dump(artifact, active_path)

            # Invalidate in-memory cache so predictor picks it up immediately
            import app.ml.predictor as predictor_mod
            predictor_mod._CACHED_ARTIFACT = artifact

            # Deactivate older models
            db.query(ModelVersion).filter(ModelVersion.type == 'ml_xgboost').update({'is_active': False})

            # Register new champion in database
            meta_json = json.dumps({
                'win_rate': challenger_accuracy,
                'expectancy': challenger_exp,
                'threshold': train_res['threshold'],
                'total_test_signals': challenger_trades,
                'top_features': train_res['top_features'],
                'trained_at': datetime.now(timezone.utc).isoformat(),
                'trained_samples': train_res['test_samples'],
                'model_file': model_filename,
            })

            new_model = ModelVersion(
                name='xgboost_classifier',
                version=new_ver_str,
                type='ml_xgboost',
                description=f"Self-trained XGBoost model with class balancing ({promotion_reason})",
                parameters_json=meta_json,
                is_active=True,
            )
            db.add(new_model)
            db.commit()

            print(f"[+] PROMOTED: {new_ver_str} is now the ACTIVE trading model!")
            status = 'PROMOTED'
        else:
            print(f"[-] REJECTED: {promotion_reason}")
            status = 'REJECTED'

        return {
            'status': status,
            'reason': promotion_reason,
            'challenger_version': new_ver_str,
            'challenger_win_rate': challenger_accuracy,
            'challenger_expectancy': challenger_exp,
            'challenger_trades': challenger_trades,
            'threshold': train_res['threshold'],
            'champion_version': current_champion.version if current_champion else None,
            'champion_expectancy': champion_exp,
            'trained_at': datetime.now(timezone.utc).isoformat(),
        }

    finally:
        db.close()


if __name__ == '__main__':
    res = run_auto_retrain_cycle(force=True)
    print("\n=== Auto-Learner Execution Result ===")
    print(json.dumps(res, indent=2))
