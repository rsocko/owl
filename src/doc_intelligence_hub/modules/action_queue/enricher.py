"""Paperless custom field enricher for neutral document-level metadata."""

import logging

from doc_intelligence_hub.core.paperless import PaperlessClient

from .config import settings

logger = logging.getLogger(__name__)


def _make_paperless_client() -> PaperlessClient:
    return PaperlessClient(base_url=settings.paperless_url, token=settings.paperless_api_token)


# OWL owns inferred action details. Paperless receives only document-level values
# and enough disposition metadata to support its document workflow.
CUSTOM_FIELD_DEFINITIONS = [
    {
        "name": "Document Amount",
        "data_type": "float",
    },
    {
        "name": "Action Status",
        "data_type": "select",
        "extra_data": {
            "select_options": [
                {"label": "pending"},
                {"label": "acknowledged"},
                {"label": "completed"},
                {"label": "snoozed"},
                {"label": "dismissed"},
                {"label": "not_an_action"},
            ]
        },
    },
    {
        "name": "Action Analyzed",
        "data_type": "date",
    },
]

LEGACY_AMOUNT_FIELD = "Action Amount"
LEGACY_INFERRED_FIELDS = (
    "Action Type",
    "Action Due Date",
    "Action Urgency",
    "Action Summary",
    "Action Count",
)


class PaperlessEnricher:
    """Writes document metadata and OWL disposition back to Paperless."""

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

        if "Document Amount" not in existing_by_name and LEGACY_AMOUNT_FIELD in existing_by_name:
            legacy_field = existing_by_name.pop(LEGACY_AMOUNT_FIELD)
            renamed = await self.client.update_custom_field(
                legacy_field["id"], {"name": "Document Amount"}
            )
            existing_by_name["Document Amount"] = renamed
            logger.info(
                "Renamed Paperless custom field %r to %r",
                LEGACY_AMOUNT_FIELD,
                "Document Amount",
            )

        field_map = {}
        for field_def in CUSTOM_FIELD_DEFINITIONS:
            name = field_def["name"]
            if name in existing_by_name:
                existing_field = existing_by_name[name]
                if field_def.get("data_type") == "select":
                    existing_field = await self._ensure_select_options(existing_field, field_def)
                field_map[name] = existing_field["id"]
                # Cache select option IDs for existing fields
                if field_def.get("data_type") == "select":
                    self._cache_select_options(name, existing_field)
            else:
                try:
                    created = await self.client.create_custom_field(field_def)
                    field_map[name] = created["id"]
                    print(f"  Created custom field: {name} (id={created['id']})")
                    logger.info("Created Paperless custom field: %s (id=%s)", name, created["id"])
                    # Cache select option IDs for newly created fields
                    if field_def.get("data_type") == "select":
                        self._cache_select_options(name, created)
                except Exception as e:
                    logger.warning(
                        "Failed to create custom field %r (data_type=%s): %s",
                        name,
                        field_def.get("data_type"),
                        e,
                    )
                    # Continue creating other fields — partial enrichment is better than none

        # Keep IDs for legacy inferred fields so a no-action disposition can
        # remove stale values without recreating those fields.
        for name in LEGACY_INFERRED_FIELDS:
            existing_field = existing_by_name.get(name)
            if existing_field:
                field_map[name] = existing_field["id"]

        self._field_id_cache = field_map
        return field_map

    async def _ensure_select_options(self, existing: dict, desired: dict) -> dict:
        """Append missing select options without replacing existing option IDs."""
        existing_extra = existing.get("extra_data", {})
        existing_options = existing_extra.get("select_options", [])
        existing_labels = {
            option.get("label") for option in existing_options if isinstance(option, dict)
        }
        missing = [
            option
            for option in desired.get("extra_data", {}).get("select_options", [])
            if option.get("label") not in existing_labels
        ]
        if not missing:
            return existing

        updated = await self.client.update_custom_field(
            existing["id"],
            {
                "extra_data": {
                    **existing_extra,
                    "select_options": [*existing_options, *missing],
                }
            },
        )
        logger.info(
            "Added Paperless select options %s to %r",
            [option["label"] for option in missing],
            existing["name"],
        )
        return updated

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
        if options and not option_map:
            logger.debug(
                "Select field %r has %d options but none have IDs yet — "
                "will fall back to label-based values",
                field_name,
                len(options),
            )
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

    async def enrich_document(self, document_id: int, extraction: dict) -> None:
        """Write document-level extraction results to Paperless custom fields.

        Args:
            document_id: Paperless document ID
            extraction: Parsed OWL result; inferred action details remain in OWL
        """
        if not settings.write_to_paperless:
            return  # Safety: writes disabled via config

        field_ids = await self.get_field_ids()
        from datetime import date

        custom_fields = []

        def _add_field(name: str, value) -> None:
            """Append a field entry if the field ID exists in Paperless."""
            fid = field_ids.get(name)
            if fid is not None:
                custom_fields.append({"field": fid, "value": value})

        # Amount describes the document and remains useful even without an action.
        if extraction.get("amount") is not None:
            _add_field("Document Amount", extraction["amount"])

        # Status (select — always starts as pending)
        _add_field("Action Status", self._resolve_select_value("Action Status", "pending"))

        # Analyzed date
        _add_field("Action Analyzed", date.today().isoformat())

        if custom_fields:
            import asyncio

            await asyncio.sleep(settings.rate_limit_delay)  # Rate-limit Paperless writes
            logger.info(
                "Writing %d custom field(s) to Paperless document_id=%s: %s",
                len(custom_fields),
                document_id,
                [f["field"] for f in custom_fields],
            )
            await self.client.update_custom_fields(document_id, custom_fields)

    async def sync_status(self, document_id: int, status: str) -> None:
        """Mirror a status change from internal DB back to Paperless.

        Called when a classification or user action changes the action lifecycle.
        When the status resolves the action (completed, dismissed, not_an_action),
        also removes the configured source tags (e.g. "Todo") from the document
        so it no longer surfaces in the intake queue.
        """
        if not settings.write_to_paperless:
            return  # Safety: writes disabled via config

        field_ids = await self.get_field_ids()
        status_field_id = field_ids.get("Action Status")
        if not status_field_id:
            raise RuntimeError("Cannot sync status: 'Action Status' field not found in Paperless")

        updates = [
            {
                "field": status_field_id,
                "value": self._resolve_select_value("Action Status", status),
            }
        ]
        if status == "not_an_action":
            updates.extend(
                {"field": field_ids[name], "value": None}
                for name in LEGACY_INFERRED_FIELDS
                if name in field_ids
            )

        await self.client.update_custom_fields(document_id, updates)

        # Remove source tags when the action is resolved
        resolved_statuses = {"completed", "dismissed", "not_an_action"}
        if settings.remove_source_tag_on_resolve and status in resolved_statuses:
            tags_to_remove = settings.monitor_tags
            if tags_to_remove:
                await self.client.remove_tags_from_document(document_id, tags_to_remove)
                logger.info(
                    "Removed source tags %s from document %d (status=%s)",
                    tags_to_remove,
                    document_id,
                    status,
                )

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
