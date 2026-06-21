from __future__ import annotations

from ict_review.narrative.models import ReviewDraft
from ict_review.validation.evidence_validator import require_valid_review_draft


def render_markdown(data: dict | ReviewDraft, available_evidence_ids: set[str] | tuple[str, ...] | list[str]) -> str:
    draft = require_valid_review_draft(data, available_evidence_ids)
    lines: list[str] = [
        f"# Review Draft {draft.run_id}",
        "",
        "## Episodes",
    ]
    lines.extend(f"- {episode_id}" for episode_id in draft.episode_ids)
    lines.extend(["", "## Metrics", "| Metric | Value | Evidence |", "|---|---:|---|"])
    for metric in sorted(draft.metrics, key=lambda item: item.name):
        lines.append(f"| {metric.name} | {metric.value} | {metric.evidence_id} |")
    lines.extend(["", "## Observations"])
    for observation in draft.observations:
        evidence = ", ".join(observation.evidence_ids)
        lines.append(f"- {observation.text} [{evidence}]")
    lines.extend(["", "## Questions"])
    lines.extend(f"- {question}" for question in draft.questions)
    lines.extend(["", "## Pattern Candidates"])
    lines.extend(f"- {candidate}" for candidate in draft.pattern_candidates)
    lines.extend(["", "## Model Metadata"])
    for key in sorted(draft.model_metadata):
        lines.append(f"- {key}: {draft.model_metadata[key]}")
    lines.append("")
    return "\n".join(lines)
