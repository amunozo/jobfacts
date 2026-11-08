#!/usr/bin/env bash
# Copy the LOCAL development database to the server, replacing the server's database.
# For the first deploy (or to reset the server to your local data). Run on your own machine:
#   deploy/push-db.sh ubuntu@203.0.113.7 [path/to/ssh-key.pem]
# The local database is the compose one at the repository root (`docker compose up -d`).
# Environment overrides: REMOTE_DIR (repository folder on the server, default jobfacts),
# SSH_CMD and SCP_CMD (the programs used to reach the server, default ssh and scp).
set -euo pipefail

TARGET="${1:?usage: deploy/push-db.sh USER@SERVER [SSH_KEY_FILE]}"
KEY="${2:-}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_DIR="${REMOTE_DIR:-jobfacts}"
SSH_CMD="${SSH_CMD:-ssh}"
SCP_CMD="${SCP_CMD:-scp}"
opts=()
[ -n "$KEY" ] && opts=(-i "$KEY")

echo "This REPLACES the database on $TARGET with your local database."
echo "Whatever the server collected since the last push is lost (it keeps nothing else)."
read -r -p "Type 'overwrite' to continue: " answer
[ "$answer" = "overwrite" ] || { echo "cancelled"; exit 1; }

dump="$(mktemp --suffix=.dump)"
remote_dirty=0 # 1 while a copy of the dump may be on the server
# Runs on every way out (success, failure, Ctrl-C): `set -e` would otherwise skip any cleanup
# written after the failing command. The exit status of the script is kept.
cleanup() {
	rm -f "$dump"
	if [ "$remote_dirty" = 1 ]; then
		"$SSH_CMD" "${opts[@]}" "$TARGET" "rm -f $REMOTE_DIR/.push-db.dump" || true
	fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

echo "1/4 dumping the local database"
(cd "$ROOT" && docker compose exec -T db pg_dump -U jobfacts -d jobfacts -Fc) >"$dump"
[ -s "$dump" ] || { echo "the local dump is empty (is the local database up?)" >&2; exit 1; }

echo "2/4 copying $(du -h "$dump" | cut -f1) to the server"
remote_dirty=1
"$SCP_CMD" "${opts[@]}" "$dump" "$TARGET:$REMOTE_DIR/.push-db.dump"

echo "3/4 restoring on the server (the app is stopped for a moment)"
# The remote script: first a backup of what is there (abort if it fails), then DROP and restore.
# shellcheck disable=SC2016 # the variables are meant to expand on the server, not here
status=0
"$SSH_CMD" "${opts[@]}" "$TARGET" "set -uo pipefail; cd $REMOTE_DIR
dc() { if docker info >/dev/null 2>&1; then deploy/dc \"\$@\"; else sudo deploy/dc \"\$@\"; fi; }
dc up -d db --wait || { rm -f .push-db.dump; exit 1; }
# Is there data to protect? (the first deploy has an empty database: no backup needed)
has_data=\$(printf %s \"select count(*) from pg_tables where tablename = 'postings'\" | dc exec -T db sh -c 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tA' 2>/dev/null | tr -d '[:space:]')
if [ \"\$has_data\" = 1 ]; then
  echo 'backing up the server database before replacing it'
  if docker info >/dev/null 2>&1; then deploy/backup.sh; else sudo env HOME=\"\$HOME\" deploy/backup.sh; fi || {
    echo 'the backup failed: nothing was changed. Fix it and run push-db.sh again.' >&2
    rm -f .push-db.dump; exit 1; }
else
  echo 'the server database is empty or missing: no backup needed'
fi
dc stop app
dc exec -T db sh -c 'psql -U \"\$POSTGRES_USER\" -d postgres -v ON_ERROR_STOP=1 \\
-c \"DROP DATABASE IF EXISTS \$POSTGRES_DB WITH (FORCE)\" -c \"CREATE DATABASE \$POSTGRES_DB\"' || { rm -f .push-db.dump; exit 1; }
if ! dc exec -T db sh -c 'pg_restore -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" --no-owner --exit-on-error' < .push-db.dump; then
  rm -f .push-db.dump
  {
    echo 'RESTORE FAILED: the server database is now incomplete and the app is stopped.'
    echo 'To recover, on the server, restore the backup just taken (newest file):'
    echo '  ls -t ~/jobfacts-backups/jobfacts-*.dump | head -1'
    echo '  then follow \"Restore for real\" in docs/deploy.md, and start the app: deploy/dc up -d'
    echo '(on a first deploy there is no backup: fix the cause and run push-db.sh again)'
  } >&2
  exit 1
fi
rm -f .push-db.dump
dc run --rm -T app jobfacts migrate || exit 1
dc up -d --wait --wait-timeout 180" || status=$?
if [ "$status" -ne 0 ]; then
	# The exit trap removes the temporary dump on the server (the remote script may have failed
	# before reaching its own rm).
	echo "push-db failed (see above). The temporary dump on the server is being removed." >&2
	exit "$status"
fi
remote_dirty=0 # the remote script removed the dump itself

echo "4/4 done. Check:  curl https://YOUR-DOMAIN/health"
