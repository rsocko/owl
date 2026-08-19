"""Typed configuration for the Document Views launcher."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator


class ViewProvider(str, Enum):
    PAPERLESS = "paperless"
    OWL = "owl"


class ViewLaunch(str, Enum):
    PAPERLESS = "paperless"
    OWL = "owl"


class OwlViewDefinition(BaseModel):
    """Registered OWL-native count and navigation contract."""

    model_config = ConfigDict(frozen=True)

    route: str
    item_type: str | None = None


OWL_VIEW_DEFINITIONS: dict[str, OwlViewDefinition] = {
    "triage.pending": OwlViewDefinition(route="/triage"),
    "triage.eob-match-review": OwlViewDefinition(
        route="/triage?type=eob_match_review",
        item_type="eob_match_review",
    ),
    "triage.grouping-anomaly": OwlViewDefinition(
        route="/triage?type=grouping_anomaly",
        item_type="grouping_anomaly",
    ),
    "triage.orphan-document": OwlViewDefinition(
        route="/triage?type=orphan_document",
        item_type="orphan_document",
    ),
}


class DocumentViewConfig(BaseModel):
    """One allowlisted view in an operator-defined group."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    label: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=300)
    provider: ViewProvider
    source_id: StrictInt | StrictStr
    launch: ViewLaunch | None = None
    owl_route: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_provider_contract(self) -> DocumentViewConfig:
        if self.provider is ViewProvider.PAPERLESS:
            if isinstance(self.source_id, bool) or not isinstance(self.source_id, int):
                raise ValueError("Paperless views require a numeric source_id")
            if self.source_id <= 0:
                raise ValueError("Paperless source_id must be a positive integer")
            self.launch = self.launch or ViewLaunch.PAPERLESS
        else:
            if not isinstance(self.source_id, str):
                raise ValueError("OWL views require a registered string source_id")
            if self.source_id not in OWL_VIEW_DEFINITIONS:
                raise ValueError(f"Unknown OWL view source_id: {self.source_id}")
            if self.launch is ViewLaunch.PAPERLESS:
                raise ValueError("OWL-native views cannot launch Paperless")
            self.launch = ViewLaunch.OWL

        if self.launch is ViewLaunch.OWL and self.provider is ViewProvider.PAPERLESS:
            if not self.owl_route:
                raise ValueError("Paperless views that launch OWL require owl_route")
        elif self.owl_route is not None:
            raise ValueError("owl_route is only valid for Paperless views that launch OWL")

        if self.owl_route is not None and (
            not self.owl_route.startswith("/")
            or self.owl_route.startswith("//")
            or "://" in self.owl_route
        ):
            raise ValueError("owl_route must be an internal application route")
        return self

    @property
    def resolved_source_id(self) -> int | str:
        return self.source_id

    @property
    def resolved_launch(self) -> ViewLaunch:
        if self.launch is None:
            raise RuntimeError("Document view launch was not resolved during validation")
        return self.launch

    @property
    def resolved_owl_route(self) -> str | None:
        if self.provider is ViewProvider.OWL:
            return OWL_VIEW_DEFINITIONS[str(self.source_id)].route
        return self.owl_route


class DocumentViewGroupConfig(BaseModel):
    """A collapsible group of document views."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    label: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=300)
    default_expanded: bool = False
    views: list[DocumentViewConfig] = Field(default_factory=list, max_length=25)


class DocumentViewCatalogConfig(BaseModel):
    """Complete deployment allowlist for the Document Views page."""

    model_config = ConfigDict(extra="forbid")

    groups: list[DocumentViewGroupConfig] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> DocumentViewCatalogConfig:
        group_ids: set[str] = set()
        view_ids: set[str] = set()
        view_count = 0
        for group in self.groups:
            if group.id in group_ids:
                raise ValueError(f"Duplicate document view group id: {group.id}")
            group_ids.add(group.id)
            for view in group.views:
                if view.id in view_ids:
                    raise ValueError(f"Duplicate document view id: {view.id}")
                view_ids.add(view.id)
                view_count += 1
        if view_count > 50:
            raise ValueError("Document view catalog cannot contain more than 50 views")
        return self


def load_document_view_catalog(path: str | Path) -> DocumentViewCatalogConfig:
    """Load and validate one Document Views YAML file."""
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"Document views config file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError("Document views config must contain a YAML object")
    return DocumentViewCatalogConfig.model_validate(raw)


__all__ = [
    "DocumentViewCatalogConfig",
    "DocumentViewConfig",
    "DocumentViewGroupConfig",
    "OWL_VIEW_DEFINITIONS",
    "OwlViewDefinition",
    "ViewLaunch",
    "ViewProvider",
    "load_document_view_catalog",
]
