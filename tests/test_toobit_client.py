import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from ict_review.integrations import toobit_client
from ict_review.integrations.toobit_client import ToobitClientError, daily_kst_window, fetch_toobit_daily_snapshot


def runtime_dir(name: str) -> Path:
    path = Path("tests") / "fixtures" / "runtime" / f"{name}-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def test_daily_kst_window_converts_to_utc_milliseconds():
    window = daily_kst_window(date(2026, 6, 16))

    assert window.start_kst.isoformat() == "2026-06-16T00:00:00+09:00"
    assert window.start_utc.isoformat() == "2026-06-15T15:00:00+00:00"
    assert window.end_utc_exclusive.isoformat() == "2026-06-16T15:00:00+00:00"
    assert window.end_ms_inclusive == window.start_ms + (24 * 60 * 60 * 1000) - 1


def test_daily_kst_window_boundaries_for_start_and_end_of_day():
    window = daily_kst_window(date(2026, 6, 15))

    assert window.start_ms == int(datetime(2026, 6, 14, 15, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert window.end_ms_inclusive == int(datetime(2026, 6, 15, 14, 59, 59, 999000, tzinfo=timezone.utc).timestamp() * 1000)


def test_fetch_toobit_daily_snapshot_uses_env_and_saves_raw_without_secrets(monkeypatch):
    root = runtime_dir("toobit-client")
    project = root / "project"
    project.mkdir()
    (project / ".env").write_text(
        "TOOBIT_ACCESS_KEY=access-secret\nTOOBIT_SECRET_KEY=signing-secret\nTOOBIT_API_URL=https://example.invalid\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TOOBIT_ACCESS_KEY", raising=False)
    monkeypatch.delenv("TOOBIT_SECRET_KEY", raising=False)
    calls = []

    def fake_http_get(url, headers, *, timeout=20):
        calls.append((url, headers))
        query = parse_qs(urlparse(url).query)
        assert query["startTime"] == [str(daily_kst_window(date(2026, 6, 16)).start_ms)]
        assert query["endTime"] == [str(daily_kst_window(date(2026, 6, 16)).end_ms_inclusive)]
        if "userTrades" in url:
            assert headers["X-MBX-APIKEY"] == "access-secret"
            return 200, [
                {
                    "time": str(daily_kst_window(date(2026, 6, 16)).start_ms),
                    "id": "fill-001",
                    "orderId": "order-001",
                    "symbol": "BTC-SWAP-USDT",
                    "price": "100",
                    "qty": "1",
                    "commissionAsset": "USDT",
                    "commission": "0.1",
                    "makerRebate": "0",
                    "side": "BUY_OPEN",
                    "realizedPnl": "0",
                }
            ]
        return 200, [[str(daily_kst_window(date(2026, 6, 16)).start_ms), "100", "101", "99", "100.5", "10"]]

    monkeypatch.setattr(toobit_client, "_http_get", fake_http_get)
    output = fetch_toobit_daily_snapshot(date(2026, 6, 16), output_path=root / "raw" / "snapshot.json", project_root=project)

    payload = json.loads(output.read_text(encoding="utf-8"))
    serialized = json.dumps(payload)
    assert payload["date_kst"] == "2026-06-16"
    assert "fills_raw" in payload
    assert "candles_raw" in payload
    assert "access-secret" not in serialized
    assert "signing-secret" not in serialized
    assert len(calls) == 2


def test_fetch_toobit_daily_snapshot_fails_on_no_fills(monkeypatch):
    root = runtime_dir("toobit-empty")
    project = root / "project"
    project.mkdir()
    (project / ".env").write_text("TOOBIT_ACCESS_KEY=access\nTOOBIT_SECRET_KEY=secret\n", encoding="utf-8")
    monkeypatch.delenv("TOOBIT_ACCESS_KEY", raising=False)
    monkeypatch.delenv("TOOBIT_SECRET_KEY", raising=False)

    def fake_http_get(url, headers, *, timeout=20):
        return 200, []

    monkeypatch.setattr(toobit_client, "_http_get", fake_http_get)

    with pytest.raises(ToobitClientError, match="returned no fills"):
        fetch_toobit_daily_snapshot(date(2026, 6, 16), output_path=root / "raw" / "snapshot.json", project_root=project)
