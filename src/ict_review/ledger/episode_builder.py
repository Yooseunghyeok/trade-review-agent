from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from ict_review.ledger.models import Fill, LedgerError, TradeEpisode
from ict_review.ledger.position_engine import validate_fills

PNL_QUANT = Decimal("0.000000000000000001")


@dataclass
class _OpenEpisode:
    episode_id: str
    symbol: str
    direction: str
    opened_at: datetime
    entry_fill_ids: list[str]
    exit_fill_ids: list[str]
    entry_quantity: Decimal
    exit_quantity: Decimal
    entry_notional: Decimal
    exit_notional: Decimal
    gross_realized_pnl: Decimal
    fees: Decimal
    rebates: Decimal
    funding: Decimal
    assumptions: list[str]

    @property
    def signed_position(self) -> Decimal:
        return self.entry_quantity - self.exit_quantity if self.direction == "LONG" else self.exit_quantity - self.entry_quantity

    @property
    def open_quantity(self) -> Decimal:
        return abs(self.signed_position)

    @property
    def entry_vwap(self) -> Decimal:
        return self.entry_notional / self.entry_quantity


def _new_episode(
    fill: Fill,
    index: int,
    quantity: Decimal | None = None,
    *,
    fee: Decimal | None = None,
    rebate: Decimal | None = None,
    funding: Decimal | None = None,
) -> _OpenEpisode:
    qty = quantity or fill.quantity
    direction = "LONG" if fill.side == "BUY" else "SHORT"
    assumptions = [
        "one-way position mode",
        "exchange_realized_pnl is stored separately and is not treated as ground truth",
    ]
    return _OpenEpisode(
        episode_id=f"episode-{index:04d}",
        symbol=fill.symbol,
        direction=direction,
        opened_at=fill.filled_at,
        entry_fill_ids=[fill.fill_id],
        exit_fill_ids=[],
        entry_quantity=qty,
        exit_quantity=Decimal("0"),
        entry_notional=qty * fill.price,
        exit_notional=Decimal("0"),
        gross_realized_pnl=Decimal("0"),
        fees=fill.fee if fee is None else fee,
        rebates=fill.rebate if rebate is None else rebate,
        funding=fill.funding if funding is None else funding,
        assumptions=assumptions,
    )


def _close_episode(open_ep: _OpenEpisode, closed_at: datetime | None) -> TradeEpisode:
    exit_vwap = None if open_ep.exit_quantity == 0 else open_ep.exit_notional / open_ep.exit_quantity
    gross = open_ep.gross_realized_pnl.quantize(PNL_QUANT)
    net = (gross - open_ep.fees + open_ep.rebates + open_ep.funding).quantize(PNL_QUANT)
    return TradeEpisode(
        episode_id=open_ep.episode_id,
        symbol=open_ep.symbol,
        direction=open_ep.direction,
        opened_at=open_ep.opened_at,
        closed_at=closed_at,
        entry_fill_ids=tuple(open_ep.entry_fill_ids),
        exit_fill_ids=tuple(open_ep.exit_fill_ids),
        entry_quantity=open_ep.entry_quantity,
        exit_quantity=open_ep.exit_quantity,
        entry_vwap=open_ep.entry_vwap,
        exit_vwap=exit_vwap,
        gross_realized_pnl=gross,
        fees=open_ep.fees,
        rebates=open_ep.rebates,
        funding=open_ep.funding,
        calculated_net_pnl=net,
        reconciliation_status="CALCULATED_ONLY",
        assumptions=tuple(open_ep.assumptions),
    )


def _entry_side(direction: str) -> str:
    return "BUY" if direction == "LONG" else "SELL"


def _realized_delta(direction: str, entry_vwap: Decimal, exit_price: Decimal, quantity: Decimal) -> Decimal:
    if direction == "LONG":
        return (exit_price - entry_vwap) * quantity
    return (entry_vwap - exit_price) * quantity


def build_trade_episodes(fills: Iterable[Fill]) -> tuple[TradeEpisode, ...]:
    rows = validate_fills(tuple(sorted(fills, key=lambda fill: (fill.filled_at, fill.fill_id))))
    episodes: list[TradeEpisode] = []
    open_ep: _OpenEpisode | None = None
    next_index = 1

    for fill in rows:
        remaining = fill.quantity
        while remaining > 0:
            if open_ep is None:
                open_ep = _new_episode(fill, next_index, remaining)
                next_index += 1
                remaining = Decimal("0")
                continue

            if fill.symbol != open_ep.symbol:
                raise LedgerError("multiple symbols in one open position are not supported")

            if fill.side == _entry_side(open_ep.direction):
                open_ep.entry_fill_ids.append(fill.fill_id)
                open_ep.entry_quantity += remaining
                open_ep.entry_notional += remaining * fill.price
                open_ep.fees += fill.fee
                open_ep.rebates += fill.rebate
                open_ep.funding += fill.funding
                remaining = Decimal("0")
                continue

            closing_qty = min(open_ep.open_quantity, remaining)
            open_ep.exit_fill_ids.append(fill.fill_id)
            open_ep.exit_quantity += closing_qty
            open_ep.exit_notional += closing_qty * fill.price
            open_ep.gross_realized_pnl += _realized_delta(open_ep.direction, open_ep.entry_vwap, fill.price, closing_qty)
            fee_share = closing_qty / fill.quantity
            open_ep.fees += fill.fee * fee_share
            open_ep.rebates += fill.rebate * fee_share
            open_ep.funding += fill.funding * fee_share
            remaining -= closing_qty

            if open_ep.open_quantity == 0:
                episodes.append(_close_episode(open_ep, fill.filled_at))
                open_ep = None

            if remaining > 0:
                entry_share = remaining / fill.quantity
                open_ep = _new_episode(
                    fill,
                    next_index,
                    remaining,
                    fee=fill.fee * entry_share,
                    rebate=fill.rebate * entry_share,
                    funding=fill.funding * entry_share,
                )
                next_index += 1
                remaining = Decimal("0")

    if open_ep is not None:
        episodes.append(_close_episode(open_ep, None))
    return tuple(episodes)
