FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM python:3.13.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

RUN groupadd --system --gid 10001 aiw \
    && useradd --system --uid 10001 --gid aiw --home-dir /nonexistent --shell /usr/sbin/nologin aiw

WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY --chown=aiw:aiw pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=aiw:aiw app ./app
COPY --chown=aiw:aiw alembic ./alembic
COPY --chown=aiw:aiw alembic.ini ./
RUN uv sync --frozen --no-dev \
    && find /app -type d -name __pycache__ -prune -exec rm -rf {} +

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
