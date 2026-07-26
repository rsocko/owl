---
title: "Technology Stack"
sidebar_label: Tech Stack
sidebar_position: 5
---

# Technology Stack Recommendations

## Overview

This document provides detailed technology recommendations for implementing the Statement Tracking and Management System. Each recommendation includes rationale, alternatives, and architectural decision records (ADRs).

---

## Core Technology Decisions

### ADR-001: Self-Hosted First Principle

**Status:** Accepted

**Context:**
Statement documents contain highly sensitive personal and financial information. Users store these in paperless-ngx specifically for privacy and control. Any statement tracking system must respect and extend these privacy principles.

**Decision:**
All document processing, analysis, and storage will happen on self-hosted infrastructure. No document content or metadata will be sent to external services without explicit user consent.

**Consequences:**
- ✅ Complete data privacy and control
- ✅ No subscription costs for external services
- ✅ Works offline once configured
- ✅ Aligns with paperless-ngx philosophy
- ❌ Higher local resource requirements
- ❌ User responsible for backups and maintenance
- ❌ May need more initial setup

**Implementation:**
- Use local databases (SQLite, PostgreSQL)
- Use self-hosted ML models (Ollama, llama.cpp) if ML is implemented
- Process all documents locally
- Store all data in user's infrastructure

---

### ADR-002: Programming Language - Python

**Status:** Accepted

**Context:**
Need to choose primary programming language for implementation. Requirements include strong paperless-ngx API integration, good data processing libraries, ML framework support, and ease of deployment.

**Decision:**
Use Python 3.8+ as the primary implementation language.

**Rationale:**
1. **paperless-ngx Integration**: Paperless itself is Python-based, ensuring good ecosystem fit
2. **Data Processing**: Excellent libraries (pandas, numpy, scipy)
3. **Date/Time Handling**: Robust datetime libraries (datetime, dateutil)
4. **ML Libraries**: If needed, scikit-learn, transformers, spaCy
5. **API Clients**: requests, httpx for REST APIs
6. **Web Frameworks**: Flask, FastAPI for dashboard
7. **Scheduling**: APScheduler for recurring tasks
8. **Community**: Large community, extensive documentation

**Alternatives Considered:**

**Node.js/TypeScript:**
- ✅ Good for web UIs and n8n integration
- ✅ Async I/O for API calls
- ❌ Weaker data science ecosystem
- ❌ Less mature date/time libraries
- ❌ Limited ML framework support

**Go:**
- ✅ Fast performance
- ✅ Easy deployment (single binary)
- ❌ Limited data science libraries
- ❌ No ML framework support
- ❌ Overkill for this use case

**Consequences:**
- ✅ Rich ecosystem for all requirements
- ✅ Easy to prototype and iterate
- ✅ Good ML integration path
- ✅ Strong date/time manipulation
- ❌ Runtime dependency (Python interpreter)
- ❌ May be slower than compiled languages (not critical for this use case)

---

### ADR-003: Database - SQLite for MVP, PostgreSQL for Production

**Status:** Accepted

**Context:**
Need to store provider catalog, statement records, patterns, and recommendations. Requirements include relationship support, querying capability, ACID compliance, and easy deployment.

**Decision:**
- **MVP/Single User:** SQLite
- **Production/Multi-User:** PostgreSQL
- **Schema Management:** Alembic migrations

**Rationale:**

**SQLite Advantages:**
- Zero configuration database
- Single file, easy backup
- Built into Python
- Perfect for single-user deployments
- Good performance for <10,000 records

**PostgreSQL Advantages:**
- Production-grade reliability
- Multi-user support
- Advanced querying (JSON, full-text search)
- Better performance at scale
- Standard for self-hosted apps

**Alternatives Considered:**

**JSON Files:**
- ✅ Simplest implementation
- ✅ Human-readable
- ❌ No transaction support
- ❌ Poor query performance
- ❌ Concurrent access issues

**MongoDB:**
- ✅ Flexible schema
- ✅ Good for JSON documents
- ❌ Overkill for structured data
- ❌ Heavier resource usage
- ❌ Relationships more complex

**Consequences:**
- ✅ Easy to start with SQLite
- ✅ Clear migration path to PostgreSQL
- ✅ Standard SQL, wide support
- ✅ Alembic makes schema evolution easy
- ❌ Need to support two database backends

**Implementation:**
```python
# Use SQLAlchemy for database abstraction
from sqlalchemy import create_engine

# Development/Single User
engine = create_engine('sqlite:///statement_tracker.db')

# Production
engine = create_engine('postgresql://user:pass@localhost/statement_tracker')
```

---

### ADR-004: API Framework - FastAPI

**Status:** Accepted

**Context:**
Need to provide API for dashboard UI and potential future integrations. Requirements include REST API, async support, automatic documentation, and type safety.

**Decision:**
Use FastAPI for the API layer.

**Rationale:**
1. **Modern Async**: Built on ASGI, excellent async support
2. **Type Safety**: Leverages Python type hints
3. **Auto Documentation**: OpenAPI/Swagger UI generated automatically
4. **Validation**: Pydantic for request/response validation
5. **Performance**: One of the fastest Python frameworks
6. **Easy Testing**: Built-in test client
7. **Growing Adoption**: Active community, good documentation

**Alternatives Considered:**

**Flask:**
- ✅ Simpler, more established
- ✅ Larger ecosystem
- ❌ Less modern architecture
- ❌ Sync by default
- ❌ Manual API documentation

**Django REST Framework:**
- ✅ Very comprehensive
- ✅ Built-in admin
- ❌ Heavy for this use case
- ❌ More boilerplate
- ❌ Opinionated structure

**Consequences:**
- ✅ Modern, maintainable codebase
- ✅ Excellent developer experience
- ✅ Auto-generated API docs
- ✅ Type safety catches bugs early
- ❌ Newer, smaller community than Flask
- ❌ ASGI deployment (but good options exist)

**Implementation:**
```python
from fastapi import FastAPI, Depends
from pydantic import BaseModel

app = FastAPI(title="Statement Tracker API")

@app.get("/api/providers")
async def list_providers():
    return await get_all_providers()

@app.get("/api/recommendations")
async def get_recommendations():
    return await detect_missing_statements()
```

---

### ADR-005: Frontend Framework - React + TypeScript

**Status:** Accepted

**Context:**
Need to build dashboard UI for reviewing providers, confirming statements, and viewing recommendations. Requirements include component reusability, type safety, and good developer experience.

**Decision:**
Use React with TypeScript for the frontend dashboard.

**Rationale:**
1. **Component-Based**: Perfect for modular UI (provider cards, statement lists)
2. **Type Safety**: TypeScript catches errors at compile time
3. **Rich Ecosystem**: Countless UI component libraries
4. **Developer Experience**: Hot reload, good tooling
5. **Community**: Largest frontend community
6. **Charting**: Excellent libraries (Recharts, Chart.js) for visualizations

**Recommended Stack:**
- **Framework**: React 18+
- **Language**: TypeScript
- **Build Tool**: Vite (fast, modern)
- **UI Library**: Ant Design or Material-UI
- **State Management**: React Query for API state, Zustand for UI state
- **Routing**: React Router v6
- **Forms**: React Hook Form
- **Date Picker**: react-datepicker
- **Charts**: Recharts

**Alternatives Considered:**

**Vue.js:**
- ✅ Simpler learning curve
- ✅ Good reactivity system
- ❌ Smaller ecosystem than React
- ❌ Less TypeScript integration

**Svelte:**
- ✅ Very fast, minimal code
- ✅ Great developer experience
- ❌ Smaller ecosystem
- ❌ Less mature for complex apps

**Plain HTML/JavaScript:**
- ✅ No build step
- ✅ Simpler deployment
- ❌ Hard to maintain complex UIs
- ❌ Manual DOM manipulation
- ❌ Poor developer experience

**Consequences:**
- ✅ Modern, maintainable frontend
- ✅ Type safety across stack
- ✅ Rich component ecosystem
- ✅ Good for complex UIs
- ❌ Build step required
- ❌ Larger bundle size than minimal approaches

---

### ADR-006: ML Framework - spaCy + DistilBERT (If ML Approach)

**Status:** Proposed (for future ML implementation)

**Context:**
If implementing ML-based approach (Approach 2 or 3), need to choose ML frameworks that can run locally/self-hosted while providing good accuracy.

**Decision:**
- **NLP/NER**: spaCy for named entity recognition
- **Document Classification**: DistilBERT via transformers library
- **Time Series**: Prophet for recurrence prediction
- **Inference**: Ollama for additional LLM capabilities (optional)

**Rationale:**

**spaCy:**
- Self-hosted, privacy-preserving
- Efficient CPU/GPU inference
- Pre-trained models available
- Easy to fine-tune on custom data
- Excellent documentation

**DistilBERT:**
- Smaller, faster than BERT
- Good accuracy (97% of BERT's performance)
- Can run on CPU reasonably well
- Pre-trained on text classification
- Easy to fine-tune

**Prophet:**
- Purpose-built for time series
- Handles missing data well
- Automatically detects patterns
- Works with irregular intervals
- Interpretable forecasts

**Alternatives Considered:**

**GPT Models via OpenAI API:**
- ✅ State-of-art accuracy
- ✅ No training required
- ❌ Sends sensitive data to external service
- ❌ Ongoing API costs
- ❌ Requires internet connection
- **Status**: REJECTED due to privacy concerns

**Full BERT Models:**
- ✅ Better accuracy than DistilBERT
- ❌ Much slower inference
- ❌ High memory requirements
- ❌ Overkill for this task

**scikit-learn Traditional ML:**
- ✅ Very fast inference
- ✅ Simple to understand
- ❌ Lower accuracy on text
- ❌ Requires manual feature engineering

**Consequences:**
- ✅ Complete privacy preservation
- ✅ No external API costs
- ✅ Works offline
- ✅ Reasonable accuracy
- ❌ Higher CPU/RAM requirements
- ❌ Need to manage model files
- ❌ Slower than cloud APIs

**Implementation:**
```python
import spacy
from transformers import pipeline

# NER for date/period extraction
nlp = spacy.load("en_core_web_sm")

# Document classification
classifier = pipeline(
    "text-classification",
    model="distilbert-base-uncased-finetuned-sst-2-english",
    device=-1  # CPU inference
)

# Fine-tune on user's documents
from transformers import Trainer, TrainingArguments
# Custom training code
```

---

### ADR-007: Paperless API Client - httpx

**Status:** Accepted

**Context:**
Need to communicate with paperless-ngx REST API efficiently and reliably.

**Decision:**
Use httpx as the HTTP client library.

**Rationale:**
1. **Async Support**: Native async/await support
2. **HTTP/2**: More efficient than HTTP/1.1
3. **Modern API**: Similar to requests but more features
4. **Type Hints**: Better IDE support
5. **Testing**: Excellent test support

**Implementation:**
```python
import httpx
from typing import List, Dict, Any

class PaperlessClient:
    def __init__(self, base_url: str, api_token: str):
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Token {api_token}"},
            timeout=30.0
        )
    
    async def get_documents(
        self, 
        page: int = 1, 
        correspondent: int = None
    ) -> Dict[str, Any]:
        params = {"page": page}
        if correspondent:
            params["correspondent__id"] = correspondent
        
        response = await self.client.get("/api/documents/", params=params)
        response.raise_for_status()
        return response.json()
    
    async def get_document(self, document_id: int) -> Dict[str, Any]:
        response = await self.client.get(f"/api/documents/{document_id}/")
        response.raise_for_status()
        return response.json()
```

---

### ADR-008: Configuration Management - YAML + Pydantic

**Status:** Accepted

**Context:**
Need to manage configuration for database connections, paperless URLs, provider definitions, and detection rules.

**Decision:**
Use YAML files for configuration with Pydantic models for validation.

**Rationale:**
1. **YAML**: Human-readable, supports comments
2. **Pydantic**: Type validation, clear error messages
3. **Environment Overrides**: Support environment variables for secrets
4. **Schema Validation**: Catch config errors early

**Implementation:**
```python
from pydantic import BaseModel, HttpUrl, validator
import yaml

class PaperlessConfig(BaseModel):
    url: HttpUrl
    api_token: str
    
class DatabaseConfig(BaseModel):
    type: str  # 'sqlite' or 'postgresql'
    connection_string: str
    
class AppConfig(BaseModel):
    paperless: PaperlessConfig
    database: DatabaseConfig
    analysis_interval_hours: int = 24
    
def load_config(path: str) -> AppConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    return AppConfig(**data)
```

**Example config.yaml:**
```yaml
paperless:
  url: "http://localhost:8000"
  api_token: "${PAPERLESS_API_TOKEN}"  # From environment

database:
  type: "sqlite"
  connection_string: "statement_tracker.db"

analysis_interval_hours: 24

providers:
  - name: "Chase Visa"
    type: "credit_card"
    importance: "high"
```

---

### ADR-009: Task Scheduling - APScheduler

**Status:** Accepted

**Context:**
Need to run periodic tasks like document analysis, missing statement detection, and sending notifications.

**Decision:**
Use APScheduler for task scheduling.

**Rationale:**
1. **Pure Python**: No external dependencies
2. **Flexible**: Cron-like, interval, and one-time jobs
3. **Persistent**: Can store jobs in database
4. **Timezone-Aware**: Handles timezones correctly
5. **Decorator API**: Easy to define scheduled tasks

**Alternatives Considered:**

**Celery:**
- ✅ Distributed task queue
- ✅ More powerful
- ❌ Requires Redis/RabbitMQ
- ❌ Overkill for single-user system

**Cron:**
- ✅ System-level, reliable
- ✅ Universal on Linux
- ❌ Hard to configure from Python
- ❌ Less flexible
- ❌ Platform-specific

**Implementation:**
```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler()

# Run daily at 2 AM
@scheduler.scheduled_job(CronTrigger(hour=2, minute=0))
async def daily_analysis():
    await analyze_new_documents()
    await detect_missing_statements()
    await send_notifications()

# Run every 6 hours
@scheduler.scheduled_job('interval', hours=6)
async def check_for_new_documents():
    await sync_with_paperless()

scheduler.start()
```

---

### ADR-010: Logging - structlog

**Status:** Accepted

**Context:**
Need comprehensive logging for debugging, auditing, and monitoring system behavior.

**Decision:**
Use structlog for structured logging.

**Rationale:**
1. **Structured**: JSON-based logs, easy to parse
2. **Context**: Add context to log entries
3. **Performance**: Low overhead
4. **Integrations**: Works with standard logging

**Implementation:**
```python
import structlog

logger = structlog.get_logger()

# Structured logging with context
logger.info(
    "pattern_detected",
    provider="Chase Visa",
    frequency="monthly",
    confidence=0.92,
    documents_analyzed=24
)

# Bind context for related logs
log = logger.bind(provider_id="chase-visa-1234")
log.info("analyzing_documents", count=24)
log.info("pattern_found", type="monthly")
```

---

## Technology Stack Summary

### Backend
| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| **Language** | Python | 3.8+ | Core implementation |
| **Web Framework** | FastAPI | 0.100+ | REST API |
| **ORM** | SQLAlchemy | 2.0+ | Database abstraction |
| **Migrations** | Alembic | 1.11+ | Schema management |
| **Database (MVP)** | SQLite | 3.36+ | Data storage |
| **Database (Prod)** | PostgreSQL | 13+ | Data storage |
| **HTTP Client** | httpx | 0.24+ | Paperless API |
| **Scheduling** | APScheduler | 3.10+ | Periodic tasks |
| **Logging** | structlog | 23.1+ | Structured logging |
| **Config** | PyYAML + Pydantic | Latest | Configuration |
| **Date/Time** | python-dateutil | 2.8+ | Date manipulation |
| **Testing** | pytest | 7.4+ | Unit/integration tests |

### Frontend (Dashboard)
| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Framework** | React | UI components |
| **Language** | TypeScript | Type safety |
| **Build Tool** | Vite | Fast builds |
| **UI Library** | Ant Design | Component library |
| **State Management** | React Query + Zustand | API & UI state |
| **HTTP Client** | axios | API calls |
| **Charts** | Recharts | Visualizations |
| **Forms** | React Hook Form | Form handling |
| **Date Picker** | react-datepicker | Date inputs |

### ML (Optional - Future)
| Component | Technology | Purpose |
|-----------|-----------|---------|
| **NLP** | spaCy | Named entity recognition |
| **Classification** | transformers (DistilBERT) | Document classification |
| **Time Series** | Prophet | Pattern prediction |
| **Local LLM** | Ollama (optional) | Enhanced analysis |

### DevOps
| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Containers** | Docker | Deployment |
| **Process Manager** | systemd or docker-compose | Service management |
| **Reverse Proxy** | nginx or Caddy | HTTPS, routing |
| **Backup** | restic or borg | Data backup |

---

## Deployment Options

### Option 1: Docker Compose (Recommended)

**Pros:**
- ✅ Easy setup and deployment
- ✅ Isolated environment
- ✅ Easy to upgrade
- ✅ Cross-platform
- ✅ Can run alongside paperless-ngx

**Example docker-compose.yml:**
```yaml
version: '3.8'

services:
  statement-tracker:
    build: .
    ports:
      - "8001:8000"
    environment:
      - PAPERLESS_URL=http://paperless:8000
      - PAPERLESS_API_TOKEN=${PAPERLESS_TOKEN}
      - DATABASE_URL=${DATABASE_URL}
    volumes:
      - ./data:/app/data
      - ./config.yaml:/app/config.yaml
    depends_on:
      - db
  
  db:
    image: postgres:15
    environment:
      - POSTGRES_DB=statements
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

### Option 2: Native Installation

**Pros:**
- ✅ No Docker required
- ✅ Direct access to system
- ✅ Easier for debugging

**Installation:**
```bash
# Install Python dependencies
pip install -r requirements.txt

# Initialize database
alembic upgrade head

# Configure
cp config.example.yaml config.yaml
# Edit config.yaml with your settings

# Run
python -m statement_tracker serve
```

### Option 3: Managed Hosting (Future)

Could be packaged as:
- Unraid Community App
- Home Assistant Add-on
- YunoHost App
- Cloudron App

---

## Resource Requirements

### Minimum (Rule-Based, SQLite)
- **CPU**: 1 core
- **RAM**: 512 MB
- **Storage**: 500 MB
- **Network**: Access to paperless-ngx instance

### Recommended (Rule-Based, PostgreSQL)
- **CPU**: 2 cores
- **RAM**: 2 GB
- **Storage**: 5 GB
- **Network**: Access to paperless-ngx instance

### With ML (Hybrid Approach)
- **CPU**: 4 cores
- **RAM**: 4 GB
- **Storage**: 10 GB (includes model files)
- **GPU**: Optional, speeds up inference

---

## Security Considerations

### Secrets Management

**Never commit:**
- API tokens
- Database passwords
- Encryption keys
- Provider credentials

**Use environment variables:**
```bash
export PAPERLESS_API_TOKEN="your-token-here"
export DB_PASSWORD="secure-password"
export ENCRYPTION_KEY="generated-key"
```

**Or use .env file (gitignored):**
```
PAPERLESS_API_TOKEN=your-token-here
DB_PASSWORD=secure-password
ENCRYPTION_KEY=generated-key
```

### Data Encryption

**Sensitive fields:**
```python
from cryptography.fernet import Fernet

class EncryptedField:
    def __init__(self, key: bytes):
        self.cipher = Fernet(key)
    
    def encrypt(self, data: str) -> bytes:
        return self.cipher.encrypt(data.encode())
    
    def decrypt(self, encrypted: bytes) -> str:
        return self.cipher.decrypt(encrypted).decode()

# Store provider credentials encrypted
credential = encrypt_field.encrypt(account_password)
```

### API Security

**Authentication:**
```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer

security = HTTPBearer()

async def verify_token(credentials = Depends(security)):
    if credentials.credentials != expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token"
        )
    return credentials.credentials

@app.get("/api/providers", dependencies=[Depends(verify_token)])
async def get_providers():
    # Protected endpoint
    pass
```

---

## Development Tools

### Recommended IDE
- **VS Code** with extensions:
  - Python
  - Pylance
  - Python Test Explorer
  - SQLite Viewer
  - Docker
  - YAML

### Code Quality
```bash
# Linting
ruff check .

# Type checking
mypy statement_tracker/

# Formatting
black statement_tracker/

# Testing
pytest tests/ --cov=statement_tracker
```

### Pre-commit Hooks
```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/psf/black
    rev: 23.7.0
    hooks:
      - id: black
  
  - repo: https://github.com/charliermarsh/ruff-pre-commit
    rev: v0.0.280
    hooks:
      - id: ruff
  
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.4.1
    hooks:
      - id: mypy
```

---

## Testing Strategy

### Unit Tests
```python
import pytest
from statement_tracker.detector import detect_temporal_pattern

def test_monthly_pattern_detection():
    dates = [
        date(2025, 1, 5),
        date(2025, 2, 6),
        date(2025, 3, 7),
        date(2025, 4, 5),
    ]
    
    pattern = detect_temporal_pattern(dates)
    
    assert pattern['frequency'] == 'monthly'
    assert pattern['confidence'] > 0.80
```

### Integration Tests
```python
@pytest.mark.asyncio
async def test_paperless_integration(paperless_client):
    documents = await paperless_client.get_documents()
    assert len(documents) > 0
    assert 'results' in documents
```

### End-to-End Tests
```python
def test_full_analysis_workflow(test_db, paperless_mock):
    # Run full analysis
    result = run_analysis()
    
    # Verify providers discovered
    assert len(result.providers) > 0
    
    # Verify patterns detected
    for provider in result.providers:
        assert provider.pattern is not None
    
    # Verify missing statements detected
    assert len(result.missing_statements) >= 0
```

---

## Performance Benchmarks (Target)

| Operation | Target Time | Notes |
|-----------|------------|-------|
| Analyze 1000 documents | <10 seconds | Rule-based |
| Detect pattern (20 docs) | <1 second | Single provider |
| Check missing statements | <5 seconds | All providers |
| API response time | <200ms | 95th percentile |
| Dashboard load | <2 seconds | Initial load |
| Database query | <50ms | Single provider |

---

## Migration Path

### Phase 1: SQLite (MVP)
```
Development → Single User Testing → Initial Deployment
```

### Phase 2: PostgreSQL (Production)
```
1. Export data from SQLite
2. Set up PostgreSQL
3. Run Alembic migrations
4. Import data
5. Switch configuration
6. Verify operation
```

### Phase 3: Add ML (Optional)
```
1. Collect training data
2. Label statements (UI or manual)
3. Train models locally
4. Deploy models alongside app
5. Configure hybrid mode
6. Monitor performance
```

---

## Cost Analysis

### Self-Hosted Costs
- **Hardware**: $0 (use existing server)
- **Development**: Time investment
- **Maintenance**: ~1 hour/month
- **Total**: Effectively $0

### Cloud Hosting (Alternative)
- **VPS**: $5-10/month
- **Database**: $0 (included) or $7/month (managed)
- **Total**: $5-17/month

### vs. Commercial Alternative
- **Managed service**: $10-30/month
- **Privacy concerns**: High
- **Our solution**: $0/month, complete privacy

---

## References

- **FastAPI**: https://fastapi.tiangolo.com/
- **SQLAlchemy**: https://docs.sqlalchemy.org/
- **React**: https://react.dev/
- **spaCy**: https://spacy.io/
- **Transformers**: https://huggingface.co/docs/transformers/
- **APScheduler**: https://apscheduler.readthedocs.io/
- **Pydantic**: https://docs.pydantic.dev/

---

**Document Version:** 1.0  
**Last Updated:** 2026-02-14  
**Status:** Recommendations Complete
