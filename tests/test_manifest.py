import json
import re
import uuid
from pathlib import Path

import pytest

from ict_review.ingestion.manifest import (
    RUN_ID_RE,
    ManifestError,
    build_input_file,
    create_manifest,
    generate_run_id,
    load_manifest,
    sha256_file,
    verify_manifest_inputs,
    write_manifest,
)


def workspace_case_dir(name: str) -> Path:
    path = Path("tests") / "fixtures" / "runtime" / f"{name}-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def test_run_id_format():
    assert re.match(RUN_ID_RE, generate_run_id())


def test_manifest_serialization_round_trip():
    case_dir = workspace_case_dir("manifest-round-trip")
    source = case_dir / "fills.json"
    source.write_text('{"fills": []}', encoding="utf-8")
    manifest = create_manifest(generate_run_id(), [build_input_file(source, "fills")])
    path = write_manifest(manifest, case_dir / "data")

    loaded = load_manifest(path)

    assert loaded == manifest
    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == manifest.run_id


def test_sha256_matches_file_content():
    case_dir = workspace_case_dir("sha256")
    source = case_dir / "candles.json"
    source.write_text("abc", encoding="utf-8")

    assert sha256_file(source) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_missing_input_fails():
    case_dir = workspace_case_dir("missing-input")
    with pytest.raises(ManifestError, match="missing input file"):
        build_input_file(case_dir / "missing.json", "fills")


def test_existing_run_is_not_overwritten():
    case_dir = workspace_case_dir("overwrite")
    source = case_dir / "fills.json"
    source.write_text("[]", encoding="utf-8")
    run_id = generate_run_id()
    manifest = create_manifest(run_id, [build_input_file(source, "fills")])
    write_manifest(manifest, case_dir / "data")

    with pytest.raises(ManifestError, match="already exists"):
        write_manifest(manifest, case_dir / "data")


def test_manifest_detects_hash_changes():
    case_dir = workspace_case_dir("hash-change")
    source = case_dir / "fills.json"
    source.write_text("[]", encoding="utf-8")
    manifest = create_manifest(generate_run_id(), [build_input_file(source, "fills")])
    source.write_text("[1]", encoding="utf-8")

    with pytest.raises(ManifestError, match="hash mismatch"):
        verify_manifest_inputs(manifest)


def test_runs_do_not_mix_files():
    case_dir = workspace_case_dir("run-isolation")
    first = case_dir / "run-a-fills.json"
    second = case_dir / "run-b-fills.json"
    first.write_text('[{"id":"a"}]', encoding="utf-8")
    second.write_text('[{"id":"b"}]', encoding="utf-8")

    manifest_a = create_manifest(generate_run_id(), [build_input_file(first, "fills")])
    manifest_b = create_manifest(generate_run_id(), [build_input_file(second, "fills")])

    assert manifest_a.inputs[0].path != manifest_b.inputs[0].path
    assert manifest_a.inputs[0].sha256 != manifest_b.inputs[0].sha256
