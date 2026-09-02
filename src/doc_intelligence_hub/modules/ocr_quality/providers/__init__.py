"""Candidate-generation provider implementations (issue #18, slice 1).

One provider owns each candidate's PDF/text result end to end. Outputs from
different providers are never merged into one text layer (design doc safety
invariant #3).
"""

from __future__ import annotations
