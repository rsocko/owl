"""Tests for the OllamaAnalyzer prompt building — ensures integer tag IDs don't crash."""

from unittest.mock import AsyncMock, patch

import pytest

from doc_intelligence_hub.modules.action_queue.analyzer import (
    OllamaAnalyzer,
    normalize_extracted_data,
)


class TestPromptBuildingWithIntegerTags:
    """Regression tests for the TypeError when Paperless returns integer tag IDs."""

    @pytest.fixture()
    def analyzer(self):
        with patch("doc_intelligence_hub.modules.action_queue.analyzer.get_llm_settings") as mock:
            mock.return_value.model = "test-model"
            mock.return_value.base_url = "http://localhost"
            return OllamaAnalyzer()

    @pytest.mark.asyncio
    async def test_integer_tag_ids_do_not_raise(self, analyzer):
        """Tags from Paperless API are integers — joining them must not TypeError."""
        document = {
            "title": "Electric Bill",
            "content": "Amount due: $50",
            "tags": [3, 7, 12],  # Paperless returns tag IDs as ints
        }
        with patch(
            "doc_intelligence_hub.modules.action_queue.analyzer.chat_json", new_callable=AsyncMock
        ) as mock_chat:
            mock_chat.return_value = {
                "actions": [],
                "document_assessment": {"requires_action": False},
            }
            # Should not raise TypeError: sequence item 0: expected str instance, int found
            result = await analyzer.analyze_document(document)
            assert result is not None
            # Verify the prompt was built with stringified tags
            call_args = mock_chat.call_args
            prompt_sent = call_args[0][0]
            assert "3, 7, 12" in prompt_sent

    @pytest.mark.asyncio
    async def test_string_tag_names_still_work(self, analyzer):
        """When tag_names are present (strings), they should work as before."""
        document = {
            "title": "Electric Bill",
            "content": "Amount due: $50",
            "tag_names": ["bills", "utilities"],
            "tags": [3, 7],
        }
        with patch(
            "doc_intelligence_hub.modules.action_queue.analyzer.chat_json", new_callable=AsyncMock
        ) as mock_chat:
            mock_chat.return_value = {
                "actions": [],
                "document_assessment": {"requires_action": False},
            }
            result = await analyzer.analyze_document(document)
            assert result is not None
            prompt_sent = mock_chat.call_args[0][0]
            assert "bills, utilities" in prompt_sent

    @pytest.mark.asyncio
    async def test_empty_tags_do_not_raise(self, analyzer):
        """Empty tag lists should produce an empty string, not crash."""
        document = {
            "title": "Receipt",
            "content": "Thank you for your payment",
            "tags": [],
        }
        with patch(
            "doc_intelligence_hub.modules.action_queue.analyzer.chat_json", new_callable=AsyncMock
        ) as mock_chat:
            mock_chat.return_value = {
                "actions": [],
                "document_assessment": {"requires_action": False},
            }
            result = await analyzer.analyze_document(document)
            assert result is not None

    @pytest.mark.asyncio
    async def test_mixed_types_in_tags(self, analyzer):
        """Handle edge case where tags might contain mixed types."""
        document = {
            "title": "Doc",
            "content": "Some content",
            "tags": [1, "manual-tag", 99],
        }
        with patch(
            "doc_intelligence_hub.modules.action_queue.analyzer.chat_json", new_callable=AsyncMock
        ) as mock_chat:
            mock_chat.return_value = {
                "actions": [],
                "document_assessment": {"requires_action": False},
            }
            result = await analyzer.analyze_document(document)
            assert result is not None
            prompt_sent = mock_chat.call_args[0][0]
            assert "1, manual-tag, 99" in prompt_sent

    @pytest.mark.asyncio
    async def test_receipt_document_type_skips_llm_and_pay_action(self, analyzer):
        document = {
            "title": "Utility payment",
            "content": "Utility bill payment $142.50. Thank you for your payment.",
            "document_type_name": "Receipt",
            "tag_names": ["Inbox"],
        }
        with patch(
            "doc_intelligence_hub.modules.action_queue.analyzer.chat_json", new_callable=AsyncMock
        ) as mock_chat:
            result = await analyzer.analyze_document(document)

        assert result["actions"] == []
        assert result["document_assessment"]["requires_action"] is False
        mock_chat.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_document_type_is_included_in_prompt(self, analyzer):
        document = {
            "title": "Policy notice",
            "content": "Please review this policy notice.",
            "document_type_name": "Correspondence",
            "tag_names": ["Inbox"],
        }
        with patch(
            "doc_intelligence_hub.modules.action_queue.analyzer.chat_json", new_callable=AsyncMock
        ) as mock_chat:
            mock_chat.return_value = {
                "actions": [],
                "document_assessment": {"requires_action": False},
            }
            await analyzer.analyze_document(document)

        assert "- Document type: Correspondence" in mock_chat.call_args[0][0]


def test_normalize_extracted_data_keeps_safe_links_and_rejects_unsafe_urls():
    extracted = normalize_extracted_data(
        {
            "payment_url": "https://billing.example/pay",
            "links": [
                {"url": "https://billing.example/pay", "label": "Duplicate", "purpose": "payment"},
                {"url": "www.example.com/form", "label": "Complete form", "purpose": "form"},
                {"url": "javascript:alert(1)", "label": "Unsafe", "purpose": "other"},
            ],
        }
    )

    assert extracted["links"] == [
        {"url": "https://billing.example/pay", "label": "Pay online", "purpose": "payment"},
        {"url": "https://www.example.com/form", "label": "Complete form", "purpose": "form"},
    ]
