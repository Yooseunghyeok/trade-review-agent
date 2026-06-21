import json
from datetime import timezone
from decimal import Decimal
from pathlib import Path

import pytest

from ict_review.integrations.toobit_adapter import ToobitAdapterError, adapt_raw_toobit_fills
from ict_review.ledger.episode_builder import build_trade_episodes


def raw_fixture():
    return json.loads(Path("tests/fixtures/toobit_raw_synthetic.json").read_text(encoding="utf-8"))


def test_toobit_adapter_maps_raw_fields_to_v2_fills():
    result = adapt_raw_toobit_fills(raw_fixture())

    assert len(result.fills) == 2
    assert result.fills[0].fill_id == "synthetic-fill-open"
    assert result.fills[0].order_id == "synthetic-order-open"
    assert result.fills[0].side == "SELL"
    assert result.fills[0].filled_at.tzinfo == timezone.utc
    assert result.fills[1].side == "BUY"
    assert result.fills[1].fee == Decimal("1")
    assert result.fills[1].rebate == Decimal("0.1")
    assert result.fills[1].funding == Decimal("0")
    assert result.fills[1].exchange_realized_pnl == Decimal("10")


def test_toobit_adapter_rejects_non_one_way_mode():
    with pytest.raises(ToobitAdapterError, match="unsupported Toobit position mode"):
        adapt_raw_toobit_fills(raw_fixture(), position_mode="hedge")


def test_toobit_adapter_rejects_duplicate_fill_id():
    raw = raw_fixture()
    raw["attempts"][0]["response"][1]["id"] = "synthetic-fill-close"

    with pytest.raises(ToobitAdapterError, match="duplicate fill_id"):
        adapt_raw_toobit_fills(raw)


def test_toobit_adapter_rejects_bad_timestamp():
    raw = raw_fixture()
    raw["attempts"][0]["response"][0]["time"] = "2026-06-15T00:00:00"

    with pytest.raises(ToobitAdapterError, match="invalid millisecond timestamp"):
        adapt_raw_toobit_fills(raw)


def test_toobit_btc_swap_contract_quantity_does_not_multiply_pnl_by_1000():
    raw = {
        "successful_endpoint": "/api/v1/futures/userTrades",
        "attempts": [{
            "endpoint": "/api/v1/futures/userTrades",
            "status": 200,
            "ok": True,
            "response": [
                {
                    "time": "1781535600000",
                    "id": "fill-open",
                    "orderId": "order-open",
                    "symbol": "BTC-SWAP-USDT",
                    "price": "64000",
                    "qty": "253.5",
                    "commissionAsset": "USDT",
                    "commission": "3",
                    "makerRebate": "0",
                    "side": "SELL_OPEN",
                    "realizedPnl": "0",
                },
                {
                    "time": "1781535900000",
                    "id": "fill-close",
                    "orderId": "order-close",
                    "symbol": "BTC-SWAP-USDT",
                    "price": "63989.81641025641025641025641",
                    "qty": "253.5",
                    "commissionAsset": "USDT",
                    "commission": "3.4090123",
                    "makerRebate": "0",
                    "side": "BUY_CLOSE",
                    "realizedPnl": "2.58154",
                },
            ],
        }],
    }

    fills = adapt_raw_toobit_fills(raw).fills
    assert fills[0].quantity == Decimal("0.2535")
    assert fills[1].quantity == Decimal("0.2535")

    episode = build_trade_episodes(fills)[0]
    assert episode.gross_realized_pnl == Decimal("2.581540000000000000")
    assert episode.fees == Decimal("6.4090123")
    assert episode.calculated_net_pnl == Decimal("-3.827472300000000000")
