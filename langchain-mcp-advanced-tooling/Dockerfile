# syntax=docker/dockerfile:1
# Una sola imagen para los tres servicios (servidor MCP, API y UI): cambia el comando en docker-compose.yml.
# Dependencias fijadas por uv.lock.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

COPY *.py ./
RUN useradd --system --uid 10001 --home /app app && mkdir /data && chown app /data   # /data: la base SQLite de las sesiones
USER app

ENV PATH="/app/.venv/bin:$PATH" MCP_HOST=0.0.0.0 MCP_PORT=8000 SESSIONS_DB=/data/sessions.db
EXPOSE 8000 8001 8501
CMD ["python", "mcp_server.py", "--http"]
