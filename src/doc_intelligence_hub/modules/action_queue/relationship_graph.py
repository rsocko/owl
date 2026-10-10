"""Read-only relationship projection for one Action Queue obligation."""

from __future__ import annotations

from typing import Any

from doc_intelligence_hub.modules.triage.database import (
    DocumentRelationship,
)
from doc_intelligence_hub.modules.triage.database import (
    get_session as get_triage_session,
)

from .database import (
    Action,
    Obligation,
    ObligationDocument,
    ObligationPaymentEdge,
    PaymentEvidence,
    get_session,
)


class RelationshipGraphNotFoundError(LookupError):
    """Raised when the requested action does not exist."""


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def build_relationship_graph(action_id: int) -> dict[str, Any]:
    """Project durable OWL identities without inventing external-system nodes."""
    db = get_session()
    try:
        anchor = db.query(Action).filter_by(id=action_id).first()
        if not anchor:
            raise RelationshipGraphNotFoundError(f"Action {action_id} was not found")

        obligation = (
            db.query(Obligation).filter_by(id=anchor.obligation_id).first()
            if anchor.obligation_id
            else None
        )
        actions = (
            db.query(Action)
            .filter_by(obligation_id=obligation.id)
            .order_by(Action.created_at, Action.id)
            .all()
            if obligation
            else [anchor]
        )
        documents = (
            db.query(ObligationDocument)
            .filter_by(obligation_id=obligation.id, excluded=False)
            .order_by(ObligationDocument.document_date, ObligationDocument.created_at)
            .all()
            if obligation
            else []
        )
        if not documents:
            documents = [
                ObligationDocument(
                    obligation_id=obligation.id if obligation else "",
                    document_id=anchor.document_id,
                    role="supporting",
                    title=anchor.document_title,
                    document_type=anchor.document_type,
                    correspondent=anchor.correspondent,
                    document_date=anchor.document_date,
                    amount=anchor.document_amount
                    if anchor.document_amount is not None
                    else anchor.amount,
                    source="action_queue",
                )
            ]

        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        has_tyrion_evidence = False
        if obligation:
            nodes.append(
                {
                    "id": f"obligation:{obligation.id}",
                    "kind": "obligation",
                    "label": anchor.title or f"Obligation {obligation.id[:8]}",
                    "subtitle": obligation.relationship_explanation,
                    "occurred_at": _iso(obligation.created_at),
                    "status": obligation.status,
                    "metadata": {
                        "obligation_id": obligation.id,
                        "identity_key": obligation.identity_key,
                        "revision": obligation.revision,
                        "authoritative_document_id": obligation.authoritative_document_id,
                        "relationship_rules": obligation.relationship_rules or [],
                    },
                }
            )

        for action in actions:
            node_id = f"action:{action.id}"
            nodes.append(
                {
                    "id": node_id,
                    "kind": "action",
                    "label": action.title or f"Action {action.id}",
                    "subtitle": action.summary,
                    "occurred_at": _iso(action.created_at),
                    "status": action.status,
                    "metadata": {
                        "action_id": action.id,
                        "action_type": action.action_type,
                        "is_primary": bool(action.is_primary),
                        "external_projections": ["mission_control"],
                    },
                }
            )
            if obligation:
                edges.append(
                    {
                        "id": f"action-obligation:{action.id}:{obligation.id}",
                        "source": node_id,
                        "target": f"obligation:{obligation.id}",
                        "kind": "represents",
                        "label": "represents",
                        "status": "active",
                        "metadata": {},
                    }
                )

        document_ids = {document.document_id for document in documents}
        for document in documents:
            node_id = f"document:{document.document_id}"
            is_authoritative = bool(
                obligation and document.document_id == obligation.authoritative_document_id
            )
            nodes.append(
                {
                    "id": node_id,
                    "kind": "document",
                    "label": document.title or f"Document {document.document_id}",
                    "subtitle": document.correspondent or document.document_type,
                    "occurred_at": _iso(document.document_date or document.created_at),
                    "status": "authoritative" if is_authoritative else document.role,
                    "metadata": {
                        "document_id": document.document_id,
                        "role": document.role,
                        "document_type": document.document_type,
                        "amount": document.amount,
                        "confidence": document.confidence,
                        "source": document.source,
                        "is_authoritative": is_authoritative,
                    },
                }
            )
            if obligation:
                edges.append(
                    {
                        "id": f"obligation-document:{obligation.id}:{document.document_id}",
                        "source": f"obligation:{obligation.id}",
                        "target": node_id,
                        "kind": "evidence",
                        "label": document.role,
                        "status": "active",
                        "metadata": {"source": document.source},
                    }
                )
            else:
                edges.append(
                    {
                        "id": f"action-document:{anchor.id}:{document.document_id}",
                        "source": f"action:{anchor.id}",
                        "target": node_id,
                        "kind": "derived_from",
                        "label": "derived from",
                        "status": "active",
                        "metadata": {},
                    }
                )

        if obligation:
            payment_rows = (
                db.query(PaymentEvidence, ObligationPaymentEdge)
                .join(
                    ObligationPaymentEdge, ObligationPaymentEdge.evidence_id == PaymentEvidence.id
                )
                .filter(
                    ObligationPaymentEdge.obligation_id == obligation.id,
                    ObligationPaymentEdge.removed_at.is_(None),
                    PaymentEvidence.active.is_(True),
                )
                .order_by(PaymentEvidence.evidence_date, PaymentEvidence.created_at)
                .all()
            )
            for evidence, edge in payment_rows:
                has_tyrion_evidence = has_tyrion_evidence or evidence.source_system.startswith(
                    "tyrion"
                )
                node_id = f"payment-evidence:{evidence.id}"
                nodes.append(
                    {
                        "id": node_id,
                        "kind": "payment_evidence",
                        "label": evidence.payee_hint or evidence.kind.replace("_", " ").title(),
                        "subtitle": evidence.source_system.replace("_", " ").title(),
                        "occurred_at": _iso(evidence.evidence_date or evidence.created_at),
                        "status": evidence.payment_status,
                        "metadata": {
                            "evidence_id": evidence.id,
                            "source_system": evidence.source_system,
                            "kind": evidence.kind,
                            "amount_minor": evidence.amount_minor,
                            "currency": evidence.currency,
                            "match_state": evidence.match_state,
                            "confidence": evidence.confidence,
                            "reason_codes": evidence.reason_codes or [],
                        },
                    }
                )
                edges.append(
                    {
                        "id": f"payment-edge:{edge.id}",
                        "source": node_id,
                        "target": f"obligation:{obligation.id}",
                        "kind": "payment_evidence",
                        "label": edge.state,
                        "status": edge.state,
                        "metadata": {
                            "edge_id": edge.id,
                            "provenance": edge.provenance,
                            "confidence": edge.confidence,
                            "reason_codes": edge.reason_codes or [],
                        },
                    }
                )
    finally:
        db.close()

    if len(document_ids) > 1:
        triage_db = get_triage_session()
        try:
            relationships = (
                triage_db.query(DocumentRelationship)
                .filter(
                    DocumentRelationship.removed_at.is_(None),
                    DocumentRelationship.source_document_id.in_(document_ids),
                    DocumentRelationship.target_document_id.in_(document_ids),
                )
                .order_by(DocumentRelationship.created_at, DocumentRelationship.id)
                .all()
            )
            for relationship in relationships:
                edges.append(
                    {
                        "id": f"document-relationship:{relationship.id}",
                        "source": f"document:{relationship.source_document_id}",
                        "target": f"document:{relationship.target_document_id}",
                        "kind": relationship.relationship_type,
                        "label": relationship.relationship_type.replace("_", " "),
                        "status": "active",
                        "metadata": {
                            "relationship_id": relationship.id,
                            "provenance": relationship.provenance,
                            "confidence": relationship.confidence,
                            "priority_adjustment": relationship.priority_adjustment,
                        },
                    }
                )
        finally:
            triage_db.close()

    warnings = [
        {
            "code": "mission_control_task_identity_unavailable",
            "message": (
                "Mission Control task nodes are omitted: OWL exports actions but does not "
                "receive a durable Mission Control task identity."
            ),
        }
    ]
    if has_tyrion_evidence:
        warnings.append(
            {
                "code": "tyrion_source_record_identity_private",
                "message": (
                    "Tyrion source-record nodes are omitted: the graph uses OWL's durable, "
                    "privacy-bounded payment-evidence identity instead of upstream references."
                ),
            }
        )
    if not obligation:
        warnings.append(
            {
                "code": "obligation_identity_unavailable",
                "message": "This action has not been assigned a durable OWL obligation identity.",
            }
        )

    return {
        "contract_version": "1.0",
        "scope": {
            "action_id": action_id,
            "obligation_id": obligation.id if obligation else None,
        },
        "ownership": {
            "node_identity_owner": {
                "obligation": "owl",
                "action": "owl",
                "document": "paperless",
                "payment_evidence": "owl",
            },
            "edge_identity_owner": "owl",
            "external_identity_policy": (
                "External source references are not graph identities unless a connector "
                "contract persists an opaque, stable reference in OWL."
            ),
        },
        "nodes": nodes,
        "edges": edges,
        "warnings": warnings,
    }
