# OpenRouter 연결 준비

현재 단계는 **수동 연결 시험 준비**입니다. 수집·Qwen·Luna·웹 게시·텔레그램 자동화는 변경하지 않습니다.
추가 패키지 없이 기존 Python 환경과 본문 근거 검증을 재사용합니다.

## 1. 결제 후 설정

1. https://openrouter.ai/settings/credits 에서 승인 금액만 충전합니다. 자동충전은 끕니다.
2. https://openrouter.ai/settings/keys 에서 News Alarm 전용 키를 만듭니다. 시험용 키 한도는 우선 $1로 설정합니다.
3. 키는 `--save-key` 실행 시 숨김 입력으로 넣습니다. Windows 계정에 종속된 암호문을 Git 제외 경로 `data/openrouter-key.dpapi`에 저장합니다. 평문을 명령행·소스·로그에 넣지 않습니다.
   이미 `OPENROUTER_API_KEY` 환경변수가 있으면 그 값을 우선 사용합니다. 저장 키가 없으면 실행 시 숨김 입력을 요청합니다.

기본 모델은 사용자 선택에 따라 `qwen/qwen3.7-flash`입니다. `qwen/qwen3.5-flash-02-23`도 명시적으로 선택할 수 있습니다.
추론 비활성화, 기본 1건, 출력 1,600토큰 제한을 유지합니다. 상시 유료 분석은 활성화하지 않았습니다.

```powershell
# 키를 변경하거나 다른 Windows 계정에서 설정할 때. 이 명령 자체는 API를 호출하지 않음.
.\.venv\Scripts\python.exe -X utf8 openrouter_pilot.py --save-key
```

## 2. 지금 가능한 무료 점검

프로젝트 PowerShell에서:

```powershell
.\.venv\Scripts\python.exe -X utf8 test_openrouter_pilot.py
.\.venv\Scripts\python.exe -X utf8 openrouter_pilot.py --limit 1
```

기본 실행은 DB의 최신 기사와 입력 범위만 확인합니다. 네트워크 요청·키 입력·모델 실행·운영 DB 쓰기는 없습니다.
저장된 본문이 있으면 최대 12,000자를 재사용하고, 없으면 제목·RSS 요약임을 명시합니다. 이 명령은 본문을 새로 다운로드하지 않습니다.

## 3. 충전 후 직접 실행할 유료 연결 시험

```powershell
# 최신 기사 1건, 최대 출력 1,600토큰, 타임아웃 90초
.\.venv\Scripts\python.exe -X utf8 openrouter_pilot.py --limit 1 --run

# 다른 후보 (이 명령도 별도 유료 호출)
.\.venv\Scripts\python.exe -X utf8 openrouter_pilot.py --model qwen/qwen3.5-flash-02-23 --limit 1 --run
```

한 실행의 상한은 10건입니다. 순차 호출하며 오류 시 즉시 중단합니다. 자동 재시도·다른 모델 전환·제공자 폴백은 없습니다.
입출력 토큰·비용 등 응답 usage, 소요시간, 본문 확보 범위와 검증 결과를 `data/openrouter-pilot-날짜시간.json`에 저장합니다.
응답이 잘리거나 근거가 입력과 맞지 않으면 성공으로 처리하지 않습니다. 기존 운영 DB와 웹에는 시험 결과를 반영하지 않습니다.

요청은 데이터 수집을 허용하지 않는 제공자 경로와 지원 파라미터를 요구합니다. 해당 경로가 없으면 실패하며, 자동으로 정책을 완화하지 않습니다.
이는 모든 제공자의 무보관을 보장한다는 의미는 아닙니다. 결제 후 허용 제공자 정책을 확인합니다.
401/403은 키·접근 권한, 402는 잔액, 429는 호출 제한을 확인하고 다시 실행합니다. 오류 본문과 인증 헤더는 기록하지 않습니다.
실제 단가는 실행 전에 확인하고, 키 한도로 지출을 제한합니다. 이 스크립트 자체에는 월 결제 한도 기능이 없습니다.

## 4. 운영 전환 조건

연결 성공 후 동일 기사 표본으로 모델 간 근거 정확도·중요 사건 누락·처리시간·비용을 비교합니다.
그 결과를 확인한 뒤 별도 지시로 상시 Qwen/Luna 경로를 전환합니다. 현재는 시험 명령만 준비됐습니다.

공식 문서: https://openrouter.ai/docs/api_reference/overview
 / https://openrouter.ai/docs/guides/routing/provider-selection
