from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _optional_int(name: str) -> int | None:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return None

    try:
        return int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} doit contenir un identifiant numérique.") from exc


def _required_int(name: str) -> int:
    value = _optional_int(name)
    if value is None:
        raise RuntimeError(f"La variable obligatoire {name} est absente.")
    return value


def _bool_env(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().casefold() in {"1", "true", "yes", "oui", "on"}


def _positive_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} doit être un nombre entier.") from exc

    return max(minimum, min(value, maximum))


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str
    codex_channel_id: int
    codex_ping_role_id: int | None
    discord_guild_id: int | None
    sync_commands: bool
    check_interval_minutes: int
    max_articles_per_check: int
    first_run_mode: str
    reseed_on_start: bool
    database_path: Path
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise RuntimeError("La variable obligatoire DISCORD_TOKEN est absente.")

        first_run_mode = os.getenv("CODEX_FIRST_RUN_MODE", "seed").strip().lower()
        if first_run_mode not in {"seed", "publish"}:
            raise RuntimeError("CODEX_FIRST_RUN_MODE doit être 'seed' ou 'publish'.")

        database_raw = os.getenv("DATABASE_PATH", "data/codex_news.sqlite3").strip()
        if not database_raw:
            database_raw = "data/codex_news.sqlite3"

        return cls(
            discord_token=token,
            codex_channel_id=_required_int("CODEX_CHANNEL_ID"),
            codex_ping_role_id=_optional_int("CODEX_PING_ROLE_ID"),
            discord_guild_id=_optional_int("DISCORD_GUILD_ID"),
            sync_commands=_bool_env("SYNC_COMMANDS", False),
            check_interval_minutes=_positive_int(
                "CODEX_CHECK_INTERVAL_MINUTES",
                default=10,
                minimum=5,
                maximum=1_440,
            ),
            max_articles_per_check=_positive_int(
                "CODEX_MAX_ARTICLES_PER_CHECK",
                default=5,
                minimum=1,
                maximum=20,
            ),
            first_run_mode=first_run_mode,
            reseed_on_start=_bool_env("CODEX_RESEED_ON_START", False),
            database_path=Path(database_raw),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        )
