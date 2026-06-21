import json
import uuid
from pathlib import Path

from ict_review.cli.review_offline import run_offline


def runtime_dir(name: str) -> Path:
    path = Path("tests") / "fixtures" / "runtime" / f"{name}-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def test_offline_pipeline_writes_run_outputs_and_manifest():
    data_root = runtime_dir("offline-pipeline") / "data"
    fixture = Path("tests") / "fixtures" / "offline_review_fixture.json"
    run_dir = run_offline(fixture, data_root=data_root)

    assert run_dir.parent == data_root / "runs"
    for name in [
        "manifest.json",
        "normalized_fills.json",
        "episodes.json",
        "features.json",
        "review_draft.json",
        "review.md",
        "evidence.json",
    ]:
        assert (run_dir / name).exists()

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "VERIFIED"
    assert {output["data_kind"] for output in manifest["outputs"]} >= {
        "normalized_fills",
        "trade_episodes",
        "event_time_features",
        "structured_review_draft",
        "review_markdown",
    }


def test_offline_pipeline_reproducible_calculations_for_same_fixture():
    fixture = Path("tests") / "fixtures" / "offline_review_fixture.json"
    first = run_offline(fixture, data_root=runtime_dir("offline-repro-a") / "data")
    second = run_offline(fixture, data_root=runtime_dir("offline-repro-b") / "data")

    first_draft = json.loads((first / "review_draft.json").read_text(encoding="utf-8"))
    second_draft = json.loads((second / "review_draft.json").read_text(encoding="utf-8"))

    assert first_draft["metrics"] == second_draft["metrics"]
    assert first_draft["observations"] == second_draft["observations"]
