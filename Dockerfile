# =============================================================================
# Document Intelligence Hub — Multi-stage Production Dockerfile
# =============================================================================
# Build:  docker build -t doc-intelligence-hub .
# Run:    docker run -p 8001:8001 doc-intelligence-hub
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: frontend-build — build the React/Vite UI
# ---------------------------------------------------------------------------
FROM node:20-slim AS frontend-build

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend ./
# Override vite.config.ts's outDir (which targets the local repo checkout) so
# the build lands in a clean, self-contained directory for the next stage.
RUN npm run build -- --outDir /frontend-dist --emptyOutDir

# ---------------------------------------------------------------------------
# Stage 2: builder — install deps, build wheel
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Install build tooling
RUN pip install --no-cache-dir build

# Copy only dependency metadata first (maximizes layer cache)
COPY pyproject.toml README.md ./
COPY src ./src

# Overlay the freshly-built frontend on top of whatever's committed under
# api/static/, so the wheel always ships the current frontend build rather
# than whatever happened to be committed to source control.
COPY --from=frontend-build /frontend-dist ./src/doc_intelligence_hub/api/static

# Build wheel
RUN python -m build --wheel --outdir /build/dist

# ---------------------------------------------------------------------------
# Stage 3: runtime — slim production image
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

# Install the pre-built wheel and gosu for privilege drop
COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -rf /tmp/*.whl && \
    apt-get update && apt-get install -y --no-install-recommends gosu && \
    rm -rf /var/lib/apt/lists/*

# Copy runtime config and entrypoint
COPY config/config.docker.yaml ./config/config.docker.yaml
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Create non-root user and data directory
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/false --create-home appuser && \
    mkdir -p /app/data && \
    chown -R appuser:appuser /app

EXPOSE 8001

# Healthcheck — lightweight HTTP probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

# Default: run the unified hub API (includes /admin, /statements, /api/*)
# Override with other entrypoints for different modules:
#   eob-match run --limit 100
#   paq run --dry-run
ENV STATEMENT_TRACKER_CONFIG=/app/config/config.docker.yaml

# Entrypoint runs as root, fixes data volume permissions, then drops to appuser
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["doc-hub-serve"]
