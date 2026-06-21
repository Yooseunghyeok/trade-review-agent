import pytest

from ict_review.rendering.markdown_renderer import render_markdown
from ict_review.validation.evidence_validator import ReviewValidationError, validate_review_draft


def valid_draft():
    return {
        "run_id": "run_20260601T120000Z_abcdef123456",
        "episode_ids": ["episode-0001"],
        "metrics": [
            {"name": "entry_quantity", "value": "1", "evidence_id": "ev-entry"},
            {"name": "exit_quantity", "value": "1", "evidence_id": "ev-exit"},
            {"name": "gross_realized_pnl", "value": "10", "evidence_id": "ev-pnl"},
            {"name": "calculated_net_pnl", "value": "8", "evidence_id": "ev-pnl"},
            {"name": "fees", "value": "2", "evidence_id": "ev-fee"},
        ],
        "observations": [
            {"text": "The long episode closed with positive calculated net PnL.", "evidence_ids": ["ev-pnl"]}
        ],
        "questions": ["Was the entry rule documented before execution?"],
        "pattern_candidates": ["long-positive-net-after-full-close"],
        "evidence_ids": ["ev-entry", "ev-exit", "ev-pnl", "ev-fee"],
        "model_metadata": {"provider": "offline-fixture", "model": "none"},
        "schema_version": "2.0",
    }


def test_valid_review_draft_passes():
    result = validate_review_draft(valid_draft(), {"ev-entry", "ev-exit", "ev-pnl", "ev-fee"})

    assert result.passed is True
    assert result.issues == ()


def test_unknown_evidence_fails():
    draft = valid_draft()
    draft["metrics"][0]["evidence_id"] = "ev-missing"

    result = validate_review_draft(draft, {"ev-entry", "ev-exit", "ev-pnl", "ev-fee"})

    assert result.passed is False
    assert any(issue.code == "UNKNOWN_EVIDENCE" for issue in result.issues)


def test_missing_required_metric_fails():
    draft = valid_draft()
    draft["metrics"] = [metric for metric in draft["metrics"] if metric["name"] != "fees"]

    result = validate_review_draft(draft, {"ev-entry", "ev-exit", "ev-pnl", "ev-fee"})

    assert result.passed is False
    assert any(issue.code == "MISSING_REQUIRED_METRIC" for issue in result.issues)


def test_psychology_assertion_fails():
    draft = valid_draft()
    draft["observations"] = [{"text": "The trader felt FOMO during entry.", "evidence_ids": ["ev-entry"]}]

    result = validate_review_draft(draft, {"ev-entry", "ev-exit", "ev-pnl", "ev-fee"})

    assert result.passed is False
    assert any(issue.code == "UNSUPPORTED_PSYCHOLOGY_ASSERTION" for issue in result.issues)


def test_rounded_numeric_claim_fails():
    draft = valid_draft()
    draft["observations"] = [{"text": "Calculated net PnL was 8.0.", "evidence_ids": ["ev-pnl"]}]

    result = validate_review_draft(draft, {"ev-entry", "ev-exit", "ev-pnl", "ev-fee"})

    assert result.passed is False
    assert any(issue.code == "INEXACT_NUMERIC_CLAIM" for issue in result.issues)


def test_same_json_produces_same_markdown():
    evidence = {"ev-entry", "ev-exit", "ev-pnl", "ev-fee"}

    assert render_markdown(valid_draft(), evidence) == render_markdown(valid_draft(), evidence)


def test_invalid_json_blocks_markdown_rendering():
    draft = valid_draft()
    draft["metrics"][0]["evidence_id"] = "ev-missing"

    with pytest.raises(ReviewValidationError):
        render_markdown(draft, {"ev-entry", "ev-exit", "ev-pnl", "ev-fee"})
