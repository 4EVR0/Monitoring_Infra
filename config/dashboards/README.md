# Grafana 대시보드

코드형(as-code)으로 관리하는 Grafana 대시보드 모음. UI에서 만든 대시보드도 여기에 JSON으로 내려받아 버전 관리한다.

## 목록

| 파일 | 제목 | uid | 데이터소스 |
|------|------|-----|-----------|
| `vllm.json` | vLLM Serving (4EVR0) | `vllm-4evr0` | Prometheus |
| `airflow.json` | Airflow 모니터링 | `airflow-monitoring` | Prometheus · Loki |
| `airflow-dataquality-table.json` | Oliveyoung 데이터 정합성 (테이블 소스) | `oliveyoung-dq-table` | Infinity → dq_api · Loki |
| `neo4j.json` | Neo4j (GraphDB) | — | Prometheus |

Airflow·DQ JSON은 현재 Grafana에 자동 provisioning되지 않는다. 파일 수정만으로 운영 화면이
바뀌지 않으므로, 배포 후 각각 기존 UID의 대시보드에 JSON을 임포트하거나 API로 갱신한다.
새 대시보드로 중복 생성하지 말고 UID를 확인한다.

DQ 오류 유형 패널은 `/dq/error-types` API를 사용한다. 운영 `dq_api`에 해당 엔드포인트를
포함한 이미지를 먼저 배포하고, Grafana의 Infinity 데이터소스가
`http://dq_api:8000`에 접근 가능한지 확인한다. 유형별 `err_*`는 새
bronze→silver 실행부터 기록된다. 과거 실행에 `no_breakdown`이 뜨는 것은 0건이 아니라
유형별 기록이 없는 상태다. `incomplete`는 합계 불일치이므로 막대를 정상 결과로 읽지 않는다.
막대는 기본 팔레트로 표시한다. API의 `class`는 응답과 필드 매핑에 남아 있지만, 현재 막대 색을 유형 분류별로 고정하지 않는다. 실제 막대·축·값 표시와 어두운 테마에서의 가독성은 운영 Grafana에서 확인한다.

### DQ·백필 대시보드 변경 이력 (2026-09-10~21)

아래 이력은 Git 커밋과 이번 디버깅에서 제공된 Grafana Inspector 응답을 바탕으로 작성했다. 커밋·PR 생성과 운영 Grafana 반영은 별개의 단계다.

| 시점 | 커밋·PR | 변경 내용 |
|---|---|---|
| 9/10 | `2b356f9` (#14), `d3f0ab5` (#15) | 정합성 그래프와 최신 값 패널의 쿼리에 Grafana 선택 기간 `from`/`to`를 연결했다. API도 `created_at` 기준 기간 필터를 처리한다. |
| 9/19 | `d1b7fc9` (#16) | `/dq/error-types`와 정상 실행의 오류 유형 패널, Airflow 실행 상태 패널을 추가했다. API는 선택 기간에서 `silver_error` 마커의 `created_at`이 가장 늦은 실행 한 건을 골라, 같은 `run_id`·`batch_date`의 `err_*`를 집계한다. |
| 9/21 | `381e86d` (#17) | 수동 백필 DAG와 분리된 DQ stage `bronze_to_silver_backfill`의 가시성을 추가했다. |
| 9/21 | `f789118` (#18), `87f8e54` | 정상·백필을 토글로 묶는 구성을 거쳐, 두 실행 종류를 각각의 행에 항상 나란히 보여주는 구성으로 바꿨다. `87f8e54`는 이후 브랜치 커밋이다. |
| 9/21 | `1a0be74` | Infinity 쿼리에 `parser: backend`를 명시했다. 이 커밋은 #18 이후 브랜치에서 작성됐다. |
| 9/21 | `559630a` ([PR #19](https://github.com/4EVR0/Monitoring_Infra/pull/19)) | Grafana 13 내보내기 JSON을 반영했다. 문자열 Stat의 `status`·`batch_date` 필드를 명시하고, 정상·백필 Bar chart 옵션과 두 행의 배치(막대 17×9, 오른쪽 Stat 7×3 세 개)를 같게 했다. 기본 기간은 30일이다. |

#### 오류 유형 패널의 의미와 표시 문제

- 정상과 백필은 각각 `stage=bronze_to_silver`, `stage=bronze_to_silver_backfill`로 조회한다. 각 막대 패널은 **선택 기간 안에서 기록 시각이 가장 늦은 해당 stage 실행 한 건**의 오류 유형별 **레코드** 수를 보여준다. 기간 전체 합계나 제외 상품 수가 아니다. `batch_date`가 과거여도 최근에 기록된 재실행·백필이면 최신 실행이 될 수 있다.
- API는 유형별 합계가 `silver_error`와 일치할 때만 `rows`를 반환한다. `no_breakdown`·`incomplete`일 때 빈 막대는 0건을 뜻하지 않는다. 옆의 집계 상태를 함께 본다.
- 2026-09-21 사용자가 제공한 백필 Inspector 결과에서 `backfill_20260810`의 `silver_error=1121`, 유형별 합계 1121, `rows` 7개, HTTP 200과 `count` 숫자 필드를 확인했다. 당시 패널이 회색 빈 화면이었던 원인은 API 응답이나 Infinity 파싱 부재로 볼 수 없었다. 기본 Bar chart 템플릿으로는 같은 데이터가 표시됐다.
- 문자열 Stat이 `No data`였던 문제는 `reduceOptions.fields`가 비어 있던 설정을 `status` 또는 `batch_date` 필드 선택으로 바꿔 해결했다. Bar chart는 표시된 기본 템플릿 옵션을 기준으로 맞췄다. 기존 설정 중 `colorByField`, 강제 값 표시, 범례 설정 중 정확히 어느 하나가 빈 화면을 일으켰는지는 분리 검증하지 않았다.

백필 생성·입력 무결성·조건부 교체·완료 리포트의 구현 이력과 실행 전 게이트는 Oliveyoung_Pipeline의 `docs/backfill.md`에 있다. 이 문서만으로 백필 데이터의 실제 적재 성공을 판단하지 않는다.

Airflow JSON은 기존 StatsD 집계 행과 새 상태 observer 행을 분리한다. observer 행의
호스트별 수집 상태·현재 태스크/DAG 경과·직전 완료 결과는 각 Airflow 호스트에
`Airflow_Infra/airflow_exporter`와 Alloy 설정을 배포한 뒤에만 채워진다.
`수집 실패=0`과 `활성 실행=0`은 다르다. 현재 표는 REST에서 시작/대기 시각이
없는 인스턴스를 생략할 수 있으므로 상단 활성 건수와 함께 본다. 경과시간만으로
크롤 정체를 판정하지 않으며, 실제 진행 로그(Loki) 결합은 후속 작업이다.
기존 p50 timer의 원본 단위는 운영 exporter 출력으로 확인한 뒤 패널의 단위를
초 또는 밀리초로 지정한다.

## vLLM 대시보드 패널 구성

| 행 | 패널 | 의미 |
|----|------|------|
| 상태 개요 | vLLM 상태 / running / waiting / KV 캐시 | 살아있나, 처리·대기 요청 수, 메모리 압박 |
| 처리량 | 토큰 처리량(tokens/s) / 요청 처리율 | 생성·프롬프트 토큰 속도, finished_reason별 req/s |
| 지연 | E2E / TTFT / TPOT / 큐 대기 (P50·P90·P99) | 응답시간 분해 — 어디서 느린지 |
| 캐시·효율 | Prefix 캐시 적중률 / 선점률 | 프롬프트 재사용 효율, KV 부족으로 인한 선점 |
| 앱 파이프라인 (WAS) | 단계별 처리시간 / 폴백률 / 요청율 / 성분 0개 비율 | 추천 1건의 내부 분해 — 어느 단계가 느린가, LLM 장애·데이터 회귀 신호 |

> **병목 진단 팁**: `waiting`이 쌓이고 `큐 대기`가 늘면 → 동시성 한계.
> `선점`이 발생하면 → KV 캐시 부족(모델/배치 크기 조정 검토).

### 앱 파이프라인 (WAS) 행 — Phase 2 추가

FastAPI 앱(`4EVR0-Server`)이 `/metrics`로 노출하는 비즈니스 메트릭. vLLM(GPU) 메트릭만으로는
안 보이는 "추천 1건이 왜 N초 걸렸나"를 단계별로 분해해 본다.

| 패널 | 메트릭 | 읽는 법 |
|------|--------|---------|
| 단계별 평균 처리시간 (스택) | `recommend_stage_latency_seconds` | extract/neo4j/llm_response 누적 → "추출 1.3s / Neo4j 0.1s / 응답 4.4s"처럼 분해 |
| 프로필 추출 폴백률 | `profile_extraction_method_total{method}` | `rule_based` 비율 상승 = LLM(vLLM) 장애 조기경보 |
| 추천 요청 처리율 (ok/error) | `recommend_requests_total{status}` | error 비율로 앱 레벨 실패 추적 |
| 성분 0개 응답 비율 | `recommend_ingredients_found` | 0개 비율 상승 = Neo4j 데이터/쿼리 회귀 신호 |

> 스크레이프 타깃: `was-app` (`macbook-pro-3.tailb70036.ts.net:8000`) — `prometheus.yml` 참고.
> ⚠️ 앱이 개발자 Mac 로컬이라 **상시 가동 아님**. 앱이 꺼져 있으면 타깃 down + 패널 No data(정상).

## 새 환경에 임포트하는 법 (UI)

1. Grafana → **Dashboards → New → Import**
2. `vllm.json` 업로드 (또는 내용 붙여넣기)
3. 프롬프트되는 **Prometheus 데이터소스** 선택 → Import

> 레포의 `vllm.json`은 데이터소스를 `${DS_PROMETHEUS}` 입력 변수로 두어 어떤 환경에서도
> 임포트 시 데이터소스를 고를 수 있다. (특정 환경의 datasource uid에 묶이지 않음)

### 기존 Airflow·DQ 대시보드 갱신

1. 운영 Grafana에서 기존 대시보드 JSON을 백업하고 UID가 각각
   `airflow-monitoring`, `oliveyoung-dq-table`인지 확인한다.
2. `airflow.json`, `airflow-dataquality-table.json`을 임포트해 **같은 UID의 기존
   대시보드를 갱신**한다. 이름만 같은 새 대시보드가 생기지 않았는지 확인한다.
3. Airflow의 Prometheus·Loki, DQ의 Infinity·Loki 변수와 `dq_api_url`이 운영
   데이터소스/Compose 네트워크에 맞는지 확인한다.
4. DQ는 기간에 새 실행이 없으면 유형별 상태가 `no_data_in_range`, 구버전 실행만
   있으면 `no_breakdown`일 수 있다. 새 bronze→silver 실행 후 `ok`와
   유형별 건수 합계가 계산된 `silver_error`와 일치하는지 본다.

## API로 푸시하는 법 (자동화)

```bash
# ${datasource} 를 실제 prometheus uid 로 치환 후 POST /api/dashboards/db
# (uid 확인: GET /api/datasources)
```

## 주의

- 기존에 `vLLM`, `vLLM-test` 라는 이름의 대시보드가 있으나, 내용은 Crossplane/Kubernetes
  컨트롤플레인용이라 이 프로젝트의 vLLM 메트릭과 무관하다(전부 No data). 정리 권장.
- vLLM 메트릭 이름은 엔진 버전에 따라 다르다. 현재 서버는 **V1 엔진**이며
  `vllm:kv_cache_usage_perc`(구버전의 `gpu_cache_usage_perc` 아님)를 사용한다.
