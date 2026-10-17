#!/usr/bin/env bash
# 모니터링 서버 배포 — GitHub Actions(SSM)가 git pull 직후 실행한다.
#   사용: scripts/deploy.sh <pull 이전 SHA>
#   pull은 workflow가 레포 소유자 권한으로 먼저 하고, 이 스크립트는 바뀐 파일만 보고 반영한다.
#   비밀(.env)은 읽거나 출력하지 않는다(public 레포 Actions 로그).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"
BEFORE="${1:-}"
AFTER="$(git -c safe.directory="$REPO_DIR" rev-parse HEAD)"

echo "[deploy] $REPO_DIR  ${BEFORE:-(없음)} -> $AFTER"
if [[ -n "$BEFORE" && "$BEFORE" != "$AFTER" ]]; then
  CHANGED="$(git -c safe.directory="$REPO_DIR" diff --name-only "$BEFORE" "$AFTER")"
else
  CHANGED=""
fi
echo "[deploy] 바뀐 파일:"; echo "${CHANGED:-  (없음)}" | sed 's/^/  /'

changed() { grep -qE "$1" <<<"$CHANGED"; }

# 컨테이너 정의·dq_api 이미지 변경 반영(바뀐 게 없으면 그대로 둠)
docker compose up -d --build

# 마운트된 설정은 compose가 변경을 감지하지 못한다 → 해당 서비스만 재시작
if changed '^config/provisioning/'; then echo "[deploy] 알림 provisioning 변경 → grafana 재시작"; docker compose restart grafana; fi
if changed '^prometheus\.yml$';      then echo "[deploy] prometheus.yml 변경 → prometheus 재시작"; docker compose restart prometheus; fi
if changed '^loki-config\.yml$';     then echo "[deploy] loki-config.yml 변경 → loki 재시작"; docker compose restart loki; fi

# 헬스체크 — 재시작 직후라 잠깐 기다리며 재시도
check() {
  local name="$1" url="$2"
  for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null --max-time 10 "$url"; then echo "[health] $name OK"; return 0; fi
    sleep 5
  done
  echo "[health] $name 실패: $url" >&2; return 1
}
fail=0
check grafana    http://localhost:3000/api/health || fail=1
check prometheus http://localhost:9090/-/ready    || fail=1
check dq_api     http://localhost:8000/health     || fail=1   # 실제 S3 스캔이라 수 초 걸림

docker compose ps --format 'table {{.Service}}\t{{.Status}}'
if (( fail )); then echo "[deploy] 헬스체크 실패" >&2; exit 1; fi
echo "[deploy] 완료 $AFTER"
