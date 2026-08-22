"""Validation tests for analysis execution contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from doc_intelligence_hub.modules.analysis.models import ExecuteRequest


@pytest.mark.parametrize("document_id", [0, -1, True, 1.5])
def test_execute_request_rejects_invalid_document_scope(document_id):
    with pytest.raises(ValidationError):
        ExecuteRequest(document_id=document_id)


@pytest.mark.parametrize("document_id", [42, "42"])
def test_execute_request_accepts_positive_document_id(document_id):
    request = ExecuteRequest(document_id=document_id)

    assert request.document_id == 42
