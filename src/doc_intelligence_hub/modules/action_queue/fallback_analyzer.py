"""Rule-based fallback analyzer for when Ollama is unavailable.

Creates basic action items from document metadata (title, correspondent, tags)
using keyword matching and heuristics. Less accurate than LLM analysis but
ensures the pipeline still produces useful results without AI.
"""

import re
from datetime import date, timedelta

from .analyzer import (
    _normalize_cta,
    normalize_extracted_data,
    receipt_no_action_result,
)

# Keyword patterns for action type detection
_PAY_KEYWORDS = re.compile(
    r"\b(invoice|bill|statement|payment\s*due|balance\s*due|amount\s*due|"
    r"pay\s*by|remit|overdue|past\s*due|total\s*due|account\s*balance|"
    r"minimum\s*payment|autopay|payment\s*plan|installment|"
    r"copay|co[\s-]?pay|deductible|premium\s*due|"
    r"utility|electric|gas|water|sewer|trash|internet|cable|"
    r"mortgage|rent|loan\s*payment|tuition|assessment)\b",
    re.IGNORECASE,
)
_RESPOND_KEYWORDS = re.compile(
    r"\b(respond|reply|action\s*required|rsvp|confirm|"
    r"please\s*call|contact\s*us|follow[\s-]?up|signature\s*required|"
    r"verification\s*needed|verify\s*your|update\s*your\s*information|"
    r"authorization\s*required|"
    r"jury\s*duty|summons|subpoena|citation)\b",
    re.IGNORECASE,
)
_SCHEDULE_KEYWORDS = re.compile(
    r"\b(appointment|renewal|schedule|expires?|expir(ation|ing)|"
    r"renew\s*by|deadline|due\s*date|"
    r"annual\s*review|open\s*enrollment|re[\s-]?enroll|"
    r"registration|license\s*renewal|inspection\s*due|"
    r"maintenance|service\s*due|recall|warranty)\b",
    re.IGNORECASE,
)
_REVIEW_KEYWORDS = re.compile(
    r"\b(notice|policy|contract|agreement|terms|"
    r"important\s*information|review|update|change|"
    r"explanation\s*of\s*benefits|eob|claim\s*summary|"
    r"coverage\s*change|benefit\s*change|rate\s*change|"
    r"privacy\s*notice|hipaa|disclosure|amendment|"
    r"tax\s*assessment|property\s*assessment|zoning)\b",
    re.IGNORECASE,
)
_FILE_KEYWORDS = re.compile(
    r"\b(receipt|confirmation|summary|record|tax\s*form|"
    r"w[\-\s]?2|1099|w[\-\s]?4|1098|k[\-\s]?1|"
    r"statement\s*of|proof\s*of|certificate|"
    r"annual\s*statement|year[\s-]?end|quarterly\s*report|"
    r"closing\s*disclosure|settlement\s*statement|title\s*report|"
    r"vaccination|immunization|lab\s*results?|diagnosis|"
    r"prescription|referral|discharge\s*summary)\b",
    re.IGNORECASE,
)
_SIGN_KEYWORDS = re.compile(
    r"\b(sign\s*here|signature\s*required|notarize|witness|"
    r"consent\s*form|authorization\s*form|power\s*of\s*attorney|"
    r"affidavit|declaration|acknowledgment)\b",
    re.IGNORECASE,
)
_ARCHIVE_KEYWORDS = re.compile(
    r"\b(for\s*your\s*records|keep\s*for|retain|archive|"
    r"no\s*action\s*needed|informational\s*only|fyi)\b",
    re.IGNORECASE,
)
_TASK_KEYWORDS = re.compile(
    r"\b(create\s*(an?\s*)?account|register|enroll|activate|set\s*up|"
    r"log\s*in|go\s*online|download|install|update\s*your|"
    r"transfer|submit|complete\s*form)\b",
    re.IGNORECASE,
)
_CANCEL_KEYWORDS = re.compile(
    r"\b(cancel|cancellation|opt[\s-]?out|unsubscribe|terminate|"
    r"discontinue|close\s*(my\s*)?account|end\s*service|"
    r"stop\s*auto[\s-]?pay|withdrawal|do\s*not\s*renew)\b",
    re.IGNORECASE,
)
_RENEW_KEYWORDS = re.compile(
    r"\b(renew(al)?|subscription\s*(due|expir)|membership\s*(due|expir)|"
    r"auto[\s-]?renew|annual\s*fee\s*due|license\s*renewal|"
    r"re[\s-]?enroll|re[\s-]?register|reinstate|continuation)\b",
    re.IGNORECASE,
)
_DISPUTE_KEYWORDS = re.compile(
    r"\b(dispute|overcharge|error|discrepancy|incorrect|"
    r"billing\s*error|wrong\s*amount|charge\s*back|"
    r"appeal|grievance|challenge|protest|contest|"
    r"not\s*authorized|fraud(ulent)?|unauthorized\s*charge)\b",
    re.IGNORECASE,
)

# Amount extraction
_AMOUNT_PATTERN = re.compile(r"\$\s*([\d,]+\.?\d{0,2})")

# Date extraction (simple patterns)
_DATE_PATTERN = re.compile(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](20\d{2})\b")
_URL_PATTERN = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>{}\[\]\"']+")
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


class RuleBasedAnalyzer:
    """Fallback analyzer using keyword matching and heuristics."""

    def analyze_document(self, document: dict) -> dict | None:
        """Analyze a document using rules only (no LLM needed).

        Returns the same format as OllamaAnalyzer for compatibility.
        """
        receipt_result = receipt_no_action_result(document)
        if receipt_result:
            return receipt_result

        title = document.get("title", "")
        content = document.get("content", "")
        correspondent = str(document.get("correspondent_name", document.get("correspondent", "")))
        tags = document.get("tag_names", document.get("tags", []))

        # Combine title and first 2000 chars of content for analysis
        text = f"{title}\n{correspondent}\n{content[:2000]}"

        # Detect action type
        action_type, confidence = self._detect_action_type(text, tags)

        # Skip if we can't determine anything useful
        if action_type == "FILE" and confidence < 30:
            return {
                "actions": [],
                "document_assessment": {
                    "primary_action_index": 0,
                    "correspondent": correspondent or None,
                    "overall_confidence": confidence,
                    "requires_action": False,
                    "reasoning": "Rule-based: no clear actionable keywords found",
                    "text_quality": "fair",
                    "extracted_data": {},
                },
            }

        # Extract amount if it looks like a bill
        amount = self._extract_amount(text) if action_type == "PAY" else None

        # Extract due date
        due_date = self._extract_due_date(text)

        # Determine urgency
        urgency = self._determine_urgency(text, due_date)

        # Build action title
        action_title = self._build_title(action_type, title, correspondent, amount)
        urls = self._extract_urls(text)
        extracted_data = normalize_extracted_data(
            {
                "account_number": None,
                "payment_url": urls[0] if action_type == "PAY" and urls else None,
                "phone": None,
                "email": self._extract_email(text),
                "reference_number": None,
                "links": [
                    {
                        "url": url,
                        "label": "Pay online"
                        if action_type == "PAY" and index == 0
                        else "Open document link",
                        "purpose": "payment" if action_type == "PAY" and index == 0 else "other",
                    }
                    for index, url in enumerate(urls)
                ],
            }
        )
        action_payload = {
            "action_type": action_type,
            "title": action_title,
            "summary": f"Auto-detected from document metadata (rule-based, no AI). Correspondent: {correspondent or 'unknown'}",
            "due_date": due_date.isoformat() if due_date else None,
            "amount": amount,
            "urgency": urgency,
            "confidence": confidence,
        }
        action_payload["recommended_cta"] = _normalize_cta(
            None, action_payload, {"extracted_data": extracted_data}
        )

        return {
            "actions": [action_payload],
            "document_assessment": {
                "primary_action_index": 0,
                "correspondent": correspondent or None,
                "overall_confidence": confidence,
                "requires_action": True,
                "reasoning": f"Rule-based fallback (Ollama unavailable). Detected {action_type} from keywords.",
                "text_quality": "fair",
                "extracted_data": extracted_data,
            },
        }

    def _extract_urls(self, text: str) -> list[str]:
        """Extract up to eight distinct web URLs from OCR text."""
        urls: list[str] = []
        for match in _URL_PATTERN.findall(text):
            url = match.rstrip(".,;:!?)\"]}'")
            if url not in urls:
                urls.append(url)
            if len(urls) == 8:
                break
        return urls

    def _extract_email(self, text: str) -> str | None:
        """Extract the first contact email address."""
        match = _EMAIL_PATTERN.search(text)
        return match.group(0) if match else None

    def _detect_action_type(self, text: str, tags: list) -> tuple[str, int]:
        """Return (action_type, confidence) based on keyword matching."""
        scores = {
            "PAY": len(_PAY_KEYWORDS.findall(text)) * 20,
            "RESPOND": len(_RESPOND_KEYWORDS.findall(text)) * 18,
            "SCHEDULE": len(_SCHEDULE_KEYWORDS.findall(text)) * 15,
            "REVIEW": len(_REVIEW_KEYWORDS.findall(text)) * 12,
            "FILE": len(_FILE_KEYWORDS.findall(text)) * 10,
            "SIGN": len(_SIGN_KEYWORDS.findall(text)) * 18,
            "ARCHIVE": len(_ARCHIVE_KEYWORDS.findall(text)) * 8,
            "TASK": len(_TASK_KEYWORDS.findall(text)) * 15,
            "CANCEL": len(_CANCEL_KEYWORDS.findall(text)) * 18,
            "RENEW": len(_RENEW_KEYWORDS.findall(text)) * 17,
            "DISPUTE": len(_DISPUTE_KEYWORDS.findall(text)) * 19,
        }

        # Boost from tags
        tag_str = " ".join(str(t) for t in tags).lower()
        if "bill" in tag_str or "invoice" in tag_str:
            scores["PAY"] += 30
        if "inbox" in tag_str or "todo" in tag_str:
            scores["REVIEW"] += 15
        if "medical" in tag_str or "health" in tag_str or "eob" in tag_str:
            scores["REVIEW"] += 20
        if "tax" in tag_str or "financial" in tag_str:
            scores["FILE"] += 20
        if "legal" in tag_str or "contract" in tag_str:
            scores["SIGN"] += 15
        if "insurance" in tag_str:
            scores["REVIEW"] += 15
            scores["PAY"] += 10

        # Find best match
        best_type = max(scores, key=scores.get)
        best_score = scores[best_type]

        # Confidence is capped at 70 for rule-based (always less certain than AI)
        confidence = min(70, max(15, best_score))

        if best_score == 0:
            return "FILE", 10

        return best_type, confidence

    def _extract_amount(self, text: str) -> float | None:
        """Extract the largest dollar amount from text."""
        matches = _AMOUNT_PATTERN.findall(text)
        if not matches:
            return None
        amounts = []
        for m in matches:
            try:
                amounts.append(float(m.replace(",", "")))
            except ValueError:
                continue
        # Return the largest amount (likely the total due)
        return max(amounts) if amounts else None

    def _extract_due_date(self, text: str) -> date | None:
        """Extract the first plausible future date from text."""
        matches = _DATE_PATTERN.findall(text)
        today = date.today()
        for month, day, year in matches:
            try:
                d = date(int(year), int(month), int(day))
                # Only consider dates within reasonable range
                if today - timedelta(days=90) <= d <= today + timedelta(days=365):
                    return d
            except ValueError:
                continue
        return None

    def _determine_urgency(self, text: str, due_date: date | None) -> str:
        """Determine urgency from keywords and due date."""
        text_lower = text.lower()
        if any(
            w in text_lower for w in ["final notice", "collection", "legal", "immediate", "overdue"]
        ):
            return "CRITICAL"
        if due_date:
            days_until = (due_date - date.today()).days
            if days_until < 0:
                return "CRITICAL"
            if days_until <= 7:
                return "HIGH"
            if days_until <= 30:
                return "MEDIUM"
            return "LOW"
        if any(w in text_lower for w in ["urgent", "asap", "immediately"]):
            return "HIGH"
        return "MEDIUM"

    def _build_title(
        self, action_type: str, title: str, correspondent: str, amount: float | None
    ) -> str:
        """Build a concise action title."""
        correspondent = str(correspondent) if correspondent else ""
        # If correspondent is just a numeric ID, prefer the title instead
        is_name = correspondent and not correspondent.isdigit()
        parts = []
        if action_type == "PAY":
            parts.append("Pay")
            if is_name:
                parts.append(correspondent)
            elif title:
                parts.append(title[:40])
            if amount:
                parts.append(f"— ${amount:,.2f}")
        elif action_type == "RESPOND":
            parts.append("Respond to")
            parts.append(correspondent if is_name else title[:40])
        elif action_type == "SCHEDULE":
            parts.append("Schedule/Renew:")
            parts.append(title[:50] if title else correspondent or "Unknown")
        else:
            parts.append(f"{action_type}:")
            parts.append(title[:50] if title else correspondent or "Unknown document")

        return " ".join(parts)
