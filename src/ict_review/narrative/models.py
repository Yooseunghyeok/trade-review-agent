from __future__ import annotations

from dataclasses import dataclass
from typing import Any


REQUIRED_METRICS = {"entry_quantity", "exit_quantity", "gross_realized_pnl", "calculated_net_pnl", "fees"}
SCHEMA_VERSION = "2.0"


@dataclass(frozen=True)
class ReviewMetric:
    name: str
    value: Any
    evidence_id: str


@dataclass(frozen=True)
class Observation:
    text: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReviewDraft:
    run_id: str
    episode_ids: tuple[str, ...]
    metrics: tuple[ReviewMetric, ...]
    observations: tuple[Observation, ...]
    questions: tuple[str, ...]
    pattern_candidates: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    model_metadata: dict[str, Any]
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewDraft":
        return cls(
            run_id=str(data["run_id"]),
            episode_ids=tuple(str(item) for item in data["episode_ids"]),
            metrics=tuple(ReviewMetric(name=str(item["name"]), value=item["value"], evidence_id=str(item["evidence_id"])) for item in data["metrics"]),
            observations=tuple(
                Observation(text=str(item["text"]), evidence_ids=tuple(str(eid) for eid in item["evidence_ids"]))
                for item in data["observations"]
            ),
            questions=tuple(str(item) for item in data["questions"]),
            pattern_candidates=tuple(str(item) for item in data["pattern_candidates"]),
            evidence_ids=tuple(str(item) for item in data["evidence_ids"]),
            model_metadata=dict(data["model_metadata"]),
            schema_version=str(data["schema_version"]),
        )
