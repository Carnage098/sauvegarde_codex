from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

import aiosqlite
from rapidfuzz import fuzz

from models.article import CodexArticle
from models.article_record import ArticleRecord


_PLACEHOLDER_TITLES = {
    "Article présent avant le premier lancement",
    "Article déjà présent au démarrage",
}


def normalize_search_text(value: str | None) -> str:
    if not value:
        return ""

    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    lowered = without_accents.casefold()
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class ArticleRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._database: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._database = await aiosqlite.connect(self.database_path)
        self._database.row_factory = aiosqlite.Row

        await self._database.execute("PRAGMA foreign_keys = ON")
        await self._database.execute("PRAGMA journal_mode = WAL")

        await self._database.executescript(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                description TEXT,
                image_url TEXT,
                author TEXT,
                published_at TEXT,
                detected_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_premium INTEGER NOT NULL DEFAULT 0,
                announced INTEGER NOT NULL DEFAULT 0,
                discord_message_id TEXT,
                search_text TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                normalized_name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS article_categories (
                article_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (article_id, category_id),
                FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE,
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS library_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_articles_published_at
                ON articles(published_at DESC);
            CREATE INDEX IF NOT EXISTS idx_articles_announced
                ON articles(announced, published_at);
            CREATE INDEX IF NOT EXISTS idx_articles_search_text
                ON articles(search_text);
            CREATE INDEX IF NOT EXISTS idx_article_categories_category
                ON article_categories(category_id, article_id);
            """
        )

        await self._migrate_legacy_table()
        await self._database.commit()

    async def close(self) -> None:
        if self._database is not None:
            await self._database.close()
            self._database = None

    def _require_database(self) -> aiosqlite.Connection:
        if self._database is None:
            raise RuntimeError("La base de données n'est pas connectée.")
        return self._database

    async def _table_exists(self, table_name: str) -> bool:
        database = self._require_database()
        async with database.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def _migrate_legacy_table(self) -> None:
        """Importe une seule fois l'ancienne table anti-doublon."""
        database = self._require_database()
        if not await self._table_exists("published_articles"):
            return

        async with database.execute(
            "SELECT 1 FROM library_metadata WHERE key = 'legacy_migration_completed'"
        ) as cursor:
            if await cursor.fetchone() is not None:
                return

        async with database.execute(
            """
            SELECT url, title, categories, detected_at, published_at,
                   discord_message_id, seeded
            FROM published_articles
            """
        ) as cursor:
            rows = await cursor.fetchall()

        for row in rows:
            now = datetime.now(timezone.utc).isoformat()
            title = str(row["title"] or "Article Codex YGO")
            categories = tuple(
                category.strip()
                for category in str(row["categories"] or "").split("|")
                if category.strip()
            )
            article = CodexArticle(
                title=title,
                url=str(row["url"]),
                categories=categories,
                published_at=_parse_datetime(row["published_at"]),
            )
            await self.upsert(
                article,
                announced=True,
                discord_message_id=(
                    int(row["discord_message_id"])
                    if row["discord_message_id"]
                    and str(row["discord_message_id"]).isdigit()
                    else None
                ),
                detected_at=str(row["detected_at"] or now),
            )

        await database.execute(
            """
            INSERT OR REPLACE INTO library_metadata(key, value)
            VALUES ('legacy_migration_completed', ?)
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )

    @staticmethod
    def _build_search_text(article: CodexArticle) -> str:
        parts = [
            article.title,
            article.description or "",
            article.author or "",
            " ".join(article.categories),
            article.url.rsplit("/", 2)[-2].replace("-", " "),
        ]
        return normalize_search_text(" ".join(parts))

    async def _replace_categories(
        self,
        article_id: int,
        categories: Sequence[str],
    ) -> None:
        database = self._require_database()
        await database.execute(
            "DELETE FROM article_categories WHERE article_id = ?",
            (article_id,),
        )

        for position, category_name in enumerate(categories):
            normalized = normalize_search_text(category_name)
            if not normalized:
                continue

            await database.execute(
                """
                INSERT INTO categories(name, normalized_name)
                VALUES (?, ?)
                ON CONFLICT(normalized_name) DO UPDATE SET name = excluded.name
                """,
                (category_name, normalized),
            )
            async with database.execute(
                "SELECT id FROM categories WHERE normalized_name = ?",
                (normalized,),
            ) as cursor:
                category_row = await cursor.fetchone()

            if category_row is not None:
                await database.execute(
                    """
                    INSERT OR REPLACE INTO article_categories(
                        article_id, category_id, position
                    ) VALUES (?, ?, ?)
                    """,
                    (article_id, int(category_row["id"]), position),
                )

    async def upsert(
        self,
        article: CodexArticle,
        *,
        announced: bool | None = None,
        discord_message_id: int | None = None,
        detected_at: str | None = None,
    ) -> tuple[int, bool]:
        """Ajoute ou actualise un article. Retourne (id, nouvel_article)."""
        database = self._require_database()
        now = datetime.now(timezone.utc).isoformat()

        async with database.execute(
            """
            SELECT id, title, description, image_url, author, published_at,
                   is_premium, announced, discord_message_id
            FROM articles
            WHERE url = ?
            """,
            (article.url,),
        ) as cursor:
            existing = await cursor.fetchone()

        if existing is None:
            final_article = article
        else:
            existing_id = int(existing["id"])
            existing_categories = (
                await self._categories_for_ids([existing_id])
            ).get(existing_id, ())
            final_article = CodexArticle(
                title=(
                    article.title
                    if article.title not in _PLACEHOLDER_TITLES
                    else str(existing["title"] or article.title)
                ),
                url=article.url,
                description=article.description or existing["description"],
                image_url=article.image_url or existing["image_url"],
                author=article.author or existing["author"],
                categories=article.categories or existing_categories,
                published_at=(
                    article.published_at
                    or _parse_datetime(existing["published_at"])
                ),
                is_premium=(
                    article.is_premium or bool(existing["is_premium"])
                ),
            )

        published_at = (
            final_article.published_at.isoformat()
            if final_article.published_at
            else None
        )
        search_text = self._build_search_text(final_article)

        if existing is None:
            cursor = await database.execute(
                """
                INSERT INTO articles(
                    url, title, description, image_url, author,
                    published_at, detected_at, updated_at, is_premium,
                    announced, discord_message_id, search_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    final_article.url,
                    final_article.title,
                    final_article.description,
                    final_article.image_url,
                    final_article.author,
                    published_at,
                    detected_at or now,
                    now,
                    int(final_article.is_premium),
                    int(bool(announced)),
                    str(discord_message_id) if discord_message_id else None,
                    search_text,
                ),
            )
            article_id = int(cursor.lastrowid)
            inserted = True
        else:
            article_id = int(existing["id"])
            current_announced = bool(existing["announced"])
            final_announced = current_announced if announced is None else announced
            final_message_id = (
                str(discord_message_id)
                if discord_message_id is not None
                else existing["discord_message_id"]
            )
            await database.execute(
                """
                UPDATE articles
                SET title = ?, description = ?, image_url = ?, author = ?,
                    published_at = COALESCE(?, published_at),
                    updated_at = ?, is_premium = ?, announced = ?,
                    discord_message_id = ?, search_text = ?
                WHERE id = ?
                """,
                (
                    final_article.title,
                    final_article.description,
                    final_article.image_url,
                    final_article.author,
                    published_at,
                    now,
                    int(final_article.is_premium),
                    int(final_announced),
                    final_message_id,
                    search_text,
                    article_id,
                ),
            )
            inserted = False

        await self._replace_categories(article_id, final_article.categories)
        await database.commit()
        return article_id, inserted

    async def seed_url(self, url: str) -> None:
        """Compatibilité : mémorise une URL sans la considérer comme nouvelle."""
        await self.upsert(
            CodexArticle(
                title="Article présent avant le premier lancement",
                url=url,
            ),
            announced=True,
        )

    async def save(
        self,
        article: CodexArticle,
        *,
        discord_message_id: int | None = None,
        seeded: bool = False,
    ) -> None:
        await self.upsert(
            article,
            announced=True,
            discord_message_id=discord_message_id,
        )

    async def contains(self, url: str) -> bool:
        database = self._require_database()
        async with database.execute(
            "SELECT 1 FROM articles WHERE url = ? LIMIT 1",
            (url,),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def is_announced(self, url: str) -> bool:
        database = self._require_database()
        async with database.execute(
            "SELECT announced FROM articles WHERE url = ? LIMIT 1",
            (url,),
        ) as cursor:
            row = await cursor.fetchone()
        return bool(row["announced"]) if row else False

    async def mark_announced(
        self,
        article_id: int,
        discord_message_id: int | None = None,
    ) -> None:
        database = self._require_database()
        await database.execute(
            """
            UPDATE articles
            SET announced = 1,
                discord_message_id = COALESCE(?, discord_message_id),
                updated_at = ?
            WHERE id = ?
            """,
            (
                str(discord_message_id) if discord_message_id else None,
                datetime.now(timezone.utc).isoformat(),
                article_id,
            ),
        )
        await database.commit()

    async def is_empty(self) -> bool:
        return await self.count() == 0

    async def count(self) -> int:
        database = self._require_database()
        async with database.execute("SELECT COUNT(*) AS total FROM articles") as cursor:
            row = await cursor.fetchone()
        return int(row["total"]) if row else 0

    async def pending_count(self) -> int:
        database = self._require_database()
        async with database.execute(
            "SELECT COUNT(*) AS total FROM articles WHERE announced = 0"
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["total"]) if row else 0

    async def existing_urls(self, urls: Iterable[str]) -> set[str]:
        values = list(dict.fromkeys(urls))
        if not values:
            return set()

        database = self._require_database()
        existing: set[str] = set()
        chunk_size = 400
        for start in range(0, len(values), chunk_size):
            chunk = values[start : start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            async with database.execute(
                f"SELECT url FROM articles WHERE url IN ({placeholders})",
                tuple(chunk),
            ) as cursor:
                rows = await cursor.fetchall()
            existing.update(str(row["url"]) for row in rows)
        return existing

    async def urls_needing_metadata(self, urls: Iterable[str]) -> list[str]:
        values = list(dict.fromkeys(urls))
        if not values:
            return []

        existing = await self.existing_urls(values)
        result = [url for url in values if url not in existing]

        database = self._require_database()
        known = [url for url in values if url in existing]
        chunk_size = 400
        for start in range(0, len(known), chunk_size):
            chunk = known[start : start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            async with database.execute(
                f"""
                SELECT
                    a.id,
                    a.url,
                    a.title,
                    a.description,
                    a.search_text,
                    (
                        SELECT COUNT(*)
                        FROM article_categories ac
                        WHERE ac.article_id = a.id
                    ) AS category_count
                FROM articles a
                WHERE a.url IN ({placeholders})
                """,
                tuple(chunk),
            ) as cursor:
                rows = await cursor.fetchall()

            for row in rows:
                title = str(row["title"] or "")
                if (
                    title in _PLACEHOLDER_TITLES
                    or not row["description"]
                    or not row["search_text"]
                    or int(row["category_count"] or 0) == 0
                ):
                    result.append(str(row["url"]))

        return list(dict.fromkeys(result))

    async def _categories_for_ids(
        self,
        article_ids: Sequence[int],
    ) -> dict[int, tuple[str, ...]]:
        if not article_ids:
            return {}

        database = self._require_database()
        output: dict[int, list[str]] = {article_id: [] for article_id in article_ids}
        chunk_size = 400
        for start in range(0, len(article_ids), chunk_size):
            chunk = article_ids[start : start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            async with database.execute(
                f"""
                SELECT ac.article_id, c.name
                FROM article_categories ac
                JOIN categories c ON c.id = ac.category_id
                WHERE ac.article_id IN ({placeholders})
                ORDER BY ac.article_id, ac.position
                """,
                tuple(chunk),
            ) as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                output.setdefault(int(row["article_id"]), []).append(str(row["name"]))

        return {
            article_id: tuple(category_names)
            for article_id, category_names in output.items()
        }

    async def _records_from_rows(
        self,
        rows: Sequence[aiosqlite.Row],
    ) -> list[ArticleRecord]:
        article_ids = [int(row["id"]) for row in rows]
        categories_by_id = await self._categories_for_ids(article_ids)
        records: list[ArticleRecord] = []

        for row in rows:
            article_id = int(row["id"])
            records.append(
                ArticleRecord(
                    id=article_id,
                    article=CodexArticle(
                        title=str(row["title"]),
                        url=str(row["url"]),
                        description=row["description"],
                        image_url=row["image_url"],
                        author=row["author"],
                        categories=categories_by_id.get(article_id, ()),
                        published_at=_parse_datetime(row["published_at"]),
                        is_premium=bool(row["is_premium"]),
                    ),
                    announced=bool(row["announced"]),
                    detected_at=_parse_datetime(row["detected_at"]),
                    updated_at=_parse_datetime(row["updated_at"]),
                )
            )
        return records

    async def get_by_id(self, article_id: int) -> ArticleRecord | None:
        database = self._require_database()
        async with database.execute(
            "SELECT * FROM articles WHERE id = ? LIMIT 1",
            (article_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        records = await self._records_from_rows([row])
        return records[0] if records else None

    async def get_by_url(self, url: str) -> ArticleRecord | None:
        database = self._require_database()
        async with database.execute(
            "SELECT * FROM articles WHERE url = ? LIMIT 1",
            (url,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        records = await self._records_from_rows([row])
        return records[0] if records else None

    async def pending(self, limit: int = 5) -> list[ArticleRecord]:
        database = self._require_database()
        async with database.execute(
            """
            SELECT * FROM articles
            WHERE announced = 0
            ORDER BY COALESCE(published_at, detected_at) ASC
            LIMIT ?
            """,
            (max(1, limit),),
        ) as cursor:
            rows = await cursor.fetchall()
        return await self._records_from_rows(rows)

    async def recent_records(self, limit: int = 10) -> list[ArticleRecord]:
        database = self._require_database()
        async with database.execute(
            """
            SELECT * FROM articles
            ORDER BY COALESCE(published_at, detected_at) DESC
            LIMIT ?
            """,
            (max(1, limit),),
        ) as cursor:
            rows = await cursor.fetchall()
        return await self._records_from_rows(rows)

    async def search(
        self,
        query: str,
        *,
        category: str | None = None,
        limit: int = 25,
    ) -> list[ArticleRecord]:
        """Recherche tolérante aux fautes, mots incomplets et ordre différent.

        La base étant une bibliothèque de taille raisonnable, on récupère un
        ensemble large de candidats puis on les classe en Python avec
        RapidFuzz. Cela évite qu'une faute comme ``banlsit`` élimine tous les
        résultats au niveau SQL.
        """
        normalized_query = normalize_search_text(query)
        tokens = [token for token in normalized_query.split() if token]
        normalized_category = normalize_search_text(category)
        database = self._require_database()

        conditions: list[str] = []
        parameters: list[object] = []

        if normalized_category:
            conditions.append(
                """
                EXISTS (
                    SELECT 1
                    FROM article_categories ac2
                    JOIN categories c2 ON c2.id = ac2.category_id
                    WHERE ac2.article_id = a.id
                      AND c2.normalized_name = ?
                )
                """
            )
            parameters.append(normalized_category)

        # Pour un texte très court, un préfiltre SQL évite de proposer des
        # articles sans rapport. À partir de 3 caractères, on garde un groupe
        # beaucoup plus large afin de tolérer les fautes de frappe.
        if normalized_query and len(normalized_query) <= 2:
            conditions.append("a.search_text LIKE ?")
            parameters.append(f"%{normalized_query}%")

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        candidate_limit = max(150, min(1_000, limit * 30))
        parameters.append(candidate_limit)

        async with database.execute(
            f"""
            SELECT a.*
            FROM articles a
            WHERE {where_clause}
            ORDER BY COALESCE(a.published_at, a.detected_at) DESC
            LIMIT ?
            """,
            tuple(parameters),
        ) as cursor:
            rows = await cursor.fetchall()

        records = await self._records_from_rows(rows)
        if not normalized_query:
            return records[:limit]

        def score(
            record: ArticleRecord,
        ) -> tuple[float, float, bool, float]:
            article = record.article
            normalized_title = normalize_search_text(article.title)
            normalized_description = normalize_search_text(article.description)
            normalized_categories = normalize_search_text(" ".join(article.categories))
            normalized_slug = normalize_search_text(
                article.url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
            )
            searchable = " ".join(
                value
                for value in (
                    normalized_title,
                    normalized_description,
                    normalized_categories,
                    normalized_slug,
                )
                if value
            )

            direct_match = any(
                normalized_query in value
                for value in (
                    normalized_title,
                    normalized_description,
                    normalized_categories,
                    normalized_slug,
                )
                if value
            )

            title_wratio = fuzz.WRatio(normalized_query, normalized_title)
            title_partial = fuzz.partial_ratio(normalized_query, normalized_title)
            title_tokens = fuzz.token_set_ratio(normalized_query, normalized_title)
            slug_wratio = fuzz.WRatio(normalized_query, normalized_slug)
            full_tokens = fuzz.token_set_ratio(normalized_query, searchable)
            best_similarity = max(
                title_wratio,
                title_partial,
                title_tokens,
                slug_wratio,
                full_tokens,
            )

            value = 0.0

            # Les correspondances exactes et les débuts de titre restent
            # prioritaires devant les correspondances floues.
            if normalized_title == normalized_query:
                value += 2_000
            if normalized_title.startswith(normalized_query):
                value += 900
            if normalized_query in normalized_title:
                value += 700
            if normalized_query in normalized_slug:
                value += 450
            if normalized_query in normalized_categories:
                value += 250
            if normalized_query in normalized_description:
                value += 120

            # RapidFuzz reconnaît les fautes, mots partiels et mots dans un
            # ordre différent. Le titre reçoit le poids le plus important.
            value += title_wratio * 6.0
            value += title_partial * 3.0
            value += title_tokens * 3.0
            value += slug_wratio * 2.0
            value += full_tokens * 1.5

            for token in tokens:
                if token in normalized_title:
                    value += 90
                elif len(token) >= 3:
                    value += fuzz.partial_ratio(token, normalized_title) * 0.8
                if token in normalized_categories:
                    value += 35
                if token in normalized_description:
                    value += 12

            published_timestamp = (
                article.published_at.timestamp() if article.published_at else 0.0
            )
            return value, published_timestamp, direct_match, best_similarity

        scored_records = [(record, score(record)) for record in records]
        scored_records.sort(
            key=lambda item: (item[1][0], item[1][1]),
            reverse=True,
        )

        # Pour une saisie très courte, on exige une grande ressemblance afin
        # d'éviter des suggestions aléatoires. Les requêtes plus longues
        # tolèrent davantage les fautes de frappe.
        query_length = len(normalized_query.replace(" ", ""))
        if query_length <= 3:
            minimum_similarity = 88.0
        elif query_length <= 5:
            minimum_similarity = 74.0
        else:
            minimum_similarity = 57.0

        filtered = [
            record
            for record, (_, _, direct_match, best_similarity) in scored_records
            if direct_match or best_similarity >= minimum_similarity
        ]
        return filtered[:limit]

    async def category_stats(self) -> list[tuple[str, int]]:
        database = self._require_database()
        async with database.execute(
            """
            SELECT c.name, COUNT(*) AS total
            FROM categories c
            JOIN article_categories ac ON ac.category_id = c.id
            GROUP BY c.id, c.name
            ORDER BY total DESC, c.name COLLATE NOCASE
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return [(str(row["name"]), int(row["total"])) for row in rows]

    async def recent(self, limit: int = 5) -> list[aiosqlite.Row]:
        """Compatibilité avec l'ancienne commande de statut."""
        database = self._require_database()
        async with database.execute(
            """
            SELECT title, url, detected_at, announced AS seeded
            FROM articles
            ORDER BY detected_at DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
        return list(rows)
