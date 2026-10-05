# syntax=docker/dockerfile:1
# Imagen de `agentcore serve` (ADR infra 0003: Dockerfile y build son de agent-core).
# Build:  docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t agent-core .
# Solo se despliega por digest inmutable, nunca por tag.

# Bases fijadas por digest del índice multi-arquitectura (linux/amd64 y linux/arm64):
#   python:3.12-slim-bookworm  ·  ghcr.io/astral-sh/uv:0.8
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS build
COPY --from=ghcr.io/astral-sh/uv:0.8@sha256:1d31be550ff927957472b2a491dc3de1ea9b5c2d319a9cea5b6a48021e2990a6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencias primero para aprovechar la caché de capas.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY agent_core ./agent_core
COPY agent_telemetry ./agent_telemetry
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
ARG GIT_SHA=""
ENV AGENTCORE_GIT_SHA=$GIT_SHA
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --system --uid 10001 --no-create-home agentcore
COPY --from=build --chown=agentcore:agentcore /app/.venv /app/.venv
USER 10001
STOPSIGNAL SIGTERM
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]
# Sin secretos en la imagen: DSN, claves y LLM_ENDPOINTS llegan por variables de entorno.
# Otros comandos: `docker run … agentcore migrate`, `agentcore sweep --once`.
ENTRYPOINT ["agentcore"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
