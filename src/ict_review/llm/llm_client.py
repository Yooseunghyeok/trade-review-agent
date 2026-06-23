from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from ict_review.ledger.models import TradeEpisode
from ict_review.narrative.models import ReviewDraft
from ict_review.validation.evidence_validator import require_valid_review_draft


EVIDENCE_IDS = ("ev-entry", "ev-exit", "ev-pnl", "ev-fee", "ev-features")

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]+?)\s*```")


class LlmClientError(RuntimeError):
    """Raised when the LiteLLM proxy call fails or the model returns invalid output."""


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_FENCE_RE.search(text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise LlmClientError(f"cannot extract JSON from model response: {text[:300]!r}")


def _build_prompt(
    run_id: str,
    episode: TradeEpisode,
    features: dict[str, Any],
) -> tuple[str, str]:
    ev = {
        "entry_qty": str(episode.entry_quantity),
        "entry_vwap": str(episode.entry_vwap),
        "exit_qty": str(episode.exit_quantity),
        "exit_vwap": str(episode.exit_vwap) if episode.exit_vwap is not None else "N/A (open)",
        "gross": str(episode.gross_realized_pnl),
        "net": str(episode.calculated_net_pnl),
        "fees": str(episode.fees),
        "rebates": str(episode.rebates),
        "funding": str(episode.funding),
        "pre_count": str(features.get("pre_trade_close_count", 0)),
        "last_close": str(features.get("pre_trade_last_close", "N/A")),
    }

    system_prompt = (
        "You are a read-only trade review assistant. "
        "Generate a ReviewDraft JSON based on Python-calculated trade data.\n\n"
        "STRICT RULES:\n"
        "1. Metric values must exactly match the provided evidence — never round or alter numbers.\n"
        "2. Any observation containing a number must reference the correct evidence_id.\n"
        "3. Do not assert psychological states. Forbidden words: felt, emotional, greedy, FOMO, panic, revenge.\n"
        "4. Return ONLY valid JSON. No markdown fences, no explanation text."
    )

    user_prompt = f"""Generate a ReviewDraft for this trade.

Run ID: {run_id}
Episode: {episode.episode_id} | {episode.symbol} {episode.direction}
Opened: {episode.opened_at.isoformat()}
Closed: {episode.closed_at.isoformat() if episode.closed_at else "open position"}

=== PYTHON-CALCULATED EVIDENCE (ground truth) ===
ev-entry    entry_quantity={ev["entry_qty"]}, entry_vwap={ev["entry_vwap"]}
ev-exit     exit_quantity={ev["exit_qty"]}, exit_vwap={ev["exit_vwap"]}
ev-pnl      gross_realized_pnl={ev["gross"]}, calculated_net_pnl={ev["net"]}
ev-fee      fees={ev["fees"]}, rebates={ev["rebates"]}, funding={ev["funding"]}
ev-features pre_trade_candle_count={ev["pre_count"]}, last_pre_trade_close={ev["last_close"]}

=== REQUIRED JSON (metrics are fixed — write observations and questions) ===
{{
  "run_id": "{run_id}",
  "schema_version": "2.0",
  "episode_ids": ["{episode.episode_id}"],
  "evidence_ids": ["ev-entry", "ev-exit", "ev-pnl", "ev-fee", "ev-features"],
  "metrics": [
    {{"name": "entry_quantity",     "value": "{ev["entry_qty"]}",  "evidence_id": "ev-entry"}},
    {{"name": "exit_quantity",      "value": "{ev["exit_qty"]}",   "evidence_id": "ev-exit"}},
    {{"name": "gross_realized_pnl", "value": "{ev["gross"]}",      "evidence_id": "ev-pnl"}},
    {{"name": "calculated_net_pnl", "value": "{ev["net"]}",        "evidence_id": "ev-pnl"}},
    {{"name": "fees",               "value": "{ev["fees"]}",       "evidence_id": "ev-fee"}}
  ],
  "observations": [
    {{"text": "WRITE_OBSERVATION_HERE", "evidence_ids": ["ev-PICK_ONE"]}},
    {{"text": "WRITE_OBSERVATION_HERE", "evidence_ids": ["ev-PICK_ONE"]}}
  ],
  "questions": ["WRITE_QUESTION_HERE"],
  "pattern_candidates": [],
  "model_metadata": {{"provider": "litellm-proxy", "model": "PLACEHOLDER"}}
}}

Write 2-3 observations grounded in the evidence. Write 1-2 reflective questions for the trader."""

    return system_prompt, user_prompt


def call_llm(
    run_id: str,
    episode: TradeEpisode,
    features: dict[str, Any],
    *,
    base_url: str = "http://127.0.0.1:4000",
    model: str = "vertex-gemini-flash",
    timeout: int = 60,
    max_retries: int = 2,
) -> ReviewDraft:
    """Call the LiteLLM proxy and return a validated ReviewDraft.

    Requires LiteLLM proxy running at base_url (default http://127.0.0.1:4000).
    Set LITELLM_BASE_URL and LITELLM_MODEL env vars to override defaults.
    """
    system_prompt, user_prompt = _build_prompt(run_id, episode, features)

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }).encode("utf-8")

    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    last_error: LlmClientError = LlmClientError("no attempts made")

    for attempt in range(max_retries + 1):
        if attempt > 0:
            time.sleep(5 * attempt)

        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                last_error = LlmClientError("rate limited by LiteLLM proxy (HTTP 429)")
                continue
            raise LlmClientError(f"LiteLLM proxy HTTP {exc.code}: {exc.reason}") from exc
        except OSError as exc:
            raise LlmClientError(
                f"cannot reach LiteLLM proxy at {url} — is it running? ({exc})"
            ) from exc

        content = (
            body.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        if not content:
            last_error = LlmClientError("model returned empty response")
            continue

        raw = _extract_json(content)

        # Overwrite placeholder model name with actual model from response
        actual_model = body.get("model", model)
        if isinstance(raw.get("model_metadata"), dict):
            raw["model_metadata"]["model"] = actual_model

        return require_valid_review_draft(raw, EVIDENCE_IDS)

    raise last_error
