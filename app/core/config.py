from functools import lru_cache
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'Binance Prediction Tracker'
    app_env: str = 'development'
    app_debug: bool = True

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
    collector_interval_seconds: int = 30
    evaluator_interval_seconds: int = 20

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
