"""Calibration measurement for #17's activation gate (issue #167).

Read-only: computes agreement between the deterministic accept/reject
"lean" already available on every decided ``OcrQualityCandidate`` row
(see ``comparison.classify_deterministic_lean``) and the actual human
``decision`` recorded via #18's accept/reject workflow. This is the
measurement tooling the design doc's activation gate requires before #17
(LLM secondary review) can even be considered — it does not itself decide
or recommend anything, and it never calls an LLM.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from .candidate_models import DeterministicLean
from .comparison import classify_deterministic_lean
from .database import OcrQualityCandidate

SessionFactory = Callable[[], Session]


def _rate(count: int, total: int) -> float | None:
    """Round to 4 decimals; ``None`` (not 0.0) when there is no denominator,
    so callers can't mistake "no data" for "0% agreement".
    """
    if total == 0:
        return None
    return round(count / total, 4)


class OcrCalibrationService:
    """Computes the deterministic-lean-vs-human-decision calibration summary."""

    def __init__(self, session_factory: SessionFactory):
        self.session_factory = session_factory

    def get_summary(self) -> dict[str, Any]:
        db = self.session_factory()
        try:
            rows = (
                db.query(OcrQualityCandidate).filter(OcrQualityCandidate.decision.isnot(None)).all()
            )
        finally:
            db.close()

        decided_count = len(rows)
        agreement_count = 0
        false_positive_count = 0  # lean favors_accept, human rejected
        false_negative_count = 0  # lean favors_reject, human accepted
        uncertain_count = 0
        uncertain_accepted_count = 0
        uncertain_rejected_count = 0

        for row in rows:
            lean = classify_deterministic_lean(
                blocking_findings=row.blocking_findings or [],
                overlay_score_delta=row.overlay_score_delta,
                content_score_delta=row.content_score_delta,
            )
            decision = row.decision

            if lean is DeterministicLean.NO_STRONG_SIGNAL:
                uncertain_count += 1
                if decision == "accepted":
                    uncertain_accepted_count += 1
                elif decision == "rejected":
                    uncertain_rejected_count += 1
                continue

            if lean is DeterministicLean.FAVORS_ACCEPT:
                if decision == "accepted":
                    agreement_count += 1
                elif decision == "rejected":
                    false_positive_count += 1
            elif lean is DeterministicLean.FAVORS_REJECT:
                if decision == "rejected":
                    agreement_count += 1
                elif decision == "accepted":
                    false_negative_count += 1

        return {
            "decided_count": decided_count,
            "agreement_count": agreement_count,
            "agreement_rate": _rate(agreement_count, decided_count),
            "false_positive_count": false_positive_count,
            "false_positive_rate": _rate(false_positive_count, decided_count),
            "false_negative_count": false_negative_count,
            "false_negative_rate": _rate(false_negative_count, decided_count),
            "uncertain_count": uncertain_count,
            "uncertain_rate": _rate(uncertain_count, decided_count),
            "uncertain_accepted_count": uncertain_accepted_count,
            "uncertain_rejected_count": uncertain_rejected_count,
        }
