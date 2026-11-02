# 배포 절차 (아직 해 보지 않은 초안)

Azure VM 한 대에 컨테이너 넷을 띄운다. 큰 그림은 설계 문서
[11 서버 아키텍처](https://github.com/seominji58/drawtale-pt_docs/blob/main/11-server-architecture.md)에 있다.

> **이 절차는 한 번도 돌려 보지 않았다.** 해 보면 빠진 것이 나온다.
> 막히는 곳과 실제로 걸린 시간을 이 파일에 적어 가며 고친다.

## 미리 준비해야 하는 것 (사람이 해야 하는 일)

| 무엇 | 비고 |
|---|---|
| **Azure VM** | 메모리 **16GB 급**, Ubuntu. Docker 와 Docker Compose 설치 |
| **VM 공인 IP 를 DB 방화벽에 등록** | Azure Database for PostgreSQL → 네트워킹 |
| Blob 컨테이너 | `drawtale-prod` 를 **비공개**로 만든다 |
| 도메인 | `drawtale.kr` (가비아). `api` A 레코드는 VM 공인 IP, `www` CNAME 은 Static Web Apps 주소 |
| 프론트 배포 | Azure Static Web Apps. `VITE_API_BASE=https://api.drawtale.kr/api/v1` |

## 순서

```bash
# 1. VM 에 두 저장소를 나란히 받는다 (로컬과 같은 구조)
mkdir -p ~/drawtale && cd ~/drawtale
git clone https://github.com/seominji58/drawtale-backend.git
git clone https://github.com/seominji58/drawtale-ai.git

# 2. 설정값을 넣는다
cd drawtale-backend/deploy
cp .env.prod.example .env.prod
nano .env.prod          # 비밀값은 저장소에 없다. 손으로 넣는다

# 3. 띄운다 (torchserve 이미지 빌드가 오래 걸린다 — 아래 참고)
docker compose -f docker-compose.prod.yml up -d --build

# 4. 올라왔는지 본다
docker compose -f docker-compose.prod.yml ps
# backend · ai 이미지에는 curl 이 없어 파이썬으로 부른다
docker compose -f docker-compose.prod.yml exec backend python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/health').read().decode())"
docker compose -f docker-compose.prod.yml exec ai python -c "import urllib.request as u; print(u.urlopen('http://localhost:8001/internal/v1/health').read().decode())"
```

마지막 줄의 `model_loaded` 가 `true` 여야 한다. `false` 면 `torchserve` 가 아직 모델을
올리는 중이거나 메모리가 모자란 것이다.

## 로컬 리허설 기록 (2026-10-13)

VM 없이 **이 compose 파일 그대로** 로컬에서 `ai` · `torchserve` 를 띄워 끝까지 돌려 봤다.

| 무엇 | 결과 |
|---|---|
| compose 유효성 | **통과.** 포트가 `caddy` 에만 있다 |
| 컨테이너 안에서 `/internal/v1/health` | **`model_loaded: true`** |
| **호스트에서 8001 · 8080** | **연결 안 됨** (의도한 대로) |
| 분석 (검출 + 관절 15개 + 마스크) | **1.8초**, 검출 신뢰도 0.998 |
| **렌더 (MP4)** | **47.5초**, 695KB |
| `ai` 최대 메모리 | **1,089 MiB** |
| `torchserve` 최대 메모리 | **1,895 MiB** |

**알게 된 것 둘**

1. **렌더가 문서보다 느리다.** 35~40초로 적어 뒀는데 **47.5초**였다. 그것도 20코어 PC 에서다.
   VM 이 느리면 더 걸린다. 그래서 `AI_TIMEOUT_SECONDS` 를 **120** 으로 올렸다 (전에 60)
2. **메모리는 생각보다 적게 쓴다.** 둘 합쳐 **3GB 안팎**이다.
   `torchserve` 상한을 8GB 로 잡았는데 3GB 로 줄였다. **VM 을 16GB 로 잡지 않아도 될 수 있다**

> 그림 한 장 · 동작 하나 · PC 한 대에서 잰 값이다. **VM 에서 다시 재야 한다.**

## 로컬 리허설 기록 (2026-11-02)

`caddy` · `backend` 를 임시 Postgres 에 붙여 돌렸다. `ai` · `torchserve` · Azure · 외부 API 는 빠졌다.

| 무엇 | 결과 |
|---|---|
| `Caddyfile` | **켜지지 않던 오류를 고쳤다.** `timeouts` 를 전역 설정으로 옮겨 검증 통과 |
| Caddy → backend `/health` | **200** (HTTPS 대신 `:80` 으로 시험) |
| 마이그레이션 | 빈 DB 에서 `b1c2d3e4f5a6` 까지 통과 |
| 카드 이야기 씨앗 | **2,048편**, 두 번째 실행은 그대로 |
| `backend` 메모리 | 씨앗을 넣은 뒤 123 MiB |

## 꼭 확인할 것

| 무엇 | 왜 | 어떻게 |
|---|---|---|
| **AI 포트가 바깥에 열려 있지 않은지** | AI 서버에는 **로그인 검사가 없다.** 열리면 누구나 영상을 만든다 | 다른 PC 에서 `curl http://VM주소:8001/internal/v1/health` → **연결되면 안 된다** |
| **메모리가 16GB 에 들어가는지** | 재 본 적이 없다. `mem_limit` 은 추정값이다 | `docker stats` 를 보며 끝까지 한 번 돌려 본다 |
| **렌더가 타임아웃 안에 끝나는지** | 35~40초인데 한계가 60초다 | 이야기를 몇 번 만들어 보고 `jobs` 에 `failed` 가 없는지 |
| CORS | 로컬 주소가 남아 있으면 프론트에서 안 된다 | 브라우저 콘솔 |
| **API 문서가 닫혔는지** (2026-11-02) | 구조가 그대로 보인다 | `https://api.도메인/docs` → 404 (`APP_ENV=prod` 면 저절로) |
| **파일 주소가 서명 없이 안 열리는지** (2026-11-02) | 아이 원본 그림 · 영상이 주소만으로 열리면 안 된다. `.env.prod` 에 `FILE_URL_SECRET` 이 없으면 서버를 다시 켤 때마다 예전 주소가 안 열린다 | 응답의 파일 주소에서 `?exp=&sig=` 를 떼고 열어 본다 → 404 여야 한다 |
| 그림이 Blob 에 올라가는지 | 로컬 디스크면 컨테이너를 지울 때 사라진다 | Azure 포털에서 `uploads/` 확인 |

## 미리 알아 둘 것

**`torchserve` 이미지 빌드가 오래 걸린다.** conda 설치, mmcv 빌드, 모델 두 개 내려받기가
들어 있어 **수십 분**이 걸릴 수 있다. VM 사양이 낮으면 더 걸린다.
처음 한 번은 `--build` 로 돌려 두고 기다리는 편이 낫다.

**마이그레이션은 공용 DB 를 건드린다.** `backend` 컨테이너가 뜰 때
`alembic upgrade head` 를 돌리므로, **올리기 전에 팀에 알린다.**

**카드 이야기 씨앗.** 마이그레이션 뒤 한 번 넣는다 (몇 번 돌려도 같다):
`docker compose -f docker-compose.prod.yml exec backend python -m app.seed.card_stories`
— 저장소의 `app/seed/card_stories.jsonl.gz`(통과한 2,048편)를 `card_stories` 표에 넣는다.

## 아직 정하지 않은 것

| 무엇 | |
|---|---|
| 이미지를 레지스트리로 보낼지 | 지금 절차는 **VM 에서 직접 빌드**한다. 느리지만 단계가 적다 |
| 비밀값을 VM 에 넣는 방법 | 지금은 손으로 `nano`. Azure 쪽 설정값으로 옮길지 |
| 로그를 모을지 | 지금은 `docker compose logs`. 컨테이너를 지우면 사라진다 |
| 백업 | DB · Blob 둘 다 없다 |
