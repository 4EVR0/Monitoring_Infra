# 베타 앱 EC2 관측 에이전트

앱 릴리스(`/srv/4evr0/releases/<sha>`, 4EVR0-Server `compose.beta.yml`)와 **분리된** compose 프로젝트(`4evr0-observe`).
Alloy가 앱 `proxy` 네트워크에 고정 IP(`.4`)로 붙어 수집하고 모니터링 서버(Tailscale)로 push한다. 호스트 포트 없음.

| 수집 | job | 비고 |
|---|---|---|
| 앱 `/metrics` | `beta-app` | 15초 |
| node-exporter | `beta-node` | 디스크·메모리·노드 끊김 알림(S1~S4) |
| `/ready` 합성 확인 | `beta-probe` | 60초, `probe_success` (DB·Redis·Neo4j·GPU) |
| app·caddy·redis 로그 | Loki `job=beta-app` | 라벨 `service`, graph_query는 `func` |

모든 메트릭에 `host=beta-app`.

## 전제
- EC2가 모니터링 서버와 같은 tailnet(`tailb70036`)에 가입
- 앱 프로젝트 이름 `4evr0-beta`(→ 네트워크 `4evr0-beta_proxy`), 앱 `PROXY_SUBNET`의 `.4`는 비워 둠
  (다르면 `APP_COMPOSE_PROJECT`·`ALLOY_PROXY_IP` 환경변수로 지정)

## 설치 (1회)
```bash
sudo git clone --filter=blob:none --sparse https://github.com/4EVR0/Monitoring_Infra.git /srv/4evr0-observe
cd /srv/4evr0-observe && sudo git sparse-checkout set agents/beta-app && cd agents/beta-app
sudo cp observe.env.example observe.env && sudo chmod 600 observe.env
curl -fsS http://100.105.250.65:9090/-/ready && curl -fsS http://100.105.250.65:3100/ready   # 경로 확인
sudo docker network inspect 4evr0-beta_proxy --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'  # 기본 172.30.90.0/28
sudo docker compose up -d
sudo docker compose logs --tail 30 alloy   # level=error 없으면 정상
```

## 갱신 / 중지
```bash
cd /srv/4evr0-observe && sudo git pull && cd agents/beta-app && sudo docker compose up -d && sudo docker compose restart alloy
sudo docker compose stop     # 중지(앱에는 영향 없음)
```
앱 이미지 교체·재배포는 이 스택과 무관하다. 앱 재생성 후에도 같은 네트워크 이름이면 계속 수집한다.
