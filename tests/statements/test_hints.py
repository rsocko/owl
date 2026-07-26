"""Tests for provider hints processing."""

from __future__ import annotations

from datetime import date

from doc_intelligence_hub.modules.statements.config import ProviderHint, ProviderHintGroup
from doc_intelligence_hub.modules.statements.hints import apply_hints
from doc_intelligence_hub.modules.statements.models import (
    AnalysisPattern,
    DocumentRecord,
    ProviderCandidate,
)


def _make_provider(name: str, key: str = "", frequency: str = "monthly") -> ProviderCandidate:
    return ProviderCandidate(
        provider_key=key or name.lower().replace(" ", "-"),
        provider_name=name,
        correspondent_id=1,
        document_count=6,
        normalized_title=name.lower(),
        title_consistency=1.0,
        pattern=AnalysisPattern(
            frequency=frequency,
            pattern_type="fixed_day",
            confidence=0.9,
            anchor_day=15,
            variance_days=1,
            grace_period_days=5,
        ),
        sample_document_ids=[1, 2, 3],
        first_seen=date(2025, 1, 15),
        last_seen=date(2025, 6, 15),
    )


def _eversource_docs() -> list[DocumentRecord]:
    """Simulate 2 Eversource accounts (Natick + Cape)."""
    docs = []
    for i, month in enumerate(range(1, 7)):
        docs.append(
            DocumentRecord(
                id=100 + i,
                title=f"Eversource Bill Natick {date(2025, month, 10).strftime('%B %Y')}",
                correspondent_id=20,
                correspondent_name="Eversource",
                created=date(2025, month, 10),
                tags=["bill"],
            )
        )
        docs.append(
            DocumentRecord(
                id=200 + i,
                title=f"Eversource Bill Cape {date(2025, month, 12).strftime('%B %Y')}",
                correspondent_id=20,
                correspondent_name="Eversource",
                created=date(2025, month, 12),
                tags=["bill"],
            )
        )
    return docs


def test_ignore_removes_matching_provider() -> None:
    providers = [_make_provider("Chase Visa"), _make_provider("Junk Provider")]
    hint = ProviderHint(correspondent="Junk Provider", action="ignore")

    result = apply_hints(providers, [], [hint])

    names = {p.provider_name for p in result}
    assert "Chase Visa" in names
    assert "Junk Provider" not in names


def test_ignore_by_provider_key() -> None:
    providers = [_make_provider("Chase Visa", key="chase-visa"), _make_provider("Other")]
    hint = ProviderHint(provider_key="chase-visa", action="ignore")

    result = apply_hints(providers, [], [hint])

    assert len(result) == 1
    assert result[0].provider_name == "Other"


def test_rename_changes_provider_name() -> None:
    providers = [_make_provider("Electric Statement Natick")]
    hint = ProviderHint(
        correspondent="Electric Statement Natick",
        action="rename",
        rename_to="Eversource - Natick",
    )

    result = apply_hints(providers, [], [hint])

    assert result[0].provider_name == "Eversource - Natick"


def test_split_creates_sub_groups() -> None:
    providers = [_make_provider("Eversource")]
    docs = _eversource_docs()
    hint = ProviderHint(
        correspondent="Eversource",
        action="split",
        groups=[
            ProviderHintGroup(name="Eversource - Natick", title_match="natick"),
            ProviderHintGroup(name="Eversource - Cape", title_match="cape"),
        ],
    )

    result = apply_hints(providers, docs, [hint])

    names = {p.provider_name for p in result}
    assert "Eversource - Natick" in names
    assert "Eversource - Cape" in names
    assert "Eversource" not in names
    # Each should have 6 docs
    for p in result:
        assert p.document_count == 6


def test_split_preserves_original_if_no_matches() -> None:
    providers = [_make_provider("Eversource")]
    hint = ProviderHint(
        correspondent="Eversource",
        action="split",
        groups=[
            ProviderHintGroup(name="Eversource - Moon Base", title_match="moon"),
        ],
    )

    result = apply_hints(providers, _eversource_docs(), [hint])

    assert len(result) == 1
    assert result[0].provider_name == "Eversource"


def test_merge_combines_providers() -> None:
    providers = [
        _make_provider("NG Gas", key="ng-gas"),
        _make_provider("Ng Bill", key="ng-bill"),
        _make_provider("Other", key="other"),
    ]
    hint = ProviderHint(
        action="merge",
        merge_keys=["ng-gas", "ng-bill"],
        rename_to="National Grid",
    )

    result = apply_hints(providers, [], [hint])

    names = {p.provider_name for p in result}
    assert "National Grid" in names
    assert "NG Gas" not in names
    assert "Ng Bill" not in names
    assert "Other" in names
    merged = next(p for p in result if p.provider_name == "National Grid")
    assert merged.document_count == 12


def test_define_creates_manual_provider() -> None:
    docs = _eversource_docs()
    providers = [_make_provider("Eversource")]
    hint = ProviderHint(
        correspondent="Eversource",
        action="define",
        rename_to="Eversource - Natick",
        frequency="monthly",
        anchor_day=10,
        groups=[ProviderHintGroup(name="natick", title_match="natick")],
    )

    result = apply_hints(providers, docs, [hint])

    names = {p.provider_name for p in result}
    assert "Eversource - Natick" in names
    assert "Eversource" not in names
    defined = next(p for p in result if p.provider_name == "Eversource - Natick")
    assert defined.pattern.frequency == "monthly"
    assert defined.pattern.anchor_day == 10
    assert defined.pattern.confidence == 1.0


def test_multiple_hints_applied_in_order() -> None:
    providers = [
        _make_provider("Chase Visa", key="chase-visa"),
        _make_provider("Junk", key="junk"),
        _make_provider("Old Name", key="old-name"),
    ]
    hints = [
        ProviderHint(provider_key="junk", action="ignore"),
        ProviderHint(correspondent="Old Name", action="rename", rename_to="New Name"),
    ]

    result = apply_hints(providers, [], hints)

    names = {p.provider_name for p in result}
    assert "Chase Visa" in names
    assert "Junk" not in names
    assert "New Name" in names
    assert "Old Name" not in names


def test_hints_from_config_yaml(tmp_path) -> None:
    """Verify hints are parsed correctly from YAML config."""
    from doc_intelligence_hub.modules.statements.config import load_config

    config_content = """
source:
  mode: fixture
  fixture_path: ../tests/fixtures/paperless_documents.json

analysis:
  min_documents_for_pattern: 3

runtime:
  snapshot_path: ../data/catalog.snapshot.json
  database_path: ../data/statement_tracker.db

provider_hints:
  - correspondent: "Chase Visa"
    action: rename
    rename_to: "Chase Credit Card"
  - correspondent: "Eversource"
    action: split
    groups:
      - name: "Eversource - Natick"
        title_match: "natick"
      - name: "Eversource - Cape"
        title_match: "cape"
  - action: ignore
    provider_key: "junk-provider"
"""
    config_file = tmp_path / "test_config.yaml"
    config_file.write_text(config_content)
    # Create required fixture path
    (tmp_path / "tests" / "fixtures").mkdir(parents=True)
    import pathlib
    import shutil

    fixture_src = pathlib.Path(__file__).parent / "fixtures" / "paperless_documents.json"
    shutil.copy(fixture_src, tmp_path / "tests" / "fixtures" / "paperless_documents.json")

    config = load_config(str(config_file))

    assert len(config.provider_hints) == 3
    assert config.provider_hints[0].action == "rename"
    assert config.provider_hints[0].rename_to == "Chase Credit Card"
    assert config.provider_hints[1].action == "split"
    assert len(config.provider_hints[1].groups) == 2
    assert config.provider_hints[2].action == "ignore"
