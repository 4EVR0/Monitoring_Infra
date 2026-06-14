# 공용 모니터링 스택 (/srv/monitoring)

기존 `/home/ubuntu/monitoring` 에서 이전됨 (2026-06-10). 소유 그룹: `adv` (setgid).

## 구성
- **Prometheus** `:9090` — 이미지 `prom/prometheus:v3.11.3` (태그 고정)
- **Grafana** `:3000` — 이미지 `grafana/grafana:13.0.1` (태그 고정), admin/admin
- 데이터는 named volume 으로 영속화: `monitoring_grafana-data`, `monitoring_prometheus-data`
  - ⚠️ 이전엔 Grafana 가 볼륨 없이 떠 있어서 컨테이너 재생성 시 대시보드가 전부 날아가는 상태였음. 이제 영속화됨.

## 운영
```bash
cd /srv/monitoring
docker-compose ps
docker-compose up -d        # 기동
docker-compose logs -f grafana
```

⚠️ **docker-compose v1(1.29.2) 버그 주의**: 기존 컨테이너가 떠 있는 상태에서 `up`(재생성)을
하면 `KeyError: ContainerConfig` 로 죽음. 그 경우:
```bash
docker rm -f monitoring_grafana_1 monitoring_prometheus_1   # 데이터(볼륨)는 안 지워짐
cd /srv/monitoring && docker-compose up -d                  # 새로 생성
```
→ 근본 해결은 Docker Compose v2 플러그인으로 업그레이드 권장.

## 스크레이프 대상 (prometheus.yml)
- `vllm`: vast-gpu-server-2.tailb70036.ts.net:18000  (vLLM 메트릭, GPU 서버 꺼지면 down)
  - GPU 하드웨어 지표(사용률/온도/VRAM)는 GPU 서버에 dcgm-exporter 추가 필요.
- TODO: EC2 airflow, EC2 neo4j(GraphDB), node_exporter, Loki

## 백업
`/srv/monitoring/backup/` 에 이전 시점 스냅샷(grafana-data, prometheus-data, dashboards-json) 보관.
