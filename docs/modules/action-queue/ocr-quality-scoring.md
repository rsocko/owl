---
title: "OCR Quality Scoring"
sidebar_label: OCR Scoring
sidebar_position: 9
---

# OCR Quality Scoring — Algorithm Specification

## Overview

This document specifies the scoring algorithm used to assess OCR quality for documents in Paperless-ngx. The scorer is implemented as a Python module used by the `scorer-service` FastAPI application.

**Scoring philosophy:** Fast, cheap, no model inference required. The heuristic scorer runs on the text string returned by the Paperless REST API (`document.content`) — no PDF download is required for scoring. PDF download is only needed for type classification and remediation.

---

## Step 1 — PDF Type Classification

**Requires:** PDF bytes (download via `GET /api/documents/{id}/download/?original=true`)  
**Library:** `PyMuPDF` (`pip install pymupdf`)

Classification determines whether a document can be improved by re-OCR at all. Digital-native PDFs with embedded vector text are exempt from all scoring and remediation.

```python
import fitz  # PyMuPDF

def classify_pdf(pdf_bytes: bytes) -> dict:
    """
    Classify a PDF as DIGITAL, SCANNED_OCR, or NO_TEXT.

    Returns:
        {
            "pdf_type": "DIGITAL" | "SCANNED_OCR" | "NO_TEXT",
            "page_count": int,
            "total_chars": int,
            "pages_with_full_images": int,
            "creator": str | None,     # PDF creator metadata (hint only)
            "producer": str | None,    # PDF producer metadata (hint only)
        }
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    meta = doc.metadata

    page_results = []
    for page in doc:
        text = page.get_text("text")
        blocks = page.get_text("dict")["blocks"]
        image_blocks = [b for b in blocks if b["type"] == 1]

        # A "full-page image" heuristic: image block covers >50% of page area
        page_area = page.rect.width * page.rect.height
        large_images = [
            b for b in image_blocks
            if (b["bbox"][2] - b["bbox"][0]) * (b["bbox"][3] - b["bbox"][1]) > page_area * 0.5
        ]

        page_results.append({
            "char_count": len(text.strip()),
            "has_large_image": len(large_images) > 0,
        })

    total_chars = sum(p["char_count"] for p in page_results)
    pages_with_images = sum(1 for p in page_results if p["has_large_image"])
    page_count = len(page_results)

    # Classification rules
    if total_chars < 100:
        pdf_type = "NO_TEXT"
    elif page_count > 0 and (pages_with_images / page_count) >= 0.5:
        # Most pages are full-page images → scanned document with OCR overlay
        pdf_type = "SCANNED_OCR"
    else:
        # Text without dominant full-page images → digital native
        pdf_type = "DIGITAL"

    return {
        "pdf_type": pdf_type,
        "page_count": page_count,
        "total_chars": total_chars,
        "pages_with_full_images": pages_with_images,
        "creator": meta.get("creator"),
        "producer": meta.get("producer"),
    }
```

**Classification outcomes:**

| Type | Action |
|------|--------|
| `DIGITAL` | Write `ocr_grade=EXEMPT` to Paperless. Stop. No scoring, no remediation. |
| `SCANNED_OCR` | Proceed to heuristic scoring. |
| `NO_TEXT` | Skip heuristic scoring. Write `ocr_grade=F` directly. Queue for Tier 1 remediation immediately. |

**Producer metadata as a hint (informational only, not a gate):**

| Producer / Creator value | Likely meaning |
|--------------------------|---------------|
| `ABBYY FineReader` | ScanSnap-produced OCR |
| `Tesseract` | Paperless or third-party OCR |
| `Adobe Acrobat` | Digital-native or Adobe Acrobat scan |
| `Microsoft Print to PDF` | Digital-native |
| `PrimoPDF`, `CutePDF`, `macOS Quartz` | Digital-native |

This metadata is stored in the score record for debugging but does not override classification logic.

---

## Step 2 — Heuristic Scoring

**Requires:** Text string from `document.content` (Paperless API)  
**Library:** `wordfreq` (`pip install wordfreq`)

The heuristic scorer computes four independent signals and combines them into a single 0–100 composite score.

### Signal 1 — Dictionary Validity Ratio (weight: 45%)

Measures what fraction of alphabetic tokens (3+ characters) appear in the `wordfreq` English word frequency database. A token is "valid" if its real-world word frequency is greater than zero.

`wordfreq` is preferred over `pyspellchecker` because it uses frequency-weighted data from large corpora and handles proper nouns, technical terms, and abbreviations gracefully — it returns a non-zero frequency for words like "USD", "IBAN", "Toyota", "HVAC" whereas a spell-checker would flag them as errors.

```python
from wordfreq import word_frequency
import re

def dictionary_ratio(text: str, lang: str = "en") -> float:
    """
    Fraction of 3+ character alphabetic tokens that are recognized English words.
    Range: 0.0 (all garbage) to 1.0 (all valid words).
    """
    tokens = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    if not tokens:
        return 0.0
    valid = sum(1 for t in tokens if word_frequency(t, lang) > 0)
    return valid / len(tokens)
```

**Calibration benchmarks:**
- Digital-native or excellent OCR: 0.75–0.95
- Good ScanSnap ABBYY OCR: 0.65–0.85 (proper nouns, account numbers reduce it)
- Mediocre Tesseract OCR: 0.45–0.65
- Poor OCR with significant artifacts: 0.20–0.45
- Unreadable garbage: 0.0–0.20

### Signal 2 — Character Quality Ratio (weight: 25%)

Measures the fraction of characters that are standard printable characters (letters, digits, common punctuation, whitespace). Non-standard characters appear when OCR misreads characters as Unicode symbols, box-drawing characters, or control characters.

```python
VALID_CHARS = set(
    'abcdefghijklmnopqrstuvwxyz'
    'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    '0123456789'
    ' \t\n\r'
    '.,!?;:\'"()-_/\\$%@#&+=[]{}|<>~`^*'
)

def char_quality_ratio(text: str) -> float:
    """
    Fraction of characters that are standard printable ASCII.
    Range: 0.0 to 1.0.
    """
    if not text:
        return 0.0
    valid = sum(1 for c in text if c in VALID_CHARS)
    return valid / len(text)
```

### Signal 3 — Garbage Token Ratio (weight: 20%)

Penalizes tokens that match known OCR artifact patterns. Returns the fraction of tokens that are garbage, so this is inverted in the composite formula.

```python
import re

# Patterns that indicate OCR artifacts
GARBAGE_PATTERNS = [
    re.compile(r'^[^a-zA-Z0-9]{3,}$'),    # all-symbol token (e.g., "---", "|||")
    re.compile(r'[a-zA-Z]{20,}'),           # absurdly long "word" (line concat failure)
    re.compile(r'^[a-zA-Z0-9]$'),           # single character token (OCR fragment)
    re.compile(r'[^\x00-\x7F]{2,}'),        # run of non-ASCII characters
    re.compile(r'^[lI1|]{4,}$'),            # run of common OCR-confused vertical chars
    re.compile(r'\d{15,}'),                 # implausibly long number string
]

def garbage_token_ratio(text: str) -> float:
    """
    Fraction of whitespace-delimited tokens that match OCR artifact patterns.
    Range: 0.0 (no garbage) to 1.0 (all garbage).
    """
    tokens = text.split()
    if not tokens:
        return 0.0
    garbage_count = sum(
        1 for t in tokens
        if any(p.search(t) for p in GARBAGE_PATTERNS)
    )
    return garbage_count / len(tokens)
```

### Signal 4 — Text Density Score (weight: 10%)

Sanity-checks that a scanned document produced a plausible amount of text. Very sparse text on a page that has a full-page image indicates OCR failure or a nearly-blank page.

```python
def text_density_score(char_count: int, page_count: int) -> float:
    """
    Normalized score for characters-per-page. 
    Range: 0.0 (empty) to 1.0 (normal document density).
    
    Typical values:
      - Standard letter/statement: 1,500–4,000 chars/page → 1.0
      - Short form or label: 200–800 chars/page → 0.6
      - Near-blank page: 0–50 chars/page → 0.0
    """
    if page_count <= 0:
        return 0.0
    chars_per_page = char_count / page_count
    if chars_per_page < 50:
        return 0.0
    elif chars_per_page < 200:
        return 0.2
    elif chars_per_page < 500:
        return 0.4
    elif chars_per_page < 1000:
        return 0.6
    elif chars_per_page < 1500:
        return 0.8
    else:
        return 1.0
```

---

## Step 3 — Composite Score Calculation

```python
def compute_ocr_quality_score(text: str, page_count: int) -> dict:
    """
    Compute a composite OCR quality score.

    Args:
        text: The document content string from Paperless API.
        page_count: Number of pages (from PDF classification or Paperless metadata).

    Returns:
        {
            "score": float,          # 0.0–100.0 (higher = better)
            "grade": str,            # "A" | "B" | "C" | "F"
            "dict_ratio": float,
            "char_quality": float,
            "garbage_ratio": float,
            "density_score": float,
            "token_count": int,
            "char_count": int,
        }
    """
    dr = dictionary_ratio(text)
    cq = char_quality_ratio(text)
    gr = garbage_token_ratio(text)
    ds = text_density_score(len(text), page_count)

    score = (
        dr  * 0.45 +
        cq  * 0.25 +
        (1.0 - gr) * 0.20 +
        ds  * 0.10
    ) * 100.0

    score = round(min(max(score, 0.0), 100.0), 1)

    if score >= 80:
        grade = "A"
    elif score >= 65:
        grade = "B"
    elif score >= 45:
        grade = "C"
    else:
        grade = "F"

    tokens = text.split()
    return {
        "score": score,
        "grade": grade,
        "dict_ratio": round(dr, 4),
        "char_quality": round(cq, 4),
        "garbage_ratio": round(gr, 4),
        "density_score": round(ds, 4),
        "token_count": len(tokens),
        "char_count": len(text),
    }
```

### Score Thresholds

| Score | Grade | Interpretation | Pipeline Action |
|-------|-------|----------------|-----------------|
| 80–100 | **A** | Excellent OCR, no visible artifacts | Log score only, no remediation |
| 65–79 | **B** | Good OCR, occasional minor artifacts | Log score only; flag for optional human review |
| 45–64 | **C** | Mediocre OCR, artifacts present | Ollama secondary validation → queue for remediation if confirmed |
| 0–44 | **F** | Poor or failed OCR | Direct queue for Tier 1 remediation |

> **Threshold tuning:** Run the Phase 0 baseline inventory first. These thresholds are starting points; the actual distribution in your library may require adjustment. See [OCR-BASELINE-INVENTORY.md](OCR-BASELINE-INVENTORY.md).

---

## Step 4 — Ollama Secondary Validation (C-Grade Only)

Ollama (`phi3:mini`) is invoked as a secondary signal only for documents scoring in the 40–70 range. This resolves ambiguity between "document has lots of proper nouns and abbreviations" (legitimately lower dict_ratio but good OCR) vs "document has genuine OCR garbage."

Full prompt design is specified in [OCR-OLLAMA-INTEGRATION.md](OCR-OLLAMA-INTEGRATION.md).

**Decision logic after Ollama response:**

```python
def should_queue_for_remediation(heuristic_score: float, ollama_result: dict | None) -> bool:
    """
    Decide if a document should be queued for remediation based on
    heuristic score and optional Ollama secondary score.
    """
    if heuristic_score < 45:
        # F-grade: always queue, no Ollama needed
        return True

    if heuristic_score >= 70:
        # B/A-grade: never queue, no Ollama needed
        return False

    # C-grade (45–70): use Ollama to decide
    if ollama_result is None:
        # Ollama unavailable: conservative choice — queue if score < 55
        return heuristic_score < 55

    ollama_score = ollama_result.get("quality_score", 5)
    has_artifacts = ollama_result.get("has_artifacts", False)
    is_coherent = ollama_result.get("coherent", True)

    # Queue if Ollama confirms quality issues
    return (not is_coherent) or has_artifacts or (ollama_score <= 5)
```

---

## Data Schema

### Table: `ocr_quality_scores`

```sql
CREATE TABLE IF NOT EXISTS ocr_quality_scores (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id             INTEGER NOT NULL UNIQUE,
    assessed_at             TEXT NOT NULL,            -- ISO 8601 timestamp
    doc_modified_at         TEXT,                     -- Paperless document.modified
    
    -- Classification
    pdf_type                TEXT NOT NULL,            -- DIGITAL | SCANNED_OCR | NO_TEXT
    pdf_creator             TEXT,                     -- from PDF metadata (hint)
    pdf_producer            TEXT,                     -- from PDF metadata (hint)
    page_count              INTEGER,
    
    -- Heuristic score
    score                   REAL,                     -- 0.0–100.0
    grade                   TEXT,                     -- A | B | C | F | EXEMPT
    dict_ratio              REAL,
    char_quality            REAL,
    garbage_ratio           REAL,
    density_score           REAL,
    token_count             INTEGER,
    char_count              INTEGER,
    
    -- Ollama secondary score (nullable — only used for C-grade)
    ollama_quality_score    INTEGER,                  -- 1–10
    ollama_coherent         INTEGER,                  -- 0 or 1 (boolean)
    ollama_has_artifacts    INTEGER,                  -- 0 or 1 (boolean)
    ollama_raw_response     TEXT,                     -- full JSON for debugging
    
    -- Remediation tracking
    remediation_status      TEXT NOT NULL DEFAULT 'NONE',
        -- NONE | QUEUED | IN_PROGRESS | IMPROVED | FAILED | REJECTED | DEFERRED_BUDGET | EXEMPT
    remediation_queued_at   TEXT,
    remediation_completed_at TEXT,
    pre_remediation_score   REAL,
    post_remediation_score  REAL,
    ocr_engine_used         TEXT,                     -- tesseract | azure | none
    azure_pages_used        INTEGER DEFAULT 0,        -- for budget tracking
    
    -- Ollama comparison gate result (nullable)
    ollama_comparison       TEXT,                     -- "A" | "B" | null
    ollama_comparison_confidence TEXT,                -- low | medium | high | null
    
    -- Paperless custom field sync
    paperless_fields_updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_grade ON ocr_quality_scores(grade);
CREATE INDEX IF NOT EXISTS idx_remediation_status ON ocr_quality_scores(remediation_status);
CREATE INDEX IF NOT EXISTS idx_assessed_at ON ocr_quality_scores(assessed_at);
```

### Table: `azure_usage_log`

```sql
CREATE TABLE IF NOT EXISTS azure_usage_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     INTEGER NOT NULL,
    analyzed_at     TEXT NOT NULL,
    pages_analyzed  INTEGER NOT NULL,
    result_accepted INTEGER NOT NULL DEFAULT 0,   -- 1 if comparison gate passed
    cost_usd        REAL                          -- estimated: pages * 0.0015
);

CREATE INDEX IF NOT EXISTS idx_azure_month ON azure_usage_log(
    substr(analyzed_at, 1, 7)   -- "YYYY-MM" for monthly budget queries
);
```

---

## FastAPI Endpoint Specification

```
GET  /api/ocr/score/{document_id}
     → Returns current score record (or 404 if not yet assessed)

POST /api/ocr/score/{document_id}
     → Triggers a new assessment (downloads PDF, classifies, scores)
     → Query param: ?force=true to re-assess even if recently assessed
     → Returns score result

GET  /api/ocr/queue
     → Returns documents in remediation queue
     → Query params: ?status=QUEUED&grade=F&limit=50

GET  /api/ocr/stats
     → Returns grade distribution, remediation status summary, Azure spend this month

POST /api/ocr/remediate/{document_id}
     → Manually trigger remediation for a specific document
     → Body: {"tier": "auto" | "tesseract" | "azure"}
```

---

## Dependencies

```
# requirements-ocr-scorer.txt
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
pymupdf>=1.24.0         # PDF classification
wordfreq>=3.1.1         # dictionary ratio signal
requests>=2.31.0        # Paperless API client
httpx>=0.27.0           # async HTTP (for Ollama + Azure calls)
pydantic>=2.6.0
```
