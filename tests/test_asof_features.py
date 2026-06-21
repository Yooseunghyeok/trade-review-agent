from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ict_review.features.asof import AsOfError, Candle, split_candles_asof, split_timeframes_asof


def c(timeframe: str, close_time: datetime, close: str) -> Candle:
    price = Decimal(close)
    return Candle(
        timeframe=timeframe,
        close_time=close_time,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1"),
    )


def test_future_candles_excluded_from_pre_trade():
    event_time = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    candles = [
        c("5m", datetime(2026, 6, 1, 11, 55, tzinfo=timezone.utc), "100"),
        c("5m", datetime(2026, 6, 1, 12, 5, tzinfo=timezone.utc), "200"),
    ]

    result = split_candles_asof(candles, event_time)

    assert [row.close for row in result.pre_trade] == [Decimal("100")]


def test_close_time_equal_to_event_time_is_included():
    event_time = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    candles = [c("1h", event_time, "100")]

    result = split_candles_asof(candles, event_time)

    assert result.pre_trade == tuple(candles)


def test_timezone_mismatch_fails():
    event_time = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    candles = [c("5m", datetime(2026, 6, 1, 12, 0), "100")]

    with pytest.raises(AsOfError, match="timezone"):
        split_candles_asof(candles, event_time)


def test_unsorted_input_fails():
    event_time = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    candles = [
        c("5m", datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc), "100"),
        c("5m", datetime(2026, 6, 1, 11, 55, tzinfo=timezone.utc), "90"),
    ]

    with pytest.raises(AsOfError, match="sorted"):
        split_candles_asof(candles, event_time)


def test_duplicate_close_time_fails():
    event_time = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    candles = [
        c("5m", event_time, "100"),
        c("5m", event_time, "101"),
    ]

    with pytest.raises(AsOfError, match="duplicate"):
        split_candles_asof(candles, event_time)


def test_future_data_does_not_change_pre_trade():
    event_time = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    base = [
        c("5m", datetime(2026, 6, 1, 11, 55, tzinfo=timezone.utc), "100"),
        c("5m", event_time, "101"),
    ]
    with_future = [*base, c("5m", datetime(2026, 6, 1, 12, 5, tzinfo=timezone.utc), "999")]

    assert split_candles_asof(base, event_time).pre_trade == split_candles_asof(with_future, event_time).pre_trade


def test_post_trade_only_contains_allowed_window():
    event_time = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    post_until = datetime(2026, 6, 1, 12, 10, tzinfo=timezone.utc)
    candles = [
        c("5m", event_time, "100"),
        c("5m", datetime(2026, 6, 1, 12, 5, tzinfo=timezone.utc), "101"),
        c("5m", datetime(2026, 6, 1, 12, 10, tzinfo=timezone.utc), "102"),
        c("5m", datetime(2026, 6, 1, 12, 15, tzinfo=timezone.utc), "103"),
    ]

    result = split_candles_asof(candles, event_time, post_until=post_until)

    assert [row.close for row in result.post_trade] == [Decimal("101"), Decimal("102")]


def test_all_requested_timeframes_use_same_rule():
    event_time = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    candles_by_timeframe = {
        tf: [
            c(tf, datetime(2026, 6, 1, 11, 0, tzinfo=timezone.utc), "100"),
            c(tf, datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc), "200"),
        ]
        for tf in ("5m", "1h", "4h")
    }

    result = split_timeframes_asof(candles_by_timeframe, event_time)

    assert set(result) == {"5m", "1h", "4h"}
    assert all([row.close for row in split.pre_trade] == [Decimal("100")] for split in result.values())
