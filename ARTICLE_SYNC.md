# 기사별 시간축 DB 전환

신규 기사와 변경된 분석 결과만 D1 `news_articles`에 저장한다. 발행 시각과 ID의 복합 인덱스로
기간별 기사를 50건씩 조회한다. 동일 발행 시각은 ID로 이어 읽으므로 페이지 경계에서 누락하지 않는다.
기사 목록·사건 근거는 한 번 저장하고 기존 24h/72h/7d/30d/전체 선택을 유지한다.
섹터·종목별 통계는 기존 Python 계산을 유지한 작은 캐시(`news_catalog`)다.
브라우저는 선택 기간의 모든 페이지를 확보한 뒤 연결망을 구성하며, 같은 게시 버전은 재사용한다.
전체 기간 선택은 저장된 모든 기사를 불러오므로 장기 누적 시 초기 조회가 느릴 수 있다.

- `published_at`: 원문 발행 시각. 기간 필터 기준이며 D1에서는 UTC 밀리초.
- `first_collected_at`: 신규 수집부터 기록. 과거 기록이 없는 기사는 null 유지.
- `analysis_updated_at`: 검증된 Qwen/API 분석 완료 시각. 분석되지 않은 기사는 null.
- `updated_at`: 외부 DB 동기화 버전. 오래된 전송은 최신 행을 덮어쓰지 못한다.

기존 기사 ID·발행 시각·분석·근거를 보존한다. 모델 재분석이나 과거 최초 수집 시각 추정은 하지 않는다.
로컬 기사 DB와 기존 호스팅 스냅샷은 삭제하지 않는다. 새 catalog가 없으면 기존 스냅샷을 표시한다.
배치마다 성공 응답을 받은 ID/해시만 로컬 동기화 원장에 기록한다. 실패 후 같은 명령으로 재개할 수 있다.
작은 기간별 통계는 기사 배치가 모두 저장된 뒤 갱신된다. 동기화 중의 데이터는 잠시 통계와 차이가 날 수 있으며,
기사 수/게시 버전이 달라지면 웹은 마지막 정상 화면을 유지하고 다음 갱신에서 다시 읽는다.

## 승인 후 실행 순서

1. Sites 표준 절차로 `hosted-site`를 빌드·배포한다. 새 Drizzle migration 0013가 테이블과 인덱스만 추가한다.
2. 계획 확인(읽기 전용):

```powershell
.venv\Scripts\python.exe -X utf8 article_sync.py --limit 20000
```

3. 기존 데이터 이관 및 이후 자동 증분 동기화 활성화(실제 외부 DB 쓰기):

```powershell
.venv\Scripts\python.exe -X utf8 article_sync.py --run --limit 20000 --enable
```

사이트가 protocol 2를 지원하지 않으면 쓰기 전에 중단한다. 회차 상한을 넘으면 이미 저장한 배치는
보존하고 중단한다. 상한을 올리거나 같은 명령을 다시 실행하여 남은 행을 처리한다.
성공 후 `data/site-upload.json`의 `article_sync_enabled=true`가 되어 기존 수집 완료/분석 완료 게시 경로가
자동으로 증분 동기화를 호출한다. 별도 예약·모델 호출을 추가하지 않는다.
월 OpenRouter 예산과 이관은 별개이며 이관 자체의 모델 비용은 0이다. D1/호스팅 사용량은 발생한다.

로컬 검증:

```powershell
.venv\Scripts\python.exe -X utf8 test_article_sync.py
node hosted-site/tests/news-data.test.mjs
node hosted-site/tests/news.test.mjs
```

## Protocol 3 publication (2026-09-30, local changes only)

Article batches now stage by revision. The final catalog request publishes rows, removals and catalogs in one D1 transaction. Readers recheck the revision after fetching rows and return 409 if publication intervened. The sync endpoint advertises protocol 3; the dashboard display protocol stays 2.

Rollout requires separate deployment authorization: deploy the worker first, verify the authenticated sync capability is 3, then update/run the local sender. The worker creates its staging table idempotently; existing live data stays available. The sender invalidates old hashes but preserves known IDs for removal, abandons protocol-2 pending plans and resends the current archive. Keep the sync state DB; do not delete it. Failed protocol-3 runs resume their frozen revision. Local archive saves update only changed/removed rows, retaining first-collection times and duplicate reconciliation; full-history read/dedup still occurs.

Only temporary SQLite and mocked exchange tests were run; no hosted D1 data or notifications were changed.
