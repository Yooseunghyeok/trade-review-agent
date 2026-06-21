from datetime import date

import pytest

from ict_review.narrative.pattern_memory import PatternMemoryError, confirm_candidate, confirmed_patterns, make_candidate


def test_pattern_memory_keeps_candidate_separate_from_confirmed():
    candidate = make_candidate("late-exit", episode_id="episode-0001", trading_date=date(2026, 6, 1), evidence_id="ev-pnl")

    assert candidate.status == "CANDIDATE"
    assert confirmed_patterns([candidate]) == ()

    confirmed = confirm_candidate(candidate, user_answer="Yes, this matches my reviewed behavior.")
    assert confirmed.status == "CONFIRMED"
    assert confirmed.user_answer
    assert confirmed_patterns([candidate, confirmed]) == (confirmed,)


def test_confirmed_pattern_requires_user_answer():
    candidate = make_candidate("late-exit", episode_id="episode-0001", trading_date="2026-06-01", evidence_id="ev-pnl")

    with pytest.raises(PatternMemoryError, match="user_answer"):
        confirm_candidate(candidate, user_answer="")
