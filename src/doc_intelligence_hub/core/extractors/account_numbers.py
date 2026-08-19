"""Account number extraction pipeline.

Extracts clearly labeled account identifiers from document OCR text and applies
the metadata registry's storage policy before Paperless projection.

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

from doc_intelligence_hub.core.paperless import (
    AccountIdentifierClass,
    AccountIdentifierProjection,
    MetadataFieldKey,
    PaperlessMetadataResolver,
    build_account_identifier_update,
    build_metadata_update,
    govern_account_identifier,
    mask_account_identifier,
)

logger = logging.getLogger(__name__)

# Common account number patterns — each captures the meaningful identifier portion.
# Ordered from most specific to least specific to reduce false positives.
ACCOUNT_PATTERNS: list[tuple[str, AccountIdentifierClass, float, re.Pattern[str]]] = [
    (
        "bank_account",
        AccountIdentifierClass.BANK_ACCOUNT,
        0.99,
        re.compile(
            r"(?:bank|checking|savings)\s+account\s*(?:#|number|no\.?)[\s.:]*([A-Z0-9][\w-]{3,24})",
            re.IGNORECASE,
        ),
    ),
    (
        "payment_card",
        AccountIdentifierClass.PAYMENT_CARD,
        0.99,
        re.compile(
            r"(?:payment\s+)?card\s*(?:#|number|no\.?)[\s.:]*([*Xx.\s-]*\d{4,19})",
            re.IGNORECASE,
        ),
    ),
    (
        "payment_card_ending",
        AccountIdentifierClass.PAYMENT_CARD,
        0.99,
        re.compile(r"(?:payment\s+)?card\s+ending\s+(?:in\s+)?(\d{4})", re.IGNORECASE),
    ),
    (
        "account_last4",
        AccountIdentifierClass.PROVIDER_ACCOUNT,
        0.99,
        re.compile(
            r"(?:account|acct)[\s.]*(?:#|number|no\.?|num\.?)[\s.:]*[*Xx.]+(\d{4})",
            re.IGNORECASE,
        ),
    ),
    (
        "account_number_full",
        AccountIdentifierClass.PROVIDER_ACCOUNT,
        0.99,
        re.compile(
            r"(?:account|acct)[\s.]*(?:#|number|no\.?|num\.?)[\s.:]*([A-Z0-9][\w-]{2,18}[A-Z0-9])",
            re.IGNORECASE,
        ),
    ),
    (
        "ending_in",
        AccountIdentifierClass.AMBIGUOUS,
        0.60,
        re.compile(
            r"(?:ending[\s.:]+(?:in[\s.:]+)?|last\s*4[\s.:]+(?:in[\s.:]+)?)(\d{4})",
            re.IGNORECASE,
        ),
    ),
    (
        "masked_number",
        AccountIdentifierClass.AMBIGUOUS,
        0.50,
        re.compile(
            r"[*Xx]{4,}\s*(\d{4})",
        ),
    ),
    (
        "member_id",
        AccountIdentifierClass.MEMBER,
        0.99,
        re.compile(
            r"member\s*(?:id|#|number)[\s.:]*([A-Z0-9]{6,15})",
            re.IGNORECASE,
        ),
    ),
    (
        "policy_number",
        AccountIdentifierClass.POLICY,
        0.99,
        re.compile(
            r"policy\s*(?:#|number|no\.?)[\s.:]*([A-Z0-9]{4,15})",
            re.IGNORECASE,
        ),
    ),
    (
        "claim_number",
        AccountIdentifierClass.CLAIM,
        0.99,
        re.compile(
            r"claim\s*(?:#|number|no\.?)[\s.:]*([A-Z0-9-]{4,20})",
            re.IGNORECASE,
        ),
    ),
    (
        "invoice_number",
        AccountIdentifierClass.INVOICE,
        0.99,
        re.compile(
            r"invoice\s*(?:#|number|no\.?)[\s.:]*([A-Z0-9-]{4,20})",
            re.IGNORECASE,
        ),
    ),
]


@dataclass
class ExtractionResult:
    """Result of account number extraction from a document."""

    document_id: int
    pattern_matches: list[dict[str, str]] = field(default_factory=list)
    raw_text_length: int = 0
    success: bool = False
    error: str | None = None


@dataclass(frozen=True)
class AccountExtractionDecision:
    candidate: dict[str, str] | None
    projection: AccountIdentifierProjection | None
    candidate_count: int
    requires_review: bool
    reason: str | None = None
    display_values: tuple[str, ...] = ()


def extract_account_numbers(text: str) -> list[dict[str, str]]:
    """Extract account numbers from text using regex patterns.

    Exact values exist only in this transient in-memory result. Callers must apply
    ``evaluate_account_identifiers`` and never serialize these dictionaries.
    """
    if not text or not text.strip():
        return []

    matches: list[dict[str, str]] = []
    seen_values: set[str] = set()

    for pattern_name, identifier_class, confidence, pattern in ACCOUNT_PATTERNS:
        for m in pattern.finditer(text):
            raw_value = m.group(1).strip()
            # Normalize: collapse whitespace, uppercase
            normalized = re.sub(r"\s+", "", raw_value).upper()
            if normalized and normalized not in seen_values:
                seen_values.add(normalized)
                matches.append(
                    {
                        "pattern": pattern_name,
                        "value": raw_value,
                        "normalized": normalized,
                        "identifier_class": identifier_class.value,
                        "confidence": str(confidence),
                    }
                )

    return matches


def pick_best_account_identifier(matches: list[dict[str, str]]) -> str | None:
    """Return an auto-projectable value, retaining compatibility for internal callers."""
    decision = evaluate_account_identifiers(matches)
    return decision.projection.paperless_value if decision.projection else None


def evaluate_account_identifiers(matches: list[dict[str, str]]) -> AccountExtractionDecision:
    """Classify candidates and reject ambiguous, multiple, or dedicated-field values."""
    eligible = [
        match
        for match in matches
        if AccountIdentifierClass(match["identifier_class"])
        not in {AccountIdentifierClass.CLAIM, AccountIdentifierClass.INVOICE}
    ]
    if not eligible:
        return AccountExtractionDecision(None, None, 0, False, "No account identifier found")

    distinct = {match["normalized"] for match in eligible}
    if len(distinct) > 1:
        display_values = tuple(
            dict.fromkeys(
                display
                for match in eligible
                if (
                    display := mask_account_identifier(
                        match["value"],
                        match["identifier_class"],
                    )
                )
            )
        )
        return AccountExtractionDecision(
            None,
            None,
            len(distinct),
            True,
            "Multiple plausible account identifiers require review",
            display_values,
        )

    candidate = eligible[0]
    projection = govern_account_identifier(
        candidate["value"],
        candidate["identifier_class"],
        float(candidate["confidence"]),
    )
    return AccountExtractionDecision(
        candidate,
        projection,
        1,
        projection.requires_review,
        projection.reason,
        (projection.display_value,) if projection.display_value else (),
    )


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
    *,
    identifier_class: AccountIdentifierClass | str | None = None,
    confidence: float = 1.0,
) -> bool:
    """Write a governed identifier directly to the canonical Paperless field."""
    try:
        if getattr(paperless_client, "list_custom_fields", None) is None:
            logger.warning("Paperless client cannot resolve custom-field definitions")
            return False
        schema = await PaperlessMetadataResolver(paperless_client).resolve(
            (MetadataFieldKey.ACCOUNT_IDENTIFIER,)
        )
        if identifier_class is None:
            update = build_metadata_update(
                MetadataFieldKey.ACCOUNT_IDENTIFIER,
                account_identifier,
                schema,
            )
        else:
            update, _projection = build_account_identifier_update(
                account_identifier,
                identifier_class,
                confidence,
                schema,
            )

        update_fields_fn = getattr(paperless_client, "update_custom_fields", None)
        if update_fields_fn is not None:
            await _call_sync_or_async(update_fields_fn, document_id, [update])
            logger.info("Wrote governed account identifier to Paperless doc %d", document_id)
            return True

        patch_fn = getattr(paperless_client, "patch", None)
        if patch_fn is None:
            logger.warning("Paperless client cannot update custom fields")
            return False

        await _call_sync_or_async(
            patch_fn,
            f"/api/documents/{document_id}/",
            {"custom_fields": [update]},
        )
        logger.info("Wrote governed account identifier to Paperless doc %d", document_id)
        return True
    except Exception:
        logger.error("Failed to write governed account identifier to Paperless doc %d", document_id)
        return False
