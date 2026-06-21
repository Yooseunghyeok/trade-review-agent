from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


RUN_ID_RE = re.compile(r"^run_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{12}$")
MANIFEST_SCHEMA_VERSION = "2.0"


class ManifestError(ValueError):
    """Raised when a manifest cannot be created or verified safely."""


class RunStatus(str, Enum):
    CREATED = "CREATED"
    SNAPSHOT_VALIDATED = "SNAPSHOT_VALIDATED"
    RECONCILED = "RECONCILED"
    FEATURED = "FEATURED"
    EVALUATED = "EVALUATED"
    NARRATED = "NARRATED"
    VERIFIED = "VERIFIED"
    WAITING_FOR_LLM = "WAITING_FOR_LLM"
    INVALID_LLM_OUTPUT = "INVALID_LLM_OUTPUT"
    MODEL_EMPTY_RESPONSE = "MODEL_EMPTY_RESPONSE"
    MODEL_RATE_LIMIT = "MODEL_RATE_LIMIT"
    PUBLISHED = "PUBLISHED"
    FAILED_PROXY_START = "FAILED_PROXY_START"
    FAILED = "FAILED"


@dataclass(frozen=True)
class InputFile:
    path: str
    data_kind: str
    sha256: str
    size_bytes: int
    created_at: str
    time_range_start: str | None = None
    time_range_end: str | None = None
    schema_version: str = MANIFEST_SCHEMA_VERSION


@dataclass(frozen=True)
class OutputFile:
    path: str
    data_kind: str
    sha256: str
    size_bytes: int
    created_at: str
    schema_version: str = MANIFEST_SCHEMA_VERSION


@dataclass(frozen=True)
class Manifest:
    run_id: str
    status: RunStatus
    inputs: tuple[InputFile, ...]
    outputs: tuple[OutputFile, ...] = field(default_factory=tuple)
    created_at: str = field(default_factory=lambda: utc_now_iso())
    schema_version: str = MANIFEST_SCHEMA_VERSION
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Manifest":
        return cls(
            run_id=str(data["run_id"]),
            status=RunStatus(str(data["status"])),
            inputs=tuple(InputFile(**item) for item in data.get("inputs", [])),
            outputs=tuple(OutputFile(**item) for item in data.get("outputs", [])),
            created_at=str(data["created_at"]),
            schema_version=str(data.get("schema_version", MANIFEST_SCHEMA_VERSION)),
            failure_reason=data.get("failure_reason"),
        )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def generate_run_id(now: datetime | None = None) -> str:
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return f"run_{timestamp.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:12]}"


def validate_run_id(run_id: str) -> None:
    if not RUN_ID_RE.match(run_id):
        raise ManifestError(f"invalid run_id format: {run_id}")


def sha256_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        raise ManifestError(f"missing input file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_created_at(path: Path) -> str:
    stat = path.stat()
    return datetime.fromtimestamp(stat.st_ctime, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_input_file(
    path: Path,
    data_kind: str,
    *,
    time_range_start: str | None = None,
    time_range_end: str | None = None,
    schema_version: str = MANIFEST_SCHEMA_VERSION,
) -> InputFile:
    if not path.exists() or not path.is_file():
        raise ManifestError(f"missing input file: {path}")
    resolved = path.resolve()
    stat = resolved.stat()
    return InputFile(
        path=str(resolved),
        data_kind=data_kind,
        sha256=sha256_file(resolved),
        size_bytes=stat.st_size,
        created_at=_file_created_at(resolved),
        time_range_start=time_range_start,
        time_range_end=time_range_end,
        schema_version=schema_version,
    )


def build_output_file(path: Path, data_kind: str, *, schema_version: str = MANIFEST_SCHEMA_VERSION) -> OutputFile:
    if not path.exists() or not path.is_file():
        raise ManifestError(f"missing output file: {path}")
    resolved = path.resolve()
    stat = resolved.stat()
    return OutputFile(
        path=str(resolved),
        data_kind=data_kind,
        sha256=sha256_file(resolved),
        size_bytes=stat.st_size,
        created_at=_file_created_at(resolved),
        schema_version=schema_version,
    )


def create_manifest(run_id: str, input_files: Iterable[InputFile], *, status: RunStatus = RunStatus.CREATED) -> Manifest:
    validate_run_id(run_id)
    inputs = tuple(input_files)
    if not inputs:
        raise ManifestError("manifest requires at least one explicit input file")
    return Manifest(run_id=run_id, status=status, inputs=inputs)


def write_manifest(manifest: Manifest, data_root: Path) -> Path:
    validate_run_id(manifest.run_id)
    run_dir = data_root / "runs" / manifest.run_id
    if run_dir.exists():
        raise ManifestError(f"run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return manifest_path


def load_manifest(path: Path) -> Manifest:
    return Manifest.from_dict(json.loads(path.read_text(encoding="utf-8")))


def rewrite_manifest(path: Path, manifest: Manifest) -> None:
    path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def verify_manifest_inputs(manifest: Manifest) -> None:
    for item in manifest.inputs:
        path = Path(item.path)
        actual = sha256_file(path)
        if actual != item.sha256:
            raise ManifestError(f"input hash mismatch for {path}: expected {item.sha256}, got {actual}")


def add_outputs(manifest: Manifest, outputs: Iterable[OutputFile], status: RunStatus) -> Manifest:
    return Manifest(
        run_id=manifest.run_id,
        status=status,
        inputs=manifest.inputs,
        outputs=tuple([*manifest.outputs, *outputs]),
        created_at=manifest.created_at,
        schema_version=manifest.schema_version,
        failure_reason=manifest.failure_reason,
    )


def mark_failed(manifest: Manifest, reason: str) -> Manifest:
    return Manifest(
        run_id=manifest.run_id,
        status=RunStatus.FAILED,
        inputs=manifest.inputs,
        outputs=manifest.outputs,
        created_at=manifest.created_at,
        schema_version=manifest.schema_version,
        failure_reason=reason,
    )


def parse_input_spec(spec: str) -> tuple[Path, str]:
    if "=" not in spec:
        raise ManifestError("input spec must be KIND=PATH")
    kind, raw_path = spec.split("=", 1)
    if not kind or not raw_path:
        raise ManifestError("input spec must be KIND=PATH")
    return Path(raw_path), kind


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create an explicit V2 run manifest from fixture files.")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--run-id")
    parser.add_argument("--input", action="append", required=True, help="Explicit input in KIND=PATH form.")
    args = parser.parse_args(argv)

    run_id = args.run_id or generate_run_id()
    inputs = [build_input_file(path, kind) for path, kind in (parse_input_spec(item) for item in args.input)]
    manifest_path = write_manifest(create_manifest(run_id, inputs), Path(args.data_root))
    print(manifest_path)
    return 0
