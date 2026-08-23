from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import httpx
from sqlalchemy.exc import SQLAlchemyError

from doc_intelligence_hub.core.resilience import CircuitOpenError, PaperlessError
from doc_intelligence_hub.modules.statements.correspondent_models import (
    DocumentMetadataSnapshot,
    PolicyApplyRequest,
    PolicyApplyResponse,
    PolicyOperationResult,
    PolicyPatchOperation,
    PolicyUndoRequest,
    SelectedPolicyOperation,
)
from doc_intelligence_hub.modules.statements.policy_evaluation import policy_operation_id
from doc_intelligence_hub.modules.triage.database import (
    complete_policy_correction_undo,
    create_policy_correction_event,
    get_policy_correction_event,
    mark_correction_synced,
)

_SENSITIVE_NUMBER = re.compile(r"\b\d{3,}\b")


def _safe_display(value: str | None) -> str | None:
    if value is None:
        return None

    return _SENSITIVE_NUMBER.sub("[redacted]", value)[:128]


def _digest(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _snapshot(
    document: dict[str, Any],
    tag_names: dict[int, str],
    document_type_names: dict[int, str],
) -> DocumentMetadataSnapshot:
    tag_ids = sorted(int(tag_id) for tag_id in document.get("tags") or [])
    document_type = document.get("document_type")
    document_type_id = int(document_type) if document_type is not None else None
    return DocumentMetadataSnapshot(
        title=str(document.get("title") or ""),
        tag_ids=tag_ids,
        tag_names=[tag_names.get(tag_id, str(tag_id)) for tag_id in tag_ids],
        document_type_id=document_type_id,
        document_type_name=(
            document_type_names.get(document_type_id) if document_type_id is not None else None
        ),
    )


def _proposed(
    operation: PolicyPatchOperation,
    tag_names: dict[int, str],
    document_type_names: dict[int, str],
) -> DocumentMetadataSnapshot:
    patch = operation.patch
    tag_ids = sorted(patch.tags if patch.tags is not None else operation.expected.tag_ids)
    document_type_id = (
        patch.document_type
        if patch.document_type is not None
        else operation.expected.document_type_id
    )
    return DocumentMetadataSnapshot(
        title=patch.title if patch.title is not None else operation.expected.title,
        tag_ids=tag_ids,
        tag_names=[tag_names.get(tag_id, str(tag_id)) for tag_id in tag_ids],
        document_type_id=document_type_id,
        document_type_name=(
            document_type_names.get(document_type_id) if document_type_id is not None else None
        ),
    )


def _audit_payload(
    *,
    expectation_id: str,
    actor: str,
    reason: str,
    selected: SelectedPolicyOperation,
    proposed: DocumentMetadataSnapshot,
) -> dict[str, Any]:
    old = selected.operation.expected
    patch = selected.operation.patch
    old_tags = set(old.tag_ids)
    new_tags = set(proposed.tag_ids)
    fields = [name for name in ("title", "tags", "document_type") if name in patch.model_dump()]
    return {
        "schema_version": 1,
        "actor": _safe_display(actor),
        "expectation_id": expectation_id,
        "reason": _safe_display(reason),
        "paperless_document_id": selected.operation.document_id,
        "preview_id": selected.preview_id,
        "changed_fields": fields,
        "old_display": {
            "title": _safe_display(old.title) if patch.title is not None else None,
            "tags": [_safe_display(name) for name in old.tag_names]
            if patch.tags is not None
            else None,
            "document_type": (
                _safe_display(old.document_type_name) if patch.document_type is not None else None
            ),
        },
        "new_display": {
            "title": _safe_display(proposed.title) if patch.title is not None else None,
            "tags": (
                [_safe_display(name) for name in proposed.tag_names]
                if patch.tags is not None
                else None
            ),
            "document_type": (
                _safe_display(proposed.document_type_name)
                if patch.document_type is not None
                else None
            ),
        },
        "old_digest": _digest(old.model_dump(mode="json")),
        "new_digest": _digest(proposed.model_dump(mode="json")),
        "tag_ids_added": sorted(new_tags - old_tags),
        "tag_ids_removed": sorted(old_tags - new_tags),
        "old_document_type_id": (old.document_type_id if patch.document_type is not None else None),
        "new_document_type_id": (
            proposed.document_type_id if patch.document_type is not None else None
        ),
    }


async def apply_policy_operations(
    *,
    expectation_id: str,
    request: PolicyApplyRequest,
    client: Any,
    tag_names: dict[int, str],
    document_type_names: dict[int, str],
    preview_signing_key: str,
) -> PolicyApplyResponse:
    results: list[PolicyOperationResult] = []
    seen_documents: set[int] = set()
    for selected in request.operations:
        operation = selected.operation
        if operation.expectation_id != expectation_id:
            results.append(
                _failure(
                    selected, "expectation_mismatch", "Operation belongs to another expectation."
                )
            )
            continue
        if policy_operation_id(operation, signing_key=preview_signing_key) != selected.preview_id:
            results.append(
                _failure(
                    selected, "tampered_preview", "Preview operation failed integrity validation."
                )
            )
            continue
        if operation.document_id in seen_documents:
            results.append(
                _failure(selected, "duplicate_document", "Document was selected more than once.")
            )
            continue
        seen_documents.add(operation.document_id)
        patch = operation.patch.model_dump(mode="json")
        if not patch:
            results.append(
                _failure(selected, "no_changes", "Preview contains no resolvable metadata changes.")
            )
            continue
        try:
            current = _snapshot(
                await client.get_document(operation.document_id),
                tag_names,
                document_type_names,
            )
        except (CircuitOpenError, PaperlessError, httpx.HTTPError) as exc:
            results.append(_failure(selected, "paperless_read_failed", str(exc)))
            continue
        if current != operation.expected:
            results.append(
                _failure(
                    selected,
                    "stale_document",
                    "Paperless metadata changed after preview; refresh before applying.",
                )
            )
            continue

        proposed = _proposed(operation, tag_names, document_type_names)
        try:
            event_id = create_policy_correction_event(
                document_id=operation.document_id,
                actor=_safe_display(request.actor) or "user",
                payload=_audit_payload(
                    expectation_id=expectation_id,
                    actor=request.actor,
                    reason=request.reason,
                    selected=selected,
                    proposed=proposed,
                ),
            )
        except SQLAlchemyError:
            results.append(
                _failure(selected, "audit_write_failed", "Correction audit could not be recorded.")
            )
            continue

        try:
            await client.update_document(operation.document_id, patch)
        except (CircuitOpenError, PaperlessError, httpx.HTTPError) as exc:
            results.append(_failure(selected, "paperless_patch_failed", str(exc), event_id))
            continue
        try:
            synced = mark_correction_synced(event_id)
        except SQLAlchemyError:
            synced = False
        if not synced:
            results.append(
                _failure(
                    selected,
                    "audit_finalize_failed",
                    "Paperless was updated, but the audit record could not be finalized.",
                    event_id,
                )
            )
            continue
        results.append(
            PolicyOperationResult(
                preview_id=selected.preview_id,
                document_id=operation.document_id,
                status="succeeded",
                audit_event_id=event_id,
                message="Approved metadata correction applied.",
            )
        )
    return PolicyApplyResponse(expectation_id=expectation_id, results=results)


async def undo_policy_operation(
    *,
    event_id: str,
    request: PolicyUndoRequest,
    client: Any,
    tag_names: dict[int, str],
    document_type_names: dict[int, str],
    preview_signing_key: str,
) -> PolicyOperationResult:
    selected = SelectedPolicyOperation(preview_id=request.preview_id, operation=request.operation)
    if (
        policy_operation_id(request.operation, signing_key=preview_signing_key)
        != request.preview_id
    ):
        return _failure(
            selected, "tampered_preview", "Preview operation failed integrity validation."
        )
    try:
        audit = get_policy_correction_event(event_id)
    except (SQLAlchemyError, json.JSONDecodeError):
        return _failure(selected, "audit_read_failed", "Correction audit could not be read.")
    if audit is None:
        return _failure(selected, "audit_not_found", "Correction audit was not found.")
    if audit["undone"]:
        return _failure(selected, "already_undone", "Correction was already undone.")
    payload = audit["payload"]
    if (
        audit["target_id"] != str(request.operation.document_id)
        or payload.get("preview_id") != request.preview_id
        or payload.get("expectation_id") != request.operation.expectation_id
    ):
        return _failure(selected, "audit_mismatch", "Undo request does not match the audit record.")

    proposed = _proposed(request.operation, tag_names, document_type_names)
    if payload.get("old_digest") != _digest(
        request.operation.expected.model_dump(mode="json")
    ) or payload.get("new_digest") != _digest(proposed.model_dump(mode="json")):
        return _failure(selected, "audit_mismatch", "Undo values do not match the audit record.")
    try:
        current = _snapshot(
            await client.get_document(request.operation.document_id),
            tag_names,
            document_type_names,
        )
    except (CircuitOpenError, PaperlessError, httpx.HTTPError) as exc:
        return _failure(selected, "paperless_read_failed", str(exc))

    restore_patch: dict[str, Any] = {}
    patch = request.operation.patch
    if patch.title is not None:
        if current.title != proposed.title:
            return _failure(selected, "undo_conflict", "Document title changed after correction.")
        restore_patch["title"] = request.operation.expected.title
    if patch.document_type is not None:
        if current.document_type_id != proposed.document_type_id:
            return _failure(selected, "undo_conflict", "Document type changed after correction.")
        restore_patch["document_type"] = request.operation.expected.document_type_id
    if patch.tags is not None:
        current_tags = set(current.tag_ids)
        added = set(payload.get("tag_ids_added") or [])
        removed = set(payload.get("tag_ids_removed") or [])
        if not added.issubset(current_tags) or removed & current_tags:
            return _failure(
                selected, "undo_conflict", "Policy-managed tags changed after correction."
            )
        restore_patch["tags"] = sorted((current_tags - added) | removed)

    try:
        await client.update_document(request.operation.document_id, restore_patch)
    except (CircuitOpenError, PaperlessError, httpx.HTTPError) as exc:
        return _failure(selected, "paperless_patch_failed", str(exc))
    try:
        undo_event_id = complete_policy_correction_undo(
            event_id,
            actor=request.actor,
            payload={
                "schema_version": 1,
                "actor": _safe_display(request.actor),
                "reason": _safe_display(request.reason),
                "paperless_document_id": request.operation.document_id,
                "expectation_id": request.operation.expectation_id,
                "original_event_id": event_id,
                "preview_id": request.preview_id,
                "restored_fields": sorted(restore_patch),
            },
        )
    except (KeyError, ValueError, SQLAlchemyError):
        return _failure(
            selected,
            "audit_finalize_failed",
            "Paperless was restored, but the undo audit could not be finalized.",
        )
    return PolicyOperationResult(
        preview_id=request.preview_id,
        document_id=request.operation.document_id,
        status="succeeded",
        audit_event_id=undo_event_id,
        message="Correction undone without changing unrelated metadata.",
    )


def _failure(
    selected: SelectedPolicyOperation,
    code: str,
    message: str,
    event_id: str | None = None,
) -> PolicyOperationResult:
    return PolicyOperationResult(
        preview_id=selected.preview_id,
        document_id=selected.operation.document_id,
        status="failed",
        audit_event_id=event_id,
        error_code=code,
        message=message,
    )
