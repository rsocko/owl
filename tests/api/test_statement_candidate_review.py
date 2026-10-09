from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from doc_intelligence_hub.api.app import HubSettings
from doc_intelligence_hub.modules.statements.database import Database
from doc_intelligence_hub.modules.statements.models import (
    AnalysisPattern,
    DiscoveryResult,
    DocumentRecord,
    ProviderCandidate,
)


def test_candidate_documents_can_be_reviewed_and_confirmed(
    client: TestClient,
    hub_settings: HubSettings,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "statement-candidates.db"
    Path(hub_settings.statement_tracker_config).write_text(
        f"runtime:\n  database_path: {database_path.as_posix()}\n",
        encoding="utf-8",
    )
    provider_key = "mutual-one-heat-loan"
    database = Database(str(database_path))
    try:
        database.save_discovery(
            DiscoveryResult(
                analyzed_documents=2,
                providers=[
                    ProviderCandidate(
                        provider_key=provider_key,
                        provider_name="Mutual One",
                        statement_name="Heat Loan",
                        correspondent_id=12,
                        document_count=2,
                        normalized_title="heat loan",
                        title_consistency=1.0,
                        pattern=AnalysisPattern(
                            frequency="monthly",
                            pattern_type="fixed_day",
                            confidence=0.98,
                            anchor_day=29,
                        ),
                        sample_document_ids=[9746, 9748],
                        documents=[
                            DocumentRecord(
                                id=9746,
                                title="Heat Loan Statement May 2026",
                                correspondent_id=12,
                                correspondent_name="Mutual One",
                                created=date(2026, 5, 29),
                            ),
                            DocumentRecord(
                                id=9748,
                                title="Heat Loan Statement June 2026",
                                correspondent_id=12,
                                correspondent_name="Mutual One",
                                created=date(2026, 6, 26),
                            ),
                        ],
                        first_seen=date(2026, 5, 29),
                        last_seen=date(2026, 6, 26),
                    )
                ],
            )
        )
    finally:
        database.close()

    detail = client.get(f"/api/statements/series/{provider_key}")
    assert detail.status_code == 200
    assert detail.json()["membership_complete"] is True
    assert [document["document_id"] for document in detail.json()["documents"]] == [
        "9746",
        "9748",
    ]

    excluded = client.post(
        f"/api/statements/series/{provider_key}/candidate-documents/9746/exclude"
    )
    assert excluded.status_code == 200

    reviewed = client.get(f"/api/statements/series/{provider_key}").json()
    assert [document["document_id"] for document in reviewed["documents"]] == ["9748"]
    assert [document["document_id"] for document in reviewed["excluded_documents"]] == ["9746"]

    confirmed = client.post(f"/api/statements/series/{provider_key}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["series"]["name"] == "Heat Loan"

    curated = client.get(f"/api/statements/series/{provider_key}").json()
    assert curated["membership_complete"] is True
    assert curated["series"]["manually_curated"] is True
    assert [document["document_id"] for document in curated["documents"]] == ["9748"]
