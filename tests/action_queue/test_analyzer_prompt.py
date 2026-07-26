"""Tests for the OllamaAnalyzer prompt building — ensures integer tag IDs don't crash."""

from unittest.mock import AsyncMock, patch

import pytest

from doc_intelligence_hub.modules.action_queue.analyzer import OllamaAnalyzer


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
