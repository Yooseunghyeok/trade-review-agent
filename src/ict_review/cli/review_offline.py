from __future__ import annotations

import argparse
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ict_review.features.asof import Candle, split_candles_asof
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
from ict_review.ledger.episode_builder import build_trade_episodes
from ict_review.ledger.normalize_fills import normalize_fills
from ict_review.rendering.markdown_renderer import render_markdown


def _json_default(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return {
            key: _json_default(getattr(value, key))
            for key in value.__dataclass_fields__
        }
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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


def _episode_to_evidence(episode: Any) -> dict[str, Any]:
    return {
        "episode_id": episode.episode_id,
        "entry_quantity": episode.entry_quantity,
        "exit_quantity": episode.exit_quantity,
        "gross_realized_pnl": episode.gross_realized_pnl,
        "calculated_net_pnl": episode.calculated_net_pnl,
        "fees": episode.fees,
    }


def run_offline(fixture_path: Path, *, data_root: Path = Path("data"), run_id: str | None = None) -> Path:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    actual_run_id = run_id or generate_run_id()
    manifest = create_manifest(actual_run_id, [build_input_file(fixture_path, "offline_fixture")])
    manifest_path = write_manifest(manifest, data_root)
    run_dir = manifest_path.parent

    try:
        fills = normalize_fills(fixture["fills"])
        normalized_path = run_dir / "normalized_fills.json"
        _write_json(normalized_path, fills)
        manifest = add_outputs(manifest, [build_output_file(normalized_path, "normalized_fills")], RunStatus.RECONCILED)
        rewrite_manifest(manifest_path, manifest)

        episodes = build_trade_episodes(fills)
        episodes_path = run_dir / "episodes.json"
        _write_json(episodes_path, episodes)
        manifest = add_outputs(manifest, [build_output_file(episodes_path, "trade_episodes")], RunStatus.RECONCILED)
        rewrite_manifest(manifest_path, manifest)

        event_time = _parse_time(str(fixture["event_time"]))
        post_until = _parse_time(str(fixture["post_until"])) if fixture.get("post_until") else None
        candles = [_parse_candle(row) for row in fixture["candles"]]
        split = split_candles_asof(candles, event_time, post_until=post_until)
        features = {
            "event_time": event_time.isoformat(),
            "pre_trade_close_count": len(split.pre_trade),
            "post_trade_close_count": len(split.post_trade),
            "pre_trade_last_close": None if not split.pre_trade else split.pre_trade[-1].close,
        }
        features_path = run_dir / "features.json"
        _write_json(features_path, features)
        manifest = add_outputs(manifest, [build_output_file(features_path, "event_time_features")], RunStatus.FEATURED)
        rewrite_manifest(manifest_path, manifest)

        first_episode = episodes[0]
        evidence_ids = ["ev-entry", "ev-exit", "ev-pnl", "ev-fee", "ev-features"]
        review_draft = {
            "run_id": actual_run_id,
            "episode_ids": [episode.episode_id for episode in episodes],
            "metrics": [
                {"name": "entry_quantity", "value": str(first_episode.entry_quantity), "evidence_id": "ev-entry"},
                {"name": "exit_quantity", "value": str(first_episode.exit_quantity), "evidence_id": "ev-exit"},
                {"name": "gross_realized_pnl", "value": str(first_episode.gross_realized_pnl), "evidence_id": "ev-pnl"},
                {"name": "calculated_net_pnl", "value": str(first_episode.calculated_net_pnl), "evidence_id": "ev-pnl"},
                {"name": "fees", "value": str(first_episode.fees), "evidence_id": "ev-fee"},
            ],
            "observations": [
                {
                    "text": f"Pre-trade context used {len(split.pre_trade)} candle closes.",
                    "evidence_ids": ["ev-features"],
                }
            ],
            "questions": ["Was the setup rule documented before entry?"],
            "pattern_candidates": ["offline-fixture-single-episode"],
            "evidence_ids": evidence_ids,
            "model_metadata": {"provider": "offline-fixture", "model": "deterministic"},
            "schema_version": "2.0",
        }
        draft_path = run_dir / "review_draft.json"
        _write_json(draft_path, review_draft)
        manifest = add_outputs(manifest, [build_output_file(draft_path, "structured_review_draft")], RunStatus.NARRATED)
        rewrite_manifest(manifest_path, manifest)

        markdown = render_markdown(review_draft, set(evidence_ids))
        markdown_path = run_dir / "review.md"
        markdown_path.write_text(markdown, encoding="utf-8")
        evidence_path = run_dir / "evidence.json"
        _write_json(evidence_path, {"ids": evidence_ids, "episode": _episode_to_evidence(first_episode), "features": features})
        manifest = add_outputs(
            manifest,
            [build_output_file(markdown_path, "review_markdown"), build_output_file(evidence_path, "evidence")],
            RunStatus.VERIFIED,
        )
        rewrite_manifest(manifest_path, manifest)
        return run_dir
    except (KeyError, ValueError, TypeError) as exc:
        failed = mark_failed(manifest, str(exc))
        rewrite_manifest(manifest_path, failed)
        failure_path = run_dir / "failure.json"
        _write_json(failure_path, {"reason": str(exc)})
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the V2 offline review pipeline from a local fixture.")
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)

    run_dir = run_offline(Path(args.fixture), data_root=Path(args.data_root), run_id=args.run_id)
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
