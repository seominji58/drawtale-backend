#!/bin/sh
# DB 를 하루 한 번 덤프한다. VM 의 cron 이 부른다 (README 「백업」).
# 같은 디스크에 두므로 잘못된 마이그레이션 · 실수로 지운 자료는 되살리지만, VM 이 사라지면 함께 사라진다.
set -eu

KEEP_DAYS=7
DIR="$HOME/backups"
mkdir -p "$DIR"
cd "$HOME/drawtale/drawtale-backend/deploy"

sudo docker compose -f docker-compose.prod.yml exec -T db pg_dump -U drawtale -Fc drawtale \
	>"$DIR/drawtale-$(date +%Y%m%d).dump"
find "$DIR" -name 'drawtale-*.dump' -mtime +"$KEEP_DAYS" -delete
