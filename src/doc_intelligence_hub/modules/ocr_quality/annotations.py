"""CRUD helpers for reviewer-drawn document annotations (issue #134, Part 2).

Plain SQLAlchemy session-per-call helpers, matching the rest of this
module's convention (see ``service.py``): open a session, do the work,
commit/close in a ``finally`` block. No auth/user-identity concept exists
anywhere in this API today, so ``created_by`` is accepted as opaque,
optional free text supplied by the caller — not bound to a real identity.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from .database import DocumentAnnotation

SessionFactory = Callable[[], Session]


def _to_dict(row: DocumentAnnotation) -> dict[str, Any]:
    return {
        "id": row.id,
        "document_id": row.document_id,
        "page": row.page,
        "x0": row.x0,
        "top": row.top,
        "x1": row.x1,
        "bottom": row.bottom,
        "label": row.label,
        "note": row.note,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_annotations(
    session_factory: SessionFactory, *, document_id: int, page: int | None = None
) -> list[dict[str, Any]]:
    db = session_factory()
    try:
        query = db.query(DocumentAnnotation).filter(DocumentAnnotation.document_id == document_id)
        if page is not None:
            query = query.filter(DocumentAnnotation.page == page)
        rows = query.order_by(DocumentAnnotation.page.asc(), DocumentAnnotation.id.asc()).all()
        return [_to_dict(row) for row in rows]
    finally:
        db.close()


def create_annotation(
    session_factory: SessionFactory,
    *,
    document_id: int,
    page: int,
    x0: float,
    top: float,
    x1: float,
    bottom: float,
    label: str,
    note: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    db = session_factory()
    try:
        row = DocumentAnnotation(
            document_id=document_id,
            page=page,
            x0=x0,
            top=top,
            x1=x1,
            bottom=bottom,
            label=label,
            note=note,
            created_by=created_by,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _to_dict(row)
    finally:
        db.close()


def get_annotation(
    session_factory: SessionFactory, *, document_id: int, annotation_id: int
) -> dict[str, Any] | None:
    db = session_factory()
    try:
        row = (
            db.query(DocumentAnnotation)
            .filter(
                DocumentAnnotation.id == annotation_id,
                DocumentAnnotation.document_id == document_id,
            )
            .one_or_none()
        )
        return _to_dict(row) if row is not None else None
    finally:
        db.close()


_UPDATABLE_FIELDS = frozenset({"page", "x0", "top", "x1", "bottom", "label", "note"})


def update_annotation(
    session_factory: SessionFactory,
    *,
    document_id: int,
    annotation_id: int,
    updates: dict[str, Any],
) -> dict[str, Any] | None:
    """Patch an existing annotation.

    ``updates`` should come from the request body with unset fields already
    excluded (e.g. Pydantic's ``model_dump(exclude_unset=True)``), so a key
    that IS present is always applied — including ``note=None`` to
    explicitly clear it — while a key that's absent is left untouched.
    """
    db = session_factory()
    try:
        row = (
            db.query(DocumentAnnotation)
            .filter(
                DocumentAnnotation.id == annotation_id,
                DocumentAnnotation.document_id == document_id,
            )
            .one_or_none()
        )
        if row is None:
            return None
        for key, value in updates.items():
            if key in _UPDATABLE_FIELDS:
                setattr(row, key, value)
        row.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
        return _to_dict(row)
    finally:
        db.close()


def delete_annotation(
    session_factory: SessionFactory, *, document_id: int, annotation_id: int
) -> bool:
    db = session_factory()
    try:
        row = (
            db.query(DocumentAnnotation)
            .filter(
                DocumentAnnotation.id == annotation_id,
                DocumentAnnotation.document_id == document_id,
            )
            .one_or_none()
        )
        if row is None:
            return False
        db.delete(row)
        db.commit()
        return True
    finally:
        db.close()


__all__ = [
    "create_annotation",
    "delete_annotation",
    "get_annotation",
    "list_annotations",
    "update_annotation",
]
