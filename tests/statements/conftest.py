"""Shared test configuration — loads .env before tests run."""

from pathlib import Path

import dotenv

# Load .env from the project root (statement-tracking/)
_env_path = Path(__file__).resolve().parent.parent / ".env"
dotenv.load_dotenv(_env_path)
