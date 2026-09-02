"""Versioned contracts for the OCR quality baseline inventory (issue #25).

Schema versions are recorded on every persisted row so re-runs, resumes, and
the future #29 scorer can tell exactly which logic produced a given result.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

# Stage-1 preliminary signal/heuristic version. This is deliberately NOT the
# quality scorer version — issue #29 owns the real overlay/machine-extraction
# scorer. This version only identifies the *stratification* heuristic so a
# rerun with a changed heuristic is treated as a new assessment.
INVENTORY_SIGNAL_VERSION = "ocr-quality-inventory-signals-v1"

# Stage-2 PDF page-profiling logic version.
PDF_PROFILE_VERSION = "ocr-quality-pdf-profile-v1"

SUMMARY_SCHEMA_VERSION = "1.0"


class RunStage(str, Enum):
    STAGE_1_CORPUS_SCAN = "stage_1_corpus_scan"
    STAGE_2_STRATIFIED_SAMPLE = "stage_2_stratified_sample"
    # A single-document, on-demand Stage-2 trigger (e.g. from the document
    # detail page) rather than a corpus-wide random sample.
    STAGE_2_MANUAL_SINGLE_DOCUMENT = "stage_2_manual_single_document"


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Disposition(str, Enum):
    """Per-document outcome of a Stage-1 or Stage-2 attempt."""

    ASSESSED = "assessed"
    SKIPPED = "skipped"
    FAILED = "failed"


class ReasonCode(str, Enum):
    """Safe, non-content reason codes for skips/failures and document flags."""

    OK = "ok"
    EMPTY_CONTENT = "empty_content"
    MISSING_DOCUMENT_ID = "missing_document_id"
    FETCH_FAILED = "fetch_failed"
    PDF_DOWNLOAD_FAILED = "pdf_download_failed"
    PDF_PARSE_FAILED = "pdf_parse_failed"
    PDF_ENCRYPTED = "pdf_encrypted"
    ALREADY_UP_TO_DATE = "already_up_to_date"
    LEGACY_SCORE_UNAVAILABLE = "legacy_score_unavailable"
    RESUMED_FROM_CURSOR = "resumed_from_cursor"


class DocumentProfile(str, Enum):
    """Stage-2 page-aware document classification."""

    DIGITAL_TEXT = "digital_text"
    SCANNED_WITH_OVERLAY = "scanned_with_overlay"
    NO_TEXT = "no_text"
    MIXED = "mixed"
    UNKNOWN = "unknown"


@dataclasses.dataclass(frozen=True)
class DocumentSignals:
    """Pure, derived Stage-1 signals for one document. No raw OCR text."""

    content_length: int
    word_count: int
    non_ascii_ratio: float
    whitespace_ratio: float
    repetition_ratio: float
    avg_token_length: float
    distinct_token_ratio: float
    table_shape_hint: bool
    code_shape_hint: bool
    preliminary_score: int  # 0-100 quality-risk estimate, stratification only
    reason_codes: tuple[ReasonCode, ...] = ()


@dataclasses.dataclass(frozen=True)
class PageSignal:
    """Whether one PDF page has an embedded text layer and/or images."""

    has_text: bool
    has_image: bool


@dataclasses.dataclass(frozen=True)
class DocProfileResult:
    profile: DocumentProfile
    page_count: int
    digital_pages: int
    scanned_overlay_pages: int
    no_text_pages: int
    reason_codes: tuple[ReasonCode, ...] = ()


@dataclasses.dataclass
class RunSummary:
    run_id: str
    stage: RunStage
    started_at: str
    scope_digest: str
    config_digest: str
    instance_digest: str
    signal_version: str
    finished_at: str | None = None
    status: RunStatus = RunStatus.RUNNING
    counts: Counter[str] = dataclasses.field(default_factory=Counter)
    throughput_docs_per_second: float | None = None
    schema_version: str = SUMMARY_SCHEMA_VERSION
    redacted: bool = True

    def add(self, disposition: Disposition) -> None:
        self.counts[disposition.value] += 1

    def finish(self, *, status: RunStatus = RunStatus.COMPLETED) -> None:
        self.finished_at = datetime.now(UTC).isoformat()
        self.status = status


def to_json_safe(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_json_safe(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Counter):
        return dict(value)
    if isinstance(value, dict):
        return {str(key): to_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_safe(item) for item in value]
    return value
