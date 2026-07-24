FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY config/config.docker.yaml ./config/config.docker.yaml

RUN pip install --no-cache-dir .

# Create data directory for runtime artifacts (snapshots, databases)
RUN mkdir -p /app/data

EXPOSE 8001

# Default: run the unified hub API (includes /admin, /statements, /api/*)
# Override with other commands for different modules:
#   eob-match run --limit 100
#   paq run --dry-run
ENV STATEMENT_TRACKER_CONFIG=/app/config/config.docker.yaml
ENTRYPOINT ["doc-hub-serve"]
