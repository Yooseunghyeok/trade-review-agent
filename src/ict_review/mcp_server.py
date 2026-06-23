from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# 프로젝트 루트를 sys.path에 추가
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from ict_review.features.asof import Candle, split_candles_asof, split_timeframes_asof
from ict_review.ledger.episode_builder import build_trade_episodes
from ict_review.ledger.normalize_fills import normalize_fills
from ict_review.narrative.models import ReviewDraft
from ict_review.narrative.pattern_memory import (
    confirmed_patterns,
    load_pattern_memory,
    make_candidate,
    save_pattern_memory,
)
from ict_review.rendering.markdown_renderer import render_markdown
from ict_review.validation.evidence_validator import validate_review_draft as _validate_draft

mcp = FastMCP(
    "trade-review-agent",
    instructions=(
        "매매 복기 에이전트입니다. 권장 순서: "
        "1) run_offline_review → 2) get_confirmed_patterns → 3) get_trade_episodes → "
        "4) query_knowledge(ICT 개념 필요시) → 5) analyze_ict_checklist → 6) analyze_risk → "
        "7) validate_review_draft → 8) finalize_review → 9) save_pattern_candidate. "
        "수치는 항상 get_trade_episodes에서 반환된 값을 그대로 사용하고 절대 반올림하지 마세요."
    ),
)

_DATA_ROOT = _ROOT / "data"
_MEMORY_PATH = _ROOT / "memory" / "pattern_memory.json"


def _to_serializable(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_to_serializable(i) for i in v]
    if isinstance(v, dict):
        return {k: _to_serializable(val) for k, val in v.items()}
    if hasattr(v, "__dataclass_fields__"):
        return {k: _to_serializable(getattr(v, k)) for k in v.__dataclass_fields__}
    return str(v)


def _dump(obj: Any) -> str:
    return json.dumps(_to_serializable(obj), ensure_ascii=False, indent=2)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_candle(row: dict) -> Candle:
    return Candle(
        timeframe=str(row["timeframe"]),
        close_time=_parse_time(str(row["close_time"])),
        open=Decimal(str(row["open"])),
        high=Decimal(str(row["high"])),
        low=Decimal(str(row["low"])),
        close=Decimal(str(row["close"])),
        volume=Decimal(str(row.get("volume", "0"))),
    )


# ── 도구 1 ──────────────────────────────────────────────────────────────────

@mcp.tool()
def run_offline_review(fixture_path: str = "examples/synthetic_input.json") -> str:
    """
    fixture JSON 파일을 읽어 체결 정규화 → 포지션 에피소드 복원 → PnL 계산 →
    캔들 시점 분리까지 전체 파이프라인을 실행합니다.
    반환값: run_id (이후 도구 호출에 사용)
    사용 시점: 복기를 시작할 때 가장 먼저 호출하세요.
    """
    from ict_review.ingestion.manifest import (
        RunStatus, add_outputs, build_input_file, build_output_file,
        create_manifest, generate_run_id, rewrite_manifest, write_manifest,
    )

    fixture = json.loads((_ROOT / fixture_path).read_text(encoding="utf-8"))
    run_id = generate_run_id()
    manifest = create_manifest(run_id, [build_input_file(_ROOT / fixture_path, "offline_fixture")])
    manifest_path = write_manifest(manifest, _DATA_ROOT)
    run_dir = manifest_path.parent

    fills = normalize_fills(fixture["fills"])
    episodes = build_trade_episodes(fills)

    event_time = _parse_time(str(fixture["event_time"]))
    post_until = _parse_time(str(fixture["post_until"])) if fixture.get("post_until") else None
    all_candles = [_parse_candle(r) for r in fixture["candles"]]

    # 타임프레임별로 그룹화 후 멀티 타임프레임 분리
    candles_by_tf: dict[str, list[Candle]] = {}
    for c in all_candles:
        candles_by_tf.setdefault(c.timeframe, []).append(c)

    splits = split_timeframes_asof(candles_by_tf, event_time, post_until=post_until)
    primary_tf = "5m" if "5m" in splits else (next(iter(splits), None))
    primary_split = splits[primary_tf] if primary_tf else None

    tf_features: dict[str, dict] = {
        tf: {
            "pre_trade_close_count": len(sp.pre_trade),
            "post_trade_close_count": len(sp.post_trade),
            "pre_trade_last_close": str(sp.pre_trade[-1].close) if sp.pre_trade else None,
        }
        for tf, sp in splits.items()
    }
    features = {
        "event_time": event_time.isoformat(),
        "pre_trade_close_count": len(primary_split.pre_trade) if primary_split else 0,
        "post_trade_close_count": len(primary_split.post_trade) if primary_split else 0,
        "pre_trade_last_close": str(primary_split.pre_trade[-1].close) if primary_split and primary_split.pre_trade else None,
        "timeframes": tf_features,
    }

    (run_dir / "episodes.json").write_text(_dump([e for e in episodes]), encoding="utf-8")
    (run_dir / "features.json").write_text(_dump(features), encoding="utf-8")

    summary = {
        "run_id": run_id,
        "episode_count": len(episodes),
        "episodes": [
            {
                "episode_id": ep.episode_id,
                "symbol": ep.symbol,
                "direction": ep.direction,
                "entry_quantity": str(ep.entry_quantity),
                "entry_vwap": str(ep.entry_vwap),
                "exit_quantity": str(ep.exit_quantity),
                "exit_vwap": str(ep.exit_vwap) if ep.exit_vwap else None,
                "gross_realized_pnl": str(ep.gross_realized_pnl),
                "calculated_net_pnl": str(ep.calculated_net_pnl),
                "fees": str(ep.fees),
            }
            for ep in episodes
        ],
        "features": {k: str(v) if isinstance(v, Decimal) else v for k, v in features.items()},
        "evidence_ids": ["ev-entry", "ev-exit", "ev-pnl", "ev-fee", "ev-features"],
        "next_step": f"get_trade_episodes('{run_id}') 로 상세 확인 후 복기 초안을 작성하세요.",
    }
    return _dump(summary)


# ── 도구 2 ──────────────────────────────────────────────────────────────────

@mcp.tool()
def get_trade_episodes(run_id: str) -> str:
    """
    run_id에 해당하는 포지션 에피소드와 PnL 계산값을 반환합니다.
    반환된 수치가 복기 초안의 ground truth입니다. 이 값을 그대로 사용하세요.
    사용 시점: run_offline_review 실행 후 복기 초안 작성 전에 호출하세요.
    """
    run_dir = _DATA_ROOT / "runs" / run_id
    episodes_path = run_dir / "episodes.json"
    features_path = run_dir / "features.json"

    if not episodes_path.exists():
        return json.dumps({"error": f"run_id '{run_id}' 를 찾을 수 없습니다. run_offline_review를 먼저 실행하세요."})

    episodes = json.loads(episodes_path.read_text(encoding="utf-8"))
    features = json.loads(features_path.read_text(encoding="utf-8")) if features_path.exists() else {}

    return _dump({
        "run_id": run_id,
        "episodes": episodes,
        "features": features,
        "evidence_ids": ["ev-entry", "ev-exit", "ev-pnl", "ev-fee", "ev-features"],
        "rules": [
            "metrics 수치는 이 응답의 값을 verbatim 복사하세요 (반올림 금지).",
            "모든 수치 주장에는 evidence_id를 달아야 합니다.",
            "심리 추측 서술 금지: felt, FOMO, panic, greedy, revenge.",
        ],
    })


# ── 도구 3 ──────────────────────────────────────────────────────────────────

@mcp.tool()
def validate_review_draft(run_id: str, draft_json: str) -> str:
    """
    작성한 복기 초안 JSON을 검증합니다.
    passed=true이면 finalize_review를 호출하세요.
    passed=false이면 issues를 읽고 수정 후 다시 검증하세요.
    사용 시점: 복기 초안 작성 완료 후 저장 전에 반드시 호출하세요.
    """
    evidence_ids = ["ev-entry", "ev-exit", "ev-pnl", "ev-fee", "ev-features"]
    try:
        raw = json.loads(draft_json)
    except json.JSONDecodeError as exc:
        return _dump({"passed": False, "issues": [{"code": "INVALID_JSON", "detail": str(exc)}]})

    result = _validate_draft(raw, evidence_ids)
    return _dump({
        "passed": result.passed,
        "issues": [{"code": i.code, "detail": i.detail} for i in result.issues],
        "next_step": "finalize_review를 호출하세요." if result.passed else "issues를 수정하고 다시 validate_review_draft를 호출하세요.",
    })


# ── 도구 4 ──────────────────────────────────────────────────────────────────

@mcp.tool()
def finalize_review(run_id: str, draft_json: str) -> str:
    """
    검증된 복기 초안을 저장하고 Markdown 리포트를 생성합니다.
    반드시 validate_review_draft가 passed=true인 초안만 전달하세요.
    사용 시점: validate_review_draft 통과 후 최종 저장 시 호출하세요.
    """
    evidence_ids = ["ev-entry", "ev-exit", "ev-pnl", "ev-fee", "ev-features"]
    run_dir = _DATA_ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        raw = json.loads(draft_json)
    except json.JSONDecodeError as exc:
        return _dump({"ok": False, "error": f"JSON 파싱 실패: {exc}"})

    try:
        markdown = render_markdown(raw, evidence_ids)
    except Exception as exc:
        return _dump({"ok": False, "error": f"검증 실패: {exc}"})

    draft_path = run_dir / "review_draft.json"
    md_path = run_dir / "review.md"
    draft_path.write_text(draft_json, encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")

    return _dump({
        "ok": True,
        "run_id": run_id,
        "saved": {"draft": str(draft_path), "markdown": str(md_path)},
        "preview": markdown[:500],
        "next_step": "get_confirmed_patterns()로 패턴 메모리를 확인하고 save_pattern_candidate로 후보를 저장하세요.",
    })


# ── 도구 5 ──────────────────────────────────────────────────────────────────

@mcp.tool()
def get_confirmed_patterns() -> str:
    """
    사용자가 확인한 확정 매매 패턴 목록을 반환합니다.
    복기 초안 작성 전에 확인하면 과거 패턴을 복기에 반영할 수 있습니다.
    사용 시점: run_offline_review 직후, 복기 초안 작성 전에 호출하세요.
    """
    records = load_pattern_memory(_MEMORY_PATH)
    confirmed = confirmed_patterns(records)
    candidates = [r for r in records if r.status == "CANDIDATE"]

    return _dump({
        "confirmed_count": len(confirmed),
        "candidate_count": len(candidates),
        "confirmed": [
            {
                "pattern_id": r.pattern_id,
                "episode_id": r.episode_id,
                "date": r.date,
                "user_answer": r.user_answer,
                "confirmed_at": r.confirmed_at,
            }
            for r in confirmed
        ],
        "candidates": [
            {
                "pattern_id": r.pattern_id,
                "episode_id": r.episode_id,
                "date": r.date,
                "created_at": r.created_at,
            }
            for r in candidates
        ],
        "note": "confirmed 패턴만 복기에 사실로 반영하세요. candidate는 아직 사용자 확인 전입니다.",
    })


# ── 도구 6 ──────────────────────────────────────────────────────────────────

@mcp.tool()
def save_pattern_candidate(
    pattern_id: str,
    episode_id: str,
    evidence_id: str,
    trading_date: str,
) -> str:
    """
    복기에서 발견한 매매 패턴을 후보로 저장합니다.
    저장된 후보는 사용자가 직접 확인(confirm)해야 다음 복기에 반영됩니다.
    사용 시점: finalize_review 후 주목할 패턴이 있을 때 호출하세요.

    Args:
        pattern_id: 패턴 이름 (예: "fvg-entry-long", "ob-rejection-short")
        episode_id: 관련 에피소드 ID (예: "episode-0001")
        evidence_id: 근거 evidence ID (예: "ev-pnl")
        trading_date: 매매 날짜 YYYY-MM-DD
    """
    records = list(load_pattern_memory(_MEMORY_PATH))
    existing_ids = {r.pattern_id for r in records}
    if pattern_id in existing_ids:
        return _dump({"ok": False, "reason": f"'{pattern_id}' 는 이미 존재합니다."})

    candidate = make_candidate(
        pattern_id,
        episode_id=episode_id,
        trading_date=trading_date,
        evidence_id=evidence_id,
    )
    records.append(candidate)
    save_pattern_memory(_MEMORY_PATH, records)

    return _dump({
        "ok": True,
        "saved": candidate.to_dict(),
        "note": "CANDIDATE 상태입니다. 사용자가 직접 confirm해야 다음 복기에 반영됩니다.",
    })


# ── 도구 7 ──────────────────────────────────────────────────────────────────

_KNOWLEDGE_DIR = _ROOT / "docs" / "knowledge"


@mcp.tool()
def query_knowledge(topic: str) -> str:
    """
    ICT 매매 개념 문서에서 topic과 관련된 내용을 검색합니다.
    FVG, Order Block, MSS, Liquidity Sweep, Premium/Discount 등 ICT 개념을 복기에 활용하세요.
    사용 시점: analyze_ict_checklist 전에 관련 개념을 확인할 때 호출하세요.

    Args:
        topic: 검색할 키워드 (예: "FVG", "Order Block", "진입 체크리스트")
    """
    if not _KNOWLEDGE_DIR.exists():
        return _dump({"error": "docs/knowledge 디렉터리가 없습니다."})

    keywords = [kw.strip().lower() for kw in topic.replace(",", " ").split() if kw.strip()]
    results: list[dict] = []

    for md_file in sorted(_KNOWLEDGE_DIR.glob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        matched_sections: list[str] = []
        current_section = ""
        for line in content.splitlines():
            if line.startswith("#"):
                if current_section and any(kw in current_section.lower() for kw in keywords):
                    matched_sections.append(current_section.strip())
                current_section = line + "\n"
            else:
                current_section += line + "\n"
        if current_section and any(kw in current_section.lower() for kw in keywords):
            matched_sections.append(current_section.strip())

        if matched_sections:
            results.append({"file": md_file.name, "sections": matched_sections})

    if not results:
        all_files = [f.name for f in sorted(_KNOWLEDGE_DIR.glob("*.md"))]
        return _dump({
            "found": False,
            "topic": topic,
            "hint": f"'{topic}'에 해당하는 섹션을 찾지 못했습니다. 전체 파일: {all_files}",
        })

    return _dump({"found": True, "topic": topic, "results": results})


# ── 도구 8 ──────────────────────────────────────────────────────────────────

@mcp.tool()
def analyze_ict_checklist(run_id: str) -> str:
    """
    에피소드 데이터를 바탕으로 ICT 진입 체크리스트 항목별 확인 질문을 생성합니다.
    수치 계산은 Python이 하고, LLM은 이 질문들을 복기 관찰에 반영하세요.
    사용 시점: get_trade_episodes 조회 후, 복기 초안 작성 전에 호출하세요.
    """
    run_dir = _DATA_ROOT / "runs" / run_id
    episodes_path = run_dir / "episodes.json"
    features_path = run_dir / "features.json"

    if not episodes_path.exists():
        return _dump({"error": f"run_id '{run_id}' 를 찾을 수 없습니다."})

    episodes = json.loads(episodes_path.read_text(encoding="utf-8"))
    features = json.loads(features_path.read_text(encoding="utf-8")) if features_path.exists() else {}

    # 멀티 타임프레임 features 추출
    tf_features: dict[str, dict] = features.get("timeframes", {})
    # primary: 5m 우선, 없으면 첫 번째 타임프레임
    primary_tf = "5m" if "5m" in tf_features else (next(iter(tf_features), None))
    primary_features = tf_features.get(primary_tf, {}) if primary_tf else {}

    checklist_items = []
    for ep in episodes:
        direction = ep.get("direction", "UNKNOWN")
        entry_vwap = Decimal(str(ep.get("entry_vwap", "0")))
        exit_vwap_raw = ep.get("exit_vwap")
        exit_vwap = Decimal(str(exit_vwap_raw)) if exit_vwap_raw else None
        # 멀티 타임프레임 있으면 primary에서, 없으면 features 루트에서 legacy 호환
        pre_close_raw = primary_features.get("pre_trade_last_close") or features.get("pre_trade_last_close")
        pre_close = Decimal(str(pre_close_raw)) if pre_close_raw else None

        premium_discount = None
        if pre_close and pre_close != 0:
            if direction == "LONG":
                premium_discount = "DISCOUNT" if entry_vwap < pre_close else "PREMIUM"
            elif direction == "SHORT":
                premium_discount = "PREMIUM" if entry_vwap > pre_close else "DISCOUNT"

        # 상위 타임프레임 컨텍스트 (있을 경우)
        htf_context: dict[str, str] = {}
        for tf in ("1h", "4h", "1d"):
            tf_data = tf_features.get(tf, {})
            if tf_data.get("pre_trade_last_close"):
                htf_context[tf] = f"직전 종가: {tf_data['pre_trade_last_close']} (캔들 {tf_data.get('pre_trade_close_count', '?')}개)"

        checklist_items.append({
            "episode_id": ep.get("episode_id"),
            "direction": direction,
            "entry_vwap": str(entry_vwap),
            "pre_trade_last_close": str(pre_close) if pre_close else None,
            "entry_zone": premium_discount,
            "higher_timeframe_context": htf_context,
            "checklist_questions": [
                f"[1] 상위 타임프레임 바이어스가 {direction} 방향과 일치했는가?",
                "[2] 진입 전 유동성 스윕(Liquidity Sweep)이 발생했는가?",
                "[3] 스윕 이후 Displacement(강한 이동)가 있었는가?",
                "[4] Market Structure Shift(MSS)가 확인됐는가?",
                f"[5] 진입 가격({entry_vwap})이 FVG 또는 Order Block 내에 있었는가?",
                f"[6] 진입 구간이 {'Discount(저평가) 구간' if premium_discount == 'DISCOUNT' else 'Premium(고평가) 구간' if premium_discount == 'PREMIUM' else '불명 (pre-trade 데이터 부족)'}이었는가?",
                "[7] 손절 위치가 사전에 정해졌는가? (미정이면 진입 근거 약함)",
                "[8] 익절 목표가 반대편 유동성 또는 구조적 레벨에 설정됐는가?",
                "[9] R:R이 최소 1:2 이상이었는가?",
                "[10] 추가 컨플루언스(OB, 세션 오픈, HTF 레벨 등)가 있었는가?",
            ],
            "note": (
                f"진입 구간이 {premium_discount}로 판단됩니다. "
                f"{'LONG은 Discount 구간 진입이 ICT 원칙에 부합합니다.' if direction == 'LONG' and premium_discount == 'DISCOUNT' else ''}"
                f"{'SHORT은 Premium 구간 진입이 ICT 원칙에 부합합니다.' if direction == 'SHORT' and premium_discount == 'PREMIUM' else ''}"
                f"{'ICT 원칙과 반대 구간 진입입니다. 추가 근거가 필요합니다.' if (direction == 'LONG' and premium_discount == 'PREMIUM') or (direction == 'SHORT' and premium_discount == 'DISCOUNT') else ''}"
            ) if premium_discount else "pre-trade 캔들 데이터가 부족해 Premium/Discount 판단 불가",
        })

    return _dump({
        "run_id": run_id,
        "analyst": "ict-technical",
        "checklist": checklist_items,
        "reference": "docs/knowledge/entry-checklist.md",
    })


# ── 도구 9 ──────────────────────────────────────────────────────────────────

@mcp.tool()
def analyze_risk(run_id: str) -> str:
    """
    에피소드의 수수료 비율, 손익 구조, 리스크 지표를 분석합니다.
    수치 계산은 Python이 하고, LLM은 이 분석을 복기 관찰에 반영하세요.
    사용 시점: get_trade_episodes 조회 후, 복기 초안 작성 전에 호출하세요.
    """
    run_dir = _DATA_ROOT / "runs" / run_id
    episodes_path = run_dir / "episodes.json"

    if not episodes_path.exists():
        return _dump({"error": f"run_id '{run_id}' 를 찾을 수 없습니다."})

    episodes = json.loads(episodes_path.read_text(encoding="utf-8"))
    analyses = []

    for ep in episodes:
        gross = Decimal(str(ep.get("gross_realized_pnl", "0")))
        net = Decimal(str(ep.get("calculated_net_pnl", "0")))
        fees = Decimal(str(ep.get("fees", "0")))
        entry_vwap = Decimal(str(ep.get("entry_vwap", "0")))
        exit_vwap_raw = ep.get("exit_vwap")
        exit_vwap = Decimal(str(exit_vwap_raw)) if exit_vwap_raw else None
        qty = Decimal(str(ep.get("entry_quantity", "0")))
        direction = ep.get("direction", "UNKNOWN")

        fee_ratio = (fees / abs(gross) * 100) if gross != 0 else None
        is_winner = net > 0
        price_move = None
        if exit_vwap and entry_vwap and entry_vwap != 0:
            if direction == "LONG":
                price_move = exit_vwap - entry_vwap
            elif direction == "SHORT":
                price_move = entry_vwap - exit_vwap

        flags = []
        if fee_ratio and fee_ratio > Decimal("30"):
            flags.append(f"수수료가 gross PnL의 {fee_ratio:.1f}%입니다. 수수료 비중이 높습니다.")
        if not is_winner and gross > 0:
            flags.append("gross PnL은 양수지만 수수료로 인해 net PnL이 음수입니다.")
        if price_move is not None and price_move < 0:
            flags.append(f"가격이 {direction} 방향과 반대로 움직였습니다 ({price_move:+}).")

        analyses.append({
            "episode_id": ep.get("episode_id"),
            "direction": direction,
            "result": "WIN" if is_winner else "LOSS",
            "gross_pnl": str(gross),
            "net_pnl": str(net),
            "fees": str(fees),
            "fee_ratio_pct": f"{fee_ratio:.2f}" if fee_ratio is not None else None,
            "price_move": str(price_move) if price_move is not None else None,
            "flags": flags,
            "missing_data": [
                k for k, v in [
                    ("stop_loss", None),
                    ("take_profit", None),
                    ("planned_rr", None),
                ] if v is None
            ],
            "note": "손절/익절/계획 R:R 데이터가 fixture에 없습니다. 복기 질문으로 추가하세요.",
        })

    return _dump({
        "run_id": run_id,
        "analyst": "risk",
        "analyses": analyses,
        "evidence_ids": ["ev-pnl", "ev-fee"],
    })


if __name__ == "__main__":
    mcp.run()
