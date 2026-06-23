from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[3]
WEB_FILE = PROJECT_ROOT / "dashboard" / "agent_dashboard.html"
KST = ZoneInfo("Asia/Seoul")
MAX_LOG_CHARS = 80_000
SECRET_RE = re.compile(r"(?i)(key|secret|token|password)(\s*[=:]\s*)\S+")


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _safe_text(text: str) -> str:
    return SECRET_RE.sub(r"\1\2***", text)


def _daily_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((PROJECT_ROOT / "data" / "daily").glob("*.json"), reverse=True):
        payload = _read_json(path, {})
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _gateway_log_state() -> dict[str, Any]:
    log_path = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "logs" / "gateway.log"
    if not log_path.exists():
        return {"last_activity": None}
    modified = datetime.fromtimestamp(log_path.stat().st_mtime, tz=KST)
    return {"last_activity": modified.isoformat(timespec="seconds")}


def _url_ok(url: str) -> bool:
    try:
        with urlopen(url, timeout=1.5) as response:
            return 200 <= response.status < 500
    except OSError:
        return False


class ManagedService:
    def __init__(self, name: str, command: list[str], *, health_url: str | None = None) -> None:
        self.name = name
        self.command = command
        self.health_url = health_url
        self.process: subprocess.Popen[str] | None = None
        self.external = False
        self.desired = False
        self.failures = 0
        self.started_at: str | None = None
        self.last_exit: int | None = None
        self.log = ""
        self._lock = threading.Lock()

    def _append(self, value: str) -> None:
        with self._lock:
            self.log = (self.log + _safe_text(value))[-MAX_LOG_CHARS:]

    def start(self, *, manual: bool = False) -> tuple[bool, str]:
        with self._lock:
            if self.process is not None and self.process.poll() is None:
                return True, f"{self.name}가 이미 실행 중입니다."
            if manual:
                self.failures = 0
            self.desired = True
        if self.health_url and _url_ok(self.health_url):
            with self._lock:
                self.external = True
            return True, f"기존 {self.name} 프로세스를 사용합니다."
        executable = shutil.which(self.command[0])
        if not executable:
            return False, f"{self.command[0]} 실행 파일을 찾지 못했습니다."
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                [executable, *self.command[1:]],
                cwd=PROJECT_ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
        except OSError as exc:
            self._append(f"시작 실패: {exc}\n")
            return False, f"{self.name} 시작 실패: {exc}"
        with self._lock:
            self.process = process
            self.external = False
            self.started_at = datetime.now(KST).isoformat(timespec="seconds")
            self.last_exit = None
            self.log += f"[{self.started_at}] 시작: {' '.join(self.command)}\n"
        threading.Thread(target=self._read_output, args=(process,), daemon=True).start()
        return True, f"{self.name}를 시작했습니다."

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is not None:
            for line in process.stdout:
                self._append(line)
        code = process.wait()
        with self._lock:
            if self.process is process:
                self.last_exit = code
                self.failures += 1
                self.log += f"[{datetime.now(KST).isoformat(timespec='seconds')}] 종료 코드 {code}\n"

    def ensure(self) -> None:
        with self._lock:
            external = self.external
        if external and self.health_url and _url_ok(self.health_url):
            return
        if external:
            with self._lock:
                self.external = False
        with self._lock:
            should_start = self.desired and self.failures < 3 and (self.process is None or self.process.poll() is not None)
        if should_start:
            time.sleep(2)
            self.start()

    def stop(self) -> None:
        with self._lock:
            self.desired = False
            process = self.process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            owned_running = self.process is not None and self.process.poll() is None
            external = self.external
            running = owned_running
        if external and self.health_url:
            running = _url_ok(self.health_url)
        with self._lock:
            return {
                "running": running,
                "label": "동작 중" if running else ("재시작 중" if self.desired and self.failures < 3 else "중지됨"),
                "started_at": self.started_at,
                "last_exit": self.last_exit,
                "failures": self.failures,
                "log": self.log,
                "managed": owned_running,
            }


SERVICES = {
    "litellm": ManagedService(
        "LiteLLM",
        ["litellm", "--config", str(PROJECT_ROOT / "litellm_config.yaml"), "--port", "4000"],
        health_url="http://127.0.0.1:4000/health/readiness",
    ),
    "gateway": ManagedService("Hermes Gateway", ["hermes", "gateway", "run"]),
    "hermes_ui": ManagedService(
        "Hermes Agent UI",
        ["hermes", "dashboard", "--host", "127.0.0.1", "--port", "9119", "--no-open", "--tui", "--skip-build"],
        health_url="http://127.0.0.1:9119",
    ),
}

DEMO_MODE = False


def _demo_state_payload() -> dict[str, Any]:
    """Return synthetic state loaded from examples/ — no API keys or real data needed."""
    review_md = (PROJECT_ROOT / "examples" / "synthetic_review.md").read_text(encoding="utf-8")
    now = datetime.now(KST).isoformat(timespec="seconds")
    today = datetime.now(KST).date().isoformat()
    demo_service: dict[str, Any] = {
        "running": False,
        "label": "중지됨 (데모 모드)",
        "started_at": None,
        "last_exit": None,
        "failures": 0,
        "log": "[DEMO] 데모 모드에서는 외부 서비스가 실행되지 않습니다.\n",
        "managed": False,
    }
    return {
        "now": now,
        "today": today,
        "today_review": {
            "date": "2026-01-15",
            "latest_run_id": "run_20260115T090000Z_abcdef123456",
            "latest_status": "VERIFIED",
            "next_action": "패턴 확인 대기 중 (synthetic demo)",
        },
        "recent_days": [
            {"date": "2026-01-15", "latest_run_id": "run_20260115T090000Z_abcdef123456", "latest_status": "VERIFIED"},
        ],
        "gateway": {**demo_service, "last_activity": None},
        "hermes_ui": {**demo_service, "healthy": False},
        "litellm": {**demo_service, "healthy": False},
        "cron": [],
        "patterns": {
            "confirmed": [],
            "candidates": [
                {
                    "pattern_id": "offline-fixture-single-episode",
                    "status": "CANDIDATE",
                    "episode_id": "episode-0001",
                    "date": "2026-01-15",
                    "evidence_id": "ev-pnl",
                    "user_answer": "",
                    "created_at": "2026-01-15T09:10:00Z",
                    "schema_version": "2.0",
                }
            ],
        },
        "latest_review": {
            "date": "2026-01-15",
            "run_id": "run_20260115T090000Z_abcdef123456",
            "content": review_md,
        },
        "job": {"running": False, "action": "", "started_at": None, "finished_at": None, "returncode": None, "log": ""},
    }


def launch_gateway() -> tuple[bool, str]:
    return SERVICES["gateway"].start(manual=True)


def _cron_state() -> list[dict[str, Any]]:
    path = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "cron" / "jobs.json"
    payload = _read_json(path, {})
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    return [
        {
            "name": row.get("name", "unknown"),
            "enabled": bool(row.get("enabled")),
            "last_status": row.get("last_status"),
            "last_run_at": row.get("last_run_at"),
            "next_run_at": row.get("next_run_at"),
            "delivery": row.get("deliver"),
        }
        for row in jobs
        if isinstance(row, dict)
    ]


def _patterns() -> dict[str, list[dict[str, Any]]]:
    payload = _read_json(PROJECT_ROOT / "memory" / "pattern_memory.json", {})
    rows = payload.get("patterns", []) if isinstance(payload, dict) else []
    return {
        "confirmed": [row for row in rows if row.get("status") == "CONFIRMED"],
        "candidates": [row for row in rows if row.get("status") == "CANDIDATE"],
    }


def _latest_review(daily: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in daily:
        run_id = row.get("latest_run_id")
        if not run_id:
            continue
        review = PROJECT_ROOT / "data" / "runs" / str(run_id) / "review.md"
        if review.exists():
            return {
                "date": row.get("date"),
                "run_id": run_id,
                "content": review.read_text(encoding="utf-8", errors="replace")[:20_000],
            }
    return None


class AgentJob:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running = False
        self.action = ""
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.returncode: int | None = None
        self.log = ""

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "action": self.action,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "returncode": self.returncode,
                "log": self.log,
            }

    def _append(self, text: str) -> None:
        with self._lock:
            self.log = (self.log + _safe_text(text))[-MAX_LOG_CHARS:]

    def start(self, action: str, command: list[str]) -> bool:
        with self._lock:
            if self.running:
                return False
            self.running = True
            self.action = action
            self.started_at = datetime.now(KST).isoformat(timespec="seconds")
            self.finished_at = None
            self.returncode = None
            self.log = f"[{self.started_at}] {action} 시작\n"
        threading.Thread(target=self._run, args=(command,), daemon=True).start()
        return True

    def _run(self, command: list[str]) -> None:
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        try:
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert process.stdout is not None
            for line in process.stdout:
                self._append(line)
            code = process.wait()
        except Exception as exc:  # surfaced in the local UI
            self._append(f"실행 실패: {exc}\n")
            code = -1
        with self._lock:
            self.running = False
            self.returncode = code
            self.finished_at = datetime.now(KST).isoformat(timespec="seconds")
            self.log += f"\n[{self.finished_at}] 종료 코드 {code}\n"


JOB = AgentJob()


def state_payload() -> dict[str, Any]:
    if DEMO_MODE:
        return _demo_state_payload()
    daily = _daily_rows()
    today = datetime.now(KST).date().isoformat()
    today_row = next((row for row in daily if row.get("date") == today), None)
    gateway = SERVICES["gateway"].snapshot()
    gateway.update(_gateway_log_state())
    hermes_ui = SERVICES["hermes_ui"].snapshot()
    hermes_ui["healthy"] = _url_ok("http://127.0.0.1:9119")
    litellm = SERVICES["litellm"].snapshot()
    litellm["healthy"] = _url_ok("http://127.0.0.1:4000/health/readiness")
    litellm["label"] = "준비됨" if litellm["healthy"] else litellm["label"]
    return {
        "now": datetime.now(KST).isoformat(timespec="seconds"),
        "today": today,
        "today_review": today_row or {"date": today, "latest_status": "MISSING", "next_action": "오늘 복기를 실행하세요."},
        "recent_days": daily[:7],
        "gateway": gateway,
        "hermes_ui": hermes_ui,
        "litellm": litellm,
        "cron": _cron_state(),
        "patterns": _patterns(),
        "latest_review": _latest_review(daily),
        "job": JOB.snapshot(),
    }


def action_command(action: str) -> tuple[str, list[str]] | None:
    if action == "review":
        return "오늘 복기", [sys.executable, str(PROJECT_ROOT / "scripts" / "hermes" / "run_daily_review.py")]
    if action == "test":
        pytest_executable = shutil.which("pytest")
        if pytest_executable:
            return "시스템 테스트", [pytest_executable, "-q"]
        return "시스템 테스트", [sys.executable, "-m", "pytest", "-q"]
    return None


def chat_action(message: str) -> tuple[str, str | None]:
    value = message.strip().lower()
    if any(word in value for word in ("복기", "리뷰 실행", "오늘 실행")):
        return "오늘 복기를 실행합니다.", "review"
    if any(word in value for word in ("갱신", "새로고침", "대시보드 생성")):
        return "손익 대시보드를 다시 생성합니다.", "refresh"
    if any(word in value for word in ("테스트", "검사")):
        return "시스템 테스트를 실행합니다.", "test"
    if "게이트웨이" in value and any(word in value for word in ("시작", "켜", "실행")):
        return "Hermes Gateway를 시작합니다.", "gateway"
    if any(word in value for word in ("상태", "어떻게", "현황")):
        state = state_payload()
        today = state["today_review"].get("latest_status", "MISSING")
        gateway = state["gateway"]["label"]
        return f"오늘 복기 상태는 {today}, Hermes Gateway는 {gateway}입니다.", None
    if any(word in value for word in ("패턴", "기억", "학습")):
        patterns = _patterns()
        return f"확정 패턴 {len(patterns['confirmed'])}개, 확인 대기 {len(patterns['candidates'])}개입니다.", None
    return "가능한 요청: 상태 확인, Hermes 게이트웨이 시작, 오늘 복기 실행, 대시보드 갱신, 패턴 확인, 시스템 테스트", None


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "ICTReviewUI/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            try:
                page = WEB_FILE.read_text(encoding="utf-8").replace("__ICT_UI_TOKEN__", self.server.ui_token)  # type: ignore[attr-defined]
            except OSError as exc:
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            body = page.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/state":
            self._json(state_payload())
        elif path == "/health":
            self._json({"ok": True})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.headers.get("X-ICT-UI-Token") != self.server.ui_token:  # type: ignore[attr-defined]
            self._json({"error": "invalid local UI token"}, HTTPStatus.FORBIDDEN)
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 8_192)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid JSON"}, HTTPStatus.BAD_REQUEST)
            return
        path = urlparse(self.path).path
        if path == "/api/action":
            requested_action = str(payload.get("action", ""))
            if requested_action == "gateway":
                ok, message = launch_gateway()
                self._json({"ok": ok, "message": message}, HTTPStatus.ACCEPTED if ok else HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            selected = action_command(requested_action)
            if selected is None:
                self._json({"error": "unknown action"}, HTTPStatus.BAD_REQUEST)
                return
            label, command = selected
            if not JOB.start(label, command):
                self._json({"error": "another task is already running"}, HTTPStatus.CONFLICT)
                return
            self._json({"ok": True, "message": f"{label}을 시작했습니다."}, HTTPStatus.ACCEPTED)
        elif path == "/api/chat":
            reply, action = chat_action(str(payload.get("message", "")))
            started = False
            if action:
                if action == "gateway":
                    started, reply = launch_gateway()
                else:
                    selected = action_command(action)
                    if selected:
                        started = JOB.start(*selected)
                if action != "gateway" and selected:
                    if not started:
                        reply = "다른 작업이 실행 중입니다. 완료 후 다시 요청하세요."
            self._json({"ok": True, "reply": reply, "started": started})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)


def main(argv: list[str] | None = None) -> int:
    global DEMO_MODE
    parser = argparse.ArgumentParser(description="Run the local ICT review dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--demo", action="store_true", help="Use synthetic example data — no API keys or real data needed.")
    parser.add_argument("--smoke-test", action="store_true", help="start managed services, print health, then stop")
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost"}:
        parser.error("The dashboard is intentionally local-only; use 127.0.0.1 or localhost.")
    DEMO_MODE = args.demo
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    server.ui_token = secrets.token_urlsafe(24)  # type: ignore[attr-defined]
    url = f"http://{args.host}:{args.port}"
    print(f"ICT review dashboard: {url}", flush=True)
    if DEMO_MODE:
        print("[DEMO] synthetic 예제 데이터로 실행 중 — 외부 서비스 미시작", flush=True)
    print("종료: Ctrl+C", flush=True)
    if not DEMO_MODE:
        for service in SERVICES.values():
            ok, message = service.start(manual=True)
            print(message, flush=True)
    stop_monitor = threading.Event()

    def monitor_services() -> None:
        while not stop_monitor.wait(5):
            if not DEMO_MODE:
                for service in SERVICES.values():
                    service.ensure()

    threading.Thread(target=monitor_services, daemon=True).start()
    if args.smoke_test:
        if not DEMO_MODE:
            for _ in range(30):
                if _url_ok("http://127.0.0.1:9119"):
                    break
                time.sleep(2)
        result = state_payload()
        print(json.dumps({
            "gateway": result["gateway"],
            "hermes_ui": result["hermes_ui"],
            "litellm": result["litellm"],
        }, ensure_ascii=False, indent=2), flush=True)
        stop_monitor.set()
        if not DEMO_MODE:
            for service in SERVICES.values():
                service.stop()
        server.server_close()
        return 0
    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_monitor.set()
        if not DEMO_MODE:
            for service in SERVICES.values():
                service.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
