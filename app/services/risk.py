from __future__ import annotations

from dataclasses import dataclass
from app.core.config import get_settings


@dataclass
class RiskPlan:
    side: str
    margin_usd: float
    leverage: float
    notional_usd: float
    quantity: float
    take_profit_price: float
    stop_loss_price: float
    liquidation_price: float | None
    take_profit_pnl_usd: float
    stop_loss_pnl_usd: float


def signal_side(prediction: str) -> str:
    return {
        'UP': 'BUY',
        'DOWN': 'SELL',
        'BUY': 'BUY',
        'SELL': 'SELL',
    }.get(prediction, prediction)


def build_risk_plan(prediction: str, entry_price: float) -> RiskPlan:
    settings = get_settings()
    side = signal_side(prediction)
    leverage = settings.futures_leverage if settings.futures_mode else 1.0
    margin_usd = settings.paper_position_usd
    notional_usd = margin_usd * leverage
    quantity = notional_usd / entry_price
    if side == 'SELL':
        take_profit_price = entry_price * (1 - settings.take_profit_percent)
        stop_loss_price = entry_price * (1 + settings.stop_loss_percent)
        liquidation_price = entry_price * (1 + (0.90 / leverage)) if settings.futures_mode and leverage > 0 else None
    else:
        take_profit_price = entry_price * (1 + settings.take_profit_percent)
        stop_loss_price = entry_price * (1 - settings.stop_loss_percent)
        liquidation_price = entry_price * (1 - (0.90 / leverage)) if settings.futures_mode and leverage > 0 else None
    return RiskPlan(
        side=side,
        margin_usd=margin_usd,
        leverage=leverage,
        notional_usd=notional_usd,
        quantity=quantity,
        take_profit_price=take_profit_price,
        stop_loss_price=stop_loss_price,
        liquidation_price=liquidation_price,
        take_profit_pnl_usd=notional_usd * settings.take_profit_percent,
        stop_loss_pnl_usd=-(notional_usd * settings.stop_loss_percent),
    )


def paper_return(side: str, entry_price: float, current_price: float) -> float:
    raw_return = (current_price / entry_price) - 1
    return -raw_return if side == 'SELL' else raw_return


def paper_pnl_usd(side: str, entry_price: float, current_price: float) -> float:
    settings = get_settings()
    leverage = settings.futures_leverage if settings.futures_mode else 1.0
    return settings.paper_position_usd * leverage * paper_return(side, entry_price, current_price)


def risk_status(side: str, entry_price: float, current_price: float) -> str:
    settings = get_settings()
    current_return = paper_return(side, entry_price, current_price)
    if current_return >= settings.take_profit_percent:
        return 'TAKE_PROFIT_HIT'
    if current_return <= -settings.stop_loss_percent:
        return 'STOP_LOSS_HIT'
    near_stop_return = -(settings.stop_loss_percent * settings.near_stop_percent)
    if current_return <= near_stop_return:
        return 'NEAR_STOP'
    if current_return > 0:
        return 'IN_PROFIT'
    return 'OPEN'
