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
