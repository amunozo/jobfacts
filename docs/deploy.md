# Deploying jobfacts to a small server

This guide puts the search page and the API on one rented server, behind HTTPS and a
password, with the daily collection running there. The files are in `deploy/`, plus the
`Dockerfile` at the top of the repository. Nothing here needs a domain name to be bought.

The plan, as decided: one AWS Lightsail Linux instance (Ubuntu 24.04 LTS, x86_64), the
1 GB RAM plan, a budget of at most 10 EUR per month, everything in Docker Compose, the page
private behind HTTP Basic (`JOBFACTS_WEB_PASSWORD`).

## What each piece is

- **Server (instance):** a computer in an Amazon data centre that you rent by the month.
  Lightsail is Amazon's simple, fixed-price way to rent one.
- **Container:** a program started in its own small, isolated Linux environment, with its own
  files. If you delete the container, nothing on the server is changed.
- **Image:** the recipe-built package a container starts from (Python, our code and its
  libraries). The `Dockerfile` is the recipe. Building is done on the server.
- **Docker Compose:** one file (`deploy/compose.prod.yml`) that lists the containers, how they
  talk to each other, their memory limits and their disks, and starts them with one command.
- **Volume:** a disk folder managed by Docker that survives when a container is replaced. The
  database files and the certificates live in volumes.
- **Reverse proxy (Caddy):** the one program the internet talks to. It speaks HTTPS to the
  visitor and passes the request on to the app over the private Docker network. The app and
  the database have no public port at all.
- **TLS certificate:** the file that lets a browser trust that it is talking to your server
  and encrypts the traffic (the padlock). Caddy gets a free one from Let's Encrypt and renews
  it by itself. It needs ports 80 and 443 open and a web name that points at the server.
- **sslip.io:** a free public name service. `203-0-113-7.sslip.io` always answers with the
  address 203.0.113.7, so a bought domain is not needed. You can use a real domain later by
  pointing its A record at the server's IP and changing `JOBFACTS_DOMAIN` in `.env`.
- **systemd timer:** Linux's built-in scheduler (like cron). A timer starts a service at a
  set time. `Persistent=true` means "if the machine was off at that time, run once when it is
  back".
- **Swap:** a file on disk that Linux uses as slow extra memory when RAM is full. A 1 GB
  machine gets a 2 GB swap file so a memory peak slows things down instead of killing a
  program.
- **HTTP Basic:** the browser's own password pop-up. Any user name works; the password is
  `JOBFACTS_WEB_PASSWORD`. It is only safe because the traffic is HTTPS.

What runs on the server:

| Container | What | Memory limit |
|---|---|---|
| `db` | PostgreSQL 16 with pgvector, tuned small (128 MB shared buffers, 20 connections) | 384 MB |
| `app` | `jobfacts serve`, the search page and API | 256 MB |
| `caddy` | HTTPS and the reverse proxy, ports 80 and 443 | 96 MB |
| `daily` | `jobfacts daily`, started by the timer, not always on | 512 MB |

Two systemd timers on the host: the daily run at 06:30 Europe/Zurich and a database backup
at 03:30 Europe/Zurich.

## Part 1: AWS (by hand, once)

Screens change; the names below are what to look for.

1. **Create the account** at aws.amazon.com (an email address, a card, a phone number).
2. **Protect the root user with MFA.** The root user is the email login that can do
   everything, including spending. In the console: account menu, Security credentials, Assign
   MFA device. Use an authenticator app. Do not create access keys for the root user.
3. **Create a budget alarm at 10 EUR.** Console, Billing and Cost Management, Budgets,
   Create budget, "Monthly cost budget", amount 10, set your currency or USD as offered, alert
   at 80% actual and 100% forecast, to your email. A budget only warns; it does not stop
   spending.
4. **Create the Lightsail instance** at lightsail.aws.amazon.com.
   - Region: **Frankfurt (eu-central-1)**. Lightsail's documentation (checked 8 Nov 2026,
     "Regions and Availability Zones for Lightsail") lists Frankfurt, Paris, London, Ireland,
     Stockholm and others, but **no Zurich region**; the page itself suggests Frankfurt or
     Paris for Swiss traffic.
   - Platform Linux/Unix, blueprint "OS Only", **Ubuntu 24.04 LTS**.
   - Plan: the **1 GB** plan **with a public IPv4 address** (not the cheaper IPv6-only plan:
     the sslip.io name and the certificate need IPv4). The 2 GB plan only if the budget allows;
     the stack was designed for 1 GB plus swap.
   - SSH key: keep the default key, or upload your own public key. Download the `.pem` file
     once and keep it (it only works for instances in this region).
   - Name it `jobfacts` and create it.
5. **Attach a static IP** (Networking tab, Create static IP, attach to the instance). Without
   it the address changes when the instance is stopped and started, and the sslip.io name
   (and the certificate) would break. Lightsail's pricing page says a static IP is included
   in the plans; a static IP that is not attached to an instance may be charged, so release it
   if you delete the instance.
6. **Open ports 80 and 443** (instance, Networking tab, IPv4 Firewall, Add rule: HTTP 80,
   HTTPS 443). Port 22 (SSH) is open by default; you can restrict it to your own IP address
   there. Do not open 5432: the database must stay private.
7. **Connect:** `ssh -i ~/Downloads/LightsailDefaultKey-eu-central-1.pem ubuntu@STATIC_IP`
   (`chmod 600` the key file first), or use the browser SSH button on the instance.

## Part 2: set up the server

The repository must be on GitHub with the `deploy/` folder pushed (the server clones it).
On the server:

```
git clone https://github.com/amunozo/jobfacts.git ~/jobfacts
~/jobfacts/deploy/setup.sh
```

`setup.sh` is safe to run again. It:

1. installs Docker Engine and the compose plugin from Docker's official apt repository;
2. creates a 2 GB swap file (`/swapfile`, in `/etc/fstab`) and sets `vm.swappiness=10`;
3. switches on automatic security updates (`unattended-upgrades`);
4. updates the repository (`git pull --ff-only`);
5. creates `.env` (permissions 600) from `.env.example` and asks for what is missing:
   - the database password is generated for you (letters and digits, stored only in `.env`);
   - the page password (typed silently; at least 16 characters; empty = one is generated,
     read it with `grep JOBFACTS_WEB_PASSWORD ~/jobfacts/.env`);
   - the web address, suggesting `<ip-with-dashes>.sslip.io` from the server's public IP;
   - `MISTRAL_API_KEY`, `TYPESAFE_API_KEY`, `OPENROUTER_API_KEY` (pasted silently; never
     shown or logged). The meaning search and the daily run need the first two;
6. installs and enables the two timers;
7. builds the image, starts the stack, and applies the database migrations.

At the end it prints the address to check. Log out and in once, so your user is in the
`docker` group (until then the scripts use `sudo`).

## Part 3: copy your local data (first deploy)

The server starts with an empty database. On **your own computer**, with the local database
running (`docker compose up -d`):

```
deploy/push-db.sh ubuntu@STATIC_IP path/to/key.pem
```

It asks you to type `overwrite`, dumps the local database (`pg_dump`), copies the file with
`scp`, stops the app, drops and recreates the server's database, restores the dump, applies any
newer migrations and starts the app again. After the push, let **only the server** run the
daily job; if both run, they collect separately and the next push throws one side away.

## Check that it works

```
curl https://203-0-113-7.sslip.io/health                       # 200, JSON with open_postings
curl -i https://203-0-113-7.sslip.io/                          # 401: the page needs the password
curl -u me:THE_PASSWORD https://203-0-113-7.sslip.io/api/search?limit=1
```

The very first request can take a few seconds while Caddy asks Let's Encrypt for the
certificate. If it stays broken: port 443 not open in the Lightsail firewall, the name does
not point at the server's IP, or `JOBFACTS_DOMAIN` is mistyped. Let's Encrypt limits how often
it issues certificates for a name, so do not delete the `caddy_data` volume without need.

In a browser, open the same address with `/`, enter any user name and the password.

## Alerts (monitoring)

Two free outside services watch the server; set them up once (details and what a
dead-man switch is: `docs/monitoring.md`):

- A healthchecks.io check (daily, grace 2 hours, cron `30 6 * * *`, time zone
  Europe/Zurich, matching the systemd timer). Put its ping URL in the server's `.env` as
  `JOBFACTS_HEALTHCHECK_URL=https://hc-ping.com/<uuid>`. It is a secret: anyone with it can
  send fake pings. The `daily` service reads `.env`, so nothing else changes. If a daily run
  fails or never happens, the service e-mails you.
- An uptime monitor for `https://<your-domain>/health` (for example UptimeRobot; check the
  current free-plan terms yourself). `/health` is public and also reports the last daily run.

The page `/runs` lists the last 30 daily runs. Migration 010 (the `daily_runs` table) is
applied by the next `jobfacts daily` or `deploy/update.sh`.

## Day to day

On the server (`~/jobfacts/deploy/dc` is `docker compose` for this project):

```
~/jobfacts/deploy/dc ps                          # containers and health
~/jobfacts/deploy/dc logs -f app                 # the app (also: caddy, db)
systemctl list-timers 'jobfacts-*'               # next daily run and backup
journalctl -u jobfacts-daily.service -n 100      # output of the last daily run
sudo systemctl start jobfacts-daily.service      # run the daily job now
free -m; docker stats --no-stream                # memory
```

**Update** after you push new code to GitHub:

```
~/jobfacts/deploy/update.sh
```

It pulls the code, rebuilds the image (`build --pull`, fresh base layers), pulls the other
images (`pull`), applies migrations with the new image **before** the new code starts (if they
fail, the old containers keep running), restarts what changed and removes old images. If `deploy/*.service` or `deploy/*.timer` changed, run `deploy/setup.sh` again.

## Image versions and the project name

The images are pinned to exact versions: `python:3.12.16-slim-trixie` (Dockerfile),
`pgvector/pgvector:0.8.7-pg16` and `caddy:2.11.9-alpine` (`deploy/compose.prod.yml`, Caddy
twice). To bump one, look up the newest tag of the same major version on Docker Hub, edit the
tag, commit, push, and run `deploy/update.sh` (it pulls the new image). Do not change the
PostgreSQL major version (`pg16`) without a dump and restore.

The production project is named `jobfacts-prod` (volumes `jobfacts-prod_pgdata`, ...), so it
never shares containers or volumes with the development `docker-compose.yml` of the same clone
(project `jobfacts`; its database port is bound to 127.0.0.1 only). Always use `deploy/dc`.

## Backups

Every night at 03:30 (Zurich) `deploy/backup.sh` writes
`~/jobfacts-backups/jobfacts-YYYYMMDD-HHMMSS.dump` (`pg_dump` custom format) and keeps the
newest 7. Run one by hand: `~/jobfacts/deploy/backup.sh`.

These dumps are on the same disk as the database. They protect against a mistake, not against
losing the instance. For that, also (a) copy one to your computer now and then:
`scp -i key.pem ubuntu@STATIC_IP:'jobfacts-backups/jobfacts-*.dump' ~/backups/`, or (b) turn on
Lightsail snapshots (instance, Snapshots tab; automatic daily snapshots can be enabled there).
Lightsail's pricing page lists snapshots at 0.05 USD per GB per month.

**Restore** into a scratch database first, to look (on the server):

```
cd ~/jobfacts
deploy/dc exec -T db sh -c 'psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE scratch"'
deploy/dc exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d scratch --no-owner --exit-on-error' \
  < ~/jobfacts-backups/jobfacts-YYYYMMDD-HHMMSS.dump
deploy/dc exec -T db sh -c 'psql -U "$POSTGRES_USER" -d scratch -c "select count(*) from postings"'
deploy/dc exec -T db sh -c 'psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE scratch"'
```

**Restore for real** (replaces the live database; stops the app for a moment):

```
cd ~/jobfacts
deploy/dc stop app
deploy/dc exec -T db sh -c 'psql -U "$POSTGRES_USER" -d postgres \
  -c "DROP DATABASE jobfacts WITH (FORCE)" -c "CREATE DATABASE jobfacts"'
deploy/dc exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d jobfacts --no-owner --exit-on-error' \
  < ~/jobfacts-backups/jobfacts-YYYYMMDD-HHMMSS.dump
deploy/dc up -d
```

## Memory on a 1 GB machine

Measured on a laptop with the same images and the full local data (6,042 open postings,
7,696 postings in all), at idle after a few searches: `db` 156 MiB, `app` 61 MiB, `caddy`
12 MiB, about 230 MiB together. The daily job in its own container peaked near 130 MiB in a
run that made no collection and no paid calls; a real collection run will use more, and the
limit is 512 MB. Everything together fits in 1 GB with the swap file, with the operating
system and Docker taking roughly 200 to 300 MB. If `docker stats` shows the daily job
regularly near its limit or the machine swapping heavily, move to the 2 GB plan.

## Rough monthly cost

Prices must be checked on the official page before you decide:
https://aws.amazon.com/lightsail/pricing/. What it showed on 8 Nov 2026 (in US dollars; the
euro amount depends on the exchange rate and on VAT, which this guide does not know):

- General purpose Linux plan with a public IPv4 address: 1 GB RAM, 2 vCPU, 40 GB disk, 2 TB
  transfer: 7 USD per month. 2 GB RAM, 60 GB disk, 3 TB transfer: 12 USD per month. The
  IPv6-only versions are 5 and 10 USD, but are not used here.
- A static IP is listed as included in the plans.
- Snapshots: 0.05 USD per GB per month.

So the 1 GB plan fits the 10 EUR limit. The 2 GB plan at 12 USD may or may not, depending on
the exchange rate and tax; check before choosing it. Not included: the pay-per-use API calls
(Mistral embeddings, Jev extraction) that the daily run makes, and a domain name if you buy
one. Whether a free trial or credit applies to your new account is stated on that page and in
the Lightsail console; this guide does not rely on it.

## Security notes

- Only ports 22, 80 and 443 are open; the database and the app have no published port.
- `.env` holds all secrets, is git-ignored, has permissions 600, and the image does not
  contain it (`.dockerignore`); the containers get it at start.
- The page has one shared password and no rate limit on guesses: use a long one. `/health` is
  public on purpose and shows only a count and the last collection time.
- Caddy adds `Strict-Transport-Security`; the app already sets the other security headers.
- Security updates of the operating system install themselves; after a kernel update the
  machine may need a reboot (`sudo reboot`; the containers and timers come back on their own).
