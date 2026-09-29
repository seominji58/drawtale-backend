# drawtale-backend

DrawTale Backend (FastAPI) — **Python 3.11**

## 구조

```text
app/
├── main.py            # FastAPI 앱, CORS, 라우터 등록
├── core/config.py     # 환경변수 설정 (.env)
├── api/health.py      # GET /health
├── api/v1/router.py   # /api/v1 Public API
├── db/                # SQLAlchemy Base, Session
├── models/            # DB 모델 (Alembic autogenerate 대상)
├── schemas/           # Pydantic Request/Response
└── services/          # AI client, Blob, OpenAI, TTS
alembic/               # DB migration
tests/
```

## 로컬 실행

```powershell
# 1. 가상환경 (Windows)
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

# 2. 환경변수
copy .env.example .env

# 3. DB 준비 (둘 중 하나)
#   a) Azure 공유 DB: .env의 DATABASE_URL을 전달받은 drawtale_dev 주소로 변경
#   b) 로컬 DB: docker compose up -d db   (Docker Desktop 필요, 포트 5433)
alembic upgrade head

# 4. 서버 실행
uvicorn app.main:app --reload
```

macOS / Linux: `python3.11 -m venv .venv` → `source .venv/bin/activate`, `cp .env.example .env`

- http://127.0.0.1:8000/health
- http://127.0.0.1:8000/docs
- API 계약서: [docs/api-contract.md](docs/api-contract.md)
- **Frontend 담당자용 시작 가이드: [docs/프론트엔드-시작하기.md](docs/프론트엔드-시작하기.md)**

## Frontend 담당자용: Docker로 Backend 띄우기

Python 설치 없이 Docker Desktop만 있으면 된다. AI는 Mock으로 동작한다.

```bash
git pull
cp .env.example .env          # DATABASE_URL을 전달받은 Azure drawtale_dev 주소로 변경
docker compose --profile app up -d --build
# → http://127.0.0.1:8000/docs
```

- 코드가 바뀌면 `git pull` 후 같은 명령을 다시 실행한다. (시작할 때 migration이 자동 적용된다)
- 로그: `docker compose logs -f backend` · 종료: `docker compose --profile app down`
- Azure DB 연결이 안 되면 현재 IP가 방화벽에 없는 것이다. https://api.ipify.org 결과를 Backend 담당에게 전달한다.
- Frontend `.env`: `VITE_API_BASE_URL=http://127.0.0.1:8000`

## 이야기 음성 (TTS) 설정

`TTS_PROVIDER` 로 고른다. 키가 없으면 음성 없이 이야기와 애니메이션만 나간다.

- `openai` (기본): `OPENAI_API_KEY` (또는 `TTS_API_KEY`)
- `elevenlabs`: `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`(콘솔 Voices 에서 고른 목소리 id),
  `ELEVENLABS_MODEL_ID`(기본 `eleven_multilingual_v2` — 한국어 품질. 빠르고 싸게는 `eleven_flash_v2_5`)

두 업체 모두 같은 속도(0.9)로 읽는다. 키는 `.env` 에만 둔다.

## 소셜 로그인 설정

카카오·구글 로그인은 `.env` 의 `*_CLIENT_ID` · `*_CLIENT_SECRET` 이 있어야 동작한다
(없으면 `503 PROVIDER_NOT_CONFIGURED`). 각 개발자 콘솔에서:

- Redirect URI: `http://localhost:5173/auth/{kakao|google}/callback` (배포 주소도 따로)
- **동의 항목·scope 는 켜지 않는다** — 회원번호만 받는다 (구글은 `openid` 만)
- 시크릿은 Backend `.env` 에만 둔다. Frontend 에는 client id 만

API 는 [docs/api-contract.md](docs/api-contract.md) 2-7, 2-8.

## 자주 쓰는 명령

```bash
pytest                                              # 테스트
ruff check .                                        # 린트
alembic revision --autogenerate -m "add character"  # migration 생성 (검토 후 커밋)
alembic upgrade head                                # migration 적용
```

## 규칙

- 패키지 추가 시 `requirements.txt`에 버전 고정(`==`)해서 추가한다.
- 주소·키는 코드에 쓰지 않고 `.env`로 받는다. 새 변수는 `.env.example`에도 추가한다.
- 테이블 변경은 Alembic migration으로만 한다.
