import json
import uuid
from datetime import date
from pathlib import Path

import pytest

from ict_review.cli.daily_review import DailyReviewError, finalize, mark_run_status, normalize_llm_review_file, normalize_llm_review_payload, prepare, status
from ict_review.integrations.toobit_client import daily_kst_window


def runtime_dir(name: str) -> Path:
    path = Path("tests") / "fixtures" / "runtime" / f"{name}-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def test_daily_prepare_records_waiting_state():
    data_root = runtime_dir("daily-prepare") / "data"
    run_dir = prepare("2026-06-01", data_root=data_root, fixture=Path("tests/fixtures/offline_review_fixture.json"))

    assert (run_dir / "review_request.json").exists()
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "WAITING_FOR_LLM"
    daily_status = status("2026-06-01", data_root=data_root)
    assert daily_status["latest_status"] == "WAITING_FOR_LLM"


def test_finalize_publishes_valid_review_and_is_idempotent():
    data_root = runtime_dir("daily-finalize") / "data"
    run_dir = prepare("2026-06-01", data_root=data_root, fixture=Path("tests/fixtures/offline_review_fixture.json"))
    request = json.loads((run_dir / "review_request.json").read_text(encoding="utf-8"))
    review = {
        "run_id": request["run_id"],
        "episode_ids": request["episode_ids"],
        "metrics": request["required_metrics"],
        "observations": [{"text": "Pre-trade context used fixture evidence.", "evidence_ids": ["ev-features"]}],
        "questions": ["Was the setup rule documented before entry?"],
        "pattern_candidates": ["daily-fixture-candidate"],
        "evidence_ids": request["evidence_ids"],
        "model_metadata": {"provider": "fixture", "model": "deterministic"},
        "schema_version": "2.0",
    }
    review_path = run_dir / "llm_review.json"
    review_path.write_text(json.dumps(review), encoding="utf-8")

    markdown = finalize(request["run_id"], review_path, data_root=data_root)
    assert markdown.exists()
    assert finalize(request["run_id"], review_path, data_root=data_root) == markdown
    assert status("2026-06-01", data_root=data_root)["latest_status"] == "PUBLISHED"


def test_finalize_invalid_review_records_invalid_state():
    data_root = runtime_dir("daily-invalid") / "data"
    run_dir = prepare("2026-06-01", data_root=data_root, fixture=Path("tests/fixtures/offline_review_fixture.json"))
    request = json.loads((run_dir / "review_request.json").read_text(encoding="utf-8"))
    bad_review = {
        "run_id": request["run_id"],
        "episode_ids": request["episode_ids"],
        "metrics": [],
        "observations": [],
        "questions": [],
        "pattern_candidates": [],
        "evidence_ids": [],
        "model_metadata": {},
        "schema_version": "2.0",
    }
    review_path = run_dir / "bad_review.json"
    review_path.write_text(json.dumps(bad_review), encoding="utf-8")

    with pytest.raises(Exception):
        finalize(request["run_id"], review_path, data_root=data_root)
    assert status("2026-06-01", data_root=data_root)["latest_status"] == "INVALID_LLM_OUTPUT"


def test_finalize_accepts_json_code_fence_and_drops_unsupported_pattern_candidates():
    data_root = runtime_dir("daily-fenced") / "data"
    run_dir = prepare("2026-06-01", data_root=data_root, fixture=Path("tests/fixtures/offline_review_fixture.json"))
    request = json.loads((run_dir / "review_request.json").read_text(encoding="utf-8"))
    review = {
        "run_id": request["run_id"],
        "episode_ids": request["episode_ids"],
        "metrics": request["required_metrics"],
        "observations": [{"text": "Evidence-linked observation.", "evidence_ids": ["ev-features"]}],
        "questions": [],
        "pattern_candidates": ["unsupported-ict-pattern"],
        "evidence_ids": request["evidence_ids"],
        "model_metadata": {"provider": "fixture"},
        "schema_version": "2.0",
    }
    review_path = run_dir / "fenced_review.json"
    review_path.write_text("```json\n" + json.dumps(review) + "\n```", encoding="utf-8")

    markdown = finalize(request["run_id"], review_path, data_root=data_root)

    assert markdown.exists()
    content = markdown.read_text(encoding="utf-8")
    assert "unsupported-ict-pattern" not in content


def test_normalize_llm_output_handles_utf16_code_fence_wrapper_and_forces_run_id():
    root = runtime_dir("llm-normalize")
    raw_path = root / "hermes.raw.txt"
    output_path = root / "review_draft.json"
    payload = {
        "response": "```json\n" + json.dumps({
            "episode_ids": ["episode-0001"],
            "metrics": [
                {"name": "entry_quantity", "value": "1", "evidence_id": "ev-entry"},
                {"name": "exit_quantity", "value": "1", "evidence_id": "ev-exit"},
                {"name": "gross_realized_pnl", "value": "2.58154", "evidence_id": "ev-pnl"},
                {"name": "calculated_net_pnl", "value": "-3.8274723", "evidence_id": "ev-pnl"},
                {"name": "fees", "value": "6.4090123", "evidence_id": "ev-fee"},
            ],
            "observations": [{"text": "Evidence-linked.", "evidence_ids": ["ev-pnl"]}],
            "questions": [],
            "pattern_candidates": ["unsupported"],
            "evidence_ids": ["ev-entry", "ev-exit", "ev-pnl", "ev-fee"],
            "model_metadata": {"provider": "hermes"},
            "schema_version": "2.0",
        }) + "\n```"
    }
    raw_path.write_text(json.dumps(payload), encoding="utf-16")

    normalize_llm_review_file(run_id="run_20260616T000000Z_abcdefabcdef", raw_path=raw_path, output_path=output_path)

    raw_bytes = output_path.read_bytes()
    assert not raw_bytes.startswith(b"\xff\xfe")
    normalized = json.loads(raw_bytes.decode("utf-8"))
    assert normalized["run_id"] == "run_20260616T000000Z_abcdefabcdef"
    assert normalized["pattern_candidates"] == []


def test_normalize_llm_output_reports_missing_fields_and_top_level_keys():
    root = runtime_dir("llm-missing")
    raw_path = root / "hermes.raw.txt"
    output_path = root / "review_draft.json"
    raw_path.write_text(json.dumps({"content": {"metrics": []}}), encoding="utf-8")

    with pytest.raises(DailyReviewError, match="missing required fields: .*top-level keys: metrics"):
        normalize_llm_review_file(run_id="run_20260616T000000Z_abcdefabcdef", raw_path=raw_path, output_path=output_path)


def test_normalize_llm_output_detects_model_rate_limit_without_writing_draft():
    root = runtime_dir("llm-rate-limit")
    raw_path = root / "hermes.raw.txt"
    output_path = root / "review_draft.json"
    raw_path.write_text("API call failed after 3 retries: HTTP 429 Resource exhausted", encoding="utf-8")

    with pytest.raises(DailyReviewError, match="MODEL_RATE_LIMIT"):
        normalize_llm_review_file(run_id="run_20260616T000000Z_abcdefabcdef", raw_path=raw_path, output_path=output_path)

    assert not output_path.exists()


def test_normalize_llm_output_detects_empty_model_response():
    with pytest.raises(DailyReviewError, match="MODEL_EMPTY_RESPONSE"):
        normalize_llm_review_payload("No reply: the model returned empty content after retries", run_id="run_20260601T000000Z_aaaaaaaaaaaa")


def test_mark_run_status_records_model_rate_limit():
    data_root = runtime_dir("daily-model-rate-limit") / "data"
    run_dir = prepare("2026-06-01", data_root=data_root, fixture=Path("tests/fixtures/offline_review_fixture.json"))
    run_id = run_dir.name

    mark_run_status(run_id, "MODEL_RATE_LIMIT", "Hermes output indicated model rate limit.", data_root=data_root)

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "MODEL_RATE_LIMIT"
    assert "rate limit" in manifest["failure_reason"]
    assert status("2026-06-01", data_root=data_root)["latest_status"] == "MODEL_RATE_LIMIT"


def test_prepare_blocks_same_date_after_published_run():
    data_root = runtime_dir("daily-duplicate") / "data"
    run_dir = prepare("2026-06-01", data_root=data_root, fixture=Path("tests/fixtures/offline_review_fixture.json"))
    request = json.loads((run_dir / "review_request.json").read_text(encoding="utf-8"))
    review = {
        "run_id": request["run_id"],
        "episode_ids": request["episode_ids"],
        "metrics": request["required_metrics"],
        "observations": [{"text": "Evidence-linked observation.", "evidence_ids": ["ev-features"]}],
        "questions": [],
        "pattern_candidates": [],
        "evidence_ids": request["evidence_ids"],
        "model_metadata": {"provider": "fixture"},
        "schema_version": "2.0",
    }
    review_path = run_dir / "llm_review.json"
    review_path.write_text(json.dumps(review), encoding="utf-8")
    finalize(request["run_id"], review_path, data_root=data_root)

    with pytest.raises(DailyReviewError, match="PUBLISHED run already exists"):
        prepare("2026-06-01", data_root=data_root, fixture=Path("tests/fixtures/offline_review_fixture.json"))


def test_prepare_fetches_read_only_snapshot_when_fixture_and_raw_are_missing(monkeypatch):
    data_root = runtime_dir("daily-fetch") / "data"
    window_start_ms = daily_kst_window(date(2026, 6, 16)).start_ms

    def fake_fetch(trading_date, *, output_path, project_root, symbol=None, interval="5m", intervals=None, limit=500):
        output_path.parent.mkdir(parents=True)
        output_path.write_text(json.dumps({
            "mode": "read-only-daily-snapshot",
            "date_kst": str(trading_date),
            "fills_raw": {
                "successful_endpoint": "/api/v1/futures/userTrades",
                "attempts": [{
                    "endpoint": "/api/v1/futures/userTrades",
                    "status": 200,
                    "ok": True,
                    "response": [{
                        "time": str(window_start_ms),
                        "id": "fill-kst-001",
                        "orderId": "order-kst-001",
                        "symbol": "BTC-SWAP-USDT",
                        "price": "100",
                        "qty": "1",
                        "commissionAsset": "USDT",
                        "commission": "0.1",
                        "makerRebate": "0",
                        "side": "BUY_OPEN",
                        "realizedPnl": "0"
                    }]
                }]
            },
            "candles_raw": {
                "interval": "5m",
                "response": [[str(window_start_ms), "100", "101", "99", "100.5", "10"]]
            }
        }), encoding="utf-8")
        return output_path

    monkeypatch.setattr("ict_review.cli.daily_review.fetch_toobit_daily_snapshot", fake_fetch)
    run_dir = prepare("2026-06-16", data_root=data_root, run_id="run_20260616T000000Z_abcdefabcdef")

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    input_path = Path(manifest["inputs"][0]["path"])
    assert input_path.name == "toobit_daily_snapshot.json"
    assert "run_20260616T000000Z_abcdefabcdef" in str(input_path)
    assert manifest["inputs"][0]["sha256"]
    assert manifest["status"] == "WAITING_FOR_LLM"


def test_prepare_filters_to_requested_kst_day_boundaries(monkeypatch):
    data_root = runtime_dir("daily-kst-boundary") / "data"
    window = daily_kst_window(date(2026, 6, 15))
    next_day_start_ms = daily_kst_window(date(2026, 6, 16)).start_ms

    def fake_fetch(trading_date, *, output_path, project_root, symbol=None, interval="5m", intervals=None, limit=500):
        output_path.parent.mkdir(parents=True)
        output_path.write_text(json.dumps({
            "mode": "read-only-daily-snapshot",
            "date_kst": str(trading_date),
            "fills_raw": {
                "successful_endpoint": "/api/v1/futures/userTrades",
                "attempts": [{
                    "endpoint": "/api/v1/futures/userTrades",
                    "status": 200,
                    "ok": True,
                    "response": [
                        {
                            "time": str(window.start_ms),
                            "id": "fill-kst-start",
                            "orderId": "order-kst-start",
                            "symbol": "BTC-SWAP-USDT",
                            "price": "100",
                            "qty": "1",
                            "commissionAsset": "USDT",
                            "commission": "0.1",
                            "makerRebate": "0",
                            "side": "BUY_OPEN",
                            "realizedPnl": "0"
                        },
                        {
                            "time": str(window.end_ms_inclusive),
                            "id": "fill-kst-end",
                            "orderId": "order-kst-end",
                            "symbol": "BTC-SWAP-USDT",
                            "price": "110",
                            "qty": "1",
                            "commissionAsset": "USDT",
                            "commission": "0.1",
                            "makerRebate": "0",
                            "side": "SELL_CLOSE",
                            "realizedPnl": "10"
                        },
                        {
                            "time": str(next_day_start_ms),
                            "id": "fill-next-day-start",
                            "orderId": "order-next-day-start",
                            "symbol": "BTC-SWAP-USDT",
                            "price": "111",
                            "qty": "1",
                            "commissionAsset": "USDT",
                            "commission": "0.1",
                            "makerRebate": "0",
                            "side": "BUY_OPEN",
                            "realizedPnl": "0"
                        }
                    ]
                }]
            },
            "candles_raw": {
                "interval": "5m",
                "response": [[str(window.start_ms), "100", "101", "99", "100.5", "10"]]
            }
        }), encoding="utf-8")
        return output_path

    monkeypatch.setattr("ict_review.cli.daily_review.fetch_toobit_daily_snapshot", fake_fetch)
    run_dir = prepare("2026-06-15", data_root=data_root, run_id="run_20260615T000000Z_abcdefabcdef")

    fills = json.loads((run_dir / "normalized_fills.json").read_text(encoding="utf-8"))
    assert [fill["fill_id"] for fill in fills] == ["fill-kst-start", "fill-kst-end"]
