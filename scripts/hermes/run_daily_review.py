"""Hermes cron entrypoint for the Daily Trading Review.

Hermes cron runs `--script` files with Python, so this wrapper must be Python.
It runs the existing PowerShell bootstrap (prepare -> Hermes review ->
normalize -> finalize), then prints a Korean summary built from the EXACT
verified numbers (Python ground truth). With `--no-agent --deliver telegram`
that summary is what gets delivered, so no rounding / "약" / "대략" hedging.

Optional env overrides:
- ICT_TRADING_WIKI_ROOT : project root (else resolve from this script)
- ICT_REVIEW_DATE       : YYYY-MM-DD to review (else today, KST/local)
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2]


def _project_root() -> Path:
    env_root = os.environ.get("ICT_TRADING_WIKI_ROOT")
    if env_root and Path(env_root).exists():
        return Path(env_root)
    if DEFAULT_ROOT.exists():
        return DEFAULT_ROOT
    return Path.cwd()


def _review_date() -> str:
    return os.environ.get("ICT_REVIEW_DATE") or _dt.date.today().isoformat()


def _quant(value, places: str) -> str:
    try:
        d = Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)
    except Exception:
        return str(value)
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _money(value) -> str:
    return _quant(value, "0.01")


def _qty(value) -> str:
    return _quant(value, "0.0001")


def _kst_hms(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        t = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(
            _dt.timezone(_dt.timedelta(hours=9))
        )
    except Exception:
        return None
    return t.strftime("%H:%M:%S")


def _korean_summary(root: Path, date_str: str) -> str | None:
    daily = root / "data" / "daily" / f"{date_str}.json"
    if not daily.exists():
        return None
    index = json.loads(daily.read_text(encoding="utf-8"))
    run_id = index.get("latest_run_id")
    if not run_id:
        return None
    run_dir = root / "data" / "runs" / run_id
    episodes_path = run_dir / "episodes.json"
    if not episodes_path.exists():
        return None
    episodes = json.loads(episodes_path.read_text(encoding="utf-8"))

    lines = [f"📊 {date_str} 매매 복기 (검산값, 정확 · 시각 KST)", f"매매 {len(episodes)}건", ""]
    for ep in episodes:
        symbol = str(ep.get("symbol", "")).split("-")[0] or "?"
        is_short = str(ep.get("direction", "")).upper() == "SHORT"
        arrow = "🔻숏" if is_short else "🔺롱"
        opened = _kst_hms(ep.get("opened_at"))
        closed = _kst_hms(ep.get("closed_at"))
        net = ep.get("calculated_net_pnl")
        sign = "+" if Decimal(str(net or "0")) > 0 else ""

        if closed:
            lines.append(f"{arrow} {symbol}  ({opened} → {closed})")
            lines.append(f"  진입가 {_qty(ep.get('entry_vwap'))} → 청산가 {_qty(ep.get('exit_vwap'))}  ·  {_qty(ep.get('entry_quantity'))}개")
        else:
            lines.append(f"{arrow} {symbol}  ({opened} 진입, 미청산)")
            lines.append(f"  진입가 {_qty(ep.get('entry_vwap'))}  ·  {_qty(ep.get('entry_quantity'))}개")
        lines.append(
            f"  순손익 {sign}{_money(net)} USDT  "
            f"(총 {_money(ep.get('gross_realized_pnl'))} · 수수료 {_money(ep.get('fees'))})"
        )

    lines += _memory_lines(root)
    return "\n".join(lines)


def _memory_lines(root: Path) -> list[str]:
    pm = root / "memory" / "pattern_memory.json"
    if not pm.exists():
        return []
    try:
        patterns = json.loads(pm.read_text(encoding="utf-8")).get("patterns", [])
    except Exception:
        return []
    confirmed = [p for p in patterns if p.get("status") == "CONFIRMED"]
    candidates = [p for p in patterns if p.get("status") == "CANDIDATE"]
    if not confirmed and not candidates:
        return []
    out = ["", f"🧠 학습 위키: 확정 기억 {len(confirmed)} · 후보 {len(candidates)}"]
    for p in confirmed[:3]:
        note = str(p.get("user_answer", "")).strip().replace("\n", " ")
        out.append(f"  ✓ {p.get('pattern_id')}" + (f" — {note[:40]}" if note else ""))
    if candidates:
        out.append(f"  · 확인 대기 {len(candidates)}건 (확정하면 다음 복기에 반영)")
    return out


def main() -> int:
    root = _project_root()
    date_str = _review_date()
    bootstrap = root / "scripts" / "bootstrap_daily_review.ps1"
    if not bootstrap.exists():
        print(f"MISSING bootstrap: {bootstrap}", flush=True)
        return 1

    args = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(bootstrap), "-ProjectRoot", str(root)]
    if os.environ.get("ICT_REVIEW_DATE"):
        args += ["-Date", date_str]

    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = str(root / "src") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    proc = subprocess.run(args, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    log = (proc.stdout or "") + (proc.stderr or "")

    if proc.returncode != 0:
        # Deliver the failure so the user sees WHY, not silence.
        print(f"⚠️ {date_str} 복기 실패 (code {proc.returncode})\n{log.strip()}", flush=True)
        return proc.returncode

    summary = _korean_summary(root, date_str)
    if summary:
        print(summary, flush=True)
    else:
        # Published already or nothing to review — surface the bootstrap note.
        print(log.strip() or f"{date_str}: 처리할 신규 매매 없음", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
