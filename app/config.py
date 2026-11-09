"""Percorsi e segreti. Dati e chiavi stanno fuori da OneDrive, in %LOCALAPPDATA%."""
from __future__ import annotations

import json
import os
from pathlib import Path

_default = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "GarminCoach"
DATA_DIR = Path(os.environ.get("GARMIN_COACH_DATA") or _default)
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEMO = os.environ.get("GARMIN_COACH_DEMO") == "1"
DB_PATH = DATA_DIR / ("demo.db" if DEMO else "coach.db")
SECRETS_PATH = DATA_DIR / "secrets.json"
GARMIN_TOKEN_CACHE = DATA_DIR / "garmin_bearer_token.json"

HOST = "127.0.0.1"
PORT = int(os.environ.get("GARMIN_COACH_PORT", "8765"))

SECRET_NAMES = ("garmin_api_key", "anthropic_api_key")


def read_secrets() -> dict:
    try:
        return json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_secret(name: str, value: str) -> None:
    if name not in SECRET_NAMES:
        raise ValueError(name)
    data = read_secrets()
    if value:
        data[name] = value
    else:
        data.pop(name, None)
    SECRETS_PATH.write_text(json.dumps(data), encoding="utf-8")


def get_secret(name: str) -> str | None:
    env = {"garmin_api_key": "GARMIN_PERSONAL_API_KEY", "anthropic_api_key": "ANTHROPIC_API_KEY"}[name]
    return read_secrets().get(name) or os.environ.get(env) or None
