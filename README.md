# Trade Review Agent

개인 거래 원본과 비밀정보를 제외한 거래 리뷰 파이프라인의 공개용 코드 사본입니다.

## 구현된 기능

- 읽기 전용 거래소 응답을 내부 체결 모델로 정규화
- 체결을 포지션 에피소드로 구성하고 검증 가능한 손익 지표 계산
- 미래 데이터 혼입을 제한한 시점 기준 특징 생성
- Python 자체 검증 로직으로 LLM 출력 구조와 evidence 참조를 검증
- 가상 fixture 기반 오프라인 파이프라인 및 로컬 대시보드
- 일일 리뷰 준비·검증·완료 작업을 위한 CLI와 보조 스크립트

## 한계

- 예제 데이터는 가상 데이터이며 실제 운용 성과를 나타내지 않습니다.
- 이 프로젝트는 주문 실행, 자동매매 또는 수익 개선을 제공하지 않습니다.
- 외부 LLM/Hermes 연동은 별도 로컬 설정이 필요하며 재현 테스트는 오프라인 경로를 기준으로 합니다.
- 거래소 응답 형식이 바뀌면 어댑터 수정이 필요할 수 있습니다.
- `schemas` 폴더의 JSON Schema는 출력 형식 문서화와 향후 표준 검증 도입을 위한 참고 자료이며, 현재 실행 시 직접 사용되지는 않습니다.

## 실행 요구사항

- Python 3.10 이상

## 빠른 확인

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "src"
pytest -q
python -m ict_review.cli.review_offline --fixture examples\synthetic_input.json --data-root .pytest-tmp\example-data --run-id run_20260115T090000Z_abcdef123456
```

실제 API 자격 증명은 커밋하지 말고 `.env.example`을 복사한 로컬 `.env`에만 설정하십시오.
