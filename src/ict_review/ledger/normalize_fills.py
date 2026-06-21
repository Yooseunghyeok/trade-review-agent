from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable

from ict_review.ledger.models import Fill, LedgerError


def _decimal(value: Any, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise LedgerError(f"invalid decimal for {field_name}: {value}") from exc


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise LedgerError(f"invalid filled_at: {value}")
    text = value.replace("Z", "+00:00")
    return datetime.fromisoformat(text)


def normalize_fills(raw_fills: Iterable[dict[str, Any]]) -> tuple[Fill, ...]:
    fills: list[Fill] = []
    seen: set[str] = set()
    for row in raw_fills:
        fill_id = str(row["fill_id"])
        if fill_id in seen:
            raise LedgerError(f"duplicate fill_id: {fill_id}")
        seen.add(fill_id)
        quantity = _decimal(row["quantity"], "quantity")
        price = _decimal(row["price"], "price")
        if quantity <= 0:
            raise LedgerError(f"fill quantity must be positive: {fill_id}")
        if price < 0:
            raise LedgerError(f"fill price must be non-negative: {fill_id}")
        side = str(row["side"]).upper()
        if side not in {"BUY", "SELL"}:
            raise LedgerError(f"unsupported one-way side: {side}")
        exchange_value = row.get("exchange_realized_pnl")
        fills.append(
            Fill(
                fill_id=fill_id,
                order_id=str(row.get("order_id", fill_id)),
                symbol=str(row["symbol"]),
                side=side,
                quantity=quantity,
                price=price,
                fee=_decimal(row.get("fee", "0"), "fee"),
                rebate=_decimal(row.get("rebate", "0"), "rebate"),
                funding=_decimal(row.get("funding", "0"), "funding"),
                filled_at=_datetime(row["filled_at"]),
                exchange_realized_pnl=None if exchange_value is None else _decimal(exchange_value, "exchange_realized_pnl"),
            )
        )
    return tuple(sorted(fills, key=lambda fill: (fill.filled_at, fill.fill_id)))
