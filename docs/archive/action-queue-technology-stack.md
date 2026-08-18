---
title: "Action Queue Technology Stack (Archived)"
sidebar_label: AQ Tech Stack
sidebar_position: 2
draft: true
---

:::note
This document is archived for historical reference. It may not reflect current implementation. The Action Queue module now uses FastAPI, SQLite via SQLAlchemy, and Ollama via the Bifrost LLM gateway — not the spaCy/DistilBERT/Streamlit stack recommended here.
:::

# Technology Stack Recommendations

## Overview

This document provides specific technology recommendations for implementing the Paperless-NGX Action Queue Agent. All recommendations prioritize self-hosted, privacy-focused solutions suitable for homelab environments.

## Architecture Decision Records (ADR)

### ADR-001: Self-Hosted First
**Decision:** Prioritize self-hosted solutions over cloud services for all components.

**Rationale:**
- Personal documents contain sensitive financial and personal information
- Homelab infrastructure already exists
- Avoid recurring cloud costs
- Full control over data and processing

**Implications:**
- Requires sufficient homelab resources
- Need to manage updates and maintenance
- Limited by local compute capabilities

---

### ADR-002: Modular Architecture
**Decision:** Build as independent, containerized services that communicate via APIs.

**Rationale:**
- Easy to upgrade individual components
- Can replace technologies without full rewrite
- Simplifies development and testing
- Supports future scaling

**Implications:**
- More complex deployment initially
- Need orchestration (Docker Compose or Kubernetes)
- Network overhead between services

---

### ADR-003: Hybrid AI Approach
**Decision:** Combine rule-based processing with ML models (not purely ML).

**Rationale:**
- Rule-based is reliable for structured data (dates, amounts)
- ML handles ambiguous content and classification
- Easier to debug and explain decisions
- Better accuracy with less training data

**Implications:**
- Need to maintain both codebases
- Rules need periodic updates
- More complex architecture

## Recommended Technology Stack

### 1. Orchestration & Scheduling

**Recommended: n8n (Primary) + Cron (Backup)**

**n8n - Workflow Automation Platform**
- **Why:** Visual workflow builder, built-in scheduling, self-hosted
- **Deployment:** Docker container
- **Pros:**
  - Low-code, easy to modify workflows
  - Built-in Paperless-NGX integration possible via HTTP nodes
  - Error handling and retry logic
  - Webhook support for real-time triggers
  - Active community and documentation
- **Cons:**
  - Additional service to manage
  - Learning curve for complex workflows
- **Resource Requirements:** 512MB RAM, minimal CPU

**Alternative: Apache Airflow**
- **Why:** Production-grade workflow orchestration
- **Use When:** Need complex DAGs, data lineage tracking
- **Pros:** Robust, scalable, Python-based
- **Cons:** Heavier resource usage, more complex setup

**Fallback: Systemd Timers + Python Script**
- **Why:** Simplest possible implementation
- **Use When:** Minimizing dependencies
- **Pros:** No additional services, native to Linux
- **Cons:** Less flexible, no UI, manual error handling

### 2. Backend API & Business Logic

**Recommended: FastAPI (Python)**

**Why FastAPI:**
- Modern Python web framework
- Async support for concurrent processing
- Automatic API documentation (OpenAPI/Swagger)
- Type hints and validation with Pydantic
- Excellent performance
- Easy integration with ML libraries

**Architecture:**
```
api/
├── main.py              # FastAPI application
├── routers/
│   ├── actions.py       # Action CRUD endpoints
│   ├── documents.py     # Paperless integration
│   └── feedback.py      # Feedback endpoints
├── services/
│   ├── paperless.py     # Paperless API client
│   ├── analyzer.py      # Document analysis orchestrator
│   └── ml_engine.py     # ML model inference
├── models/
│   ├── action.py        # Pydantic models
│   └── document.py      # Data models
└── database/
    ├── database.py      # Database connection
    └── crud.py          # Database operations
```

**Key Libraries:**
```python
fastapi==0.109.0
pydantic==2.6.0
httpx==0.26.0          # Async HTTP client
python-multipart==0.0.6
uvicorn==0.27.0        # ASGI server
sqlalchemy==2.0.25     # ORM
alembic==1.13.1        # Database migrations
```

**Alternative: Node.js + Express**
- **Use When:** JavaScript expertise, need npm ecosystem
- **Pros:** Large ecosystem, widely known
- **Cons:** Less ideal for ML integration

### 3. AI/ML Processing

**Recommended: Hybrid Approach**

#### Document Classification & NER

**Option A: spaCy + Custom Models (Recommended for Homelab)**

```python
# Installation
pip install spacy==3.7.2
python -m spacy download en_core_web_trf

# Usage for NER
import spacy
nlp = spacy.load("en_core_web_trf")
doc = nlp(document_text)
for ent in doc.ents:
    if ent.label_ == "DATE":
        dates.append(ent.text)
    elif ent.label_ == "MONEY":
        amounts.append(ent.text)
```

**Model Details:**
- **en_core_web_trf:** 438MB, transformer-based, high accuracy
- **Alternative (lighter):** en_core_web_sm (13MB) for resource-constrained environments
- **Custom Training:** Use spaCy's training pipeline for document-specific entities

**Pros:**
- Excellent accuracy for standard entities
- Efficient CPU inference
- Easy to extend with custom entities
- Good documentation

**Cons:**
- Need separate model for document classification
- Limited understanding of document context

#### Document Classification

**Option B: Fine-tuned BERT/DistilBERT**

```python
# Using Hugging Face Transformers
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch

model_name = "distilbert-base-uncased"
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForSequenceClassification.from_pretrained(
    model_name, 
    num_labels=8  # 8 document types
)

# Inference
inputs = tokenizer(document_text, return_tensors="pt", truncation=True, max_length=512)
outputs = model(**inputs)
predicted_class = torch.argmax(outputs.logits)
```

**Document Classes:**
1. Bill/Invoice
2. Form
3. Letter/Correspondence
4. Statement
5. Receipt
6. Contract
7. Notice
8. Other

**Training Strategy:**
- Start with pre-trained DistilBERT
- Fine-tune on your labeled documents (100-200 examples per class)
- Use active learning to improve over time

**Pros:**
- High accuracy for text classification
- Transfer learning requires less data
- Can handle subtle differences

**Cons:**
- Requires GPU for fast inference (or slower on CPU)
- ~250MB model size
- Need training data

#### Option C: Small Language Model (SLM) - Unified Processing

**Recommended Models:**

1. **Phi-3 Mini (3.8B parameters)** - Microsoft
   - **Size:** 2.4GB (quantized)
   - **Inference:** CPU-friendly with quantization
   - **Capability:** Strong reasoning, can handle all tasks
   - **Deployment:** ONNX Runtime or llama.cpp

2. **Mistral 7B Instruct**
   - **Size:** 4GB (quantized)
   - **Inference:** Requires GPU or powerful CPU
   - **Capability:** Excellent instruction following
   - **Deployment:** Ollama (recommended), llama.cpp, or vLLM

3. **Llama 3.2 3B**
   - **Size:** 2GB (quantized)
   - **Inference:** CPU-friendly
   - **Capability:** Good for structured extraction
   - **Deployment:** Ollama

**Using Ollama (Recommended):**

```python
# Installation
# curl -fsSL https://ollama.com/install.sh | sh
# ollama pull phi3

import requests

def analyze_document(document_text):
    prompt = f"""Analyze this document and extract:
1. Document type (bill, form, letter, etc.)
2. Due date (if any)
3. Amount (if any)
4. Recommended action
5. Urgency level (low, medium, high)

Document:
{document_text}

Respond in JSON format."""

    response = requests.post('http://localhost:11434/api/generate',
        json={
            "model": "phi3",
            "prompt": prompt,
            "stream": False
        })
    
    return response.json()
```

**Pros:**
- Single model for all AI tasks
- More context-aware decisions
- Better at understanding document nuances
- Easy to add new capabilities with prompts

**Cons:**
- Higher resource requirements
- Slower inference (3-10 seconds per document)
- Less predictable outputs (need validation)
- Requires careful prompt engineering

**Recommended Deployment:**
- Run Ollama as a separate service
- Use model with 4-bit quantization for speed
- Implement caching for similar documents
- Set timeout limits (10 seconds max)

#### Rule-Based Extraction (Complement to ML)

**Date Extraction:**
```python
from dateparser import parse
from datetime import datetime

def extract_dates(text):
    # Common date phrases for bills
    date_patterns = [
        r"due date:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        r"payment due:?\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})",
        r"deadline:?\s*(\d{1,2}/\d{1,2}/\d{4})"
    ]
    
    dates = []
    for pattern in date_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            parsed = parse(match)
            if parsed:
                dates.append(parsed)
    return dates
```

**Amount Extraction:**
```python
import re

def extract_amounts(text):
    # Currency patterns
    patterns = [
        r"\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)",  # $1,234.56
        r"(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*USD",  # 1,234.56 USD
        r"amount:?\s*\$?(\d+\.\d{2})"  # Amount: $123.45
    ]
    
    amounts = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        amounts.extend(matches)
    return amounts
```

**URL Extraction:**
```python
def extract_urls(text):
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    urls = re.findall(url_pattern, text)
    return urls
```

### 4. Database

**Recommended: PostgreSQL**

**Why PostgreSQL:**
- Robust, mature, well-documented
- JSON support for flexible data storage
- Full-text search capabilities
- Excellent Python support (psycopg2, SQLAlchemy)
- Self-hosted in Docker

**Deployment:**
```yaml
# docker-compose.yml
postgres:
  image: postgres:16-alpine
  environment:
    POSTGRES_DB: paperless_actions
    POSTGRES_USER: actions_user
    POSTGRES_PASSWORD: ${DB_PASSWORD}
  volumes:
    - ./data/postgres:/var/lib/postgresql/data
  ports:
    - "5432:5432"
```

**Schema Management:**
- Use Alembic for migrations
- Version control all schema changes
- Regular backups

**Alternative: SQLite**
- **Use When:** Simplest deployment, single-user
- **Pros:** Zero configuration, file-based
- **Cons:** Limited concurrency, no network access

### 5. Frontend Dashboard

**Recommended: Streamlit (Rapid Development) or Vue.js (Production)**

#### Option A: Streamlit (Recommended for MVP)

**Why Streamlit:**
- Python-based (same as backend)
- Rapid development
- Built-in components for data apps
- Easy to deploy
- No JavaScript required

**Example:**
```python
import streamlit as st
import requests

st.title("📋 Document Action Queue")

# Fetch actions
response = requests.get("http://api:8000/actions?status=pending")
actions = response.json()

# Display actions
for action in actions:
    with st.expander(f"{action['title']} - Due: {action['due_date']}"):
        st.write(f"**Type:** {action['action_type']}")
        st.write(f"**Amount:** {action['amount']}")
        st.write(f"**Urgency:** {action['urgency']}")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("Complete", key=f"complete_{action['id']}"):
                requests.post(f"http://api:8000/actions/{action['id']}/complete")
                st.rerun()
        with col2:
            if st.button("View Doc", key=f"view_{action['id']}"):
                st.write(action['document_url'])
        with col3:
            if st.button("Dismiss", key=f"dismiss_{action['id']}"):
                requests.post(f"http://api:8000/actions/{action['id']}/dismiss")
                st.rerun()
```

**Pros:**
- Fastest to develop
- Python developers can build UI
- Auto-refresh capabilities
- Built-in authentication

**Cons:**
- Less customizable than JavaScript frameworks
- Limited mobile optimization
- Can be slow with many widgets

#### Option B: Vue.js + Vuetify (Production)

**Why Vue.js:**
- Modern, reactive framework
- Component-based architecture
- Excellent mobile support with Vuetify
- Rich ecosystem

**Tech Stack:**
```json
{
  "dependencies": {
    "vue": "^3.4.0",
    "vuetify": "^3.5.0",
    "vue-router": "^4.2.0",
    "pinia": "^2.1.0",
    "axios": "^1.6.0"
  }
}
```

**Pros:**
- Professional UI/UX
- Highly customizable
- Excellent performance
- PWA support for mobile

**Cons:**
- Requires JavaScript expertise
- Longer development time
- Separate deployment

#### Option C: Home Assistant Integration (Complement)

**Create Custom Component:**

```python
# custom_components/paperless_actions/sensor.py
from homeassistant.components.sensor import SensorEntity

class PaperlessActionSensor(SensorEntity):
    """Sensor for pending document actions."""
    
    def __init__(self, api_client):
        self._api_client = api_client
        self._state = 0
        self._actions = []
    
    @property
    def name(self):
        return "Paperless Pending Actions"
    
    @property
    def state(self):
        return self._state
    
    @property
    def extra_state_attributes(self):
        return {
            "actions": self._actions[:5],  # Top 5 urgent
            "urgent_count": len([a for a in self._actions if a["urgency"] == "HIGH"])
        }
    
    async def async_update(self):
        actions = await self._api_client.get_pending_actions()
        self._state = len(actions)
        self._actions = actions
```

**Dashboard Card:**
```yaml
# Lovelace UI
type: custom:auto-entities
card:
  type: entities
  title: 📋 Document Actions
filter:
  include:
    - entity_id: sensor.paperless_pending_actions
  exclude: []
```

**Pros:**
- Native to Home Assistant ecosystem
- Notifications and automations
- Mobile app access
- No separate UI to build

**Cons:**
- Limited UI flexibility
- Requires Home Assistant
- Custom component maintenance

### 6. Model Serving & Inference

**Recommended: Dedicated ML Service Container**

**Architecture:**
```
ml-service/
├── Dockerfile
├── requirements.txt
├── main.py              # FastAPI server
├── models/
│   ├── classifier/      # Document type model
│   ├── ner/            # Entity extraction model
│   └── config.json     # Model configuration
└── inference/
    ├── classifier.py   # Classification logic
    └── extractor.py    # Extraction logic
```

**Dockerfile:**
```dockerfile
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download models
RUN python -m spacy download en_core_web_trf

# Copy application
COPY . .

# Expose port
EXPOSE 8001

# Run
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
```

**API Endpoints:**
```python
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class Document(BaseModel):
    text: str
    metadata: dict

class AnalysisResult(BaseModel):
    document_type: str
    entities: dict
    intent: str
    confidence: float

@app.post("/analyze", response_model=AnalysisResult)
async def analyze_document(document: Document):
    # Run ML models
    doc_type = classifier.predict(document.text)
    entities = ner_model.extract(document.text)
    intent = intent_detector.predict(doc_type, entities)
    
    return AnalysisResult(
        document_type=doc_type,
        entities=entities,
        intent=intent,
        confidence=0.92
    )
```

**Alternative: Ollama Service**

If using SLM approach:
```yaml
# docker-compose.yml
ollama:
  image: ollama/ollama:latest
  volumes:
    - ./models:/root/.ollama
  ports:
    - "11434:11434"
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]  # Optional
```

### 7. Development & Testing

**Development Tools:**
```python
# requirements-dev.txt
pytest==7.4.3
pytest-asyncio==0.21.1
pytest-cov==4.1.0
black==23.12.1          # Code formatting
ruff==0.1.9             # Linting
mypy==1.8.0             # Type checking
```

**Testing Framework:**
```python
# tests/test_extractor.py
import pytest
from services.extractor import extract_dates, extract_amounts

def test_extract_due_date():
    text = "Payment due by March 15, 2026"
    dates = extract_dates(text)
    assert len(dates) == 1
    assert dates[0].month == 3
    assert dates[0].day == 15

def test_extract_amount():
    text = "Amount due: $142.35"
    amounts = extract_amounts(text)
    assert amounts[0] == "142.35"
```

### 8. Deployment & Orchestration

**Recommended: Docker Compose**

**Full Stack docker-compose.yml:**
```yaml
version: '3.8'

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: paperless_actions
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - backend

  ml-service:
    build: ./ml-service
    environment:
      MODEL_PATH: /models
    volumes:
      - ./models:/models
    networks:
      - backend
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 4G

  api:
    build: ./api
    environment:
      DATABASE_URL: postgresql://${DB_USER}:${DB_PASSWORD}@postgres:5432/paperless_actions
      ML_SERVICE_URL: http://ml-service:8001
      PAPERLESS_URL: ${PAPERLESS_URL}
      PAPERLESS_TOKEN: ${PAPERLESS_TOKEN}
    depends_on:
      - postgres
      - ml-service
    networks:
      - backend
      - frontend

  dashboard:
    build: ./dashboard
    environment:
      API_URL: http://api:8000
    ports:
      - "8501:8501"
    depends_on:
      - api
    networks:
      - frontend

  n8n:
    image: n8nio/n8n:latest
    environment:
      - N8N_BASIC_AUTH_ACTIVE=true
      - N8N_BASIC_AUTH_USER=${N8N_USER}
      - N8N_BASIC_AUTH_PASSWORD=${N8N_PASSWORD}
    ports:
      - "5678:5678"
    volumes:
      - n8n_data:/home/node/.n8n
    networks:
      - backend

networks:
  backend:
  frontend:

volumes:
  postgres_data:
  n8n_data:
```

**Alternative: Kubernetes**
- **Use When:** Multiple services, need scaling, high availability
- **Pros:** Production-grade orchestration
- **Cons:** Complex, overkill for single-user homelab

### 9. Monitoring & Logging

**Recommended: Lightweight Stack**

**Grafana + Prometheus (Optional):**
```yaml
prometheus:
  image: prom/prometheus:latest
  volumes:
    - ./prometheus.yml:/etc/prometheus/prometheus.yml
    - prometheus_data:/prometheus
  ports:
    - "9090:9090"

grafana:
  image: grafana/grafana:latest
  ports:
    - "3000:3000"
  volumes:
    - grafana_data:/var/lib/grafana
```

**Application Logging:**
```python
# Use Python's logging
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/paperless-actions/app.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)
```

**Simpler Alternative: Log to files + Portainer**
- View container logs through Portainer UI
- Minimal overhead
- Good for homelab scale

## Resource Requirements Summary

### Minimal Configuration
- **CPU:** 2 cores
- **RAM:** 4 GB
- **Storage:** 10 GB
- **Approach:** SQLite, small models, Streamlit

### Recommended Configuration
- **CPU:** 4 cores
- **RAM:** 8 GB
- **Storage:** 20 GB
- **Approach:** PostgreSQL, DistilBERT + spaCy, Vue.js

### Optimal Configuration
- **CPU:** 6-8 cores (or GPU)
- **RAM:** 16 GB
- **Storage:** 50 GB
- **Approach:** PostgreSQL, SLM (Phi-3), Vue.js, full monitoring

## External Dependencies

### Required
- Paperless-NGX instance with API access
- Docker and Docker Compose
- Python 3.11+

### Optional
- Home Assistant (for integration)
- Reverse proxy (Traefik, Nginx)
- GPU (for faster ML inference)

### Not Required (Homelab Only)
- ❌ Cloud services (AWS, Azure, GCP)
- ❌ External APIs (OpenAI, Anthropic)
- ❌ SaaS platforms

## Recommended Implementation Phases

### Phase 1: MVP (2-3 weeks)
- **Stack:** FastAPI + SQLite + Streamlit + spaCy + rules
- **Features:** Basic document analysis, simple dashboard
- **Goal:** Validate concept and gather initial feedback

### Phase 2: Enhanced (1-2 months)
- **Stack:** Add PostgreSQL, train custom classifier
- **Features:** Feedback loop, improved accuracy
- **Goal:** Daily production use

### Phase 3: Advanced (2-3 months)
- **Stack:** Add Vue.js dashboard, SLM, Home Assistant integration
- **Features:** Mobile-responsive UI, notifications, advanced AI
- **Goal:** Polished, maintainable solution

## Cost Analysis

**One-time Costs:**
- Development time: $0 (self-implemented)
- Hardware: $0 (existing homelab)

**Recurring Costs:**
- Electricity: ~$5-10/month (running containers 24/7)
- Domain/SSL: $0 (local network) or $12/year (if exposing)
- Total: < $10/month

**Time Investment:**
- MVP: 40-60 hours
- Full implementation: 100-150 hours
- Maintenance: 2-4 hours/month

## Technology Comparison Matrix

| Criteria | Streamlit | Vue.js | HA Integration |
|----------|-----------|--------|----------------|
| Development Speed | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| Customization | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐ |
| Mobile Support | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| Maintenance | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ |
| Python Integration | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ |

| Criteria | spaCy + Rules | DistilBERT | SLM (Phi-3) |
|----------|---------------|------------|-------------|
| Accuracy | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| Speed | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ |
| Resource Usage | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ |
| Flexibility | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| Setup Complexity | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ |

## Final Recommendations

**For MVP/Proof of Concept:**
- **Backend:** FastAPI + SQLite
- **AI:** spaCy + rule-based extraction
- **Frontend:** Streamlit
- **Orchestration:** Systemd timer + Python script
- **Deployment:** Single Docker Compose stack

**For Production Use:**
- **Backend:** FastAPI + PostgreSQL
- **AI:** Hybrid (spaCy + DistilBERT classifier)
- **Frontend:** Streamlit initially, migrate to Vue.js if needed
- **Orchestration:** n8n
- **Deployment:** Multi-container Docker Compose
- **Optional:** Home Assistant integration for notifications

**For Advanced/Future:**
- **AI:** Migrate to Phi-3 or similar SLM for unified processing
- **Frontend:** Custom Vue.js dashboard + mobile PWA
- **Integration:** Full Home Assistant custom component
- **Monitoring:** Prometheus + Grafana

## Getting Started Checklist

- [ ] Verify Paperless-NGX API access
- [ ] Set up development environment (Python 3.11+, Docker)
- [ ] Install required Python packages
- [ ] Download spaCy models
- [ ] Create database schema
- [ ] Implement Paperless API client
- [ ] Build basic extraction logic
- [ ] Create simple Streamlit dashboard
- [ ] Test with real documents
- [ ] Iterate based on feedback

## Resources & References

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [spaCy Documentation](https://spacy.io/)
- [Streamlit Documentation](https://docs.streamlit.io/)
- [Ollama Documentation](https://ollama.ai/)
- [Paperless-NGX API Docs](https://docs.paperless-ngx.com/api/)
- [Home Assistant Developer Docs](https://developers.home-assistant.io/)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
