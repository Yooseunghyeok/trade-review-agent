#!/usr/bin/env python3
"""
Clean generated workspace clutter while keeping recent raw trading evidence.

Default mode is dry-run. Use --apply to delete.
"""

from __future__ import annotations

import argparse
import re
import shutil
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class CleanupItem:
    path: Path
    reason: str


def is_inside_base(path: Path) -> bool:
    try:
        path.resolve().relative_to(BASE_DIR.resolve())
        return True
    except ValueError:
        return False


def size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                pass
    return total


def run_id_from_raw_json(path: Path) -> str | None:
    match = re.search(r"_(\d{8}_\d{6})\.json$", path.name)
    return match.group(1) if match else None


def raw_json_family(path: Path) -> str | None:
    match = re.match(r"(.+)_(\d{8}_\d{6})\.json$", path.name)
    return match.group(1) if match else None


def keep_latest_raw_json_files(raw_dir: Path, retention: int) -> set[Path]:
    families: dict[str, list[Path]] = {}
    for path in raw_dir.glob("*.json"):
        family = raw_json_family(path)
        if family:
            families.setdefault(family, []).append(path)

    keep: set[Path] = set()
    for paths in families.values():
        keep.update(sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)[:retention])
    return keep


def keep_latest_data_raw_runs(data_dir: Path, retention: int) -> set[Path]:
    run_dirs = [p for p in data_dir.rglob("run_*") if p.is_dir()]
    return {
        path
        for path in sorted(run_dirs, key=lambda p: p.stat().st_mtime, reverse=True)[:retention]
    }


def collect_items(raw_retention: int) -> list[CleanupItem]:
    items: list[CleanupItem] = []

    direct_dirs = [
        ".pytest_cache",
        ".pytest-tmp",
        "logs",
        "outputs",
        "_attic",
        "capture",
        "demo",
        "tests/fixtures/runtime",
    ]
    for rel in direct_dirs:
        path = BASE_DIR / rel
        if path.exists():
            items.append(CleanupItem(path, "generated or scratch directory"))

    for pycache in BASE_DIR.rglob("__pycache__"):
        items.append(CleanupItem(pycache, "Python bytecode cache"))
    for pyc in BASE_DIR.rglob("*.pyc"):
        items.append(CleanupItem(pyc, "Python bytecode file"))

    prompts = BASE_DIR / "prompts"
    if prompts.exists():
        for path in prompts.glob("*"):
            items.append(CleanupItem(path, "generated prompt"))

    scratch_files = [
        "demo-script.md",
        "experiment-summary.md",
        "ppt-agent-concept-review.md",
        "presentation-outline.md",
        "scheduler-setup-guide.md",
    ]
    for rel in scratch_files:
        path = BASE_DIR / rel
        if path.exists():
            items.append(CleanupItem(path, "presentation or experiment artifact"))

    reviews = BASE_DIR / "reviews"
    if reviews.exists():
        review_patterns = [
            "_draft-*.md",
            "final-review-20260612-*.md",
            "final-review-20260614-*.md",
            "verified-performance-summary-run20260611_*.md",
            "final-review-20260615-0135.md",
        ]
        for pattern in review_patterns:
            for path in reviews.glob(pattern):
                items.append(CleanupItem(path, "old or broken generated review"))

    raw_dir = BASE_DIR / "trades" / "raw-json"
    if raw_dir.exists():
        keep_files = keep_latest_raw_json_files(raw_dir, raw_retention)
        for path in raw_dir.glob("*.json"):
            if raw_json_family(path) and path not in keep_files:
                items.append(CleanupItem(path, f"older raw JSON file; keeping latest {raw_retention} per data type"))

    data_raw = BASE_DIR / "data" / "raw"
    if data_raw.exists():
        keep_dirs = keep_latest_data_raw_runs(data_raw, raw_retention)
        for run_dir in data_raw.rglob("run_*"):
            if run_dir.is_dir() and run_dir not in keep_dirs:
                items.append(CleanupItem(run_dir, f"older data/raw run; keeping latest {raw_retention} runs"))

    # Deduplicate and avoid nested double-delete noise.
    unique: dict[Path, CleanupItem] = {}
    for item in items:
        resolved = item.path.resolve()
        if not is_inside_base(resolved):
            raise RuntimeError(f"Refusing to clean outside project: {item.path}")
        unique[resolved] = CleanupItem(resolved, item.reason)

    paths = sorted(unique.values(), key=lambda item: len(item.path.parts))
    filtered: list[CleanupItem] = []
    deleted_parents: list[Path] = []
    for item in paths:
        if any(parent in item.path.parents for parent in deleted_parents):
            continue
        filtered.append(item)
        if item.path.is_dir():
            deleted_parents.append(item.path)
    return filtered


def remove_item(path: Path) -> bool:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        return True
    except OSError as exc:
        rel = path.relative_to(BASE_DIR)
        print(f"[cleanup] skip={rel} error={exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="delete the listed paths")
    parser.add_argument("--raw-retention", type=int, default=3, help="number of raw run groups to keep")
    args = parser.parse_args()

    if args.raw_retention < 1:
        raise SystemExit("--raw-retention must be >= 1")

    items = collect_items(args.raw_retention)
    total = sum(size_bytes(item.path) for item in items)
    mode = "APPLY" if args.apply else "DRY_RUN"
    print(f"[cleanup] mode={mode} items={len(items)} bytes={total}")
    for item in items:
        rel = item.path.relative_to(BASE_DIR)
        print(f"{rel} | {size_bytes(item.path)} bytes | {item.reason}")

    if args.apply:
        deleted = 0
        for item in items:
            if remove_item(item.path):
                deleted += 1
        print(f"[cleanup] deleted={deleted} skipped={len(items) - deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
