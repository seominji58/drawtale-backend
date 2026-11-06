"""로컬 시험 계정으로 로그인 (pt_docs 20 4-4, 2026-11-06).

카카오 · 구글 로그인 없이 실제 모드(백엔드 · AI 를 띄운 상태)로 화면을 시험할 때 쓴다. 전에는 시험할 때마다
컨테이너에서 토큰을 손으로 발급해 브라우저 저장소에 넣었다. 시험 계정은 하나(consent_version = "dev-login")를
다시 쓰고, 부를 때마다 새 토큰을 발급해 프론트 개발 서버 주소로 알려 준다.
프론트는 개발 서버(npm run dev)에서만 주소의 `dev_token` 을 받는다 — 운영 빌드에는 그 코드가 없다.

**APP_ENV 가 local 이 아니면 아무것도 하지 않는다** (운영 · 공유 서버에서 비밀번호 없는 계정을 만들지 않게).

    docker exec drawtale-backend-git-backend-1 python -m scripts.dev_login
    docker exec drawtale-backend-git-backend-1 python -m scripts.dev_login --pin 2580 --onboarded   # 보호자 비밀번호 · 소개 카드 본 상태로
    docker exec drawtale-backend-git-backend-1 python -m scripts.dev_login --fresh                  # 비밀번호 · 소개 카드를 처음 상태로
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.core.auth import issue_token
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import User
from app.models.mixins import utcnow
from app.services import guardian_pin

MARK = "dev-login"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="로컬 시험 계정 토큰 발급")
    ap.add_argument("--pin", help="보호자 비밀번호(숫자 4자리)를 이걸로 정해 둔다")
    ap.add_argument("--onboarded", action="store_true", help="보호자 소개 카드를 본 것으로")
    ap.add_argument("--fresh", action="store_true", help="보호자 비밀번호 · 소개 카드를 처음 상태로")
    ap.add_argument("--front", default="http://localhost:5173", help="프론트 개발 서버 주소")
    args = ap.parse_args(argv)

    settings = get_settings()
    if settings.app_env.lower() != "local":
        print(f"APP_ENV={settings.app_env} — 로컬(local)에서만 쓴다. 아무것도 하지 않았다.", file=sys.stderr)
        return 2
    if args.pin is not None and not (args.pin.isdigit() and len(args.pin) == 4):
        print("--pin 은 숫자 4자리", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.consent_version == MARK).order_by(User.created_at))
        made = user is None
        if made:
            user = User(terms_agreed_at=utcnow(), consent_version=MARK)
            db.add(user)
            db.flush()
        if args.fresh:
            user.guardian_pin_hash = None
            user.guardian_pin_failures = 0
            user.guardian_pin_locked_until = None
            user.onboarded_at = None
        if args.pin:
            user.guardian_pin_hash = guardian_pin.make_hash(args.pin)
            user.guardian_pin_failures = 0
            user.guardian_pin_locked_until = None
        if args.onboarded:
            user.onboarded_at = user.onboarded_at or utcnow()
        token = issue_token(db, user)
        db.commit()
        state = (
            f"보호자 비밀번호 {'있음' if user.guardian_pin_hash else '없음'}, "
            f"소개 카드 {'봄' if user.onboarded_at else '안 봄'}"
        )
        print(f"시험 계정 {'새로 만듦' if made else '다시 씀'} ({user.id}) — {state}")
    print(f"브라우저로 열기: {args.front.rstrip('/')}/?dev_token={token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
