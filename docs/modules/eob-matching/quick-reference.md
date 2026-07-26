---
title: "EOB Quick Reference"
sidebar_label: Quick Reference
sidebar_position: 3
---

# Quick Reference: Medical EOB & Bill Matching Implementation

## Overview

This quick reference provides step-by-step instructions for implementing the medical EOB and bill matching system. Choose your implementation approach and follow the corresponding guide.

---

## Implementation Approaches

### Option 1: Rule-Based MVP (Recommended Start)
**Timeline**: 2-3 weeks  
**Complexity**: Low-Medium  
**Accuracy**: 80-85%  
**Best For**: Proof of concept, getting started quickly

### Option 2: ML-Based
**Timeline**: 2-3 months  
**Complexity**: High  
**Accuracy**: 70-95%  
**Best For**: Long-term solution after collecting training data

### Option 3: Hybrid (Production)
**Timeline**: 1-2 months  
**Complexity**: Medium-High  
**Accuracy**: 85-95%  
**Best For**: Production deployment after MVP validation

---

## Quick Start: Rule-Based MVP

### Week 1: Setup & Foundation

#### Day 1-2: Environment Setup

```bash
# 1. Clone repository (if using)
git clone https://github.com/your-repo/medical-eob-matching.git
cd medical-eob-matching

# 2. Create virtual environment
python3.9 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and configure environment variables
cp .env.example .env
nano .env
```

**Configure `.env`:**
```env
# Paperless-ngx
PAPERLESS_BASE_URL=http://localhost:8000
PAPERLESS_API_TOKEN=your_token_here

# Database
DB_PATH=./data/medical.db
DB_ENCRYPTION_KEY=your_secure_key_here

# Logging
LOG_LEVEL=INFO
LOG_PATH=./logs/app.log
```

#### Day 3: Paperless Setup

Follow [SETUP-PAPERLESS.md](docs/SETUP-PAPERLESS.md):

1. Enable Paperless API
2. Generate API token
3. Create custom fields (8 fields)
4. Create tags (7 tags)
5. Run integration test

```bash
python scripts/test-paperless-api.py
```

**Expected**: All tests pass ✅

#### Day 4-5: Core Components

**File Structure:**
```
backend/
├── main.py                 # Entry point
├── config.py              # Configuration
├── models.py              # Data models
├── paperless_client.py    # Paperless API wrapper
├── classifier.py          # Document classification
├── extractor.py           # Data extraction
├── matcher.py             # Matching engine
└── database.py            # SQLite operations
```

**Create skeleton files:**
```bash
mkdir backend
cd backend
touch main.py config.py models.py paperless_client.py
touch classifier.py extractor.py matcher.py database.py
```

---

### Week 2: Core Implementation

#### Day 1-2: Document Classification

**File**: `backend/classifier.py`

```python
import re
from typing import Literal

DocType = Literal["EOB", "BILL", "OTHER"]

def classify_document(text: str, metadata: dict) -> DocType:
    """
    Classify document as EOB, Bill, or Other.
    Uses rule-based pattern matching.
    """
    text_lower = text.lower()
    eob_score = 0
    bill_score = 0
    
    # EOB patterns
    if "explanation of benefits" in text_lower:
        eob_score += 50
    if "this is not a bill" in text_lower:
        eob_score += 40
    if re.search(r"(united|aetna|blue cross|kaiser|cigna)", text_lower):
        eob_score += 30
    if "amount your plan pays" in text_lower:
        eob_score += 20
    
    # Bill patterns
    if any(word in text_lower for word in ["invoice", "amount due", "balance"]):
        bill_score += 40
    if "please remit payment" in text_lower:
        bill_score += 30
    if re.search(r"due date:?\s*\d{1,2}/\d{1,2}/\d{4}", text_lower):
        bill_score += 25
    
    # Decision
    if eob_score > bill_score and eob_score >= 60:
        return "EOB"
    elif bill_score > eob_score and bill_score >= 60:
        return "BILL"
    else:
        return "OTHER"
```

**Test:**
```bash
python -m pytest tests/test_classifier.py -v
```

#### Day 3-4: Data Extraction

**File**: `backend/extractor.py`

```python
import re
from datetime import datetime
from dateutil import parser

def extract_eob_data(text: str) -> dict:
    """Extract structured data from EOB document."""
    data = {
        "date_of_service": extract_date_of_service(text),
        "provider_name": extract_provider_name(text),
        "patient_name": extract_patient_name(text),
        "amounts": extract_amounts(text),
        "insurance_company": extract_insurance_company(text),
    }
    return data

def extract_date_of_service(text: str) -> str:
    """Extract date of service using regex patterns."""
    patterns = [
        r"date\s+of\s+service:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        r"service\s+date:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        r"DOS:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            date_str = match.group(1)
            try:
                date = parser.parse(date_str)
                return date.strftime("%Y-%m-%d")
            except:
                continue
    return None

def extract_amounts(text: str) -> dict:
    """Extract dollar amounts from document."""
    amounts = {}
    
    # Pattern: "Total: $123.45"
    total_pattern = r"total.*?:?\s*\$?([\d,]+\.?\d{0,2})"
    match = re.search(total_pattern, text, re.IGNORECASE)
    if match:
        amounts["total"] = float(match.group(1).replace(",", ""))
    
    # Add more patterns for other amounts
    return amounts
```

**Test:**
```bash
python -m pytest tests/test_extractor.py -v
```

#### Day 5: Matching Engine

**File**: `backend/matcher.py`

```python
from fuzzywuzzy import fuzz
from datetime import timedelta

def calculate_match_score(eob: dict, bill: dict) -> dict:
    """
    Calculate match score between EOB and Bill.
    Returns score 0-100 and confidence level.
    """
    date_score = score_date_similarity(eob["date_of_service"], bill["date_of_service"])
    provider_score = score_provider_similarity(eob["provider_name"], bill["provider_name"])
    patient_score = score_patient_similarity(eob["patient_name"], bill["patient_name"])
    amount_score = score_amount_similarity(eob["total_patient_resp"], bill["amount_due"])
    
    # Weighted average
    total_score = (
        date_score * 0.30 +
        provider_score * 0.25 +
        patient_score * 0.20 +
        amount_score * 0.15 +
        50 * 0.10  # Placeholder for procedure score
    )
    
    # Determine confidence
    if total_score >= 85:
        confidence = "HIGH"
    elif total_score >= 70:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    
    return {
        "score": total_score,
        "confidence": confidence,
        "breakdown": {
            "date": date_score,
            "provider": provider_score,
            "patient": patient_score,
            "amount": amount_score
        }
    }

def score_provider_similarity(eob_provider: str, bill_provider: str) -> float:
    """Use fuzzy matching for provider names."""
    if not eob_provider or not bill_provider:
        return 0
    
    ratio = fuzz.ratio(eob_provider.lower(), bill_provider.lower())
    token_sort = fuzz.token_sort_ratio(eob_provider.lower(), bill_provider.lower())
    partial = fuzz.partial_ratio(eob_provider.lower(), bill_provider.lower())
    
    return max(ratio, token_sort, partial)
```

**Test:**
```bash
python -m pytest tests/test_matcher.py -v
```

---

### Week 3: Integration & Dashboard

#### Day 1-2: Paperless Integration

**File**: `backend/paperless_client.py`

```python
import requests
from typing import List, Optional

class PaperlessClient:
    def __init__(self, base_url: str, api_token: str):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Token {api_token}"}
    
    def get_documents(self, filters: Optional[dict] = None) -> List[dict]:
        """Fetch documents from Paperless."""
        url = f"{self.base_url}/api/documents/"
        response = requests.get(url, headers=self.headers, params=filters)
        response.raise_for_status()
        return response.json()["results"]
    
    def update_document(self, doc_id: int, updates: dict) -> dict:
        """Update document tags, custom fields, etc."""
        url = f"{self.base_url}/api/documents/{doc_id}/"
        response = requests.patch(url, headers=self.headers, json=updates)
        response.raise_for_status()
        return response.json()
    
    def create_link(self, doc_id: int, target_doc_id: int) -> dict:
        """Create link between two documents."""
        url = f"{self.base_url}/api/documents/{doc_id}/links/"
        response = requests.post(
            url,
            headers=self.headers,
            json={"target_document": target_doc_id}
        )
        response.raise_for_status()
        return response.json()
```

#### Day 3: Processing Pipeline

**File**: `backend/main.py`

```python
from paperless_client import PaperlessClient
from classifier import classify_document
from extractor import extract_eob_data, extract_bill_data
from matcher import find_matches
from database import Database

def process_document(paperless_client, db, doc_id):
    """Main processing pipeline for a document."""
    # 1. Fetch document
    doc = paperless_client.get_document(doc_id)
    text = paperless_client.get_document_text(doc_id)
    
    # 2. Classify
    doc_type = classify_document(text, doc)
    if doc_type not in ["EOB", "BILL"]:
        return
    
    # 3. Extract data
    if doc_type == "EOB":
        data = extract_eob_data(text)
    else:
        data = extract_bill_data(text)
    
    # 4. Store in database
    db.insert_document(doc_id, doc_type, data)
    
    # 5. Update Paperless
    paperless_client.update_document(doc_id, {
        "tags": [get_tag_id(f"medical-{doc_type.lower()}")],
        "custom_fields": [
            {"field": 1, "value": doc_type}
        ]
    })
    
    # 6. Find matches
    matches = find_matches(doc_id, doc_type, data, db)
    
    # 7. Process high-confidence matches
    for match in matches:
        if match["confidence"] == "HIGH":
            create_match(paperless_client, db, doc_id, match)

def main():
    # Initialize clients
    paperless = PaperlessClient(BASE_URL, API_TOKEN)
    db = Database(DB_PATH)
    
    # Poll for new documents
    while True:
        new_docs = paperless.get_documents({"created__gte": last_check})
        for doc in new_docs:
            process_document(paperless, db, doc["id"])
        
        time.sleep(60)  # Check every minute

if __name__ == "__main__":
    main()
```

#### Day 4-5: Dashboard Setup

**Using AppSmith:**

1. Install AppSmith (Docker):
```bash
docker run -d --name appsmith -p 8080:80 \
  -v "$PWD/appsmith-data:/appsmith-stacks" \
  appsmith/appsmith-ce
```

2. Access: `http://localhost:8080`

3. Create new app: "Medical Bills Dashboard"

4. Connect to data sources:
   - SQLite database (via REST API)
   - FastAPI backend

5. Build pages following [UI-DESIGN.md](docs/UI-DESIGN.md):
   - Overview Dashboard
   - Match Review
   - Unmatched Documents
   - Payment Tracking

**See [UI-DESIGN.md](docs/UI-DESIGN.md) for detailed UI specifications.**

---

### Week 4: Testing & Refinement

#### Day 1-2: Integration Testing

**Test with real documents:**
1. Upload 5-10 EOBs to Paperless
2. Upload corresponding bills
3. Verify classification accuracy
4. Check match quality
5. Test dashboard functionality

**Metrics to track:**
- Classification accuracy
- Match accuracy
- False positive rate
- Processing time per document

#### Day 3: Pattern Refinement

Based on test results, refine:
- Classification patterns
- Extraction regex
- Matching thresholds
- Confidence scoring

#### Day 4-5: Documentation & Deployment

1. Document any custom patterns added
2. Create user guide for dashboard
3. Set up monitoring and alerts
4. Deploy to production environment

---

## n8n Workflow Setup

### Install n8n

```bash
# Docker
docker run -d --name n8n -p 5678:5678 \
  -v "$PWD/n8n-data:/home/node/.n8n" \
  n8nio/n8n

# Access: http://localhost:5678
```

### Create Workflow

1. **Trigger**: Schedule (every 5 minutes)
2. **HTTP Request**: Get new documents from Paperless
3. **Filter**: Only unprocessed documents
4. **HTTP Request**: POST to processing API
5. **Condition**: Check for matches
6. **Send Email**: Alert on high-value matches or errors

**See [workflows/README.md](workflows/README.md) for n8n workflow JSON.**

---

## Maintenance Guide

### Daily Tasks
- Check dashboard for alerts
- Review new matches
- Approve/reject uncertain matches

### Weekly Tasks
- Review unmatched documents
- Check for processing errors
- Validate payment status accuracy

### Monthly Tasks
- Analyze match accuracy metrics
- Refine classification patterns
- Update extraction rules
- Back up database

---

## Common Commands

```bash
# Run processing pipeline
python backend/main.py

# Run API server (for dashboard)
python backend/api.py

# Run tests
pytest tests/ -v

# Check logs
tail -f logs/app.log

# Backup database
sqlite3 data/medical.db ".backup data/medical-backup-$(date +%Y%m%d).db"

# Clear cache
rm -rf data/cache/*
```

---

## Troubleshooting

### Issue: Classification Incorrect

**Solution**: Add patterns to `classifier.py`
```python
# Add more EOB indicators
if "your insurance has paid" in text_lower:
    eob_score += 25
```

### Issue: Extraction Missing Fields

**Solution**: Add more regex patterns to `extractor.py`
```python
# Add alternative date format
r"service:?\s*(\d{1,2}/\d{1,2}/\d{2,4})"
```

### Issue: No Matches Found

**Solution**: Lower matching threshold temporarily
```python
# In matcher.py
if total_score >= 60:  # Was 70
    confidence = "MEDIUM"
```

---

## Upgrading to Hybrid Approach

After MVP is working:

1. **Collect Training Data**
   - Export 50-100 labeled documents
   - Include edge cases and errors

2. **Train ML Models**
   ```bash
   python scripts/train_classifier.py
   python scripts/train_ner.py
   ```

3. **Integrate ML Components**
   - Use ML as fallback for uncertain classifications
   - Combine rule-based and ML extraction
   - Implement confidence voting

4. **Monitor Performance**
   - Track accuracy improvements
   - A/B test rule-based vs hybrid
   - Continuously retrain models

---

## Resources

- [Full Design Documentation](docs/DESIGN.md)
- [Technology Stack](docs/TECHNOLOGY-STACK.md)
- [UI Design Specifications](docs/UI-DESIGN.md)
- [Paperless Setup Guide](docs/SETUP-PAPERLESS.md)
- [Experiment Summary](SUMMARY.md)

---

## Getting Help

- Review logs: `logs/app.log`
- Check test output: `pytest tests/ -v`
- Paperless API docs: `http://your-paperless-url/api/docs`
- Open issue in repository
- Community support forums

---

*Document Version: 1.0*  
*Last Updated: 2026-02-14*  
*Status: Quick Reference Complete*
