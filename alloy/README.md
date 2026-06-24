# Alloy 로그 수집 (앱 → Loki)

기존 Airflow 로그는 EC2/홈서버의 Alloy 가 `/opt/airflow/logs/*` 를 tail 해 Loki 로 보낸다.
여기 `was-app.alloy` 는 **개발자 Mac 로컬에서 도는 추천 앱(FastAPI) 로그**를 같은 Loki 로 보낸다.

## 현재 Loki 수집 현황 (2026-06-24 점검)

| job | service_name | host | 비고 |
|-----|--------------|------|------|
| `airflow` | `airflow` | `airflow-ec2`, `home-server` | 기존 |
| `was-app` | `4evr0-recommend` | `macbook-pro-3` | **이번에 추가** |

- Loki: `http://monitoring-server-1.tailb70036.ts.net:3100` (`auth_enabled: false`, Tailscale 내부망)
- 라벨 관례: `job` / `service_name` / `host` / `filename`(자동) — 저카디널리티만 라벨로.
- `trace_id` 는 **structured metadata** (앱이 trace_id 별로 로그를 묶음 → Loki에서 요청 단위 추적).

## 실행 (Mac)

### 1) Alloy 설치
```bash
brew install grafana/grafana/alloy
```

### 2) 앱을 로그파일로 기록하며 실행
앱은 JSON 로그를 stdout 으로 찍는다(`LOG_FORMAT=json`, 기본). 이를 파일로도 남긴다:
```bash
cd /Users/hyeokjun/4EVR0/4EVR0-Server
mkdir -p logs
uvicorn app.main:app --host 0.0.0.0 --port 8000 2>&1 | tee -a logs/app.log
```

### 3) Alloy 실행
```bash
export WAS_LOG_PATH=/Users/hyeokjun/4EVR0/4EVR0-Server/logs/app.log
alloy run /Users/hyeokjun/4EVR0/Monitoring_Infra/alloy/was-app.alloy
```
> 설정 문법 검사만: `alloy fmt was-app.alloy`

## 검증 (Grafana Explore / LogQL)

Grafana → Explore → Loki 데이터소스:
```logql
{job="was-app"}                                  # 앱 로그 전체
{job="was-app"} | json | level="ERROR"           # 에러만
{job="was-app"} | trace_id="<응답 X-Trace-ID 값>"  # 특정 요청의 모든 로그 (structured metadata)
```
앱에 요청을 한 건 보내고(`/api/v1/recommend` 등), 응답 헤더 `X-Trace-ID` 값으로 위 쿼리를 돌리면
그 요청의 모든 로그가 한 trace_id 로 묶여 보인다.

curl 로 직접 확인:
```bash
LOKI=http://monitoring-server-1.tailb70036.ts.net:3100
curl -s "$LOKI/loki/api/v1/label/job/values"   # was-app 이 보이면 수집 시작된 것
```

## ⚠️ 주의
- 앱이 Mac 로컬이라 상시 가동 아님 — 앱/Alloy 꺼져 있으면 그 구간 로그 없음(정상).
- `trace_id` 를 절대 라벨(`stage.labels`)로 올리지 말 것 — 스트림 카디널리티 폭발.
