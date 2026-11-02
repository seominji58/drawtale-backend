#!/bin/sh
# GitHub Actions 가 부르는 배포 입구. VM 에서 ~/remote-deploy.sh 로 둔다.
#
# 배포 전용 키는 authorized_keys 의 command= 로 이 스크립트만 돌 수 있다.
# 무엇을 올릴지는 ssh 뒤에 붙인 낱말(backend · ai · frontend)로, 코드는 표준 입력의 tar.gz 로 받는다.
#   git archive --format=tar.gz HEAD | ssh azureuser@VM backend
set -eu

TARGET="${SSH_ORIGINAL_COMMAND:-}"
ROOT="$HOME/drawtale"
DEPLOY="$ROOT/drawtale-backend/deploy"

case "$TARGET" in
backend | ai | frontend) ;;
*)
	echo "알 수 없는 대상: $TARGET (backend · ai · frontend)" >&2
	exit 2
	;;
esac

# 저장소 셋이 동시에 올라와도 하나씩 한다
exec 9>"$HOME/.deploy.lock"
flock 9

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
tar -xzf - -C "$TMP"

cd "$DEPLOY"
compose() { sudo docker compose -f docker-compose.prod.yml "$@"; }

# 컨테이너를 새로 만들면 uvicorn 이 뜨기까지 몇 초 걸린다. 그동안은 다시 묻는다
health() {
	for _ in $(seq 1 30); do
		if compose exec -T "$1" python -c \
			"import urllib.request as u; print(u.urlopen('$2').read().decode())" 2>/dev/null; then
			return 0
		fi
		sleep 2
	done
	echo "$1 가 응답하지 않는다" >&2
	return 1
}

case "$TARGET" in
backend)
	# 비밀값과 화면 빌드 결과는 저장소에 없으므로 지우지 않는다
	rsync -a --delete --exclude deploy/.env --exclude deploy/.env.prod --exclude deploy/www \
		"$TMP"/ "$ROOT/drawtale-backend/"
	cp "$DEPLOY/remote-deploy.sh" "$HOME/remote-deploy.sh"
	compose up -d --build db backend
	# rsync 가 Caddyfile 을 새 파일로 바꾸면 띄워 둔 컨테이너는 옛 파일을 본다. reload 로는 안 바뀐다
	compose up -d --force-recreate caddy
	health backend http://localhost:8000/health
	compose exec -T backend python -m app.seed.card_stories
	;;
ai)
	rsync -a --delete "$TMP"/ "$ROOT/drawtale-ai/"
	compose up -d --build torchserve ai
	health ai http://localhost:8001/internal/v1/health
	;;
frontend)
	mkdir -p "$DEPLOY/www"
	rsync -a --delete "$TMP"/ "$DEPLOY/www/"
	echo "화면 $(find "$DEPLOY/www" -type f | wc -l)개 파일"
	;;
esac

sudo docker image prune -f >/dev/null
echo "배포 완료: $TARGET"
