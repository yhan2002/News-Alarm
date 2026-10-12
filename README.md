# News Alarm

미국 기업·거시 뉴스의 수집, 중복 제거, 근거 확인과 로컬 대시보드를 위한 연구용 프로젝트입니다. 제목·RSS 요약과 선택적인 본문을 분석하며, 결과는 투자 판단을 위한 검토 자료입니다. 감성·중요도·영향 점수는 수익률이나 정답 확률이 아닙니다.

이 저장소는 2026-10-12 로컬 소스의 정제 스냅샷입니다. 기존 Git 이력, 기사·DB·로그·모델 가중치, 실포트폴리오, 사용자 투자 논리, 인증파일과 배포 식별자는 포함하지 않습니다. 공개 예제 유니버스는 네 개 티커뿐이며 실제 운용 목록을 나타내지 않습니다. 새 설치에는 저장 기사가 없습니다.

## 구조

| 경로 | 역할 |
| --- | --- |
| `app.py`, `news.py`, `web/` | 비밀번호로 보호되는 로컬 뉴스 API·화면과 RSS 처리 |
| `universe.py`, `config/universe.example.py` | 코드 실행 없는 AST 유니버스 읽기와 예제 |
| `first_pass.py`, `content_assessment.py`, `semantics.py`, `company_links.py` | 기사 근거·기업·관계 분석과 검증 |
| `openrouter_*.py`, `evaluate_*.py`, `glm_relations.py` | 선택적 모델/API 연구 및 제한된 평가 |
| `daily_schedule.py`, `portfolio_events.py` | 선택적 일정·Telegram·보유 종목 연동 |
| `publish_news.py`, `article_sync.py` | 명시적 업로드를 위한 내보내기·기사 동기화 |
| `hosted-site/` | Regime Router/Sector Forecast 사이트에 뉴스 기능을 통합한 별도 웹 소스. 로컬 `web/`과 다른 앱 |
| `test_*`, `tools/run_offline_tests.py` | 합성 데이터 회귀·설정 안전성 검사 |

`hosted-site/build/`는 필수 Vite 플러그인 소스이며 생성물이 아닙니다. `drizzle/`의 SQL·JSON은 스키마와 마이그레이션입니다. 초기 스냅샷에는 자동 배포 workflow가 없습니다.

## 설치와 로컬 실행

Python 3.10+와 PowerShell 또는 터미널을 사용합니다. 뉴스 화면의 기본 실행은 Python 표준 라이브러리를 사용하고, 일정·전체 Python 회귀에는 `lxml`과 `tzdata`가 필요합니다.

아래는 새 환경 설치 예시입니다. 이번 점검에서는 기존 Python 3.12 환경을 사용했으며 새 환경 설치는 실행하지 않았습니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-calendar.txt
.\.venv\Scripts\python.exe app.py set-password
.\.venv\Scripts\python.exe app.py serve
```

`http://127.0.0.1:8873`에서 확인합니다. 비밀번호는 숨김 입력하며 검증값은 무시되는 `data/auth.json`에 저장됩니다. 화면에서는 저장된 기사와 합성 데모를 구분합니다. 서버는 루프백에만 열리고 별도 프로세스의 수집·모델 실행·발송을 시작하지 않습니다. `.\run.ps1`은 Python 실행기 선택을 돕는 선택적 래퍼입니다.

수집 대상만 확인하는 명령은 다음과 같습니다. 이 명령은 RSS 조회·모델 로드·저장을 하지 않습니다.

```powershell
.\.venv\Scripts\python.exe app.py collect --tickers CSQ_UNIV --macro --dry-run
```

실제 수집은 `collect`에서 `--dry-run`을 제거할 때 시작합니다. 기본 엔진 FinBERT에는 `requirements-finbert.txt` 설치와 별도 모델 준비(`app.py download-model`)가 필요합니다. 모델 파일은 저장소에 포함하지 않습니다. `--engine lexicon`은 간단한 어휘 기준 연구 대안이며 의미 분석을 대신하지 않습니다.

## 설정과 외부 연결

`.env.example`은 값 없는 목록이며 자동으로 읽히지 않습니다. 필요한 값만 프로세스 환경변수로 설정하세요.

- `NEWS_UNIVERSE_CONFIG`: 선택한 로컬 Python 설정 파일. `universe_groups = {"CSQ_UNIV": [...]}`를 AST로 읽으며 실행하지 않습니다. 미설정이면 저장소의 네 티커 예제만 사용합니다. 이웃 QuantBot 폴더는 자동 탐색하지 않습니다.
- `NEWS_PORTFOLIO_ROOT`: 명시적으로 연결할 포트폴리오 자료 폴더. 미설정이면 조회가 비활성화되고 빈 결과와 설정 안내를 반환합니다. 실제 계좌·보유 내역은 게시하지 마세요.
- `NEWS_TELEGRAM_BOT_TOKEN`, `NEWS_TELEGRAM_CHAT_ID`: 선택적 Telegram 발송 설정. 둘 다 필요하며 기본 수신자는 없습니다. 기존 AST 설정을 재사용하려면 `QUANTBOT_CONFIG_PATH`를 명시적으로 지정할 수도 있습니다.
- `OPENROUTER_API_KEY`, `ZAI_API_KEY`: 선택적 외부 모델용 키. OpenRouter의 로컬 암호화 저장 기능은 Windows 계정에 묶인 `data/` 파일을 사용합니다.
- 호스팅의 `NEWS_AUTH`, `NEWS_SESSION_KEY`, `NEWS_UPLOAD_TOKEN` 및 플랫폼 설정은 별도 환경의 비밀값입니다. 로컬 업로드 설정 `data/site-upload.json`도 Git에서 제외됩니다.

`daily_schedule.py`는 기본 미리보기에서도 외부 일정 조회를 할 수 있습니다. `--send`, `publish_news.py --upload`, 모델/API 실행 옵션은 외부 전송을 수반합니다. `schedule.ps1`은 Windows 예약 작업을 관리하고 `server-switch.ps1/.vbs`는 서버·터널 프로세스를 관리하므로 설치·테스트 명령과 구분하세요. 이 스냅샷 점검에서는 실행·등록·배포·발송하지 않았습니다.

RSS 제공자의 제한과 기사 사용 조건, 모델 라이선스가 별도로 적용됩니다. OpenRouter/Z.ai 호출 및 Cloudflare/Sites 호스팅은 공급자별 사용료·한도를 확인해야 합니다. 로컬 모델도 다운로드 용량과 CPU/GPU 자원을 사용합니다. 이번 점검에서는 유료 API나 모델 다운로드를 호출하지 않았습니다.

## 검증

```powershell
.\.venv\Scripts\python.exe -B tools/run_offline_tests.py
node --import ./tools/offline-node-guard.mjs test_web.js
node --import ./tools/offline-node-guard.mjs test_journal.js
node --import ./tools/offline-node-guard.mjs hosted-site/tests/news.test.mjs
node --import ./tools/offline-node-guard.mjs hosted-site/tests/news-data.test.mjs
```

이번 스냅샷에서 Python 스크립트 28개, 로컬 웹 Node 테스트 파일 2개, 호스팅 테스트 36개(실패·건너뜀 0), PowerShell 모의 테스트 2개가 통과했습니다. 호스팅은 Node 24.18.1에서 기존 npm 캐시만 사용하는 `npm ci --offline --ignore-scripts --no-audit --no-fund`와 외부 소켓·DNS·fetch를 차단한 `npm run build`, `node --test tests/*.test.mjs`로 검증했습니다. 실제 서버·터널·예약 작업은 실행하지 않았습니다. Python 가상환경 신규 설치는 실행하지 않았습니다.

Python runner는 임시 복사본·합성 회사 목록/기사를 사용하고 외부 소켓과 테스트 내부 자식 프로세스를 차단합니다. 인증 HTTP 테스트만 루프백 연결을 사용합니다. `test_config_safety.py`는 예제 기본값, 명시적 설정과 미설정 연동을 별도로 검증합니다. 기존 개별 테스트 중에는 생성 파일이나 특정 회사 fixture를 전제하는 것이 있어 전체 검증에는 runner를 사용하세요.

호스팅 소스는 Node 22.13+와 잠금 파일을 제공합니다. `hosted-site`에서 `npm ci`, `npm run build`는 별도 설치·빌드 절차이며 폰트 등의 네트워크 접근이 필요할 수 있습니다. `npm test`는 빌드를 먼저 실행합니다. `hosting.example.json`에는 로컬 `DB` 바인딩과 임시 DB 식별자만 사용하며 실제 배포 대상이나 권한을 구성하지 않습니다. 실제 호스팅·D1 마이그레이션·모델 품질·유료 API·Telegram 전달·운영 예약 동작은 오프라인 테스트로 검증되지 않습니다.

기능별 참고: [기사 동기화](ARTICLE_SYNC.md), [호스팅 경계](HOSTING_RUNBOOK.md), [영향도 평가](IMPACT_REVIEW.md), [투자 논리 UI](INVESTMENT_JOURNAL.md), [OpenRouter 설정](OPENROUTER_SETUP.md). 운영 이력·실기사 평가 보고서는 초기 게시 대상에서 제외했습니다.
