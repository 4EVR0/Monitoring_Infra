# Monitoring_Infra

- 4EVR0 공용 모니터링 스택. 메트릭(Prometheus) · 대시보드(Grafana) · 로그(Loki)를 한 서버에서 운영한다
- Airflow(EC2·홈서버), vLLM 추론 서버, 추천 앱(FastAPI) 등 흩어진 컴포넌트의 상태를 한곳에서 본다
- 관측(수집·시각화)에 더해 **알림(Grafana Alerting → Discord)** 까지 이 서버에서 처리한다

## 아키텍처

소스(Airflow·vLLM·앱) → 수집(Alloy/pull) → 저장(Prometheus·Loki·Iceberg) → 노출(Grafana) → 알림(Discord) 흐름.

<img width="1674" height="1100" alt="모니터링" src="https://github.com/user-attachments/assets/902c378c-5a23-434d-a03d-bcad8454fbac" />


## 구성

```
docker-compose.yml              Prometheus · Grafana · Loki · dq_api · autoheal
prometheus.yml                  스크레이프 타깃 + remote_write 수신
loki-config.yml                 단일 바이너리 Loki, 청크는 S3
alloy/                          앱 로그 수집용 Alloy 설정 (was-app.alloy)
config/dashboards/              Grafana 대시보드 (as-code JSON)
config/provisioning/alerting/   Grafana 알림 룰·연결처·정책 (as-code YAML)
dq_api/                         정합성(DQ) 메트릭 조회 API (Iceberg→DuckDB→JSON)
docs/                           운영 컨텍스트·계획 (gitignore)
```

| 서비스 | 포트 | 역할 |
|--------|------|------|
| Prometheus | `:9090` | 메트릭 TSDB. Alloy `remote_write` 수신 활성화 |
| Grafana | `:3000` | 대시보드 + 알림. Neo4j · Infinity(DQ API) datasource 플러그인 포함 |
| Loki | `:3100` | 로그 집계. 인덱스 로컬, 청크는 S3 (IAM Role 인증) |
| dq_api | `:8000` | 정합성 메트릭 조회 API. Iceberg `dq_metrics` → Grafana(Infinity). 자세한 건 `dq_api/README.md` |
| autoheal | — | `unhealthy` 컨테이너 자동 재시작 (`label=autoheal=true` 만 감시) |

이미지 태그는 모두 고정해 `latest` 자동 업그레이드를 막는다.
데이터(TSDB·Grafana·Loki)는 named volume 으로 영속화한다 —
특히 `grafana-data` 로 대시보드·데이터소스·계정이 재기동에도 남는다(이전엔 휘발성이었음).

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

## 알림 (Discord 2트랙)

문제 상황은 **Grafana Alerting**, 배치 결과 요약은 **완료 리포트**로 나눠 서로 다른 Discord 채널에 보낸다.

- **문제 알람** — 이 레포가 담당. `config/provisioning/alerting/` YAML 을 Grafana 부팅 시 로딩(`config/provisioning` 마운트). 룰이 발화하면 Discord 로 알린다
- **완료 리포트** — 파이프라인 쪽 담당. 각 단계가 `dq_metrics` 요약을 Discord 로 보낸다(Airflow `DISCORD_DQ_WEBHOOK_URL`). 만성적이지만 치명적이지 않은 지표(예: `categories_failed`)는 알람 대신 이 리포트로 노출한다

알림 룰 8종은 Prometheus(운영 지표)와 dq_api(데이터 품질)를 소스로 한다.

| 소스 | 알림 |
|------|------|
| Prometheus | DAG 실패 · 스케줄러 다운 |
| dq_api (Infinity) | crawl 신선도 · 전처리 지연(lag) · 품질 급락(match_rate) · 빈 카테고리 · 수집량 급감 · 전처리 오류율 |

- 전처리 DAG 는 crawl 완료 시 트리거되므로 절대 신선도로 못 잰다(crawl 주기 3~4일) → crawl 대비 **상대 지연(lag)** 으로 판정
- 메시지는 `contact-points.yaml` 의 title/message 에 인라인(별도 templates 참조가 provisioning 에서 빈 값으로 렌더되는 이슈 회피). `pipeline` 라벨로 대시보드 링크를 분기
- Infinity 알림 룰은 `parser: backend` 필수, dq_api 풀스캔 부하를 감안해 룰 그룹은 10분 간격
- 룰 상세·배포 절차는 `config/provisioning/alerting/README.md`

> **Monitoring_Infra 는 public 레포** — Discord 웹훅 URL 절대 커밋 금지. `.env`(gitignore)의
> `DISCORD_ALERT_WEBHOOK_URL` 로만 주입하고, 연결처 YAML 은 이 변수를 참조한다.

## 운영

```bash
docker compose up -d
docker compose ps
docker compose logs -f grafana
```

- `.env`(gitignore)에 `GF_SECURITY_ADMIN_PASSWORD`(Grafana 관리자)와 `DISCORD_ALERT_WEBHOOK_URL`(알림 연결처)을 주입한다.
- 알림 룰·연결처 YAML 을 바꾼 뒤에는 `docker compose up -d`(Grafana 재기동)로 provisioning 을 다시 읽힌다.
- Loki 는 자체 웹 UI 가 없다 → 조회는 Grafana Explore 또는 `:3100/loki/api/v1/...`.
- 스크레이프 타깃·세부 호스트/인증 설정은 `prometheus.yml`, `loki-config.yml`, `docs/` 참고.

## 메모

- vLLM 메트릭 이름은 엔진 버전에 따라 다르다. 현재는 V1 엔진(`vllm:kv_cache_usage_perc`) 기준
- Loki retention 은 현재 끔(장기 보관). 정책이 정해지면 compactor 에서 활성화
- 외부 소스(개발자 로컬 앱 등)는 상시 가동이 아니므로, 꺼져 있으면 해당 타깃 down / 패널 No data 는 정상
- **dq_api + autoheal** — 장기 구동 시 pyarrow/aws-sdk 의 TLS 컨텍스트가 부패해 S3 접근이 `curlCode77` 로 실패한다.
  `/health` 를 캐시 무시·실제 S3 스캔으로 두어 부패를 감지하면 컨테이너가 `unhealthy` → autoheal 이 재시작한다.
  (일반 트래픽은 dq_api 캐시로 견디고, 헬스 경로만 실제 S3 를 탄다)
