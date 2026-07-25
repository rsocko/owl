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
# EOB Matching           — Weekly Sun 10 AM — POST /api/eob/run
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
