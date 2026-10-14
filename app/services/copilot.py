from __future__ import annotations
import json
import os
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.models.entities import ModelVersion, PredictionLog, PredictionResult, Symbol
from app.services.risk import signal_side
from app.services.stats import summary

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')


def get_aegis_context_data(db: Session) -> dict:
    """Collects real-time quantitative context from the local AEGIS database."""
    # 1. Model Info
    model = (
        db.query(ModelVersion)
        .filter(ModelVersion.type == 'ml_xgboost', ModelVersion.is_active == True)
        .order_by(ModelVersion.id.desc())
        .first()
    )
    model_info = {
        'name': model.name if model else 'XGBoost Quant',
        'version': model.version if model else 'v1.0.0-xgb',
        'type': model.type if model else 'ml_xgboost',
    }

    # 2. Stats
    stats = summary(db)

    # 3. Active Positions
    active_logs = (
        db.query(PredictionLog)
        .join(Symbol)
        .filter(PredictionLog.status == 'PENDING', PredictionLog.prediction.in_(['BUY', 'SELL', 'UP', 'DOWN']))
        .order_by(PredictionLog.prediction_time.desc())
        .limit(20)
        .all()
    )

    positions = []
    seen_symbols = set()
    for p in active_logs:
        sym = p.symbol.symbol
        if sym in seen_symbols:
            continue
        seen_symbols.add(sym)
        positions.append({
            'symbol': sym,
            'side': signal_side(p.prediction),
            'entry_price': p.entry_price,
            'confidence': round(p.confidence * 100, 2),
            'market_regime': p.market_regime,
            'reason': p.reason_summary,
            'due_time': p.evaluation_due_time.isoformat() if p.evaluation_due_time else None,
        })

    # 4. Recent signals
    recent_signals = (
        db.query(PredictionLog)
        .join(Symbol)
        .order_by(PredictionLog.id.desc())
        .limit(6)
        .all()
    )
    recent = [{
        'symbol': r.symbol.symbol,
        'direction': r.prediction,
        'confidence': f"{round(r.confidence * 100, 1)}%",
        'status': r.status,
        'reason': r.reason_summary,
    } for r in recent_signals]

    return {
        'model': model_info,
        'stats': stats,
        'active_positions': positions,
        'recent_signals': recent,
    }


def generate_local_fallback_reply(message: str, ctx: dict) -> str:
    """High-fidelity local conversational reasoning when Gemini API Key is not yet populated."""
    msg = message.lower()
    stats = ctx['stats']
    positions = ctx['active_positions']
    model = ctx['model']

    # 1. Ask about specific active coin
    matched_pos = None
    for p in positions:
        if p['symbol'].lower() in msg or p['symbol'].replace('USDT', '').lower() in msg:
            matched_pos = p
            break

    if matched_pos:
        side = matched_pos['side']
        sym = matched_pos['symbol']
        conf = matched_pos['confidence']
        reason = matched_pos['reason']
        action_word = "Short (Jual)" if side == "SELL" else "Long (Beli)"
        return (
            f"🎯 **Analisis AEGIS untuk {sym}:**\n\n"
            f"AEGIS mengambil tindakan **{side} ({action_word})** pada harga masuk **${matched_pos['entry_price']}** dengan tingkat keyakinan **{conf}%**.\n\n"
            f"**Alasan Logika Model:**\n{reason}\n\n"
            f"Posisi ini dilindungi dengan target Take Profit **+0.35%** dan batas ketat Stop Loss **-0.20%** di horizon futures 10x."
        )

    # 2. Ask about performance / PnL / win rate
    if any(k in msg for k in ['performa', 'pnl', 'profit', 'untung', 'rugi', 'hasil', 'win']):
        pnl = stats.get('total_pnl_usd', 0.0)
        acc = stats.get('directional_accuracy', 0.0)
        tp = stats.get('take_profit', 0)
        sl = stats.get('stop_loss', 0)
        return (
            f"📊 **Ringkasan Kinerja AEGIS Saat Ini:**\n\n"
            f"• **Model Aktif:** {model['name']} ({model['version']})\n"
            f"• **Total PnL:** **${pnl:+.4f} USD**\n"
            f"• **Akurasi Win-Rate:** **{acc}%**\n"
            f"• **Posisi Berhasil (TP):** {tp} trade\n"
            f"• **Posisi Terpotong (SL):** {sl} trade\n"
            f"• **Posisi Terbuka Sekarang:** {len(positions)} trade\n\n"
            f"Sistem bekerja dengan prinsip *Positive Expectancy* di mana target profit selalu lebih besar dari batas risiko per trade."
        )

    # 3. Ask about strategy / how it works
    if any(k in msg for k in ['strategi', 'cara kerja', 'bagaimana', 'model', 'apa itu']):
        return (
            f"🧠 **Strategi & Otak AEGIS:**\n\n"
            f"1. **Model Inti:** Model machine learning lokal **XGBoost ({model['version']})** yang mengevaluasi 22 indikator teknikal (EMA spread, RSI, Bollinger Bands, ATR volatilitas, dan volume ratio).\n"
            f"2. **Triple Barrier Risk:** Sinyal hanya dibuka saat probabilitas Take Profit (+0.35%) sebelum Stop Loss (-0.20%) dalam 10-15 menit bernilai tinggi.\n"
            f"3. **Auto-Learner:** Model melatih ulang dirinya setiap 6 jam untuk terus beradaptasi dengan perubahan pola pasar Binance."
        )

    # 4. General fallback overview
    active_summary = ", ".join([f"{p['symbol']} ({p['side']} {p['confidence']}%)" for p in positions]) if positions else "Tidak ada (semua HOLD)"
    return (
        f"Halo! Saya adalah juru bicara **AEGIS Quant Engine**.\n\n"
        f"Saat ini AEGIS menjalankan model **{model['version']}** dengan **{len(positions)} posisi aktif**: {active_summary}.\n\n"
        f"Anda dapat menanyakan hal-hal seperti:\n"
        f"• *\"Kenapa AEGIS buka posisi di XRP?\"*\n"
        f"• *\"Bagaimana performa trading hari ini?\"*\n"
        f"• *\"Jelaskan strategi manajemen risiko saat ini\"*\n\n"
        f"*(💡 Info: Masukkan `GEMINI_API_KEY` di file .env jika ingin mengaktifkan penalaran penuh Google Gemini LLM)*"
    )


async def ask_gemini_copilot(message: str) -> str:
    """Main copilot inference: uses Google Gemini LLM if key is configured, else smart local analyst."""
    db: Session = SessionLocal()
    try:
        ctx = get_aegis_context_data(db)
    finally:
        db.close()

    from app.core.config import get_settings
    settings = get_settings()
    api_key = settings.gemini_api_key or os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')

    if not api_key:
        return generate_local_fallback_reply(message, ctx)

    # If Gemini API Key is available, use Google GenAI
    try:
        from google import genai
        client = genai.Client(api_key=api_key)

        system_instruction = (
            "Kamu adalah Asisten Kuantitatif Resmi untuk sistem trading lokal bernama AEGIS. "
            "Semua keputusan trading diambil secara lokal oleh model machine learning XGBoost AEGIS. "
            "Tugasmu adalah bertindak sebagai juru bicara yang cerdas, ramah, dan profesional kepada pengguna dalam Bahasa Indonesia. "
            "Berikut adalah data realtime lengkap sistem AEGIS saat ini:\n"
            f"{json.dumps(ctx, indent=2)}\n\n"
            "Jawab pertanyaan pengguna secara akurat, lugas, dan mengacu pada data fakta di atas."
        )

        candidate_models = ['gemini-3.5-flash-lite', 'gemini-3.8-flash', 'gemini-flash-latest']
        last_err = None
        for m in candidate_models:
            try:
                response = client.models.generate_content(
                    model=m,
                    contents=message,
                    config={'system_instruction': system_instruction},
                )
                return response.text
            except Exception as e:
                last_err = e
                continue

        if last_err:
            print(f"[!] Gemini API Error: {last_err}, using local fallback...")
        return generate_local_fallback_reply(message, ctx)
    except Exception as e:
        print(f"[!] Gemini Client Error: {e}, using local fallback...")
        return generate_local_fallback_reply(message, ctx)
