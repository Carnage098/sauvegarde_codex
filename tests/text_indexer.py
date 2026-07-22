from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from models.article import CodexArticle
from repositories.article_repository import ArticleRepository
from services.library_indexer import CodexLibraryIndexer


class FakeCodexClient:
    def __init__(self, articles: list[CodexArticle]) -> None:
        self.articles = articles

    async def fetch_archive_article_urls(self, **kwargs) -> list[str]:
        return [article.url for article in self.articles]

    async def fetch_articles(self, urls, *, concurrency: int = 3):
        requested = set(urls)
        return [article for article in self.articles if article.url in requested]


class LibraryIndexerTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_archive_is_seeded_without_announcements(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ArticleRepository(Path(temp_dir) / "library.sqlite3")
            await repository.connect()
            articles = [
                CodexArticle(
                    title="Archive ancienne",
                    url="https://codexygo.fr/article/archive-1/",
                    categories=("Dossiers", "Focus"),
                    published_at=datetime.now(timezone.utc) - timedelta(days=100),
                ),
                CodexArticle(
                    title="Article récent déjà présent au premier index",
                    url="https://codexygo.fr/article/recent-2/",
                    categories=("Actualités", "OCG / TCG"),
                    published_at=datetime.now(timezone.utc) - timedelta(hours=2),
                ),
            ]
            indexer = CodexLibraryIndexer(FakeCodexClient(articles), repository)
            report = await indexer.sync(
                scroll_rounds=5,
                max_pages_per_category=5,
                concurrency=2,
            )

            self.assertEqual(report.inserted_articles, 2)
            self.assertEqual(await repository.pending_count(), 0)
            await repository.close()

    async def test_recent_article_found_later_remains_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ArticleRepository(Path(temp_dir) / "library.sqlite3")
            await repository.connect()
            await repository.upsert(
                CodexArticle(
                    title="Article initial",
                    url="https://codexygo.fr/article/initial-1/",
                    categories=("Actualités", "OCG / TCG"),
                ),
                announced=True,
            )

            recent = CodexArticle(
                title="Nouvelle publication",
                url="https://codexygo.fr/article/nouvelle-2/",
                categories=("Actualités", "OCG / TCG"),
                published_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )
            indexer = CodexLibraryIndexer(FakeCodexClient([recent]), repository)
            await indexer.sync(
                scroll_rounds=5,
                max_pages_per_category=5,
                concurrency=2,
            )

            self.assertEqual(await repository.pending_count(), 1)
            await repository.close()


if __name__ == "__main__":
    unittest.main()
