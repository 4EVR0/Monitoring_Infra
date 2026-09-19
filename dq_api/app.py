"""
dq_api — 정합성(DQ) 메트릭 조회 API

Iceberg 테이블(oliveyoung_db.dq_metrics)을 pyiceberg로 읽어 DuckDB로 질의하고,
Grafana Infinity 데이터소스가 바로 쓰는 JSON을 반환한다.

독립 레포 — oliveyoung_common에 의존하지 않는다(테이블명만 공유 상수로 맞춘다).

환경변수
    ICEBERG_WAREHOUSE_PATH  Iceberg 메타데이터 S3 경로 (필수)
    AWS_REGION              AWS 리전 (기본: ap-northeast-2)
    DQ_METRICS_TABLE        테이블 식별자 (기본: oliveyoung_db.dq_metrics)
"""

import os
import threading
import time
from functools import lru_cache

import duckdb
from fastapi import FastAPI, HTTPException, Query
from pyiceberg.catalog import load_catalog

DQ_METRICS_TABLE = os.environ.get("DQ_METRICS_TABLE", "oliveyoung_db.dq_metrics")
AWS_REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
# dq_metrics는 배치마다(며칠~월 단위)만 바뀌므로 스캔 결과를 짧게 캐시한다.
DQ_CACHE_TTL = float(os.environ.get("DQ_CACHE_TTL", "60"))  # 초

# boto3(pyiceberg의 Glue 클라이언트 포함)가 리전을 항상 찾도록 보장 — NoRegionError 방지
os.environ.setdefault("AWS_DEFAULT_REGION", AWS_REGION)
os.environ.setdefault("AWS_REGION", AWS_REGION)

app = FastAPI(title="dq_api", version="0.1.0")


@lru_cache(maxsize=1)
def _catalog():
    warehouse = os.environ.get("ICEBERG_WAREHOUSE_PATH")
    if not warehouse:
        raise RuntimeError("ICEBERG_WAREHOUSE_PATH is not set")
    return load_catalog(
        "glue",
        **{"type": "glue", "warehouse": warehouse, "s3.region": AWS_REGION},
    )


_cache: dict = {"arrow": None, "ts": 0.0}
_cache_lock = threading.Lock()


def _load_arrow(force: bool = False):
    """dq_metrics를 arrow로 로드하되 TTL 캐시로 재사용한다.

    대시보드 1회 로드에 패널 여러 개가 동시에 때려도 스캔은 캐시 주기당 1번만 일어난다
    (락으로 직렬화해 thundering herd 방지). 스캔 실패 시 직전 캐시로 폴백해
    S3/TLS 순간 장애에도 패널·알림이 에러 대신 살짝 stale한 값을 받는다.
    force=True(헬스체크)는 캐시를 무시하고 실제 스캔 → 실패를 전파해 autoheal 재시작을 유도.
    """
    with _cache_lock:
        fresh = _cache["arrow"] is not None and (time.monotonic() - _cache["ts"]) < DQ_CACHE_TTL
        if fresh and not force:
            return _cache["arrow"]
        try:
            arrow = _catalog().load_table(DQ_METRICS_TABLE).scan().to_arrow()
            _cache["arrow"] = arrow
            _cache["ts"] = time.monotonic()
            return arrow
        except Exception:
            if not force and _cache["arrow"] is not None:
                return _cache["arrow"]  # 일반 트래픽은 직전 캐시로 폴백(에러 안 냄)
            raise                        # 헬스체크(force)는 실패 전파 → autoheal


def _query(sql: str, params: list) -> list[dict]:
    """캐시된 dq_metrics arrow를 DuckDB로 질의 → dict 목록 반환."""
    arrow = _load_arrow()
    con = duckdb.connect()
    try:
        con.register("dq", arrow)
        df = con.execute(sql, params).df()
    finally:
        con.close()
    # created_at 등 timestamp는 ISO 문자열로 직렬화
    return df.astype(object).where(df.notna(), None).to_dict(orient="records")


@app.get("/health")
def health():
    # 캐시 무시하고 실제 S3 스캔 → TLS 부패 등 진짜 장애를 잡아 autoheal 재시작을 유도.
    # 일반 트래픽은 캐시로 견디되, 이 경로만 실패 시 500 → 컨테이너 재시작.
    try:
        _load_arrow(force=True)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"iceberg scan failed: {e}")
    return {"status": "ok", "table": DQ_METRICS_TABLE}


@app.get("/dq/latest")
def latest(
    stage: str = Query(..., description="crawl | bronze_to_silver | silver_to_gold"),
    metric: str = Query(..., description="지표명 (match_rate 등)"),
    from_ms: int | None = Query(None, alias="from", description="Grafana ${__from} (epoch ms)"),
    to_ms: int | None = Query(None, alias="to", description="Grafana ${__to} (epoch ms)"),
):
    """해당 (stage, metric)의 최신 run 값 1건. 점수판 stat 타일용.

    from/to(Grafana 시간범위, epoch ms)가 오면 그 구간 안의 최신 run을 고른다 →
    점수판도 시리즈/표와 똑같이 시간 선택기를 따른다(7월 선택 시 9월 최신값이 뜨던 괴리 제거).
    미지정 시 과거 동작대로 역대 최신 run(직접 호출·하위호환용). 구간 판정은
    series/rows와 동일하게 epoch_ms(created_at) 정수 비교.
    """
    rows = _query(
        """
        SELECT metric_value, batch_date, run_id, target_table, created_at
        FROM dq
        WHERE stage = ? AND metric_name = ?
          AND (? IS NULL OR epoch_ms(created_at) >= ?)
          AND (? IS NULL OR epoch_ms(created_at) <= ?)
        ORDER BY created_at DESC
        LIMIT 1
        """,
        [stage, metric, from_ms, from_ms, to_ms, to_ms],
    )
    if not rows:
        raise HTTPException(status_code=404, detail="해당 지표 데이터 없음")
    return rows[0]


@app.get("/dq/series")
def series(
    stage: str = Query(...),
    metric: str = Query(...),
    days: int = Query(29, ge=1, le=365, description="조회 기간(일). from/to 미지정 시 폴백."),
    from_ms: int | None = Query(None, alias="from", description="Grafana ${__from} (epoch ms)"),
    to_ms: int | None = Query(None, alias="to", description="Grafana ${__to} (epoch ms)"),
):
    """(stage, metric)의 시계열. 추세 timeseries 패널용.

    from/to(Grafana 시간범위, epoch ms)가 오면 그 구간을 조회 → 대시보드 시간 선택기가
    실제로 먹는다. 둘 다 없으면 과거 동작대로 now 기준 최근 N일로 폴백(직접 호출·하위호환용).
    구간 판정은 epoch_ms(created_at)로 정수 비교 — timestamp/timestamptz 암묵 캐스팅 이슈 회피.
    """
    if from_ms is not None and to_ms is not None:
        return _query(
            """
            SELECT created_at, metric_value, batch_date, run_id
            FROM dq
            WHERE stage = ? AND metric_name = ?
              AND epoch_ms(created_at) BETWEEN ? AND ?
            ORDER BY created_at
            """,
            [stage, metric, from_ms, to_ms],
        )
    # days는 검증된 정수(1~365)라 인터벌에 직접 삽입 — 파라미터 바인딩 인터벌 이슈 회피
    return _query(
        f"""
        SELECT created_at, metric_value, batch_date, run_id
        FROM dq
        WHERE stage = ? AND metric_name = ?
          AND created_at >= now() - INTERVAL '{int(days)}' DAY
        ORDER BY created_at
        """,
        [stage, metric],
    )


@app.get("/dq/freshness")
def freshness(
    stage: str | None = Query(None, description="특정 stage만 (미지정 시 전 stage)"),
):
    """stage별 최근 run 신선도(age_hours). crawl 신선도 알림·대시보드용.

    임계 판정은 안 한다 — age_hours 숫자만 내려주고 임계치(96h 등)는 Grafana 룰이 적용.
    신선도는 batch_date(논리 기준일, 백필 시 과거)가 아니라 created_at(실제 기록 시각)으로 잰다.
    """
    return _query(
        """
        SELECT stage,
               MAX(created_at)                                   AS last_run_at,
               round(epoch(now() - MAX(created_at)) / 3600.0, 1) AS age_hours
        FROM dq
        WHERE (? IS NULL OR stage = ?)
        GROUP BY stage
        ORDER BY stage
        """,
        [stage, stage],
    )


@app.get("/dq/pipeline-lag")
def pipeline_lag():
    """전처리 DAG가 최신 crawl을 얼마나 못 따라잡았나(lag_hours). 전처리 지연 알림용.

    전처리는 crawl 완료 시 트리거되므로 절대 신선도(1h 등)로는 못 잰다
    (crawl 주기가 3~4일이라 절대 age는 늘 크다) → crawl 대비 상대 지연으로 판정.
    lag_hours = crawl 마지막 실행 − silver_to_gold(전처리 종단) 마지막 실행.
      · 정상: 전처리가 crawl 직후 따라옴 → lag ≈ 0 (음수면 0 이하)
      · 이상: 새 crawl 뒤 전처리 실패/지연 → lag 증가 → Grafana 룰이 >1h 판정
    (전처리가 역대 한 번도 안 돈 극단 케이스는 lag=NULL → 미알림. #2 DAG 실패 알림이 커버)
    """
    return _query(
        """
        SELECT
            MAX(created_at) FILTER (WHERE stage = 'crawl')          AS crawl_last_run,
            MAX(created_at) FILTER (WHERE stage = 'silver_to_gold') AS silver_last_run,
            round(epoch(
                MAX(created_at) FILTER (WHERE stage = 'crawl')
                - MAX(created_at) FILTER (WHERE stage = 'silver_to_gold')
            ) / 3600.0, 1) AS lag_hours
        FROM dq
        """,
        [],
    )


@app.get("/dq/rows")
def rows(
    stage: str | None = Query(None),
    metric: str | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    from_ms: int | None = Query(None, alias="from", description="Grafana ${__from} (epoch ms)"),
    to_ms: int | None = Query(None, alias="to", description="Grafana ${__to} (epoch ms)"),
):
    """최근 원시 행. 표/드릴다운용. stage·metric·기간(from/to)은 모두 선택 필터.

    from/to(epoch ms)를 주면 해당 구간만 — 표 패널도 대시보드 시간 선택기를 따른다.
    미지정 필터는 `? IS NULL OR ...` 가드로 통과(stage/metric과 동일 패턴).
    """
    return _query(
        """
        SELECT batch_date, run_id, stage, metric_name, metric_value, target_table, created_at
        FROM dq
        WHERE (? IS NULL OR stage = ?)
          AND (? IS NULL OR metric_name = ?)
          AND (? IS NULL OR epoch_ms(created_at) >= ?)
          AND (? IS NULL OR epoch_ms(created_at) <= ?)
        ORDER BY created_at DESC
        LIMIT ?
        """,
        [stage, stage, metric, metric, from_ms, from_ms, to_ms, to_ms, limit],
    )


@app.get("/dq/error-types")
def error_types(
    stage: str = Query("bronze_to_silver", description="전처리 단계"),
    from_ms: int | None = Query(None, alias="from", description="Grafana ${__from} (epoch ms)"),
    to_ms: int | None = Query(None, alias="to", description="Grafana ${__to} (epoch ms)"),
):
    """최신 실행의 error_type별 레코드 수 + 집계 완전성. 유형별 막대 패널용.

    - 최신 run = silver_error(마커, 0건도 매 배치 기록)의 created_at 최댓값 실행.
      from/to면 그 구간 내 최신. → batch_date 최대가 아니라 created_at 기준(재실행·백필 대응).
    - 요약(silver_error)과 유형별(err_*)은 별도 append라 부분 성공 가능 →
      sum(err_*) != silver_error면 status='incomplete'. 유형 행 없다고 0/이전 배치값으로 보이면 안 됨.
      status: ok | incomplete | no_data_in_range.
    """
    marker = _query(
        """
        SELECT run_id, batch_date, metric_value AS silver_error, created_at
        FROM dq
        WHERE stage = ? AND metric_name = 'silver_error'
          AND (? IS NULL OR epoch_ms(created_at) >= ?)
          AND (? IS NULL OR epoch_ms(created_at) <= ?)
        ORDER BY created_at DESC
        LIMIT 1
        """,
        [stage, from_ms, from_ms, to_ms, to_ms],
    )
    if not marker:
        return {"status": "no_data_in_range", "types": {}, "silver_error": None}

    m = marker[0]
    run_id = m["run_id"]
    silver_error = None if m["silver_error"] is None else int(m["silver_error"])

    rows_ = _query(
        """
        SELECT metric_name, metric_value
        FROM dq
        WHERE run_id = ? AND stage = ? AND starts_with(metric_name, 'err_')
        """,
        [run_id, stage],
    )
    types = {r["metric_name"][4:]: int(r["metric_value"]) for r in rows_}  # 'err_' 제거
    total = sum(types.values())

    # 완전성: 유형 없음+0건=ok / 합==silver_error=ok / 그 외=incomplete(부분 저장·불일치)
    if not types:
        status = "ok" if silver_error == 0 else "incomplete"
    elif silver_error is not None and total == silver_error:
        status = "ok"
    else:
        status = "incomplete"

    return {
        "status": status,
        "run_id": run_id,
        "batch_date": m["batch_date"],
        "created_at": m["created_at"],
        "silver_error": silver_error,
        "types_total": total,
        "types": types,
    }
