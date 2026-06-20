# Quick Reference Guide

## Overview

This guide covers the current Phase 1 prototype only. The implemented surface area is intentionally small: fixture-backed or Paperless-backed discovery, missing-statement recommendations, a JSON snapshot, and a small API.

---

## Local Setup

```bash
# From the statement-tracking project directory
python -m pip install -e .[dev]
pytest
```

If PowerShell blocks venv activation on Windows, call the interpreter directly instead of running `Activate.ps1`:

```powershell
c:/dev/ideation/.venv/Scripts/python.exe -m pip install -e .[dev]
c:/dev/ideation/.venv/Scripts/python.exe -m pytest
```

---

## Fixture Validation

```bash
# Discover recurring providers from the synthetic fixture
statement-tracker discover --config config/config.fixture.yaml

# Inspect accepted groups and near-misses
statement-tracker debug-discovery --config config/config.fixture.yaml --limit 20

# Compute missing and overdue windows for a fixed date
statement-tracker check-missing --config config/config.fixture.yaml --as-of 2026-05-12

# Start the local API on port 8001
statement-tracker serve --config config/config.fixture.yaml
```

Expected artifacts:

- Discovery output printed as JSON
- Recommendation output printed as JSON
- Snapshot written to `data/catalog.snapshot.json`

---

## API Endpoints

With the service running:

```bash
***REMOVED*** http://localhost:8001/health
***REMOVED*** -X POST http://localhost:8001/api/discovery/run
***REMOVED*** -X POST "http://localhost:8001/api/recommendations/run?as_of=2026-05-12"
```

---

## Paperless Smoke Test

1. Copy `config/config.paperless.example.yaml` to `config/config.paperless.yaml`.
2. Update `paperless_url` if needed.
3. Set `PAPERLESS_API_TOKEN` in your shell or deployment environment.
4. Run:

```bash
statement-tracker test-connection --config config/config.paperless.yaml
statement-tracker discover --config config/config.paperless.yaml
statement-tracker debug-discovery --config config/config.paperless.yaml --limit 30
statement-tracker serve --config config/config.paperless.yaml
```

PowerShell example:

```powershell
$env:PAPERLESS_API_TOKEN = "your-token-here"
statement-tracker test-connection --config config/config.paperless.yaml
```

Phase 1 only reads from Paperless. It does not write documents, tags, or metadata back.

---

## Docker Image Workflow

Build and push the image from a machine that can run Docker:

```bash
docker build -t service-007.example.invalid/statement-tracker:phase1 .
docker push service-007.example.invalid/statement-tracker:phase1
```

Deploy the prebuilt image:

```bash
docker compose -f deploy/docker-compose.image.yaml up -d
```

Required host files:

- `config/config.paperless.yaml`
- `data/` directory for snapshots

---

## Current CLI Surface

```bash
statement-tracker discover --config <path>
statement-tracker debug-discovery --config <path> --limit <n>
statement-tracker check-missing --config <path> --as-of YYYY-MM-DD
statement-tracker test-connection --config <path>
statement-tracker serve --config <path>
```

Anything outside those commands is still design-stage documentation, not implemented behavior.
3. Click "Edit Period"
4. Update dates
5. Save (auto-recalculates pattern)

### Workflow 4: Handle Irregular Provider

**Provider with variable schedule:**

```bash
# 1. Add provider with custom pattern
statement-tracker providers add \
  --name "Quarterly Insurance" \
  --type insurance \
  --frequency quarterly \
  --pattern custom

# 2. Define custom availability windows
statement-tracker providers set-windows \
  --provider quarterly-insurance-123 \
  --Q1 "first week of January" \
  --Q2 "first week of April" \
  --Q3 "first week of July" \
  --Q4 "first week of October"

# 3. Set larger grace period
statement-tracker providers set-grace-period \
  --provider quarterly-insurance-123 \
  --days 14
```

---

## Dashboard Navigation

### Main Dashboard

**URL:** `/`

**Shows:**
- Summary statistics (total providers, missing statements, recent additions)
- Priority list of missing statements
- Recent statement additions
- Quick actions

**Actions:**
- Click provider → Provider detail page
- Click missing statement → Download instructions
- "Check Now" button → Run immediate check

### Providers Page

**URL:** `/providers`

**Shows:**
- List of all configured providers
- Status (active, paused, needs review)
- Last statement date
- Next expected date

**Actions:**
- Add new provider
- Edit provider settings
- View provider details
- Pause/resume tracking

### Provider Detail Page

**URL:** `/providers/{id}`

**Shows:**
- Provider information
- Recurrence pattern details
- Timeline of statements (visual)
- Missing periods highlighted
- Pattern confidence score

**Actions:**
- Edit provider settings
- View/edit each statement
- Mark exceptions
- Recalculate pattern
- Export statement list

### Recommendations Page

**URL:** `/recommendations`

**Shows:**
- All missing statements
- Sorted by priority
- Status indicators (missing, overdue, pending)
- Expected vs. available dates

**Actions:**
- Mark as downloaded
- Add to download queue
- Dismiss recommendation
- Set reminder

### Settings Page

**URL:** `/settings`

**Sections:**
- Paperless-ngx connection
- Analysis schedule
- Notification settings
- Provider defaults
- Detection rules

---

## API Endpoints

### Providers

```http
GET    /api/providers              # List all providers
GET    /api/providers/{id}          # Get provider details
POST   /api/providers              # Create provider
PUT    /api/providers/{id}          # Update provider
DELETE /api/providers/{id}          # Delete provider
POST   /api/providers/{id}/analyze  # Analyze provider
```

### Statements

```http
GET    /api/statements                    # List statements
GET    /api/statements/{id}               # Get statement details
PUT    /api/statements/{id}               # Update statement
POST   /api/statements/{id}/confirm       # Confirm period
POST   /api/statements/{id}/mark-exception # Mark as exception
```

### Recommendations

```http
GET    /api/recommendations           # Get missing statements
GET    /api/recommendations/priority  # Sorted by priority
POST   /api/recommendations/{id}/acknowledge # Mark as acknowledged
POST   /api/recommendations/{id}/dismiss     # Dismiss recommendation
```

### Analysis

```http
POST   /api/analysis/discover         # Run discovery
POST   /api/analysis/check-missing    # Check for missing
GET    /api/analysis/status           # Get analysis status
```

### Example API Calls

**Get all providers:**
```bash
Authorization: ${PAPERLESS_AUTH_HEADER} \
     http://localhost:8001/api/providers
```

**Check for missing statements:**
```bash
***REMOVED***
Authorization: ${PAPERLESS_AUTH_HEADER} \
     http://localhost:8001/api/analysis/check-missing
```

**Confirm statement period:**
```bash
***REMOVED***
Authorization: ${PAPERLESS_AUTH_HEADER} \
     -H "Content-Type: application/json" \
     -d '{"period_start": "2025-01-01", "period_end": "2025-01-31"}' \
     http://localhost:8001/api/statements/8742/confirm
```

---

## Configuration Reference

### config.yaml Structure

```yaml
# Paperless-ngx connection
paperless:
  url: "http://localhost:8000"
  api_token: "${PAPERLESS_API_TOKEN}"  # From environment
  verify_ssl: true

# Database configuration
database:
  type: "sqlite"  # or "postgresql"
  connection_string: "statement_tracker.db"
  # For PostgreSQL:
  # connection_string: "postgresql://user:pass@localhost/statements"

# Analysis settings
analysis:
  schedule: "0 2 * * *"  # Cron: Daily at 2 AM
  auto_discover: true
  min_confidence: 0.70
  
# Detection rules
detection:
  min_documents_for_pattern: 3
  max_variance_days: 7
  grace_period_days: 5
  
# Notifications (optional)
notifications:
  email:
    enabled: false
    smtp_server: "smtp.gmail.com"
    smtp_port: 587
    from_address: "alerts@example.com"
    to_address: "user@example.com"
  
  webhook:
    enabled: false
    url: "https://hooks.example.com/statement-alerts"

# Provider defaults
provider_defaults:
  importance: "medium"
  grace_period_days: 5
  auto_add_to_catalog: false  # Require manual confirmation

# Logging
logging:
  level: "INFO"  # DEBUG, INFO, WARNING, ERROR
  format: "json"  # or "text"
  file: "logs/statement_tracker.log"
```

---

## Troubleshooting

### Issue: Discovery finds no providers

**Possible causes:**
1. No documents in paperless-ngx
2. Documents don't match detection patterns
3. Confidence threshold too high

**Solutions:**
```bash
# Check document count
Authorization: ${PAPERLESS_AUTH_HEADER} \
     http://localhost:8000/api/documents/ | jq '.count'

# Lower confidence threshold temporarily
statement-tracker discover --min-confidence 0.50

# Check detection logs
tail -f logs/statement_tracker.log | grep detection
```

### Issue: Pattern detection incorrect

**Symptoms:**
- Wrong frequency (e.g., weekly instead of monthly)
- Wrong expected dates
- Low confidence score

**Solutions:**
```bash
# Review detected pattern
statement-tracker providers show chase-visa-1234 --verbose

# Check source documents
statement-tracker statements list --provider "Chase Visa" --dates

# Manually override pattern
statement-tracker providers edit chase-visa-1234
# Then set correct pattern in UI or config
```

### Issue: Missing statements not detected

**Possible causes:**
1. Statement exists but not linked to provider
2. Grace period not elapsed
3. Provider paused

**Solutions:**
```bash
# Check provider status
statement-tracker providers show chase-visa-1234

# Check if document exists in paperless
Authorization: ${PAPERLESS_AUTH_HEADER} \
     "http://localhost:8000/api/documents/?correspondent__id=42"

# Force check without grace period
statement-tracker check-missing --no-grace-period

# Re-link documents to provider
statement-tracker analyze --provider "Chase Visa" --reindex
```

### Issue: Paperless connection fails

**Symptoms:**
- "Connection refused" errors
- "Unauthorized" errors
- Timeouts

**Solutions:**
```bash
# Verify paperless is running
***REMOVED***

***REMOVED***
Authorization: ${PAPERLESS_AUTH_HEADER} \
     http://localhost:8000/api/documents/?page=1&page_size=1

# Check token in config
statement-tracker config check

# Regenerate token in paperless-ngx if needed
# Settings → API Tokens → Add Token
```

### Issue: High memory usage

**Causes:**
- Large document set
- ML models loaded
- Memory leak

**Solutions:**
```bash
# Reduce batch size
statement-tracker config set analysis.batch_size 50

# Disable ML features if not needed
statement-tracker config set ml.enabled false

# Run garbage collection
statement-tracker gc

# Restart service
statement-tracker restart
```

---

## Maintenance Tasks

### Daily
- Check dashboard for missing statements
- Review priority recommendations
- Download and upload statements as needed

### Weekly
- Review newly added documents
- Confirm auto-detected patterns
- Check for pattern anomalies

### Monthly
- Export catalog backup
- Review provider list (add/remove as needed)
- Check logs for errors
- Update provider importance levels

### Quarterly
- Database backup
- Review and update detection rules
- Performance check
- Update dependencies

---

## Backup & Restore

### Backup Database

**SQLite:**
```bash
# Stop service
statement-tracker stop

# Copy database
cp statement_tracker.db backups/statement_tracker_$(date +%Y%m%d).db

# Start service
statement-tracker start
```

**PostgreSQL:**
```bash
# Backup
pg_dump statement_tracker > backups/statement_tracker_$(date +%Y%m%d).sql

# Or use compressed backup
pg_dump statement_tracker | gzip > backups/statement_tracker_$(date +%Y%m%d).sql.gz
```

### Restore Database

**SQLite:**
```bash
statement-tracker stop
cp backups/statement_tracker_20260214.db statement_tracker.db
statement-tracker start
```

**PostgreSQL:**
```bash
# Restore
psql statement_tracker < backups/statement_tracker_20260214.sql

# Or from compressed
gunzip -c backups/statement_tracker_20260214.sql.gz | psql statement_tracker
```

### Export/Import Catalog

```bash
# Export all providers and statements
statement-tracker export --output full_catalog.json

# Import catalog
statement-tracker import --input full_catalog.json

# Merge with existing (don't overwrite)
statement-tracker import --input full_catalog.json --merge
```

---

## Performance Tips

### For Large Document Collections (10,000+)

1. **Use PostgreSQL instead of SQLite**
2. **Enable caching:**
   ```yaml
   cache:
     enabled: true
     ttl: 3600  # 1 hour
   ```
3. **Increase batch size:**
   ```yaml
   analysis:
     batch_size: 200
   ```
4. **Run analysis during off-hours**
5. **Use incremental analysis** (only check new documents)

### For Slow API Calls

1. **Enable HTTP/2** (httpx with http2 extra)
2. **Increase timeout:**
   ```yaml
   paperless:
     timeout: 60
   ```
3. **Use connection pooling**
4. **Enable API caching in paperless-ngx**

---

## Security Checklist

- [ ] API token stored in environment variable, not config file
- [ ] Database password encrypted or in environment variable
- [ ] HTTPS enabled for web dashboard
- [ ] API authentication enabled
- [ ] Regular backups configured
- [ ] Logs don't contain sensitive data
- [ ] File permissions correct (600 for config, 700 for data directory)
- [ ] Firewall rules configured
- [ ] Dashboard not exposed to public internet (or behind auth)

---

## Getting Help

### Logs
```bash
# View recent logs
tail -f logs/statement_tracker.log

# Search for errors
grep ERROR logs/statement_tracker.log

# View specific provider logs
grep "provider_id=chase-visa-1234" logs/statement_tracker.log
```

### Debug Mode
```bash
# Run with verbose output
statement-tracker --debug serve

# Or set in config
logging:
  level: "DEBUG"
```

### Check System Status
```bash
# Full status check
statement-tracker status --detailed

# Shows:
# - Service status
# - Database connection
# - Paperless connection
# - Last analysis time
# - Provider count
# - Statement count
```

---

## Useful Queries

### Find statements in specific date range
```bash
statement-tracker statements list \
  --from "2025-01-01" \
  --to "2025-03-31"
```

### List providers by type
```bash
statement-tracker providers list --type credit_card
statement-tracker providers list --type utility
statement-tracker providers list --type insurance
```

### Get statistics
```bash
statement-tracker stats

# Output:
# Providers: 23
# Statements: 456
# Missing (last 90 days): 3
# Average confidence: 0.87
# Coverage: 98.5%
```

---

## Integration Examples

### n8n Workflow Trigger

When statement is detected as missing, trigger n8n workflow:

```json
{
  "webhook_url": "https://n8n.example.com/webhook/statement-missing",
  "payload": {
    "provider": "Chase Visa",
    "period": "2025-02",
    "expected_date": "2025-02-28",
    "priority": 7
  }
}
```

### Home Assistant Integration

Create sensor for missing statement count:

```yaml
# configuration.yaml
sensor:
  - platform: rest
    name: "Missing Statements"
    resource: "http://localhost:8001/api/recommendations/count"
    headers:
      Authorization: "Bearer YOUR_TOKEN"
    value_template: "{{ value_json.count }}"
    scan_interval: 3600  # Check hourly
```

---

**Document Version:** 1.0  
**Last Updated:** 2026-02-14  
**Status:** Reference Guide Complete
