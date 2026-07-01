# Monitoring_Infra

- 4EVR0 공용 모니터링 스택. 메트릭(Prometheus) · 대시보드(Grafana) · 로그(Loki)를 한 서버에서 운영한다
- Airflow(EC2·홈서버), vLLM 추론 서버, 추천 앱(FastAPI) 등 흩어진 컴포넌트의 상태를 한곳에서 본다

## 구성

```
docker-compose.yml          Prometheus · Grafana · Loki
prometheus.yml              스크레이프 타깃 + remote_write 수신
loki-config.yml             단일 바이너리 Loki, 청크는 S3
alloy/                      앱 로그 수집용 Alloy 설정 (was-app.alloy)
config/dashboards/          Grafana 대시보드 (as-code JSON)
dq_api/                     정합성(DQ) 메트릭 조회 API (Iceberg→DuckDB→JSON)
docs/                       운영 컨텍스트·계획 (gitignore)
```

| 서비스 | 포트 | 역할 |
|--------|------|------|
| Prometheus | `:9090` | 메트릭 TSDB. Alloy `remote_write` 수신 활성화 |
| Grafana | `:3000` | 대시보드. Neo4j datasource 플러그인 포함 |
| Loki | `:3100` | 로그 집계. 인덱스 로컬, 청크는 S3 (IAM Role 인증) |
| dq_api | `:8000` | 정합성 메트릭 조회 API. Iceberg `dq_metrics` → Grafana(Infinity). 자세한 건 `dq_api/README.md` |

이미지 태그는 모두 고정해 `latest` 자동 업그레이드를 막는다.
데이터는 named volume 으로 영속화한다

## 수집 방식

```
Airflow (EC2 · 홈서버)  ──Alloy push──▶  메트릭 → Prometheus / 로그 → Loki
vLLM 추론 서버           ──Prometheus pull──▶  /metrics
추천 앱 (FastAPI)        ──pull(메트릭) + Alloy push(로그)──▶  Prometheus / Loki
```

- 망이 다르거나 단발성인 소스는 **push**(Alloy remote_write·Loki push), 상시 떠 있는 소스는 **pull** 로 받는다
- 로그·메트릭 라벨을 `job` / `host` / `dag_id` / `task_id` 로 맞춰, Grafana 에서 메트릭 패널 → 해당 로그로 바로 점프한다
- 카디널리티가 큰 값(run_id, trace_id 등)은 라벨 대신 LogQL 필터 / structured metadata 로 처리한다

## 대시보드

`config/dashboards/` 에 JSON 으로 버전 관리한다. UI 에서 만든 것도 내려받아 커밋한다.

| 파일 | 내용 |
|------|------|
| `airflow.json` | DAG/태스크 실행, 호스트 지표, 로그 점프 |
| `airflow-dataquality.json` | 파이프라인 데이터 품질 지표 (Loki 로그 기반) |
| `airflow-dataquality-table.json` | 정합성 지표 (Iceberg `dq_metrics` → dq_api, Infinity 소스) |
| `vllm.json` | vLLM 처리량·지연(TTFT/TPOT)·KV 캐시 + 추천 앱 단계별 latency |
| `neo4j.json` | Neo4j(GraphDB) 상태 |

레포의 대시보드는 데이터소스를 입력 변수로 두어, 다른 환경에서도 임포트 시 datasource 만 고르면 된다.

## 운영

```bash
docker compose up -d
docker compose ps
docker compose logs -f grafana
```

- Grafana 관리자 비밀번호는 `.env`(`GF_SECURITY_ADMIN_PASSWORD`)로 주입한다.
- Loki 는 자체 웹 UI 가 없다 → 조회는 Grafana Explore 또는 `:3100/loki/api/v1/...`.
- 스크레이프 타깃·세부 호스트/인증 설정은 `prometheus.yml`, `loki-config.yml`, `docs/` 참고.

## 메모

- vLLM 메트릭 이름은 엔진 버전에 따라 다르다. 현재는 V1 엔진(`vllm:kv_cache_usage_perc`) 기준
- Loki retention 은 현재 끔(장기 보관). 정책이 정해지면 compactor 에서 활성화
- 외부 소스(개발자 로컬 앱 등)는 상시 가동이 아니므로, 꺼져 있으면 해당 타깃 down / 패널 No data 는 정상
