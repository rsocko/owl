"""Ollama-based document analyzer."""

import json
from typing import Optional

import httpx

from .config import settings

ANALYSIS_PROMPT = """You are a document analysis assistant. Analyze the following document text and extract ALL actionable items.

Respond with ONLY a JSON object (no markdown, no explanation) with these fields:

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
- Most documents will have exactly ONE action. Only include multiple if the document genuinely requires separate actions (e.g., "pay bill AND return signed form").
- If the document requires NO action (informational receipt, old statement, etc.), set "requires_action": false and return an empty actions array.
- If the text is unreadable or too garbled to understand, set text_quality to "unreadable" and requires_action to false.

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
    """Analyzes documents using Ollama (local LLM)."""

    def __init__(self):
        self.base_url = settings.ollama_url.rstrip("/")
        self.model = settings.ollama_model

    async def analyze_document(self, document: dict) -> Optional[dict]:
        """Send document text to Ollama and parse the structured response.

        Args:
            document: Paperless document dict with 'content', 'title', etc.

        Returns:
            Parsed extraction dict, or None if analysis failed.
        """
        prompt = ANALYSIS_PROMPT.format(
            title=document.get("title", "Unknown"),
            correspondent=document.get("correspondent_name", document.get("correspondent", "Unknown")),
            tags=", ".join(document.get("tag_names", document.get("tags", []))),
            created=document.get("created", "Unknown"),
            content=self._truncate_content(document.get("content", "")),
        )

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.1,  # Low temp for structured output
                            "num_predict": 1024,
                        },
                    },
                )
                resp.raise_for_status()
                result = resp.json()
                response_text = result.get("response", "")
                return self._parse_response(response_text)

        except httpx.HTTPError as e:
            print(f"Ollama HTTP error: {e}")
            return None
        except Exception as e:
            print(f"Ollama analysis failed: {e}")
            return None

    def _parse_response(self, text: str) -> Optional[dict]:
        """Parse JSON from Ollama response, handling common issues."""
        # Strip markdown code fences if present
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON object in the response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end])
                except json.JSONDecodeError:
                    return None
            else:
                return None

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

        # Validate each action
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

        # Validate text_quality
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
        # Keep beginning and end (most important info is usually there)
        half = max_chars // 2
        return content[:half] + "\n\n[...content truncated...]\n\n" + content[-half:]

    async def health_check(self) -> bool:
        """Verify Ollama is running and model is available."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                if resp.status_code != 200:
                    return False
                models = resp.json().get("models", [])
                return any(m.get("name", "").startswith(self.model.split(":")[0]) for m in models)
        except Exception:
            return False
