from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from models.article import CodexArticle


class ArticleRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._database: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._database = await aiosqlite.connect(self.database_path)
        self._database.row_factory = aiosqlite.Row

        await self._database.execute(
            """
            CREATE TABLE IF NOT EXISTS published_articles (
                url TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                categories TEXT NOT NULL DEFAULT '',
                detected_at TEXT NOT NULL,
                published_at TEXT,
                discord_message_id TEXT,
                seeded INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await self._database.commit()

    async def close(self) -> None:
        if self._database is not None:
            await self._database.close()
            self._database = None

    def _require_database(self) -> aiosqlite.Connection:
        if self._database is None:
            raise RuntimeError("La base de données n'est pas connectée.")
        return self._database

    async def contains(self, url: str) -> bool:
        database = self._require_database()
        async with database.execute(
            "SELECT 1 FROM published_articles WHERE url = ? LIMIT 1",
            (url,),
        ) as cursor:
            row = await cursor.fetchone()
        return row is not None

    async def is_empty(self) -> bool:
        return await self.count() == 0

    async def count(self) -> int:
        database = self._require_database()
        async with database.execute(
            "SELECT COUNT(*) AS total FROM published_articles"
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["total"]) if row else 0

    async def seed_url(self, url: str) -> None:
        database = self._require_database()
        detected_at = datetime.now(timezone.utc).isoformat()

        await database.execute(
            """
            INSERT OR IGNORE INTO published_articles (
                url,
                title,
                categories,
                detected_at,
                published_at,
                discord_message_id,
                seeded
            )
            VALUES (?, ?, '', ?, NULL, NULL, 1)
            """,
            (url, "Article présent avant le premier lancement", detected_at),
        )
        await database.commit()

    async def save(
        self,
        article: CodexArticle,
        *,
        discord_message_id: int | None = None,
        seeded: bool = False,
    ) -> None:
        database = self._require_database()
        detected_at = datetime.now(timezone.utc).isoformat()
        published_at = article.published_at.isoformat() if article.published_at else None

        await database.execute(
            """
            INSERT OR IGNORE INTO published_articles (
                url,
                title,
                categories,
                detected_at,
                published_at,
                discord_message_id,
                seeded
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article.url,
                article.title,
                "|".join(article.categories),
                detected_at,
                published_at,
                str(discord_message_id) if discord_message_id else None,
                int(seeded),
            ),
        )
        await database.commit()

    async def recent(self, limit: int = 5) -> list[aiosqlite.Row]:
        database = self._require_database()
        async with database.execute(
            """
            SELECT title, url, categories, detected_at, seeded
            FROM published_articles
            ORDER BY detected_at DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
        return list(rows)
