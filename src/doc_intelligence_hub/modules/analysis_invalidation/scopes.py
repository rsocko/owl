"""Manual-invalidation scope resolution.

Resolves human-facing invalidation scopes (``all``, ``low_confidence_failed``,
``documents``) to a bounded list of document IDs. The ``low_confidence_failed``
scope reads OCR quality's own database read-only — the same cross-module
read pattern ``ocr_quality.service`` already uses against the Action Queue
database (best-effort: any failure just yields no documents from that scope
rather than raising).
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .config import settings

logger = logging.getLogger(__name__)

# ocr_quality.scoring_models.AssessmentStatus values that represent a
# document whose OCR-derived analysis is not trustworthy as-is.
_LOW_CONFIDENCE_FAILED_STATUSES = ("REVIEW_RECOMMENDED", "FAILED")


def resolve_low_confidence_failed_document_ids(limit: int) -> list[int]:
    """Document IDs whose latest OCR quality assessment is low-confidence/failed.

    Best-effort, read-only: if the OCR quality database is unavailable or
    the schema doesn't match, returns an empty list rather than raising.
    """
    try:
        from doc_intelligence_hub.modules.ocr_quality.database import DocumentAssessment

        engine = create_engine(settings.ocr_quality_database_url, echo=False)
        session_local = sessionmaker(bind=engine)
        db = session_local()
        try:
            rows = (
                db.query(DocumentAssessment.document_id)
                .filter(DocumentAssessment.review_status.in_(_LOW_CONFIDENCE_FAILED_STATUSES))
                .order_by(DocumentAssessment.document_id)
                .distinct()
                .limit(limit)
                .all()
            )
            return [row[0] for row in rows]
        finally:
            db.close()
    except Exception:  # pragma: no cover - best-effort cross-module read only
        logger.exception("Could not resolve low_confidence_failed invalidation scope")
        return []
