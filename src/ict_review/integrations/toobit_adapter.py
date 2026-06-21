from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from ict_review.ledger.models import Fill, LedgerError
from ict_review.ledger.position_engine import validate_fills


class ToobitAdapterError(LedgerError):
    """Raised when a Toobit raw response cannot be mapped safely."""


@dataclass(frozen=True)
class ToobitAdaptation:
    fills: tuple[Fill, ...]
    assumptions: tuple[str, ...]
    warnings: tuple[str, ...]


SIDE_TO_ONE_WAY = {
    "BUY_OPEN": "BUY",
    "BUY_CLOSE": "BUY",
    "SELL_OPEN": "SELL",
    "SELL_CLOSE": "SELL",
}

TOOBIT_CONTRACT_MULTIPLIERS = {
    "BTC-SWAP-USDT": Decimal("0.001"),
}


def _decimal(row: dict[str, Any], key: str, *, default: str | None = None) -> Decimal:
    value = row.get(key, default)
    if value is None or value == "":
        raise ToobitAdapterError(f"missing decimal field: {key}")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ToobitAdapterError(f"invalid decimal field {key}: {value}") from exc


def _utc_datetime_from_ms(value: Any, *, key: str = "time") -> datetime:
    if value is None or value == "":
        raise ToobitAdapterError(f"missing timestamp field: {key}")
    try:
        timestamp_ms = int(str(value))
    except ValueError as exc:
        raise ToobitAdapterError(f"invalid millisecond timestamp field {key}: {value}") from exc
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


def _pnl_quantity(row: dict[str, Any]) -> Decimal:
    symbol = str(row.get("symbol") or "")
    raw_quantity = _decimal(row, "qty")
    multiplier = TOOBIT_CONTRACT_MULTIPLIERS.get(symbol, Decimal("1"))
    return raw_quantity * multiplier


def _extract_trade_rows(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [dict(item) for item in raw]
    if not isinstance(raw, dict):
        raise ToobitAdapterError("raw Toobit payload must be a dict or list")

    successful_endpoint = raw.get("successful_endpoint")
    attempts = raw.get("attempts")
    if successful_endpoint and isinstance(attempts, list):
        for attempt in attempts:
            if not isinstance(attempt, dict) or attempt.get("endpoint") != successful_endpoint:
                continue
            rows = _rows_from_response(attempt.get("response"))
            if rows is not None:
                return rows

    rows = _rows_from_response(raw.get("response"))
    if rows is not None:
        return rows

    if isinstance(attempts, list):
        for attempt in attempts:
            if isinstance(attempt, dict):
                rows = _rows_from_response(attempt.get("response"))
                if rows is not None:
                    return rows
    raise ToobitAdapterError("could not find trade rows in Toobit payload")


def _rows_from_response(response: Any) -> list[dict[str, Any]] | None:
    if isinstance(response, list):
        return [dict(item) for item in response]
    if isinstance(response, dict):
        for key in ("data", "result", "rows", "list"):
            value = response.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value]
    return None


def adapt_raw_toobit_fills(raw: Any, *, position_mode: str = "one_way") -> ToobitAdaptation:
    if position_mode not in {"one_way", "ONE_WAY", "one-way"}:
        raise ToobitAdapterError(f"unsupported Toobit position mode: {position_mode}")

    warnings: list[str] = []
    assumptions = [
        "Toobit side values BUY_OPEN, BUY_CLOSE, SELL_OPEN, SELL_CLOSE are mapped to one-way BUY/SELL fills.",
        "For BTC-SWAP-USDT, Toobit qty is contract quantity and is converted with a 0.001 PnL multiplier.",
        "Toobit realizedPnl is preserved as exchange_realized_pnl and is not merged into fee, rebate, or funding.",
        "No read/write Toobit API call is made by this adapter.",
    ]

    fills: list[Fill] = []
    for row in _extract_trade_rows(raw):
        side_raw = str(row.get("side", "")).upper()
        if side_raw not in SIDE_TO_ONE_WAY:
            raise ToobitAdapterError(f"unsupported Toobit side for one-way mode: {side_raw}")
        position_side = str(row.get("positionSide", "")).upper()
        if position_side and position_side not in {"BOTH", "ONE_WAY"}:
            raise ToobitAdapterError(f"unsupported Toobit positionSide for one-way mode: {position_side}")
        if row.get("commissionAsset") and str(row.get("commissionAsset")).upper() != "USDT":
            warnings.append(f"commissionAsset is not USDT for fill {row.get('id')}")

        fill_id = str(row.get("id") or "")
        order_id = str(row.get("orderId") or "")
        if not fill_id:
            raise ToobitAdapterError("missing fill id field: id")
        if not order_id:
            raise ToobitAdapterError(f"missing orderId for fill {fill_id}")

        fills.append(
            Fill(
                fill_id=fill_id,
                order_id=order_id,
                symbol=str(row.get("symbol") or ""),
                side=SIDE_TO_ONE_WAY[side_raw],
                quantity=_pnl_quantity(row),
                price=_decimal(row, "price"),
                fee=_decimal(row, "commission", default="0"),
                rebate=_decimal(row, "makerRebate", default="0"),
                funding=_decimal(row, "funding", default="0"),
                filled_at=_utc_datetime_from_ms(row.get("time")),
                exchange_realized_pnl=_decimal(row, "realizedPnl", default="0"),
            )
        )

    try:
        rows = validate_fills(tuple(sorted(fills, key=lambda fill: (fill.filled_at, fill.fill_id))))
    except LedgerError as exc:
        raise ToobitAdapterError(str(exc)) from exc
    return ToobitAdaptation(fills=rows, assumptions=tuple(assumptions), warnings=tuple(warnings))
