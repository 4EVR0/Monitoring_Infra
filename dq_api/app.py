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
from functools import lru_cache

import duckdb
from fastapi import FastAPI, HTTPException, Query
from pyiceberg.catalog import load_catalog

DQ_METRICS_TABLE = os.environ.get("DQ_METRICS_TABLE", "oliveyoung_db.dq_metrics")
AWS_REGION = os.environ.get("AWS_REGION", "ap-northeast-2")

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


def _query(sql: str, params: list) -> list[dict]:
    """dq_metrics 전체를 arrow로 읽어 DuckDB로 질의 → dict 목록 반환.

    (run당 몇 행짜리 초소형 테이블이라 매 요청 full scan해도 무방)
    """
    arrow = _catalog().load_table(DQ_METRICS_TABLE).scan().to_arrow()
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
    return {"status": "ok", "table": DQ_METRICS_TABLE}


@app.get("/dq/latest")
def latest(
    stage: str = Query(..., description="crawl | bronze_to_silver | silver_to_gold"),
    metric: str = Query(..., description="지표명 (match_rate 등)"),
):
    """해당 (stage, metric)의 최신 run 값 1건. 점수판 stat 타일용."""
    rows = _query(
        """
        SELECT metric_value, batch_job, target_table, created_at
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
        SELECT created_at, metric_value, batch_job
        FROM dq
        WHERE stage = ? AND metric_name = ?
          AND created_at >= now() - INTERVAL '{int(days)}' DAY
        ORDER BY created_at
        """,
        [stage, metric],
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
        SELECT batch_job, stage, metric_name, metric_value, target_table, created_at
        FROM dq
        WHERE (? IS NULL OR stage = ?)
          AND (? IS NULL OR metric_name = ?)
        ORDER BY created_at DESC
        LIMIT ?
        """,
        [stage, stage, metric, metric, limit],
    )
