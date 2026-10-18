"""Conservative explicit-reference extraction and Paperless resolution."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Iterable

MAX_EXTRACTED_REFERENCES = 12
MAX_SUBSTRING_MATCHES = 200
MAX_RESOLVED_MATCHES = 3

_REFERENCE_LABELS = {
    "invoice": r"invoice|bill",
    "claim": r"claim",
    "order": r"(?:purchase\s+)?order|po",
    "contract": r"contract|agreement",
    "case": r"case|file|matter",
    "policy": r"policy",
    "account": r"account|acct",
    "reference": r"reference|ref",
}
_LABELED_REFERENCE_PATTERN = re.compile(
    r"\b(?P<label>"
    + "|".join(f"(?P<{kind}>{labels})" for kind, labels in _REFERENCE_LABELS.items())
    + r")\s*(?:number|no\.?|#|id|:|-)?\s*"
    r"(?P<value>[A-Z0-9][A-Z0-9./_-]{2,63})\b",
    re.IGNORECASE,
)
_GENERIC_TOKEN_PATTERN = re.compile(r"\b(?=[A-Z0-9./_-]*\d)[A-Z0-9][A-Z0-9./_-]{3,63}\b", re.I)
_REFERENCE_CUE_PATTERN = re.compile(
    r"\b(invoice|bill|claim|order|contract|agreement|case|file|matter|policy|"
    r"account|acct|reference|ref)\b",
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION = ".,;:)]}"


@dataclass(frozen=True)
class ExplicitReference:
    kind: str
    value: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "value": self.value, "source": self.source}


@dataclass(frozen=True)
class ResolvedReference:
    reference: ExplicitReference
    document_ids: tuple[int, ...]
    substring_match_count: int
    status: str


def _normalized_reference(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().strip(_TRAILING_PUNCTUATION))


def _reference_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _valid_reference(kind: str, value: str) -> bool:
    normalized = _normalized_reference(value)
    if (
        len(normalized) < 4
        or len(normalized) > 64
        or not any(char.isdigit() for char in normalized)
    ):
        return False
    compact = _reference_key(normalized)
    if not compact or (len(compact) == 4 and compact.startswith(("19", "20"))):
        return False
    if compact.isdigit() and len(compact) < 5 and kind == "reference":
        return False
    return True


def _deduplicate(references: Iterable[ExplicitReference]) -> list[ExplicitReference]:
    results: list[ExplicitReference] = []
    seen: set[str] = set()
    for reference in references:
        value = _normalized_reference(reference.value)
        key = _reference_key(value)
        if key in seen or not _valid_reference(reference.kind, value):
            continue
        seen.add(key)
        results.append(ExplicitReference(kind=reference.kind, value=value, source=reference.source))
        if len(results) >= MAX_EXTRACTED_REFERENCES:
            break
    return results


def extract_labeled_references(
    text: str, *, source: str = "deterministic"
) -> list[ExplicitReference]:
    """Extract explicitly labeled document identifiers without guessing bare numbers."""
    references: list[ExplicitReference] = []
    for match in _LABELED_REFERENCE_PATTERN.finditer(text or ""):
        kind = next(
            (candidate for candidate in _REFERENCE_LABELS if match.group(candidate)),
            "reference",
        )
        references.append(ExplicitReference(kind=kind, value=match.group("value"), source=source))
    return _deduplicate(references)


def extract_title_references(title: str) -> list[ExplicitReference]:
    """Extract labeled references plus conservative mixed tokens from a short title."""
    references = extract_labeled_references(title, source="title")
    references.extend(
        ExplicitReference(kind="reference", value=value, source="title")
        for value in _GENERIC_TOKEN_PATTERN.findall(title or "")
    )
    return _deduplicate(references)


async def extract_explicit_references(
    text: str,
    *,
    title: str = "",
    document_type: str = "",
    use_llm: bool = True,
) -> list[ExplicitReference]:
    """Extract explicit references deterministically, then optionally augment with the LLM."""
    deterministic = _deduplicate(
        [
            *extract_labeled_references(title, source="title"),
            *extract_labeled_references(text, source="content"),
        ]
    )
    if (
        deterministic
        or not use_llm
        or not text.strip()
        or not _REFERENCE_CUE_PATTERN.search(f"{title}\n{text}")
    ):
        return deterministic

    from doc_intelligence_hub.core.llm import chat_json

    prompt = (
        "Extract identifiers that explicitly refer to another document or record. "
        "Examples include invoice, claim, purchase order, contract, case, file, policy, "
        "account, and reference numbers. Do not return dates, amounts, phone numbers, "
        "page numbers, or identifiers belonging only to the current document unless the "
        "text uses them to reference another record. Preserve punctuation inside identifiers.\n\n"
        f"Title: {title or 'unknown'}\n"
        f"Document type: {document_type or 'unknown'}\n"
        f"Document text:\n{text[:12000]}\n\n"
        'Return JSON only: {"references":[{"kind":"invoice","value":"INV-1234"}]}.'
    )
    result = await chat_json(
        prompt,
        system=(
            "You extract explicit cross-document references conservatively. "
            "Return an empty references array when none are printed."
        ),
        temperature=0.0,
        max_tokens=500,
    )
    raw_references = result.get("references", []) if isinstance(result, dict) else []
    llm_references: list[ExplicitReference] = []
    if isinstance(raw_references, list):
        for item in raw_references:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "reference").strip().lower()
            if kind not in _REFERENCE_LABELS:
                kind = "reference"
            llm_references.append(
                ExplicitReference(kind=kind, value=str(item.get("value") or ""), source="llm")
            )
    return _deduplicate([*deterministic, *llm_references])


def _whole_reference_pattern(reference: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![A-Z0-9]){re.escape(reference)}(?![A-Z0-9])",
        re.IGNORECASE,
    )


async def resolve_reference(
    client: Any,
    reference: ExplicitReference,
    *,
    exclude_document_id: int,
) -> ResolvedReference:
    """Resolve one reference through bounded substring search and whole-token verification."""
    documents = await client.search_documents_by_content(
        reference.value,
        limit=MAX_SUBSTRING_MATCHES + 1,
    )
    if len(documents) > MAX_SUBSTRING_MATCHES:
        return ResolvedReference(reference, (), len(documents), "scan_limit_exceeded")

    pattern = _whole_reference_pattern(reference.value)
    semaphore = asyncio.Semaphore(8)

    async def verify(document: dict[str, Any]) -> int | None:
        document_id = document.get("id")
        if (
            isinstance(document_id, bool)
            or not isinstance(document_id, int)
            or document_id == exclude_document_id
        ):
            return None
        content = document.get("content")
        if not isinstance(content, str):
            async with semaphore:
                content = await client.get_document_content(document_id)
        searchable = f"{document.get('title') or ''}\n{content or ''}"
        return document_id if pattern.search(searchable) else None

    verified = await asyncio.gather(*(verify(document) for document in documents))
    document_ids = tuple(sorted({document_id for document_id in verified if document_id}))
    if len(document_ids) > MAX_RESOLVED_MATCHES:
        return ResolvedReference(reference, (), len(documents), "ambiguous")
    return ResolvedReference(
        reference,
        document_ids,
        len(documents),
        "resolved" if document_ids else "not_found",
    )


async def resolve_references(
    client: Any,
    references: Iterable[ExplicitReference],
    *,
    exclude_document_id: int,
) -> list[ResolvedReference]:
    """Resolve a bounded set of references with deterministic output ordering."""
    results = await asyncio.gather(
        *(
            resolve_reference(
                client,
                reference,
                exclude_document_id=exclude_document_id,
            )
            for reference in list(references)[:MAX_EXTRACTED_REFERENCES]
        )
    )
    return sorted(
        results, key=lambda result: (_reference_key(result.reference.value), result.status)
    )
