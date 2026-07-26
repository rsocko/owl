"""Account number extraction pipeline.

Extracts account numbers/identifiers from document OCR text using regex patterns
and optionally LLM fallback. Writes results to Paperless custom field `di_account_id`.

Patterns target common formats found in financial/medical statements:
  - Account #...4321, Account Number: 4321
  - ending in 4321, last 4: 4321
  - Card #****4321, ****4321
  - Member ID: ABC1234567
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Common account number patterns — each captures the meaningful identifier portion.
# Ordered from most specific to least specific to reduce false positives.
ACCOUNT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("account_last4", re.compile(
        r"(?:account|acct)[\s.]*(?:#|number|no\.?|num\.?)[\s.:]*[*Xx.]+(\d{4})",
        re.IGNORECASE,
    )),
    ("account_number_full", re.compile(
        r"(?:account|acct)[\s.]*(?:#|number|no\.?|num\.?)[\s.:]*([A-Z0-9][\w-]{2,18}[A-Z0-9])",
        re.IGNORECASE,
    )),
    ("ending_in", re.compile(
        r"(?:ending|last\s*4)\s+(?:in\s+)?(\d{4})",
        re.IGNORECASE,
    )),
    ("card_number", re.compile(
        r"card\s+(?:#|number)[\s.:]*[*Xx.]+(\d{4})",
        re.IGNORECASE,
    )),
    ("masked_number", re.compile(
        r"[*Xx]{4,}\s*(\d{4})",
    )),
    ("member_id", re.compile(
        r"member\s*(?:id|#|number)[\s.:]*([A-Z0-9]{6,15})",
        re.IGNORECASE,
    )),
    ("policy_number", re.compile(
        r"policy\s*(?:#|number|no\.?)[\s.:]*([A-Z0-9]{4,15})",
        re.IGNORECASE,
    )),
    ("claim_number", re.compile(
        r"claim\s*(?:#|number|no\.?)[\s.:]*([A-Z0-9-]{4,20})",
        re.IGNORECASE,
    )),
]


@dataclass
class ExtractionResult:
    """Result of account number extraction from a document."""

    document_id: int
    account_numbers: list[str] = field(default_factory=list)
    pattern_matches: list[dict[str, str]] = field(default_factory=list)
    raw_text_length: int = 0
    success: bool = False
    error: str | None = None


def extract_account_numbers(text: str) -> list[dict[str, str]]:
    """Extract account numbers from text using regex patterns.

    Returns a list of dicts with 'pattern', 'value', and 'raw_match' keys.
    Deduplicates by normalized value.
    """
    if not text or not text.strip():
        return []

    matches: list[dict[str, str]] = []
    seen_values: set[str] = set()

    for pattern_name, pattern in ACCOUNT_PATTERNS:
        for m in pattern.finditer(text):
            raw_value = m.group(1).strip()
            # Normalize: collapse whitespace, uppercase
            normalized = re.sub(r"\s+", "", raw_value).upper()
            if normalized and normalized not in seen_values:
                seen_values.add(normalized)
                matches.append({
                    "pattern": pattern_name,
                    "value": raw_value,
                    "normalized": normalized,
                    "raw_match": m.group(0).strip(),
                })

    return matches


def pick_best_account_identifier(matches: list[dict[str, str]]) -> str | None:
    """Pick the best account identifier from extracted matches.

    Prefers last-4-digit identifiers (masked format) as they're the most
    commonly used for account disambiguation in statements.
    """
    if not matches:
        return None

    # Prefer last-4 patterns
    for m in matches:
        if m["pattern"] in ("account_last4", "ending_in", "card_number", "masked_number"):
            return f"ending {m['normalized']}"

    # Fall back to first full account number
    return matches[0]["value"]


async def extract_from_document(
    document_id: int,
    paperless_client: object,
) -> ExtractionResult:
    """Fetch document text from Paperless and extract account numbers.

    Args:
        document_id: Paperless document ID
        paperless_client: PaperlessClient instance with get_document_text method
    """
    result = ExtractionResult(document_id=document_id)

    try:
        # Fetch document content/text from Paperless
        text = await _fetch_document_text(document_id, paperless_client)
        if not text:
            result.error = "No text content available for document"
            return result

        result.raw_text_length = len(text)
        result.pattern_matches = extract_account_numbers(text)
        result.account_numbers = [m["normalized"] for m in result.pattern_matches]
        result.success = True

    except Exception as exc:
        logger.error("Account extraction failed for doc %d: %s", document_id, exc)
        result.error = str(exc)

    return result


async def _fetch_document_text(document_id: int, client: object) -> str | None:
    """Fetch document text content from Paperless-ngx."""
    # The PaperlessClient has a get() method for arbitrary API paths.
    # Paperless exposes document content at /api/documents/{id}/ which includes a 'content' field.
    try:
        get_fn = getattr(client, "get", None)
        if get_fn is None:
            logger.warning("Paperless client has no get() method")
            return None

        data = await _call_sync_or_async(get_fn, f"/api/documents/{document_id}/")
        if isinstance(data, dict):
            return data.get("content", "")
        return None
    except Exception as exc:
        logger.error("Failed to fetch text for document %d: %s", document_id, exc)
        return None


async def _call_sync_or_async(fn: object, *args: object) -> object:
    """Call a function that might be sync or async."""
    import asyncio
    import inspect

    if inspect.iscoroutinefunction(fn):
        return await fn(*args)
    else:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn, *args)


async def write_account_to_paperless(
    document_id: int,
    account_identifier: str,
    paperless_client: object,
) -> bool:
    """Write extracted account identifier to Paperless custom field."""
    try:
        patch_fn = getattr(paperless_client, "patch", None)
        if patch_fn is None:
            logger.warning("Paperless client has no patch() method")
            return False

        # Write to custom field via Paperless API
        await _call_sync_or_async(
            patch_fn,
            f"/api/documents/{document_id}/",
            {"custom_fields": [{"field": "di_account_id", "value": account_identifier}]},
        )
        logger.info("Wrote account_identifier '%s' to Paperless doc %d", account_identifier, document_id)
        return True
    except Exception as exc:
        logger.error("Failed to write account to Paperless doc %d: %s", document_id, exc)
        return False
