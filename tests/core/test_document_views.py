from __future__ import annotations

import pytest
from pydantic import ValidationError

from doc_intelligence_hub.core.document_views import (
    DocumentViewCatalogConfig,
    DocumentViewConfig,
    ViewLaunch,
    load_document_view_catalog,
)


def test_paperless_view_defaults_to_paperless_launch():
    view = DocumentViewConfig(
        id="missing-correspondent",
        label="Missing correspondent",
        provider="paperless",
        source_id=17,
    )

    assert view.resolved_launch is ViewLaunch.PAPERLESS
    assert view.resolved_owl_route is None


def test_paperless_view_can_launch_internal_owl_workflow():
    view = DocumentViewConfig(
        id="account-review",
        label="Account review",
        provider="paperless",
        source_id=18,
        launch="owl",
        owl_route="/triage?type=account-review",
    )

    assert view.resolved_launch is ViewLaunch.OWL
    assert view.resolved_owl_route == "/triage?type=account-review"


@pytest.mark.parametrize(
    ("source_id", "message"),
    [
        ("17", "numeric source_id"),
        (0, "positive integer"),
    ],
)
def test_paperless_view_requires_positive_strict_numeric_id(source_id, message):
    with pytest.raises(ValidationError, match=message):
        DocumentViewConfig(
            id="paperless-view",
            label="Paperless view",
            provider="paperless",
            source_id=source_id,
        )


def test_owl_view_requires_registered_source():
    with pytest.raises(ValidationError, match="Unknown OWL view source_id"):
        DocumentViewConfig(
            id="unknown-review",
            label="Unknown review",
            provider="owl",
            source_id="triage.unknown",
        )


def test_owl_route_rejects_external_url():
    with pytest.raises(ValidationError, match="internal application route"):
        DocumentViewConfig(
            id="unsafe-route",
            label="Unsafe route",
            provider="paperless",
            source_id=19,
            launch="owl",
            owl_route="https://example.invalid/review",
        )


def test_catalog_rejects_duplicate_view_ids():
    with pytest.raises(ValidationError, match="Duplicate document view id"):
        DocumentViewCatalogConfig.model_validate(
            {
                "groups": [
                    {
                        "id": "first",
                        "label": "First",
                        "views": [
                            {
                                "id": "duplicate",
                                "label": "One",
                                "provider": "paperless",
                                "source_id": 1,
                            }
                        ],
                    },
                    {
                        "id": "second",
                        "label": "Second",
                        "views": [
                            {
                                "id": "duplicate",
                                "label": "Two",
                                "provider": "owl",
                                "source_id": "triage.pending",
                            }
                        ],
                    },
                ]
            }
        )


def test_load_document_view_catalog(tmp_path):
    path = tmp_path / "document-views.yaml"
    path.write_text(
        """
groups:
  - id: daily-review
    label: Daily Review
    default_expanded: true
    views:
      - id: inbox
        label: Inbox
        provider: paperless
        source_id: 7
""",
        encoding="utf-8",
    )

    catalog = load_document_view_catalog(path)

    assert catalog.groups[0].id == "daily-review"
    assert catalog.groups[0].views[0].source_id == 7


def test_load_document_view_catalog_fails_for_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="Document views config file not found"):
        load_document_view_catalog(tmp_path / "missing.yaml")
