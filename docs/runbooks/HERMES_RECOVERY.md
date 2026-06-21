# Hermes Recovery

## 정상 확인 순서

1. `http://127.0.0.1:4000/health/readiness` — LiteLLM 준비 상태
2. `hermes --model vertex-gemini-flash -z "Reply with exactly OK"` — 단순 모델 응답
3. `powershell -ExecutionPolicy Bypass -File scripts\bootstrap_daily_review.ps1 -Date YYYY-MM-DD` — 실제 구조화 복기
4. `python -m ict_review.cli.daily_review status --date YYYY-MM-DD` — `PUBLISHED` 확인

## 실패 상태

- `MODEL_EMPTY_RESPONSE`: 모델이 HTTP 성공 뒤 최종 본문을 주지 않음
- `INVALID_LLM_OUTPUT`: JSON은 반환했지만 9개 최상위 키 또는 증거 계약 위반
- `MODEL_RATE_LIMIT`: Vertex/LiteLLM 할당량 오류

실패 후 bootstrap을 같은 날짜로 다시 실행하면 기존 `run_id`와 검산 데이터를 재사용한다.

## 현재 설치 기준

- Hermes Agent 0.17.0
- 공식 Agent UI 사전 빌드 위치: `%LOCALAPPDATA%\hermes\hermes-agent\hermes_cli\web_dist`
- Telegram 선택 의존성: `python-telegram-bot[webhooks]==22.6`
- Hermes 설정 백업은 `archive/hermes-backups/` 및 `%LOCALAPPDATA%\hermes\backups/`에 보관한다.
