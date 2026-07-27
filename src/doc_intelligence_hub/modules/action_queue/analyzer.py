"""Document analyzer — uses shared LLM client via Bifrost gateway."""

from __future__ import annotations

import asyncio
import logging
import time

from doc_intelligence_hub.core.llm import (
    chat_json,
    get_llm_settings,
)
from doc_intelligence_hub.core.llm import (
    health_check as llm_health_check,
)

from .config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a document analysis assistant. You analyze documents and extract actionable items.
Respond with ONLY valid JSON — no markdown, no explanation."""

ANALYSIS_PROMPT = """Analyze the following document and extract ALL actionable items.

Return a JSON object with these fields:

{{
  "actions": [
    {{
      "action_type": "PAY|RESPOND|FILE|REVIEW|SHARE|SCHEDULE|SIGN|ARCHIVE|CANCEL|RENEW|DISPUTE|TASK",
      "title": "Short action title (e.g., 'Pay Electric Bill - $142.35')",
      "summary": "One sentence describing what needs to be done",
      "due_date": "YYYY-MM-DD or null if no deadline",
      "amount": 123.45 or null if no monetary amount,
      "urgency": "CRITICAL|HIGH|MEDIUM|LOW",
      "confidence": 0-100 (how confident you are in this specific action),
      "recommended_cta": {{
        "id": "pay-online|open-document|call-provider|email-provider|schedule-event|sign-document|share-document|archive|review-document|create-task",
        "label": "Human-readable button label (e.g., 'Pay Online', 'Call Billing')",
        "url": "https://... or null if no URL found in document",
        "phone": "phone number or null",
        "metadata": {{}}
      }}
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
- RESPOND: Forms, surveys, letters requiring a reply or communication back
- FILE: Statements, receipts, informational documents (no action needed)
- REVIEW: Contracts, policies, notices requiring careful reading before deciding
- SHARE: Tax forms, documents to forward to accountant/lawyer/family
- SCHEDULE: Appointments, renewals, events to put on calendar
- SIGN: Documents requiring signature
- ARCHIVE: Already processed, ready for long-term storage
- CANCEL: Cancellation notices, opt-outs, unsubscribes, service terminations requiring confirmation
- RENEW: Subscriptions, memberships, licenses, or registrations coming due for renewal
- DISPUTE: Documents indicating an error, overcharge, or discrepancy requiring correction or challenge
- TASK: General to-do items that don't fit the above (create account, register, update info, etc.)

Rules for recommended_cta:
- Extract the MOST useful next action the user can take, including a URL or phone number from the document when available.
- CTA id should be one of: pay-online, open-document, call-provider, email-provider, schedule-event, sign-document, share-document, archive, review-document, create-task
- If the document contains a payment URL, use "pay-online" with the URL.
- If the document has a phone number for billing/support, use "call-provider" with the phone.
- For documents with no specific deep-link, use the most natural action (e.g., "review-document" for REVIEW type).

Document metadata:
- Title: {title}
- Correspondent: {correspondent}
- Tags: {tags}
- Created: {created}

Document content:
---
{content}
---"""


# ------------------------------------------------------------------
# CTA (Call-to-Action) normalization — extensible registry pattern
# ------------------------------------------------------------------

VALID_CTA_IDS = {
    "pay-online", "open-document", "call-provider", "email-provider",
    "schedule-event", "sign-document", "share-document", "archive",
    "review-document", "create-task",
}

# Default CTA mapping by action type (fallback when AI doesn't provide one)
_DEFAULT_CTA_BY_ACTION_TYPE: dict[str, dict] = {
    "PAY": {"id": "pay-online", "label": "Pay Online"},
    "RESPOND": {"id": "email-provider", "label": "Draft Response"},
    "FILE": {"id": "archive", "label": "File Document"},
    "REVIEW": {"id": "review-document", "label": "Open & Review"},
    "SHARE": {"id": "share-document", "label": "Share Document"},
    "SCHEDULE": {"id": "schedule-event", "label": "Add to Calendar"},
    "SIGN": {"id": "sign-document", "label": "Sign Document"},
    "ARCHIVE": {"id": "archive", "label": "Archive"},
    "TASK": {"id": "create-task", "label": "Create Task"},
}


def _normalize_cta(raw_cta: dict | str | None, action: dict, assessment: dict) -> dict:
    """Normalize a CTA from LLM response into a consistent structure.

    Falls back to a sensible default based on action_type if the AI didn't
    provide a valid CTA. Enriches with URLs/phone from extracted_data when available.
    """
    action_type = action.get("action_type", "REVIEW")
    extracted = assessment.get("extracted_data", {}) or {}

    # Start with a default CTA for this action type
    default = _DEFAULT_CTA_BY_ACTION_TYPE.get(action_type, {"id": "review-document", "label": "Review"})
    result = {**default, "url": None, "phone": None, "metadata": {}}

    # If the AI returned a structured CTA, merge it
    if isinstance(raw_cta, dict):
        cta_id = raw_cta.get("id", "").lower().strip()
        if cta_id in VALID_CTA_IDS:
            result["id"] = cta_id
        if raw_cta.get("label"):
            result["label"] = raw_cta["label"]
        if raw_cta.get("url"):
            result["url"] = raw_cta["url"]
        if raw_cta.get("phone"):
            result["phone"] = raw_cta["phone"]
        if isinstance(raw_cta.get("metadata"), dict):
            result["metadata"] = raw_cta["metadata"]
    elif isinstance(raw_cta, str) and raw_cta.lower().strip() in VALID_CTA_IDS:
        result["id"] = raw_cta.lower().strip()

    # Enrich from extracted_data if CTA doesn't already have a URL/phone
    if not result["url"] and extracted.get("payment_url"):
        result["url"] = extracted["payment_url"]
    if not result["phone"] and extracted.get("phone"):
        result["phone"] = extracted["phone"]

    return result


def urgency_to_severity(urgency: str | None) -> str:
    """Map 4-tier urgency to 3-tier severity for consistent display."""
    mapping = {"CRITICAL": "critical", "HIGH": "focus", "MEDIUM": "focus", "LOW": "safe"}
    return mapping.get((urgency or "LOW").upper(), "safe")


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

    async def analyze_document(self, document: dict) -> dict | None:
        """Send document text to LLM and parse the structured response.

        Applies a configurable timeout (``settings.llm_timeout_seconds``). If the
        first attempt times out or errors, retries once with a shorter timeout
        before giving up and letting the caller fall back to the rule-based
        analyzer.

        Args:
            document: Paperless document dict with 'content', 'title', etc.

        Returns:
            Parsed extraction dict, or None if analysis failed.
        """
        doc_id = document.get("id")
        prompt = ANALYSIS_PROMPT.format(
            title=document.get("title", "Unknown"),
            correspondent=document.get(
                "correspondent_name", document.get("correspondent", "Unknown")
            ),
            tags=", ".join(str(t) for t in document.get("tag_names", document.get("tags", []))),
            created=document.get("created", "Unknown"),
            content=self._truncate_content(document.get("content", "")),
        )

        est_prompt_tokens = (len(prompt) + len(SYSTEM_PROMPT)) // 4
        timeout = settings.llm_timeout_seconds
        logger.info(
            "LLM call starting: doc_id=%s model=%s est_prompt_tokens=%d timeout=%.0fs",
            doc_id,
            self.model,
            est_prompt_tokens,
            timeout,
        )

        data = await self._call_with_timeout(prompt, doc_id, timeout)
        if data is None:
            retry_timeout = max(1.0, timeout / 2)
            logger.warning(
                "LLM call failed/timed out for doc_id=%s — retrying once with %.0fs timeout",
                doc_id,
                retry_timeout,
            )
            data = await self._call_with_timeout(prompt, doc_id, retry_timeout)

        if data is None:
            logger.error(
                "LLM call failed after retry for doc_id=%s — caller should fall back to rule-based analyzer",
                doc_id,
            )
            return None

        return self._validate_response(data)

    async def _call_with_timeout(self, prompt: str, doc_id, timeout: float) -> dict | None:
        """Run a single LLM call bounded by ``timeout`` seconds, logging duration/outcome."""
        start = time.monotonic()
        try:
            data = await asyncio.wait_for(
                chat_json(prompt, system=SYSTEM_PROMPT, model=self.model, max_tokens=1024),
                timeout=timeout,
            )
            elapsed = time.monotonic() - start
            if data is None:
                logger.warning(
                    "LLM call for doc_id=%s returned no parsable data after %.2fs", doc_id, elapsed
                )
            else:
                logger.info("LLM call for doc_id=%s succeeded in %.2fs", doc_id, elapsed)
            return data
        except TimeoutError:
            elapsed = time.monotonic() - start
            logger.warning(
                "LLM call for doc_id=%s timed out after %.2fs (limit %.0fs)",
                doc_id,
                elapsed,
                timeout,
            )
            return None
        except Exception:
            elapsed = time.monotonic() - start
            logger.exception(
                "LLM call for doc_id=%s raised an exception after %.2fs",
                doc_id,
                elapsed,
            )
            return None

    def _validate_response(self, data: dict) -> dict | None:
        """Validate and normalize the LLM response."""
        # Handle new multi-action format
        if "actions" in data and "document_assessment" in data:
            return self._validate_multi_action(data)

        # Handle legacy single-action format (backwards compat)
        if "action_type" in data:
            return self._convert_legacy_to_multi(data)

        return None

    def _validate_multi_action(self, data: dict) -> dict | None:
        """Validate the multi-action response format."""
        assessment = data.get("document_assessment", {})

        valid_types = {"PAY", "RESPOND", "FILE", "REVIEW", "SHARE", "SCHEDULE", "SIGN", "ARCHIVE", "CANCEL", "RENEW", "DISPUTE", "TASK"}
        valid_urgency = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}

        validated_actions = []
        for action in data.get("actions", []):
            if not action.get("action_type") or not action.get("title"):
                continue
            action["action_type"] = action["action_type"].upper()
            if action["action_type"] not in valid_types:
                action["action_type"] = "TASK"
            action["urgency"] = action.get("urgency", "MEDIUM").upper()
            if action["urgency"] not in valid_urgency:
                action["urgency"] = "MEDIUM"
            # Normalize CTA if present
            action["recommended_cta"] = _normalize_cta(action.get("recommended_cta"), action, assessment)
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
        valid_types = {"PAY", "RESPOND", "FILE", "REVIEW", "SHARE", "SCHEDULE", "SIGN", "ARCHIVE", "CANCEL", "RENEW", "DISPUTE", "TASK"}
        valid_urgency = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}

        action_type = data.get("action_type", "TASK").upper()
        if action_type not in valid_types:
            action_type = "TASK"

        urgency = data.get("urgency", "MEDIUM").upper()
        if urgency not in valid_urgency:
            urgency = "MEDIUM"

        return {
            "actions": [
                {
                    "action_type": action_type,
                    "title": data.get("title", "Unknown action"),
                    "summary": data.get("summary"),
                    "due_date": data.get("due_date"),
                    "amount": data.get("amount"),
                    "urgency": urgency,
                    "confidence": data.get("confidence", 50),
                }
            ],
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
