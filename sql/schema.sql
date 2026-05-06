CREATE TABLE IF NOT EXISTS symbols (
  id INT AUTO_INCREMENT PRIMARY KEY,
  symbol VARCHAR(32) NOT NULL UNIQUE,
  base_asset VARCHAR(16),
  quote_asset VARCHAR(16),
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_symbols_symbol (symbol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS market_candles (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  symbol_id INT NOT NULL,
  timeframe VARCHAR(8) NOT NULL,
  open_time DATETIME NOT NULL,
  close_time DATETIME NOT NULL,
  open_price DOUBLE NOT NULL,
  high_price DOUBLE NOT NULL,
  low_price DOUBLE NOT NULL,
  close_price DOUBLE NOT NULL,
  volume DOUBLE NOT NULL,
  quote_volume DOUBLE NULL,
  trade_count INT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_candle_symbol FOREIGN KEY (symbol_id) REFERENCES symbols(id),
  UNIQUE KEY uq_candle_symbol_tf_time (symbol_id, timeframe, open_time),
  KEY idx_candle_symbol_tf_time (symbol_id, timeframe, open_time),
  KEY idx_candle_open_time (open_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS feature_snapshots (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  symbol_id INT NOT NULL,
  timeframe VARCHAR(8) NOT NULL,
  snapshot_time DATETIME NOT NULL,
  ema_9 DOUBLE NULL,
  ema_21 DOUBLE NULL,
  ema_50 DOUBLE NULL,
  rsi_7 DOUBLE NULL,
  rsi_14 DOUBLE NULL,
  vwap DOUBLE NULL,
  volume_ratio DOUBLE NULL,
  volatility DOUBLE NULL,
  candle_range DOUBLE NULL,
  body_to_wick_ratio DOUBLE NULL,
  spread DOUBLE NULL,
  orderbook_imbalance_5 DOUBLE NULL,
  market_regime VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_feature_symbol FOREIGN KEY (symbol_id) REFERENCES symbols(id),
  UNIQUE KEY uq_feature_symbol_tf_time (symbol_id, timeframe, snapshot_time),
  KEY idx_feature_symbol_tf_time (symbol_id, timeframe, snapshot_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS model_versions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(64) NOT NULL,
  version VARCHAR(32) NOT NULL,
  type VARCHAR(32) NOT NULL DEFAULT 'rule_based',
  description TEXT NULL,
  parameters_json TEXT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_model_name_version (name, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS prediction_logs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  symbol_id INT NOT NULL,
  timeframe VARCHAR(8) NOT NULL,
  prediction_time DATETIME NOT NULL,
  entry_price DOUBLE NOT NULL,
  prediction VARCHAR(8) NOT NULL,
  confidence DOUBLE NOT NULL,
  score_total INT NOT NULL,
  horizon_minutes INT NOT NULL,
  threshold_percent DOUBLE NOT NULL,
  evaluation_due_time DATETIME NOT NULL,
  model_version_id INT NOT NULL,
  feature_snapshot_id BIGINT NULL,
  market_regime VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
  reason_summary TEXT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_prediction_symbol FOREIGN KEY (symbol_id) REFERENCES symbols(id),
  CONSTRAINT fk_prediction_model FOREIGN KEY (model_version_id) REFERENCES model_versions(id),
  CONSTRAINT fk_prediction_feature FOREIGN KEY (feature_snapshot_id) REFERENCES feature_snapshots(id),
  KEY idx_prediction_status_due (status, evaluation_due_time),
  KEY idx_prediction_symbol_time (symbol_id, prediction_time),
  KEY idx_prediction_symbol_tf_horizon_time (symbol_id, timeframe, horizon_minutes, prediction_time),
  KEY idx_prediction_model_time (model_version_id, prediction_time),
  KEY idx_prediction_confidence (confidence),
  KEY idx_prediction_regime_time (market_regime, prediction_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS prediction_results (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  prediction_id BIGINT NOT NULL UNIQUE,
  evaluation_time DATETIME NOT NULL,
  actual_price DOUBLE NOT NULL,
  actual_return_percent DOUBLE NOT NULL,
  actual_direction VARCHAR(8) NOT NULL,
  result VARCHAR(16) NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_result_prediction FOREIGN KEY (prediction_id) REFERENCES prediction_logs(id),
  KEY idx_result_result (result),
  KEY idx_result_eval_time (evaluation_time),
  KEY idx_result_actual_direction (actual_direction)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS accuracy_snapshots (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  snapshot_date DATE NOT NULL,
  symbol_id INT NULL,
  timeframe VARCHAR(8) NULL,
  horizon_minutes INT NULL,
  model_version_id INT NULL,
  market_regime VARCHAR(32) NULL,
  confidence_bucket VARCHAR(16) NULL,
  total_predictions INT NOT NULL DEFAULT 0,
  correct_count INT NOT NULL DEFAULT 0,
  wrong_count INT NOT NULL DEFAULT 0,
  neutral_count INT NOT NULL DEFAULT 0,
  directional_accuracy DOUBLE NOT NULL DEFAULT 0,
  total_accuracy DOUBLE NOT NULL DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_accuracy_snapshot_date (snapshot_date),
  KEY idx_accuracy_dimension (symbol_id, timeframe, horizon_minutes, model_version_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
