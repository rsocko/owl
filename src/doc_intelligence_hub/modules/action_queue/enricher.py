"""Paperless custom field enricher — writes action metadata back to documents."""

from typing import Optional

from .paperless_client import PaperlessClient
from .config import settings

# Custom field definitions to auto-create in Paperless
CUSTOM_FIELD_DEFINITIONS = [
    {
        "name": "Action Type",
        "data_type": "select",
        "extra_data": {
            "select_options": ["PAY", "RESPOND", "FILE", "REVIEW", "SHARE", "SCHEDULE", "SIGN", "ARCHIVE"]
        },
    },
    {
        "name": "Action Due Date",
        "data_type": "date",
    },
    {
        "name": "Action Amount",
        "data_type": "float",
    },
    {
        "name": "Action Urgency",
        "data_type": "select",
        "extra_data": {
            "select_options": ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
        },
    },
    {
        "name": "Action Status",
        "data_type": "select",
        "extra_data": {
            "select_options": ["pending", "completed", "dismissed"]
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
        self.client = PaperlessClient()
        self._field_id_cache: dict[str, int] = {}

    async def ensure_custom_fields_exist(self) -> dict[str, int]:
        """Create custom fields in Paperless if they don't exist.

        Returns:
            Mapping of field name -> field ID
        """
        existing = await self.client.get_custom_fields()
        existing_names = {f["name"]: f["id"] for f in existing}

        field_map = {}
        for field_def in CUSTOM_FIELD_DEFINITIONS:
            name = field_def["name"]
            if name in existing_names:
                field_map[name] = existing_names[name]
            else:
                created = await self.client.create_custom_field(field_def)
                field_map[name] = created["id"]
                print(f"  Created custom field: {name} (id={created['id']})")

        self._field_id_cache = field_map
        return field_map

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

        # Action Type
        if extraction.get("action_type"):
            custom_fields.append({
                "field": field_ids["Action Type"],
                "value": extraction["action_type"],
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

        # Urgency
        if extraction.get("urgency"):
            custom_fields.append({
                "field": field_ids["Action Urgency"],
                "value": extraction["urgency"],
            })

        # Status (always starts as pending)
        custom_fields.append({
            "field": field_ids["Action Status"],
            "value": "pending",
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
            await self.client.update_custom_fields(document_id, custom_fields)

    async def sync_status(self, document_id: int, status: str) -> None:
        """Mirror a status change from internal DB back to Paperless.

        Called when user marks an action complete/dismissed in the dashboard.
        """
        if not settings.write_to_paperless:
            return  # Safety: writes disabled via config

        field_ids = await self.get_field_ids()
        await self.client.update_custom_fields(document_id, [
            {"field": field_ids["Action Status"], "value": status}
        ])

    async def read_paperless_status(self, document_id: int) -> str | None:
        """Read the current Action Status value from Paperless.

        Used for bidirectional sync — detect if user changed the field in Paperless.
        """
        field_ids = await self.get_field_ids()
        status_field_id = field_ids.get("Action Status")
        if not status_field_id:
            return None

        doc = await self.client.get_document(document_id)
        custom_fields = doc.get("custom_fields", [])
        for field in custom_fields:
            if field.get("field") == status_field_id:
                return field.get("value")
        return None
