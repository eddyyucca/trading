from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from app.db.session import Base, engine
from app.models.entities import ModelVersion, Symbol
from app.core.config import get_settings


def _migrate_feature_columns() -> None:
    """Safely add new indicator columns to feature_snapshots without dropping data."""
    try:
        inspector = inspect(engine)
        if 'feature_snapshots' not in inspector.get_table_names():
            return
        existing = {col['name'] for col in inspector.get_columns('feature_snapshots')}
        new_cols = [
            ('ema_200',       'FLOAT NULL'),
            ('macd_line',     'FLOAT NULL'),
            ('macd_signal',   'FLOAT NULL'),
            ('macd_hist',     'FLOAT NULL'),
            ('atr_14',        'FLOAT NULL'),
            ('bb_upper',      'FLOAT NULL'),
            ('bb_lower',      'FLOAT NULL'),
            ('bb_percent_b',  'FLOAT NULL'),
            ('adx_14',        'FLOAT NULL'),
            ('stoch_rsi_k',   'FLOAT NULL'),
            ('candle_pattern','VARCHAR(32) NULL'),
            ('macd_hist_prev', 'FLOAT NULL'),
        ]
        with engine.connect() as conn:
            for col_name, col_def in new_cols:
                if col_name not in existing:
                    conn.execute(text(f'ALTER TABLE feature_snapshots ADD COLUMN {col_name} {col_def}'))
            conn.commit()
    except Exception:
        pass  # SQLite or already-migrated — non-fatal


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_feature_columns()


def seed_defaults(db: Session) -> None:
    settings = get_settings()
    for symbol_text in settings.symbol_list:
        exists = db.query(Symbol).filter(Symbol.symbol == symbol_text).first()
        if not exists:
            base = symbol_text.replace('USDT', '') if symbol_text.endswith('USDT') else None
            db.add(Symbol(symbol=symbol_text, base_asset=base, quote_asset='USDT'))

    model = db.query(ModelVersion).filter(
        ModelVersion.name == 'score_rule',
        ModelVersion.version == 'v6_professional'
    ).first()
    if not model:
        db.add(ModelVersion(
            name='score_rule',
            version='v6_professional',
            type='rule_based',
            description=(
                'Professional futures signal engine. '
                'Multi-confluence scoring: EMA200 macro trend, EMA stack, MACD momentum, '
                'VWAP, RSI, Bollinger Bands, ADX trend strength, StochRSI, volume & candle patterns. '
                'Requires 13/24 confluence score + multi-timeframe confirmation.'
            ),
            parameters_json=(
                '{"primary_threshold": 13, "side_threshold": 8, "confirmation_threshold": 5, '
                '"confirmation_required": 1, "max_score": 24, "futures_leverage": 10}'
            ),
        ))
    db.commit()
