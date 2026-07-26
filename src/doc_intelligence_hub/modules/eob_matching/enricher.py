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
            "select_options": [
                {"label": "matched"},
                {"label": "unmatched"},
                {"label": "review_needed"},
            ],
        },
    },
    {
        "name": "EOB Match Score",
        "data_type": "decimal",
    },
    {
        "name": "EOB Match Confidence",
        "data_type": "select",
        "extra_data": {
            "select_options": [{"label": "HIGH"}, {"label": "MEDIUM"}, {"label": "LOW"}],
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
            "select_options": [{"label": "EOB"}, {"label": "BILL"}],
        },
    },
    {
        "name": "EOB Patient Responsibility",
        "data_type": "decimal",
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
        self._select_option_cache: dict[str, dict[str, int]] = {}

    async def ensure_custom_fields_exist(self) -> dict[str, int]:
        """Create custom fields in Paperless if they don't exist."""
        existing = await self.client.list_custom_fields()
        existing_by_name = {f["name"]: f for f in existing}

        field_map = {}
        for field_def in CUSTOM_FIELD_DEFINITIONS:
            name = field_def["name"]
            if name in existing_by_name:
                field_map[name] = existing_by_name[name]["id"]
                if field_def.get("data_type") == "select":
                    self._cache_select_options(name, existing_by_name[name])
            else:
                created = await self.client.create_custom_field(field_def)
                field_map[name] = created["id"]
                if field_def.get("data_type") == "select":
                    self._cache_select_options(name, created)

        self._field_id_cache = field_map
        return field_map

    def _cache_select_options(self, field_name: str, field_data: dict) -> None:
        """Build label -> option_id mapping for a select field."""
        extra_data = field_data.get("extra_data", {})
        options = extra_data.get("select_options", [])
        option_map = {}
        for opt in options:
            if isinstance(opt, dict):
                label = opt.get("label", "")
                opt_id = opt.get("id")
                if label and opt_id is not None:
                    option_map[label] = opt_id
        self._select_option_cache[field_name] = option_map

    def _resolve_select_value(self, field_name: str, label: str) -> int | str:
        """Resolve a select option label to its ID. Falls back to label if not cached."""
        option_map = self._select_option_cache.get(field_name, {})
        if label in option_map:
            return option_map[label]
        return label

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
            {
                "field": field_ids["EOB Match Status"],
                "value": self._resolve_select_value("EOB Match Status", "matched"),
            },
            {"field": field_ids["EOB Match Score"], "value": round(score, 1)},
            {
                "field": field_ids["EOB Match Confidence"],
                "value": self._resolve_select_value("EOB Match Confidence", confidence),
            },
            {"field": field_ids["EOB Matched Document"], "value": bill_document_id},
            {
                "field": field_ids["EOB Document Type"],
                "value": self._resolve_select_value("EOB Document Type", "EOB"),
            },
            {"field": field_ids["EOB Analyzed"], "value": today},
        ]
        if patient_responsibility is not None:
            eob_fields.append(
                {"field": field_ids["EOB Patient Responsibility"], "value": patient_responsibility}
            )
        await self.client.update_custom_fields(eob_document_id, eob_fields)

        # Tag the Bill document
        bill_fields = [
            {
                "field": field_ids["EOB Match Status"],
                "value": self._resolve_select_value("EOB Match Status", "matched"),
            },
            {"field": field_ids["EOB Match Score"], "value": round(score, 1)},
            {
                "field": field_ids["EOB Match Confidence"],
                "value": self._resolve_select_value("EOB Match Confidence", confidence),
            },
            {"field": field_ids["EOB Matched Document"], "value": eob_document_id},
            {
                "field": field_ids["EOB Document Type"],
                "value": self._resolve_select_value("EOB Document Type", "BILL"),
            },
            {"field": field_ids["EOB Analyzed"], "value": today},
        ]
        await self.client.update_custom_fields(bill_document_id, bill_fields)

    async def mark_unmatched(self, document_id: int, doc_type: str) -> None:
        """Tag a document as unmatched after analysis."""
        from datetime import date

        field_ids = await self.get_field_ids()
        await self.client.update_custom_fields(
            document_id,
            [
                {
                    "field": field_ids["EOB Match Status"],
                    "value": self._resolve_select_value("EOB Match Status", "unmatched"),
                },
                {
                    "field": field_ids["EOB Document Type"],
                    "value": self._resolve_select_value("EOB Document Type", doc_type),
                },
                {"field": field_ids["EOB Analyzed"], "value": date.today().isoformat()},
            ],
        )
