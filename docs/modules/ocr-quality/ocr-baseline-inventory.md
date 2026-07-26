---
title: "OCR Baseline Inventory"
sidebar_label: OCR Inventory
sidebar_position: 1
---

# OCR Baseline Inventory — Phase 0 Specification

## Overview

Phase 0 is a **one-shot analysis script** you run once before building anything else. Its purpose is to understand the actual composition and quality distribution of your existing Paperless-ngx library so you can:

1. Calibrate the scoring thresholds against real documents
2. Quantify the remediation workload before committing to building the full pipeline
3. Identify any immediately critical documents (grade F, important tags)
4. Validate that the heuristic scorer is working correctly against your actual content

**Run time estimate:** For 3,000 documents at 1 request/second Paperless API rate: ~50 minutes for text-only scoring. PDF download for classification adds time — do classification on a 10% sample for the baseline if time is a concern.

---

## What the Inventory Script Does

1. Fetches all documents from Paperless-ngx (paginated)
2. For each document: retrieves `content` (text) and metadata (page count, correspondent, tags, created date)
3. Runs heuristic scoring on the text
4. For a configurable sample: downloads the PDF and classifies the type (DIGITAL / SCANNED_OCR / NO_TEXT)
5. Outputs a CSV report and a JSON summary

---

## Script: `scripts/ocr_baseline_inventory.py`

```python
#!/usr/bin/env python3
"""
OCR Baseline Inventory Script
Phase 0: One-shot analysis of all documents in Paperless-ngx.

Usage:
    python scripts/ocr_baseline_inventory.py \
        --paperless-url http://paperless-ngx:8000 \
        --paperless-token <your-token> \
        --output-dir ./baseline_output \
        [--classify-sample 0.1]   # fraction of docs to download+classify (0.1 = 10%)
        [--limit 100]              # limit total docs for a quick test run
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# ── wordfreq ──────────────────────────────────────────────────────────────────
try:
    from wordfreq import word_frequency
    WORDFREQ_AVAILABLE = True
except ImportError:
    print("WARNING: wordfreq not installed. Install with: pip install wordfreq")
    WORDFREQ_AVAILABLE = False

# ── PyMuPDF ───────────────────────────────────────────────────────────────────
try:
    import fitz
    FITZ_AVAILABLE = True
except ImportError:
    print("WARNING: PyMuPDF not installed. PDF classification disabled. Install with: pip install pymupdf")
    FITZ_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
# Scoring functions (inline copies — production versions live in scorer-service)
# ─────────────────────────────────────────────────────────────────────────────

GARBAGE_PATTERNS = [
    re.compile(r'^[^a-zA-Z0-9]{3,}$'),
    re.compile(r'[a-zA-Z]{20,}'),
    re.compile(r'^[a-zA-Z0-9]$'),
    re.compile(r'[^\x00-\x7F]{2,}'),
    re.compile(r'^[lI1|]{4,}$'),
    re.compile(r'\d{15,}'),
]

VALID_CHARS = set(
    'abcdefghijklmnopqrstuvwxyz'
    'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    '0123456789 \t\n\r'
    '.,!?;:\'"()-_/\\$%@#&+=[]{}|<>~`^*'
)


def dictionary_ratio(text: str) -> float:
    if not WORDFREQ_AVAILABLE:
        return -1.0
    tokens = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    if not tokens:
        return 0.0
    valid = sum(1 for t in tokens if word_frequency(t, "en") > 0)
    return valid / len(tokens)


def char_quality_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for c in text if c in VALID_CHARS) / len(text)


def garbage_token_ratio(text: str) -> float:
    tokens = text.split()
    if not tokens:
        return 0.0
    garbage = sum(1 for t in tokens if any(p.search(t) for p in GARBAGE_PATTERNS))
    return garbage / len(tokens)


def text_density_score(char_count: int, page_count: int) -> float:
    if page_count <= 0:
        return 0.0
    cpp = char_count / page_count
    if cpp < 50:    return 0.0
    if cpp < 200:   return 0.2
    if cpp < 500:   return 0.4
    if cpp < 1000:  return 0.6
    if cpp < 1500:  return 0.8
    return 1.0


def compute_score(text: str, page_count: int) -> dict:
    dr = dictionary_ratio(text)
    cq = char_quality_ratio(text)
    gr = garbage_token_ratio(text)
    ds = text_density_score(len(text), page_count)
    score = (dr * 0.45 + cq * 0.25 + (1.0 - gr) * 0.20 + ds * 0.10) * 100.0
    score = round(min(max(score, 0.0), 100.0), 1)
    if score >= 80:   grade = "A"
    elif score >= 65: grade = "B"
    elif score >= 45: grade = "C"
    else:             grade = "F"
    return {
        "score": score, "grade": grade,
        "dict_ratio": round(dr, 4), "char_quality": round(cq, 4),
        "garbage_ratio": round(gr, 4), "density_score": round(ds, 4),
        "token_count": len(text.split()), "char_count": len(text),
    }


def classify_pdf(pdf_bytes: bytes) -> dict:
    if not FITZ_AVAILABLE:
        return {"pdf_type": "UNKNOWN", "page_count": 0, "creator": None, "producer": None}
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        meta = doc.metadata
        page_results = []
        for page in doc:
            text = page.get_text("text")
            blocks = page.get_text("dict")["blocks"]
            page_area = page.rect.width * page.rect.height
            large_images = [
                b for b in blocks if b["type"] == 1 and
                (b["bbox"][2] - b["bbox"][0]) * (b["bbox"][3] - b["bbox"][1]) > page_area * 0.5
            ]
            page_results.append({
                "char_count": len(text.strip()),
                "has_large_image": len(large_images) > 0,
            })
        total_chars = sum(p["char_count"] for p in page_results)
        page_count = len(page_results)
        pages_with_images = sum(1 for p in page_results if p["has_large_image"])

        if total_chars < 100:
            pdf_type = "NO_TEXT"
        elif page_count > 0 and (pages_with_images / page_count) >= 0.5:
            pdf_type = "SCANNED_OCR"
        else:
            pdf_type = "DIGITAL"

        return {
            "pdf_type": pdf_type,
            "page_count": page_count,
            "creator": meta.get("creator", ""),
            "producer": meta.get("producer", ""),
        }
    except Exception as e:
        return {"pdf_type": "ERROR", "page_count": 0, "creator": None, "producer": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Paperless API client (minimal)
# ─────────────────────────────────────────────────────────────────────────────

class PaperlessClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Token {token}"

    def get_documents_page(self, page: int, page_size: int = 100) -> dict:
        r = self.session.get(
            f"{self.base_url}/api/documents/",
            params={"page": page, "page_size": page_size},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def get_document_content(self, doc_id: int) -> str:
        r = self.session.get(f"{self.base_url}/api/documents/{doc_id}/", timeout=30)
        r.raise_for_status()
        return r.json().get("content", "")

    def download_original(self, doc_id: int) -> bytes:
        r = self.session.get(
            f"{self.base_url}/api/documents/{doc_id}/download/",
            params={"original": "true"},
            timeout=120,
        )
        r.raise_for_status()
        return r.content


# ─────────────────────────────────────────────────────────────────────────────
# Main inventory run
# ─────────────────────────────────────────────────────────────────────────────

def run_inventory(args):
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    client = PaperlessClient(args.paperless_url, args.paperless_token)

    # Discover total document count
    first_page = client.get_documents_page(1, page_size=1)
    total = first_page["count"]
    if args.limit:
        total = min(total, args.limit)
    print(f"Total documents to process: {total}")

    classify_every_n = max(1, int(1.0 / args.classify_sample)) if args.classify_sample > 0 else None

    results = []
    csv_path = output_dir / "ocr_baseline.csv"
    csv_fields = [
        "document_id", "title", "correspondent", "created_date", "tags",
        "page_count_from_meta", "char_count", "token_count",
        "score", "grade", "dict_ratio", "char_quality", "garbage_ratio", "density_score",
        "pdf_type", "pdf_creator", "pdf_producer", "classify_error",
    ]

    with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=csv_fields)
        writer.writeheader()

        page_size = 100
        page_num = 1
        processed = 0

        while processed < total:
            page_data = client.get_documents_page(page_num, page_size)
            docs = page_data["results"]

            for doc in docs:
                if processed >= total:
                    break

                doc_id = doc["id"]
                title = doc.get("title", "")
                correspondent = doc.get("correspondent", "")
                created_date = doc.get("created", "")[:10] if doc.get("created") else ""
                tags = ",".join(str(t) for t in doc.get("tags", []))
                page_count_from_meta = doc.get("page_count", 1) or 1

                # Fetch full content (not always in list view)
                try:
                    content = doc.get("content") or client.get_document_content(doc_id)
                    time.sleep(0.2)  # rate limit
                except Exception as e:
                    print(f"  [WARN] doc_id={doc_id}: failed to fetch content: {e}")
                    content = ""

                score_result = compute_score(content, page_count_from_meta)

                row = {
                    "document_id": doc_id,
                    "title": title,
                    "correspondent": correspondent,
                    "created_date": created_date,
                    "tags": tags,
                    "page_count_from_meta": page_count_from_meta,
                    "char_count": score_result["char_count"],
                    "token_count": score_result["token_count"],
                    "score": score_result["score"],
                    "grade": score_result["grade"],
                    "dict_ratio": score_result["dict_ratio"],
                    "char_quality": score_result["char_quality"],
                    "garbage_ratio": score_result["garbage_ratio"],
                    "density_score": score_result["density_score"],
                    "pdf_type": "",
                    "pdf_creator": "",
                    "pdf_producer": "",
                    "classify_error": "",
                }

                # PDF classification (sampled)
                if classify_every_n and (processed % classify_every_n == 0):
                    try:
                        pdf_bytes = client.download_original(doc_id)
                        time.sleep(0.5)
                        clf = classify_pdf(pdf_bytes)
                        row["pdf_type"] = clf["pdf_type"]
                        row["pdf_creator"] = clf.get("creator", "")
                        row["pdf_producer"] = clf.get("producer", "")
                    except Exception as e:
                        row["classify_error"] = str(e)[:100]

                writer.writerow(row)
                results.append(row)
                processed += 1

                if processed % 50 == 0:
                    print(f"  Progress: {processed}/{total}")

            page_num += 1
            if not page_data.get("next"):
                break

    # ── Summary report ──
    grade_counts = {"A": 0, "B": 0, "C": 0, "F": 0}
    for r in results:
        if r["grade"] in grade_counts:
            grade_counts[r["grade"]] += 1

    scores = [r["score"] for r in results if r["score"] is not None]
    f_docs = [r for r in results if r["grade"] == "F"]
    c_docs = [r for r in results if r["grade"] == "C"]

    summary = {
        "generated_at": datetime.utcnow().isoformat(),
        "total_documents": len(results),
        "grade_distribution": grade_counts,
        "score_stats": {
            "min": round(min(scores), 1) if scores else None,
            "max": round(max(scores), 1) if scores else None,
            "mean": round(sum(scores) / len(scores), 1) if scores else None,
            "median": round(sorted(scores)[len(scores) // 2], 1) if scores else None,
        },
        "f_grade_sample": f_docs[:20],  # first 20 F-grade docs for inspection
        "c_grade_sample": c_docs[:10],
        "pdf_type_distribution": {},
    }

    # PDF type distribution (from classified sample)
    classified = [r for r in results if r["pdf_type"]]
    for t in ("DIGITAL", "SCANNED_OCR", "NO_TEXT", "UNKNOWN", "ERROR"):
        count = sum(1 for r in classified if r["pdf_type"] == t)
        if count > 0:
            summary["pdf_type_distribution"][t] = count

    summary_path = output_dir / "ocr_baseline_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    print("\n" + "="*60)
    print("BASELINE INVENTORY COMPLETE")
    print("="*60)
    print(f"Total documents: {len(results)}")
    print(f"Grade A: {grade_counts['A']}  B: {grade_counts['B']}  C: {grade_counts['C']}  F: {grade_counts['F']}")
    if scores:
        print(f"Score range: {min(scores):.1f} – {max(scores):.1f}  (mean: {sum(scores)/len(scores):.1f})")
    print(f"\nOutputs written to: {output_dir}/")
    print(f"  {csv_path.name}         — full document-by-document results")
    print(f"  {summary_path.name}  — grade distribution + sample docs")
    print("\nNext steps:")
    print("  1. Open ocr_baseline.csv in a spreadsheet")
    print("  2. Manually inspect 5–10 documents from each grade tier")
    print("  3. Adjust score thresholds in OCR-QUALITY-SCORING.md if needed")
    print("  4. Check the F-grade list for important documents that need urgent attention")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OCR Baseline Inventory for Paperless-ngx")
    parser.add_argument("--paperless-url", required=True, help="Paperless-ngx base URL")
    parser.add_argument("--paperless-token", required=True, help="Paperless API token")
    parser.add_argument("--output-dir", default="./baseline_output", help="Directory for output files")
    parser.add_argument("--classify-sample", type=float, default=0.1,
                        help="Fraction of documents to download+classify for PDF type (default: 0.1 = 10%%)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit total documents (for testing)")
    args = parser.parse_args()
    run_inventory(args)
```

---

## Running the Script

### Install Dependencies

```bash
cd experiments/home-automation/paperless-action-queue
python -m venv .venv
.venv/Scripts/activate        # Windows
# or: source .venv/bin/activate   # Mac/Linux

pip install requests pymupdf wordfreq
```

### Get Your Paperless API Token

In Paperless-ngx: **Settings → API Token** (or generate via admin panel at `/admin/authtoken/tokenproxy/`)

### Run

```bash
# Full run: score all documents, classify 10% sample
python scripts/ocr_baseline_inventory.py \
    --paperless-url http://your-paperless-host:8000 \
    --paperless-token YOUR_TOKEN_HERE \
    --output-dir ./baseline_output

# Quick test run: first 50 documents only
python scripts/ocr_baseline_inventory.py \
    --paperless-url http://your-paperless-host:8000 \
    --paperless-token YOUR_TOKEN_HERE \
    --output-dir ./baseline_output_test \
    --limit 50
```

---

## Interpreting the Results

### Grade Distribution Expectations

Based on the library composition described (mix of ScanSnap scans and digital PDFs):

| Scenario | Expected distribution |
|----------|----------------------|
| Mostly digital-native PDFs (40%+) | Heavy A concentration once those are excluded |
| Healthy ScanSnap library | Bulk of scanned docs in B range (65–80) |
| Some historical scans on old scanner | C/F tail of 5–15% |
| Significant old faded documents | F count could be 10–20% |

### What to Manually Inspect

After running, open `ocr_baseline.csv` in Excel or a CSV viewer (VS Code's Rainbow CSV extension works well).

**Sort by score ascending** and look at the bottom 20 documents. For each:
1. Open the document in Paperless-ngx
2. Look at the actual PDF
3. Look at the extracted `content` field
4. Ask: does the score feel right?

**Common calibration findings:**

| Finding | Adjustment |
|---------|-----------|
| Medical/financial docs with lots of numbers score low but look fine | Raise C threshold slightly to 42 or lower it to 43 |
| Old receipts that look terrible score B | Lower the B threshold, or accept that heuristics miss pure-number docs |
| Digital bank statements score 60 (C) due to table structure | They should be EXEMPT — check PDF classification is working |
| Handwritten documents score F — expected, can't OCR handwriting | Mark those as permanently REJECTED in the pipeline |

### The F-Grade List is Your Priority

The `f_grade_sample` in `ocr_baseline_summary.json` lists up to 20 F-grade documents with their titles and correspondents. Scan this list for anything that matters:

- Tax documents (critical)
- Medical EOBs or records  
- Legal documents (contracts, deeds)
- Insurance documents

These are your immediate manual review candidates, even before the automated pipeline is built.

---

## Output Files

| File | Contents |
|------|---------|
| `ocr_baseline.csv` | One row per document: ID, title, correspondent, tags, score, grade, all signal values, PDF type |
| `ocr_baseline_summary.json` | Grade distribution, score statistics, sample lists of F/C docs, PDF type breakdown |

The CSV is the primary artifact. Import it into a spreadsheet to pivot, filter, and explore your library composition before building Phase 1.
