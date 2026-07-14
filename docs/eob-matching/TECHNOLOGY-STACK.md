# Technology Stack: Medical EOB & Bill Matching

## Table of Contents

1. [Architecture Decision Records (ADRs)](#architecture-decision-records)
2. [Core Technology Stack](#core-technology-stack)
3. [Implementation Approach Comparison](#implementation-approach-comparison)
4. [Component Technologies](#component-technologies)
5. [Security & Privacy Stack](#security--privacy-stack)
6. [Deployment Options](#deployment-options)
7. [Cost Analysis](#cost-analysis)

---

## Architecture Decision Records

### ADR-001: Self-Hosted First Principle

**Status**: Accepted

**Context**: Medical documents contain Protected Health Information (PHI). Using cloud services for document processing introduces privacy risks and potential HIPAA compliance requirements.

**Decision**: All document processing, analysis, and storage will be performed locally on self-hosted infrastructure. No medical document data will be sent to external APIs or cloud services.

**Consequences**:
- ✅ Complete control over PHI
- ✅ No cloud service costs
- ✅ No compliance requirements for external vendors
- ✅ Works offline
- ❌ Requires local compute resources
- ❌ User responsible for backups and security
- ❌ Cannot leverage advanced cloud AI services

**Alternatives Considered**:
- Cloud OCR services (Google Vision, AWS Textract) - Rejected due to PHI concerns
- OpenAI API for document analysis - Rejected due to data sharing policies
- Cloud-hosted databases - Rejected for data sovereignty

---

### ADR-002: Rule-Based MVP, Hybrid for Production

**Status**: Accepted

**Context**: Three approaches were evaluated (see [DESIGN.md](experiments/personal-automation/mission-control/DESIGN.md#10-implementation-approaches)). Need to balance time-to-value with long-term accuracy.

**Decision**: 
1. Phase 1: Implement rule-based approach for MVP (2-3 weeks)
2. Phase 2: Collect training data during MVP usage
3. Phase 3: Migrate to hybrid approach (rules + ML) for production

**Consequences**:
- ✅ Fast time-to-value with MVP
- ✅ Learn from real usage before investing in ML
- ✅ Incremental complexity increase
- ✅ Graceful degradation if ML unavailable
- ❌ May need to refactor some code for ML integration
- ❌ Users experience two phases of accuracy improvement

---

### ADR-003: Python as Primary Language

**Status**: Accepted

**Context**: Need to choose between Python, Node.js, or other languages for implementation.

**Decision**: Use Python 3.9+ as the primary implementation language.

**Rationale**:
- Excellent PDF processing libraries (pdfplumber, PyPDF2)
- Rich ecosystem for ML (scikit-learn, spaCy) if needed later
- Good data processing libraries (pandas, dateutil)
- Strong regex and text processing support
- Easy to deploy in Docker containers
- Widely known for maintainability

**Consequences**:
- ✅ Access to best-in-class PDF and ML libraries
- ✅ Shorter development time
- ✅ Easy hiring/community support
- ❌ Slightly slower than compiled languages (not a concern for this use case)

**Alternatives Considered**:
- Node.js - Good for web apps, but weaker PDF/ML ecosystem
- Go - Fast, but limited ML libraries
- Java - Verbose, heavy runtime

---

### ADR-004: SQLite for Data Storage

**Status**: Accepted

**Context**: Need local database for match tracking, extraction caching, and metadata.

**Decision**: Use SQLite with SQLCipher for encryption.

**Rationale**:
- Serverless, no separate database process required
- Built into Python standard library
- Fast for read-heavy workloads
- Easy backup (single file)
- SQLCipher provides transparent encryption for PHI protection

**Consequences**:
- ✅ Zero-configuration database
- ✅ Easy to backup and restore
- ✅ Encryption at rest via SQLCipher
- ✅ Sufficient for single-user workload
- ❌ Not suitable for multi-user concurrent access
- ❌ Limited to single server

**Alternatives Considered**:
- PostgreSQL - Overkill for single-user, requires server process
- MongoDB - No relational integrity needed, adds complexity
- In-memory only - Lost on restart, no historical tracking

---

### ADR-005: Standalone Web App for Dashboard UI

**Status**: Accepted (Revised)

**Context**: Need user interface for reviewing matches, tracking payments, and managing unmatched documents (see Issue #27). Originally considered AppSmith (low-code), but the user does not currently run AppSmith and prefers a self-contained web app deployable on their homelab.

**Decision**: Build a lightweight standalone web application using **FastAPI (backend) + React or vanilla JS (frontend)**, served as a single deployable unit (Docker container).

**Rationale**:
- No additional platform dependency (AppSmith not needed)
- Full control over UX for match review, unmatched document surfacing, and payment tracking
- Can be served directly from the FastAPI backend already needed for the processing engine
- Single Docker container keeps deployment simple on homelab
- Static HTML/JS frontend can be as simple or rich as needed
- Easier to evolve and customize than a low-code platform

**Consequences**:
- ✅ Single service to deploy (FastAPI serves both API + frontend)
- ✅ Full customization for unmatched document UX (Issue #27)
- ✅ No additional platform to learn or maintain
- ✅ Lighter resource footprint than AppSmith
- ✅ Can start with simple vanilla JS, upgrade to React later if needed
- ❌ More frontend development effort than low-code
- ❌ Need to build responsive layout manually

**Alternatives Considered**:
- AppSmith (self-hosted) - Good low-code option, but adds another service to deploy and maintain; user doesn't currently run it
- Streamlit - Python-native, but less polished UI and limited interactivity
- Retool - Commercial, cloud-only, not self-hosted
- Home Assistant - Not well-suited for document management workflows; better for sensor/device dashboards
- n8n - Good for workflows (already used for automation), but weak for complex UIs

---

### ADR-006: n8n for Workflow Automation

**Status**: Accepted

**Context**: Need orchestration for polling Paperless, triggering processing, and sending notifications.

**Decision**: Use n8n (self-hosted) for workflow automation.

**Rationale**:
- Visual workflow editor is easy to understand and modify
- Built-in HTTP node for Paperless API
- Scheduling and webhook support
- Can send notifications (email, Slack, etc.)
- Self-hosted version is free
- Good logging and error handling

**Consequences**:
- ✅ No-code workflow management
- ✅ Easy to add new triggers and actions
- ✅ Built-in scheduling and retry logic
- ✅ Visual debugging
- ❌ Another service to deploy
- ❌ Learning curve for n8n-specific concepts

**Alternatives Considered**:
- Cron jobs + Python scripts - Simple, but limited error handling
- Apache Airflow - Overkill for simple workflows
- Custom scheduler - Reinventing the wheel

---

### ADR-007: No Cloud ML APIs

**Status**: Accepted

**Context**: Considered using OpenAI, Google Cloud Vision, or AWS Comprehend Medical for document understanding.

**Decision**: If ML is needed, use self-hosted models only (spaCy, Ollama + local LLMs).

**Rationale**:
- **Privacy First**: PHI must never leave local infrastructure
- **HIPAA Compliance**: Cloud APIs would require Business Associate Agreements
- **Cost Control**: Per-API-call pricing can be expensive
- **Offline Capability**: Works without internet
- **Data Sovereignty**: Complete control over data

**Consequences**:
- ✅ No PHI exposure to third parties
- ✅ No ongoing API costs
- ✅ Works offline
- ✅ No rate limits
- ❌ Lower ML accuracy than GPT-4/Claude
- ❌ Requires more powerful local hardware
- ❌ Manual model management

**Self-Hosted ML Options**:
- spaCy with pre-trained models (CPU-friendly)
- Ollama with Phi-3 or Mistral 7B (needs GPU)
- DistilBERT for document classification (medium compute)

---

## Core Technology Stack

### Backend (Document Processing)

```yaml
language: Python 3.9+
frameworks:
  api: FastAPI 0.100+
  cli: Click or Typer
  
libraries:
  pdf_processing:
    - pdfplumber: PDF text extraction
    - PyPDF2: PDF manipulation
    - camelot-py: Table extraction
    
  text_processing:
    - python-dateutil: Date parsing
    - fuzzywuzzy: Fuzzy string matching
    - python-Levenshtein: Faster fuzzy matching
    - regex: Advanced pattern matching
    
  api_clients:
    - requests: HTTP client
    - httpx: Async HTTP client
    
  database:
    - sqlite3: Built-in database
    - sqlalchemy: ORM (optional)
    - pysqlcipher3: Encryption for SQLite
    
  ml_optional:
    - spacy: NLP and NER
    - scikit-learn: ML algorithms
    - pandas: Data manipulation
    - numpy: Numerical computing
    
  utilities:
    - tenacity: Retry logic
    - python-dotenv: Environment variables
    - pydantic: Data validation
    - loguru: Logging
```

### Frontend (Dashboard)

```yaml
platform: AppSmith (self-hosted)
version: Latest stable

features:
  - REST API integration
  - Custom SQL queries
  - Tables, charts, forms
  - Role-based access control
  - Responsive design

deployment:
  - Docker container
  - Connects to Python FastAPI backend
  - Connects to SQLite database
```

### Workflow Automation

```yaml
platform: n8n (self-hosted)
version: Latest stable

workflows:
  - Paperless document polling
  - Document processing triggers
  - Match notifications
  - Payment reminders
  - Error alerts

deployment:
  - Docker container
  - Webhooks for real-time triggers
  - Scheduled cron jobs for polling
```

### Database

```yaml
primary: SQLite 3.35+
encryption: SQLCipher 4.5+

schema:
  - documents (metadata cache)
  - matches (EOB-Bill relationships)
  - payment_tracking (payment status)
  - extraction_cache (parsed data)
  - processing_log (audit trail)

backup:
  - sqlite3 .backup command
  - rsync to backup location
  - frequency: daily
```

---

## Implementation Approach Comparison

### Option 1: Rule-Based (MVP)

**Technology Stack:**
```yaml
core:
  - Python 3.9+
  - pdfplumber
  - fuzzywuzzy
  - requests
  - sqlite3

frameworks:
  - FastAPI (REST API)
  - Click (CLI)

ui:
  - AppSmith (dashboard)

automation:
  - n8n (workflows)

deployment:
  - Docker Compose (all services)

timeline: 2-3 weeks
complexity: Low-Medium
cost: $0/month (self-hosted)
```

**Advantages:**
- Fast implementation
- Minimal dependencies
- Easy to debug
- Deterministic results
- Low resource requirements

---

### Option 2: ML-Based

**Technology Stack:**
```yaml
core:
  - Python 3.9+
  - All Option 1 libraries
  
ml_frameworks:
  - spaCy 3.5+ (NLP)
  - scikit-learn (classifiers)
  - pandas, numpy
  
  optional_advanced:
    - Ollama (LLM hosting)
    - Hugging Face Transformers
    - PyTorch or TensorFlow

ml_models:
  - Document classifier (custom trained)
  - NER model for field extraction
  - Similarity model for matching

training:
  - Jupyter notebooks for experiments
  - MLflow for model versioning
  - DVC for data versioning

hardware:
  - CPU: 4+ cores
  - RAM: 8+ GB
  - GPU: Recommended for transformer models

timeline: 2-3 months
complexity: High
cost: $0/month (self-hosted) + higher hardware requirements
```

**Advantages:**
- Higher accuracy ceiling
- Learns from data
- Handles variety better
- Improves over time

**Disadvantages:**
- Requires training data
- More complex to maintain
- Higher compute requirements
- Less explainable

---

### Option 3: Hybrid (Recommended Production)

**Technology Stack:**
```yaml
core:
  - All Option 1 technologies
  
ml_components:
  - spaCy (NER)
  - Simple classifier (Naive Bayes or Random Forest)
  - No heavy transformers initially

architecture:
  - Rule-based as primary (fast path)
  - ML as fallback (slow path)
  - Confidence scoring determines which to use

deployment:
  - Same as Option 1
  - ML models loaded on-demand
  - Graceful degradation if ML unavailable

timeline: 1-2 months (MVP + ML integration)
complexity: Medium-High
cost: $0/month (self-hosted)
```

**Migration Path:**
1. Deploy Option 1 (rule-based)
2. Collect documents and feedback
3. Train ML models on real data
4. Integrate ML as secondary classifier
5. Gradually increase ML usage

---

## Component Technologies

### PDF Processing

**pdfplumber** (Primary)
```yaml
pros:
  - Excellent table extraction
  - Good text positioning
  - Active maintenance
  - Pure Python

cons:
  - Can be slow on large PDFs
  - Memory intensive

usage:
  - Extract structured tables from EOBs
  - Precise text positioning
```

**PyPDF2** (Supplementary)
```yaml
pros:
  - Fast text extraction
  - Good metadata extraction
  - Low memory usage

cons:
  - Poor table handling
  - Less accurate positioning

usage:
  - Quick text extraction
  - PDF metadata
```

### String Matching

**fuzzywuzzy** (Primary)
```yaml
pros:
  - Multiple matching algorithms
  - Easy to use
  - Good for provider names

cons:
  - Can be slow without C extension

usage:
  - Provider name matching
  - Patient name verification
  - Insurance company detection

algorithms:
  - ratio(): Simple similarity
  - partial_ratio(): Substring matching
  - token_sort_ratio(): Order-independent
```

### Date Parsing

**python-dateutil** (Primary)
```yaml
pros:
  - Handles many date formats
  - Fuzzy parsing
  - Timezone aware

usage:
  - Extract dates from text
  - Parse various formats (MM/DD/YYYY, DD-MM-YYYY, etc.)
  - Handle relative dates

example: |
  from dateutil import parser
  date = parser.parse("Service Date: 01/15/2024", fuzzy=True)
```

### API Framework

**FastAPI** (Backend API)
```yaml
pros:
  - Fast and modern
  - Automatic API documentation
  - Type hints and validation
  - Async support

usage:
  - REST API for dashboard
  - Webhook endpoints
  - Health checks

endpoints:
  - GET /api/matches (list matched pairs)
  - GET /api/unmatched (unmatched documents)
  - POST /api/process (trigger processing)
  - GET /api/health (health check)
```

---

## Security & Privacy Stack

### Encryption

**SQLCipher** (Database Encryption)
```yaml
purpose: Encrypt SQLite database at rest
algorithm: AES-256
key_management: Environment variable or OS keychain

usage: |
  from pysqlcipher3 import dbapi2 as sqlite
  conn = sqlite.connect('medical.db')
  conn.execute(f"PRAGMA key = '{encryption_key}'")
```

**HTTPS** (Transport Security)
```yaml
purpose: Encrypt API communications
certificate: Self-signed or Let's Encrypt
implementation: Traefik or Nginx reverse proxy
```

### Access Control

**API Authentication**
```yaml
method: API token or JWT
storage: Environment variables, never in code
rotation: Every 90 days

headers: |
  Authorization: Bearer <token>
```

**Dashboard Authentication**
```yaml
method: AppSmith built-in auth
options:
  - Local username/password
  - LDAP (if available)
  - OAuth2 (optional)

session_timeout: 15 minutes
```

### Logging & Audit

**Loguru** (Logging Library)
```yaml
features:
  - Structured logging
  - Automatic rotation
  - PHI-safe (no sensitive data in logs)

configuration: |
  logger.add(
      "logs/app.log",
      rotation="10 MB",
      retention="30 days",
      enqueue=True,
      level="INFO"
  )

rules:
  - Never log patient names
  - Never log dollar amounts
  - Log document IDs only
  - Anonymize in logs
```

---

## Deployment Options

### Option A: Docker Compose (Recommended)

**docker-compose.yml:**
```yaml
version: '3.8'

services:
  # Document processing backend
  eob-processor:
    build: ./backend
    volumes:
      - ./data:/data
      - ./config:/config
    environment:
      - PAPERLESS_URL=http://paperless:8000
      - PAPERLESS_TOKEN=${PAPERLESS_TOKEN}
      - DB_ENCRYPTION_KEY=${DB_ENCRYPTION_KEY}
    depends_on:
      - paperless
    restart: unless-stopped

  # Dashboard UI
  appsmith:
    image: appsmith/appsmith-ce:latest
    ports:
      - "8080:80"
    volumes:
      - ./appsmith-data:/appsmith-stacks
    restart: unless-stopped

  # Workflow automation
  n8n:
    image: n8nio/n8n:latest
    ports:
      - "5678:5678"
    volumes:
      - ./n8n-data:/home/node/.n8n
    environment:
      - N8N_BASIC_AUTH_ACTIVE=true
      - N8N_BASIC_AUTH_USER=${N8N_USER}
      - N8N_BASIC_AUTH_PASSWORD=${N8N_PASSWORD}
    restart: unless-stopped

  # Paperless-ngx (assumed already running)
  paperless:
    image: ghcr.io/paperless-ngx/paperless-ngx:latest
    # ... (your existing paperless config)

volumes:
  appsmith-data:
  n8n-data:
  processor-data:
```

**Deployment Steps:**
1. Copy `docker-compose.yml` to server
2. Create `.env` file with secrets
3. Run `docker-compose up -d`
4. Access AppSmith at `http://localhost:8080`
5. Access n8n at `http://localhost:5678`

**Resource Requirements:**
- CPU: 2+ cores
- RAM: 4GB minimum, 8GB recommended
- Disk: 20GB for system, 1GB per 1000 documents
- Network: 1Mbps+ for Paperless API calls

---

### Option B: Kubernetes (Advanced)

For users with existing K8s infrastructure:

```yaml
# Not detailed here, but would include:
# - Deployments for each service
# - Services for networking
# - Ingress for HTTPS
# - Persistent volumes for data
# - Secrets for credentials
```

**Complexity**: High  
**Best For**: Users with existing K8s expertise  
**Overkill**: For single-user deployment

---

### Option C: Bare Metal / VM

```bash
# Install Python and dependencies
python3.9 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Install n8n
npm install -g n8n

# Install AppSmith
docker run -d -p 80:80 -v "$PWD/appsmith:/appsmith-stacks" appsmith/appsmith-ce

# Run processor
python main.py serve

# Run n8n
n8n start
```

**Complexity**: Medium  
**Best For**: Single-user, simple deployments  
**Maintenance**: Manual updates required

---

## Cost Analysis

### Self-Hosted (Recommended)

| Component | Monthly Cost | Notes |
|-----------|--------------|-------|
| Hardware | $0* | Use existing server/NAS |
| Electricity | ~$5-10 | 24/7 server at 50W |
| Software | $0 | All open-source |
| Backups | $0 | Local backup to NAS |
| **Total** | **$5-10/month** | Minimal cost |

*Assumes existing hardware. New NUC/mini PC: $400-800 one-time.

### Cloud-Hosted (Not Recommended for PHI)

| Component | Monthly Cost | Notes |
|-----------|--------------|-------|
| VPS (4GB RAM) | $20-40 | DigitalOcean, Linode, etc. |
| Storage (100GB) | $5-10 | Block storage |
| Backups | $5 | Snapshot backups |
| n8n Cloud | $20 | Managed n8n (optional) |
| AppSmith Cloud | $10-40 | Managed AppSmith (optional) |
| **Total** | **$60-115/month** | ⚠️ PHI in cloud! |

**Recommendation**: Self-hosted only for PHI security.

---

## Development Tools

### Recommended IDE Setup

```yaml
editor: VS Code or PyCharm

extensions:
  vscode:
    - Python
    - Pylance
    - SQLite Viewer
    - Docker
    - REST Client

pycharm:
  - Database Tools
  - Docker plugin
```

### Testing Tools

```yaml
unit_tests: pytest
integration_tests: pytest + pytest-docker
mocking: pytest-mock
coverage: pytest-cov
linting: ruff or flake8
formatting: black
type_checking: mypy
```

### Documentation Tools

```yaml
api_docs: FastAPI auto-generated (Swagger UI)
code_docs: Sphinx or mkdocs
diagrams: mermaid.js (as seen in this document)
```

---

## Performance Monitoring

### Metrics to Track

```yaml
processing:
  - Documents processed per hour
  - Average processing time per document
  - Match accuracy rate
  - False positive rate
  
system:
  - CPU usage
  - Memory usage
  - Disk usage
  - API response times
  
business:
  - Unmatched documents count
  - Payment tracking accuracy
  - User review time saved
```

### Monitoring Tools

**Option 1: Simple Logging**
```python
# Log metrics to file
logger.info(f"Processed {doc_id} in {elapsed}s, confidence={confidence}")
```

**Option 2: Prometheus + Grafana** (Advanced)
```yaml
prometheus:
  - Scrape metrics endpoint
  - Store time-series data

grafana:
  - Visualize dashboards
  - Set up alerts
```

---

## Backup Strategy

### What to Backup

```yaml
critical:
  - SQLite database (all matches and metadata)
  - n8n workflows
  - AppSmith apps
  - Configuration files (.env, config.yaml)

not_needed:
  - Extraction cache (can be regenerated)
  - Logs (kept for 30 days, then purged)
  - Paperless documents (already backed up by Paperless)
```

### Backup Methods

```bash
# Daily SQLite backup
sqlite3 medical.db ".backup medical-$(date +%Y%m%d).db"

# Encrypt backup
gpg -c medical-$(date +%Y%m%d).db

# Sync to NAS
rsync -avz backups/ /mnt/nas/medical-backup/

# Retention: Keep daily for 7 days, weekly for 30 days, monthly for 1 year
```

---

## Conclusion

This technology stack prioritizes:
1. **Privacy First**: All processing local, no cloud APIs
2. **Cost Effectiveness**: $0-10/month with self-hosting
3. **Ease of Use**: Low-code tools (AppSmith, n8n)
4. **Extensibility**: Can add ML later without rewrite
5. **Security**: Encryption at rest and in transit

**Recommended Starting Point:**
- Deploy Option 1 (Rule-Based) with Docker Compose
- Use AppSmith for dashboard
- Use n8n for automation
- SQLite with SQLCipher for data
- Migrate to Hybrid (Option 3) after collecting training data

---

*Document Version: 1.0*  
*Last Updated: 2026-02-14*  
*Status: Technology Decisions Finalized*
