from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
DEFAULT_BASE_URL = "https://api.toobit.com"
DEFAULT_SYMBOL = "BTC-SWAP-USDT"


class ToobitClientError(ValueError):
    """Raised when read-only Toobit data cannot be collected safely."""


@dataclass(frozen=True)
class ToobitDailyWindow:
    trading_date: date
    start_kst: datetime
    end_kst_exclusive: datetime
    start_utc: datetime
    end_utc_exclusive: datetime
    start_ms: int
    end_ms_inclusive: int


def daily_kst_window(trading_date: date) -> ToobitDailyWindow:
    start_kst = datetime.combine(trading_date, time.min, tzinfo=KST)
    end_kst = start_kst + timedelta(days=1)
    start_utc = start_kst.astimezone(timezone.utc)
    end_utc = end_kst.astimezone(timezone.utc)
    return ToobitDailyWindow(
        trading_date=trading_date,
        start_kst=start_kst,
        end_kst_exclusive=end_kst,
        start_utc=start_utc,
        end_utc_exclusive=end_utc,
        start_ms=int(start_utc.timestamp() * 1000),
        end_ms_inclusive=int(end_utc.timestamp() * 1000) - 1,
    )


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _sign_query(params: dict[str, Any], secret: str) -> str:
    query = urlencode(params)
    signature = hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{query}&signature={signature}"


def _http_get(url: str, headers: dict[str, str], *, timeout: int = 20) -> tuple[int, Any]:
    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = body
        return exc.code, parsed
    except URLError as exc:
        return 0, {"error": "URLError", "reason": str(exc.reason)}


def _rows(response: Any) -> list[Any]:
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        for key in ("data", "result", "rows", "list"):
            value = response.get(key)
            if isinstance(value, list):
                return value
    return []


def fetch_toobit_daily_snapshot(
    trading_date: date,
    *,
    output_path: Path,
    project_root: Path,
    symbol: str | None = None,
    interval: str = "5m",
    limit: int = 500,
) -> Path:
    """Collect read-only Toobit fills and candles for one KST trading date.

    This function never prints credentials and never performs account mutation calls.
    """
    load_env_file(project_root / ".env")
    access_key = os.getenv("TOOBIT_ACCESS_KEY", "").strip()
    secret_key = os.getenv("TOOBIT_SECRET_KEY", "").strip()
    if not access_key or not secret_key:
        raise ToobitClientError("missing TOOBIT_ACCESS_KEY or TOOBIT_SECRET_KEY in environment")

    actual_symbol = symbol or os.getenv("TOOBIT_SYMBOL", "").strip() or DEFAULT_SYMBOL
    base_url = (os.getenv("TOOBIT_API_URL", "").strip() or DEFAULT_BASE_URL).rstrip("/")
    window = daily_kst_window(trading_date)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    trade_params = {
        "symbol": actual_symbol,
        "startTime": window.start_ms,
        "endTime": window.end_ms_inclusive,
        "limit": limit,
        "recvWindow": 5000,
        "timestamp": now_ms,
    }
    headers = {
        "User-Agent": "ict-trading-wiki-readonly/0.1",
        "Accept": "application/json",
        "X-MBX-APIKEY": access_key,
        "X-BH-APIKEY": access_key,
        "X-API-KEY": access_key,
        "X-BB-APIKEY": access_key,
    }
    trade_endpoint = "/api/v1/futures/userTrades"
    trade_url = f"{base_url}{trade_endpoint}?{_sign_query(trade_params, secret_key)}"
    trade_status, trade_response = _http_get(trade_url, headers)
    if not (200 <= trade_status < 300):
        raise ToobitClientError(f"Toobit userTrades failed with HTTP {trade_status}")
    if not _rows(trade_response):
        raise ToobitClientError(f"Toobit userTrades returned no fills for {trading_date.isoformat()} KST")

    candle_params = {
        "symbol": actual_symbol,
        "interval": interval,
        "startTime": window.start_ms,
        "endTime": window.end_ms_inclusive,
        "limit": limit,
    }
    candle_endpoint = "/quote/v1/klines"
    candle_url = f"{base_url}{candle_endpoint}?{urlencode(candle_params)}"
    candle_status, candle_response = _http_get(candle_url, {"User-Agent": "ict-trading-wiki-candle-fetch/0.1", "Accept": "application/json"})
    if not (200 <= candle_status < 300):
        raise ToobitClientError(f"Toobit klines failed with HTTP {candle_status}")
    if not _rows(candle_response):
        raise ToobitClientError(f"Toobit klines returned no candles for {trading_date.isoformat()} KST")

    payload = {
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "mode": "read-only-daily-snapshot",
        "symbol": actual_symbol,
        "date_kst": trading_date.isoformat(),
        "window": {
            "start_kst": window.start_kst.isoformat(),
            "end_kst_exclusive": window.end_kst_exclusive.isoformat(),
            "start_utc": window.start_utc.isoformat(),
            "end_utc_exclusive": window.end_utc_exclusive.isoformat(),
            "start_ms": window.start_ms,
            "end_ms_inclusive": window.end_ms_inclusive,
        },
        "fills_raw": {
            "successful_endpoint": trade_endpoint,
            "attempts": [{"endpoint": trade_endpoint, "status": trade_status, "ok": True, "response": trade_response}],
        },
        "candles_raw": {
            "endpoint": candle_endpoint,
            "status": candle_status,
            "ok": True,
            "interval": interval,
            "response": candle_response,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=False)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return output_path
