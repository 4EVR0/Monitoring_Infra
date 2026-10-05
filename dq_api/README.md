# DQ_API

프로젝트 데이터 파이프라인의 **데이터 정합성(DQ) 메트릭 조회 API**.

여러 파이프라인(크롤링·전처리·성분매핑 등)이 각자 남기는 정합성 수치를 모아둔
공용 Iceberg 테이블 `oliveyoung_db.dq_metrics`를 pyiceberg로 읽고 DuckDB로 질의해,
Grafana(Infinity 데이터소스)가 바로 쓰는 JSON을 반환한다. 특정 파이프라인에 묶이지
않고, 파이프라인/모니터링 레포와도 독립적으로 관리된다 — warehouse 경로·리전·IAM만
맞으면 어느 EC2에서든 뜬다.

## 구조

```
파이프라인(각 단계) → dq_metrics (Iceberg, key/value)
                          └─ DQ_API (pyiceberg+DuckDB) → Grafana(Infinity)
```

## 엔드포인트

| 메서드 · 경로 | 용도 |
|---|---|
| `GET /health` | 캐시를 무시한 Iceberg/S3 실제 스캔 헬스체크 |
| `GET /dq/latest?stage=&metric=&from=&to=` | 선택 기간의 최신 run 값 1건 (점수판 stat 타일) |
| `GET /dq/series?stage=&metric=&from=&to=` | 선택 기간 시계열 (`from`/`to` 미지정 시 `days=29`) |
| `GET /dq/freshness?stage=` | stage별 마지막 기록 시각과 경과 시간 |
| `GET /dq/pipeline-lag` | 최신 crawl 뒤 silver_to_gold가 아직 안 끝나 밀린 시간(끝났으면 0, 게이트 보류 배치 제외) |
| `GET /dq/rows?stage=&metric=&from=&to=&limit=200` | 최근 원시 행 (표/드릴다운) |
| `GET /dq/error-types?stage=&from=&to=` | 선택 기간의 최신 전처리 실행 한 건에 대한 오류 유형별 건수와 집계 상태 |

`stage`: `crawl` / `bronze_to_silver` / `bronze_to_silver_backfill` / `silver_to_gold`
`metric`: `match_rate`, `error_rate`, `categories_failed` 등

`from`/`to`는 Grafana가 넘기는 epoch millisecond다. `/dq/error-types`는 `silver_error`를
마커로 선택 기간의 최신 실행 한 건을 고르고, 같은 run의 `err_*` 합계를 비교한다.
`status`는 `ok`, `no_breakdown`(유형별 기록 없음), `incomplete`(합계 불일치),
`no_data_in_range`, `no_data` 중 하나다. `ok`가 아니면 `rows`를 빈 배열로 반환해
부분 집계를 정상 막대로 표시하지 않는다.

## 환경변수

| 이름 | 필수 | 예시 / 기본값 |
|---|---|---|
| `ICEBERG_WAREHOUSE_PATH` | ✅ | `s3://oliveyoung-crawl-data/olive_young_iceberg_metadata/` |
| `AWS_REGION` | | `ap-northeast-2` |
| `DQ_METRICS_TABLE` | | `oliveyoung_db.dq_metrics` |

AWS 자격증명은 **EC2 IAM Role 사용 권장** — 이 경우 키를 `.env`에 넣지 않는다.
로컬 테스트 시에만 `.env`에 키를 둔다. (`.env`는 커밋 금지 — `.gitignore` 처리됨)

## IAM 권한 (읽기 전용)

배포 서버 롤에 아래가 필요하다. dq_metrics 실제 파일은 테이블 `location`
(`olive_young_gold/dq_metrics/`)에 있으므로 **`olive_young_gold` 읽기가 핵심**이다.

- **Glue** (읽기): `GetDatabase(s)`, `GetTable(s)`, `GetTableVersion(s)` — `oliveyoung_db` + 테이블
- **S3** (읽기): `s3:GetObject` on `olive_young_gold/*` (+ 카탈로그 확인용 `olive_young_iceberg_metadata/*`),
  `s3:ListBucket` on 같은 prefix
- 쓰기 권한 불필요 (조회 전용)

## 로컬 실행

```bash
pip install -r requirements.txt
cp .env.example .env   # 값 채우기
uvicorn app:app --reload
```

## 배포 (도커)

```bash
docker build -t dq_api .
docker run -d --name dq_api -p 8000:8000 --env-file .env dq_api
```

- `--env-file .env` — `KEY=value` 줄을 주입. **따옴표·`export` 없이** 작성(도커는 따옴표를 값에 그대로 넣음).
- ⚠️ **IAM Role + 기본 bridge 네트워크**면 컨테이너가 인스턴스 메타데이터(IMDS)에 못 닿아
  크레덴셜을 못 받을 수 있다(`NoCredentialsError`). 이때 **host 네트워크**로 실행:
  ```bash
  docker run -d --name dq_api --env-file .env --network host dq_api   # -p 불필요
  ```
  또는 인스턴스 IMDSv2 hop limit을 2로: `aws ec2 modify-instance-metadata-options --instance-id <id> --http-put-response-hop-limit 2`

## 검증

```bash
curl localhost:8000/health              # 실제 Iceberg/S3 스캔 성공 시 {"status":"ok",...}
curl "localhost:8000/dq/rows?limit=5"   # 실제 dq_metrics 행 조회
```

`/health`가 `503`이면 테이블·크레덴셜·IAM·S3 연결을 확인한다. 이 경로는 캐시를 쓰지 않아
장기 구동 중 TLS/S3 접근 장애도 감지하며, Compose healthcheck 실패 시 autoheal이 컨테이너를 재시작한다.
