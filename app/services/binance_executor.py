"""
AEGIS Binance Futures Live Order Executor
==========================================
Handles safe, authenticated order execution on Binance USDT-M Futures.
Includes full paper-trading safety fallbacks if LIVE_TRADING_ENABLED=False.
"""
from __future__ import annotations
import hashlib
import hmac
import time
from typing import Any, Dict
import httpx
from app.core.config import get_settings


class BinanceFuturesExecutor:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.is_live = bool(
            self.settings.live_trading_enabled
            and self.settings.binance_api_key
            and self.settings.binance_api_secret
        )
        self.base_url = (
            'https://testnet.binancefuture.com'
            if self.settings.binance_testnet
            else 'https://fapi.binance.com'
        )
        self.api_key = self.settings.binance_api_key
        self.api_secret = self.settings.binance_api_secret

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        """Appends timestamp and HMAC-SHA256 signature to parameters."""
        params = dict(params)
        params['timestamp'] = int(time.time() * 1000)
        query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items()) if v is not None])
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        params['signature'] = signature
        return params

    def _headers(self) -> dict[str, str]:
        return {
            'X-MBX-APIKEY': self.api_key,
            'Content-Type': 'application/x-www-form-urlencoded',
        }

    def check_connection(self) -> dict[str, Any]:
        """Validates API credentials and retrieves USDT futures balance."""
        if not self.is_live:
            return {
                'mode': 'PAPER_TRADING',
                'status': 'READY',
                'message': 'Sistem berjalan dalam mode simulasi paper trading yang aman (Saldo virtual).',
                'available_margin': float(self.settings.paper_position_usd * 20),
            }

        try:
            params = self._sign({})
            with httpx.Client(base_url=self.base_url, timeout=10) as client:
                res = client.get('/fapi/v2/account', params=params, headers=self._headers())
                res.raise_for_status()
                data = res.json()
                usdt_asset = next((a for a in data.get('assets', []) if a.get('asset') == 'USDT'), None)
                available = float(usdt_asset.get('availableBalance', 0.0)) if usdt_asset else 0.0
                return {
                    'mode': 'LIVE_FUTURES',
                    'status': 'CONNECTED',
                    'available_margin': available,
                    'total_wallet_balance': float(usdt_asset.get('walletBalance', 0.0)) if usdt_asset else 0.0,
                }
        except Exception as e:
            return {
                'mode': 'LIVE_FUTURES_ERROR',
                'status': 'ERROR',
                'error': str(e),
                'available_margin': 0.0,
            }

    def set_leverage(self, symbol: str, leverage: int = 10) -> dict[str, Any]:
        """Sets leverage for symbol on Binance Futures."""
        if not self.is_live:
            return {'symbol': symbol, 'leverage': leverage, 'simulated': True}

        try:
            params = self._sign({'symbol': symbol.upper(), 'leverage': leverage})
            with httpx.Client(base_url=self.base_url, timeout=10) as client:
                res = client.post('/fapi/v1/leverage', params=params, headers=self._headers())
                res.raise_for_status()
                return res.json()
        except Exception as e:
            print(f"[!] Gagal mengatur leverage untuk {symbol}: {e}")
            return {'error': str(e)}

    def execute_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        take_profit_price: float,
        stop_loss_price: float,
        prediction_id: int | None = None,
    ) -> dict[str, Any]:
        """
        Executes real futures order with automatic TP and SL bracket orders.
        If LIVE_TRADING_ENABLED=False, safely logs simulated order.
        """
        side = side.upper()
        sym = symbol.upper()

        if not self.is_live:
            print(f"[*] [PAPER TRADING] Eksekusi {side} {sym} @ {entry_price} (TP: {take_profit_price:.4f}, SL: {stop_loss_price:.4f})")
            return {
                'executed': True,
                'mode': 'PAPER_TRADING',
                'symbol': sym,
                'side': side,
                'entry_price': entry_price,
                'quantity': quantity,
                'take_profit_price': take_profit_price,
                'stop_loss_price': stop_loss_price,
            }

        try:
            # 1. Set leverage first
            self.set_leverage(sym, int(self.settings.futures_leverage))

            client_order_id = f"AEGIS_{prediction_id}_{int(time.time())}" if prediction_id else f"AEGIS_{int(time.time())}"

            # 2. Market Entry Order
            entry_params = self._sign({
                'symbol': sym,
                'side': side,
                'type': 'MARKET',
                'quantity': round(quantity, 4),
                'newClientOrderId': client_order_id,
            })

            with httpx.Client(base_url=self.base_url, timeout=12) as client:
                res = client.post('/fapi/v1/order', params=entry_params, headers=self._headers())
                res.raise_for_status()
                entry_data = res.json()

                # 3. Place Take Profit Market Order (reduceOnly=True)
                exit_side = 'SELL' if side == 'BUY' else 'BUY'
                tp_params = self._sign({
                    'symbol': sym,
                    'side': exit_side,
                    'type': 'TAKE_PROFIT_MARKET',
                    'stopPrice': round(take_profit_price, 4),
                    'closePosition': 'true',
                })
                client.post('/fapi/v1/order', params=tp_params, headers=self._headers())

                # 4. Place Stop Loss Market Order (reduceOnly=True)
                sl_params = self._sign({
                    'symbol': sym,
                    'side': exit_side,
                    'type': 'STOP_MARKET',
                    'stopPrice': round(stop_loss_price, 4),
                    'closePosition': 'true',
                })
                client.post('/fapi/v1/order', params=sl_params, headers=self._headers())

                print(f"[+] [LIVE FUTURES] Berhasil eksekusi {side} {sym} di Binance Futures! OrderId: {entry_data.get('orderId')}")
                return {
                    'executed': True,
                    'mode': 'LIVE_FUTURES',
                    'orderId': entry_data.get('orderId'),
                    'symbol': sym,
                    'side': side,
                    'entry_price': entry_price,
                    'quantity': quantity,
                }

        except Exception as e:
            print(f"[!] [LIVE FUTURES ERROR] Gagal mengeksekusi order {side} {sym}: {e}")
            return {
                'executed': False,
                'mode': 'LIVE_FUTURES_ERROR',
                'error': str(e),
                'symbol': sym,
                'side': side,
            }
