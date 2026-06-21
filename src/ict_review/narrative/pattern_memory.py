from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = "2.0"
VALID_STATUSES = {"CANDIDATE", "CONFIRMED"}


class PatternMemoryError(ValueError):
    """Raised when pattern memory would violate the human-confirmation contract."""


@dataclass(frozen=True)
class PatternMemoryRecord:
    pattern_id: str
    status: str
    episode_id: str
    date: str
    evidence_id: str
    user_answer: str
    created_at: str
    confirmed_at: str | None = None
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: dict) -> "PatternMemoryRecord":
        record = cls(
            pattern_id=str(data["pattern_id"]),
            status=str(data["status"]),
            episode_id=str(data["episode_id"]),
            date=str(data["date"]),
            evidence_id=str(data["evidence_id"]),
            user_answer=str(data.get("user_answer", "")),
            created_at=str(data["created_at"]),
            confirmed_at=data.get("confirmed_at"),
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
        )
        validate_record(record)
        return record

    def to_dict(self) -> dict:
        return asdict(self)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_record(record: PatternMemoryRecord) -> None:
    if record.status not in VALID_STATUSES:
        raise PatternMemoryError(f"unsupported pattern status: {record.status}")
    if record.status == "CONFIRMED" and not record.user_answer.strip():
        raise PatternMemoryError("CONFIRMED pattern requires user_answer")
    date.fromisoformat(record.date)
    if not record.pattern_id or not record.episode_id or not record.evidence_id:
        raise PatternMemoryError("pattern_id, episode_id, and evidence_id are required")


def make_candidate(pattern_id: str, *, episode_id: str, trading_date: date | str, evidence_id: str) -> PatternMemoryRecord:
    return PatternMemoryRecord(
        pattern_id=pattern_id,
        status="CANDIDATE",
        episode_id=episode_id,
        date=str(trading_date),
        evidence_id=evidence_id,
        user_answer="",
        created_at=utc_now_iso(),
    )


def confirm_candidate(record: PatternMemoryRecord, *, user_answer: str) -> PatternMemoryRecord:
    if record.status != "CANDIDATE":
        raise PatternMemoryError("only CANDIDATE patterns can be confirmed")
    confirmed = PatternMemoryRecord(
        pattern_id=record.pattern_id,
        status="CONFIRMED",
        episode_id=record.episode_id,
        date=record.date,
        evidence_id=record.evidence_id,
        user_answer=user_answer,
        created_at=record.created_at,
        confirmed_at=utc_now_iso(),
        schema_version=record.schema_version,
    )
    validate_record(confirmed)
    return confirmed


def load_pattern_memory(path: Path) -> tuple[PatternMemoryRecord, ...]:
    if not path.exists():
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload if isinstance(payload, list) else payload.get("patterns", [])
    return tuple(PatternMemoryRecord.from_dict(item) for item in rows)


def save_pattern_memory(path: Path, records: Iterable[PatternMemoryRecord]) -> None:
    rows = [record.to_dict() for record in records]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"patterns": rows, "schema_version": SCHEMA_VERSION}, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def confirmed_patterns(records: Iterable[PatternMemoryRecord]) -> tuple[PatternMemoryRecord, ...]:
    return tuple(record for record in records if record.status == "CONFIRMED")
