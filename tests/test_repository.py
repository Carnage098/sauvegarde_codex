from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from models.article import CodexArticle
from repositories.article_repository import ArticleRepository, normalize_search_text


class ArticleRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "library.sqlite3"
        self.repository = ArticleRepository(self.database_path)
        await self.repository.connect()

    async def asyncTearDown(self) -> None:
        await self.repository.close()
        self.temp_dir.cleanup()

    async def test_upsert_search_and_categories(self) -> None:
        article = CodexArticle(
            title="Correction du texte de Théoréalisation Après l'Accalmie",
            url="https://codexygo.fr/article/test-ruling-1/",
            description="Une correction de ruling importante pour le TCG.",
            author="Joeri_sama",
            categories=("Dossiers", "Rulings"),
            published_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )

        article_id, created = await self.repository.upsert(
            article,
            announced=True,
        )
        self.assertTrue(created)
        self.assertGreater(article_id, 0)

        results = await self.repository.search("theorealisation accalmie")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].article.title, article.title)

        category_results = await self.repository.search(
            "correction",
            category="Rulings",
        )
        self.assertEqual(len(category_results), 1)

        stats = dict(await self.repository.category_stats())
        self.assertEqual(stats["Dossiers"], 1)
        self.assertEqual(stats["Rulings"], 1)

    async def test_partial_update_preserves_existing_metadata(self) -> None:
        url = "https://codexygo.fr/article/conservation-10/"
        await self.repository.upsert(
            CodexArticle(
                title="Titre complet",
                url=url,
                description="Description conservée.",
                image_url="https://codexygo.fr/image.jpg",
                author="Auteur",
                categories=("Dossiers", "Focus"),
            ),
            announced=True,
        )
        await self.repository.upsert(
            CodexArticle(title="Titre complet", url=url),
            announced=None,
        )

        record = await self.repository.get_by_url(url)
        self.assertIsNotNone(record)
        self.assertEqual(record.article.description, "Description conservée.")
        self.assertEqual(record.article.categories, ("Dossiers", "Focus"))

    async def test_pending_then_mark_announced(self) -> None:
        article = CodexArticle(
            title="Nouvel article",
            url="https://codexygo.fr/article/nouveau-999/",
            categories=("Actualités", "OCG / TCG"),
        )
        article_id, _ = await self.repository.upsert(article, announced=False)
        self.assertEqual(await self.repository.pending_count(), 1)

        pending = await self.repository.pending(5)
        self.assertEqual([record.id for record in pending], [article_id])

        await self.repository.mark_announced(article_id, 123456789)
        self.assertEqual(await self.repository.pending_count(), 0)

    def test_search_normalization(self) -> None:
        self.assertEqual(
            normalize_search_text("Théoréalisation — Règles !"),
            "theorealisation regles",
        )


class LegacyMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_table_is_migrated_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "legacy.sqlite3"
            connection = sqlite3.connect(database_path)
            connection.execute(
                """
                CREATE TABLE published_articles (
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
            connection.execute(
                """
                INSERT INTO published_articles(
                    url, title, categories, detected_at, published_at,
                    discord_message_id, seeded
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "https://codexygo.fr/article/ancien-1/",
                    "Article présent avant le premier lancement",
                    "Actualités|OCG / TCG",
                    datetime.now(timezone.utc).isoformat(),
                    None,
                    None,
                    1,
                ),
            )
            connection.commit()
            connection.close()

            repository = ArticleRepository(database_path)
            await repository.connect()
            record = await repository.get_by_url(
                "https://codexygo.fr/article/ancien-1/"
            )
            self.assertIsNotNone(record)
            self.assertTrue(record.announced)

            await repository.upsert(
                CodexArticle(
                    title="Titre enrichi",
                    url="https://codexygo.fr/article/ancien-1/",
                    description="Description complète.",
                    categories=("Actualités", "OCG / TCG"),
                ),
                announced=None,
            )
            await repository.close()

            repository = ArticleRepository(database_path)
            await repository.connect()
            enriched = await repository.get_by_url(
                "https://codexygo.fr/article/ancien-1/"
            )
            self.assertIsNotNone(enriched)
            self.assertEqual(enriched.article.title, "Titre enrichi")
            await repository.close()


if __name__ == "__main__":
    unittest.main()
