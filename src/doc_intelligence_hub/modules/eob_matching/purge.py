"""Identify and purge stale EOB records with garbage extracted data.

Stale records are those where the provider_name field contains document
boilerplate text that was incorrectly extracted before validation logic
was added (PR #792).

Criteria for a stale record (any one is sufficient):
- provider_name contains >8 words
- provider_name matches known boilerplate phrases
- all amount fields are 0 or null AND provider_name is suspiciously long (>4 words)
"""

from __future__ import annotations

import re
from typing import NamedTuple

from sqlalchemy.orm import Session

from doc_intelligence_hub.modules.eob_matching.database import EOBRecord, MatchRecord

# Known boilerplate phrases that indicate garbage extraction
BOILERPLATE_PATTERNS = [
    r"the summary below",
    r"this is not a bill",
    r"explanation of benefits",
    r"this document is",
    r"for informational purposes",
    r"please review the",
    r"intended to help you understand",
    r"your health plan",
    r"this statement shows",
    r"not a bill",
]

_BOILERPLATE_RE = re.compile("|".join(BOILERPLATE_PATTERNS), re.IGNORECASE)


class PurgeResult(NamedTuple):
    """Result of a purge-stale operation."""

    purged_count: int
    orphaned_matches_removed: int
    record_ids: list[int]
    document_ids: list[int]


def is_stale_eob(record: EOBRecord) -> bool:
    """Return True if the EOB record has garbage extracted data."""
    provider = record.provider_name or ""

    # Check word count > 8
    word_count = len(provider.split())
    if word_count > 8:
        return True

    # Check known boilerplate patterns
    if _BOILERPLATE_RE.search(provider):
        return True

    # All amounts are 0/null with suspiciously long provider (>4 words)
    amounts = [
        record.total_billed,
        record.total_allowed,
        record.total_plan_pays,
        record.total_patient_responsibility,
    ]
    all_amounts_empty = all(a is None or a == 0 for a in amounts)
    return bool(all_amounts_empty and word_count > 4)


def find_stale_eobs(db: Session) -> list[EOBRecord]:
    """Query all EOB records and return those that are stale."""
    all_records = db.query(EOBRecord).all()
    return [r for r in all_records if is_stale_eob(r)]


def purge_stale_eobs(db: Session, *, dry_run: bool = False) -> PurgeResult:
    """Delete stale EOB records and any orphaned match records.

    Args:
        db: SQLAlchemy session
        dry_run: If True, identify stale records without deleting them

    Returns:
        PurgeResult with counts and IDs of affected records
    """
    stale_records = find_stale_eobs(db)

    if not stale_records:
        return PurgeResult(
            purged_count=0,
            orphaned_matches_removed=0,
            record_ids=[],
            document_ids=[],
        )

    record_ids = [r.id for r in stale_records]
    document_ids = [r.document_id for r in stale_records]

    # Find matches that reference these EOB document_ids
    orphaned_matches = (
        db.query(MatchRecord).filter(MatchRecord.eob_document_id.in_(document_ids)).all()
    )
    orphaned_count = len(orphaned_matches)

    if not dry_run:
        # Delete orphaned matches first (FK-like cleanup)
        for match in orphaned_matches:
            db.delete(match)

        # Delete stale EOB records
        for record in stale_records:
            db.delete(record)

        db.commit()

    return PurgeResult(
        purged_count=len(stale_records),
        orphaned_matches_removed=orphaned_count,
        record_ids=record_ids,
        document_ids=document_ids,
    )
