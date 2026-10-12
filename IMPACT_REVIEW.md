# 잠재적 가격 영향 평가 v1

신규 OpenRouter 분석 한 번에 사건·관계·영향도를 함께 받는다. 0~100은 수익률 예측이나
정답 확률이 아니다. 직접 영향을 받는 대상의 가치평가 기대를 바꿀 잠재력이며 5점 단위다.
영향 범위(company/sector/market), 대상, 신뢰도, 시장 예상치 확인 여부는 별도 필드다.
기사 유형별 고정 가산점은 없다. 원문에 없는 컨센서스·선반영·규모는 추정하지 않는다.

0~30 반복·해설/작은 변화, 35~65 제한적·불확실한 영향, 70~85 유의미한 가치평가 변화,
90~100 존속·핵심 가치평가 전제 변화. 미확정 대형 사건도 높은 잠재 영향을 가질 수 있다.
본문 미확보/발췌는 coverage에 보존하고 높은 신뢰도를 허용하지 않는다.
이 구간은 검증된 수익률 기준이 아닌 초기 운영 기준이다.

## 보존과 교정

기존 first-pass.sqlite의 facts.impact에 rubric version, 점수, 네 가지 판단 근거,
원문 인용, 범위·대상·신뢰도·coverage를 저장한다. 같은 레코드의 at/model/text_hash와
openrouter_calls의 원응답을 함께 보존한다. 기존 평가를 새 점수로 소급 변환하거나 재호출하지 않는다.
최종 사람 검토 점수가 있으면 그것이 우선한다. 구버전 기사는 기존 점수로 남는다.

주간 표본은 고득점만 고르지 않고 점수 구간·종목/섹터/시장·coverage별로 모은다.
평가 당시 기록은 수정하지 않고 별도 로컬 CSV(예: data/impact-review.csv)에 아래 열로 기록한다:

article_key, assessed_at, rubric_version, model_score, reviewer_score, verdict,
reason, target, event_time, horizon, target_return, market_return, sector_return,
other_events, attribution, evidence_url

verdict: over/under/reasonable/uncertain. attribution: supported/uncertain/confounded.
가격을 보기 전에 근거 적정성을 검토한 reviewer_score를 기록한다. 이후 동일 시간 구간의
종목·시장·섹터 수익률과 동시 발생 실적/정책/다른 뉴스를 확인한다. 단순 상대수익률도
인과 증명은 아니다. 이벤트 시각·비교가격이 없거나 교란이 크면 uncertain으로 둔다.
상승/하락 방향과 중요도의 크기를 혼동하지 않는다. 다음 거래일과 5거래일처럼 사전에
정한 관찰 구간을 일관되게 쓰며, 결과를 보고 유리한 구간을 고르지 않는다.

반복되는 과대·과소평가 원인을 모아 프롬프트를 개정하고 버전을 올린다.
교정에 사용하지 않은 별도 표본으로 재평가한다. 자동 가격 수집·인과 판정·유료 재평가는
현재 구현 범위가 아니며, 실제 사례가 쌓인 뒤 별도 실행 범위를 정한다.

## 무료 검증

`.venv\Scripts\python.exe -X utf8 test_impact_assessment.py`

`.venv\Scripts\python.exe -X utf8 test_openrouter_worker.py`

실제 API 테스트, 과거 DB 재평가, 웹 코드 배포는 이 변경에서 실행하지 않는다.
기존 예약 워커는 다음 실행부터 새 응답 형식을 사용한다. 요청 횟수·출력 상한1600토큰·
월 $5 한도를 유지하나 실제 출력량 증가로 건당 비용/지연이 늘 수 있다.
