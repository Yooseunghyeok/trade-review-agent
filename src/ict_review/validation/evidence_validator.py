from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from ict_review.narrative.models import REQUIRED_METRICS, ReviewDraft


class ReviewValidationError(ValueError):
    """Raised when a structured review draft violates the V2 contract."""


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    detail: str


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    issues: tuple[ValidationIssue, ...]


PSYCHOLOGY_ASSERTION_RE = re.compile(
    r"\b(felt|was emotional|was greedy|had fomo|panicked|revenge traded)\b",
    re.IGNORECASE,
)
NUMERIC_CLAIM_RE = re.compile(r"(?<![\w-])[-+]?\d+(?:\.\d+)?(?![\w-])")


def require_valid_review_draft(data: dict[str, Any] | ReviewDraft, available_evidence_ids: Iterable[str]) -> ReviewDraft:
    draft = data if isinstance(data, ReviewDraft) else ReviewDraft.from_dict(data)
    result = validate_review_draft(draft, available_evidence_ids)
    if not result.passed:
        details = "; ".join(f"{issue.code}: {issue.detail}" for issue in result.issues)
        raise ReviewValidationError(details)
    return draft


def validate_review_draft(data: dict[str, Any] | ReviewDraft, available_evidence_ids: Iterable[str]) -> ValidationResult:
    issues: list[ValidationIssue] = []
    try:
        draft = data if isinstance(data, ReviewDraft) else ReviewDraft.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        return ValidationResult(False, (ValidationIssue("INVALID_STRUCTURE", str(exc)),))

    available = set(available_evidence_ids)
    declared = set(draft.evidence_ids)
    if not declared.issubset(available):
        missing = sorted(declared - available)
        issues.append(ValidationIssue("UNKNOWN_DECLARED_EVIDENCE", ",".join(missing)))

    metric_names = {metric.name for metric in draft.metrics}
    exact_metric_values = {str(metric.value) for metric in draft.metrics}
    missing_metrics = sorted(REQUIRED_METRICS - metric_names)
    if missing_metrics:
        issues.append(ValidationIssue("MISSING_REQUIRED_METRIC", ",".join(missing_metrics)))

    for metric in draft.metrics:
        if not metric.evidence_id:
            issues.append(ValidationIssue("MISSING_EVIDENCE", metric.name))
        elif metric.evidence_id not in declared:
            issues.append(ValidationIssue("UNKNOWN_EVIDENCE", f"{metric.name}:{metric.evidence_id}"))

    for observation in draft.observations:
        for evidence_id in observation.evidence_ids:
            if evidence_id not in declared:
                issues.append(ValidationIssue("UNKNOWN_EVIDENCE", f"observation:{evidence_id}"))
        if PSYCHOLOGY_ASSERTION_RE.search(observation.text):
            issues.append(ValidationIssue("UNSUPPORTED_PSYCHOLOGY_ASSERTION", observation.text))
        for number in NUMERIC_CLAIM_RE.findall(observation.text):
            if number not in exact_metric_values:
                issues.append(ValidationIssue("INEXACT_NUMERIC_CLAIM", f"observation:{number}"))

    for question in draft.questions:
        for number in NUMERIC_CLAIM_RE.findall(question):
            if number not in exact_metric_values:
                issues.append(ValidationIssue("INEXACT_NUMERIC_CLAIM", f"question:{number}"))

    return ValidationResult(len(issues) == 0, tuple(issues))
