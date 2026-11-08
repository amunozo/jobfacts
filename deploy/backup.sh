#!/usr/bin/env bash
# Nightly backup: a pg_dump of the database in custom format, keeping the newest 7 dumps.
# Restore: see docs/deploy.md ("Backups").
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="${BACKUP_DIR:-$HOME/jobfacts-backups}"
KEEP="${BACKUP_KEEP:-7}"

mkdir -p "$DIR"
chmod 700 "$DIR"
stamp="$(date +%Y%m%d-%H%M%S)"
tmp="$DIR/.jobfacts-$stamp.dump.partial"
final="$DIR/jobfacts-$stamp.dump"
trap 'rm -f "$tmp"' EXIT

# shellcheck disable=SC2016 # $POSTGRES_* must expand inside the container, not here
# -T: no terminal. The user and database name come from the container's own environment.
"$ROOT/deploy/dc" exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"$tmp"
if [ ! -s "$tmp" ]; then
	echo "backup: the dump is empty" >&2
	exit 1
fi
mv "$tmp" "$final"
trap - EXIT

# shellcheck disable=SC2012 # the names are ours (jobfacts-DATE.dump), so ls is safe
# Keep the newest $KEEP dumps (names sort by date), delete the rest.
ls -1 "$DIR"/jobfacts-*.dump | sort | head -n -"$KEEP" | xargs -r rm -f --
kept="$(find "$DIR" -maxdepth 1 -name 'jobfacts-*.dump' | wc -l)"
echo "backup: wrote $final ($(du -h "$final" | cut -f1)); $kept kept"
