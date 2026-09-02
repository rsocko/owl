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

There is no real production trigger yet: issue #18's "apply an accepted OCR
candidate" step, which will call ``AnalysisFreshnessService.record_invalidation``
(or the CLI/API's manual-invalidation entry points) whenever a Paperless
document's accepted version actually changes, does not exist yet. Until then,
``simulate_version_change`` / the CLI ``simulate-version-change`` command /
the ``POST /api/analysis-invalidation/simulate-version-change`` endpoint are
the supported way to exercise and validate this mechanism.
"""

from __future__ import annotations
