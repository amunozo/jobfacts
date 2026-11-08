# The jobfacts image: the web app (`jobfacts serve`) and the commands (`jobfacts daily`, ...).
# Built on the server with `deploy/compose.prod.yml`; see docs/deploy.md.

# --- stage 1: build the virtual environment with uv -------------------------------------
FROM python:3.12.16-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.9.27 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first: this layer is rebuilt only when pyproject.toml or uv.lock change.
# No dev tools, and not the `embed-local` group (torch): the server embeds with the Mistral API.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# The code. The package is installed in editable mode, so it keeps reading `config/` and
# `migrations/` from /app, the same layout as in the repository.
COPY src ./src
COPY config ./config
COPY migrations ./migrations
RUN uv sync --frozen --no-dev && .venv/bin/python -m compileall -q src

# --- stage 2: the runtime image ---------------------------------------------------------
FROM python:3.12.16-slim-trixie
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin app
WORKDIR /app
# Code and .venv stay owned by root: the process (user `app`) cannot rewrite its own code.
# Bytecode is compiled at build time (UV_COMPILE_BYTECODE); nothing writes under /app at runtime.
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER app
EXPOSE 8000
CMD ["jobfacts", "serve", "--host", "0.0.0.0", "--port", "8000"]
