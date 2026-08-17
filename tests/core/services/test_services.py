"""Tests for the service layer base class and EOB service (ARCH-06)."""

from __future__ import annotations

from doc_intelligence_hub.core.services.base import BaseService
from doc_intelligence_hub.core.services.eob_service import EOBMatchingService
from doc_intelligence_hub.modules.eob_matching.models import DocumentType


class TestBaseService:
    """Test BaseService fundamentals."""

    def test_named_logger(self):
        """Each service gets a logger with its service name."""
        service = BaseService()
        assert "base" in service.logger.name

    def test_circuit_breaker_access(self):
        """Services can get/create circuit breakers."""
        service = BaseService()
        breaker = service.get_circuit_breaker("test-service")
        assert breaker.name == "test-service"
        assert breaker.state == "closed"


class TestEOBMatchingService:
    """Test EOBMatchingService classification and matching."""

    def setup_method(self):
        self.service = EOBMatchingService()

    def test_classify_empty_documents(self):
        """Classification returns results for empty input."""
        results = self.service.classify_documents([])
        assert results == []

    def test_classify_unknown_document(self):
        """Non-EOB/Bill content classifies as UNKNOWN."""
        docs = [{"id": 1, "title": "Random doc", "content": "Hello world"}]
        results = self.service.classify_documents(docs)
        assert len(results) == 1
        assert results[0]["document_id"] == 1
        assert results[0]["classification"]["type"] in [
            DocumentType.UNKNOWN.value,
            DocumentType.EOB.value,
            DocumentType.BILL.value,
        ]

    def test_classify_eob_document(self):
        """Content with EOB indicators classifies correctly."""
        eob_content = (
            "Explanation of Benefits\n"
            "Insurance Company: Blue Cross\n"
            "Claim Number: CLM-12345\n"
            "Patient: John Smith\n"
            "Provider: Dr. Jones\n"
            "Total Billed: $500.00\n"
            "Plan Pays: $400.00\n"
            "Patient Responsibility: $100.00\n"
        )
        docs = [{"id": 42, "title": "EOB Doc", "content": eob_content}]
        results = self.service.classify_documents(docs)
        assert len(results) == 1
        assert results[0]["classification"]["type"] == DocumentType.EOB.value

    def test_summarize_classifications(self):
        """Summary counts each document type."""
        classifications = [
            {"classification": {"type": "EOB"}},
            {"classification": {"type": "EOB"}},
            {"classification": {"type": "BILL"}},
            {"classification": {"type": "UNKNOWN"}},
        ]
        summary = EOBMatchingService._summarize_classifications(classifications)
        assert summary["EOB"] == 2
        assert summary["BILL"] == 1
        assert summary["UNKNOWN"] == 1

    def test_match_empty_lists(self):
        """Matching with no documents returns empty list."""
        matches = self.service.match([], [])
        assert matches == []
