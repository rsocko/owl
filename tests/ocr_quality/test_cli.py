from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from doc_intelligence_hub.modules.ocr_quality import cli as cli_module
from doc_intelligence_hub.modules.ocr_quality import config as ocr_quality_config
from doc_intelligence_hub.modules.ocr_quality.cli import cli
from doc_intelligence_hub.modules.ocr_quality.database import init_db


class _FakeAsyncClient:
    base_url = "https://paperless.invalid"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    async def list_tags(self):
        return [{"id": 1, "name": "Medical"}]

    async def list_correspondents(self):
        return [{"id": 2, "name": "Acme"}]


@pytest.fixture()
def ocr_db(tmp_path):
    original = ocr_quality_config.settings.database_url
    ocr_quality_config.settings.database_url = f"sqlite:///{tmp_path / 'cli_test.db'}"
    init_db()
    yield
    ocr_quality_config.settings.database_url = original


@pytest.fixture()
def fake_client(monkeypatch):
    monkeypatch.setattr(cli_module, "_client", lambda url, token: _FakeAsyncClient())
    return _FakeAsyncClient


class TestRunCommand:
    def test_run_invokes_service_and_prints_json(self, ocr_db, fake_client, monkeypatch):
        fake_result = {"run_id": "abc", "status": "completed", "counts": {"assessed": 3}}
        mock_scan = AsyncMock(return_value=fake_result)
        monkeypatch.setattr(cli_module.OcrQualityInventoryService, "run_corpus_scan", mock_scan)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "run",
                "--paperless-url",
                "https://paperless.invalid",
                "--paperless-token",
                "synthetic-token",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["run_id"] == "abc"
        mock_scan.assert_awaited_once()

    def test_run_with_tag_scope_resolves_and_forwards(self, ocr_db, fake_client, monkeypatch):
        mock_scan = AsyncMock(return_value={"run_id": "x", "status": "completed", "counts": {}})
        monkeypatch.setattr(cli_module.OcrQualityInventoryService, "run_corpus_scan", mock_scan)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "run",
                "--paperless-url",
                "https://paperless.invalid",
                "--paperless-token",
                "synthetic-token",
                "--tag",
                "Medical",
            ],
        )
        assert result.exit_code == 0, result.output
        _, kwargs = mock_scan.call_args
        assert kwargs["scope_params"] == {"tags__id__in": "1"}


class TestSampleCommand:
    def test_sample_invokes_service_and_prints_json(self, ocr_db, fake_client, monkeypatch):
        fake_result = {"run_id": "s1", "status": "completed", "sample_size_selected": 2}
        mock_sample = AsyncMock(return_value=fake_result)
        monkeypatch.setattr(
            cli_module.OcrQualityInventoryService, "run_stratified_sample", mock_sample
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "sample",
                "--paperless-url",
                "https://paperless.invalid",
                "--paperless-token",
                "synthetic-token",
                "--source-run-id",
                "source-1",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["run_id"] == "s1"


class TestReportCommand:
    def test_report_unknown_run_id_errors_cleanly(self, ocr_db):
        runner = CliRunner()
        result = runner.invoke(cli, ["report", "--run-id", "does-not-exist"])
        assert result.exit_code != 0

    def test_report_known_run_prints_redacted_json(self, ocr_db, monkeypatch):
        fake_report = {"run_id": "r1", "redacted": True, "counts": {"assessed": 1}}
        monkeypatch.setattr(
            cli_module.OcrQualityInventoryService,
            "build_aggregate_report",
            lambda self, run_id: fake_report,
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["report", "--run-id", "r1"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["redacted"] is True


class TestStatusCommand:
    def test_status_with_no_runs(self, ocr_db):
        runner = CliRunner()
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "No OCR quality inventory runs yet" in result.output


class TestInitDatabaseCommand:
    def test_init_database(self, ocr_db):
        runner = CliRunner()
        result = runner.invoke(cli, ["init-database"])
        assert result.exit_code == 0
        assert "Database initialized" in result.output
