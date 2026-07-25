# =============================================================================
# Document Intelligence Hub — Multi-stage Production Dockerfile
# =============================================================================
# Build:  docker build -t doc-intelligence-hub .
# Run:    docker run -p 8001:8001 doc-intelligence-hub
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: builder — install deps, build wheel
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Install build tooling
RUN pip install --no-cache-dir build

# Copy only dependency metadata first (maximizes layer cache)
COPY pyproject.toml README.md ./
COPY src ./src

# Build wheel
RUN python -m build --wheel --outdir /build/dist

# ---------------------------------------------------------------------------
# Stage 2: runtime — slim production image
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Build-time labels (OCI standard)
ARG BUILD_DATE
ARG VCS_REF
ARG VERSION=0.2.0
LABEL org.opencontainers.image.title="Document Intelligence Hub" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/rsocko/ideation" \
      org.opencontainers.image.description="Unified Paperless-ngx document analysis platform"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install the pre-built wheel (no build tools needed in runtime)
COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -rf /tmp/*.whl

# Copy runtime config
COPY config/config.docker.yaml ./config/config.docker.yaml

# Create non-root user and data directory
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/false --create-home appuser && \
    mkdir -p /app/data && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8001

# Healthcheck — lightweight HTTP probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

# Default: run the unified hub API (includes /admin, /statements, /api/*)
# Override with other entrypoints for different modules:
#   eob-match run --limit 100
#   paq run --dry-run
ENV STATEMENT_TRACKER_CONFIG=/app/config/config.docker.yaml
ENTRYPOINT ["doc-hub-serve"]
