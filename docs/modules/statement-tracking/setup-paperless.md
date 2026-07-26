---
title: "Paperless Setup for Statements"
sidebar_label: Paperless Setup
sidebar_position: 6
---

# Paperless-ngx Integration Setup Guide

## Overview

This guide covers how to set up the connection between the Statement Tracking System and your paperless-ngx instance.

---

## Prerequisites

- Running paperless-ngx instance (v1.17.0 or later recommended)
- Admin access to paperless-ngx
- Network connectivity between statement tracker and paperless-ngx

---

## Step 1: Generate API Token

### Via Web Interface

1. Log into your paperless-ngx instance
2. Navigate to **Settings** (gear icon in top right)
3. Click **API Tokens** in the left sidebar
4. Click **Create Token** button
5. Enter a name: `Statement Tracker`
6. Click **Create**
7. **Copy the token immediately** - it won't be shown again
8. Store token securely

### Via Command Line (Alternative)

```bash
# SSH into your paperless-ngx server
ssh user@paperless-server

# Enter paperless container
docker exec -it paperless-ngx bash

# Create token
python manage.py create_api_token statement_tracker

# Output will be:
# Token created: <generated-token>
```

---

## Step 2: Test API Access

### Test with ***REMOVED***

```bash
# Replace with your paperless URL and token
export PAPERLESS_URL="http://localhost:8000"
export PAPERLESS_TOKEN="your-token-here"

# Test basic connectivity
***REMOVED*** -H "Authorization: Token $PAPERLESS_TOKEN" \
     "$PAPERLESS_URL/api/"

# Expected output: JSON with API version info

# Test document access
***REMOVED*** -H "Authorization: Token $PAPERLESS_TOKEN" \
     "$PAPERLESS_URL/api/documents/?page=1&page_size=5"

# Expected output: JSON with document list
```

### Test with Python

```python
import requests

PAPERLESS_URL = "http://localhost:8000"
PAPERLESS_TOKEN = "your-token-here"

headers = {"Authorization": f"Token {PAPERLESS_TOKEN}"}

# Test connection
response = requests.get(f"{PAPERLESS_URL}/api/", headers=headers)
print(f"Status: {response.status_code}")
print(f"API Version: {response.json()}")

# Fetch documents
response = requests.get(
    f"{PAPERLESS_URL}/api/documents/",
    headers=headers,
    params={"page": 1, "page_size": 5}
)
print(f"Document count: {response.json()['count']}")
```

---

## Step 3: Configure Statement Tracker

### Option 1: Config File Plus Environment Variable (Current Implementation)

Copy the example file and edit it for your environment:

```bash
cp config/config.paperless.example.yaml config/config.paperless.yaml
```

```yaml
# config/config.paperless.yaml
source:
  mode: paperless
  paperless_url: http://paperless:8000
  api_token_env: PAPERLESS_API_TOKEN
  verify_ssl: true
  timeout_seconds: 30

runtime:
  snapshot_path: ../data/catalog.snapshot.json

server:
  host: 0.0.0.0
  port: 8001
```

Then set the token in your shell or deployment environment:

```bash
export PAPERLESS_API_TOKEN="your-token-here"
```

PowerShell:

```powershell
$env:PAPERLESS_API_TOKEN = "your-token-here"
```

### Option 2: Docker Compose Environment

```yaml
# deploy/docker-compose.image.yaml
services:
  statement-tracker:
    image: service-007.example.invalid/statement-tracker:phase1
    environment:
      PAPERLESS_API_TOKEN: ${PAPERLESS_API_TOKEN}
    volumes:
      - ../config:/app/config:ro
      - ../data:/app/data
```

---

## Step 4: Organize Your Documents

For best results with statement tracking, organize your paperless documents:

### 1. Create Correspondents

Create correspondents for each statement provider:
- Chase Bank
- Comcast
- State Farm Insurance
- etc.

**How:**
1. Go to **Correspondents** in paperless
2. Click **Add Correspondent**
3. Enter name (e.g., "Chase Visa")
4. Save

### 2. Use Consistent Tags

Create and apply tags:
- `statement`
- `bill`
- `invoice`
- `credit-card`
- `utility`
- `insurance`

**Why:** Tags help the detection algorithm identify statement types.

### 3. Apply Document Types (Optional)

Create document types:
- Credit Card Statement
- Utility Bill
- Bank Statement
- Insurance Statement

**How:**
1. Go to **Document Types** in paperless
2. Click **Add Document Type**
3. Enter name
4. Save

### 4. Use Consistent File Naming (Recommended)

When possible, name your files with a pattern:
- `{provider}_{type}_{YYYY-MM}.pdf`
- Examples:
  - `chase_statement_2025-01.pdf`
  - `comcast_bill_2025-02.pdf`
  - `insurance_quarterly_2025-Q1.pdf`

**How:** Use paperless-ngx's filename handling:
- Settings → General → Document Filename Format
- Pattern: `{correspondent}/{document_type}_{created_year}-{created_month:02d}`

---

## Step 5: Verify Data Structure

Check that your documents have the necessary metadata:

```bash
# List documents with metadata
***REMOVED*** -H "Authorization: Token $PAPERLESS_TOKEN" \
     "$PAPERLESS_URL/api/documents/?page=1&page_size=10" | jq

# Check specific document
***REMOVED*** -H "Authorization: Token $PAPERLESS_TOKEN" \
     "$PAPERLESS_URL/api/documents/123/" | jq

# Important fields:
# - title: Document title
# - correspondent: Correspondent ID
# - created: Document date
# - tags: Tag IDs
# - document_type: Document type ID
```

---

## Step 6: Initial Discovery Run

Once everything is configured, run the initial discovery:

```bash
# Install the package locally first if needed
python -m pip install -e .[dev]

# Validate connectivity first
statement-tracker test-connection --config config/config.paperless.yaml

# Run discovery
statement-tracker discover --config config/config.paperless.yaml

# Expected output:
# Analyzing 2,345 documents...
# Found 23 potential statement providers:
# - Chase Visa (...1234): Monthly, confidence 0.92
# - Comcast: Monthly, confidence 0.88
# - State Farm Insurance: Quarterly, confidence 0.85
# ...
```

To expose the API locally after the smoke test:

```bash
statement-tracker serve --config config/config.paperless.yaml
```

---

## Paperless API Reference

### Key Endpoints Used

#### List Documents
```http
GET /api/documents/
Parameters:
  - page: Page number
  - page_size: Results per page
  - correspondent__id: Filter by correspondent
  - tags__id__all: Filter by tags
  - created__date__gte: Filter by date (greater than or equal)
  - ordering: Sort order (e.g., "-created")
```

#### Get Document
```http
GET /api/documents/{id}/
```

#### List Correspondents
```http
GET /api/correspondents/
```

#### List Tags
```http
GET /api/tags/
```

#### List Document Types
```http
GET /api/document_types/
```

### Response Format

**Document Object:**
```json
{
  "id": 8742,
  "correspondent": 42,
  "document_type": 5,
  "title": "Chase Statement - January 2025",
  "content": "Full text content...",
  "tags": [12, 34],
  "created": "2025-02-03",
  "created_date": "2025-02-03",
  "added": "2025-02-05T14:23:00Z",
  "modified": "2025-02-05T14:23:00Z",
  "archive_serial_number": null,
  "original_file_name": "chase_statement_2025-01.pdf",
  "archived_file_name": "0008742.pdf"
}
```

---

## Troubleshooting

### Error: "Connection refused"

**Cause:** Cannot reach paperless-ngx

**Solutions:**
1. Verify paperless is running: `docker ps | grep paperless`
2. Check URL is correct
3. If using Docker, ensure containers are on same network
4. Check firewall rules

### Error: "401 Unauthorized"

**Cause:** Invalid or missing API token

**Solutions:**
1. Verify token is correct
2. Regenerate token in paperless
3. Check token hasn't expired
4. Ensure token is properly formatted in request

### Error: "No documents found"

**Cause:** No documents in paperless or permissions issue

**Solutions:**
1. Verify documents exist: Check paperless web interface
2. Check API token permissions
3. Try fetching documents directly via ***REMOVED***
4. Review paperless logs

### Error: "Timeout"

**Cause:** Request taking too long

**Solutions:**
1. Increase timeout in configuration
2. Reduce page size (fetch fewer documents at once)
3. Check paperless performance
4. Check database performance

---

## Performance Optimization

### For Large Document Collections

**Pagination:**
```python
# Fetch in smaller batches
page_size = 100  # Default is 25, max is 100
page = 1

while True:
    response = get_documents(page=page, page_size=page_size)
    documents = response['results']
    
    if not documents:
        break
    
    process_documents(documents)
    page += 1
```

**Filtering:**
```python
# Only fetch documents since last sync
since_date = "2025-02-01"
documents = get_documents(created__date__gte=since_date)
```

**Specific Correspondents:**
```python
# Only fetch from statement correspondents
correspondent_ids = [42, 43, 44]  # Known statement providers
for correspondent_id in correspondent_ids:
    documents = get_documents(correspondent__id=correspondent_id)
```

### Caching

```python
# Cache correspondent and tag metadata
correspondents = get_correspondents()  # Cache for 1 hour
tags = get_tags()  # Cache for 1 hour

# Only fetch new documents
last_sync = load_last_sync_time()
new_docs = get_documents(added__gte=last_sync)
```

---

## Security Best Practices

### API Token Security

1. **Store Securely:**
   - Use environment variables
   - Never commit to version control
   - Use secret management (e.g., Docker secrets)

2. **Rotate Regularly:**
   - Generate new token every 6 months
   - Update configuration
   - Delete old token in paperless

3. **Limit Scope:**
   - Use read-only token if possible
   - Create dedicated user for statement tracker
   - Audit API access logs

### Network Security

1. **Use HTTPS:**
   ```yaml
   paperless:
     url: "https://paperless.example.com"
     verify_ssl: true
   ```

2. **Restrict Access:**
   - Firewall rules to limit access
   - VPN if accessing remotely
   - Internal network only if possible

3. **Monitor Access:**
   - Review paperless access logs
   - Set up alerts for unusual activity

---

## Advanced Configuration

### Custom API Client

```python
from statement_tracker.paperless import PaperlessClient

# Advanced configuration
client = PaperlessClient(
    base_url="http://localhost:8000",
    api_token="your-token",
    timeout=60,  # Longer timeout
    max_retries=3,
    retry_delay=5,
    verify_ssl=True,
    user_agent="StatementTracker/1.0"
)

# With connection pooling
client = PaperlessClient(
    base_url="http://localhost:8000",
    api_token="your-token",
    connection_pool_size=10,
    connection_pool_maxsize=20
)
```

### Webhook Integration (Future)

For real-time updates when documents are added:

```python
# Paperless can call webhook on document creation
@app.post("/webhooks/paperless/document-added")
async def handle_document_added(document_data: dict):
    # Analyze new document immediately
    await analyze_document(document_data['document_id'])
    return {"status": "processed"}
```

---

## Maintenance

### Regular Tasks

**Weekly:**
- Verify API connectivity
- Check for failed syncs
- Review detection accuracy

**Monthly:**
- Rotate API token (optional)
- Review paperless performance
- Clean up test data

**Quarterly:**
- Update paperless-ngx (check compatibility)
- Review and optimize queries
- Archive old analysis logs

---

## Support Resources

- **Paperless-ngx Documentation:** https://docs.paperless-ngx.com/
- **Paperless-ngx API Docs:** https://docs.paperless-ngx.com/api/
- **Paperless-ngx GitHub:** https://github.com/paperless-ngx/paperless-ngx
- **Paperless-ngx Discord:** https://discord.gg/eMPF5Ss7Zd

---

**Document Version:** 1.0  
**Last Updated:** 2026-02-14  
**Paperless-ngx Version:** 1.17.0+
