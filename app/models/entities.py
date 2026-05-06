from datetime import datetime
from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.session import Base


class Symbol(Base):
    __tablename__ = 'symbols'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    base_asset: Mapped[str | None] = mapped_column(String(16), nullable=True)
    quote_asset: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MarketCandle(Base):
    __tablename__ = 'market_candles'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey('symbols.id'), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    open_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    close_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    open_price: Mapped[float] = mapped_column(Float, nullable=False)
    high_price: Mapped[float] = mapped_column(Float, nullable=False)
    low_price: Mapped[float] = mapped_column(Float, nullable=False)
    close_price: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    quote_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    trade_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    symbol = relationship('Symbol')
    __table_args__ = (
        UniqueConstraint('symbol_id', 'timeframe', 'open_time', name='uq_candle_symbol_tf_time'),
        Index('idx_candle_symbol_tf_time', 'symbol_id', 'timeframe', 'open_time'),
        Index('idx_candle_open_time', 'open_time'),
    )


class FeatureSnapshot(Base):
    __tablename__ = 'feature_snapshots'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey('symbols.id'), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ema_9: Mapped[float | None] = mapped_column(Float, nullable=True)
    ema_21: Mapped[float | None] = mapped_column(Float, nullable=True)
    ema_50: Mapped[float | None] = mapped_column(Float, nullable=True)
    rsi_7: Mapped[float | None] = mapped_column(Float, nullable=True)
    rsi_14: Mapped[float | None] = mapped_column(Float, nullable=True)
    vwap: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    candle_range: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_to_wick_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    orderbook_imbalance_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_regime: Mapped[str] = mapped_column(String(32), default='UNKNOWN')
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    symbol = relationship('Symbol')
    __table_args__ = (
        UniqueConstraint('symbol_id', 'timeframe', 'snapshot_time', name='uq_feature_symbol_tf_time'),
        Index('idx_feature_symbol_tf_time', 'symbol_id', 'timeframe', 'snapshot_time'),
    )


class ModelVersion(Base):
    __tablename__ = 'model_versions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    type: Mapped[str] = mapped_column(String(32), default='rule_based')
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    parameters_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (UniqueConstraint('name', 'version', name='uq_model_name_version'),)


class PredictionLog(Base):
    __tablename__ = 'prediction_logs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey('symbols.id'), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    prediction_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    prediction: Mapped[str] = mapped_column(String(8), nullable=False)  # UP/DOWN/SKIP
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    score_total: Mapped[int] = mapped_column(Integer, nullable=False)
    horizon_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    threshold_percent: Mapped[float] = mapped_column(Float, nullable=False)
    evaluation_due_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    model_version_id: Mapped[int] = mapped_column(ForeignKey('model_versions.id'), nullable=False)
    feature_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey('feature_snapshots.id'), nullable=True)
    market_regime: Mapped[str] = mapped_column(String(32), default='UNKNOWN')
    reason_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default='PENDING')
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    symbol = relationship('Symbol')
    model_version = relationship('ModelVersion')
    feature_snapshot = relationship('FeatureSnapshot')
    result = relationship('PredictionResult', back_populates='prediction_log', uselist=False)

    __table_args__ = (
        Index('idx_prediction_status_due', 'status', 'evaluation_due_time'),
        Index('idx_prediction_symbol_time', 'symbol_id', 'prediction_time'),
        Index('idx_prediction_symbol_tf_horizon_time', 'symbol_id', 'timeframe', 'horizon_minutes', 'prediction_time'),
        Index('idx_prediction_model_time', 'model_version_id', 'prediction_time'),
        Index('idx_prediction_confidence', 'confidence'),
        Index('idx_prediction_regime_time', 'market_regime', 'prediction_time'),
    )


class PredictionResult(Base):
    __tablename__ = 'prediction_results'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey('prediction_logs.id'), nullable=False, unique=True)
    evaluation_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    actual_price: Mapped[float] = mapped_column(Float, nullable=False)
    actual_return_percent: Mapped[float] = mapped_column(Float, nullable=False)
    actual_direction: Mapped[str] = mapped_column(String(8), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    prediction_log = relationship('PredictionLog', back_populates='result')

    __table_args__ = (
        Index('idx_result_result', 'result'),
        Index('idx_result_eval_time', 'evaluation_time'),
        Index('idx_result_actual_direction', 'actual_direction'),
    )
