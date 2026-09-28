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

# 3. 로컬 DB (Docker Desktop 필요)
docker compose -f docker-compose.local.yml up -d db
alembic upgrade head

# 4. 서버 실행
uvicorn app.main:app --reload
```

macOS / Linux: `python3.11 -m venv .venv` → `source .venv/bin/activate`, `cp .env.example .env`

- http://127.0.0.1:8000/health
- http://127.0.0.1:8000/docs

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
