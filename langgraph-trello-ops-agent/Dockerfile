# syntax=docker/dockerfile:1
# langgraph-trello-ops-agent: el agente (línea de comandos) y, con otro comando, su API (FastAPI) y su interfaz (Streamlit). Dependencias fijadas por uv.lock, usuario sin privilegios.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app

# 1) Dependencias (esta capa se reutiliza mientras no cambien pyproject.toml ni uv.lock).
#    Para las herramientas MCP opcionales: docker compose build --build-arg WITH_MCP=1
ARG WITH_MCP=0
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "$WITH_MCP" = "1" ]; then uv sync --frozen --no-dev --group mcp --no-install-project; \
    else uv sync --frozen --no-dev --no-install-project; fi

# 2) Código y runbook (en docker-compose, data/ se monta encima para editar el runbook sin reconstruir la imagen)
COPY main.py api.py ui.py langgraph.json ./
COPY trello_ops ./trello_ops
COPY data ./data

# /app/.data guarda la memoria (SQLite); como volumen nombrado hereda este dueño sin privilegios
RUN useradd --system --uid 10001 --home /app app && mkdir /app/.data && chown app /app/.data
USER app

# RUNNING_IN_DOCKER=1 hace que LOCAL_LLM_URL=http://localhost:... apunte a tu máquina (host.docker.internal), donde corre LM Studio
ENV PATH="/app/.venv/bin:$PATH" RUNNING_IN_DOCKER=1 OPS_DB=/app/.data/ops.sqlite
ENTRYPOINT ["python", "main.py"]
CMD ["--trace", "Muestra mis tableros"]
