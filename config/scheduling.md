# Document Intelligence Hub — Scheduling Configuration
#
# Both Action Queue (paq) and EOB Matching (eob-match) can be scheduled
# to run periodically using cron, systemd timers, or Docker labels.
#
# ===================================================================
# Option 1: Cron (simplest)
# ===================================================================
#
# Add to your crontab (crontab -e):
#
# # Action Queue — run every 6 hours against inbox documents
# 0 */6 * * * docker compose -f /path/to/docker-compose.yml run --rm action-queue
#
# # EOB Matching — run daily at 2 AM
# 0 2 * * * docker compose -f /path/to/docker-compose.yml run --rm eob-matching
#
# ===================================================================
# Option 2: Systemd Timers (recommended for homelab)
# ===================================================================
#
# Create two files per job:
#
# --- /etc/systemd/system/doc-intelligence-action-queue.service ---
# [Unit]
# Description=Document Intelligence — Action Queue Pipeline
# After=docker.service
# Requires=docker.service
#
# [Service]
# Type=oneshot
# WorkingDirectory=/opt/doc-intelligence
# ExecStart=/usr/bin/docker compose run --rm action-queue
# TimeoutStartSec=300
#
# --- /etc/systemd/system/doc-intelligence-action-queue.timer ---
# [Unit]
# Description=Run Action Queue every 6 hours
#
# [Timer]
# OnCalendar=*-*-* 00/6:00:00
# Persistent=true
# RandomizedDelaySec=300
#
# [Install]
# WantedBy=timers.target
#
# --- /etc/systemd/system/doc-intelligence-eob-matching.service ---
# [Unit]
# Description=Document Intelligence — EOB Matching Pipeline
# After=docker.service
# Requires=docker.service
#
# [Service]
# Type=oneshot
# WorkingDirectory=/opt/doc-intelligence
# ExecStart=/usr/bin/docker compose run --rm eob-matching
# TimeoutStartSec=600
#
# --- /etc/systemd/system/doc-intelligence-eob-matching.timer ---
# [Unit]
# Description=Run EOB Matching daily at 2 AM
#
# [Timer]
# OnCalendar=*-*-* 02:00:00
# Persistent=true
# RandomizedDelaySec=300
#
# [Install]
# WantedBy=timers.target
#
# Enable timers:
#   sudo systemctl enable --now doc-intelligence-action-queue.timer
#   sudo systemctl enable --now doc-intelligence-eob-matching.timer
#
# Check status:
#   systemctl list-timers doc-intelligence-*
#
# ===================================================================
# Option 3: n8n Workflow (if using n8n for orchestration)
# ===================================================================
#
# Create a workflow with:
#   1. Schedule Trigger node → Cron: 0 */6 * * *
#   2. HTTP Request node → POST http://doc-hub:8001/api/queue/run
#      Body: {"dry_run": false, "limit": 50}
#   3. IF node → Check result.failed > 0
#   4. (Optional) Send notification on failures
#
# For EOB matching:
#   1. Schedule Trigger node → Cron: 0 2 * * *
#   2. HTTP Request node → POST http://doc-hub:8001/api/eob/run
#      Body: {"limit": 200, "tags": ["medical"]}
