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

# Default: run the statement tracker web server
# Override with other commands for different modules:
#   eob-match run --limit 100
#   paq run --dry-run
ENTRYPOINT ["doc-hub"]
CMD ["serve", "--config", "/app/config/config.docker.yaml"]
