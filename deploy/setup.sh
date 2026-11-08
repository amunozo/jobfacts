#!/usr/bin/env bash
# One-time (and safe to repeat) setup of a fresh Ubuntu 24.04 server for jobfacts.
# Run as the normal user (ubuntu on Lightsail) from inside the cloned repository:
#   git clone https://github.com/amunozo/jobfacts.git ~/jobfacts && ~/jobfacts/deploy/setup.sh
# What it does: Docker, a 2 GB swap file, automatic security updates, the server's .env,
# the systemd timers (daily run, nightly backup), the containers, the database migrations.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
RUN_USER="$(id -un)"
SWAP_SIZE="${SWAP_SIZE:-2G}"

say() { printf '\n== %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "run as a normal user with sudo (for example ubuntu), not as root"
command -v sudo >/dev/null || die "sudo is needed"
# shellcheck disable=SC1091
. /etc/os-release
[ "${ID:-}" = "ubuntu" ] || die "this script is written for Ubuntu (found ${ID:-unknown})"
[ "${VERSION_ID:-}" = "24.04" ] || echo "warning: tested for Ubuntu 24.04, found ${VERSION_ID:-?}"

dc() {
	if docker info >/dev/null 2>&1; then "$ROOT/deploy/dc" "$@"; else sudo "$ROOT/deploy/dc" "$@"; fi
}

# --- .env helpers (builtins only, so a secret never appears in a process list) -------------
env_get() { # value of KEY in .env, without quotes
	local line value=""
	while IFS= read -r line || [ -n "$line" ]; do
		case $line in "$1="*) value=${line#*=} ;; esac
	done <"$ENV_FILE"
	value=${value#\'}
	printf '%s' "${value%\'}"
}
env_set() { # env_set KEY VALUE: replace the KEY= line (or append it); value in single quotes
	local key=$1 value=$2 line found=0 tmp
	case $value in *\'* | *$'\n'*) die "$key: quotes and line breaks are not allowed in a value" ;; esac
	tmp="$(mktemp "$ENV_FILE.XXXXXX")"
	while IFS= read -r line || [ -n "$line" ]; do
		if [[ $line == "$key="* ]]; then
			printf "%s='%s'\n" "$key" "$value"
			found=1
		else
			printf '%s\n' "$line"
		fi
	done <"$ENV_FILE" >"$tmp"
	[ "$found" -eq 1 ] || printf "%s='%s'\n" "$key" "$value" >>"$tmp"
	chmod 600 "$tmp"
	mv "$tmp" "$ENV_FILE"
}
ask() { # ask VAR "question" [secret]: read from the terminal, never echo secrets
	[ -r /dev/tty ] || die "needs a terminal to ask for $1; fill $ENV_FILE by hand and run again"
	if [ "${3:-}" = secret ]; then
		read -r -s -p "$2: " "$1" </dev/tty
		echo
	else
		read -r -p "$2: " "$1" </dev/tty
	fi
}
random() { openssl rand -hex "$1"; }

# --- 1. packages -------------------------------------------------------------------------
say "System packages"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl git openssl \
	unattended-upgrades

# --- 2. Docker Engine and the compose plugin, from Docker's own apt repository -------------
say "Docker"
if docker compose version >/dev/null 2>&1; then
	echo "docker compose already installed: $(docker compose version)"
else
	sudo install -m 0755 -d /etc/apt/keyrings
	sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
	sudo chmod a+r /etc/apt/keyrings/docker.asc
	arch="$(dpkg --print-architecture)"
	echo "deb [arch=$arch signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" |
		sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
	sudo apt-get update -y
	sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io \
		docker-buildx-plugin docker-compose-plugin
fi
sudo systemctl enable --now docker
if ! id -nG "$RUN_USER" | tr ' ' '\n' | grep -qx docker; then
	sudo usermod -aG docker "$RUN_USER"
	echo "added $RUN_USER to the docker group (takes effect at the next login; this script uses sudo until then)"
fi

# --- 3. swap: the 1 GB machine needs room for the daily job's peaks ----------------------------
say "Swap file ($SWAP_SIZE)"
if swapon --show=NAME --noheadings | grep -q .; then
	echo "swap already active:"
	swapon --show
else
	if [ ! -f /swapfile ]; then
		sudo fallocate -l "$SWAP_SIZE" /swapfile
		sudo chmod 600 /swapfile
		sudo mkswap /swapfile
	fi
	sudo swapon /swapfile
	grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi
# Prefer RAM for the programs and the page cache; use swap only under pressure.
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-jobfacts-swap.conf >/dev/null
sudo sysctl -q -p /etc/sysctl.d/99-jobfacts-swap.conf

# --- 4. automatic security updates -------------------------------------------------------
say "Unattended security upgrades"
sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF
sudo systemctl enable --now unattended-upgrades

# --- 5. code -----------------------------------------------------------------------------
say "Code"
if git -C "$ROOT" remote get-url origin >/dev/null 2>&1; then
	git -C "$ROOT" pull --ff-only
fi

# --- 6. the server's .env ----------------------------------------------------------------
say "Configuration ($ENV_FILE)"
if [ ! -f "$ENV_FILE" ]; then
	install -m 600 "$ROOT/.env.example" "$ENV_FILE"
	echo "created $ENV_FILE from .env.example"
fi
chmod 600 "$ENV_FILE"

if [ -z "$(env_get POSTGRES_PASSWORD)" ]; then
	env_set POSTGRES_PASSWORD "$(random 24)"
	echo "generated a database password (stored only in .env)"
fi
if [ -z "$(env_get JOBFACTS_WEB_PASSWORD)" ]; then
	ask pw "Password for the search page (at least 16 characters; empty = generate one)" secret
	if [ -z "$pw" ]; then
		pw="$(random 16)"
		echo "generated a page password; read it with:  grep JOBFACTS_WEB_PASSWORD $ENV_FILE"
	fi
	[ "${#pw}" -ge 16 ] || die "the page password must have at least 16 characters"
	env_set JOBFACTS_WEB_PASSWORD "$pw"
	unset pw
fi
if [ -z "$(env_get JOBFACTS_DOMAIN)" ]; then
	ip="$(curl -fsS --max-time 10 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
	default=""
	[ -z "$ip" ] || default="${ip//./-}.sslip.io"
	ask domain "Web address for the site [${default:-none}] (a domain pointing at this server, or the sslip.io name)"
	domain="${domain:-$default}"
	[ -n "$domain" ] || die "a domain is needed"
	env_set JOBFACTS_DOMAIN "$domain"
fi
for key in MISTRAL_API_KEY TYPESAFE_API_KEY OPENROUTER_API_KEY; do
	if [ -z "$(env_get "$key")" ]; then
		ask value "Paste $key (empty = skip; the daily run needs MISTRAL and TYPESAFE)" secret
		[ -z "$value" ] || env_set "$key" "$value"
		unset value
	fi
done
for key in MISTRAL_API_KEY TYPESAFE_API_KEY; do
	[ -n "$(env_get "$key")" ] || echo "warning: $key is empty in .env: fill it in before the first daily run"
done

# --- 7. systemd timers -------------------------------------------------------------------
say "Timers"
for unit in jobfacts-daily.service jobfacts-daily.timer jobfacts-backup.service jobfacts-backup.timer; do
	sed -e "s#/home/ubuntu/jobfacts#$ROOT#g" -e "s#^User=ubuntu#User=$RUN_USER#" \
		"$ROOT/deploy/$unit" | sudo tee "/etc/systemd/system/$unit" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now jobfacts-daily.timer jobfacts-backup.timer
systemctl list-timers 'jobfacts-*' --no-pager

# --- 8. containers and database ----------------------------------------------------------
say "Containers"
dc up -d --build --wait --wait-timeout 300
dc run --rm -T app jobfacts migrate
dc ps

domain="$(env_get JOBFACTS_DOMAIN)"
say "Done"
echo "Check:  curl https://$domain/health"
echo "(the first request can take a few seconds while Caddy gets the certificate)"
