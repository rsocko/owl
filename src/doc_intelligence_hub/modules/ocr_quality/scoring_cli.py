"""Standalone CLI for the OCR quality scorer.

Scores a single local PDF or text file and prints the resulting
:class:`OCRQualityAssessment` as JSON. This is a manual/ad-hoc tool — the
issue #25 batch inventory scanner calls ``assess_document`` directly rather
than shelling out to this CLI.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from doc_intelligence_hub.modules.ocr_quality.scorer import assess_document
from doc_intelligence_hub.modules.ocr_quality.scoring_config import load_config


@click.command()
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--text",
    "as_text",
    is_flag=True,
    default=False,
    help="Treat the file as plain extracted text instead of a PDF.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Optional YAML scoring config override.",
)
def cli(path: Path, as_text: bool, config_path: Path | None) -> None:
    """Score PATH (a PDF or text file) and print the assessment as JSON."""
    config = load_config(config_path)

    if as_text or path.suffix.lower() not in (".pdf",):
        text_content = path.read_text(encoding="utf-8", errors="replace")
        assessment = assess_document(text_content=text_content, config=config)
    else:
        pdf_bytes = path.read_bytes()
        assessment = assess_document(pdf_bytes=pdf_bytes, config=config)

    click.echo(json.dumps(json.loads(assessment.model_dump_json()), indent=2, default=str))


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
