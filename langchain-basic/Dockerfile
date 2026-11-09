# syntax=docker/dockerfile:1
# langchain-basic: un agente de línea de comandos. Imagen pequeña, dependencias fijadas por uv.lock, usuario sin privilegios.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app

# 1) Dependencias (esta capa se reutiliza mientras no cambien pyproject.toml ni uv.lock)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

# 2) Código
COPY *.py ./

RUN useradd --system --uid 10001 --home /app app
USER app

# RUNNING_IN_DOCKER=1 hace que LOCAL_LLM_URL=http://localhost:... apunte a tu máquina (host.docker.internal), donde corre LM Studio
ENV PATH="/app/.venv/bin:$PATH" RUNNING_IN_DOCKER=1
ENTRYPOINT ["python", "agent.py"]
CMD ["--trace", "¿Cómo está la VPN?"]
