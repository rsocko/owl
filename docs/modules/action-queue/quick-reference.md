---
title: "Action Queue Quick Reference"
sidebar_label: Quick Reference
sidebar_position: 3
---

# Quick Reference Guide

## At-a-Glance Overview

**Project:** Paperless-NGX Action Queue Agent  
**Status:** Planning & Design Phase  
**Goal:** Automate document action management with AI

## Core Concepts

### What It Does
1. Scans Paperless-NGX for documents tagged "Inbox" or "Todo"
2. Uses AI to extract information and recommend actions
3. Presents actions in a prioritized dashboard
4. Learns from user feedback to improve accuracy

### Action Categories
| Category | Example Documents | Typical Actions |
|----------|------------------|-----------------|
| PAY | Bills, invoices | Make payment by due date |
| RESPOND | Forms, letters | Submit response, provide info |
| FILE | Statements, receipts | Categorize and archive |
| REVIEW | Contracts, notices | Read and understand |
| SHARE | Tax docs, reports | Send to accountant/family |
| SCHEDULE | Appointments, renewals | Add to calendar |
| SIGN | Contracts, forms | Sign and return |
| ARCHIVE | Processed items | Move to long-term storage |

## Recommended Stack (MVP)

### Backend
- **Framework:** FastAPI (Python)
- **Database:** SQLite (upgrade to PostgreSQL later)
- **API Client:** HTTPX for Paperless-NGX

### AI/ML
- **NER:** spaCy (en_core_web_trf)
- **Classification:** DistilBERT or rule-based
- **Extraction:** Regex + dateparser library
- **Future:** Phi-3 Mini (SLM) for unified processing

### Frontend
- **MVP:** Streamlit (Python-based)
- **Production:** Vue.js + Vuetify
- **Integration:** Home Assistant custom component

### Orchestration
- **Scheduling:** n8n or systemd timers
- **Containerization:** Docker + Docker Compose

## Quick Start Commands

### Development Setup
```bash
# Clone and setup
git clone <repo>
cd paperless-action-queue

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install -r requirements.txt

# Download ML models
python -m spacy download en_core_web_trf

# Set environment variables
cp .env.example .env
# Edit .env with your Paperless-NGX credentials

# Initialize database
python scripts/init_db.py

# Run development server
uvicorn api.main:app --reload
```

### Docker Deployment
```bash
# Build and start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down

# Rebuild after changes
docker-compose up -d --build
```

## API Endpoints (Future Implementation)

### Actions
```
GET    /actions                 # List all actions
GET    /actions/{id}            # Get specific action
POST   /actions/{id}/complete   # Mark action complete
POST   /actions/{id}/dismiss    # Dismiss action
PATCH  /actions/{id}            # Update action details
```

### Documents
```
GET    /documents               # List documents from Paperless
POST   /documents/{id}/analyze  # Trigger re-analysis
```

### Feedback
```
POST   /feedback                # Submit user feedback
GET    /feedback/stats          # Get feedback statistics
```

### Admin
```
POST   /admin/sync              # Trigger manual sync
GET    /admin/stats             # System statistics
```

## Configuration Options

### Environment Variables
```bash
# Paperless-NGX
PAPERLESS_URL=http://paperless:8000
PAPERLESS_TOKEN=your_api_token

# Database
DATABASE_URL=sqlite:///./actions.db
# DATABASE_URL=postgresql://user:pass@localhost/paperless_actions

# ML Models
ML_SERVICE_URL=http://ml-service:8001
MODEL_PATH=/models

# Application
DEBUG=false
LOG_LEVEL=INFO
CONFIDENCE_THRESHOLD=80  # Only show actions above this confidence
```

### n8n Workflow Configuration
```json
{
  "schedule": "0 2 * * *",
  "tags": ["Inbox", "Todo"],
  "confidence_threshold": 80,
  "notify_on_urgent": true,
  "auto_mark_processed": true
}
```

## Database Schema

### Actions Table
```sql
CREATE TABLE actions (
    action_id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    title TEXT NOT NULL,
    due_date DATE,
    amount DECIMAL(10,2),
    urgency TEXT,
    confidence INTEGER,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Key Indexes
```sql
CREATE INDEX idx_status ON actions(status);
CREATE INDEX idx_due_date ON actions(due_date);
CREATE INDEX idx_urgency ON actions(urgency);
```

## Common Tasks

### Adding a New Action Type
1. Update `ACTION_TYPES` enum in models
2. Add classification rule in `analyzer.py`
3. Add mapping in intent detector
4. Update UI to handle new type

### Retraining Classification Model
```python
# scripts/train_classifier.py
from services.ml_engine import ClassifierTrainer

trainer = ClassifierTrainer()
trainer.load_training_data('data/labeled_documents.csv')
trainer.train()
trainer.save_model('models/classifier/')
```

### Manual Document Analysis
```bash
# Analyze specific document
curl -X POST http://localhost:8000/documents/12345/analyze

# Force re-analysis of all documents
curl -X POST http://localhost:8000/admin/sync?force=true
```

## Troubleshooting

### Issue: ML Models Not Loading
```bash
# Check model files exist
ls -lh models/

# Re-download spaCy model
python -m spacy download en_core_web_trf --force

# Verify model path in config
echo $MODEL_PATH
```

### Issue: Paperless API Connection Failed
```bash
# Test API connectivity
Authorization: ${PAPERLESS_AUTH_HEADER} \
     http://paperless:8000/api/documents/

# Check token permissions in Paperless settings
# Ensure token has read access to documents

# Verify network connectivity
docker exec api ping paperless
```

### Issue: Low Confidence Scores
1. Check if document OCR is complete in Paperless
2. Verify document text quality
3. Adjust confidence threshold
4. Review and improve training data
5. Consider using SLM for better understanding

### Issue: Duplicate Actions Created
```bash
# Check processing history
SELECT * FROM processing_history WHERE document_id = 12345;

# Clear history for specific document
DELETE FROM processing_history WHERE document_id = 12345;

# Rebuild deduplication index
python scripts/rebuild_dedup_index.py
```

## Performance Tuning

### Optimize Database Queries
```python
# Use eager loading
actions = session.query(Action).options(
    joinedload(Action.document)
).all()

# Add pagination
actions = session.query(Action)\
    .order_by(Action.due_date)\
    .limit(20).offset(0).all()
```

### Speed Up ML Inference
```python
# Batch processing
documents = fetch_documents()
texts = [doc['text'] for doc in documents]
results = model.predict_batch(texts)  # Faster than loop

# Model caching
from functools import lru_cache

@lru_cache(maxsize=128)
def classify_document(text_hash):
    return classifier.predict(text)
```

### Reduce Memory Usage
```python
# Use quantized models
from transformers import AutoModelForSequenceClassification
model = AutoModelForSequenceClassification.from_pretrained(
    "distilbert-base-uncased",
    torch_dtype="int8"  # 4x smaller
)

# Stream results instead of loading all at once
def get_actions_streaming():
    for action in query.yield_per(10):
        yield action
```

## Testing

### Run Tests
```bash
# All tests
pytest

# Specific test file
pytest tests/test_extractor.py

# With coverage
pytest --cov=api --cov-report=html

# Fast tests only (skip ML)
pytest -m "not ml"
```

### Test Coverage Goals
- Unit tests: > 80% coverage
- Integration tests: Core workflows
- E2E tests: Complete user journeys

## Monitoring

### Health Check
```bash
# API health
curl http://localhost:8000/health

# ML service health
curl http://localhost:8001/health

# Database health
psql -U user -d paperless_actions -c "SELECT 1"
```

### Key Metrics to Monitor
- Actions created per day
- Average confidence score
- Processing time per document
- User completion rate
- Dismissal rate (feedback quality)
- API response times

## Backup & Recovery

### Database Backup
```bash
# PostgreSQL
pg_dump paperless_actions > backup_$(date +%Y%m%d).sql

# SQLite
sqlite3 actions.db ".backup backup_$(date +%Y%m%d).db"

# Restore
psql paperless_actions < backup_20260214.sql
```

### Model Backup
```bash
# Backup trained models
tar -czf models_backup_$(date +%Y%m%d).tar.gz models/

# Restore
tar -xzf models_backup_20260214.tar.gz
```

---

## OCR Quality Assessment — Quick Reference

### Design Decision Summary
- **Assess first** — run a non-mutating corpus inventory and human calibration.
- **Use two dimensions** — overlay/readability and machine-extraction quality.
- **Review in OWL** — Paperless remains the document system of record.
- **Preserve the source** — never rewrite the exact file originally ingested.
- **Stage candidates** — Tesseract and Azure produce separate searchable PDFs;
  their outputs are never merged.
- **Require explicit acceptance** — no fixed score delta, automatic replacement,
  `accept all`, or scheduled remediation in the initial release.
- **Use Paperless versions** — preserve the prior usable version before an
  accepted candidate becomes latest.
- **Keep n8n optional** — OWL owns run, candidate, comparison, and decision state.
- **Defer LLM review** — any future secondary reviewer is advisory only.

### Initial workflow

1. Verify deployed Paperless original/archive/version behavior.
2. Inventory the 8,000+ document corpus without mutation.
3. Calibrate separate quality dimensions against human review.
4. Review current-document quality in OWL.
5. Generate a candidate for one document or a capped explicit batch.
6. Compare PDFs, text, geometry, and downstream extraction in OWL.
7. Accept or reject each document explicitly.
8. Apply accepted candidates as Paperless versions with rollback.

### OCR Design Documentation

| Document | Purpose |
|---------|---------|
| [OCR Quality Design](../ocr-quality/ocr-quality-design.md) | OWL-first architecture, Paperless artifacts and versions, review policy |
| [OCR Quality Scoring](../ocr-quality/ocr-quality-scoring.md) | Overlay/readability and machine-extraction score contract |
| [OCR Candidate Engine](../ocr-quality/ocr-remediation-engine.md) | Independent providers, comparison, Paperless application, rollback |
| [OCR Orchestration](../ocr-quality/ocr-n8n-workflow.md) | OWL-owned manual, batch, event, and schedule contract |
| [OCR Secondary Review](../ocr-quality/ocr-ollama-integration.md) | Deferred provider-neutral advisory review |
| [OCR Baseline Inventory](../ocr-quality/ocr-baseline-inventory.md) | Full-corpus assessment, human calibration, engine bake-off |

### Implementation Phases

- [ ] **Phase 0** — Verify deployed Paperless version and artifact/version APIs
- [ ] **Phase 1** — Run non-mutating corpus inventory and human calibration
- [ ] **Phase 2** — Implement multidimensional scoring and OWL review UI
- [ ] **Phase 3** — Generate staged Tesseract and Azure candidates
- [ ] **Phase 4** — Apply explicitly accepted candidates as Paperless versions
- [ ] **Phase 5** — Validate rollback, orchestration, and end-to-end failure isolation
- [ ] **Phase 6** — Evaluate advisory secondary review only if calibration supports it

---

## Useful Resources

### Documentation
- [Full Design Doc](./design.md)
- [Technology Stack](../../archive/action-queue-technology-stack.md)
- [UI Design](../../archive/action-queue-ui-design.md)
- [Paperless-NGX API](https://docs.paperless-ngx.com/api/)

### Community & Support
- [Paperless-NGX Community](https://github.com/paperless-ngx/paperless-ngx/discussions)
- [Home Assistant Forums](https://community.home-assistant.io/)
- [FastAPI Discord](https://discord.gg/fastapi)

### Related Projects
- [Paperless-GPT](https://github.com/icereed/paperless-gpt) - AI tagging for Paperless
- [Home Assistant Paperless Integration](https://github.com/tb1337/paperless-api)

## Next Steps

### Immediate (This Week)
1. ✅ Complete design documentation
2. ⏳ Set up development environment
3. ⏳ Create project skeleton
4. ⏳ Implement Paperless API client

### Short-term (This Month)
1. Build core extraction logic
2. Implement basic classification
3. Create simple Streamlit dashboard
4. Test with real documents

### Medium-term (Next 3 Months)
1. Train custom ML models
2. Implement feedback loop
3. Build production Vue.js UI
4. Deploy to homelab

### Long-term (6+ Months)
1. Home Assistant integration
2. Advanced AI with SLM
3. Mobile app
4. Community release

## Contact & Contribution

This is a personal homelab project, but feedback and ideas are welcome!

- **Repository:** [Link to be added]
- **Issues:** Use GitHub Issues for bug reports
- **Discussions:** Use GitHub Discussions for questions

---

*Last Updated: February 14, 2026*
