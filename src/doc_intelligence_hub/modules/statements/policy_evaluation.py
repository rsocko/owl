from __future__ import annotations

import hashlib
import json
from datetime import date
from string import Formatter

from doc_intelligence_hub.modules.statements.correspondent_analysis import period_label
from doc_intelligence_hub.modules.statements.correspondent_models import (
    DocumentExpectation,
    DocumentMetadataSnapshot,
    ExpectationPolicyPreview,
    PaperlessDocumentPatch,
    PolicyPatchOperation,
    PolicyViolationCode,
    PolicyViolationPreview,
)
from doc_intelligence_hub.modules.statements.models import DocumentRecord


def evaluate_expectation_policy(
    expectation: DocumentExpectation,
    correspondent_name: str,
    documents: list[DocumentRecord],
    *,
    tag_names: dict[int, str],
    document_type_names: dict[int, str],
    series_documents: dict[int, dict] | None = None,
) -> ExpectationPolicyPreview:
    """Evaluate a confirmed expectation without mutating Paperless or OWL state."""
    if expectation.status != "confirmed":
        raise ValueError("Only confirmed expectations can be evaluated")

    findings: list[PolicyViolationPreview] = []
    compliant = 0
    series_documents = series_documents or {}
    for document in sorted(documents, key=lambda item: item.id):
        finding = _evaluate_document(
            expectation,
            correspondent_name,
            document,
            tag_names=tag_names,
            document_type_names=document_type_names,
            series_document=series_documents.get(document.id),
        )
        if finding is None:
            compliant += 1
        else:
            findings.append(finding)
    return ExpectationPolicyPreview(
        expectation_id=expectation.id,
        correspondent_id=expectation.correspondent_id,
        matched_document_count=len(documents),
        compliant_document_count=compliant,
        findings=findings,
    )


def _evaluate_document(
    expectation: DocumentExpectation,
    correspondent_name: str,
    document: DocumentRecord,
    *,
    tag_names: dict[int, str],
    document_type_names: dict[int, str],
    series_document: dict | None,
) -> PolicyViolationPreview | None:
    policy = expectation.metadata_policy
    current_tags = set(document.tag_ids)
    violations: list[PolicyViolationCode] = []
    unresolved: list[PolicyViolationCode] = []

    missing_all = set(policy.all_of) - current_tags
    if missing_all:
        violations.append("missing_all_of")
    missing_any = bool(policy.any_of and not (set(policy.any_of) & current_tags))
    if missing_any:
        violations.append("missing_any_of")
    forbidden = set(policy.none_of) & current_tags
    if forbidden:
        violations.append("forbidden_tags")

    proposed_tag_set = current_tags | set(policy.all_of)
    if missing_any and len(policy.any_of) == 1:
        proposed_tag_set.add(policy.any_of[0])
    proposed_tag_set -= set(policy.none_of)
    proposed_tags = sorted(proposed_tag_set)
    if missing_any and not (set(policy.any_of) & proposed_tag_set):
        unresolved.append("missing_any_of")
    proposed_type = document.document_type_id
    if (
        policy.required_document_type_id is not None
        and document.document_type_id != policy.required_document_type_id
    ):
        violations.append("wrong_document_type")
        proposed_type = policy.required_document_type_id

    proposed_title = document.title
    missing_title_fields: list[str] = []
    if expectation.title_convention is not None:
        values = _title_values(
            expectation,
            correspondent_name,
            document,
            tag_names,
            series_document,
            proposed_tags,
        )
        missing_title_fields = _required_missing_fields(
            expectation.title_convention.template, values
        )
        if missing_title_fields:
            violations.append("title_missing_fields")
            unresolved.append("title_missing_fields")
        else:
            try:
                proposed_title = expectation.title_convention.render(values)
            except ValueError as exc:
                if "128-character" not in str(exc):
                    raise
                violations.append("title_too_long")
                unresolved.append("title_too_long")
            else:
                if proposed_title != document.title:
                    violations.append("title_mismatch")

    if not violations:
        return None

    current = _snapshot(
        document.title,
        sorted(current_tags),
        document.document_type_id,
        tag_names,
        document_type_names,
    )
    proposed = _snapshot(
        proposed_title,
        proposed_tags,
        proposed_type,
        tag_names,
        document_type_names,
    )
    patch = PaperlessDocumentPatch(
        title=proposed.title if proposed.title != current.title else None,
        tags=proposed.tag_ids if proposed.tag_ids != current.tag_ids else None,
        document_type=(
            proposed.document_type_id
            if proposed.document_type_id != current.document_type_id
            else None
        ),
    )
    operation = PolicyPatchOperation(
        expectation_id=expectation.id,
        document_id=document.id,
        expected=current,
        patch=patch,
    )
    preview_id = _preview_id(operation)
    return PolicyViolationPreview(
        preview_id=preview_id,
        operation=operation,
        proposed=proposed,
        violations=violations,
        unresolved_violations=unresolved,
        missing_title_fields=missing_title_fields,
    )


def _title_values(
    expectation: DocumentExpectation,
    correspondent_name: str,
    document: DocumentRecord,
    tag_names: dict[int, str],
    series_document: dict | None,
    proposed_tag_ids: list[int],
) -> dict[str, str | date | None]:
    period = _document_period(document, expectation, series_document)
    subject = _subject_from_any_of(expectation.metadata_policy.any_of, proposed_tag_ids, tag_names)
    return {
        "correspondent": correspondent_name,
        "series": expectation.series_discriminator,
        "kind": expectation.kind.replace("_", " ").title(),
        "period": period,
        "document_date": document.created,
        "subject": subject,
    }


def _document_period(
    document: DocumentRecord,
    expectation: DocumentExpectation,
    series_document: dict | None,
) -> str | None:
    if series_document is not None:
        if series_document.get("period_label"):
            return str(series_document["period_label"])
        if series_document.get("statement_date"):
            return period_label(
                date.fromisoformat(str(series_document["statement_date"])),
                expectation.cadence,
            )
        return None
    if expectation.cadence is not None:
        return period_label(document.created, expectation.cadence)
    return None


def _subject_from_any_of(
    allowed_tag_ids: list[int], assigned_tag_ids: list[int], tag_names: dict[int, str]
) -> str | None:
    matches = sorted(set(allowed_tag_ids) & set(assigned_tag_ids))
    if len(matches) != 1:
        return None
    name = tag_names.get(matches[0])
    if not name:
        return None
    return name.split(":", 1)[1].strip() if ":" in name else name


def _required_missing_fields(template: str, values: dict[str, str | date | None]) -> list[str]:
    required = {
        field_name for _, field_name, _, _ in Formatter().parse(template) if field_name is not None
    }
    return sorted(field for field in required if values.get(field) in (None, ""))


def _snapshot(
    title: str,
    tag_ids: list[int],
    document_type_id: int | None,
    tag_names: dict[int, str],
    document_type_names: dict[int, str],
) -> DocumentMetadataSnapshot:
    return DocumentMetadataSnapshot(
        title=title,
        tag_ids=tag_ids,
        tag_names=[tag_names.get(tag_id, str(tag_id)) for tag_id in tag_ids],
        document_type_id=document_type_id,
        document_type_name=(
            document_type_names.get(document_type_id) if document_type_id is not None else None
        ),
    )


def _preview_id(operation: PolicyPatchOperation) -> str:
    canonical = json.dumps(operation.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
