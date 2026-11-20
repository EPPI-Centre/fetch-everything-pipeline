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
# No BuildKit cache mounts: ACR's remote builder (az acr build) doesn't support
# them. --no-cache keeps uv's download cache out of the image layers.
RUN uv sync --locked --no-install-project --no-cache

COPY fetch_everything_pipeline/ ./fetch_everything_pipeline/
RUN uv sync --locked --no-cache

# Runs as root for now, so the job can write to whatever output directory it's
# given (bind mounts, Azure ML output folders).
# to do: implement non-root user for writing output directories safely
ENV PATH="/app/.venv/bin:$PATH"
CMD ["fetch-pipeline", "--help"]
