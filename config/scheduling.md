# Document Intelligence Hub — Scheduling Configuration
#
# The hub has a BUILT-IN SCHEDULER (APScheduler) that runs all module
# pipelines automatically. No external orchestrator is needed.
#
# Schedule defaults are defined in config/schedules.yaml and can be
# changed at runtime via the admin API or admin UI.
#
# ===================================================================
# Architecture
# ===================================================================
#
# The scheduler lives in src/doc_intelligence_hub/core/scheduler.py.
# It starts automatically on app boot and calls the hub's own API
# endpoints via httpx on each cron tick — same path as any external
# caller, so all middleware/error handling is exercised.
#
# ===================================================================
# Module Schedule Summary
# ===================================================================
#
# Statement Discovery    — Daily 9:00 AM  — POST /api/statements/discovery/run
# Statement Gap Check    — Daily 9:30 AM  — POST /api/statements/recommendations/run?as_of=TODAY
# EOB Matching           — Daily 10:00 AM — POST /api/eob/run (incremental via since_last_run)
# Action Queue           — Daily 8 AM & 2 PM — POST /api/queue/run
#
# ===================================================================
# Admin API
# ===================================================================
#
# GET  /api/admin/schedules       — view all schedules (with next_run, last_run)
# PUT  /api/admin/schedules       — update & reschedule (takes effect immediately)
#
# Example:
#   ***REMOVED*** -X PUT http://localhost:8071/api/admin/schedules \
#     -H 'Content-Type: application/json' \
#     -d '{"eob_matching": {"cron": "0 10 * * 1", "enabled": true}}'
#
# ===================================================================
# Admin UI
# ===================================================================
#
# The "Scan Schedules" page in the admin panel shows all four module
# schedules with editable cron expressions, limits, enable/disable
# toggles, next run times, last run status, and "Run Now" buttons.
#
# ===================================================================
# Data Retention Cleanup (weekly)
# ===================================================================
#
# Cleanup of stale records runs weekly (Sunday 2 AM by default).
# It can be triggered via the built-in scheduler, the admin API,
# or the CLI.
#
# --- Admin API ---
#   POST /api/admin/cleanup        — trigger cleanup (supports dry_run)
#   GET  /api/admin/retention       — view retention policy
#   PUT  /api/admin/retention       — update retention policy at runtime
#   GET  /api/admin/storage         — view storage usage breakdown
#
# --- CLI (manual / dry-run) ---
#   doc-hub cleanup --dry-run      # Preview what would be deleted
#   doc-hub cleanup                # Actually delete stale records
#
# --- Fallback: Crontab ---
#   0 2 * * 0 ***REMOVED*** -s -X POST http://localhost:8071/api/admin/cleanup \
#     -H 'Content-Type: application/json' -d '{"dry_run": false}'
#
# ===================================================================
# Fallback: Crontab (external scheduling)
# ===================================================================
#
# For environments where the built-in scheduler is not suitable,
# config/crontab.example provides equivalent cron entries that call
# the hub API via ***REMOVED***. Copy them into your crontab:
#
#   crontab -e
#   # Paste entries from config/crontab.example
#
# ===================================================================
# Docker-native scheduling (Dockhand / supercronic)
# ===================================================================
#
# For Dockhand stacks or environments where host cron is impractical,
# use the eob-scheduler sidecar container. It runs supercronic
# (a lightweight cron replacement for containers) with a bundled
# crontab that triggers `eob-match run --since-last-run` daily.
#
# Start the scheduler:
#   docker compose --profile scheduled up -d eob-scheduler
#
# The schedule is defined in config/crontab.eob-scheduler and can
# be customized by editing the file and rebuilding the image.
#
# ===================================================================
# Incremental mode (--since-last-run)
# ===================================================================
#
# The --since-last-run flag (CLI) and since_last_run field (API)
# query the last successful pipeline run's finished_at timestamp
# and use it as a created_after date filter. This makes daily runs
# efficient: only newly-added documents are processed.
#
# CLI: eob-match run --since-last-run --limit 200
# API: POST /api/eob/run {"since_last_run": true, "limit": 200}
#
# If no prior successful run exists, all documents are processed.
# The flag is mutually exclusive with --created-after / created_after.
#
# ===================================================================
# Failure monitoring
# ===================================================================
#
# When the EOB matching pipeline fails (CLI or API), an alert is
# emitted via the unified alerts system (core/alerts.py):
#
#   alert_type: eob_run_failed
#   severity: high
#   module: eob
#
# These alerts are visible in:
#   - GET /api/insights/alerts
#   - The Mission Control connector
#   - The admin dashboard's alerts panel
