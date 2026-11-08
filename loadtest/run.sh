#!/usr/bin/env bash
# The load test of docs/loadtest.md, in one command, on your own computer:
#   loadtest/run.sh                    # 1, 10, 25 and 50 users, 60 s each after the ramp-up
#   USERS="10 50" STEP_SECONDS=30 loadtest/run.sh
# It copies the repository to a scratch folder, starts the PRODUCTION stack from there (the same
# deploy/compose.prod.yml: database, app, Caddy with HTTPS) with its own fake .env, loads the
# data of the local development database (the compose one at the repository root, which must
# be running), warms the query-embedding cache, runs Locust at each user count and removes
# everything again. Needs: docker, uv, taskset, and MISTRAL_API_KEY in the repository's .env
# (hybrid searches embed the query; the cache keeps this to one API call per distinct phrase).
# Results (Locust CSV files, the log of each step, container memory samples): RESULTS_DIR.
# Nothing is printed or stored of any key or password; the scratch folder is deleted at the end.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USERS="${USERS:-1 10 25 50}"
STEP_SECONDS="${STEP_SECONDS:-60}" # measured seconds per step, after the ramp-up
SPAWN_RATE="${SPAWN_RATE:-5}"      # new users per second while ramping up
HTTP_PORT="${HTTP_PORT:-18080}"    # not 80/443, which may be in use on a computer
HTTPS_PORT="${HTTPS_PORT:-18443}"
# The stack gets two CPU threads (the Lightsail 1 GB plan has 2 vCPUs); Locust runs on others.
# On the author's 12-thread laptop, 4-5 are two separate efficiency cores and 0-3 the
# performance cores. Change both for another computer (see `lscpu -e`).
STACK_CPUS="${STACK_CPUS:-4,5}"
LOCUST_CPUS="${LOCUST_CPUS:-0-3}"
RESULTS_DIR="${RESULTS_DIR:-$ROOT/loadtest/results/$(date +%F)}"
PROJECT=jobfacts-loadtest
IMAGE=jobfacts-loadtest:latest

SCRATCH="$(mktemp -d)"
sampler_pid=""
dc() {
	docker compose -p "$PROJECT" --project-directory "$SCRATCH" \
		-f "$SCRATCH/deploy/compose.prod.yml" -f "$SCRATCH/loadtest/compose.loadtest.yml" "$@"
}
cleanup() {
	set +e
	[ -n "$sampler_pid" ] && kill "$sampler_pid" 2>/dev/null
	echo "cleaning up: containers, volumes, image, scratch folder"
	dc down -v --remove-orphans >/dev/null 2>&1
	docker image rm "$IMAGE" >/dev/null 2>&1
	rm -rf "$SCRATCH"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

mkdir -p "$RESULTS_DIR"
export LOADTEST_STACK_CPUS="$STACK_CPUS"

echo "1/6 copying the repository to a scratch folder"
(cd "$ROOT" && git ls-files -z --cached --others --exclude-standard -- \
	Dockerfile pyproject.toml uv.lock src config migrations deploy loadtest ':!loadtest/results' |
	tar --null -T - -cf -) | tar -C "$SCRATCH" -xf -

# A fake .env: temporary passwords, the real Mistral key copied without printing it.
password="$(openssl rand -hex 16)"
{
	echo "POSTGRES_PASSWORD=$(openssl rand -hex 12)"
	echo "JOBFACTS_WEB_PASSWORD=$password"
	echo "JOBFACTS_DOMAIN=localhost"
	echo "HTTP_PORT=$HTTP_PORT"
	echo "HTTPS_PORT=$HTTPS_PORT"
	grep -E '^MISTRAL_API_KEY=.+' "$ROOT/.env" ||
		{ echo "no MISTRAL_API_KEY in $ROOT/.env" >&2; exit 1; }
} >"$SCRATCH/.env"
chmod 600 "$SCRATCH/.env"

echo "2/6 building the image and starting the database"
dc build app >"$RESULTS_DIR/build.log" 2>&1
dc up -d db --wait >/dev/null

echo "3/6 loading the development database into the scratch database"
dump="$SCRATCH/dev.dump"
(cd "$ROOT" && docker compose exec -T db pg_dump -U jobfacts -d jobfacts -Fc) >"$dump"
[ -s "$dump" ] || { echo "the dump is empty (is the development database up?)" >&2; exit 1; }
# shellcheck disable=SC2016 # the variables are meant to expand inside the container
dc exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error' <"$dump"
rm -f "$dump"
dc run --rm -T app jobfacts migrate
dc up -d --wait --wait-timeout 180 >/dev/null

base="https://localhost:$HTTPS_PORT"
export JOBFACTS_WEB_PASSWORD="$password" JOBFACTS_INSECURE_TLS=1
{
	echo "date: $(date --iso-8601=seconds)"
	echo "cpu: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ //')"
	echo "stack cpus: $STACK_CPUS; locust cpus: $LOCUST_CPUS"
	for c in $(dc ps -q); do
		docker inspect -f '{{.Name}} cpuset={{.HostConfig.CpusetCpus}} memory={{.HostConfig.Memory}}' "$c"
	done
	echo "health: $(curl -sk "$base/health")"
	# shellcheck disable=SC2016 # expanded inside the container
	dc exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -c "select model, count(*) from posting_embeddings group by 1 order by 1"'
} >"$RESULTS_DIR/setup.txt"
cat "$RESULTS_DIR/setup.txt"

echo "4/6 warming the cache (one request per phrase)"
uv run --project "$ROOT" python "$ROOT/loadtest/warmup.py" "$base" | tee "$RESULTS_DIR/warmup.txt"

echo "5/6 load steps: $USERS users"
for n in $USERS; do
	ramp=$(((n + SPAWN_RATE - 1) / SPAWN_RATE))
	echo "--- $n users (ramp-up $ramp s, then $STEP_SECONDS s measured)"
	(while :; do
		# shellcheck disable=SC2046 # one container id per word is wanted
		docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.CPUPerc}}' $(dc ps -q) >>"$RESULTS_DIR/docker_stats_$n.txt"
		sleep 3
	done) &
	sampler_pid=$!
	taskset -c "$LOCUST_CPUS" uv run --project "$ROOT" locust -f "$ROOT/loadtest/locustfile.py" \
		--headless --host "$base" -u "$n" -r "$SPAWN_RATE" --reset-stats \
		--run-time "$((ramp + STEP_SECONDS))s" --csv "$RESULTS_DIR/users_$n" \
		>"$RESULTS_DIR/users_$n.log" 2>&1 || echo "locust exited with $? (failed requests?)"
	kill "$sampler_pid" 2>/dev/null || true
	wait "$sampler_pid" 2>/dev/null || true
	sampler_pid=""
	dc logs app --since "$((ramp + STEP_SECONDS + 20))s" 2>&1 | grep -v ' 200 OK' |
		tail -n 30 >"$RESULTS_DIR/app_log_$n.txt" || true
	sleep 10
done

echo "6/6 the table"
uv run --project "$ROOT" python "$ROOT/loadtest/summarize.py" "$RESULTS_DIR" | tee "$RESULTS_DIR/table.md"
