"""Analysis invalidation / staleness infrastructure (issue #114).

Provides a durable, privacy-safe record of when a Paperless document's
accepted OCR version (or relevant metadata/module configuration) has
changed, and a generic staleness-check/fingerprint-recording contract that
any downstream analysis module (Action Queue, EOB matching, statements,
correspondent review, TYRION, Insights) can adopt to know when its cached
output no longer reflects current input.

This module intentionally does not depend on issue #28 (a general event
bus) — it is durable SQLite records plus a small service API, following the
same ``config.py``/``database.py``/``service.py``/``cli.py`` shape as the
``ocr_quality`` and ``action_queue`` modules.

Issue #18 slice 2 ("apply an accepted OCR candidate to Paperless") is the
real production trigger: ``modules.ocr_quality.application_service
.OcrCandidateApplicationService.apply_candidate`` calls
``AnalysisFreshnessService.record_invalidation`` (with
``InvalidationReason.VERSION_CHANGED``) once Paperless has actually accepted
and verified the new document version, and its ``rollback`` calls it again
with ``InvalidationReason.ROLLBACK``. ``simulate_version_change`` / the CLI
``simulate-version-change`` command / the
``POST /api/analysis-invalidation/simulate-version-change`` endpoint remain
useful for exercising this mechanism in isolation (e.g. without a real
Paperless instance), but are no longer the only caller.
"""

from __future__ import annotations
