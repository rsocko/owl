from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date

import httpx
import uvicorn

from doc_intelligence_hub.modules.statements.api import create_app
from doc_intelligence_hub.modules.statements.config import load_config
from doc_intelligence_hub.modules.statements.service import run_connection_test, run_discovery, run_discovery_debug, run_recommendations, validate_source_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="statement-tracker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover")
    discover.add_argument("--config", required=True)

    debug_discovery = subparsers.add_parser("debug-discovery")
    debug_discovery.add_argument("--config", required=True)
    debug_discovery.add_argument("--limit", type=int, default=20)

    check_missing = subparsers.add_parser("check-missing")
    check_missing.add_argument("--config", required=True)
    check_missing.add_argument("--as-of", required=True)

    test_connection = subparsers.add_parser("test-connection")
    test_connection.add_argument("--config", required=True)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--config", required=True)

    # Data retention cleanup
    cleanup = subparsers.add_parser("cleanup", help="Run data retention cleanup across all DI modules")
    cleanup.add_argument("--dry-run", action="store_true", default=False, help="Preview only — don't delete anything")
    cleanup.add_argument("--config", required=False, help="Path to retention.yaml config file")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "cleanup":
            from pathlib import Path

            from doc_intelligence_hub.core.retention import load_retention_config, run_cleanup

            config_path = Path(args.config) if getattr(args, "config", None) else None
            cfg = load_retention_config(config_path)
            result = run_cleanup(dry_run=args.dry_run, config=cfg)
            print(json.dumps(result.to_dict(), indent=2))
            return

        if args.command == "discover":
            result = asyncio.run(run_discovery(args.config))
            print(json.dumps(result.model_dump(mode="json"), indent=2))
            return

        if args.command == "debug-discovery":
            result = asyncio.run(run_discovery_debug(args.config, args.limit))
            print(json.dumps(result.model_dump(mode="json"), indent=2))
            return

        if args.command == "check-missing":
            as_of = date.fromisoformat(args.as_of)
            result = asyncio.run(run_recommendations(args.config, as_of))
            print(json.dumps(result.model_dump(mode="json"), indent=2))
            return

        if args.command == "test-connection":
            result = asyncio.run(run_connection_test(args.config))
            print(json.dumps(result, indent=2))
            return

        if args.command == "serve":
            config = load_config(args.config)
            validate_source_config(config)
            asyncio.run(run_connection_test(args.config))
            uvicorn.run(create_app(args.config), host=config.server.host, port=config.server.port)
            return
    except (FileNotFoundError, ValueError) as error:
        parser.exit(2, f"statement-tracker: error: {error}\n")
    except httpx.HTTPStatusError as error:
        parser.exit(
            2,
            "statement-tracker: error: "
            f"Paperless API returned {error.response.status_code} for {error.request.url}. "
            "Check the base URL, scheme (https vs http), and API token permissions.\n",
        )
    except httpx.RequestError as error:
        parser.exit(
            2,
            "statement-tracker: error: "
            f"Could not reach the Paperless API at {error.request.url}. "
            "Check DNS, network access, and TLS settings.\n",
        )

    parser.error(f"Unsupported command: {args.command}")

