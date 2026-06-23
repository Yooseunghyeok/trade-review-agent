from __future__ import annotations

import io
import json
import unittest.mock
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ict_review.ledger.models import TradeEpisode
from ict_review.llm.llm_client import LlmClientError, _build_prompt, _extract_json, call_llm


def _episode() -> TradeEpisode:
    return TradeEpisode(
        episode_id="episode-0001",
        symbol="BTCUSDT",
        direction="LONG",
        opened_at=datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc),
        closed_at=datetime(2026, 1, 15, 9, 5, tzinfo=timezone.utc),
        entry_fill_ids=("f1",),
        exit_fill_ids=("f2",),
        entry_quantity=Decimal("1"),
        exit_quantity=Decimal("1"),
        entry_vwap=Decimal("100"),
        exit_vwap=Decimal("110"),
        gross_realized_pnl=Decimal("10"),
        fees=Decimal("2"),
        rebates=Decimal("0"),
        funding=Decimal("0"),
        calculated_net_pnl=Decimal("8"),
        reconciliation_status="CALCULATED_ONLY",
        assumptions=("one-way position mode",),
    )


def _features() -> dict:
    return {
        "pre_trade_close_count": 2,
        "pre_trade_last_close": Decimal("101"),
    }


def _valid_draft(run_id: str = "run_test") -> dict:
    return {
        "run_id": run_id,
        "schema_version": "2.0",
        "episode_ids": ["episode-0001"],
        "evidence_ids": ["ev-entry", "ev-exit", "ev-pnl", "ev-fee", "ev-features"],
        "metrics": [
            {"name": "entry_quantity",     "value": "1",                       "evidence_id": "ev-entry"},
            {"name": "exit_quantity",      "value": "1",                       "evidence_id": "ev-exit"},
            {"name": "gross_realized_pnl", "value": "10",                      "evidence_id": "ev-pnl"},
            {"name": "calculated_net_pnl", "value": "8",                       "evidence_id": "ev-pnl"},
            {"name": "fees",               "value": "2",                       "evidence_id": "ev-fee"},
        ],
        "observations": [
            {"text": "Pre-trade context used 2 candle closes.", "evidence_ids": ["ev-features"]},
        ],
        "questions": ["Was the entry rule documented before this trade?"],
        "pattern_candidates": [],
        "model_metadata": {"provider": "litellm-proxy", "model": "vertex-gemini-flash"},
    }


def _proxy_response(draft: dict, model: str = "vertex-gemini-flash") -> bytes:
    return json.dumps({
        "model": model,
        "choices": [{"message": {"content": json.dumps(draft)}}],
    }).encode("utf-8")


class _MockHTTPResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.status = 200

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_MockHTTPResponse":
        return self

    def __exit__(self, *_) -> None:
        pass


def test_extract_json_direct():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_from_markdown_fence():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_from_bare_fence():
    assert _extract_json('```\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_from_surrounding_text():
    assert _extract_json('here is the result: {"a": 1} done') == {"a": 1}


def test_extract_json_raises_on_garbage():
    with pytest.raises(LlmClientError, match="cannot extract JSON"):
        _extract_json("this is not json at all")


def test_build_prompt_contains_evidence_values():
    ep = _episode()
    _, user_prompt = _build_prompt("run_test", ep, _features())
    assert "ev-entry" in user_prompt
    assert "ev-pnl" in user_prompt
    assert "10" in user_prompt  # gross_realized_pnl
    assert "8" in user_prompt   # calculated_net_pnl
    assert "FOMO" not in user_prompt  # rule is mentioned in system, not in forbidden words here
    assert "episode-0001" in user_prompt


def test_call_llm_returns_valid_review_draft():
    draft = _valid_draft("run_test")
    mock_resp = _MockHTTPResponse(_proxy_response(draft))

    with unittest.mock.patch("urllib.request.urlopen", return_value=mock_resp):
        result = call_llm("run_test", _episode(), _features())

    assert result.run_id == "run_test"
    assert result.episode_ids == ("episode-0001",)
    metric_names = {m.name for m in result.metrics}
    assert "gross_realized_pnl" in metric_names
    assert "calculated_net_pnl" in metric_names


def test_call_llm_retries_on_429():
    draft = _valid_draft("run_retry")
    mock_resp = _MockHTTPResponse(_proxy_response(draft))

    import urllib.error
    call_count = 0

    def _urlopen_side_effect(req, timeout):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
        return mock_resp

    with unittest.mock.patch("urllib.request.urlopen", side_effect=_urlopen_side_effect):
        with unittest.mock.patch("time.sleep"):
            result = call_llm("run_retry", _episode(), _features(), max_retries=1)

    assert result.run_id == "run_retry"
    assert call_count == 2


def test_call_llm_raises_on_connection_error():
    with unittest.mock.patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        with pytest.raises(LlmClientError, match="cannot reach LiteLLM proxy"):
            call_llm("run_fail", _episode(), _features())


def test_call_llm_raises_after_empty_response_retries():
    empty_resp = _MockHTTPResponse(json.dumps({
        "model": "vertex-gemini-flash",
        "choices": [{"message": {"content": ""}}],
    }).encode())

    with unittest.mock.patch("urllib.request.urlopen", return_value=empty_resp):
        with unittest.mock.patch("time.sleep"):
            with pytest.raises(LlmClientError, match="empty response"):
                call_llm("run_empty", _episode(), _features(), max_retries=1)


def test_call_llm_strips_markdown_fence_from_response():
    draft = _valid_draft("run_fence")
    fenced = f"```json\n{json.dumps(draft)}\n```"
    mock_resp = _MockHTTPResponse(json.dumps({
        "model": "vertex-gemini-flash",
        "choices": [{"message": {"content": fenced}}],
    }).encode())

    with unittest.mock.patch("urllib.request.urlopen", return_value=mock_resp):
        result = call_llm("run_fence", _episode(), _features())

    assert result.run_id == "run_fence"
