from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from ict_review.cli.review_offline import _json_default
from ict_review.features.asof import Candle, split_candles_asof, split_timeframes_asof
from ict_review.ingestion.manifest import (
    RunStatus,
    add_outputs,
    build_input_file,
    build_output_file,
    create_manifest,
    generate_run_id,
    mark_failed,
    rewrite_manifest,
    write_manifest,
)
from ict_review.integrations.toobit_adapter import adapt_raw_toobit_fills
from ict_review.integrations.toobit_client import ToobitClientError, fetch_toobit_daily_snapshot
from ict_review.ledger.episode_builder import build_trade_episodes
from ict_review.ledger.models import Fill
from ict_review.ledger.normalize_fills import normalize_fills
from ict_review.narrative.pattern_memory import (
    PatternMemoryError,
    confirm_candidate,
    confirmed_patterns,
    load_pattern_memory,
    make_candidate,
    save_pattern_memory,
)
from ict_review.rendering.markdown_renderer import render_markdown
from ict_review.validation.evidence_validator import ReviewValidationError, require_valid_review_draft


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data"
DEFAULT_PATTERN_MEMORY = PROJECT_ROOT / "memory" / "pattern_memory.json"
KST = ZoneInfo("Asia/Seoul")


class DailyReviewError(ValueError):
    """Raised when a daily run cannot advance deterministically."""


REVIEW_DRAFT_REQUIRED_FIELDS = {
    "run_id",
    "episode_ids",
    "metrics",
    "observations",
    "questions",
    "pattern_candidates",
    "evidence_ids",
    "model_metadata",
    "schema_version",
}
LLM_WRAPPER_KEYS = ("response", "content", "message", "output")
MODEL_RATE_LIMIT_PATTERNS = ("API call failed", "HTTP 429", "Resource exhausted")
MODEL_EMPTY_RESPONSE_PATTERNS = ("No reply:", "empty content after retries", "model returned empty content")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text_any_encoding(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if b"\x00" in raw[:200]:
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def is_model_rate_limit_output(text: str) -> bool:
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in MODEL_RATE_LIMIT_PATTERNS)


def is_model_empty_output(text: str) -> bool:
    lowered = text.lower()
    return not text.strip() or any(pattern.lower() in lowered for pattern in MODEL_EMPTY_RESPONSE_PATTERNS)


def _strip_json_code_fence(text: str) -> str:
    stripped = text.strip()
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if not lines:
        return stripped
    if lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _extract_json_text(text: str) -> str:
    stripped = text.strip()
    try:
        json.loads(stripped)
        return stripped
    except json.JSONDecodeError:
        pass

    stripped = _strip_json_code_fence(stripped)
    try:
        json.loads(stripped)
        return stripped
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char not in "[{":
            continue
        try:
            _, end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        return stripped[index:index + end]
    raise DailyReviewError("could not extract JSON object from Hermes output")


def _unwrap_llm_payload(payload: Any) -> dict[str, Any]:
    current = payload
    seen = 0
    while isinstance(current, dict) and seen < 8:
        if REVIEW_DRAFT_REQUIRED_FIELDS & set(current):
            return current
        for key in LLM_WRAPPER_KEYS:
            if key in current:
                current = current[key]
                if isinstance(current, str):
                    current = json.loads(_extract_json_text(current))
                seen += 1
                break
        else:
            return current
    if not isinstance(current, dict):
        raise DailyReviewError(f"LLM review JSON must be an object after unwrapping, got {type(current).__name__}")
    return current


def normalize_llm_review_payload(raw_text: str, *, run_id: str) -> dict[str, Any]:
    if is_model_rate_limit_output(raw_text):
        raise DailyReviewError("MODEL_RATE_LIMIT: Hermes output indicates API call failed with HTTP 429 / Resource exhausted")
    if is_model_empty_output(raw_text):
        raise DailyReviewError("MODEL_EMPTY_RESPONSE: Hermes returned no review content")
    payload = json.loads(_extract_json_text(raw_text))
    if isinstance(payload, str):
        payload = json.loads(_extract_json_text(payload))
    payload = _unwrap_llm_payload(payload)
    if not isinstance(payload, dict):
        raise DailyReviewError("LLM review JSON must be an object")
    payload = dict(payload)
    payload["run_id"] = run_id
    payload["pattern_candidates"] = []
    missing = sorted(REVIEW_DRAFT_REQUIRED_FIELDS - set(payload))
    if missing:
        actual_keys = sorted(str(key) for key in payload.keys())
        raise DailyReviewError(f"LLM review JSON missing required fields: {', '.join(missing)}; top-level keys: {', '.join(actual_keys)}")
    return payload


def normalize_llm_review_file(*, run_id: str, raw_path: Path, output_path: Path) -> Path:
    payload = normalize_llm_review_payload(_read_text_any_encoding(raw_path), run_id=run_id)
    _write_json(output_path, payload)
    return output_path


def _read_llm_review_json(path: Path, *, run_id: str | None = None) -> dict[str, Any]:
    actual_run_id = run_id or ""
    payload = normalize_llm_review_payload(_read_text_any_encoding(path), run_id=actual_run_id)
    if run_id is None and not actual_run_id:
        payload["run_id"] = payload.get("run_id", "")
    return payload


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _coerce_date(value: date | str) -> date:
    return value if isinstance(value, date) else _parse_date(str(value))


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyReviewError(f"timezone-aware timestamp required: {value}")
    return parsed.astimezone(timezone.utc)


def _fill_trading_date_kst(fill: Fill) -> date:
    return fill.filled_at.astimezone(KST).date()


def _parse_candle(row: dict[str, Any]) -> Candle:
    return Candle(
        timeframe=str(row["timeframe"]),
        close_time=_parse_time(str(row["close_time"])),
        open=Decimal(str(row["open"])),
        high=Decimal(str(row["high"])),
        low=Decimal(str(row["low"])),
        close=Decimal(str(row["close"])),
        volume=Decimal(str(row.get("volume", "0"))),
    )


def _extract_response_rows(response: Any) -> list[Any]:
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        for key in ("data", "result", "rows", "list"):
            value = response.get(key)
            if isinstance(value, list):
                return value
    return []


def _parse_toobit_candle(row: Any, timeframe: str) -> Candle:
    if isinstance(row, dict):
        raw_time = row.get("closeTime") or row.get("time") or row.get("openTime") or row.get("startTime") or row.get("t")
        return Candle(
            timeframe=timeframe,
            close_time=datetime.fromtimestamp(int(str(raw_time)) / 1000, tz=timezone.utc),
            open=Decimal(str(row.get("open") or row.get("o"))),
            high=Decimal(str(row.get("high") or row.get("h"))),
            low=Decimal(str(row.get("low") or row.get("l"))),
            close=Decimal(str(row.get("close") or row.get("c"))),
            volume=Decimal(str(row.get("volume") or row.get("v") or row.get("qty") or "0")),
        )
    if isinstance(row, list) and len(row) >= 5:
        return Candle(
            timeframe=timeframe,
            close_time=datetime.fromtimestamp(int(str(row[0])) / 1000, tz=timezone.utc),
            open=Decimal(str(row[1])),
            high=Decimal(str(row[2])),
            low=Decimal(str(row[3])),
            close=Decimal(str(row[4])),
            volume=Decimal(str(row[5] if len(row) > 5 else "0")),
        )
    raise DailyReviewError(f"unsupported Toobit candle row shape: {type(row).__name__}")


def _daily_index_path(data_root: Path, trading_date: date) -> Path:
    return data_root / "daily" / f"{trading_date.isoformat()}.json"


def _read_daily_index(data_root: Path, trading_date: date) -> dict[str, Any]:
    path = _daily_index_path(data_root, trading_date)
    if not path.exists():
        return {"date": trading_date.isoformat(), "runs": []}
    return _read_json(path)


def _write_daily_index(data_root: Path, trading_date: date, payload: dict[str, Any]) -> None:
    _write_json(_daily_index_path(data_root, trading_date), payload)


def _record_daily_status(data_root: Path, trading_date: date, run_id: str, status: str, next_action: str) -> None:
    payload = _read_daily_index(data_root, trading_date)
    rows = [row for row in payload.get("runs", []) if row.get("run_id") != run_id]
    rows.append({"run_id": run_id, "status": status, "next_action": next_action})
    payload["runs"] = rows
    payload["latest_run_id"] = run_id
    payload["latest_status"] = status
    payload["next_action"] = next_action
    _write_daily_index(data_root, trading_date, payload)


def _has_published_run(data_root: Path, trading_date: date) -> bool:
    return any(row.get("status") == RunStatus.PUBLISHED.value for row in _read_daily_index(data_root, trading_date).get("runs", []))


def _run_dir(data_root: Path, run_id: str) -> Path:
    return data_root / "runs" / run_id


def _group_candles_by_timeframe(candles: list[Candle]) -> dict[str, list[Candle]]:
    result: dict[str, list[Candle]] = {}
    for c in candles:
        result.setdefault(c.timeframe, []).append(c)
    return result


def _load_fills_and_candles(
    trading_date: date,
    fixture: Path | None,
    *,
    data_root: Path,
    run_id: str,
    intervals: list[str] | None = None,
) -> tuple[Path, tuple[Fill, ...], dict[str, list[Candle]], datetime]:
    if fixture is not None:
        payload = _read_json(fixture)
        fills = normalize_fills(payload["fills"])
        event_time = _parse_time(str(payload.get("event_time") or min(fill.filled_at for fill in fills).isoformat()))
        candles = [_parse_candle(row) for row in payload.get("candles", [])]
        return fixture, tuple(fill for fill in fills if _fill_trading_date_kst(fill) == trading_date), _group_candles_by_timeframe(candles), event_time

    fixture_path = PROJECT_ROOT / "data" / "daily-fixtures" / f"{trading_date.isoformat()}.json"
    if fixture_path.exists():
        return _load_fills_and_candles(trading_date, fixture_path, data_root=data_root, run_id=run_id, intervals=intervals)

    raw_path = data_root / "raw" / "toobit" / trading_date.isoformat() / run_id / "toobit_daily_snapshot.json"
    try:
        fetch_toobit_daily_snapshot(trading_date, output_path=raw_path, project_root=PROJECT_ROOT, intervals=intervals)
    except ToobitClientError as exc:
        raise DailyReviewError(str(exc)) from exc
    snapshot = _read_json(raw_path)
    adapted = adapt_raw_toobit_fills(snapshot["fills_raw"])
    fills = tuple(fill for fill in adapted.fills if _fill_trading_date_kst(fill) == trading_date)
    if not fills:
        raise DailyReviewError(f"Toobit snapshot has no fills for {trading_date.isoformat()} KST")

    # 멀티 타임프레임 지원: candles_by_interval 우선, 없으면 candles_raw 하위 호환
    candles_by_tf: dict[str, list[Candle]] = {}
    if "candles_by_interval" in snapshot:
        for tf, tf_data in snapshot["candles_by_interval"].items():
            rows = [_parse_toobit_candle(row, tf) for row in _extract_response_rows(tf_data["response"])]
            if rows:
                candles_by_tf[tf] = rows
    if not candles_by_tf and "candles_raw" in snapshot:
        tf = str(snapshot["candles_raw"].get("interval", "5m"))
        rows = [_parse_toobit_candle(row, tf) for row in _extract_response_rows(snapshot["candles_raw"]["response"])]
        if rows:
            candles_by_tf[tf] = rows

    if not candles_by_tf:
        raise DailyReviewError(f"Toobit snapshot has no candles for {trading_date.isoformat()}")
    event_time = min((fill.filled_at for fill in fills), default=datetime.combine(trading_date, time.min, tzinfo=timezone.utc))
    return raw_path, fills, candles_by_tf, event_time


def _evidence_for(episodes: tuple[Any, ...], features: dict[str, Any]) -> dict[str, Any]:
    evidence = {"ev-features": features}
    for episode in episodes:
        prefix = episode.episode_id
        evidence[f"{prefix}:entry"] = {"episode_id": episode.episode_id, "entry_quantity": episode.entry_quantity, "entry_vwap": episode.entry_vwap}
        evidence[f"{prefix}:exit"] = {"episode_id": episode.episode_id, "exit_quantity": episode.exit_quantity, "exit_vwap": episode.exit_vwap}
        evidence[f"{prefix}:pnl"] = {"episode_id": episode.episode_id, "gross_realized_pnl": episode.gross_realized_pnl, "calculated_net_pnl": episode.calculated_net_pnl}
        evidence[f"{prefix}:fees"] = {"episode_id": episode.episode_id, "fees": episode.fees, "rebates": episode.rebates, "funding": episode.funding}
    return evidence


def _pattern_memory_path(pattern_memory_path: Path | None) -> Path:
    return pattern_memory_path or DEFAULT_PATTERN_MEMORY


def _pattern_key(record: Any) -> tuple[str, str, str]:
    return (record.pattern_id, record.episode_id, record.date)


def _derive_pattern_candidates(episodes_raw: list[dict[str, Any]], trading_date: date) -> list[Any]:
    """Deterministic behavioral candidates from verified episode numbers.

    These are facts about the trade itself (fees, PnL sign, open position) — never
    ICT structural claims (rule 8). They stay CANDIDATE until the user confirms.
    """
    candidates: list[Any] = []
    for ep in episodes_raw:
        eid = str(ep.get("episode_id", ""))
        if not eid:
            continue
        net = Decimal(str(ep.get("calculated_net_pnl", "0")))
        gross = Decimal(str(ep.get("gross_realized_pnl", "0")))
        fees = Decimal(str(ep.get("fees", "0")))
        exit_qty = Decimal(str(ep.get("exit_quantity", "0")))
        closed = ep.get("closed_at")
        if gross > 0 and fees > gross:
            candidates.append(make_candidate("fees-exceeded-gross", episode_id=eid, trading_date=trading_date, evidence_id=f"{eid}:fees"))
        if net < 0:
            candidates.append(make_candidate("net-loss", episode_id=eid, trading_date=trading_date, evidence_id=f"{eid}:pnl"))
        if exit_qty == 0 or closed is None:
            candidates.append(make_candidate("position-left-open", episode_id=eid, trading_date=trading_date, evidence_id=f"{eid}:exit"))
    return candidates


def _merge_pattern_records(existing: Iterable[Any], derived: Iterable[Any]) -> tuple[Any, ...]:
    """Add new CANDIDATEs without duplicating or downgrading existing records (CONFIRMED preserved)."""
    by_key = {_pattern_key(record): record for record in existing}
    for candidate in derived:
        by_key.setdefault(_pattern_key(candidate), candidate)
    return tuple(by_key.values())


def record_pattern_candidates(run_dir: Path, trading_date: date, *, pattern_memory_path: Path | None = None) -> int:
    """After a run publishes, grow the living wiki with new behavioral CANDIDATEs. Returns count added."""
    path = _pattern_memory_path(pattern_memory_path)
    episodes_raw = _read_json(run_dir / "episodes.json")
    existing = load_pattern_memory(path)
    merged = _merge_pattern_records(existing, _derive_pattern_candidates(episodes_raw, trading_date))
    save_pattern_memory(path, merged)
    return len(merged) - len(existing)


def _confirmed_patterns_for_request(pattern_memory_path: Path | None) -> list[dict[str, Any]]:
    records = confirmed_patterns(load_pattern_memory(_pattern_memory_path(pattern_memory_path)))
    return [
        {
            "pattern_id": record.pattern_id,
            "episode_id": record.episode_id,
            "date": record.date,
            "evidence_id": record.evidence_id,
            "user_answer": record.user_answer,
        }
        for record in records
    ]


def confirm_pattern(pattern_id: str, episode_id: str, trading_date: date | str, user_answer: str, *, pattern_memory_path: Path | None = None) -> Any:
    """Promote a single CANDIDATE to CONFIRMED. Only the user runs this."""
    if not user_answer.strip():
        raise DailyReviewError("confirm requires a non-empty --answer")
    path = _pattern_memory_path(pattern_memory_path)
    target = (pattern_id, episode_id, str(_coerce_date(trading_date)))
    found = None
    out: list[Any] = []
    for record in load_pattern_memory(path):
        if found is None and _pattern_key(record) == target and record.status == "CANDIDATE":
            found = confirm_candidate(record, user_answer=user_answer)
            out.append(found)
        else:
            out.append(record)
    if found is None:
        raise DailyReviewError(f"no CANDIDATE pattern matched {target}")
    save_pattern_memory(path, out)
    return found


def list_patterns(*, pattern_memory_path: Path | None = None) -> dict[str, Any]:
    records = load_pattern_memory(_pattern_memory_path(pattern_memory_path))
    return {
        "total": len(records),
        "candidates": [record.to_dict() for record in records if record.status == "CANDIDATE"],
        "confirmed": [record.to_dict() for record in records if record.status == "CONFIRMED"],
    }


def prepare(
    trading_date: date | str,
    *,
    data_root: Path = DEFAULT_DATA_ROOT,
    fixture: Path | None = None,
    run_id: str | None = None,
    pattern_memory_path: Path | None = None,
    intervals: list[str] | None = None,
) -> Path:
    trading_date = _coerce_date(trading_date)
    if _has_published_run(data_root, trading_date):
        raise DailyReviewError(f"PUBLISHED run already exists for {trading_date.isoformat()}")

    actual_run_id = run_id or generate_run_id()
    input_path, fills, candles_by_tf, event_time = _load_fills_and_candles(
        trading_date, fixture, data_root=data_root, run_id=actual_run_id, intervals=intervals
    )
    manifest = create_manifest(actual_run_id, [build_input_file(input_path, "daily_trade_source", time_range_start=trading_date.isoformat(), time_range_end=trading_date.isoformat())])
    manifest_path = write_manifest(manifest, data_root)
    run_dir = manifest_path.parent

    try:
        _write_json(run_dir / "normalized_fills.json", fills)
        manifest = add_outputs(manifest, [build_output_file(run_dir / "normalized_fills.json", "normalized_fills")], RunStatus.RECONCILED)
        rewrite_manifest(manifest_path, manifest)

        episodes = build_trade_episodes(fills)
        _write_json(run_dir / "episodes.json", episodes)
        manifest = add_outputs(manifest, [build_output_file(run_dir / "episodes.json", "trade_episodes")], RunStatus.RECONCILED)
        rewrite_manifest(manifest_path, manifest)

        splits = split_timeframes_asof(candles_by_tf, event_time) if candles_by_tf else {}
        primary_tf = next(iter(splits), None)
        primary_split = splits[primary_tf] if primary_tf else None

        # 타임프레임별 features
        tf_features: dict[str, dict] = {}
        for tf, sp in splits.items():
            tf_features[tf] = {
                "pre_trade_close_count": len(sp.pre_trade),
                "post_trade_close_count": len(sp.post_trade),
                "pre_trade_last_close": str(sp.pre_trade[-1].close) if sp.pre_trade else None,
            }

        features = {
            "date": trading_date.isoformat(),
            "event_time": event_time.isoformat(),
            # 하위 호환: primary 타임프레임 값
            "pre_trade_close_count": 0 if primary_split is None else len(primary_split.pre_trade),
            "post_trade_close_count": 0 if primary_split is None else len(primary_split.post_trade),
            # 멀티 타임프레임 데이터
            "timeframes": tf_features,
        }
        _write_json(run_dir / "features.json", features)
        manifest = add_outputs(manifest, [build_output_file(run_dir / "features.json", "event_time_features")], RunStatus.FEATURED)
        rewrite_manifest(manifest_path, manifest)

        evidence = _evidence_for(episodes, features)
        _write_json(run_dir / "evidence.json", {"ids": sorted(evidence), "items": evidence})
        first = episodes[0] if episodes else None
        review_request = {
            "run_id": actual_run_id,
            "date": trading_date.isoformat(),
            "episode_ids": [episode.episode_id for episode in episodes],
            "required_schema": "schemas/review-draft.schema.json",
            "required_metrics": [] if first is None else [
                {"name": "entry_quantity", "value": str(first.entry_quantity), "evidence_id": f"{first.episode_id}:entry"},
                {"name": "exit_quantity", "value": str(first.exit_quantity), "evidence_id": f"{first.episode_id}:exit"},
                {"name": "gross_realized_pnl", "value": str(first.gross_realized_pnl), "evidence_id": f"{first.episode_id}:pnl"},
                {"name": "calculated_net_pnl", "value": str(first.calculated_net_pnl), "evidence_id": f"{first.episode_id}:pnl"},
                {"name": "fees", "value": str(first.fees), "evidence_id": f"{first.episode_id}:fees"},
            ],
            "evidence_ids": sorted(evidence),
            "confirmed_patterns": _confirmed_patterns_for_request(pattern_memory_path),
            "instructions": [
                "Return only the structured review JSON.",
                "Attach evidence_id or evidence_ids to every numeric claim.",
                "Do not assert trader psychology as fact.",
                "Treat confirmed_patterns as the trader's own confirmed memory; weigh them when reviewing.",
            ],
        }
        _write_json(run_dir / "review_request.json", review_request)
        manifest = add_outputs(manifest, [build_output_file(run_dir / "review_request.json", "llm_review_request"), build_output_file(run_dir / "evidence.json", "evidence")], RunStatus.WAITING_FOR_LLM)
        rewrite_manifest(manifest_path, manifest)
        _record_daily_status(data_root, trading_date, actual_run_id, RunStatus.WAITING_FOR_LLM.value, "Run Hermes daily review skill, then finalize with review JSON.")
        return run_dir
    except Exception as exc:
        failed = mark_failed(manifest, str(exc))
        rewrite_manifest(manifest_path, failed)
        _record_daily_status(data_root, trading_date, actual_run_id, RunStatus.FAILED.value, "Inspect failure.json and rerun prepare after fixing input.")
        _write_json(run_dir / "failure.json", {"reason": str(exc)})
        raise


def finalize(run_id: str, review_json: Path, *, data_root: Path = DEFAULT_DATA_ROOT, pattern_memory_path: Path | None = None) -> Path:
    run_dir = _run_dir(data_root, run_id)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["status"] == RunStatus.PUBLISHED.value:
        return run_dir / "review.md"
    if manifest["status"] != RunStatus.WAITING_FOR_LLM.value:
        raise DailyReviewError(f"run is not waiting for LLM output: {manifest['status']}")

    evidence = _read_json(run_dir / "evidence.json")
    trading_date = _parse_date(str(_read_json(run_dir / "features.json")["date"]))
    try:
        review = require_valid_review_draft(_read_llm_review_json(review_json, run_id=run_id), evidence["ids"])
    except (ReviewValidationError, DailyReviewError) as exc:
        manifest["status"] = RunStatus.INVALID_LLM_OUTPUT.value
        manifest["failure_reason"] = str(exc)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        _record_daily_status(data_root, trading_date, run_id, RunStatus.INVALID_LLM_OUTPUT.value, "Fix review JSON once and rerun finalize.")
        _write_json(run_dir / "invalid_llm_output.json", {"reason": str(exc), "review_json": str(review_json.resolve())})
        raise

    review_path = run_dir / "review.md"
    review_path.write_text(render_markdown(review, evidence["ids"]), encoding="utf-8")
    manifest["status"] = RunStatus.PUBLISHED.value
    manifest["outputs"] = [
        item for item in manifest.get("outputs", [])
        if item.get("data_kind") not in {"llm_review_json", "review_markdown"}
    ]
    manifest["outputs"].append(build_output_file(review_json, "llm_review_json").__dict__)
    manifest["outputs"].append(build_output_file(review_path, "review_markdown").__dict__)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    _record_daily_status(data_root, trading_date, run_id, RunStatus.PUBLISHED.value, "No action required.")
    try:
        record_pattern_candidates(run_dir, trading_date, pattern_memory_path=pattern_memory_path)
    except Exception:
        pass  # pattern-memory growth must never block publishing
    return review_path


def mark_run_status(run_id: str, status_value: str, reason: str, *, data_root: Path = DEFAULT_DATA_ROOT) -> Path:
    run_dir = _run_dir(data_root, run_id)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    status = RunStatus(status_value)
    manifest["status"] = status.value
    manifest["failure_reason"] = reason
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    features_path = run_dir / "features.json"
    if features_path.exists():
        trading_date = _parse_date(str(_read_json(features_path)["date"]))
        if status == RunStatus.MODEL_RATE_LIMIT:
            next_action = "Retry the Hermes stage after model quota recovers."
        elif status == RunStatus.MODEL_EMPTY_RESPONSE:
            next_action = "Inspect Hermes/provider health, then retry the same run."
        elif status == RunStatus.WAITING_FOR_LLM:
            next_action = "Run Hermes for the existing review request."
        else:
            next_action = "Inspect run logs."
        _record_daily_status(data_root, trading_date, run_id, status.value, next_action)
    return manifest_path


def status(trading_date: date | str, *, data_root: Path = DEFAULT_DATA_ROOT) -> dict[str, Any]:
    trading_date = _coerce_date(trading_date)
    payload = _read_daily_index(data_root, trading_date)
    if not payload.get("runs"):
        payload["latest_status"] = "MISSING"
        payload["next_action"] = "Run prepare for this date."
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare, finalize, and inspect Daily Trading Review V2 runs.")
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    sub = parser.add_subparsers(dest="command", required=True)

    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--date", required=True)
    prepare_parser.add_argument("--fixture")
    prepare_parser.add_argument(
        "--intervals",
        default="5m",
        help="쉼표로 구분된 캔들 타임프레임 (예: 5m,15m,1h,4h). 기본값: 5m",
    )

    finalize_parser = sub.add_parser("finalize")
    finalize_parser.add_argument("--run-id", required=True)
    finalize_parser.add_argument("--review-json", required=True)

    status_parser = sub.add_parser("status")
    status_parser.add_argument("--date", required=True)

    normalize_parser = sub.add_parser("normalize-llm-output")
    normalize_parser.add_argument("--run-id", required=True)
    normalize_parser.add_argument("--raw", required=True)
    normalize_parser.add_argument("--output", required=True)

    mark_parser = sub.add_parser("mark-status")
    mark_parser.add_argument("--run-id", required=True)
    mark_parser.add_argument("--status", required=True)
    mark_parser.add_argument("--reason", required=True)

    sub.add_parser("patterns")

    confirm_parser = sub.add_parser("confirm-pattern")
    confirm_parser.add_argument("--pattern-id", required=True)
    confirm_parser.add_argument("--episode-id", required=True)
    confirm_parser.add_argument("--date", required=True)
    confirm_parser.add_argument("--answer", required=True)

    args = parser.parse_args(argv)
    data_root = Path(args.data_root)
    if args.command == "prepare":
        parsed_intervals = [tf.strip() for tf in args.intervals.split(",") if tf.strip()]
        print(prepare(
            _parse_date(args.date),
            data_root=data_root,
            fixture=None if args.fixture is None else Path(args.fixture),
            intervals=parsed_intervals if parsed_intervals != ["5m"] else None,
        ))
    elif args.command == "finalize":
        print(finalize(args.run_id, Path(args.review_json), data_root=data_root))
    elif args.command == "status":
        print(json.dumps(status(_parse_date(args.date), data_root=data_root), ensure_ascii=False, indent=2, sort_keys=True))
    elif args.command == "normalize-llm-output":
        print(normalize_llm_review_file(run_id=args.run_id, raw_path=Path(args.raw), output_path=Path(args.output)))
    elif args.command == "mark-status":
        print(mark_run_status(args.run_id, args.status, args.reason, data_root=data_root))
    elif args.command == "patterns":
        print(json.dumps(list_patterns(), ensure_ascii=False, indent=2, sort_keys=True))
    elif args.command == "confirm-pattern":
        confirmed = confirm_pattern(args.pattern_id, args.episode_id, _parse_date(args.date), args.answer)
        print(json.dumps(confirmed.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
