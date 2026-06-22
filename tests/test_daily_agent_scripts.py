import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest


PROJECT_ROOT = Path.cwd()
requires_powershell = pytest.mark.skipif(
    shutil.which("powershell") is None,
    reason="requires PowerShell (Windows only)",
)


def run_powershell(args, *, env=None):
    return subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", *args], cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=60, env=env)


@requires_powershell
def test_bootstrap_dry_run_does_not_print_secrets():
    result = run_powershell([
        "-File",
        "scripts/bootstrap_daily_review.ps1",
        "-DryRun",
        "-Date",
        "2026-06-01",
    ])

    combined = result.stdout + result.stderr
    assert result.returncode == 0
    assert "DRY_RUN" in combined
    assert "TOOBIT_SECRET" not in combined
    assert "TOOBIT_ACCESS" not in combined


@requires_powershell
def test_bootstrap_lock_prevents_duplicate_run():
    root = PROJECT_ROOT / "tests" / "fixtures" / "runtime" / f"lock-{uuid.uuid4().hex}"
    lock_dir = root / "data" / "locks"
    package_dir = root / "src" / "ict_review" / "cli"
    package_dir.mkdir(parents=True)
    (root / "src" / "ict_review" / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "daily_review.py").write_text("", encoding="utf-8")
    lock_dir.mkdir(parents=True)
    (lock_dir / "daily-review.lock").write_text("held", encoding="utf-8")

    result = run_powershell([
        "-File",
        "scripts/bootstrap_daily_review.ps1",
        "-DryRun",
        "-Date",
        "2026-06-01",
        "-ProjectRoot",
        str(root),
    ])

    combined = result.stdout + result.stderr
    assert result.returncode == 2
    assert "LOCK_HELD" in combined


@requires_powershell
def test_bootstrap_dry_run_stops_when_python_import_fails():
    root = PROJECT_ROOT / "tests" / "fixtures" / "runtime" / f"missing-src-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    result = run_powershell([
        "-File",
        "scripts/bootstrap_daily_review.ps1",
        "-DryRun",
        "-Date",
        "2026-06-01",
        "-ProjectRoot",
        str(root),
    ], env=env)

    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "PYTHON_FAILED" in combined
    assert "DRY_RUN Hermes one-shot" not in combined


@requires_powershell
def test_install_script_dry_run():
    result = run_powershell(["-File", "scripts/install_daily_review_agent.ps1", "-DryRun"])

    combined = result.stdout + result.stderr
    assert result.returncode == 0
    assert "DRY_RUN" in combined
    assert "toobit-daily-review" in combined


def test_bootstrap_uses_current_hermes_one_shot_syntax():
    script = (PROJECT_ROOT / "scripts" / "bootstrap_daily_review.ps1").read_text(encoding="utf-8")

    assert '"run" "--skill"' not in script
    assert "$psi.ArgumentList" not in script
    assert "Quote-ProcessArgument" in script
    assert "$psi.Arguments" in script
    assert "& python @Arguments" not in script
    assert '$psi.FileName = "python"' in script
    assert '"--skills", "toobit-daily-review"' in script
    assert '"-z", $prompt' in script
    assert "hermes.raw.txt" in script
    assert "Invoke-HermesChecked" in script
    assert "UTF8Encoding($false)" in script
    assert "normalize-llm-output" in script
    assert "MODEL_RATE_LIMIT" in script
    assert '"vertex-gemini-flash", "vertex-gemini-pro", "vertex-gemini-flash-lite"' in script
    assert "MODEL_EMPTY_RESPONSE" in script
    assert "RESUMED" in script
    assert "mark-status" in script
    assert "review_draft.json" in script
    assert "finalize" in script
    assert "if ($code -ne 0)" in script
