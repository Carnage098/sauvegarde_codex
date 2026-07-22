from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from models.index_report import IndexReport
from repositories.article_repository import ArticleRepository
from services.codex_client import CodexClient

LOGGER = logging.getLogger(__name__)


class CodexLibraryIndexer:
    def __init__(
        self,
        client: CodexClient,
        repository: ArticleRepository,
    ) -> None:
        self.client = client
        self.repository = repository

    @staticmethod
    def _is_recent(article_date: datetime | None) -> bool:
        if article_date is None:
            return False
        if article_date.tzinfo is None:
            article_date = article_date.replace(tzinfo=timezone.utc)
        return article_date.astimezone(timezone.utc) >= (
            datetime.now(timezone.utc) - timedelta(hours=48)
        )

    async def sync(
        self,
        *,
        scroll_rounds: int,
        max_pages_per_category: int,
        concurrency: int,
    ) -> IndexReport:
        urls = await self.client.fetch_archive_article_urls(
            scroll_rounds=scroll_rounds,
            max_pages_per_category=max_pages_per_category,
        )
        if not urls:
            return IndexReport()

        library_was_empty = await self.repository.is_empty()
        existing_urls = await self.repository.existing_urls(urls)
        target_urls = await self.repository.urls_needing_metadata(urls)

        inserted = 0
        updated = 0
        fetched = 0

        # Des lots modérés évitent de créer des centaines de tâches HTTP d'un coup.
        batch_size = 40
        for start in range(0, len(target_urls), batch_size):
            batch = target_urls[start : start + batch_size]
            articles = await self.client.fetch_articles(
                batch,
                concurrency=concurrency,
            )
            fetched += len(articles)

            for article in articles:
                is_new = article.url not in existing_urls
                if is_new:
                    # Une première construction de bibliothèque ne doit jamais
                    # republier les archives. Lors des synchronisations suivantes,
                    # une publication de moins de 48 h reste en attente d'annonce.
                    announced = (
                        True
                        if library_was_empty
                        else not self._is_recent(article.published_at)
                    )
                else:
                    announced = None

                _, created = await self.repository.upsert(
                    article,
                    announced=announced,
                )
                if created:
                    inserted += 1
                    existing_urls.add(article.url)
                else:
                    updated += 1

        failed = max(0, len(target_urls) - fetched)
        report = IndexReport(
            discovered_urls=len(urls),
            fetched_articles=fetched,
            inserted_articles=inserted,
            updated_articles=updated,
            failed_articles=failed,
        )
        LOGGER.info(
            "Bibliothèque Codex synchronisée : %s URL(s), %s ajout(s), "
            "%s mise(s) à jour, %s échec(s).",
            report.discovered_urls,
            report.inserted_articles,
            report.updated_articles,
            report.failed_articles,
        )
        return report
