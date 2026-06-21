from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ict_review.ledger.episode_builder import build_trade_episodes
from ict_review.ledger.models import Fill, LedgerError
from ict_review.ledger.normalize_fills import normalize_fills
from ict_review.ledger.position_engine import build_transitions


def t(minute: int) -> datetime:
    return datetime(2026, 6, 1, 12, minute, tzinfo=timezone.utc)


def fill(
    fill_id: str,
    side: str,
    qty: str,
    price: str,
    minute: int,
    *,
    fee: str = "1",
    rebate: str = "0",
    funding: str = "0",
) -> Fill:
    return Fill(
        fill_id=fill_id,
        order_id=fill_id,
        symbol="BTCUSDT",
        side=side,
        quantity=Decimal(qty),
        price=Decimal(price),
        fee=Decimal(fee),
        rebate=Decimal(rebate),
        funding=Decimal(funding),
        filled_at=t(minute),
    )


def test_single_entry_then_full_close():
    episodes = build_trade_episodes([fill("f1", "BUY", "1", "100", 0), fill("f2", "SELL", "1", "110", 5)])

    episode = episodes[0]
    assert episode.direction == "LONG"
    assert episode.closed_at == t(5)
    assert episode.entry_vwap == Decimal("100")
    assert episode.exit_vwap == Decimal("110")
    assert episode.gross_realized_pnl == Decimal("10")
    assert episode.calculated_net_pnl == Decimal("8")


def test_multiple_partial_fills_for_one_entry():
    episodes = build_trade_episodes([
        fill("f1", "BUY", "1", "100", 0),
        fill("f2", "BUY", "1", "102", 1),
        fill("f3", "SELL", "2", "105", 2),
    ])

    assert episodes[0].entry_fill_ids == ("f1", "f2")
    assert episodes[0].entry_quantity == Decimal("2")
    assert episodes[0].entry_vwap == Decimal("101")


def test_scale_in():
    episodes = build_trade_episodes([
        fill("f1", "SELL", "1", "100", 0),
        fill("f2", "SELL", "2", "90", 1),
        fill("f3", "BUY", "3", "80", 2),
    ])

    episode = episodes[0]
    assert episode.direction == "SHORT"
    assert episode.entry_quantity == Decimal("3")
    assert episode.entry_vwap == Decimal("93.33333333333333333333333333")
    assert episode.gross_realized_pnl == Decimal("40.0000000000000000000000000")


def test_partial_close_then_full_close():
    episodes = build_trade_episodes([
        fill("f1", "BUY", "3", "100", 0),
        fill("f2", "SELL", "1", "110", 1),
        fill("f3", "SELL", "2", "120", 2),
    ])

    episode = episodes[0]
    assert episode.exit_fill_ids == ("f2", "f3")
    assert episode.exit_quantity == Decimal("3")
    assert episode.gross_realized_pnl == Decimal("50")


def test_open_position_episode_has_null_close_fields():
    episodes = build_trade_episodes([fill("f1", "BUY", "1", "100", 0)])

    assert episodes[0].closed_at is None
    assert episodes[0].exit_vwap is None
    assert episodes[0].exit_quantity == Decimal("0")


def test_position_reversal_creates_two_episodes():
    episodes = build_trade_episodes([fill("f1", "BUY", "1", "100", 0), fill("f2", "SELL", "2", "90", 1)])

    assert len(episodes) == 2
    assert episodes[0].direction == "LONG"
    assert episodes[0].closed_at == t(1)
    assert episodes[1].direction == "SHORT"
    assert episodes[1].closed_at is None


def test_reversal_allocates_fee_rebate_and_funding_without_duplication():
    original = [
        fill("f1", "BUY", "1", "100", 0, fee="1", rebate="0.1", funding="-0.2"),
        fill("f2", "SELL", "2", "90", 1, fee="2", rebate="0.4", funding="-0.6"),
    ]
    episodes = build_trade_episodes(original)

    assert len(episodes) == 2
    assert episodes[0].direction == "LONG"
    assert episodes[0].fees == Decimal("2.0")
    assert episodes[0].rebates == Decimal("0.3")
    assert episodes[0].funding == Decimal("-0.5")
    assert episodes[1].direction == "SHORT"
    assert episodes[1].fees == Decimal("1.0")
    assert episodes[1].rebates == Decimal("0.2")
    assert episodes[1].funding == Decimal("-0.3")

    assert sum(fill.fee for fill in original) == sum(episode.fees for episode in episodes)
    assert sum(fill.rebate for fill in original) == sum(episode.rebates for episode in episodes)
    assert sum(fill.funding for fill in original) == sum(episode.funding for episode in episodes)


def test_negative_quantity_fails():
    with pytest.raises(LedgerError, match="positive"):
        normalize_fills([
            {"fill_id": "f1", "order_id": "o1", "symbol": "BTCUSDT", "side": "BUY", "quantity": "-1", "price": "100", "filled_at": "2026-06-01T12:00:00+00:00"}
        ])


def test_duplicate_fill_id_fails():
    fills = [fill("f1", "BUY", "1", "100", 0), fill("f1", "SELL", "1", "101", 1)]

    with pytest.raises(LedgerError, match="duplicate"):
        build_trade_episodes(fills)


def test_transitions_are_available_for_audit():
    transitions = build_transitions([fill("f1", "BUY", "1", "100", 0), fill("f2", "SELL", "1", "101", 1)])

    assert [transition.transition_type for transition in transitions] == ["OPEN_OR_SCALE_IN", "CLOSE"]
