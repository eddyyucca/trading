from functools import lru_cache
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'Binance Prediction Tracker'
    app_env: str = 'development'
    app_debug: bool = True
    auto_start_jobs: bool = False
    auto_backfill_on_start: bool = True
    startup_backfill_limit: int = 300
    startup_min_candles: int = 120

    database_url: str = 'sqlite:///./local.db'
    binance_rest_base_url: str = 'https://api.binance.com'
    binance_verify_ssl: bool = True

    symbols: str = 'BTCUSDT,ETHUSDT,BNBUSDT'
    timeframes: str = '1m,5m,15m'
    prediction_timeframe: str = '1m'
    horizons_minutes: str = '3,5,10'

    min_confidence: float = 0.65
    threshold_short: float = 0.0005
    threshold_long: float = 0.0010
    paper_position_usd: float = 5.0
    futures_mode: bool = False
    futures_leverage: float = 1.0
    take_profit_percent: float = 0.0025
    stop_loss_percent: float = 0.0015
    near_stop_percent: float = 0.70
    signal_cooldown_minutes: int = 20
    max_open_positions: int = 3
    performance_guard_enabled: bool = True
    performance_guard_min_results: int = 8
    performance_guard_min_accuracy: float = 0.45
    performance_guard_min_pnl_usd: float = 0.0
    collector_interval_seconds: int = 30
    evaluator_interval_seconds: int = 20
    gemini_api_key: str = ''

    @property
    def symbol_list(self) -> List[str]:
        return [x.strip().upper() for x in self.symbols.split(',') if x.strip()]

    @property
    def timeframe_list(self) -> List[str]:
        return [x.strip() for x in self.timeframes.split(',') if x.strip()]

    @property
    def horizon_list(self) -> List[int]:
        return [int(x.strip()) for x in self.horizons_minutes.split(',') if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
