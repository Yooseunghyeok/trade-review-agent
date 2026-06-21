from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


SCHEMA_VERSION = "2.0"


class LedgerError(ValueError):
    """Raised when fills cannot be converted into deterministic episodes."""


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal = Decimal("0")
    rebate: Decimal = Decimal("0")
    funding: Decimal = Decimal("0")
    filled_at: datetime = field(default_factory=datetime.utcnow)
    exchange_realized_pnl: Decimal | None = None


@dataclass(frozen=True)
class Order:
    order_id: str
    symbol: str
    side: str
    quantity: Decimal
    created_at: datetime
    fill_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PositionTransition:
    fill_id: str
    symbol: str
    previous_quantity: Decimal
    new_quantity: Decimal
    realized_pnl_delta: Decimal
    transition_type: str


@dataclass(frozen=True)
class TradeEpisode:
    episode_id: str
    symbol: str
    direction: str
    opened_at: datetime
    closed_at: datetime | None
    entry_fill_ids: tuple[str, ...]
    exit_fill_ids: tuple[str, ...]
    entry_quantity: Decimal
    exit_quantity: Decimal
    entry_vwap: Decimal
    exit_vwap: Decimal | None
    gross_realized_pnl: Decimal
    fees: Decimal
    rebates: Decimal
    funding: Decimal
    calculated_net_pnl: Decimal
    reconciliation_status: str
    assumptions: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION
