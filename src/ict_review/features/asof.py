from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable, Mapping


class AsOfError(ValueError):
    """Raised when candle data cannot be split without look-ahead risk."""


@dataclass(frozen=True)
class Candle:
    timeframe: str
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal("0")


@dataclass(frozen=True)
class AsOfSplit:
    pre_trade: tuple[Candle, ...]
    post_trade: tuple[Candle, ...]


def _timezone_kind(value: datetime) -> str:
    return "aware" if value.tzinfo is not None and value.utcoffset() is not None else "naive"


def _validate_timezones(event_time: datetime, candles: Iterable[Candle]) -> None:
    expected = _timezone_kind(event_time)
    for candle in candles:
        if _timezone_kind(candle.close_time) != expected:
            raise AsOfError("timezone-aware and timezone-naive timestamps cannot be mixed")


def _validate_sorted_and_unique(candles: tuple[Candle, ...]) -> None:
    previous: datetime | None = None
    seen: set[datetime] = set()
    for candle in candles:
        if candle.close_time in seen:
            raise AsOfError(f"duplicate close_time is not allowed: {candle.close_time.isoformat()}")
        seen.add(candle.close_time)
        if previous is not None and candle.close_time < previous:
            raise AsOfError("candles must be sorted by close_time ascending")
        previous = candle.close_time


def split_candles_asof(
    candles: Iterable[Candle],
    event_time: datetime,
    *,
    post_until: datetime | None = None,
) -> AsOfSplit:
    """Split candles by event time without including future closes in pre-trade context.

    Duplicate close times are rejected instead of merged because deterministic OHLCV merge
    semantics depend on source-specific rules that are not defined in V2 yet.
    """
    rows = tuple(candles)
    _validate_timezones(event_time, rows)
    if post_until is not None and _timezone_kind(post_until) != _timezone_kind(event_time):
        raise AsOfError("post_until timezone must match event_time")
    _validate_sorted_and_unique(rows)
    if post_until is not None and post_until < event_time:
        raise AsOfError("post_until must be greater than or equal to event_time")

    pre = tuple(candle for candle in rows if candle.close_time <= event_time)
    post = tuple(
        candle
        for candle in rows
        if candle.close_time > event_time and (post_until is None or candle.close_time <= post_until)
    )
    return AsOfSplit(pre_trade=pre, post_trade=post)


def split_timeframes_asof(
    candles_by_timeframe: Mapping[str, Iterable[Candle]],
    event_time: datetime,
    *,
    post_until: datetime | None = None,
) -> dict[str, AsOfSplit]:
    return {
        timeframe: split_candles_asof(candles, event_time, post_until=post_until)
        for timeframe, candles in candles_by_timeframe.items()
    }
