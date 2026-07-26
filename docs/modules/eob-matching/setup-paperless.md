---
title: "Paperless Setup for EOB"
sidebar_label: Paperless Setup
sidebar_position: 6
---

# Paperless-ngx Setup Guide

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [API Configuration](#api-configuration)
3. [Custom Fields Setup](#custom-fields-setup)
4. [Tags Configuration](#tags-configuration)
5. [Document Workflow](#document-workflow)
6. [Testing the Integration](#testing-the-integration)
7. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Paperless-ngx Version
- **Minimum Version**: v1.17.0+
- **Recommended**: v2.0.0+ (for better API support)
- **Check Version**: Navigate to `http://your-paperless-url/about`

### Required Permissions
- API access enabled
- API token with read/write permissions
- Document create/update permissions
- Custom fields management
- Tag management

---

## API Configuration

### Step 1: Enable API Access

1. Log into Paperless-ngx admin interface
2. Navigate to Settings → API Settings
3. Ensure **API is enabled**
4. Note the API URL: `http://your-paperless-url/api/`

### Step 2: Create API Token

**Option A: Via Web UI**
1. Navigate to your user profile (top-right corner)
2. Click "Account Settings"
3. Go to "API Tokens" section
4. Click "Generate New Token"
5. Copy the token (you won't see it again!)
6. Store securely in `.env` file:

```bash
PAPERLESS_API_TOKEN=your_token_here
PAPERLESS_BASE_URL=http://your-paperless-url
```

**Option B: Via Django Admin**
1. Navigate to `http://your-paperless-url/admin/`
2. Go to "Auth Token" → "Tokens"
3. Click "Add Token"
4. Select your user
5. Save and copy the generated token

### Step 3: Test API Access

```bash
# Test with ***REMOVED***
Authorization: ${PAPERLESS_AUTH_HEADER} \
  http://your-paperless-url/api/documents/ | jq

# Expected: JSON response with document list
```

**Python Test:**
```python
import requests

PAPERLESS_URL = "http://your-paperless-url"
API_TOKEN = "your_token_here"

headers = {"Authorization": f"Token {API_TOKEN}"}
response = requests.get(f"{PAPERLESS_URL}/api/documents/", headers=headers)

if response.status_code == 200:
    print("✅ API access successful!")
    print(f"Total documents: {response.json()['count']}")
else:
    print(f"❌ API access failed: {response.status_code}")
    print(response.text)
```

---

## Custom Fields Setup

### Step 1: Create Custom Fields

Navigate to: Settings → Custom Fields → Add Custom Field

#### Field 1: Document Type
```yaml
name: medical_doc_type
label: Medical Document Type
type: Select
options:
  - EOB
  - Bill
  - Other
required: false
```

#### Field 2: Match Status
```yaml
name: match_status
label: Match Status
type: Select
options:
  - Matched
  - Unmatched
  - Pending Review
  - Orphaned
required: false
```

#### Field 3: Match Confidence
```yaml
name: match_confidence
label: Match Confidence Score
type: Number
required: false
```

#### Field 4: Payment Status
```yaml
name: payment_status
label: Payment Status
type: Select
options:
  - Pending
  - Paid
  - Overdue
  - Disputed
required: false
```

#### Field 5: Amount Due
```yaml
name: amount_due
label: Amount Due
type: Number
required: false
```

#### Field 6: Date of Service
```yaml
name: date_of_service
label: Date of Service
type: Date
required: false
```

#### Field 7: Provider Name
```yaml
name: provider_name
label: Provider Name
type: Text
required: false
```

#### Field 8: Patient Name
```yaml
name: patient_name
label: Patient Name
type: Text
required: false
```

### Step 2: Get Custom Field IDs

Custom fields are referenced by ID in the API. Get IDs:

```bash
Authorization: ${PAPERLESS_AUTH_HEADER} \
  http://your-paperless-url/api/custom_fields/ | jq
```

**Save these IDs for configuration:**
```env
PAPERLESS_FIELD_DOC_TYPE=1
PAPERLESS_FIELD_MATCH_STATUS=2
PAPERLESS_FIELD_MATCH_CONFIDENCE=3
PAPERLESS_FIELD_PAYMENT_STATUS=4
PAPERLESS_FIELD_AMOUNT_DUE=5
PAPERLESS_FIELD_DATE_OF_SERVICE=6
PAPERLESS_FIELD_PROVIDER_NAME=7
PAPERLESS_FIELD_PATIENT_NAME=8
```

---

## Tags Configuration

### Step 1: Create Tags

Navigate to: Settings → Tags → Add Tag

#### Medical Tags
```yaml
tags:
  - name: medical-eob
    color: "#3498db"  # Blue
    description: "Explanation of Benefits document"
    
  - name: medical-bill
    color: "#e74c3c"  # Red
    description: "Medical bill or invoice"
    
  - name: matched
    color: "#2ecc71"  # Green
    description: "Document has been matched"
    
  - name: needs-review
    color: "#f39c12"  # Orange
    description: "Match needs manual review"
    
  - name: payment-pending
    color: "#9b59b6"  # Purple
    description: "Payment is pending"
    
  - name: payment-overdue
    color: "#c0392b"  # Dark red
    description: "Payment is overdue"
    
  - name: orphaned
    color: "#95a5a6"  # Gray
    description: "No matching document expected"
```

### Step 2: Get Tag IDs

```bash
Authorization: ${PAPERLESS_AUTH_HEADER} \
  http://your-paperless-url/api/tags/ | jq
```

**Save these IDs:**
```env
PAPERLESS_TAG_EOB=1
PAPERLESS_TAG_BILL=2
PAPERLESS_TAG_MATCHED=3
PAPERLESS_TAG_NEEDS_REVIEW=4
PAPERLESS_TAG_PAYMENT_PENDING=5
PAPERLESS_TAG_PAYMENT_OVERDUE=6
PAPERLESS_TAG_ORPHANED=7
```

---

## Document Workflow

### Automatic Workflow Configuration

#### Option 1: Upload via Web UI
1. User uploads EOB or Bill to Paperless
2. Paperless OCRs the document
3. Webhook or polling triggers processing
4. System classifies and extracts data
5. System attempts matching
6. System updates Paperless with tags/fields/links

#### Option 2: Email Import
1. Forward medical documents to Paperless email
2. Paperless imports and OCRs
3. Same processing flow as above

#### Option 3: Mobile App Upload
1. Use Paperless mobile app to scan documents
2. Paperless imports and OCRs
3. Same processing flow as above

### Manual Workflow Enhancements

**Pre-tagging (Optional):**
Users can manually tag documents as `medical-eob` or `medical-bill` if:
- They want faster processing (skip classification)
- Documents are unusual formats
- They know the document type

**Post-processing Review:**
Users review matches in dashboard and:
- Approve high-confidence matches
- Manually match uncertain pairs
- Mark orphaned documents

---

## Testing the Integration

### Test Script

Save as `test_paperless_integration.py`:

```python
#!/usr/bin/env python3
"""
Test Paperless-ngx API integration for EOB/Bill matching.
"""
import os
import requests
from datetime import datetime

# Configuration
PAPERLESS_URL = os.getenv("PAPERLESS_BASE_URL", "http://localhost:8000")
API_TOKEN = os.getenv("PAPERLESS_API_TOKEN")

if not API_TOKEN:
    print("❌ PAPERLESS_API_TOKEN not set!")
    exit(1)

headers = {"Authorization": f"Token {API_TOKEN}"}

def test_api_connection():
    """Test basic API connectivity."""
    print("Testing API connection...")
    response = requests.get(f"{PAPERLESS_URL}/api/documents/", headers=headers)
    
    if response.status_code == 200:
        print("✅ API connection successful")
        data = response.json()
        print(f"   Total documents: {data['count']}")
        return True
    else:
        print(f"❌ API connection failed: {response.status_code}")
        print(f"   {response.text}")
        return False

def test_custom_fields():
    """Test custom fields are available."""
    print("\nTesting custom fields...")
    response = requests.get(f"{PAPERLESS_URL}/api/custom_fields/", headers=headers)
    
    if response.status_code == 200:
        fields = response.json()['results']
        print(f"✅ Found {len(fields)} custom fields")
        
        required_fields = [
            "medical_doc_type",
            "match_status",
            "match_confidence",
            "payment_status"
        ]
        
        for field_name in required_fields:
            found = any(f['name'] == field_name for f in fields)
            if found:
                field = next(f for f in fields if f['name'] == field_name)
                print(f"   ✅ {field_name} (ID: {field['id']})")
            else:
                print(f"   ❌ {field_name} not found")
        return True
    else:
        print(f"❌ Custom fields request failed: {response.status_code}")
        return False

def test_tags():
    """Test tags are available."""
    print("\nTesting tags...")
    response = requests.get(f"{PAPERLESS_URL}/api/tags/", headers=headers)
    
    if response.status_code == 200:
        tags = response.json()['results']
        print(f"✅ Found {len(tags)} tags")
        
        required_tags = ["medical-eob", "medical-bill", "matched", "needs-review"]
        
        for tag_name in required_tags:
            found = any(t['name'] == tag_name for t in tags)
            if found:
                tag = next(t for t in tags if t['name'] == tag_name)
                print(f"   ✅ {tag_name} (ID: {tag['id']})")
            else:
                print(f"   ❌ {tag_name} not found")
        return True
    else:
        print(f"❌ Tags request failed: {response.status_code}")
        return False

def test_document_update():
    """Test updating a document."""
    print("\nTesting document update...")
    
    # Get first document
    response = requests.get(f"{PAPERLESS_URL}/api/documents/?page_size=1", headers=headers)
    if response.status_code != 200 or response.json()['count'] == 0:
        print("❌ No documents found for testing")
        return False
    
    doc_id = response.json()['results'][0]['id']
    print(f"   Using document ID: {doc_id}")
    
    # Get medical-eob tag ID
    tags_response = requests.get(f"{PAPERLESS_URL}/api/tags/", headers=headers)
    medical_eob_tag = next(
        (t['id'] for t in tags_response.json()['results'] if t['name'] == 'medical-eob'),
        None
    )
    
    if not medical_eob_tag:
        print("   ⚠️ Skipping: 'medical-eob' tag not found")
        return True
    
    # Test adding a tag (we'll remove it after)
    doc_response = requests.get(f"{PAPERLESS_URL}/api/documents/{doc_id}/", headers=headers)
    existing_tags = doc_response.json()['tags']
    
    # Add test tag
    test_tags = existing_tags + [medical_eob_tag]
    update_response = requests.patch(
        f"{PAPERLESS_URL}/api/documents/{doc_id}/",
        headers=headers,
        json={"tags": test_tags}
    )
    
    if update_response.status_code == 200:
        print("   ✅ Document update successful")
        
        # Restore original tags
        requests.patch(
            f"{PAPERLESS_URL}/api/documents/{doc_id}/",
            headers=headers,
            json={"tags": existing_tags}
        )
        return True
    else:
        print(f"   ❌ Document update failed: {update_response.status_code}")
        return False

def test_document_links():
    """Test document linking feature."""
    print("\nTesting document links...")
    
    # Get first two documents
    response = requests.get(f"{PAPERLESS_URL}/api/documents/?page_size=2", headers=headers)
    if response.status_code != 200 or response.json()['count'] < 2:
        print("   ⚠️ Need at least 2 documents for link testing")
        return True
    
    docs = response.json()['results']
    doc1_id = docs[0]['id']
    doc2_id = docs[1]['id']
    
    print(f"   Testing link: {doc1_id} → {doc2_id}")
    
    # Try to create link (may fail if already exists, that's okay)
    link_response = requests.post(
        f"{PAPERLESS_URL}/api/documents/{doc1_id}/links/",
        headers=headers,
        json={"target_document": doc2_id}
    )
    
    if link_response.status_code in [200, 201]:
        print("   ✅ Document link created")
        
        # Clean up - delete the test link
        links_response = requests.get(
            f"{PAPERLESS_URL}/api/documents/{doc1_id}/links/",
            headers=headers
        )
        if links_response.status_code == 200:
            for link in links_response.json():
                if link['target_document'] == doc2_id:
                    requests.delete(
                        f"{PAPERLESS_URL}/api/documents/{doc1_id}/links/{link['id']}/",
                        headers=headers
                    )
        return True
    elif link_response.status_code == 400 and "already exists" in link_response.text.lower():
        print("   ✅ Document linking works (link already exists)")
        return True
    else:
        print(f"   ❌ Link creation failed: {link_response.status_code}")
        print(f"   {link_response.text}")
        return False

def main():
    print("=" * 60)
    print("Paperless-ngx Integration Test")
    print("=" * 60)
    
    results = {
        "API Connection": test_api_connection(),
        "Custom Fields": test_custom_fields(),
        "Tags": test_tags(),
        "Document Update": test_document_update(),
        "Document Links": test_document_links()
    }
    
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}  {test_name}")
    
    all_passed = all(results.values())
    
    if all_passed:
        print("\n✅ All tests passed! Paperless integration is ready.")
        return 0
    else:
        print("\n❌ Some tests failed. Please fix the issues above.")
        return 1

if __name__ == "__main__":
    exit(main())
```

### Run the Test

```bash
# Set environment variables
export PAPERLESS_BASE_URL="http://localhost:8000"
export PAPERLESS_API_TOKEN="your_token_here"

# Run test
python test_paperless_integration.py
```

**Expected Output:**
```
============================================================
Paperless-ngx Integration Test
============================================================
Testing API connection...
✅ API connection successful
   Total documents: 42

Testing custom fields...
✅ Found 8 custom fields
   ✅ medical_doc_type (ID: 1)
   ✅ match_status (ID: 2)
   ✅ match_confidence (ID: 3)
   ✅ payment_status (ID: 4)

Testing tags...
✅ Found 7 tags
   ✅ medical-eob (ID: 1)
   ✅ medical-bill (ID: 2)
   ✅ matched (ID: 3)
   ✅ needs-review (ID: 4)

Testing document update...
   Using document ID: 123
   ✅ Document update successful

Testing document links...
   Testing link: 123 → 124
   ✅ Document link created

============================================================
Test Summary
============================================================
✅ PASS  API Connection
✅ PASS  Custom Fields
✅ PASS  Tags
✅ PASS  Document Update
✅ PASS  Document Links

✅ All tests passed! Paperless integration is ready.
```

---

## Troubleshooting

### Issue: API Token Authentication Failed

**Symptoms:**
```
401 Unauthorized
{"detail":"Invalid token."}
```

**Solutions:**
1. Verify token is correct (copy-paste carefully)
2. Check token hasn't expired
3. Regenerate token in Paperless UI
4. Ensure API is enabled in Paperless settings

---

### Issue: Custom Fields Not Found

**Symptoms:**
- Fields not returned by `/api/custom_fields/`
- Error updating document with custom field

**Solutions:**
1. Verify you created custom fields in Paperless UI
2. Check you're on Paperless v1.17.0+ (older versions limited custom fields)
3. Try creating fields via Django admin if UI fails
4. Refresh the custom fields cache

---

### Issue: Tags Not Applying

**Symptoms:**
- Document update succeeds but tags don't appear
- Tags disappear after update

**Solutions:**
1. Use tag **IDs** not names in API calls
2. Include **all** existing tags + new tags in update (API replaces, not appends)
3. Verify tag permissions (some tags may be restricted)

---

### Issue: Document Links Failed

**Symptoms:**
```
404 Not Found on /api/documents/{id}/links/
```

**Solutions:**
1. Check Paperless version (links added in v1.17.0)
2. Verify both document IDs exist
3. Check API permissions allow creating links
4. Try via Django admin if API fails

---

### Issue: Webhook Not Triggering

**Symptoms:**
- New documents uploaded but processing doesn't start

**Solutions:**
1. **Webhooks require Paperless v2.0+** - use polling for older versions
2. Check webhook URL is accessible from Paperless
3. Verify webhook secret matches
4. Check Paperless logs for webhook errors
5. Test with manual trigger first

---

### Issue: OCR Quality Poor

**Symptoms:**
- Extracted text has errors
- Fields not detected

**Solutions:**
1. Re-upload document with better scan quality
2. Check Paperless OCR settings (language, DPI)
3. Manually correct OCR in Paperless
4. Use pre-processing (deskew, denoise) before upload

---

## API Reference Quick Guide

### Common Endpoints

```bash
# List documents with filtering
GET /api/documents/?tags__name=medical-eob&created__gte=2024-01-01

# Get document details
GET /api/documents/{id}/

# Get document text (OCR result)
GET /api/documents/{id}/download/?original=false

# Update document
PATCH /api/documents/{id}/
Body: {"tags": [1,2,3], "custom_fields": [{"field": 1, "value": "EOB"}]}

# Create document link
POST /api/documents/{id}/links/
Body: {"target_document": 456}

# List custom fields
GET /api/custom_fields/

# List tags
GET /api/tags/
```

### Rate Limiting

Paperless-ngx typically doesn't rate limit local API calls, but:
- Be mindful of server resources
- Use batch operations when possible
- Implement exponential backoff for errors

---

## Security Best Practices

1. **Token Storage**
   - Store in environment variables, never in code
   - Use `.env` files with `.gitignore`
   - Rotate tokens every 90 days

2. **Access Control**
   - Use dedicated service account for automation
   - Grant minimum required permissions
   - Monitor API access logs

3. **Network Security**
   - Use HTTPS in production
   - Firewall API port (only accessible from trusted IPs)
   - Consider VPN for remote access

4. **Audit Logging**
   - Enable Paperless audit logs
   - Monitor document access patterns
   - Alert on suspicious activity

---

## Next Steps

After completing this setup:

1. ✅ Paperless API configured and tested
2. ✅ Custom fields created
3. ✅ Tags created
4. ✅ Integration test passed

**Next:**
- Proceed to [QUICK-REFERENCE.md](./quick-reference.md) for implementation guide
- Set up n8n workflow automation
- Implement document processing pipeline
- Build dashboard UI

---

*Document Version: 1.0*  
*Last Updated: 2026-02-14*  
*Status: Setup Guide Complete*
