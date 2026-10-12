# 호스팅 소스와 게시 경계

`hosted-site/`는 기존 Regime Router/Sector Forecast 웹 앱에 `/news/` 뉴스 화면·인증 API를 통합한 소스입니다. 로컬 `web/`의 일부 정적 파일 복사본과 별도 Worker를 포함합니다. 서버는 게시된 결과를 읽으며 로컬 뉴스 수집기를 대신하지 않습니다.

- `worker/news.mjs`, `worker/news-data.mjs`: 인증, snapshot·기사 API와 명시적 업로드 처리.
- `drizzle/`: 빈 데이터베이스를 위한 구조·마이그레이션. 실제 D1 데이터는 포함하지 않음.
- `build/sites-vite-plugin.ts`: 빌드 산출물에 마이그레이션을 묶는 소스.
- `hosting.example.json`: 로컬 `DB` 바인딩만 포함. 실제 프로젝트 ID·원격 DB ID·토큰 없음.
- `publish_news.py`: 저장 뉴스 내보내기, `--upload` 때에만 설정된 목적지로 전송.
- `article_sync.py`: 설정된 기사별 동기화. [설계](ARTICLE_SYNC.md) 참고.

원격 호스팅에는 사용자의 배포 대상, D1 권한과 비밀환경 `NEWS_AUTH`, `NEWS_SESSION_KEY`, `NEWS_UPLOAD_TOKEN`이 필요합니다. 로컬 게시 설정은 무시되는 `data/site-upload.json`에만 둡니다. 새 설치에는 게시 설정이나 기본 수신 서버가 없습니다. 비밀번호 원문·포트폴리오·기사 DB·원문 캐시를 정적 asset에 넣지 마세요.

Node 22.13+에서 `npm ci` 후 `npm run build`가 별도 준비 절차입니다. 빌드는 폰트 등을 내려받을 수 있고 로컬 `dist/`를 생성합니다. 실제 배포와 원격 마이그레이션은 이 저장소의 초기 게시·오프라인 검증에 포함되지 않습니다. 배포 상태나 서비스 가용성을 이 스냅샷으로 보장하지 않습니다.

`python tools/run_offline_tests.py`의 합성 snapshot round-trip, `node hosted-site/tests/news.test.mjs`, `node hosted-site/tests/news-data.test.mjs`는 업로드 없이 수행할 수 있습니다. 기존 운영 snapshot·배포 메타데이터·미리보기 이미지는 포함하지 않습니다.
