# Local Dashboard

외부 서버 없이 이 PC에서만 사용하는 운영 화면이다. 서버는 `127.0.0.1`에만 바인딩되며 컴퓨터 또는 실행 창을 끄면 함께 종료된다. Control Room이 Hermes Gateway와 공식 Agent UI 프로세스를 직접 관리한다.

## 실행

프로젝트 루트에서 다음 명령을 실행한다. 브라우저가 자동으로 아래 주소를 연다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_dashboard.ps1
```

```text
http://127.0.0.1:8765
```

실제 Agent 채팅 화면은 `http://127.0.0.1:9119`이며 Control Room의 `Agent UI 열기` 버튼으로 연다.

종료는 실행 중인 터미널에서 `Ctrl+C`를 누른다.

## 화면 기능

- 오늘 복기, Hermes Gateway, LiteLLM, cron, 학습 패턴 상태 확인
- 오늘 복기 실행, 단계별 진행 로그와 실패 원인 확인
- Hermes 공식 UI에서 실제 모델 채팅·세션·Agent 진행 과정 확인
- 전체 테스트 실행

Control Room은 서버에서 허용한 고정 명령만 실행한다. 실제 자유 대화는 Hermes 공식 UI에서 수행한다. 매매 주문, 취소, 출금, 전송, 레버리지 변경 기능은 제공하지 않는다.

## 주의

`오늘 복기 실행`은 Toobit 읽기 전용 조회와 Hermes 모델 호출을 수행한다. 모델이 빈 응답이나 잘못된 JSON을 반환하면 같은 run을 보존한 채 정확한 실패 상태를 표시하고 Hermes 단계부터 재개한다.
