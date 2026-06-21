import json
from pathlib import Path

from ict_review.ui import server
from ict_review.ui.server import AgentJob, action_command, chat_action


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_page_and_launcher_exist():
    page = (PROJECT_ROOT / "dashboard" / "agent_dashboard.html").read_text(encoding="utf-8")
    launcher = (PROJECT_ROOT / "scripts" / "start_dashboard.ps1").read_text(encoding="utf-8")
    assert "__ICT_UI_TOKEN__" in page
    assert "오늘 복기 실행" in page
    assert "Hermes 공식 대시보드" in page
    assert "ict_review.ui.server" in launcher


def test_action_commands_are_allowlisted():
    assert action_command("review") is not None
    assert action_command("test") is not None
    assert action_command("refresh") is None
    assert action_command("rm -rf") is None


def test_system_test_prefers_pytest_executable(monkeypatch):
    monkeypatch.setattr(server.shutil, "which", lambda name: "C:/tools/pytest.exe" if name == "pytest" else None)
    label, command = action_command("test")
    assert label == "시스템 테스트"
    assert command == ["C:/tools/pytest.exe", "-q"]


def test_chat_maps_known_requests_without_arbitrary_shell():
    reply, action = chat_action("오늘 복기 실행해줘")
    assert "복기" in reply
    assert action == "review"
    _, action = chat_action("dir & whoami 실행")
    assert action is None
    _, action = chat_action("게이트웨이 켜줘")
    assert action == "gateway"


def test_agent_job_redacts_secrets_in_log():
    job = AgentJob()
    job._append("TOKEN=very-secret-value\n")
    snapshot = job.snapshot()
    assert "very-secret-value" not in snapshot["log"]
    assert "TOKEN=***" in snapshot["log"]
