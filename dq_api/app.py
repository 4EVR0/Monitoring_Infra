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
):
    """해당 (stage, metric)의 최신 run 값 1건. 점수판 stat 타일용."""
    rows = _query(
        """
        SELECT metric_value, batch_date, run_id, target_table, created_at
        FROM dq
        WHERE stage = ? AND metric_name = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        [stage, metric],
    )
    if not rows:
        raise HTTPException(status_code=404, detail="해당 지표 데이터 없음")
    return rows[0]


@app.get("/dq/series")
def series(
    stage: str = Query(...),
    metric: str = Query(...),
    days: int = Query(29, ge=1, le=365, description="조회 기간(일)"),
):
    """(stage, metric)의 최근 N일 시계열. 추세 timeseries 패널용."""
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
):
    """최근 원시 행. 표/드릴다운용. stage·metric은 선택 필터."""
    return _query(
        """
        SELECT batch_date, run_id, stage, metric_name, metric_value, target_table, created_at
        FROM dq
        WHERE (? IS NULL OR stage = ?)
          AND (? IS NULL OR metric_name = ?)
        ORDER BY created_at DESC
        LIMIT ?
        """,
        [stage, stage, metric, metric, limit],
    )
