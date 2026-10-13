# AEGIS — Autonomous AI Quant Prediction & Trading Engine

**AEGIS** adalah sistem trading kuantitatif dan prediksi futures otonom berbasis Machine Learning lokal (XGBoost GPU/CPU) yang terus belajar secara mandiri (*Closed-Loop Continual Learning*). Mengambil data real-time dari Binance, menghitung 22 fitur teknikal, mengeksekusi keputusan paper trading `BUY` dan `SELL` dengan target Take Profit & Stop Loss ketat, serta mengevaluasi performa secara kontinu.

## Fitur

- FastAPI backend
- MySQL lokal via SQLAlchemy + PyMySQL
- SQLite fallback untuk tes cepat
- Binance public REST collector untuk candle/kline
- Scanner top crypto USDT pairs
- Feature engine: EMA, RSI, VWAP, volume ratio, volatility, candle range
- Rule-based signal engine: BUY / SELL / HOLD
- Confidence score dan scoring sederhana
- Evaluator horizon 3, 5, dan 10 menit
- Dashboard HTML real-time polling
- Background jobs otomatis untuk collect, predict, dan evaluate

## Prasyarat

- Gunakan Python 3.11 atau 3.12.
- Python 3.14 tidak didukung oleh dependency yang dipin saat ini.
- MySQL lokal harus aktif jika `DATABASE_URL` memakai MySQL.

## Setup Windows PowerShell

```powershell
cd C:\xampp\htdocs\trading
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Pastikan database MySQL sudah ada:

```sql
CREATE DATABASE binance_prediction_tracker CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

Contoh `.env` untuk XAMPP MySQL default:

```env
DATABASE_URL=mysql+pymysql://root:@127.0.0.1:3306/binance_prediction_tracker?charset=utf8mb4
BINANCE_REST_BASE_URL=https://data-api.binance.vision
BINANCE_VERIFY_SSL=false
AUTO_START_JOBS=true
AUTO_BACKFILL_ON_START=true
STARTUP_BACKFILL_LIMIT=300
STARTUP_MIN_CANDLES=120
PAPER_POSITION_USD=5
TAKE_PROFIT_PERCENT=0.0025
STOP_LOSS_PERCENT=0.0015
NEAR_STOP_PERCENT=0.70
```

## Menjalankan

```powershell
cd C:\xampp\htdocs\trading
.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 0.0.0.0 --port 8010
```

Buka dashboard:

```text
http://localhost:8010
```

Jika port `8010` sudah dipakai, berarti server kemungkinan sudah berjalan. Buka dashboard langsung atau hentikan proses lama sebelum menjalankan ulang.

## Cara Pakai

1. Klik `Backfill` untuk mengambil data candle awal.
2. Klik `Start` untuk menjalankan collector, predictor, dan evaluator otomatis.
3. Pantau `Live Signal Feed` untuk sinyal `BUY`, `SELL`, atau `HOLD`.
4. Gunakan `Collect Once`, `Predict Once`, dan `Evaluate Once` jika ingin menjalankan proses manual.

Dengan `AUTO_START_JOBS=true`, job otomatis akan berjalan saat server start.

Dengan `AUTO_BACKFILL_ON_START=true`, aplikasi akan mengecek history candle saat server start. Jika data per symbol/timeframe kurang dari `STARTUP_MIN_CANDLES`, aplikasi otomatis mengambil history dari REST API sebanyak `STARTUP_BACKFILL_LIMIT`, sehingga predictor tidak harus menunggu collector realtime.

## Risk Management Paper

Setiap sinyal `BUY` atau `SELL` dianggap sebagai posisi futures paper. Model agresif saat ini memakai margin `$5`, leverage `10x`, `PREDICTION_TIMEFRAME=5m`, satu horizon `10m`, cooldown pendek, performance guard, dan maksimal 5 posisi aktif.

- `FUTURES_LEVERAGE=10` berarti margin `$5` menjadi notional sekitar `$50`.
- `TAKE_PROFIT_PERCENT=0.0040` berarti target pergerakan harga `0.40%`, atau sekitar `$0.20` dari notional `$50`.
- `STOP_LOSS_PERCENT=0.0025` berarti batas pergerakan rugi `0.25%`, atau sekitar `-$0.125` dari notional `$50`.
- `NEAR_STOP_PERCENT=0.95` berarti posisi ditutup lebih cepat saat loss berjalan sudah mencapai 95% dari jarak stop-loss.
- `SIGNAL_COOLDOWN_MINUTES=5` mencegah symbol yang baru loss langsung dibuka lagi terlalu cepat.
- `MAX_OPEN_POSITIONS=5` membatasi jumlah posisi futures paper aktif.
- `PERFORMANCE_GUARD_ENABLED=true` menghentikan entry baru sementara jika hasil terbaru turun di bawah ambang akurasi/PnL.

Evaluator akan menutup posisi lebih cepat jika candle menyentuh take-profit, stop-loss, atau near-stop sebelum horizon evaluasi selesai.

## Endpoint Penting

- `GET /` dashboard
- `GET /api/health`
- `GET /api/control/status`
- `POST /api/control/start`
- `POST /api/control/stop`
- `GET /api/ai/status` (Melihat status & metrik model AI aktif)
- `POST /api/ai/retrain` (Memicu siklus Auto-Learner untuk retrain & upgrade model)
- `POST /api/collector/backfill?limit=300`
- `POST /api/collector/run-once`
- `POST /api/predict/run-once`
- `POST /api/evaluator/run-once`
- `GET /api/predictions/recent`
- `GET /api/stats/summary`

## AI & Continual Learning (Self-Improving Engine)

Sistem telah dilengkapi dengan model Machine Learning **XGBoost** dan modul **Auto-Learner**:
- **Dataset & Fitur:** 22 fitur teknikal (RSI, EMA spreads, MACD, Bollinger Bands, ATR, volume).
- **Target Labeling:** Metode *Triple Barrier* (+0.35% Take Profit vs -0.20% Stop Loss).
- **Continuous Learning Loop:** Setiap 6 jam (atau dipicu manual via `/api/ai/retrain`), job `auto_learner` akan melatih model penantang (*Challenger*). Model penantang hanya dipromosikan jika performanya lebih baik dari model yang sedang berjalan (*Champion*).
- **Model Version Registry:** Tercatat di tabel `model_versions` database MySQL.

## Catatan Risiko

Sinyal ini berbasis analisis probabilitas kuantitatif paper trading. Gunakan minimal 1.000 hasil evaluasi sebelum menilai performa awal, dan jangan hubungkan ke order real tanpa risk management, backtest, dan validasi tambahan.
