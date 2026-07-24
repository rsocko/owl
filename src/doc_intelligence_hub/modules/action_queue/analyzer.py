"""Document analyzer — uses shared LLM client via Bifrost gateway."""

from __future__ import annotations

import json
from typing import Optional

from doc_intelligence_hub.core.llm import chat_json, get_llm_settings, health_check as llm_health_check

from .config import settings

SYSTEM_PROMPT = """You are a document analysis assistant. You analyze documents and extract actionable items.
Respond with ONLY valid JSON — no markdown, no explanation."""

ANALYSIS_PROMPT = """Analyze the following document and extract ALL actionable items.

Return a JSON object with these fields:

{{
  "actions": [
    {{
      "action_type": "PAY|RESPOND|FILE|REVIEW|SHARE|SCHEDULE|SIGN|ARCHIVE",
      "title": "Short action title (e.g., 'Pay Electric Bill - $142.35')",
      "summary": "One sentence describing what needs to be done",
      "due_date": "YYYY-MM-DD or null if no deadline",
      "amount": 123.45 or null if no monetary amount,
      "urgency": "CRITICAL|HIGH|MEDIUM|LOW",
      "confidence": 0-100 (how confident you are in this specific action)
    }}
  ],
  "document_assessment": {{
    "primary_action_index": 0,
    "correspondent": "Who sent this document (company/person name)",
    "overall_confidence": 0-100,
    "requires_action": true,
    "reasoning": "Brief explanation of your assessment",
    "text_quality": "good|fair|poor|unreadable",
    "extracted_data": {{
      "account_number": "if found, else null",
      "payment_url": "if found, else null",
      "phone": "if found, else null",
      "reference_number": "if found, else null"
    }}
  }}
}}

IMPORTANT:
- Most documents will have exactly ONE action. Only include multiple if the document genuinely requires separate actions.
- If the document requires NO action (informational receipt, old statement, etc.), set "requires_action": false and return an empty actions array.
- If the text is unreadable or too garbled, set text_quality to "unreadable" and requires_action to false.

Rules for urgency:
- CRITICAL: Legal threats, final notices, collection actions, expired deadlines
- HIGH: Due within 7 days, or overdue
- MEDIUM: Due within 30 days
- LOW: No deadline, or due in more than 30 days

Rules for action_type:
- PAY: Bills, invoices, payment requests
- RESPOND: Forms, surveys, letters requiring a reply
- FILE: Statements, receipts, informational documents (no action needed)
- REVIEW: Contracts, policies, notices requiring careful reading before deciding
- SHARE: Tax forms, documents to forward to accountant/lawyer/family
- SCHEDULE: Appointments, renewals, events to put on calendar
- SIGN: Documents requiring signature
- ARCHIVE: Already processed, ready for long-term storage

Document metadata:
- Title: {title}
- Correspondent: {correspondent}
- Tags: {tags}
- Created: {created}

Document content:
---
{content}
---"""


class OllamaAnalyzer:
    """Analyzes documents using LLM via Bifrost gateway.

    Named OllamaAnalyzer for backwards compatibility, but now routes through
    the shared LLM client (Bifrost → any provider).
    """

    def __init__(self):
        llm_settings = get_llm_settings()
        # Allow action-queue config to override model if set
        self.model = settings.llm_model or llm_settings.model
        self.base_url = llm_settings.base_url

    async def analyze_document(self, document: dict) -> Optional[dict]:
        """Send document text to LLM and parse the structured response.

        Args:
            document: Paperless document dict with 'content', 'title', etc.

        Returns:
            Parsed extraction dict, or None if analysis failed.
        """
        prompt = ANALYSIS_PROMPT.format(
            title=document.get("title", "Unknown"),
            correspondent=document.get("correspondent_name", document.get("correspondent", "Unknown")),
            tags=", ".join(str(t) for t in document.get("tag_names", document.get("tags", []))),
            created=document.get("created", "Unknown"),
            content=self._truncate_content(document.get("content", "")),
        )

        data = await chat_json(
            prompt,
            system=SYSTEM_PROMPT,
            model=self.model,
            max_tokens=1024,
        )
        if data is None:
            return None
        return self._validate_response(data)

    def _validate_response(self, data: dict) -> Optional[dict]:
        """Validate and normalize the LLM response."""
        # Handle new multi-action format
        if "actions" in data and "document_assessment" in data:
            return self._validate_multi_action(data)

        # Handle legacy single-action format (backwards compat)
        if "action_type" in data:
            return self._convert_legacy_to_multi(data)

        return None

    def _validate_multi_action(self, data: dict) -> Optional[dict]:
        """Validate the multi-action response format."""
        assessment = data.get("document_assessment", {})

        valid_types = {"PAY", "RESPOND", "FILE", "REVIEW", "SHARE", "SCHEDULE", "SIGN", "ARCHIVE"}
        valid_urgency = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}

        validated_actions = []
        for action in data.get("actions", []):
            if not action.get("action_type") or not action.get("title"):
                continue
            action["action_type"] = action["action_type"].upper()
            if action["action_type"] not in valid_types:
                action["action_type"] = "REVIEW"
            action["urgency"] = action.get("urgency", "MEDIUM").upper()
            if action["urgency"] not in valid_urgency:
                action["urgency"] = "MEDIUM"
            validated_actions.append(action)

        data["actions"] = validated_actions

        valid_quality = {"good", "fair", "poor", "unreadable"}
        if assessment.get("text_quality", "").lower() not in valid_quality:
            assessment["text_quality"] = "fair"
        else:
            assessment["text_quality"] = assessment["text_quality"].lower()

        data["document_assessment"] = assessment
        return data

    def _convert_legacy_to_multi(self, data: dict) -> dict:
        """Convert old single-action format to multi-action format."""
        valid_types = {"PAY", "RESPOND", "FILE", "REVIEW", "SHARE", "SCHEDULE", "SIGN", "ARCHIVE"}
        valid_urgency = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}

        action_type = data.get("action_type", "REVIEW").upper()
        if action_type not in valid_types:
            action_type = "REVIEW"

        urgency = data.get("urgency", "MEDIUM").upper()
        if urgency not in valid_urgency:
            urgency = "MEDIUM"

        return {
            "actions": [{
                "action_type": action_type,
                "title": data.get("title", "Unknown action"),
                "summary": data.get("summary"),
                "due_date": data.get("due_date"),
                "amount": data.get("amount"),
                "urgency": urgency,
                "confidence": data.get("confidence", 50),
            }],
            "document_assessment": {
                "primary_action_index": 0,
                "correspondent": data.get("correspondent"),
                "overall_confidence": data.get("confidence", 50),
                "requires_action": True,
                "reasoning": data.get("reasoning"),
                "text_quality": "fair",
                "extracted_data": data.get("extracted_data"),
            },
        }

    def _truncate_content(self, content: str, max_chars: int = 4000) -> str:
        """Truncate document content to fit within model context."""
        if len(content) <= max_chars:
            return content
        half = max_chars // 2
        return content[:half] + "\n\n[...content truncated...]\n\n" + content[-half:]

    async def health_check(self) -> bool:
        """Verify LLM gateway is running and responsive."""
        result = await llm_health_check()
        return result.get("status") == "ok"

