import json
import uuid
from datetime import date
from pathlib import Path

import pytest

from ict_review.cli.daily_review import (
    DailyReviewError,
    _confirmed_patterns_for_request,
    confirm_pattern,
    list_patterns,
    record_pattern_candidates,
)


def runtime_dir(name: str) -> Path:
    path = Path("tests") / "fixtures" / "runtime" / f"{name}-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def _run_with_episodes() -> tuple[Path, Path]:
    base = runtime_dir("self-improve")
    run_dir = base / "run"
    run_dir.mkdir()
    episodes = [
        {  # profitable, fully closed -> no candidate
            "episode_id": "episode-0001",
            "calculated_net_pnl": "54.49",
            "gross_realized_pnl": "67.57",
            "fees": "13.08",
            "exit_quantity": "0.4972",
            "closed_at": "2026-06-17T00:22:13+00:00",
        },
        {  # net loss + left open -> net-loss + position-left-open candidates
            "episode_id": "episode-0002",
            "calculated_net_pnl": "-6.64",
            "gross_realized_pnl": "0",
            "fees": "6.64",
            "exit_quantity": "0",
            "closed_at": None,
        },
    ]
    (run_dir / "episodes.json").write_text(json.dumps(episodes), encoding="utf-8")
    return run_dir, base / "pattern_memory.json"


def test_review_grows_candidates_only_from_facts():
    run_dir, pm = _run_with_episodes()
    added = record_pattern_candidates(run_dir, date(2026, 6, 17), pattern_memory_path=pm)
    assert added == 2

    memory = list_patterns(pattern_memory_path=pm)
    ids = sorted(c["pattern_id"] for c in memory["candidates"])
    assert ids == ["net-loss", "position-left-open"]
    assert memory["confirmed"] == []
    # profitable closed episode produced nothing
    assert all(c["episode_id"] == "episode-0002" for c in memory["candidates"])


def test_record_is_idempotent():
    run_dir, pm = _run_with_episodes()
    assert record_pattern_candidates(run_dir, date(2026, 6, 17), pattern_memory_path=pm) == 2
    assert record_pattern_candidates(run_dir, date(2026, 6, 17), pattern_memory_path=pm) == 0


def test_confirm_promotes_and_feeds_next_request():
    run_dir, pm = _run_with_episodes()
    record_pattern_candidates(run_dir, date(2026, 6, 17), pattern_memory_path=pm)

    # nothing is confirmed until the user confirms
    assert _confirmed_patterns_for_request(pm) == []

    confirmed = confirm_pattern("net-loss", "episode-0002", "2026-06-17", "맞음. 손절 없이 버팀.", pattern_memory_path=pm)
    assert confirmed.status == "CONFIRMED"
    assert confirmed.user_answer

    fed = _confirmed_patterns_for_request(pm)
    assert len(fed) == 1
    assert fed[0]["pattern_id"] == "net-loss"
    assert fed[0]["user_answer"]

    # confirming does not downgrade or duplicate on a later run
    record_pattern_candidates(run_dir, date(2026, 6, 17), pattern_memory_path=pm)
    assert len(_confirmed_patterns_for_request(pm)) == 1


def test_confirm_requires_answer_and_existing_candidate():
    run_dir, pm = _run_with_episodes()
    record_pattern_candidates(run_dir, date(2026, 6, 17), pattern_memory_path=pm)

    with pytest.raises(DailyReviewError, match="answer"):
        confirm_pattern("net-loss", "episode-0002", "2026-06-17", "   ", pattern_memory_path=pm)

    with pytest.raises(DailyReviewError, match="no CANDIDATE"):
        confirm_pattern("does-not-exist", "episode-0002", "2026-06-17", "x", pattern_memory_path=pm)
