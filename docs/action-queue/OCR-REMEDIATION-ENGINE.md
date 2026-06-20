# OCR Remediation Engine — Design & Implementation Specification

## Overview

The remediation engine improves the OCR quality of documents that score grade C or F. It operates as a background worker process that pulls from the remediation queue, runs OCR in tiers of increasing capability and cost, and commits improved versions to Paperless-ngx only when a quality comparison gate confirms measurable improvement.

**Core principle: never degrade.** The gate ensures that existing OCR text (including high-quality ScanSnap ABBYY text) is only replaced when the new OCR is demonstrably better by both heuristic score and Ollama validation.

---

## Architecture

```
Remediation Queue
(score_db: remediation_status = QUEUED)
         │
         ▼
┌────────────────────┐
│  Worker Process    │  (polls queue, one document at a time)
│  remediation_      │
│  worker.py         │
└────────┬───────────┘
         │
         ├── 1. Download original PDF
         │      Paperless GET /api/documents/{id}/download/?original=true
         │
         ├── 2. Tier 1: OCRmyPDF + Tesseract 5
         │      Run locally, free, fast
         │      ↓
         │   Comparison Gate
         │      pass → commit to Paperless, done
         │      fail → escalate to Tier 2
         │
         └── 3. Tier 2: Azure Document Intelligence Read API
                Run in cloud, ~$0.0015/page, budget-controlled
                ↓
             Comparison Gate
                pass → commit to Paperless, done
                fail → mark REJECTED, keep original, done
```

---

## Comparison Gate

The comparison gate is the most critical component. It runs after every OCR attempt and decides whether to accept or reject the new text.

### Gate Logic

```python
MIN_SCORE_DELTA = 5.0  # minimum score improvement required to accept new OCR

def comparison_gate(
    old_text: str,
    new_text: str,
    page_count: int,
    old_score: float,
    ollama_client,
) -> dict:
    """
    Decide whether to accept new OCR text over existing text.

    Returns:
        {
            "accept": bool,
            "old_score": float,
            "new_score": float,
            "score_delta": float,
            "ollama_comparison": "A" | "B" | None,
            "ollama_confidence": "low" | "medium" | "high" | None,
            "reason": str,
        }
    """
    new_score_result = compute_ocr_quality_score(new_text, page_count)
    new_score = new_score_result["score"]
    score_delta = new_score - old_score

    # Fast reject: new score is not better
    if score_delta < MIN_SCORE_DELTA:
        return {
            "accept": False,
            "old_score": old_score,
            "new_score": new_score,
            "score_delta": score_delta,
            "ollama_comparison": None,
            "ollama_confidence": None,
            "reason": f"Score delta {score_delta:.1f} below threshold {MIN_SCORE_DELTA}",
        }

    # New score is better — use Ollama to validate if available
    ollama_result = None
    if ollama_client is not None:
        ollama_result = ollama_client.compare_ocr_versions(
            old_text=old_text[:1500],  # sample first 1500 chars
            new_text=new_text[:1500],
        )

    if ollama_result is not None:
        if ollama_result["better_version"] == "A" and ollama_result["confidence"] != "low":
            # Ollama is confident the original was better — reject despite numeric gain
            return {
                "accept": False,
                "old_score": old_score,
                "new_score": new_score,
                "score_delta": score_delta,
                "ollama_comparison": "A",
                "ollama_confidence": ollama_result["confidence"],
                "reason": "Ollama confirms original is more readable despite score improvement",
            }

    accept = True
    reason = f"Score improved by {score_delta:.1f} points"
    if ollama_result:
        reason += f"; Ollama confirms version B better ({ollama_result['confidence']} confidence)"

    return {
        "accept": accept,
        "old_score": old_score,
        "new_score": new_score,
        "score_delta": score_delta,
        "ollama_comparison": ollama_result["better_version"] if ollama_result else None,
        "ollama_confidence": ollama_result["confidence"] if ollama_result else None,
        "reason": reason,
    }
```

---

## Tier 1 — OCRmyPDF + Tesseract 5

### When Used

- Any document with `grade=C` or `grade=F` and `pdf_type=SCANNED_OCR` or `NO_TEXT`
- Free, runs locally, no external dependencies

### Capability

Tesseract 5 (LSTM mode) performs well on:
- Clean black-and-white printed text at ≥200 DPI
- Standard Latin character sets
- Single-column layouts

Tesseract 5 underperforms on:
- Faded or low-contrast scans
- Multi-column newspaper/newsletter layouts
- Tables with merged cells
- Handwritten annotations
- Colored or patterned backgrounds

### OCRmyPDF Configuration

```bash
ocrmypdf \
  --redo-ocr \           # re-OCR pages that already have text (needed for C-grade docs)
  --deskew \             # correct page tilt from scanner
  --clean \              # remove scanning noise artifacts before OCR
  --clean-final \        # also clean in the final archived PDF
  --optimize 1 \         # light PDF optimization (size without quality loss)
  --language eng \
  --sidecar /tmp/{doc_id}_sidecar.txt \   # extract text without parsing the output PDF
  /tmp/{doc_id}_input.pdf \
  /tmp/{doc_id}_output.pdf
```

**Why `--sidecar`:** The sidecar `.txt` file contains the plain text output from Tesseract, allowing the comparison gate to score the new text before touching the PDF. This is the critical separation: score first, commit only if better.

### Python Implementation

```python
import subprocess
import tempfile
import os
from pathlib import Path

def run_tesseract_ocr(pdf_bytes: bytes, doc_id: int) -> dict:
    """
    Run OCRmyPDF + Tesseract 5 on PDF bytes.

    Returns:
        {
            "success": bool,
            "new_text": str | None,
            "output_pdf_bytes": bytes | None,
            "error": str | None,
        }
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / f"{doc_id}_input.pdf"
        output_path = Path(tmpdir) / f"{doc_id}_output.pdf"
        sidecar_path = Path(tmpdir) / f"{doc_id}_sidecar.txt"

        input_path.write_bytes(pdf_bytes)

        cmd = [
            "ocrmypdf",
            "--redo-ocr",
            "--deskew",
            "--clean",
            "--clean-final",
            "--optimize", "1",
            "--language", "eng",
            "--sidecar", str(sidecar_path),
            str(input_path),
            str(output_path),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout per document
            )
        except subprocess.TimeoutExpired:
            return {"success": False, "new_text": None, "output_pdf_bytes": None, "error": "timeout"}

        if result.returncode != 0:
            return {
                "success": False,
                "new_text": None,
                "output_pdf_bytes": None,
                "error": result.stderr[:500],
            }

        new_text = sidecar_path.read_text(encoding="utf-8", errors="replace") if sidecar_path.exists() else ""
        output_pdf_bytes = output_path.read_bytes() if output_path.exists() else None

        return {
            "success": True,
            "new_text": new_text,
            "output_pdf_bytes": output_pdf_bytes,
            "error": None,
        }
```

### Exit Codes

OCRmyPDF uses specific exit codes. Handle these explicitly:

| Code | Meaning | Action |
|------|---------|--------|
| 0 | Success | Proceed to comparison gate |
| 1 | Bad arguments | Log error, mark FAILED |
| 2 | Input not readable | Log error, mark FAILED |
| 3 | Invalid output PDF | Log error, escalate to Tier 2 |
| 4 | Encryption / DRM | Log error, mark REJECTED permanently |
| 5 | `--redo-ocr` failed on a page | Check sidecar — partial success may still be useful |
| 6 | Already has OCR and `--skip-text` was set | Should not occur with `--redo-ocr` |
| 10 | Tesseract not found | Fatal configuration error |

---

## Tier 2 — Azure Document Intelligence Read API

### When Used

- Documents that failed Tier 1 (comparison gate rejected Tesseract output)
- Documents with `pdf_type=NO_TEXT` that Tesseract also failed to process
- Subject to monthly page budget cap

### Azure Setup

1. Create an Azure Document Intelligence resource in Azure Portal (Standard S0 tier for >500 pages/month, or Free F0 for ≤500 pages/month)
2. Note the endpoint URL and API key
3. Store credentials in environment variables:
   ```
   AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=https://<resource>.cognitiveservices.azure.com/
   AZURE_DOCUMENT_INTELLIGENCE_KEY=<your-key>
   ```

### Python Implementation

```python
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
import os

def run_azure_ocr(pdf_bytes: bytes) -> dict:
    """
    Run Azure Document Intelligence Read API on PDF bytes.

    Returns:
        {
            "success": bool,
            "new_text": str | None,
            "pages_analyzed": int,
            "word_confidences": list[float],  # per-word confidence scores
            "error": str | None,
        }
    """
    endpoint = os.environ["AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"]
    key = os.environ["AZURE_DOCUMENT_INTELLIGENCE_KEY"]

    client = DocumentIntelligenceClient(
        endpoint=endpoint,
        credential=AzureKeyCredential(key),
    )

    try:
        poller = client.begin_analyze_document(
            model_id="prebuilt-read",
            body=pdf_bytes,
            content_type="application/pdf",
        )
        result = poller.result()
    except Exception as e:
        return {"success": False, "new_text": None, "pages_analyzed": 0, "word_confidences": [], "error": str(e)}

    # Extract full text and per-word confidence
    lines = []
    confidences = []

    for page in result.pages:
        for line in (page.lines or []):
            lines.append(line.content)
        for word in (page.words or []):
            confidences.append(word.confidence)

    full_text = "\n".join(lines)
    pages_analyzed = len(result.pages)

    return {
        "success": True,
        "new_text": full_text,
        "pages_analyzed": pages_analyzed,
        "word_confidences": confidences,
        "error": None,
    }
```

### Azure Word Confidence as an Additional Gate Signal

Azure returns per-word confidence scores (0.0–1.0). Use these as an additional signal in the comparison gate:

```python
def azure_confidence_summary(word_confidences: list[float]) -> dict:
    if not word_confidences:
        return {"mean_confidence": 0.0, "low_confidence_fraction": 1.0}
    mean_conf = sum(word_confidences) / len(word_confidences)
    low_conf_fraction = sum(1 for c in word_confidences if c < 0.7) / len(word_confidences)
    return {
        "mean_confidence": round(mean_conf, 4),
        "low_confidence_fraction": round(low_conf_fraction, 4),
    }
```

If `mean_confidence < 0.75` from Azure, the document is a challenging scan that even Azure struggled with. In this case, the comparison gate uses a higher bar (`MIN_SCORE_DELTA = 10`) before accepting.

### Budget Control

```python
AZURE_MONTHLY_PAGE_BUDGET = 500  # pages per month; adjust based on your tier and willingness

def check_azure_budget(db_conn, pages_requested: int) -> dict:
    """
    Check if Azure page budget allows processing this document.

    Returns:
        {"allowed": bool, "used_this_month": int, "budget": int, "remaining": int}
    """
    current_month = datetime.utcnow().strftime("%Y-%m")
    row = db_conn.execute(
        "SELECT COALESCE(SUM(pages_analyzed), 0) FROM azure_usage_log WHERE substr(analyzed_at, 1, 7) = ?",
        (current_month,)
    ).fetchone()
    used = row[0] if row else 0
    remaining = AZURE_MONTHLY_PAGE_BUDGET - used
    return {
        "allowed": (used + pages_requested) <= AZURE_MONTHLY_PAGE_BUDGET,
        "used_this_month": used,
        "budget": AZURE_MONTHLY_PAGE_BUDGET,
        "remaining": remaining,
    }
```

---

## Committing Improved PDFs to Paperless

When the comparison gate accepts new OCR text, the improved PDF is delivered to Paperless via the consume directory.

### Consume Directory Method (Recommended)

Drop the improved PDF into Paperless's watch/consume folder with metadata sidecar:

```python
import shutil
import json
from pathlib import Path

PAPERLESS_CONSUME_DIR = Path(os.environ.get("PAPERLESS_CONSUME_DIR", "/consume"))

def commit_improved_pdf(
    doc_id: int,
    output_pdf_bytes: bytes,
    original_filename: str,
    ocr_metadata: dict,
) -> bool:
    """
    Drop improved PDF into Paperless consume directory.
    Uses paperless-ngx metadata sidecar to preserve correspondent,
    tags, dates, and custom fields from the original document.
    """
    # Paperless will re-consume with original metadata via the .json sidecar
    consume_name = f"ocr_improved_{doc_id}_{original_filename}"
    pdf_path = PAPERLESS_CONSUME_DIR / consume_name
    meta_path = PAPERLESS_CONSUME_DIR / f"{consume_name}.json"

    pdf_path.write_bytes(output_pdf_bytes)

    # Paperless JSON metadata sidecar format
    meta = {
        "title": ocr_metadata.get("title"),
        "correspondent": ocr_metadata.get("correspondent"),
        "document_type": ocr_metadata.get("document_type"),
        "tags": ocr_metadata.get("tags", []),
        "created_date": ocr_metadata.get("created_date"),
        "custom_fields": [
            {"field": "OCR Score", "value": str(ocr_metadata["new_score"])},
            {"field": "OCR Grade", "value": ocr_metadata["new_grade"]},
            {"field": "OCR Reviewed", "value": datetime.utcnow().strftime("%Y-%m-%d")},
            {"field": "OCR Engine", "value": ocr_metadata["engine"]},
            {"field": "OCR Remediation", "value": "IMPROVED"},
        ],
    }
    meta_path.write_text(json.dumps(meta, indent=2))

    return True
```

> **Note:** After Paperless re-consumes the improved file, the original document will appear as a duplicate. Paperless's deduplication logic (based on content hash) may merge them — if not, the old document should be manually archived or deleted. Track the document ID pair in the score database for post-processing.

### Alternative: Paperless Management Command

If the consume directory approach creates duplicate management complexity, the `document_archiver` management command can be used instead — but only from within the Paperless container:

```bash
docker exec paperless-ngx python manage.py document_archiver \
  --document {document_id} \
  --overwrite
```

This approach does not allow injecting a pre-OCR'd PDF — it re-runs Paperless's own OCR pipeline (Tesseract). Use the consume directory for Azure results where the improved PDF was created externally.

---

## Worker Process Design

```python
import time
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60

def remediation_worker_loop(db, paperless_client, ollama_client, azure_budget):
    """
    Main worker loop. Polls the queue and processes one document at a time.
    """
    while True:
        doc = db.fetch_next_queued_document()

        if doc is None:
            logger.debug("Queue empty, sleeping %ds", POLL_INTERVAL_SECONDS)
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        logger.info("Processing doc_id=%d (grade=%s)", doc["document_id"], doc["grade"])
        db.update_remediation_status(doc["document_id"], "IN_PROGRESS")

        try:
            process_document(doc, db, paperless_client, ollama_client, azure_budget)
        except Exception as e:
            logger.exception("Unhandled error processing doc_id=%d", doc["document_id"])
            db.update_remediation_status(doc["document_id"], "FAILED", error=str(e))

        time.sleep(1)  # Paperless API rate limiting


def process_document(doc, db, paperless_client, ollama_client, azure_budget):
    doc_id = doc["document_id"]
    old_score = doc["score"]
    old_text = paperless_client.get_document_content(doc_id)
    page_count = doc["page_count"] or 1

    # --- Tier 1: Tesseract ---
    pdf_bytes = paperless_client.download_original(doc_id)
    t1_result = run_tesseract_ocr(pdf_bytes, doc_id)

    if t1_result["success"] and t1_result["new_text"]:
        gate = comparison_gate(old_text, t1_result["new_text"], page_count, old_score, ollama_client)
        if gate["accept"]:
            commit_improved_pdf(doc_id, t1_result["output_pdf_bytes"], ...)
            db.mark_improved(doc_id, gate, engine="tesseract")
            return

    logger.info("Tier 1 rejected for doc_id=%d; escalating to Tier 2", doc_id)

    # --- Tier 2: Azure ---
    budget_check = azure_budget.check(page_count)
    if not budget_check["allowed"]:
        logger.warning("Azure budget exhausted for doc_id=%d; deferring", doc_id)
        db.update_remediation_status(doc_id, "DEFERRED_BUDGET")
        return

    t2_result = run_azure_ocr(pdf_bytes)

    if t2_result["success"] and t2_result["new_text"]:
        gate = comparison_gate(old_text, t2_result["new_text"], page_count, old_score, ollama_client)
        azure_budget.record_usage(doc_id, t2_result["pages_analyzed"], gate["accept"])

        if gate["accept"]:
            # For Azure, we must reconstruct the PDF with new text layer or use the Azure result
            # Best approach: re-run OCRmyPDF with the Azure text as forced output (via stdin)
            # Simpler approach: use the Azure text, re-embed via ocrmypdf --pdf-renderer hocr
            # For MVP: use Tesseract output PDF but replace the text layer with Azure text
            # In practice: store Azure text in Paperless via API PATCH to document.content
            paperless_client.update_document_content(doc_id, t2_result["new_text"])
            db.mark_improved(doc_id, gate, engine="azure")
            return

    # Both tiers failed — keep original
    logger.info("Both tiers rejected for doc_id=%d; marking REJECTED", doc_id)
    db.update_remediation_status(doc_id, "REJECTED")
```

> **Azure text embedding note:** Azure Document Intelligence returns extracted text but not a new PDF. For Tier 2 improvements, the recommended approach is to update the `content` field directly in Paperless via `PATCH /api/documents/{id}/` with the Azure text. This is simpler than generating a new PDF and avoids consume-directory duplication issues. The original scanned PDF is preserved as the archive file.

---

## Updating Paperless Content via PATCH

```python
def update_document_content(self, doc_id: int, new_content: str):
    """
    Update the extracted text content of a Paperless document directly.
    Used for Azure remediation results (text only, no new PDF).
    """
    response = self.session.patch(
        f"{self.base_url}/api/documents/{doc_id}/",
        json={"content": new_content},
    )
    response.raise_for_status()
    return response.json()
```

---

## Dependencies

```
# requirements-remediation.txt
ocrmypdf>=16.3.0
azure-ai-documentintelligence>=1.0.0
azure-core>=1.30.0
pymupdf>=1.24.0
requests>=2.31.0
```

Install OCRmyPDF system dependencies (Tesseract must be installed separately):
```bash
# Ubuntu/Debian
apt-get install tesseract-ocr tesseract-ocr-eng ghostscript unpaper

# Verify
tesseract --version   # expect 5.x
ocrmypdf --version
```

For Docker deployment, use the official `jbarlow83/ocrmypdf` base image.
