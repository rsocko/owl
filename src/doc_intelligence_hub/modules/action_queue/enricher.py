"""Paperless custom field enricher — writes action metadata back to documents."""

import logging
from typing import Optional

from doc_intelligence_hub.core.paperless import PaperlessClient
from .config import settings

logger = logging.getLogger(__name__)


def _make_paperless_client() -> PaperlessClient:
    return PaperlessClient(base_url=settings.paperless_url, token=settings.paperless_api_token)

# Custom field definitions to auto-create in Paperless
CUSTOM_FIELD_DEFINITIONS = [
    {
        "name": "Action Type",
        "data_type": "select",
        "extra_data": {
            "select_options": [
                {"label": "PAY"}, {"label": "RESPOND"}, {"label": "FILE"},
                {"label": "REVIEW"}, {"label": "SHARE"}, {"label": "SCHEDULE"},
                {"label": "SIGN"}, {"label": "ARCHIVE"},
            ]
        },
    },
    {
        "name": "Action Due Date",
        "data_type": "date",
    },
    {
        "name": "Action Amount",
        "data_type": "decimal",
    },
    {
        "name": "Action Urgency",
        "data_type": "select",
        "extra_data": {
            "select_options": [
                {"label": "CRITICAL"}, {"label": "HIGH"},
                {"label": "MEDIUM"}, {"label": "LOW"},
            ]
        },
    },
    {
        "name": "Action Status",
        "data_type": "select",
        "extra_data": {
            "select_options": [
                {"label": "pending"}, {"label": "completed"}, {"label": "dismissed"},
            ]
        },
    },
    {
        "name": "Action Summary",
        "data_type": "string",
    },
    {
        "name": "Action Analyzed",
        "data_type": "date",
    },
    {
        "name": "Action Count",
        "data_type": "integer",
    },
]


class PaperlessEnricher:
    """Writes extracted action metadata back to Paperless custom fields."""

    def __init__(self):
        self.client = _make_paperless_client()
        self._field_id_cache: dict[str, int] = {}
        self._select_option_cache: dict[str, dict[str, int]] = {}

    async def ensure_custom_fields_exist(self) -> dict[str, int]:
        """Create custom fields in Paperless if they don't exist.

        Returns:
            Mapping of field name -> field ID
        """
        existing = await self.client.list_custom_fields()
        existing_by_name = {f["name"]: f for f in existing}

        field_map = {}
        for field_def in CUSTOM_FIELD_DEFINITIONS:
            name = field_def["name"]
            if name in existing_by_name:
                field_map[name] = existing_by_name[name]["id"]
                # Cache select option IDs for existing fields
                if field_def.get("data_type") == "select":
                    self._cache_select_options(name, existing_by_name[name])
            else:
                created = await self.client.create_custom_field(field_def)
                field_map[name] = created["id"]
                print(f"  Created custom field: {name} (id={created['id']})")
                logger.info("Created Paperless custom field: %s (id=%s)", name, created["id"])
                # Cache select option IDs for newly created fields
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
        # Fallback: return label directly (works on some Paperless versions)
        return label

    async def get_field_ids(self) -> dict[str, int]:
        """Get cached field IDs, populating cache if needed."""
        if not self._field_id_cache:
            await self.ensure_custom_fields_exist()
        return self._field_id_cache

    async def enrich_document(self, document_id: int, extraction: dict, action_count: int = 1) -> None:
        """Write extraction results to Paperless custom fields.

        Args:
            document_id: Paperless document ID
            extraction: Parsed result from OllamaAnalyzer (primary action + assessment)
            action_count: Total number of actions identified for this document
        """
        if not settings.write_to_paperless:
            return  # Safety: writes disabled via config

        import asyncio
        await asyncio.sleep(settings.rate_limit_delay)  # Be nice to Paperless

        field_ids = await self.get_field_ids()
        from datetime import date

        custom_fields = []

        # Action Type (select)
        if extraction.get("action_type"):
            custom_fields.append({
                "field": field_ids["Action Type"],
                "value": self._resolve_select_value("Action Type", extraction["action_type"]),
            })

        # Due Date
        if extraction.get("due_date"):
            custom_fields.append({
                "field": field_ids["Action Due Date"],
                "value": extraction["due_date"],
            })

        # Amount
        if extraction.get("amount") is not None:
            custom_fields.append({
                "field": field_ids["Action Amount"],
                "value": extraction["amount"],
            })

        # Urgency (select)
        if extraction.get("urgency"):
            custom_fields.append({
                "field": field_ids["Action Urgency"],
                "value": self._resolve_select_value("Action Urgency", extraction["urgency"]),
            })

        # Status (select — always starts as pending)
        custom_fields.append({
            "field": field_ids["Action Status"],
            "value": self._resolve_select_value("Action Status", "pending"),
        })

        # Summary (include action count hint if multiple)
        summary = extraction.get("summary", "")
        if action_count > 1:
            summary = f"[{action_count} actions] {summary}"
        if summary:
            custom_fields.append({
                "field": field_ids["Action Summary"],
                "value": summary[:255],
            })

        # Analyzed date
        custom_fields.append({
            "field": field_ids["Action Analyzed"],
            "value": date.today().isoformat(),
        })

        # Action Count
        custom_fields.append({
            "field": field_ids["Action Count"],
            "value": action_count,
        })

        if custom_fields:
            logger.info(
                "Writing %d custom field(s) to Paperless document_id=%s: %s",
                len(custom_fields), document_id, [f["field"] for f in custom_fields],
            )
            await self.client.update_custom_fields(document_id, custom_fields)

    async def sync_status(self, document_id: int, status: str) -> None:
        """Mirror a status change from internal DB back to Paperless.

        Called when user marks an action complete/dismissed in the dashboard.
        """
        if not settings.write_to_paperless:
            return  # Safety: writes disabled via config

        field_ids = await self.get_field_ids()
        await self.client.update_custom_fields(document_id, [
            {"field": field_ids["Action Status"], "value": self._resolve_select_value("Action Status", status)}
        ])

    async def read_paperless_status(self, document_id: int) -> str | None:
        """Read the current Action Status value from Paperless.

        Used for bidirectional sync — detect if user changed the field in Paperless.
        Returns the label string (e.g., "pending", "completed", "dismissed").
        """
        field_ids = await self.get_field_ids()
        status_field_id = field_ids.get("Action Status")
        if not status_field_id:
            return None

        doc = await self.client.get_document(document_id)
        custom_fields = doc.get("custom_fields", [])
        for field in custom_fields:
            if field.get("field") == status_field_id:
                value = field.get("value")
                # Reverse-map option ID back to label
                option_map = self._select_option_cache.get("Action Status", {})
                id_to_label = {v: k for k, v in option_map.items()}
                if isinstance(value, int) and value in id_to_label:
                    return id_to_label[value]
                return value  # Already a string or unknown
        return None
