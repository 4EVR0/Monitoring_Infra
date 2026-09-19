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
새 패널의 막대 방향·유형별 색은 운영 Grafana에서 한 번 시각 확인한다.

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
