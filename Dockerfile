FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Fewer parallel downloads and a longer timeout: bursts of DNS lookups fail
# behind Docker Desktop's resolver (especially on a VPN).
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 \
    UV_CONCURRENT_DOWNLOADS=4 UV_HTTP_TIMEOUT=60

COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project

COPY fetch_everything_pipeline/ ./fetch_everything_pipeline/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

RUN addgroup --system pipeline \
    && adduser --system --ingroup pipeline --home /home/pipeline pipeline \
    && chown pipeline:pipeline /app

ENV HOME=/home/pipeline PATH="/app/.venv/bin:$PATH"
USER pipeline
CMD ["fetch-pipeline", "--help"]
