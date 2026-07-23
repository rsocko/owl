"""Paperless custom field enricher for EOB Matching — writes match metadata to documents."""

from __future__ import annotations

from typing import Optional

from doc_intelligence_hub.core.paperless import PaperlessClient


# Custom field definitions for EOB matching
CUSTOM_FIELD_DEFINITIONS = [
    {
        "name": "EOB Match Status",
        "data_type": "select",
        "extra_data": {
            "select_options": ["matched", "unmatched", "review_needed"],
        },
    },
    {
        "name": "EOB Match Score",
        "data_type": "float",
    },
    {
        "name": "EOB Match Confidence",
        "data_type": "select",
        "extra_data": {
            "select_options": ["HIGH", "MEDIUM", "LOW"],
        },
    },
    {
        "name": "EOB Matched Document",
        "data_type": "document_link",
    },
    {
        "name": "EOB Document Type",
        "data_type": "select",
        "extra_data": {
            "select_options": ["EOB", "BILL"],
        },
    },
    {
        "name": "EOB Patient Responsibility",
        "data_type": "float",
    },
    {
        "name": "EOB Analyzed",
        "data_type": "date",
    },
]


class EOBEnricher:
    """Writes EOB match results back to Paperless custom fields."""

    def __init__(self, client: PaperlessClient):
        self.client = client
        self._field_id_cache: dict[str, int] = {}

    async def ensure_custom_fields_exist(self) -> dict[str, int]:
        """Create custom fields in Paperless if they don't exist."""
        existing = await self.client.list_custom_fields()
        existing_names = {f["name"]: f["id"] for f in existing}

        field_map = {}
        for field_def in CUSTOM_FIELD_DEFINITIONS:
            name = field_def["name"]
            if name in existing_names:
                field_map[name] = existing_names[name]
            else:
                created = await self.client.create_custom_field(field_def)
                field_map[name] = created["id"]

        self._field_id_cache = field_map
        return field_map

    async def get_field_ids(self) -> dict[str, int]:
        if not self._field_id_cache:
            await self.ensure_custom_fields_exist()
        return self._field_id_cache

    async def link_match(
        self,
        eob_document_id: int,
        bill_document_id: int,
        score: float,
        confidence: str,
        patient_responsibility: Optional[float] = None,
    ) -> None:
        """Write match relationship to both EOB and Bill documents in Paperless."""
        from datetime import date

        field_ids = await self.get_field_ids()
        today = date.today().isoformat()

        # Tag the EOB document
        eob_fields = [
            {"field": field_ids["EOB Match Status"], "value": "matched"},
            {"field": field_ids["EOB Match Score"], "value": round(score, 1)},
            {"field": field_ids["EOB Match Confidence"], "value": confidence},
            {"field": field_ids["EOB Matched Document"], "value": bill_document_id},
            {"field": field_ids["EOB Document Type"], "value": "EOB"},
            {"field": field_ids["EOB Analyzed"], "value": today},
        ]
        if patient_responsibility is not None:
            eob_fields.append(
                {"field": field_ids["EOB Patient Responsibility"], "value": patient_responsibility}
            )
        await self.client.update_custom_fields(eob_document_id, eob_fields)

        # Tag the Bill document
        bill_fields = [
            {"field": field_ids["EOB Match Status"], "value": "matched"},
            {"field": field_ids["EOB Match Score"], "value": round(score, 1)},
            {"field": field_ids["EOB Match Confidence"], "value": confidence},
            {"field": field_ids["EOB Matched Document"], "value": eob_document_id},
            {"field": field_ids["EOB Document Type"], "value": "BILL"},
            {"field": field_ids["EOB Analyzed"], "value": today},
        ]
        await self.client.update_custom_fields(bill_document_id, bill_fields)

    async def mark_unmatched(self, document_id: int, doc_type: str) -> None:
        """Tag a document as unmatched after analysis."""
        from datetime import date

        field_ids = await self.get_field_ids()
        await self.client.update_custom_fields(document_id, [
            {"field": field_ids["EOB Match Status"], "value": "unmatched"},
            {"field": field_ids["EOB Document Type"], "value": doc_type},
            {"field": field_ids["EOB Analyzed"], "value": date.today().isoformat()},
        ])
