from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from ict_review.ledger.models import Fill, LedgerError, PositionTransition


def signed_quantity(fill: Fill) -> Decimal:
    return fill.quantity if fill.side == "BUY" else -fill.quantity


def validate_fills(fills: Iterable[Fill]) -> tuple[Fill, ...]:
    rows = tuple(fills)
    seen: set[str] = set()
    for fill in rows:
        if fill.fill_id in seen:
            raise LedgerError(f"duplicate fill_id: {fill.fill_id}")
        seen.add(fill.fill_id)
        if fill.quantity <= 0:
            raise LedgerError(f"fill quantity must be positive: {fill.fill_id}")
        if fill.side not in {"BUY", "SELL"}:
            raise LedgerError(f"unsupported one-way side: {fill.side}")
    return rows


def build_transitions(fills: Iterable[Fill]) -> tuple[PositionTransition, ...]:
    position = Decimal("0")
    transitions: list[PositionTransition] = []
    for fill in validate_fills(fills):
        previous = position
        position += signed_quantity(fill)
        if previous == 0 or (previous > 0 and position > previous) or (previous < 0 and position < previous):
            transition_type = "OPEN_OR_SCALE_IN"
        elif position == 0:
            transition_type = "CLOSE"
        elif (previous > 0 > position) or (previous < 0 < position):
            transition_type = "REVERSE"
        else:
            transition_type = "PARTIAL_CLOSE"
        transitions.append(
            PositionTransition(
                fill_id=fill.fill_id,
                symbol=fill.symbol,
                previous_quantity=previous,
                new_quantity=position,
                realized_pnl_delta=Decimal("0"),
                transition_type=transition_type,
            )
        )
    return tuple(transitions)
