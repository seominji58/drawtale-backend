"""배포된 사이트 보안 점검 (2026-11-02).

아무 PC 에서나 돌린다 — 서버에 접속하지 않고 바깥에서 본다.

    python deploy/check_live.py                  # api.drawtale.kr · www.drawtale.kr
    python deploy/check_live.py https://api.example.com https://www.example.com

돈이 드는 요청(이야기 만들기 · 목소리 등록)은 보내지 않는다. 그림 올리기도 하지 않는다.
"""

import json
import sys
import urllib.error
import urllib.request

API = sys.argv[1] if len(sys.argv) > 1 else "https://api.drawtale.kr"
WWW = sys.argv[2] if len(sys.argv) > 2 else "https://www.drawtale.kr"


def get(url: str, method: str = "GET", headers: dict | None = None) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def main() -> int:
    results: list[tuple[bool, str, str]] = []

    def check(ok: bool, name: str, note: str = "") -> None:
        results.append((ok, name, note))

    code, h, body = get(f"{API}/health")
    check(code == 200, "API 가 응답한다", f"{code} {body[:60]!r}")
    env = json.loads(body or b"{}").get("env") if code == 200 else None
    check(env in ("prod", "production"), "APP_ENV 가 prod", f"env={env}")
    for name in (
        "x-content-type-options", "x-frame-options", "referrer-policy", "strict-transport-security"
    ):
        check(name in h, f"API 보안 헤더 {name}", h.get(name, "없음"))
    hidden = "uvicorn" not in h.get("server", "")
    check(hidden, "API 가 서버 이름을 숨긴다", h.get("server", ""))

    code, _, _ = get(f"{API}/docs")
    check(code == 404, "API 문서(/docs)가 닫혀 있다", str(code))
    code, _, _ = get(f"{API}/openapi.json")
    check(code == 404, "API 구조(/openapi.json)가 닫혀 있다", str(code))

    code, _, _ = get(f"{API}/files/uploads/check-live.png")
    check(code == 404, "서명 없는 /files 주소는 안 열린다", str(code))

    code, _, _ = get(f"{API}/api/v1/auth/logout", method="POST")
    check(code == 204, "로그아웃 API 가 있다 (새 코드가 올라갔는지)", str(code))

    code, h, _ = get(f"{API}/api/v1/me/voice", headers={"Origin": "https://evil.example"})
    allowed = h.get("access-control-allow-origin", "없음")
    check(allowed not in ("*", "https://evil.example"), "다른 사이트에서 API 를 못 부른다 (CORS)", allowed)

    code, h, _ = get(f"{WWW}/")
    check(code == 200, "화면이 응답한다", str(code))
    csp = h.get("content-security-policy", "")
    strict = "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    check(strict, "화면 CSP", csp[:60] or "없음")
    for name in (
        "x-content-type-options", "x-frame-options", "strict-transport-security", "permissions-policy"
    ):
        check(name in h, f"화면 보안 헤더 {name}", h.get(name, "없음"))

    width = max(len(n) for _, n, _ in results)
    for ok, name, note in results:
        print(f"{'통과' if ok else '실패'}  {name.ljust(width)}  {note}")
    failed = sum(1 for ok, _, _ in results if not ok)
    print(f"\n{len(results) - failed} / {len(results)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
