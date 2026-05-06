from sqlalchemy.orm import Session
from app.db.session import Base, engine
from app.models.entities import ModelVersion, Symbol
from app.core.config import get_settings


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def seed_defaults(db: Session) -> None:
    settings = get_settings()
    for symbol_text in settings.symbol_list:
        exists = db.query(Symbol).filter(Symbol.symbol == symbol_text).first()
        if not exists:
            base = symbol_text.replace('USDT', '') if symbol_text.endswith('USDT') else None
            db.add(Symbol(symbol=symbol_text, base_asset=base, quote_asset='USDT'))

    model = db.query(ModelVersion).filter(
        ModelVersion.name == 'score_rule',
        ModelVersion.version == 'v5_futures_aggressive'
    ).first()
    if not model:
        db.add(ModelVersion(
            name='score_rule',
            version='v5_futures_aggressive',
            type='rule_based',
            description='Aggressive futures paper signal engine with leverage, wider TP, capped positions and performance guard.',
            parameters_json='{"min_confidence": 0.74, "primary_threshold": 7, "confirmation_threshold": 4, "single_horizon": true, "cooldown_minutes": 5, "max_open_positions": 5, "futures_leverage": 10}'
        ))
    db.commit()
