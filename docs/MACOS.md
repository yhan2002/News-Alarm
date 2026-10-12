# News-Alarm: M1 Mac 시작 안내

M1 Apple Silicon의 **arm64 Python**을 기준으로 합니다. macOS 버전은 아직 모르며 실제 Mac 설치/실행은 미검증입니다. 먼저 [공통 M1 설치 안내](https://github.com/yhan2002/Market-Lens/blob/codex/macos-m1-20261012/docs/MACOS-M1-START.md)를 확인하세요. 이 안내와 호환성 보완은 draft PR의 `codex/macos-m1-20261012` 브랜치에 있습니다.

## 설치와 첫 확인

Python 3.10 이상을 지원하는 소스이며, 아래는 Windows 오프라인 점검에 사용한 버전 계열과 맞춘 **Python 3.12** 예시입니다. 새 Mac에서 직접 복제한 이 저장소 루트에서 실행합니다. 기존 `.venv`가 있으면 재사용하기 전에 Python/아키텍처를 확인하고 덮어쓰지 마세요.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -c "import platform, sys; print(sys.version); print(platform.machine()); print(sys.executable)"
python -m pip install -r requirements-calendar.txt
python -B tools/run_offline_tests.py
python app.py collect --tickers CSQ_UNIV --macro --dry-run
```

Python 아키텍처는 `arm64`, 실행 파일은 이 저장소의 `.venv/bin/python`이어야 합니다. `pip install`은 패키지를 내려받습니다. 위 `dry-run`은 종목/피드 수만 보여주며 RSS, 모델 로드, 저장을 실행하지 않습니다. 기본 샘플은 공개 4종목입니다. 전체 오프라인 테스트는 임시 복사본/합성 입력에서 실행하고 외부 네트워크와 테스트의 자식 프로세스를 차단합니다. 인증 HTTP 테스트의 localhost 연결만 허용합니다.

로컬 뉴스 화면의 기본 실행은 표준 라이브러리만 필요합니다. 일정 기능과 전체 회귀 테스트에는 `requirements-calendar.txt`의 `lxml`, `tzdata`가 필요합니다. M1용 wheel이 설치되지 않는 경우 Rosetta로 임의 전환하지 말고 macOS/Python 버전과 패키지 설치 오류를 확인하세요.

## 로컬 데모 화면

```bash
python app.py set-password
python app.py serve
```

비밀번호는 숨김 입력으로 설정하며, 해시가 Git 제외 경로 `data/auth.json`에 저장됩니다. 브라우저에서 `http://127.0.0.1:8873`을 열고 데모 화면을 확인합니다. 서버는 loopback에서 실행되며 종료는 `Ctrl+C`입니다. 개인 Mac에서 새로 비밀번호를 설정하세요. 기존 회사의 인증 파일/기사 DB를 복사할 필요가 없습니다. 두 명령은 RSS 수집, 외부 모델 호출, 터널, 배포를 시작하지 않습니다.

## 기능별 범위

| 기능/진입점 | M1 안내 |
| --- | --- |
| `app.py serve`, `collect --dry-run`, `self-check` | 첫 확인에 사용할 로컬/오프라인 경로 |
| `app.py collect` (dry-run 없음) | 실제 외부 RSS 수집·분석·DB 저장; 설치 검증 단계에서 실행하지 않음 |
| `daily_schedule.py` | 기본 preview도 외부 일정 조회 가능; 오프라인 명령으로 간주하지 않음 |
| `first_pass.py`, `collection_job.py` | Windows `msvcrt` 잠금이 필요해 Mac CLI 실행은 명시 오류로 중단 |
| `article_sync.py --run`, `openrouter_worker.py --activate/--relation-verifier`, `reassess_impact.py --run` | Windows 잠금이 필요한 쓰기/실행 경로; Mac에서 지원하지 않음 |
| `run.ps1`, `schedule.ps1`, `server-switch.ps1/.vbs` | Windows 실행·예약작업·프로세스 관리용; Mac 설치 단계에서 실행하지 않음 |
| `hosted-site/` | Python 로컬 화면과 별도인 통합 웹 프로젝트; 아래 별도 설치 필요 |

Windows 잠금을 제거하거나 우회해서 작업기를 실행하지 마세요. 해당 경로의 동시 실행 제어·과금 한도 보호를 macOS용으로 이식한 변경은 아닙니다. 일반 분석 함수의 계산과 Windows 실행 흐름은 유지합니다.

## 개인 설정과 선택적 모델

[.env.example](../.env.example)은 항목 참고용이며 자동으로 로드되지 않습니다. 프로그램은 실행 프로세스의 환경변수를 읽습니다. `NEWS_UNIVERSE_CONFIG`와 `NEWS_PORTFOLIO_ROOT`가 없다면 공개 샘플/빈 포트폴리오 경로를 사용합니다. 회사 설정·포트폴리오를 옮기거나 `QUANTBOT_CONFIG_PATH`를 회사 파일에 연결하지 마세요. 직접 승인한 개인 데이터가 준비된 뒤 각 설정의 실제 경로를 지정합니다.

OpenRouter의 `--save-key`는 Windows DPAPI 전용입니다. Mac에서는 키를 묻기 전에 명시 오류로 중단하며, 복사된 `.dpapi` 파일을 해독하지 않습니다. 선택적 API 작업을 별도로 실행할 때 `OPENROUTER_API_KEY` 환경변수가 우선하며, 저장 파일이 없는 대화형 경로는 `getpass` 숨김 입력을 지원합니다. 자동 Keychain 저장을 추가하지 않았습니다. 키를 명령행 인수·소스·셸 이력·문서에 적지 마세요. 설치/데모/오프라인 테스트에는 API 키가 필요 없습니다. 자세한 기존 동작은 [OpenRouter 설정](../OPENROUTER_SETUP.md)을 참고하세요.

FinBERT는 선택 사항입니다. `requirements-finbert.txt`는 `transformers>=4.51,<5`, `torch>=2.6,<3`을 선언하며 기본 수집 엔진은 FinBERT입니다. 실제 수집 전에 의존성과 모델 다운로드가 필요합니다. 현재 [Apple의 PyTorch 안내](https://developer.apple.com/metal/pytorch/)에서 macOS 버전 요구 사항을 확인한 후 별도로 설치하세요. OS 버전 미상인 M1에서 현재 resolver가 고르는 torch 버전의 호환성을 확인하지 않았습니다. 이 프로젝트의 모델 로더는 CPU 경로를 사용하며 MPS/GPU 가속을 추가하거나 검증하지 않았습니다. 첫 화면 확인을 위해 모델을 설치하거나 다운로드할 필요는 없습니다. OS/torch 호환성을 확인하고 모델 사용을 별도로 결정한 경우의 실제 명령은 다음과 같습니다. 두 단계 모두 다운로드가 발생하며 이번 작업에서는 실행하지 않았습니다.

```bash
python -m pip install -r requirements-finbert.txt
python app.py download-model
```


## 선택적 통합 웹 프로젝트

[hosted-site 안내](../hosted-site/README.md)를 먼저 읽습니다. Node 22.13 이상(공통 안내는 arm64 Node 24 사용)과 npm이 필요합니다. Cloudflare 도구의 [macOS 지원 요건](https://developers.cloudflare.com/workers/wrangler/install-and-update/)도 현재 OS와 비교하세요. 문서 확인 시 요구 사항은 macOS 13.5 이상입니다.

```bash
cd hosted-site
node -p "process.version + ' ' + process.arch"
npm ci --ignore-scripts --no-audit --no-fund
npm run build
node --test tests/*.test.mjs
cd ..
```

`npm ci`는 잠금 파일에 맞는 패키지를 내려받고, 빌드는 폰트를 내려받을 수 있습니다. 이 블록을 오프라인 명령으로 분류하지 않습니다. 소스와 로컬 빌드만 확인하며 배포, D1 원격 migration, 사이트 업로드는 실행하지 않습니다. 실제 hosting 식별자와 운영 데이터가 없는 snapshot이므로 빌드 성공이 운영 배포 준비를 뜻하지는 않습니다.

## 검증한 것과 남은 것

- Windows Python 3.12에서 네트워크를 차단한 전체 **29개 스크립트 테스트**가 통과했습니다. 새 테스트는 Mac 키 처리와 Windows 작업기 5개가 파일 생성 전에 중단되는 경계를 모의 검증합니다.
- 기존 Windows 키 저장 로직, 뉴스/영향도/투자 계산, 모델 버전 범위는 변경하지 않았습니다. 실제 API 호출·모델 다운로드·스케줄 등록·운영 봇 실행은 하지 않았습니다.
- 실제 M1에서 새 가상환경의 패키지 설치, localhost 화면, arm64 Node/Cloudflare 바이너리, 선택적 torch/모델 실행은 아직 미검증입니다. Mac에서 먼저 OS/아키텍처, 오프라인 테스트, 데모 화면 순으로 확인하세요.
