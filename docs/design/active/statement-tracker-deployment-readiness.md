---
title: "Statement Tracker Deployment Readiness"
sidebar_label: Deployment Readiness
sidebar_position: 3
---

# Statement Tracker — Homelab Deployment Readiness Assessment

> Prepared for DI ↔ Mission Control Phase 0 integration review

## Summary

**Status: ✅ Ready for deployment** — All infrastructure pieces are in place. No code blockers remain; deployment is a configuration/ops task.

---

## Infrastructure Checklist

| Component | Status | Notes |
|-----------|--------|-------|
| Dockerfile | ✅ Ready | Python 3.12-slim, `doc-hub serve` entrypoint, port 8001 |
| docker-compose.yml | ✅ Ready | `statement-tracker` service with `restart: unless-stopped` |
| CI/CD image build | ✅ Ready | GitHub Actions workflow pushes to `service-007.example.invalid/doc-intelligence-hub` |
| Self-hosted runner | ✅ Ready | Runs on `[self-hosted, linux, docker, build, homelab, dockhand]` |
| Docker config | ✅ Ready | `config/config.docker.yaml` points to `http://service-006.example.invalid` |
| Env template | ✅ Ready | `config/.env.docker` with all required variables |
| Persistent volume | ✅ Ready | `hub-data` volume for snapshot + SQLite databases |
| Paperless setup guide | ✅ Ready | `docs/statement-tracking/SETUP-PAPERLESS.md` covers token creation, API testing |

## Live Paperless Connectivity

The `config.docker.yaml` is pre-configured for live Paperless:

```yaml
source:
  mode: paperless
  paperless_url: http://service-006.example.invalid
  api_token_env: PAPERLESS_API_TOKEN
  verify_ssl: false     # Internal homelab, self-signed cert
  timeout_seconds: 60
```

The Statement Tracker will connect to the existing Paperless instance at `service-006.example.invalid` over the internal network. SSL verification is disabled (appropriate for internal homelab traffic).

## Deployment Steps

1. **Build and push image** — Trigger the `Build Document Intelligence Hub Image` workflow from GitHub Actions (or let it auto-build on merge)
2. **Set environment** — Copy `config/.env.docker` to the deployment host and fill in `PAPERLESS_API_TOKEN`
3. **Deploy** — `docker compose up -d statement-tracker` from the deployment directory
4. **Verify** — `***REMOVED*** http://localhost:8001/health` should return `{"status": "ok"}`

## What the Hub API Exposes (for MC Integration)

When deployed, the unified hub API at port 8001 provides:

- `GET /health` — Service health check
- `GET /api/queue/actions` — Action queue items (now includes `preview_url`)
- `GET /api/queue/status` — Pipeline run status
- `POST /api/statements/discovery/run` — Statement pattern discovery
- `POST /api/statements/recommendations/run` — Missing statement recommendations
- `GET /api/eob/status` — EOB matching status

## Notes

- The Statement Tracker runs inside the unified `doc-intelligence-hub` image alongside action queue and EOB matching API routes
- The hub runs as a single FastAPI process; all three modules share the same port
- Paperless document URLs follow the pattern `{paperless_url}/documents/{doc_id}/details` — the action queue API now includes this as `preview_url`
- The docker-compose also defines `eob-matching` and `action-queue` services as job-profile containers (run on-demand, not always-on)
