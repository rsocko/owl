"""Data contracts for OCR candidate generation and comparison (issue #18, slice 1),
plus the apply/rollback state fields added in slice 2.

Slice 1 covers *generation*, *comparison*, and *staging* of alternate OCR
candidates and never writes to Paperless. Slice 2 (``application_service.py``)
applies an accepted candidate as the new latest Paperless version, preserves
the prior version, supports rollback, and durably records the issue #114
downstream-invalidation event — see
``docs/modules/ocr-quality/ocr-remediation-engine.md`` for the full design
and safety invariants. Nothing in *this* module or in ``candidate_service.py``
writes to Paperless; only ``application_service.py`` does.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class CandidateState(str, Enum):
    """The candidate lifecycle state machine (design doc "Candidate state").

    ``REQUESTED -> RUNNING -> READY -> APPLYING -> ACCEPTED``, or
    ``READY -> REJECTED | EXPIRED | FAILED``.

    ``APPLYING`` is an OWL-internal addition to the design doc's state
    diagram: it covers the window between a user's accept decision and
    Paperless confirming the new version (the coordinated operation in
    ``application_service.py``). A failed apply returns the candidate to
    ``READY`` (bounded by ``apply_attempts``/``candidate_max_apply_attempts``)
    rather than a terminal state, so the same candidate can be retried.
    ``ACCEPTED`` means what the design doc says: Paperless confirmed the new
    latest version and content, and the downstream-invalidation record for
    issue #114 is durable.
    """

    REQUESTED = "requested"
    RUNNING = "running"
    READY = "ready"
    APPLYING = "applying"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


# States from which a candidate may still be cancelled (best-effort).
# APPLYING is deliberately excluded: once a Paperless write may be in
# flight, cancellation could race the upload — bounded polling/timeout in
# application_service.py is the only way out of APPLYING.
CANCELLABLE_STATES = frozenset({CandidateState.REQUESTED, CandidateState.RUNNING})

# Terminal states — no further generation/decision work happens.
TERMINAL_STATES = frozenset(
    {
        CandidateState.ACCEPTED,
        CandidateState.REJECTED,
        CandidateState.EXPIRED,
        CandidateState.FAILED,
    }
)


class ComparisonBlockingFinding(str, Enum):
    """A blocking-severity finding from comparing a candidate to the current document.

    Presence of any blocking finding does not itself reject a candidate in
    this slice — a human reviewer still makes the final accept/reject call —
    but it is surfaced prominently so a higher text score alone can never be
    read as authorization.
    """

    PAGES_MISSING = "pages_missing"
    PAGES_REORDERED = "pages_reordered"
    NOT_SEARCHABLE_PDF = "not_searchable_pdf"
    TEXT_MISALIGNED = "text_misaligned"
    MACHINE_REGRESSION = "machine_regression"
    VERSION_STALE = "version_stale"
    UNKNOWN_ERROR = "unknown_error"


class EngineName(str, Enum):
    """Supported candidate-generation engines. One engine owns each candidate."""

    OCRMYPDF_TESSERACT = "ocrmypdf-tesseract-5"
    AZURE_PREBUILT_READ = "azure-prebuilt-read"


class Decision(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class CandidateGenResult(BaseModel):
    """Result of a single provider's ``generate_candidate`` call.

    Never raises for expected failure modes (missing binary, provider error,
    timeout) — callers inspect ``success``/``error_message`` and transition
    the candidate to ``FAILED`` rather than letting an exception propagate.
    """

    success: bool
    candidate_pdf_bytes: bytes | None = None
    candidate_text: str | None = None
    page_count: int = 0
    runtime_seconds: float = 0.0
    cost_estimate: float | None = Field(
        default=None, description="Estimated USD cost, e.g. for a billed Azure call."
    )
    provider_operation_id: str | None = None
    word_confidence: dict[str, Any] | None = Field(
        default=None, description="Optional per-provider confidence/geometry summary."
    )
    error_message: str | None = None

    model_config = {"arbitrary_types_allowed": True}


class ComparisonResult(BaseModel):
    """Comparison of a READY candidate against the current document.

    Purely informational: a higher text/machine score is evidence, not
    authorization. Downstream-extractor regression detection is noted but not
    fully implemented in this slice (tracked in issue #114).
    """

    comparison_id: str = Field(default_factory=lambda: str(uuid4()))
    source_checksum: str
    candidate_checksum: str
    blocking_findings: list[ComparisonBlockingFinding] = Field(default_factory=list)
    page_count_current: int
    page_count_candidate: int
    text_diff_summary: dict[str, Any] = Field(default_factory=dict)
    overlay_score_delta: float | None = None
    machine_score_delta: float | None = None
    downstream_regression_note: str = (
        "Downstream extractor regression detection is not implemented in this "
        "slice; tracked in issue #114."
    )
    performed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    performer_notes: str | None = None


class OcrQualityCandidate(BaseModel):
    """Versioned OCR candidate for a single document (design doc "Candidate state")."""

    candidate_id: str = Field(default_factory=lambda: str(uuid4()))
    document_id: int
    source_version_id: str | None = Field(
        default=None, description="Paperless version proxy key, e.g. 'checksum:<sha>'."
    )
    source_checksum: str = Field(description="SHA-256 of the current document's PDF bytes.")

    state: CandidateState = CandidateState.REQUESTED

    engine: EngineName
    model_version: str
    settings: dict[str, Any] = Field(default_factory=dict)

    candidate_pdf_checksum: str | None = None
    candidate_text_checksum: str | None = None

    page_count: int = 0
    runtime_seconds: float | None = None
    cost_estimate: float | None = None
    provider_operation_id: str | None = None

    overlay_score: float | None = None
    machine_score: float | None = None
    scorer_version: str | None = None

    comparison: ComparisonResult | None = None

    actor: str = "system"
    decision: Decision | None = None
    decision_reason: str | None = None
    decided_at: datetime | None = None

    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    expires_at: datetime
    retention_window_days: int = 30

    failure_reason: str | None = None

    # --- Apply/rollback (slice 2) ---
    apply_attempts: int = Field(
        default=0, description="Number of apply attempts made so far (bounded by config)."
    )
    apply_last_error: str | None = Field(
        default=None, description="Error message from the most recent failed apply attempt."
    )
    paperless_task_id: str | None = Field(
        default=None,
        description=(
            "Celery task id from the in-flight or most recent update_version call, "
            "kept so a crash mid-apply can resume/verify instead of re-uploading."
        ),
    )
    applied_paperless_version_id: int | None = Field(
        default=None, description="Paperless document id of the version this candidate became."
    )
    applied_at: datetime | None = None
    invalidation_recorded: bool = Field(
        default=False,
        description=(
            "Whether AnalysisFreshnessService.record_invalidation() succeeded for this "
            "candidate's application. A Paperless write is never undone if this is False; "
            "the candidate is still ACCEPTED but flagged so downstream freshness can be retried."
        ),
    )
