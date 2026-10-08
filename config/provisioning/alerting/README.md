# Grafana 알림 (as-code)

Grafana Unified Alerting을 provisioning YAML로 관리한다. Grafana 부팅 시
`/etc/grafana/provisioning/alerting/*.yaml`을 읽어 연결처·정책·룰을 자동 구성한다.
(docker-compose에서 `./config/provisioning`을 마운트)

## 파일

| 파일 | 역할 |
|------|------|
| `contact-points.yaml` | Discord 연결처. 웹훅 URL `${DISCORD_ALERT_WEBHOOK_URL}` 참조 + 메시지 디자인(스타일 B) title/message 인라인 |
| `notification-policy.yaml` | 기본 → discord(4h 반복) / `kind=batch_quality` → discord(기록 1건당 1회, 120h) / `board=system` → discord-system(해소 ✅) |
| `rules.yaml` | 알림 룰 14종 |

## 알림 14종

| # | 알림 | pipeline | 소스 · 조건 |
|---|------|------|------|
| 1a | crawl 신선도 | 크롤링 | `/dq/freshness?stage=crawl` · `age_hours > 120` |
| 1b | 전처리 지연 | 전처리 | `/dq/pipeline-lag` · `lag_hours > 1` (최신 crawl 뒤 전처리가 안 끝난 시간) |
| 2 | DAG 실패 | 파이프라인 | Prometheus · `increase(airflow_dagrun_duration_failed_count[10m]) > 0` |
| 3 | 스케줄러 다운 | 파이프라인 | Prometheus · `increase(airflow_scheduler_heartbeat[5m]) < 1` |
| 4 | 품질 급락(match_rate) | 전처리 | `/dq/latest?stage=silver_to_gold&metric=match_rate` · `< 0.95` |
| 7 | 전처리 오류율 | 전처리 | `/dq/latest?stage=bronze_to_silver&metric=error_rate` · `> 0.7` |
| 8 | 크롤 품질 경고 | 크롤링 | `/dq/latest?stage=bronze_gate&metric=gate_status` · `= 1` |
| 9 | 전처리 보류 | 전처리 | `/dq/latest?stage=bronze_gate&metric=gate_status` · `= 2`(3=우회 진행은 미발화) |
| 10 | 크롤 장기 실행 | 크롤링 | Prometheus(REST observer) · 크롤 DAG run `> 60h` |
| S1~S4 | 디스크 부족·위험 / 메모리 / 노드 끊김 | system | node-exporter |
| S5 | 관측 수집 실패 | system | Prometheus · observer `collection_success == 0`(15m) 또는 home-server 시리즈 부재 |

> 1a/1b 설계 근거: 전처리 DAG는 crawl 완료 시 트리거되므로 절대 신선도로는 못 잰다
> (crawl 주기 3~4일 → 절대 age는 늘 큼). 대신 최신 crawl 뒤 전처리가 아직 안 끝난 밀린 시간(lag)으로 판정한다.
> 4·7·8·9는 **배치 품질 알림**(`kind=batch_quality`): Infinity 응답의 `created_at`·`batch_date`를 라벨로 써서 기록 1건당 1회만 알린다(끝난 배치를 4h마다 재알림하지 않음, 해소 알림 없음).
> 크롤 품질 판정(누락·연속 누락·부분 수집·수집률)은 Oliveyoung_Pipeline 입력 품질 게이트가 하고 #8·#9는 결과만 읽는다. 옛 #5 빈 카테고리·#6 수집량 급감은 게이트로 대체해 삭제(2026-10-08).
> dq_api(Infinity) 룰은 `execErrState: KeepLast` — 조회 실패 때 상태를 유지해 같은 기록이 다시 알림되지 않게 한다.

## 메시지 디자인 (contact-points.yaml title/message 인라인, 스타일 B)

> 별도 templates.yaml의 `{{ template }}` 참조는 provisioning에서 빈 값으로 렌더되는 이슈가 있어,
> 연결처의 title/message에 직접 인라인했다(연결처 필드가 곧 Go 템플릿).

- 제목: `{심각도이모지} [올리브영 {pipeline}] {알림명}` (⚠️warning / 🚨critical / ✅resolved)
- 본문: 요약 문구 + (DAG 실패면 `dag_id`) + 링크
- `pipeline` 라벨로 대시보드 링크 분기: 크롤링·전처리 → `oliveyoung-dq-table`, 파이프라인 → `airflow-monitoring` + Loki 로그

## 배포 절차

데이터소스 UID·임계치는 2026-07-16에 확보해 `rules.yaml`에 반영 완료:
`cfltz3i4tlfr4e`(Prometheus) / `efqsx4w1y7caof`(Infinity, DQ API) / `match_rate < 0.95`.
남은 건 웹훅 주입 → 배포 → 검증.

1. **`.env`에 웹훅 주입** (커밋 금지): `DISCORD_ALERT_WEBHOOK_URL=https://discord.com/api/webhooks/...`
2. **배포**: `docker compose up -d`
3. **검증**: 일부러 룰 하나를 터뜨려 Discord 실수신 확인
   (예: `rules.yaml`의 match_rate 하한을 잠깐 `1.0`으로 올려 강제 발화 → 확인 후 `0.95`로 원복)

> 값 재확인이 필요하면:
> ```bash
> curl -s -u admin:$GF_SECURITY_ADMIN_PASSWORD http://localhost:3000/api/datasources \
>   | jq -r '.[] | "\(.uid)\t\(.type)\t\(.name)"'
> curl -s "http://localhost:8000/dq/latest?stage=silver_to_gold&metric=match_rate"
> ```

## 주의

- **Monitoring_Infra는 public 레포** — 웹훅 URL 절대 커밋 금지. `.env`(gitignore)에만.
- Infinity 알림 룰은 반드시 `parser: backend` (알림 엔진은 백엔드 파서만 실행).
- dq_api는 요청마다 Iceberg 풀스캔 → Infinity 룰 그룹은 10분 간격(과다 호출 방지).
