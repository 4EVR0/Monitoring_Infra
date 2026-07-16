# Grafana 알림 (as-code)

Grafana Unified Alerting을 provisioning YAML로 관리한다. Grafana 부팅 시
`/etc/grafana/provisioning/alerting/*.yaml`을 읽어 연결처·정책·룰을 자동 구성한다.
(docker-compose에서 `./config/provisioning`을 마운트)

## 파일

| 파일 | 역할 |
|------|------|
| `contact-points.yaml` | Discord 연결처. 웹훅 URL은 `${DISCORD_WEBHOOK_URL}` 환경변수 참조 |
| `notification-policy.yaml` | 모든 알림 → discord 라우팅 |
| `rules.yaml` | 알림 룰 5종 |

## 알림 5종

| # | 알림 | 소스 | 조건 |
|---|------|------|------|
| 1a | crawl 신선도 | dq_api `/dq/freshness?stage=crawl` (Infinity) | `age_hours > 96` |
| 1b | 전처리 지연 | dq_api `/dq/pipeline-lag` (Infinity) | `lag_hours > 1` |
| 2 | DAG 실패 | Prometheus | `increase(airflow_dagrun_duration_failed_count[10m]) > 0` |
| 3 | 스케줄러 다운 | Prometheus | `increase(airflow_scheduler_heartbeat[5m]) < 1` |
| 4 | 품질 급락 | dq_api `/dq/latest?...match_rate` (Infinity) | `metric_value < 임계치` |

> 1a/1b 설계 근거: 전처리 DAG는 crawl 완료 시 트리거되므로 절대 신선도로는 못 잰다
> (crawl 주기 3~4일 → 절대 age는 늘 큼). 대신 crawl 대비 상대 지연(lag)으로 판정한다.

## 배포 절차

1. **라이브 서버에서 값 3개 확보**
   ```bash
   # 데이터소스 UID (Prometheus·Infinity)
   curl -s -u admin:$GF_PW http://localhost:3000/api/datasources \
     | jq -r '.[] | "\(.uid)\t\(.type)\t\(.name)"'
   # match_rate 평상시 값 → 임계치 산정 (스케일 0~1 vs 0~100 확인)
   curl -s "http://localhost:8000/dq/latest?stage=bronze_to_silver&metric=match_rate"
   ```
2. **`rules.yaml`의 플레이스홀더 치환**: `__PROMETHEUS_UID__`, `__INFINITY_UID__`, `__MATCH_RATE_MIN__`
3. **`.env`에 웹훅 주입** (커밋 금지): `DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...`
4. **배포**: `docker compose up -d`
5. **검증**: 일부러 룰 하나를 터뜨려 Discord 실수신 확인
   (예: `__MATCH_RATE_MIN__`을 잠깐 1.0으로 올려 강제 발화 → 확인 후 원복)

## 주의

- **Monitoring_Infra는 public 레포** — 웹훅 URL 절대 커밋 금지. `.env`(gitignore)에만.
- Infinity 알림 룰은 반드시 `parser: backend` (알림 엔진은 백엔드 파서만 실행).
- dq_api는 요청마다 Iceberg 풀스캔 → Infinity 룰 그룹은 10분 간격(과다 호출 방지).
