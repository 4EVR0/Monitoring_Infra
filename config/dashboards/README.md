# Grafana 대시보드

코드형(as-code)으로 관리하는 Grafana 대시보드 모음. UI에서 만든 대시보드도 여기에 JSON으로 내려받아 버전 관리한다.

## 목록

| 파일 | 제목 | uid | 데이터소스 |
|------|------|-----|-----------|
| `vllm.json` | vLLM Serving (4EVR0) | `vllm-4evr0` | Prometheus |
| `airflow.json` | Airflow | — | Prometheus |
| `neo4j.json` | Neo4j (GraphDB) | — | Prometheus |

## vLLM 대시보드 패널 구성

| 행 | 패널 | 의미 |
|----|------|------|
| 상태 개요 | vLLM 상태 / running / waiting / KV 캐시 | 살아있나, 처리·대기 요청 수, 메모리 압박 |
| 처리량 | 토큰 처리량(tokens/s) / 요청 처리율 | 생성·프롬프트 토큰 속도, finished_reason별 req/s |
| 지연 | E2E / TTFT / TPOT / 큐 대기 (P50·P90·P99) | 응답시간 분해 — 어디서 느린지 |
| 캐시·효율 | Prefix 캐시 적중률 / 선점률 | 프롬프트 재사용 효율, KV 부족으로 인한 선점 |

> **병목 진단 팁**: `waiting`이 쌓이고 `큐 대기`가 늘면 → 동시성 한계.
> `선점`이 발생하면 → KV 캐시 부족(모델/배치 크기 조정 검토).

## 새 환경에 임포트하는 법 (UI)

1. Grafana → **Dashboards → New → Import**
2. `vllm.json` 업로드 (또는 내용 붙여넣기)
3. 프롬프트되는 **Prometheus 데이터소스** 선택 → Import

> 레포의 `vllm.json`은 데이터소스를 `${DS_PROMETHEUS}` 입력 변수로 두어 어떤 환경에서도
> 임포트 시 데이터소스를 고를 수 있다. (특정 환경의 datasource uid에 묶이지 않음)

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
